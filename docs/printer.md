# The virtual printer

Recipe Library shows up on the home network as a printer called **Recipe Library**.
Print a recipe web page to it from any device and it lands in the library as a PDF,
then gets its ingredients and steps extracted.

## How it works

- A small IPP server (the same protocol every modern network printer speaks) listens on
  TCP port 8631. It runs inside `recipes serve`; no CUPS, drivers or admin rights needed.
- It is advertised with mDNS/DNS-SD (Bonjour) as `_ipp._tcp` with the `_universal`
  subtype AirPrint looks for, so iPhones, iPads, Macs, Windows 10/11, Android and Linux
  discover it automatically. The printer's own host name is `recipe-library.local`.
- It accepts PDF, Apple URF raster, PWG raster, JPEG and PNG. Raster jobs are converted
  to an image PDF and run through OCR. Android and most iOS/macOS jobs arrive as PDF.
- The print job's name (usually the web page's title) becomes the recipe's initial title,
  and the URL that browsers put in the print footer is picked up when present.
- A job is reported "completed" to the device as soon as the file is on disk; the slow
  work (OCR, extraction) happens afterwards in the background.

## Adding it by hand

If a device doesn't list it, add a printer by address:

```
ipp://<server-ip>:8631/ipp/print
```

macOS: System Settings → Printers → Add → IP, protocol "Internet Printing Protocol – IPP",
queue `ipp/print`, use "Generic PostScript" or "AirPrint" as the driver.
Windows: Settings → Printers → Add device → "The printer that I want isn't listed" →
"Select a shared printer by name" and paste `http://<server-ip>:8631/ipp/print`.
Linux (CUPS): `lpadmin -p RecipeLibrary -E -v ipp://<server-ip>:8631/ipp/print -m everywhere`.

## Testing

- `recipes printer-test` sends a sample PDF from the command line.
- `tests/e2e/run_ipptool.sh` runs CUPS' `ipptool` conformance tests (`apt install cups-ipp-utils`).
- `recipes serve --dump-ipp ~/ipp-dumps` writes every request a device sends as raw bytes
  plus a readable summary — the way to debug a phone that refuses to print.

## macOS

On a Mac the printer is announced through the system's own Bonjour (`dns-sd`), so it is
not affected by macOS's Local Network privacy setting, which can silently block background
programs from sending on the network. The SRV record uses the Mac's own name
(`macm5.local`).

## Linux and avahi

python-zeroconf coexists with avahi-daemon in practice. If discovery ever fails on a Linux
host, `recipes service-template avahi` prints an avahi service file you can drop into
`/etc/avahi/services/` so avahi advertises the printer instead.

## Configuration

```toml
printer_enabled = true
printer_name = "Recipe Library"    # what devices show
printer_location = "Kitchen"
ipp_port = 8631
paper = "letter"                   # default media, "a4" elsewhere
```
