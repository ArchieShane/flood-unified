# 交换契约的 JSON Schema

这里是六个外部源与两个对外产物的正式模式（JSON Schema draft 2020-12），给 ADM 侧对接的人看、也给校验用。

| 文件 | 契约 | 谁产生 | 怎么给我们 |
|---|---|---|---|
| `incidents.schema.json` | `swbrain.incidents/1` | ADM 应急系统 / 993 | `POST /api/feed/incidents`，或放 `exchange/incidents.json`，或配 `[feeds.incidents].url` 让我们拉 |
| `checkins.schema.json` | `swbrain.checkins/1` | 承包商到场登记 | 同上，`/api/feed/checkins` |
| `bridges.schema.json` | `swbrain.bridges/1` | 桥梁限重 / 净高台账 | 同上，`/api/feed/bridges` |
| `contractors.schema.json` | `swbrain.contractors/1` | 承包商服务边界 | 同上，`/api/feed/contractors` |
| `events.schema.json` | `swbrain.events/1` | 历史 / 预报降雨事件 | 同上，`/api/feed/events` |
| `episodes.schema.json` | `swbrain.episodes/1` | 逐点积水记录（投诉 / 巡查 / 993 / 卫星） | 同上，`/api/feed/episodes`；也可用 `python -m swbrain.episodes_import` 从客户原始表转 |

**约定**：顶层是 `{schema, as_of, source, rows[]}`；`rows` 里每行的必填项见各 schema 的 `required`。我们的 `swbrain/feeds.py::validate()` 与这些 schema 同源（`python -m swbrain.schemagen --check` 比对），不合格的行会被原样退回并说明原因，不会硬塞。

示例数据在 `exchange/examples/`。坐标一律 WGS84 经纬度（`lon` 在前、`lat` 在后是常见错误来源，schema 里都标了范围）。
