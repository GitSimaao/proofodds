#!/usr/bin/env bash
# ProofOdds — turn /opt/proofodds into a git repository that pushes the ledger.
#
#   sudo bash /opt/proofodds/scripts/setup-git.sh git@github.com:USER/proofodds.git
#
# The ledger's independent witness is a commit timestamp on a host we do not
# control. Until this runs, the chain is only as trustworthy as the server it
# sits on — which is to say, as trustworthy as us, which is the thing the whole
# project refuses to ask anyone to assume.
#
# The script refuses to proceed if the secrets file would be committed. That
# check is the reason this is a script and not three lines of instructions: a
# token pushed to a public repository is in the history for ever, and rotating
# it afterwards does not remove it.

set -euo pipefail

APP_DIR=/opt/proofodds
APP_USER=proofodds
REMOTE="${1:-}"

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$1"; }
die()  { printf '\n\033[1;31m==> %s\033[0m\n' "$1" >&2; exit 1; }

# HOME is deliberate: `sudo -u` keeps the CALLING user's HOME, so git would
# look for ~/.ssh in the wrong place and fail in a confusing way.
asuser() { sudo -H -u "$APP_USER" env HOME="$APP_DIR" "$@"; }

[[ $EUID -eq 0 ]] || die "Run as root (or with sudo)."
[[ -n $REMOTE ]] || die "Usage: setup-git.sh git@github.com:USER/proofodds.git"
[[ -d $APP_DIR ]] || die "$APP_DIR not found."

cd "$APP_DIR"

# --------------------------------------------------------------------------- #
say "Checking what would be committed"

grep -qxF '.env' .gitignore 2>/dev/null || die \
  ".gitignore does not exclude .env — refusing to continue.
    Add a line containing exactly '.env' and run again."

grep -qxF 'data/' .gitignore 2>/dev/null || warn \
  "data/ is not ignored. The current season's CSV changes on every run, so it
    would show as modified for ever and bury the ledger commits in noise."

[[ -f .env ]] && chmod 600 .env

# --------------------------------------------------------------------------- #
if [[ ! -d .git ]]; then
  say "Creating the repository"
  asuser git init -q -b main
else
  say "Repository already exists"
fi

asuser git config user.name "ProofOdds"
asuser git config user.email "bot@proofodds.com"
asuser git config commit.gpgsign false

# --------------------------------------------------------------------------- #
say "Staging"
asuser git add -A

# Last line of defence: look at what is actually staged, not at what we think
# .gitignore does.
if asuser git diff --cached --name-only | grep -qx '.env'; then
  asuser git reset -q
  die ".env is STAGED. Nothing was committed. Fix .gitignore and run again."
fi

leaks=$(asuser git diff --cached --name-only \
        | xargs -r -I{} sh -c 'grep -lE "[A-Za-z0-9_-]{28,}" "{}" 2>/dev/null || true' \
        | grep -vE '^(predictions/|data/|.*\.(csv|json))' || true)
if [[ -n $leaks ]]; then
  warn "Long random-looking strings found in: $leaks"
  warn "Check these are not credentials before you push."
fi

echo
asuser git diff --cached --stat | tail -20

# --------------------------------------------------------------------------- #
say "SSH access to the remote"

mkdir -p "$APP_DIR/.ssh"
chown "$APP_USER:$APP_USER" "$APP_DIR/.ssh"
chmod 700 "$APP_DIR/.ssh"

if [[ ! -f $APP_DIR/.ssh/id_ed25519 ]]; then
  asuser ssh-keygen -t ed25519 -f "$APP_DIR/.ssh/id_ed25519" -N '' -q -C "proofodds-deploy"
fi

# Without this, the first push from a shell-less user fails with
# "Host key verification failed" and no way to answer the prompt.
host=$(printf '%s' "$REMOTE" | sed -E 's#^(git@|ssh://git@)([^:/]+).*#\2#')
if ! asuser ssh-keygen -F "$host" -f "$APP_DIR/.ssh/known_hosts" >/dev/null 2>&1; then
  ssh-keyscan -t rsa,ecdsa,ed25519 "$host" 2>/dev/null \
    >> "$APP_DIR/.ssh/known_hosts"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.ssh/known_hosts"
  chmod 600 "$APP_DIR/.ssh/known_hosts"
  echo "    added $host to known_hosts"
fi

asuser git remote remove origin 2>/dev/null || true
asuser git remote add origin "$REMOTE"

# --------------------------------------------------------------------------- #
say "Deploy key — add this to the repository, with WRITE access"
echo
cat "$APP_DIR/.ssh/id_ed25519.pub"
echo
echo "    GitHub: Settings -> Deploy keys -> Add deploy key"
echo "            tick 'Allow write access'"
echo
read -r -p "Press Enter once the key is added (Ctrl-C to stop here)… " _

# --------------------------------------------------------------------------- #
say "Testing the connection"
if ! asuser ssh -o BatchMode=yes -T "git@${host}" 2>&1 | grep -qi 'success\|authenticated'; then
  warn "The remote did not confirm authentication. Push may fail — check the key."
fi

say "First commit and push"
if asuser git diff --cached --quiet; then
  echo "    nothing staged; repository already up to date"
else
  asuser git commit -q -m "ProofOdds: phase 0 — sealed ledger, scorecard, site"
fi
asuser git push -u origin main

chown -R "$APP_USER:$APP_USER" "$APP_DIR/.git"

say "Done"
cat <<DONE

  The ledger now has a witness. From the next run onwards, every sealed entry
  is committed and pushed automatically by scripts/daily.py.

  Two things left to make the site tell the truth about itself:

    1. Put the real URL in $APP_DIR/.env so the footer and the ledger page
       link to the actual repository:
         PROOFODDS_REPO=https://github.com/USER/proofodds

    2. Confirm the next run pushes:
         sudo systemctl start proofodds.service
         sudo -H -u $APP_USER env HOME=$APP_DIR git -C $APP_DIR log --oneline -3

DONE
