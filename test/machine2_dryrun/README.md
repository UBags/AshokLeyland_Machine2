# Machine 2 synthetic dry-runs and interactive test windows

Harnesses used to verify the Machine 2 (28-state) changes without a PLC, camera or QR scanner.
All need Redis on localhost:6379; the DB ones need PostgreSQL on 127.0.0.1:5432 (postgres/postgres).
Run from a scratch directory: image files are written under `C:/AshokLeylandImages/...` relative to it on non-Windows.

| Script | What it exercises |
|---|---|
| `DryRun_IOServer_Two_Full_Cycles.py` | Real `costhetaio/allenbradley/IOServer.py` with a simulated Allen-Bradley PLC (in-memory tags) and simulated QR/camera servers; runs two full cycles and prints the state trace, the state-2 cycle settings written, and the IO->FE messages. |
| `DryRun_IOServer_Fault_Scenarios.py` | Same harness, the non-happy paths, with automatic PASS/FAIL checks (exit code 1 on failure): (A) camera NOT OK -> OK=False/Done=True, back to the picture state, retried, cycle completes; (B) rejected QR -> IOServer stays in WRITE_QR_CODE and writes nothing to the PLC (current behaviour, see CHANGES section 9), the accepted code then proceeds with its own side/model; (C) emergency abort mid-cycle -> ABORT to QR server, emergency to FE, the 7 key queues cleared, all 37 per-cycle handshake tags (38 on Siemens, with the rotation-done bit) reset (explicit list), QR re-armed, and a full cycle afterwards; (D) state-2 cycle settings: Allen-Bradley side/model + setpoints from the properties, Siemens side + the 5 settings in force in `machine_settings` (no model), the settings written published to Redis for the DB record, and (Siemens) a settings change made mid-cycle written in the next cycle; (E) values before Done: every Done=True after its OK, state-2 settings before QRCodeCheckOK/Done; a setting the PLC refuses (or, Siemens, no settings in the database: nothing written) -> QRCodeCheckOK=False, Done=True, back to the QR code, re-scan then runs the cycle. |
| `DryRun_DBServer_And_Persistence.py` | Persistence + real DBServer, automatic checks: DB/schema/table set-up (Machine 2 columns, dropped free-rotation columns), roles/admin, direct `insertData()` field by field, `checkIfQRCodeExists()`, a full FE record through `fe2db.q` stored field by field with the 9 images saved as distinct files pixel-identical to those sent, DUPLICATE remark on a re-sent QR, an aborted-cycle record, report/counter functions, clean-up; the IOServer's published cycle settings stored in the component's record (`setting_*` columns, NULL when none); `machine_settings`: default row, out-of-range values rejected by the code and by the database CHECK constraints, a valid change in force with `updated_by`, history. Covers `test/Test_DBServer_And_Persistence.py`. |
| `DryRun_FEServer_Cycles.py` | The real frontend alone (PySide6, headless) with QR/camera/IO simulated on Redis and its `fe2db.q` record captured, automatic checks: all-OK cycle (step order, tiles, QR display, torque rounding, pictures pixel-identical, success screen, one record), camera NOT OK then OK, IO message for the wrong state ignored, emergency abort mid-cycle (partial record, abort screen, next cycle), final NOT OK (failure screen); tightening tiles show the torque and, below it in a smaller font, Rotation Done / Rotation Not Done, and the record carries both; Machine Settings menu enabled for an Administrator, Operator refused, the dialog's entry checks; the GUI thread never blocked by Redis reads (emergency and server-status reads do not wait), status tiles keep their last status until the heartbeat server is silent for 3 intervals, emergency abort shown within about 1 s. Covers the automatable part of the three `test/Test_FEServer_*_UI.py` programs. |
| `DryRun_Logging_And_Live_Config.py` | Logging and live configuration, 37 automatic checks, on a copy of the properties file that the script edits while the code runs (`COSTHETA_CONFIG_FILE`), with the real central file logger writing to `logs/dryrun_logging/` and the real IOServer on the simulated PLC: per-server log levels applied while running (and raised to CONSIDER_ACTION in PRODUCTION), console prints mirrored to the file, `[QR=... cycle=N]` context, uncaught thread exception traceback in the file, ignored exceptions logged once per place, no silent loss (messages kept while Redis is down; a full queue counted and reported), size rotation and retention, live configuration (a change applied and logged; a half-written file ignored), vision constants re-bound while running, IOServer PLC audit trail in order (settings, QR OK, Done, state change, trigger, OK, Done), IOServer intervals applied live, restart warning for tag-name changes, messages queued before the logging server started written, stale ones counted; `logBoth` (the logging call of the production vision modules) mapped to the levels here, filtered by the server level, with the calling function recorded. |
| `DryRun_Heartbeat_And_Startup.py` | Heartbeats and start-up, 16 automatic checks (Redis only): the heartbeat server's single read of all 5 servers' heartbeats (latest heartbeat counts, no waiting per dead server, a DEAD heartbeat is not alive); the real heartbeat server marking the camera DEAD after the configured missed checks and ALIVE again; MainProgram no longer flushing Redis when a spawned server process re-imports it. |
| `DryRun_FEServer_To_DBServer_Pipeline.py` | Frontend record -> `fe2db.q` -> real `persistence/DBServer.py` -> PostgreSQL (test DB); prints the stored row. |
| `DryRun_All_Servers_Integrated_Cycle.py` | Everything together: simulated PLC/camera/QR + real IOServer, Frontend (PySide6), DBServer and HeartbeatAndAlarmServer; one cycle, saves `integrated_end_of_cycle.png` and prints the stored DB row. |
| `DryRun_DBServer_No_Record_Loss.py` | DBServer FIFO/no-loss behaviour: burst of records, repeatedly failing insert (retry then `FailedRecords/` JSON), drain on stop. |
| `DryRun_CheckKnuckle_Images.py` | The real CheckKnuckle on this machine's pictures (`knuckle_images/<MODEL>_<SIDE>_<n>.jpg`) with this machine's `<vendor>.vision.knuckle.*` settings: each picture OK with its own QR code and NOT OK with the other three, a dark object on the other end of the bar rejected, and every region at least 20 grey levels from its threshold (margins printed). Run again after re-tuning. |
| `Verify_Vision_Config_Equivalence.py` | Static proof that the configurable vision parameters equal the hard-coded values of the reference commit 5728def (the production vision files, see CHANGES sections 11 and 28); CheckKnuckle against its own reference commit (section 33). |

