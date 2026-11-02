"""
EndpointsDB - the database access shared by the web service (AshokLeylandWebService) and the MI dashboard
(AshokLeyland_MI_Light).

Reads al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data, the table written by the DBServer at the end of every
cycle. The exported columns are persistence.Persistence.all_columns_record_header (the same columns, in the same order,
as the frontend's CSV export), so the exports follow the table when it changes.

Databases: "production" (ashok_leyland_production, the default) or "test" (ashok_leyland_test); the full database
names are accepted too. Anything else is refused.
Dates: a record belongs to the day of its created_on (the end of its cycle). Date limits are whole days and both are
included: start_date 2026-11-01, end_date 2026-11-02 = from 2026-11-01 00:00:00 up to, not including, 2026-11-03.
"""
import csv
from datetime import date, datetime, timedelta
from io import StringIO
from typing import Dict, List, Optional, Sequence, Tuple

import psycopg2
from psycopg2 import sql

from persistence.Persistence import all_columns_record_header, schema_name, okResult

DATABASES: Dict[str, str] = {"production": "ashok_leyland_production", "test": "ashok_leyland_test"}
DEFAULT_DATABASE = DATABASES["production"]
TABLE = "hub_and_disc_assembly_data"
COLUMNS: List[str] = list(all_columns_record_header)
OK_VALUE: str = okResult                      # value of ok_notok_result for a good component ("OK")
CONNECTION = dict(user="postgres", password="postgres", host="127.0.0.1", port="5432")


class RequestError(ValueError):
    """A request the caller has to correct (unknown database, missing model name, bad date)."""


def resolveDatabase(name: Optional[str]) -> str:
    if not name:
        return DEFAULT_DATABASE
    key = str(name).strip().lower()
    if key in DATABASES:
        return DATABASES[key]
    if key in DATABASES.values():
        return key
    raise RequestError(f"Unknown database '{name}'. Use one of: {', '.join(list(DATABASES) + list(DATABASES.values()))}")


def parseDate(text: Optional[str], name: str) -> Optional[date]:
    if text is None or str(text).strip() == "":
        return None
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise RequestError(f"{name} must be in YYYY-MM-DD format, received: {text}")


def dayLimits(startDate: Optional[str], endDate: Optional[str]) -> Tuple[datetime, datetime]:
    """[start of the start day, start of the day after the end day). A missing date means today."""
    today = date.today()
    start = parseDate(startDate, "start date") or today
    end = parseDate(endDate, "end date") or today
    if end < start:
        raise RequestError(f"end date {end} is before start date {start}")
    return datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())


def _query(database: str, query, params) -> List[tuple]:
    """Run one SELECT on its own connection and close it (psycopg2's 'with connection' only ends the transaction)."""
    connection = psycopg2.connect(database=database, connect_timeout=5, **CONNECTION)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()
    finally:
        connection.close()


def _table() -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(schema_name), sql.Identifier(TABLE))


def fetchRecords(database: str, modelName: Optional[str] = None,
                 start: Optional[datetime] = None, endExclusive: Optional[datetime] = None) -> List[tuple]:
    """Records in COLUMNS order, oldest first. modelName matches whole names, ignoring case."""
    conditions, params = [], []
    if modelName is not None:
        if not str(modelName).strip():
            raise RequestError("model name must not be empty")
        conditions.append(sql.SQL("lower(model_name) = lower(%s)"))
        params.append(str(modelName).strip())
    if start is not None:
        conditions.append(sql.SQL("created_on >= %s"))
        params.append(start)
    if endExclusive is not None:
        conditions.append(sql.SQL("created_on < %s"))
        params.append(endExclusive)
    query = sql.SQL("SELECT {} FROM {}{} ORDER BY created_on ASC, record_id ASC").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNS), _table(),
        sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL(""))
    rows = _query(database, query, params)
    return rows


def fetchModelNames(database: str, start: Optional[datetime] = None,
                    endExclusive: Optional[datetime] = None) -> List[str]:
    conditions, params = [sql.SQL("model_name IS NOT NULL"), sql.SQL("model_name <> 'Not Populated'")], []
    if start is not None:
        conditions.append(sql.SQL("created_on >= %s"))
        params.append(start)
    if endExclusive is not None:
        conditions.append(sql.SQL("created_on < %s"))
        params.append(endExclusive)
    query = sql.SQL("SELECT DISTINCT model_name FROM {} WHERE {} ORDER BY model_name").format(
        _table(), sql.SQL(" AND ").join(conditions))
    rows = _query(database, query, params)
    return [row[0] for row in rows]


def fetchColumns(database: str, columns: Sequence[str], start: datetime, endExclusive: datetime) -> List[tuple]:
    """Selected columns of the records created in [start, endExclusive), oldest first (for the dashboard)."""
    query = sql.SQL("SELECT {} FROM {} WHERE created_on >= %s AND created_on < %s ORDER BY created_on").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in columns), _table())
    rows = _query(database, query, (start, endExclusive))
    return rows


def _text(value) -> object:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


def recordsToJson(records: List[tuple]) -> List[dict]:
    return [{c: _text(v) for c, v in zip(COLUMNS, record)} for record in records]


def recordsToCsv(records: List[tuple]) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(COLUMNS)
    for record in records:
        writer.writerow(["" if v is None else _text(v) for v in record])
    return output.getvalue()


def modelNamesToCsv(names: List[str]) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["model_name"])
    for name in names:
        writer.writerow([name])
    return output.getvalue()
