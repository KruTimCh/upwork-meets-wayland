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

## Phase 5 — all-screens mode (diagnosed 2026-10-26, two bugs)

**Symptom:** snaps uploaded black (or missing) in all-screens mode, while
active-screen mode worked. The shim log proved the refill was correct, so the
failure sat somewhere else. It turned out to be two independent bugs.

### 5a. Our own diagnostic crashed Upwork (fixed)

The refilled-pixbuf PNG dump added for this investigation called
`gdk_pixbuf_save(pb, path, "png", NULL)` — a **variadic** function that needs
`(…, GError **error, <key,value>…, NULL)`. The missing terminator made GDK read
garbage as an option key → `strlen(garbage)` → **SIGSEGV on every refill**
(coredump stack: `strlen ← g_strdup ← gdk_pixbuf_save ← libuwsnap.so`). The
process died ~1 s after each `gdk refill done`, so snaps vanished and the app
"kept crashing".

Fix: `gdk_save(pb, shot, "png", NULL, NULL)`, and the dump now only runs when
`/tmp/uwsnap-dump` exists (a debug aid must not be able to crash the product
path). Verified: snaps at 16:29, 16:35, 16:59 survived; the dumped PNG of the
3600x1080 grab showed both monitors correctly.

### 5b. Upwork's "Electron screensnap enhancement" overwrites the good image (fixed)

Even with a perfect refill the thumbnail was black. Evidence:

- the refilled pixbuf (dumped PNG) was real, 3600x1080;
- the app log recorded the upload as `xxxxxxxx-x.jpg`, **4053x1200** — not our
  3600x1080 (active-screen snaps are `screen.png`, native size);
- the shim logged only one grab per snap, so the black image did not come
  through GDK at all.

Reading `out/main/main.js` from `app.asar` (`OSServices.collectUserActivity`):

```js
const z = yield utaNative.snapScreen(...)            // native snap  (our refill)
J.screenSnap = {blob: z.data, filename: z.filename, ...}
try {
  ... yield desktopCapturer.getSources({types:["screen"], thumbnailSize:{...}})
      .reduce((L,{thumbnail,display_id}) =>
          ((!snapOnlyActiveDisplay || display_id.includes(`${activeDisplay.id}`))
           && !thumbnail.isEmpty() && L.push(thumbnail.toPNG()), L), [])
  if (re.length) { ...stitch into a JPEG...; J.screenSnap = {filename: uuid+".jpg", blob, ...} }
} catch (L) { warn("Falling back to Native implementation. Electron Screensnap enhancement failed.") }
```

So Upwork takes the native snap, then **replaces** it with a stitched
`desktopCapturer` JPEG whenever any non-empty thumbnail comes back. Under
XWayland, Chromium's X11 capturer reads the same black root window, but a
black thumbnail still has a size, so `!isEmpty()` passes and the good image is
overwritten. In active-screen mode the `display_id.includes(activeId)` filter
never matches on XWayland, the list stays empty, and the native snap survives —
which is exactly why only all-screens broke.

**Fix** (`files/patch-asar.sh`, needs root, backup `app.asar.bak`): a
same-length byte edit in `/opt/Upwork/resources/app.asar`:

```
if(re.length){const L=yield   →   if(0.0000000){const L=yield
```

`desktopCapturer` still runs and returns normally; its result is ignored, so
the native (refilled) snap is kept. Electron 18 on Linux does not verify asar
integrity.

**Gotcha (cost one snap):** the first version of the patch changed the source
type `types:["screen"]` → `types:["scrnxx"]`. An unknown type makes
`getSources` **hang** (never resolves), so `collectUserActivity` hits its
30 s timeout (`Redux - collectUserActivity Timed out after 30 seconds`) and
the tracker shows "Failed to take screenshot". Never make `getSources` invalid;
neutralise the *use* of its result instead.

### Other facts learned

- Upwork resets the capture mode to **active screen** on every restart.
- `FetchPixbufImage` does `gdk_pixbuf_scale_simple` (thumbnail 620x320) then
  `gdk_pixbuf_save_to_callback` — no alpha handling issue (pixbuf is 3-channel
  RGB, `nch=3`), so the alpha hypothesis was tested and ruled out.
- Quitting Upwork (SIGTERM or window close) produces a coredump
  (`SIGTRAP` at shutdown) — harmless; do not count those as crashes. Count
  only dumps whose timestamp matches a snap.
- App log lives in `~/.Upwork/Upwork/Logs/upwork..YYYYMMDD.log`
  (`Electron Screensnap succeeded`, `screenSnap` blocks with filename/size).

- **Links ("View work diary") open via a `kioexec` window instead of the
  browser** — not caused by this fix. Upwork's `shell.openExternal` runs
  `xdg-open` → `kde-open`; if the default browser's `.desktop` file has an
  `Exec=` line without `%u`/`%U`, KDE falls back to `kioexec`
  (`journalctl | grep kioexec`: "contains supported protocols but doesn't use
  %u or %U"). Fix: add `%U` to the main `Exec=` line of the browser's
  `.desktop` file. (Seen with a custom Brave wrapper launcher.)

### Verification procedure

1. Start Upwork via the launcher, set **all screens**, start tracking.
2. After a snap: `tail /tmp/uwsnap.log` → `gdk grab 3600x1080 … WAS-BLACK
   refilling` + `refill done`.
3. App log: the new `screenSnap.filename` should be `screen.png`/native size,
   **not** a `.jpg` at a stitched size.
4. The tracker's "Latest screenshot" shows both monitors.

## Current deployed state (this machine)

| Piece | Location | Notes |
|---|---|---|
| Shim (source / built) | `~/upwork-wayland-fix/files/libuwsnap.c` → `~/.local/lib/libuwsnap.so` | preloaded via `/etc/ld.so.preload` |
| Addon patch | `/opt/Upwork/uta_native.node` | import renamed; backup `.bak` |
| Helper | `~/upwork-wayland-fix/files/uwsnap.py` → `~/.local/bin/uwsnap.py` | backends: kde/gnome/wlroots/cosmic/portal/command |
| Launcher | `~/upwork-wayland-fix/files/upwork-launcher.sh` → `~/.local/bin/upwork` | flags: `--no-sandbox --js-flags=--noexpose-wasm` |
| Desktop entry | `~/.local/share/applications/upwork.desktop` | points at the launcher |
| Asar patch | `/opt/Upwork/resources/app.asar` | `if(re.length)`→`if(0.0000000)`; backup `app.asar.bak`; `files/patch-asar.sh [--restore]` |
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
