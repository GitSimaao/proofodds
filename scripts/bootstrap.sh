#!/usr/bin/env bash
# ProofOdds — setup on a Debian 12 / Ubuntu 24.04 server.
#
#   git clone https://github.com/yourname/proofodds /opt/proofodds
#   sudo bash /opt/proofodds/scripts/bootstrap.sh
#
# The script picks a web server rather than assuming one:
#
#   * nginx already running   -> adds ONE server block for proofodds.com and
#                                leaves every other site untouched. This is the
#                                case on a box that already serves something.
#   * nothing on port 80      -> installs Caddy, which handles TLS by itself.
#   * something else on :80   -> stops and tells you, rather than fighting it.
#
# Idempotent: safe to run again after a change. It never edits an existing
# vhost, and it always runs `nginx -t` before reloading.

set -euo pipefail

APP_DIR=/opt/proofodds
APP_USER=proofodds
DOMAIN=proofodds.com

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$1"; }
die()  { printf '\n\033[1;31m==> %s\033[0m\n' "$1" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root (or with sudo)."
[[ -f "$APP_DIR/requirements.txt" ]] || die \
  "No requirements.txt in $APP_DIR — is the project one directory deeper? See README."

# --------------------------------------------------------------------------- #
say "Deciding how to serve the site"

WEB=""
if systemctl is-active --quiet nginx 2>/dev/null; then
  WEB=nginx
  echo "    nginx is running — adding a server block beside your existing sites."
elif systemctl is-active --quiet caddy 2>/dev/null; then
  WEB=caddy
  echo "    Caddy is running — using it."
elif ss -tln 2>/dev/null | grep -qE ':(80|443)\s'; then
  ss -tlnp | grep -E ':(80|443)\s' || true
  die "Something is already listening on 80/443 and it is neither nginx nor Caddy.
    Stop it, or serve the site from it manually with /opt/proofodds/site as the web root."
else
  WEB=caddy
  echo "    Nothing on port 80 — installing Caddy."
fi

# --------------------------------------------------------------------------- #
say "Installing packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates

if [[ $WEB == caddy ]] && ! command -v caddy >/dev/null; then
  say "Installing Caddy"
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
fi

# --------------------------------------------------------------------------- #
say "Service user and Python environment"
id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

mkdir -p "$APP_DIR/site" "$APP_DIR/data" "$APP_DIR/predictions"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 755 "$APP_DIR"          # the web server needs to traverse into site/

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/deploy/.env.example" "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  warn "Created $APP_DIR/.env — fill in the fixtures token before the first real run."
fi

# --------------------------------------------------------------------------- #
say "First build (so the web server has something to serve)"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/daily.py" --build-only --no-git \
  || warn "First build failed — the site directory may be empty for now."

# --------------------------------------------------------------------------- #
if [[ $WEB == nginx ]]; then
  say "Adding the nginx server block"

  mkdir -p /etc/nginx/snippets
  cp "$APP_DIR/deploy/nginx-security-headers.conf" /etc/nginx/snippets/

  if [[ -f /etc/nginx/sites-available/proofodds ]]; then
    warn "/etc/nginx/sites-available/proofodds already exists — leaving it alone."
    warn "Delete it first if you want this script to write a fresh one."
  else
    cp "$APP_DIR/deploy/nginx-proofodds.conf" /etc/nginx/sites-available/proofodds
    ln -sf /etc/nginx/sites-available/proofodds /etc/nginx/sites-enabled/proofodds
  fi

  # Never reload a broken config onto a server that is already serving traffic.
  if nginx -t; then
    systemctl reload nginx
    echo "    nginx reloaded — your other sites were not touched."
  else
    rm -f /etc/nginx/sites-enabled/proofodds
    die "nginx config test failed. The ProofOdds block was disabled again and
    nginx was NOT reloaded, so your existing sites are unaffected."
  fi

  if command -v certbot >/dev/null; then
    say "Certificate"
    echo "    Run this when you are ready (it edits the block in place):"
    echo "      certbot --nginx -d $DOMAIN -d www.$DOMAIN"
  else
    warn "certbot not found — install it with: apt-get install -y certbot python3-certbot-nginx"
  fi

else
  say "Configuring Caddy"
  cp "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
  mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy
  systemctl reload caddy || systemctl restart caddy
  echo "    Caddy will get the certificate on its own once DNS resolves here."
fi

# --------------------------------------------------------------------------- #
say "Timer"
cp "$APP_DIR/deploy/proofodds.service" /etc/systemd/system/
cp "$APP_DIR/deploy/proofodds.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now proofodds.timer

say "Done"
cat <<DONE

  Site root   : $APP_DIR/site
  Web server  : $WEB
  Timer       : systemctl list-timers proofodds.timer
  Logs        : journalctl -u proofodds.service -f

  Still to do:
    1. ${WEB:+$([[ $WEB == nginx ]] && echo "certbot --nginx -d $DOMAIN -d www.$DOMAIN" || echo "nothing — Caddy handles TLS")}
    2. Put a football-data.org token in $APP_DIR/.env  (free, for fixtures)
    3. Add a git remote so the ledger gets an independent timestamp:
         cd $APP_DIR
         sudo -u $APP_USER git remote add origin git@github.com:yourname/proofodds.git

DONE
