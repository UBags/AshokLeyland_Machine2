"""Dry run: persistence.Persistence + the real persistence.DBServer against PostgreSQL (TEST database) and Redis,
with automatic PASS/FAIL checks (exit code 1 on failure). Covers everything test/Test_DBServer_And_Persistence.py does,
with field-by-field checks:

  1. Database set-up: setDatabaseName("Test") creates DB, schema and tables; the Machine 2 columns are there and the
     dropped free-rotation columns are not; the three roles and the admin user exist.
  2. Direct Persistence.insertData(): every column stored as given (results normalised to OK / NOTOK).
  3. checkIfQRCodeExists(): true for a stored code, false for an unknown one.
  4. Full FE record -> fe2db.q -> DBServer -> PostgreSQL: every one of the 45 record fields stored exactly (results,
     the 3 torques, the 14 step datetimes, overall result), model / side / tonnage derived from the QR code, username,
     empty remarks; the 9 inspection images saved as 9 distinct files, each pixel-identical to the image sent.
  5. Same QR code sent again: stored as a second row with remarks = DUPLICATE.
  6. Aborted cycle (as the frontend sends it after an emergency abort): steps not reached have no picture, NOT OK and
     1970 datetimes, torques 0.0 -> stored as NOTOK, no image file, 1970 datetimes, overall NOTOK.
  7. Report / counter functions used by the frontend menus: getAllDataOfToday, getDataByModelName,
     getDataByModelNameAndDateLimits, getUniqueModelNames, getComponentsProducedToday (counts OK rows only).
  8. Clean-up of every row and image file this script created.
  9. Machine settings (machine_settings table, frontend Machine Settings dialog): the default row (30 rpm, 15 / 19 Nm,
     1 / 1 rotations) exists; validateMachineSettings / insertNewMachineSettings reject out-of-range values (RPM
     outside 20-40 or not whole, torques not in (0, 20) or > 2 decimals, counts not 1 / 2); the database CHECK
     constraints reject them too; a valid change becomes the settings in force, with updated_by, and is in the history.
     Section 4 also checks that the settings the IOServer published for a QR code (cycleSettings:<QR>) are stored in
     that component's record (setting_* columns), and section 6 that a record without them stores NULL.

Needs Redis on localhost:6379 and PostgreSQL on 127.0.0.1:5432 (postgres/postgres). Run from a scratch directory
(image files are written under C:/AshokLeylandImages/Test/... relative to it on non-Windows hosts):
    python <project>/test/machine2_dryrun/DryRun_DBServer_And_Persistence.py
"""
import os, sys, time, threading, uuid
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np, cv2, psycopg2, psycopg2.extras, psycopg2.errors
from redis import Redis
from utils.RedisUtils import sendDataFromFEServerToDatabaseServer, ok, notok, clearQueues, fe2dbq, publishCycleSettingsForQRCode
import persistence.Persistence as P
from persistence.DBServer import DataPersistence

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

DB = "ashok_leyland_test"
SCHEMA, TABLE = "al_hub_and_disc_assembly_schema", "hub_and_disc_assembly_data"
RUN = uuid.uuid4().hex[:6].upper()                # makes this run's QR codes unique
def qr(prefix, n):       # DOST LHS part number + a unique serial
    return f"{prefix}${RUN}{n}$30.10.2026$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
QR_LHS = "7204838$400112VA1D"        # DOST LHS 2.0T
QR_RHS_PLUS = "8201206$400102VA1C"   # DOSTPLUS RHS 2.5T
OKS, NOTOKS = P.rationaliseOK_NotOK(ok), P.rationaliseOK_NotOK(notok)

def conn():
    c = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database=DB); c.autocommit = True; return c

def rows_for(code):
    c = conn(); cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"select * from {SCHEMA}.{TABLE} where qr_code = %s order by record_id", (code,))
    out = cur.fetchall(); c.close(); return out

