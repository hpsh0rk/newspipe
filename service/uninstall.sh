#!/bin/sh
# 卸载 launchd 常驻服务（切到容器时必须跑一次，否则下次登录会双跑）。
#
#   ./service/uninstall.sh
#
# 只做两件事：bootout + 把 plist 移出加载路径。plist 不删，改名留档（`*.disabled-<日期>`），
# 想切回 launchd 时 `mv` 回去再 bootstrap 即可，或直接重跑 install.sh。
set -eu

LABEL="com.newspipe.service"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$TARGET" ]; then
  echo "没找到 plist：$TARGET（可能已卸载）"
else
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  mv "$TARGET" "$TARGET.disabled-$STAMP"
  echo "已停止并移出加载路径：$TARGET.disabled-$STAMP"
fi

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
  echo "⚠️  launchd 仍持有 $LABEL，请手动检查：launchctl list | grep newspipe" >&2
  exit 1
fi

echo "launchd 侧已清空。若容器在跑，它就是唯一 runner："
echo "  docker compose ps"
