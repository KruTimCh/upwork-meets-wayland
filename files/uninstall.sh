#!/bin/bash
# upwork-wayland-fix uninstaller — restores Upwork to stock.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== removing preload entry"
if [ -f /etc/ld.so.preload ]; then
    sudo sed -i '\|libuwsnap|d' /etc/ld.so.preload
fi

echo "== restoring uta_native.node from backup"
NODE=/opt/Upwork/uta_native.node
if [ -f "$NODE.bak" ]; then
    sudo cp "$NODE.bak" "$NODE"
    echo "   restored"
else
    echo "   no backup found (was the fix installed?)"
fi

echo "== removing files"
rm -f "$HOME/.local/lib/libuwsnap.so"
rm -f "$HOME/.local/bin/uwsnap.py"
rm -f "$HOME/.local/bin/upwork"          # only if it is our launcher
rm -f "$HOME/.config/uwsnap-active-only"
rm -f "$HOME/.local/share/applications/upwork.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "== done. Restart the Upwork app."
