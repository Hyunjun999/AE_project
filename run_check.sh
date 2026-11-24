#!/usr/bin/env bash
# Run all backend checks in order. A failing step does not stop the rest.
#
#   bash run_checks.sh            no LLM tokens used (formula titles)
#   bash run_checks.sh --llm      draft titles with the LLM instead (uses tokens)
#   bash run_checks.sh --fresh    set agent.db aside first, so drafts are created again
#
# Everything printed is also saved to check_log.txt.

cd "$(dirname "$0")/backend" || exit 1

PYTHON="${PYTHON:-python}"
PRODUCT_NO="${PRODUCT_NO:-13733764452}"   # an existing product in the store
CATEGORY_KEYWORD="${CATEGORY_KEYWORD:-발톱}"
TAG_KEYWORD="${TAG_KEYWORD:-강아지 발톱깎이}"
LOG="../check_log.txt"

USE_LLM=0
FRESH=0
for arg in "$@"; do
  case "$arg" in
    --llm) USE_LLM=1 ;;
    --fresh) FRESH=1 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

: > "$LOG"
PASS=0
FAIL=0
RESULTS=()

run() {
  local name="$1"
  shift
  { echo; echo "=== $name ==="; echo "\$ $*"; } | tee -a "$LOG"
  "$@" 2>&1 | tee -a "$LOG"
  local code=${PIPESTATUS[0]}
  if [ "$code" -eq 0 ]; then
    PASS=$((PASS + 1))
    RESULTS+=("PASS  $name")
  else
    FAIL=$((FAIL + 1))
    RESULTS+=("FAIL  $name (exit code $code)")
  fi
}

if [ "$FRESH" -eq 1 ] && [ -f agent.db ]; then
  backup="agent.db.bak-$(date +%Y%m%d-%H%M%S)"
  mv agent.db "$backup"
  echo "Moved agent.db to $backup" | tee -a "$LOG"
fi

run "1. price floor examples" "$PYTHON" price.py
run "1b. price floor matches the plan (7630 and 10810)" "$PYTHON" -c \
  "from price import price_floor as f; assert f(6000) == 7630 and f(6000, 2500) == 10810, (f(6000), f(6000, 2500)); print('ok')"

if [ "$USE_LLM" -eq 1 ]; then
  run "2. drafts with LLM titles (uses tokens)" "$PYTHON" draft.py
else
  run "2. drafts with formula titles (no tokens)" "$PYTHON" draft.py --no-llm
fi

run "3. approval queue (list only)" "$PYTHON" review.py --list
run "4. products in the store" "$PYTHON" list_products.py
run "5. existing product JSON saved to exports/" "$PYTHON" explore_naver.py product "$PRODUCT_NO"
run "6. category search" "$PYTHON" explore_naver.py categories "$CATEGORY_KEYWORD"
run "7. order changes in the last 24 hours" "$PYTHON" explore_naver.py orders 24
# shellcheck disable=SC2086
run "8. recommended tags" "$PYTHON" explore_naver.py tags $TAG_KEYWORD
run "9. LLM token usage this week" "$PYTHON" llm.py
run "10. web server and pages import cleanly" "$PYTHON" -c \
  "import server; assert (server.FRONTEND / 'index.html').is_file(), server.FRONTEND; print('ok', server.FRONTEND)"

{
  echo
  echo "=============== SUMMARY ==============="
  for line in "${RESULTS[@]}"; do echo "$line"; done
  echo "passed: $PASS | failed: $FAIL | full output: check_log.txt"
} | tee -a "$LOG"

[ "$FAIL" -eq 0 ]
