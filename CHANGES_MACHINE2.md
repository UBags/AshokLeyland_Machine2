# Machine 2: frontend, database, Redis and IOServer changes

- **Branch:** `machine2-frontend-db`
- **Base:** `main` @ `5633458`
- **Date:** 2026-10-30
- **PLC:** still Allen-Bradley (`pycomm3.LogixDriver`). The Siemens port comes later.

## Why

Machine 2 has the same sequence as Machine 1 except for one difference. The free rotation now happens *during* tightening at station 2, so Machine 1's separate state 13 (`READ_FREE_ROTATIONS_DONE`) is gone. Machine 1 states 14–29 become states 13–28, and the cycle wraps to state 1 after state 28.

In state 2, the PC now writes side, model, tightening-torque setpoints and rotation RPMs. The rotation counts are no longer written.

---

## 1. Database: `persistence/`

| File | Change |
|---|---|
| `Persistence.py`: DDL (`create_tables_string`) | Removed `free_rotations_done` and `free_rotations_datetime` from `hub_and_disc_assembly_data`. |
| `Persistence.py`: DDL migration | Added `ALTER TABLE … DROP COLUMN IF EXISTS free_rotations_done / free_rotations_datetime`. It runs on every start through `setDatabaseName()` → `createAllTables()`. A database created by Machine 1 code loses these two columns; all other data is kept. On a new database the statement does nothing. |
| `Persistence.py`: `insertData()` | Removed the two parameters, their result normalisation, the `(result, datetime)` pair in `step_data`, and the two columns in the INSERT. |
| `Persistence.py`: report queries | Removed the two columns from `all_columns_record_header` and from every SELECT behind the frontend reports: `getDataByModelName`, `getDataByModelNameAndDateLimits`, `getDataOfTodayByModelNumber`, `getDataByDateLimits` and `getAllDataOfToday`. These now produce a 49-column CSV. |
| `Persistence.py`: legacy template `insert_table_data` | Removed the two columns. |
| `DBServer.py` | Removed the two fields from the unpacking of `readDataInDatabaseServerFromFEServer()` and from the `insertData()` call. |
| `Test_DBServer_Persistence.py` (now `test/Test_DBServer_And_Persistence.py`, see §8) | Removed the two fields from its test records. |

The `machine_settings` table is left in place (still created and seeded) but nothing reads it any more. The frontend's Machine Settings menu was removed (see §8).

## 2. Redis messaging: `utils/RedisUtils.py`

| Link | Change |
|---|---|
| **FEServer → DBServer** (`fe2db.q`) | `sendDataFromFEServerToDatabaseServer()` lost the `freeRotationDone` and `freeRotationDatetime` parameters and stream fields. `readDataInDatabaseServerFromFEServer()` no longer reads them, and its return tuple (all 3 return paths) is 2 elements shorter. The key constants `freeRotationDoneKeyString` and `freeRotationDatetimeKeyString` were removed. The record now has 44 fields. |
| **IOServer → FEServer** (`io2frontend.q`) | No format change. The IO server no longer sends a `READ_FREE_ROTATIONS_DONE` message, and the frontend no longer waits for one. States travel as their integer values, and both ends use the same renumbered enum. |
| **FEServer → IOServer** (`fe2io.q`) | No change. The queue is still unused by the frontend. |
| **MainProgram ↔ FEServer** (`stopcommandQ` / `stoppedresponseQ`) | No change. Neither side refers to state numbers or to the removed fields. |
| **Monitoring/heartbeat server ↔ all** (`*2hb.q`, `hb2fe.q`, `hb2io.q`, `alarm.q`) | No change. Payloads are ALIVE/DEAD and alarm only. |

## 3. State machine and configuration

