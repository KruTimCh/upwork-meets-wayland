#!/bin/sh
# Disable Upwork's Electron "screensnap enhancement" (needs root).
#
# Upwork's main.js (collectUserActivity) takes the native snap (our refilled
# pixbuf), then calls desktopCapturer.getSources({types:["screen"]}) and, if any
# non-empty thumbnail comes back, REPLACES the native snap with a stitched JPEG.
# Under XWayland desktopCapturer reads the same black root window, so the good
# image is overwritten with black (all-screens mode). In active-screen mode the
# display_id filter matches nothing, so the native snap survives — which is why
# only all-screens broke.
#
# The patch is a same-length byte edit that makes the replacement condition
# constant-false:  if(re.length){const L=yield  ->  if(0.0000000){const L=yield
# desktopCapturer still runs and returns normally; its result is just ignored,
# so Upwork keeps the native snap. (An invalid source type instead makes
# getSources hang and collectUserActivity time out after 30 s — don't.)
# A backup is kept as app.asar.bak; uninstall.sh / `patch-asar.sh --restore`
# puts it back.
set -e
ASAR="${UPWORK_DIR:-/opt/Upwork}/resources/app.asar"

if [ "$1" = "--restore" ]; then
    [ -f "$ASAR.bak" ] || { echo "no backup at $ASAR.bak"; exit 1; }
    cp "$ASAR.bak" "$ASAR"
    echo "restored $ASAR"
    exit 0
fi

[ -f "$ASAR.bak" ] || cp -p "$ASAR" "$ASAR.bak"
python3 -I - "$ASAR" <<'PY'
import sys
p = sys.argv[1]
d = open(p, "rb").read()
old = b'if(re.length){const L=yield'
new = b'if(0.0000000){const L=yield'
assert len(old) == len(new)
if d.count(new) == 1 and d.count(old) == 0:
    print("already patched")
    sys.exit(0)
if d.count(old) != 1:
    sys.exit("unexpected app.asar (restore first?): patch target found %d times" % d.count(old))
open(p, "wb").write(d.replace(old, new))
print("patched")
PY