def wait_rows(code, n, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rs = rows_for(code)
        if len(rs) >= n: return rs
        time.sleep(0.3)
    return rows_for(code)

def image(label, seed):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    cv2.putText(a, label, (100, 360), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
    return a

# (column-name stem, FE picture kwarg or None, FE result kwarg, FE datetime kwarg)
STEPS = [
    ("check_knuckle", "knucklePicture", "knuckleResult", "knuckleDatetime"),
    ("check_hub_and_bottom_bearing", "hubAndBottomBearingPicture", "hubAndBottomBearingResult", "hubAndBottomBearingDatetime"),
    ("check_top_bearing", "topBearingPicture", "topBearingResult", "topBearingDatetime"),
    ("check_nut_and_platewasher", "nutAndPlateWasherPicture", "nutAndPlateWasherResult", "nutAndPlateWasherDatetime"),
    ("nut_tightening_torque_1", None, "tighteningTorque1Result", "tighteningTorque1Datetime"),
    ("check_bung_for_component_press", "componentPressBungCheckingPicture", "componentPressBungCheckingResult", "componentPressBungCheckDatetime"),
    ("component_press", None, "componentPressDone", "componentPressDoneDatetime"),
    ("check_no_bung", "noBungCheckingPicture", "noBungCheckingResult", "noBungCheckDatetime"),
    ("nut_tightening_torque_2", None, "tighteningTorque2Result", "tighteningTorque2Datetime"),
    ("check_splitpin_and_washer", "splitPinAndWasherPicture", "splitPinAndWasherResult", "splitPinAndWasherDatetime"),
    ("check_cap", "capCheckingPicture", "capCheckingResult", "capCheckingDatetime"),
    ("check_bung_cap_press", "bungCheckingPicture", "capPressBungCheckingResult", "capPressBungCheckDatetime"),
    ("cap_press", None, "pressDone", "capPressDoneDatetime"),
    ("free_rotation_torque_1", None, "freeRotationTorque1Result", "freeRotationTorque1Datetime"),
]
def result_col(stem): return {"component_press": "component_press_done", "cap_press": "cap_press_done"}.get(stem, f"{stem}_result")
def datetime_col(stem): return f"{stem}_datetime"

def full_record(code, reached=len(STEPS), seed=0):
    """FE record with the first `reached` steps done OK (distinct images, distinct datetimes); the rest as the
    frontend sends them after an abort: no picture, NOT OK, 1970 datetime, torque 0.0."""
    kw, expected, images = {"qrCode": code}, {}, {}
    for i, (stem, pic, res, dt) in enumerate(STEPS):
        done = i < reached
        when = f"2026-10-30 10:{i:02d}:{i:02d}" if done else "1970-01-01 00:00:00"
        if pic:
            kw[pic] = image(stem, seed * 100 + i) if done else None
            if done: images[stem] = kw[pic]
        kw[res] = ok if done else notok
        kw[dt] = when
        expected[result_col(stem)] = OKS if done else NOTOKS
        expected[datetime_col(stem)] = when
    torques = {"tighteningTorque1": ("nut_tightening_torque_1", 19.08), "tighteningTorque2": ("nut_tightening_torque_2", 19.03),
               "freeRotationTorque1": ("free_rotation_torque_1", 2.41)}
    for k, (col, v) in torques.items():
        step = [s[0] for s in STEPS].index(col)
        kw[k] = v if step < reached else 0.0
        expected[col] = kw[k]
    kw["overallResult"] = ok if reached == len(STEPS) else notok
    if reached == len(STEPS):        # rotation during tightening done, sent with each tightening torque
        kw["tighteningTorque1RotationDone"], kw["tighteningTorque2RotationDone"] = True, False
    expected["ok_notok_result"] = OKS if reached == len(STEPS) else NOTOKS
    return kw, expected, images

CREATED_QRS, CREATED_FILES = [], []

# =====================================================================================================
print("\n=== 1. Database set-up ===", flush=True)
name = P.setDatabaseName("Test")
check("1 setDatabaseName('Test') -> ashok_leyland_test", P.getDatabaseName() == DB, str(name))
check("1 database exists", P.checkIfDatabaseExists(DB))
c = conn(); cur = c.cursor()
cur.execute("select column_name from information_schema.columns where table_schema=%s and table_name=%s", (SCHEMA, TABLE))
cols = {r[0] for r in cur.fetchall()}
m2_cols = {"check_bung_for_component_press_result", "component_press_done", "check_no_bung_result", "nut_tightening_torque_2",
           "check_bung_cap_press_result", "cap_press_done", "free_rotation_torque_1", "free_rotation_torque_1_result"}
check("1 table has the Machine 2 columns", m2_cols <= cols, str(sorted(m2_cols - cols)))
check("1 rotation-during-tightening columns present (boolean)",
      {"nut_tightening_torque_1_rotation_done", "nut_tightening_torque_2_rotation_done"} <= cols)
check("1 dropped free-rotation columns are absent", not ({"free_rotations_done", "free_rotations_datetime"} & cols))
cur.execute(f"select role_name from {SCHEMA}.roles"); roles = {r[0] for r in cur.fetchall()}
check("1 roles Administrator / Supervisor / Operator exist", {"Administrator", "Supervisor", "Operator"} <= roles, str(roles))
cur.execute(f"select count(*) from {SCHEMA}.users where username='admin'")
check("1 admin user exists", cur.fetchone()[0] == 1)
c.close()

# =====================================================================================================
print("\n=== 2-3. Direct insertData() and checkIfQRCodeExists() ===", flush=True)
code = qr(QR_RHS_PLUS, 1); CREATED_QRS.append(code)
model, side, tonnage = "DOSTPLUS", "RHS", "2.5T"
direct = dict(qr_code=code, model_name=model, lhs_rhs=side, model_tonnage=tonnage,
              component_assembly_start_datetime="2026-10-30 09:00:00",
              check_knuckle_imagefile="k.png", check_knuckle_result="ok", check_knuckle_datetime="2026-10-30 09:00:01",
              nut_tightening_torque_1=19.08, nut_tightening_torque_1_result="OK", nut_tightening_torque_1_datetime="2026-10-30 09:00:02",
              check_cap_result="notok", nut_tightening_torque_2="bad-value",
              nut_tightening_torque_1_rotation_done=True,
              ok_notok_result="Not OK", username="admin", remarks="direct")
check("2 insertData() returns True", P.insertData(db_name=DB, **direct))
rs = rows_for(code)
check("2 exactly one row stored", len(rs) == 1, str(len(rs)))
if rs:
    row = rs[0]
    want = {"model_name": model, "lhs_rhs": side, "model_tonnage": tonnage, "check_knuckle_imagefile": "k.png",
            "check_knuckle_result": OKS, "nut_tightening_torque_1": 19.08, "nut_tightening_torque_1_result": OKS,
            "check_cap_result": NOTOKS, "check_top_bearing_result": "Not Checked", "nut_tightening_torque_2": -1.0,
            "ok_notok_result": NOTOKS, "username": "admin", "remarks": "direct"}
    bad = {k: (row[k], v) for k, v in want.items() if row[k] != v}
    check("2 stored values (results normalised; unparsable torque -> -1.0; unset step 'Not Checked')", bad == {}, str(bad))
    check("2 rotation done stored: torque 1 True, torque 2 not given -> NULL",
          (row["nut_tightening_torque_1_rotation_done"], row["nut_tightening_torque_2_rotation_done"]) == (True, None),
          str((row["nut_tightening_torque_1_rotation_done"], row["nut_tightening_torque_2_rotation_done"])))
    check("2 datetimes stored", str(row["check_knuckle_datetime"]) == "2026-10-30 09:00:01"
          and str(row["component_assembly_start_datetime"]) == "2026-10-30 09:00:00" and row["created_on"] is not None)
check("3 checkIfQRCodeExists() true for the stored code", P.checkIfQRCodeExists(qr_code=code, db_name=DB))
check("3 checkIfQRCodeExists() false for an unknown code", not P.checkIfQRCodeExists(qr_code=qr(QR_LHS, 999), db_name=DB))

# =====================================================================================================
print("\n=== 4-6. FE record -> fe2db.q -> DBServer ===", flush=True)
r = Redis('localhost', 6379); clearQueues(r)
okBefore = P.getComponentsProducedToday(db_name=DB)
dp = DataPersistence(mode="Test", username="admin")
time.sleep(2)

code4 = qr(QR_LHS, 4); CREATED_QRS.append(code4)
kw, expected, images = full_record(code4, seed=4)
SETTINGS4 = {"rotation_rpm": 33.0, "tightening_torque1_setpoint": 18.75, "tightening_torque2_setpoint": 17.5,
             "no_of_rotation1_ccw": 2.0, "no_of_rotation1_cw": 1.0}
publishCycleSettingsForQRCode(r, code4, SETTINGS4)          # as the IOServer does in state 2
check("4 FE record sent", sendDataFromFEServerToDatabaseServer(r, **kw))
rs = wait_rows(code4, 1)
check("4 stored by DBServer", len(rs) == 1, f"{len(rs)} rows")
if rs:
    row = rs[0]
    bad = {}
    for col, v in expected.items():
        got = row[col]
        got = str(got) if col.endswith("_datetime") else got
        if got != v: bad[col] = (got, v)
    check(f"4 all {len(expected)} result / torque / datetime / overall fields stored exactly", bad == {}, str(bad))
    check("4 model / side / tonnage from the QR code", (row["model_name"], row["lhs_rhs"], row["model_tonnage"]) == ("DOST", "LHS", "2.0T"),
          str((row["model_name"], row["lhs_rhs"], row["model_tonnage"])))
    check("4 rotation done with torque 1 / 2 stored from the FE record (True / False)",
          (row["nut_tightening_torque_1_rotation_done"], row["nut_tightening_torque_2_rotation_done"]) == (True, False),
          str((row["nut_tightening_torque_1_rotation_done"], row["nut_tightening_torque_2_rotation_done"])))
    stored = {k: row[f"setting_{k}"] for k in SETTINGS4}
    check("4 machine settings used for the component stored with it (setting_* columns)", stored == SETTINGS4, str(stored))
    check("4 username admin, remarks empty", row["username"] == "admin" and (row["remarks"] or "") == "", str((row["username"], row["remarks"])))
    paths = {stem: row[f"{stem}_imagefile"] for stem in images}
    CREATED_FILES += [p for p in paths.values() if p]
    check("4 nine image paths, all distinct", len(paths) == 9 and len(set(paths.values())) == 9, str(paths))
    mism = [stem for stem, p in paths.items() if not (p and os.path.isfile(p) and np.array_equal(cv2.imread(p), images[stem]))]
    check("4 each image file exists and is pixel-identical to the image sent", mism == [], str(mism))
    check("4 model sub-folder used (…/DOST/…)", all("/DOST/" in p for p in paths.values()))

kw, _, _ = full_record(code4, seed=5)
sendDataFromFEServerToDatabaseServer(r, **kw)
rs = wait_rows(code4, 2)
check("5 same QR again -> second row", len(rs) == 2, f"{len(rs)} rows")
if len(rs) == 2:
    check("5 second row remarks = DUPLICATE, first row unchanged", rs[1]["remarks"] == "DUPLICATE" and (rs[0]["remarks"] or "") == "",
          str([x["remarks"] for x in rs]))
    CREATED_FILES += [rs[1][f"{s}_imagefile"] for s in images if rs[1][f"{s}_imagefile"]]

code6 = qr(QR_LHS, 6); CREATED_QRS.append(code6)
kw, expected, images = full_record(code6, reached=4, seed=6)        # aborted after the nut & washer check
sendDataFromFEServerToDatabaseServer(r, **kw)
rs = wait_rows(code6, 1)
check("6 aborted-cycle record stored", len(rs) == 1)
if rs:
    row = rs[0]
    bad = {}
    for col, v in expected.items():
        got = row[col]; got = str(got) if col.endswith("_datetime") else got
        if got != v: bad[col] = (got, v)
    check("6 reached steps OK, unreached NOTOK with 1970 datetimes, torques 0.0, overall NOTOK", bad == {}, str(bad))
    check("6 aborted before the tightening steps -> rotation-done columns NULL",
          (row["nut_tightening_torque_1_rotation_done"], row["nut_tightening_torque_2_rotation_done"]) == (None, None))
    check("6 no settings published for this QR -> setting_* columns NULL",
          all(row[f"setting_{k}"] is None for k in P.MACHINE_SETTINGS_KEYS))
    unreached_files = {s[0]: row[f"{s[0]}_imagefile"] for s in STEPS[4:] if s[1]}
    check("6 no image file for the unreached picture steps", all(not v for v in unreached_files.values()), str(unreached_files))
    CREATED_FILES += [row[f"{s}_imagefile"] for s in images if row[f"{s}_imagefile"]]

# 6b (2026-11-13): component press and second tightening bypassed (Machine Setting per model) - as the frontend sends it
check("6b record column bypassed_press_and_tightening2 (boolean) present", "bypassed_press_and_tightening2" in cols)
code6b = qr(QR_LHS, 7); CREATED_QRS.append(code6b)
kw, expected, images = full_record(code6b, seed=7)
SKIPPED_STEPS = ("check_bung_for_component_press", "component_press", "check_no_bung", "nut_tightening_torque_2")
for stem, pic, res, dt in STEPS:
    if stem in SKIPPED_STEPS:
        if pic: kw[pic] = None; images.pop(stem, None)
        kw[res] = "Bypassed"; kw[dt] = ""
        expected[result_col(stem)] = "Bypassed"; expected[datetime_col(stem)] = "None"
kw["tighteningTorque2"] = -1.0; expected["nut_tightening_torque_2"] = -1.0
kw["tighteningTorque2RotationDone"] = None
sendDataFromFEServerToDatabaseServer(r, bypassedPressAndTightening2=True, **kw)
rs = wait_rows(code6b, 1)
check("6b bypassed record stored", len(rs) == 1)
if rs:
    row = rs[0]
    bad = {}
    for col, v in expected.items():
        got = row[col]; got = str(got) if col.endswith("_datetime") else got
        if got != v: bad[col] = (got, v)
    check("6b 'Bypassed' + NULL datetimes for the 4 skipped steps, torque 2 -1.0, the rest OK, overall OK", bad == {}, str(bad))
    check("6b marked bypassed; rotation done with torque 2 NULL",
          row["bypassed_press_and_tightening2"] is True and row["nut_tightening_torque_2_rotation_done"] is None,
          str((row["bypassed_press_and_tightening2"], row["nut_tightening_torque_2_rotation_done"])))
    check("6b no image file for the two bung checks",
          not row["check_bung_for_component_press_imagefile"] and not row["check_no_bung_imagefile"])
    CREATED_FILES += [row[f"{s}_imagefile"] for s in images if row[f"{s}_imagefile"]]
rs6 = rows_for(code6)
check("6b an ordinary record is not marked bypassed", rs6 and rs6[0]["bypassed_press_and_tightening2"] is False)

# =====================================================================================================
print("\n=== 7. Reports and counters ===", flush=True)
today = P.getAllDataOfToday(db_name=DB)
check("7 getAllDataOfToday CSV contains today's records", all(c_ in today for c_ in (code4, code6)), f"{len(today)} chars")
check("7 CSV header has no free-rotation-done column", "free_rotations_done" not in today.splitlines()[0] if today else False)
check("7 CSV header has the two rotation-done columns", all(c_ in today.splitlines()[0] for c_ in
      ("nut_tightening_torque_1_rotation_done", "nut_tightening_torque_2_rotation_done")) if today else False)
byModel = P.getDataByModelName(modelName="DOST", db_name=DB)
check("7 getDataByModelName('DOST') contains the DOST records and not the DOSTPLUS one",
      code4 in byModel and code6 in byModel and code not in byModel)
d = time.strftime("%Y-%m-%d")
byModelDates = P.getDataByModelNameAndDateLimits(modelName="DOSTPLUS", db_name=DB, startDate=d, endDate=d)
check("7 getDataByModelNameAndDateLimits('DOSTPLUS', today) contains the DOSTPLUS record", code in byModelDates)
check("7 getUniqueModelNames includes DOST and DOSTPLUS", {"DOST", "DOSTPLUS"} <= set(P.getUniqueModelNames(db_name=DB)))
okAfter = P.getComponentsProducedToday(db_name=DB)
check("7 getComponentsProducedToday counts only the 3 OK rows added (the bypassed one included; not the NOTOK ones)",
      okAfter - okBefore == 3,
      f"{okBefore} -> {okAfter}")

# =====================================================================================================
print("\n=== 8. Clean-up ===", flush=True)
c = conn(); cur = c.cursor()
cur.execute(f"delete from {SCHEMA}.{TABLE} where qr_code = any(%s)", (CREATED_QRS,)); deleted = cur.rowcount; c.close()
for f in CREATED_FILES:
    try: os.remove(f)
    except OSError: pass
check("8 test rows deleted (1 direct + 2 duplicate + 1 aborted + 1 bypassed)", deleted == 5 and all(not rows_for(q_) for q_ in CREATED_QRS), f"{deleted} deleted")
check("8 test image files removed", not any(os.path.exists(f) for f in CREATED_FILES))

# =====================================================================================================
print("\n=== 9. Machine settings ===", flush=True)
c = conn(); cur = c.cursor()
cur.execute(f"select rotation_rpm, tightening_torque1_setpoint, tightening_torque2_setpoint, no_of_rotation1_ccw, "
            f"no_of_rotation1_cw, updated_by from {SCHEMA}.machine_settings order by setting_id limit 1")
first = cur.fetchone()
_d = P.DEFAULT_MACHINE_SETTINGS
check(f"9 default settings row ({_d['rotation_rpm']:g}, {_d['tightening_torque1_setpoint']:g}, {_d['tightening_torque2_setpoint']:g}, "
      f"{_d['no_of_rotation1_ccw']:g}, {_d['no_of_rotation1_cw']:g}, 'system (default)'); a database created before 2026-11-01 "
      f"has the earlier default 19 / 19 Nm: drop and recreate it",
      first == tuple(float(_d[k]) for k in P.MACHINE_SETTINGS_KEYS) + ("system (default)",), str(first))
before = P.getMachineSettings(DB)
check("9 getMachineSettings returns the 5 settings as floats", set(P.MACHINE_SETTINGS_KEYS) <= set(before)
      and all(isinstance(before[k], float) for k in P.MACHINE_SETTINGS_KEYS), str(before))
BAD = [dict(rotation_rpm=19), dict(rotation_rpm=41), dict(rotation_rpm=30.5),
       dict(tightening_torque1_setpoint=0), dict(tightening_torque1_setpoint=20), dict(tightening_torque2_setpoint=12.345),
       dict(no_of_rotation1_ccw=0), dict(no_of_rotation1_ccw=5), dict(no_of_rotation1_cw=1.5)]
rejected = []
for change in BAD:
    candidate = dict(P.DEFAULT_MACHINE_SETTINGS); candidate.update(change)
    rejected.append(bool(P.validateMachineSettings(candidate)) and
                    not P.insertNewMachineSettings(DB, updated_by="dryrun-db", **candidate))
check(f"9 {len(BAD)} out-of-range settings rejected by validateMachineSettings / insertNewMachineSettings", all(rejected), str(rejected))
check("9 nothing stored by the rejected attempts", P.getMachineSettings(DB)["setting_id"] == before["setting_id"])
db_rejects = []
for col, value in (("rotation_rpm", 45), ("tightening_torque1_setpoint", 20), ("no_of_rotation1_cw", 5)):
    vals = dict(P.DEFAULT_MACHINE_SETTINGS); vals[col] = value
    try:
        cur.execute(f"insert into {SCHEMA}.machine_settings (rotation_rpm, tightening_torque1_setpoint, "
                    f"tightening_torque2_setpoint, no_of_rotation1_ccw, no_of_rotation1_cw, updated_by) "
                    f"values (%s, %s, %s, %s, %s, 'dryrun-db')",
                    tuple(vals[k] for k in P.MACHINE_SETTINGS_KEYS))
        db_rejects.append(False)
    except psycopg2.errors.CheckViolation:
        db_rejects.append(True)
check("9 database CHECK constraints reject RPM 45, torque 20, count 5", all(db_rejects), str(db_rejects))
NEW = dict(rotation_rpm=25.0, tightening_torque1_setpoint=15.25, tightening_torque2_setpoint=16.5,
           no_of_rotation1_ccw=2.0, no_of_rotation1_cw=2.0)
check("9 valid change saved", P.insertNewMachineSettings(DB, updated_by="dryrun-db", **NEW))
now = P.getMachineSettings(DB)
check("9 the change is the settings in force, with updated_by", {k: now[k] for k in NEW} == NEW and now["updated_by"] == "dryrun-db", str(now))
MODELS = P.settingsModelNames()
check("9 models from qrcode.partmapping.N: DOST, DOSTPLUS", MODELS == ["DOST", "DOSTPLUS"], str(MODELS))
perModel = {mdl: P.getMachineSettings(DB, model_name=mdl) for mdl in MODELS}
check("9 a change saved without a model applies to every model",
      all({k: perModel[mdl][k] for k in NEW} == NEW and perModel[mdl]["model_name"] == mdl for mdl in MODELS), str(perModel))
DOST_ONLY = dict(rotation_rpm=35.0, tightening_torque1_setpoint=12.5, tightening_torque2_setpoint=18.0,
                 no_of_rotation1_ccw=3.0, no_of_rotation1_cw=4.0)
check("9 per model: DOST saved with rotation counts 3 / 4 (1 - 4 allowed)",
      P.insertNewMachineSettings(DB, updated_by="dryrun-db", model_name="DOST", **DOST_ONLY))
d, dp = P.getMachineSettings(DB, model_name="DOST"), P.getMachineSettings(DB, model_name="DOSTPLUS")
check("9 per model: DOST has its new settings, DOSTPLUS keeps its own",
      {k: d[k] for k in DOST_ONLY} == DOST_ONLY and {k: dp[k] for k in NEW} == NEW, f"{d} / {dp}")
history = P.getMachineSettingsHistory(DB, 2, model_name="DOST")
check("9 history of a model newest first: its latest change, then the one before",
      len(history) == 2 and history[0]["setting_id"] == d["setting_id"]
      and history[1]["setting_id"] == perModel["DOST"]["setting_id"], str(history))
check("9 bypass switch per model: off by default", d.get("bypass_press_and_tightening2") is False
      and dp.get("bypass_press_and_tightening2") is False, f"{d.get('bypass_press_and_tightening2')} / {dp.get('bypass_press_and_tightening2')}")
check("9 bypass switch saved for DOST only",
      P.insertNewMachineSettings(DB, updated_by="dryrun-db", model_name="DOST", bypass_press_and_tightening2=True, **DOST_ONLY)
      and P.getMachineSettings(DB, model_name="DOST")["bypass_press_and_tightening2"] is True
      and P.getMachineSettings(DB, model_name="DOSTPLUS")["bypass_press_and_tightening2"] is False)
assert P.insertNewMachineSettings(DB, updated_by="dryrun-db", model_name="DOST", **DOST_ONLY)   # off again
cur.execute(f"delete from {SCHEMA}.machine_settings where updated_by = 'dryrun-db'")
check("9 clean-up: settings in force back to what they were", P.getMachineSettings(DB)["setting_id"] == before["setting_id"])
cur.execute(f"select count(*) from {SCHEMA}.machine_settings where model_name is null")
legacy = cur.fetchone()[0]
check("9 every model has settings after the clean-up (copied / defaults when the database is set up)",
      all(P.getMachineSettings(DB, model_name=mdl) for mdl in MODELS), str(legacy))
c.close()

# ---------------- 10. Bunk -> Bung column rename (CHANGES 125) ----------------
print("\n=== 10. A database made before 2026-11-17 (columns named 'bunk'): renamed at set-up, data kept ===", flush=True)
MIG_DB = "ashok_leyland_bung_migration_dryrun"
OLD_NEW = [(n.replace("bung", "bunk"), n) for n in (
    "check_bung_for_component_press_imagefile", "check_bung_for_component_press_result",
    "check_bung_for_component_press_datetime", "check_no_bung_imagefile", "check_no_bung_result", "check_no_bung_datetime",
    "check_bung_cap_press_imagefile", "check_bung_cap_press_result", "check_bung_cap_press_datetime")]
adm = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database="postgres"); adm.autocommit = True
adm.cursor().execute(f"drop database if exists {MIG_DB}"); adm.cursor().execute(f"create database {MIG_DB}")
P.createSchema(db_name=MIG_DB); P.createAllTables(db_name=MIG_DB)
P.insertRole(role_name="Administrator", db_name=MIG_DB)
P.insertNewUser(username="admin", password="admin123", first_name="Administrator", middle_name="", last_name="", email="",
                role_name="Administrator", mobile="", db_name=MIG_DB)
m = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database=MIG_DB); m.autocommit = True
mc = m.cursor()
for old, new in OLD_NEW:                       # make it look like a database from before the rename
    mc.execute(f"alter table {SCHEMA}.hub_and_disc_assembly_data rename column {new} to {old}")
