#!/usr/bin/env python3
"""Fill {{placeholder}} templates for the document-builder agent.

Supported template types: .docx, .md, .markdown, .txt, .html, .htm
Standard library only, so it runs anywhere Python 3.8+ is available.

Commands:
  fields <template>                       List the placeholders a template expects.
  text   <template>                       Print a template's plain text (useful for .docx).
  fill   <template> <values.json> <out>   Write a filled copy of the template.

Placeholders look like {{client_name}} or {{ client.name }}. Values come from a
JSON object; dotted names read nested objects. Multi-line values become line
breaks in .docx output. Placeholders with no value are left in place and
reported, or cause an error with --strict.
"""

import argparse
import html
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
TEXT_TYPES = {".md", ".markdown", ".txt", ".html", ".htm"}
DOCX_PARTS = re.compile(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")
PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.S)
TEXT_NODE = re.compile(r"(<w:t(?: [^>]*)?>)(.*?)(</w:t>)", re.S)
TEXT_OR_BREAK = re.compile(r"<w:br\s*/>|" + TEXT_NODE.pattern, re.S)


def lookup(values, name):
    node = values
    for key in name.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def to_text(value):
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


def docx_parts(zf):
    return [n for n in zf.namelist() if DOCX_PARTS.search(n)]


def docx_paragraph_texts(xml):
    for para in PARAGRAPH.findall(xml):
        yield "".join(
            "\n" if m.group(0).startswith("<w:br") else html.unescape(m.group(2))
            for m in TEXT_OR_BREAK.finditer(para)
        )


def read_text(path):
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as zf:
            lines = []
            for part in docx_parts(zf):
                lines.extend(docx_paragraph_texts(zf.read(part).decode("utf-8")))
            return "\n".join(lines)
    return path.read_text(encoding="utf-8")


def list_fields(path):
    seen = []
    for name in PLACEHOLDER.findall(read_text(path)):
        if name not in seen:
            seen.append(name)
    return seen


def fill_plain(text, values, missing, escape):
    def replace(match):
        value = lookup(values, match.group(1))
        if value is None:
            missing.add(match.group(1))
            return match.group(0)
        value = to_text(value)
        return html.escape(value, quote=False) if escape else value

    return PLACEHOLDER.sub(replace, text)


def fill_paragraph(para, values, missing):
    """Replace placeholders in one <w:p>, even when Word split them across runs.

    Only the runs a placeholder touches are rewritten; the value takes the
    formatting of the run where the placeholder starts.
    """
    nodes = list(TEXT_NODE.finditer(para))
    if not nodes:
        return para
    texts = [html.unescape(n.group(2)) for n in nodes]
    full = "".join(texts)
    if "{{" not in full:
        return para

    starts, pos = [], 0
    for t in texts:
        starts.append(pos)
        pos += len(t)

    def node_at(offset):
        for i in range(len(starts) - 1, -1, -1):
            if starts[i] <= offset:
                return i
        return 0

    changed = False
    for match in reversed(list(PLACEHOLDER.finditer(full))):
        value = lookup(values, match.group(1))
        if value is None:
            missing.add(match.group(1))
            continue
        first, last = node_at(match.start()), node_at(match.end() - 1)
        head = texts[first][: match.start() - starts[first]]
        tail = texts[last][match.end() - starts[last]:]
        texts[first] = head + "\x00" + to_text(value) + "\x00" + (tail if first == last else "")
        if first != last:
            for i in range(first + 1, last):
                texts[i] = ""
            texts[last] = tail
        changed = True
    if not changed:
        return para

    out, cursor = [], 0
    for node, text in zip(nodes, texts):
        body = html.escape(text.replace("\x00", ""), quote=False)
        body = body.replace("\n", '</w:t><w:br/><w:t xml:space="preserve">')
        open_tag = node.group(1)
        if "xml:space" not in open_tag:
            open_tag = open_tag[:-1] + ' xml:space="preserve">'
        out.append(para[cursor:node.start()])
        out.append(open_tag + body + node.group(3))
        cursor = node.end()
    out.append(para[cursor:])
    return "".join(out)


def fill_docx(src, dest, values, missing):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        parts = set(docx_parts(zin))
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename in parts:
                xml = data.decode("utf-8")
                xml = PARAGRAPH.sub(lambda m: fill_paragraph(m.group(0), values, missing), xml)
                data = xml.encode("utf-8")
            zout.writestr(item, data)


def fill(template, values_path, output, strict):
    values = json.loads(Path(values_path).read_text(encoding="utf-8"))
    suffix = template.suffix.lower()
    output.parent.mkdir(parents=True, exist_ok=True)
    missing = set()

    if suffix == ".docx":
        fill_docx(template, output, values, missing)
    elif suffix in TEXT_TYPES:
        escape = suffix in {".html", ".htm"}
        text = fill_plain(template.read_text(encoding="utf-8"), values, missing, escape)
        output.write_text(text, encoding="utf-8")
    else:
        sys.exit(f"Unsupported template type: {suffix}")

    if missing:
        names = ", ".join(sorted(missing))
        if strict:
            output.unlink(missing_ok=True)
            sys.exit(f"Missing values for: {names}")
        print(f"Warning: left unfilled: {names}", file=sys.stderr)
    print(f"Wrote {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fields", "text"):
        sub.add_parser(name).add_argument("template", type=Path)
    fill_cmd = sub.add_parser("fill")
    fill_cmd.add_argument("template", type=Path)
    fill_cmd.add_argument("values", type=Path)
    fill_cmd.add_argument("output", type=Path)
    fill_cmd.add_argument("--strict", action="store_true", help="fail if any placeholder has no value")
    args = parser.parse_args()

    if not args.template.exists():
        sys.exit(f"Template not found: {args.template}")
    if args.command == "fields":
        print(json.dumps(list_fields(args.template), indent=2))
    elif args.command == "text":
        print(read_text(args.template))
    else:
        if args.output.resolve() == args.template.resolve():
            sys.exit("Refusing to overwrite the template; choose a different output path.")
        fill(args.template, args.values, args.output, args.strict)


if __name__ == "__main__":
    main()
