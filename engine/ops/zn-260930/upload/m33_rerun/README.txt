33 mm 重跑包（2026-10-10）：三台服务器都解压同一个包，放哪个目录都行（脚本用同目录找文件）。
  unzip m33_rerun.zip && cd m33_rerun && chmod +x *.sh
旧脚本（M32 版）会被覆盖；容器里的 reset 标记、结果、备份都不会动。
先 plan 看任务是不是 M33*，再 start。

服务器 2：
  TAGS="M33H1 M33H2" bash c1_15m_server2.sh plan
  TAGS="M33H1 M33H2" bash c1_15m_server2.sh start
  查进度：TAGS="M33H1 M33H2" bash c1_15m_server2.sh status

服务器 3（C1 和 Mussafah 两个调度器同时跑，各 4 槽）：
  TAGS="M33H24" SLOTS=4 bash c1_15m_server3.sh start
  TAGS="M33H1 M33H2 M33H24" SLOTS=4 bash ms_15m_server3.sh start
  查进度：TAGS="M33H24" bash c1_15m_server3.sh status ；TAGS="M33H1 M33H2 M33H24" bash ms_15m_server3.sh status

服务器 1（5 m；先杀旧调度器再 start，在跑的 61/96 mm 容器会被认领，不会重算）：
  docker exec -i swbrain python - <<'PY'
  import os, signal
  me = os.getpid()
  for p in os.listdir('/proc'):
      if p.isdigit() and int(p) != me:
          try: cmd = open(f'/proc/{p}/cmdline').read().replace('\0', ' ')
          except OSError: continue
          if 'c1_batch.py' in cmd and ' run' in cmd:
              print('kill', p); os.kill(int(p), signal.SIGTERM)
  PY
  bash c1_g5_server1.sh start        # 缺省 TAGS = M61H1G5 M96H1G5 M96H2G5 M33H1G5 M33H2G5
  查进度：bash c1_g5_server1.sh status

注意：暂停用 docker pause，不要 docker stop。
