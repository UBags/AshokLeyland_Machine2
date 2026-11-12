"""
AshokLeylandWebService - the data web service of the hub and disc assembly machine (Machine 2 database).

What it is for: other systems (and people with a browser) on the plant network fetch the records of the assembled
components - every check result, image file name, torque, the machine settings used and the final OK / NOT OK - as
CSV or JSON, by model, by date range, or for today. Port 8000 (the next free port if 8000 is taken), all interfaces.

GET  /                        the query page (choose model / dates, download CSV or JSON) with the API reference
POST /api/<method>/<csv|json>/  the API; the body is JSON (empty {} where nothing is needed):

  method                                   body
  get_unique_model_names                   {}
  get_data_by_model_name                   {"model_name": "DOST"}
  get_data_by_model_name_and_date_limits   {"model_name": "DOST", "start_date": "2026-11-01", "end_date": "2026-11-02"}
  get_data_of_today_by_model_number        {"model_name": "DOST"}
  get_data_by_date_limits                  {"start_date": "2026-11-01", "end_date": "2026-11-02"}
  get_all_data_of_today                    {}

  The page itself uses the same queries under the names by_model_name, by_model_name_and_date, today_by_model,
  by_date_limits and all_today (they accept modelName / startDate / endDate). Every body may add
  "db_name": "production" (default) or "test". Dates are whole days, both included, by the record's created_on.
  Aborted cycles: every records body may add
    "aborts": "include" (default: every record) | "only" (only aborted cycles) | "exclude" (no aborted cycles) |
              "emergency" (only aborts with the machine's emergency button) | "manual" (only manual aborts, Ctrl+Alt+Z)
    "result": "all" (default) | "ok" | "notok"
    "bypassed": "include" (default) | "only" (component press and second tightening bypassed) | "exclude" (full cycles)
  e.g. {"aborts": "exclude", "result": "notok"} = the completed cycles that failed a check (rejects, not aborts).
  The records carry the columns aborted (true / false) and abort_reason.
  Model names match whole names, ignoring case.
  json -> {"data": [ {column: value, ...}, ... ]}   (get_unique_model_names: {"model_names": [...]})
  csv  -> text/csv, the column names in the first row
  POST /api/<method> (no format) -> JSON {"data": "<the CSV text>"} (get_unique_model_names: {"model_names": [...]}),
  as the earlier service did.
  Errors: 400 for a request to correct (unknown database, missing model, bad date), 500 for a database error;
  JSON {"error": "..."} or, for csv, text "Error: ...".

The columns are those of the frontend's CSV export (persistence.Persistence.all_columns_record_header); see
endpoints/EndpointsDB.py. Started by OtherServices.py (startWebService).
"""
import logging
import os
import socket
import sys
import warnings
from typing import Callable, Dict, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project root, when run directly

from flask import Flask, jsonify, render_template_string, request
from flask_cors import CORS

from endpoints import EndpointsDB as DB

warnings.filterwarnings("ignore")
logging.getLogger("werkzeug").setLevel(logging.ERROR)

DEFAULT_PORT = 8000

app = Flask(__name__)          # serves endpoints/static/app.js (the page's script)
CORS(app)


# ------------------------------------------------------------------------------------------------ queries
def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _first(body: dict, *names):
    for name in names:
        if body.get(name) not in (None, ""):
            return body[name]
    return None


def _filters(body: dict) -> dict:
    """The abort and result filters of a records body (checked here, so a bad value is a 400 before any query)."""
    return {"aborts": DB.resolveAbortFilter(_first(body, "aborts", "abort_filter", "abortFilter")),
            "result": DB.resolveResultFilter(_first(body, "result", "result_filter", "resultFilter")),
            "bypassed": DB.resolveBypassFilter(_first(body, "bypassed", "bypass_filter", "bypassFilter"))}


def _modelName(body: dict) -> str:
    name = _first(body, "model_name", "modelName")
    if name is None:
        raise DB.RequestError("model_name is required")
    return str(name)


def _byModel(body):
    return DB.fetchRecords(DB.resolveDatabase(body.get("db_name")), modelName=_modelName(body), **_filters(body))


def _byModelAndDates(body):
    start, end = DB.dayLimits(_first(body, "start_date", "startDate"), _first(body, "end_date", "endDate"))
    return DB.fetchRecords(DB.resolveDatabase(body.get("db_name")), modelName=_modelName(body), start=start,
                           endExclusive=end, **_filters(body))


def _todayByModel(body):
    start, end = DB.dayLimits(None, None)
    return DB.fetchRecords(DB.resolveDatabase(body.get("db_name")), modelName=_modelName(body), start=start,
                           endExclusive=end, **_filters(body))


