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

extern void inp_apply(const char *msg, int w, int h);

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

static ws_t g_ws;
static volatile LONG g_stop = 0;

static DWORD WINAPI input_loop(LPVOID arg) {
    (void)arg;
    char buf[8192];
    while (!g_stop) {
        if (ws_wait(&g_ws, 100000) == 1) {
            int op = 0;
            long n = ws_recv(&g_ws, buf, sizeof(buf) - 1, &op);
            if (n <= 0) { g_stop = 1; break; }
            buf[n] = 0;
            if (op == 1) inp_apply(buf, 0, 0);
        }
    }
    return 0;
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show) {
    (void)inst; (void)prev; (void)cmd; (void)show;
    SetProcessDPIAware(); /* physical pixels: capture and SendInput must agree */
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

    g_ws.fd = WS_INVALID;
    if (ws_connect(&g_ws, host, port, path) != 0) {
        fprintf(stderr, "rd-win: cannot connect\n");
        return 1;
    }

    char hello[256];
    snprintf(hello, sizeof(hello),
             "{\"t\":\"hello\",\"os\":\"windows\",\"w\":%d,\"h\":%d,\"view_only\":false,"
             "\"mode\":\"desktop\",\"caps\":[\"desktop\",\"input\"],\"hvnc\":false}", screen_w,
             screen_h);
    ws_send(&g_ws, hello, strlen(hello), 1);

    long delay = 80000;
    g_stop = 0;
    HANDLE th = CreateThread(NULL, 0, input_loop, NULL, 0, NULL);
    for (;;) {
        if (g_stop) break;
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
        int rc = ws_send(&g_ws, mb.data, mb.len, 2);
        free(mb.data);
        if (rc != 0) break;
        DWORD elapsed = GetTickCount() - start;
        if (elapsed > 100) delay = 300000;
        else if (elapsed < 40) delay = 80000;
        Sleep((DWORD)(delay / 1000));
    }
    g_stop = 1;
    if (th) { WaitForSingleObject(th, 2000); CloseHandle(th); }
    ws_close(&g_ws);
    WSACleanup();
    return 0;
}
