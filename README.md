# upwork-wayland-fix

**Upwork time-tracker screenshots and stable rendering on Wayland** — for KDE
Plasma, GNOME, COSMIC and wlroots compositors (sway/Hyprland/river/labwc/
Wayfire). No more black screenshots, no "please switch to Xorg", no
crash-looping app window.

Works with the official Upwork desktop app 5.8.x (Electron 18). Nothing in
Upwork is replaced — one import string inside its native module is renamed so
its capture call lands in a shim that substitutes real screen pixels.

```
Upwork snap:  gdk_pixbuf_get_from_window(XWayland root)  →  all-black frame
                    │  (every Wayland compositor, no exceptions)
                    ▼
libuwsnap.so (preloaded): detects the black result
                    │  real capture via your compositor's tool
                    ▼
uwsnap.py → Spectacle / gnome-screenshot / grim / xdg-desktop-portal
                    │  rescaled to the X11 layout, active screen cropped
                    ▼
Upwork encodes and uploads REAL pixels
```

## Status

| Compositor | Capture backend | Status |
|---|---|---|
| KDE Plasma 6 | Spectacle | ✅ **tested end-to-end** by the author (active screen + all screens) |
| GNOME | gnome-screenshot / portal | 🟡 the approach is proven by the community projects credited below (tested there on Fedora, Ubuntu 24.04+, Arch); **this tool's GNOME backend is untested — testers wanted** |
| Arch Linux (any DE) | per-DE backend | 🟡 community-tested approach (see credits); testers wanted |
| sway / Hyprland / river / labwc / Wayfire | grim | ⚠️ implemented, untested — testers wanted |
| COSMIC | xdg-desktop-portal Screenshot | ⚠️ implemented, untested — testers wanted |
| any, via portal | xdg-desktop-portal Screenshot | ⚠️ fallback; may ask consent once |
| real X11 session | unnecessary — Upwork works natively | — |

### Help wanted: test your compositor

Run `~/.local/bin/uwsnap.py --test` and open an issue with the output, your
distro, compositor and Upwork version (and `/tmp/uwsnap.log` after one snap).
I adapt the backends from your logs. Every report moves a ⚠️ to a ✅.

## Why Upwork fails on Wayland (reverse engineering)

The official app's snap module (`uta_native.node`) captures through GDK:

```
uta::DesktopSessionAgent::SnapWholeDesktop()
└─ gdk_pixbuf_get_from_window(root_window)   ← all-black on Wayland
```

Under Wayland no compositor draws into the XWayland root window (that is a
compositor-independent property of XWayland), so the pixbuf comes back black
and gets uploaded. The app's own gate (`isWayland()`) normally refuses to snap
at all and shows "Screenshots are not supported on Wayland" — spoofs that
claim to be X11 make it snap into the black.

Separately, the app's renderers crash-loop on some stacks with a V8 CHECK
(`UnwindAndFindHandler`, SIGILL / "bad IPC message") — fixed by disabling
WebAssembly (`--js-flags=--noexpose-wasm`).

