"""Dry run: QR code validation (GetAndValidateQRCode.evaluateQRCode with the patterns of ApplicationConfiguration
.properties) on every QR code written in the repo's tests, which are in the real codes' format (2026-11-07, CHANGES
section 85: section 84 had made the pattern match the whole code, which rejected every real code - they go on after
the pattern, "...$1$SPDL ASSY-KNULH$SNPL-MAT$").

  V1 every sample code in the repo's tests (real format) is valid, with and without the trailing newline / spaces
     the scanner sends
  V2 side and model are read from each (getModel_LHSRHS_AndTonnage)
  V3 not valid: empty, a wrong code, a date with another separator (the dots are matched as dots), a code cut short
  V4 an invalid scan during a read (CHANGES 110): "QR Code not valid" for the frontend (notice "qrcode") and the
     alarm signal (as for a vision NOT OK); the read goes on; the next valid scan is taken and clears the notice.
     Needs Redis on localhost:6379 (skipped, and said so, without it)
  V5 a scan that arrives across two serial reads (readline returns what it has when its 0.1 s timeout expires;
     CHANGES 112) is taken whole, with no notice and no alarm: line end LF, line end CR, no line end (taken after
     0.3 s quiet); a bad scan then a split good one: one notice / alarm, the good code taken

No scanner (a stand-in serial port).   Run from the project root:  python test/machine2_dryrun/DryRun_QRCode_Validation.py
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

# V4: invalid scan during a read -> notice + alarm; then a valid scan -> taken, notice cleared
try:
    from redis import Redis
    from utils.RedisUtils import (alarmq, FRONTEND_NOTICE_PREFIX, readFrontendNotices, timeKeyString, actionKeyString)
    from costhetaio.qrcodescanner.QRCodeScanningServer import GetAndValidateQRCodeCommand, INVALID_QR_CODE_NOTICE
    r = Redis("localhost", 6379); r.ping()
    r.delete(alarmq, f"{FRONTEND_NOTICE_PREFIX}qrcode")
    class _Serial:
        def __init__(self, lines): self.lines = list(lines)
        def readline(self):
            if self.lines:
                return self.lines.pop(0)
            import time as _t; _t.sleep(0.05); return b""
        def reset_input_buffer(self): pass
    noticesSeen, alarmsSeen = [], []
    class _Parent:
        def __init__(self, serial): self.qrCodeSerialConnection = serial
        def getQRCodeConnectionStatus(self):
            noticesSeen.append(readFrontendNotices(r)); alarmsSeen.append(r.xlen(alarmq)); return True
    reader = object.__new__(GetAndValidateQRCode)
    reader.regexPatterns = validator.regexPatterns
    reader.parent = _Parent(_Serial([b"HELLO$WORLD\r\n", (one + "\r\n").encode()]))
    reader.sleepTime = 0.01; reader.stopped = False; reader.abortRead = False; reader.readInProgress = False
    reader.currentQRCodeValue = ""; reader.redisConnectionForNotices = None
    result = reader.processItem({timeKeyString: 1.0, actionKeyString: GetAndValidateQRCodeCommand})
    shownAfterInvalid = [n for n in noticesSeen if n]
    check("V4 invalid scan: 'QR Code not valid' left for the frontend (notice qrcode)",
          any(INVALID_QR_CODE_NOTICE in n for n in shownAfterInvalid),
          [t for n in shownAfterInvalid for t in n if t == INVALID_QR_CODE_NOTICE][:1])
    check("V4 ... and the alarm signal sent (as for a vision NOT OK)", max(alarmsSeen or [0]) >= 1, alarmsSeen)
    check("V4 the read goes on: the next valid scan is taken", result and result.get("qrCode") == one,
          result.get("qrCode") if result else None)
    check("V4 ... and the notice is cleared", not r.exists(f"{FRONTEND_NOTICE_PREFIX}qrcode"),
          r.get(f"{FRONTEND_NOTICE_PREFIX}qrcode"))
    r.delete(alarmq)
    # V5: split reads (pyserial readline returns the part that arrived before its timeout)
    def readSplit(chunks):
        r.delete(alarmq, f"{FRONTEND_NOTICE_PREFIX}qrcode")
        seen = []
        reader2 = object.__new__(GetAndValidateQRCode)
        reader2.regexPatterns = validator.regexPatterns
        reader2.parent = _Parent(_Serial(chunks))
        reader2.sleepTime = 0.01; reader2.stopped = False; reader2.abortRead = False; reader2.readInProgress = False
        reader2.currentQRCodeValue = ""; reader2.redisConnectionForNotices = None
        res = reader2.processItem({timeKeyString: 1.0, actionKeyString: GetAndValidateQRCodeCommand})
        return (res.get("qrCode") if res else None), r.xlen(alarmq), \
            any(INVALID_QR_CODE_NOTICE in n for n in noticesSeen[-200:] if n) or bool(r.exists(f"{FRONTEND_NOTICE_PREFIX}qrcode"))
    half = len(one) // 2
    for label, chunks in (("line end LF", [one[:half].encode(), (one[half:] + "\r\n").encode()]),
                          ("line end CR", [one[:half].encode(), (one[half:] + "\r").encode()]),
                          ("no line end (taken after 0.3 s quiet)", [one[:half].encode(), one[half:].encode()])):
        noticesSeen.clear()
        got, alarms, notice = readSplit(chunks)
        check(f"V5 split across two reads, {label}: taken whole, no alarm, no notice",
              got == one and alarms == 0 and not notice, (got == one, alarms, notice))
    noticesSeen.clear()
    got, alarms, notice = readSplit([b"HELLO$WORLD\r\n", one[:half].encode(), (one[half:] + "\n").encode()])
    check("V5 a bad scan, then a split good one: one alarm, the good code taken", got == one and alarms == 1,
          (got == one, alarms))
    r.delete(alarmq)
except Exception as e:
    import redis as _redis
    if isinstance(e, _redis.exceptions.ConnectionError):
        print(f"SKIP  V4 (no Redis on localhost:6379: {e})", flush=True)
    else:
        import traceback; traceback.print_exc()
        check(f"V4 crashed: {e!r}", False)

failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""),
      flush=True)
os._exit(1 if failed else 0)
