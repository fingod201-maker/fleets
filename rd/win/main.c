/* fleet rd-win: GDI BitBlt capture + SendInput (absolute) -> WebSocket JPEG.
 * JPEG via stb_image_write (dependency-free; mingw has no libjpeg package). */
#include <winsock2.h>
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../ws.h"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "../stb_image_write.h"

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
    if (!txt || !txt[0]) return;
    wchar_t wbuf[600];
    int n = MultiByteToWideChar(CP_UTF8, 0, txt, -1, wbuf, 600);
    for (int i = 0; i < n - 1; i++) {
        INPUT in;
        memset(&in, 0, sizeof(in));
        in.type = INPUT_KEYBOARD;
        in.ki.wVk = 0;
        in.ki.wScan = (WORD)wbuf[i];
        in.ki.dwFlags = KEYEVENTF_UNICODE;
        SendInput(1, &in, sizeof(in));
        in.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP;
        SendInput(1, &in, sizeof(in));
    }
}

static unsigned char *capture_rgb(int *out_w, int *out_h) {
    /* whole virtual desktop: covers every monitor, including negative origins */
    int vx = GetSystemMetrics(SM_XVIRTUALSCREEN);
    int vy = GetSystemMetrics(SM_YVIRTUALSCREEN);
    int vw = GetSystemMetrics(SM_CXVIRTUALSCREEN);
    int vh = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    if (vw <= 0 || vh <= 0) {
        vx = vy = 0;
        vw = GetSystemMetrics(SM_CXSCREEN);
        vh = GetSystemMetrics(SM_CYSCREEN);
    }
    if (vw <= 0 || vh <= 0) return NULL;
    /* downscale very wide multi-monitor desktops for bandwidth */
    int w = vw, h = vh, maxw = 1280;
    if (vw > maxw) {
        w = maxw;
        h = (int)((long long)vh * maxw / vw);
        if (h <= 0) h = 1;
    }
    HDC screen = GetDC(NULL);
    if (!screen) return NULL;
    HDC mem = CreateCompatibleDC(screen);
    HBITMAP bmp = CreateCompatibleBitmap(screen, w, h);
    HGDIOBJ old = SelectObject(mem, bmp);
    SetStretchBltMode(mem, HALFTONE);
    SetBrushOrgEx(mem, 0, 0, NULL);
    StretchBlt(mem, 0, 0, w, h, screen, vx, vy, vw, vh, SRCCOPY);

    BITMAPINFO bi;
    memset(&bi, 0, sizeof(bi));
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = w;
    bi.bmiHeader.biHeight = -h; /* top-down */
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;

    unsigned char *bgra = (unsigned char *)malloc((size_t)w * h * 4);
    unsigned char *rgb = (unsigned char *)malloc((size_t)w * h * 3);
    if (!bgra || !rgb) {
        free(bgra);
        free(rgb);
        SelectObject(mem, old);
        DeleteObject(bmp);
        DeleteDC(mem);
        ReleaseDC(NULL, screen);
        return NULL;
    }
    GetDIBits(mem, bmp, 0, (UINT)h, bgra, &bi, DIB_RGB_COLORS);
    for (size_t i = 0, n = (size_t)w * h; i < n; i++) {
        rgb[i * 3 + 0] = bgra[i * 4 + 2];
        rgb[i * 3 + 1] = bgra[i * 4 + 1];
        rgb[i * 3 + 2] = bgra[i * 4 + 0];
    }
    free(bgra);
    SelectObject(mem, old);
    DeleteObject(bmp);
    DeleteDC(mem);
    ReleaseDC(NULL, screen);
    *out_w = w;
    *out_h = h;
    return rgb;
}

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

static void mouse_input(DWORD flags, LONG data) {
    INPUT in;
    memset(&in, 0, sizeof(in));
    in.type = INPUT_MOUSE;
    in.mi.dwFlags = flags;
    in.mi.mouseData = (DWORD)data;
    SendInput(1, &in, sizeof(INPUT));
}

