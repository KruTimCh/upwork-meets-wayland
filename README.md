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
| KDE Plasma | Spectacle | ✅ tested end-to-end (active screen and all screens) |
| GNOME | gnome-screenshot / Shell D-Bus | ✅ approach corroborated by two prior projects |
| sway / Hyprland / river / labwc / Wayfire | grim | ⚠️ backend documented, untested here |
| COSMIC | xdg-desktop-portal Screenshot | ⚠️ untested here |
| any, via portal | xdg-desktop-portal Screenshot | ⚠️ fallback; may ask consent once |
| real X11 session | unnecessary — Upwork works natively | — |

Validate on your system with `~/.local/bin/uwsnap.py --test` and open an issue
with the output.

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

## License

GPL-3.0-or-later. Standing on the shoulders of
[MarSoft](https://github.com/MarSoft/upwork-wayland),
[muhammad-rafey](https://github.com/muhammad-rafey/upwork-wayland-workaround)
and [tiesselune](https://github.com/tiesselune/upwork-wayland).

Unofficial project. Comply with Upwork's terms of service.
