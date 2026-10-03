#!/usr/bin/env bash
# End-to-end check of a running service: health, parse, validate, detect, error handling.
# Usage: scripts/smoke-test.sh [base-url]     (default http://localhost:8080)
set -euo pipefail
URL="${1:-http://localhost:8080}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
check() {  # check <name> <expected-substring> <command...>
  local name="$1" want="$2"; shift 2
  local out; out="$("$@" 2>&1 || true)"
  if [[ "$out" == *"$want"* ]]; then echo "  ok    $name"; pass=$((pass+1))
  else echo "  FAIL  $name"; echo "        expected: $want"; echo "        got: ${out:0:300}"; fail=$((fail+1)); fi
}
post() { curl -s --data-binary @"$ROOT/$1" "$URL$2"; }

echo "Smoke testing $URL"
check "health"                 '"status":"ok"'          curl -s "$URL/healthz"
check "X12 5010 850"           '"messages": 1'          post samples/x12/5010/850_purchase_order.edi "/v1/parse?include=summary"
check "X12 4010, 2 documents"  '"messages": 2'          post samples/x12/4010/850_purchase_order_pipe_delims.edi "/v1/parse?include=summary"
check "837 claim fields"       '"CLM01": "CLM-0001"'    post samples/x12/5010/837P_professional_claim.edi "/v1/parse?include=message"
check "EDIFACT"                '"type": "ORDERS"'       post samples/edifact/ORDERS_D96A_una.edi "/v1/parse?include=message&segments=false"
check "TRADACOMS"              '"messages": 3'          post samples/tradacoms/ORDERS_order_file.edi "/v1/parse?include=summary"
check "HL7"                    '"version": "2.5.1"'     post samples/hl7/ADT_A01_A08.hl7 "/v1/parse?include=message&segments=false"
check "gzip upload"            '"valid": true'          bash -c "gzip -c '$ROOT/samples/x12/5010/835_claim_payment.edi' | curl -s --data-binary @- -H 'Content-Encoding: gzip' '$URL/v1/parse?include=summary'"
check "validate"               '"valid":true'           post samples/x12/5010/834_benefit_enrollment.edi "/v1/validate"
check "detect"                 '"repetition":"!"'       post samples/x12/5010/837P_professional_claim_alt_delims.edi "/v1/detect"
check "rejects non-EDI (422)"  '422'                    curl -s -o /dev/null -w "%{http_code}" --data-binary "not edi" "$URL/v1/parse"
echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
