#!/usr/bin/env python3
"""Turn a SKU catalog into order form values, checking the catalog's sales rules.

Standard library only. Reads .xlsx (first sheet) or .csv exports.

Commands:
  import <catalog.xlsx|csv> <catalog.json>
      Convert a catalog export to JSON the agent can read.

  list <catalog.json> [--industry NAME] [--all]
      Show sellable SKUs (add --all to include inactive and on-hold SKUs).

  quote <catalog.json> --sku SKU [--sku SKU:QTY ...] --start YYYY-MM-DD
        [--industry NAME] [--out values.json]
      Check a set of SKUs for one Service Location and print the fee lines,
      totals, proration math, and every rule or control that applies. With
      --out, also writes the matching order form values. Exits with status 2
      when a rule blocks the order.
"""

import argparse
import calendar
import csv
import datetime as dt
import json
import re
import sys
import zipfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

COLUMNS = {
    "sku": ["sku / plan code", "sku", "plan code"],
    "family": ["product family", "family"],
    "tier": ["plan tier", "tier"],
    "industry": ["industry"],
    "name": ["display name", "name"],
    "price": ["monthly price", "price"],
    "billing": ["billing basis"],
    "quantity_allowed": ["quantity allowed"],
    "active": ["active"],
    "description": ["description", "scope"],
    "control": ["required control", "control"],
    "min_term": ["min term", "minimum term"],
}
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MANDATORY = re.compile(r"Mandatory on all ([A-Za-z ]+?) engagements", re.I)
MEDIA_COMMISSION = re.compile(r"(\d+(?:\.\d+)?)% of managed media spend[^.]*", re.I)


# ---------- import ----------

def read_xlsx(path):
    with zipfile.ZipFile(path) as zf:
        strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                strings.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet = sorted(n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
        rows = []
        for row in ET.fromstring(zf.read(sheet)).iter(f"{{{NS['m']}}}row"):
            cells = {}
            for c in row.findall("m:c", NS):
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                idx = 0
                for ch in col:
                    idx = idx * 26 + ord(ch) - 64
                v = c.find("m:v", NS)
                if c.get("t") == "s" and v is not None:
                    value = strings[int(v.text)]
                elif c.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
                else:
                    value = v.text if v is not None else None
                cells[idx - 1] = value
            if cells:
                rows.append([cells.get(i) for i in range(max(cells) + 1)])
        return rows


def read_rows(path):
    if path.suffix.lower() == ".xlsx":
        return read_xlsx(path)
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def import_catalog(src, dest):
    rows = [r for r in read_rows(src) if any(v not in (None, "") for v in r)]
    header = [str(h or "").strip().lower() for h in rows[0]]
    index = {}
    for key, names in COLUMNS.items():
        for name in names:
            if name in header:
                index[key] = header.index(name)
                break
    for required in ("sku", "name", "price"):
        if required not in index:
            sys.exit(f"Catalog is missing a '{COLUMNS[required][0]}' column")
    items = []
    for r in rows[1:]:
        r = r + [None] * (len(header) - len(r))
        item = {k: (str(r[i]).strip() if r[i] not in (None, "") else None) for k, i in index.items()}
        if not item["sku"]:
            continue
        item["price"] = str(Decimal(item["price"]).quantize(Decimal("0.01")))
        items.append(item)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"source": src.name, "items": items}, indent=2), encoding="utf-8")
    print(f"Imported {len(items)} SKUs to {dest}")


# ---------- helpers ----------

def load(path):
    return {i["sku"]: i for i in json.loads(Path(path).read_text(encoding="utf-8"))["items"]}


def yes(value):
    return str(value or "").strip().lower() in ("yes", "y", "true", "1")


def money(amount):
    return f"${amount.quantize(Decimal('0.01'), ROUND_HALF_UP):,.2f}"


def months(term):
    m = re.match(r"(\d+)\s*month", str(term or ""), re.I)
    return int(m.group(1)) if m else 0


def long_date(d):
    return f"{d:%B} {d.day}, {d.year}"


def sentence(text):
    text = (text or "").strip()
    return text if not text or text.endswith(".") else text + "."


# ---------- list ----------

def list_skus(catalog, industry, show_all):
    for item in catalog.values():
        if industry and (item.get("industry") or "").lower() != industry.lower():
            continue
        if not show_all and not yes(item.get("active")):
            continue
        flag = "" if yes(item.get("active")) else "  [NOT SELLABLE]"
        print(f"{item['sku']:<14} {money(Decimal(item['price'])):>10}  {item['name']}{flag}")


# ---------- quote ----------

