"""
DryRun_Endpoints - the web service (endpoints/AshokLeylandWebService.py) and the MI dashboard
(endpoints/AshokLeyland_MI_Light.py) against the TEST database (ashok_leyland_test).

  1. Adds about 120 components spread over the last 100 days to ashok_leyland_test, all with QR codes starting
     DRYRUN-ENDPOINTS- (models DRYRUN-A / DRYRUN-B, OK and NOT OK, torques with some not measured (-1), two torque 1
     setpoints, some re-assembled = remarks DUPLICATE). Other records are not touched.
  2. Starts both services on free ports in this process and checks every API method (JSON and CSV, the page's own
     routes, the no-format routes, the error answers) and the dashboard data against counts made directly in SQL.
  3. Deletes its records again (also when a check fails). Production is only read (one model-list call).

Needs PostgreSQL with the test database (it is created if missing). No Redis, PLC or camera.
Run from the project root:   python test/machine1_dryrun/DryRun_Endpoints.py
Prints PASS / FAIL per check and RESULT n/m; exit code 0 = all passed.
"""
import os
import pathlib
import random
import sys
import threading
import time
from datetime import date, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import csv
import io
import socket

import psycopg2
import requests

import persistence.Persistence as P
from endpoints import EndpointsDB as DB

TAG = "DRYRUN-ENDPOINTS-"
MODELS = ("DRYRUN-A", "DRYRUN-B")
TABLE = f"{P.schema_name}.hub_and_disc_assembly_data"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)


def connection():
    return psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", port="5432",
                            database=DB.DATABASES["test"])


def sqlCount(where, params=()):
    c = connection()
    try:
        with c.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {TABLE} WHERE qr_code LIKE %s AND " + where, (TAG + "%",) + params)
            return cursor.fetchone()[0]
    finally:
        c.close()


def seed():
    random.seed(11)
    c = connection()
    now, n = datetime.now(), 0
    try:
        with c.cursor() as cursor:
            cursor.execute(f"DELETE FROM {TABLE} WHERE qr_code LIKE %s", (TAG + "%",))
            for day in range(0, 100, 1):
                for k in range(random.choice((0, 1, 1, 2)) if day else 3):
                    created = (now - timedelta(days=day)).replace(hour=1, minute=0, second=0, microsecond=0) + \
                              timedelta(minutes=20 * k + 1)
                    created = min(created, now - timedelta(seconds=10 * (k + 1)))
                    ok = random.random() > 0.15
                    # an aborted cycle is NOT OK; emergency button or manual abort (2026-11-08)
                    aborted = (not ok) and random.random() < 0.5
                    reason = (random.choice(("emergency button pressed on the machine",
                                             "manual abort (Ctrl+Alt+Z) in the frontend")) if aborted else None)
                    n += 1
                    bypassed = (not aborted) and n % 5 == 0     # component press + second tightening bypassed (2026-11-13)
                    t1 = round(random.gauss(15.2, 0.25), 2) if random.random() > 0.08 else -1.0
                    t2 = -1.0 if bypassed else round(random.gauss(19.1, 0.3), 2)
                    rot = round(random.gauss(2.4, 0.15), 2)
                    # each torque's time: measured -> the cycle's; bypassed torque 2 -> NULL; as the frontend records
                    # an aborted cycle (CHANGES 127): steps not reached 0.0 with the placeholder 1970-01-01 00:00:00
                    epoch = datetime(1970, 1, 1)
                    w1, w2, wr = created, (None if bypassed else created), created
                    if aborted:
                        reached = n % 3                          # 0: before torque 1, 1: after torque 1, 2: after torque 2
                        if reached < 1:
                            t1, w1 = 0.0, epoch
                        if reached < 2:
                            t2, w2 = 0.0, epoch
                        rot, wr = 0.0, epoch
                    cursor.execute(
                        f"INSERT INTO {TABLE} (qr_code, model_name, lhs_rhs, model_tonnage, nut_tightening_torque_1, "
                        f"nut_tightening_torque_2, free_rotation_torque_1, setting_tightening_torque1_setpoint, "
                        f"setting_tightening_torque2_setpoint, ok_notok_result, username, remarks, created_on, "
                        f"aborted, abort_reason, bypassed_press_and_tightening2, nut_tightening_torque_1_datetime, "
                        f"nut_tightening_torque_2_datetime, free_rotation_torque_1_datetime) "
                        f"VALUES (%s, %s, 'LHS', '2.5T', %s, %s, %s, %s, 19.0, %s, 'admin', %s, %s, %s, %s, %s, %s, %s, %s)",
                        (f"{TAG}{n:05d}", random.choice(MODELS), t1, t2, rot,
                         15.0 if day > 40 else 15.5, DB.OK_VALUE if ok else "NotOK",
                         "DUPLICATE" if random.random() < 0.08 else "", created, aborted, reason, bypassed,
                         w1, w2, wr))
        c.commit()
    finally:
        c.close()
    return n


