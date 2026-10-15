#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# compare_models.sh — Run the committed sample drawings through one or
# more models and store results per model in separate subdirectories.
#
# Usage:
#   ./scripts/compare_models.sh                          # default model
#   ./scripts/compare_models.sh -m "qwen3.6-35b"        # specific orchestrator
#   ./scripts/compare_models.sh -m "qwen3.6-35b" -v "internvl3-78b-instruct"
#   ./scripts/compare_models.sh -a "http://localhost:1234/v1" -m "gemma3-27b"
#   ./scripts/compare_models.sh -o results/comparison    # custom output root
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Drawing set: the committed samples/ sheets ─────────────────────
# Both are vector furniture elevations (zabudowy meblowe) in cm: one
# cabinet-per-view sheet and one multi-view sheet. Add more drawings
# from the git-ignored sample-data/<PROJECT>/ locally if needed.
DATA_DIR="samples"
DRAWINGS=(
  # 1) Cabinet elevations, one cabinet per view
  "${DATA_DIR}/GLADIOGROUP_P02_PW-ZM05_12.pdf"
  # 2) Multi-view sheet: desks, cladding, reception
  "${DATA_DIR}/06_A_METROPILITAN_NRF-zabudowy meblowe_widoki.pdf"
)

# ── Defaults ───────────────────────────────────────────────────────
ORCHESTRATOR=""
VISION_MODEL=""
API_BASE=""
OUTPUT_ROOT="results/model_comparison"
CONFIG="config/default.yaml"
VERBOSE=0

# ── Parse args ─────────────────────────────────────────────────────
usage() {
  echo "Usage: $0 [-m ORCHESTRATOR] [-v VISION] [-a API_BASE] [-o OUTPUT_DIR] [-c CONFIG] [--verbose]"
  echo ""
  echo "Options:"
  echo "  -m, --orchestrator MODEL   Orchestrator model (default: from config)"
  echo "  -v, --vision MODEL         Vision model (default: from config)"
  echo "  -a, --api-base URL         API base URL (default: from config)"
  echo "  -o, --output DIR           Output root directory (default: results/model_comparison)"
  echo "  -c, --config PATH          Config YAML path (default: config/default.yaml)"
  echo "      --verbose              Enable verbose logging"
  echo ""
  echo "Results are stored in: <OUTPUT_ROOT>/<model_name>/"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    -m|--orchestrator) ORCHESTRATOR="$2"; shift 2 ;;
    -v|--vision)       VISION_MODEL="$2"; shift 2 ;;
    -a|--api-base)     API_BASE="$2"; shift 2 ;;
    -o|--output)       OUTPUT_ROOT="$2"; shift 2 ;;
    -c|--config)       CONFIG="$2"; shift 2 ;;
    --verbose)         VERBOSE=1; shift ;;
    -h|--help)         usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# ── Build CLI flags ────────────────────────────────────────────────
CLI_FLAGS=(--config "$CONFIG")
[[ -n "$ORCHESTRATOR" ]] && CLI_FLAGS+=(--orchestrator "$ORCHESTRATOR")
[[ -n "$VISION_MODEL" ]] && CLI_FLAGS+=(--vision "$VISION_MODEL")
[[ -n "$API_BASE" ]]     && CLI_FLAGS+=(--api-base "$API_BASE")
(( VERBOSE ))            && CLI_FLAGS+=(-v)

# Derive a label for the output subdirectory
if [[ -n "$ORCHESTRATOR" ]]; then
  MODEL_LABEL="$ORCHESTRATOR"
else
  # Extract default model from config YAML
  MODEL_LABEL=$(grep 'orchestrator:' "$CONFIG" | head -1 | awk '{print $2}' | tr -d '"')
fi
MODEL_LABEL=$(echo "$MODEL_LABEL" | tr '/' '_')

OUTPUT_DIR="${OUTPUT_ROOT}/${MODEL_LABEL}"
mkdir -p "$OUTPUT_DIR"

# ── Run ────────────────────────────────────────────────────────────
PYTHON="uv run python"
TOTAL=${#DRAWINGS[@]}
OK=0
FAIL=0

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Model Comparison — ${TOTAL} drawings"
echo "  Orchestrator: ${ORCHESTRATOR:-<from config>}"
echo "  Vision:       ${VISION_MODEL:-<from config>}"
echo "  API base:     ${API_BASE:-<from config>}"
echo "  Output:       ${OUTPUT_DIR}/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

SECONDS=0

for i in "${!DRAWINGS[@]}"; do
  DRAWING="${DRAWINGS[$i]}"
  NUM=$((i + 1))
  BASENAME=$(basename "$DRAWING")

  # Verify file exists
  if [[ ! -f "$DRAWING" ]]; then
    echo "[$NUM/$TOTAL] SKIP — file not found: $DRAWING"
    (( FAIL++ )) || true
    continue
  fi

  echo "[$NUM/$TOTAL] $BASENAME"
  echo "────────────────────────────────────────"

  if $PYTHON main.py -i "$DRAWING" -o "$OUTPUT_DIR" --single "${CLI_FLAGS[@]}"; then
    (( OK++ )) || true
    echo "  ✓ done"
  else
    (( FAIL++ )) || true
    echo "  ✗ FAILED"
  fi
  echo ""
done

ELAPSED=$((SECONDS / 60))m$((SECONDS % 60))s

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done in ${ELAPSED} — ${OK} ok, ${FAIL} failed out of ${TOTAL}"
echo "  Results: ${OUTPUT_DIR}/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# List produced files
echo ""
echo "Output files:"
find "$OUTPUT_DIR" -name "*.json" -exec basename {} \; | sort

if (( FAIL > 0 )); then
  exit 1
fi
