服务器 2 / 3 跑 C1 5 m（2026-10-12）。每台 32 核 / 64 GB：每个 5 m 态约 15–18 GB，防 OOM 缺省按保守值：SLOTS=2、THREADS=10、每个计算容器内存上限 MEM_GB=22（docker --memory，不设 swap，真超了只杀这一个态，其他不受影响，重跑 start 接着算）、新开态前要求可用内存 ≥ 26 GB、两次开态间隔 600 s、槽数×MEM_GB 超过本机内存 85% 拒绝启动。
先用 2 槽跑起来，开跑后看 docker stats 里每个计算容器的实际峰值内存；稳定在 16 GB 以下、且总可用内存还剩 15 GB 以上，再 SLOTS=3 MEM_GB=18 重新 start（10-08 服务器 2 四态并发 OOM 过，不要超过 3 槽）。
整场雨只在一台机器上跑（镜像版本不同，四个态不要跨机器混算）。

分工建议：
  服务器 2：TAGS="M33H1G5 M33H2G5"   （33 mm 短雨，各四态）
  服务器 3：TAGS="M33H24G5"           （33 mm / 24 h，四态，每态可能 3–4 天）
  服务器 1：不要再跑 M33*。注意旧版 c1_g5_server1.sh 的缺省 TAGS 带 M33H1G5 M33H2G5（已在 server1_update 里改掉），先看服务器 1 在跑的调度器带不带它们：
            docker exec swbrain grep "同时最多" /work/c2d/logs/c1_g5_batch.log | tail -1
          若带了，用新版 c1_g5_server1.sh 重新 start（已完成的跳过、在跑的容器会被认领），保持 M61H1G5 M96H1G5 M96H2G5，之后再加 M61H24G5 M96H24G5

上传：把本目录所有文件放同一个目录（如 ~/g5），然后
  bash c1_g5_small.sh check                      # 只读检查（要先给 TAGS）
  TAGS="M33H1G5 M33H2G5" bash c1_g5_small.sh start
  TAGS="M33H1G5 M33H2G5" bash c1_g5_small.sh status
  bash c1_g5_small.sh log
开跑前先 free -g / nproc / df -h /work 确认；/work 剩余不到 300 GB 会拒绝启动（MIN_DISK_GB=… 可改）。
算完后用 sync_to_nas.sh 同步 NAS，verify 后再 clean_synced.sh。
