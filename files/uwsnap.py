#!/usr/bin/env python3
"""uwsnap — real-capture helper for the libuwsnap Upwork/Wayland fix.

Called by the libuwsnap shim inside the Upwork process:

    uwsnap.py ROOT_W ROOT_H X Y W H      # raw BGRA region on stdout
    uwsnap.py --test                     # capture-backend self-test
    uwsnap.py --active-window            # "PID<TAB>Title" of the focused window

The caller's environment is Chromium-sanitized (no usable WAYLAND_DISPLAY /
DBUS vars), so this script builds its own session environment and captures
the real screen through a compositor-appropriate backend:

    override   UPWORK_SCREENSHOT_COMMAND (output path appended, flameshot-style)
    KDE/Plasma spectacle
    GNOME      gnome-screenshot, fallback org.gnome.Shell.Screenshot D-Bus
    wlroots    grim (sway/Hyprland/river/labwc/Wayfire)
    COSMIC     xdg-desktop-portal Screenshot
    X11        import (ImageMagick)

The capture is rescaled to the X11 root layout, cropped to the requested
region, and — in active-screen mode (flag file ~/.config/uwsnap-active-only) —
the focused monitor is scaled to fill the whole request.
"""
import os
import subprocess
import sys
import tempfile
import time

CACHE = "/tmp/uwsnap-last.png"
CACHE_TTL = 3.0
ACTIVE_FLAG = os.path.expanduser("~/.config/uwsnap-active-only")


# ---------------------------------------------------------------- environment

def session_env():
    """Known-good session env. The caller (inside Upwork) has a Chromium-
    sanitized environment; prefer anything real it still carries, else
    reconstruct. WAYLAND_DISPLAY_REAL (saved by the launcher) wins."""
    uid = os.getuid()
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env = {
        "PATH": os.environ.get("PATH") or "/usr/bin:/bin",
        "HOME": os.environ.get("HOME") or f"/home/{os.environ.get('USER', 'tims')}",
        "DISPLAY": os.environ.get("DISPLAY") or ":1",
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        or f"unix:path={runtime}/bus",
        "LANG": "C.UTF-8",
    }
    wl = os.environ.get("WAYLAND_DISPLAY_REAL") or os.environ.get("WAYLAND_DISPLAY")
    if wl:
        env["WAYLAND_DISPLAY"] = wl
        env["XDG_SESSION_TYPE"] = "wayland"
        env["QT_QPA_PLATFORM"] = "wayland"
    else:
        env["XDG_SESSION_TYPE"] = "x11"
        env["QT_QPA_PLATFORM"] = "xcb"
    if env.get("XDG_SESSION_TYPE") == "wayland" and env["DISPLAY"] == ":1":
        env.pop("DISPLAY", None)  # pure-wayland tools must not fall back to X
    xa = os.environ.get("XAUTHORITY")
    if xa:
        env["XAUTHORITY"] = xa
    return env


ENV = session_env()


def run(cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, env=ENV)


# ---------------------------------------------------------------- backends

def _wait_file(path, timeout=20):
    for _ in range(int(timeout / 0.25)):
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            time.sleep(0.15)
            if os.path.getsize(path) > 1000:
                return True
        time.sleep(0.25)
    return False


def cap_kde(out):
    subprocess.run(["/usr/bin/spectacle", "-b", "-n", "-f", "-o", out],
                   check=False, timeout=25, env=ENV)
    return _wait_file(out)


def cap_gnome(out):
    r = run(["/usr/bin/gnome-screenshot", "-f", out])
    if r.returncode == 0 and _wait_file(out, 8):
        return True
    # direct GNOME Shell D-Bus API (Muhammad-rafey style, as capture source)
    try:
        import dbus
        bus = dbus.SessionBus()
        shell = bus.get_object("org.gnome.Shell.Screenshot",
                               "/org/gnome/Shell/Screenshot")
        shell.Screenshot(False, False, out,
                         dbus_interface="org.gnome.Shell.Screenshot")
        return _wait_file(out, 8)
    except Exception:
        return False


def cap_wlroots(out):
    r = run(["/usr/bin/grim", out])
    return r.returncode == 0 and os.path.exists(out)


def cap_portal(out):
    """xdg-desktop-portal Screenshot (COSMIC and any DE with a portal
    backend). Non-interactive; on compositors that require consent the first
    snap shows a dialog — approve it once, the permission store remembers."""
    try:
        import dbus
        bus = dbus.SessionBus()
        portal = bus.get_object("org.freedesktop.portal.Desktop",
                                "/org/freedesktop/portal/desktop")
        opts = {"handle_token": "uwsnap", "interactive": False}
        reply = portal.Screenshot("", opts,
                                  dbus_interface="org.freedesktop.portal.Screenshot")
        # the Response signal carries the URI; poll for any new file instead
        deadline = time.time() + 15
        before = _snapshot_dir_state()
        while time.time() < deadline:
            new = _snapshot_dir_state() - before
            if new:
                newest = max(new, key=lambda p: os.path.getmtime(p))
                if os.path.getsize(newest) > 1000:
                    os.replace(newest, out)
                    return True
            time.sleep(0.25)
        return _wait_file(out, 1)
    except Exception:
        return False


