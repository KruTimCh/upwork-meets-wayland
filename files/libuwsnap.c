/* libuwsnap v2 — make Upwork's time-tracker screenshots work on Wayland.
 *
 * Upwork 5.8.x (Electron 18 + the uta_native.node addon) captures its tracker
 * screenshots through GDK:
 *
 *     uta::DesktopSessionAgent::SnapWholeDesktop()
 *   → gdk_pixbuf_get_from_window(root_window)
 *
 * On a Wayland session the XWayland root window reads back all-black (no
 * compositor composites into it), so every snap is a black frame — on KDE,
 * GNOME, COSMIC and wlroots compositors alike. This shim fixes it at the
 * exact point the black pixels enter the app.
 *
 * Loading strategy (see install.sh):
 *   1. LD_PRELOAD via the launcher (works where Chromium does not re-exec)
 *   2. /etc/ld.so.preload (survives Chromium's env-sanitizing re-exec)
 * Both may be installed at once; ld.so deduplicates the mapping.
 *
 * IMPORTANT: the refill is exported as `uwsnap_gdk_pixbuf_refill` and
 * /opt/Upwork/uta_native.node's import string is patched to bind to it.
 * DO NOT define `gdk_pixbuf_get_from_window` here: preloading a library that
 * shadows that symbol kills Chromium renderers every ~50 s ("Terminating
 * renderer for bad IPC") even when the function is never called
 * (bisection-verified).
 *
 * Deferred (see docs/reverse-engineering.md — they need an async design;
 * synchronous versions of these kill renderers via IPC stalls):
 *   - XGetWindowAttributes zeroing to defeat Upwork's "enhancing snapshot"
 *   - XGetWindowProperty spoofing of _NET_WM_PID/_NET_WM_NAME (activity
 *     tracking metadata; requires popen inside a hot X11 path)
 *
 * The refill calls uwsnap.py, which captures the real screen through a
 * compositor-appropriate backend (Spectacle / gnome-screenshot / grim /
 * xdg-desktop-portal) — see uwsnap.py.
 *
 * Copyright: GPL-3.0-or-later. Concepts drawn from tiesselune/upwork-wayland
 * (GPL-3.0) and muhammad-rafey/upwork-wayland-workaround (MIT).
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdarg.h>
#include <unistd.h>
#include <stdint.h>
#include <stdarg.h>
#include <X11/Xlib.h>
#include <X11/extensions/XShm.h>

#ifndef UWSNAP_HELPER_DEFAULT
#define UWSNAP_HELPER_DEFAULT "$HOME/.local/bin/uwsnap.py"
#endif

static void ulog(const char *fmt, ...)
{
    FILE *f = fopen("/tmp/uwsnap.log", "a");
    if (!f)
        return;
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    fprintf(f, "%02d:%02d:%02d ", tm.tm_hour, tm.tm_min, tm.tm_sec);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

__attribute__((constructor)) static void uwsnap_init(void)
{
    setvbuf(stderr, NULL, _IONBF, 0);
}

/* ------------------------------------------------------------------ */
/* small plumbing                                                      */
/* ------------------------------------------------------------------ */

typedef XImage *(*xgetimage_fn)(Display *, Drawable, int, int, unsigned int,
                                unsigned int, unsigned long, int);
typedef Status (*xshmgetimage_fn)(Display *, Drawable, XImage *, int, int,
                                  unsigned long);
typedef Status (*xgetgeometry_fn)(Display *, Drawable, Window *, int *, int *,
                                  unsigned int *, unsigned int *, unsigned int *,
                                  unsigned int *);
typedef Status (*xgetwinattr_fn)(Display *, Window, XWindowAttributes *);
typedef Status (*xgetprop_fn)(Display *, Window, Atom, long, long, Bool, Atom,
                              Atom *, int *, unsigned long *, unsigned long *,
                              unsigned char **);
typedef char *(*xgetatomname_fn)(Display *, Atom);

static void *xlib_handle(void)
{
    static void *h;
    if (!h)
        h = dlopen("libX11.so.6", RTLD_LAZY | RTLD_GLOBAL);
    return h;
}

static void *xlib_sym(const char *name)
{
    void *h = xlib_handle();
    return h ? dlsym(h, name) : NULL;
}

/* Path of the capture helper: $UWSNAP_HELPER, else the install default. */
static const char *helper_path(void)
{
    static const char *p;
    if (!p) {
        p = getenv("UWSNAP_HELPER");
        if (!p || !*p) {
            static char buf[512];
            const char *home = getenv("HOME");
            if (!home) home = "/tmp";
            snprintf(buf, sizeof(buf), "%s/.local/bin/uwsnap.py", home);
            p = buf;
        }
    }
    return p;
}

/* Only act in processes that actually run Upwork's snap module — this shim
 * may be loaded system-wide via /etc/ld.so.preload and must be inert
 * everywhere else (spectacle, kwin, browsers, ...). */
