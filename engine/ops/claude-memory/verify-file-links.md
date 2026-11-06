---
name: verify-file-links
description: "回复里给的文件链接必须先确认文件存在，路径从工作目录起写完整；否则界面弹 \"Couldn't show this in Explorer\""
metadata:
  node_type: memory
  type: feedback
  originSessionId: a20e6b1a-e272-4db0-8100-9953574f6a27
  modified: 2026-10-20T12:58:49.926Z
---

用户 10-20 截图指出：点我回复里的文件路径弹出 “Couldn’t show this in Explorer. It may have been deleted or moved.”，要求以后一定避免。

**Why:** 链接指向不存在 / 写错层级的路径（工作目录在会话中会变，相对路径容易错位；`__pycache__` 之类被拦删的目录也不要当成可点链接）。
**How to apply:** 回复里要引用文件时，先用 ls 确认存在；用 markdown 链接，路径从当前主要工作目录（C:\freedo\zn-260930）起写完整，如 [adm100_run.py](upload/adm_100m/adm100_run.py)；不确定是否存在、或是服务器上的路径，就写成普通代码文本，不做链接。
