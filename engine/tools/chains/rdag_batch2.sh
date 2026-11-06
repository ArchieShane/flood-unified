#!/usr/bin/env bash
# RD-A 治理数据版一二维 · 批次 2 = 正式算例（2026-10-28 18:5x，用户同意开算）
# 管网初始 25 % 有水（客户经验值，口径待客户 P-1～P-3），建筑高糙率 n 0.5（挖洞对比待定）
# A61D1 × L1F / L2F / L2PF / OPTA61PH2F；雨后 10 h（共 11 h）；4 个并行、每个 5 线程
# 模型由 tools/rda_gov_build.py --suffix F 建好；couple2way_v2.py / inlet_hyd.py 开跑前从仓库拷到 c2d/
W=D:/swbrain_work/swmm
REPO="D:/阿布扎比/排水智能体"
cp "$REPO/tools/couple2way_v2.py" "$REPO/tools/inlet_hyd.py" $W/c2d/
mkdir -p $W/c2d/logs
for st in L1F L2F L2PF OPTA61PH2F; do
  docker rm -f rdag_$st >/dev/null 2>&1
  docker run --rm -d --name rdag_$st -e OMP_NUM_THREADS=5 -e C2D_DEM_RAW=1 -e C2D_SAVE_UV=1 -v "$W:/work" anuga-swmm:1.0 \
    sh -c "python -u /work/c2d/couple2way_v2.py RDAG_A61D1 $st 15 11 60 > /work/c2d/logs/rdag_A61D1_$st.log 2>&1" >/dev/null
  echo "$(date +%T) 开始 $st"
  sleep 5
done
while docker ps --format '{{.Names}}' | grep -q '^rdag_'; do sleep 30; done
echo "$(date +%T) 全部结束"
for st in L1F L2F L2PF OPTA61PH2F; do
  echo "== $st"; tail -6 $W/c2d/logs/rdag_A61D1_$st.log
  grep "Continuity Error (%)" $W/c2d/RDAG_A61D1_${st}_2way.rpt | tail -1
done