def cleanup():
    c = connection()
    try:
        with c.cursor() as cursor:
            cursor.execute(f"DELETE FROM {TABLE} WHERE qr_code LIKE %s", (TAG + "%",))
            removed = cursor.rowcount
        c.commit()
    finally:
        c.close()
    print(f"\nRemoved the {removed} dry-run records from {DB.DATABASES['test']}", flush=True)


def freePort():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def startServices():
    from http.server import ThreadingHTTPServer
    from endpoints import AshokLeylandWebService as WS
    from endpoints import AshokLeyland_MI_Light as MI
    from werkzeug.serving import make_server
    wsPort, miPort = freePort(), freePort()
    ws = make_server("127.0.0.1", wsPort, WS.app, threaded=True)
    mi = ThreadingHTTPServer(("127.0.0.1", miPort), MI.DashboardHandler)
    for server in (ws, mi):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{wsPort}", f"http://127.0.0.1:{miPort}"


def webServiceChecks(B):
    print("\n=== Web service ===", flush=True)
    T = {"db_name": "test"}
    names = requests.post(B + "/api/get_unique_model_names/json/", json=T).json().get("model_names", [])
    check("get_unique_model_names: the dry-run models are listed", set(MODELS) <= set(names), names)
    r = requests.post(B + "/api/get_unique_model_names/csv/", json=T)
    check("get_unique_model_names csv", r.ok and r.text.splitlines()[0] == "model_name" and MODELS[0] in r.text)
    n = sqlCount("model_name = %s", (MODELS[0],))
    r = requests.post(B + "/api/get_data_by_model_name/json/", json={**T, "model_name": MODELS[0].lower()})
    data = [x for x in r.json().get("data", []) if x["qr_code"].startswith(TAG)]
    check(f"get_data_by_model_name json, case ignored: {len(data)} = SQL {n}", r.ok and len(data) == n)
    check("JSON records carry the table's columns", data and set(DB.COLUMNS) == set(data[0]),
          sorted(set(DB.COLUMNS) ^ set(data[0])) if data else "no data")
    r = requests.post(B + "/api/get_data_by_model_name/csv/", json={**T, "model_name": MODELS[0]})
    rows = [x for x in csv.reader(io.StringIO(r.text))]
    check("get_data_by_model_name csv: header = the columns, same rows",
          r.ok and rows[0] == DB.COLUMNS and len([x for x in rows[1:] if x[0].startswith(TAG)]) == n)
    today = date.today()
    d1, d2 = today - timedelta(days=20), today - timedelta(days=5)
    lo, hi = datetime.combine(d1, datetime.min.time()), datetime.combine(d2 + timedelta(days=1), datetime.min.time())
    n = sqlCount("model_name = %s AND created_on >= %s AND created_on < %s", (MODELS[1], lo, hi))
    for route, body in (("get_data_by_model_name_and_date_limits",
                         {"model_name": MODELS[1], "start_date": str(d1), "end_date": str(d2)}),
                        ("by_model_name_and_date", {"modelName": MODELS[1], "startDate": str(d1), "endDate": str(d2)})):
        r = requests.post(B + f"/api/{route}/json/", json={**T, **body})
        got = [x for x in r.json().get("data", []) if x["qr_code"].startswith(TAG)]
        check(f"{route} {d1}..{d2} (both days included): {len(got)} = SQL {n}", r.ok and len(got) == n)
    n = sqlCount("created_on >= %s AND created_on < %s", (lo, hi))
    for route, body in (("get_data_by_date_limits", {"start_date": str(d1), "end_date": str(d2)}),
                        ("by_date_limits", {"startDate": str(d1), "endDate": str(d2)})):
        r = requests.post(B + f"/api/{route}/csv/", json={**T, **body})
        got = [x for x in r.text.splitlines()[1:] if x.startswith(TAG)]
        check(f"{route} csv: {len(got)} = SQL {n}", r.ok and len(got) == n)
    lo = datetime.combine(today, datetime.min.time())
    n = sqlCount("created_on >= %s", (lo,))
    for route in ("get_all_data_of_today", "all_today"):
        r = requests.post(B + f"/api/{route}/json/", json=T)
        got = [x for x in r.json().get("data", []) if x["qr_code"].startswith(TAG)]
        check(f"{route}: {len(got)} = SQL {n} (> 0)", r.ok and len(got) == n and n > 0)
    for route, key in (("get_data_of_today_by_model_number", "model_name"), ("today_by_model", "model_name")):
        n = sqlCount("model_name = %s AND created_on >= %s", (MODELS[0], lo))
        r = requests.post(B + f"/api/{route}/json/", json={**T, key: MODELS[0]})
        got = [x for x in r.json().get("data", []) if x["qr_code"].startswith(TAG)]
        check(f"{route}: {len(got)} = SQL {n}", r.ok and len(got) == n)
    r = requests.post(B + "/api/get_data_by_model_name", json={**T, "model_name": MODELS[0]})
    check("no-format route: JSON with the CSV text in data", r.ok and r.json()["data"].startswith(DB.COLUMNS[0] + ","))
    for name, route, body, status in (
            ("missing model_name", "get_data_by_model_name/json/", T, 400),
            ("bad date", "get_data_by_date_limits/json/", {**T, "start_date": "2026-13-01", "end_date": "2026-11-01"}, 400),
            ("end date before start date", "get_data_by_date_limits/json/", {**T, "start_date": "2026-11-02", "end_date": "2026-11-01"}, 400),
            ("unknown database", "get_all_data_of_today/json/", {"db_name": "postgres"}, 400),
            ("unknown format", "get_all_data_of_today/xml/", T, 400),
            ("unknown method", "no_such_method/json/", T, 400)):
        r = requests.post(B + "/api/" + route, json=body)
        check(f"{name} -> HTTP {status}", r.status_code == status, (r.status_code, r.text[:120]))
    r = requests.get(B + "/", headers={"Host": "192.168.1.50:8000"})
    check("query page renders; API examples use the address the page was opened with",
          r.ok and "http://192.168.1.50:8000/api/get_data_by_model_name_and_date_limits/json/" in r.text)
    r = requests.get(B + "/static/app.js")
    check("page script calls the service it came from (no localhost)", r.ok and "localhost" not in r.text)


