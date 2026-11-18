#!/usr/bin/env bash
# Install a PaperJay release next to the current one and switch to it. Run as root INSIDE the container:
#   bash update.sh <tag|branch|path/to/source.tar.gz>
# Releases live in /opt/paperjay/releases/<version>-<time>/ (each with its own Python venv);
# /opt/paperjay/current points at the running one and /opt/paperjay/previous at the one before, so
# going back is just moving a link. The database is backed up first. Prints BACKUP=<file> and RELEASE=<dir>.
# For a private repository, put a GitHub token in /etc/paperjay/github-token.
set -euo pipefail
REF=${1:?usage: update.sh <tag|branch|source.tar.gz>}
ROOT=/opt/paperjay
ENVFILE=/etc/paperjay/env
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if [ -f "$REF" ]; then
  cp "$REF" "$WORK/src.tgz"
else
  [[ "$REF" =~ ^[A-Za-z0-9._/-]{1,100}$ ]] || { echo "bad ref: $REF" >&2; exit 1; }
  AUTH=()
  [ -s /etc/paperjay/github-token ] && AUTH=(-H "Authorization: Bearer $(cat /etc/paperjay/github-token)")
  echo "==> downloading $REF"
  curl -fsSL "${AUTH[@]}" -o "$WORK/src.tgz" "https://api.github.com/repos/3g15h1/paperjay/tarball/$REF"
fi
tar -xzf "$WORK/src.tgz" -C "$WORK"
SRC=$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n1)
[ -f "$SRC/paperjay/__init__.py" ] || { echo "the download doesn't contain PaperJay" >&2; exit 1; }
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/paperjay/__init__.py")
NAME="$VERSION-$(date +%Y%m%d%H%M%S)"
DEST=$ROOT/releases/$NAME

echo "==> installing $VERSION"
mkdir -p "$ROOT/releases"
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install -q --disable-pip-version-check "$SRC"
cp -a "$SRC/deploy" "$DEST/deploy"
# 0.5.0: the built-in search model (about 130 MB, once; later updates find it in DATA_DIR/models)
if id paperjay >/dev/null 2>&1; then
  echo "==> getting the built-in search model"
  runuser -u paperjay -- sh -c "set -a; [ -f '$ENVFILE' ] && . '$ENVFILE'; exec '$DEST/venv/bin/paperjay' fetch-models" \
    || echo "    couldn't download it now; PaperJay retries when it first needs it"
fi

if [ -x "$ROOT/current/venv/bin/paperjay" ] && [ -f /var/lib/paperjay/paperjay.db ]; then
  echo "==> backing up your documents' database"
  BACKUP=$(runuser -u paperjay -- sh -c "set -a; . '$ENVFILE'; exec '$ROOT/current/venv/bin/paperjay' backup" | tail -n1)
  echo "BACKUP=$BACKUP"
fi

# 0.5.3: PaperJay itself reads the token (only to see whether a newer release is out)
if [ -s /etc/paperjay/github-token ] && id paperjay >/dev/null 2>&1; then
  chgrp paperjay /etc/paperjay/github-token && chmod 640 /etc/paperjay/github-token
fi
install -m 644 "$SRC/deploy/paperjay.service" "$SRC/deploy/paperjay-update.service" "$SRC/deploy/paperjay-update.path" /etc/systemd/system/
install -m 755 "$SRC/deploy/paperjay-update" /usr/local/sbin/paperjay-update
if [ -L "$ROOT/current" ]; then ln -sfn "$(readlink "$ROOT/current")" "$ROOT/previous"; fi
ln -sfn "$DEST" "$ROOT/current.new" && mv -T "$ROOT/current.new" "$ROOT/current"
chown -R root:root "$DEST" && chmod -R a+rX "$DEST"
# 0.2.0: PaperJay's own HTTPS (for the live camera scanner) on the standard port in the container
if [ -f "$ENVFILE" ] && ! grep -q '^HTTPS_PORT=' "$ENVFILE"; then
  printf '# https for the live camera scanner (see Settings → Secure address); "off" to disable\nHTTPS_PORT=443\n' >> "$ENVFILE"
fi
systemctl daemon-reload
systemctl enable -q --now paperjay-update.path
systemctl enable -q paperjay
echo "==> restarting PaperJay"
systemctl restart paperjay
echo "RELEASE=$DEST"

# keep the current, the previous and the two newest others
keep=$(printf '%s\n%s\n' "$(readlink -f "$ROOT/current")" "$(readlink -f "$ROOT/previous" 2>/dev/null || true)")
ls -1dt "$ROOT"/releases/* | tail -n +5 | while read -r old; do
  grep -qxF "$old" <<<"$keep" || rm -rf "$old"
done
