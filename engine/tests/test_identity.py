"""统一身份（SDIP，2026-10-01）：可选，缺省 shared_key 照旧；sdip 模式按职能判。

用一个本地假签发源（真 RSA 签名的 JWT + 假的令牌 / 交换 / 门户会话接口）起真服务，走完整的授权码登录，不联网。
锁住的行为：
  · 缺省 shared_key：不要 PyJWT、不要 [identity] 段，口令照旧；sdip 配不齐启动即失败，不会悄悄退回口令；
  · 登录：PKCE + state 一次性 + 必须回到发起登录的同一个浏览器；没有 SWBRAIN.VIEW 建不了会话；
  · 职能：写动作前现查平台（撤销立即生效）；FIELD/ALERT 等要先持有 VIEW；feed/events 要 ALERT；刷新路况要 ADMIN；
  · sdip 模式下共享口令一律不认；带会话 cookie 的写请求要 Origin 是本服务；
  · 管理端认 SWBRAIN.ADMIN；有管理权限就能批计算申请（含自己提交的），提交人与批准人都记主体号；
  · 委托凭据：智能体经 MCP 回调时还原成发起对话的人。
"""
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

jwt = pytest.importorskip("jwt")
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from swbrain import identity as I, routes as RTS, serve  # noqa: E402

ISS = "http://idp.test/realms/sdip-dmt"
PUB = "http://127.0.0.1:8766"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks():
    k = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key()))
    return {"keys": [{**k, "kid": "k1", "use": "sig", "alg": "RS256"}]}


def _tok(sub, aud, **extra):
    now = int(time.time())
    return jwt.encode({"iss": ISS, "sub": sub, "aud": aud, "exp": now + 300, "iat": now, **extra}, _KEY, algorithm="RS256", headers={"kid": "k1"})


class FakeIdp:
    """令牌接口（授权码 / 刷新 / 令牌交换）+ 门户会话。users：用户名 → (主体号, 职能列表)。"""

    def __init__(self):
        self.users = {"u1": ("2101", ["SWBRAIN.VIEW", "SWBRAIN.FIELD"]), "u2": ("2102", ["SWBRAIN.VIEW", "SWBRAIN.ADMIN"]),
                      "u3": ("2103", ["SWBRAIN.VIEW", "SWBRAIN.ADMIN"]), "nobody": ("2109", []), "alerter": ("2104", ["SWBRAIN.ALERT"])}
        self.codes = {}; self.down = False; self.calls = []

    def post(self, url, data, timeout=15):
        self.calls.append(data.get("grant_type"))
        if self.down:
            raise I.IdentityUnavailable("idp down")
        g = data["grant_type"]
        if g == "authorization_code":
            u = self.codes.pop(data["code"], None)
            if not u:
                return 400, {"error": "invalid_grant"}
            return 200, {"access_token": _tok(u, ["swbrain", "account"], preferred_username=u), "refresh_token": "r-" + u, "id_token": "id-" + u, "expires_in": 300}
        if g == "refresh_token":
            u = data["refresh_token"][2:]
            return 200, {"access_token": _tok(u, ["swbrain", "account"], preferred_username=u), "refresh_token": data["refresh_token"], "expires_in": 300}
        if g.endswith("token-exchange"):
            assert data["scope"] == I.EXCHANGE_SCOPE and "audience" not in data       # 传 scope 不传 audience
            sub = jwt.decode(data["subject_token"], options={"verify_signature": False})["sub"]
            return 200, {"access_token": _tok(sub, ["swbrain", "sdip-semantic", "account"], sdip_principal_id=int(self.users[sub][0]))}
        return 400, {"error": "unsupported_grant_type"}

    def get(self, url, headers, timeout=15):
        assert url.endswith("/api/portal/session") and headers["X-Sdip-Tenant"] == "2"
        sub = jwt.decode(headers["Authorization"][7:], options={"verify_signature": False})["sub"]
        pid, fns = self.users[sub]
        return 200, {"principalId": int(pid), "displayName": sub.upper(), "functions": [{"code": c, "grantDoc": "SWBRAIN-AUTH-2026-01", "validUntil": None} for c in fns]}


SDIP_CFG = {"identity": {"mode": "sdip", "issuer": ISS, "portal_base": "http://portal.test", "tenant_id": 2, "public_base": PUB, "client_secret_env": "SWB_TEST_SECRET"}}


class _State:
    def __init__(self, cfg):
        self.cfg = cfg


@pytest.fixture()
def sdip(monkeypatch, tmp_path):
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    from swbrain.agents import admin_routes as ADM, computejobs as CJ
    monkeypatch.setattr(ADM, "AUDIT", tmp_path / "audit.jsonl"); monkeypatch.setattr(CJ, "DIR", tmp_path / "jobs")
    idp = FakeIdp()
    idn = I.Identity(SDIP_CFG, jwks_fetch=_jwks, post=idp.post, get=idp.get)
    st = _State(SDIP_CFG)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(st, idn))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"port": httpd.server_address[1], "idp": idp, "idn": idn, "state": st}
    httpd.shutdown()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def req(port, path, method="GET", body=None, headers=None):
    """→ (状态码, 响应头, 正文 dict 或文本)。不跟随跳转。"""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    op = urllib.request.build_opener(_NoRedirect)
    try:
        resp = op.open(r, timeout=10); code, hd, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        code, hd, raw = e.code, e.headers, e.read()
    try:
        return code, hd, json.loads(raw.decode() or "null")
    except ValueError:
        return code, hd, raw.decode("utf-8", "replace")