def _snapshot_dir_state():
    roots = ["/tmp", os.path.join(ENV.get("XDG_RUNTIME_DIR", "/run/user/0"),
                                  "doc")]
    found = set()
    for root in roots:
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.endswith((".png", ".jpg")):
                    p = os.path.join(dirpath, f)
                    try:
                        if time.time() - os.path.getmtime(p) < 60:
                            found.add(p)
                    except OSError:
                        pass
    return found


def cap_x11(out):
    for cmd in (["/usr/bin/import", "-window", "root", out],
                ["/usr/bin/xwd", "-root", "-silent", "-display",
                 ENV.get("DISPLAY", ":0")]):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=15, env=ENV)
            if r.returncode == 0 and os.path.exists(out) \
                    and os.path.getsize(out) > 1000:
                return True
        except Exception:
            continue
    return False


BACKENDS = {
    "kde": cap_kde,
    "gnome": cap_gnome,
    "wlroots": cap_wlroots,
    "cosmic": cap_portal,
    "portal": cap_portal,
    "x11": cap_x11,
}


def detect_backend():
    override = os.environ.get("UPWORK_SCREENSHOT_COMMAND")
    if override:
        return "command"
    de = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get(
        "XDG_SESSION_DESKTOP") or "").lower()
    if "kde" in de or "plasma" in de:
        return "kde"
    if "cosmic" in de:
        return "cosmic"
    if (os.environ.get("SWAYSOCK") or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
            or any(w in de for w in ("sway", "hyprland", "wayfire", "river",
                                     "labwc"))):
        return "wlroots"
    if "gnome" in de or "ubuntu" in de or "unity" in de:
        return "gnome"
    session = os.environ.get("XDG_SESSION_TYPE_REAL") or os.environ.get(
        "XDG_SESSION_TYPE")
    if session and "wayland" not in session:
        return "x11"
    return "portal"


def capture(root_w, root_h):
    """Returns path to a real full-layout capture (rescaled to root size)."""
    now = time.time()
    if os.path.exists(CACHE) and now - os.path.getmtime(CACHE) < CACHE_TTL:
        return CACHE
    backend = detect_backend()
    fd, tmp = tempfile.mkstemp(suffix=".png", dir="/tmp")
    os.close(fd)
    os.remove(tmp)
    ok = False
    try:
        if backend == "command":
            cmd = os.environ["UPWORK_SCREENSHOT_COMMAND"] + " " + tmp
            subprocess.run(cmd, shell=True, timeout=40, env=ENV)
            ok = _wait_file(tmp)
        else:
            ok = BACKENDS[backend](tmp)
            if not ok and backend != "portal":
                ok = cap_portal(tmp)  # universal fallback
                backend = backend + "+portal" if ok else backend
    except Exception as e:
        print(f"[uwsnap] backend {backend} failed: {e}", file=sys.stderr)
        sys.exit(3)
    if not ok:
        print(f"[uwsnap] backend {backend} produced no capture", file=sys.stderr)
        sys.exit(3)
    os.replace(tmp, CACHE)
    print(f"[uwsnap] capture via {backend}", file=sys.stderr)
    return CACHE


# ------------------------------------------------------------- active window

