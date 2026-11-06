# 261101：按本机主机名判断是第几台服务器（被 g261101-here.sh / g261101-monitor.sh 引用，不单独执行）。
#   AZPDW006aim01 → 1    AZPDW006uca03 → 2    AZPDW006uca06 → 3    其它主机名认不出（输出空）
g261101_detect() {
  case "$(hostname -s 2>/dev/null | tr 'A-Z' 'a-z')" in
    *aim01) echo 1;; *uca03) echo 2;; *uca06) echo 3;; *) echo "";;
  esac
}
# 服务器号校验：给了号就必须和本机主机名一致（防止在 3 上敲成 1）；没给就用主机名；认不出主机名又没给号就报错。
# 用法：g261101_resolve "<参数里的号或空>" → 输出最终号；错误信息写 stderr 并返回 1。
g261101_resolve() {
  local given="$1" det; det="$(g261101_detect)"
  if [ -n "$given" ] && [ -n "$det" ] && [ "$given" != "$det" ] && [ "${G261101_FORCE_N:-}" != 1 ]; then
    echo "✗ 你写的服务器号是 $given，但这台机器（$(hostname -s)）是服务器 $det。号可以不写，脚本会按主机名自己判断；确实要覆盖请设 G261101_FORCE_N=1。" >&2; return 1
  fi
  if [ -n "$given" ]; then echo "$given"; elif [ -n "$det" ]; then echo "$det"
  else echo "✗ 认不出这台机器（主机名 $(hostname -s)），请在命令里写服务器号 1 / 2 / 3。" >&2; return 1; fi
}
