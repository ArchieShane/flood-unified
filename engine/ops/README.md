# ops/ —— 推演任务交接（来自旧工作机 C:\freedo\zn-260930）

- `claude-memory/`：Claude 记忆快照（MEMORY.md 为索引）。新机器可复制到 `~/.claude/projects/<项目路径转义>/memory/` 继续沿用。
- `zn-260930/`：工作目录里的推演辅助代码（不含数据/镜像/大文件）：
  - `upload/<包名>/`：各批次推演上传包里的 runner、shell、案例 json（分工见记忆 c1-5m-servers、city-250m-*、adm-* 等）
  - `_build/`：生成上传包/计划表的脚本
  - `flood-viewer/`：NAS 成果展示前端（docker 部署）
  - `nas_request/`、`pond_sources_map/`、`catchment_extract/`、`c1_dem_report/`：辅助脚本与说明
- 核心脚本在仓库 `tools/`、`docker/anuga-swmm`、`.claude/skills/`；镜像用 `docker build -t anuga-swmm:1.0 docker/anuga-swmm` 重建。
- 未入库（需另行拷贝）：GDB/DEM 原始数据、计划表 xlsx、npz/shp 结果、上传包里 >1MB 的文件（如 upload/city_250m/citywide/node_runoff.json）、镜像 tar。
- 脚本均需 LF 换行（见记忆 ask-when-unsure）。
