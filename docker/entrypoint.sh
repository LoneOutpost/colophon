#!/bin/sh
set -e

# Align the built-in `app` user/group to the caller's ids so files Colophon
# writes into the media volumes are owned by their media account. `-o` allows a
# non-unique id in case one already exists.
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

groupmod -o -g "$PGID" app 2>/dev/null || groupadd -o -g "$PGID" app
usermod -o -u "$PUID" -g "$PGID" app 2>/dev/null || true

# `/config` is the only volume Colophon owns; fix it and never touch the (large)
# media volumes, whose new files are created correctly as the dropped user.
mkdir -p /config
chown -R "$PUID:$PGID" /config

exec gosu app python -m colophon
