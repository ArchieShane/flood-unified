# 核对：排水智能体（swbrain）接入统一身份

对《回执：排水智能体接入统一身份与职能授权》（2026-10-01）的核对。排水智能体项目组。

**进度：全部核对通过。** 平台侧、服务账号、五个人员账号的会话，以及职能在运行中的服务里实际生效（第六节），都与回执一致。回调按平台更新后的回执用 http（平台整体以后再上 HTTPS），见第五节。

---

## 一、平台侧（不要账号）— 已通过

2026-10-01，`python -m swbrain.identity_check`：

| 项 | 结果 |
|---|---|
| 签发源 `http://10.0.0.47:8480/realms/sdip-dmt` 可达 | ✓ |
| 发现文档里的 `issuer` 与我方配置一致 | ✓ |
| JWKS 可取 | ✓ |
| 客户端 `swbrain` 已登记 | ✓（开通前是 `Client not found`） |
| 回调 `https://127.0.0.1:8766/auth/callback` 被接受 | ✓ |
| 门户 `http://127.0.0.1:8081` 在线（未带令牌回 `SEM_UNAUTHENTICATED`） | ✓ |

## 二、按回执改的（已改，已测）

| 回执 | 我方改动 |
|---|---|
| 交换后令牌**不带** `sdip_principal_id`，主体号从会话 `subject.principalId` 取（路 ②） | 本来两条路都认；自检第 3 步不再要求令牌里有主体号，第 5 步改为核对会话给出的主体号；补了按回执形状的测试 |
| 回调登记的是 **https** | 服务支持 HTTPS（`[serve].tls_cert` / `tls_key`）；本机自签 `python -m swbrain.tlscert`，证书含 `127.0.0.1`、`10.0.0.47`。`public_base` 是 https 却没配证书时，服务启动即报错 |
| 服务账号 `swb-feed` = `swbrain` 客户端的客户端凭据，用户名 `service-account-swbrain` | 推外部源只认 `azp = swbrain` 且用户名在 `[identity].feed_clients`（缺省 `service-account-swbrain`）的令牌；人员令牌不能冒充服务账号 |
| 签发源地址会变 | 只改 `[identity].issuer` 一处（JWKS、令牌地址由它推出）；`identity_check` 会报出「`iss` 与配置不一致」并给出新地址 |

## 三、还要核对的（等客户端密钥）

| # | 项 | 怎么核 | 结果 |
|---|---|---|---|
| 1 | 五个人员账号登录后会话里的 `functions` 与回执第五节一致 | 每个账号 `python -m swbrain.identity_check --login` | **✓ 2026-10-01**，见第五节 |
| 2 | 用户令牌 `aud = ["swbrain","account"]`、直打平台被拒、交换后含 `sdip-semantic`、租户 2 可见 | 同上，自检 1–4 步 | **✓ 2026-10-01**，五个账号都过 |
| 3 | 服务账号整条路（客户端凭据 → 交换 → 会话 `functions = ["SWBRAIN.FEED"]`、主体号 2030） | 我方用客户端凭据取一次令牌重走 | **✓ 2026-10-01**，见第四节 |
| 4 | 职能实际生效：`swb-uat-02` 不能签发预警、`swb-uat-05` 不能登记观测、只有 `swb-uat-04` 进得了管理端、`swb-uat-04` 不能批自己提交的计算申请 | 起服务后逐个账号操作 | **✓ 2026-10-01**，见第六节 |

## 四、服务账号实测（2026-10-01）

我方用 `swbrain` 客户端凭据取令牌，和平台第五节逐条对得上：

```
一 客户端凭据令牌   200；aud = ["swbrain","account"]，azp = swbrain，用户名 service-account-swbrain
二 直接打平台       401 SEM_UNAUTHENTICATED                       ← 隔离有效
三 交换后           aud = ["swbrain","sdip-semantic","account"]；令牌里无 sdip_principal_id（路 ②，与回执一致）
四 /api/portal/tenants   200，tenantId 2（Abu Dhabi Department of Municipalities and Transport）
五 /api/portal/session   subject.principalId 2030，role SUP，effective true，functions = ["SWBRAIN.FEED"]
```

再经本系统的服务端鉴权走一遍（真令牌、真平台，feed 处理函数换成只回显身份，不写数据）：

| 请求 | 结果 |
|---|---|
| 推 `/api/feed/incidents`（要 FEED） | 200，记为 `sdip:2030` |
| 推 `/api/feed/events`（要 ALERT） | 403「要职能 SWBRAIN.ALERT」 |
| 打开控制台接口 `/api/agents` | 401：服务账号不能用控制台 |

## 五、人员账号实测（2026-10-01，本人在浏览器里登录）