def active_window_info():
    """Focused window as 'PID<TAB>Title', per compositor. Best effort."""
    cmd = os.environ.get("UWSNAP_ACTIVE_WINDOW_CMD")
    if cmd:
        r = run(["/bin/sh", "-c", cmd], timeout=5)
        if r.returncode == 0 and "\t" in r.stdout:
            return r.stdout.strip()
    if os.environ.get("SWAYSOCK"):
        r = run(["/bin/sh", "-c",
                 "swaymsg -t get_tree | jq -r '.. | (.nodes? // empty)[] | "
                 "select(.focused) | \"\\(.pid)\\t\\(.name)\""], timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        r = run(["/bin/sh", "-c",
                 "hyprctl activewindow -j | jq -r '\"\\(.pid)\\t\\(.title)\"'"],
                timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    for tool in ("kdotool", "xdotool"):
        if os.path.exists(f"/usr/bin/{tool}"):
            r = run([f"/usr/bin/{tool}", "getactivewindow"], timeout=5)
            if r.returncode != 0:
                continue
            wid = r.stdout.strip()
            r2 = run([f"/usr/bin/{tool}", "getwindowpid", wid], timeout=5)
            r3 = run([f"/usr/bin/{tool}", "getwindowname", wid], timeout=5)
            if r2.returncode == 0:
                return f"{r2.stdout.strip()}\t{r3.stdout.strip()}"
    return ""


RANDR_CACHE = {"t": 0.0, "data": None}


def find_xauthority():
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() != "plasmashell":
                    continue
            with open(f"/proc/{pid}/environ", "rb") as f:
                for kv in f.read().split(b"\0"):
                    if kv.startswith(b"XAUTHORITY="):
                        return kv[11:].decode()
        except Exception:
            continue
    return os.environ.get("XAUTHORITY")


def active_monitor_region():
    """(x, y, w, h) of the active output in X11 root coords, cached 5 s."""
    if time.time() - RANDR_CACHE["t"] < 5 and RANDR_CACHE["data"] is not None:
        return RANDR_CACHE["data"]
    region = None
    env = dict(ENV)
    xa = find_xauthority()
    if xa:
        env["XAUTHORITY"] = xa
    try:
        out = run(["/usr/bin/qdbus6", "org.kde.KWin", "/KWin",
                   "org.kde.KWin.activeOutputName"], timeout=5).stdout.strip()
        rr = subprocess.run(["/usr/bin/xrandr", "--query"], capture_output=True,
                            text=True, timeout=5,
                            env={**env, "DISPLAY": env.get("DISPLAY", ":1")}).stdout
        for line in rr.splitlines():
            if out and out in line and " connected " in line:
                for p in line.split():
                    if ("+" in p and "x" in p
                            and p.replace("+", "").replace("x", "").isdigit()):
                        geo = p.split("+")
                        if len(geo) == 3:
                            w, h, x, y = (*geo[0].split("x"), geo[1], geo[2])
                            region = (int(x), int(y), int(w), int(h))
                break
    except Exception:
        region = None
    RANDR_CACHE["t"] = time.time()
    RANDR_CACHE["data"] = region
    return region


def monitor_rects():
    rects = []
    env = dict(ENV)
    xa = find_xauthority()
    if xa:
        env["XAUTHORITY"] = xa
    try:
        rr = subprocess.run(["/usr/bin/xrandr", "--query"], capture_output=True,
                            text=True, timeout=5,
                            env={**env, "DISPLAY": env.get("DISPLAY", ":1")}).stdout
    except Exception:
        return rects
    for line in rr.splitlines():
        if " connected " not in line:
            continue
        for p in line.split():
            if ("+" in p and "x" in p
                    and p.replace("+", "").replace("x", "").isdigit()):
                geo = p.split("+")
                if len(geo) == 3:
                    w, h, x, y = (*geo[0].split("x"), geo[1], geo[2])
                    rects.append((int(x), int(y), int(w), int(h)))
    return rects


def covers(a, b, tol=0.9):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy >= tol * bw * bh


# --------------------------------------------------------------------- main

def emit_region(shot, root_w, root_h, x, y, w, h):
    from PIL import Image
    if shot.size != (root_w, root_h):
        shot = shot.resize((root_w, root_h), Image.BILINEAR)

    req = (x, y, w, h)
    whole_root = covers(req, (0, 0, root_w, root_h))
    rects = monitor_rects()
    act = active_monitor_region()

    if whole_root:
        if os.path.exists(ACTIVE_FLAG) and act:
            mx, my, mw, mh = act
            out = shot.crop((mx, my, mx + mw, my + mh)).resize(
                (w, h), Image.LANCZOS)
        else:
            out = shot.crop((x, y, x + w, y + h))
        sys.stdout.buffer.write(out.tobytes("raw", "BGRX"))
        return

    if act:
        req_monitor = next((r for r in rects if covers(req, r, 0.85)), None)
        if req_monitor and not covers(act, req_monitor, 0.85):
            mx, my, mw, mh = act
            out = shot.crop((mx, my, mx + mw, my + mh)).resize(
                (w, h), Image.LANCZOS)
            sys.stdout.buffer.write(out.tobytes("raw", "BGRX"))
            return

    region = shot.crop((x, y, x + w, y + h))
    sys.stdout.buffer.write(region.tobytes("raw", "BGRX"))


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--test":
        from PIL import Image
        import statistics
        backend = detect_backend()
        try:
            path = capture(0, 0)
            im = Image.open(path).convert("L")
            px = list(im.getdata())[::997]
            verdict = "REAL" if statistics.pstdev(px) > 15 else "BLACK/empty"
            failed = verdict != "REAL"
        except Exception as e:
            print(f"backend={backend} verdict=FAILED ({e})")
            sys.exit(1)
        print(f"backend={backend} size={im.size} verdict={verdict}")
        sys.exit(1 if failed else 0)

    if argv and argv[0] == "--active-window":
        info = active_window_info()
        if info:
            print(info)
        sys.exit(0 if info else 1)

    if len(argv) != 6:
        print(__doc__)
        sys.exit(2)
    root_w, root_h, x, y, w, h = map(int, argv)
    from PIL import Image
    shot = Image.open(capture(root_w, root_h)).convert("RGB")
    emit_region(shot, root_w, root_h, x, y, w, h)


if __name__ == "__main__":
    main()
