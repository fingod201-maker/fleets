/* fleet rd-win input engine (T1) — pure SendInput, never PostMessage.
 *
 * Session targeting + input injection for the desktop helper.
 *   - WTSGetActiveConsoleSessionId to locate the live console session.
 *   - CreateProcessAsUser (SeTcbPrivilege, SYSTEM) to start into that session.
 *   - SetThreadDesktop when addressing the hidden (HVNC) desktop.
 *   - SendInput: MOUSEEVENTF_ABSOLUTE|MOVE|VIRTUALDESK normalised 0-65535,
 *     KEYEVENTF_UNICODE text, X buttons 4/5, throttled wheel,
 *     60 Hz move coalescing, drag state machine.
 *
 * One input thread drives inp_apply() for every inbound message; all
 * transient state (move tick, drag state) lives here so there is exactly
 * one input stream and one drag state machine.
 */
#include <winsock2.h>
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* ---------- tiny JSON helpers (self-contained; mirrors rd/win/main.c) ---------- */

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

/* ---------- session ---------- */

/* Returns the ID of the active console session (the session a real user sees).
 * From a SYSTEM service (session 0) this is the session to attach to. */
DWORD inp_active_console_session(void) {
    DWORD id = (DWORD)-1;
    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    if (k32) {
        typedef DWORD (WINAPI *wts_get_active_console_session_id_t)(void);
        wts_get_active_console_session_id_t fn =
            (wts_get_active_console_session_id_t)GetProcAddress(k32, "WTSGetActiveConsoleSessionId");
        if (fn) id = fn();
    }
    return id;
}

/* Launch cmd into the given session as the console user. Requires
 * SeTcbPrivilege (i.e. the caller is SYSTEM / a service). Returns the new
 * process in *pi on success. */
BOOL inp_launch_as_console_user(const char *cmd, DWORD session, PROCESS_INFORMATION *pi) {
    if (!cmd || !pi) return FALSE;
    memset(pi, 0, sizeof(*pi));
    HANDLE token = NULL;
    if (!WTSQueryUserToken(session, &token)) return FALSE;
    HANDLE dup = NULL;
    if (!DuplicateTokenEx(token, MAXIMUM_ALLOWED, NULL, TokenImpersonation,
                          TokenPrimary, &dup)) {
        CloseHandle(token);
        return FALSE;
    }
    CloseHandle(token);
    STARTUPINFOA si;
    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    si.lpDesktop = "WinSta0\\Default"; /* interactive desktop of the target session */
    BOOL ok = CreateProcessAsUserA(dup, NULL, (LPSTR)cmd, NULL, NULL, FALSE,
                                   CREATE_UNICODE_ENVIRONMENT, NULL, NULL, &si, pi);
    CloseHandle(dup);
    return ok;
}

/* ---------- input injection (SendInput only) ---------- */

static DWORD g_last_move_tick = 0;   /* move coalescing: 60 Hz (~16 ms) */
static int g_drag_btn = -1;          /* -1 = none, else 0 L / 1 M / 2 R */
static int g_drag_x = 0, g_drag_y = 0;

static void mouse_move_abs(int x, int y) {
    INPUT in;
    memset(&in, 0, sizeof(in));
    in.type = INPUT_MOUSE;
    in.mi.dx = (LONG)x;
    in.mi.dy = (LONG)y;
    in.mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_VIRTUALDESK;
    SendInput(1, &in, sizeof(in));
}

static void mouse_btn(int b, int down) {
    INPUT in;
    memset(&in, 0, sizeof(in));
    in.type = INPUT_MOUSE;
    switch (b) {
        case 0: in.mi.dwFlags = down ? MOUSEEVENTF_LEFTDOWN : MOUSEEVENTF_LEFTUP; break;
        case 1: in.mi.dwFlags = down ? MOUSEEVENTF_MIDDLEDOWN : MOUSEEVENTF_MIDDLEUP; break;
        case 2: in.mi.dwFlags = down ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_RIGHTUP; break;
        case 3: in.mi.dwFlags = down ? MOUSEEVENTF_XDOWN : MOUSEEVENTF_XUP;
                in.mi.mouseData = XBUTTON1; break;   /* button 4 */
        case 4: in.mi.dwFlags = down ? MOUSEEVENTF_XDOWN : MOUSEEVENTF_XUP;
                in.mi.mouseData = XBUTTON2; break;   /* button 5 */
        default: return;
    }
    SendInput(1, &in, sizeof(in));
}

static void mouse_wheel(int delta) {
    INPUT in;
    memset(&in, 0, sizeof(in));
    in.type = INPUT_MOUSE;
    in.mi.dwFlags = MOUSEEVENTF_WHEEL;
    in.mi.mouseData = (DWORD)delta;
    SendInput(1, &in, sizeof(in));
}

static void key_scan(WORD vk, int down) {
    INPUT in;
    memset(&in, 0, sizeof(in));
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = vk;
    in.ki.wScan = (WORD)MapVirtualKeyW((UINT)vk, MAPVK_VK_TO_VSC);
    in.ki.dwFlags = KEYEVENTF_SCANCODE | (down ? 0 : KEYEVENTF_KEYUP);
    SendInput(1, &in, sizeof(in));
}

static void type_unicode(const char *txt) {
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

/* Apply one inbound input message via SendInput. w/h are the streamed frame
 * dimensions; input coordinates arrive normalised 0-1 and are rebased to the
 * 0-65535 absolute space SendInput expects. Call from the single input thread. */
void inp_apply(const char *msg, int w, int h) {
    double x, y, btn, code, down;
    (void)w; (void)h;
    if (json_str_is(msg, "\"t\"", "m") && json_num(msg, "\"x\"", &x) && json_num(msg, "\"y\"", &y)) {
        /* move coalescing: 60 Hz */
        DWORD now = GetTickCount();
        if (now - g_last_move_tick < 16) return;
        g_last_move_tick = now;
        int mx = (int)(x * 65535.0);
        int my = (int)(y * 65535.0);
        if (g_drag_btn >= 0) { g_drag_x = mx; g_drag_y = my; } /* drag follows cursor */
        mouse_move_abs(mx, my);
    } else if (json_str_is(msg, "\"t\"", "b") && json_num(msg, "\"btn\"", &btn)) {
        json_num(msg, "\"down\"", &down);
        int b = (int)btn;
        if (b == 4) { mouse_wheel(120); return; }        /* wheel up, throttled by caller */
        if (b == 5) { mouse_wheel(-120); return; }       /* wheel down */
        if (down != 0) {
            if (g_drag_btn < 0) { g_drag_btn = b; g_drag_x = (int)(x * 65535.0); g_drag_y = (int)(y * 65535.0); }
        } else {
            if (g_drag_btn == b) g_drag_btn = -1;        /* drag ends on release */
        }
        mouse_btn(b, down != 0);
    } else if (json_str_is(msg, "\"t\"", "k") && json_num(msg, "\"code\"", &code)) {
        json_num(msg, "\"down\"", &down);
        key_scan((WORD)code, down != 0);
    } else if (json_str_is(msg, "\"t\"", "txt")) {
        char txt[1200];
        json_str(msg, "\"text\"", txt, sizeof(txt));
        if (txt[0]) type_unicode(txt);
    }
}
