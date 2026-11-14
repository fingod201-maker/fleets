/* Minimal, dependency-free WebSocket client (RFC6455) shared by the rd helpers.
 *
 * Plain ws:// only; when the helper must cross TLS it is built with -DUSE_TLS
 * and linked against OpenSSL, which wraps the same read/write hooks. The M4
 * verification path uses ws:// on loopback.
 */
#ifndef FLEET_RD_WS_H
#define FLEET_RD_WS_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET ws_sock_t;
#define WS_INVALID INVALID_SOCKET
#define ws_closesock closesocket
#else
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int ws_sock_t;
#define WS_INVALID (-1)
#define ws_closesock close
#endif

typedef struct {
    ws_sock_t fd;
} ws_t;

static const char *WS_B64 =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static int ws_send_all(ws_t *ws, const void *buf, size_t len) {
    const char *p = (const char *)buf;
    while (len > 0) {
#ifdef _WIN32
        int n = send(ws->fd, p, (int)len, 0);
#else
        ssize_t n = send(ws->fd, p, len, 0);
#endif
        if (n <= 0) return -1;
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

static int ws_recv_all(ws_t *ws, void *buf, size_t len) {
    char *p = (char *)buf;
    while (len > 0) {
#ifdef _WIN32
        int n = recv(ws->fd, p, (int)len, 0);
#else
        ssize_t n = recv(ws->fd, p, len, 0);
#endif
        if (n <= 0) return -1;
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

static int ws_send(ws_t *ws, const void *data, size_t len, int opcode) {
    unsigned char header[14];
    size_t hlen = 2;
    unsigned char mask[4];
    header[0] = (unsigned char)(0x80 | (opcode & 0x0f));
    if (len < 126) {
        header[1] = (unsigned char)(0x80 | len);
    } else if (len < 65536) {
        header[1] = 0x80 | 126;
        header[2] = (unsigned char)((len >> 8) & 0xff);
        header[3] = (unsigned char)(len & 0xff);
        hlen = 4;
    } else {
        header[1] = 0x80 | 127;
        for (int i = 0; i < 8; i++) {
            header[2 + i] = (unsigned char)((len >> (8 * (7 - i))) & 0xff);
        }
        hlen = 10;
    }
    for (int i = 0; i < 4; i++) mask[i] = (unsigned char)(rand() & 0xff);
    memcpy(header + hlen, mask, 4);
    hlen += 4;
    if (ws_send_all(ws, header, hlen) != 0) return -1;
    if (len == 0) return 0;
    unsigned char *tmp = (unsigned char *)malloc(len);
    if (!tmp) return -1;
    const unsigned char *src = (const unsigned char *)data;
    for (size_t i = 0; i < len; i++) tmp[i] = src[i] ^ mask[i & 3];
    int rc = ws_send_all(ws, tmp, len);
    free(tmp);
    return rc;
}

/* Returns payload length, 0 on close, -1 on error. Handles ping/pong inline. */
static long ws_recv(ws_t *ws, void *buf, size_t buflen, int *opcode) {
    unsigned char h[2];
    if (ws_recv_all(ws, h, 2) != 0) return -1;
    int op = h[0] & 0x0f;
    size_t len = h[1] & 0x7f;
    if (len == 126) {
        unsigned char e[2];
        if (ws_recv_all(ws, e, 2) != 0) return -1;
        len = ((size_t)e[0] << 8) | e[1];
    } else if (len == 127) {
        unsigned char e[8];
        if (ws_recv_all(ws, e, 8) != 0) return -1;
        len = 0;
        for (int i = 0; i < 8; i++) len = (len << 8) | e[i];
    }
    unsigned char mask[4];
    int masked = (h[1] & 0x80) != 0;
    if (masked && ws_recv_all(ws, mask, 4) != 0) return -1;
    if (len > buflen) len = buflen;
    if (len > 0 && ws_recv_all(ws, buf, len) != 0) return -1;
    if (masked) {
        unsigned char *p = (unsigned char *)buf;
        for (size_t i = 0; i < len; i++) p[i] ^= mask[i & 3];
    }
    if (op == 0x9) {
        ws_send(ws, buf, len, 0xA);
    }
    if (op == 0x8) return 0;
    if (opcode) *opcode = op;
    return (long)len;
}

static int ws_wait(ws_t *ws, long usec) {
    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(ws->fd, &rfds);
    struct timeval tv;
    tv.tv_sec = usec / 1000000;
    tv.tv_usec = usec % 1000000;
    int rc = select((int)(ws->fd + 1), &rfds, NULL, NULL, &tv);
    return rc > 0 ? 1 : 0;
}

static int ws_connect(ws_t *ws, const char *host, int port, const char *path) {
    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    struct addrinfo *res = NULL;
    char portstr[16];
    snprintf(portstr, sizeof(portstr), "%d", port);
    if (getaddrinfo(host, portstr, &hints, &res) != 0 || !res) return -1;

    ws_sock_t fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd == WS_INVALID) {
        freeaddrinfo(res);
        return -1;
    }
    if (connect(fd, res->ai_addr, (int)res->ai_addrlen) != 0) {
        ws_closesock(fd);
        freeaddrinfo(res);
        return -1;
    }
    freeaddrinfo(res);
    ws->fd = fd;

    unsigned char key[16];
    for (int i = 0; i < 16; i++) key[i] = (unsigned char)(rand() & 0xff);
    char b64key[32];
    for (int i = 0; i < 16; i += 3) {
        int n = (16 - i) >= 3 ? 3 : (16 - i);
        uint32_t v = (uint32_t)key[i] << 16;
        if (n > 1) v |= (uint32_t)key[i + 1] << 8;
        if (n > 2) v |= key[i + 2];
        int o = (i / 3) * 4;
        b64key[o] = WS_B64[(v >> 18) & 0x3f];
        b64key[o + 1] = WS_B64[(v >> 12) & 0x3f];
        b64key[o + 2] = n > 1 ? WS_B64[(v >> 6) & 0x3f] : '=';
        b64key[o + 3] = n > 2 ? WS_B64[v & 0x3f] : '=';
    }
    b64key[24] = 0; /* 16 bytes -> 24 base64 chars; must terminate */

    char req[512];
    int rlen = snprintf(req, sizeof(req),
                        "GET %s HTTP/1.1\r\n"
                        "Host: %s:%d\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        "Sec-WebSocket-Key: %s\r\n"
                        "Sec-WebSocket-Version: 13\r\n"
                        "\r\n",
                        path, host, port, b64key);
    if (ws_send_all(ws, req, (size_t)rlen) != 0) return -1;

    char resp[1024];
    size_t total = 0;
    while (total < sizeof(resp) - 1) {
        char c;
        if (ws_recv_all(ws, &c, 1) != 0) return -1;
        resp[total++] = c;
        if (total >= 4 && memcmp(resp + total - 4, "\r\n\r\n", 4) == 0) break;
    }
    resp[total] = 0;
	if (strncmp(resp, "HTTP/1.1 101", 12) != 0 && strncmp(resp, "HTTP/1.0 101", 12) != 0) {
#ifdef WS_TRACE
		{
			FILE *f = fopen("C:\\ProgramData\\ws_trace.log", "a");
			if (f) { fprintf(f, "resp: %.80s\n", resp); fclose(f); }
		}
#endif
		return -1;
	}
    return 0;
}

static void ws_close(ws_t *ws) {
    if (ws->fd != WS_INVALID) {
        ws_send(ws, "", 0, 0x8);
        ws_closesock(ws->fd);
        ws->fd = WS_INVALID;
    }
}

#endif
