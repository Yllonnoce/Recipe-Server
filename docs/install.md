# Installing Recipe Library

Works the same on Linux, Windows and macOS. Nothing needs to be installed beforehand:
the installer fetches its own Python 3.12, the app, the headless browser and the local
model, and offers to install [Ollama](https://ollama.com) if it is missing.

## One-command install

Linux / macOS (from the project folder):

```bash
./install.sh --service        # --service: start at login; --no-model to skip the 5 GB model
```

Windows (double-click `install.bat`, or from a command prompt in the project folder):

```bat
install.bat /service /firewall
```

`/firewall` adds the Windows Firewall rules other devices need (asks for admin). On Linux
the installer opens the ports in ufw when ufw is active; on macOS the built-in firewall
prompts the first time the server starts.

Re-running the installer is safe: it upgrades the app in place and keeps your library.

### Port 80 (so the address is just `http://recipes.local`)

Linux / macOS: add `--nginx`. The installer installs nginx if needed and writes a site that
forwards port 80 to the app, with `client_max_body_size 500m` and ten-minute timeouts so big
PDF and photo uploads and slow captures get through. The app itself then listens only on
127.0.0.1:8000 behind the proxy. `recipes service-template nginx` prints the site file if
you want to place it yourself.

Windows: add `/port80`. Windows lets a normal user bind port 80, so no proxy is needed; the
app simply listens there (and `/firewall` opens it).

To take it away again: `./uninstall.sh` removes the Recipe Library site and leaves nginx
installed; `./uninstall.sh --nginx` uninstalls nginx as well.

Any setting can be changed later with `recipes config set <key> <value>`, e.g.
`recipes config set port 80`, then restart the server.

## Manual install

If you'd rather do it by hand:

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

## Updating

Install from a git clone and updates are one step:

```bash
git clone https://github.com/Yllonnoce/Recipe-Server.git HomeServer && cd HomeServer
./install.sh --service            # first time
```

Later, any of these pulls the newest version, installs new dependencies, runs database
migrations and restarts the server:

- **Settings page** → "Check for updates" / "Update now". A small "update" badge appears next
  to Settings in the header when a newer version has been published (checked once a day).
- `recipes update` from a terminal (`recipes update --check` only reports).
- Re-running `./install.sh` / `install.bat`, which also upgrades in place.

The update is a fast-forward `git pull`, so local edits to the code are never overwritten;
if you have changed files and the pull cannot fast-forward, the Settings page shows git's
message and nothing else changes.

## Running at boot

`./install.sh --service` (Linux: systemd user unit, macOS: launchd agent) and
`install.bat /service` (Windows: Task Scheduler at login, no window) set this up. To do it by
hand, `recipes service-template systemd`, `launchd` or `windows-task` prints a ready-to-use
definition with the right paths for this machine.

To remove: Linux `systemctl --user disable --now recipelib`; macOS
`launchctl unload ~/Library/LaunchAgents/com.recipelib.server.plist`; Windows
`schtasks /Delete /TN "Recipe Library" /F`. Or run `./uninstall.sh` / `uninstall.bat`, which
remove the service, firewall rules, environment and config; add `--purge` / `/purge` to
also delete the library. Your recipes otherwise stay in `~/RecipeLibrary`.

## Backups

`recipes backup` writes a zip of the database and all PDFs to `~/RecipeLibrary/backups`.
Restoring is unzipping it into a fresh library folder.

## HTTPS (optional)

Browsers only allow the "keep the screen awake" feature in cook mode on secure pages.
On the LAN a self-signed certificate is enough:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=recipes.local" \
  -keyout ~/RecipeLibrary/key.pem -out ~/RecipeLibrary/cert.pem
```

then in the config file:

```toml
ssl_certfile = "/home/you/RecipeLibrary/cert.pem"
ssl_keyfile = "/home/you/RecipeLibrary/key.pem"
```

and open `https://<server>:8000`, accepting the certificate once on each device. Without
HTTPS cook mode falls back to a silent looping video, which keeps most tablets awake.
