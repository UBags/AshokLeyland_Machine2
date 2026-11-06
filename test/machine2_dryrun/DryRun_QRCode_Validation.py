"""Dry run: QR code validation (GetAndValidateQRCode.evaluateQRCode with the patterns of ApplicationConfiguration
.properties) on every QR code written in the repo's tests, which are in the real codes' format (2026-11-07, CHANGES
section 85: section 84 had made the pattern match the whole code, which rejected every real code - they go on after
the pattern, "...$1$SPDL ASSY-KNULH$SNPL-MAT$").

  V1 every sample code in the repo's tests (real format) is valid, with and without the trailing newline / spaces
     the scanner sends
  V2 side and model are read from each (getModel_LHSRHS_AndTonnage)
  V3 not valid: empty, a wrong code, a date with another separator (the dots are matched as dots), a code cut short

No Redis, no scanner.   Run from the project root:  python test/machine2_dryrun/DryRun_QRCode_Validation.py
"""
import os, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

from Configuration import CosThetaConfigurator as C
from costhetaio.qrcodescanner.QRCodeScanningServer import GetAndValidateQRCode

validator = object.__new__(GetAndValidateQRCode)
validator.regexPatterns = C.getInstance().getQRCodeRegexPatterns()
check("V0 patterns read from the properties", len(validator.regexPatterns) >= 1, f"{len(validator.regexPatterns)}")

# every QR code literal in the tests: 7 digits $ 6 digits 2 letters digit letter $ ... up to the closing quote
samples = set()
for path in (ROOT / "test").rglob("*.py"):
    for m in re.finditer(r'"(\d{7}\$\d{6}[A-Z]{2}\d[A-Z]\$\d{3,5}\$\d{2}\.\d{2}\.\d{4}\$[^"]+)"',
                         path.read_text(encoding="utf-8", errors="replace")):
        samples.add(m.group(1))
check("V1 sample codes found in the tests", len(samples) >= 3, f"{len(samples)}: {sorted(samples)[:3]}")
bad = [s for s in samples if not validator.evaluateQRCode(s)]
check("V1 every sample code (real format, with what follows the pattern) is valid", not bad, f"{bad[:3]}")
bad = [s for s in samples if not validator.evaluateQRCode(f"  {s}\r\n")]
check("V1 ... also with the scanner's line end and spaces", not bad, f"{bad[:3]}")

from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
parsed = {s: getModel_LHSRHS_AndTonnage(s) for s in samples}
bad = {s: v for s, v in parsed.items() if not v or (v[1] or "").upper() not in ("LHS", "RHS") or not v[0]}
check("V2 model and side read from every sample code", not bad, f"{list(bad.items())[:2]}")

one = sorted(samples)[0]
for label, code in (("empty", ""), ("a wrong code", "HELLO$WORLD"),
                    ("a date with dashes", one.replace(".", "-", 2)),
                    ("cut after the date", one[:one.index("$", 25)])):
    check(f"V3 not valid: {label}", not validator.evaluateQRCode(code), code)

failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""),
      flush=True)
os._exit(1 if failed else 0)
