# Machine 2 synthetic dry-runs

Harnesses used to verify the Machine 2 (28-state) changes without a PLC, camera or QR scanner.
All need Redis on localhost:6379; the DB ones need PostgreSQL on 127.0.0.1:5432 (postgres/postgres).
Run from a scratch directory: image files are written under `C:/AshokLeylandImages/...` relative to it on non-Windows.

| Script | What it exercises |
|---|---|
| `io_cycle.py` | Real `costhetaio/IOServer.py` with a simulated Allen-Bradley PLC (in-memory tags) and simulated QR/camera servers; runs two full cycles and prints the state trace, the state-2 cycle settings written, and the IO->FE messages. |
| `db_pipeline.py` | Frontend record -> `fe2db.q` -> real `persistence/DBServer.py` -> PostgreSQL (test DB); prints the stored row. |
| `integrated.py` | Everything together: simulated PLC/camera/QR + real IOServer, Frontend (PySide6), DBServer and HeartbeatAndAlarmServer; one cycle, saves `integrated_end_of_cycle.png` and prints the stored DB row. |

`m1_frontend_sample.png` (a Machine 1 screenshot) supplies the inspection pictures shown in the frontend.
