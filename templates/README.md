# Document Templates

Drop templates here and the `document-builder` agent will turn them into finished documents.

## How to use it

In Claude Code, run:

```
/build-document templates/client-welcome-letter.md for Jane, Transformation package, starting Oct 7
```

Or just ask: "Use the welcome letter template to build a letter for Jane." The agent reads the template, figures out what it needs, asks you for anything missing, and saves the result to `documents/<name>/`.

## Writing a template

Any of these work:

- **Placeholders (most reliable):** put `{{field_name}}` where values go. Dotted names like `{{client.first_name}}` are fine. Works in `.docx`, `.md`, `.html`, and `.txt`.
- **Informal markers:** `[Client Name]`, `<insert date>`, `TBD`, and so on. The agent will spot and replace them.
- **An example document:** share a finished document and ask for a new one "in the same format."

Optional: add a `<template-name>.example.json` next to a template with sample values. It shows the agent what each field means.

## Templates in this folder

| Template | What it builds |
| --- | --- |
| `client-welcome-letter.md` | Best Life Nutrition new-client welcome letter |

Each has a matching `.example.json` with made-up sample values. The `_notes` inside explain fields that need a calculation or a choice.

## Templates where every blank looks the same

Some forms use the same placeholder, like `{{field}}`, for every blank. The agent handles this: it lists each blank with its label, gives each one a real name, and saves a reusable named copy here. You can do it by hand too:

```
python3 tools/docbuilder/fill_template.py occurrences my-form.docx
python3 tools/docbuilder/fill_template.py rename my-form.docx names.json templates/my-form.docx --strip-underscores
```

`names.json` is a list of names in the order the blanks appear. `--strip-underscores` removes `____` fill-in lines next to a blank so the filled form looks clean.

## Word templates

For `.docx` templates, type placeholders in Word as normal. The fill script handles Word splitting a placeholder across formatting runs, and the filled value keeps the formatting of the placeholder. Values with multiple lines (or JSON lists) become line breaks.

## Running the fill script directly

```
python3 tools/docbuilder/fill_template.py fields templates/client-welcome-letter.md
python3 tools/docbuilder/fill_template.py fill templates/client-welcome-letter.md templates/client-welcome-letter.example.json documents/test/welcome.md --strict
python3 tools/docbuilder/fill_template.py text path/to/template.docx
```

## Pricing from a SKU catalog

Keep catalogs in `catalog/` (not committed: pricing stays private). Import an export once, then quote by SKU:

```
python3 tools/docbuilder/catalog.py import my-catalog.xlsx catalog/my-catalog.json
python3 tools/docbuilder/catalog.py list catalog/my-catalog.json --industry Healthcare
python3 tools/docbuilder/catalog.py quote catalog/my-catalog.json --sku P-SEM-1-HC --sku HC-COMP-1-HC --start 2026-10-15 --out quote.json
```

`quote` builds the fee table lines, total, prorated first charge, and billing dates, and checks the catalog's rules: inactive or on-hold SKUs, industry mismatches, quantity limits, mandatory SKUs, and minimum terms. It also lists every compliance control that applies. Any blocking problem stops the document from being built.

In Word templates, list values inside a table row repeat that row once per item, so one fee table row becomes one row per SKU.
