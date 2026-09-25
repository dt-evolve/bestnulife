---
name: build-document
description: Build a finished document from a template. Use when the user runs /build-document or asks to fill in, complete, or generate a document from a template file, pasted template, or example document.
argument-hint: <template path> [details or notes]
---

Build a document from a template using the `document-builder` subagent.

1. Work out the template from the arguments: `$ARGUMENTS`. If no template was given, list the files in `templates/` and ask which one to use (or ask the user to paste one).
2. Launch the `document-builder` agent with the template path, every detail the user supplied, and any tone or format instructions.
3. If the agent comes back asking for missing information, ask the user for exactly those items, then send the answers back to the agent.
4. When it finishes, tell the user where the document was saved and point out any sections the agent drafted that they should review.
