set -euo pipefail

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY before running evaluation}"
: "${OPENAI_MODEL:=gpt-4.1}"

python3 -m scientific_eval.cli \
  --input result1.json \
  --output evaluation_outputs/result1_report.json \
  --model "$OPENAI_MODEL"
