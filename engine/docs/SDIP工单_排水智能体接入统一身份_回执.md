# 回执：排水智能体（swbrain）接入统一身份与职能授权

对《SDIP 工单：排水智能体（swbrain）接入统一身份与职能授权》的回执。2026-10-01，平台侧，部署 DMT，租户 `2`。

平台侧已全部开通。排水智能体可以填 `[identity]` 联调了。

---

## 一、地址（第四节第 1、6 条）

| 项 | 值 |
|---|---|
| 签发源 | `http://10.0.0.47:8480/realms/sdip-dmt` |
| JWKS | `http://10.0.0.47:8480/realms/sdip-dmt/protocol/openid-connect/certs` |
| 令牌地址 | `http://10.0.0.47:8480/realms/sdip-dmt/protocol/openid-connect/token` |
| 平台对外地址 | `http://10.0.0.47:8081`（本机也可用 `http://127.0.0.1:8081`） |

⚠️ 局域网地址会变（指南 §5c）。今天 11:41 刚从 `10.255.254.88` 换成 `10.0.0.47`。以 `.well-known/openid-configuration` 里的 `issuer` 为准。

## 二、客户端（第四节第 2、5 条）

| 项 | 值 |
|---|---|
| 客户端 ID | `swbrain` |
| 类型 | 机密客户端，授权码流，直接授权关闭，隐式流关闭 |
| 服务账号 | 已开（给 `swb-feed`，见第四节） |
| 标准令牌交换 V2 | 已开 |
| 可选作用域 | `sdip-semantic-aud`、`sdip-identity` |
| 用户令牌 `aud` | `["swbrain", "account"]`，不含 `sdip-semantic` |
| 回调 | `http://127.0.0.1:8766/auth/callback`、`http://10.0.0.47:8766/auth/callback`（精确） |
| 注销回跳 | `http://127.0.0.1:8766/`、`http://10.0.0.47:8766/`（精确） |
| 客户端密钥 | **另行交付，不在本文件** |

**回调是 http，不是工单写的 https。** 平台上其他应用（门户、REMS、DeerFlow）都是 http，签发源本身也是 http；你们 8766 现在也没配证书。10-01 先按工单登记成 https，登录走不通，已改成 http。
所以 `[identity].public_base` 请保持缺省的 `http://127.0.0.1:8766`，局域网上用 `http://10.0.0.47:8766`，`[serve]` 不用配 `tls_cert`／`tls_key`。
以后整套平台（签发源、门户、各业务应用）一起上 HTTPS 时，平台另行通知，回调一并改。

局域网地址变了，平台会自动把新地址的回调与注销回跳加进来（`swbrain` 已加入换地址的脚本 `deploy/开放局域网.py`），不用再提工单。你们要跟着改的是签发源那三项（指南 §5c）。

## 三、六项职能已登记（第四节第 3 条）

依据 `SWBRAIN-AUTH-2026-01`。

| 职能代码 | 名称 | 前提 |
|---|---|---|
| `SWBRAIN.VIEW` | 查看与分析 | — |
| `SWBRAIN.FIELD` | 现场登记 | `SWBRAIN.VIEW` |
| `SWBRAIN.ALERT` | 签发预警 | `SWBRAIN.VIEW` |
| `SWBRAIN.PLAN` | 工程建议 | `SWBRAIN.VIEW` |
| `SWBRAIN.ADMIN` | 系统管理 | `SWBRAIN.VIEW` |
| `SWBRAIN.FEED` | 外部源推送 | — |

没有登记互斥，按工单。

## 四、账号（第四节第 4 条）

依据 `SWBRAIN-AUTH-2026-01`，有效期都到 `2026-12-31T23:59:59Z`。门户授权一律是 `SUP`、`W1`、密级 `0`、辖区 `AE-AZ`。每个主体都锚定这个账号真实的 Keycloak `sub`。

| 账号 | 用途 | 主体号 | 门户授权号 | 职能（授权号） |
|---|---|---|---|---|
| `swb-uat-01` | 值班工程师 | 2031 | 52 | `VIEW`(12)、`FIELD`(13)、`ALERT`(14) |
| `swb-uat-02` | 运行室 / 现场 | 2032 | 53 | `VIEW`(15)、`FIELD`(16) |
| `swb-uat-03` | 规划 / 资本计划 | 2033 | 54 | `VIEW`(17)、`PLAN`(18) |
| `swb-uat-04` | 系统管理员 | 2034 | 55 | `VIEW`(19)、`ADMIN`(20) |
| `swb-uat-05` | 只读 | 2035 | 56 | `VIEW`(21) |
| `swb-feed`（服务账号，Keycloak 用户名 `service-account-swbrain`） | 外部源推送 | 2030 | 51 | `FEED`(22) |

- 五个人员账号都用临时口令，首次登录必须改。**临时口令已单独交给业务负责人**，不在本文件。
- `swb-feed` 不能交互登录。它用 `swbrain` 客户端的客户端凭据（`grant_type=client_credentials`）取令牌，再像人员令牌一样做一次交换。

## 五、平台已经实测的

服务账号整条路平台跑过了：

```
一 客户端凭据令牌   aud = ["swbrain","account"]，azp = swbrain
二 直接打平台       401 SEM_UNAUTHENTICATED          ← 隔离有效
三 交换后           aud 含 sdip-semantic
四 /api/portal/tenants   → [2]
五 /api/portal/session   → principalId 2030，role SUP，effective true，functions = ["SWBRAIN.FEED"]
```

注意第三步：交换后的令牌里**没有** `sdip_principal_id`。这是正常的，平台按（签发源、`sub`）查到主体（指南 §3.3 路 ②），REMS 也是这样。所以 `principalId` 请从 `/api/portal/session` 的 `subject.principalId` 取，不要从令牌里取。

人员账号的登录要本人来做，平台侧不代登。你们用 `python -m swbrain.identity_check --login` 逐项核对时，预期会话里的 `functions`：

| 账号 | 预期 `functions` |
|---|---|
| `swb-uat-01` | `SWBRAIN.ALERT`、`SWBRAIN.FIELD`、`SWBRAIN.VIEW` |
| `swb-uat-02` | `SWBRAIN.FIELD`、`SWBRAIN.VIEW` |
| `swb-uat-03` | `SWBRAIN.PLAN`、`SWBRAIN.VIEW` |
| `swb-uat-04` | `SWBRAIN.ADMIN`、`SWBRAIN.VIEW` |
| `swb-uat-05` | `SWBRAIN.VIEW` |

核对结果请写进《SDIP工单_排水智能体接入统一身份_核对.md》。
