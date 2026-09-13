# Installing Recipe Library

Works the same on Linux, Windows and macOS. Nothing needs to be installed beforehand:
the installer fetches its own Python 3.12, the app, the headless browser and the local
model, and offers to install [Ollama](https://ollama.com) if it is missing.

## One-command install

Linux / macOS (from the project folder, as your normal user, **not** with `sudo`; it asks
for your password itself where needed):

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

- **Settings page** → "Check for updates" / "Update now". The server checks every six hours;
  when a newer version has been published, a banner with an "Update now" button appears at the
  top of every page (dismiss it and it stays hidden until the next version) and a small "update"
  badge shows next to Settings.
- `recipes update` from a terminal (`recipes update --check` only reports).
- Re-running `./install.sh` / `install.bat`, which also upgrades in place.

The update is a fast-forward `git pull`, so local edits to the code are never overwritten;
if you have changed files and the pull cannot fast-forward, the Settings page shows git's
message and nothing else changes.

## Running at boot

```bash
recipes service install     # systemd user unit (Linux), launchd agent (macOS), Task Scheduler (Windows)
recipes service status
recipes service restart
recipes service logs
recipes service remove
```

`./install.sh --service` and `install.bat /service` run `recipes service install` for you.

On Linux and macOS `--service` is a per-user service: it starts when you log in. For a
machine that is a server first (boots to the login window, or you manage it over SSH) use
`./install.sh --system` or `recipes service install --system` instead: a boot-time service
that runs as your user with no login needed. It asks for your password for the few
privileged steps. `recipes service remove` removes whichever kind is installed.
`recipes service-template systemd|launchd|windows-task` prints the definition if you want to
place it yourself (for example as a system-wide unit).

To remove: Linux `systemctl --user disable --now recipelib`; macOS
`launchctl unload ~/Library/LaunchAgents/com.recipelib.server.plist`; Windows
`schtasks /Delete /TN "Recipe Library" /F`. Or run `./uninstall.sh` / `uninstall.bat`, which
remove the service, firewall rules, environment and config; add `--purge` / `/purge` to
also delete the library. Your recipes otherwise stay in `~/RecipeLibrary`.

## Backup server (a second machine that mirrors the first)

Install Recipe Library on a second machine as usual, then in its Settings → "Backup server"
enter the main server's address (for example `http://macm5.local`), click Test, Save, and
"Sync now". From then on it pulls a fresh backup from the main server every 24 hours
(`mirror_interval_hours`) and merges it in, the main server's version winning on conflicts;
`mirror_mode = "replace"` makes it an exact copy instead. Or run `recipes sync-from
http://macm5.local` by hand or from cron. If the main server dies, the backup server already
has everything and can be used directly (clear `mirror_of` so it stops trying to pull).

Anyone on the network can download a backup from a Recipe Library (`/api/backup/latest`),
the same as the Backups page; keep the servers on a private network.

## Backups and restore

A backup is one zip with the database and every PDF and picture. The server makes one
automatically every day and keeps the newest seven in `~/RecipeLibrary/backups`
(`auto_backup_days` and `backup_keep` in the config). Copy them somewhere else as well.

- Settings page → Backups: create, download, upload, and restore.
- `recipes backup` / `recipes restore <zip>`.

Restore has two modes. **Merge** adds recipes the library doesn't have (matched by the
PDF's fingerprint or the source URL) and keeps everything else, so it is also how you
combine two libraries, say the laptop's and the Mac's; tick "let backup win" to have the
backup's version replace matching local recipes. **Replace** wipes the library first and
puts the backup in its place. A backup from an older version is migrated on the way in.

## File sizes

Phone photos and printed web pages are big. By default (`shrink_files = true`) the server
recompresses photos with Pillow (longest side `image_max_px` = 2000, JPEG quality
`jpeg_quality` = 82), downsamples the images inside incoming PDFs to `pdf_image_dpi` = 150
with PyMuPDF, and stores covers and thumbnails as JPEG. The original is kept whenever
shrinking would save less than 10 %. Set `pdf_image_dpi = 0` to leave PDFs untouched, or
`shrink_files = false` to turn it all off. `recipes shrink` (or Settings → Maintenance →
"Shrink stored PDFs and pictures") applies the same to what is already in the library.

## Running the model on another computer

Extraction can use an Ollama on any machine on your network, say a PC with a good graphics
card, while the library itself runs on a small always-on box. In Settings → "Recipe
extraction (Ollama)", enter the other machine's address (`http://<name-or-ip>:11434`),
click Test, then "Pull model there" if it doesn't have the model yet, then Save. Or
`recipes config set ollama_host http://<name-or-ip>:11434`.

Ollama listens only on its own machine by default. On the machine that runs it:

- **Linux (systemd install):** `sudo systemctl edit ollama`, add
  `[Service]` / `Environment="OLLAMA_HOST=0.0.0.0"`, then `sudo systemctl restart ollama`.
  Snap install: `sudo snap set ollama host=0.0.0.0`.
- **macOS (Ollama app):** `launchctl setenv OLLAMA_HOST "0.0.0.0"`, then quit and reopen
  Ollama from the menu bar.
- **Windows (Ollama app):** Settings → System → About → Advanced system settings →
  Environment Variables → add `OLLAMA_HOST` = `0.0.0.0`, then quit Ollama from the tray and
  start it again.

Open TCP port 11434 on that machine's firewall for your private network.

**macOS as the client (the server runs on a Mac and Ollama elsewhere):** macOS's Local
Network privacy blocks programs that haven't been allowed from reaching other devices, and
the app's Python shows "No route to host" while `curl` works. Allow it under System
Settings → Privacy & Security → Local Network ("python3"). If it isn't listed, run
`sudo tccutil reset LocalNetwork`, then trigger the prompt with
`.venv/bin/python -c "import httpx; print(httpx.get('http://<ollama-host>:11434/api/version').text)"`
and click Allow. Restart the service afterwards. "Find Ollama on
my network" in Settings scans the local network for machines that answer on that port, and
Settings → Network (or `recipes scan`, optionally `--port 1234`) finds Ollama hosts, other
Recipe Library servers and their printers in one go.

## Memory and the model

The server loads the model into memory at startup and keeps it there
(`llm_keep_loaded = true`), so captures never wait for a 5 GB load; the first capture after
a restart used to stall a small Mac for a minute or two. That costs about 6 GB of RAM while
the server runs. On a machine that is short of memory set `llm_keep_loaded = false` (the
model then unloads 30 minutes after the last capture) or use a smaller model:
`ollama pull qwen3:4b` and `recipes config set ollama_model qwen3:4b`.

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