def abortFilterChecks(B):
    print("\n=== Aborted cycles: filters on the web service ===", flush=True)
    d1, d2 = (date.today() - timedelta(days=60)).isoformat(), date.today().isoformat()
    s, e = datetime.combine(date.today() - timedelta(days=60), datetime.min.time()), \
        datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
    base = "created_on >= %s AND created_on < %s"
    cases = [  # (aborts, result, SQL condition, label)
        (None, None, "true", "default = every record"),
        ("include", None, "true", "include"),
        ("only", None, "aborted", "only aborts"),
        ("exclude", None, "NOT aborted", "no aborts"),
        ("emergency", None, "aborted AND abort_reason ILIKE 'emergency%%'", "only emergency-button aborts"),
        ("manual", None, "aborted AND abort_reason ILIKE 'manual%%'", "only manual aborts"),
        (None, "ok", "ok_notok_result = 'OK'", "result OK"),
        (None, "notok", "ok_notok_result <> 'OK'", "result NOT OK (aborts included)"),
        ("exclude", "notok", "NOT aborted AND ok_notok_result <> 'OK'", "completed NOT OK (rejects)"),
        ("exclude", "ok", "NOT aborted AND ok_notok_result = 'OK'", "completed OK"),
        ("only", "ok", "aborted AND ok_notok_result = 'OK'", "aborted and OK (none)"),
        ("no_aborts", None, "NOT aborted", "alias no_aborts"),
        ("ALL", None, "true", "alias ALL (case ignored)"),
    ]
    for aborts, result, cond, label in cases:
        body = {"db_name": "test", "start_date": d1, "end_date": d2}
        if aborts is not None: body["aborts"] = aborts
        if result is not None: body["result"] = result
        r = requests.post(B + "/api/get_data_by_date_limits/json/", json=body)
        got = [x for x in r.json().get("data", []) if x["qr_code"].startswith(TAG)] if r.ok else []
        n = sqlCount(f"{base} AND {cond}", (s, e))
        check(f"by dates, {label}: {len(got)} = SQL {n}", r.ok and len(got) == n, r.text[:150])
    r = requests.post(B + "/api/get_data_by_date_limits/json/", json={"db_name": "test", "start_date": d1, "end_date": d2, "aborts": "only"})
    rows = [x for x in r.json()["data"] if x["qr_code"].startswith(TAG)]
    check("aborted records carry aborted = true and their reason", rows and all(x["aborted"] is True and x["abort_reason"] for x in rows))
    for method, extra in (("get_data_by_model_name", {"model_name": MODELS[0]}),
                          ("get_data_by_model_name_and_date_limits", {"model_name": MODELS[0], "start_date": d1, "end_date": d2}),
                          ("get_data_of_today_by_model_number", {"model_name": MODELS[0]}),
                          ("get_all_data_of_today", {})):
        r = requests.post(B + f"/api/{method}/csv/", json={"db_name": "test", "aborts": "exclude", **extra})
        lines = [l for l in r.text.splitlines()[1:] if l.startswith(TAG)]
        idx = DB.COLUMNS.index("aborted")
        check(f"{method} csv, aborts exclude: no aborted record", r.ok and all(l.split(",")[idx] == "False" for l in lines),
              r.text[:120])
    r = requests.post(B + "/api/by_date_limits/json/", json={"db_name": "test", "startDate": d1, "endDate": d2, "aborts": "only", "result": "notok"})
    check("page route (by_date_limits) takes the filters too",
          r.ok and len([x for x in r.json()["data"] if x["qr_code"].startswith(TAG)]) == sqlCount(f"{base} AND aborted", (s, e)))
    for body, label in (({"aborts": "sometimes"}, "unknown aborts filter"), ({"result": "maybe"}, "unknown result filter")):
        r = requests.post(B + "/api/get_all_data_of_today/json/", json={"db_name": "test", **body})
        check(f"{label} -> HTTP 400 naming the choices", r.status_code == 400 and "Use one of" in r.json().get("error", ""), r.text[:120])
    r = requests.get(B + "/")
    check("query page has the Aborted cycles and Result selectors", r.ok and 'id="aborts"' in r.text and 'id="result"' in r.text)