def _byDates(body):
    start, end = DB.dayLimits(_first(body, "start_date", "startDate"), _first(body, "end_date", "endDate"))
    return DB.fetchRecords(DB.resolveDatabase(body.get("db_name")), start=start, endExclusive=end, **_filters(body))


def _allToday(body):
    start, end = DB.dayLimits(None, None)
    return DB.fetchRecords(DB.resolveDatabase(body.get("db_name")), start=start, endExclusive=end, **_filters(body))


RECORD_METHODS: Dict[str, Callable[[dict], list]] = {
    # API names
    "get_data_by_model_name": _byModel,
    "get_data_by_model_name_and_date_limits": _byModelAndDates,
    "get_data_of_today_by_model_number": _todayByModel,
    "get_data_by_date_limits": _byDates,
    "get_all_data_of_today": _allToday,
    # names used by the query page
    "by_model_name": _byModel,
    "by_model_name_and_date": _byModelAndDates,
    "today_by_model": _todayByModel,
    "by_date_limits": _byDates,
    "all_today": _allToday,
}


def _error(exception: Exception, format: str) -> Tuple:
    status = 400 if isinstance(exception, DB.RequestError) else 500
    if not isinstance(exception, DB.RequestError):
        print(f"WebService database error: {exception}")
    message = str(exception)
    if format == "csv":
        return f"Error: {message}", status, {"Content-Type": "text/plain; charset=utf-8"}
    return jsonify({"error": message}), status


# ------------------------------------------------------------------------------------------------ routes
@app.route("/api/<method>/<format>/", methods=["POST"])
def api(method: str, format: str):
    if format not in ("csv", "json"):
        return jsonify({"error": "Invalid format. Use /csv/ or /json/"}), 400
    body = _body()
    try:
        if method == "get_unique_model_names":
            names = DB.fetchModelNames(DB.resolveDatabase(body.get("db_name")))
            if format == "json":
                return jsonify({"model_names": names})
            return DB.modelNamesToCsv(names), 200, {"Content-Type": "text/csv; charset=utf-8"}
        if method not in RECORD_METHODS:
            return _error(DB.RequestError(f"Unknown method '{method}'"), format)
        records = RECORD_METHODS[method](body)
        if format == "json":
            return jsonify({"data": DB.recordsToJson(records)})
        return DB.recordsToCsv(records), 200, {"Content-Type": "text/csv; charset=utf-8"}
    except Exception as e:
        return _error(e, format)


@app.route("/api/<method>", methods=["POST"])
def apiDefault(method: str):
    """No format: JSON with the CSV text in "data" (as the earlier service answered these)."""
    body = _body()
    try:
        if method == "get_unique_model_names":
            return jsonify({"model_names": DB.fetchModelNames(DB.resolveDatabase(body.get("db_name")))})
        if method not in RECORD_METHODS:
            return _error(DB.RequestError(f"Unknown method '{method}'"), "json")
        return jsonify({"data": DB.recordsToCsv(RECORD_METHODS[method](body))})
    except Exception as e:
        return _error(e, "json")


@app.route("/")
def index():
    try:
        modelNames = DB.fetchModelNames(DB.DEFAULT_DATABASE)
        databaseNote = ""
    except Exception as e:
        modelNames, databaseNote = [], f"Database not reachable: {e}"
    base = request.host_url.rstrip("/")
    return render_template_string(PAGE, model_names=modelNames, base=base, api=API_REFERENCE,
                                  columns=DB.COLUMNS, note=databaseNote, abort_filters=DB.ABORT_FILTERS,
                                  result_filters=DB.RESULT_FILTERS, bypass_filters=DB.BYPASS_FILTERS)


