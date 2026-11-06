"""HTTP 层的协议行为：鉴权覆盖全部方法、错误响应发得出去、cookie 只在该种的时候种。

用一个桩 state 起服务，不装载真实数据，所以在 CI 上也能跑。
2026-09-23 评审实测过的四件事，这里逐条钉住：
  1. HEAD 没被覆盖 → 完全绕过口令（GET 401 / HEAD 200）
  2. log_message 假定第一个参数是请求行 → send_error 之前就抛 TypeError → 畸形请求、404、DELETE 一律零字节
  3. end_headers 直接读 self.path → 请求行畸形时抛 AttributeError，同样零字节
  4. 种 cookie 只看 "key" in qs → 带一个错的 ?key= 能把已登录用户的有效 cookie 覆盖掉
"""
import socket
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from swbrain import serve

KEY = "test-key-123"


class _StubState:
    """make_handler 在鉴权与静态文件这条路上只用到 cfg。"""
    cfg = {"serve": {"access_key": KEY}}


@pytest.fixture(scope="module")
def srv():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(_StubState()))
    th = threading.Thread(target=httpd.serve_forever, daemon=True); th.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def raw(port, req: bytes, timeout=6.0) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout); s.settimeout(timeout)
    try:
        s.sendall(req)
        buf = b""
        while len(buf) < 200000:
            try:
                x = s.recv(65536)
            except socket.timeout:
                break
            if not x:
                break
            buf += x
        return buf
    finally:
        s.close()


def status(port, req: bytes) -> int:
    r = raw(port, req)
    assert r, "服务端一个字节都没回，连接被直接断开"
    line = r.split(b"\r\n")[0].decode("latin-1")
    assert line.startswith("HTTP/"), f"不是 HTTP 响应：{line[:60]}"
    return int(line.split()[1])


def _req(method, path, *, key=None, cookie=None, version="HTTP/1.1"):
    p = path + (("?key=" + key) if key else "")
    h = f"{method} {p} {version}\r\nHost: t\r\nConnection: close\r\n"
    if cookie:
        h += f"Cookie: {cookie}\r\n"
    return (h + "\r\n").encode()


# ---- 1 鉴权覆盖全部方法 ----

def test_get_without_key_is_401(srv):
    assert status(srv, _req("GET", "/index.html")) == 401


def test_head_without_key_is_401(srv):
    """HEAD 曾经完全绕过鉴权：同一路径 GET 401、HEAD 200。"""
    assert status(srv, _req("HEAD", "/index.html")) == 401


def test_head_with_key_is_200(srv):
    assert status(srv, _req("HEAD", "/index.html", key=KEY)) == 200


def test_api_without_key_is_401(srv):
    assert status(srv, _req("GET", "/api/health")) == 401


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_methods_are_405_not_silence(srv, method):
    assert status(srv, _req(method, "/", key=KEY)) == 405


# ---- 2 / 3 错误响应必须发得出去 ----

def test_missing_static_file_is_404(srv):
    assert status(srv, _req("GET", "/no-such-file-here.txt", key=KEY)) == 404


def test_unknown_api_is_404(srv):
    assert status(srv, _req("GET", "/api/no-such-endpoint", key=KEY)) == 404


def test_bad_method_token_is_501(srv):
    assert status(srv, _req("GARBAGE", "/")) == 501


def test_bad_http_version_is_505(srv):
    assert status(srv, _req("GET", "/", version="HTTP/9.9")) == 505


def test_malformed_request_line_still_answers(srv):
    assert status(srv, b"GARBAGE\r\n\r\n") == 400


def test_oversized_request_line_is_414(srv):
    assert status(srv, b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\nHost: t\r\n\r\n") == 414


# ---- 4 cookie 只在靠 ?key= 过的时候种 ----

def _set_cookie(port, req):
    head = raw(port, req).split(b"\r\n\r\n")[0].decode("latin-1", "replace")
    got = [l for l in head.split("\r\n") if l.lower().startswith("set-cookie")]
    return got[0] if got else None


def test_correct_key_in_url_sets_cookie(srv):
    sc = _set_cookie(srv, _req("GET", "/index.html", key=KEY))
    assert sc and KEY in sc and "HttpOnly" in sc


def test_wrong_key_does_not_overwrite_good_cookie(srv):
    """带一个错的 ?key= 不能把已登录用户的有效 cookie 换掉（否则 30 天进不来）。"""
    req = _req("GET", "/index.html", key="WRONG-KEY", cookie=f"swb_key={KEY}")
    assert status(srv, req) == 200                      # cookie 仍然有效，放行
    assert _set_cookie(srv, req) is None                # 但不许重新种


def test_cookie_only_request_does_not_reset_cookie(srv):
    assert _set_cookie(srv, _req("GET", "/index.html", cookie=f"swb_key={KEY}")) is None


# ---- key_source 的纯函数判定 ----

def test_key_source_reports_how_it_passed():
    cfg = {"serve": {"access_key": "abc 123"}}
    assert serve.key_source({}, None, {}) == "open"
    assert serve.key_source(cfg, None, {"key": ["abc 123"]}) == "query"
    assert serve.key_source(cfg, "swb_key=abc%20123", {}) == "cookie"
    assert serve.key_source(cfg, "swb_key=abc 123", {"key": ["wrong"]}) == "cookie"     # 错的 key 不算，cookie 兜住
    assert serve.key_source(cfg, None, {"key": ["wrong"]}) == ""
