# Device checklist

Tick these off on a fresh install. Each row is a real device printing a recipe web page
to "Recipe Library" and the card appearing on the Add page within a few seconds.

| Device | How to print | Expected format | Status |
|---|---|---|---|
| iPhone / iPad (Safari) | Share → Print → pick "Recipe Library" | PDF (URF for mono/multi-copy) | ☐ |
| Mac (Safari/Chrome) | ⌘P → printer "Recipe Library" | PDF | ☐ |
| Windows 10/11 (Edge/Chrome) | Ctrl+P → "Recipe Library" (installs itself via the IPP class driver) | PDF or PWG raster (log it!) | ☐ |
| Android (Chrome) | ⋮ → Share → Print → "Recipe Library" (Default Print Service) | PDF | ☐ |
| Linux (CUPS) | driverless queue, `lp -d` | PDF | ☐ |

The format each device sent is in `~/RecipeLibrary/logs/recipelib.log` on the line
`printer: job N '...' (<format>, <bytes>) queued`.

Known quirks

- iOS hides printers whose `URF=` TXT record and `urf-supported` attribute differ, or whose
  `UUID` doesn't match `printer-uuid`. Both are generated from one source here.
- Windows caches printer capabilities; after changing the printer's attributes remove and
  re-add it.
- Chrome's print dialog can be slow to show network printers the first time; wait a few seconds.