| File | Change |
|---|---|
| `statemachine/StateMachine.py` | `MachineState` now has the 28 Machine 2 states. `READ_FREE_ROTATIONS_DONE` was removed and later states renumbered (13 = bunk for component press … 28 = free-rotation torque). The instruction map is now built from the enum, using `state.<value>.instruction`, instead of 29 hand-written lines. `MAX_SECONDS_TO_WAIT_FOR_CAMERA_FEEDBACK` moved out of the IntEnum; inside it, it had silently become an alias of state 5. Wrap and clamp logic still keys off `READ_FREE_ROTATION_TORQUE_1`, now 28. |
| `ApplicationConfiguration.properties` | `state.13.instruction` (free rotation) removed; 14–29 renumbered to 13–28. Per-state image sizes renumbered from `machine.state.{14,17,21,23,25}` to `{13,16,20,22,24}`. Machine 1 rotation-count tag names (`NoOfRotation*`, `RotationUnitRPM`) replaced by the Machine 2 tags and setpoints below. |
| `Configuration.py` | `MACHINE_STATE_{14,17,21,23,25}_TARGET_*_KEY` renamed to `{13,16,20,22,24}`. New getters for the Machine 2 tag names and setpoints. Setpoints default to **-1**, as do missing or invalid values. |

New properties (Allen-Bradley tag names are placeholders; change them to match the PLC):

```properties
pc.plc.lh.rh.selection.tagname=LH_RH_Selection              # INT  1=LHS, 2=RHS
pc.plc.model.selection.tagname=ModelSelection               # INT  1=DOST, 2=DOSTPLUS
pc.plc.tightening.torque1.setpoint.tagname=TighteningTorque1Setpoint   # REAL Nm
pc.plc.tightening.torque2.setpoint.tagname=TighteningTorque2Setpoint   # REAL Nm
pc.plc.rotation1.rpm.tagname=RotationRPM1                   # INT
pc.plc.rotation2.rpm.tagname=RotationRPM2                   # INT
tightening.torque1.setpoint.nm=19.0      # -1 = do not write
tightening.torque2.setpoint.nm=19.0      # -1 = do not write
rotation1.rpm=-1                         # -1 = do not write  <-- set the real value (5..30)
rotation2.rpm=-1                         # -1 = do not write  <-- set the real value (5..30)
```

## 4. IOServer, Allen-Bradley: `costhetaio/IOServer.py`

- **State 13 removed.** The `READ_FREE_ROTATIONS_DONE` branches are gone from `getCurrentTagsToBeRead()` and `process_current_read_state()`. The class attribute `PLC_PC_Station3RotationDone` and its entries in `resetAll()` and `getAllTagValues()` are removed as well. As a result, the station-3 tag (`PLC_PC_Station3ValueSet`) is now used only in state 27, and the old shared-tag hazard between states 13 and 28 disappears.
- **State 2 writes**: `writeRotationSettingsToPLC()` is replaced by `writeCycleSettingsToPLC()`.
  - `LH_RH_Selection` (1/2) and `ModelSelection` (1 = DOST, 2 = DOSTPLUS) come from the QR code.
  - The torque setpoints are written as REAL and the RPMs as INT. **Any setpoint of -1 is skipped.**
  - An unknown side or model logs an error and writes nothing, which is the same behaviour as Machine 1 for an unknown side.
  - Machine settings are no longer read from the database, and the `getMachineSettings` import was dropped.
- **Cycle-time T4** now runs from the nut/plate-washer trigger to the reading of torque 1. Previously it ended at the "free rotation done" step, which no longer exists.
- No other changes. The handshake, emergency abort, heartbeat, watchdog and torque reset logic are untouched.

## 5. Frontend: `frontend/`

