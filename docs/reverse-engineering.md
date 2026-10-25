# Reverse-engineering notes — how Upwork 5.8.x snaps (and why it fails)

Target: Upwork 5.8.0-35 (Electron 18 / Chromium 100), `uta_native.node`
(not stripped — symbols readable with `nm -C`).

## The snap call chain

Disassembling `uta::DesktopSessionAgent::SnapWholeDesktop(bool, bool)`:

```
uta::GetScreenSnapAsync()                       [Node entry, called by JS]
└─ uta::DesktopSessionAgent::SnapWholeDesktop(bool, bool)
   ├─ uta::DesktopSessionData::define_session_type()
   ├─ branch on DesktopSessionData+0x108:
   │
   │   ├─ nonzero → GDK path:
   │   │   gdk_get_default_root_window()
   │   │   gdk_window_get_width/height/origin()
   │   │   gdk_pixbuf_get_from_window(root, 0, 0, W, H)   ← ALL-BLACK on Wayland
   │   │   uta::FetchPixbufImage(ScreenSnap&, GdkPixbuf*, …)
   │   │
   │   └─ zero → uta::DbusScreenshot::Create() → virtual call
   │            (GNOME / portal path; not taken when the session reads as X11)
   │
   └─ uta::FetchProcessesInfo(), uta::FetchActiveTaskInfo()
```

The JS-side gate that decides "Wayland → show placeholder" is:

```js
isWayland() = !!process.env.WAYLAND_DISPLAY
           || process.env.XDG_SESSION_TYPE === "wayland"
           || utaNative.isWayland()
```

So the launcher spoofs `XDG_SESSION_TYPE=x11` and unsets `WAYLAND_DISPLAY`:
the gate passes and the app takes a *real* capture attempt — which is black,
because `gdk_pixbuf_get_from_window()` reads the **XWayland root window**, and
no Wayland compositor composites into it. This is compositor-independent:
KDE, GNOME, COSMIC and wlroots all behave the same here.

Empirically established (and important):

- The addon makes **no XShmGetImage / XGetImage calls** on this path —
  verified with unbuffered stderr markers on every interposed symbol.
- It makes **no D-Bus calls** during a snap — verified with a full
  `dbus-monitor`. The GNOME-D-Bus-bridge workarounds never get contacted on
  an X11-spoofed session; they solve a different branch.

## The renderer crash (separate bug)

All five observed crashes hit the **same instruction**: a `ud2` (deliberate
CHECK) inside `v8::internal::Isolate::UnwindAndFindHandler()+0xbaa`. V8's
wasm-trap unwinding state machine hits an impossible state → SIGILL →
Electron logs `exitCode 132` → renderer respawns → dies again (~50 s loop) →
black app window.

Fix: launch with `--js-flags=--noexpose-wasm`. Notes:

- the flag name must be exact: `--no-wasm-trap-handler(s)` are *unrecognized*
  by this V8, and an unrecognized `--js-flags` value makes **every renderer
  die in a ~50 s loop** ("Terminating renderer for bad IPC message") — black
  window even while snaps keep working (the main process is unaffected)
- `--noexpose-wasm` removes WebAssembly, hence the trap-handler mechanism
  entirely; verified zero renderer terminations after the swap

## Why /etc/ld.so.preload (the env-sanitization trap)

The natural approach — `LD_PRELOAD` in a launcher — silently fails on some
distros: Chromium re-execs its whole process tree through the SUID sandbox
with a sanitized environment (all `LD_*` stripped). Ubuntu restricts
unprivileged user namespaces → SUID sandbox → strip. Arch allows userns →
no strip, which is why simpler `LD_PRELOAD`-based projects work there.

`/etc/ld.so.preload` is applied by `ld.so` itself and survives everything.
It is also system-wide, so the shim guards itself: `in_upwork()` checks for
`uta_native.node` in `/proc/self/maps` and passes through untouched in every
other process.

## The renderer-killing symbol (bisection story)

With the shim preloaded, renderers still died every ~49 s — but with **zero
calls** into any interposed symbol (unbuffered stderr markers prove it).
Bisection over the shim's contents:

| preload library | renderer deaths in 80 s |
|---|---|
| empty `.so` | 0 |
| helpers only (`in_upwork`, `fill_from_helper`, no exported interposers) | 0 |
| helpers + `gdk_pixbuf_get_from_window` definition | dying |
| full shim (X11 + wl + gdk definitions) | dying |
| X11 definitions only (`XShmGetImage`, `XGetImage`) | 0 |

Conclusion: merely *defining* `gdk_pixbuf_get_from_window` in a preloaded
library poisons Chromium's processes — even never-called. The mechanism is
not fully understood (the function is shadowed for the whole process tree;
possibly a GTK/GDK init path in the browser process or symbol-preemption
side effects). The fix: **don't shadow it.**

Instead, the refill is exported under a neutral name
(`uwsnap_gdk_pixbuf_refill`) and the addon's `.dynstr` import string is
patched in place:

```
gdk_pixbuf_get_from_window\0   →   uwsnap_gdk_pixbuf_refill\0\0\0
```

(24 chars ≤ 26, so the byte-for-byte rename keeps every offset; GTK3 has no
ELF symbol versioning, so no version-record conflicts.) Only the addon binds
the refill; Chromium's own GDK usage — and the renderers — are untouched.

Bisection also found a second death mode: the refill intercepting small
legitimately-black pixbufs (icons/avatars) inside the browser and blocking
its main thread for seconds per capture. Hence the size gate: only grabs
≥800×600 are refilled.

## The "enhancing snapshot" second pass

After encoding, Upwork's JS re-reads the root window size for an
"enhancing snapshot" pass (a second capture at higher quality). With the
refill in place this second pass is another black capture + another ~2 s
refill. Defeated the way tiesselune/upwork-wayland does it: after
`gdk_pixbuf_save_to_callback` is seen, the next `XGetWindowAttributes` on the
root reports 0×0, so Upwork's JS gives up and reuses the already-refilled
first capture.

## Dead ends catalogued (do not repeat)

- Interposing `XShmGetImage`/`XGetImage`: the addon never calls them on the
  GDK path. (Kept in the shim, inert, for other app versions.)
- GNOME `org.gnome.Shell.Screenshot` D-Bus bridge for an X11-spoofed session:
  the addon never makes that D-Bus call on this path.
- Spoofing `XDG_CURRENT_DESKTOP=GNOME`: black-screens Upwork's renderer.
  Only `XDG_SESSION_TYPE`/`WAYLAND_DISPLAY` may be spoofed.
- `--js-flags=--no-wasm-trap-handler(s)`: unrecognized by V8 → unrecognized
  flags cause the renderer death loop by themselves. Accepted: `--noexpose-wasm`.
- `dpkg -S <directory>`: returns "not found" for directories — false-negative
  orphan checks (it deleted a working spare kernel's modules once).
- Debugging note: V8/CHECK crashes put the fatal message as ASCII on the
  stack — `gdb` on a coredump shows it in backtrace frames.
