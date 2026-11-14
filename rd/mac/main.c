/* fleet rd-mac: CGDisplayCreateImage capture + CGEventPost input -> WebSocket JPEG.
 * Requires Accessibility TCC for input; if not granted the helper still streams
 * and sets "needs_accessibility" so the panel shows a banner.
 * Built on macOS only (frameworks + stb_image_write). */
#include <ApplicationServices/ApplicationServices.h>
#include <CoreGraphics/CoreGraphics.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "../ws.h"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "../stb_image_write.h"

typedef struct {
    unsigned char *data;
    size_t len;
    size_t cap;
} membuf;

static void jpg_write(void *ctx, void *data, int size) {
    membuf *buf = (membuf *)ctx;
    if (buf->len + (size_t)size > buf->cap) {
        buf->cap = (buf->len + (size_t)size) * 2 + 1024;
        buf->data = (unsigned char *)realloc(buf->data, buf->cap);
    }
    memcpy(buf->data + buf->len, data, (size_t)size);
    buf->len += (size_t)size;
}

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

static void json_str(const char *s, const char *key, char *out, size_t cap) {
    out[0] = 0;
    const char *p = strstr(s, key);
    if (!p) return;
    p += strlen(key);
    while (*p == ' ' || *p == ':' || *p == '\t') p++;
    if (*p != '"') return;
    p++;
    size_t o = 0;
    while (*p && *p != '"' && o + 1 < cap) {
        if (*p == '\\' && p[1]) {
            p++;
            if (*p == 'n') { out[o++] = '\n'; p++; continue; }
            if (*p == 't') { out[o++] = '\t'; p++; continue; }
        }
        out[o++] = *p++;
    }
    out[o] = 0;
}

