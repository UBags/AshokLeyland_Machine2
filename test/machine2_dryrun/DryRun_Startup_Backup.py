"""
DryRun_Startup_Backup - the start-up database backup runs in a thread beside the mode and login dialogs (CHANGES 174).

B1  startDatabaseBackupThread returns at once; the backup goes on in a non-daemon thread
B2  waitForDatabaseBackup waits for it and says how long; a second call returns 0 at once
B3  an exception in doBackup is reported, the thread ends, nothing else breaks
B4  main(): both databases set up before the backup starts; the mode only selects the name (createDB=False);
    every exit after the backup started waits for it; the DB server starts only after it
B5  why that order (PostgreSQL): while a table is locked as pg_dump locks it, a read (the login) is not held up,
    but setDatabaseName's ALTER TABLE ... ADD COLUMN IF NOT EXISTS waits
B6  the real doBackup in the thread on this PostgreSQL: a backup file written, the "done in .. s" line

Needs Redis (MainProgram connects at import) and PostgreSQL on 127.0.0.1:5432 (postgres / postgres) for B5 / B6.
No PLC, camera or GUI windows. Run from the project root:
    python test/machine2_dryrun/DryRun_Startup_Backup.py
"""
import ast, os, pathlib, sys, threading, time, types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "allenbradley" if "machine1" in str(pathlib.Path(__file__).parent) else "siemens")
if sys.platform != 'win32':
    sys.modules['winsound'] = types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0,
                                                    Beep=lambda *a: None)

import MainProgram as M

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)


realBackup = M.doBackup

# ---------------------------------------------------------------- B1 / B2
print("\n=== B1-B2 backup thread and the wait ===", flush=True)
M.doBackup = lambda *a, **k: (time.sleep(2.0), True)[1]
t0 = time.monotonic()
thread = M.startDatabaseBackupThread()
returnedAfter = time.monotonic() - t0
check("B1 startDatabaseBackupThread returns at once (the dialogs are not held up)", returnedAfter < 0.3, round(returnedAfter, 3))
check("B1 ... the backup runs in a live, non-daemon thread", thread.is_alive() and not thread.daemon,
      (thread.is_alive(), thread.daemon))
time.sleep(0.5)                                     # the operator choosing the mode
waited = M.waitForDatabaseBackup(thread, "starting the DB server")
check("B2 waitForDatabaseBackup waits for the rest of the backup (~1.5 s)", 1.2 < waited < 2.0 and not thread.is_alive(),
      round(waited, 2))
again = M.waitForDatabaseBackup(thread, "exiting")
check("B2 ... a second wait returns 0 at once", again == 0.0, again)
check("B2 ... None (no thread) returns 0", M.waitForDatabaseBackup(None, "x") == 0.0)

# ---------------------------------------------------------------- B3
print("\n=== B3 exception in doBackup ===", flush=True)
def failingBackup(*a, **k):
    raise RuntimeError("pg_dump exploded")
M.doBackup = failingBackup
thread = M.startDatabaseBackupThread()
M.waitForDatabaseBackup(thread, "exiting")
check("B3 the exception is reported in the thread and the thread ends", not thread.is_alive())
M.doBackup = realBackup

# ---------------------------------------------------------------- B4 order in main()
print("\n=== B4 order of the steps in main() ===", flush=True)
source = (ROOT / "MainProgram.py").read_text(encoding="utf-8")
mainNode = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "main")
mainText = ast.get_source_segment(source, mainNode)
lines = mainText.splitlines()


def lineOf(fragment, start=0):
    for i in range(start, len(lines)):
        if fragment in lines[i] and not lines[i].strip().startswith("#"):
            return i
    return -1


startAt = lineOf("startDatabaseBackupThread()")
prodAt, testAt = lineOf('setDatabaseName(mode="Production")'), lineOf('setDatabaseName(mode="Test")')
check("B4 both databases are set up before the backup starts", 0 <= prodAt < startAt and 0 <= testAt < startAt,
      (prodAt, testAt, startAt))
modeAt = lineOf("chooseMode()")
check("B4 the backup starts before the mode dialog", 0 <= startAt < modeAt, (startAt, modeAt))
selectAt = lineOf("setDatabaseName(mode=currentMode")
check("B4 after the mode: the database name only (createDB=False)",
      selectAt > modeAt and "createDB=False" in lines[selectAt], lines[selectAt].strip() if selectAt >= 0 else None)
