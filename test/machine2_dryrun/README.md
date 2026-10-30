# Machine 2 synthetic dry-runs and interactive test windows

Harnesses used to verify the Machine 2 (28-state) changes without a PLC, camera or QR scanner.
All need Redis on localhost:6379; the DB ones need PostgreSQL on 127.0.0.1:5432 (postgres/postgres).
Run from a scratch directory: image files are written under `C:/AshokLeylandImages/...` relative to it on non-Windows.

| Script | What it exercises |
|---|---|
| `DryRun_IOServer_Two_Full_Cycles.py` | Real `costhetaio/allenbradley/IOServer.py` with a simulated Allen-Bradley PLC (in-memory tags) and simulated QR/camera servers; runs two full cycles and prints the state trace, the state-2 cycle settings written, and the IO->FE messages. |
| `DryRun_IOServer_Fault_Scenarios.py` | Same harness, the non-happy paths, with automatic PASS/FAIL checks (exit code 1 on failure): (A) camera NOT OK -> OK=False/Done=True, back to the picture state, retried, cycle completes; (B) rejected QR -> IOServer stays in WRITE_QR_CODE and writes nothing to the PLC (current behaviour, see CHANGES section 9), the accepted code then proceeds with its own side/model; (C) emergency abort mid-cycle -> ABORT to QR server, emergency to FE, the 7 key queues cleared, all 37 per-cycle handshake tags reset (explicit list), QR re-armed, and a full cycle afterwards. |
| `DryRun_DBServer_And_Persistence.py` | Persistence + real DBServer, 33 automatic checks: DB/schema/table set-up (Machine 2 columns, dropped free-rotation columns), roles/admin, direct `insertData()` field by field, `checkIfQRCodeExists()`, a full FE record through `fe2db.q` stored field by field with the 9 images saved as distinct files pixel-identical to those sent, DUPLICATE remark on a re-sent QR, an aborted-cycle record, report/counter functions, clean-up. Covers `test/Test_DBServer_And_Persistence.py`. |
| `DryRun_FEServer_Cycles.py` | The real frontend alone (PySide6, headless) with QR/camera/IO simulated on Redis and its `fe2db.q` record captured, automatic checks: all-OK cycle (step order, tiles, QR display, torque rounding, pictures pixel-identical, success screen, one record), camera NOT OK then OK, IO message for the wrong state ignored, emergency abort mid-cycle (partial record, abort screen, next cycle), final NOT OK (failure screen). Covers the automatable part of the three `test/Test_FEServer_*_UI.py` programs. |
| `DryRun_FEServer_To_DBServer_Pipeline.py` | Frontend record -> `fe2db.q` -> real `persistence/DBServer.py` -> PostgreSQL (test DB); prints the stored row. |
| `DryRun_All_Servers_Integrated_Cycle.py` | Everything together: simulated PLC/camera/QR + real IOServer, Frontend (PySide6), DBServer and HeartbeatAndAlarmServer; one cycle, saves `integrated_end_of_cycle.png` and prints the stored DB row. |
| `DryRun_DBServer_No_Record_Loss.py` | DBServer FIFO/no-loss behaviour: burst of records, repeatedly failing insert (retry then `FailedRecords/` JSON), drain on stop. |
| `Verify_Vision_Config_Equivalence.py` | Static proof that the configurable vision parameters equal the previous hard-coded values (see CHANGES section 11). |

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
