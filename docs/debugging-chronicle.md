# Debugging chronicle — Upwork screenshots on Wayland

**Machine:** KDE neon (Ubuntu 24.04), Plasma 6.8.1, Wayland session, Intel Iris
Xe iGPU, dual monitor: eDP-1 laptop 1920×1080 (scale 1.25, left) + HDMI-A-1
1680×1050 (scale 1.0, right).
**App:** Upwork desktop 5.8.0-35 (Electron 18 / Chromium 100).
**Debugged:** night of 2026-10-25 → 2026-10-26.

This document is the full account of how each problem was solved and what
remains open, written so another agent (or human) can continue without
repeating the dead ends. The companion doc
[reverse-engineering.md](reverse-engineering.md) has the binary-level
analysis; this one is the operational story.

---

## Phase 0 — context

Upwork's tracker screenshots officially don't work on Wayland ("please switch
to Xorg"). The app gates its own snap logic on an environment check, and on
Wayland the capture itself reads a black frame. Goal: make both capture modes
("active screen" and "all screens") produce real images on KDE Wayland — and
eventually any compositor.

**The winning idea** (validated against three prior projects):
[MarSoft](https://github.com/MarSoft/upwork-wayland),
[muhammad-rafey/upwork-wayland-workaround](https://github.com/muhammad-rafey/upwork-wayland-workaround)
(GNOME D-Bus bridge), and
[tiesselune/upwork-wayland](https://github.com/tiesselune/upwork-wayland)
(AUR `upwork-wayland` — "replaces its GDK API calls"). Ours: interpose the
exact GDK function the app captures through, detect the black result, and
swap in real pixels from a compositor-native capture tool.

---

## Phase 1 — the black screenshot (solved)

**Symptom:** snaps upload as pure black frames.

**Dead ends (do not repeat):**

1. Interposing `XShmGetImage`/`XGetImage` — the addon **never calls them** on
   its real path (verified later with unconditional entry markers).
2. A GNOME `org.gnome.Shell.Screenshot` D-Bus bridge — the addon makes **no
   D-Bus calls** during a snap on an X11-spoofed session (verified with full
   `dbus-monitor`).
3. Spoofing `XDG_CURRENT_DESKTOP=GNOME` — it **black-screens Upwork's
   renderer** entirely. Only `XDG_SESSION_TYPE=x11` + unsetting
   `WAYLAND_DISPLAY` may be spoofed.

**Breakthrough:** stopped guessing, read the binary. `nm -C` on
`uta_native.node` (not stripped) revealed
`uta::DesktopSessionAgent::SnapWholeDesktop`; `objdump` showed its call
sequence — the capture is `gdk_pixbuf_get_from_window(root_window)`, which
reads the **XWayland root window: black on every Wayland compositor, no
exceptions**.

**The fix (three layers):**

1. **Shim** (`libuwsnap.c`): exports `uwsnap_gdk_pixbuf_refill()` — calls the
   real GDK function, samples the returned pixbuf for all-black content, and
   if black, overwrites the pixels with a real capture from `uwsnap.py`
   (Spectacle on KDE) rescaled to the X11 root layout.
2. **Addon patch**: `/opt/Upwork/uta_native.node`'s import string
   `gdk_pixbuf_get_from_window` renamed in-place to
   `uwsnap_gdk_pixbuf_refill` (see Phase 3 for why the rename is mandatory).
   Backup kept at `uta_native.node.bak`.
3. **Helper** (`uwsnap.py`): builds its own session environment (the caller's
   is Chromium-sanitized), captures via the compositor's tool, converts to
   BGRA on stdout. Supports `UPWORK_SCREENSHOT_COMMAND` override and a
   `--test` self-test.

**Result:** refills verified in the log and real images in the work diary.

---

## Phase 2 — the renderer crash loop (solved)

**Symptom:** the whole Upwork window goes black; renderers die in an exact
~49-second cycle. Two signatures observed:

- `Terminating renderer for bad IPC message` (with `--no-sandbox`)
- SIGILL, `exitCode 132`, kernel `trap invalid opcode` at
  `v8::internal::Isolate::UnwindAndFindHandler+0xbaa` (without it)

**Diagnosis:** all crashes hit the *same instruction* — a deliberate `ud2`
(V8 CHECK) inside V8's WebAssembly trap-handler unwinding. The fatal message
was readable as ASCII on the coredump stack via gdb.

**Fix:** launch flag `--js-flags=--noexpose-wasm`. Verified: zero renderer
terminations after the swap.

**Gotcha that cost an hour:** the flag name must be exact.
`--js-flags=--no-wasm-trap-handler(s)` is **unrecognized** by this V8, and an
unrecognized flag makes *every renderer die in the same ~50 s loop* — the
"fix" looked like it didn't work because it silently re-broke everything.

---

## Phase 3 — the shim that killed renderers (solved, subtle)

**Symptom:** with the shim preloaded, renderers died every ~49 s with
"Terminating renderer for bad IPC" — **even though no interposed function was
ever called** (unbuffered stderr markers on every interposition proved zero
calls).

**Bisection** (each variant ~80 s of observation):

| preload library | renderer deaths |
|---|---|
| empty `.so` | 0 |
| helpers only, no exported interposers | 0 |
| helpers + `gdk_pixbuf_get_from_window` **definition** | dying |
| full shim | dying |

**Conclusion:** merely *defining* `gdk_pixbuf_get_from_window` in a preloaded
library poisons every Chromium process — mechanism not fully understood, but
100% reproducible.

**Fix:** the refill is exported under a **neutral name**
(`uwsnap_gdk_pixbuf_refill`) and the addon's import is renamed to bind to it
(Phase 1 layer 2). `gdk_pixbuf_get_from_window` is never shadowed — renderers
stable, refill still reachable by the addon only.

**Also discovered along the way:**

- `LD_PRELOAD` from a launcher is **stripped by Chromium** (it re-execs its
  whole tree with a sanitized environment when it falls back to the SUID
  sandbox — Ubuntu restricts unprivileged user namespaces, which is why plain
  `LD_PRELOAD` shims work on Arch but not Ubuntu). The reliable injection is
  **`/etc/ld.so.preload`**, guarded by `in_upwork()` (checks for
  `uta_native.node` in `/proc/self/maps`) so it stays inert in every other
  process. System services with `ProtectHome` log a harmless "cannot be
  preloaded" error — ignore it.
- The launcher also needs `--no-sandbox` (the SUID sandbox kills preloaded
  shims).

---

## Phase 4 — active-screen mode (solved)

Single-screen snaps (Upwork setting "active screen" or the override flag
`~/.config/uwsnap-active-only`): the helper crops the **focused monitor**
(KWin `activeOutputName` + xrandr layout) and scales it to fill the whole
snap — readable at native resolution. Verified end-to-end; user confirmed.

One subtlety fixed: GDK cannot see Wayland focus, so in Upwork's own
"active screen" mode it may pick the wrong monitor. The helper detects the
true active output and substitutes its content when needed — but only in
forced mode (`ACTIVE_FLAG` present); in all-screens mode per-monitor grabs
are captured faithfully (a bug where the substitution hijacked per-monitor
captures was fixed on 2026-10-26 ~03:40).

---

## Phase 5 — all-screens mode (OPEN — resume here)

**Status:** the pipeline refills correctly (log-proven repeatedly), but the
user reports the uploaded snaps are still black / sometimes missing entirely.

**Verified facts:**

- Whole-root grabs (3600×1080) are detected black and refilled; the capture
  cache (`/tmp/uwsnap-last.png`) contains real both-screens content
  (e.g. 03:16:26, 03:26:18, verified by pixel sampling).
- `screenshotTaken` events fire for some snaps (03:04:20) but **not** for
  others (02:44:32, 02:52:30 — those also match the user's black reports).
- 02:52:30 root cause: transient Spectacle failure, old helper had one
  attempt and no logging → fixed with retries (helper 3×, shim 1×) + file
  logging.
- Renderer terminations: 0 in the current build (the crash fixes hold).

**Current leading hypothesis:** the failure sits **between the refilled
pixbuf and the upload** — either Upwork's "enhancing snapshot" second pass
(which does *not* re-call `gdk_pixbuf_get_from_window` — no second refill
markers) replaces the data, or the encode of the huge 3600×1080 pixbuf
fails. The 02:44/02:52 snaps also show `XGetWP2r1:0:0` /
`XGetWP4r1:0` warnings from `main.uta_native` (its own failing
XGetWindowProperty queries — pre-existing since 00:28, present in working
snaps too, so probably innocent).

**Instrumentation deployed (2026-10-26 ~03:30):** the shim now logs to
`/tmp/uwsnap.log` (file-based, survives stderr redirection) for every
screen-sized grab:

```
HH:MM:SS [uwsnap] gdk grab 3600x1080+0+0: nonblack=X/12 mean=Y -> WAS-BLACK refilling
HH:MM:SS capture via kde -> /tmp/uwsnap-last.png
HH:MM:SS [uwsnap] gdk refill done
HH:MM:SS [uwsnap] refilled pixbuf dumped to /tmp/uwsnap-refilled-HHMMSS.png
```

The **dumped PNG is the exact pixel data the app received** — visual proof at
the boundary.

### Tomorrow's procedure

1. Start Upwork (any normal way — all entry points run the launcher), enable
   tracking, set **all screens**.
2. Let one snap cycle land (~10 min).
3. `cat /tmp/uwsnap.log | tail -20`
4. Decision tree:
   - Log shows `gdk grab … WAS-BLACK refilling` + `refill done` + a dumped
     PNG that is **real** → the pixbuf the app received was real; the black
     enters **downstream** (encode/upload). Next: interpose
     `gdk_pixbuf_save_to_callback` as a logger (safe now — the toxic shadow
     is gone) and dump what actually gets encoded.
   - Dumped PNG is **black** → the refill fails inside the dock-launched
     context → compare with a launcher-launched instance; suspect
     environment (the helper rebuilds its env, but check
     `WAYLAND_DISPLAY_REAL` propagation).
   - Log shows `gdk grab … looks-real passthrough` → the "black" frame is
     actually non-black garbage; loosen/adjust the detection (log the sample
     values, decide a real threshold).
   - No `gdk grab` line at all → the snap didn't reach the GDK path (the
     DbusScreenshot branch) — check the app log for the snap time and
     consider intercepting `uta::DbusScreenshot::Create`'s D-Bus call.
5. ⚠️ `/tmp` does not survive a reboot — copy evidence into this repo's
   `evidence/` folder before rebooting.

---

## Current deployed state (this machine)

| Piece | Location | Notes |
|---|---|---|
| Shim (source / built) | `~/upwork-wayland-fix/files/libuwsnap.c` → `~/.local/lib/libuwsnap.so` | preloaded via `/etc/ld.so.preload` |
| Addon patch | `/opt/Upwork/uta_native.node` | import renamed; backup `.bak` |
| Helper | `~/upwork-wayland-fix/files/uwsnap.py` → `~/.local/bin/uwsnap.py` | backends: kde/gnome/wlroots/cosmic/portal/command |
| Launcher | `~/upwork-wayland-fix/files/upwork-launcher.sh` → `~/.local/bin/upwork` | flags: `--no-sandbox --js-flags=--noexpose-wasm` |
| Desktop entry | `~/.local/share/applications/upwork.desktop` | points at the launcher |
| Modes | `~/.config/uwsnap-active-only` | flag file present = force active screen; absent = all screens. **Currently absent (all screens).** |
| Logs | `/tmp/uwsnap.log` (shim + helper) | ⚠️ not reboot-safe; copy before reboot |
| Repo | `~/upwork-wayland-fix` (git) | README, docs/, files/, install/uninstall |

## Cheat sheet

```bash
~/.local/bin/uwsnap.py --test        # capture-backend self-test (REAL/BLACK verdict)
cat /tmp/uwsnap.log                  # the full snap pipeline trace
tail -f /tmp/uwsnap.log              # watch live during a snap cycle
ls /tmp/uwsnap-refilled-*.png        # what the app actually received
git -C ~/upwork-wayland-fix log      # change history
```
