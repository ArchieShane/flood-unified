"""HTTP 层：口令与 cookie、静态文件（console/）、把 /api/* 分发到 routes.py 的接口表。
算什么在 state.py，接口声明与参数解析在 routes.py；这里只管请求 / 响应与错误码。"""
from __future__ import annotations
import argparse
import hmac
import html
import json
import secrets
import math
import sys
import threading
import time
import socket
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

from . import configcheck, db, identity, routes
from .ontology import runtime as ontrt
from .state import State                                  # re-export：老的 from swbrain.serve import State 照旧

ROOT = Path(__file__).resolve().parents[1]

COOKIE = "swb_key"
MAX_BODY_BYTES = 8 * 1024 * 1024          # POST 正文上限 8 MB
LOGIN_HTML = """<!doctype html><meta charset="utf-8"><title>会思考的排水系统</title>
<body style="font-family:system-ui;background:#f4f6f8;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<form onsubmit="document.cookie='%s='+encodeURIComponent(document.getElementById('k').value)+'; path=/; max-age=2592000; SameSite=Lax'; location.replace(location.pathname+location.search); return false"
 style="background:#fff;padding:24px 28px;border-radius:12px;box-shadow:0 8px 24px -12px rgba(0,0,0,.25);min-width:320px">
<h2 style="margin:0 0 6px;font-size:18px">会思考的排水系统 · 阿布扎比</h2><div style="color:#66717f;font-size:13px;margin-bottom:14px">请输入访问口令 / Enter the access key</div>
<input id="k" type="password" autofocus style="width:100%%;padding:8px 10px;border:1px solid #d3dce3;border-radius:8px;font-size:14px;box-sizing:border-box">
<button style="margin-top:12px;width:100%%;padding:8px;border:0;border-radius:8px;background:#0b5fd7;color:#fff;font-size:14px;cursor:pointer">进入 / Enter</button></form>"""

def key_ok(cfg: dict, cookie_header: str | None, query: dict) -> bool:
    """[serve].access_key 为空 = 不设口令；否则 cookie swb_key 或 ?key= 与之相等才放行。"""
    return bool(key_source(cfg, cookie_header, query))


def key_source(cfg: dict, cookie_header: str | None, query: dict) -> str:
    """放行的话说明是靠什么过的：'open'（没设口令）/ 'query'（地址里带对了）/ 'cookie'；没过给空串。"""
    want = str((cfg.get("serve") or {}).get("access_key") or "").strip()
    if not want:
        return "open"
    q = (query.get("key") or [None])[0]
    if q is not None and hmac.compare_digest(str(q), want):
        return "query"
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and hmac.compare_digest(unquote(v), want):
            return "cookie"
    return ""



class DualStackServer(ThreadingHTTPServer):
    """同时收 IPv4 与 IPv6。只绑 0.0.0.0 时 localhost 会先解析到 ::1，连不上再回退，
    curl 要多花 0.2 s、Python 的 urllib 要多花 2 s——而演示地址用的就是 localhost（2026-09-23 修）。"""
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        return super().server_bind()


class LiteModeError(ontrt.ActionError, AttributeError):
    """既是动作错误（接口回 400 + 说明）又是属性错误（getattr(state, x, 缺省) 照常退回缺省）。"""


class LiteState:
    """精简模式的服务状态：只有配置。需要运行产物的接口会报「精简模式」，静态页面、C1 结果、管理端计算队列照常。"""
    lite = True

    def __init__(self, cfg: dict):
        self.cfg = cfg; self.agent = None; self.multi = None; self.ontology = None
        self.loaded_at = time.strftime("%Y-%m-%d %H:%M:%S")

    def __getattr__(self, name):
        raise LiteModeError("精简模式：还没有运行产物（out/state/、out/summary.json），这个功能要先放好运行产物或跑 python -m swbrain.run，再重启服务")