# ------------------------------------------------------------------------------------------------ page
API_REFERENCE = [
    ("getUniqueModelNames", "get_unique_model_names", "{}"),
    ("getDataByModelName", "get_data_by_model_name", '{"model_name": "DOST"}'),
    ("getDataByModelNameAndDateLimits", "get_data_by_model_name_and_date_limits",
     '{"model_name": "DOST", "start_date": "2026-11-01", "end_date": "2026-11-02"}'),
    ("getDataOfTodayByModelNumber", "get_data_of_today_by_model_number", '{"model_name": "DOST"}'),
    ("getDataByDateLimits", "get_data_by_date_limits", '{"start_date": "2026-11-01", "end_date": "2026-11-02"}'),
    ("getAllDataOfToday", "get_all_data_of_today", "{}"),
    ("... only aborted cycles", "get_data_by_date_limits",
     '{"start_date": "2026-11-01", "end_date": "2026-11-07", "aborts": "only"}'),
    ("... no aborted cycles", "get_data_by_model_name",
     '{"model_name": "DOST", "aborts": "exclude"}'),
    ("... emergency-button aborts", "get_all_data_of_today", '{"aborts": "emergency"}'),
    ("... completed but NOT OK (rejects)", "get_data_by_date_limits",
     '{"start_date": "2026-11-01", "end_date": "2026-11-07", "aborts": "exclude", "result": "notok"}'),
]

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hub and Disc Data Query</title>
<style>
  body { font-family: Arial, sans-serif; margin: 20px; background: #f4f6f9; color: #2c3e50; }
  h1 { text-align: center; }
  .section { margin-bottom: 16px; padding: 14px; background: #fff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,.1); }
  h3 { margin: 0 0 10px 0; color: #34495e; }
  button { padding: 9px 16px; margin: 4px; background: #3498db; color: #fff; border: none; border-radius: 5px; cursor: pointer; }
  button:hover { background: #2980b9; }
  button:disabled { background: #bdc3c7; cursor: not-allowed; }
  select, input[type=date] { padding: 7px; margin: 4px; min-width: 180px; border: 1px solid #ddd; border-radius: 5px; }
  textarea { width: 100%; height: 220px; border: 1px solid #ddd; border-radius: 5px; padding: 8px; font-family: monospace; box-sizing: border-box; }
  #recordCount { margin-left: 12px; color: #27ae60; font-weight: bold; }
  pre { background: #2b2b2b; color: #f8f8f8; padding: 12px; border-radius: 5px; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 18px; }
  th, td { padding: 8px; border: 1px solid #ddd; text-align: left; vertical-align: top; }
  th.json { background: #3498db; color: #fff; } th.csv { background: #27ae60; color: #fff; }
  td.payload { background: #eee; font-family: monospace; }
  .note { color: #c0392b; font-weight: bold; }
  .small { font-size: 0.85rem; color: #6c757d; }
</style>
</head>
<body>
<h1>Hub and Disc Data Query</h1>
{% if note %}<p class="note">{{ note }}</p>{% endif %}

<div class="section">
  <label>Database:</label>
  <select id="dbName"><option value="production">Production</option><option value="test">Test</option></select>
  <label>Aborted cycles:</label>
  <select id="aborts">{% for k, v in abort_filters.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select>
  <label>Result:</label>
  <select id="result">{% for k, v in result_filters.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select>
  <label>Bypassed:</label>
  <select id="bypassed">{% for k, v in bypass_filters.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select>
  <span class="small">Dates are whole days (both included), by the time the record was saved. The filters apply to
  every query below.</span>
</div>

<div class="section">
  <h3>Data by model</h3>
  <select id="modelName1"><option value="">Select model</option>{% for m in model_names %}<option value="{{ m }}">{{ m }}</option>{% endfor %}</select>
  <button id="by_model_name_csv_btn" onclick="validateAndQuery('by_model_name','csv','modelName1')">CSV</button>
  <button id="by_model_name_json_btn" onclick="validateAndQuery('by_model_name','json','modelName1')">JSON</button>
</div>

<div class="section">
  <h3>Data by model and dates</h3>
  <select id="modelName2"><option value="">Select model</option>{% for m in model_names %}<option value="{{ m }}">{{ m }}</option>{% endfor %}</select>
  <label>From</label><input type="date" id="startDate"><label>to</label><input type="date" id="endDate">
  <button id="by_model_name_and_date_csv_btn" onclick="validateAndQuery('by_model_name_and_date','csv','modelName2')">CSV</button>
  <button id="by_model_name_and_date_json_btn" onclick="validateAndQuery('by_model_name_and_date','json','modelName2')">JSON</button>
</div>

<div class="section">
  <h3>Today's data by model</h3>
  <select id="modelName3"><option value="">Select model</option>{% for m in model_names %}<option value="{{ m }}">{{ m }}</option>{% endfor %}</select>
  <button id="today_by_model_csv_btn" onclick="validateAndQuery('today_by_model','csv','modelName3')">CSV</button>
  <button id="today_by_model_json_btn" onclick="validateAndQuery('today_by_model','json','modelName3')">JSON</button>
</div>

<div class="section">
  <h3>Data by dates (all models)</h3>
  <label>From</label><input type="date" id="startDate2"><label>to</label><input type="date" id="endDate2">
  <button id="by_date_limits_csv_btn" onclick="validateAndQuery('by_date_limits','csv')">CSV</button>
  <button id="by_date_limits_json_btn" onclick="validateAndQuery('by_date_limits','json')">JSON</button>
</div>

<div class="section">
  <h3>All of today's data</h3>
  <button id="all_today_csv_btn" onclick="validateAndQuery('all_today','csv')">CSV</button>
  <button id="all_today_json_btn" onclick="validateAndQuery('all_today','json')">JSON</button>
</div>

<h3>Result <span id="recordCount"></span></h3>
<textarea id="result" readonly></textarea>

<div class="section">
  <h2>API</h2>
  <p>POST, body JSON. Add <code>"db_name": "test"</code> to any body to read the test database. Dates YYYY-MM-DD, both
  included. JSON answers <code>{"data": [...]}</code> (<code>{"model_names": [...]}</code>); CSV answers the column names
  in the first row. Errors: HTTP 400 (request to correct) or 500 (database), <code>{"error": "..."}</code>.</p>
  <p>Aborted cycles: add <code>"aborts"</code> to any records body:
  {% for k, v in abort_filters.items() %}<code>"{{ k }}"</code> {{ v }}{% if not loop.last %}; {% endif %}{% endfor %}.
  And <code>"result"</code>: {% for k, v in result_filters.items() %}<code>"{{ k }}"</code> {{ v }}{% if not loop.last %}; {% endif %}{% endfor %}.
  Both together, e.g. <code>{"aborts": "exclude", "result": "notok"}</code>: completed cycles that failed a check.
  And <code>"bypassed"</code>: {% for k, v in bypass_filters.items() %}<code>"{{ k }}"</code> {{ v }}{% if not loop.last %}; {% endif %}{% endfor %}.
  Every record has the columns <code>aborted</code> and <code>abort_reason</code>.</p>
  {% for fmt, cls in (("json", "json"), ("csv", "csv")) %}
  <h3>{{ fmt|upper }} responses</h3>
  <table>
    <tr><th class="{{ cls }}">Method</th><th class="{{ cls }}">Endpoint</th><th class="{{ cls }}">Body</th></tr>
    {% for name, path, payload in api %}
    <tr><td>{{ name }}</td><td>{{ base }}/api/{{ path }}/{{ fmt }}/</td><td class="payload">{{ payload }}</td></tr>
    {% endfor %}
  </table>
  {% endfor %}
  <h3>Columns</h3>
  <p class="small">{{ columns|join(", ") }}</p>
</div>

<div class="section">
  <h3>Examples</h3>
  <h4>Python (requests)</h4>
<pre>
import requests
r = requests.post("{{ base }}/api/get_data_by_model_name_and_date_limits/json/",
                  json={"model_name": "DOST", "start_date": "2026-11-01", "end_date": "2026-11-02"})
r.raise_for_status()
for record in r.json()["data"]:
    print(record["qr_code"], record["ok_notok_result"], record["nut_tightening_torque_1"])

csv_text = requests.post("{{ base }}/api/get_all_data_of_today/csv/", json={}).text
</pre>
  <h4>JavaScript (fetch)</h4>
<pre>
fetch("{{ base }}/api/get_unique_model_names/json/", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"})
  .then(r => r.json()).then(d => console.log(d.model_names));
</pre>
  <h4>C# (HttpClient)</h4>
<pre>
using var client = new HttpClient();
var body = new StringContent("{\\"model_name\\": \\"DOST\\"}", Encoding.UTF8, "application/json");
var response = await client.PostAsync("{{ base }}/api/get_data_of_today_by_model_number/csv/", body);
Console.WriteLine(await response.Content.ReadAsStringAsync());
</pre>
  <h4>Java (HttpClient)</h4>
<pre>
HttpRequest request = HttpRequest.newBuilder(URI.create("{{ base }}/api/get_data_by_date_limits/json/"))
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString("{\\"start_date\\": \\"2026-11-01\\", \\"end_date\\": \\"2026-11-02\\"}"))
    .build();
System.out.println(HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString()).body());
</pre>
  <h4>cURL</h4>
<pre>
curl -X POST {{ base }}/api/get_data_by_model_name/csv/ -H "Content-Type: application/json" -d "{\\"model_name\\": \\"DOST\\"}" -o DOST.csv
</pre>
</div>
<script src="static/app.js"></script>
</body>
</html>
"""


# ------------------------------------------------------------------------------------------------ start
def _portFree(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _freePort() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def startWebService(port: int = DEFAULT_PORT):
    if not _portFree(port):
        print(f"WebService: port {port} is in use", end="; ")
        port = _freePort()
    print(f"WebService on http://0.0.0.0:{port}/ (this PC: http://127.0.0.1:{port}/)")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    startWebService()
