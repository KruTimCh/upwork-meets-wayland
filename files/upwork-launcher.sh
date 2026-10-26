#!/bin/sh
# Upwork launcher for Wayland sessions — part of the upwork-wayland-fix.
#
# What it does:
#   1. verifies XWayland is available (Upwork renders through it)
#   2. saves the real Wayland socket (WAYLAND_DISPLAY_REAL) for the capture
#      helper, then spoofs an X11 session so the tracker enables its capture
#      path instead of showing "please switch to Xorg"
#   3. LD_PRELOADs the libuwsnap shim (harmless where Chromium strips it —
#      install.sh also installs /etc/ld.so.preload as the fallback)
#   4. applies the Electron flags this app needs:
#      --no-sandbox              the SUID sandbox kills preloaded shims
#      --js-flags=--noexpose-wasm  fixes the renderer crash loop
#                                (V8 UnwindAndFindHandler CHECK, exitCode 132)
#
# Optional environment:
#   UPWORK_SCREENSHOT_COMMAND   custom capture command, output path appended
#                               (default: the compositor auto-detection in
#                               uwsnap.py — Spectacle/grim/gnome-screenshot/…)

if [ -z "$DISPLAY" ]; then
    echo "upwork-launcher: DISPLAY is empty — XWayland is not available." >&2
    exit 1
fi

if [ -n "$WAYLAND_DISPLAY" ]; then
    WAYLAND_DISPLAY_REAL="$WAYLAND_DISPLAY"
    export WAYLAND_DISPLAY_REAL
fi

# Optional: force a specific capture command (output path is appended):
# export UPWORK_SCREENSHOT_COMMAND="flameshot full -p"

export XDG_SESSION_TYPE=x11
unset WAYLAND_DISPLAY

SHIM="${UWSNAP_SHIM:-$HOME/.local/lib/libuwsnap.so}"
if [ -f "$SHIM" ]; then
    export LD_PRELOAD="$SHIM${LD_PRELOAD:+:$LD_PRELOAD}"
fi

exec "${UPWORK_BIN:-${UPWORK_DIR:-/opt/Upwork}/upwork}" --no-sandbox --js-flags=--noexpose-wasm "$@"