| File | Change |
|---|---|
| `newwidgets/CosThetaResultsContainer.py` | "Free Rotations" tile removed. New **"Free Rotation Torque"** tile added for the station-3 reading; Machine 1 squeezed this value into the Cap Press tile. Tiles are re-ordered by the Machine 2 sequence. Row 1: Knuckle, Hub, Top Bearing, Nut & Washer, Torque 1, **Bunk - Comp Press** (renamed), Component Press. Row 2: Remove bunk, Torque 2, Split Pin & Washer, Cap, Bunk Check, Cap Press Result, Free Rotation Torque. Matching setters and defaults updated. |
| `AshokLeylandFrontEnd.py` | Free-rotation state, handlers and reset removed. After torque 1, the frontend goes straight to the bunk-for-component-press picture. The free-rotation torque goes to its own tile, and "Cap Press Result" now shows only "Done". The overall OK/NOT OK result no longer includes free-rotation-done, and the DB record no longer carries it. |
| `AshokLeylandFrontEnd.py`: **bug fix** | Torques were truncated with `int(value*100)/100`, so 19.08 became **19.07** on screen *and in the database* because of float error. This was found in the dry run and replaced with `round(value, 2)` for torque 1, torque 2 and the free-rotation torque. |

## 6. Verification (synthetic dry runs; harnesses in `test/machine2_dryrun/DryRun_*.py`)

| Run | Result |
|---|---|
| Migration: database built with `main` (Machine 1) code plus one Machine 1 row, then Machine 2 code started on it | 52 → 50 columns. Only `free_rotations_done` and `free_rotations_datetime` were dropped, and the existing row survived. A second start makes no further change. |
| FE record → `fe2db.q` → real DBServer → PostgreSQL | Row stored with correct model, side, torques and results; image files written. |
| All five report queries plus the three counters | Run without errors. CSVs are a consistent 49 columns with no `free_rotations*` columns. |
| IOServer with a simulated Allen-Bradley PLC, two full cycles | The state trace is exactly `…26, 27, 28, 1, 2, 3 … 28`. In state 2 it wrote `LH_RH_Selection=1`, `ModelSelection=1`, and both torque setpoints at 19.0; the RPMs were correctly **not** written (-1). IO→FE messages: torque 1, component press, torque 2, cap press, then free-rotation torque with `moveAheadToNextComponent`. |
| Real frontend rendered at 1920×1080 (offscreen), fed a full cycle over Redis | Layout matches the agreed mock-up. The frontend walked the 15 operator-facing states in order and emitted a 44-field DB record. |
| **Integrated**: simulated PLC, camera and QR, with the real IOServer, frontend, DBServer and heartbeat server, for one DOSTPLUS RHS cycle | All four connection tiles green. State 2 wrote side=2, model=2. The DB row reads DOSTPLUS / RHS / 2.5T / 19.08 / 19.03 / 2.41 / all OK. |

## 7. Not changed, and open items

- **Not updated:** `siemens/IOServer.py`, the Siemens port. It will be redone for the S7-1515 in the next phase.
- `ApplicationConfiguration - Production - 29-01-2026.properties` (the Machine 1 snapshot) is left untouched. **Do not deploy it on Machine 2.**
- **To set before commissioning:** `rotation1.rpm` and `rotation2.rpm`; confirm the 19.0 Nm torque setpoints; the actual PLC tag names for the six state-2 tags.
- The reporting web services (`endpoints/`, started by `OtherServices.py`) still query the old Machine 1 schema and remain broken. This is a pre-existing issue.

## 8. Second round (2026-10-30, later)

### Production code
- **Machine Settings menu removed** from the frontend (`AshokLeylandFrontEnd.py` menu and Operator-role disable; `SimplePopups.updateMachineSettingsDialog()` deleted). Setpoints come only from the properties file.
- `costhetaio/MockPLC.py`: rotation-done tag removed, the six state-2 tags added. `IOServer(mockInstance=True)` now imports it as `costhetaio.MockPLC`, so it works when run as a package.
- `IOServer.getAllTagValues()` (the `publishTags`/`receiveTags` tag dump used by the real-PLC tester) now includes the six state-2 tags and `PC_PLC_ConnectionStatus`. It reports the state as its **name**: `str(IntEnum)` is just `"1"` on Python 3.11+.
- Unused property `plc.pc.station3.rotation.done.tagname` removed. Its getter stays, with a default.
- **Frontend bug fix:** `showATemporaryLargeImage()` tested `(result is not None) or …`, which is always true, so NOT OK images were shown for the OK time (0.5 s) instead of the NOT OK time (2.5 s). It now tests the result itself.

