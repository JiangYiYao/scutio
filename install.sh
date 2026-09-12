#!/usr/bin/env bash
# Install one Scutio skill; user data and Python live outside the skill directory.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd -P)"
SRC="$ROOT/skills/scutio"
SCUTIO_HOME="${SCUTIO_HOME:-$HOME/.scutio}"
CONFIG_DIR="${SCUTIO_CONFIG_DIR:-}"
DEST="${SCUTIO_SKILLS_DIR:-}"
VENV_DIR="${SCUTIO_VENV:-$SCUTIO_HOME/.venv}"
MODE=copy
WITH_VENV=0
RECREATE_VENV=0
BASE_PYTHON="${SCUTIO_BASE_PYTHON:-}"
NEW_VENV=0
VENV_BACKUP=""
STAGING=""

usage() {
  cat <<'USAGE'
用法: ./install.sh --dest DIR [选项]

  --copy           复制独立 skill（默认）
  --link           软链到当前仓库（开发用；需保留仓库）
  --with-venv      安装并验证 Python 依赖
  --python EXE     选择 Python 3.11+；默认寻找兼容解释器
  --venv-dir DIR   环境位置（默认 $SCUTIO_HOME/.venv）
  --recreate-venv  备份后重建环境，失败时恢复原环境；需同时 --with-venv
  --dest DIR       宿主扫描目录，也可用 SCUTIO_SKILLS_DIR 指定

示例:
  ./install.sh --dest "$HOME/.agents/skills" --with-venv
  ./install.sh --dest "$HOME/.claude/skills" --with-venv --python python3.12
USAGE
}

fail() { echo "$*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --link) MODE=link; shift ;;
    --copy) MODE=copy; shift ;;
    --with-venv) WITH_VENV=1; shift ;;
    --recreate-venv) RECREATE_VENV=1; shift ;;
    --dest|--python|--venv-dir)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$1 需要一个值"
      case "$1" in
        --dest) DEST="$2" ;;
        --python) BASE_PYTHON="$2" ;;
        --venv-dir) VENV_DIR="$2" ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知参数: $1（用 --help 查看用法）" ;;
  esac
done
[[ -n "$DEST" ]] || fail '请用 --dest 或 SCUTIO_SKILLS_DIR 指定技能目录'
[[ "$RECREATE_VENV" -eq 0 || "$WITH_VENV" -eq 1 ]] || fail '--recreate-venv 需同时指定 --with-venv'
[[ -f "$SRC/SKILL.md" && -f "$SRC/requirements.txt" ]] || fail "技能包不完整: $SRC"

# Resolve existing directory aliases without requiring Python for a file-only install.
# Nonexistent suffixes are normalized without creating user data during preflight.
absolute_directory() {
  local value="$1" current=/ part
  case "$value" in '~') value="$HOME" ;; '~/'*) value="$HOME/${value#\~/}" ;; esac
  case "$value" in /*) ;; *) value="$(pwd -P)/$value" ;; esac
  while [[ -n "$value" ]]; do
    part="${value%%/*}"
    if [[ "$value" == */* ]]; then value="${value#*/}"; else value=""; fi
    case "$part" in
      ''|.) continue ;;
      ..) current="${current%/*}"; current="${current:-/}" ;;
      *)
        current="${current%/}/$part"
        if [[ "${2:-physical}" == lexical ]]; then
          continue
        elif [[ -d "$current" ]]; then
          current="$(cd "$current" && pwd -P)" || fail "无法解析目录: $current"
        elif [[ -e "$current" || -L "$current" ]]; then
          fail "目录路径不是可解析的目录: $current"
        fi ;;
    esac
  done
  printf '%s\n' "$current"
}

overlapping_directories() {
  local left="${1%/}/" right="${2%/}/"
  case "$left" in "$right"*) return 0 ;; esac
  case "$right" in "$left"*) return 0 ;; esac
  return 1
}

if [[ "$WITH_VENV" -eq 1 && -L "$VENV_DIR" ]]; then
  fail "环境路径不能是软链，请用 --venv-dir 选择独立目录: $VENV_DIR"
