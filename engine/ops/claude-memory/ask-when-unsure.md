---
name: ask-when-unsure
description: 用户要求：不确定的就先问，不要猜着做再返工（服务器脚本改一次他要倒腾好几下）
metadata:
  node_type: memory
  type: feedback
  originSessionId: f12630a7-5f53-4b79-a4a2-48390eb5014b
  modified: 2026-10-17T15:00:35.134Z
---

用户 2026-10-17 在“拷成果并改英文名”的脚本上连返工几轮（目标目录限制过宽、CRLF 换行、误把 shp_dat 当成要拷的“推演数据”）后明确要求：**不确定的就先问我**。

**Why:** 每更新一次服务器脚本/镜像，用户都要重新上传、载入、重跑，成本很高。
**How to apply:** 动手前凡是对象、范围、路径、口径有歧义（比如“16 份”指什么、“推演数据”是 result 还是 shp_dat）一律先用问题确认，再一次做对。给服务器的脚本在本机用假数据测完再交；在 Windows 上生成 .sh 必须是 LF 换行。见 [[server-run-delivery-checklist]]。
