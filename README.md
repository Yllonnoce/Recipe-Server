# Recipe Library

A self-hosted recipe library for the family. Print a recipe page from any device to the
**Recipe Library** network printer, paste a URL, upload a PDF or photo, or drop a file in
the inbox folder. The server keeps the PDF as the archive copy, pulls out the ingredients
and steps with a local model (no cloud), and lets everyone on the home Wi-Fi browse,
search, read, cook, plan meals and build a shopping list from any browser.

## Highlights

- **Four ways in**: virtual printer (IPP + Bonjour, works from iOS, Android, Windows, macOS,
  Linux), URL fetch, upload (PDFs and photos), watched inbox folder.
- **PDF reader** with page-flip book, single-page and continuous-scroll layouts, zoom and
  pan, bookmarks, table of contents, fullscreen, and resume-where-you-left-off.
- **Structured recipes** from page markup when available, otherwise from a local Ollama
  model over the page text (with OCR for scans). Everything is editable.
- **Cook mode** for the kitchen tablet: big type, scale servings, tick off steps, timers,
  screen stays awake.
- **Shopping list** that merges ingredients across recipes and sorts by aisle.
- **Meal plan** by week, with a one-click shopping list for the week.
- **Search** across titles, ingredients, tags and the full PDF text.

## Quick start

```bash
./install.sh --service            # Linux / macOS
install.bat /service /firewall    # Windows (or double-click install.bat)
```

The installer fetches its own Python, the app, the browser and the model, then starts
the server at login. Manual steps are in the docs. Update later from the Settings page or
with `recipes update`.

Open `http://<this-machine>:8000`. See [docs/install.md](docs/install.md),
[docs/printer.md](docs/printer.md) and [docs/devices.md](docs/devices.md).

## Layout

```
src/recipelib/
  capture/     job queue, ingest pipeline, sources (url, upload, watch, printer), OCR
  extract/     ingredient parsing, LLM schema + Ollama client
  ipp/         the virtual printer: IPP codec, attributes, ops, server, mDNS, raster decoders
  db/          SQLite schema (migrations), ORM models, FTS5
  domain/      recipes, shopping, meal plan
  web/         FastAPI routes, Jinja templates, static (htmx, PDF.js reader, cook mode)
tests/         unit + web tests; tests/e2e/run_ipptool.sh for the printer
docs/          install, printer, device checklist
```

## Development

```bash
pip install -e ".[ocr,dev]"
pytest
tests/e2e/run_ipptool.sh                  # needs a running server and cups-ipp-utils
recipes serve --dump-ipp ~/ipp-dumps      # capture what a device sends the printer
```