fi
HOME_INPUT="$(absolute_directory "$SCUTIO_HOME" lexical)"
CONFIG_INPUT=""
if [[ -n "$CONFIG_DIR" ]]; then CONFIG_INPUT="$(absolute_directory "$CONFIG_DIR" lexical)"; fi
DST_INPUT="$(absolute_directory "$DEST" lexical)/scutio"
SCUTIO_HOME="$(absolute_directory "$SCUTIO_HOME")"
VENV_DIR="$(absolute_directory "$VENV_DIR")"
if [[ -n "$CONFIG_DIR" ]]; then CONFIG_DIR="$(absolute_directory "$CONFIG_DIR")"; fi
DEST="$(absolute_directory "$DEST")"
SRC="$(absolute_directory "$SRC")"
DST="$DEST/scutio"
RESOLVED_DST="$(absolute_directory "$DST")"
for data_root in "$SCUTIO_HOME" "$CONFIG_DIR" "$HOME_INPUT" "$CONFIG_INPUT"; do
  [[ -n "$data_root" ]] || continue
  for skill_root in "$SRC" "$DST" "$RESOLVED_DST" "$DST_INPUT"; do
    if overlapping_directories "$data_root" "$skill_root"; then
      fail "SCUTIO_HOME/SCUTIO_CONFIG_DIR 与技能目录不能互相包含，拒绝覆盖: $data_root ($skill_root)"
    fi
  done
done

supported_python() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}
select_python() {
  if [[ -n "$BASE_PYTHON" ]]; then
    command -v "$BASE_PYTHON" >/dev/null 2>&1 || fail "找不到解释器: $BASE_PYTHON"
    supported_python "$BASE_PYTHON" || fail "需要 Python 3.11+，所选解释器不兼容或无法启动: $BASE_PYTHON"
  else
    for candidate in python3.13 python3.12 python3.11 python3; do
      if command -v "$candidate" >/dev/null 2>&1 && supported_python "$candidate"; then
        BASE_PYTHON="$candidate"
        return
      fi
    done
    fail '未找到 Python 3.11+。安装兼容 Python 后，用 --python 指定；尚未安装 skill 或创建环境。'
  fi
}

# Check before creating a venv or replacing an installed skill.
if [[ "$WITH_VENV" -eq 1 ]]; then
  if [[ -e "$VENV_DIR" || -L "$VENV_DIR" ]]; then
    [[ ! -L "$VENV_DIR" && -f "$VENV_DIR/pyvenv.cfg" ]] || fail "环境路径已存在但不是独立 venv，请用 --venv-dir 选择新目录: $VENV_DIR"
    if [[ "$RECREATE_VENV" -eq 0 ]]; then
      supported_python "$VENV_DIR/bin/python" || fail '已有环境不兼容或无法启动。用 --recreate-venv --python python3.12 备份后重建，或用 --venv-dir 指定新目录。'
    fi
  fi
  if [[ ! -e "$VENV_DIR" || "$RECREATE_VENV" -eq 1 || -n "$BASE_PYTHON" ]]; then
    select_python
  fi
fi

case "${DEST%/}" in
  ''|/|"$HOME"|/tmp|/var|/usr|/opt|/etc) fail '安装目标过于宽泛，请指定宿主的 skills 子目录' ;;
esac
SKILLS_SOURCE="$(cd "$SRC/.." && pwd -P)"
case "$DEST/" in "$SKILLS_SOURCE/"*) fail '安装目标不能是仓库 skills 源目录或其子目录' ;; esac
if [[ ! -L "$DST" ]]; then
  case "$ROOT/" in "$DST/"*) fail "安装目标包含当前仓库，拒绝覆盖: $DST" ;; esac
  case "$SRC/" in "$DST/"*) fail "安装目标包含技能源码，拒绝覆盖: $DST" ;; esac
fi
if [[ "$WITH_VENV" -eq 1 ]]; then
  CHECK_PYTHON="${BASE_PYTHON:-$VENV_DIR/bin/python}"
  "$CHECK_PYTHON" -c '
