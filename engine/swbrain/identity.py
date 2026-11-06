# -*- coding: utf-8 -*-
"""统一身份与职能授权（SDIP，2026-10-01）。工单：docs/SDIP工单_排水智能体接入统一身份.md；照 REMS 已实跑的接法（D:/REMS/rems-app/docs/SDIP接入_配置说明.md）。

两种模式（[identity].mode）：
  shared_key  原来的两把共享口令（[serve].access_key / admin_key）。谁做的分不出，只给本机与演示用。缺省。
  sdip        每人用 SDIP（Keycloak）账号登录：授权码 + PKCE；服务端拿用户令牌做令牌交换（scope=openid sdip-semantic-aud sdip-identity，
              不是 audience），用换出来的令牌带 X-Sdip-Tenant 读 /api/portal/session，按其中 functions 判职能。共享口令一律不认。

职能（工单第二节）：SWBRAIN.VIEW 查看与分析 / FIELD 现场登记 / ALERT 签发预警 / PLAN 工程建议 / ADMIN 系统管理 / FEED 外部源推送（只给服务账号）。
除 VIEW、FEED 外都要先持有 VIEW——平台登记了前提，这里再核一次（平台改了也不会因此放宽）。

几条从 REMS 带过来的规矩：
  · 写动作前现查会话（撤销立即生效）；读接口用 60 s 缓存，拒绝缓存 15 s。连不上签发源 / 平台是 503，不是登出。
  · 未知 kid 触发的 JWKS 重拉每 30 s 最多一次（否则任何人都能用编出来的 kid 让本服务替他打 Keycloak）。
  · 主体号 0 是平台内置主体，一律拒。
  · 浏览器只持有一个 HttpOnly 会话号；令牌只在服务端。登录回调必须回到发起登录的同一个浏览器（预认证 cookie）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace

from . import envkeys

VIEW, FIELD, ALERT, PLAN, ADMIN, FEED = (f"SWBRAIN.{x}" for x in ("VIEW", "FIELD", "ALERT", "PLAN", "ADMIN", "FEED"))
FUNCTIONS = {VIEW: "查看与分析", FIELD: "现场登记", ALERT: "签发预警", PLAN: "工程建议", ADMIN: "系统管理", FEED: "外部源推送"}
NEEDS_VIEW = {FIELD, ALERT, PLAN, ADMIN}

# 本体动作 → 要的职能（没列的动作只读，要 VIEW）。traffic_status 只有 refresh=true 才要 ADMIN，见 action_function。
ACTION_FUNCTION = {"record_observation": FIELD, "update_episode": FIELD, "close_point": FIELD,
                   "open_event": ALERT, "issue_alert": ALERT, "set_proposal_status": PLAN,
                   "hydro_submit": VIEW, "report_submit": VIEW}
# /api/feed/<源> → 要的职能：推进来的事件同样进事件清单，和 open_event 同级；经历和 update_episode 同级
FEED_FUNCTION = {"events": ALERT, "episodes": FIELD, "incidents": FEED, "checkins": FEED, "bridges": FEED, "contractors": FEED}
EXCHANGE_SCOPE = "openid sdip-semantic-aud sdip-identity"


def action_function(api: str, params: dict | None = None) -> str:
    if api == "traffic_status" and str((params or {}).get("refresh", "")).lower() in ("1", "true", "yes"):
        return ADMIN                                        # 刷新路况调 Google Routes，按次计费
    return ACTION_FUNCTION.get(api, VIEW)


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_LAN_CACHE: tuple[float, tuple[str, ...]] = (0.0, ())
_LAN_TTL_S = 30.0


def local_ipv4s() -> tuple[str, ...]:
    """本机当前的非回环 IPv4（局域网地址会变：DHCP 换租、换网络）。30 s 缓存，换了地址不用重启服务。
    先问路由（UDP connect 不发包）拿默认出口地址，再补主机名解析出的其余网卡。"""
    global _LAN_CACHE
    now = time.monotonic()
    if _LAN_CACHE[1] and now - _LAN_CACHE[0] < _LAN_TTL_S:
        return _LAN_CACHE[1]
    import socket
    out: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1)); out.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        out += [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    ips = tuple(dict.fromkeys(ip for ip in out if ip and not ip.startswith(("127.", "0.", "169.254."))))
    _LAN_CACHE = (now, ips)
    return ips


def _with_host(base: str, host: str) -> str:
    u = urllib.parse.urlparse(base)
    return u._replace(netloc=host + (f":{u.port}" if u.port else "")).geturl()


class IdentityError(Exception):
    """认证 / 授权不成立（401 / 403）。消息给日志；给用户的话由调用方定，不区分「不存在」与「无权限」。"""


class IdentityUnavailable(Exception):
    """签发源或平台连不上 / 5xx（503）。不是登出。"""


class Forbidden(IdentityError):
    """已认证，但缺职能（403）。"""

    def __init__(self, need: str, why: str = ""):
        self.need = need
        super().__init__(why or f"缺职能 {need}（{FUNCTIONS.get(need, need)}）")


# ---------------------------------------------------------------- 配置
@dataclass(frozen=True)
class Config:
    mode: str = "shared_key"
    issuer: str = ""
    jwks_uri: str = ""
    token_uri: str = ""
    client_id: str = "swbrain"
    client_secret: str = ""
    audience: str = "swbrain"
    portal_base: str = ""
    tenant_id: int | None = None
    public_base: str = "http://127.0.0.1:8766"
    public_bases: tuple[str, ...] = ("http://127.0.0.1:8766",)
    exchange_scope: str = EXCHANGE_SCOPE
    leeway_s: int = 30
    session_hours: float = 12.0
    feed_clients: tuple[str, ...] = ()
    # 平台转发过来的令牌的 aud。见 Identity.受平台转发 —— 平台在把令牌交给智能体层之前
    # 做过一次换域，带过来的就是那一张。缺省只认这一项。
    accept_audience: tuple[str, ...] = ("sdip-semantic",)
    # extra_public_bases 里写了 "auto"：本机当前的每个局域网 IPv4 都算登记过的对外地址（端口、协议同 public_base）。
    # 局域网地址会变（2026-10-08 从 10.0.0.47 换成 192.168.50.224），写死的地址一换就进不来
    auto_lan: bool = False
    # issuer_discovery = true：启动时取 <issuer>/.well-known/openid-configuration，按它给的 issuer 验 iss、按它给的地址让浏览器去登录。
    # 这样 issuer 可以写本机回环地址（服务端到签发源始终走得通），签发源换了局域网地址也不用改配置；验签遇到 iss 不符时再取一次
    discover: bool = False
    _disc: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    @property
    def sdip(self) -> bool:
        return self.mode == "sdip"

    @property
    def redirect_uri(self) -> str:
        return self.public_base + "/auth/callback"

    def all_bases(self) -> tuple[str, ...]:
        """登记的对外地址 + （auto 时）本机当前每个局域网地址。主地址在最前。"""
        if not self.auto_lan:
            return self.public_bases
        return tuple(dict.fromkeys(self.public_bases + tuple(_with_host(self.public_base, ip) for ip in local_ipv4s())))

    def base_for(self, host: str | None) -> str:
        """这个请求从哪个对外地址进来的（按 Host 头，只认登记过的 / 本机当前的局域网地址；对不上用主地址）。
        回调、注销回跳、写请求的来源校验都按它：本机用 127.0.0.1、局域网用局域网地址，各走各的。
        只认清单里的，所以伪造 Host 头换不出别的回调（Keycloak 那边也只认登记过的）。"""
        h = str(host or "").strip().lower()
        for b in self.all_bases():
            if urllib.parse.urlparse(b).netloc.lower() == h:
                return b
        return self.public_base

    def loopback_alias(self, host: str | None) -> str | None:
        """Host 是本机回环的别名（localhost、[::1]、127.0.0.1 换了写法）但没登记：返回该跳去的登记地址，否则 None。
        平台只登记了 127.0.0.1 的回调；用 localhost 打开时登录回不来、写请求的 Origin 也对不上，所以整页跳过去。"""
        h = str(host or "").strip().lower()
        if not h or any(urllib.parse.urlparse(b).netloc.lower() == h for b in self.all_bases()):
            return None
        hu = urllib.parse.urlparse("//" + h)
        if (hu.hostname or "") not in LOOPBACK_HOSTS:
            return None
        for b in self.all_bases():
            bu = urllib.parse.urlparse(b)                    # 同一个端口才是同一个服务（别的端口上的是另一回事，不管）
            if (bu.hostname or "") in LOOPBACK_HOSTS and bu.hostname != hu.hostname and (bu.port or 80) == (hu.port or 80):
                return b
        return None

    @property
    def iss(self) -> str:
        """令牌里应有的 iss：发现文档给的优先（签发源按它自己的主机名签），否则配置里的。"""
        return str(self._disc.get("issuer") or self.issuer).rstrip("/")

    @property
    def authorization_endpoint(self) -> str:
        return self._disc.get("authorization_endpoint") or self.iss + "/protocol/openid-connect/auth"

    @property
    def end_session_endpoint(self) -> str:
        return self._disc.get("end_session_endpoint") or self.iss + "/protocol/openid-connect/logout"

    def rediscover(self, min_interval_s: float = 60.0, get=None) -> bool:
        """取一次发现文档（discover 才取；两次之间至少隔 min_interval_s）。返回 iss 有没有变。连不上就保留原来的。"""
        if not (self.discover and self.issuer):
            return False
        now = time.monotonic()
        if now - float(self._disc.get("_at", -1e9)) < min_interval_s:
            return False
        self._disc["_at"] = now
        if get is not None:
            self._disc["_get"] = get                          # 以后验签时的重取用同一个（测试里是假签发源）
        try:
            st, d = (self._disc.get("_get") or _get_json)(self.issuer + "/.well-known/openid-configuration", {})
        except IdentityUnavailable:
            return False
        if st != 200 or not isinstance(d, dict) or not d.get("issuer"):
            return False
        before = self.iss
        for k in ("issuer", "authorization_endpoint", "end_session_endpoint"):
            if d.get(k):
                self._disc[k] = str(d[k]).rstrip("/") if k == "issuer" else str(d[k])
        return self.iss != before

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Config":
        I = (cfg or {}).get("identity") or {}
        mode = str(I.get("mode") or "shared_key").strip()
        if mode not in ("shared_key", "sdip"):
            raise ValueError(f"[identity].mode 只能是 shared_key 或 sdip：{mode!r}")
        issuer = str(I.get("issuer") or "").strip().rstrip("/")
        secret = envkeys.get(str(I.get("client_secret_env") or "SWBRAIN_SDIP_CLIENT_SECRET")) or ""
        tenant = I.get("tenant_id")
        c = cls(mode=mode, issuer=issuer,
                # 只配签发源即可：JWKS 与令牌地址按 Keycloak 约定推出，平台换地址只改一处（指南 §5c）
                jwks_uri=str(I.get("jwks_uri") or (issuer + "/protocol/openid-connect/certs" if issuer else "")).strip(),
                token_uri=str(I.get("token_uri") or (issuer + "/protocol/openid-connect/token" if issuer else "")).strip(),
                client_id=str(I.get("client_id") or "swbrain").strip(), client_secret=secret.strip(),
                audience=str(I.get("audience") or I.get("client_id") or "swbrain").strip(),
                portal_base=str(I.get("portal_base") or "").strip().rstrip("/"),
                tenant_id=int(tenant) if str(tenant or "").strip().lstrip("-").isdigit() else None,
                public_base=str(I.get("public_base") or "http://127.0.0.1:8766").strip().rstrip("/"),
                # 其余对外地址（如局域网 http://10.0.0.47:8766）：每个都要在平台登记回调；主地址自动在清单里
                public_bases=tuple(dict.fromkeys([str(I.get("public_base") or "http://127.0.0.1:8766").strip().rstrip("/")]
                                                 + [str(x).strip().rstrip("/") for x in (I.get("extra_public_bases") or [])
                                                    if str(x).strip() and str(x).strip().lower() != "auto"])),
                auto_lan=any(str(x).strip().lower() == "auto" for x in (I.get("extra_public_bases") or [])),
                discover=bool(I.get("issuer_discovery", False)),
                leeway_s=int(I.get("leeway_s", 30)), session_hours=float(I.get("session_hours", 12)),
                accept_audience=tuple(str(x).strip() for x in (I.get("accept_audience") or ["sdip-semantic"]) if str(x).strip()),
                feed_clients=tuple(str(x) for x in (I.get("feed_clients") or ["service-account-" + str(I.get("client_id") or "swbrain")])))
        if c.sdip:
            miss = [k for k, v in (("issuer", c.issuer), ("portal_base", c.portal_base), ("tenant_id", c.tenant_id), ("client_secret（环境变量）", c.client_secret)) if v in ("", None)]
            if miss:
                raise ValueError("[identity].mode = sdip 但缺：" + "、".join(miss) + "。未配齐时不放行任何请求，也不会退回共享口令")
            try:
                import jwt  # noqa: F401   只有 sdip 模式要验签；shared_key 模式不装也能用
            except ImportError:
                raise ValueError('[identity].mode = sdip 要验签库：pip install "PyJWT[crypto]==2.10.1"（不对接统一身份就把 mode 改回 shared_key）')
        return c


# ---------------------------------------------------------------- HTTP（纯 urllib，和本仓库其他外呼一致）
def _post_form(url: str, data: dict, timeout: float = 15) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    return _send(req, timeout)


def _get_json(url: str, headers: dict, timeout: float = 15) -> tuple[int, dict | list]:
    return _send(urllib.request.Request(url, headers={"Accept": "application/json", **headers}), timeout)


def _send(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return e.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise IdentityUnavailable(f"{req.full_url.split('?')[0]}：{type(e).__name__}") from e


# ---------------------------------------------------------------- 验签
class Jwks:
    """签发源公钥集。到期（5 min）重取；未知 kid 触发的重拉每 30 s 最多一次，限频期内未知 kid 直接拒。"""
    REFRESH_S = 30.0
    LIFESPAN_S = 300.0

    def __init__(self, uri: str, fetch=None, clock=time.monotonic):
        self.uri, self._fetch_fn, self._clock = uri, fetch or self._fetch_http, clock
        self._keys: dict | None = None; self._at = 0.0; self._tried: float | None = None; self._lock = threading.Lock()

    def _fetch_http(self) -> dict:
        code, body = _get_json(self.uri, {}, timeout=10)
        if code >= 500:
            raise IdentityUnavailable(f"JWKS 返回 {code}")
        if code != 200 or not isinstance(body, dict):
            raise IdentityError(f"JWKS 返回 {code}")
        return body

    def _refresh(self, now):
        import jwt
        self._tried = now
        ks = jwt.PyJWKSet.from_dict(self._fetch_fn())
        self._keys = {k.key_id: k for k in ks.keys}; self._at = now

    def key_for(self, token: str):
        import jwt
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except Exception as ex:
            raise IdentityError(f"令牌头无法解析：{type(ex).__name__}") from ex
        with self._lock:
            now = self._clock(); may = self._tried is None or now - self._tried >= self.REFRESH_S
            if (self._keys is None or now - self._at >= self.LIFESPAN_S) and may:
                try:
                    self._refresh(now)
                except IdentityUnavailable:
                    if self._keys is None:
                        raise                                   # 手里有上一版就继续用
                may = False
            if self._keys is None:
                raise IdentityUnavailable("JWKS 尚未取到，且在重拉限频窗口内")
            k = self._keys.get(kid)
            if k is None and may:
                self._refresh(now); k = self._keys.get(kid)
        if k is None:
            raise IdentityError(f"未知的签名密钥 kid={kid!r}")
        return k.key


def verify(cfg: Config, jwks: Jwks, token: str, audience: str | tuple[str, ...] | None = None) -> dict:
    """验签并校验 iss / aud / exp / sub。只认配置里那一个签发源。

    `audience` 可以给一组：查平台会话那次必须用换域后的令牌（`aud` 含
    `sdip-semantic`），而平台转发过来的也是那一张 —— 两处都传
    `cfg.accept_audience`。给一组不是放宽：列的是**我们确实认的那几个**，
    不在这一组里的（比如浏览器直接带的人员令牌，`aud` 只有 `swbrain`）
    照样进不来。
    """
    import jwt
    def _decode():
        return jwt.decode(token, jwks.key_for(token), algorithms=["RS256", "ES256"], audience=audience or cfg.audience, issuer=cfg.iss,
                          leeway=cfg.leeway_s, options={"require": ["exp", "iss", "aud", "sub"]})
    try:
        try:
            return _decode()
        except jwt.InvalidIssuerError:
            # 签名已经过了（PyJWT 先验签再查声明），只是 iss 不符：签发源多半换了局域网地址。重取发现文档，变了就再验一次
            if cfg.rediscover():
                return _decode()
            raise
    except (IdentityError, IdentityUnavailable):
        raise
    except Exception as ex:
        raise IdentityError(f"令牌校验失败：{type(ex).__name__}: {ex}") from ex


# ---------------------------------------------------------------- 主体与职能
@dataclass
class Principal:
    """一个已认证的调用人。principal_id 是平台主体号（审计记它），kind = user / service / local。"""
    principal_id: str
    name: str = ""
    tenant_id: int | None = None
    functions: dict = field(default_factory=dict)           # code → {grantDoc, validUntil}
    kind: str = "user"
    username: str = ""
    refresh: object = field(default=None, repr=False, compare=False)     # () -> Principal | None：现查平台；serve.py 按请求挂上
    session_ref: str | None = field(default=None, repr=False, compare=False)   # 这个请求的会话号（只在内存，发委托凭据时回指会话用）
    readonly: bool = False        # 只读：会写数据的动作一律拒（Harness 原生 Web UI 入口用它，那个入口分不出是谁在用）

    def has(self, code: str) -> bool:
        if code not in self.functions:
            return False
        return code not in NEEDS_VIEW or VIEW in self.functions     # 前提再核一次

    def need(self, code: str):
        if not self.has(code):
            raise Forbidden(code)

    @property
    def actor(self) -> str:
        """记进动作日志 / 审计的身份：sdip:<主体号>；共享口令模式是 local:<来源>。"""
        return f"sdip:{self.principal_id}" if self.kind in ("user", "service") else f"local:{self.principal_id}"

    def public(self) -> dict:
        return {"principal_id": self.principal_id, "name": self.name, "username": self.username, "tenant_id": self.tenant_id, "kind": self.kind,
                "functions": sorted(c for c in self.functions if self.has(c)), "actor": self.actor, "readonly": self.readonly}


def local_principal(src: str, admin: bool = False) -> Principal:
    """共享口令模式：拿口令的人什么都能做（原来就是这样，包括刷新路况），身份记 local:console / local:admin。
    管理端在这个模式下照旧另凭 admin_key（admin_routes.is_admin），不看这里的职能。"""
    fns = {c: {"grantDoc": "shared_key"} for c in FUNCTIONS}
    return Principal(principal_id="admin" if admin else (src or "console"), name="共享口令", kind="local", functions=fns)


def _functions(session: dict) -> dict:
    """会话里本系统的生效职能。平台只返回生效的；这里只做形状防御、只收 SWBRAIN.*，不补默认值。"""
    items = session.get("functions") if isinstance(session, dict) else None
    return {str(i["code"]): {"grantDoc": i.get("grantDoc"), "validUntil": i.get("validUntil")}
            for i in items or [] if isinstance(i, dict) and str(i.get("code") or "").startswith("SWBRAIN.")}


class Portal:
    """令牌交换与 /api/portal/session。"""

    def __init__(self, cfg: Config, jwks: Jwks, post=None, get=None):
        self.cfg, self.jwks = cfg, jwks
        self._post, self._get = post or _post_form, get or _get_json
        # 平台转发过来的令牌 → 主体（见 受平台转发）。它落在本类而不是 Identity：
        # 判据（验签 ＋ 查会话）都在这里，缓存的键也因此在这里。
        # 与 Identity 的 _bearer 分开两张表：两者的「谁的凭据」不同
        # （一张是本客户端的服务账号、一张是平台转来的人），
        # 合在一起时一张表会被另一侧的清空逻辑带走，症状是「偶尔要重新认一次」。
        self._forward: dict[str, tuple] = {}
        self._bl = threading.Lock()

    def exchange(self, user_token: str) -> str:
        """用户令牌 → 能调 SDIP 的令牌。传 scope，不传 audience（后者在这套 Keycloak 上报 Requested audience not available）。"""
        code, body = self._post(self.cfg.token_uri, {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "client_id": self.cfg.client_id,
                                                     "client_secret": self.cfg.client_secret, "subject_token": user_token,
                                                     "subject_token_type": "urn:ietf:params:oauth:token-type:access_token", "scope": self.cfg.exchange_scope})
        if code >= 500:
            raise IdentityUnavailable(f"令牌交换返回 {code}")
        if code != 200 or not body.get("access_token"):
            raise IdentityError(f"令牌交换失败：{body.get('error', code)}")          # 只取 error 码：正文可能含令牌片段
        return body["access_token"]

    def session(self, exchanged: str) -> dict:
        code, body = self._get(f"{self.cfg.portal_base}/api/portal/session",
                               {"Authorization": f"Bearer {exchanged}", "X-Sdip-Tenant": str(self.cfg.tenant_id), "Accept-Language": "zh"})
        if code >= 500:
            raise IdentityUnavailable(f"/api/portal/session 返回 {code}")
        if code != 200 or not isinstance(body, dict):
            raise IdentityError(f"/api/portal/session 返回 {code}")
        return body

    def establish(self, user_token: str) -> Principal:
        """交换 → 验交换后的令牌 → 查会话 → 定主体号与职能。主体号两条路（令牌里的 sdip_principal_id、会话里的 principalId）都有时必须一致。

        ⚠️ 受众只认 `sdip-semantic`（写死），不传 `accept_audience`：
        我们换出来的令牌一定是那一个，而宽一点的集合是为**转发**那一侧准备的。
        两边共用一处判据（{@link #_从换域令牌}）不等于共用同一个受众集合。
        """
        return self._从换域令牌(self.exchange(user_token), ("sdip-semantic",))

    def 受平台转发(self, token: str, 租户头: str | None = None) -> Principal:
        """平台把一次问数转给本系统时带过来的令牌：<b>它已经换过域了</b>，不再换第二次。

        <h2>为什么单开一条，不复用 establish</h2>

        平台在把令牌交给智能体层之前已经做过一次换域（`aud` 里因此含
        `sdip-semantic`）。再换一次有两处不对：一是那张令牌的发起客户端不是本系统，
        交换要么被拒要么换出另一张语义不同的；二是「谁在问」这件事会多绕一跳，
        而绕的那一跳出错时报的是「令牌交换失败」，离「转发没带对令牌」很远。

        所以这里只做两件事：**验签**（`iss` 是本签发源、`aud` 在 accept_audience 里），
        **查平台会话**（主体号与 `SWBRAIN.*` 职能）。两件都与 establish 的后半段同源 ——
        共用 {@link #_从换域令牌}，不各写一份：主体号两条路、函数前缀过滤、
        没有 VIEW 就拒，这些判据只许有一处。

        ⚠️ **不因此放宽原有的两条**：浏览器直接带的人员令牌（`aud` 只含 `swbrain`）
        在这里照样进不来（`aud` 对不上 accept_audience）；`/api/feed/*` 那条路
        仍只认客户端凭据（见 {@link #bearer}）。转发这一支只多开一个入口，不动别的。

        @param 租户头 平台带过来的 `X-Sdip-Tenant`。给了就核一次 —— 配的是租户 2
                      而请求头说 3，那是平台侧配错了端点的租户，早拒比把租户 3 的
                      职能用来判租户 2 的事好

        <h2>结果缓 60 秒，写动作前现查</h2>

        与 {@link #bearer} 同一个做法：同一条令牌 60 秒里只查一次平台会话
        （一次问数里模型会连着调几个工具，每个都查一遍纯属白花）。
        {@code refresh} 照样挂上 —— 那个字段由服务端在写请求前调一次，
        所以「撤销立即生效」这条对它也成立，不因为它是转发来的就打折。
        """
        if 自报租户(租户头) not in (None, self.cfg.tenant_id):
            raise IdentityError(
                f"这次转发声明的是租户 {自报租户(租户头)}，而本系统接的是租户 {self.cfg.tenant_id}。"
                "平台侧那条外部能力端点登记到了别的租户上")
        k = _digest(token)
        with self._bl:
            c = self._forward.get(k)
        if c and time.monotonic() - c[1] < (REFUSED_TTL_S if isinstance(c[0], Exception) else GRANT_TTL_S):
            if isinstance(c[0], Exception):
                raise IdentityError(str(c[0]))
            c[0].refresh = lambda: self._转发现查(token)                    # 现查一次新的（写动作前由服务端调）
            return c[0]
        try:
            p = self._从换域令牌(token, self.cfg.accept_audience)
        except IdentityError as ex:
            # ⚠️ 只缓 IdentityError（拒）。IdentityUnavailable（签发源连不上）
            #    不进缓存 —— 那是一次临时故障，缓存下来会让恢复之后还继续拒 60 秒
            with self._bl:
                self._forward[k] = (ex, time.monotonic())
            raise
        p.refresh = lambda: self._转发现查(token)
        with self._bl:
            if len(self._forward) > 1024:
                self._forward.clear()
            self._forward[k] = (p, time.monotonic())
        return p

    def _转发现查(self, token: str) -> Principal | None:
        """写动作前现查一次平台（撤销立即生效）。查不动就回 None —— 与浏览器会话那条路同一个信号：
        服务端拿到 None 时回 401「会话已结束，请重新登录」，而不是 500。
        这里抛异常的话会落进 兜底那个 `except Exception` 变成 500，而「职能被撤了」
        报成 500 会让人去查服务日志，离真因（那个人已经没有这项职能了）很远。"""
        try:
            p = self._从换域令牌(token, self.cfg.accept_audience)
        except (IdentityError, IdentityUnavailable):
            return None
        p.refresh = lambda: self._转发现查(token)
        with self._bl:
            self._forward[_digest(token)] = (p, time.monotonic())
        return p

    def _从换域令牌(self, ex: str, 受众: tuple[str, ...] | str) -> Principal:
        """一张已换域的令牌 → 主体。两条入口（本地换域、平台转发）共用这一处判据。

        ⚠️ 受众是**参数**不是 self.cfg.accept_audience：两条入口该认的集合不同
        （本地换出来的只会是 sdip-semantic，而平台转过来的按配置），
        共用一处判据指的是「主体号两条路、函数前缀过滤、没有 VIEW 就拒」这几条，
        不含受众。合成一个默认值就会变成「改转发那一侧顺带放宽了本地那一侧」。
        """
        claims = verify(self.cfg, self.jwks, ex, audience=受众)
        s = self.session(ex)
        sub = s.get("subject") if isinstance(s.get("subject"), dict) else {}
        declared, resolved = claims.get("sdip_principal_id"), s.get("principalId") or sub.get("principalId")
        if declared in (None, "") and resolved in (None, ""):
            raise IdentityError("交换后的令牌没有 sdip_principal_id，会话也没有 principalId")
        if declared not in (None, "") and resolved not in (None, "") and str(declared) != str(resolved):
            raise IdentityError(f"令牌声明的主体 {declared} 与平台查出的 {resolved} 不一致")
        pid = str(resolved if resolved not in (None, "") else declared)
        if pid == "0":
            raise IdentityError("解析出内置主体 0，拒绝")
        fns = _functions(s)
        if VIEW not in fns and FEED not in fns:
            raise Forbidden(VIEW, f"主体 {pid} 在租户 {self.cfg.tenant_id} 没有本系统的职能")
        return Principal(principal_id=pid, name=str(s.get("displayName") or sub.get("displayName") or claims.get("name") or ""),
                         tenant_id=self.cfg.tenant_id, functions=fns, kind="user" if VIEW in fns else "service",
                         username=str(claims.get("preferred_username") or claims.get("azp") or ""))


# ---------------------------------------------------------------- 会话（内存；服务重启 = 大家重新登录一次）
SESSION_COOKIE = "swb_session"
LOGIN_COOKIE = "swb_login"            # 预认证 cookie：把发起登录的浏览器与完成回调的浏览器绑在一起（防登录 CSRF）
PENDING_TTL_S = 600                   # 首次登录要在 Keycloak 改初始口令，不能太短
GRANT_TTL_S, REFUSED_TTL_S = 60.0, 15.0


def _digest(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def new_pkce() -> tuple[str, str]:
    v = _b64url(secrets.token_bytes(64))
    return v, _b64url(hashlib.sha256(v.encode("ascii")).digest())


def safe_next(v: str | None) -> str:
    """只收站内相对路径（//evil、/\\evil、控制字符都会被浏览器变成外站）。"""
    if not v or not v.startswith("/") or v.startswith("//") or "\\" in v or any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        return "/"
    return v


def 自报租户(头: str | None) -> int | None:
    """平台转发时带的 `X-Sdip-Tenant`。不是数字就当没给 —— 那一头是调用方自报的标签，
    不是身份（同 `api_actor` 那条纪律）；它的用途只有「与本地配置对不上就早拒」这一条。"""
    v = str(头 or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


@dataclass
class Session:
    user_token: str
    refresh_token: str | None
    id_token: str | None
    expires_at: float
    refresh_expires_at: float | None = None
    created_at: float = field(default_factory=time.time)
    principal: Principal | None = None                 # 最近一次 establish 的结果
    grant_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    RENEW_BEFORE_S = 30

    def dead(self, max_h: float) -> bool:
        now = time.time()
        return (now - self.created_at > max_h * 3600 or (self.refresh_expires_at is not None and now >= self.refresh_expires_at)
                or (not self.refresh_token and now >= self.expires_at))


class Sessions:
    def __init__(self, max_sessions: int = 512):
        self._s: dict[str, Session] = {}; self._pending: dict[str, tuple] = {}; self._lock = threading.Lock(); self.max = max_sessions

    def begin(self, verifier: str, next_url: str, binding: str, redirect_uri: str = "") -> str:
        state = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._pending = {k: v for k, v in self._pending.items() if v[3] > now}
            if len(self._pending) > 4096:
                raise IdentityUnavailable("待完成的登录太多")
            self._pending[state] = (verifier, next_url, _digest(binding), now + PENDING_TTL_S, redirect_uri)
        return state

    def take(self, state: str, binding: str | None) -> tuple[str, str, str] | None:
        """state 一次性、要在有效期内、且必须是发起登录的同一个浏览器（预认证 cookie 摘要对得上）。
        返回 (verifier, next_url, redirect_uri)：换令牌时的回调必须和发起登录时的一字不差（OAuth 规定）。"""
        with self._lock:
            p = self._pending.pop(state or "", None)
        if not p or p[3] < time.time() or not binding or not secrets.compare_digest(p[2], _digest(binding)):
            return None
        return p[0], p[1], (p[4] if len(p) > 4 else "")

    def create(self, s: Session) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            if len(self._s) >= self.max:                    # 满了先丢最老的
                for k in sorted(self._s, key=lambda k: self._s[k].created_at)[: len(self._s) - self.max + 1]:
                    self._s.pop(k, None)
            self._s[_digest(sid)] = s
        return sid

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            return self._s.get(_digest(sid))

    def drop(self, sid: str | None):
        if sid:
            with self._lock:
                self._s.pop(_digest(sid), None)

    def __len__(self):
        return len(self._s)


def cookie(header: str | None, name: str) -> str:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return urllib.parse.unquote(v)
    return ""


# ---------------------------------------------------------------- 当前请求是谁（每个请求一个线程，serve.py 设、处理函数读）
import contextvars  # noqa: E402

_current: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("swb_principal", default=None)


def current() -> Principal | None:
    return _current.get()


def bind(p: Principal | None):
    return _current.set(p)


def unbind(tok):
    _current.reset(tok)


# ---------------------------------------------------------------- 对外入口
class Identity:
    """serve.py 持有一个。shared_key 模式下大部分方法用不到。"""

    def __init__(self, cfg: dict, jwks_fetch=None, post=None, get=None):
        self.cfg = Config.from_cfg(cfg)
        if self.cfg.sdip:
            self.cfg.rediscover(0, get=get)              # issuer_discovery = true 时才真去取；取不到照配置的 issuer
        self.jwks = Jwks(self.cfg.jwks_uri, fetch=jwks_fetch) if self.cfg.sdip else None
        self._post = post or _post_form
        self.portal = Portal(self.cfg, self.jwks, post=self._post, get=get) if self.cfg.sdip else None
        self.sessions = Sessions()
        self._bearer: dict[str, tuple] = {}; self._bl = threading.Lock()
        # 平台转发过来的令牌 → 主体（见 受平台转发）。与 _bearer 分开两张表：
        # 两者的「谁的凭据」不同（一张是本客户端的服务账号、一张是平台转来的人），
        # 合在一起时一张表被另一侧的清空逻辑带走，症状是「偶尔要重新认一次」。
        self._forward: dict[str, tuple] = {}
        self._delegates: dict[str, tuple] = {}; self._dl = threading.Lock()

    @property
    def sdip(self) -> bool:
        return self.cfg.sdip

    # ---- 登录
    def authorize_url(self, state: str, challenge: str, redirect_uri: str | None = None) -> str:
        q = urllib.parse.urlencode({"client_id": self.cfg.client_id, "response_type": "code", "redirect_uri": redirect_uri or self.cfg.redirect_uri, "scope": "openid",
                                    "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        return f"{self.cfg.authorization_endpoint}?{q}"

    def end_session_url(self, id_token: str | None, base: str | None = None) -> str:
        """RP 发起的注销。只清本地会话是假注销：SSO 会话还在，再点登录不用输口令就回来了。"""
        q = {"post_logout_redirect_uri": (base or self.cfg.public_base) + "/", "client_id": self.cfg.client_id}
        if id_token:
            q["id_token_hint"] = id_token
        return f"{self.cfg.end_session_endpoint}?{urllib.parse.urlencode(q)}"

    def finish_login(self, code: str, verifier: str, redirect_uri: str | None = None) -> tuple[str, Principal]:
        """code → 令牌 → 验签 → 交换 + 查职能。返回 (会话号, 主体)。没有本系统任何职能的人建不了会话。
        redirect_uri 要和发起登录时用的一致（本机、局域网各一条）。"""
        st, body = self._post(self.cfg.token_uri, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri or self.cfg.redirect_uri,
                                                   "client_id": self.cfg.client_id, "client_secret": self.cfg.client_secret, "code_verifier": verifier})
        if st >= 500:
            raise IdentityUnavailable(f"换取令牌返回 {st}")
        if st != 200 or not body.get("access_token"):
            raise IdentityError(f"换取令牌失败：{body.get('error', st)}")
        tok = body["access_token"]
        claims = verify(self.cfg, self.jwks, tok)
        try:
            p = self.portal.establish(tok)
            if p.kind != "user":
                raise Forbidden(VIEW, "服务账号不能登录控制台")
        except Forbidden as ex:
            ex.username = str(claims.get("preferred_username") or claims.get("sub") or "")      # 登录被拒时告诉人是哪个账号（常见：浏览器里还留着别的账号的会话）
            raise
        s = Session(user_token=tok, refresh_token=body.get("refresh_token"), id_token=body.get("id_token"),
                    expires_at=time.time() + int(body.get("expires_in") or 300), refresh_expires_at=_refresh_deadline(body), principal=p, grant_at=time.monotonic())
        return self.sessions.create(s), p

    def _renew(self, sid: str, s: Session) -> Session | None:
        """访问令牌快到期就用 refresh_token 续。签发源明确拒（invalid_grant）才结束会话；连不上抛 IdentityUnavailable、不登出。"""
        with s.lock:
            if time.time() < s.expires_at - s.RENEW_BEFORE_S:
                return s
            if s.dead(self.cfg.session_hours) or not s.refresh_token:
                self.sessions.drop(sid); return None
            st, body = self._post(self.cfg.token_uri, {"grant_type": "refresh_token", "refresh_token": s.refresh_token,
                                                       "client_id": self.cfg.client_id, "client_secret": self.cfg.client_secret})
            if st == 400 and body.get("error") == "invalid_grant":
                self.sessions.drop(sid); return None
            if st != 200 or not body.get("access_token"):
                raise IdentityUnavailable(f"续期返回 {st}")
            try:
                verify(self.cfg, self.jwks, body["access_token"])
            except IdentityError:
                self.sessions.drop(sid); return None
            s.user_token = body["access_token"]; s.refresh_token = body.get("refresh_token") or s.refresh_token
            s.id_token = body.get("id_token") or s.id_token
            s.expires_at = time.time() + int(body.get("expires_in") or 300); s.refresh_expires_at = _refresh_deadline(body) or s.refresh_expires_at
            return s

    def principal(self, sid: str | None, fresh: bool = False) -> Principal | None:
        """会话号 → 主体。fresh = True（写动作前）现查平台，撤销立即生效；否则用 60 s 内的结果。没登录 / 会话没了返回 None。"""
        s = self.sessions.get(sid)
        if s is None:
            return None
        if s.dead(self.cfg.session_hours):
            self.sessions.drop(sid); return None
        s = self._renew(sid, s)
        if s is None:
            return None
        if fresh or s.principal is None or time.monotonic() - s.grant_at > GRANT_TTL_S:
            try:
                s.principal = self.portal.establish(s.user_token); s.grant_at = time.monotonic()
            except Forbidden:
                s.principal = Principal(principal_id=s.principal.principal_id if s.principal else "?", name=s.principal.name if s.principal else "",
                                        tenant_id=self.cfg.tenant_id, functions={}); s.grant_at = time.monotonic()
        return s.principal

    def logout(self, sid: str | None, base: str | None = None) -> str:
        s = self.sessions.get(sid); self.sessions.drop(sid)
        return self.end_session_url(s.id_token if s else None, base)

    # ---- 平台转发（serve.py 走这一支，判据在 Portal 上）
    def 受平台转发(self, token: str, 租户头: str | None = None) -> Principal:
        """平台把一次问数转给本系统时带过来的令牌。判据在 {@link Portal#受平台转发} ——
        这里只做一件事：**没配 sdip 时明确拒**，而不是让它落到 portal 为 None 的
        AttributeError 上（那句报错读起来像代码 bug，而真因是这套部署没接统一身份）。"""
        if not self.cfg.sdip:
            raise IdentityError("本部署没接统一身份（[identity].mode 不是 sdip），不接受平台转发")
        return self.portal.受平台转发(token, 租户头)

    # ---- 服务账号（Authorization: Bearer，只给 /api/feed/*）
    def bearer(self, token: str, fresh: bool = True) -> Principal:
        """服务账号的客户端凭据令牌。要 aud 含本客户端、azp 在 [identity].feed_clients 里；职能照样从平台会话读。拒绝结果缓存 15 s（不让拒绝变成放大器）。"""
        k = _digest(token)
        with self._bl:
            c = self._bearer.get(k)
        if c and not fresh and time.monotonic() - c[1] < (REFUSED_TTL_S if isinstance(c[0], Exception) else GRANT_TTL_S):
            if isinstance(c[0], Exception):
                raise IdentityError(str(c[0]))
            return c[0]
        try:
            claims = verify(self.cfg, self.jwks, token)
            # 服务账号是本客户端（swbrain）的客户端凭据令牌：azp 是本客户端，Keycloak 用户名是 service-account-<客户端>（回执 2026-10-01）。
            # 只认 [identity].feed_clients 里列的用户名；人员令牌即使 azp 也是 swbrain，用户名对不上，进不来。
            who = str(claims.get("preferred_username") or "")
            if str(claims.get("azp") or "") != self.cfg.client_id or who not in self.cfg.feed_clients:
                raise IdentityError(f"不是登记的服务账号：azp={claims.get('azp')!r} 用户名={who!r}")
            p = self.portal.establish(token)
            if p.kind != "service":
                raise Forbidden(FEED, "这张令牌的主体持有人员职能，不当服务账号用")
        except IdentityError as ex:
            with self._bl:
                self._bearer[k] = (ex, time.monotonic())
            raise
        with self._bl:
            if len(self._bearer) > 1024:
                self._bearer.clear()
            self._bearer[k] = (p, time.monotonic())
        return p

    # ---- 委托：智能体（含 DeepSeek Harness 经 MCP 回调本服务）替发起对话的人做事
    def delegate(self, p: Principal, sid: str | None = None, ttl_s: float = 900, readonly: bool = False) -> str:
        """给这一次对话发一张短时凭据。MCP 桥用它回调 /api/action，服务端据此还原成发起人、按他的职能判。凭据只在内存里。
        带了会话号就回指那个会话：每次用都按会话现查（对话中途撤销了职能，智能体也立刻没有）；共享口令模式没有会话，用快照。"""
        tok = "dlg_" + secrets.token_urlsafe(32)
        with self._dl:
            now = time.monotonic()
            self._delegates = {k: v for k, v in self._delegates.items() if v[2] > now}
            self._delegates[_digest(tok)] = (sid, p, now + ttl_s, bool(readonly))
        return tok

    def from_delegate(self, tok: str | None, fresh: bool = False) -> Principal | None:
        if not tok or not tok.startswith("dlg_"):
            return None
        with self._dl:
            v = self._delegates.get(_digest(tok))
        if not v or v[2] <= time.monotonic():
            return None
        p = self.principal(v[0], fresh=fresh) if (self.sdip and v[0]) else v[1]
        return replace(p, readonly=True) if (p is not None and len(v) > 3 and v[3]) else p     # 只读凭据：本人能写也只放只读

    def revoke_delegate(self, tok: str | None):
        if tok:
            with self._dl:
                self._delegates.pop(_digest(tok), None)

    def readonly_token(self, ttl_s: float = 30 * 86400) -> str:
        """给 Harness 原生 Web UI 的共享只读凭据：那个入口分不出是谁在用（MCP 客户端 headers 是静态配置），
        所以发一张**只读**的凭据，服务端据此判成 readonly 主体——会写数据的动作一律拒，读动作按 VIEW 放行。
        有效期长（Web UI 是长驻进程），吊销 = 重启服务或改 mode。2026-10-02"""
        p = Principal(principal_id="harness-web", name="Harness Web UI（只读）", tenant_id=self.cfg.tenant_id,
                      functions={VIEW: {"grantDoc": "readonly-entry"}}, kind="user", readonly=True)
        return self.delegate(p, None, ttl_s=ttl_s, readonly=True)

    def describe(self) -> dict:
        c = self.cfg
        return {"mode": c.mode, "issuer": c.iss if c.sdip else None, "client_id": c.client_id if c.sdip else None, "tenant_id": c.tenant_id,
                "audience": c.audience if c.sdip else None, "sessions": len(self.sessions), "functions": FUNCTIONS}


def _refresh_deadline(body: dict) -> float | None:
    try:
        n = int(body.get("refresh_expires_in") or 0)
    except (TypeError, ValueError):
        return None
    return time.time() + n if n > 0 else None              # Keycloak 用 0 表示 refresh 不单独过期