def _cookies(hd):
    out = {}
    for v in hd.get_all("Set-Cookie") or []:
        k, _, rest = v.partition("="); out[k] = rest.split(";")[0]
    return out


def login(env, user):
    """走完整授权码登录，返回会话 cookie 串。"""
    port, idp = env["port"], env["idp"]
    code, hd, _ = req(port, "/auth/login?next=/index.html")
    assert code == 302
    loc = urllib.parse.urlparse(hd["Location"]); q = urllib.parse.parse_qs(loc.query)
    assert loc.geturl().startswith(ISS + "/protocol/openid-connect/auth") and q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == [PUB + "/auth/callback"]
    binding = _cookies(hd)[I.LOGIN_COOKIE]
    idp.codes["c-" + user] = user
    code, hd, body = req(port, f"/auth/callback?code=c-{user}&state={q['state'][0]}", headers={"Cookie": f"{I.LOGIN_COOKIE}={binding}"})
    if code != 302:
        return None, code, body
    assert hd["Location"] == "/index.html"
    return f"{I.SESSION_COOKIE}={_cookies(hd)[I.SESSION_COOKIE]}", code, body


class _Onto:
    """桩本体：真走 Runtime.act 的职能判定，动作本身不碰数据。"""

    def __init__(self, tmp):
        from swbrain.ontology import runtime as RTm
        self.rt = RTm.Runtime.__new__(RTm.Runtime); self.rt.log_path = tmp / "acts.jsonl"
        for api in ("record_observation", "issue_alert", "traffic_status", "count_points"):
            setattr(self.rt, f"_act_{api}", lambda P, api=api: {"done": api})
            setattr(self.rt, f"_sum_{api}", lambda r, P, lang, api=api: api)
        self.rt.validate = lambda A, p: dict(p)
        self.rt.console_link = lambda ui: None

    def act(self, *a, **k):
        return self.rt.act(*a, **k)

    def search(self, *a, **k):
        return []                                                          # 只读那条路：这一条测试只关心认人不认人


def _with_onto(env, tmp):
    env["state"].ontology = _Onto(tmp)
    return env


# ---------------------------------------------------------------- 可选：缺省照旧
def test_default_is_shared_key_and_needs_nothing():
    c = I.Config.from_cfg({})
    assert c.mode == "shared_key" and not c.sdip
    idn = I.Identity({"serve": {"access_key": "k"}})                       # 没有 [identity] 段、不碰 PyJWT
    assert idn.jwks is None and idn.portal is None
    p = I.local_principal("console")
    assert p.actor == "local:console" and all(p.has(f) for f in I.FUNCTIONS)  # 拿口令的人什么都能做（原来就是这样）


def test_sdip_incomplete_config_fails_loudly(monkeypatch):
    monkeypatch.delenv("SWBRAIN_SDIP_CLIENT_SECRET", raising=False)
    with pytest.raises(ValueError, match="缺"):
        I.Config.from_cfg({"identity": {"mode": "sdip", "issuer": ISS}})
    with pytest.raises(ValueError):
        I.Config.from_cfg({"identity": {"mode": "oidc"}})


# ---------------------------------------------------------------- 登录
def test_login_flow_and_session(sdip):
    ck, code, _ = login(sdip, "u1")
    assert ck, code
    st, _, who = req(sdip["port"], "/api/admin/whoami", headers={"Cookie": ck})
    assert st == 200 and who["mode"] == "sdip" and who["admin"] is False and who["who"]["principal_id"] == "2101" and who["who"]["actor"] == "sdip:2101"


def test_unauthenticated_gets_login(sdip):
    st, hd, _ = req(sdip["port"], "/")
    assert st == 302 and hd["Location"].startswith("/auth/login")
    st, hd, _ = req(sdip["port"], "/whoami.html")                         # 任何页面都跳登录并带回原页面（原来只认首页与管理端）
    assert st == 302 and hd["Location"] == "/auth/login?next=/whoami.html"
    assert req(sdip["port"], "/app.js")[0] == 401                           # 静态资源不跳
    st, _, body = req(sdip["port"], "/api/agents")
    assert st == 401 and body["login_url"] == "/auth/login"


def test_callback_must_come_back_to_same_browser(sdip):
    port, idp = sdip["port"], sdip["idp"]
    _, hd, _ = req(port, "/auth/login")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(hd["Location"]).query)["state"][0]
    idp.codes["c-x"] = "u1"
    st, _, _ = req(port, f"/auth/callback?code=c-x&state={state}", headers={"Cookie": f"{I.LOGIN_COOKIE}=someone-else"})
    assert st == 400                                                        # 登录 CSRF：别人的回调链接
    st, _, _ = req(port, f"/auth/callback?code=c-x&state={state}", headers={"Cookie": f"{I.LOGIN_COOKIE}=x"})
    assert st == 400                                                        # state 一次性