check("B4 no other setDatabaseName after the backup started",
      [i for i, l in enumerate(lines) if "setDatabaseName(" in l and not l.strip().startswith("#") and i > startAt] == [selectAt])
exits = [i for i, l in enumerate(lines) if "stopProcesses(" in l and not l.strip().startswith("#") and i > startAt]
unguarded = [lines[i].strip() for i in exits
             if not any("waitForDatabaseBackup(" in lines[j] for j in range(max(startAt, i - 4), i))]
check(f"B4 every exit after the backup started waits for it ({len(exits)} exits)", exits and not unguarded, unguarded)
dbAt = lineOf("target=startTheDBServer")
waitDbAt = lineOf('waitForDatabaseBackup(startupBackup, "starting the DB server")')
check("B4 the DB server starts only after the backup", 0 <= waitDbAt < dbAt, (waitDbAt, dbAt))
check("B4 the backup is no longer run in the main thread",
      not any(l.strip().startswith("doBackup(") for l in lines))

# ---------------------------------------------------------------- B5 / B6 PostgreSQL
print("\n=== B5-B6 PostgreSQL ===", flush=True)
try:
    import psycopg2
    admin = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", port="5432", connect_timeout=5)
    admin.autocommit = True
except Exception as e:
    admin = None
    print(f"SKIP  B5 / B6: no PostgreSQL ({e})", flush=True)

if admin is not None:
    with admin.cursor() as c:
        c.execute("DROP DATABASE IF EXISTS dryrun_startup_backup")
        c.execute("CREATE DATABASE dryrun_startup_backup")

    def connect():
        conn = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", port="5432",
                                dbname="dryrun_startup_backup", connect_timeout=5)
        conn.autocommit = True
        return conn

    with connect().cursor() as c:
        c.execute("CREATE TABLE t (a int, b int); INSERT INTO t VALUES (1, 1)")

    def holdLikePgDump(seconds):
        conn = connect(); conn.autocommit = False
        with conn.cursor() as c:
            c.execute("LOCK TABLE t IN ACCESS SHARE MODE")      # the lock pg_dump takes on every table it dumps
            time.sleep(seconds)
        conn.rollback(); conn.close()

    holder = threading.Thread(target=holdLikePgDump, args=(3.0,)); holder.start(); time.sleep(0.5)
    t0 = time.monotonic()
    with connect().cursor() as c:
        c.execute("SELECT count(*) FROM t"); c.fetchone()
    readTook = time.monotonic() - t0
    t0 = time.monotonic()
    with connect().cursor() as c:
        c.execute("ALTER TABLE t ADD COLUMN IF NOT EXISTS b int")          # a no-op, as in setDatabaseName
    alterTook = time.monotonic() - t0
    holder.join()
    check("B5 while pg_dump's lock is held, a read (the login) is not held up", readTook < 0.5, round(readTook, 3))
    check("B5 ... but a no-op ALTER TABLE ... ADD COLUMN IF NOT EXISTS waits for it", alterTook > 2.0, round(alterTook, 2))

    # B6: the real doBackup in the thread, against this database, into a temporary backup folder
    import tempfile
    from persistence import PostgresBackupUtility as PBU
    folder = tempfile.mkdtemp(prefix="dryrun_backup_")
    oldDb = os.environ.get("POSTGRES_DB")
    os.environ["POSTGRES_DB"] = "dryrun_startup_backup"
    configurator = M.CosThetaConfigurator.getInstance()
    oldDir = configurator.getDatabaseBackupDirectory
    configurator.getDatabaseBackupDirectory = lambda: folder
    try:
        thread = M.startDatabaseBackupThread()
        M.waitForDatabaseBackup(thread, "the check")
        files = [f for f in os.listdir(folder) if f.startswith("backup-dryrun_startup_backup-")]
        check("B6 the real doBackup in the thread writes a backup file", len(files) == 1, files)
    finally:
        configurator.getDatabaseBackupDirectory = oldDir
        if oldDb is None:
            os.environ.pop("POSTGRES_DB", None)
        else:
            os.environ["POSTGRES_DB"] = oldDb
    with admin.cursor() as c:
        c.execute("DROP DATABASE IF EXISTS dryrun_startup_backup")
    admin.close()

print(f"\n{sum(results)}/{len(results)} passed", flush=True)
sys.exit(0 if all(results) else 1)