import sys
from pathlib import Path
env, home, config, *skills = (Path(p).resolve() for p in sys.argv[1:])
if any(p.is_relative_to(env) for p in (home, config)) or any(env.is_relative_to(p) or p.is_relative_to(env) for p in skills):
    sys.exit("Python 环境与技能源码、安装目录不能互相包含；请选择独立 --venv-dir")
' "$VENV_DIR" "$SCUTIO_HOME" "${CONFIG_DIR:-$SCUTIO_HOME/config}" "$SRC" "$DST"
fi
for legacy in scutio-toolkit scutio-research scutio-scout; do
  if [[ -e "$DEST/$legacy" || -L "$DEST/$legacy" ]]; then
    fail "发现旧入口 $legacy。请先移出宿主扫描范围并保留本地修改，再安装单一 scutio。"
  fi
done

cleanup() {
  code=$?
  if [[ "$code" -ne 0 && "$NEW_VENV" -eq 1 ]]; then
    rm -rf -- "$VENV_DIR"
    if [[ -n "$VENV_BACKUP" ]]; then mv -- "$VENV_BACKUP" "$VENV_DIR"; fi
  fi
  if [[ -n "$STAGING" ]]; then
    if [[ ! -e "$DST" && ! -L "$DST" && ( -e "$STAGING/previous" || -L "$STAGING/previous" ) ]]; then
      mv -- "$STAGING/previous" "$DST"
    fi
    rm -rf -- "$STAGING"
  fi
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$WITH_VENV" -eq 1 ]]; then
  if [[ "$RECREATE_VENV" -eq 1 && -d "$VENV_DIR" ]]; then
    VENV_BACKUP="${VENV_DIR}.backup-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mv -- "$VENV_DIR" "$VENV_BACKUP"
  fi
  if [[ ! -e "$VENV_DIR" ]]; then
    NEW_VENV=1
    echo "创建 venv: $VENV_DIR ($BASE_PYTHON)"
    "$BASE_PYTHON" -m venv "$VENV_DIR"
  fi
  PY="$VENV_DIR/bin/python"
  if ! "$PY" -m pip install -r "$SRC/requirements.txt"; then
    fail '依赖安装失败，未替换 skill。请检查网络、代理、安装源与 Python 兼容性后重试。'
  fi
  "$PY" -B "$SRC/scripts/runtime_probe.py"
fi

# Stage the new files before touching the existing installation.
mkdir -p "$DEST" "$SCUTIO_HOME"
STAGING="$(mktemp -d "$DEST/.scutio-install.XXXXXX")"
if [[ "$MODE" == link ]]; then
  ln -s "$SRC" "$STAGING/scutio"
else
  cp -R "$SRC" "$STAGING/scutio"
fi
if [[ -e "$DST" || -L "$DST" ]]; then mv -- "$DST" "$STAGING/previous"; fi
mv -- "$STAGING/scutio" "$DST"
NEW_VENV=0
echo "已安装 ($MODE): $DST"
if [[ "$WITH_VENV" -eq 0 ]]; then
  echo '未安装或验证运行依赖；首次使用前用所选解释器运行 scripts/runtime_probe.py。'
fi
if [[ -n "$VENV_BACKUP" ]]; then echo "旧环境备份: $VENV_BACKUP（验证完成后可自行删除）"; fi
echo "运行前可在宿主进程中设置："
# Keep path bytes intact instead of relying on locale-sensitive Bash %q output.
print_export() {
  local value="$2"
  printf "  export %s='" "$1"
  while [[ "$value" == *"'"* ]]; do
    printf '%s' "${value%%\'*}" "'\\''"
    value="${value#*\'}"
  done
  printf "%s'\n" "$value"
}
print_export SCUTIO_HOME "$SCUTIO_HOME"
if [[ -n "$CONFIG_DIR" ]]; then print_export SCUTIO_CONFIG_DIR "$CONFIG_DIR"; fi
print_export SCUTIO_VENV "$VENV_DIR"
print_export SCUTIO_TOOLKIT_SCRIPTS "$DST/scripts"
if [[ -x "$VENV_DIR/bin/python" ]]; then print_export SCUTIO_PYTHON "$VENV_DIR/bin/python"; fi
echo '安装成功后在宿主中选择 Scutio；未发现时重启宿主。'
