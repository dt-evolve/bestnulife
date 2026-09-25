#!/usr/bin/env python3
"""Fill {{placeholder}} templates for the document-builder agent.

Supported template types: .docx, .md, .markdown, .txt, .html, .htm
Standard library only, so it runs anywhere Python 3.8+ is available.

Commands:
  fields      <template>                       List the placeholders a template expects.
  occurrences <template>                       List every placeholder in order, with nearby text
                                               as a label hint (for templates that reuse one
                                               generic name such as {{field}} for every blank).
  rename      <template> <names.json> <out>    Give each placeholder occurrence its own name.
                                               names.json is a list in document order; null
                                               keeps an occurrence as is.
  text        <template>                       Print a template's plain text (useful for .docx).
  fill        <template> <values.json> <out>   Write a filled copy of the template.

Placeholders look like {{client_name}} or {{ client.name }}. Values come from a
JSON object; dotted names read nested objects. Multi-line values become line
breaks in .docx output, except inside a table row, where list values repeat
the row once per item. Placeholders with no value are left in place and
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
TABLE_ROW = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
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


def fill_paragraph(para, resolve, pattern=PLACEHOLDER):
    """Replace placeholders in one <w:p>, even when Word split them across runs.

    resolve(name) returns the replacement text, or None to leave a placeholder
    alone. Only the runs a placeholder touches are rewritten; the value takes
    the formatting of the run where the placeholder starts.
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

    matches = list(pattern.finditer(full))
    replacements = [resolve(m.group(1)) for m in matches]
    changed = False
    for match, value in reversed(list(zip(matches, replacements))):
        if value is None:
            continue
        first, last = node_at(match.start()), node_at(match.end() - 1)
        head = texts[first][: match.start() - starts[first]]
        tail = texts[last][match.end() - starts[last]:]
        texts[first] = head + "\x00" + value + "\x00" + (tail if first == last else "")
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


def repeat_rows(xml, values, missing):
    """Repeat a table row once per item when its placeholders hold lists.

    A row with {{service.line}} and {{service.fee}}, where both are lists of
    three, becomes three rows. Scalar values in the row repeat on every copy.
    """
    def expand(match):
        row = match.group(0)
        text = "".join(html.unescape(m.group(2)) for m in TEXT_NODE.finditer(row))
        names = set(PLACEHOLDER.findall(text))
        lists = [lookup(values, n) for n in names if isinstance(lookup(values, n), list)]
        if not lists:
            return row
        copies = []
        for i in range(max(len(v) for v in lists)):
            def resolve(name, i=i):
                value = lookup(values, name)
                if value is None:
                    missing.add(name)
                    return None
                if isinstance(value, list):
                    return str(value[i]) if i < len(value) else ""
                return to_text(value)

            copy = PARAGRAPH.sub(lambda m: fill_paragraph(m.group(0), resolve), row)
            if i:  # Word expects paragraph ids to be unique
                copy = re.sub(r' w14:(paraId|textId)="[^"]*"', "", copy)
            copies.append(copy)
        return "".join(copies)

    return TABLE_ROW.sub(expand, xml)


def rewrite_docx(src, dest, resolve, pattern=PLACEHOLDER, values=None, missing=None):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        parts = docx_parts(zin)
        rewritten = {}
        for part in parts:  # same order as read_text, so occurrence numbers line up
            xml = zin.read(part).decode("utf-8")
            if values is not None:
                xml = repeat_rows(xml, values, missing)
            rewritten[part] = PARAGRAPH.sub(lambda m: fill_paragraph(m.group(0), resolve, pattern), xml)
        for item in zin.infolist():
            data = rewritten[item.filename].encode("utf-8") if item.filename in rewritten else zin.read(item.filename)
            zout.writestr(item, data)


def value_resolver(values, missing):
    def resolve(name):
        value = lookup(values, name)
        if value is None:
            missing.add(name)
            return None
        return to_text(value)

    return resolve


def list_occurrences(path):
    if path.suffix.lower() == ".docx":
        lines = [line for line in read_text(path).split("\n")]
    else:
        lines = path.read_text(encoding="utf-8").split("\n")
    found, previous = [], ""
    for line in lines:
        for match in PLACEHOLDER.finditer(line):
            found.append({
                "index": len(found),
                "name": match.group(1),
                "line": line.strip(),
                "previous_line": previous,
            })
        if line.strip():
            previous = line.strip()
    return found


def rename(template, names_path, output, strip_underscores):
    names = json.loads(Path(names_path).read_text(encoding="utf-8"))
    total = len(list_occurrences(template))
    if len(names) != total:
        sys.exit(f"names.json has {len(names)} entries but the template has {total} placeholders")
    counter = iter(names)

    def resolve(_name):
        new = next(counter)
        return None if new is None else "{{" + new + "}}"

    pattern = PLACEHOLDER
    if strip_underscores:
        pattern = re.compile(r"_*" + PLACEHOLDER.pattern + r"_*")
    output.parent.mkdir(parents=True, exist_ok=True)
    if template.suffix.lower() == ".docx":
        rewrite_docx(template, output, resolve, pattern)
    else:
        text = pattern.sub(lambda m: resolve(m.group(1)) or m.group(0), template.read_text(encoding="utf-8"))
        output.write_text(text, encoding="utf-8")
    print(f"Wrote {output}")


def fill(template, values_path, output, strict):
    values = json.loads(Path(values_path).read_text(encoding="utf-8"))
    suffix = template.suffix.lower()
    output.parent.mkdir(parents=True, exist_ok=True)
    missing = set()

    if suffix == ".docx":
        rewrite_docx(template, output, value_resolver(values, missing), values=values, missing=missing)
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
    for name in ("fields", "occurrences", "text"):
        sub.add_parser(name).add_argument("template", type=Path)
    rename_cmd = sub.add_parser("rename")
    rename_cmd.add_argument("template", type=Path)
    rename_cmd.add_argument("names", type=Path)
    rename_cmd.add_argument("output", type=Path)
    rename_cmd.add_argument("--strip-underscores", action="store_true",
                            help="also remove ____ fill-in lines touching a placeholder")
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
    elif args.command == "occurrences":
        print(json.dumps(list_occurrences(args.template), indent=2, ensure_ascii=False))
    elif args.command == "text":
        print(read_text(args.template))
    else:
        if args.output.resolve() == args.template.resolve():
            sys.exit("Refusing to overwrite the template; choose a different output path.")
        if args.command == "rename":
            rename(args.template, args.names, args.output, args.strip_underscores)
        else:
            fill(args.template, args.values, args.output, args.strict)


if __name__ == "__main__":
    main()