Prior art that independently arrived at parts of this:
[MarSoft/upwork-wayland](https://github.com/MarSoft/upwork-wayland),
[muhammad-rafey/upwork-wayland-workaround](https://github.com/muhammad-rafey/upwork-wayland-workaround),
[tiesselune/upwork-wayland](https://github.com/tiesselune/upwork-wayland)
(AUR `upwork-wayland`). This project combines the GDK-call replacement with a
black-detect refill, active-screen selection, multi-compositor capture
backends and a crash fix — see **docs/reverse-engineering.md** for the full
story including the bisection that found the renderer-kill.

## Install

```bash
git clone https://github.com/<you>/upwork-wayland-fix
cd upwork-wayland-fix/files
./install.sh
```

The installer: builds the shim (fetches X11 headers user-locally if missing),
installs the preload (`/etc/ld.so.preload` — survives Chromium's environment
sanitization), patches the one import string inside `/opt/Upwork/uta_native.node`
(a `.bak` backup is kept), applies the `app.asar` patch that stops Upwork replacing the good snap with a black `desktopCapturer` image (all-screens mode), installs the helper + launcher + desktop entry, and
enables active-screen mode.

Then **fully close and restart the Upwork app**. The first snap of each
10-minute block takes ~2 s longer (the real capture happens then).

### Capture modes

- **Active screen** (default): the focused monitor fills the whole snap —
  readable on any monitor size.
- **All screens**: `rm ~/.config/uwsnap-active-only`
- Upwork's own "active screen / all screens" setting is respected either way;
  the shim corrects GDK's monitor pick (GDK cannot see Wayland focus).

### Custom capture command

```bash
export UPWORK_SCREENSHOT_COMMAND="flameshot full -p"   # output path appended
```

## Uninstall

```bash
./files/uninstall.sh
```

Restores the original `uta_native.node` from backup, removes the preload and
all installed files.

## Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Black thumbnail, `refill done` in log | `uwsnap.py --test` | backend capture failing — install the tool for your DE |
| Black app window | `grep -c 'Terminating renderer'` in the app's stderr log | renderer loop — verify the launcher flags are applied (`--noexpose-wasm`) |
| "please switch to Xorg" placeholder | launcher used? | launch via `~/.local/bin/upwork`, not the stock binary |
| Snap shows wrong monitor | active output detection | per-DE active-window support — see docs; report your compositor |
| All-screens snap black, native `.png` replaced by a stitched `.jpg` | app log `screenSnap.filename` | run `sudo files/patch-asar.sh` (disables Upwork's desktopCapturer replacement) |
| App never snaps (placeholder) | `ps eww $(pgrep -x upwork \| head -1) \| grep -o XDG_SESSION_TYPE=x11` | session spoof missing — use the launcher |

## Docs

- [docs/reverse-engineering.md](docs/reverse-engineering.md) — how the snap
  flow was mapped from the binary, and every dead end (so you don't repeat it)
- [docs/debugging-chronicle.md](docs/debugging-chronicle.md) — the full
  debugging story: what was tried, what worked, and the open all-screens
  investigation with tomorrow's procedure

## Purpose and disclaimer

This workaround exists for **one reason**: so that the official Upwork
time-tracker can take the **real screenshots of your real screen** on Wayland,
where it otherwise captures black frames or refuses to run. It does not
generate, edit or fake any content, and it must never be used to misrepresent
your work or activity. Use it honestly and comply with Upwork's terms and your
contract with your client.

This is an unofficial, community project, **not affiliated with or endorsed by
Upwork**. It modifies local copies of files of the Upwork desktop app on your
machine (backups are kept; `uninstall.sh` restores them) and ships no Upwork
code. You use it **at your own risk**. Any modification of this code, by anyone,
is the responsibility of whoever makes it. No warranty (see the GPL-3.0
license): the author is not responsible for data loss, account actions by
Upwork, or any other consequence of using or modifying this software.

## Credits

This project combines and builds on the work of others. Thank you:

- **[MarSoft/upwork-wayland](https://github.com/MarSoft/upwork-wayland)** — the
  original idea: bridge Upwork's GNOME screenshot D-Bus interface to `grim`
  for Sway / wlroots.
- **[muhammad-rafey/upwork-wayland-workaround](https://github.com/muhammad-rafey/upwork-wayland-workaround)**
  (MIT) — GNOME Wayland D-Bus bridge plus the `XDG_SESSION_TYPE=x11` launcher
  trick; tested on Fedora 43+, Ubuntu 24.04+ and Arch.
- **[tiesselune/upwork-wayland](https://github.com/tiesselune/upwork-wayland)**
  (AUR `upwork-wayland`) — replacing Upwork's GDK capture calls with a shim
  library and an external capture command; the "enhancing snapshot" handling
  concept.

What this project adds: black-frame detection with refill at the exact GDK call
Upwork uses, multi-compositor capture backends, active-screen selection, the
renderer crash-loop fix, and the fix for Upwork's `desktopCapturer`
replacement that blacks out all-screens snaps. Full reverse-engineering notes
are in `docs/`.

## License

GPL-3.0-or-later. Standing on the shoulders of
[MarSoft](https://github.com/MarSoft/upwork-wayland),
[muhammad-rafey](https://github.com/muhammad-rafey/upwork-wayland-workaround)
and [tiesselune](https://github.com/tiesselune/upwork-wayland).

Unofficial project. Comply with Upwork's terms of service.