def serve_on(host: str, port: int, handler, tls: tuple[str, str] | None = None):
    """能双栈就双栈，这台机器没有 IPv6 就退回 IPv4。tls = (证书, 私钥) 时走 HTTPS（统一身份的回调登记的是 https）。"""
    srv = bound = None
    if host in ("0.0.0.0", "::"):
        try:
            srv, bound = DualStackServer(("::", port), handler), "::（IPv4 + IPv6）"
        except OSError:
            pass
    if srv is None:
        srv, bound = ThreadingHTTPServer((host, port), handler), host
    if tls:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(tls[0], tls[1])
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        bound += "（HTTPS）"
    return srv, bound


def tls_files(cfg: dict) -> tuple[str, str] | None:
    """[serve].tls_cert / tls_key（相对仓库根）。都空 = 不开 HTTPS。只配了一个、或文件不在，报错。"""
    SV = cfg.get("serve") or {}
    c, k = str(SV.get("tls_cert") or "").strip(), str(SV.get("tls_key") or "").strip()
    if not c and not k:
        return None
    pc, pk = (ROOT / c) if c else None, (ROOT / k) if k else None
    if not (pc and pk and pc.exists() and pk.exists()):
        raise ValueError(f"[serve].tls_cert / tls_key 要成对配、文件要在：{c or '（空）'} / {k or '（空）'}；本机自签：python -m swbrain.tlscert")
    return str(pc), str(pk)


def _admin_routes_audit(op: str, who):
    from .agents import admin_routes
    admin_routes._audit(op, {"who": who.actor, "name": who.name})


def _admin_routes_audit_raw(op: str, detail: dict):
    from .agents import admin_routes
    admin_routes._audit(op, detail)


