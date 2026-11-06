# -*- coding: utf-8 -*-
"""统一身份联调自检：逐项检查 SDIP 那一侧开通到了哪一步（照 REMS 实跑的联调清单）。

  python -m swbrain.identity_check                 # 平台侧：签发源、JWKS、客户端是否已登记、门户是否在线（不要账号）
  python -m swbrain.identity_check --login         # 再走一次真实登录（浏览器），检查令牌 aud、交换、租户、会话里的 SWBRAIN.* 职能

只读：不写平台、不建任何东西。每一项给 ✓ / ✗ 和下一步该找谁。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import db, identity as I


def _get(url: str, headers: dict | None = None, timeout: float = 8):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


class Report:
    def __init__(self):
        self.rows = []

    def add(self, ok: bool | None, item: str, detail: str = "", fix: str = ""):
        self.rows.append((ok, item, detail, fix))
        mark = {True: "✓", False: "✗", None: "—"}[ok]
        print(f" {mark} {item}" + (f"：{detail}" if detail else ""), flush=True)
        if ok is False and fix:
            print(f"     → {fix}", flush=True)
        return ok

    @property
    def failed(self):
        return [r for r in self.rows if r[0] is False]


def platform(cfg: I.Config, rep: Report) -> bool:
    """不要账号能查的：签发源与配置一致、JWKS 可取、客户端已登记、回调地址被接受、门户在线。"""
    st, body = _get(cfg.issuer + "/.well-known/openid-configuration")
    if not rep.add(st == 200, "签发源可达", cfg.issuer if st == 200 else f"{st} {body[:80]}", "检查 [identity].issuer 与网络；签发源地址会变（指南 §5c）"):
        return False
    d = json.loads(body)
    if cfg.discover:
        rep.add(d.get("issuer", "").rstrip("/") == cfg.iss, "令牌里的 iss 按发现文档取", d.get("issuer", ""),
                "issuer_discovery = true 但启动时没取到发现文档：检查签发源是否在线")
    else:
        rep.add(d.get("issuer", "").rstrip("/") == cfg.issuer, "令牌里的 iss 与配置一致", d.get("issuer", ""),
                f"把 [identity].issuer 改成 {d.get('issuer')}，或设 issuer_discovery = true（签发源换局域网地址时 iss 跟着变，配置不一致会拒掉所有令牌）")
    st, body = _get(cfg.jwks_uri)
    rep.add(st == 200 and '"keys"' in body, "JWKS 可取", cfg.jwks_uri, "检查 jwks_uri")
    for extra in cfg.all_bases()[1:]:                        # 其余对外地址（局域网）：回调也要登记；没登记的只提示，主地址照常查下去
        q = urllib.parse.urlencode({"client_id": cfg.client_id, "response_type": "code", "redirect_uri": extra + "/auth/callback", "scope": "openid", "state": "check"})
        st, body = _get(d["authorization_endpoint"] + "?" + q)
        bad = st == 400 and "redirect_uri" in body.lower()
        rep.add(None if bad else st == 200, "局域网回调已登记", extra + "/auth/callback" + ("：没登记，这个地址上登录会被拒" if bad else ""),
                "请平台登记 <地址>/auth/callback 与 <地址>/ 两条回跳")
    q = urllib.parse.urlencode({"client_id": cfg.client_id, "response_type": "code", "redirect_uri": cfg.redirect_uri, "scope": "openid", "state": "check"})
    st, body = _get(d["authorization_endpoint"] + "?" + q)
    text = " ".join(re.findall(r'(?:instruction|kc-feedback-text|alert)[^>]*>\s*([^<]{3,200})', body)) or ""
    if "Client not found" in body:
        rep.add(False, f"客户端 {cfg.client_id} 已登记", "Keycloak：Client not found", "平台还没按工单开客户端（docs/SDIP工单_排水智能体接入统一身份.md 第一节）")
    elif "redirect_uri" in body.lower() and st == 400:
        rep.add(True, f"客户端 {cfg.client_id} 已登记")
        rep.add(False, "回调地址被接受", f"{cfg.redirect_uri}：{text[:80]}", "请平台登记这条精确回调（工单第一节「回调」）；或把 [identity].public_base 改成已登记的地址")
    elif st == 200:
        rep.add(True, f"客户端 {cfg.client_id} 已登记"); rep.add(True, "回调地址被接受", cfg.redirect_uri)
    else:
        rep.add(False, f"客户端 {cfg.client_id} 已登记", f"{st} {text[:100]}", "看 Keycloak 返回的说明")
    st, body = _get(cfg.portal_base + "/api/portal/tenants")
    rep.add(st == 401 and "SEM_UNAUTHENTICATED" in body, "SDIP 门户在线", f"{cfg.portal_base}（未带令牌 → {st}，在线）" if st == 401 else f"{st} {body[:80]}",
            "检查 [identity].portal_base")
    return not rep.failed


def login_checks(idn: I.Identity, user_token: str, rep: Report) -> bool:
    """拿到一张真实用户令牌之后：aud、直打 SDIP 应被拒、交换、租户、会话与职能（联调清单 1–5）。"""
    cfg = idn.cfg
    try:
        claims = I.verify(cfg, idn.jwks, user_token)
        print(f"   登录的账号：{claims.get('preferred_username')}", flush=True)
        rep.add(True, "1 用户令牌验签通过、aud 含本客户端", str(claims.get("aud")))
    except I.IdentityError as ex:
        return rep.add(False, "1 用户令牌验签通过、aud 含本客户端", str(ex)[:160], "请平台给客户端配受众映射器（aud 含 swbrain）")
    st, body = _get(cfg.portal_base + "/api/portal/tenants", {"Authorization": f"Bearer {user_token}"})
    rep.add(st == 401, "2 用户令牌直打 SDIP 被拒（隔离有效）", f"{st}", "用户令牌不该能直接调 SDIP：请平台核对受众隔离（乙方案）")
    try:
        ex = idn.portal.exchange(user_token)
        c2 = I.verify(cfg, idn.jwks, ex, audience="sdip-semantic")
        # 主体号不一定在令牌里：这套部署交换后不带 sdip_principal_id，平台按（签发源，sub）查（路 ②，回执 2026-10-01）——在第 5 步核对
        rep.add(True, "3 令牌交换：aud 含 sdip-semantic", f"aud={c2.get('aud')}" + (f" 令牌里的主体号={c2['sdip_principal_id']}" if c2.get("sdip_principal_id") else "（令牌里无主体号，走路 ②）"))
    except I.IdentityError as e:
        return rep.add(False, "3 令牌交换", str(e)[:160], "Client is not within the token audience = 少了受众映射器；Requested audience not available = 照指南传了 audience（本系统传 scope，不该出现）")
    st, body = _get(cfg.portal_base + "/api/portal/tenants", {"Authorization": f"Bearer {ex}"})
    ten = json.loads(body) if st == 200 and body.strip()[:1] in "[{" else None
    # 实测（2026-10-01）返回 {"tenants": [{"tenantId": 2, "tenantName": …}]}；也认裸列表与 items
    items = ten if isinstance(ten, list) else ((ten or {}).get("tenants") or (ten or {}).get("items") or [])
    ids = [str(t.get("tenantId", t.get("id"))) for t in items if isinstance(t, dict)]
    rep.add(str(cfg.tenant_id) in ids, f"4 租户 {cfg.tenant_id} 在可见租户里", "、".join(ids) or "（空）",
            "回空 = 授权那一步没做，不是令牌问题（请平台给这个账号配门户授权）")
    try:
        s = idn.portal.session(ex)
    except I.IdentityError as e:
        return rep.add(False, "5 门户会话", str(e)[:160], "带 X-Sdip-Tenant 仍失败：请平台核对这个账号在本租户的授权")
    sub = s.get("subject") if isinstance(s.get("subject"), dict) else {}
    pid = s.get("principalId") or sub.get("principalId") or c2.get("sdip_principal_id")
    rep.add(pid not in (None, "", 0, "0"), "5 会话给出主体号", f"{pid}（{sub.get('displayName') or s.get('displayName') or ''}）",
            "会话里没有 principalId：请平台核对这个账号的主体是否锚定了真实的 Keycloak sub")
    fns = I._functions(s)
    rep.add("functions" in s, "5 会话返回 functions 字段", "有" if "functions" in s else f"会话里只有 {sorted(s)[:12]}", "平台还没上线职能授权（REMS 那边 2026-09-30 已上线）")
    rep.add(I.VIEW in fns, "5 本账号持有 SWBRAIN.VIEW", ", ".join(sorted(fns)) or "（一项本系统职能都没有）", "请平台按工单第二、三节登记职能并授予")
    unknown = sorted(c for c in fns if c not in I.FUNCTIONS)
    rep.add(not unknown, "5 职能代码都认识", "" if not unknown else "、".join(unknown), "平台登记的代码和工单不一致，以工单为准或改 swbrain/identity.py")
    return not rep.failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--login", action="store_true", help="再走一次真实登录：起一个临时回调，浏览器里登录后自动继续")
    ap.add_argument("--user", default=None, help="预填登录页的用户名（login_hint），逐个账号核对时用")
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    raw = db.load_config(a.config)
    I_ = (raw.get("identity") or {})
    if str(I_.get("mode") or "shared_key") != "sdip":
        print("[identity].mode 不是 sdip：照旧用共享口令。下面按 [identity] 里填的地址只查平台侧。", flush=True)
        raw = {**raw, "identity": {**I_, "mode": "sdip"}}
    if not a.login:                                       # 只查平台侧用不到客户端密钥：没有就放个占位，免得因为它报「配置不全」
        import os
        env = str((raw["identity"]).get("client_secret_env") or "SWBRAIN_SDIP_CLIENT_SECRET")
        if not os.environ.get(env):
            os.environ[env] = "unused-for-platform-check"
    try:
        idn = I.Identity(raw)
    except ValueError as ex:
        print(f"配置不全：{ex}"); return 2
    rep = Report()
    print(f"平台侧（{idn.cfg.issuer}，门户 {idn.cfg.portal_base}，租户 {idn.cfg.tenant_id}）", flush=True)
    ok = platform(idn.cfg, rep)
    if a.login:
        if not ok:
            print("平台侧还有 ✗，先不走登录。"); return 1
        tok = _interactive_login(idn, a.user)
        if tok:
            print("登录后", flush=True); login_checks(idn, tok, rep)
    print(f"\n{'全部通过' if not rep.failed else f'{len(rep.failed)} 项没过'}", flush=True)
    return 0 if not rep.failed else 1


def _interactive_login(idn: I.Identity, hint: str | None = None) -> str | None:
    """起一个临时的 /auth/callback（就在 public_base 的端口上），打开浏览器登录，拿到用户令牌就关。服务要先停（端口冲突）。"""
    import http.server
    import threading
    import webbrowser
    u = urllib.parse.urlparse(idn.cfg.public_base)
    verifier, challenge = I.new_pkce(); state = I.secrets.token_urlsafe(16); got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if urllib.parse.urlparse(self.path).path == "/auth/callback" and q.get("state", [""])[0] == state:
                got["code"] = q.get("code", [""])[0]; got["error"] = q.get("error", [""])[0]
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write("自检拿到回调了，可以关掉这个页面。".encode("utf-8"))

        def log_message(self, *a):
            pass
    try:
        srv = http.server.HTTPServer((u.hostname, u.port or (443 if u.scheme == "https" else 80)), H)
    except OSError as ex:
        print(f" ✗ 起不了临时回调 {u.hostname}:{u.port}（{ex}）：先停掉排水智能体服务再跑"); return None
    if u.scheme == "https":                              # 回调登记的是 https：临时回调也得是 https，用服务同一张证书
        from .serve import tls_files
        import ssl
        tls = tls_files(db.load_config(None))
        if not tls:
            print(" ✗ public_base 是 https 但 [serve] 没配 tls_cert / tls_key：先跑 python -m swbrain.tlscert"); srv.server_close(); return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(*tls); srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.timeout = 2                                     # 自签证书时浏览器会先断几次握手（看到警告页），不能只收 5 个请求就停

    def _loop():
        import time as _t
        end = _t.monotonic() + 600
        while "code" not in got and _t.monotonic() < end:
            try:
                srv.handle_request()
            except Exception:
                pass
    th = threading.Thread(target=_loop, daemon=True); th.start()
    # prompt=login：逐个账号核对时浏览器里还留着上一个账号的 SSO 会话，不加它会不输口令直接以上一个账号回来
    url = idn.authorize_url(state, challenge) + "&prompt=login" + (f"&login_hint={urllib.parse.quote(hint)}" if hint else "")
    print(f"   浏览器里登录（没自动打开就复制这个地址）：\n   {url}", flush=True)
    webbrowser.open(url); th.join(timeout=600); srv.server_close()
    if not got.get("code"):
        print(f" ✗ 没拿到授权码 {got.get('error') or '（超时）'}"); return None
    st, body = idn._post(idn.cfg.token_uri, {"grant_type": "authorization_code", "code": got["code"], "redirect_uri": idn.cfg.redirect_uri,
                                             "client_id": idn.cfg.client_id, "client_secret": idn.cfg.client_secret, "code_verifier": verifier})
    if st != 200:
        print(f" ✗ 换取令牌失败 {body.get('error', st)}"); return None
    return body.get("access_token")


if __name__ == "__main__":
    sys.exit(main())
