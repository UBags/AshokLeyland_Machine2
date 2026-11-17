"""Dry run (2026-11-06, CHANGES_MACHINE2 section 82): database indexes, no REINDEX per insert, the QR code server's
duplicate check without re-creating the database, and the DB server's image folders moving to the new day.
Needs PostgreSQL on 127.0.0.1:5432 (postgres/postgres) and Redis.

  X1 after setDatabaseName: one index per column (idx_qr_code, idx_model_name, idx_created_on, + the primary key);
     the duplicates the old ensureIndexesExist added are dropped (they are created first here, as an old database has)
  X2 insertData makes no REINDEX (pg_stat_activity / timing: no REINDEX statement is run) and the stored QR code is
     found at once by checkIfQRCodeExists; the planner can use idx_qr_code for it (EXPLAIN with seq scans off)
  X3 the QR code server's duplicate check (QRCodeDispatcher.createDisplayString) does not re-create the database:
     createDatabase / createSchema / createAllTables are not called, and it still finds the duplicate
  D1 the DB server's day watcher: when the date changes between two wake-ups, the image folders move to the new day
     (the folder of every step), and the new folders exist
"""
import datetime as _dt, os, pathlib, sys, threading, time, types

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
if sys.platform != 'win32':
    sys.modules['winsound'] = types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0)

import psycopg2
import persistence.Persistence as P

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

DB = "ashok_leyland_test"
def q(sql, args=None, fetch=True):
    with psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database=DB) as c:
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if fetch else None

# ------------------------------------------------------------------------------------------------ X1
P.setDatabaseName("Test")
schema = P.getSchemaName(); table = f"{schema}.hub_and_disc_assembly_data"
for col in ("qr_code", "model_name", "created_on"):           # what the old code left in every database
    q(f"CREATE INDEX IF NOT EXISTS {schema}_hub_and_disc_assembly_data_{col}_idx ON {table} ({col})", fetch=False)
before = sorted(r[0] for r in q("select indexname from pg_indexes where tablename='hub_and_disc_assembly_data'"))
P.setDatabaseName("Test")
after = sorted(r[0] for r in q("select indexname from pg_indexes where tablename='hub_and_disc_assembly_data'"))
check("X1 one index per column after set-up; the old duplicates dropped",
      after == sorted(["hub_and_disc_assembly_data_pkey", "idx_created_on", "idx_model_name", "idx_qr_code"])
      and len(before) == 7, f"before {len(before)}: after {after}")

# ------------------------------------------------------------------------------------------------ X2
calls = []
orig = P.reindexIndices
P.reindexIndices = lambda *a, **k: calls.append(1) or orig(*a, **k)
code = f"DRYRUN-INDEX-{int(time.time())}$400102VA1C$1$01.01.2026$X$"
ok_ = P.insertData(db_name=DB, qr_code=code, model_name="DOSTPLUS", lhs_rhs="RHS", model_tonnage="2.5T",
                   component_assembly_start_datetime="2026-11-06 09:00:00", ok_notok_result="OK", username="admin",
                   remarks="dry run X2")
check("X2 insert stored, no REINDEX run", ok_ and calls == [], str(calls))
check("X2 the new QR code is found at once (duplicate check)", P.checkIfQRCodeExists(qr_code=code, db_name=DB))
plan = None
with psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database=DB) as c:
    with c.cursor() as cur:
        cur.execute("SET enable_seqscan = off")
        cur.execute(f"EXPLAIN SELECT EXISTS (SELECT 1 FROM {table} WHERE qr_code = %s)", (code,))
        plan = " ".join(r[0] for r in cur.fetchall())
check("X2 the duplicate check can use idx_qr_code", "idx_qr_code" in plan, plan[:160])
P.reindexIndices = orig

# ------------------------------------------------------------------------------------------------ X3
created = []
for fn in ("createDatabase", "createSchema", "createAllTables", "ensureIndexesExist"):
    real = getattr(P, fn)
    setattr(P, fn, (lambda real, fn: lambda *a, **k: created.append(fn) or real(*a, **k))(real, fn))
import costhetaio.qrcodescanner.QRCodeScanningServer as QS
for fn in ("createDatabase", "createSchema", "createAllTables", "ensureIndexesExist"):
    if hasattr(QS, fn):
        setattr(QS, fn, getattr(P, fn))
dispatcher = QS.QRCodeDispatcher(name="dry run", mode="Test")
display = dispatcher.createDisplayString(code)
check("X3 duplicate check of a scan: the database is not re-created", created == [], str(created))
check("X3 ... and the duplicate is still found", "DUPLICATE" in (display or "").upper(), display)
q(f"DELETE FROM {table} WHERE qr_code = %s", (code,), fetch=False)

# ------------------------------------------------------------------------------------------------ D1
import persistence.DBServer as DBS
dp = DBS.DataPersistence(mode="Test", username="admin")
dp.stopped = False
DBS.DataPersistence.DAY_CHECK_INTERVAL_SECS = 0.5
today = _dt.datetime.now()
clock = {"now": today}
class FakeDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None): return clock["now"]
DBS.datetime = FakeDatetime
P.datetime = FakeDatetime            # getTodaysDateAsAFolder() names the folder
P.makeFoldersForImages("Test")
start = P.getFolderForKnuckleImages()
t = threading.Thread(target=dp.watchForNewDay, daemon=True); t.start()
time.sleep(1.5)
same = P.getFolderForKnuckleImages()
clock["now"] = today + _dt.timedelta(days=1)
tomorrow = clock["now"].strftime("%Y-%m-%d")
moved = False
for _ in range(40):
    time.sleep(0.1)
    if tomorrow in P.getFolderForKnuckleImages():
        moved = True; break
check("D1 same day: the folders are left as they are", same == start, same)
folders = [P.getFolderForKnuckleImages(), P.getFolderForHubAndBottomBearingImages(), P.getFolderForTopBearingImages(),
           P.getFolderForNutAndPlateWasherImages(), P.getFolderForBungAndNoBungImages(),
           P.getFolderForSplitPinAndWasherImages(), P.getFolderForCapImages()]
check("D1 the date changed between two wake-ups -> every step's folder is the new day's, and it exists",
      moved and all(tomorrow in f and os.path.isdir(f) for f in folders), f"{folders[0]}")
dp.stopped = True

failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
os._exit(1 if failed else 0)
