"""
AshokLeyland_MI_Light - the management-information dashboard of the hub and disc assembly machine (Machine 2 database).

What it is for: supervisors and managers on the plant network see, in a browser, how many components were assembled
(per day and per week, OK and NOT OK), how the torques are spread (nut tightening torque 1 and 2 against their
setpoints, free rotation torque), and how many components were re-assembled (a QR code scanned again: the DBServer
saves such a record with remarks "DUPLICATE"). Model filter (all models or one), period per chart, production or test
database; the page refreshes itself every minute. Port 8050 (the next free port if 8050 is taken), all interfaces.

GET /                 the page
GET /data?db=&model=&aborts=&bypassed=&day_period=&week_period=&torque1_period=&torque2_period=&rotation_period=
         &reworked_period=&aborted_period=
                      the figures (Plotly JSON), today's / this week's / this month's counts and the database's models.
                      aborts: include (default) / only / exclude / emergency / manual (EndpointsDB.ABORT_FILTERS),
                      applied to every chart and count.
GET /assets/...       bootstrap (endpoints/assets) and plotly.js (from the installed plotly package): the page needs no
                      internet access.

Values not measured are stored as negative numbers (-1.0); they are left out of the torque charts. So are the torques
of steps an aborted cycle never reached: the frontend records them as 0.0 with the placeholder time 1970-01-01
(CHANGES 127; before, each aborted cycle put up to three 0.0 values into the torque charts). A record counts as
OK when ok_notok_result is the configured OK value; an aborted cycle (aborted = true: emergency button on the machine
or manual abort in the frontend) is counted as ABORTED, not as NOT OK, in the counts. Days and weeks are by created_on
(the end of the cycle); weeks start on Monday. Started by OtherServices.py (startMIServer).
"""
import json
import os
import sys
from datetime import datetime, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project root, when run directly

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import get_plotlyjs

from endpoints import EndpointsDB as DB
from Configuration import CosThetaConfigurator

DEFAULT_PORT = CosThetaConfigurator.getTuning("endpoints.mi.port", 8050, minimum=1)
ALL_MODELS = "ALL MODELS"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
GREEN, RED, BLUE, ORANGE, GREY, PURPLE = "#2e7d32", "#c62828", "#1565c0", "#ef6c00", "#616161", "#6a1b9a"
COLUMNS = ["created_on", "model_name", "ok_notok_result", "nut_tightening_torque_1", "nut_tightening_torque_2",
           "free_rotation_torque_1", "setting_tightening_torque1_setpoint", "setting_tightening_torque2_setpoint",
           "remarks", "aborted", "abort_reason", "bypassed_press_and_tightening2",
           "nut_tightening_torque_1_datetime", "nut_tightening_torque_2_datetime", "free_rotation_torque_1_datetime"]
# a torque whose datetime is before this was never measured (the frontend's placeholder 1970-01-01 00:00:00)
NOT_MEASURED_BEFORE = pd.Timestamp("1971-01-01")
TORQUES = {  # key -> (column, title, setpoint column)
    "torque1": ("nut_tightening_torque_1", "Nut Tightening Torque 1", "setting_tightening_torque1_setpoint"),
    "torque2": ("nut_tightening_torque_2", "Nut Tightening Torque 2", "setting_tightening_torque2_setpoint"),
    "rotation": ("free_rotation_torque_1", "Free Rotation Torque", None),
}
DAY_PERIODS = {"7_days": "Last 7 Days", "30_days": "Last 30 Days", "90_days": "Last 90 Days",
               "365_days": "Last 365 Days", "ytd": "Year-to-Date", "qtd": "Quarter-to-Date", "mtd": "Month-to-Date"}
WEEK_PERIODS = {"12_weeks": "Last 12 Weeks", "52_weeks": "Last 52 Weeks", "ytd": "Year-to-Date",
                "qtd": "Quarter-to-Date", "mtd": "Month-to-Date"}
SHORT_PERIODS = {"30_days": "Last 30 Days", "90_days": "Last 90 Days", "365_days": "Last 365 Days"}


