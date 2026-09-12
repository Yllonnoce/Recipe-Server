#!/usr/bin/env bash
# End-to-end check of the virtual printer with CUPS' ipptool (apt install cups-ipp-utils).
# Usage: tests/e2e/run_ipptool.sh [ipp://host:8631/ipp/print] [web http://host:8000]
set -u
URI="${1:-ipp://localhost:8631/ipp/print}"
WEB="${2:-http://localhost:8000}"
T=/usr/share/cups/ipptool
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
fail=0
run() { echo "== $*"; ipptool -tv "$URI" "$@" > "$TMP/out.txt" 2>&1; rc=$?; grep -E "^\s*(PASS|FAIL)|EXPECTED|GOT" "$TMP/out.txt" | head -20; [ $rc -ne 0 ] && { echo "   ipptool exit $rc"; tail -5 "$TMP/out.txt"; fail=1; }; }

python3 - "$TMP/sample.pdf" <<'PY'
import sys, pymupdf
d = pymupdf.open(); p = d.new_page(); p.insert_text((72, 72), "ipptool sample: Tomato Soup\n2 cans tomatoes\n1 onion", fontsize=14); d.save(sys.argv[1])
PY
python3 - "$TMP/sample.pwg" "$TMP/sample.urf" <<'PY'
import sys
sys.path.insert(0, "tests")
from tools import rasterenc as E
g = E.gradient(200, 120, 1)
open(sys.argv[1], "wb").write(E.pwg([(200, 120, 1, g)], dpi=100))
open(sys.argv[2], "wb").write(E.urf([(200, 120, 3, E.gradient(200, 120, 3))], dpi=100))
PY

run "$T/get-printer-attributes.test"
run "$T/validate-job.test" -f "$TMP/sample.pdf"
run "$T/print-job.test" -f "$TMP/sample.pdf"
run -d filetype=image/pwg-raster "$T/print-job.test" -f "$TMP/sample.pwg"
run -d filetype=image/urf "$T/print-job.test" -f "$TMP/sample.urf"
run "$T/get-jobs.test"
run "$T/get-completed-jobs.test"
run "$T/create-job.test"
run "$T/cancel-current-job.test"
run "$T/identify-printer.test"

sleep 4
echo "== library now lists:"
curl -s "$WEB/partials/jobs" | grep -o 'ipptool sample\|sample\|DONE\|done\|failed' | sort | uniq -c
rm -rf "$TMP"
exit $fail