def bypassFilterChecks(B, M):
    print("\n=== Bypassed cycles (component press and second tightening): filters ===", flush=True)
    d1, d2 = (date.today() - timedelta(days=60)).isoformat(), date.today().isoformat()
    s, e = datetime.combine(date.today() - timedelta(days=60), datetime.min.time()), \
        datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
    base = "created_on >= %s AND created_on < %s"
    check("seed has bypassed records", sqlCount(f"{base} AND bypassed_press_and_tightening2", (s, e)) > 0)
    for bypassed, aborts, cond, label in (
            (None, None, "true", "default = every record"),
            ("only", None, "bypassed_press_and_tightening2", "only bypassed"),
            ("exclude", None, "NOT bypassed_press_and_tightening2", "no bypassed"),
            ("bypassed", None, "bypassed_press_and_tightening2", "alias bypassed"),
            ("only", "exclude", "bypassed_press_and_tightening2 AND NOT aborted", "bypassed, no aborts")):
        body = {"db_name": "test", "start_date": d1, "end_date": d2}
        if bypassed is not None: body["bypassed"] = bypassed
        if aborts is not None: body["aborts"] = aborts
        r = requests.post(B + "/api/get_data_by_date_limits/json/", json=body)
        got = [x for x in r.json().get("data", []) if x["qr_code"].startswith(TAG)] if r.ok else []
        n = sqlCount(f"{base} AND {cond}", (s, e))
        check(f"by dates, {label}: {len(got)} = SQL {n}", r.ok and len(got) == n, r.text[:150])
    r = requests.post(B + "/api/get_data_by_date_limits/json/", json={"db_name": "test", "start_date": d1, "end_date": d2,
                                                                     "bypassed": "only"})
    rows = [x for x in r.json()["data"] if x["qr_code"].startswith(TAG)]
    check("bypassed records carry bypassed_press_and_tightening2 = true", rows and all(x["bypassed_press_and_tightening2"] is True for x in rows))
    r = requests.post(B + "/api/get_all_data_of_today/json/", json={"db_name": "test", "bypassed": "sometimes"})
    check("unknown bypassed filter -> HTTP 400 naming the choices", r.status_code == 400 and "Use one of" in r.json().get("error", ""))
    check("query page has the Bypassed selector", 'id="bypassed"' in requests.get(B + "/").text)
    start = date.today() - timedelta(days=29)
    for bypassed, cond in (("only", "bypassed_press_and_tightening2"), ("exclude", "NOT bypassed_press_and_tightening2")):
        r2 = requests.get(M + "/data", params={"db": "test", "model": MODELS[0], "day_period": "30_days", "bypassed": bypassed})
        total = sum(sum(x["y"]) for x in r2.json()["components_day"]["data"]) if r2.ok else -1
        check(f"[{MODELS[0]}] dashboard bypassed={bypassed}: 30-day total = SQL",
              r2.ok and total == sqlCount(f"model_name = %s AND created_on >= %s AND {cond}", (MODELS[0], start)), total)
    r2 = requests.get(M + "/data", params={"db": "test", "model": MODELS[0], "torque2_period": "90_days"})
    values = r2.json()["torque2"]["data"][0]["x"] if r2.ok and r2.json()["torque2"]["data"] else []
    check("dashboard torque 2: bypassed records (-1.0) left out", values and min(values) >= 0)
    check("unknown bypassed filter on the dashboard -> HTTP 400",
          requests.get(M + "/data", params={"db": "test", "bypassed": "x"}).status_code == 400)
    check("dashboard page has the Bypassed selector", 'id="bypassed"' in requests.get(M + "/").text)