`m1_frontend_sample.png` (a Machine 1 screenshot) supplies the inspection pictures shown in the frontend.


## Interactive Tkinter test windows

Moved here from `test/` on 2026-10-31, after checking they match the current 28-state sequence and its tags. Run them
from the project root, e.g. `python test/machine2_dryrun/Test_IOServer_With_Mock_PLC_UI.py`.

| Window | Plays | Needs |
|---|---|---|
| `Test_IOServer_With_Mock_PLC_UI.py` | the PLC (in-memory MockPLC), QR and camera servers against the REAL IOServer; buttons per step, OK / NOT OK, QR accept / reject, auto cycle, tag monitor | Redis |
| `Test_IOServer_With_Real_PLC_UI.py` | the QR and camera servers against an IOServer connected to the real PLC; waits for the PLC-driven steps; shows live PLC tags | Redis, running IOServer + PLC |
| `Test_IOServer_With_Simulated_QR_And_Camera_UI.py` | the QR and camera results only, in order, towards a running IOServer | Redis, running IOServer |
| `Test_FEServer_Simulated_Inputs_UI.py` | the QR, camera and IO servers towards the running frontend; step by step or auto-run | Redis, running frontend |
| `Test_FEServer_Mock_With_Image_Files_UI.py` | as above, running the real camera checks on image files you pick | + the models (torch) |
| `Test_FEServer_With_Live_Camera_UI.py` | as above, or with the live camera through a running CameraProcessorServer | + camera / CameraProcessorServer |
| `Test_Integrated_All_Servers_Running_UI.py` | the IOServer, with every other server running | all other servers |

## Allen-Bradley or Siemens

`DryRun_IOServer_Two_Full_Cycles.py`, `DryRun_IOServer_Fault_Scenarios.py` and `DryRun_All_Servers_Integrated_Cycle.py`
run the Allen-Bradley IOServer by default. With the environment variable `COSTHETA_PLC_VENDOR=siemens` they run the
Siemens IOServer (`costhetaio/siemens/IOServer.py`) instead, on a simulated S7 data block (`_plc_sim.py`): every tag
goes through the real pyS7 backend, its DB500 address and the S7 byte encoding. PowerShell:

    $env:COSTHETA_PLC_VENDOR = "siemens"; python test/machine2_dryrun/DryRun_IOServer_Fault_Scenarios.py; Remove-Item Env:COSTHETA_PLC_VENDOR
