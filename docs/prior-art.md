# Prior art: how the other Upwork-on-Wayland fixes work

Reviewed 2026-10-26 by reading the code only (nothing was executed). Facts below
are from each repo's own files; "untested" means the author's README does not
claim it. Credit and thanks to all of them.

| Project | License | Mechanism | Verified on | Notes |
|---|---|---|---|---|
| [MarSoft/upwork-wayland](https://github.com/MarSoft/upwork-wayland) (2022) | none | Python D-Bus service imitating `org.gnome.Shell.Screenshot` + `Mutter.IdleMonitor`; `grim`, idle via `swayidle` | Sway | Original idea. Obsolete for 5.8.x periodic snaps (they use GDK, not D-Bus, under XWayland). |
| [drsh4dow/upwork-wlroots-bridge](https://github.com/drsh4dow/upwork-wlroots-bridge) (2023) | MIT | Rust port of the same D-Bus bridge; `grim`, `swaymsg` for focused window, `swayidle` | wlroots/Sway | Same limitation: needs an Upwork build that uses the GNOME D-Bus path. |
| [muhammad-rafey/upwork-wayland-workaround](https://github.com/muhammad-rafey/upwork-wayland-workaround) (2026) | MIT | same D-Bus bridge in Python + launcher spoofing `XDG_SESSION_TYPE=x11` | README claims GNOME on Fedora/Ubuntu/Arch | `grim` does not work on GNOME (no wlr-screencopy), and the README itself says Sway needs adjustments: treat the tested-on claims with care. |
| [tiesselune/upwork-wayland](https://github.com/tiesselune/upwork-wayland) (2025) | none stated | `LD_PRELOAD` shim replacing `gdk_pixbuf_get_from_window`; runs `UPWORK_SCREENSHOT_COMMAND` (default flameshot) | GNOME 49 | The shim ancestor. Defines the GDK symbol directly, which kills Chromium renderers in our tests. Zeroes `XGetWindowAttributes` after `save_to_callback` to defeat the "enhancing" pass. |
| [niiithish/upwork-wayland-hyprland](https://github.com/niiithish/upwork-wayland-hyprland) (2026) | attribution note (source says GPL-3.0 via tiesselune) | tiesselune's shim with `grim`; `hyprctl activewindow -j` for window title/pid | Omarchy/Hyprland | Fixed `/tmp` path, no per-output capture, no scaling. |
| [kellpossible/upwork-wayland](https://github.com/kellpossible/upwork-wayland) (2026) | MIT | Rust launcher: `WAYLAND_DISPLAY=""` + `XDG_SESSION_TYPE=x11`, `LD_PRELOAD` shim replacing the GDK call with an xdg-desktop-portal screenshot; also serves the GNOME screenshot and idle D-Bus interfaces; idle via `ext_idle_notify_v1` | **KDE Plasma 6.6 / Fedora 44** | Documents the `isWayland` probe (`wl_display_connect` cached once). Does not handle all-screens `desktopCapturer` overwrite, the renderer crash, or input counts. Self-described as "mostly vibe-coded". |
| [badereddine-mechergui/upwork-wayland-screenshot-fix](https://github.com/badereddine-mechergui/upwork-wayland-screenshot-fix) (2026) | MIT | patches `app.asar` (`main.js`): the Wayland branch runs a portal-screenshot helper; wraps `getInputInfo()` with an evdev keyboard/mouse counter daemon | **Upwork 5.8.0.35 on Ubuntu 26.04 GNOME** | Only other project verified on this exact Upwork version. Needs `sudo`, `input` group, pip/npm installs. |

## What each approach solves

| Problem | This project | Others |
|---|---|---|
| Black snap from the XWayland root (`gdk_pixbuf_get_from_window`) | refill at the exact call, renamed import (no symbol shadowing) | tiesselune/niiithish/kellpossible: replace the call; badereddine: bypass via `isWayland()` branch |
| All-screens snaps overwritten by black `desktopCapturer` image | `app.asar` one-line patch | not addressed (tiesselune/niiithish's `XGetWindowAttributes` 0x0 trick targets a different second pass) |
| Renderer crash loop | `--js-flags=--noexpose-wasm` | not addressed |
| Keyboard/mouse **activity counts** | **not yet** (log shows 0) | badereddine: evdev daemon |
| Idle time | not yet | kellpossible (`ext_idle_notify_v1`), swayidle-based bridges |
| `isWayland` probe in native code | spoof `XDG_SESSION_TYPE`, unset `WAYLAND_DISPLAY` | kellpossible: use an **empty** `WAYLAND_DISPLAY` (unset lets libwayland fall back to `wayland-0`) |

## Worth adopting next (roadmap)

1. **Activity counting** (biggest gap): keyboard/mouse counts and idle on Wayland.
   Options: an evdev counter like badereddine's (accurate, but needs the `input`
   group: make it opt-in and explain the privacy tradeoff) or compositor idle
   protocols (`ext_idle_notify_v1`).
2. **Portal screenshot backend**, non-interactive, with a pre-subscribed
   `Response` signal and a registered app id, as the default for KDE/GNOME.
3. **Sway/Hyprland backends done properly:** private temp files in
   `$XDG_RUNTIME_DIR`; active output via `swaymsg -t get_outputs` /
   `hyprctl monitors -j` then `grim -o <name>`; all-screens via plain `grim`;
   window metadata via `hyprctl activewindow -j` / `swaymsg -t get_tree` (parse the
   JSON, don't regex it); a failed or empty capture returns the original frame
   rather than uploading garbage.
4. **Test `WAYLAND_DISPLAY=""`** (empty) instead of unset for the native probe.
5. Launcher hygiene: tie the helper's lifetime to Upwork (`PR_SET_PDEATHSIG`).

Licensing note: this repo is GPL-3.0-or-later. Ideas are free to reuse; copy code
only from MIT projects with attribution, and write the small `grim`/`hyprctl`
calls fresh rather than lifting them from repos without a clear license.
