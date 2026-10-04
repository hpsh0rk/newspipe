#!/bin/sh
# 实例化并安装 newspipe 常驻服务（macOS launchd）。
#
#   ./service/install.sh                  # 数据目录默认 ~/.newspipe
#   NEWSPIPE_NEWS_DIR=/data/news ./service/install.sh
#   NEWSPIPE_HOST_HOME=~/.hermes ./service/install.sh   # 复用宿主模型配置时
#
# 幂等：重复执行会先 bootout 再 bootstrap。密钥不需要在这里给（见 credentials.py）。
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.newspipe.service"
TEMPLATE="$REPO/service/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
NEWS_DIR="${NEWSPIPE_NEWS_DIR:-$HOME/.newspipe}"
HOST_HOME="${NEWSPIPE_HOST_HOME:-}"
LOG_DIR="${NEWSPIPE_LOG_DIR:-$HOME/Library/Logs}"

[ -x "$PYTHON" ] || { echo "找不到可执行的 python：$PYTHON" >&2
                      echo "先建虚拟环境：python3 -m venv .venv && .venv/bin/pip install -e '.[feishu]'" >&2
                      exit 2; }
[ -f "$TEMPLATE" ] || { echo "缺模板：$TEMPLATE" >&2; exit 2; }

# 护栏：容器在跑时不许再装 launchd —— 两个 runner 会各发一遍卡（重复投递），
# 而 plist 的 RunAtLoad+KeepAlive 会让这件事在**下次登录**才发生，非常难归因。
# 放在写任何文件之前：被拒时不该留下半装的 plist。
if command -v docker >/dev/null 2>&1 \
   && [ -n "$(docker ps --filter name=newspipe --filter status=running -q 2>/dev/null)" ]; then
  echo "拒绝安装：容器 newspipe 正在运行，它是当前的 runner。" >&2
  echo "  先停掉容器再装：docker compose down   （或保持容器，删掉这个 plist）" >&2
  echo "  确实要双跑（例如只在调试时）就设 NEWSPIPE_ALLOW_DOUBLE_RUNNER=1。" >&2
  [ "${NEWSPIPE_ALLOW_DOUBLE_RUNNER:-}" = "1" ] || exit 2
fi

mkdir -p "$NEWS_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"

# 占位符替换：用 | 作分隔符，避免路径里的 / 干扰
sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__REPO__|$REPO|g" \
    -e "s|__NEWS_DIR__|$NEWS_DIR|g" \
    -e "s|__HOST_HOME__|$HOST_HOME|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$TEMPLATE" > "$TARGET"

# 没配宿主就删掉那一行（空字符串会让后端认为「配了个空目录」）
if [ -z "$HOST_HOME" ]; then
  python3 - "$TARGET" <<'PY'
import sys, re
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
t = re.sub(r"\s*<key>NEWSPIPE_HOST_HOME</key>\s*\n\s*<string></string>", "", t)
open(p, "w", encoding="utf-8").write(t)
PY
fi

plutil -lint "$TARGET" >/dev/null || { echo "生成的 plist 不合法：$TARGET" >&2; exit 1; }

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "已安装并启动：$LABEL"
echo "  数据目录：$NEWS_DIR"
echo "  日志：    $LOG_DIR/newspipe.log"
echo "  查状态：  launchctl print gui/$(id -u)/$LABEL | grep -E 'state|pid'"
