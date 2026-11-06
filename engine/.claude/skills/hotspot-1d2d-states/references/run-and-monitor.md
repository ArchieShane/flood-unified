# 计算与监控

## 建模（以 tag `M61`、计算域文件 `out/units_from_models/c1_model_domain.geojson` 为例）
```bash
export SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
export PROJ_DATA=$(python -c "import rasterio,os;print(os.path.join(os.path.dirname(rasterio.__file__),'proj_data'))")
python tools/build_c1.py --rain 61 --tag M61 --domain out/units_from_models/c1_model_domain.geojson   # 设计暴雨加 --rain 61.34 --dur 24 --tag D24
python tools/build_c1_l2.py  --rain 61 --tag M61
python tools/build_c1_l2p.py --rain 61 --tag M61 --l2tag M61
# 塘面完全在二维域外的：pilot_C1_M61_L2P.json 里该塘 no_surface = true
MSYS_NO_PATHCONV=1 docker run --rm -v D:/swbrain_work/swmm:/work anuga-swmm:1.0 python /work/c2d/_chk_inp.py C1_M61_L1 C1_M61_L2 C1_M61_L2P
```

## 开跑（三态并行）
```bash
export MSYS_NO_PATHCONV=1
for s in L1 L2 L2P; do
  docker run -d --rm --name c1m_R61_$s -e OMP_NUM_THREADS=6 -e C2D_DEM_RAW=1 -e C2D_SAVE_UV=1 \
    -v D:/swbrain_work/swmm:/work anuga-swmm:1.0 \
    sh -c "python -u /work/c2d/couple2way_v2.py C1_M61 $s 15 6 60 > /work/c2d/logs/C1_M61_$s.log 2>&1"
done
```
参数：`<名> <态> <网格 m> <终止 h> <交换步 s>`；24 h 雨终止 28。

## 接力（后台链）
- `tools/chains/c1m_l2s3.sh`：等 `twoway_C1_M61_L2P.json` 出现且容器退出 → `c1_design_l2s3.py` → 读入检查 → 开跑 L2S3。
- `tools/chains/c1m_post.sh`：等 L2S3 → 全套后处理 → 交付导出。
- 写链时要处理「容器退出但没结果」「上游链已结束」两种终止，并且**只用本机有的命令**（Git Bash 没有 `pgrep`，用 `ps -ef | grep -v grep | grep -q …`）。
- 需要「A 完成后恢复 B」时同样写链（`c1_d24_resume.sh` 用 `docker unpause`）。

## 监控
- 日志每帧一行：`t= 1.25h 域内 … m³ 淹没 … ha 最深 … ↓1D→2D … ↑2D→1D … 雨 … 进塘 … [墙钟 s | 各段耗时]`。
- 看：`t=` 在走、没有 `Traceback`、`域内` 水量没有暴涨（发散的征兆是某帧域内突然多出上千万 m³）。
- 速度：前段无雨很快，雨峰后二维湿区大、最慢；按「最近一段每模拟小时的墙钟」外推完成时间，不要按开头的速度估。
- 暂停 / 恢复：`docker pause <容器>` / `docker unpause <容器>`；暂停中容器仍在 `docker ps` 里（状态 Paused），链会继续等。

## 完成判据
`twoway_C1_T_S.json` 与 `C1_T_S_2d.npz` 都在、容器已退出、日志末尾有「水量对账」三行。