static int in_upwork(void)
{
    static int cached = -1;
    if (cached >= 0)
        return cached;
    cached = 0;
    if (access("/tmp/uwsnap-test-mode", F_OK) == 0)
        return (cached = 1);
    FILE *f = fopen("/proc/self/maps", "r");
    if (!f)
        return 0;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        if (strstr(line, "uta_native.node")) {
            fclose(f);
            return (cached = 1);
        }
    }
    fclose(f);
    return 0;
}

/* X11 root geometry for the requested display connection. */
static void root_geometry(Display *dpy, unsigned int *rw, unsigned int *rh)
{
    static xgetgeometry_fn geo;
    Window junk_w;
    int junk_i;
    unsigned int junk;
    *rw = 3600;
    *rh = 1080;
    if (!geo)
        geo = (xgetgeometry_fn)xlib_sym("XGetGeometry");
    if (geo && dpy)
        geo(dpy, DefaultRootWindow(dpy), &junk_w, &junk_i, &junk_i,
            rw, rh, &junk, &junk);
}

static Display *ensure_xdisplay(void)
{
    static Display *dpy;
    if (!dpy) {
        Display *(*xopen)(const char *);
        xopen = (Display *(*)(const char *))xlib_sym("XOpenDisplay");
        if (xopen)
            dpy = xopen(NULL);
    }
    return dpy;
}

/* ------------------------------------------------------------------ */
/* GDK refill — bound ONLY by the patched uta_native.node import       */
/* ------------------------------------------------------------------ */

typedef struct _GdkPixbuf GdkPixbuf;
typedef struct _GdkWindow GdkWindow;
typedef unsigned char Guchar;
typedef GdkPixbuf *(*gdk_from_window_fn)(GdkWindow *, int, int, int, int);

static void *gdk_sym(const char *name)
{
    return dlsym(RTLD_DEFAULT, name);
}

GdkPixbuf *uwsnap_gdk_pixbuf_refill(GdkWindow *window, int sx, int sy,
                                    int width, int height)
{
    static gdk_from_window_fn real;
    static int (*get_w)(const GdkPixbuf *);
    static int (*get_h)(const GdkPixbuf *);
    static int (*get_rs)(const GdkPixbuf *);
    static int (*get_n)(const GdkPixbuf *);
    static Guchar *(*get_px)(const GdkPixbuf *);
    if (!real) {
        real  = (gdk_from_window_fn)(uintptr_t)gdk_sym("gdk_pixbuf_get_from_window");
        get_w = (int (*)(const GdkPixbuf *))(uintptr_t)gdk_sym("gdk_pixbuf_get_width");
        get_h = (int (*)(const GdkPixbuf *))(uintptr_t)gdk_sym("gdk_pixbuf_get_height");
        get_rs = (int (*)(const GdkPixbuf *))(uintptr_t)gdk_sym("gdk_pixbuf_get_rowstride");
        get_n  = (int (*)(const GdkPixbuf *))(uintptr_t)gdk_sym("gdk_pixbuf_get_n_channels");
        get_px = (Guchar *(*)(const GdkPixbuf *))(uintptr_t)gdk_sym("gdk_pixbuf_get_pixels");
    }
    if (!real || !get_px)
        return NULL;

    GdkPixbuf *pb = real(window, sx, sy, width, height);
    if (!pb || width < 800 || height < 600)
        return pb;                      /* small pixbufs are never touched */

    int rs = get_rs(pb), nch = get_n(pb);
    Guchar *px = get_px(pb);
    if (!px || nch < 3)
        return pb;

    /* all-black check (sampled): the XWayland root readback signature */
    long nonblack = 0, samples = 0;
    unsigned long long sum = 0;
    long step = (long)rs * height / 97 + 1;
    for (long i = 0; i + 2 < (long)rs * height && samples < 4000;
         i += step, samples++) {
        sum += px[i] + px[i + 1] + px[i + 2];
        if (samples < 12)
            nonblack += (px[i] > 28 || px[i + 1] > 28 || px[i + 2] > 28);
    }
    ulog("[uwsnap] gdk grab %dx%d+%d+%d: nonblack=%ld/12 mean=%llu -> %s",
         width, height, sx, sy, nonblack,
         samples ? sum / (samples * 3) : 0,
         nonblack >= 12 ? "looks-real passthrough" : "WAS-BLACK refilling");
    if (nonblack >= 12)
        return pb;                      /* real content — nothing to do */

    Display *dpy = ensure_xdisplay();
    unsigned int rw = 3600, rh = 1080;
    root_geometry(dpy, &rw, &rh);

    char cmd[512];
    snprintf(cmd, sizeof(cmd), "%s %u %u %d %d %d %d",
             helper_path(), rw, rh, sx, sy, width, height);
    FILE *p = popen(cmd, "r");
    if (!p)
        return pb;
    for (int r = 0; r < height; r++) {
        static unsigned char row[16384 * 4];
        size_t need = (size_t)width * 4;
        if (need > sizeof(row) || fread(row, 1, need, p) != need) {
            /* transient capture failure: one retry after a short delay */
            pclose(p);
            sleep(1);
            p = popen(cmd, "r");
            if (!p)
                return pb;
            r = -1;
            continue;
        }
        Guchar *dst = px + (size_t)r * rs;
        for (int c = 0; c < width; c++) {
            dst[c * nch + 0] = row[c * 4 + 2];  /* R */
            dst[c * nch + 1] = row[c * 4 + 1];  /* G */
            dst[c * nch + 2] = row[c * 4 + 0];  /* B */
        }
    }
    pclose(p);
    ulog("[uwsnap] gdk refill done");
    {
        /* dump the refilled pixbuf: visual proof of what the app received */
        static int (*gdk_save)(GdkPixbuf *, const char *, const char *,
                               void **, ...);
        if (!gdk_save)
            gdk_save = (int (*)(GdkPixbuf *, const char *, const char *,
                                void **, ...))(uintptr_t)gdk_sym("gdk_pixbuf_save");
        if (gdk_save) {
            char shot[64];
            time_t t = time(NULL);
            struct tm tm;
            localtime_r(&t, &tm);
            snprintf(shot, sizeof(shot), "/tmp/uwsnap-refilled-%02d%02d%02d.png",
                     tm.tm_hour, tm.tm_min, tm.tm_sec);
            gdk_save(pb, shot, "png", NULL);
            ulog("[uwsnap] refilled pixbuf dumped to %s", shot);
        }
    }
    return pb;
}

