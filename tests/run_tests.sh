#!/usr/bin/env bash
# 运行 scutio_data 测试并生成人类可读 Markdown 报表。
#
# 用法（在仓库根或本目录）:
#   ./tests/run_tests.sh              # 离线（默认）
#   ./tests/run_tests.sh --live       # 真实通路：HTTP smoke
#   ./tests/run_tests.sh --live-http  # 仅 SCUTIO_LIVE HTTP/多源
#   ./tests/run_tests.sh --live-only  # 只跑 live case（需同时 --live）
#   ./tests/run_tests.sh --no-report  # 不写报表
#   ./tests/run_tests.sh -k split_code
#
# 报表默认: $SCUTIO_HOME/cache/test_reports/scutio_data_<时间戳>.md
#           以及 scutio_data_latest.md
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
SCRIPTS="$REPO_ROOT/skills/scutio/scripts"

SCUTIO_HOME="${SCUTIO_HOME:-$HOME/.scutio}"
REPORT_DIR="${SCUTIO_TEST_REPORT_DIR:-$SCUTIO_HOME/cache/test_reports}"
VENV_PY="${SCUTIO_PYTHON:-$SCUTIO_HOME/.venv/bin/python}"

LIVE_HTTP=0
LIVE_ONLY=0
REPORT=1
PYTEST_ARGS=()

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \?//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live) LIVE_HTTP=1; shift ;;
    --live-http) LIVE_HTTP=1; shift ;;
    --live-only) LIVE_ONLY=1; shift ;;
    --no-report) REPORT=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) PYTEST_ARGS+=("$1"); shift ;;
  esac
done

pick_python() {
  if [[ -x "$VENV_PY" ]]; then
    echo "$VENV_PY"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  echo "找不到 python（试过 SCUTIO_PYTHON=$VENV_PY）" >&2
  exit 1
}

if [[ ! -d "$SCRIPTS/scutio_data" ]]; then
  echo "找不到 scutio_data：期望 $SCRIPTS/scutio_data" >&2
  exit 1
fi

PY="$(pick_python)"
echo "python: $PY"
echo "scripts: $SCRIPTS"
echo "tests: $TESTS_DIR"

REQ_FILE="$TESTS_DIR/requirements.txt"
# 缺 pytest 或任一运行时依赖时，从 tests 专用 requirements 安装（含 -r 运行时）。
# 这里与 runtime requirements 保持完整对应，避免已有 venv 漏装新增依赖。
if ! "$PY" -c "import pytest, akshare, requests, pandas, lxml, xlrd, exchange_calendars, pypdf, tzdata" 2>/dev/null; then
  if [[ ! -f "$REQ_FILE" ]]; then
    echo "缺少 $REQ_FILE，且当前环境缺少测试或运行时依赖" >&2
    exit 1
  fi
  echo "安装测试依赖: $REQ_FILE"
  "$PY" -m pip install -q -r "$REQ_FILE"
fi

export PYTHONPATH="${SCRIPTS}${PYTHONPATH:+:$PYTHONPATH}"
export SCUTIO_TEST_REPORT_DIR="$REPORT_DIR"
if [[ "$REPORT" -eq 0 ]]; then
  export SCUTIO_TEST_REPORT=0
else
  export SCUTIO_TEST_REPORT=1
fi
if [[ "$LIVE_HTTP" -eq 1 ]]; then
  export SCUTIO_LIVE=1
  echo "SCUTIO_LIVE=1（HTTP / 多源真实通路）"
fi

if [[ "$REPORT" -eq 1 ]]; then
  mkdir -p "$REPORT_DIR"
fi
cd "$TESTS_DIR"
set +e
if [[ "$LIVE_ONLY" -eq 1 ]]; then
  echo "pytest live-only: test_live_smoke.py ${PYTEST_ARGS[*]:-}"
  "$PY" -m pytest \
    "data/test_live_smoke.py" \
    -q --tb=short \
    "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}"
else
  echo "pytest tests ${PYTEST_ARGS[*]:-}"
  "$PY" -m pytest . -q --tb=short "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}"
fi
code=$?
set -e

if [[ "$REPORT" -eq 1 && -f "$REPORT_DIR/scutio_data_latest.md" ]]; then
  echo ""
  echo "报表: $REPORT_DIR/scutio_data_latest.md"
fi
exit "$code"
