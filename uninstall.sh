#!/usr/bin/env bash
# Recipe Library uninstaller for Linux and macOS.
#
#   ./uninstall.sh            stop + remove the service, firewall rules, .venv and config
#   ./uninstall.sh --purge    ...and delete the library (all recipes, PDFs, database) after confirming
#   ./uninstall.sh --nginx    ...and uninstall nginx itself (not just the Recipe Library site); needs sudo
#
# Left alone: uv, Ollama and its models, and Playwright's Chromium cache (~/.cache/ms-playwright).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"
PURGE=0; NGINX=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    --nginx) NGINX=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# Paths before we delete anything. Honour RECIPELIB_CONFIG / RECIPELIB_LIBRARY_DIR
# exactly as the app does, then the config file, then the defaults. Never guess
# from a half-removed install: if we can't tell where the library is, we won't
# offer to delete it.
if [ -n "${RECIPELIB_CONFIG:-}" ]; then CONFIG="$RECIPELIB_CONFIG"
elif [ "$OS" = "Darwin" ]; then CONFIG="$HOME/Library/Application Support/recipelib/config.toml"
else CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/recipelib/config.toml"; fi
LIB=""
if [ -n "${RECIPELIB_LIBRARY_DIR:-}" ]; then LIB="$RECIPELIB_LIBRARY_DIR"
elif [ -f "$CONFIG" ]; then LIB="$(sed -n 's/^library_dir *= *"\(.*\)".*/\1/p' "$CONFIG" | head -1)"; LIB="${LIB/#\~/$HOME}"
elif [ -x "$HERE/.venv/bin/python" ]; then LIB="$("$HERE/.venv/bin/python" -c 'from recipelib.config import get_settings; print(get_settings().library_dir)' 2>/dev/null || true)"
fi

# ---------------------------------------------------------------- service
if [ -x "$HERE/.venv/bin/recipes" ]; then
  "$HERE/.venv/bin/recipes" service remove 2>/dev/null || true
fi
if [ "$OS" = "Linux" ] && [ -f "$HOME/.config/systemd/user/recipelib.service" ]; then
  systemctl --user disable --now recipelib 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/recipelib.service"; systemctl --user daemon-reload 2>/dev/null || true
fi
if [ "$OS" = "Darwin" ] && [ -f "$HOME/Library/LaunchAgents/com.recipelib.server.plist" ]; then
  launchctl unload "$HOME/Library/LaunchAgents/com.recipelib.server.plist" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/com.recipelib.server.plist"
fi
# anything still running from a terminal
pkill -f "\.venv/bin/recipes serve" 2>/dev/null && say "Stopped a running server" || true

# ---------------------------------------------------------------- firewall
if [ "$OS" = "Linux" ] && have ufw && sudo -n ufw status 2>/dev/null | grep -q "Status: active"; then
  say "Removing the ufw rules"
  sudo ufw delete allow 8000/tcp >/dev/null 2>&1; sudo ufw delete allow 8631/tcp >/dev/null 2>&1; sudo ufw delete allow 5353/udp >/dev/null 2>&1
fi

# ---------------------------------------------------------------- nginx
SITE_REMOVED=0
if [ -e /etc/nginx/sites-enabled/recipelib ] || [ -e /etc/nginx/sites-available/recipelib ] || [ -e /etc/nginx/conf.d/recipelib.conf ]; then
  say "Removing the Recipe Library nginx site"
  sudo rm -f /etc/nginx/sites-enabled/recipelib /etc/nginx/sites-available/recipelib /etc/nginx/conf.d/recipelib.conf
  SITE_REMOVED=1
  if [ "$NGINX" != 1 ]; then sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx || true; fi
fi
if [ "$OS" = "Darwin" ] && have brew && [ -e "$(brew --prefix)/etc/nginx/servers/recipelib.conf" ]; then
  say "Removing the Recipe Library nginx site"
  rm -f "$(brew --prefix)/etc/nginx/servers/recipelib.conf"
  SITE_REMOVED=1
  if [ "$NGINX" != 1 ]; then sudo brew services restart nginx 2>/dev/null || true; fi
fi
if [ "$NGINX" = 1 ]; then
  if have nginx; then
    say "Uninstalling nginx"
    if [ "$OS" = "Darwin" ]; then
      sudo brew services stop nginx 2>/dev/null || true; brew services stop nginx 2>/dev/null || true
      brew uninstall nginx || warn "brew could not remove nginx"
    else
      sudo systemctl disable --now nginx 2>/dev/null || true
      if have apt-get; then sudo apt-get remove -y --purge nginx nginx-common nginx-core 2>/dev/null || sudo apt-get remove -y nginx
      elif have dnf; then sudo dnf remove -y nginx
      else warn "remove nginx with your package manager"; fi
    fi
  else
    [ "$SITE_REMOVED" = 1 ] || warn "nginx is not installed; nothing to remove"
  fi
  if [ "$OS" = "Linux" ] && have ufw && sudo -n ufw status 2>/dev/null | grep -q "Status: active"; then sudo ufw delete allow 80/tcp >/dev/null 2>&1 || true; fi
elif [ "$SITE_REMOVED" = 1 ]; then
  echo "    nginx itself is still installed (re-run with --nginx to remove it too)"
fi
# the app was bound to 127.0.0.1 behind the proxy; if the config survives, put it back on the LAN
if [ "$SITE_REMOVED" = 1 ] && [ -x "$HERE/.venv/bin/recipes" ]; then "$HERE/.venv/bin/recipes" config set host 0.0.0.0 >/dev/null 2>&1 || true; fi

# ---------------------------------------------------------------- app + config
if [ -d "$HERE/.venv" ]; then
  say "Removing the Python environment $HERE/.venv"
  rm -rf "$HERE/.venv"
fi
if [ -f "$CONFIG" ]; then
  say "Removing the config file $CONFIG"
  rm -f "$CONFIG"
  rmdir "$(dirname "$CONFIG")" 2>/dev/null || true
fi
find "$HERE/src" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$HERE"/src/*.egg-info 2>/dev/null || true

# ---------------------------------------------------------------- library
if [ "$PURGE" = 1 ]; then
  if [ -z "$LIB" ]; then
    warn "Could not determine the library folder (no config found); not deleting anything."
  elif [ -d "$LIB" ] && [ -f "$LIB/recipes.db" ]; then
    warn "This deletes every recipe, PDF and the database in: $LIB"
    read -r -p "Type the full path above to confirm: " ans
    if [ "$ans" = "$LIB" ]; then rm -rf "$LIB"; say "Library deleted"; else say "Library kept"; fi
  else
    warn "$LIB does not look like a Recipe Library folder (no recipes.db); not deleting it."
  fi
else
  say "Your recipes are untouched in ${LIB:-the library folder}  (re-run with --purge to delete them)"
fi

say "Uninstalled. Still installed and safe to remove yourself if unwanted:"
echo "    uv:        rm -rf ~/.local/bin/uv ~/.local/bin/uvx"
echo "    Chromium:  rm -rf ~/.cache/ms-playwright   (macOS: ~/Library/Caches/ms-playwright)"
echo "    Ollama:    https://github.com/ollama/ollama/blob/main/docs/faq.md  (model: ollama rm qwen3:8b)"
echo "    This folder ($HERE) is just the source code; delete it if you like."