### Test programs: updated to the 28-state sequence, renamed, all in `test/`
Every program's docstring says what it tests and how to run it: `python -m test.<Module>` from the project root, or `python test/<file>.py`.

| New name | Was | What it does | Verified here |
|---|---|---|---|
| `Test_DBServer_And_Persistence.py` | `persistence/Test_DBServer_Persistence.py` | Persistence + DBServer: direct and Redis inserts, retrieval, duplicate handling (stored with `remarks='DUPLICATE'`, since `qr_code` is not unique), image files, datetimes | Yes: 10/10 against Postgres + Redis |
| `Test_LoggingServer.py` | `test/TestMasterConsoleAndMasterFileLoggers.py` | Starts the logging server as MainProgram does, logs at every level via the slave loggers, checks console/file filtering, queue drain and clean stop | Yes: 16/16 |
| `Test_FEServer_Mock_With_Image_Files_UI.py` | `test/IntegratedTestWithoutCamera_UI.py` | Drives the running frontend; runs the real `camera/Check*.py` algorithm on image files you pick and sends the result | Yes: full cycle on the real frontend (algorithms needing torch not available here) |
| `Test_FEServer_With_Live_Camera_UI.py` | `test/IntegratedTestWithCameraToTestHappyPath_UI.py` | As above, plus live mode: sends take-picture commands to the CameraProcessorServer and waits for its result | File mode: yes. Live mode: yes, against a stand-in camera server (no camera here) |
| `Test_FEServer_Simulated_Inputs_UI.py` | `test/TestingFrontEndServer.py` | Plays the QR, camera and IO servers towards the frontend with fixed image files (`AL_TEST_IMAGE_DIR`); Auto-Run All OK | Yes: full cycle on the real frontend |
| `Test_IOServer_With_Mock_PLC_UI.py` | `costhetaio/TestIOServerWithMockPLC.py` | Real IOServer on MockPLC; buttons for each PLC signal and QR/camera reply; tag monitor including the state-2 settings; Auto Cycle | Yes: 1→28→1, NOT OK retry, settings written |
| `Test_IOServer_With_Real_PLC_UI.py` | `costhetaio/TestIOServerWithPLC.py` | Runs alongside an IOServer connected to the real PLC; answers QR/camera and reads the tag dump | Yes: against an IOServer on MockPLC (no PLC here) |
| `Test_IOServer_With_Simulated_QR_And_Camera_UI.py` | `test/TestingIOServer.py` | Plays the QR and camera servers towards a running IOServer, OK / NOT OK per step | Yes: real IOServer walked 28 states, NOT OK retry |
| `Test_Integrated_All_Servers_Running_UI.py` | `test/TestAshokLeylandIntegration.py` | With all other servers up, plays the IOServer: commands QR/camera servers and sends torque/press values to the frontend, step by step | Yes: real frontend + DBServer + heartbeat; DB row correct |
| `Test_CameraServer_Live_Camera.py` | *(new)* | Sends take-picture for the 9 picture states (component QR first), saves original and processed images, prints a pass/fail table; `--start-server` runs the CameraProcessorServer in-process | Against a fake camera responder only (CameraProcessorServer needs torch, models/, ffmpeg) |

`test/machine2_dryrun/` harnesses renamed `DryRun_IOServer_Two_Full_Cycles.py`, `DryRun_FEServer_To_DBServer_Pipeline.py` and `DryRun_All_Servers_Integrated_Cycle.py`.

