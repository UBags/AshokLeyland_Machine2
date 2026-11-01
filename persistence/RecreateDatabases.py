"""
RecreateDatabases - DROP the two application databases (ashok_leyland_test, ashok_leyland_production) and create them
again, empty, exactly as the application creates them at start-up: schema, tables, indexes, the default machine
settings (machine_settings: 30 rpm, 15 / 19 Nm, 1 / 1 rotations), the roles and the default users.

ALL DATA IN BOTH DATABASES IS LOST (component records, users added later, settings history). Stop the application
(MainProgram and every server) first: open connections are closed by this script.

Run from the project root:
    python persistence/RecreateDatabases.py                 # asks you to type YES
    python persistence/RecreateDatabases.py --yes           # no question
    python persistence/RecreateDatabases.py --only test     # only ashok_leyland_test (or: --only production)
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import psycopg2
from psycopg2 import sql

DATABASES = {"test": ("ashok_leyland_test", "Test"), "production": ("ashok_leyland_production", "Production")}


def drop(name: str) -> None:
    connection = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", port="5432",
                                  dbname="postgres")
    connection.autocommit = True
    cursor = connection.cursor()
    cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                   (name,))
    closed = cursor.rowcount
    cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
    cursor.close()
    connection.close()
    print(f"Dropped {name}" + (f" (closed {closed} open connection(s))" if closed else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Drop and recreate the application databases (ALL DATA IS LOST).")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--only", choices=sorted(DATABASES), help="only this database")
    args = ap.parse_args()
    chosen = [args.only] if args.only else ["test", "production"]
    names = ", ".join(DATABASES[c][0] for c in chosen)
    if not args.yes:
        answer = input(f"This DELETES ALL DATA in {names}. Stop the application first. Type YES to continue: ")
        if answer.strip() != "YES":
            print("Nothing done.")
            return 1
    for c in chosen:
        drop(DATABASES[c][0])
    from persistence.Persistence import setDatabaseName, getMachineSettings, getDatabaseName
    for c in chosen:
        name, mode = DATABASES[c]
        setDatabaseName(mode, createDB=True)
        settings = getMachineSettings(getDatabaseName())
        shown = {k: settings.get(k) for k in ("rotation_rpm", "tightening_torque1_setpoint", "tightening_torque2_setpoint",
                                              "no_of_rotation1_ccw", "no_of_rotation1_cw")} if settings else settings
        print(f"Created {name}: schema, tables, roles, default users; machine settings {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