每个账号 `python -m swbrain.identity_check --login --user <账号>`，自检 1–5 步全部 ✓：

| 账号 | 主体号 | 会话 `functions`（实测） | 与回执第五节 |
|---|---|---|---|
| `swb-uat-01` | 2031 | `ALERT`、`FIELD`、`VIEW` | 一致 |
| `swb-uat-02` | 2032 | `FIELD`、`VIEW` | 一致 |
| `swb-uat-03` | 2033 | `PLAN`、`VIEW` | 一致 |
| `swb-uat-04` | 2034 | `ADMIN`、`VIEW` | 一致 |
| `swb-uat-05` | 2035 | `VIEW` | 一致 |

五个账号共同的：用户令牌 `aud = ["swbrain","account"]`；直打平台 401；交换后 `aud` 含 `sdip-semantic`、令牌里无主体号（路 ②）；租户 2 可见；会话给出的主体号与回执第四节一致。

**一处要平台确认：回调协议被改了。** 核对 `swb-uat-01` 时（约 19:55）平台还接受 `https://127.0.0.1:8766/auth/callback`；核对 `swb-uat-02` 时（约 20:20）它回 `Invalid parameter: redirect_uri`。逐条探测，现在接受的是：

| 回调 | 现在 |
|---|---|
| `http://127.0.0.1:8766/auth/callback` | 接受 |
| `http://10.0.0.47:8766/auth/callback` | 接受 |
| `https://127.0.0.1:8766/auth/callback` | **拒** |
| `https://10.0.0.47:8766/auth/callback` | **拒** |

平台随后更新了回执第二节：这是有意改的——平台上其他应用和签发源本身都是 http，整套平台以后一起上 HTTPS，回调届时一并改。我方照此：`public_base = http://127.0.0.1:8766`，不配证书。

我方的处理：本机用时服务只监听 127.0.0.1（`public_base` 是回环地址时自动如此），回环流量不出本机。**要在局域网上用 `http://10.0.0.47:8766` 时请知悉**：授权码与会话 cookie 会明文过局域网，与平台其他 http 应用同一风险等级。平台上 HTTPS 时，我方填 `[serve].tls_cert` / `tls_key`、把 `public_base` 改成 https 即可，代码已支持。

## 六、职能在运行中的服务里实际生效（2026-10-01 20:57–21:00）

服务以 `[identity].mode = "sdip"` 运行，共享口令一律不认（带口令直接调接口回 401）。五个账号依次在浏览器里登录 `http://127.0.0.1:8766/whoami.html`，页面按当前登录人现查平台职能，逐个判 9 个会写数据的动作；结果记 `out/identity_uat.jsonl`，身份以服务端认定的主体号为准（`server_actor`），不信页面自报。

| 动作（要的职能） | 01 值班 | 02 现场 | 03 规划 | 04 管理员 | 05 只读 |
|---|---|---|---|---|---|
| 登记观测 / 生命周期 / 关闭积水点（FIELD） | 能 | 能 | 不能 | 不能 | 不能 |
| 开启降雨事件 / 签发预警（ALERT） | 能 | **不能** | 不能 | 不能 | 不能 |
| 推进工程建议状态（PLAN） | 不能 | 不能 | 能 | 不能 | 不能 |
| 提交计算 / 重出交付申请（VIEW） | 能 | 能 | 能 | 能 | 能 |
| 刷新路况（ADMIN，按次计费） | 不能 | 不能 | 不能 | 能 | 不能 |
| 进管理端 `/api/admin/jobs` | 403 | 403 | 403 | **200** | 403 |

每一格都与回执第四节授予的职能一致：`swb-uat-02` 不能签发预警，`swb-uat-05` 不能登记观测，只有 `swb-uat-04` 进得了管理端。

**自己提交的计算申请不能自己批**（`swb-uat-04`）：

```
20:59:26  swb-uat-04 提交测试申请 7eec2b7a3db6（prep · M61，待批准，不起计算）
          动作日志 actor = sdip:2034/api:anon；申请 requested_by.principal = sdip:2034
20:59:26  swb-uat-04 批准 7eec2b7a3db6 → 400「自己提交的申请不能自己批，请另一位管理员批准」
20:59:26  swb-uat-04 驳回 7eec2b7a3db6（清理测试申请）→ 申请历史 by = sdip:2034；管理审计 who = sdip:2034
```

管理审计 `out/agents/admin_audit.jsonl` 里五次登录都记了主体号（`login_sdip`，2031–2035）。

核对中发现并已修的两处（我方）：`feed_clients` 原来写的是 `swb-feed`，实际的 Keycloak 用户名是 `service-account-swbrain`，已改；自检脚本没认 `/api/portal/tenants` 的实际返回形状 `{"tenants": [{"tenantId": …}]}`，已改。
