#!/usr/bin/env bash
# RD-A 治理数据版一二维 · 批次 1 + 敏感性（2026-10-28，用户同意；设计见 docs/RD-A_一二维重算_设计与参数登记_20261028.md）
# A61D1 × L1 / L2 / L2P / OPTA61PH2，敏感性 L1B（交换按一维井盖）、L1I（绿地 10 mm/h 下渗）；雨后 10 h（共 11 h）；4 个并行、每个 5 线程
# 模型先由 tools/rda_gov_build.py 建好；couple2way_v2.py / inlet_hyd.py 开跑前从仓库拷到 c2d/
W=D:/swbrain_work/swmm
REPO="D:/阿布扎比/排水智能体"
cp "$REPO/tools/couple2way_v2.py" "$REPO/tools/inlet_hyd.py" $W/c2d/
mkdir -p $W/c2d/logs
PAR=4
for st in L1 L2 L2P OPTA61PH2 L1B L1I; do
  while [ "$(docker ps --format '{{.Names}}' | grep -c '^rdag_')" -ge $PAR ]; do sleep 30; done
  docker rm -f rdag_$st >/dev/null 2>&1
  docker run --rm -d --name rdag_$st -e OMP_NUM_THREADS=5 -e C2D_DEM_RAW=1 -e C2D_SAVE_UV=1 -v "$W:/work" anuga-swmm:1.0 \
    sh -c "python -u /work/c2d/couple2way_v2.py RDAG_A61D1 $st 15 11 60 > /work/c2d/logs/rdag_A61D1_$st.log 2>&1" >/dev/null
  echo "$(date +%T) 开始 $st"
  sleep 5
done
while docker ps --format '{{.Names}}' | grep -q '^rdag_'; do sleep 30; done
echo "$(date +%T) 全部结束"
for st in L1 L2 L2P OPTA61PH2 L1B L1I; do
  echo "== $st"; tail -6 $W/c2d/logs/rdag_A61D1_$st.log
  grep "Continuity Error (%)" $W/c2d/RDAG_A61D1_${st}_2way.rpt | tail -1
done