# ------------------------------------------------------------------------------------------------ data
def periodStart(period: str, now: datetime) -> datetime:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period.endswith("_days"):
        return today - timedelta(days=int(period.split("_")[0]) - 1)
    if period.endswith("_weeks"):
        monday = today - timedelta(days=today.weekday())
        return monday - timedelta(weeks=int(period.split("_")[0]) - 1)
    if period == "ytd":
        return today.replace(month=1, day=1)
    if period == "qtd":
        return today.replace(month=3 * ((today.month - 1) // 3) + 1, day=1)
    if period == "mtd":
        return today.replace(day=1)
    raise DB.RequestError(f"Unknown period '{period}'")


def loadFrame(database: str, now: datetime) -> pd.DataFrame:
    """The last 366 days (enough for every period) of the columns the charts use."""
    start = min(periodStart("365_days", now), periodStart("ytd", now))
    rows = DB.fetchColumns(database, COLUMNS, start, now + timedelta(days=1))
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame["created_on"] = pd.to_datetime(frame["created_on"])
    for column in COLUMNS[3:8]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["aborted"] = frame["aborted"].fillna(False).astype(bool)
    # torques of steps not reached (aborted cycle): 0.0 with the 1970 placeholder time -> not measured (NaN). A NULL
    # time (bypassed torque 2 = -1.0, records from before the times were stored) is left to the "< 0" rule.
    for column, *_ in TORQUES.values():
        when = pd.to_datetime(frame[f"{column}_datetime"], errors="coerce")
        frame.loc[when.notna() & (when < NOT_MEASURED_BEFORE), column] = float("nan")
    frame["bypassed"] = frame["bypassed_press_and_tightening2"].fillna(False).astype(bool)
    frame["ok"] = (frame["ok_notok_result"].fillna("") == DB.OK_VALUE) & ~frame["aborted"]
    frame["reworked"] = frame["remarks"].fillna("").str.contains("DUPLICATE", case=False)
    reason = frame["abort_reason"].fillna("").str.lower()
    frame["abortKind"] = ""
    frame.loc[frame["aborted"], "abortKind"] = "other"
    frame.loc[frame["aborted"] & reason.str.startswith("emergency"), "abortKind"] = "emergency"
    frame.loc[frame["aborted"] & reason.str.startswith("manual"), "abortKind"] = "manual"
    return frame


def bypassFiltered(frame: pd.DataFrame, bypassed: str) -> pd.DataFrame:
    """The records the bypass filter keeps (EndpointsDB.BYPASS_FILTERS; resolved name)."""
    if bypassed == "only":
        return frame[frame["bypassed"]]
    if bypassed == "exclude":
        return frame[~frame["bypassed"]]
    return frame


def abortFiltered(frame: pd.DataFrame, aborts: str) -> pd.DataFrame:
    """The records the abort filter keeps (EndpointsDB.ABORT_FILTERS; resolved name)."""
    if aborts == "only":
        return frame[frame["aborted"]]
    if aborts == "exclude":
        return frame[~frame["aborted"]]
    if aborts in ("emergency", "manual"):
        return frame[frame["abortKind"] == aborts]
    return frame


def select(frame: pd.DataFrame, model: str, start: datetime) -> pd.DataFrame:
    part = frame[frame["created_on"] >= start]
    return part if model == ALL_MODELS else part[part["model_name"] == model]


# ------------------------------------------------------------------------------------------------ figures
def _layout(fig: go.Figure, title: str, xTitle: str, yTitle: str, empty: bool) -> dict:
    fig.update_layout(title=title + (" - no data" if empty else ""), template="plotly_white", barmode="stack",
                      xaxis_title=xTitle, yaxis_title=yTitle, legend=dict(orientation="h", y=1.12),
                      margin=dict(l=60, r=20, t=80, b=50), font=dict(size=12))
    return json.loads(pio.to_json(fig))


def _outcome(part: pd.DataFrame) -> pd.Series:
    """OK / NOT OK / ABORTED per record (an aborted cycle is ABORTED, not NOT OK)."""
    return pd.Series(["ABORTED" if a else ("OK" if o else "NOT OK") for a, o in zip(part["aborted"], part["ok"])],
                     index=part.index)


def countsFigure(part: pd.DataFrame, by: str, title: str) -> dict:
    """Stacked OK / NOT OK / ABORTED counts per day (by="day") or per week starting Monday (by="week"). The three
    series are always there (in this order), zero where there is none."""
    fig = go.Figure()
    if not part.empty:
        key = part["created_on"].dt.normalize()
        if by == "week":
            key = key - pd.to_timedelta(key.dt.weekday, unit="D")
        counts = part.groupby([key, _outcome(part)]).size().unstack(fill_value=0)
        labels = [d.strftime("%Y-%m-%d") for d in counts.index]
        for name, colour in (("OK", GREEN), ("NOT OK", RED), ("ABORTED", GREY)):
            fig.add_bar(x=labels, y=counts.get(name, pd.Series(0, counts.index)).tolist(), name=name,
                        marker_color=colour)
        fig.update_xaxes(type="category")
    return _layout(fig, title, "Week starting" if by == "week" else "Date", "Components", part.empty)


def torqueFigure(part: pd.DataFrame, key: str, title: str) -> dict:
    column, name, setpointColumn = TORQUES[key]
    values = part[column].dropna()
    values = values[values >= 0]                     # -1.0 = not measured
    fig = go.Figure()
    if not values.empty:
        span = float(values.max() - values.min())
        size = 0.05 if span / 0.05 <= 200 else span / 60
        fig.add_histogram(x=values.tolist(), xbins=dict(start=float(values.min()) - size / 2,
                                                         end=float(values.max()) + size, size=size),
                          marker=dict(color="rgba(21, 101, 192, 0.45)", line=dict(color=BLUE, width=1)),
                          name="components")
        if setpointColumn:
            setpoints = part.loc[values.index, setpointColumn].dropna()
            for setpoint in sorted(setpoints.unique())[-3:]:    # the setpoints in force in this period (at most 3)
                fig.add_vline(x=float(setpoint), line=dict(color=ORANGE, dash="dash", width=2),
                              annotation_text=f"setpoint {setpoint:g}", annotation_position="top")
        title = (f"{title}: n = {len(values)}, mean {values.mean():.2f}, sd {values.std(ddof=0):.2f}, "
                 f"min {values.min():.2f}, max {values.max():.2f} Nm")
    return _layout(fig, title, f"{name} (Nm)", "Components", values.empty)


def reworkedFigure(part: pd.DataFrame, title: str) -> dict:
    reworked = part[part["reworked"]]
    fig = go.Figure()
    if not reworked.empty:
        counts = reworked.groupby(reworked["created_on"].dt.normalize()).size()
        fig.add_bar(x=[d.strftime("%Y-%m-%d") for d in counts.index], y=counts.tolist(), marker_color=ORANGE,
                    name="re-assembled")
        fig.update_xaxes(type="category")
    return _layout(fig, title, "Date", "Re-assembled components", reworked.empty)


def abortedFigure(part: pd.DataFrame, title: str) -> dict:
    """Aborted cycles per day, stacked by reason: emergency button on the machine, manual abort in the frontend."""
    aborted = part[part["aborted"]]
    fig = go.Figure()
    if not aborted.empty:
        counts = aborted.groupby([aborted["created_on"].dt.normalize(), "abortKind"]).size().unstack(fill_value=0)
        labels = [d.strftime("%Y-%m-%d") for d in counts.index]
        for kind, name, colour in (("emergency", "Emergency button", RED), ("manual", "Manual (frontend)", PURPLE),
                                   ("other", "Other", GREY)):
            if kind in counts:
                fig.add_bar(x=labels, y=counts[kind].tolist(), name=name, marker_color=colour)
        fig.update_xaxes(type="category")
    return _layout(fig, title, "Date", "Aborted cycles", aborted.empty)


def summary(frame: pd.DataFrame, model: str, now: datetime) -> dict:
    out = {}
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for label, start in (("today", today), ("week", today - timedelta(days=today.weekday())),
                         ("month", today.replace(day=1))):
        part = select(frame, model, start)
        aborted = int(part["aborted"].sum())
        out[label] = {"total": int(len(part)), "ok": int(part["ok"].sum()),
                      "notok": int(len(part) - part["ok"].sum() - aborted), "aborted": aborted}
    return out


def dashboardData(query: dict) -> dict:
    get = lambda name, default: query.get(name, [default])[0] or default
    database = DB.resolveDatabase(get("db", "production"))
    model = get("model", ALL_MODELS)
    aborts = DB.resolveAbortFilter(get("aborts", "include"))
    bypassed = DB.resolveBypassFilter(get("bypassed", "include"))
    periods = {name: get(name, default) for name, default in (
        ("day_period", "7_days"), ("week_period", "12_weeks"), ("torque1_period", "30_days"),
        ("torque2_period", "30_days"), ("rotation_period", "30_days"), ("reworked_period", "30_days"),
        ("aborted_period", "30_days"))}
    now = datetime.now()
    frame = bypassFiltered(abortFiltered(loadFrame(database, now), aborts), bypassed)
    suffix = ("All Models" if model == ALL_MODELS else model) + \
             ({"only": ", aborted only", "exclude": ", no aborts", "emergency": ", emergency aborts",
               "manual": ", manual aborts"}.get(aborts, "")) + \
             {"only": ", bypassed only", "exclude": ", no bypassed"}.get(bypassed, "")
    label = lambda name, table: table.get(periods[name], periods[name])
    data = {
        "models": DB.fetchModelNames(database),
        "summary": summary(frame, model, now),
        "components_day": countsFigure(select(frame, model, periodStart(periods["day_period"], now)), "day",
                                       f"Components per Day ({label('day_period', DAY_PERIODS)} - {suffix})"),
        "components_week": countsFigure(select(frame, model, periodStart(periods["week_period"], now)), "week",
                                        f"Components per Week ({label('week_period', WEEK_PERIODS)} - {suffix})"),
        "reworked_components": reworkedFigure(select(frame, model, periodStart(periods["reworked_period"], now)),
                                              f"Re-assembled Components per Day "
                                              f"({label('reworked_period', SHORT_PERIODS)} - {suffix})"),
        "aborted_components": abortedFigure(select(frame, model, periodStart(periods["aborted_period"], now)),
                                            f"Aborted Cycles per Day ({label('aborted_period', SHORT_PERIODS)} - "
                                            f"{suffix})"),
        "aborts": aborts,
        "bypassed": bypassed,
    }
    for key in TORQUES:
        name = f"{key}_period"
        data[key] = torqueFigure(select(frame, model, periodStart(periods[name], now)), key,
                                 f"{TORQUES[key][1]} ({label(name, SHORT_PERIODS)} - {suffix})")
    return data


# ------------------------------------------------------------------------------------------------ page
def _options(table: dict) -> str:
    return "".join(f'<option value="{k}">{v}</option>' for k, v in table.items())


def _tab(key: str, title: str, periods: dict, description: str, active: bool = False) -> str:
    return f"""
    <div class="tab-pane fade{' show active' if active else ''}" id="{key}" role="tabpanel">
      <div class="card mt-3"><div class="card-body">
        <div class="d-flex align-items-center gap-2 mb-2"><label class="fw-semibold" for="{key}-period">Period</label>
          <select id="{key}-period" class="form-select period" style="max-width: 220px;">{_options(periods)}</select></div>
        <div id="{key}-chart" class="chart"></div>
        <p class="description">{description}</p>
      </div></div>
    </div>"""


TABS = [  # (key in /data, tab label, period table, description, query parameter)
    ("components_day", "Components per Day", DAY_PERIODS,
     "Components per day, split into OK, NOT OK (completed, a check failed) and ABORTED (emergency button on the "
     "machine or manual abort).", "day_period"),
    ("components_week", "Components per Week", WEEK_PERIODS,
     "Components per week (weeks start on Monday): OK, NOT OK and ABORTED.", "week_period"),
    ("torque1", "Nut Tightening Torque 1", SHORT_PERIODS,
     "Spread of the first nut tightening torque; dashed line: the torque 1 setpoint used.", "torque1_period"),
    ("torque2", "Nut Tightening Torque 2", SHORT_PERIODS,
     "Spread of the second nut tightening torque; dashed line: the torque 2 setpoint used.", "torque2_period"),
    ("rotation", "Free Rotation Torque", SHORT_PERIODS,
     "Spread of the free rotation torque measured at the end of the cycle.", "rotation_period"),
    ("reworked_components", "Re-assembled", SHORT_PERIODS,
     "Components whose QR code was scanned again (saved with remarks DUPLICATE), per day.", "reworked_period"),
    ("aborted_components", "Aborted Cycles", SHORT_PERIODS,
     "Cycles aborted per day, by reason: the emergency button on the machine, or a manual abort in the frontend "
     "(Ctrl+Alt+Z). An abort while waiting for the QR code makes no record and is not counted.", "aborted_period"),
]


def page(models) -> str:
    modelOptions = "".join(f'<option value="{escape(m)}">{escape(m)}</option>' for m in [ALL_MODELS] + list(models))
    navs = "".join(f'<li class="nav-item"><a class="nav-link{" active" if i == 0 else ""}" data-bs-toggle="tab" '
                   f'href="#{k}" role="tab">{label}</a></li>' for i, (k, label, *_) in enumerate(TABS))
    panes = "".join(_tab(k, label, periods, text, i == 0) for i, (k, label, periods, text, _) in enumerate(TABS))
    params = json.dumps({k: p for k, *_, p in TABS})
    abortOptions = "".join(f'<option value="{k}">{escape(v)}</option>' for k, v in DB.ABORT_FILTERS.items())
    bypassOptions = "".join(f'<option value="{k}">{escape(v)}</option>' for k, v in DB.BYPASS_FILTERS.items())
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hub and Disc Assembly Dashboard</title>
<link href="/assets/bootstrap.min.css" rel="stylesheet">
<script src="/assets/bootstrap.bundle.min.js"></script>
<script src="/assets/plotly.min.js"></script>
<style>
  body {{ background: linear-gradient(to bottom, #f8f9fa, #e9ecef); }}
  .card {{ border: none; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
  .nav-tabs .nav-link.active {{ background-color: #0d6efd; color: white; }}
  .chart {{ width: 100%; height: 430px; }}
  .description {{ color: #6c757d; font-size: 0.9rem; margin: 0.5rem 0 0 0; }}
  .stat {{ font-size: 1.6rem; font-weight: 700; }}
</style></head>
<body><div class="container-fluid p-4">
  <div class="d-flex flex-wrap align-items-end gap-3 mb-3">
    <h1 class="text-primary mb-0 me-auto" style="font-weight: 700;">Hub and Disc Assembly Dashboard</h1>
    <div><label class="form-label fw-semibold mb-0" for="model">Model</label>
      <select id="model" class="form-select" style="min-width: 220px;">{modelOptions}</select></div>
    <div><label class="form-label fw-semibold mb-0" for="aborts">Aborted cycles</label>
      <select id="aborts" class="form-select" style="min-width: 260px;">{abortOptions}</select></div>
    <div><label class="form-label fw-semibold mb-0" for="bypassed">Bypassed</label>
      <select id="bypassed" class="form-select" style="min-width: 260px;">{bypassOptions}</select></div>
    <div><label class="form-label fw-semibold mb-0" for="db">Database</label>
      <select id="db" class="form-select"><option value="production">Production</option><option value="test">Test</option></select></div>
  </div>
  <div class="row g-3 mb-3" id="summary">
    <div class="col-md-4"><div class="card"><div class="card-body"><div class="text-muted">Today</div><div class="stat" id="s-today">-</div></div></div></div>
    <div class="col-md-4"><div class="card"><div class="card-body"><div class="text-muted">This week</div><div class="stat" id="s-week">-</div></div></div></div>
    <div class="col-md-4"><div class="card"><div class="card-body"><div class="text-muted">This month</div><div class="stat" id="s-month">-</div></div></div></div>
  </div>
  <ul class="nav nav-tabs" role="tablist">{navs}</ul>
  <div class="tab-content">{panes}</div>
  <p class="text-muted small mt-3" id="status"></p>
</div>
<script>
const PARAMS = {params};
const shown = new Set();
function draw(data) {{
  for (const key of Object.keys(PARAMS)) {{
    const div = document.getElementById(key + '-chart');
    if (!data[key]) continue;
    if (div.offsetParent === null && !shown.has(key)) continue;     // hidden tab: drawn when opened
    Plotly.react(div, data[key].data, data[key].layout, {{responsive: true, displaylogo: false}});
    shown.add(key);
  }}
  const select = document.getElementById('model'), names = ['{ALL_MODELS}'].concat(data.models || []);
  if (Array.from(select.options).map(o => o.value).join('|') !== names.join('|')) {{   // the chosen database's models
    const keep = select.value;
    select.innerHTML = names.map(n => `<option value="${{n.replace(/"/g, '&quot;')}}">${{n.replace(/</g, '&lt;')}}</option>`).join('');
    select.value = names.includes(keep) ? keep : '{ALL_MODELS}';
  }}
  for (const p of ['today', 'week', 'month']) {{
    const s = data.summary[p];
    document.getElementById('s-' + p).innerHTML = `${{s.total}} <span class="fs-6 text-success">OK ${{s.ok}}</span> <span class="fs-6 text-danger">NOT OK ${{s.notok}}</span> <span class="fs-6 text-secondary">ABORTED ${{s.aborted}}</span>`;
  }}
}}
let last = null;
function refresh() {{
  const q = new URLSearchParams({{db: document.getElementById('db').value, model: document.getElementById('model').value,
                                  aborts: document.getElementById('aborts').value,
                                  bypassed: document.getElementById('bypassed').value}});
  for (const [key, name] of Object.entries(PARAMS)) q.set(name, document.getElementById(key + '-period').value);
  fetch('/data?' + q.toString())
    .then(r => r.json().then(j => {{ if (!r.ok) throw new Error(j.error || r.statusText); return j; }}))
    .then(data => {{ last = data; draw(data); document.getElementById('status').textContent = 'Updated ' + new Date().toLocaleTimeString(); }})
    .catch(e => {{ document.getElementById('status').textContent = 'Error: ' + e.message; }});
}}
document.querySelectorAll('select').forEach(s => s.addEventListener('change', refresh));
document.querySelectorAll('a[data-bs-toggle="tab"]').forEach(a => a.addEventListener('shown.bs.tab', () => {{ if (last) draw(last); window.dispatchEvent(new Event('resize')); }}));
refresh();
setInterval(refresh, 60 * 1000);
</script>
</body></html>"""


# ------------------------------------------------------------------------------------------------ server
_plotlyJs = None


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):          # quiet: the page polls every minute
        pass

    def _send(self, status: int, body, contentType: str, cache: bool = False):
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", contentType)
        self.send_header("Content-Length", str(len(payload)))
        if cache:
            self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        global _plotlyJs
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                try:
                    models = DB.fetchModelNames(DB.DEFAULT_DATABASE)
                except Exception as e:
                    print(f"MI dashboard: database not reachable: {e}")
                    models = []
                self._send(200, page(models), "text/html; charset=utf-8")
            elif parsed.path == "/data":
                self._send(200, json.dumps(dashboardData(parse_qs(parsed.query))), "application/json")
            elif parsed.path == "/assets/plotly.min.js":
                if _plotlyJs is None:
                    _plotlyJs = get_plotlyjs().encode("utf-8")
                self._send(200, _plotlyJs, "application/javascript", cache=True)
            elif parsed.path in ("/assets/bootstrap.min.css", "/assets/bootstrap.bundle.min.js"):
                with open(os.path.join(ASSETS, os.path.basename(parsed.path)), "rb") as f:
                    self._send(200, f.read(), "text/css" if parsed.path.endswith(".css") else "application/javascript",
                               cache=True)
            else:
                self._send(404, "Not Found", "text/plain")
        except DB.RequestError as e:
            self._send(400, json.dumps({"error": str(e)}), "application/json")
        except Exception as e:
            print(f"MI dashboard error: {e}")
            self._send(500, json.dumps({"error": str(e)}), "application/json")


def startMIServer(port: int = DEFAULT_PORT):
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    except OSError:
        server = ThreadingHTTPServer(("0.0.0.0", 0), DashboardHandler)
        print(f"MI dashboard: port {port} is in use", end="; ")
    server.daemon_threads = True
    print(f"MI dashboard on http://0.0.0.0:{server.server_address[1]}/ "
          f"(this PC: http://127.0.0.1:{server.server_address[1]}/)")
    server.serve_forever()


if __name__ == "__main__":
    startMIServer()
