/* fleet rd-mac: CGDisplayCreateImage capture + CGEventPost input -> WebSocket JPEG.
 * Requires Accessibility TCC for input; if not granted the helper still streams
 * and sets "needs_accessibility" so the panel shows a banner.
 * Built on macOS only (frameworks + stb_image_write). */
#include <ApplicationServices/ApplicationServices.h>
#include <CoreGraphics/CoreGraphics.h>
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

static unsigned char *capture_rgb(int *out_w, int *out_h) {
    CGImageRef image = CGDisplayCreateImage(CGMainDisplayID());
    if (!image) return NULL;
    size_t w = CGImageGetWidth(image);
    size_t h = CGImageGetHeight(image);
    CGDataProviderRef provider = CGImageGetDataProvider(image);
    CFDataRef raw = CGDataProviderCopyData(provider);
    const unsigned char *bytes = CFDataGetBytePtr(raw);
    if (!bytes) {
        CFRelease(raw);
        CGImageRelease(image);
        return NULL;
    }
    unsigned char *rgb = (unsigned char *)malloc(w * h * 3);
    if (rgb) {
        for (size_t i = 0, n = w * h; i < n; i++) {
            rgb[i * 3 + 0] = bytes[i * 4 + 2];
            rgb[i * 3 + 1] = bytes[i * 4 + 1];
            rgb[i * 3 + 2] = bytes[i * 4 + 0];
        }
    }
    CFRelease(raw);
    CGImageRelease(image);
    *out_w = (int)w;
    *out_h = (int)h;
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
        post_mouse(kCGEventMouseMoved, (int)(x * w), (int)(y * h), kCGMouseButtonLeft);
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
    }
}

int main(int argc, char **argv) {
    const char *host = "127.0.0.1";
    const char *session = "session";
    const char *token = "";
    int port = 8443;
    for (int i = 1; i < argc - 1; i++) {
        if (strcmp(argv[i], "--host") == 0) host = argv[++i];
        else if (strcmp(argv[i], "--port") == 0) port = atoi(argv[++i]);
        else if (strcmp(argv[i], "--session") == 0) session = argv[++i];
        else if (strcmp(argv[i], "--token") == 0) token = argv[++i];
    }
    srand((unsigned)getpid());

    int screen_w = (int)CGDisplayPixelsWide(CGMainDisplayID());
    int screen_h = (int)CGDisplayPixelsHigh(CGMainDisplayID());
    int trusted = AXIsProcessTrusted();

    char path[512];
    snprintf(path, sizeof(path), "/rd/%s?token=%s", session, token);
    ws_t ws;
    ws.fd = WS_INVALID;
    if (ws_connect(&ws, host, port, path) != 0) {
        fprintf(stderr, "rd-mac: cannot connect\n");
        return 1;
    }
    char hello[256];
    snprintf(hello, sizeof(hello),
             "{\"t\":\"hello\",\"os\":\"darwin\",\"w\":%d,\"h\":%d,\"view_only\":%s,"
             "\"needs_accessibility\":%s}",
             screen_w, screen_h, trusted ? "false" : "true", trusted ? "false" : "true");
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
