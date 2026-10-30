"""DBServer record-loss dry run: burst of 5 records, a record whose insert keeps failing (retried, then written to
FailedRecords/ without blocking the queue), and a stop command with 3 records pending (drained first).
Needs Redis + PostgreSQL; run from a scratch directory."""
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

dp = DBS.DataPersistence(mode="Test", username="admin")
time.sleep(2)
# (a) burst of 5 records sent back-to-back
for i in range(5): send(f"A{i}", 19.0 + i / 100)
time.sleep(6)
ra = rows("BURSTA"); print("RESULT (a) burst: sent 5, stored", len(ra), "in order:", [q.split('$')[2] for q, _ in ra])

# (b) insert keeps failing for one record -> retried, then written to FailedRecords/, queue not blocked
orig = DBS.insertData
def flaky(**kw):
    if "BURSTB0" in kw.get("qr_code", ""): return False
    return orig(**kw)
DBS.insertData = flaky
send("B0"); send("B1")
time.sleep(8)
DBS.insertData = orig
rb = rows("BURSTB"); files = glob.glob("C:/AshokLeylandImages/Test/FailedRecords/*BURSTB0*.json")
print("RESULT (b) failing record: stored", [q.split('$')[2] for q, _ in rb], "| failed-record file:", len(files),
      "| reason:", json.load(open(files[-1]))["_failureReason"] if files else None, "| fe2db.q length now:", r.xlen(fe2dbq))

# (c) stop command arrives while 3 records are still pending -> drained before stopping
for i in range(3): send(f"C{i}")
sendStopCommand(r, aProducer="test")
time.sleep(6)
rc = rows("BURSTC"); print("RESULT (c) stop with 3 pending: stored", len(rc), "| DBServer stopped:", dp.stopped, "| fe2db.q length:", r.xlen(fe2dbq))
r.delete(stopQ)
os._exit(0)
