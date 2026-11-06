# -*- coding: utf-8 -*-
"""Harness 原生 Web UI 的局域网入口（2026-10-02 用户要求）：控制台服务里的一个小反向代理。

dsh 自己只肯听 127.0.0.1（它能执行代码，`--host 0.0.0.0` 直接拒绝启动），所以不让它对外，而是：
  局域网浏览器 → 10.0.0.47:<lan_port>（本代理）→ 127.0.0.1:<web_port>（dsh）
代理在转发前做三件事：
  · 只放行管理员：统一身份下要 SWBRAIN.ADMIN（看控制台的会话 cookie swb_session——cookie 按主机不按端口，
    在 8766 登录过，到这个端口浏览器会带上）；共享口令下要管理员口令 cookie swb_admin。别人一律 403。
  · 防跨站：带 Origin 的请求，Origin 必须就是本代理自己；
  · 改写 Host / Origin / Referer 成 127.0.0.1:<web_port>（dsh 的 /api 与 WebSocket 只认回环主机），
    并去掉控制台自己的 cookie（swb_*），不交给 dsh；回来的 Location 改回本代理地址。
dsh 自己的一次性 token 与签名 cookie 照常起作用：管理员还得拿到控制台给的带 token 地址才进得去。
每条连接只转发一个请求（Connection: close），WebSocket 升级后双向原样转发。
"""
from __future__ import annotations

import re
import socket
import threading

MAX_HEAD = 64 * 1024
_CONSOLE_COOKIES = re.compile(r"^(swb_[A-Za-z0-9_]+)$")


def _read_head(sock: socket.socket) -> tuple[bytes, bytes] | None:
    """读到空行为止：返回 (头, 已经多读进来的正文)。连接断了或头太大返回 None。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(8192)
        if not chunk or len(buf) > MAX_HEAD:
            return None
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _parse(head: bytes) -> tuple[str, list[tuple[str, str]]]:
    lines = head.decode("latin-1").split("\r\n")
    hdrs = []
    for ln in lines[1:]:
        k, sep, v = ln.partition(":")
        if sep:
            hdrs.append((k.strip(), v.strip()))
    return lines[0], hdrs


def _get(hdrs, name: str) -> str | None:
    n = name.lower()
    return next((v for k, v in hdrs if k.lower() == n), None)


def _strip_console_cookies(v: str) -> str:
    keep = [p.strip() for p in v.split(";") if p.strip() and not _CONSOLE_COOKIES.match(p.strip().partition("=")[0].strip())]
    return "; ".join(keep)


def rewrite_request(head: bytes, upstream: str, public: str) -> bytes:
    """改写请求头：Host / Origin / Referer 换成上游（dsh）；去掉控制台的 cookie；普通请求强制 Connection: close。"""
    first, hdrs = _parse(head)
    upgrade = (_get(hdrs, "Upgrade") or "").lower() == "websocket"
    out = []
    for k, v in hdrs:
        kl = k.lower()
        if kl == "host":
            v = upstream
        elif kl in ("origin", "referer"):
            v = v.replace(f"://{public}", f"://{upstream}", 1)
        elif kl == "cookie":
            v = _strip_console_cookies(v)
            if not v:
                continue
        elif kl in ("connection", "keep-alive", "proxy-connection") and not upgrade:
            continue
        out.append(f"{k}: {v}")
    if not upgrade:
        out.append("Connection: close")
    return ("\r\n".join([first, *out]) + "\r\n\r\n").encode("latin-1")


def rewrite_response(head: bytes, upstream: str, public: str) -> bytes:
    """回来的 Location 指着 127.0.0.1:<web_port> 的，改回本代理地址（不然浏览器跳到他自己电脑上）。"""
    first, hdrs = _parse(head)
    out = [f"{k}: {v.replace(f'://{upstream}', f'://{public}', 1) if k.lower() == 'location' else v}" for k, v in hdrs]
    return ("\r\n".join([first, *out]) + "\r\n\r\n").encode("latin-1")


def _pipe(src: socket.socket, dst: socket.socket):
    try:
        while True:
            b = src.recv(65536)
            if not b:
                break
            dst.sendall(b)
    except OSError:
        pass
    finally:
        for s in (dst, src):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _reply(sock: socket.socket, code: int, reason: str, html: str):
    body = html.encode("utf-8")
    sock.sendall((f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
                  "Cache-Control: no-store\r\nConnection: close\r\n\r\n").encode("latin-1") + body)


def _page(title: str, msg: str, console: str) -> str:
    return (f"<!doctype html><meta charset=utf-8><title>{title}</title><body style='font:15px system-ui;margin:40px;max-width:640px'>"
            f"<h2>{title}</h2><p>{msg}</p><p><a href='{console}'>回控制台</a></p></body>")


class LanProxy:
    """allow(cookie_header) -> (放行?, 不放行的说明)；console_url(请求里的 Host) -> 控制台地址（给出错页的链接用）。"""

    def __init__(self, lan_port: int, web_port: int, allow, console_url, on_open=None, bind: str = "0.0.0.0"):
        self.lan_port, self.web_port, self.allow, self.console_url, self.on_open, self.bind = lan_port, web_port, allow, console_url, on_open, bind
        self.upstream = f"127.0.0.1:{web_port}"
        self.sock: socket.socket | None = None

    def start(self) -> "LanProxy":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((self.bind, self.lan_port)); s.listen(64)
        self.sock = s; self.lan_port = s.getsockname()[1]
        threading.Thread(target=self._accept, name="dsh-lan-proxy", daemon=True).start()
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _accept(self):
        while self.sock is not None:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c: socket.socket):
        up = None
        try:
            c.settimeout(30)
            got = _read_head(c)
            if got is None:
                return
            head, rest = got
            _, hdrs = _parse(head)
            public = (_get(hdrs, "Host") or "").strip()
            console = self.console_url(public)
            ok, why = self.allow(_get(hdrs, "Cookie") or "")
            if not ok:
                _reply(c, 403, "Forbidden", _page("只有管理员能从局域网打开 Harness 原生 Web UI", why, console)); return
            origin = _get(hdrs, "Origin")
            if origin is not None and origin not in (f"http://{public}", f"https://{public}"):
                _reply(c, 403, "Forbidden", _page("来源不对", "这个请求不是从本页面发出的（Origin 不符），已拒绝。", console)); return
            try:
                up = socket.create_connection(("127.0.0.1", self.web_port), timeout=5)
            except OSError:
                _reply(c, 502, "Bad Gateway", _page("Harness 原生 Web UI 还没启动", "请回控制台点「在 Harness 打开」，它会先把 Web UI 拉起来（十几秒）再给你带 token 的地址。", console)); return
            upgrade = (_get(hdrs, "Upgrade") or "").lower() == "websocket"
            if upgrade and self.on_open:
                self.on_open()
            c.settimeout(None); up.settimeout(None)
            up.sendall(rewrite_request(head, self.upstream, public) + rest)
            t = threading.Thread(target=_pipe, args=(c, up), daemon=True); t.start()      # 请求正文（与 WebSocket 上行）先接着转
            got = _read_head(up)
            if got is None:
                return
            rhead, rrest = got
            c.sendall(rewrite_response(rhead, self.upstream, public) + rrest)
            _pipe(up, c)
            t.join(timeout=5)
        except OSError:
            pass
        finally:
            for s in (c, up):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