mc.execute(f"insert into {SCHEMA}.hub_and_disc_assembly_data (qr_code, check_bunk_for_component_press_result, "
           f"check_no_bunk_result, check_bunk_cap_press_result, check_bunk_cap_press_imagefile, created_on, username) "
           f"values ('MIG-QR', 'OK', 'NOTOK', 'OK', 'old/7-BUNKAndNoBUNK/x.png', now(), 'admin')")
def columns():
    mc.execute("select column_name from information_schema.columns where table_schema = %s and table_name = "
               "'hub_and_disc_assembly_data'", (SCHEMA,))
    return {r[0] for r in mc.fetchall()}
check("10 before: the old 'bunk' columns", all(o in columns() for o, _ in OLD_NEW))
P.createAllTables(db_name=MIG_DB)              # what setDatabaseName() runs at every start
cols = columns()
check("10 after set-up: every 'bunk' column renamed to 'bung', none left",
      all(n in cols and o not in cols for o, n in OLD_NEW) and not [x for x in cols if "bunk" in x], sorted(x for x in cols if "bu" in x))
mc.execute(f"select check_bung_for_component_press_result, check_no_bung_result, check_bung_cap_press_result, "
           f"check_bung_cap_press_imagefile from {SCHEMA}.hub_and_disc_assembly_data where qr_code = 'MIG-QR'")
check("10 the old row's values kept under the new names", mc.fetchone() == ("OK", "NOTOK", "OK", "old/7-BUNKAndNoBUNK/x.png"))
P.createAllTables(db_name=MIG_DB)
check("10 set-up again (next start): nothing changes", columns() == cols)
m.close(); adm.cursor().execute(f"drop database if exists {MIG_DB}"); adm.close()

failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
os._exit(1 if failed else 0)
