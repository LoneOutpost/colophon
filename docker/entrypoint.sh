#!/bin/sh
set -e

# Align the built-in `app` user/group to the caller's ids so files Colophon
# writes into the media volumes are owned by their media account. `-o` allows a
# non-unique id in case one already exists.
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

groupmod -o -g "$PGID" app 2>/dev/null || groupadd -o -g "$PGID" app
usermod -o -u "$PUID" -g "$PGID" app 2>/dev/null || true

# Verify the realignment took before we chown /config and drop into `app`: if the
# user's ids don't match, we'd chown to one id and run as another. Checking the
# result (not usermod's exit code) stays correct even when the ids were already
# right and usermod made no change.
if [ "$(id -u app)" != "$PUID" ] || [ "$(id -g app)" != "$PGID" ]; then
    echo "entrypoint: could not set app to $PUID:$PGID (got $(id -u app):$(id -g app))" >&2
    exit 1
fi

# `/config` is the only volume Colophon owns; fix it and never touch the (large)
# media volumes, whose new files are created correctly as the dropped user.
mkdir -p /config
chown -R "$PUID:$PGID" /config

exec gosu app python -m colophon
