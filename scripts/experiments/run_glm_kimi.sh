#!/bin/bash
#
# Run the full benchmark split for GLM 5.1 and Kimi K2.6.
# One pare benchmark run invocation per (observe, execute) model pair.
#
# Mirrors run_full_benchmark.sh exactly (same experiment name, same flags),
# so trace/result directories live alongside the existing 7 models under
# the same paper_benchmark_full_... parent. The run command is additive:
# each config writes only to its own obs_<alias>_exec_<alias>_... subdir,
# so this script will not touch traces from the original 7 models.
#
# Note: `set -e` is intentionally not used so that a single failed run
# (e.g. transient probe/API failure for one model) does not abort the
# remaining configs. A summary at the end reports how many configs failed.
#
# Usage:
#   ./scripts/experiments/run_fireworks_benchmark.sh
#

STARTED_AT=$(date +"%Y-%m-%d %H:%M:%S")
echo "========================================"
echo "Fireworks benchmark run"
echo "Started at: $STARTED_AT"
echo "========================================"
echo ""

# deepseek-v4-pro is intentionally omitted: serverless serving on Fireworks
# was not responding at the time of this run (TCP connect succeeded but no
# response bytes within 600s). Re-add to this array once Fireworks confirms
# serverless availability for the slug.
MODELS=(kimi-k2.6 glm-5.1)
TOTAL_CONFIGS=${#MODELS[@]}

CURRENT=0
FAILED=0
FAILED_CONFIGS=()

for MODEL in "${MODELS[@]}"; do
  CURRENT=$((CURRENT + 1))
  echo ""
  echo "========================================"
  echo "[$CURRENT/$TOTAL_CONFIGS] split=full, user=gpt-5-mini, observe=$MODEL, execute=$MODEL"
  echo "========================================"
  if ! uv run pare benchmark run \
      --split full \
      --observe-model "$MODEL" --execute-model "$MODEL" \
      --user-model gpt-5-mini --max-turns 10 -omi 5 -emi 10 -umi 1 \
      --runs 4 -c 6 --executor-type thread \
      --experiment-name paper_benchmark --export --output-dir ./traces --log-level ERROR; then
    FAILED=$((FAILED + 1))
    FAILED_CONFIGS+=("$MODEL")
    echo "WARN: config failed: $MODEL" >&2
  fi
done

FINISHED_AT=$(date +"%Y-%m-%d %H:%M:%S")
echo ""
echo "========================================"
echo "Fireworks benchmark complete"
echo "Started:  $STARTED_AT"
echo "Finished: $FINISHED_AT"
echo "Configs:  $((CURRENT - FAILED))/$TOTAL_CONFIGS succeeded, $FAILED failed"
if (( FAILED > 0 )); then
  echo "Failed configs:"
  for CFG in "${FAILED_CONFIGS[@]}"; do
    echo "  - $CFG"
  done
fi
echo "========================================"