/* ------------------------------------------------------------------ */
/* "enhancing snapshot" defeat + save flag                             */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Active-window metadata so activity tracking names the right app     */
/* ------------------------------------------------------------------ */

/* uwsnap.py --active-window prints "PID<TAB>Title" for the focused window
 * (compositor-specific source lives in the helper). Cached for 2 s. */
static int active_window_info(char **pid_out, char **title_out)
{
    static char pid_buf[32];
    static char title_buf[512];
    static long cached_at;
    static int have;
    long now = (long)time(NULL);
    if (have && now - cached_at < 2) {
        *pid_out = pid_buf;
        *title_out = title_buf;
        return have;
    }
    have = 0;
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "%s --active-window 2>/dev/null", helper_path());
    FILE *p = popen(cmd, "r");
    if (p) {
        char line[576];
        if (fgets(line, sizeof(line), p)) {
            char *tab = strchr(line, '\t');
            if (tab) {
                *tab = 0;
                snprintf(pid_buf, sizeof(pid_buf), "%s", line);
                snprintf(title_buf, sizeof(title_buf), "%s", tab + 1);
                have = 1;
            }
        }
        pclose(p);
    }
    cached_at = have ? now : cached_at;
    *pid_out = pid_buf;
    *title_out = title_buf;
    return have;
}

/* ------------------------------------------------------------------ */
/* X11 root grabs (inert on this flow; kept for other app versions     */
/* that snap via XShmGetImage/XGetImage directly)                      */
/* ------------------------------------------------------------------ */

static int fill_from_helper(XImage *img, int x, int y, Display *dpy)
{
    if (!img || img->width <= 0 || img->height <= 0 || !img->data)
        return -1;
    unsigned int rw, rh;
    root_geometry(dpy, &rw, &rh);
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "%s %u %u %d %d %d %u",
             helper_path(), rw, rh, x, y, img->width, img->height);
    FILE *p = popen(cmd, "r");
    if (!p)
        return -2;
    unsigned char *dst = (unsigned char *)img->data;
    size_t row_bytes = (size_t)img->width * 4;
    int ok = 1;
    for (int r = 0; r < img->height; r++) {
        unsigned char *row = dst + (size_t)r * img->bytes_per_line;
        if (fread(row, 1, row_bytes, p) != row_bytes) { ok = 0; break; }
    }
    pclose(p);
    ulog("[uwsnap] XImage filled %dx%d at +%d+%d ok=%d\n",
            img->width, img->height, x, y, ok);
    return ok ? 0 : -3;
}

XImage *XGetImage(Display *dpy, Drawable d, int x, int y, unsigned int w,
                  unsigned int h, unsigned long plane_mask, int format)
{
    static xgetimage_fn real;
    if (!real)
        real = (xgetimage_fn)dlsym(RTLD_NEXT, "XGetImage");
    if (w == 0 || h == 0) {
        /* the enhancing-snapshot defeat zeroes the root attributes; Upwork's
         * JS then issues a 0x0 XGetImage, which is a fatal X error if it
         * reaches the server — answer with a NULL image instead */
        ulog("[uwsnap] XGetImage 0x0 ignored");
        return NULL;
    }
    ulog("[uwsnap] XGetImage %ux%u+%d+%d upwork=%d\n",
            w, h, x, y, in_upwork());
    XImage *img = real(dpy, d, x, y, w, h, plane_mask, format);
    if (img && img->data && in_upwork())
        fill_from_helper(img, x, y, dpy);
    return img;
}

Status XShmGetImage(Display *dpy, Drawable d, XImage *img, int x, int y,
                    unsigned long plane_mask)
{
    static xshmgetimage_fn real;
    if (!real)
        real = (xshmgetimage_fn)dlsym(RTLD_NEXT, "XShmGetImage");
    Status st = 0;
    if (real)
        st = real(dpy, d, img, x, y, plane_mask);
    if (!img || !in_upwork())
        return st;
    fill_from_helper(img, x, y, dpy);
    return st ? st : 1;
}
