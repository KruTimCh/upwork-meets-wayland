# Evidence pack for the Upwork Linux desktop engineering team

Upwork desktop 5.8.0-35 (Electron 18), KDE Plasma 6 on Wayland. All files contain
only technical data: no account, contract, window-title or process information.

| File | What it shows |
|---|---|
| [`app-log-screensnap.tsv`](app-log-screensnap.tsv) | 44 snaps from the app's own log: `screen.png` (native snap kept) vs a hex-named `.jpg` (native snap replaced by the Electron `desktopCapturer` stitched image), plus the two 30 s `collectUserActivity` timeouts |
| [`../uwsnap-2026-10-26.log`](../uwsnap-2026-10-26.log) | Shim log: `gdk grab 3600x1080 ... nonblack=0/12 mean=0 -> WAS-BLACK refilling` proves `gdk_pixbuf_get_from_window` returns black on Wayland |
| [`uta-native-call-chain.txt`](uta-native-call-chain.txt) | Symbols and call order in `uta_native.node` (`SnapWholeDesktop` -> `gdk_pixbuf_get_from_window` -> `FetchPixbufImage`) |
| [`v8-renderer-crash-SIGILL.txt`](v8-renderer-crash-SIGILL.txt) | Coredump stack of the renderer crash loop (`Isolate::UnwindAndFindHandler`) |
| [`../../docs/upwork-technical-brief.md`](../../docs/upwork-technical-brief.md) | Root causes and suggested product fixes |
| [`../../files/patch-asar.sh`](../../files/patch-asar.sh) and [`../../files/libuwsnap.c`](../../files/libuwsnap.c) | The patch notes in code form: the one-line `app.asar` edit and the GDK refill shim |
| [`../../docs/reverse-engineering.md`](../../docs/reverse-engineering.md), [`../../docs/debugging-chronicle.md`](../../docs/debugging-chronicle.md) | Full analysis and every dead end |

## What the data shows

1. **Native snap is black on Wayland.** The shim log shows every `gdk_pixbuf_get_from_window`
   result on the XWayland root has mean pixel value 0.
2. **All-screens mode is overwritten.** Before the `app.asar` change the all-screens snaps in
   the app log are `.jpg` files of a stitched size (4053x1200) and were black in the tracker
   and Work Diary. After it they are `screen.png` at the native size (1920x576, scaled from
   3600x1080) and show real content.
3. **Renderer crash.** Five SIGILLs at the same instruction; no terminations with
   `--js-flags=--noexpose-wasm`.

## Observation not yet tested: native Wayland code already exists

`uta_native.node` contains `PortalDbusScreenshot`, `PlasmaDbusScreenshot` and
`GnomeDbusScreenshot` (`SnapWholeDesktop` / `SnapActiveDisplay` / `SnapSelection`), created
by `DbusScreenshot::Create()`. But `OSServices.collectUserActivity` in `main.js` returns
before calling `snapScreen` when `utaNative.isWayland()` is true:

```js
if (this.c.utaNative.isWayland())
  return J.screenSnap = {}, J.activeProcessName = null, J.activeWindowTitle = null, J.processes = [], J;
```

So the periodic snap never reaches those native classes on a real Wayland session. The
smallest official fix may be to lift that gate and let the existing portal/Plasma/GNOME
implementations run. I have not tested whether they produce correct images (including
multi-monitor and HiDPI), so please treat this as a lead, not a result.

## Reproduce

1. Wayland session, Upwork 5.8.0-35, launch with `XDG_SESSION_TYPE=x11` and `WAYLAND_DISPLAY`
   unset so the app attempts a native snap, plus `--js-flags=--noexpose-wasm`.
2. Start tracking, set "all screens", wait for a snap: it is black.
3. Apply `files/patch-asar.sh` and the shim (`files/install.sh`): the snap contains real pixels.

Contact: open an issue on the repository, or reply on support ticket #55755952.