def test_no_function_no_session(sdip):
    ck, code, body = login(sdip, "nobody")
    assert ck is None and code == 403
    assert "nobody" in body and "switch=1" in body                         # 说出是哪个账号，并给「换个账号登录」
    st, hd, _ = req(sdip["port"], "/auth/login?switch=1")
    assert st == 302 and "prompt=login" in hd["Location"]                  # 换账号：不沿用浏览器里那个账号的 SSO 会话


def test_shared_key_not_accepted_in_sdip_mode(sdip):
    sdip["state"].cfg = {**SDIP_CFG, "serve": {"access_key": "k", "admin_key": "a"}}
    st, _, _ = req(sdip["port"], "/api/agents?key=k", headers={"Cookie": "swb_key=k; swb_admin=a"})
    assert st == 401
    st, _, body = req(sdip["port"], "/api/admin/login", "POST", {"key": "a"}, headers={"Origin": PUB})
    assert st == 401                                                        # 连管理员口令登录接口都进不去（先要会话）


# ---------------------------------------------------------------- 平台转发（对外能力端点）
def test_forwarded_token_works_and_carries_the_person(sdip, tmp_path):
    """平台把一次问数转给本系统时带的那张令牌（已换域，aud 含 sdip-semantic）。

    它不该再换一次域 —— 换第二次会 403（那张令牌的发起方不在它的 aud 里），
    而报出来的话是「令牌交换失败」，离「转发没带对令牌」很远。
    """
    _with_onto(sdip, tmp_path)
    tok = _tok("u1", ["swbrain", "sdip-semantic", "account"])               # 平台转过来的形状
    st, _, body = req(sdip["port"], "/api/objects?type=FloodPoint", headers={"Authorization": f"Bearer {tok}", "X-Sdip-Tenant": "2"})
    assert st == 200, body
    # 身份落到发起人身上，不是「某个匿名调用方」
    st, _, out = req(sdip["port"], "/api/action", "POST", {"action": "record_observation", "params": {}},
                     {"Authorization": f"Bearer {tok}", "X-Sdip-Tenant": "2"})
    assert st == 200 and out["ok"], out
    acts = [json.loads(x) for x in (tmp_path / "acts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert acts[-1]["actor"] == "sdip:2101/api:anon"                        # u1 = 主体 2101，职能按他判
    assert sdip["idp"].calls.count("urn:ietf:params:oauth:grant-type:token-exchange") == 0   # 没换第二次


def test_forwarded_token_still_needs_the_function(sdip, tmp_path):
    """转发不豁免职能：u2 没有 FIELD，登记观测照样拒。"""
    _with_onto(sdip, tmp_path)
    tok = _tok("u2", ["swbrain", "sdip-semantic", "account"])               # u2 = VIEW + ADMIN
    st, _, out = req(sdip["port"], "/api/action", "POST", {"action": "record_observation", "params": {}},
                     {"Authorization": f"Bearer {tok}", "X-Sdip-Tenant": "2"})
    assert st == 200 and out["ok"] is False and "FIELD" in out["error"]


def test_forwarded_token_refuses_other_tenants_and_aud(sdip):
    """两处必须拒：租户头对不上、受众不在 accept_audience 里。"""
    tok = _tok("u1", ["swbrain", "sdip-semantic", "account"])
    st, _, _ = req(sdip["port"], "/api/objects?type=FloodPoint", headers={"Authorization": f"Bearer {tok}", "X-Sdip-Tenant": "3"})
    assert st == 401                                                        # 端点的租户登记错了，早拒
    # ⚠️ 浏览器直接带的那张（aud 只有 swbrain）**不能**从这里进来 —— 否则
    #    「拿一张用户令牌直连对方系统」这条路被这条新入口打开了
    st, _, _ = req(sdip["port"], "/api/objects?type=FloodPoint", headers={"Authorization": f"Bearer {_tok('u1', ['swbrain', 'account'])}", "X-Sdip-Tenant": "2"})
    assert st == 401


def test_forwarded_token_revocation_takes_effect_before_next_write(sdip, tmp_path):
    """写动作前现查平台：撤销立即生效，不因为是转发来的就打折。"""
    _with_onto(sdip, tmp_path)
    tok = _tok("u1", ["swbrain", "sdip-semantic", "account"])
    h = {"Authorization": f"Bearer {tok}", "X-Sdip-Tenant": "2"}
    st, _, out = req(sdip["port"], "/api/action", "POST", {"action": "record_observation", "params": {}}, h)
    assert st == 200 and out["ok"], out
    sdip["idp"].users["u1"] = ("2101", ["SWBRAIN.VIEW"])                    # 撤掉 FIELD
    st, _, out = req(sdip["port"], "/api/action", "POST", {"action": "record_observation", "params": {}}, h)
    assert st == 200 and out["ok"] is False and "FIELD" in out["error"]


# ---------------------------------------------------------------- 职能
def test_action_needs_function_and_records_principal(sdip, tmp_path):
    _with_onto(sdip, tmp_path)
    ck, _, _ = login(sdip, "u1")                                            # VIEW + FIELD
    h = {"Cookie": ck, "Origin": PUB}
    st, _, out = req(sdip["port"], "/api/action", "POST", {"action": "record_observation", "params": {}}, h)
    assert st == 200 and out["ok"], out
    st, _, out = req(sdip["port"], "/api/action", "POST", {"action": "issue_alert", "params": {}}, h)
    assert out["ok"] is False and "SWBRAIN.ALERT" in out["error"]           # 没有 ALERT
    st, _, out = req(sdip["port"], "/api/action", "POST", {"action": "traffic_status", "params": {"refresh": True}}, h)
    assert out["ok"] is False and "SWBRAIN.ADMIN" in out["error"]           # 刷新路况计费，要 ADMIN
    log = [json.loads(x) for x in (tmp_path / "acts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[0]["actor"] == "sdip:2101/api:anon"                          # 记的是主体号，不是自报的标签


def test_revocation_takes_effect_before_next_write(sdip, tmp_path):
    _with_onto(sdip, tmp_path)
    ck, _, _ = login(sdip, "u1")
    h = {"Cookie": ck, "Origin": PUB}
    assert req(sdip["port"], "/api/action", "POST", {"action": "record_observation"}, h)[2]["ok"]
    sdip["idp"].users["u1"] = ("2101", ["SWBRAIN.VIEW"])                  # 平台撤销 FIELD
    out = req(sdip["port"], "/api/action", "POST", {"action": "record_observation"}, h)[2]
    assert out["ok"] is False and "SWBRAIN.FIELD" in out["error"]           # 不等 60 s 缓存


def test_prerequisite_view_rechecked():
    p = I.Principal("9", functions={I.ALERT: {}})
    assert not p.has(I.ALERT)                                               # 只有 ALERT、没有 VIEW：不算


def test_feed_functions_by_source():
    assert RTS.needs(None, "POST", "/api/feed/events", {}) == I.ALERT
    assert RTS.needs(None, "POST", "/api/feed/episodes", {}) == I.FIELD
    assert RTS.needs(None, "POST", "/api/feed/incidents", {}) == I.FEED
    assert RTS.needs(None, "GET", "/api/traffic", {"refresh": ["1"]}) == I.ADMIN
    assert RTS.needs(None, "GET", "/api/traffic", {}) is None


def test_write_needs_same_origin(sdip, tmp_path):
    _with_onto(sdip, tmp_path)
    ck, _, _ = login(sdip, "u1")
    st, _, _ = req(sdip["port"], "/api/action", "POST", {"action": "record_observation"}, {"Cookie": ck, "Origin": "http://evil.test"})
    assert st == 403
    st, _, _ = req(sdip["port"], "/api/action", "POST", {"action": "record_observation"}, {"Cookie": ck})
    assert st == 403


def test_idp_down_is_503_not_logout(sdip):
    ck, _, _ = login(sdip, "u1")
    sess = sdip["idn"].sessions.get(ck.split("=", 1)[1]); sess.grant_at = -1e9   # 缓存过期，要现查
    sdip["idp"].down = True
    st, _, body = req(sdip["port"], "/api/agents", headers={"Cookie": ck})
    assert st == 503 and body["auth"] == "unavailable"
    sdip["idp"].down = False
    assert req(sdip["port"], "/api/agents", headers={"Cookie": ck})[0] == 200    # 恢复后会话还在


# ---------------------------------------------------------------- 管理端与计算申请
def test_admin_by_function_and_self_approval(sdip):
    """批准只看角色（2026-10-02 用户定）：有 SWBRAIN.ADMIN 就能批，含自己提交的；提交人与批准人都留痕。"""
    from swbrain.agents import computejobs as CJ
    port = sdip["port"]
    ck1, _, _ = login(sdip, "u1")
    assert req(port, "/api/admin/jobs", headers={"Cookie": ck1})[0] == 403          # 没有 ADMIN
    ck2, _, _ = login(sdip, "u2")
    assert req(port, "/api/admin/jobs", headers={"Cookie": ck2})[0] == 200
    j = CJ.submit("post", "M61", requested_by={"agent": "report", "principal": "sdip:2102"})
    import unittest.mock as um
    with um.patch.object(CJ, "start", lambda job: CJ.save({**job, "status": "running", "pid": None})):
        st, _, out = req(port, "/api/admin/job", "POST", {"id": j["id"], "op": "approve"}, {"Cookie": ck2, "Origin": PUB})
    assert st == 200 and out["job"]["history"][-1]["by"] == "sdip:2102"            # 自己批自己：允许，记主体号
    assert out["job"]["requested_by"]["principal"] == "sdip:2102"


# ---------------------------------------------------------------- 委托凭据（智能体经 MCP 回调）
def test_delegation_restores_the_person(sdip, tmp_path):
    _with_onto(sdip, tmp_path)
    ck, _, _ = login(sdip, "u1")
    sid = ck.split("=", 1)[1]
    p = sdip["idn"].principal(sid)
    tok = sdip["idn"].delegate(p, sid)
    st, _, out = req(sdip["port"], "/api/action", "POST", {"action": "record_observation", "actor": "dsh"}, {"Authorization": f"Bearer {tok}"})
    assert st == 200 and out["ok"]
    out = req(sdip["port"], "/api/action", "POST", {"action": "issue_alert", "actor": "dsh"}, {"Authorization": f"Bearer {tok}"})[2]
    assert out["ok"] is False                                               # 智能体调的也按发起人的职能判
    sdip["idn"].revoke_delegate(tok)
    assert req(sdip["port"], "/api/action", "POST", {"action": "record_observation"}, {"Authorization": f"Bearer {tok}"})[0] == 401
    assert req(sdip["port"], "/api/action", "POST", {"action": "record_observation"}, {"Authorization": "Bearer dlg_forged"})[0] == 401


def test_shared_key_mode_delegation_snapshot():
    idn = I.Identity({})
    p = I.local_principal("console")
    assert idn.from_delegate(idn.delegate(p)) is p and idn.from_delegate("dlg_nope") is None


def test_identity_check_platform_and_login(monkeypatch):
    """联调自检：平台侧能认出「客户端没建」「iss 和配置不一致」；登录后五项照 REMS 的联调清单走（假签发源）。"""
    from swbrain import identity_check as IC
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    cfg = I.Config.from_cfg(SDIP_CFG)
    disc = json.dumps({"issuer": "http://10.0.0.47:8480/realms/sdip-dmt", "authorization_endpoint": ISS + "/protocol/openid-connect/auth"})
    pages = {"/.well-known/openid-configuration": (200, disc), "/certs": (200, '{"keys": []}'), "/auth?": (400, '<p class="instruction">Client not found.</p>'),
             "/api/portal/tenants": (401, '{"code":"SEM_UNAUTHENTICATED"}')}
    monkeypatch.setattr(IC, "_get", lambda url, headers=None, timeout=8: next(v for k, v in pages.items() if k in url))
    rep = IC.Report(); IC.platform(cfg, rep)
    bad = {r[1] for r in rep.failed}
    assert bad == {"令牌里的 iss 与配置一致", "客户端 swbrain 已登记"}, bad
    idp = FakeIdp(); idn = I.Identity(SDIP_CFG, jwks_fetch=_jwks, post=idp.post, get=idp.get)
    tenants = json.dumps({"tenants": [{"tenantId": 2, "tenantName": "Abu Dhabi DMT"}]})   # 实测形状
    monkeypatch.setattr(IC, "_get", lambda url, headers=None, timeout=8: (401, "") if "sdip-semantic" not in jwt.decode(headers["Authorization"][7:], options={"verify_signature": False})["aud"] else (200, tenants))
    rep = IC.Report()
    assert IC.login_checks(idn, _tok("u1", ["swbrain", "account"]), rep), [r for r in rep.failed]


def test_receipt_shape_principal_only_in_session_subject(monkeypatch):
    """回执（2026-10-01）：交换后的令牌里没有 sdip_principal_id；主体号只在会话的 subject.principalId（路 ②）。"""
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    idp = FakeIdp()
    post0, get0 = idp.post, idp.get

    def post(url, data, timeout=15):
        if data.get("grant_type", "").endswith("token-exchange"):
            sub = jwt.decode(data["subject_token"], options={"verify_signature": False})["sub"]
            return 200, {"access_token": _tok(sub, ["swbrain", "sdip-semantic", "account"])}          # 无 sdip_principal_id
        return post0(url, data, timeout)

    def get(url, headers, timeout=15):
        _, b = get0(url, headers, timeout)
        return 200, {"subject": {"principalId": b["principalId"], "displayName": b["displayName"]}, "role": "SUP", "effective": True, "functions": b["functions"]}
    idn = I.Identity(SDIP_CFG, jwks_fetch=_jwks, post=post, get=get)
    p = idn.portal.establish(_tok("u1", ["swbrain", "account"]))
    assert p.principal_id == "2101" and p.name == "U1" and p.has(I.FIELD)


def test_service_account_feed_only(monkeypatch):
    """swb-feed：swbrain 客户端的客户端凭据令牌，Keycloak 用户名 service-account-swbrain，只有 FEED（回执第四节）。"""
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    idp = FakeIdp(); idp.users["service-account-swbrain"] = ("2030", ["SWBRAIN.FEED"])
    idn = I.Identity(SDIP_CFG, jwks_fetch=_jwks, post=idp.post, get=idp.get)
    svc = _tok("service-account-swbrain", ["swbrain", "account"], azp="swbrain", preferred_username="service-account-swbrain")
    p = idn.bearer(svc)
    assert p.kind == "service" and p.has(I.FEED) and not p.has(I.VIEW) and p.actor == "sdip:2030"
    person = _tok("u1", ["swbrain", "account"], azp="swbrain", preferred_username="u1")
    with pytest.raises(I.IdentityError):
        idn.bearer(person)                                                  # 人员令牌不能拿来当服务账号推外部源


def test_https_serving(tmp_path):
    """平台登记的回调是 https：serve_on 带 tls 时真的走 TLS。"""
    import ssl
    from swbrain import tlscert
    crt, key = tlscert.make(tmp_path, ["127.0.0.1"])
    st = _State({"serve": {"access_key": ""}})
    srv, bound = serve.serve_on("127.0.0.1", 0, serve.make_handler(st), tls=(str(crt), str(key)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ctx = ssl.create_default_context(cafile=str(crt))
        r = urllib.request.urlopen(f"https://127.0.0.1:{srv.server_address[1]}/api/agents", context=ctx, timeout=10)
        assert "HTTPS" in bound and r.status in (200, 500)                 # 握手成功即可（桩 state 没有智能体）
    except urllib.error.HTTPError as e:
        assert e.code in (500,)
    finally:
        srv.shutdown()
    with pytest.raises(ValueError):
        serve.tls_files({"serve": {"tls_cert": "out/tls/nope.crt", "tls_key": ""}})


def test_safe_next():
    assert I.safe_next("/index.html?x=1") == "/index.html?x=1"
    for bad in ("//evil.test", "/\\evil.test", "https://evil.test", "/\tevil", None, ""):
        assert I.safe_next(bad) == "/"


# ---------------------------------------------------------------- Harness 原生 Web UI 入口（只读，2026-10-02）
def test_readonly_credential_refuses_writes(monkeypatch):
    """原生 Web UI 分不出是谁在用（dsh 的 MCP 客户端 headers 是静态配置），所以发一张只读凭据：
    读动作按 VIEW 放行，会写数据的一律拒——就算这张凭据背后的人本来有 FIELD / ALERT。"""
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    idp = FakeIdp(); idp.users["u1"] = ("2101", ["SWBRAIN.VIEW", "SWBRAIN.FIELD", "SWBRAIN.ALERT"])
    idn = I.Identity(SDIP_CFG, jwks_fetch=_jwks, post=idp.post, get=idp.get)
    tok = idn.readonly_token()
    p = idn.from_delegate(tok)
    assert p.readonly and p.has(I.VIEW) and not p.has(I.FIELD)          # 职能即使有，这个入口也只读
    tm = __import__("tempfile").mkdtemp()
    from swbrain.ontology import runtime as RTm
    rt = RTm.Runtime.__new__(RTm.Runtime); rt.log_path = __import__("pathlib").Path(tm) / "a.jsonl"
    rt.validate = lambda A, pa: dict(pa); rt.console_link = lambda ui: None
    rt._act_record_observation = lambda P: {"done": 1}; rt._sum_record_observation = lambda r, P, lang: "ok"
    rt._act_count_points = lambda P: {"n": 7}; rt._sum_count_points = lambda r, P, lang: "7"
    tk = I.bind(p)
    try:
        out = rt.act("count_points", {}, actor="dsh")                    # 只读动作：放行
        assert out.get("ok") is True, out
        bad = rt.act("record_observation", {}, actor="dsh")              # 写动作：拒
        assert bad.get("ok") is False and "只读" in bad.get("error", ""), bad
    finally:
        I.unbind(tk)
    assert [json.loads(x)["actor"] for x in (__import__("pathlib").Path(tm) / "a.jsonl").read_text(encoding="utf-8").splitlines()] == ["sdip:harness-web/dsh"] * 2
    # 两条：只读动作放行一条、写动作被拒也记一条（被拒的也记下是谁被拒的）


def test_readonly_credential_hides_write_tools_from_the_bridge(monkeypatch):
    """MCP 桥那一层也拦：只读模式下会写数据的动作不进工具清单之外还能调（直接调用被拒）。"""
    from swbrain import mcp_server
    monkeypatch.setattr(mcp_server, "READONLY", True)
    monkeypatch.setattr(mcp_server, "http_post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("只读下不该发写请求")))
    text, is_err = mcp_server.call_tool("issue_alert", {"fp_id": "ADM-62"})
    assert is_err and "只读" in text and "控制台" in text, text


def test_readonly_token_through_the_real_http_path(sdip, tmp_path):
    """走真服务的 HTTP：只读凭据能读、写动作被拒、静态资源可看（这条覆盖 MCP 桥实际走的那条路）。"""
    _with_onto(sdip, tmp_path)
    tok = sdip["idn"].readonly_token()
    h = {"Authorization": f"Bearer {tok}"}
    assert req(sdip["port"], "/api/agents", headers=h)[0] in (200, 500)          # 有会话就能过鉴权（桩 state 没有智能体，接口自身可能 500）
    out = req(sdip["port"], "/api/action", "POST", {"action": "count_points", "params": {}}, h)[2]
    assert out.get("ok") is True, out
    out = req(sdip["port"], "/api/action", "POST", {"action": "record_observation", "params": {}}, h)[2]
    assert out.get("ok") is False and "只读" in out.get("error", ""), out
    who = req(sdip["port"], "/api/whoami", headers=h)[2]
    assert who["who"]["readonly"] is True and who["who"]["functions"] == ["SWBRAIN.VIEW"]
    assert req(sdip["port"], "/app.js", headers=h)[0] == 200                     # 静态资源：登录过就能看


def test_webbridge_writes_the_credential_file(tmp_path, monkeypatch):
    """托管启动 Web UI 前把只读凭据写到 Web 主目录的 .delegation（MCP 桥每次调用前读它）。"""
    from swbrain import dsh_bridge
    cfg = {"agent": {"dsh": {"home": str(tmp_path / "h"), "home_web": str(tmp_path / "hw"), "web_port": 3099}}}

    class _Idn:
        def readonly_token(self, ttl_s=None):
            return "dlg_readonly_test"
    br = dsh_bridge.WebBridge(cfg); br.idn = _Idn()
    f = br.delegation_file()
    br.write_credential()
    assert f.read_text(encoding="utf-8") == "dlg_readonly_test" and f.parent.name == "hw"
    br.idn = None; br.write_credential()
    assert not f.exists()                                                        # 共享口令模式：不留凭据文件


def test_admin_can_create_and_delete_custom_agent_over_http(sdip, monkeypatch, tmp_path):
    """管理端新建 / 删除自建智能体：要 SWBRAIN.ADMIN；建了之后 /api/agents 里就有，删了就没有；审计记主体号。"""
    from swbrain.agents import registry as REG
    monkeypatch.setattr(REG, "CUSTOM", tmp_path / "custom.json"); monkeypatch.setattr(REG, "OVR", tmp_path / "ov.json")
    spec = {"id": "point_watch", "zh": "积水点值守", "role": "只看现状与历史。", "actions": ["inspect_point", "count_points"], "keywords": ["值守"]}
    ck1, _, _ = login(sdip, "u1")                                            # 没有 ADMIN
    assert req(sdip["port"], "/api/admin/agent_create", "POST", spec, {"Cookie": ck1, "Origin": PUB})[0] == 403
    ck2, _, _ = login(sdip, "u2")                                            # 有 ADMIN
    h = {"Cookie": ck2, "Origin": PUB}
    st, _, out = req(sdip["port"], "/api/admin/agent_create", "POST", spec, h)
    assert st == 200 and out["agent"]["custom"] is True, out
    st, _, bad = req(sdip["port"], "/api/admin/agent_create", "POST", {**spec, "actions": ["nope"]}, h)
    assert st == 400 and "不存在" in bad["error"]
    st, _, acts = req(sdip["port"], "/api/admin/actions", headers=h)
    assert st == 200 and any(a["api"] == "inspect_point" and a["owner"] == "dispatch" for a in acts["actions"])
    assert req(sdip["port"], "/api/admin/agent_delete", "POST", {"id": "hydro"}, h)[0] == 400   # 内置的只能停用
    assert req(sdip["port"], "/api/admin/agent_delete", "POST", {"id": "point_watch"}, h)[0] == 200
    assert not REG.is_custom("point_watch")


def test_two_public_bases_each_get_their_own_callback(monkeypatch):
    """本机与局域网两个地址都能登录（2026-10-02）：回调、注销回跳、来源校验按浏览器实际用的地址走，只认登记过的。"""
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    cfg = {"identity": {**SDIP_CFG["identity"], "public_base": "http://127.0.0.1:8766", "extra_public_bases": ["http://10.0.0.47:8766"]}}
    c = I.Config.from_cfg(cfg)
    assert c.public_bases == ("http://127.0.0.1:8766", "http://10.0.0.47:8766")
    assert c.base_for("10.0.0.47:8766") == "http://10.0.0.47:8766"
    assert c.base_for("127.0.0.1:8766") == "http://127.0.0.1:8766"
    assert c.base_for("evil.test") == "http://127.0.0.1:8766"                  # 伪造 Host 换不出别的回调
    idp = FakeIdp(); idn = I.Identity(cfg, jwks_fetch=_jwks, post=idp.post, get=idp.get)
    st = idn.sessions.begin("v", "/", "b", "http://10.0.0.47:8766/auth/callback")
    assert idn.sessions.take(st, "b")[2] == "http://10.0.0.47:8766/auth/callback"     # 换令牌时用发起登录那条
    assert "10.0.0.47" in idn.end_session_url(None, "http://10.0.0.47:8766")


def test_lan_login_flow_over_http(monkeypatch, tmp_path):
    """以局域网地址（Host: 10.0.0.47:8766）走完整登录：回调与会话都对，Origin 校验认局域网地址。"""
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    from swbrain.agents import admin_routes as ADM
    monkeypatch.setattr(ADM, "AUDIT", tmp_path / "audit.jsonl")
    cfg = {"identity": {**SDIP_CFG["identity"], "extra_public_bases": ["http://10.0.0.47:8766"]}}
    idp = FakeIdp(); idn = I.Identity(cfg, jwks_fetch=_jwks, post=idp.post, get=idp.get)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(_State(cfg), idn))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]; lan = {"Host": "10.0.0.47:8766"}
    try:
        code, hd, _ = req(port, "/auth/login", headers=lan)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(hd["Location"]).query)
        assert q["redirect_uri"] == ["http://10.0.0.47:8766/auth/callback"]
        binding = _cookies(hd)[I.LOGIN_COOKIE]; idp.codes["c-lan"] = "u1"
        seen = {}
        post0 = idp.post
        def post(url, data, timeout=15):
            if data.get("grant_type") == "authorization_code":
                seen["redirect_uri"] = data["redirect_uri"]
            return post0(url, data, timeout)
        idn._post = post
        code, hd, _ = req(port, f"/auth/callback?code=c-lan&state={q['state'][0]}", headers={**lan, "Cookie": f"{I.LOGIN_COOKIE}={binding}"})
        assert code == 302 and seen["redirect_uri"] == "http://10.0.0.47:8766/auth/callback"
        ck = f"{I.SESSION_COOKIE}={_cookies(hd)[I.SESSION_COOKIE]}"
        assert req(port, "/auth/logout", "POST", {}, {**lan, "Cookie": ck, "Origin": "http://10.0.0.47:8766"})[0] == 200
    finally:
        httpd.shutdown()


def test_auto_lan_follows_current_ip(monkeypatch):
    """extra_public_bases = ["auto"]（2026-10-09，局域网地址从 10.0.0.47 换成 192.168.50.224）：本机当前的局域网地址都算登记过的，换了不用改配置。"""
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    ips = ["192.168.50.224"]
    monkeypatch.setattr(I, "local_ipv4s", lambda: tuple(ips))
    c = I.Config.from_cfg({"identity": {**SDIP_CFG["identity"], "extra_public_bases": ["auto"]}})
    assert c.auto_lan and c.public_bases == (PUB,)
    assert c.base_for("192.168.50.224:8766") == "http://192.168.50.224:8766"
    ips[:] = ["10.1.2.3"]                                                       # 地址又变了：不重启就跟上，旧地址不再认
    assert c.base_for("10.1.2.3:8766") == "http://10.1.2.3:8766"
    assert c.base_for("192.168.50.224:8766") == PUB
    assert c.base_for("evil.test") == PUB


def test_localhost_redirects_to_registered_loopback(sdip):
    """用 localhost / [::1] 打开：平台只登记了 127.0.0.1 的回调，整页跳过去，不然登录回不来、写请求 Origin 对不上。"""
    c = I.Config.from_cfg(SDIP_CFG)
    assert c.loopback_alias("localhost:8766") == PUB and c.loopback_alias("[::1]:8766") == PUB
    assert c.loopback_alias("127.0.0.1:8766") is None and c.loopback_alias("evil.test") is None
    assert c.loopback_alias("localhost:9999") is None                            # 别的端口不是这个服务
    code, hd, _ = req(sdip["port"], "/index.html?x=1", headers={"Host": "localhost:8766"})
    assert code == 302 and hd["Location"] == PUB + "/index.html?x=1"


def test_issuer_discovery_follows_platform_address(monkeypatch):
    """issuer_discovery：配置写回环地址，iss 与登录地址按发现文档；签发源换了地址，验签时 iss 不符会再取一次。"""
    monkeypatch.setenv("SWB_TEST_SECRET", "s3cret")
    disc = {"issuer": ISS, "authorization_endpoint": ISS + "/protocol/openid-connect/auth"}
    def get(url, headers, timeout=15):
        if url.endswith("/.well-known/openid-configuration"):
            return 200, dict(disc)
        return FakeIdp().get(url, headers, timeout)
    cfg = {"identity": {**SDIP_CFG["identity"], "issuer": "http://127.0.0.1:8480/realms/sdip-dmt", "issuer_discovery": True}}
    idn = I.Identity(cfg, jwks_fetch=_jwks, post=FakeIdp().post, get=get)
    assert idn.cfg.iss == ISS and idn.authorize_url("s", "c").startswith(ISS + "/protocol/openid-connect/auth?")
    assert idn.cfg.jwks_uri.startswith("http://127.0.0.1:8480/")                # 服务端取公钥照走回环
    assert I.verify(idn.cfg, idn.jwks, _tok("u1", "swbrain"))["sub"] == "u1"
    new_iss = "http://192.0.2.9/realms/sdip-dmt"                                 # 平台换了地址：新令牌带新 iss
    disc["issuer"] = new_iss
    idn.cfg._disc["_at"] = -1e9
    t = jwt.encode({"iss": new_iss, "sub": "u1", "aud": "swbrain", "exp": int(time.time()) + 300}, _KEY, algorithm="RS256", headers={"kid": "k1"})
    assert I.verify(idn.cfg, idn.jwks, t)["sub"] == "u1" and idn.cfg.iss == new_iss
    with pytest.raises(I.IdentityError):                                         # 发现文档没变时，别的 iss 照拒
        I.verify(idn.cfg, idn.jwks, _tok("u1", "swbrain"))