### Superseded copies (not updated; recommend deleting)
`test/MockPLCTest.py`, `test/wip/` (MockPLCServer.py, TestIOServer.py, TestIOServerResponses.py, TestIOServerWithMockPLC.py), `test/StartMasterLoggersInACommandShell.py` + `test/E.py` (the logger launcher it starts). They reimplement or duplicate the programs above, and the IOServer ones use the removed Machine 1 state 13.

### Issues found
- **Fixed (round 3):** `DBServer` lost records. The `fe2db.q` reader kept only the newest message and deleted on read. It now reads oldest-first and deletes only after a successful insert. A failed insert is retried 3 times, then written to `<images>/<mode>/FailedRecords/*.json`. The queue is drained before stopping, and the poll interval is 1 s instead of 5 s. Dry run: `test/machine2_dryrun/DryRun_DBServer_No_Record_Loss.py`.
- **Fixed (round 3):** `camera/CheckTopBearing.py` swallowed an import failure of `utils.ImageNormalisationWithMask`, which needs scipy. The import is now required, and the unused matplotlib import is removed. In addition, `CameraProcessorServer` now verifies at start-up the segmentation engines, `mobile_sam` and the model weights, and loads the models up front. If anything is missing it exits with a clear list instead of every check silently returning NOT OK.
- `frontend/AshokLeylandFrontEnd.py`: the picture worker class is declared as `GetQRCodeWorker` and works only through a later rebinding of the name. Not changed.

## 9. TO DECIDE LATER: QR code rejection (QR server left as is, by decision of 2026-10-31)
Current behaviour:
- **Unreadable or wrong-format scan:** the QR server silently keeps waiting. Neither the PLC nor the operator is told.
- **Valid format but unknown part number:** IOServer writes QR **OK + Done** to the PLC *before* trying to write side and model. That write then fails, and the PLC carries on with missing or stale settings.
- **Duplicate QR:** allowed. It is only labelled on screen and in the DB.

Fix proposed but not approved:
- The QR server sends a reject for a bad format or an unknown model/side.
- In state 2, IOServer writes the settings first and only then QR OK + Done. On a reject or a failed write it sends OK=False + Done=True and returns to state 1, the same handshake as a vision NOT OK. It clears QR OK/Done before each new read.
- The frontend shows the reason in red.
- The PLC handles Done with OK=False as "re-scan".

Open questions: allow or reject duplicates; how to treat serial noise versus a genuine bad scan.

## 10. `ApplicationConfiguration.properties` clean-up (2026-10-31)

Each key was traced to its `Configuration.py` getter and then to that getter's callers.

- **Removed (29 keys):** nothing anywhere in the repo reads them, or only deleted legacy files did. They include:
  - old Geltec/KEMS camera, recording and Google Drive settings;
  - unused queue names (`read.qrcodeQ`, `send.qrcodeQ.*`, `recording1/2.queue`);
  - font multipliers, `terminal.debug`, `polling.time.for.disc.signal`, `report.include.invalidresults`, `consolelogging.date.format`, `qrcode.number.of.characters.to.check` and `consecutive.connect.retries`.

  Commented-out leftovers were removed as well (`wsdl.dir`, `qrcode.extractdata.N`, old queue names).
- **Moved, unchanged, into a "TO REVIEW" block (35 keys); on 2026-10-31 Uddi decided to delete the camera picture-delay, recording and audit keys (8). The 27 vision-check keys remain under review:** live code does not read them, but they may still matter.
  - Camera picture-delay tuning, used only by `PhotoCamera.py` and a Geltec test.
  - Video/picture recording settings, used only by `CameraRecorderServer.py`, which MainProgram does not start.
  - `audit.report.basedir`: the audit trail is commented out.
  - The bunk, nut/washer, no-cap-bunk and no-bunk check parameters, read only by superseded `camera/*_v0.x` files. The live checks hard-code these values.
- **Verification:**
  - No value of any remaining key changed; 258 keys remain.
  - Every key outside the review block is read by live code.
  - The integrated dry run, with all servers, passes.