UNREACHED_SEEN = []           # aborted cycles' 0.0 torques per model / chart (the seed must have some)


def dashboardChecks(B):
    print("\n=== MI dashboard ===", flush=True)
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for model in MODELS:
        r = requests.get(B + "/data", params={"db": "test", "model": model, "torque1_period": "90_days",
                                              "day_period": "30_days", "week_period": "12_weeks"})
        d = r.json()
        check(f"[{model}] /data answers; model list of the test database", r.ok and model in d.get("models", []))
        s = d["summary"]
        check(f"[{model}] today / this month = SQL",
              s["today"]["total"] == sqlCount("model_name = %s AND created_on >= %s", (model, today)) and
              s["month"]["ok"] == sqlCount("model_name = %s AND created_on >= %s AND ok_notok_result = %s",
                                           (model, today.replace(day=1), DB.OK_VALUE)), s)
        day = d["components_day"]["data"]
        start = today - timedelta(days=29)
        check(f"[{model}] per day, 30 days: OK, NOT OK (completed) and ABORTED = SQL",
              [x["name"] for x in day] == ["OK", "NOT OK", "ABORTED"] and
              sum(day[0]["y"]) == sqlCount("model_name = %s AND created_on >= %s AND ok_notok_result = %s AND NOT aborted",
                                           (model, start, DB.OK_VALUE)) and
              sum(day[1]["y"]) == sqlCount("model_name = %s AND created_on >= %s AND ok_notok_result <> %s AND NOT aborted",
                                           (model, start, DB.OK_VALUE)) and
              sum(day[2]["y"]) == sqlCount("model_name = %s AND created_on >= %s AND aborted", (model, start)),
              [(x["name"], sum(x["y"])) for x in day])
        check(f"[{model}] summary this month: OK + NOT OK + ABORTED = total, aborted = SQL",
              s["month"]["ok"] + s["month"]["notok"] + s["month"]["aborted"] == s["month"]["total"] and
              s["month"]["aborted"] == sqlCount("model_name = %s AND created_on >= %s AND aborted",
                                                (model, today.replace(day=1))), s["month"])
        ab = d["aborted_components"]["data"]
        check(f"[{model}] Aborted Cycles tab, 30 days: emergency / manual per day = SQL",
              sum(sum(x["y"]) for x in ab if x["name"] == "Emergency button") ==
              sqlCount("model_name = %s AND created_on >= %s AND aborted AND abort_reason ILIKE %s", (model, start, "emergency%")) and
              sum(sum(x["y"]) for x in ab if x["name"] == "Manual (frontend)") ==
              sqlCount("model_name = %s AND created_on >= %s AND aborted AND abort_reason ILIKE %s", (model, start, "manual%")),
              [(x["name"], sum(x["y"])) for x in ab])
        for aborts, cond in (("only", "aborted"), ("exclude", "NOT aborted"),
                             ("emergency", "aborted AND abort_reason ILIKE 'emergency%%'")):
            r2 = requests.get(B + "/data", params={"db": "test", "model": model, "day_period": "30_days", "aborts": aborts})
            d2 = r2.json()
            total = sum(sum(x["y"]) for x in d2["components_day"]["data"])
            check(f"[{model}] dashboard aborts={aborts}: 30-day total = SQL",
                  r2.ok and total == sqlCount(f"model_name = %s AND created_on >= %s AND {cond}", (model, start)), total)
        week = d["components_week"]["data"][0]["x"]
        check(f"[{model}] weeks start on Monday", all(datetime.strptime(x, "%Y-%m-%d").weekday() == 0 for x in week))
        t1 = d["torque1"]
        start = today - timedelta(days=89)
        values = t1["data"][0]["x"] if t1["data"] else []
        check(f"[{model}] torque 1, 90 days: {len(values)} measured values (the -1 'not measured' and an aborted "
              f"cycle's 0.0 of a step not reached left out) = SQL",
              len(values) == sqlCount("model_name = %s AND created_on >= %s AND nut_tightening_torque_1 >= 0 AND "
                                      "(nut_tightening_torque_1_datetime IS NULL OR "
                                      "nut_tightening_torque_1_datetime >= '1971-01-01')", (model, start))
              and min(values) >= 0)
        for key, column in (("torque1", "nut_tightening_torque_1"), ("torque2", "nut_tightening_torque_2"),
                            ("rotation", "free_rotation_torque_1")):
            r3 = requests.get(B + "/data", params={"db": "test", "model": model, f"{key}_period": "90_days"})
            xs = r3.json()[key]["data"][0]["x"] if r3.ok and r3.json()[key]["data"] else []
            unreached = sqlCount(f"model_name = %s AND created_on >= %s AND aborted AND {column} = 0 AND "
                                 f"{column}_datetime < '1971-01-01'", (model, start))
            check(f"[{model}] {key}, 90 days: the {unreached} aborted cycles' 0.0 (step not reached) not in the chart",
                  0.0 not in xs and r3.ok, (unreached, min(xs) if xs else None))
            UNREACHED_SEEN.append(unreached)
        setpoints = sorted(round(shape["x0"], 2) for shape in t1["layout"].get("shapes", []))
        check(f"[{model}] torque 1 setpoints in force drawn: {setpoints}", setpoints == [15.0, 15.5])
        start = today - timedelta(days=29)
        check(f"[{model}] re-assembled (DUPLICATE), 30 days = SQL",
              (sum(d["reworked_components"]["data"][0]["y"]) if d["reworked_components"]["data"] else 0) ==
              sqlCount("model_name = %s AND created_on >= %s AND remarks ILIKE %s", (model, start, "%DUPLICATE%")))
    check("the seed has aborted cycles with torques of steps not reached (0.0, 1970)", sum(UNREACHED_SEEN) > 0,
          UNREACHED_SEEN)
    r = requests.get(B + "/data", params={"db": "test", "model": "NO-SUCH-MODEL"})
    check("model without records: charts say 'no data'", r.ok and "no data" in r.json()["torque2"]["layout"]["title"]["text"])
    check("unknown database -> HTTP 400", requests.get(B + "/data", params={"db": "postgres"}).status_code == 400)
    check("unknown period -> HTTP 400", requests.get(B + "/data", params={"db": "test", "day_period": "x"}).status_code == 400)
    check("unknown aborts filter -> HTTP 400", requests.get(B + "/data", params={"db": "test", "aborts": "x"}).status_code == 400)
    check("dashboard page has the Aborted cycles selector and tab",
          'id="aborts"' in requests.get(B + "/").text and "Aborted Cycles" in requests.get(B + "/").text)
    r = requests.get(B + "/")
    check("page needs nothing from the internet", r.ok and "cdn" not in r.text.lower())
    for asset in ("plotly.min.js", "bootstrap.min.css", "bootstrap.bundle.min.js"):
        check(f"/assets/{asset} served", requests.get(B + "/assets/" + asset).ok)


if __name__ == "__main__":
    P.setDatabaseName("Test", createDB=True)
    print(f"Added {seed()} dry-run records to {DB.DATABASES['test']}", flush=True)
    try:
        ws, mi = startServices()
        time.sleep(0.5)
        webServiceChecks(ws)
        abortFilterChecks(ws)
        bypassFilterChecks(ws, mi)
        dashboardChecks(mi)
    except Exception as e:
        check("no exception", False, repr(e))
    finally:
        cleanup()
    print(f"\nRESULT {sum(results)}/{len(results)} checks passed", flush=True)
    os._exit(0 if all(results) else 1)
