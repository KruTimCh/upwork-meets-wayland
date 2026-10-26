#!/bin/bash
# upwork-wayland-fix installer — see README.md
# Usage: sudo not required except where marked. Run from the repo's files/ dir
# (or the repo root; it resolves its own directory).

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "== upwork-wayland-fix installer =="

# ---------------------------------------------------------------- checks
[ -f /opt/Upwork/upwork ] || { echo "WARNING: /opt/Upwork/upwork not found — is the Upwork app installed?"; }
command -v gcc >/dev/null || { echo "ERROR: gcc missing (build-essential)"; exit 1; }
command -v python3 >/dev/null || { echo "ERROR: python3 missing"; exit 1; }
python3 -c "import PIL" 2>/dev/null || { echo "ERROR: python3-pil missing (Pillow)"; exit 1; }

# capture tool per detected desktop (only a warning — the portal fallback may cover it)
DE="${XDG_CURRENT_DESKTOP:-}"; SESSION="${XDG_SESSION_TYPE:-}"
echo "Detected desktop: ${DE:-unknown}, session: ${SESSION:-unknown}"
case "$DE" in
  *KDE*|*Plasma*)  command -v spectacle >/dev/null || echo "WARNING: spectacle missing (KDE capture backend)";;
  *GNOME*|*Unity*) command -v gnome-screenshot >/dev/null || echo "WARNING: gnome-screenshot missing (GNOME capture backend)";;
  *sway*|*Hyprland*|*wayfire*|*river*|*labwc*) command -v grim >/dev/null || echo "WARNING: grim missing (wlroots capture backend)";;
  *) echo "NOTE: no known capture tool enforced — xdg-desktop-portal fallback will be used";;
esac

# ---------------------------------------------------------------- 1. shim
echo "== 1. building the shim"
X11_INC="${X11_INC:-$HOME/.local/include}"
if [ ! -f "$X11_INC/X11/Xlib.h" ]; then
    echo "   X11 headers not in $X11_INC — fetching user-locally (no sudo)"
    mkdir -p "$X11_INC/X11/extensions" /tmp/uwfix-hdr && cd /tmp/uwfix-hdr
    for pkg in libx11-dev x11proto-dev libxext-dev; do
        apt download "$pkg" 2>/dev/null || true
    done
    for f in *.deb; do dpkg -x "$f" /tmp/uwfix-hdr/root; done
    cp -r /tmp/uwfix-hdr/root/usr/include/X11/* "$X11_INC/X11/" 2>/dev/null || true
    cp -r /tmp/uwfix-hdr/root/usr/include/X11/extensions/* "$X11_INC/X11/extensions/" 2>/dev/null || true
    cd "$DIR"
fi
mkdir -p "$HOME/.local/lib" "$HOME/.local/bin"
gcc -shared -fPIC -O2 -o "$HOME/.local/lib/libuwsnap.so" libuwsnap.c \
    -ldl -I "$X11_INC"

# ---------------------------------------------------------------- 2. preload
echo "== 2. installing the preload (system-wide, survives Chromium env stripping)"
if grep -q libuwsnap /etc/ld.so.preload 2>/dev/null; then
    sed -i "s|.*libuwsnap.*|$HOME/.local/lib/libuwsnap.so|" /etc/ld.so.preload
    echo "   updated existing entry"
else
    echo "$HOME/.local/lib/libuwsnap.so" >> /etc/ld.so.preload
    echo "   added to /etc/ld.so.preload"
fi

# ---------------------------------------------------------------- 3. helper + launcher
echo "== 3. installing helper and launcher"
install -m 755 uwsnap.py "$HOME/.local/bin/uwsnap.py"
install -m 755 upwork-launcher.sh "$HOME/.local/bin/upwork"

# ---------------------------------------------------------------- 4. addon patch
echo "== 4. patching uta_native.node (renames the GDK import to the refill)"
NODE=/opt/Upwork/uta_native.node
if [ ! -f "$NODE.bak" ]; then
    sudo cp "$NODE" "$NODE.bak"
    echo "   backup saved: $NODE.bak"
fi
sudo python3 - <<'PY'
data = open("/opt/Upwork/uta_native.node", "rb").read()
old = b"gdk_pixbuf_get_from_window\x00"
new = b"uwsnap_gdk_pixbuf_refill\x00\x00\x00"
if new in data:
    print("   already patched")
elif old in data:
    open("/opt/Upwork/uta_native.node", "wb").write(data.replace(old, new))
    print("   patched")
else:
    raise SystemExit("   ERROR: import string not found — Upwork version changed? Report this.")
PY

# ---------------------------------------------------------------- 4b. asar patch
echo "== 4b. disabling Upwork's desktopCapturer screensnap replacement (all-screens fix)"
sudo sh "$DIR/patch-asar.sh"

# ---------------------------------------------------------------- 5. desktop entry
echo "== 5. desktop entry"
mkdir -p "$HOME/.local/share/applications"
if [ -f /usr/share/applications/upwork.desktop ]; then
    sed 's|^Exec=.*|Exec='"$HOME"'/\.local/bin/upwork %U|' \
        /usr/share/applications/upwork.desktop \
        > "$HOME/.local/share/applications/upwork.desktop"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

# ---------------------------------------------------------------- 6. mode
echo "== 6. active-screen mode"
touch "$HOME/.config/uwsnap-active-only"
echo "   ON (delete ~/.config/uwsnap-active-only for all-screens captures)"

echo
echo "== done. Restart the Upwork app (close it fully first)."
echo "   Self-test:  $HOME/.local/bin/uwsnap.py --test"
