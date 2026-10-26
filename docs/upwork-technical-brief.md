# Technical brief for the Upwork Linux app team

Upwork desktop 5.8.0-35 (Electron 18 / Chromium 100), Linux, Wayland sessions
(KDE Plasma, GNOME, wlroots compositors, COSMIC). Reporter: community workaround
author, tested on KDE Plasma 6 (active screen and all screens).

## Symptom

Time Tracker screenshots are uploaded as black images, or the app shows
"switch to Xorg" and does not snap at all.

## Root causes (with evidence)

1. **Native snap reads the XWayland root window.**
   `uta_native.node`: `uta::GetScreenSnapAsync` → `DesktopSessionAgent::SnapWholeDesktop`
   → `gdk_pixbuf_get_from_window(gdk_get_default_root_window(), 0, 0, W, H)`.
   Under Wayland no compositor draws into the XWayland root, so the pixbuf is
   all black (`mean=0`) on every compositor.
2. **The Electron "enhancement" overwrites a good native snap** (all-screens mode).
   `OSServices.collectUserActivity` in `main.js`:
   - takes the native snap (`utaNative.snapScreen`),
   - then calls `desktopCapturer.getSources({types:["screen"], thumbnailSize})`,
   - keeps every thumbnail with `!thumbnail.isEmpty()` and, if any exist,
     **replaces** `screenSnap` with a stitched JPEG.
   Under XWayland the thumbnail is a black image of valid size, so it passes the
   check and replaces the native snap. In active-screen mode the
   `display_id.includes(activeDisplay.id)` filter matches nothing, so the native
   snap survives. Hence only all-screens mode stays black even when the native
   snap is correct.
3. **Renderer crash loop** on some stacks: V8 CHECK (`ud2`) in
   `Isolate::UnwindAndFindHandler` (WebAssembly trap-handler unwinding), SIGILL,
   renderers die every ~50 s, black app window. Avoided with
   `--js-flags=--noexpose-wasm`.

## Suggested product fixes (smallest first)

1. In `collectUserActivity`, reject blank frames before replacing the native
   snap (e.g. sample pixels of the thumbnail; or keep the native snap when the
   Electron image is uniformly black). One condition; fixes all-screens mode.
2. On Wayland, capture through `org.freedesktop.portal.Screenshot`
   (xdg-desktop-portal) or PipeWire ScreenCast instead of
   `gdk_pixbuf_get_from_window` on the root. This works on KDE, GNOME, wlroots
   and COSMIC. The first use shows a one-time consent prompt.
3. Detect an all-black native snap and report "screen capture unavailable"
   instead of uploading it.
4. Update Electron (or ship `--noexpose-wasm` until then) to avoid the V8 crash.

## How the community workaround proves it (what we changed locally)

No Upwork code is redistributed; users patch their own install.

| Layer | What it does |
|---|---|
| Launcher | Sets `XDG_SESSION_TYPE=x11` and unsets `WAYLAND_DISPLAY` so the app's `isWayland()` gate passes and it attempts a native snap; adds `--no-sandbox --js-flags=--noexpose-wasm`. |
| Addon import rename | In `uta_native.node`, the import string `gdk_pixbuf_get_from_window` is renamed in place (same length) to `uwsnap_gdk_pixbuf_refill`, which is provided by a preloaded shim. |
| Shim (`libuwsnap.so`) | Calls the real GDK function; if the returned pixbuf is black (sampled) and ≥800×600, overwrites its pixels with a real capture from the compositor's own tool (Spectacle, grim, gnome-screenshot or the portal), rescaled to the X11 root layout. Inert in every other process. |
| `app.asar` patch | One same-length edit in `main.js` makes the replacement condition constant-false (`if(re.length){` → `if(0.0000000){`), so the real native snap is kept. |
| Result | Real pixels reach Upwork's own encoder and uploader unchanged. It does not create, edit or spoof screenshot content or activity data. |

Notes useful for a real fix:
- Making `desktopCapturer.getSources` fail (invalid source type) makes the
  promise never resolve and `collectUserActivity` times out after 30 s — handle
  empty/blank results, don't break the call.
- Preloading a library that *defines* `gdk_pixbuf_get_from_window` kills
  Chromium renderers (reproducible), which is why the refill uses a different
  symbol name.

## Evidence available on request

Shim logs (`gdk grab 3600x1080 … WAS-BLACK refilling`), the app log showing
`screenSnap.filename` / dimensions before and after the `app.asar` change, the
disassembled call chain, and coredump stacks for the crash.

Community project (credits to MarSoft, muhammad-rafey, tiesselune for earlier
workarounds this builds on): not yet public; I can share the repository with
Upwork engineers on request.
