#!/usr/bin/env bash
# Recipe Library installer for Linux and macOS.
#
#   ./install.sh                 install into ./.venv, set up config + library, pull model
#   ./install.sh --service       ...and start at login (systemd user unit / launchd agent)
#   ./install.sh --no-model      skip the Ollama model download
#   ./install.sh --no-browser    skip the Chromium download (URL capture disabled until you run it)
#
# Nothing is installed system-wide except (optionally, with your sudo password) the
# Chromium shared libraries on Linux, Ollama, and a firewall rule.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
OS="$(uname -s)"
SERVICE=0; MODEL=1; BROWSER=1
for a in "$@"; do
  case "$a" in
    --service) SERVICE=1 ;;
    --no-model) MODEL=0 ;;
    --no-browser) BROWSER=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- uv + python
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! have uv; then
  say "Installing uv (Python manager, into ~/.local/bin)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
have uv || { warn "uv did not install; see https://docs.astral.sh/uv/"; exit 1; }

say "Creating the Python 3.12 environment in $HERE/.venv"
uv venv --python 3.12 --allow-existing .venv
PY="$HERE/.venv/bin/python"

say "Installing Recipe Library and its dependencies"
uv pip install --python "$PY" -e ".[ocr]"

# ---------------------------------------------------------------- browser
if [ "$BROWSER" = 1 ]; then
  say "Downloading Chromium for URL capture"
  if [ "$OS" = "Linux" ]; then
    if have sudo && sudo -n true 2>/dev/null; then
      "$HERE/.venv/bin/playwright" install --with-deps chromium
    else
      "$HERE/.venv/bin/playwright" install chromium
      warn "Chromium's system libraries may be missing. If 'recipes doctor' or URL capture complains, run:"
      warn "    sudo $HERE/.venv/bin/playwright install-deps chromium"
    fi
  else
    "$HERE/.venv/bin/playwright" install chromium
  fi
fi

# ---------------------------------------------------------------- ollama
if ! have ollama; then
  say "Ollama (local model runner) is not installed"
  if [ "$OS" = "Linux" ]; then
    read -r -p "Install it now with the official script (needs sudo)? [Y/n] " ans
    if [ "${ans:-Y}" != "n" ] && [ "${ans:-Y}" != "N" ]; then
      curl -fsSL https://ollama.com/install.sh | sh
    fi
  else
    if have brew; then
      read -r -p "Install it now with Homebrew? [Y/n] " ans
      if [ "${ans:-Y}" != "n" ] && [ "${ans:-Y}" != "N" ]; then brew install ollama; brew services start ollama || true; fi
    else
      warn "Download it from https://ollama.com/download and run it, then: ollama pull qwen3:8b"
    fi
  fi
fi
if [ "$MODEL" = 1 ] && have ollama; then
  say "Pulling the recipe model (qwen3:8b, about 5 GB, one time)"
  if ! ollama list >/dev/null 2>&1; then
    warn "Ollama is not running; starting it in the background"
    (ollama serve >/dev/null 2>&1 &) ; sleep 3
  fi
  ollama pull qwen3:8b || warn "Model pull failed; run 'ollama pull qwen3:8b' later"
fi

# ---------------------------------------------------------------- config + library
say "Writing the config file and creating ~/RecipeLibrary"
"$HERE/.venv/bin/recipes" init
"$HERE/.venv/bin/recipes" doctor || true

# ---------------------------------------------------------------- firewall (Linux ufw)
if [ "$OS" = "Linux" ] && have ufw && sudo -n ufw status 2>/dev/null | grep -q "Status: active"; then
  say "Opening the web, printer and discovery ports in ufw"
  sudo ufw allow 8000/tcp >/dev/null && sudo ufw allow 8631/tcp >/dev/null && sudo ufw allow 5353/udp >/dev/null || warn "could not change ufw; open TCP 8000, 8631 and UDP 5353 yourself"
fi

# ---------------------------------------------------------------- service
if [ "$SERVICE" = 1 ]; then
  if [ "$OS" = "Linux" ]; then
    say "Installing a systemd user service (starts at login, survives logout)"
    mkdir -p "$HOME/.config/systemd/user"
    "$HERE/.venv/bin/recipes" service-template systemd | sed '/^#/d' > "$HOME/.config/systemd/user/recipelib.service"
    systemctl --user daemon-reload
    systemctl --user enable --now recipelib
    loginctl enable-linger "$USER" 2>/dev/null || warn "run 'sudo loginctl enable-linger $USER' so it keeps running after logout"
    echo "    status:  systemctl --user status recipelib"
    echo "    logs:    journalctl --user -u recipelib -f"
  else
    say "Installing a launchd agent (starts at login)"
    mkdir -p "$HOME/Library/LaunchAgents"
    PLIST="$HOME/Library/LaunchAgents/com.recipelib.server.plist"
    "$HERE/.venv/bin/recipes" service-template launchd | sed '/^<!--/d' > "$PLIST"
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "    stop:    launchctl unload $PLIST"
  fi
fi

# ---------------------------------------------------------------- done
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; [ -n "$IP" ] || IP="$(ipconfig getifaddr en0 2>/dev/null || echo localhost)"
say "Installed."
if [ "$SERVICE" = 1 ]; then
  echo "    The server is running:  http://$IP:8000"
else
  echo "    Start it with:          $HERE/.venv/bin/recipes serve"
  echo "    then open:              http://$IP:8000"
  echo "    (re-run with --service to start it at login automatically)"
fi
echo "    Printer for other devices: 'Recipe Library'  (ipp://$IP:8631/ipp/print)"
echo "    Config: $("$HERE/.venv/bin/python" -c 'from recipelib.config import config_path; print(config_path())')"
