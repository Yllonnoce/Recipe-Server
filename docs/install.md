# Installing Recipe Library

Works the same on Linux, Windows and macOS. You need Python 3.11–3.13 (3.12 recommended)
and, for the local recipe extraction, [Ollama](https://ollama.com).

```bash
# 1. get the code and a virtual environment
cd HomeServer
uv venv --python 3.12 .venv            # or: python3.12 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[ocr]"                # add ,dev for the test suite

# 2. the headless browser used to turn web pages into PDFs
playwright install chromium            # Linux may also need: playwright install-deps

# 3. the local model that turns page text into ingredients and steps
ollama pull qwen3:8b

# 4. create the config file and library folder, check the environment, run
recipes init
recipes doctor
recipes serve
```

Then open `http://<this-machine>:8000` from any device on the Wi-Fi.

## What lives where

| Thing | Location |
|---|---|
| Config file | `recipes init` prints it. Linux `~/.config/recipelib/config.toml`, macOS `~/Library/Application Support/recipelib/config.toml`, Windows `%APPDATA%\recipelib\config.toml` |
| Library (database, PDFs, thumbnails) | `~/RecipeLibrary` by default (`library_dir` in the config) |
| Inbox folder | `~/RecipeLibrary/inbox` — drop PDFs or photos here |
| Logs | `~/RecipeLibrary/logs/recipelib.log` |

Every config key can be overridden with an environment variable: `RECIPELIB_PORT=8080`,
`RECIPELIB_LIBRARY_DIR=/mnt/big/RecipeLibrary`, `RECIPELIB_PRINTER_ENABLED=false`, and so on.

## Ports and firewall

| Port | Purpose |
|---|---|
| TCP 8000 | the web app |
| TCP 8631 | the virtual printer (IPP) |
| UDP 5353 | mDNS/Bonjour discovery of the printer |

## Running at boot

`recipes service-template systemd`, `launchd` or `windows-task` prints a ready-to-use
service definition with the right paths for this machine.

## Backups

`recipes backup` writes a zip of the database and all PDFs to `~/RecipeLibrary/backups`.
Restoring is unzipping it into a fresh library folder.