static void apply_input(const char *msg, int w, int h) {
    double x, y, btn, code, down;
    if (json_str_is(msg, "\"t\"", "m") && json_num(msg, "\"x\"", &x) && json_num(msg, "\"y\"", &y)) {
        INPUT in;
        memset(&in, 0, sizeof(in));
        in.type = INPUT_MOUSE;
        in.mi.dx = (LONG)(x * 65535.0);
        in.mi.dy = (LONG)(y * 65535.0);
        in.mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_VIRTUALDESK;
        SendInput(1, &in, sizeof(INPUT));
    } else if (json_str_is(msg, "\"t\"", "b") && json_num(msg, "\"btn\"", &btn)) {
        json_num(msg, "\"down\"", &down);
        int b = (int)btn;
        if (b == 4) { mouse_input(MOUSEEVENTF_WHEEL, 120); return; }
        if (b == 5) { mouse_input(MOUSEEVENTF_WHEEL, -120); return; }
        DWORD f = 0;
        if (b == 0) f = down != 0 ? MOUSEEVENTF_LEFTDOWN : MOUSEEVENTF_LEFTUP;
        else if (b == 1) f = down != 0 ? MOUSEEVENTF_MIDDLEDOWN : MOUSEEVENTF_MIDDLEUP;
        else if (b == 2) f = down != 0 ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_RIGHTUP;
        if (f) mouse_input(f, 0);
    } else if (json_str_is(msg, "\"t\"", "k") && json_num(msg, "\"code\"", &code)) {
        json_num(msg, "\"down\"", &down);
        INPUT in;
        memset(&in, 0, sizeof(in));
        in.type = INPUT_KEYBOARD;
        in.ki.wVk = (WORD)code;
        in.ki.wScan = (WORD)MapVirtualKey((UINT)code, MAPVK_VK_TO_VSC);
        in.ki.dwFlags = KEYEVENTF_SCANCODE | (down == 0 ? KEYEVENTF_KEYUP : 0);
        SendInput(1, &in, sizeof(INPUT));
    } else if (json_str_is(msg, "\"t\"", "txt")) {
        char txt[1200];
        json_str(msg, "\"text\"", txt, sizeof(txt));
        type_text(txt);
    }
    (void)w;
    (void)h;
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show) {
    (void)inst; (void)prev; (void)cmd; (void)show;
    const char *host = "127.0.0.1";
    const char *session = "session";
    const char *token = "";
    int port = 8443;
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv) {
        for (int i = 1; i < argc - 1; i++) {
            char key[64] = {0}, val[MAX_PATH] = {0};
            WideCharToMultiByte(CP_ACP, 0, argv[i], -1, key, sizeof(key), NULL, NULL);
            WideCharToMultiByte(CP_ACP, 0, argv[i + 1], -1, val, sizeof(val), NULL, NULL);
            if (strcmp(key, "--host") == 0) { host = _strdup(val); i++; }
            else if (strcmp(key, "--port") == 0) { port = atoi(val); i++; }
            else if (strcmp(key, "--session") == 0) { session = _strdup(val); i++; }
            else if (strcmp(key, "--token") == 0) { token = _strdup(val); i++; }
        }
        LocalFree(argv);
    }
    srand((unsigned)GetTickCount());
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);

    int screen_w = GetSystemMetrics(SM_CXVIRTUALSCREEN);
    int screen_h = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    if (screen_w <= 0 || screen_h <= 0) {
        screen_w = GetSystemMetrics(SM_CXSCREEN);
        screen_h = GetSystemMetrics(SM_CYSCREEN);
    }
    if (screen_w > 1280) {
        screen_h = (int)((long long)screen_h * 1280 / screen_w);
        screen_w = 1280;
        if (screen_h <= 0) screen_h = 1;
    }

    char path[512];
    snprintf(path, sizeof(path), "/rd/%s?token=%s", session, token);

    ws_t ws;
    ws.fd = WS_INVALID;
    if (ws_connect(&ws, host, port, path) != 0) {
        fprintf(stderr, "rd-win: cannot connect\n");
        return 1;
    }

    char hello[256];
    snprintf(hello, sizeof(hello),
             "{\"t\":\"hello\",\"os\":\"windows\",\"w\":%d,\"h\":%d,\"view_only\":false,"
             "\"mode\":\"desktop\",\"caps\":[\"desktop\",\"input\"],\"hvnc\":false}", screen_w,
             screen_h);
    ws_send(&ws, hello, strlen(hello), 1);

    char inbuf[8192];
    long delay = 80000;
    int closed = 0;
    for (;;) {
        /* drain every pending input message so pointer/keyboard stay responsive */
        for (int k = 0; k < 128; k++) {
            if (ws_wait(&ws, 0) != 1) break;
            int op = 0;
            long n = ws_recv(&ws, inbuf, sizeof(inbuf) - 1, &op);
            if (n <= 0) { closed = 1; break; }
            inbuf[n] = 0;
            if (op == 1) apply_input(inbuf, 0, 0);
        }
        if (closed) break;
        int w = 0, h = 0;
        unsigned char *rgb = capture_rgb(&w, &h);
        if (!rgb) {
            Sleep(100);
            continue;
        }
        membuf mb;
        mb.data = NULL;
        mb.len = 0;
        mb.cap = 0;
        stbi_write_jpg_to_func(jpg_write, &mb, w, h, 3, rgb, 60);
        free(rgb);
        if (mb.len == 0) {
            free(mb.data);
            Sleep(50);
            continue;
        }
        DWORD start = GetTickCount();
        int rc = ws_send(&ws, mb.data, mb.len, 2);
        free(mb.data);
        if (rc != 0) break;
        DWORD elapsed = GetTickCount() - start;
        if (elapsed > 100) delay = 300000;
        else if (elapsed < 40) delay = 80000;
        Sleep((DWORD)(delay / 1000));
    }
    ws_close(&ws);
    WSACleanup();
    return 0;
}
