# SDIP 工单：排水智能体（swbrain）接入统一身份与职能授权

2026-10-01　排水智能体项目组　依据：《第三方应用接入指南 · 统一身份与租户》（2026-10-01 版）§5b、§9.1

## 一句话

排水智能体现在用一个共享口令（`[serve].access_key`）进控制台、另一个共享口令（`admin_key`）进管理端，**分不出是谁做的**：签发预警、关闭积水点、批准计算，记下的只是调用方自报的标签（`api:dsh`、`agent:hydro` 之类），不是人。
接入后改为每个人用 SDIP 账号登录；「谁能做什么」定义成排水智能体自己的职能，由平台登记、授予、留痕，在会话里返回。门户角色只给最低的 `SUP`。

---

## 〇、我方已就绪；平台侧现状（2026-10-01 实测，`python -m swbrain.identity_check`）

排水智能体这一侧已经实现完毕，开关是 `[identity].mode = "sdip"`。现在只等平台开通：

| 项 | 实测 |
|---|---|
| 签发源 `sdip-dmt` | 在线。发现文档里的 `issuer` 是 `http://10.0.0.47:8480/realms/sdip-dmt`（不是 REMS 配置说明里的 172.29.64.1）——我方按这个配 |
| JWKS | 可取 |
| 客户端 `swbrain` | **Keycloak 回 `Client not found`：还没建**，请按第一节开 |
| SDIP 门户 `http://127.0.0.1:8081` | 在线（未带令牌回 `SEM_UNAUTHENTICATED`） |

平台开通后，我方用同一个自检加 `--login` 走真实登录，逐项核对：用户令牌 `aud`、直打 SDIP 被拒、交换后带 `sdip_principal_id`、租户 2 可见、会话 `functions` 含 `SWBRAIN.*`。

## 一、系统

| 项 | 值 |
|---|---|
| 系统代号（职能前缀） | `SWBRAIN` |
| 用途 | 阿布扎比排水「会思考的排水系统」：积水点生命周期、三道防线、预警、应急调度、what-if |
| 部署 | DMT，租户 `2`，realm `sdip-dmt` |
| 服务 | Python 单进程，控制台、管理端与接口同一个端口 `8766`（`/`、`/admin.html`、`/api/*`） |
| 客户端类型 | 机密客户端，授权码流，服务端持密钥；直接授权关闭 |
| 服务账号 | **要一个**：`swb-feed`，只给外部源推送用（993 报案、承包商 check-in、桥梁台账、承包商边界推到 `/api/feed/*`），推送方是系统不是人。只授 `SWBRAIN.FEED`，不授其他职能、不能登录控制台 |
| 回调 | 精确路径，不用通配：`https://127.0.0.1:8766/auth/callback`、`https://<局域网地址>:8766/auth/callback`。局域网上走 HTTPS（授权码与会话 cookie 不能明文过网）；平台若暂不支持本机证书，请在回执里说明，我们先只登记 127.0.0.1 |
| 注销回跳 | `https://127.0.0.1:8766/`、`https://<局域网地址>:8766/`（精确） |
| 要不要代调 SDIP（令牌交换） | 要，只为读 `/api/portal/session` 的 `functions`。请开**标准令牌交换 V2**，挂 `sdip-semantic-aud`、`sdip-identity` 两条可选作用域；我方交换时传 `scope=openid sdip-semantic-aud sdip-identity`（不传 `audience`，照 REMS 实测） |
| 用户令牌受众 | 请给客户端配受众映射器，用户令牌 `aud` 含 `swbrain`（客户端 ID 建议就叫 `swbrain`） |

## 二、职能（由排水智能体定义）

| 职能代码 | 名称 | 持有人能做什么（对应系统里的动作） |
|---|---|---|
| `SWBRAIN.VIEW` | 查看与分析 | 看全部页面与报告；问智能体；切情景、调度试算、what-if、退水重演（都不落盘）；提交计算申请与重出交付申请（`hydro_submit`、`report_submit`，只是待批准的申请） |
| `SWBRAIN.FIELD` | 现场登记 | 登记现场观测（`record_observation`）、登记生命周期节点（`update_episode`，含推送 `/api/feed/episodes`）、关闭积水点（`close_point`） |
| `SWBRAIN.ALERT` | 签发预警 | 开启降雨事件（`open_event`，含推送 `/api/feed/events`——推进来的事件同样进事件清单，所以和 `open_event` 同一级）、签发预警提示（`issue_alert`） |
| `SWBRAIN.PLAN` | 工程建议 | 推进工程建议状态：批准、在建、建成、否决（`set_proposal_status`） |
| `SWBRAIN.ADMIN` | 系统管理 | 进管理端：改智能体配置；批准、驳回、暂停、取消计算任务；刷新路况（调 Google Routes，按次计费；动作 `traffic_status` 的 `refresh=true` 与接口 `GET /api/traffic?refresh=1` 两条路都算） |
| `SWBRAIN.FEED` | 外部源推送 | 只给服务账号：推送 `/api/feed/incidents`、`checkins`、`bridges`、`contractors`。不含 `events`、`episodes`（那两个分别要 ALERT、FIELD） |

