#!/bin/sh
# Start PaperJay as an unprivileged user. Started as root (the default), it first gives the user PUID/PGID
# (default 1000) ownership of /data, so a bind-mounted folder works without manual chown.
set -e
PUID=${PUID:-1000}
PGID=${PGID:-1000}
if [ "$(id -u)" = 0 ]; then
  mkdir -p "$DATA_DIR"
  if [ "$(stat -c %u "$DATA_DIR")" != "$PUID" ]; then
    chown -R "$PUID:$PGID" "$DATA_DIR"
  fi
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
fi
exec "$@"
