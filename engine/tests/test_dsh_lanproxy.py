"""Harness 原生 Web UI 的局域网入口（swbrain/dsh_lanproxy.py，2026-10-02）。

锁住的行为：
  · 只放管理员：allow 说不行就 403，不碰上游；
  · 带 Origin 的请求，Origin 必须是本代理自己（防跨站）；
  · 转给 dsh 的 Host / Origin 是 127.0.0.1:<web_port>（dsh 只认回环），控制台的 swb_* cookie 不交给 dsh；
  · 回来的 Location 改回代理地址；WebSocket 升级后双向原样转发；上游没起给 502 说明。
"""
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swbrain import dsh_lanproxy as P


def _upstream(handler):
    """假 dsh：收一个请求头，交给 handler(头, 连接)。"""
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8)
    seen = []
    def run():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            head, rest = P._read_head(c)
            seen.append(head.decode("latin-1"))
            handler(head, rest, c)
    threading.Thread(target=run, daemon=True).start()
    return s, seen


def _proxy(web_port, ok=True):
    return P.LanProxy(0, web_port, lambda ck: (ok and "swb_session=good" in ck, "要管理员"), lambda h: "http://10.0.0.47:8766/", bind="127.0.0.1").start()


def _send(port, raw: bytes) -> bytes:
    c = socket.create_connection(("127.0.0.1", port), timeout=5); c.sendall(raw)
    out = b""
    while True:
        b = c.recv(65536)
        if not b:
            break
        out += b
    c.close(); return out


def test_http_forward_and_rewrites():
    def h(head, rest, c):
        c.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:%d/next\r\nContent-Length: 2\r\n\r\nok" % up.getsockname()[1]); c.close()
    up, seen = _upstream(h)
    px = _proxy(up.getsockname()[1])
    pub = f"127.0.0.1:{px.lan_port}"
    r = _send(px.lan_port, (f"GET /?token=t HTTP/1.1\r\nHost: {pub}\r\nOrigin: http://{pub}\r\nCookie: swb_session=good; dsh-auth-x=abc\r\nConnection: keep-alive\r\n\r\n").encode())
    assert r.startswith(b"HTTP/1.1 302") and f"Location: http://{pub}/next".encode() in r and r.endswith(b"ok")
    fwd = seen[0]
    assert f"Host: 127.0.0.1:{up.getsockname()[1]}" in fwd and f"Origin: http://127.0.0.1:{up.getsockname()[1]}" in fwd
    assert "swb_session" not in fwd and "dsh-auth-x=abc" in fwd and "Connection: close" in fwd and "keep-alive" not in fwd
    px.close(); up.close()


def test_denied_and_bad_origin_never_reach_upstream():
    up, seen = _upstream(lambda head, rest, c: c.close())
    px = _proxy(up.getsockname()[1])
    pub = f"127.0.0.1:{px.lan_port}"
    assert _send(px.lan_port, f"GET / HTTP/1.1\r\nHost: {pub}\r\nCookie: swb_session=bad\r\n\r\n".encode()).startswith(b"HTTP/1.1 403")
    assert _send(px.lan_port, f"POST /api HTTP/1.1\r\nHost: {pub}\r\nOrigin: http://evil.test\r\nCookie: swb_session=good\r\n\r\n".encode()).startswith(b"HTTP/1.1 403")
    assert seen == []
    px.close(); up.close()


def test_websocket_upgrade_is_piped():
    def h(head, rest, c):
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        b = rest or c.recv(100); c.sendall(b"echo:" + b); c.close()
    up, seen = _upstream(h)
    px = _proxy(up.getsockname()[1])
    pub = f"127.0.0.1:{px.lan_port}"
    r = _send(px.lan_port, f"GET /ws HTTP/1.1\r\nHost: {pub}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nCookie: swb_session=good\r\n\r\nPING".encode())
    assert r.startswith(b"HTTP/1.1 101") and r.endswith(b"echo:PING")
    assert "Connection: Upgrade" in seen[0] and "Connection: close" not in seen[0]
    px.close(); up.close()


def test_upstream_down_gives_502():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); dead = s.getsockname()[1]; s.close()
    px = _proxy(dead)
    r = _send(px.lan_port, f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{px.lan_port}\r\nCookie: swb_session=good\r\n\r\n".encode())
    assert r.startswith(b"HTTP/1.1 502") and "在 Harness 打开".encode() in r
    px.close()