- 除 `SWBRAIN.VIEW` 与 `SWBRAIN.FEED` 外，其余四项都要求先持有 `SWBRAIN.VIEW`。`SWBRAIN.FEED` 只授服务账号，不授人。
- **不登记互斥。** 「自己提交的计算申请不能自己批」由排水智能体逐条记录判定，平台不用管。
- 额度上限一律留空。

## 三、首批授予

依据文号 `SWBRAIN-AUTH-2026-01`，有效期至 `2026-12-31T23:59:59Z`，租户 `2`。账号都还没有，请平台新开；门户角色一律 `SUP`。

| 账号 | 用途 | 授予的职能 |
|---|---|---|
| `swb-uat-01` | 值班工程师 | `SWBRAIN.VIEW`、`SWBRAIN.FIELD`、`SWBRAIN.ALERT` |
| `swb-uat-02` | 运行室 / 现场 | `SWBRAIN.VIEW`、`SWBRAIN.FIELD` |
| `swb-uat-03` | 规划 / 资本计划 | `SWBRAIN.VIEW`、`SWBRAIN.PLAN` |
| `swb-uat-04` | 系统管理员 | `SWBRAIN.VIEW`、`SWBRAIN.ADMIN` |
| `swb-uat-05` | 只读（管理层、演示） | `SWBRAIN.VIEW` |
| `swb-feed`（服务账号） | 外部源推送 | `SWBRAIN.FEED` |

开号按指南 §3.3：临时口令、首次登录必须改、锚定真实 `sub`。**口令单独交给业务负责人，不写进回执。** 服务账号走客户端凭据，不开交互登录。

## 四、请回执

《SDIP工单_排水智能体接入统一身份_回执.md》，写明：

1. 签发源、JWKS、令牌地址（以当时的局域网地址为准）；
2. 客户端 ID；用户令牌的 `aud`；
3. 六项职能已登记；
4. 每个账号（含服务账号 `swb-feed`）的主体号、门户授权号、职能授权号；
5. 回调能否登记 HTTPS 局域网地址；不能的话先只登记 127.0.0.1；
6. 平台对外地址。

客户端密钥与服务账号凭据另行交付，不写进回执。

---

## 附：排水智能体这一侧要改的（不需要平台做）

> **2026-10-01 已全部实现，可选开启**：`[identity].mode = "sdip"`（缺省 `shared_key` 照旧用共享口令，不对接平台的机器不受影响）。
> 代码 `swbrain/identity.py`，测试 `tests/test_identity.py`（本地假签发源走完整授权码登录，真 RSA 签名）。等平台回执到了，填 `[identity]` 的
> `issuer` / `portal_base` / `tenant_id` / `public_base` 与环境变量 `SWBRAIN_SDIP_CLIENT_SECRET` 即可联调；尚未对真实平台跑过。

1. 登录：首页与 `/admin.html` 改走授权码流，回调 `/auth/callback`；会话 cookie 存服务端会话号，不存令牌；`access_key` 与 `admin_key` 在切换完成后停用。
2. 每个写动作前取一次会话（指南 §5b：撤销立即生效，动作前重新取），按上表判职能；`actor` 只从服务端会话取，记主体号与职能代码，**不认请求体里的 `actor`**。
   - 已先做（2026-10-01）：`/api/action` 的 `actor` 改为服务端定的 `api:<标签>`，调用方再也不能自称 `console`（原来 `Runtime.act` 对 `console` 豁免管理端停用检查，带上就能调已停用的动作）。
3. 管理端的审计（`out/agents/admin_audit.jsonl`）同样记主体号。
4. 智能体调本体动作时，要带上**发起对话的那个人**的身份，按他的职能判；不能因为是智能体调的就放行写动作。
   - 控制台里的多智能体在服务进程内调，把会话主体传下去即可。
   - DeepSeek Harness 经 MCP 调的那条路最费事：`mcp_server.py` 是独立进程，现在拿配置里的共享口令调 `/api/action`；切换后共享口令停用，它也没有用户会话。要么每次对话给它一个短时、只限这位用户与这次对话的凭据，要么改成进程内调用。
5. 「自己提交的申请不能自己批」：申请的 `requested_by` 现在只记 `{"agent": "hydro"}`，要加发起人的主体号，批准时比对。
6. `/api/feed/*` 按源判职能：`events` 要 ALERT、`episodes` 要 FIELD、其余要 FEED。
7. `GET /api/traffic?refresh=1` 也要 ADMIN（GET 也会触发计费请求）。
8. 签发源三项收成一处配置，地址会变（指南 §5c）。
