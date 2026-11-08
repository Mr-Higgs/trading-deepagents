#!/usr/bin/env bash
# Phase 3 step 1: score every research-loop search space with identical optimizer settings, plus a
# default-space control run, so the research/opt_*.md reports are directly comparable.
#
#   scripts/phase3_score_spaces.sh data/research_runs/research-20261108T002255Z
#
# Settings are pinned here on purpose (same window/trials/seed/folds for every run). Each run writes
# research/opt_<PAIR>_<strategy>_<ts>.{md,json}; this script also records which space produced which
# report in $RUN/optimize_runs.tsv so the comparison step doesn't have to guess from timestamps.
set -euo pipefail
cd "$(dirname "$0")/.."

RUN=${1:?usage: $0 <research run dir>}
STRATEGY=${STRATEGY:-ichimoku_pullback}
PAIR=${PAIR:-AUD_USD}
START=${START:-2022-01-01}       # 14 rolling folds (12m train / 3m test) up to the 2026-07-01 holdout
TRIALS=${TRIALS:-200}
SEED=${SEED:-0}
JOBS=${JOBS:-6}
OUT=${OUT:-research}
LOGDIR="$RUN/optimize_logs"
TSV="$RUN/optimize_runs.tsv"
mkdir -p "$LOGDIR"
[ -f "$TSV" ] || printf 'space\tstatus\tstarted\tfinished\treport_md\treport_json\n' > "$TSV"

run_one() {  # $1 = label, $2 = --space arg ("" for default)
  local label=$1 space=$2 started finished log md js
  started=$(date -u +%FT%TZ)
  log="$LOGDIR/$label.log"
  echo "== $label  (start $started)  log: $log"
  set +e
  uv run --group optimize python -m optimize.run --pair "$PAIR" --strategy "$STRATEGY" --start "$START" \
      --trials "$TRIALS" --seed "$SEED" --jobs "$JOBS" --out "$OUT" ${space:+--space "$space"} > "$log" 2>&1
  local rc=$?
  set -e
  finished=$(date -u +%FT%TZ)
  md=$(grep -o "wrote [^ ]*\.md" "$log" | tail -1 | cut -d' ' -f2 || true)
  js=$(grep -o "and [^ ]*\.json" "$log" | tail -1 | cut -d' ' -f2 || true)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$label" "$([ $rc -eq 0 ] && echo ok || echo "fail($rc)")" "$started" "$finished" "$md" "$js" >> "$TSV"
  echo "   -> rc=$rc  $md"
}

run_one default ""
for f in "$RUN"/search_spaces/iter-*.json; do
  run_one "$(basename "$f" .json)" "$f"
done
echo; echo "all done:"; column -t -s$'\t' "$TSV"
