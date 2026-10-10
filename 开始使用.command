#!/bin/zsh
setopt PIPE_FAIL
farm_public_source="${0:A:h}"
export QQFARM_DATA_DIR="${QQFARM_DATA_DIR:-$HOME/Library/Application Support/QQFarmMonitor}"
QQFARM_DATA_DIR="${QQFARM_DATA_DIR:A}"
farm_public_log="$QQFARM_DATA_DIR/launch-public.log"

if ! mkdir -p -- "$QQFARM_DATA_DIR"; then
  print -u2 -- "无法创建用户数据目录：$QQFARM_DATA_DIR"
  [[ -t 0 ]] && read -r '?按回车关闭窗口。'
  exit 1
fi

farm_launch_public() {
  print 'QQ经典农场监控：各自登录微信后使用'
  date '+%Y-%m-%d %H:%M:%S'
  zsh "$farm_public_source/portable_bootstrap.sh" "$@"
}
farm_launch_public "$@" 2>&1 | tee -a "$farm_public_log"
farm_public_status=${pipestatus[1]}
print -r -- "退出状态：$farm_public_status；启动日志：$farm_public_log"
[[ -t 0 ]] && read -r '?按回车关闭窗口。'
exit "$farm_public_status"
