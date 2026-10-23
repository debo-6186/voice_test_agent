#!/bin/bash
# Runs the full 15-condition ladder for one (vendor, language, scenario)
# against scripts/run_caller.py, inside the polycall-bench Docker image
# (PJSIP is only prebuilt there - see docs/sip-harness.md). Real calls,
# real vendor cost - see docs/responsible-use.md before running this
# against a vendor you haven't already budgeted for.
#
# Requires: docker, config/.env with this vendor's credentials (API keys,
# agent IDs - see vendors/<vendor>.yaml), and the session corpus already
# built for this (lang, scenario) pair - see scripts/build_session_corpus.py
# if out/sessions/sessions/<lang>/<scenario>/ doesn't exist yet.
#
# --silence-s 1.0 and --connect-timeout-s 40 are not run_caller.py's
# defaults (0.7s / vendor-specific) - confirmed live these are the values
# that stop the caller interrupting a vendor mid-sentence and give Telnyx
# enough time to answer, respectively. Override either with a flag of the
# same name appended after this script's own args if a different vendor
# needs different values.
#
# Usage:
#   scripts/run_pilot.sh --vendor telnyx --lang hi-IN \
#       [--scenario S3_name_phone_date] [--out-suffix v1] [extra run_caller.py args...]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMG=polycall-bench:latest
SCENARIO="S3_name_phone_date"
OUT_SUFFIX="$(date +%s)"
VENDOR=""
LANG_CODE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vendor) VENDOR="$2"; shift 2 ;;
    --lang) LANG_CODE="$2"; shift 2 ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
    --out-suffix) OUT_SUFFIX="$2"; shift 2 ;;
    *) break ;;
  esac
done

if [[ -z "$VENDOR" || -z "$LANG_CODE" ]]; then
  echo "usage: $0 --vendor <telnyx|retell|elevenlabs> --lang <bcp47> [--scenario <id>] [--out-suffix <tag>] [extra args...]" >&2
  exit 1
fi

SESSION_BASE="/app/out/sessions/sessions/$LANG_CODE/$SCENARIO"
if [[ ! -d "$REPO/out/sessions/sessions/$LANG_CODE/$SCENARIO" ]]; then
  echo "no session corpus at out/sessions/sessions/$LANG_CODE/$SCENARIO - run scripts/build_session_corpus.py first" >&2
  exit 1
fi

CONDITIONS="ctl_clean ctl_noise_only A_cafe_snr00 A_cafe_snr05 A_cafe_snr10 A_cafe_snr15 A_cafe_snr20 A_cafe_snr30 B_car_snr10 B_office_snr10 B_street_snr10 B_tv_snr10 C_jitter30 C_loss5 C_opus8k"
LOG="/app/out/caller_pilot_${VENDOR}_${LANG_CODE}_${OUT_SUFFIX}.jsonl"
DRIVER_LOG="$REPO/out/pilot_${VENDOR}_${LANG_CODE}_${OUT_SUFFIX}_driver.log"

{
  for cond in $CONDITIONS; do
    echo "=== $cond ==="
    docker run --rm --network host \
      -v "$REPO:/app" -v polycall-hf-cache:/root/.cache/huggingface \
      --env-file "$REPO/.env" \
      --entrypoint python3 "$IMG" \
      /app/scripts/run_caller.py \
      --session-dir "$SESSION_BASE/$cond" \
      --scenario "$SCENARIO" --lang "$LANG_CODE" \
      --vendor "$VENDOR" --local-port 5070 --stun stun.l.google.com:19302 \
      --connect-timeout-s 40 --silence-s 1.0 --log "$LOG" \
      "$@" \
      > "$REPO/out/pilot_${VENDOR}_${LANG_CODE}_${OUT_SUFFIX}_${cond}.log" 2>&1
    echo "$cond done"
  done
  echo "PILOT_${VENDOR^^}_${LANG_CODE}_${OUT_SUFFIX^^}_COMPLETE"
} | tee "$DRIVER_LOG"