static void type_text(const char *txt) {
    const unsigned char *p = (const unsigned char *)txt;
    while (p && *p) {
        UniChar u;
        if (*p < 0x80) { u = *p++; }
        else if ((*p & 0xE0) == 0xC0 && p[1]) { u = (UniChar)(((p[0] & 0x1F) << 6) | (p[1] & 0x3F)); p += 2; }
        else if ((*p & 0xF0) == 0xE0 && p[1] && p[2]) { u = (UniChar)(((p[0] & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F)); p += 3; }
        else { p++; continue; }
        CGEventRef ev = CGEventCreateKeyboardEvent(NULL, 0, true);
        if (!ev) continue;
        CGEventKeyboardSetUnicodeString(ev, 1, &u);
        CGEventPost(kCGHIDEventTap, ev);
        CFRelease(ev);
        CGEventRef up = CGEventCreateKeyboardEvent(NULL, 0, false);
        if (up) {
            CGEventKeyboardSetUnicodeString(up, 1, &u);
            CGEventPost(kCGHIDEventTap, up);
            CFRelease(up);
        }
    }
}

/* union of every active display, in global points (CGEvent coordinate space) */
static double g_ox = 0, g_oy = 0, g_uw = 1, g_uh = 1;

static void display_union(void) {
    uint32_t count = 0;
    CGGetActiveDisplayList(0, NULL, &count);
    CGDirectDisplayID ids[32];
    if (count > 32) count = 32;
    if (count == 0) { g_ox = g_oy = 0; g_uw = g_uh = 1; return; }
    CGGetActiveDisplayList(count, ids, &count);
    CGRect u = CGRectNull;
    for (uint32_t i = 0; i < count; i++) u = CGRectUnion(u, CGDisplayBounds(ids[i]));
    g_ox = u.origin.x; g_oy = u.origin.y;
    g_uw = u.size.width > 0 ? u.size.width : 1;
    g_uh = u.size.height > 0 ? u.size.height : 1;
}

typedef CGImageRef (*cg_window_image_fn)(CGRect, CGWindowListOption, CGWindowID,
                                         CGWindowImageOption);
typedef CGImageRef (*cg_display_image_fn)(CGDirectDisplayID);

/* macOS 15 SDK marks these "unavailable" even though CoreGraphics still exports
 * them; resolve at runtime so the same source builds on every SDK. */
static CGImageRef composite_image(void) {
    static cg_window_image_fn window_fn = NULL;
    static cg_display_image_fn display_fn = NULL;
    static int resolved = 0;
    if (!resolved) {
        resolved = 1;
        window_fn = (cg_window_image_fn)dlsym(RTLD_DEFAULT, "CGWindowListCreateImage");
        display_fn = (cg_display_image_fn)dlsym(RTLD_DEFAULT, "CGDisplayCreateImage");
    }
    CGImageRef image = NULL;
    if (window_fn) {
        image = window_fn(CGRectInfinite, kCGWindowListOptionOnScreenOnly,
                          kCGNullWindowID, kCGWindowImageDefault);
    }
    if (!image && display_fn) image = display_fn(CGMainDisplayID());
    return image;
}

static unsigned char *capture_rgb(int *out_w, int *out_h) {
    /* composite of all displays (falls back to the main display) */
    CGImageRef image = composite_image();
    if (!image) return NULL;
    size_t w = CGImageGetWidth(image);
    size_t h = CGImageGetHeight(image);
    CGDataProviderRef provider = CGImageGetDataProvider(image);
    CFDataRef raw = CGDataProviderCopyData(provider);
    const unsigned char *bytes = raw ? CFDataGetBytePtr(raw) : NULL;
    if (!bytes) {
        if (raw) CFRelease(raw);
        CGImageRelease(image);
        return NULL;
    }
    /* rhythm: score of bytes per pixel from CGImage is BGRA (4bpp) */
    size_t bpp = CFDataGetLength(raw) / (w * h ? w * h : 1);
    if (bpp < 3) bpp = 4;
    int sw = (int)w, sh = (int)h, maxw = 1600;
    if (w > (size_t)maxw) {
        sw = maxw;
        sh = (int)((long long)h * maxw / (long long)w);
        if (sh <= 0) sh = 1;
    }
    unsigned char *rgb = (unsigned char *)malloc((size_t)sw * sh * 3);
    if (rgb) {
        for (int y = 0; y < sh; y++) {
            size_t sy = (size_t)((long long)y * (long long)h / sh);
            for (int x = 0; x < sw; x++) {
                size_t sx = (size_t)((long long)x * (long long)w / sw);
                const unsigned char *p = bytes + (sy * w + sx) * bpp;
                unsigned char *o = rgb + ((size_t)y * sw + x) * 3;
                o[0] = p[2];
                o[1] = p[1];
                o[2] = p[0];
            }
        }
    }
    CFRelease(raw);
    CGImageRelease(image);
    *out_w = sw;
    *out_h = sh;
    return rgb;
}

static void post_mouse(CGEventType type, int x, int y, CGMouseButton button) {
    CGEventRef ev = CGEventCreateMouseEvent(NULL, type, CGPointMake(x, y), button);
    if (ev) {
        CGEventPost(kCGHIDEventTap, ev);
        CFRelease(ev);
    }
}

static void apply_input(const char *msg, int w, int h) {
    double x, y, btn, code, down;
    if (json_str_is(msg, "\"t\"", "m") && json_num(msg, "\"x\"", &x) && json_num(msg, "\"y\"", &y)) {
        post_mouse(kCGEventMouseMoved, (int)(g_ox + x * g_uw), (int)(g_oy + y * g_uh),
                   kCGMouseButtonLeft);
    } else if (json_str_is(msg, "\"t\"", "b") && json_num(msg, "\"btn\"", &btn)) {
        json_num(msg, "\"down\"", &down);
        int b = (int)btn;
        CGEventType type = kCGEventLeftMouseDown;
        CGMouseButton button = kCGMouseButtonLeft;
        if (b == 2) { type = down ? kCGEventRightMouseDown : kCGEventRightMouseUp; button = kCGMouseButtonRight; }
        else if (b == 1) { type = down ? kCGEventOtherMouseDown : kCGEventOtherMouseUp; button = kCGMouseButtonCenter; }
        else { type = down ? kCGEventLeftMouseDown : kCGEventLeftMouseUp; }
        CGEventRef cur = CGEventCreate(NULL);
        CGPoint pt = cur ? CGEventGetLocation(cur) : CGPointMake(0, 0);
        if (cur) CFRelease(cur);
        post_mouse(type, (int)pt.x, (int)pt.y, button);
    } else if (json_str_is(msg, "\"t\"", "k") && json_num(msg, "\"code\"", &code)) {
        json_num(msg, "\"down\"", &down);
        CGEventRef ev = CGEventCreateKeyboardEvent(NULL, (CGKeyCode)code, down != 0);
        if (ev) { CGEventPost(kCGHIDEventTap, ev); CFRelease(ev); }
    } else if (json_str_is(msg, "\"t\"", "txt")) {
        char txt[1024];
        json_str(msg, "\"text\"", txt, sizeof(txt));
        type_text(txt);
    }
    (void)w;
    (void)h;
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
    (void)hvnc;
    srand((unsigned)getpid());

    display_union();
    int screen_w = 0, screen_h = 0;
    unsigned char *probe = capture_rgb(&screen_w, &screen_h);
    free(probe);
    int trusted = AXIsProcessTrusted();

    char path[512];
    snprintf(path, sizeof(path), "/rd/%s?token=%s", session, token);
    ws_t ws;
    ws.fd = WS_INVALID;
    if (ws_connect(&ws, host, port, path) != 0) {
        fprintf(stderr, "rd-mac: cannot connect\n");
        return 1;
    }
    char hello[384];
    snprintf(hello, sizeof(hello),
             "{\"t\":\"hello\",\"os\":\"darwin\",\"w\":%d,\"h\":%d,\"view_only\":false,"
             "\"mode\":\"%s\",\"caps\":[\"desktop\",\"input\",\"hvnc\"],\"hvnc\":true,"
             "\"needs_accessibility\":%s}",
             screen_w, screen_h, mode, trusted ? "false" : "true");
    ws_send(&ws, hello, strlen(hello), 1);

    char inbuf[4096];
    long delay = 125000;
    for (;;) {
        if (ws_wait(&ws, 0) == 1) {
            int op = 0;
            long n = ws_recv(&ws, inbuf, sizeof(inbuf) - 1, &op);
            if (n <= 0) break;
            inbuf[n] = 0;
            if (op == 1) apply_input(inbuf, screen_w, screen_h);
        }
        int w = 0, h = 0;
        unsigned char *rgb = capture_rgb(&w, &h);
        if (!rgb) { usleep(100000); continue; }
        membuf mb;
        mb.data = NULL;
        mb.len = 0;
        mb.cap = 0;
        stbi_write_jpg_to_func(jpg_write, &mb, w, h, 3, rgb, 60);
        free(rgb);
        if (mb.len == 0) { free(mb.data); usleep(50000); continue; }
        int rc = ws_send(&ws, mb.data, mb.len, 2);
        free(mb.data);
        if (rc != 0) break;
        usleep((useconds_t)delay);
    }
    ws_close(&ws);
    return 0;
}