def quote(catalog, sku_args, start, industry, out):
    errors, warnings, controls = [], [], []
    cart = []
    for arg in sku_args:
        sku, _, qty = arg.partition(":")
        sku, qty = sku.strip().upper(), int(qty or 1)
        item = catalog.get(sku)
        if not item:
            errors.append(f"{sku}: not in the catalog")
            continue
        cart.append((item, qty))

    industries = {i.get("industry") for i, _ in cart if i.get("industry")}
    if industry is None and len(industries) == 1:
        industry = industries.pop()
    elif industry is None and len(industries) > 1:
        errors.append(f"SKUs mix industries ({', '.join(sorted(industries))}); pass --industry")

    for item, qty in cart:
        sku, control = item["sku"], item.get("control") or ""
        if not yes(item.get("active")):
            errors.append(f"{sku}: not sellable (Active = {item.get('active')}). {control}".strip())
        if industry and item.get("industry") and item["industry"].lower() != industry.lower():
            errors.append(f"{sku}: is a {item['industry']} SKU, but this engagement is {industry}")
        if qty > 1 and not yes(item.get("quantity_allowed")):
            errors.append(f"{sku}: quantity {qty} requested, but Quantity allowed = No")
        if control and yes(item.get("active")):
            level = "HARD GATE" if control.upper().startswith("HARD GATE") else "control"
            controls.append({"sku": sku, "level": level, "text": control})
        commission = MEDIA_COMMISSION.search(item.get("description") or "")
        if commission:
            warnings.append(
                f"{sku}: the catalog scope includes \"{commission.group(0).strip()}\", but the order form's "
                "Overage Billing clause says \"None. No percentage of media spend is charged.\" "
                "Decide which is right before sending."
            )
        if "pass-through" in (item.get("description") or "").lower():
            warnings.append(f"{sku}: includes pass-through costs, which the form treats as Third-Party Costs outside the fee.")

    in_cart = {i["sku"] for i, _ in cart}
    for item in catalog.values():
        rule = MANDATORY.search(item.get("control") or "")
        if rule and industry and rule.group(1).strip().lower() == industry.lower() \
                and (item.get("industry") or "").lower() == industry.lower() and item["sku"] not in in_cart:
            errors.append(f"{item['sku']} ({item['name']}) is required: {item['control']}")

    lines, total = [], Decimal("0")
    for item, qty in cart:
        fee = Decimal(item["price"]) * qty
        total += fee
        label = f"{item['name']} ({item['sku']})" + (f" x {qty}" if qty > 1 else "")
        lines.append({"line": label, "scope": sentence(item.get("description")), "fee": money(fee)})

    days_in_month = calendar.monthrange(start.year, start.month)[1]
    days_billed = days_in_month - start.day + 1
    prorated = (total * days_billed / days_in_month).quantize(Decimal("0.01"), ROUND_HALF_UP)
    next_month = (start.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    min_term = max((months(i.get("min_term")) for i, _ in cart), default=0)

    values = {
        "service": {
            "line": [l["line"] for l in lines],
            "scope": [l["scope"] for l in lines],
            "fee": [l["fee"] for l in lines],
        },
        "total_recurring_fee": f"{money(total)} / month",
        "prorated_first_charge": money(prorated),
        "first_billing_date": long_date(start),
        "start_date": long_date(start),
        "recurring_start_month": f"{next_month:%B} {next_month.year}",
    }
    report = {
        "industry": industry,
        "lines": lines,
        "total_monthly": money(total),
        "proration": f"{money(total)} x {days_billed} / {days_in_month} days = {money(prorated)}",
        "minimum_initial_term": f"{min_term} months" if min_term else "none (Open-ended)",
        "errors": errors,
        "warnings": warnings,
        "controls": controls,
        "values": values,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr)
    if errors:
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("source", type=Path)
    imp.add_argument("dest", type=Path)
    lst = sub.add_parser("list")
    lst.add_argument("catalog", type=Path)
    lst.add_argument("--industry")
    lst.add_argument("--all", action="store_true")
    q = sub.add_parser("quote")
    q.add_argument("catalog", type=Path)
    q.add_argument("--sku", action="append", required=True, help="SKU or SKU:QTY; repeat for each line")
    q.add_argument("--start", required=True, type=dt.date.fromisoformat, help="Start Date, YYYY-MM-DD")
    q.add_argument("--industry")
    q.add_argument("--out", type=Path)
    args = parser.parse_args()

    if args.command == "import":
        import_catalog(args.source, args.dest)
    elif args.command == "list":
        list_skus(load(args.catalog), args.industry, args.all)
    else:
        quote(load(args.catalog), args.sku, args.start, args.industry, args.out)


if __name__ == "__main__":
    main()
