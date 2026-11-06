#!/bin/bash
# 并行运行全市一维（8 片，治理后数据，61mm/1h）
set -e
cd "/d/阿布扎比/排水智能体"

echo "=== 启动全市一维并行计算 (8 片) ==="
echo "开始时间: $(date)"

for i in {1..8}; do
    echo "启动片段 $i..."
    nohup swmm5 out/l2plus/swmm/City_L1_gov_61mm1h_p${i}.inp out/l2plus/swmm/City_L1_gov_61mm1h_p${i}.rpt > out/l2plus/swmm/City_L1_gov_61mm1h_p${i}.log 2>&1 &
done

echo ""
echo "✓ 8 个进程已启动"
echo "监控进度: tail -f out/l2plus/swmm/City_L1_gov_61mm1h_p*.log"
echo "查看进程: ps aux | grep swmm5"
