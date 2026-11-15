"""DBServer record-loss dry run: burst of 5 records, a record whose insert keeps failing (retried 5 s apart, its
pictures saved once, then written to FailedRecords/ with the picture files, without blocking the queue), a record
whose first attempt fails and second succeeds (stored, pictures saved once), the heartbeat's kept-open connection
(one connection for many checks; a broken connection is replaced), and a stop command with 3 records pending
(drained first). Needs Redis + PostgreSQL; run from a scratch directory."""
import sys, os, time, glob, json
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np, psycopg2
from redis import Redis
from utils.RedisUtils import *
import persistence.DBServer as DBS
import persistence.Persistence as P

r = Redis('localhost', 6379); clearQueues(r)
def send(tag, t1=19.0):
    qr = f"7204838$400112VA1D$BURST{tag}$30.10.2026$X$"
    img = np.full((100, 100, 3), 120, np.uint8); t = "2026-10-30 23:00:00"
    sendDataFromFEServerToDatabaseServer(r, qr, img, ok, t, img, ok, t, img, ok, t, img, ok, t, t1, ok, t,
        img, ok, t, ok, t, img, ok, t, 19.0, ok, t, img, ok, t, img, ok, t, img, ok, t, ok, t, 2.4, ok, t, ok)
    return qr
def rows(prefix):
    c = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database="ashok_leyland_test"); cur = c.cursor()
    cur.execute("select qr_code, nut_tightening_torque_1 from al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data where qr_code like %s order by record_id", (f"%{prefix}%",))
    out = cur.fetchall(); c.close(); return out
c = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database="ashok_leyland_test"); c.autocommit=True
c.cursor().execute("delete from al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data where qr_code like '%BURST%'"); c.close()
for f in glob.glob("C:/AshokLeylandImages/Test/FailedRecords/*BURST*.json"): os.remove(f)

dp = DBS.DataPersistence(mode="Test", username="admin")
time.sleep(2)
# (a) burst of 5 records sent back-to-back
for i in range(5): send(f"A{i}", 19.0 + i / 100)
time.sleep(6)
ra = rows("BURSTA"); print("RESULT (a) burst: sent 5, stored", len(ra), "in order:", [q.split('$')[2] for q, _ in ra])

# (b) insert keeps failing for one record -> retried 5 s apart (pictures saved once), then written to FailedRecords/
#     with the picture files; the queue is not blocked
orig = DBS.insertData
attemptTimes = {}
knuckleSaves = {}
origSaveKnuckle = dp.saveKnuckleImage
def countingSaveKnuckle(image):
    knuckleSaves[dp.currentModelName] = knuckleSaves.get(dp.currentModelName, 0) + 1
    return origSaveKnuckle(image)
dp.saveKnuckleImage = countingSaveKnuckle
def flaky(**kw):
    qr = kw.get("qr_code", "")
    tag = qr.split("$")[2]
    attemptTimes.setdefault(tag, []).append(time.monotonic())
    if "BURSTB0" in qr: return False
    if "BURSTD0" in qr and len(attemptTimes[tag]) == 1: return False      # the database back after one failure
    return orig(**kw)
DBS.insertData = flaky
knuckleSaves.clear()
send("B0"); send("B1")
time.sleep(14)
rb = rows("BURSTB"); files = glob.glob("C:/AshokLeylandImages/Test/FailedRecords/*BURSTB0*.json")
record = json.load(open(files[-1])) if files else {}
gaps = [round(b - a, 1) for a, b in zip(attemptTimes.get("BURSTB0", []), attemptTimes.get("BURSTB0", [])[1:])]
pictures = record.get("_pictureFiles", {})
print("RESULT (b) failing record: stored", [q.split('$')[2] for q, _ in rb], "| failed-record file:", len(files),
      "| reason:", record.get("_failureReason"), "| fe2db.q length now:", r.xlen(fe2dbq))
print("RESULT (b) attempts:", len(attemptTimes.get("BURSTB0", [])), "| gaps (s):", gaps,
      "| knuckle picture saves for B0+B1:", sum(knuckleSaves.values()),
      "| picture files in the JSON:", sum(1 for v in pictures.values() if v),
      "| all exist:", all(os.path.exists(v) for v in pictures.values() if v))
okB = (len(files) >= 1 and len(gaps) == 2 and all(g >= 4.9 for g in gaps) and sum(knuckleSaves.values()) == 2
       and pictures.get("check_knuckle_imagefile") and all(os.path.exists(v) for v in pictures.values() if v)
       and [q.split('$')[2] for q, _ in rb] == ["BURSTB1"])
print("CHECK (b)", "PASS" if okB else "FAIL")

# (d) the first attempt fails, the second (5 s later) succeeds -> stored once, pictures saved once
knuckleSaves.clear()
send("D0")
time.sleep(8)
rd = rows("BURSTD"); gapD = [round(b - a, 1) for a, b in zip(attemptTimes.get("BURSTD0", []), attemptTimes.get("BURSTD0", [])[1:])]
okD = len(rd) == 1 and gapD and gapD[0] >= 4.9 and sum(knuckleSaves.values()) == 1
print("RESULT (d) one failure then success: stored", len(rd), "| gap (s):", gapD, "| knuckle picture saves:", sum(knuckleSaves.values()))
print("CHECK (d)", "PASS" if okD else "FAIL")
DBS.insertData = orig
dp.saveKnuckleImage = origSaveKnuckle

# (e) heartbeat connection: one connection for many checks; a broken connection is replaced at the next check
connects = []
origConnect = psycopg2.connect
def countingConnect(*a, **k):
    connects.append(1); return origConnect(*a, **k)
P.psycopg2.connect = countingConnect
hb = P.PersistentDBConnection()
results = [hb.check("ashok_leyland_test") for _ in range(20)]
n20 = len(connects)
hb._connection.close()                                   # broken under it (as when PostgreSQL restarts)
afterBreak = hb.check("ashok_leyland_test")
P.psycopg2.connect = origConnect
okE = all(results) and n20 == 1 and afterBreak and len(connects) == 2
print("RESULT (e) heartbeat: 20 checks ->", n20, "connection(s); after the connection broke: check", afterBreak,
      "with", len(connects) - n20, "new connection")
print("CHECK (e)", "PASS" if okE else "FAIL")
hb.close()

# (c) stop command arrives while 3 records are still pending -> drained before stopping
for i in range(3): send(f"C{i}")
sendStopCommand(r, aProducer="test")
time.sleep(6)
rc = rows("BURSTC"); print("RESULT (c) stop with 3 pending: stored", len(rc), "| DBServer stopped:", dp.stopped, "| fe2db.q length:", r.xlen(fe2dbq))
r.delete(stopQ)
os._exit(0)