def _bearer(headers) -> str:
    h = str(headers.get("Authorization") or "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def _admin_cookie_ok(state, headers) -> bool:
    from .agents import admin_routes
    return admin_routes.is_admin(state, routes.Req(path="/", headers=headers))


def lan_admin_check(cfg: dict, idn: "identity.Identity | None"):
    """局域网入口（Harness 原生 Web UI）的放行判定：只认管理员。cookie 按主机不按端口，控制台登录过，这边浏览器会带上。"""
    def allow(cookie_header: str) -> tuple[bool, str]:
        if idn is not None and idn.sdip:
            try:
                p = idn.principal(identity.cookie(cookie_header, identity.SESSION_COOKIE))
            except identity.IdentityUnavailable:
                return False, "身份服务暂时连不上，稍后再试。"
            except identity.IdentityError:
                p = None
            if p is None:
                return False, "还没登录：请先在控制台（同一台服务器的 8766 端口）用平台账号登录，再回控制台点「在 Harness 打开」。"
            return (True, "") if p.has(identity.ADMIN) else (False, "你的账号没有 SWBRAIN.ADMIN（系统管理）职能。它能在服务器上执行代码，所以局域网只开给管理员；请用控制台里的对话。")
        want = str((cfg.get("serve") or {}).get("admin_key") or "").strip()
        got = ""
        for part in cookie_header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "swb_admin":
                got = unquote(v)
        if want and got and hmac.compare_digest(got.encode(), want.encode()):
            return True, ""
        return False, "要管理员：请先在控制台的管理端（/admin.html）用管理员口令登录，再回控制台点「在 Harness 打开」。"
    return allow


def lan_proxy(cfg: dict, idn, host: str):
    """服务听局域网、且 [agent.dsh].lan_port 非 0 时，起 Harness 原生 Web UI 的局域网入口（swbrain/dsh_lanproxy.py）。"""
    from . import dsh_bridge, dsh_lanproxy
    st = dsh_bridge.settings(cfg)
    if host == "127.0.0.1" or not st["enabled"] or not st["lan_port"] or not dsh_bridge._lan_hosts(cfg):
        return None
    def audit():
        try:
            _admin_routes_audit_raw("harness_lan_open", {"note": "局域网入口建立 WebSocket 会话"})
        except Exception:
            pass
    try:
        px = dsh_lanproxy.LanProxy(st["lan_port"], st["web_port"], lan_admin_check(cfg, idn),
                                   lambda h: f"http://{h.split(':')[0]}:{int((cfg.get('serve') or {}).get('port') or 8766)}/", on_open=audit).start()
    except OSError as ex:
        print(f"  Harness 原生 Web UI 局域网入口没起来（端口 {st['lan_port']}：{ex}）", flush=True)
        return None
    print(f"  Harness 原生 Web UI 局域网入口：{', '.join(f'http://{h}:{px.lan_port}/' for h in dsh_bridge._lan_hosts(cfg))}（只放管理员，转到 127.0.0.1:{st['web_port']}）", flush=True)
    return px


def make_handler(state: State, idn: "identity.Identity | None" = None):
    IDN = idn or getattr(state, "identity", None) or identity.Identity(state.cfg)
    try:
        state.identity = IDN                            # 处理函数（管理端、智能体）经 state.identity 拿到同一个实例
    except Exception:
        pass

    class H(SimpleHTTPRequestHandler):
        # 请求行畸形或版本号不认识时，Python 3.12 的 parse_request 在设 request_version 之前就 send_error，
        # 此时还是默认的 HTTP/0.9 —— 0.9 的响应没有状态行，只回一段 HTML，客户端拿不到 400 / 505。
        # 3.14 本地测不出来，CI（3.12）上 test_serve_protocol 两条挂过（2026-09-26）。
        default_request_version = "HTTP/1.1"

        def __init__(self, *a, **kw):
            self._set_cookie = None
            super().__init__(*a, directory=str(ROOT / "console"), **kw)

        def end_headers(self):
            # 请求行畸形时 parse_request 还没来得及设 self.path，直接读会抛 AttributeError，
            # 把整个错误响应带崩（客户端拿到空字节串）。2026-09-23 修。
            if not getattr(self, "path", "").startswith("/api/"):
                self.send_header("Cache-Control", "no-store")
            if getattr(self, "_set_cookie", None):
                self.send_header("Set-Cookie", self._set_cookie); self._set_cookie = None
            super().end_headers()

        def log_message(self, fmt, *args):
            # send_error() 走的是 log_error("code %d, message %s", HTTPStatus, str)，第一个参数不是请求行。
            # 原来直接 `"/api/" in args[0]` 会抛 TypeError，而这一句在写响应之前——畸形请求、404、非 GET/POST
            # 方法全都拿不到任何回应，连接被直接断开（2026-09-23 修）。顺带：所有请求都记，不只 /api/。
            import re as _re
            safe = tuple(_re.sub(r"(key=)[^&\s]+", r"\1***", a) if isinstance(a, str) else a for a in args)   # 口令不进日志
            try:
                super().log_message(fmt, *safe)
            except Exception:                                    # 日志绝不能把响应带崩
                pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False, default=lambda o: None if isinstance(o, float) and math.isnan(o) else str(o)).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def _raw(self, r: routes.Raw):
            self.send_response(r.code); self.send_header("Content-Type", r.content_type); self.send_header("Content-Length", str(len(r.body)))
            for k, v in (r.headers or {}).items():
                self.send_header(k, v)
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(r.body)

        def _auth(self, qs, is_api: bool) -> bool:
            """认人并把主体绑到这个请求（identity.current()）。shared_key 模式照旧看口令；sdip 模式只认会话 / 服务账号 / 委托凭据，口令一律不认。"""
            self._who = None
            if IDN.sdip:
                return self._auth_sdip(is_api)
            src = key_source(state.cfg, self.headers.get("Cookie"), qs)
            if src:
                d = IDN.from_delegate(_bearer(self.headers))                  # MCP 桥带回来的委托凭据：记成发起对话的那个人
                self._who = d or identity.local_principal("console", admin=_admin_cookie_ok(state, self.headers))
            if src:
                if src == "query":            # 只有靠地址里的 key 过的才种 30 天 cookie。
                    # 不能只看 "key" in qs：那样带一个错的 ?key= 也会把已登录用户的有效 cookie 覆盖掉，
                    # 把人锁在系统外 30 天（2026-09-23 修）。
                    self._set_cookie = f"{COOKIE}={quote(qs['key'][0])}; Path=/; Max-Age=2592000; SameSite=Lax; HttpOnly"
                return True
            return self._auth_fail(is_api)

        def _auth_sdip(self, is_api: bool) -> bool:
            path = urlparse(self.path).path
            try:
                tok = _bearer(self.headers)
                if tok:
                    p = IDN.from_delegate(tok, fresh=False)
                    if p is None and path.startswith("/api/feed/"):
                        p = IDN.bearer(tok)                                     # 服务账号只能推外部源
                    if p is None:
                        # 平台把一次问数转给本条能力时带过来的令牌。⚠️ 排在最后：
                        # 委托凭据（dlg_）与客户端凭据都是本系统自己发的，先认它们，
                        # 认不下来才去核「这是不是平台换过域的那一张」。
                        # 顺序反过来时，一条形态不对的 tok 会先被当成平台令牌去查会话，
                        # 而那句报错说的是「会话没给出主体」——与真因（凭据类型不对）差一层。
                        # ⚠️ 它认不下来时**抛**，不返回 None —— 那个异常照旧落进下面
                        #    那个 except IdentityError，结果同样是 401，少一次「到底哪种凭据」的判断
                        p = IDN.受平台转发(tok, self.headers.get("X-Sdip-Tenant"))
                else:
                    sid = identity.cookie(self.headers.get("Cookie"), identity.SESSION_COOKIE)
                    p = IDN.principal(sid)
                    if p is not None:
                        p.refresh = (lambda sid=sid: IDN.principal(sid, fresh=True)); p.session_ref = sid
            except identity.IdentityUnavailable as ex:
                self._json({"error": "身份服务暂时连不上，稍后再试", "auth": "unavailable", "detail": str(ex)[:120]}, 503); return False
            except identity.IdentityError:
                p = None
            if p is None:
                # 没登录：页面（/ 与 *.html）跳到登录，登录完回到原页面；接口与静态资源回 401 JSON。原来只认首页与管理端，别的页面拿到一段 JSON（2026-10-01）
                if not is_api and self.command in ("GET", "HEAD") and (path == "/" or path.endswith(".html")):
                    self.send_response(302); self.send_header("Location", "/auth/login?next=" + quote(path)); self.send_header("Content-Length", "0")
                    super().end_headers(); return False
                return self._auth_fail(is_api, sdip=True)
            if p.kind == "user" and not p.has(identity.VIEW):
                self._json({"error": "这个账号在本租户没有排水智能体的职能（SWBRAIN.VIEW），请找管理员授权", "auth": "forbidden"}, 403); return False
            if p.kind == "service" and not path.startswith("/api/feed/"):
                self._json({"error": "服务账号只能推外部源", "auth": "forbidden"}, 403); return False
            self._who = p
            return True

        def _auth_fail(self, is_api: bool, sdip: bool = False) -> bool:
            if sdip:
                self._json({"error": "需要登录", "auth": "required", "login_url": "/auth/login"}, 401); return False
            if is_api:
                self._json({"error": "需要访问口令：在地址后加 ?key=… 或先打开首页输入", "auth": "required"}, 401)
            else:
                body = (LOGIN_HTML % COOKIE).encode("utf-8")
                self.send_response(401); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store"); super().end_headers(); self.wfile.write(body)
            return False

        def _dispatch(self, method: str, qs: dict, body: dict):
            """查接口表 → 调处理函数 → 统一响应与错误码。没有匹配的 /api/ 路径给 404。"""
            path = urlparse(self.path).path
            r = routes.resolve(method, path)
            if r is None:
                if path.startswith("/api/") or path == "/api":
                    self._json({"error": "未知接口"}, 404); return True
                return False                                            # 交给静态文件
            req = routes.Req(path=path, method=method, headers=self.headers)      # 处理函数只看得到路径 / 方法 / 请求头
            tok = identity.bind(getattr(self, "_who", None))                     # 处理函数里 identity.current() = 这个请求的人
            try:
                need = routes.needs(r, method, path, qs) if IDN.sdip else None   # 共享口令模式不分职能（管理端另凭 admin_key）
                who = identity.current()
                if need and who is not None:
                    if method == "POST" and who.refresh is not None:
                        fresh = who.refresh()                                     # 写之前现查：撤销立即生效
                        if fresh is None:
                            self._json({"error": "会话已结束，请重新登录", "auth": "required", "login_url": "/auth/login"}, 401); return True
                        fresh.refresh = who.refresh; who = fresh; identity._current.set(who)
                    if not who.has(need):
                        self._json({"error": f"要职能 {need}（{identity.FUNCTIONS.get(need, need)}）", "auth": "forbidden", "need": need}, 403); return True
                out = r.fn(state, qs, body, req)
            except identity.IdentityUnavailable as ex:
                self._json({"error": "身份服务暂时连不上，稍后再试", "auth": "unavailable", "detail": str(ex)[:120]}, 503); return True
            except ontrt.ActionError as ex:
                self._json({"error": str(ex)}, 400); return True
            except Exception as ex:
                self._json({"error": str(ex)[:300]}, 500); return True
            finally:
                identity.unbind(tok)
            if isinstance(out, routes.Raw):
                self._raw(out)
            elif isinstance(out, tuple):
                self._json(out[0], out[1])
            else:
                self._json(out)
            return True

        def do_HEAD(self):
            u = urlparse(self.path); qs = parse_qs(u.query)
            if not self._auth(qs, u.path.startswith("/api/") or u.path == "/api"):
                return                                            # 2026-09-23 修：原来 HEAD 不鉴权，能免口令枚举 console/data 下的文件
            return super().do_HEAD()

        def _reject_method(self):
            self._json({"error": f"不支持的方法 {self.method_name()}"}, 405)

        def method_name(self):
            return str(self.command or "")

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _reject_method

        # ---- 统一身份登录（只在 [identity].mode = sdip 时有；授权码 + PKCE，令牌只在服务端）
        def _redirect(self, to: str, cookies: list[str] = ()):
            self.send_response(302); self.send_header("Location", to); self.send_header("Content-Length", "0")
            for c in cookies:
                self.send_header("Set-Cookie", c)
            self.send_header("Cache-Control", "no-store"); super().end_headers()

        def _base(self) -> str:
            """这个请求进来的对外地址（只认 public_bases 里登记过的）。"""
            return IDN.cfg.base_for(self.headers.get("Host"))

        def _secure(self) -> str:
            return "; Secure" if self._base().startswith("https://") else ""

        def _login_page(self, msg: str, code: int = 400, relogin: bool = False):
            """msg 由调用方拼好（里面的用户名已转义）。relogin：给「换个账号登录」——强制重新输入，不沿用浏览器里那个账号的会话。"""
            link = '<a href="/auth/login?switch=1">换个账号登录</a>' if relogin else '<a href="/auth/login">重新登录</a>'
            body = (f'<!doctype html><meta charset="utf-8"><title>登录</title><body style="font-family:system-ui;padding:40px;line-height:1.7"><h3>登录没有完成</h3>'
                    f'<p>{msg}</p><p>{link}</p>').encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store"); super().end_headers(); self.wfile.write(body)

        def _auth_routes(self, method: str, u, qs) -> bool:
            p = u.path
            if not p.startswith("/auth/"):
                return False
            if not IDN.sdip:
                self._json({"error": "没有启用统一身份（[identity].mode = shared_key）"}, 404); return True
            if p == "/auth/login" and method == "GET":
                verifier, challenge = identity.new_pkce(); binding = secrets.token_urlsafe(32)
                try:
                    cb = self._base() + "/auth/callback"           # 浏览器从哪个地址来，回调就回哪个（本机 / 局域网各登记了一条）
                    st = IDN.sessions.begin(verifier, identity.safe_next((qs.get("next") or ["/"])[0]), binding, cb)
                except identity.IdentityUnavailable as ex:
                    self._json({"error": str(ex)}, 503); return True
                url = IDN.authorize_url(st, challenge, cb) + ("&prompt=login" if qs.get("switch") else "")   # 换账号：Keycloak 要求重新输入，不沿用 SSO 会话
                self._redirect(url,
                               [f"{identity.LOGIN_COOKIE}={binding}; Path=/auth; Max-Age={identity.PENDING_TTL_S}; SameSite=Lax; HttpOnly{self._secure()}"])
                return True
            if p == "/auth/callback" and method == "GET":
                if qs.get("error"):
                    self._login_page("身份提供方返回 " + html.escape(qs["error"][0][:80])); return True
                pend = IDN.sessions.take((qs.get("state") or [""])[0], identity.cookie(self.headers.get("Cookie"), identity.LOGIN_COOKIE))
                if not pend:
                    self._login_page("登录请求已失效，或不是从这个浏览器发起的。请重新登录。"); return True
                try:
                    sid, who = IDN.finish_login((qs.get("code") or [""])[0], pend[0], pend[2] or None)
                except identity.Forbidden as ex:
                    u = getattr(ex, "username", "") or "（未知）"
                    _admin_routes_audit_raw("login_refused", {"username": u, "reason": str(ex)[:200]})
                    self._login_page(f"账号 <b>{html.escape(u)}</b> 在本租户没有排水智能体的职能（SWBRAIN.VIEW），进不来。<br>"
                                     "如果这不是你想用的账号：浏览器里还留着它的登录，点下面「换个账号登录」会要求重新输入用户名和口令。"
                                     "<br>如果就是这个账号：请平台给它授予 SWBRAIN.VIEW。", 403, relogin=True); return True
                except identity.IdentityUnavailable:
                    self._login_page("身份服务暂时连不上，稍后再试。", 503); return True
                except identity.IdentityError:
                    self._login_page("令牌没有通过校验。"); return True
                IDN.sessions.drop(identity.cookie(self.headers.get("Cookie"), identity.SESSION_COOKIE))     # 登录即作废这个浏览器原来的会话
                _admin_routes_audit("login_sdip", who)
                self._redirect(pend[1], [f"{identity.SESSION_COOKIE}={sid}; Path=/; SameSite=Lax; HttpOnly{self._secure()}",
                                         f"{identity.LOGIN_COOKIE}=; Path=/auth; Max-Age=0"])
                return True
            if p == "/auth/logout" and method == "POST":                  # 只收 POST：GET 注销能被任何一张 <img> 触发
                if not self._origin_ok():
                    self._json({"error": "来源不对"}, 403); return True
                target = IDN.logout(identity.cookie(self.headers.get("Cookie"), identity.SESSION_COOKIE), self._base())
                body = json.dumps({"redirect": target}).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
                self.send_header("Set-Cookie", f"{identity.SESSION_COOKIE}=; Path=/; Max-Age=0"); self.send_header("Cache-Control", "no-store")
                super().end_headers(); self.wfile.write(body); return True
            self._json({"error": "未知接口"}, 404); return True

        def _origin_ok(self) -> bool:
            """统一身份模式下，带会话 cookie 的写请求要 Origin 精确等于本服务（防跨站请求伪造）。Bearer 凭据不是浏览器自动带的，不查。"""
            if not IDN.sdip or _bearer(self.headers):
                return True
            o = str(self.headers.get("Origin") or "").rstrip("/")
            return bool(o) and o in IDN.cfg.all_bases()           # 本机与局域网地址各认各的（auto 时按本机当前的局域网地址）

        def _alias_redirect(self) -> bool:
            """统一身份下用 localhost / [::1] 打开：平台只登记了 127.0.0.1 的回调，登录回不来、写请求的 Origin 也对不上，整页跳到登记的本机地址。"""
            if not IDN.sdip:
                return False
            to = IDN.cfg.loopback_alias(self.headers.get("Host"))
            if not to:
                return False
            self._redirect(to + self.path); return True

        def do_GET(self):
            u = urlparse(self.path); qs = parse_qs(u.query)
            if self._alias_redirect():
                return
            if self._auth_routes("GET", u, qs):
                return
            if not self._auth(qs, u.path.startswith("/api/") or u.path == "/api"):
                return
            if self._dispatch("GET", qs, {}):
                return
            if u.path == "/":
                self.path = "/index.html"
            return super().do_GET()

        def do_POST(self):
            u = urlparse(self.path); qs = parse_qs(u.query)
            if self._auth_routes("POST", u, qs):
                return
            if not self._origin_ok():
                return self._json({"error": "来源（Origin）不是本服务，写请求被拒", "auth": "origin"}, 403)
            if not self._auth(qs, True):
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json({"error": "Content-Length 不是数字"}, 400)
            if n > MAX_BODY_BYTES:      # 不限长的话，声明一个 2 GB 的 Content-Length 就让服务端一次性申请 2 GB
                return self._json({"error": f"请求体太大：{n} 字节，上限 {MAX_BODY_BYTES}"}, 413)
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            except Exception as ex:
                return self._json({"error": f"请求体不是 JSON：{str(ex)[:120]}"}, 400)
            if not self._dispatch("POST", qs, body):
                self._json({"error": "未知接口"}, 404)
    return H


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default=None, help="缺省：设了 [serve].access_key 用 0.0.0.0（局域网可访问，凭口令），否则 127.0.0.1 只给本机看")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-warm", action="store_true", help="不在后台预热全部情景")
    a = ap.parse_args(argv)
    cfg = db.load_config(a.config)
    SV = cfg.get("serve") or {}
    for st in (sys.stdout, sys.stderr):                     # 日志按 UTF-8 写，Windows 重定向到文件时不再乱码
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:                                                                # 身份配置先查：装载状态要几十秒，配错了不必等到那时才报
        idn = identity.Identity(cfg)
        tls = tls_files(cfg)
        if idn.sdip and idn.cfg.public_base.startswith("https://") and not tls:
            raise ValueError(f"[identity].public_base 是 {idn.cfg.public_base}（平台登记的回调是 https），但 [serve] 没配 tls_cert / tls_key；"
                             "本机自签：python -m swbrain.tlscert，或请平台加一条 http 回调后把 public_base 改成 http")
    except ValueError as ex:
        print(f"配置不对：{ex}", flush=True)
        return 2
    print(f"  身份：{'统一身份 SDIP（' + idn.cfg.issuer + '，租户 ' + str(idn.cfg.tenant_id) + '）' if idn.sdip else '共享口令（[identity].mode = shared_key）'}", flush=True)
    # 统一身份：public_base 是本机回环（http://127.0.0.1…）就只监听本机——否则局域网上的人能用明文 http 连进来，登录回调和会话 cookie 都过网；
    # 要给局域网用，public_base 写局域网地址并配 [serve].tls_cert / tls_key（2026-10-01：本机继续用 http）
    # 登记的对外地址全是本机回环才只听本机；有一个局域网地址（extra_public_bases）就听全部网卡
    loopback = not idn.cfg.auto_lan and all((urlparse(b).hostname or "") in identity.LOOPBACK_HOSTS for b in idn.cfg.public_bases)
    host = a.host or ("127.0.0.1" if (idn.sdip and loopback) else "0.0.0.0" if (SV.get("access_key") or idn.sdip) else "127.0.0.1")
    try:                                                                # 配置漂移只警告不拦：config.toml 与 config.example.toml 的键集要一致
        cc = configcheck.check()
        if not cc.get("ok", True):
            print(f"  配置漂移：config 多出 {len(cc['extra'])} 个键 {cc['extra'][:6]}；示例多出 {len(cc['missing'])} 个键 {cc['missing'][:6]}（python -m swbrain.configcheck --sync）", flush=True)
    except Exception as ex:
        print("  配置检查失败：", str(ex)[:120], flush=True)
    print("装载状态 ...", flush=True)
    missing = [r for r in ("out/state/es.parquet", "out/state/cs.parquet", "out/state/zs.parquet", "out/state/props.json", "out/summary.json") if not (ROOT / r).exists()]
    if missing:
        # 精简模式（2026-09-30，部署包首次启动）：还没有 swbrain.run 的运行产物也能起服务——控制台 C1 页签（一二维结果）与管理端的计算队列能用，
        # 研判类页签与智能体问答要等运行产物放进来（或跑 python -m swbrain.run）后重启
        state = LiteState(cfg)
        print(f"精简模式：缺运行产物 {missing[:3]}{' 等' if len(missing) > 3 else ''}——只开 C1 一二维结果页与管理端；放好运行产物或跑 python -m swbrain.run 后重启 → http://127.0.0.1:{a.port}/", flush=True)
    else:
        state = State(cfg)
        print(f"就绪：{len(state.es):,} 段、{len(state.props)} 条建议，装载 {state.load_s}s → http://127.0.0.1:{a.port}/ {'（统一身份登录）' if idn.sdip else '（有访问口令）' if SV.get('access_key') else '（无口令，只在可信网络开）'}", flush=True)
        cs_ = state.cache.stats(); print(f"  盘上缓存 {cs_['dir']}：{cs_['entries']} 条 / {cs_['size_mb']} MB（代码 {cs_['code']} · 配置 {cs_['config']} · 状态 {cs_['state']}）；求解子进程 {state.pool.workers} 个", flush=True)
        try:                                                    # 冷缓存要先说：首问会慢到几分钟，原来没有任何提示（2026-10-01）
            allk = state.warm_order(); hot = [k for k in allk if state.cache.has(f"ctx_{k}")]
            if len(hot) < len(allk):
                print(f"  情景上下文盘上已有 {len(hot)}/{len(allk)} 个；其余 {len(allk) - len(hot)} 个要现算（每个几分钟，后台预热按 {allk} 的顺序）。"
                      "预热完成前问到它们，智能体要等；改了模型层代码（artifactcache.MODEL_MODULES）会让缓存整体作废", flush=True)
        except Exception:
            pass
        if not a.no_warm and not SV.get("no_warm", False):
            threading.Thread(target=state.warm_up, name="warm-up", daemon=True).start()
        dsh = getattr(getattr(state, "agent", None), "dsh", None)
        if dsh is not None and SV.get("dsh_prestart", True):      # 后台先把 DeepSeek Harness 拉起来：首问不再等运行时启动（约 60 s）
            dsh.auto_restart = True; dsh.prestart()
        try:                                                      # 统一身份下先把 Harness 原生 Web UI 的只读凭据写好：
            wb = getattr(getattr(state, "agent", None), "web_ui", None)   # 容器方式（docker/dsh-web）下它读同一个文件，先起容器也不会 401
            if wb is not None and idn.sdip:
                br = wb(); br.idn = idn
                if br.write_credential():
                    print(f"  Harness 原生 Web UI 只读凭据已写：{br.delegation_file()}", flush=True)
        except Exception as ex:
            print(f"  只读凭据没写成（{type(ex).__name__}: {str(ex)[:120]}）；原生 Web UI 里的工具会 401", flush=True)
    lan_proxy(cfg, idn, host)
    srv, bound = serve_on(host, a.port, make_handler(state, idn), tls=tls)
    print(f"  监听 {bound}:{a.port}", flush=True)
    if idn.sdip:
        print(f"  对外地址：{'、'.join(b + '/' for b in idn.cfg.all_bases())}{'（局域网地址按本机当前网卡，换了不用改配置）' if idn.cfg.auto_lan else ''}；"
              f"签发源 {idn.cfg.iss}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
