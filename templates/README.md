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

## Word templates

For `.docx` templates, type placeholders in Word as normal. The fill script handles Word splitting a placeholder across formatting runs, and the filled value keeps the formatting of the placeholder. Values with multiple lines (or JSON lists) become line breaks.

## Running the fill script directly

```
python3 tools/docbuilder/fill_template.py fields templates/client-welcome-letter.md
python3 tools/docbuilder/fill_template.py fill templates/client-welcome-letter.md templates/client-welcome-letter.example.json documents/test/welcome.md --strict
python3 tools/docbuilder/fill_template.py text path/to/template.docx
```
