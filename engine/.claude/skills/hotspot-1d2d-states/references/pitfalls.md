# 常见坑（都真踩过）

| 现象 | 原因 | 怎么办 |
|---|---|---|
| SWMM 打不开 .inp / 报路径错 | SWMM 不认中文路径 | 模型放 `SWBRAIN_SWMM_WORK`（纯英文路径） |
| docker 挂载的路径变成 `C:/Program Files/Git/work/...` | Git Bash 自动改写以 `/` 开头的参数 | 命令前加 `MSYS_NO_PATHCONV=1` |
| heredoc 里的 Python 补丁把 `\n`、`\d` 变成真换行 / 警告 | bash heredoc + Python 字符串两层转义 | 多行补丁用编辑工具或先写成 .py 文件再跑 |
| `CRSError` / PROJ 版本冲突 | 系统里有两套 PROJ | `export PROJ_DATA=<rasterio 的 proj_data>` |
| 后台链停了（TaskStop）进程还在 | 子进程成了孤儿 | `ps -ef` 查、手动结束；**不要编辑正在运行的 bash 脚本** |
| L2+suggested 反而让别处变深 | v1 土堤挡水、v2 不看塘库容 | 用 v3；每次都跑 `c1_l2s_side_effects.py` |
| 找不到客户的「正确汇水区」 | 按截图里的字段结构去搜，漏了字段结构不同的模型 zip | 所有客户包都翻，**以水力模型子汇水区为准**；手描的只作参考 |
| 描图 / 拼图后出现空洞、重叠 | 各单元独立描、边界不共享 | 重叠按优先级切、空洞分给共用边界最长的单元、合并面只取外轮廓 |
| DeepSeek Harness 提示 authentication required | Web UI 地址里的 token 是一次性的，被用掉了 | 重启 Harness 拿新地址；**别用 curl 去试那个地址** |
| 智能体用不上新加的本体动作 | 控制台服务进程还是旧代码 | 重启 `python -m swbrain.serve`（要用户同意） |
| Shapefile 字段名乱码 / 读不回来 | 中文字段名被截在 10 字节中间 | Shapefile 用英文字段名 |
| 设计暴雨下 L2 移动泵启停几千次 | 从检查井抽水，井蓄水小 | 正常，抽水量本身没问题；报告里说明 |
| 预计完成时间一再推后 | 按开头（无雨段）速度估 | 按雨峰后的速度估；同时跑的模型多就再放宽 |
| 结论写反 / 夸大 | 没核算就解释 | 先算来源再写；写不准就写「推断」 |
