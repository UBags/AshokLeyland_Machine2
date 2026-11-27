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

## 4. IOServer, Allen-Bradley: `costhetaio/allenbradley/IOServer.py` (formerly `costhetaio/IOServer.py`)

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

The 27 vision-check keys in the review block were then deleted together with the superseded `camera/*_v0.x` files that read them (decision of 2026-10-31, option (a)). The live checks now read their own parameters instead, as described in §11.

## 11. Vision-check parameters made configurable (2026-10-31, option (b))

The goal was that a site engineer can re-tune a check from `ApplicationConfiguration.properties` without editing code, with **zero behaviour change** while the values are left as they are.

- **New `camera/VisionConfig.py`.** Each parameter is declared where it is used, e.g. `NUT_DETECTION_RADIUS = vInt("vision.nobunk.nut.detection.radius", 80)`.
  - The default is the value that was hard-coded before.
  - A missing key gives the default. An invalid value gives the default plus a printed warning.
  - Value formats: `x,y` for tuples, `a,b,c` for lists, `x1,y1;x2,y2` for points.
  - At start-up the camera server logs every parameter that differs from its built-in default, or "all at the built-in defaults".
- **New properties section "VISION CHECK PARAMETERS".** It has 124 `vision.*` keys, grouped and commented per check, all set to today's values:

  | Check | File(s) | Keys |
  |---|---|---|
  | Knuckle | CheckKnuckle | 33: side boxes, common checks A–E, polygons, darkness and binarisation thresholds |
  | Hub & bottom bearing | HubAndBearingSegmenter, CheckHubAndBottomBearing | 28: segmentation annulus, mask area 100–3000, shape and spacing tolerances, centre check (mean < 144) |
  | Top bearing | CheckTopBearing | 24: annulus, gamma candidates and centre offsets, score > 0.65, G > 77, final delta > 30, ring thickness, edge windows |
  | Nut & washer | CheckNutAndPlateWasher | 5: washer R / R-B thresholds, gamma, nut search disc |
  | Hexagon nut detector | HexagonNutDetector | 20: mask area 3000–5500, vertices 5–8, aspect ratio 0.7–1.3, centre disc, symmetry < 55, thickness < 45, deviated-pixel rules |
  | No bunk | CheckNoBunk | 2: nut search disc |
  | Bunk (both bunk checks) | BunkSegmenter | 12: crop discs, mask area 325–1200, hole size, DOST square criteria, DOSTPLUS hole distance 75–100 |

- **Cap.** It already read the `cap.check.*` keys. Its DOST hub, top and nut thresholds are now read as well; the values are unchanged at 30/30/35. The `Configuration.py` fallback for the DOST nut threshold was corrected from 30 to 35, to match.
- **Deliberately left hard-coded:**
  - **Expected hole counts (DOST 4, DOSTPLUS 5 for hub; 4/2 for bunk).** These describe the product, and the search logic is built around them.
  - **The bunk gamma sequence.** A value without a matching lookup table would make every part fail.
  - **Intermediate image-processing settings, debug flags, and constants the code never reads.**
- **Unused constants (added 2026-10-31 at Uddi's request):** the 12 vision constants that no live code reads are now also read from the file, in a separate section "UNUSED CONSTANTS" (`vision.unused.*`): 3 bunk, 8 hub, plus the top-bearing `max.allowed.center.percent.deviation`, which was moved there from the main section. Changing them has no effect today. The section now totals 123 live keys plus 12 unused, 135 in all, and the verifier passes on all 135.
- **Findings at the time (now handled as above):**
  - **Bunk:** `EQUIDISTANT_DISTANCE_DEVIATION_THRESHOLD = 0.25`, `MINIMUM_INTERIOR_ANGLE` and `ALLOWED_STD_DEVIATION_IN_ANGLES` are not read anywhere. The live square tolerance is 0.2, which is now `vision.bunk.square.*`.
  - **Hub:** `POLYGON_*`, `GOOD_FIT_*`, `DOMINANCE_RATIO` and `REGULARITY_*` feed only functions nothing calls.
  - **Debug and log messages (fixed 2026-10-31):** 21 messages printed thresholds as fixed text. They now print the configured constant: top bearing's validation reason (3), the hexagon nut detector's reject reasons and failure details (16), and the bunk DOSTPLUS distance range (2). At the default values the text is character-for-character the same as before, which the verifier checks by formatting each message with its default. Each message was also checked to print the constant its own decision uses; this matters where two constants share a value, such as 0.65 for `LOWER_SCORE_THRESHOLD` and `INNER_EDGE_MIN_DIST_FACTOR`. Earlier note: some still printed the old numbers (e.g. "< 55", "[75, 100]"). If a value is re-tuned, those messages will be stale. The decisions themselves use the configured value.
- **Verification:**
  - `test/machine2_dryrun/Verify_Vision_Config_Equivalence.py`, with `vision_config_manifest.json`, checks all 124 parameters in 8 files. After each configurable value is replaced by its default, every module's syntax tree is **identical** to the baseline (commit 39ead96). Every default equals the old value, type included. Every properties value parses back to exactly that default. All keys are unique. The verifier was mutation-tested: a changed default, a changed use site and a malformed value are each caught.
  - Every key read through the real `CosThetaConfigurator`: 124 of 124 come from the file, and none differs from its default.
  - Classical checks, old code against new code, on a fixed seeded image set (both pass and fail outcomes): knuckle, cap and top bearing outputs (verdict, annotated-image hash, geometry) are byte-identical. Top bearing's RANSAC is unseeded and multi-threaded, so the bench seeds it and runs its workers one at a time.
  - Re-tune test: raising `vision.knuckle.common.check.threshold` changes the verdicts and is reported at start-up. A malformed value falls back to the default with a warning.
  - The torch-based checks (hub, nut, bunk) cannot be run in the sandbox, so they rely on the structural proof above.

`ApplicationConfiguration.Machine1.properties` is a byte-identical copy of `ApplicationConfiguration.properties` as of this step (347 keys, 124 of them `vision.*`). It was made at Uddi's request, and nothing in the code reads it.

## 12. Package layout for the PLC and QR code servers (2026-10-31)

| Before | After |
|---|---|
| `costhetaio/IOServer.py`, `IOServer_ModbusTCP.py`, `MockPLC.py` | `costhetaio/allenbradley/` |
| `costhetaio/QRCodeScanningServer.py` | `costhetaio/qrcodescanner/` |
| `siemens/` (IOServer, plc_backend, s7_smoke_test, IOServerProperties.yaml) | `costhetaio/siemens/` |

- **Moved with `git mv`,** so each file keeps its history. `costhetaio/QRCodeServer.py`, the older QR server that MainProgram does not start, stays where it is.
- **References updated:**
  - MainProgram's lazy imports for the QR and IO server processes;
  - the IOServer's `mockInstance` import of MockPLC, and the usage note in MockPLC's docstring;
  - the IOServer test programs, the dry-run harnesses and their README.
- **Siemens IOServer:**
  - It imports `plc_backend` package-qualified, falling back to the sibling import when run from its own folder.
  - `IOServerProperties.yaml` is now found next to the module instead of in the current directory. Before, it was only found when the program was started from inside `siemens/`.
- **`ApplicationConfiguration.Machine2.properties`** is a copy of `ApplicationConfiguration.properties` as of this step. Like the Machine 1 copy, nothing in the code reads it.
- **Verified:**
  - All moved modules import from their new paths.
  - The IOServer dry run gives the same two-cycle state trace as before.
  - The integrated all-servers dry run passes, and its DB row is unchanged.
- **Already broken before the move, not fixed:** `costhetaio/siemens/IOServer.py` imports `logBoth` from `logutils.SlaveLoggers`, and that function doesn't exist. It will be dealt with in the Siemens S7-1515 port.

## 13. IOServer fault-scenario dry run; `test/wip` removed (2026-10-31)

- **New `test/machine2_dryrun/DryRun_IOServer_Fault_Scenarios.py`** (also copied to `test/machine1_dryrun/`). It uses the same harness as the two-cycle dry run, with the real IOServer, and has 35 automatic checks (exit code 1 on failure):
  - **A. Camera NOT OK.** IOServer writes OK=False with Done=True, goes back to the picture state, asks the camera again and retries. The cycle then completes. This is exercised on the knuckle and cap steps.
  - **B. Rejected QR.** IOServer stays in WRITE_QR_CODE and writes nothing to the PLC: no QR OK/Done and no cycle settings. This is the current behaviour; see §9. The re-scanned, accepted code then proceeds with its own side and model.
  - **C. Emergency abort mid-cycle.** IOServer must:
    - send ABORT to the QR server and the emergency message to the frontend;
    - clear the 7 key queues of the stale messages planted beforehand;
    - reset all 37 per-cycle handshake tags. The test sets each to True or 12.34 first and checks it against an explicit list, so a tag dropped from `resetAll()` is caught.
    - re-arm the QR read, after which a full normal cycle completes.

    The test also fails if a new PLC tag is added to IOServer without being listed as reset or not reset on abort.
- **Mutation-tested:** each of these deliberately planted faults in the IOServer is caught:
  - NOT OK does not go back to the picture state;
  - the abort does not clear the queues;
  - `resetAll()` skips one bool tag;
  - `resetAll()` skips one torque reset;
  - a rejected QR is treated as accepted.
- **`test/wip/` deleted.** Its four programs used Machine 1's 29-state sequence and tested no real code:
  - one GUI's simulated PLC tags were not connected to the IOServer;
  - one was a separate re-implementation of the IOServer;
  - one imported a module that does not exist.

  Their features are covered by this dry run, `DryRun_IOServer_Two_Full_Cycles.py`, and the three `test/Test_IOServer_*_UI.py` programs. Two gaps remain in the interactive Mock-PLC window: it has no emergency-abort button, and the abort path is now covered only by this dry run.

## 14. DB and frontend dry runs (2026-10-31)

Both are in `test/machine2_dryrun/` and copied to `test/machine1_dryrun/`. Each has automatic PASS/FAIL checks and exits with code 1 on failure.

- **`DryRun_DBServer_And_Persistence.py`: 33/33 pass.** It covers everything in `test/Test_DBServer_And_Persistence.py`, checked field by field:
  - **Set-up:** `setDatabaseName` creates the DB, schema and table. The Machine 2 columns are present and the dropped free-rotation columns are absent. The 3 roles and the admin user exist.
  - **Direct inserts:** `insertData()` stores each value as given, with results normalised, unparsable torques stored as -1.0 and unset steps as "Not Checked". `checkIfQRCodeExists()` gives true for a stored code and false for an unknown one.
  - **FE record through `fe2db.q` and DBServer:** all 32 result, torque, datetime and overall fields are stored exactly, and model, side and tonnage are taken from the QR code. The 9 images are saved as 9 distinct files, each pixel-identical to the image sent.
  - **Duplicate QR:** the re-sent code is stored as a second row with remarks DUPLICATE.
  - **Aborted-cycle record:** unreached steps are stored as NOTOK with 1970 datetimes and no image file.
  - **Reports and counters:** the report functions return the right rows, and the OK counter counts only OK rows.
  - **Clean-up:** every row and image file the script created is removed.
- **`DryRun_FEServer_Cycles.py`: 42/43 pass.** It runs the real frontend alone, headless, with the QR, camera and IO servers simulated and its `fe2db.q` record captured. It covers the parts of the three `test/Test_FEServer_*_UI.py` programs that can be automated:
  - **All-OK cycle:**
    - the frontend walks its steps in Machine 2 order, with every tile set and the QR display string shown;
    - torques are rounded to 2 decimals and the 9 pictures are pixel-identical;
    - the success screen appears and exactly one record is sent.
  - **Camera NOT OK, then OK:** the NOT OK result is shown on the tile and the frontend stays on that step until the OK arrives.
  - **IO message for the wrong state:** it is consumed and ignored.
  - **Emergency abort:** a partial record is sent with overall NOT OK, the abort screen appears, and the next cycle runs normally.
  - **Final free-rotation torque NOT OK:** the record says NOT OK, but **the success screen is shown. This check FAILS on purpose: it exposes architecture finding R8.** `setInProcessDefaultDisplay()` tests `elif AshokLeylandFrontEnd.finalResult:`, and the string "NotOK" counts as true. The IOServer always sends torque results as OK, so today this only shows up when the frontend itself records a NOT OK final result.
- **Still manual:** running the real `camera/Check*.py` algorithms on image files, and the live camera, need real pictures, the models or the hardware. The `Test_FEServer_*_UI.py` programs remain for those.

## 15. Tkinter test windows checked and moved into the dry-run folders (2026-10-31)

The seven Tkinter test windows (`Test_IOServer_*_UI.py`, `Test_FEServer_*_UI.py`, `Test_Integrated_All_Servers_Running_UI.py`) were moved with `git mv` from `test/` to `test/machine2_dryrun/`, and copied to `test/machine1_dryrun/`. Their project-root path was adjusted for the deeper folder, and their run instructions were updated. Before the move, they were checked as follows.

- **Static check (all 7, in both folders):**
  - Every `MachineState`, `IOServer` tag and configuration getter they reference exists.
  - Every RedisUtils call matches the function's current signature, and every camera-check call matches the check method's parameters.
  - All project imports resolve.
  - None uses a hard-coded state number or a Machine 1-only name (free-rotations-done, Station3RotationDone, rotation counts, 29 states, the old `costhetaio` module paths).
- **Run headless and driven through their own buttons and methods:**
  - **Mock PLC window, 12/12:**
    - an auto cycle that walks states 1→28 and wraps, for DOSTPLUS LHS and RHS, with the correct side and model written in state 2;
    - a rejected QR followed by an accepted one;
    - knuckle NOT OK, then a retry;
    - Reset All, followed by another auto cycle;
    - the tag monitor.
  - **Simulated QR and camera window, 14/14:** run against the real IOServer with an automatic PLC. It covers all 10 results in Machine 2 order, a NOT OK followed by a retake, the cycle wrap, and the window returning to the QR step.
  - **Real PLC window, 5/5:** run against the real IOServer with an automatic PLC standing in for the PLC. It covers the tag request through `publishTags`/`receiveTags`, Run Full Cycle walking states 1→28 and wrapping, the state-2 settings, and the grouped tag display.
  - **Frontend simulated-inputs window, 24/24:** run with the real frontend. A manual cycle with a NOT OK on the knuckle sends all 15 messages, each consumed, followed by an Auto Run cycle. Each cycle's record reached `fe2db.q` with all 14 results OK.
  - **Frontend image-files window and live-camera window, 18/18 each:**
    - file mode in both windows, and live mode with a simulated camera server answering the take-picture commands;
    - all 15 steps go through in order, and the record has all results OK.

    The vision algorithms themselves were stubbed, because they need torch and real pictures.
  - **Integrated window, 19/19:** with the real frontend and simulated QR and camera servers, all 15 steps are delivered and consumed, and the record is all OK.
- **Auto Run All OK** in the frontend simulated-inputs window now starts from the current step and finishes the cycle the frontend is in. Before, it always restarted at the QR step, which stalled when pressed mid-cycle. Pressed at the QR step, it still runs a whole cycle. Verified in both folders, 11/11 checks each: a QR and a knuckle NOT OK sent by hand, then Auto Run from the knuckle to the end of the cycle, then a full Auto Run cycle; each record is all OK.
- **Deleted:** `test/MockPLCTest.py`, an old Machine 1 GUI with its own copy of the IOServer. It used Machine 1's free-rotations state and could not run against the current code. `Test_IOServer_With_Mock_PLC_UI.py` replaces it.

## 16. Siemens S7-1515 IOServer (2026-10-31)

- **Selecting the PLC:** `plc.vendor=allenbradley|siemens` in `ApplicationConfiguration.properties` decides which IOServer MainProgram starts; the default is allenbradley. For dry runs, the environment variable `COSTHETA_PLC_VENDOR` overrides it.
- **Siemens settings:** when the vendor is siemens, Configuration takes each key from its `siemens.<key>` counterpart. The new SIEMENS S7-1515 section in the properties file holds those keys:
  - the tag names from `HUb_and_Knuckle_Revised_Assembly_Process.xlsx`;
  - `siemens.plc.ip=192.168.1.10`.

  The Allen-Bradley keys are unchanged. MainProgram's PLC ping uses whichever IP applies.
- **`costhetaio/siemens/IOServer.py` is now a subclass of `costhetaio/allenbradley/IOServer.py`.** It replaces the old 1,689-line Machine 1 port. Only the PLC client changes: `connectPLC()` creates two `PyS7PLCBackend` clients (reads and writes) instead of two pycomm3 `LogixDriver` clients. Everything else is the Allen-Bradley code itself, so the functionality is identical by construction:
  - the 28 states and the OK, pause, Done handshake;
  - torque reads and the delayed tag resets;
  - the state-2 cycle settings (side, model, torque setpoints 1 and 2, RPM 1 and 2; -1 means skipped);
  - emergency abort, heartbeats and Redis messages;
  - tag publishing and mock-PLC mode.

  At start-up it refuses to run if any tag it uses has no address.
- **`costhetaio/siemens/plc_backend.py` was rewritten.** `PyS7PLCBackend` presents pyS7 through the LogixDriver interface (`open`, `close`, `connected`, `read(tag).value`, `write(tag, value)`). It resolves each tag name to its DB address through `IOServerProperties.yaml` and uses the pyS7 calls from `s7_smoke_test.py`:
  - the keyword constructor with fallbacks, and connect with retries;
  - `read([addr])[0]` and `write([addr], [value])`;
  - reading the byte and masking the bit when a bit read fails with INVALID_DATA_SIZE.

  Values are converted to the address type before writing, and an INT is range-checked to 16 bits. `load_tag_map()` rejects bad addresses, a type that disagrees with its address letter, two tags on one bit, and overlapping words.
- **`IOServerProperties.yaml`** holds all 46 tags in DB500, from the spreadsheet's last column, with the corrections of 2026-10-31:
  - Station 2 torque-set `X2.7` at both tightening steps;
  - cap-press done = `PLC_PC_Station4CapPressDone X3.2`; the cap-press-value-set bit is spurious and unused.

  These addresses are **provisional** until confirmed against the PLC programme:

  | Tag | Address | Reason |
  |---|---|---|
  | Component press done | `X3.5` | not in the sheet |
  | Split pin & washer Done / OK | `X0.5` / `X0.6` | the sheet's `X1.3` / `X1.4` are Cap OK / Done |
  | Station 3 torque-set | `X2.6` | `X2.7` is Station 2 |
  | Tightening torque setpoints 1 / 2 | `R18` / `R22` | old rotation-count slots |
  | RPM 1 / 2 | `I40` / `I42` | not in the sheet |
- **`PLC_PC_Station2RotationDuringTighteningDone`** (`X3.6`) is read and logged with each Station 2 torque value. No action is taken on it. It is also in the tag dump and the Real-PLC test window. On the Allen-Bradley its tag name is empty, so it is skipped.
- **`costhetaio/siemens/s7_tag_check.py` (new):** a read-only commissioning check. It validates the tag map, connects, reads every tag and checks the watch tag reads 1234.
- **`requirements.txt`:** adds `pyS7==3.1.2`, the PyPI release of github.com/xtimmy86x/pyS7.
- **Verified:**
  - The three IOServer dry runs now run against either PLC through `test/*_dryrun/_plc_sim.py`. The Siemens run uses a simulated S7 data block with real byte encoding, so address clashes would show up as failures, and bit reads fail once to exercise the fallback.
  - **Siemens:** fault scenarios 35/35; two full cycles with the state trace exact, the settings written to `PC_PLC_LH_RH_Selection` / `PC_PLC_ComponentType` / the setpoints, and the rotation-done bit read at both tightening steps; integrated run with all servers, giving the DB row DOSTPLUS/RHS/2.5T/19.08/19.03/2.41/all OK.
  - **Allen-Bradley:** unchanged, with the same results. `s7_tag_check.py` passes against the simulated PLC.
  - The tag-map validator catches the spreadsheet's three original clashes.

## 17. Final Siemens tag list; machine settings from the frontend and database (2026-10-31)

- **`costhetaio/siemens/IOServerProperties.yaml`** now holds the final list of 46 tags. All provisional remarks are removed. Changes from §16:
  - Station 2 torque-set is `X2.6`. Station 3 torque-set is `X2.7`.
  - Tightening torque setpoints 1 / 2 are at `R40` / `R44`.
  - New settings tags: `PC_PLC_RotationRPM` `R36`, `PC_PLC_NoOfRotation1CCW` `R18`, `PC_PLC_NoOfRotation1CW` `R22`. All are REAL.
  - `PC_PLC_ComponentType` is removed (the PLC does not need it). `PC_PLC_RotationRPM1/2` are removed.
  - Still unused: `NoOfRotation2CCW` `R26`, `NoOfRotation2CW` `R30`, `Station4CapPressValueSet` `X4.6`.
- **State-2 settings (`IOServer.writeCycleSettingsToPLC`)** now write only the tags that have a configured name. An empty name means the PLC does not have that setting. The source is chosen by `cycle.settings.source`:
  - **Siemens (`database`).** Writes `LH_RH_Selection` (INT, from the QR code). It also writes `TighteningTorque1/2Setpoint`, `RotationRPM`, `NoOfRotation1CCW` and `NoOfRotation1CW`, all REAL and all taken from the latest row of `machine_settings`. All of them are written every cycle. No model is written.
  - **Allen-Bradley (`properties`).** Unchanged: side, model, torque setpoints and RPM 1/2 from the properties, with -1 meaning "do not write".
  - The settings actually written are published to Redis under `cycleSettings:<QR>` (kept for 2 days). The DB server stores them in that component's record.
- **Database (`persistence/Persistence.py`).**
  - **New `machine_settings` table.** Every change adds a new row, with `updated_by` and the date. The settings in force are the latest row.
  - **Limits, enforced by the code and by CHECK constraints:**
    - RPM 20–40;
    - torque setpoints above 0 and below 20;
    - rotation counts 1 or 2.

    The code also requires RPM and counts to be whole numbers and torques to have at most 2 decimals. Values are stored as REAL.
  - **Default first row:** 30 rpm, 19.0 / 19.0 Nm, 1 / 1 rotations.
  - The old Machine 1 `machine_settings` table (with column `rotationunitrpm`) is dropped automatically. There is no migration; the old database had no production data.
  - **Functions:** `getMachineSettings`, `insertNewMachineSettings`, `getMachineSettingsHistory`, `validateMachineSettings`, `insertDefaultMachineSettings`.
  - **`hub_and_disc_assembly_data`** gains five `setting_*` columns: the settings used for that component, or NULL if none were published. They are added with `ADD COLUMN IF NOT EXISTS`, and they are in the reports.
- **Frontend.** A new **Machine Settings** menu (Ctrl+Alt+U) opens the Machine Settings dialog. Only Administrators and Supervisors can use it; for an Operator the menu item is disabled and the dialog refuses.
  - **Inputs:** RPM is a whole-number spin box (20–40); the torques take 2 decimals (0.01–19.99); the counts are a 1 / 2 choice.
  - The dialog shows the settings in force, and who set them and when.
  - Saving adds a new row with the user's name. The new settings apply from the next component.
- **Configuration.**
  - New keys:
    - `pc.plc.rotation.rpm.tagname`
    - `pc.plc.no.of.rotation1.ccw.tagname`
    - `pc.plc.no.of.rotation1.cw.tagname`
    - `cycle.settings.source`

    For Allen-Bradley these are empty or `properties`. For Siemens they are set, and the source is `database`.
  - The model and RPM 1/2 tag names may be empty; on Siemens they are.
  - `ApplicationConfiguration.Machine2.properties` has been re-copied from `ApplicationConfiguration.properties`.
- **TO-DO:** align the Allen-Bradley state-2 settings with Siemens later. That means dropping `rotation1/2.rpm` and the model, and taking the settings from the database instead of the properties.
- **Tests (both dry-run folders).**
  - `_plc_sim.py` has these additions:
    - `setting_tags()` returns the settings tags configured for the vendor;
    - for Siemens, `setup(mode)` creates the database and puts the default settings in force;
    - `use_machine_settings()` changes the settings.
  - **Fault scenarios, new section D:**
    - the settings written for each vendor;
    - the settings published for the DB record;
    - on Siemens, a change made during a cycle is written in the next cycle.
  - **DB dry run, new section 9:** the default row, rejection of invalid values by the code and by the CHECK constraints, a change put in force, the history, and the `setting_*` columns in the record.
  - **FE dry run, new F6:** the menu, the role restriction and the dialog's input checks.
  - The Mock-PLC and Real-PLC test windows list the vendor's settings tags.
- **Verified:**
  - Allen-Bradley: two cycles exact, fault scenarios 39/39.
  - Siemens: two cycles exact, writing 1 / 19.0 / 19.0 / 30.0 / 1.0 / 1.0; fault scenarios 42/42.
  - DB dry run: 44/44.
  - FE dry run: 48/48 after the R8 fix below.
  - Integrated runs with all servers: the Siemens DB row stores `setting_*` = 30 / 19 / 19 / 1 / 1. The Allen-Bradley row stores the torque setpoints from the properties (19 / 19), with RPM and counts NULL.

## 18. Frontend: failure screen for a NOT OK result (R8 fixed, 2026-10-31)

- `setInProcessDefaultDisplay()` tested `elif AshokLeylandFrontEnd.finalResult:`. `finalResult` is the string ok / notok, and both strings count as true, so the success screen was shown even for a NOT OK component. It now tests `== ok`.
- FE dry run F5, which failed on purpose to expose this, now passes: 48/48.

## 19. `costhetaio/siemens/s7_read_all_tags.py` (2026-10-31)

- **New read-only tool.** It reads every tag in `IOServerProperties.yaml` from the real PLC: all 46, in both directions. A PC→PLC tag can be read too; it shows what is held at that address.
- **How it reads.** Each tag is read separately, and any exception is caught and reported against that tag, so the run continues past it. The output is a table sorted by address (address, type, direction, tag, value) followed by a summary.
- **Options:** `--ip`, `--repeat N`, `--interval s`.
- **Exit codes:** 0 = every tag read; 1 = some reads failed; 2 = the PLC could not be reached.
- **Verified** against the simulated S7 (including one forced read failure, which was reported while the rest were read) and against an unreachable address (exit code 2).

## 20. Values before Done in every PC→PLC handshake; settings not written → QR NOT OK (2026-10-31)

- **State 2 (QR code).** The order of writes was wrong. Before, the PC wrote QRCodeCheckOK, then QRCodeCheckDone, and only then the cycle settings, so the PLC could see Done before the settings were in DB500. The order is now:
  1. **The cycle settings.** These are side, model (Allen-Bradley), the torque setpoints, RPM and rotation counts. All of them are worked out before anything is written: if any is unavailable (unknown side or model, no machine settings in the database), nothing is written. Each settings write gets 3 attempts.
  2. **QRCodeCheckOK, then QRCodeCheckDone.**
- **If the settings could not all be written (decision (b)).** The PC writes QRCodeCheckOK = **False**, then QRCodeCheckDone = True, and goes back to the QR code, exactly as for a NOT OK check. The cycle does not start and nothing is published for the DB record. Once the fault is gone, a re-scan writes the settings, OK = True and Done, and the cycle runs.
- **Every handshake (`perform_write_state_action`, used by the QR step and all 7 vision steps).**
  - The OK tag is written first and must succeed before Done is written. Before, Done was written even if the OK write had failed.
  - Each write is retried until it succeeds. It gives up only on shutdown, or if an emergency abort or heartbeat loss resets the cycle in the meantime; in that case Done is not written and the state is not advanced.
  - The other writes the PC makes to the PLC carry no "Done" with them: the connection-status toggle, re-arming CheckQRCode, resetting the torque or press tags after reading them, and the reset on abort.
  - `IOServer_ModbusTCP.py` is not used anywhere and was not changed.
- **New constants:** `IOServer.SETTINGS_WRITE_ATTEMPTS = 3` and `IOServer.WRITE_RETRY_INTERVAL = 0.2` s.
- **Fault scenarios, new section E (both vendors):**
  - every Done=True in a cycle is written after its OK, and in state 2 after all the settings;
  - a settings write refused by the PLC gives QR OK=False then Done=True, a return to the QR code, nothing published, and a re-scan then runs the cycle;
  - Siemens only: no settings in the database means no settings tag is written at all, then OK=False / Done, and a re-scan runs the cycle.
- **Verified:**
  - Fault scenarios: Allen-Bradley 47/47, Siemens 55/55.
  - Two full cycles: state order exact on both.
  - Integrated runs: database rows unchanged from before.
  - Mock-PLC test window: Allen-Bradley 13/13, Siemens 14/14.
  - One Allen-Bradley mock-window run failed (3/13, stuck in state 1) because it started straight after the integrated run, whose processes were still running against the same Redis. It passed 13/13 on two reruns by itself.
- **Open: frontend out of step when the QR is answered NOT OK because of the settings.** The QR server sends the QR code to the frontend at the same moment as to the IOServer, so the frontend moves on to the knuckle step while the IOServer goes back to the QR code. The operator's re-scan then stays queued for the frontend; it is picked up when the frontend next returns to READ_QR_CODE, which starts a cycle the IOServer is not in.

## 21. Siemens: state-2 cycle settings moved to DB101 (2026-10-31)

- **New addresses in `IOServerProperties.yaml`:**

  | Tag | Address |
  |---|---|
  | `PC_PLC_LH_RH_Selection` | `DB101,I246` (INT) |
  | `PC_PLC_TighteningTorque1Setpoint` | `DB101,R200` |
  | `PC_PLC_TighteningTorque2Setpoint` | `DB101,R204` |
  | `PC_PLC_RotationRPM` | `DB101,R96` |
  | `PC_PLC_NoOfRotation1CCW` | `DB101,R144` |
  | `PC_PLC_NoOfRotation1CW` | `DB101,R120` |

  The other 40 tags stay in DB500. DB500's I34, R18, R22, R36, R40 and R44 are now free.
- **PLC prerequisites:** DB101 must also have "Optimized block access" off.
- **Verified:** the tag map loads (46 tags across DB101 and DB500, no clashes), and the Siemens fault scenarios pass 55/55 on the simulated S7.

## 22. Siemens: LREAL support; RPM and rotation counts are LREAL (2026-10-31)

- **Finding.** A raw dump of DB101 showed that offsets 96, 120 and 144 hold 8-byte LReal values: 200.0, 3.0 and 0.0. Reading them as 4-byte Real gave 3.640625 and 2.125. Writing a 4-byte Real there would set only half of each value, so a setting of 30 rpm would be read by the PLC as about 4.3e9.
- **`plc_backend.py`** now accepts the `DB<n>,LR<byte>` address form (LREAL, 8 bytes, type `float`), which pyS7 already supports. The start-up validator counts LR as 8 bytes, so a tag overlapping one is rejected.
- **`IOServerProperties.yaml`:**
  - `PC_PLC_RotationRPM` = `DB101,LR96`
  - `PC_PLC_NoOfRotation1CW` = `DB101,LR120`
  - `PC_PLC_NoOfRotation1CCW` = `DB101,LR144`
  - The torque setpoints stay REAL (`R200` / `R204`), and the side stays INT (`I246`).
- **Simulated S7 (`_plc_sim.py`).** It now stores LREAL as 8 bytes and exposes `TAGS.raw(tag)`. Fault scenario D checks that the bytes in PLC memory are each value in its declared width.
- **Verified:**
  - Siemens: fault scenarios 56/56; two full cycles exact (30.0 / 1.0 / 1.0 written as LREAL).
  - Allen-Bradley: 47/47.
- **Resolved 2026-10-31.** Uddi confirmed that the defaults (30 rpm, 19.0 / 19.0 Nm, rotations 1 / 1) and the limits are correct. The 200.0 and 3.0 found at LR96 / LR120 were old values in the PLC.
- **Real PLC.** The six settings were written with the LREAL code and read back as LR96 = 30.000, LR120 = 1.000, LR144 = 1.000, R200 = 19.000, R204 = 19.000, I246 = 1. An earlier IOServer run with the old 4-byte REAL code had left LR96 ≈ 1.5e10 and LR120 = 0.0078, which matched the expected corruption exactly; this write repaired them.

## 23. Station 2 rotation-during-tightening done: frontend display and database record (2026-10-31)

- **IOServer.**
  - At both tightening steps (torque 1 and torque 2), `PLC_PC_Station2RotationDuringTighteningDone` (Siemens `DB500,X3.6`) is read with the torque value and sent to the frontend in the same message: new optional field `rotationDone` on `sendDataFromIOServerToFEServer`.
  - The bit is reset to False with the Station 2 torque tags 1.5 s after reading, the same way `Station2TorqueValueSet` and the torque value are reset, and in `resetAll()` on abort. This means torque 2 can't see a value left over from torque 1.
  - On Allen-Bradley the tag name is empty: nothing is read, sent or reset.
- **Redis.**
  - `readDataInFEServerFromIOServer(..., withRotationDone=True)` returns a 6th value: True, False, or None if the message doesn't carry it.
  - The FE→DB record carries `tighteningTorque1RotationDone` and `tighteningTorque2RotationDone` (new optional arguments of `sendDataFromFEServerToDatabaseServer`). `readDataInDatabaseServerFromFEServer(..., returnRotationDone=True)` appends them after `overallResult`.
  - Existing callers are unchanged.
- **Frontend.**
  - Each tightening-torque tile shows the torque on the first line (36 pt) and "Rotation Done" or "Rotation Not Done" below it in a smaller font (16 pt). A new method, `CosThetaOutcomeContainer.setTwoLineTextAndResult`, renders it.
  - When the PLC doesn't provide the bit (Allen-Bradley), the tile shows the torque alone, as before.
  - Both values go into the cycle record and are cleared at the start of each cycle.
- **Database.**
  - `hub_and_disc_assembly_data` gains two boolean columns: `nut_tightening_torque_1_rotation_done` and `nut_tightening_torque_2_rotation_done`. They are NULL when not provided. They are added with `ADD COLUMN IF NOT EXISTS`, so existing databases get them at start-up.
  - The new columns are also in `insertData` and in the CSV reports.
  - `DBServer` stores them from the FE record.
- **Tests (both dry-run folders):**
  - **Two full cycles:** the PLC simulator sets the bit True at torque 1 and False at torque 2. The frontend messages carry it, and the bit is reset after reading.
  - **Fault scenarios:** on Siemens the bit is in the reset-on-abort list (38 tags).
  - **DB dry run:** columns present; values stored from a direct insert and from an FE record; NULL for an aborted record; columns in the CSV header.
  - **FE dry run:** the tile text (value on the first line, then Rotation Done / Rotation Not Done in the smaller font) and the record values.
  - **Integrated run:** the DB row carries them.
- **Verified:**

  | Run | Allen-Bradley | Siemens |
  |---|---|---|
  | DB dry run | 49/49 | |
  | FE dry run | 54/54 | |
  | Fault scenarios | 47/47 | 56/56 |
  | Two full cycles | exact | exact; IO→FE messages carry rotation True / False |
  | Integrated DB row | rotation NULL / NULL | rotation True / False |

  A screenshot of the full frontend window at 1920×1080 shows both two-line tiles fitting.

## 24. Allen-Bradley aligned with Siemens; per-machine vision parameters; "Rotation Not Done" in red (2026-11-01)

- **Frontend.** "Rotation Not Done" on the tightening tiles is now red (`#d00000`). "Rotation Done" keeps the display's text colour. `setTwoLineTextAndResult` takes a `secondLineColor`.
- **Allen-Bradley IOServer: same behaviour and tag names as Siemens.** Both PLCs run the same IOServer code; the Siemens one only swaps the client. So the alignment is in the configuration:
  - **Tag names are shared.** The 50 tag and settings keys that the `siemens.*` section used to override now hold the Siemens values in the shared keys, and the `siemens.*` copies are removed. Examples:
    - `PLC_PC_CheckHub`, `PLC_PC_CheckBearing`;
    - `PLC_PC_CheckBunkForComponentPress`, `PLC_PC_CheckNoBunk`, `PLC_PC_CheckBunkForCapPress`;
    - `PLC_PC_Station4CapPressDone`, `PLC_PC_Station3TorqueValueSet`;
    - `PC_PLC_ConnectionStatusTag`;
    - `PC_PLC_LH_RH_Selection`, `PC_PLC_TighteningTorque1/2Setpoint`, `PC_PLC_RotationRPM`, `PC_PLC_NoOfRotation1CCW/1CW`;
    - `PLC_PC_Station2RotationDuringTighteningDone`.

    The `siemens.` section now holds only `siemens.plc.ip`. The `Configuration.py` getter defaults were changed to the same names.
  - **`cycle.settings.source=database` for both PLCs.** The Allen-Bradley machine writes side, setpoints, RPM and rotation counts from `machine_settings`, before QRCodeCheckOK / Done. It no longer writes a model or RPM 1/2: those tag names are empty. The earlier properties-based path is still in the code but isn't used. This resolves the §17 TO-DO.
  - **Rotation-during-tightening done** is read, shown and stored on the Allen-Bradley machine too, and reset with the torque tags.
  - **The Allen-Bradley PLC programme must provide these tag names**: pycomm3 reads and writes Logix tags by name.
- **Vision parameters are per machine.** Each of the 135 keys is written twice in `ApplicationConfiguration.properties`:
  - `allenbradley.vision.<name>` is Machine 1, and `siemens.vision.<name>` is Machine 2. The unused-constants section is duplicated the same way.
  - Both sets start with today's values. There are no unprefixed `vision.*` keys any more.
  - `CosThetaConfigurator.getVisionKeyPrefix()` returns `<plc.vendor>.`, and `getVisionValue(key)` reads `<vendor>.<key>` with no fallback to the other machine.
  - `camera/VisionConfig.py` reads every parameter through `getVisionValue`.
  - The vision modules keep their logical keys (`vInt("vision.nut.detection.radius", 80)`), so their code is unchanged.
  - The camera-server start-up log shows the full prefixed key of any re-tuned value.
- **`Verify_Vision_Config_Equivalence.py`** now checks:
  - every key exists for both machines;
  - Machine 1 values equal the built-in (baseline) defaults;
  - Machine 2 values are valid; re-tuned ones are listed as INFO, not as errors;
  - there are no unprefixed `vision.` keys, and no key that no module reads.
- **Properties copies.** `ApplicationConfiguration.Machine1.properties` and `ApplicationConfiguration.Machine2.properties` were re-copied from `ApplicationConfiguration.properties`, with `plc.vendor=allenbradley` and `plc.vendor=siemens` respectively.
- **Tests.** `_plc_sim.setup()` puts the default machine settings in force whenever the source is the database (now both PLCs). The Mock-PLC and Real-PLC test windows describe the shared settings, and the FE dry run checks the red "Rotation Not Done".
- **Verified (both PLCs):**

  | Run | Allen-Bradley | Siemens |
  |---|---|---|
  | Vision verifier (both folders) | ALL EQUIVALENT | ALL EQUIVALENT |
  | DB dry run | 49/49 | |
  | FE dry run | 55/55 | |
  | Fault scenarios | 55/55 | 56/56 (the extra check is the Siemens raw-byte check) |
  | Two full cycles | exact; writes 1 / 19 / 19 / 30 / 1 / 1; rotation True / False to the frontend; bit reset after reading | same |
  | Mock-PLC test window | 14/14 | 14/14 |
  | Integrated DB row | settings 30 / 19 / 19 / 1 / 1, rotation True / False | identical |

  A negative test of the verifier and VisionConfig: changing `siemens.vision.nobunk.nut.detection.radius` changed the value only on Siemens, and was reported as INFO; changing an `allenbradley.` value is reported as a PROBLEM.

## 25. Camera and image settings per machine; verifier fixed for Windows (2026-11-01)

- **Settings split.** The camera and image settings that existed before the vision work are now per machine, with the same values on both to start with: the same camera model is used today, but either machine's camera or camera settings can change. The 26 keys are:
  - `camera.id`, `camera.ip`, `camera.port`, `camera.uid`, `camera.pwd`;
  - `time.between.pictures.ms`, `max.allowed.none.frame.counts.in.camera`, `sleeptime.monitor.takepic`;
  - `machine.state.{3,5,7,9,13,16,20,22,24}.target.{width,height}`.
- **Properties layout.** Each key is written as `allenbradley.vision.<key>` and `siemens.vision.<key>`, in a new "CAMERA AND IMAGE SETTINGS" block at the top of each machine's vision section. The plain keys are removed, and comments where they used to be point to the new blocks.
- **`Configuration.py`.**
  - The 26 keys are listed in `CosThetaConfigurator.PER_MACHINE_CAMERA_AND_IMAGE_KEYS`.
  - `getValue(<key>)` reads `<plc.vendor>.vision.<key>` for these, with no fallback to the other machine.
  - Every existing getter (`getCameraIP()`, `getCameraPwd()`, `getTargetWidthAndHeight()`, `getTimeBetweenPicturesInMs()`, …) therefore returns this machine's value without being changed.
- **`Verify_Vision_Config_Equivalence.py`.**
  - It now also checks that the 26 camera / image keys exist for both machines.
  - **Windows fix:** the file list was built with `str(Path)` (backslashes), so `git show 39ead96:camera\X.py` found nothing. It now uses POSIX paths, also for `--files`.
  - `git show` and the manifest are read as UTF-8.
  - The "key not read by any module" check runs only when all modules are checked.
- **Verified:**
  - The getters return the same values as before on both machines.
  - **Negative test:**
    - a different `siemens.vision.camera.ip` and state-3 width took effect only with `plc.vendor=siemens`;
    - a deleted `siemens.vision.camera.port` was reported by the verifier.
  - The verifier reports ALL EQUIVALENT.
  - `ApplicationConfiguration.Machine1/Machine2.properties` were re-copied (`plc.vendor` allenbradley / siemens).
  - Integrated runs (both PLCs): DB rows unchanged. FE dry run: 55/55.

## 26. Logging strengthened (A–G), per-server logging settings, live configuration (2026-11-01)

**Findings that led to this.**
- The IOServer's `self.logger` was pycomm3's logger, set to CRITICAL with no handler, so its messages were lost.
- About 2,350 coloured prints reached the console only.
- Messages were dropped silently, both when the 256-entry queue was full and when Redis was down.
- Messages queued before the logging server started were cleared.
- Uncaught exceptions in threads were not recorded.
- Log lines had no QR code or cycle number.
- There was one log file per start, never rotated (`backuplogs.count` was unused).
- 150 `except: pass` blocks left no trace.

**A. IOServer messages and the PLC audit trail.**
- `self.logger` is now an adapter that writes to the central log file at the matching level. pycomm3 stays silent.
- The PLC audit trail (`log.audit.plc`) is written whatever the logging level, each line with `[QR=... cycle=N]`. It records:
  - every PC→PLC write (value, or the error);
  - every PLC→PC trigger seen raised and every value read, once per state, not every 0.2 s;
  - every state change, via a `stateChangeListener` on `MachineStateMachine`;
  - "CYCLE n START" with the QR code.
- The connection-status toggle is included only with `log.audit.plc.connection.toggle=True`.

**B. Console prints go to the file too.** `printBold…` and the other coloured prints are also written to the log file (`log.mirror.prints.to.file`):
- red = CONSIDER_ACTION, yellow = TAKE_NOTE, grey = DEBUG, others = INFO;
- the real caller's function and line are recorded;
- the console output itself is unchanged;
- this applies only in server processes, so command-line tools are unaffected.

**C. No silent loss.**
- The in-process queue holds 5,000 messages.
- Messages that can't be sent are kept, up to 20,000, and re-sent when Redis is back.
- Any message that has to be dropped is counted and reported in the log ("LOG MESSAGES LOST …").
- The logging server writes messages queued before it started. Only ones older than `log.file.keep.queued.minutes` (10) are counted and not written; these are leftovers from an earlier run or from tests.
- It still writes everything queued before it stops.
- **Fixed on the way:** the logging server and each server's logger checked the stop queue after every message, with a blocking `XREAD` that Redis resolves on its 100 ms timer. That limited logging to about 50 lines per second (5,000 lines took over 90 s). The check now runs at most every 0.5 s, and the logging server reads up to 200 messages at a time. The file is flushed whenever the queue is empty, so it is always current.

**D. Crash capture.**
- `sys.excepthook` and `threading.excepthook` write uncaught exceptions to the log file with the full traceback. The thread name is included, and the line notes that the thread has stopped.
- `faulthandler` writes hard crashes to `logs/faults/<server>-<pid>.log`.

**E. Cycle context.**
- When the IOServer accepts a QR code it publishes `[QR=… cycle=N]` (Redis key `log:cycleContext`).
- Every line any server writes then carries it, together with `<server>`.
- The DB server uses the QR code of the record it is storing.

**F. Log file rotation.**
- A new file starts at midnight (`log.file.rotate.daily`) and when the file exceeds `log.file.max.size.mb` (50). Each file notes where it continues and where it continued from.
- The newest `backuplogs.count` files are kept.

**G. Suppressed exceptions.** In 150 places across 23 files, `except …: pass` became `noteSuppressed()` (`utils/SuppressedExceptions.py`):
- the exception is still ignored;
- the log gets one DEBUG line per place, then one every 100th time, with the count;
- controlled by `log.suppressed.exceptions`.

Not changed: the vision modules (they are checked by the equivalence verifier), `logutils`, and the standalone Siemens tools.

**Per-server logging settings** (`ApplicationConfiguration.properties`, all applied while running):
- `log.file.level.<server>` and `log.console.level.<server>` for `mainprogram`, `ioserver`, `cameraserver`, `qrcodeserver`, `frontend`, `dbserver` and `heartbeatserver`. An empty value means the global level.
- In PRODUCTION mode, a level below CONSIDER_ACTION is raised to CONSIDER_ACTION, as `checkIntegrity` requires for the global levels.
- Each server identifies itself at start-up (`setLogProcess`, in each `MainProgram.startThe…` function and in the IOServer, DB server and frontend).

**Live configuration.**
- The properties file is re-read when it changes. Checks happen at most every `config.reload.check.interval.sec` (2 s), both from `getValue()` and from a watcher thread in each server.
- A new version is applied only if it parses and keeps at least 80% of the keys. A file caught half-written by an editor is skipped, and the next check picks it up.
- Every change is logged; password values are not.
- Listeners (`CosThetaConfigurator.addReloadListener`) refresh values cached at start-up:

  | Area | Live behaviour |
  |---|---|
  | Logging levels and options | applied while running |
  | Vision constants | re-bound in their modules and classes (`VisionConfig.reloadVisionParameters`); the next picture uses them, and each change is logged |
  | State instructions | applied while running |
  | IOServer | PLC read, write and connection-check intervals, the OK→Done delay and the disconnection-log interval are applied; a change to tag names, the PLC IP or the vendor logs "restart needed" |
  | Camera server | the gap between pictures and the missing-frame limit are applied; a change to the camera connection logs "restart needed" |
  | Image target sizes and machine settings | already read at use, so live |

- `COSTHETA_CONFIG_FILE` can point to another properties file; the dry runs use this.

**New dry run.** `test/*_dryrun/DryRun_Logging_And_Live_Config.py`, 34 checks (L1–L12). It runs on a copy of the properties file, edited while the code is running, with the real central logger and the real IOServer.
- **Also.**
  - The IOServer clears the previous run's cycle context at start.
  - `ApplicationConfiguration.Machine1/Machine2.properties` were re-copied.
- **Verified:**

  | Run | Allen-Bradley | Siemens |
  |---|---|---|
  | Logging dry run | 34/34 | 34/34 |
  | Fault scenarios | 55/55 | 56/56 |
  | Two full cycles | exact | exact |
  | Mock-PLC test window | 14/14 | 14/14 |
  | Integrated DB rows | unchanged | unchanged |
  | DB dry run | 49/49 | |
  | FE dry run | 55/55 | |
  | Vision verifier | ALL EQUIVALENT | |

## 27. Logging server: works with Redis older than 6.2 (2026-11-01)

- On the Windows machine, discarding stale queued log messages at logging-server start failed with "syntax error". The cause was `XTRIM ... MINID`, which needs Redis 6.2 or later.
- It now uses `XRANGE` and `XDEL`, which work on Redis 5 or later.
- Nothing else in the logging or live-configuration code needs a newer Redis.
- Logging dry run: 34/34 (Siemens).

## 28. Vision files: production versions, made configurable again (2026-11-01)

The 15 vision files are now the production versions (from `For_comparison.zip`), with this branch's changes re-applied on top:
CheckKnuckle, CheckNoBunk, CheckNutAndPlateWasher, CheckBunk, CheckNoCapBunk, CameraProcessorServer, HexagonNutDetector, CheckCap, HubAndBearingSegmenter, BunkSegmenter, CheckHubAndBottomBearing, CheckSplitPinAndWasher, CheckTopBearing, RTSPCam, ModelManager.

- **How.** Three-way merge with the common ancestor `6b31dd7` (ours = this branch, theirs = production). 10 files merged cleanly. The 10 conflicts in the other 5 files were resolved by hand: production code and `logBoth` calls, this branch's configurable constants in the messages.
- **Two commits.**
  - `5728def` is the reference: the production files plus only the Machine 2 changes made after `6b31dd7` (CameraProcessorServer 28 states; CheckTopBearing requires `ImageNormalisationWithMask` at start-up). The parameters are still hard-coded there.
  - The next commit makes the 135 parameters configurable again (`vInt`/`vFloat`/... with `<vendor>.vision.*` keys). It also re-applies `noteSuppressed()`, the camera server's live-configuration listener and the vision-parameter report.
- **Production changes now in the branch:**
  - CheckKnuckle: the model check is case-insensitive and None-safe.
  - CheckNoBunk, CheckNutAndPlateWasher: crop 240 instead of 180; the annotated image is centred on the bearing geometry.
  - CheckBunk, CheckNoCapBunk: the `bearing_geometry` parameter and a 300 crop; CameraProcessorServer passes `ImageProcessor.bearingCentreAndRadii`.
  - HexagonNutDetector: numpy k-means instead of sklearn; MobileSAM optional.
  - CheckCap uses `getInstance()`; message and type-hint changes elsewhere.
- **Logging.**
  - The production files log with `logBoth(level, source, message, messageType)`. New `logutils.SlaveLoggers.logBoth` maps that call onto this branch's logging. The message goes to the file and console loggers, with per-server levels, the production clamp, cycle context and live reload, and the calling function and line are recorded.
  - Level mapping: `logDebug`→DEBUG, `logInfo`→INFO, `logWarning`→TAKE_NOTE, `logError`→CONSIDER_ACTION, `logCritical`→TAKE_ACTION.
  - The branch-only messages in CameraProcessorServer (live configuration, vision-parameter report, missing dependencies) now use `logBoth` too.
- **Kept from this branch:**
  - The DOST hub/top/nut cap thresholds are read from the properties (production reads only DOSTPLUS). The values are equal: 30/30/35.
  - Messages show the configured values, not the old numbers (e.g. "need {NUT_MIN_VERTICES}-{NUT_MAX_VERTICES}").
- **Verifier.**
  - `Verify_Vision_Config_Equivalence.py` now compares against `5728def`. Each file is identical to the production version once every configurable value is replaced by its default.
  - The `vision_config_manifest.json` line references were moved to the production files.
- **Logging dry run.** It now has 37 checks; new L13 covers `logBoth`.
- **Verified.**

  | Check | Result |
  |---|---|
  | Vision verifier | ALL EQUIVALENT (135 parameters in 8 files) |
  | All 15 files import | torch stubbed here, since the sandbox has no torch; no undefined names |
  | Logging dry run | 37/37 on both vendors |
  | Fault scenarios (Siemens) | 55/55 |
  | Integrated cycle | settings 30/19/19/1/1 and rotation True/False in the DB row, on both vendors |

  The checks were not run on real images here (no torch, no camera). Run the camera server on the plant machine before production.

## 29. From production: MainProgram, heartbeat server, RedisUtils (2026-11-01)

What production does differently in these three files, and what was taken.

**Taken:**
- **MainProgram: Redis is no longer flushed at module level.**
  - On Windows every server process is started with `spawn`, which re-imports MainProgram in the new process. So the module-level `flushdb()` + `clearQueues()` ran again in each server as it started, deleting what the servers already running had put in Redis: queued log messages, heartbeats, cycle data.
  - Production commented it out. Here it is removed. `main()` still clears Redis once, before any server is started (`clearQueues` flushes the db and trims the queues).
  - Also fixed: the typo "worl" → "work".
- **Heartbeat server: one read of all 5 heartbeat streams** (`RedisUtils.readAllHeartbeatsInHeartbeatServer`), in place of 5 separate reads.
  - Each old read could block 0.5 s when its server was down, so one dead server delayed every status. Now it takes 3 Redis round trips and waits only when no server has sent anything.
  - The loop uses `time.monotonic()` for its interval.
  - The log messages use `logBoth`.
  - The production loop's old, commented-out copy was left out.
- **Three corrections to the production read:**
  - **Exact trim.** redis-py's `xtrim(q, 1)` is approximate (`MAXLEN ~ 1`) by default, and Redis then trims only whole internal nodes. So a short stream was not trimmed, and the read returned the oldest heartbeat since the last check, not the latest. `approximate=False` is used now; `XTRIM MAXLEN` exact works on Redis 5 and later.
  - `count=1`: XREAD's COUNT applies per stream.
  - A heartbeat arriving between the read and the delete is kept for the next check. Production trims it away.

**Not taken:**
- **MainProgram's start-up messages through `logMessageToConsoleAndFile`.** Here MainProgram runs with `setLogProcess("mainprogram")`, so its prints already go to the log file, with the process name and levels.
- **`logMessageToConsole/File/ConsoleAndFile` and the logging connection pool in RedisUtils.**
  - Production added them so PySide code can log without the logger threads. Here the slave loggers only put messages on an in-process queue, which is thread-safe from Qt code, and the frontend dry runs pass.
  - Production's functions would bypass the per-server levels, the production clamp and the cycle context. They filter every caller by `frontend.logging.level`.
  - The pool also opens 5 Redis connections at import, in every process.
  - If production frontend code that calls them is merged later, thin adapters onto this logging can be added then.

**Found, not changed:** the same approximate `xtrim(q, 1)` is in 23 other readers in RedisUtils, including the data queues (e.g. `io2feq`). There it is effectively a no-op, so those queues are read oldest-first and no message is dropped. The code relies on that, so it was left as it is.

**New dry run:** `test/*_dryrun/DryRun_Heartbeat_And_Startup.py` (16 checks). It covers the heartbeat read, the real heartbeat server (camera DEAD after the configured missed checks, then ALIVE again), and MainProgram imported without flushing Redis.

**Also:** the integrated dry run's screenshot is no longer committed (`.gitignore`).
- **Verified:**

  | Run | Result |
  |---|---|
  | Heartbeat/start-up dry run | 16/16 on both vendors |
  | Integrated cycle | DB row unchanged (settings 30/19/19/1/1, rotation True/False), both vendors |
  | FE dry run | 55/55 |

## 30. From production: frontend, costhetaio, persistence, processors, statemachine (2026-11-01)

Production's own changes since the common ancestor `6b31dd7` were compared with code, logging and imports ignored.
- **Mostly logging.** In DBServer, IOServer, QRCodeScanningServer, Persistence, PostgresBackupUtility and GenericQueueProcessor, production's changes are logging only: print and Slave*Logger calls became `logBoth` / `logMessageToConsoleAndFile`. Here the same messages already reach the log file, through the print mirror and `setLogProcess`.
- **No changes:** statemachine and most frontend widgets.

**Taken: no Redis wait in the frontend's GUI thread.**
- The emergency-abort and server-status reads run in the GUI thread: the worker threads only emit a signal, and the slot runs in the window's thread. Each read waited up to 0.5 s for a message. There is normally no emergency message, so the screen froze for 0.5 s at every emergency check.
  - Measured in the FE dry run: the longest GUI-thread stall was 1.08 s. It is now 0.02 s.
  - Production made these reads non-blocking (`block=None`); so does this branch.
- **Status tiles keep their last status** when no new status message has arrived. Production's non-blocking read showed every server down whenever the message had not arrived yet; production worked around that with a 5 s refresh. Here all servers are shown down only when the heartbeat server has sent nothing for 3 of its intervals, i.e. it is gone itself. `readHeartbeatsInFEServerFromHeartbeatServer(..., withReceived=True)` tells the two cases apart.
- **Emergency checked every 1 s, instead of every 3 s.** The interval came from `frontend.connectionstatus.sleepinterval.sec=3`, a key whose name did not say what it was for. It is now production's `frontend.emergency.status.sleepinterval.sec=1.0`; the old key is still read if the new one is missing. In the dry run the emergency abort screen showed 0.96 s after the PLC signal.
- **Frontend heartbeat interval from the properties.** It was a fixed 500 ms in the code. It is now production's key `frontend.status.sleepinterval.sec`, with the value kept at 0.5.
- The three frontend timer intervals (status, emergency, heartbeat) are applied while running.

**Not taken:**
- **Non-blocking reads with a 250 ms poll in the worker threads** (QR, picture, torque). Those reads run in their own QThreads, so waiting there freezes nothing. A blocking read delivers the message the moment it arrives; a 250 ms poll adds up to 250 ms.
- **Shorter server heartbeat intervals (0.25 s) and the 5 s status refresh.** The current values are well inside the 2 s heartbeat check. The 5 s refresh only hid the problem above.
- **`frontend/widgets/CosThetaIconLabel`: `QWidget.__init__()` without `self`.** That is a bug: it raises TypeError.

**Redis connection pools.**
- `redis.Redis(...)` already creates its own thread-safe `ConnectionPool`. Each command borrows a connection and returns it; a blocking XREAD holds one only while it waits.
- Production's "pool" is a queue of 5 `Redis` clients, each with its own internal pool. It adds no pooling. It gives a connection to code that has none of its own, which was the frontend's log calls.
- Each component here creates its own client. That is a few dozen connections across all processes, far below Redis's limit, so pooling would gain little.
- What a shared per-process pool could add is one place for connection settings (`health_check_interval`, `socket_keepalive`, `socket_timeout`, `retry`) and a bound on the connections. That would mean changing about 70 places where clients are created. It is not done here.

**FE dry run:** 61 checks; F7 is new: GUI thread never blocked, tiles keep their status, stale status, emergency within about 1 s, the intervals.
- **Verified:**

  | Run | Result |
  |---|---|
  | FE dry run | 61/61 |
  | Integrated cycle | DB row unchanged on both vendors |
  | Logging dry run | 37/37 |
  | Heartbeat dry run | 16/16 on both vendors |

  The heartbeat dry run scored 14/16 once, started straight after the integrated run while that run's processes were still reading the heartbeat streams. Run on its own, it passes.

## 31. "Rotation Done / Not Done": smaller font, always fits the tile (2026-11-01)

- On the plant screen the last "e" of "Rotation Not Done" went past the tile edge.
- The second line of the tightening tiles is now 13 pt (was 16 pt). If the text is still wider than 90% of the tile, as can happen on a smaller screen, with display scaling or with a wider font, it is reduced further, to 8 pt at the least. The check uses the actual font and the tile width.
- FE dry run: 62 checks. A new check: "Rotation Not Done" fits the tile, at 173 px in a 269 px tile here.

## 32. Production Configuration.py and properties (29-01-2026) compared (2026-11-01)

**Configuration.py.** Production's changes since `6b31dd7`:
- **Taken:**
  - `@classmethod` added to `getPollingTimeForCounterUpdates` and `getBaseDirForAuditReports`. Called on the class, they raised TypeError. No live code calls them, but the fix is correct.
- **Already here:**
  - A throttle on the properties-file change check. Production uses 5 s; here it is `config.reload.check.interval.sec`, 2 s.
  - The frontend emergency and heartbeat interval keys (section 30).
- **Not taken:**
  - `frontend.logging.level`, which only filters production's `logMessageTo…` functions (not taken, section 29).
  - `frontend.all.connectionstatus.sleepinterval.sec`; the status refresh stays at `general.connectionstatus.sleepinterval.sec` = 2 s (section 30).
  - 0.25 s getter defaults. They apply only when a key is missing, and the properties set the values.

**ApplicationConfiguration - Production - 29-01-2026.properties.**
- It is identical to the copy already in the repo. It is older than the properties file in `For_comparison.zip`: that one has `bunk` instead of `bung`, `C:` instead of `D:`, display times of 0.5/2.5 s and the new interval keys. This branch follows the newer file.
- Keys in production that are not here: each was checked against the code.
  - Camera settings: now `<vendor>.vision.camera.*` / `<vendor>.vision.*`.
  - Image target sizes: now `<vendor>.vision.machine.state.N.*`, numbered for the new states.
  - The rest (picture-delay tuning, recording, Google Drive, font multipliers, unused queue names, the old bunk / nut-washer / no-bunk check parameters, rotation-2 and rotation-unit tags) are read by no live code. They were removed in sections 10 and 11.
- Different values:
  - Tag names and state instructions: the new process.
  - Interval values: production's shorter heartbeat intervals were not taken, see section 30.

## 33. CheckKnuckle: both side boxes dark → rejected; comments without numbers; Machine 2 regions calibrated (2026-11-01)

**Logic change.**
- Before: when both the LHS and the RHS side boxes on the bottom bar were dark, the knuckle was taken as LHS. A glove or rag on the left end of the bar could then let a RHS knuckle pass as LHS.
- Now such an image is rejected, with "both LHS and RHS side boxes are dark; is something lying on the bar?".
- Machine 1's results on its four reference pictures are unchanged.

**Comments.** The class description and every check comment now name the configuration key (`<vendor>.vision.knuckle.*`) instead of numbers. The old numbers were partly stale: they said 230 / 96 where the code uses 220 / 84. The Step 2 failure message no longer says ">=" when a DOSTPLUS fails.

**Machine 2 regions (`siemens.vision.knuckle.*`).** Calibrated on 15 Machine 2 pictures.
- **Pictures:** DOSTPLUS LHS ×3, DOSTPLUS RHS ×4, DOST LHS ×4, DOST RHS ×4.
- **How DOST and DOSTPLUS were told apart:**
  - DOSTPLUS kingpin bosses have a flat face with a corner reaching towards the centre, and a pin with a groove ring.
  - DOST bosses are round, with a stepped bore.
  - This matches Machine 1's DOST/DOSTPLUS pictures.
- **Values:**

  | Check | Machine 2 (siemens) | Machine 1 (allenbradley, unchanged) |
  |---|---|---|
  | Side boxes (arm across the bottom bar) | LHS 420,624 20×20; RHS 792,612 20×20 | 380,650 130×50; 765,650 100×55 |
  | Side threshold | 150 | 220 |
  | A / B (casting next to the right / left boss) | 748,250 28×22 / 455,262 20×14 | 775,300 35×25 / 440,310 40×20 |
  | C / D (fixture screws, left / right) | 409,434 r9 / 814,437 r9 | 390,465 r13 / 865,465 r13 |
  | E (dark plate, bottom centre) | 590,535 50×20 | 600,600 50×20 |
  | Step 2 / Step 3 polygons (next to the bosses) | 6 points each, where the DOSTPLUS boss face reaches and the DOST casting does not | 4 points each |
  | Model threshold | 160 | 96 |
  | Common threshold | 84 (both machines) | 84 |

- **Result:** each of the 15 pictures is OK with its own QR code and NOT OK with the other three.
- **Smallest margin to the threshold:**
  - A–E: 34
  - side box with the arm: 132; without it: 105
  - S2/S3: DOSTPLUS 89, DOST 135
- **With the regions shifted by ±3 px:** the side boxes keep a gap of more than 200. The model polygons keep DOST ≤ 123 and DOSTPLUS ≥ 193 against the threshold of 160.
- **Not yet calibrated:** pictures of the empty fixture and of a rag or glove on the bar would confirm that A/B see the knuckle (on Machine 2 they lie on the casting) and test the new rejection with a real object.

**Verifier.**
- CheckKnuckle is compared with its own reference commit `637ce4a`: production's file with the hard-coded values plus this logic and comment change. The file is listed as `baseline_per_file` in `vision_config_manifest.json`.
- All 8 files: ALL EQUIVALENT.

**New dry run.**
- `test/*_dryrun/DryRun_CheckKnuckle_Images.py`, with the pictures in `knuckle_images/`. Machine 2: 32/32. Machine 1: 10/10.
- It checks every picture against all four QR codes and a simulated object on the bar, and prints the margins.
- **Also:** logging dry run 37/37.

## 34. Abort part-way through a cycle: from the frontend and from the test window; back to the QR code (2026-11-01)

**Problem.**
- "Reset to Step 0" in `Test_IOServer_With_Real_PLC_UI.py` only reset the test window. The IOServer stayed in the middle of the cycle, e.g. waiting for a camera result that never came, and did not go back to reading the QR code.
- The frontend's manual abort (Ctrl+Alt+Z) had the same gap. It stopped the QR scan and cleared the queues, but never told the IOServer.
- The frontend → IOServer queue (`fe2io.q`) existed but was not used.

**Frontend → IOServer abort.**
- `RedisUtils.sendAbortFromFEServerToIOServer(conn, reason)` puts `action=Abort` on `fe2io.q`.
- `readAbortInIOServerFromFEServer` reads and removes every message waiting there, so an abort is never lost behind another message.
- The IOServer has a new thread, "Frontend Abort Listener", that acts on it.
- The frontend's `manualAbort()` now sends it.
- **Test window.** "Reset to Step 0" sends it, as the frontend would, then waits up to 10 s until the IOServer is back at the QR code (state 1, or state 2 once it has re-armed the QR check). It reports either way.

**One abort routine in the IOServer: `abortCycle(reason, ...)`.** It is used for:
- the emergency button on the machine (`PLC_PC_EmergencyAbort`): QR server told, queues cleared, frontend told;
- lost server connections (combined heartbeat, still disabled for testing): frontend told;
- an abort from the frontend: QR server told, queues cleared, frontend NOT told, since the abort came from it.

In order, it:
1. tells the QR server to stop a scan in progress;
2. clears the camera / QR queues;
3. tells the frontend (emergency screen and abort record);
4. resets `PLC_PC_EmergencyAbort`;
5. sets READ_QR_CODE;
6. resets all handshake tags (`resetAll`);
7. clears the cycle timing;
8. re-arms `PLC_PC_CheckQRCode`.

It is serialised, logged and audited ("CYCLE ABORTED in state ...: reason"). `abortCount` and `lastAbortReason` are kept.

**Nothing of the aborted cycle moves the reset cycle on.** These race fixes were found by the new dry run:
- `MachineStateMachine` is now thread-safe (lock). It has `incrementStateFrom(expected)` / `decrementStateFrom(expected)`, which change the state only if it is still the state the step started in. All state changes in the IOServer's read and write loops use them.
- The write loop notes the state before reading the camera / QR answer. An answer that arrives after an abort is ignored ("received after the cycle was aborted; ignored").
- Before the fix, a late camera OK wrote `KnuckleCheckOK=True` into the new cycle.
- `perform_write_state_action(..., expectedState)` writes neither OK nor Done once the cycle has been aborted, including between OK and Done.

**PLC side.** The PC cannot reset the PLC's own sequence. After a PC-side abort (manual abort in the frontend, or Reset in the test window), a PLC that is still part-way through its cycle must be reset on the machine (Emergency). The frontend's abort dialog already says so.

**Dry runs.**
- **Fault scenarios, new F (Siemens 70/70, Allen-Bradley 69/69):**
  - F1: abort between steps: back at the QR code within 3 s, ABORT sent to the QR server, no emergency echoed to the frontend, tags reset, message consumed, then a full cycle.
  - F2: abort while waiting for a camera result, then a late camera OK: nothing written and the state is not moved, then a full cycle.
  - F3: `incrementStateFrom` refuses after an abort.
  - C (emergency button) still passes, through `abortCycle`.
- **FE dry run, new F8 (66/66):** a manual abort puts ABORT on `fe2io.q`, the IOServer reader sees it, the abort screen and record appear, and the frontend is back at READ_QR_CODE.
- **Also passing:** two full cycles exact on both vendors; integrated DB row unchanged on both; FE dry run 66/66 on both.

## 35. Vision OK → OK/Done back to False after a delay; default torque 1 = 15 Nm; recreate the databases (2026-11-01)

**Vision result reset.**
- After a vision check answers OK, the IOServer writes `<Check>OK=True` and `<Check>Done=True` to the PLC.
- It then starts a thread (`threading.Timer`) that sets both back to False after `plc.vision.result.reset.delay.sec` seconds (new key, 2.0, in the PLC section of the properties).
- **Which checks:** the 9 picture checks: knuckle, hub, top bearing, nut + plate washer, bunk for component press, no bunk, split pin + washer, cap, bunk for cap press.
- **Not:** after a NOT OK, nor for the QR code check.
- The delay is read at each OK, so a change in the properties applies to the next check without a restart.
- New method: `IOServer.scheduleVisionResultReset(tags, state)`. The writes go to the log ("Reset [...] to False 2.0 s after the OK of ...") and the PLC audit trail.

**Default machine settings.** Torque setpoint 1: 15.0 Nm (was 19.0). Torque setpoint 2 stays 19.0. The full defaults are now 30 rpm, 15 / 19 Nm, 1 / 1 rotations.
- **Where:** `Persistence.DEFAULT_MACHINE_SETTINGS`, and `tightening.torque1.setpoint.nm` in the properties (used only with `cycle.settings.source=properties`).
- **Existing databases:** the default row is inserted only into an empty `machine_settings` table. A database that already exists keeps its settings until they are changed in the frontend (Machine Settings) or the database is recreated.

**`persistence/RecreateDatabases.py` (new).**
- It drops `ashok_leyland_test` and `ashok_leyland_production` and creates them again, empty, exactly as the application does at start-up: schema, tables, indexes, the default machine settings, roles and default users.
- It closes open connections first, and asks for YES unless given `--yes`. `--only test|production` limits it to one database.
- ALL DATA IN THEM IS LOST; stop the application first.

**Dry runs.**
- **Fault scenarios, new G (Siemens 75/75):** after the knuckle OK, OK/Done are still True 1.4 s later and are set to False after 2.0 s. After a hub NOT OK, Done is NOT reset. The hub retry OK is reset. The cycle completes.
- **D now expects 15 / 19 Nm.**
- **DB dry run §9:** the default row is compared with `DEFAULT_MACHINE_SETTINGS`. A database created before today has 19 / 19 Nm and must be recreated for this check to pass.
- **Also passing:** fault scenarios Allen-Bradley 74/74; DB 49/49 on freshly recreated databases; two full cycles on both vendors (settings 30 / 15 / 19 / 1 / 1); integrated DB row (30, 15, 19, 1, 1) on both; logging 37/37; FE 66/66.
- **Follow-up (same day):** `scheduleVisionResultReset` names the two tags explicitly. It sets `<Check>OK` to False and then `<Check>Done` to False. Each write is retried up to 5 times (`VISION_RESULT_RESET_ATTEMPTS`, 0.2 s apart), so a single failed write cannot leave OK or Done True. The result of both writes is logged ("PC_PLC_KnuckleCheckOK -> False (done), PC_PLC_KnuckleCheckDone -> False (done)"). Fault scenarios: 75/75.

**Change (same day): reset after NOT OK too.**
- Done is set back to False whatever the result. After a vision check's result is written (`<Check>OK` = True for OK or False for NOT OK, then `<Check>Done` = True), the IOServer sets OK and then Done to False after `plc.vision.result.reset.delay.sec` (2 s), for OK and for NOT OK alike.
- The QR code check is still not included.
- **Retry race handled.** After a NOT OK the PLC re-triggers the check. If the retry's result is written before the NOT OK's reset is due, that pending reset is cancelled (`cancelVisionResultReset`, called at the start of `perform_write_state_action`), so it cannot clear the new result. The new result gets its own 2 s reset.
- **Fault scenarios G (77/77):**
  - knuckle OK: still True after 1.4 s, reset at 2.0 s;
  - hub NOT OK: OK/Done reset at 2.0 s; hub retry OK: reset;
  - top bearing NOT OK, then retry OK 1.0 s later: the NOT OK's reset is cancelled; the OK stays True until its own delay, then is reset.
- **Also passing:** Allen-Bradley fault scenarios 76/76; two full cycles; integrated DB row unchanged.

## 36. Vision result reset: one scheduler thread; Done/OK read back; Check reset after a NOT OK (2026-11-01)
- **One thread, not one per reset.** The `threading.Timer` per reset (a new short-lived thread each time) is replaced by
  `DelayedJobScheduler` in `costhetaio/allenbradley/IOServer.py`: ONE long-lived thread ("Vision Result Reset
  Scheduler") with a time-ordered queue of jobs, keyed by the check's OK tag (a new result for the same check replaces or
  cancels the pending job).
- **The job, `plc.vision.result.reset.delay.sec` after the result was written** (OK and NOT OK):
  1. reads `<Check>Done` and `<Check>OK` back from the PLC;
  2. writes `<Check>OK` = False, then `<Check>Done` = False (each with retries);
  3. if Done was read True and OK False (NOT OK), also writes the PLC's trigger `PLC_PC_Check<…>` = False, so the next
     two-hand-button (2HB) press raises it again and the picture is retaken. If Done is already False (PLC or end-of-cycle
     reset cleared it) or a read fails, the result the IOServer wrote decides;
  4. logs one line with the values read and the outcome of each write.
- **After a NOT OK, the check's READ state ignores the trigger until step 3**, so the trigger still standing from the
  NOT OK does not retake the picture without a 2HB press. A 2HB press during the delay is not seen: press again after it.
- **Abort** (emergency, frontend, "Reset to Step 0") cancels every pending reset and clears the wait.
- **PLC traffic is unchanged in kind:** only tag reads/writes (PUT/GET) on the existing read and write connections,
  serialized by their locks.
- **Tests:** fault scenarios Siemens 80/80, Allen-Bradley 79/79 (section G: knuckle OK reset; hub NOT OK — OK, Done and
  trigger reset, no picture until the next 2HB, retry OK leaves the trigger alone; top bearing — 2HB during the delay
  ignored, next press → OK; exactly one scheduler thread); two full cycles and the integrated cycle (DB row unchanged)
  for both vendors.

## 37. Hub and bottom bearing check: Machine 2 (Siemens) positions; DryRun_Hub_Check window (2026-11-01)
- **How the check works (unchanged code):** (1) "no bearing in centre": mean of a ring round the bore after gamma 3
  must be below `centre.check.max.mean`; (2) the ring `segment.inner.radius`–`segment.outer.radius` round
  `segment.center` is cropped and normalised; MobileSAMv2 (YOLO boxes + SAM) finds round shapes; they are grouped by
  distance from the centre and size; every 4- and 5-combination is tested for equal sides and angles; the most regular
  wins: 4 = DOST, 5 = DOSTPLUS; the other model's count = mismatch = NOT OK. Two rings qualify: the studs and the
  through-holes (both 4 on DOST, 5 on DOSTPLUS).
- **Machine 2 measured on 12 pictures (6 DOST, 6 DOSTPLUS):** bolt-circle centre (613, 341) (Machine 1 settings:
  635, 345); holes at radius 130–131, studs 156–167. The Machine 1 ring (635, 345, 120–250) cut the hole ring.
- **`siemens.vision.hub.*` changed** (all three properties files): `segment.center.x/y` 613/341,
  `segment.outer.radius` 230, `segment.inner.radius` 105 (both rings fully inside on both models);
  `centre.check.x/y` 613/341, `centre.check.inner/outer.radius` 35/55, `centre.check.max.mean` 127. On the pictures:
  open bore 110–115, bearing seen in the bore 140–170: the limit sits in the middle of the gap.
  Pictures DOSTPLUS_05/06 and DOST_01/04/05/06 show a bearing in the bore and are NOT OK at this check.
- **Check of the positions (proxy):** the real preprocessing and post-processing (filters, grouping, polygon search,
  model match) run with SAM/YOLO replaced by circle masks: with the new settings the 6 open-bore pictures pass with
  their own model and give MISMATCH with the other; with the Machine 1 settings 0 of 12 pass. The SAM/YOLO step itself
  is checked on the GPU machine with the window below.
- **`test/machine2_dryrun/DryRun_Hub_Check.py`** (and machine1 copy): tkinter; folder chooser, DOST/DOSTPLUS, seconds
  per picture (3); runs the real `CheckHubAndBottomBearing` on each picture; shows the program's markings plus the
  search ring (yellow), centre ring with its value (magenta / red), and an OK / NOT OK banner with the reason (bearing
  in the centre, mismatch, no polygon), the gamma and the time; optional saving to `<folder>/hub_check_results/`.
  Pictures in `test/machine2_dryrun/hub_images/`.
- **Verify_Vision_Config_Equivalence:** `plc.vision.*` (IOServer settings) no longer reported as stray vision keys.

## 38. Hub check: "no bearing in the bore" only on request (enforceBearingAbsenceCheck); limit 140 on both machines (2026-11-01)
- **Decision (user):** the hub station checks the hub; the next station checks the bearing. The centre check is kept
  but runs only when the caller asks for it: new last parameter
  `CheckHubAndBottomBearing.checkHubAndBottomBearing(..., enforceBearingAbsenceCheck: bool = False)`.
  `CameraProcessorServer` does not pass it, so production now skips the centre check on both machines.
- **Limit** `<vendor>.vision.hub.centre.check.max.mean` = **140** on both machines (Machine 1 was 144, Machine 2 127):
  open bore 105–130, bearing 150–178 on the pictures (Machine 1: open 110–129, bearing 167–178; Machine 2, ring
  35–55: open 110–115, bearing with rollers 150–170). Washer on the bearing (Machine 2): 140–144 - at the limit;
  only relevant if the check is switched on.
- **Verifier:** reference commit 88d3a0d (production CheckHubAndBottomBearing with the logic change, limit 140,
  comments that refer to the keys) in `baseline_per_file`; manifest lines updated. Both machines: ALL EQUIVALENT.
- **DryRun_Hub_Check:** "Enforce bearing absence check" tick box (off by default = production); the centre value is
  shown either way ("not enforced" when off). Controls on two rows. Proxy run on the 12 Machine 2 pictures: off -> 12
  OK; on -> 6 NOT OK "bearing in the centre" except DOSTPLUS_06 (washer, 139.8).

## 39. Top bearing check: Machine 2 (Siemens) search centre; DryRun_TopBearing_Check window (2026-11-01)
- **How the check works (unchanged code, OpenCV only):** a disc of `annular.outer.radius` round `annular.center` is
  cropped and normalised; 5 gammas x 2 start centres (`center.offsets`: the centre and 10 px above) are tried in
  parallel; each inverts the blue channel, binarises, traces rays from the start centre and fits circles (RANSAC) to
  the inner and outer edge of the bearing's bright inner race (Machine 1: r 32-36 / 46-50); the best valid branch wins;
  then the ring must be brighter than the same ring in the hub picture of the part (gamma 3, grey mean) by more than
  `final.gamma.threshold` (30).
- **Machine 1 (production settings):** 15 bearing pictures OK (5 runs each, always), 11 open-bore hub pictures NOT OK.
- **Machine 2:** bearing centres measured at (612-615, 337-345). With the Machine 1 centre (632, 360) 3 of 11 bearing
  pictures were found. With (613, 341) the "10 px above" start centre sometimes won with a smaller wrong ring
  (r 26-40, brightness difference only 28-30): DOSTPLUS_bearing_03 NOT OK in 3-8 of 20 runs. Like Machine 1, where the
  set centre is below the bearing centre and the two start centres bracket it, the centre is set to **(613, 345)**:
  `siemens.vision.topbearing.annular.center.x/y` = 613/345 (all three properties files); nothing else changed.
  Result: 11 bearing pictures x 20 runs = 220/220 OK; 5 open-bore / washer pictures x 20 = 0/100 OK. Brightness
  difference on the ring found: DOSTPLUS +42..+56, DOST +78..+83 (limit 30); washers and open bores: no ring found.
- **Run-to-run variation:** `_fit_circle_ransac` samples with the unseeded global `np.random`, so the same picture can
  give a slightly different ring (and, near a limit, a different verdict) on each run. Not changed (production code,
  both machines); noted for a decision.
- **`test/machine2_dryrun/DryRun_TopBearing_Check.py`** (and machine1 copy): tkinter; folder, hub reference picture
  (used unless `X_hub.<ext>` sits next to picture X), seconds per picture (3), saving to
  `<folder>/top_bearing_results/`. Shows the full picture with the search area and start centres (yellow), the ring
  found (green), the check's own result picture as an inset, and a banner: OK with the ring radii and the brightness
  difference against the limit, or NOT OK with "no bearing ring found" / "brighter by only ...". Pictures:
  `test/machine2_dryrun/top_bearing_images/`.

## 40. Top bearing check: repeatable circle fitting (both machines) (2026-11-01)
- **Change:** `CheckTopBearing._fit_circle_ransac` takes its 3-point samples from `np.random.default_rng(RANSAC_SEED)`
  (module constant `RANSAC_SEED = 0`; optional `seed` argument) instead of the unseeded global `np.random`. The same
  picture now always gives the same ring and the same verdict, also with the branches running in parallel threads
  (each call has its own generator). Signature of `checkTopBearing` unchanged.
- **Verifier:** reference commit 486904e (production CheckTopBearing with this change) in `baseline_per_file`;
  manifest lines updated. Both machines: ALL EQUIVALENT.
- **Results:** Machine 2 (centre 613, 345), 16 pictures x 3 runs for each of seeds 0-4: every run of a picture gives
  the same ring; 11 bearing pictures OK, 5 open-bore / washer NOT OK with every seed. Machine 1 (production settings),
  seeds 0-2: 15 bearing OK, 10 open-bore NOT OK. Two full runs of DryRun_TopBearing_Check: identical results.
- **Note:** a fixed seed makes a verdict repeatable, not right: with the earlier centre (613, 341), DOSTPLUS_bearing_03
  fails with seeds 0, 2 and 6 every time and passes with the others. The setting must leave margin (section 39);
  the seed only removes the run-to-run variation.

## 41. Nut and plate washer check: Machine 2 settings; washer comparison fixed (both machines); DryRun_NutWasher_Check (2026-11-01)
- **How the check works:** (1) nut: MobileSAMv2 shapes in a disc of radius 80 round `nutwasher.nut.detection.center`
  that cover the disc centre and lie in the area window (`nut.min/max.mask.area` 3000-5500) are tested as a hexagon
  (5-8 corners, aspect 0.7-1.3, > 2000 px left after removing a 25 px disc round the centre of mass, symmetry >= 55
  and thickness >= 45 measured round the fixed `nut.reference.center` in the search disc, straight edges);
  (2) washer (`check.washers.presence.in.nutandplatewasher=True`, shared by both machines): in the bearing ring found by
  the top bearing check, minus the nut, the top bearing picture minus the nut picture (gamma 3) must exceed
  `washer.threshold.r` in red and `washer.threshold.rb` in red-minus-blue (the washer covers the bright bearing race).
- **Fixed (both machines):** the top bearing picture was cut round the bearing centre, the nut picture round the nut
  detection centre, and one mask (in the nut crop's coordinates) was applied to both, so the area compared in the
  bearing picture was shifted by the distance between the two centres (Machine 1: up to about 12 px). The bearing
  picture is now cut round the nut detection centre too. Machine 1 pairs (same part, nut + washer): diff_R +73..+97,
  diff_RB +14..+25 after the fix (they passed before too). Verifier: reference commit 04d147d; ALL EQUIVALENT.
- **Machine 2 measured:** nut centre (612, 337-338), outline area about 4350 px (Machine 1 about 4300-4600: same
  size, area limits unchanged). Settings (all three properties files):
  `siemens.vision.nutwasher.nut.detection.center` = 612,349 (11 px below the nut centre, as on Machine 1);
  `siemens.vision.nut.reference.center.x/y` = 80/69 (the nut centre in the search disc). Washer limits unchanged
  (0.55 / 5.57 on both machines, user decision; 30 / 7.5 were proposed and withdrawn).
- **Checked here without the models** (the nut outline found by thresholding stands in for MobileSAM; everything after
  it is the real code): 6 of 7 Machine 2 nut pictures OK with symmetry 84-96 (Machine 1 reference point gave 64-70);
  the 7th only because the stand-in outline merged with the washer; washer-only pictures NOT OK (no nut); washer
  differences with a nut and washer: diff_R +63..+69, diff_RB +15..+18; without a washer (same part's bearing
  pictures compared, and nut pictures with the bearing pasted round the nut): diff_R -4.5..+0.8, diff_RB -1.6..+0.8.
  The limits 0.55 / 5.57 sit inside the "no washer" spread for diff_R; diff_RB still separates.
- **Found, not changed (for a decision):** the hexagon tests accept a round shape of nut size (a 40 px circle passes
  with symmetry 72): only the area window keeps a washer or other round part out. The nut is far brighter than what
  surrounds it on both machines (+93..+123 grey levels); a minimum contrast would reject dark or round look-alikes.
- **`test/machine2_dryrun/DryRun_NutWasher_Check.py`** (and machine1 copy): folder of nut pictures, the top bearing
  picture of the part (`X_bearing.<ext>` next to X, or one chosen), DOST/DOSTPLUS, seconds per picture, "Check washer"
  (starts as configured), saving to `<folder>/nut_washer_results/`. The bearing ring comes from the real top bearing
  check on the bearing picture. Shows the search disc and centre, the reference centre, the bearing ring, the nut found,
  the check's own picture as an inset, and OK / NOT OK with the reason (why no nut, or both washer differences against
  their limits). Pictures: `test/machine2_dryrun/nut_washer_images/` (use `top_bearing_images/DOST_bearing_01.jpg` as
  the bearing picture).
- Figure of the washer-comparison fix (Machine 1 pair, 10 px between the centres): `docs/nut_washer_comparison_fix.png`.

## 42. Endpoints: web service and MI dashboard rewritten for the Machine 2 database; MI_Panel and MI removed (2026-11-03)
- **Removed:** `endpoints/AshokLeyland_MI_Panel.py`, `endpoints/AshokLeyland_MI.py`, and `endpoints/rendered.html`
  (written by the old web service on every page view). `OtherServices.py` already starts MI_Light, the web service and
  ShowScreen; nothing else imported the removed files (MainProgram's commented-out lines now name MI_Light).
- **Why a rewrite:** both read `hub_and_disc_assembly_schema` (now `al_hub_and_disc_assembly_schema`) and Machine 1
  columns that no longer exist (`knuckle_check_*`, `hub_and_first_bearing_*`, `second_bearing_*`, torque 3, free
  rotation 2/3, `bung_*`, `component_manufacturing_date`), so every query failed.
- **`endpoints/EndpointsDB.py`** (new, shared): database "production" (default) or "test" only; the exported columns
  are `persistence.Persistence.all_columns_record_header` (as in the frontend's CSV export); a record's day is its
  `created_on`; date limits are whole days with both ends included; model names match whole names ignoring case; one
  connection per query, closed afterwards.
- **`endpoints/AshokLeylandWebService.py`** (port 8000): same routes, bodies and answer shapes as before
  (`/api/get_*` with model_name / start_date / end_date, the page's `/api/by_*` / `today_by_model` / `all_today` with
  modelName / startDate / endDate, `/csv/` and `/json/`, the no-format routes answering `{"data": "<csv>"}`), now with
  the Machine 2 columns. Fixed on the way: `db_name` is honoured (was ignored); the end date is included (the old query
  stopped at its midnight); errors give HTTP 400 (request) / 500 (database) instead of 200 with an error text; the page
  script calls the service it was loaded from (it called `http://localhost:8000`, which fails from any other PC); the
  page reloads the model lists when the database is switched; the API examples show the address the page was opened
  with; no internet resources.
- **`endpoints/AshokLeyland_MI_Light.py`** (port 8050): components per day and per week split OK / NOT OK, nut
  tightening torque 1 and 2 (with the setpoints in force as dashed lines) and free rotation torque histograms,
  re-assembled components (remarks DUPLICATE) per day, today / this week / this month counts; model filter, production
  / test, period per chart, refresh every minute. Fixed: torques not measured (-1.0) are left out (they were counted);
  quarter-to-date starts on the quarter's first day; weeks start on Monday (were labelled by their end); plotly.js and
  bootstrap are served by the dashboard itself (were CDN links: no internet, no charts); several browsers at once
  (threading server).
- **ShowScreen** unchanged (no database). It listens on 127.0.0.1 only, so the stream can be watched only on the
  machine PC itself.
- **`test/machine2_dryrun/DryRun_Endpoints.py`** (and machine1 copy): adds tagged records (QR `DRYRUN-ENDPOINTS-...`) to
  the test database, starts both services on free ports, checks every route and the dashboard numbers against SQL,
  deletes its records: 43/43. Also checked here in headless Chromium: dashboard tabs drawn, no script errors; query page
  download of CSV / JSON, model lists follow the database.

## 43. Machine 2 camera moved: knuckle check regions re-placed (2026-11-03)
- **Camera change measured** (image registration of 9 old/new pairs of the same model and side): the new pictures are
  scaled x1.098 and rotated +0.3 deg against the old ones; old (612, 345) is now (705, 353). Old point (x, y) ->
  (1.0979 x - 0.006 y + 34.7, 0.006 x + 1.0979 y - 29.8). Spread between pairs <= 5 px (parts sit slightly differently).
  The fixture plate is now bright (cleaned / replaced): the old grey levels no longer apply.
- **`siemens.vision.knuckle.*`** (all three properties files; logic, thresholds, binarisation and kernel unchanged):
  model polygons (step2 / step3) and the sizes of all regions = the old ones through the transform; the side boxes,
  the common checks A-D and E re-placed on the new pictures (positions only) because the transformed positions had
  margins of 3-11 grey levels on the brighter scene:
  side boxes 468,661 / 916,667 (22 x 22); A 785,210; B 534,295; C 547,449 r10; D 879,477 r10; E 692,590 (on the
  black bracket, as before); step2 557,201;547,231;515,247;534,250;549,241;560,215;
  step3 857,199;854,213;862,239;882,253;899,247;867,229.
- **Pictures** (`test/machine2_dryrun/knuckle_images/`, replaced; the old set is in git history): 14 knuckles (DOST LHS 5,
  DOST RHS 4, DOSTPLUS LHS 2, DOSTPLUS RHS 3; side by the arm, model as the check classifies it: the "RF0142"
  knuckles are DOST) and 5 of the empty fixture. DryRun_CheckKnuckle_Images now also checks that the empty fixture is
  NOT OK with every QR code. Result 35/35; smallest margins: A 20.6, B 29.7, C 21.0, D 32.2, E 27.3, side boxes 103,
  model polygons 52. The empty fixture fails on B, C and D (bright plate there now). Figure:
  `docs/knuckle_regions_machine2.jpg`.
- **Other stations** (hub, top bearing, nut): same camera, so their centres move by the same transform (about +93 px in
  x, +8 px in y, radii x1.10); to be measured on new pictures of those stations.

## 44. Machine 2 camera moved: hub check re-centred (2026-11-03)
- **Measured on 13 new hub pictures** (8 DOST, 5 DOSTPLUS): hole ring centre (706-709, 351-354), median (707, 352),
  radius 143.6 (was 130.5); stud ring 172-177 (DOST) / 182-186 (DOSTPLUS) (was 156-161 / 165-167). Ratio 1.10 and the
  centre agree with the camera change measured on the knuckle pictures (section 43).
- **`siemens.vision.hub.*`** (all three properties files; logic, tolerances and the centre limit 140 unchanged):
  `segment.center` 707,352 (was 613,341); `segment.outer/inner.radius` 253/116 (230/105 x1.10); `min/max.mask.area`
  121/3630 (100/3000 x1.10^2); `centre.check` 707,352, radii 38/60 (35/55 x1.10; the check runs only when
  enforceBearingAbsenceCheck=True).
- **Checked here** (the real post-processing - shape filters, grouping, polygon search, model match - with the holes and
  studs found on the picture standing in for MobileSAM): 13/13 OK with their own model and MISMATCH with the other;
  with the previous settings 0/13. Pictures replaced in `test/machine2_dryrun/hub_images/` (old set in git history).

## 45. Machine 2 camera moved: top bearing and nut / plate washer checks re-centred (2026-11-04)
- **Measured on the new pictures (8 bearing, 5 washer-only, 5 nut + washer):** bearing ring centre about (707, 351),
  inner / outer race radius 37-43 / 50-55 (previous camera 33-36 / 46-50, x1.10 as on the fixture). Nut centre
  (707.7, 346.9), outline about 4650 px (previous camera about 4380: only x1.06 in area, x1.03 across).
- **Top bearing (`siemens.vision.topbearing.*`, all three properties files):** `annular.center` 613,345 -> **707,354**
  (3 px below the ring centre, as before); `annular.outer.radius` 120 -> 132, `detection.mask.radius` 60 -> 66,
  `target.ring.thickness` 14 -> 15, `target.ring.thickness.for.area.score` 15 -> 16, `center.offsets` 0,0;0,-10 ->
  0,0;0,-11 (all x1.10). **Not scaled:** `prompt.radius` stays 40 - at 44 two DOSTPLUS bearing pictures found no ring
  (the edge windows 0.65-1.2 x 40 and 0.8-1.6 x 40 still contain the new ring, 37-43 / 50-55); the normalisation
  sigmas (`bg.norm`, `contrast.norm`) stay - they are smoothing scales, and scaling them gave a wider, off ring on one
  picture. Brightness limit (30) and all other values unchanged.
- **Top bearing results (real check, OpenCV only):** 8/8 bearing pictures OK, brightness over the open-bore hub picture
  +39.1..+57.7 (limit 30), ring centres (707-709, 350-357); 13 open-bore hub pictures and 5 washer-only pictures NOT OK
  (no ring found). Same 26/26 with the search centre moved 2 px either way (705-709 x 352-356). Previous settings: 0/8
  bearing pictures found. Either open-bore hub picture (DOST or DOSTPLUS) works as the hub reference.
- **Nut / washer (`siemens.vision.nutwasher.*`, all three properties files):** `nut.detection.center` 612,349 ->
  **708,358** (11 px below the nut centre, as before). `nut.reference.center` 80/69 unchanged: the nut centre falls at
  (79, 68) in the search disc again (composite symmetry 81.5-82.6; the Machine 1 point 72/64 gives 68-70). Nut area and
  shape limits unchanged: the nut measured only 3% larger across, and its outline (about 4650 px) sits inside
  3000-5500. Washer limits unchanged (0.55 / 5.57, user decision).
- **Nut / washer results** (nut outline by thresholding standing in for MobileSAM / YOLO, everything after it real):
  5/5 nut + washer pictures OK, diff_R +32.2..+33.9, diff_RB +7.6..+8.1; 5/5 washer-only pictures NOT OK (no nut).
  With the previous centre the nut lies outside the search disc (95 px away, radius 80). Note: diff_RB is +7.6..+8.1
  here against +15..+18 with the previous camera - about 2 above the 5.57 limit.
- Verifier: ALL EQUIVALENT. Pictures replaced: `test/machine2_dryrun/top_bearing_images/` (DOST / DOSTPLUS bearing),
  `test/machine2_dryrun/nut_washer_images/` (DOSTPLUS washer / nut; use `top_bearing_images/DOSTPLUS_bearing_01.jpg`
  as the bearing picture and `hub_images/DOSTPLUS_01.jpg` as the hub reference). Old sets in git history.

## 46. Top bearing check: ring refined after the winning branch (both machines) (2026-11-04)
- **Found (Machine 2 pictures, measured against the brightness profile across the race and an independent per-angle
  edge fit):** the branch's outer-edge points are pulled off-centre by the roller cage and grease (outer circle fits up
  to 14 px off-centre). The second edge is not fitted but taken as anchor +/- a width, the percentile of measured
  widths nearest `target.ring.thickness`; the chosen widths were 10-14.4 against a true 14 (DOST) / 15 (DOSTPLUS).
  Result: inner edge 6 px too large on 5 of 8 pictures (ring on the outer part of the race only), outer edge up to
  5.4 px off, centre up to 2.4 px off (when the outer edge was the anchor, the inner circle cut into the bore).
- **Change (`camera/CheckTopBearing.py`, both machines):** after the winning branch is chosen (branch selection and
  validation unchanged), `_refine_ring` searches on the background-normalised grey picture:
  outer edge = circles round the branch's outer circle (centre +/- `refine.centre.span` 4 px, radius
  +/- `refine.outer.radius.span` 10 px); inner edge = concentric, at a width of `target.ring.thickness`
  +/- `refine.thickness.span` 2 px. Edge contrast = mean 1-3 px on the bright side minus the dark side, median over
  angles (every 2 degrees), so sectors with little grease or bright rollers do not decide it. Each outer circle is
  scored with its best inner edge: the roller cage edge at r 62.5 has the same outer contrast as the race edge
  (92.9 against 93.5) but no race inner edge inside it (15 against 129). 1 px pass, then +/- 1 px at `refine.step` 0.5.
  The refined ring is used for the brightness comparison, the returned geometry (also used by the nut / washer
  check) and the result picture. `refine.ring=False` gives the previous behaviour. About 0.1 s per check.
- **New keys (both machines, all three properties files):** `<vendor>.vision.topbearing.refine.ring` True,
  `.refine.centre.span` 4.0, `.refine.outer.radius.span` 10.0, `.refine.thickness.span` 2.0, `.refine.step` 0.5.
  Why +/-2 round the configured width and not round the width found per picture: with the found width (10) the true
  14-15 was out of reach. Why radius +/-10: the branch's outer circle was up to 6 px too large; with +/-6, starts
  10 px off missed the race edge.
- **Results:** Machine 2: refined ring within 0.8 px of the reference edges on all 8 bearing pictures (whole-pixel
  result); brightness margin +51.7..+60.9 (was +39.1..+57.7, limit 30); 13 open-bore and 5 washer-only pictures
  NOT OK (no ring, refinement not run). Machine 1: 15 bearing OK (+101.9..+148.2), 11 open-bore NOT OK; rings
  equal or better centred (figure). Start circle moved +/-3 px (centre) and +/-4 px (radius), 27 starts per picture:
  the same ring from every start on all 23 pictures with a ring (spread 0.0 px).
- **Nut / washer check (uses the top bearing ring):** the ring now covers the full race: Machine 2 nut + washer pairs
  (5 nut x 4 bearing pictures) diff_R +57.1..+60.7 (was +32..+34), diff_RB +13.3..+14.5 (was +7.6..+8.1, limit 5.57);
  bearing against bearing of the same part (no washer) diff_RB -0.8..+0.8; washer-only pictures NOT OK (no nut).
- **Verifier:** reference commit 010969a (the previous reference with this logic, values hard-coded) in
  `baseline_per_file`; manifest lines moved. ALL EQUIVALENT. Figure: `docs/top_bearing_ring_refinement.png`.

## 47. Top bearing profile kept for the nut / washer check (cleared at cycle end and on aborts); bearing and nut size ranges (both machines) (2026-11-04)
- **Before:** the camera server kept only `{center, inner_radius, outer_radius}` (whole pixels) in its own memory
  (`ImageProcessor.bearingCentreAndRadii`), set only when the top bearing check passed (a failed retake left the
  earlier ring), cleared only when the next cycle's knuckle check started - not at the end of a cycle, not on an abort
  (the camera server is not told about either; the IOServer is).
- **Profile (`CheckTopBearing.checkTopBearing`, returned geometry):** besides `center` / `inner_radius` /
  `outer_radius` (unchanged, whole pixels): `center_precise`, `inner_radius_precise`, `outer_radius_precise`,
  `outer_diameter`, `width`, `brightness_difference`, `profile_angles_deg` (0, 2 ... 358), `inner_profile` and
  `outer_profile` (180 [x, y] picture points each: on every ray the edge position within +/- 4 px of the circle,
  `_ring_profile`) and their edge contrasts. About 10 KB as JSON; edges within 0.5 px of the circle (median) on the
  Machine 2 bearing pictures.
- **Storage (`utils/RedisUtils.py`):** Redis key `TopBearingProfile` = {componentQRCode, savedAt, profile}, 1 h expiry
  as a safety net; `saveTopBearingProfile`, `readTopBearingProfile(componentQRCode)` (None if none, unreadable, or
  saved for another component), `clearTopBearingProfile`; own connection pool with 1 s timeouts.
- **Camera server:** after every top bearing check `ImageProcessor.saveBearingProfile(geometry if OK else None)`
  (a NOT OK result or an exception removes a ring kept earlier); the nut / washer check and the two bunk checks get
  `ImageProcessor.getBearingProfile()` (Redis, current component only); removed at camera-server start and at the
  start of a cycle (knuckle step, as before).
- **IOServer (both PLC vendors):** the state machine listener (`_onStateChange`, also the audit trail) removes the
  profile whenever the machine returns to READ_QR_CODE from another state: cycle completed (from the last state) or
  aborted (PLC emergency button, heartbeat loss, abort from the frontend - all through `abortCycle`); audited
  ("Top bearing profile removed (cycle completed / aborted in <state>)"). Also removed at IOServer start.
- **Bearing size (`<vendor>.vision.topbearing.outer.diameter.min/max`, `.width.min/max`, px):** the ring after
  refinement must be within them, otherwise NOT OK ("ring is not a bearing's size"). Machine 1 86-110 / 9-20,
  Machine 2 89-113 / 10-21. Measured: bearings Machine 1 92-98 / 13-15, Machine 2 98-102 / 14-15; the nut found as a
  ring by this check (nut pictures) 75-81 / 12-13 - every nut picture (10 Machine 1, 5 Machine 2) is now NOT OK, was OK.
- **Nut size (`<vendor>.vision.nutwasher.nut.outer.diameter.min/max`, `.nut.width.min/max`, px; `_measure_nut`):**
  outer diameter = circle of the nut outline's area; bore radius = strongest dark-to-bright step between 0.45 and 0.9
  of the outer radius (median over angles); width = outer radius - bore radius. Outside the ranges: NOT OK ("shape
  found is not a nut's size"). Machine 1 64-84 / 6-13, Machine 2 66-87 / 6-13. Measured (nut outline from
  thresholding standing in for MobileSAM): Machine 2 76.8-77.0 / 10.1-10.2, Machine 1 74.6-80.7 / 8.9-10.7. A whole
  bearing offered as a nut: 99-101 px, rejected; a disc the size of the bearing's bore: 69-73 px (nut-like) but width
  15.5-18.6, rejected by the width (why the width is 6-13 and not wider).
- **Results:** top bearing 26/26 both machines (as before); nut / washer 20 nut + washer pairs OK (Machine 2), 5
  washer-only NOT OK, Machine 1 nut sizes all accepted; camera-side profile checks 5/5; DryRun_IOServer_Fault_Scenarios
  new scenario H (saved, kept through the cycle, not given to another component, expiry set, removed at cycle end,
  on a frontend abort and on a PLC emergency abort, next cycle normal): Machine 2 91/91, Machine 1 90/90.
- **Dry-run windows:** DryRun_TopBearing_Check shows the ring's outer diameter / width and the size reason;
  DryRun_NutWasher_Check shows the nut size and the size reason (machine1 copies regenerated).
- **Verifier:** reference commit 86b4e8f for CheckTopBearing and CheckNutAndPlateWasher; ALL EQUIVALENT.

## 48. Top bearing profile kept in memory (Redis key removed): end-of-cycle message IOServer -> camera server; bearing ranges 86-113 / 9-21 on both machines; mean diameter evaluated (2026-11-04)
- **Why (user):** Redis is for inter-process communication only; what stays within a process is kept in memory
  variables. The profile is made and used inside the camera server, so it is kept in `ImageProcessor` again
  (`bearingCentreAndRadii`, now the full geometry with the edge profile, and `bearingProfileQRCode`, the component it
  was kept for). The Redis key `TopBearingProfile` and its save / read / clear functions (section 47) are removed.
- **What the camera server could not know:** the end of a cycle and aborts happen in the IOServer. Now
  `IOServer._onStateChange` sends `sendEndOfCycleFromIOServerToCameraServer` (action `END_OF_CYCLE`, "completed" /
  "aborted", on io2camera.q) whenever the machine returns to READ_QR_CODE: cycle completed, PLC emergency button,
  heartbeat loss, abort from the frontend (in an abort, after the queues are cleared); audited. Own Redis connection
  with 1 s timeouts. `readDataInCameraServerFromIOServer` returns `(time, READ_QR_CODE, False)` for it (every caller
  acts only on take-picture, so nothing else changes); `MonitorGetPicQueue.processItem` then calls
  `ImageProcessor.clearBearingCentreAndRadii()`. If the message is overtaken (the camera queue keeps only the newest
  message), the next cycle's knuckle request drops the profile as before.
- **Kept / dropped:** saved after an OK top bearing check; a NOT OK top bearing result drops it (only the top bearing
  check writes it - nut / washer retakes read the same profile again); `getBearingProfile()` gives it only for the
  component it was kept for; dropped on the end-of-cycle message and at the knuckle step.
- **Bearing ranges:** `<vendor>.vision.topbearing.outer.diameter.min/max` = 86 / 113 and `.width.min/max` = 9 / 21 on
  both machines (code defaults the same).
- **Mean diameter (user's proposal: the diameter where the mean of the inner and outer profile lands, for bearing and
  nut) - evaluated, not adopted:** pooled over both machines, bearings 78.5-87.0 px, nuts 64.3-69.7 px (gap 8.8);
  outer diameter: bearings 92.3-101.7, nuts 73.9-80.9 (gap 11.4). The spread within each group is a little smaller
  (bearings 8.5 against 9.4, nuts 5.4 against 7.0) but the gap is smaller still, because the mean is the outer
  diameter minus the width and the bearing ring is 1-3 px wider than the nut's. Per machine: Machine 2 outer gap 17.0
  / mean gap 15.5; Machine 1 outer gap 11.4 / mean gap 8.8. The outer diameter stays the test.
- **Results:** top bearing 26/26 both machines, every nut picture (15) NOT OK on size; camera-side profile checks
  10/10 (kept in memory and not in Redis; another component gets none; picture requests and nut retakes keep it;
  end-of-cycle message completed / aborted drops it; NOT OK top bearing drops it; knuckle step drops it);
  DryRun_IOServer_Fault_Scenarios scenario H rewritten (no end-of-cycle message while the cycle runs; exactly one at
  completion, on a frontend abort, on a PLC emergency abort; a normal cycle afterwards): Machine 2 88/88, Machine 1
  87/87; DryRun_All_Servers_Integrated_Cycle both vendors as before.
- **Verifier:** reference commit df144cc for CheckTopBearing; ALL EQUIVALENT.

## 49. Mean diameter conditions: bearing >= 75 px, nut <= 72 px (both machines) (2026-11-04)
- **Definition:** mean diameter = along every 2 degrees, the inner edge's radius plus the outer edge's radius (from the
  ring / nut centre), averaged round the ring. Bearing (CheckTopBearing): the two edges of the ring's profile
  (`_ring_profile`, now computed before the size check). Nut (`CheckNutAndPlateWasher._measure_nut`, now returns a 4th
  value): the nut outline's radius and the bore edge (found within +/- 4 px of the bore circle).
- **Keys (both machines, all three properties files):** `<vendor>.vision.topbearing.mean.diameter.min` = 75 (ring NOT OK
  below: "ring is not a bearing's size ... mean diameter"), `<vendor>.vision.nutwasher.nut.mean.diameter.max` = 72
  (NOT OK above: "shape found is not a nut's size ... mean diameter"). In addition to the outer diameter and width
  ranges. Returned geometry gains `mean_diameter`.
- **Measured (px):** bearings Machine 2 83.5-87.0, Machine 1 78.5-83.3 (closest: 3.5 above 75); nuts in the nut check
  Machine 2 67.8-67.9, Machine 1 65.1-69.8 (closest: AB_nut_09 69.8, whose stand-in outline merged with grease; clean
  nuts <= 68.7); nut pictures in the top bearing check 64.3-68.0.
- **Each condition alone (other size limits off):** top bearing 23/23 bearings OK, 15/15 nut pictures NOT OK, 11/11
  open bores NOT OK; nut check: a whole bearing offered as a nut 84.4 / 87.5 -> rejected. A disc the size of the
  bearing's bore has mean 53.7-54.3 and passes "<= 72"; the nut width limit (6-13) rejects it.
- **Results (all limits):** top bearing 26/26 both machines; nut / washer 5/5 Machine 2 nut + washer OK, 5/5
  washer-only NOT OK, Machine 1 nut sizes all accepted (AB_nut_06/07 fail before the size test with the stand-in
  outline, as before). Dry-run windows show the mean diameter. Verifier: reference 7f62a10; ALL EQUIVALENT.

## 50. Nut / washer check: three Machine 2 DOST pictures failing with nut and washer present - bore search, failure reason, nut mean diameter minimum (2026-11-04)
- **Pictures** (added as `test/machine2_dryrun/nut_washer_images/DOST_nut_01..03.jpg`; use a DOST bearing picture):
  DOST_nut_01 = ...4720 (washer NOT OK: diff_RB +5.1 <= 5.57), DOST_nut_02 = ...6408 ("no hexagon"),
  DOST_nut_03 = ...8287 ("not a nut's size": width 21.5, mean diameter 56.5). On these DOST parts the nut face is a
  narrow bright hexagon; inside it a dark gap, then the bright collar of the shaft end, then the dark hole.
- **8287 - bore found on the shaft-end collar (fixed):** width 21.5 = bore radius 17.6 = 0.45 x the outer radius,
  the lower end of the bore search. Dark-to-bright steps measured round the nut: the nut face's inner edge (the bore)
  at 0.72-0.77 R (strength 82-117) on all 18 nut pictures; the shaft-end collar at 0.42-0.47 R (39-75, strongest on
  the DOST pictures). `_measure_nut` now searches 0.6-0.9 R. All nuts that passed: same bore (26.9-31.4) and width
  (8.4-10.6).
- **6408 - reason hidden (fixed reporting):** `NutDetector.detect_nut_in_image` kept a failure reason only for
  area / color / no_nut; every other rejection (hexagon tests, symmetry / thickness, centre overlap, centre disc)
  was dropped and the dry-run window showed "no hexagon". It now keeps the first rejection of any kind (no change to
  OK / NOT OK). With the nut outline from thresholding standing in for MobileSAM, 6408 is rejected for
  "low_thickness (score=44.3 < 45)" (8287: 45.2, just above); the reason with MobileSAM's outline is to be read in the
  window. Not changed: the thickness limit.
- **Nut mean diameter minimum `<vendor>.vision.nutwasher.nut.mean.diameter.min` = 60 (both machines):** with the
  narrower bore search a disc the size of a bearing's bore has width 13.8-14.6 (limit 13; was 15.5-18.6), so the
  width alone rejected it by 0.8 px; its mean diameter is 54.7-57.4, nuts 65.0-69.9. Range now 60-72.
- **4720 - washer (not changed, for a decision):** diff_R +45.3..+48.0 (limit 0.55), diff_RB +4.1..+5.4 (limit
  5.57) against any of the 8 bearing pictures; DOST_nut_03 diff_RB +7.1..+7.6; DOSTPLUS nut pictures +13.6..+14.4.
  Without a washer (bearing against bearing of the same part): diff_R -2.6..+2.6, diff_RB -0.8..+0.8.
- **Idea evaluated, not adopted:** a regular hexagon fitted to the nut's outer edge found along every 2 degrees within
  +/- 15 px of the nut ring: circle-fit / hexagon-fit ratio nuts 0.76-1.60, bearings 0.36-1.03; 6-fold ripple nuts
  0.5-5.2 %, bearings 0.3-6.3 %; the three failing pictures had the weakest hexagon signal (0.8-1.9 %).
- Verifier: reference fc95d43 for CheckNutAndPlateWasher and HexagonNutDetector (now in `baseline_per_file`);
  ALL EQUIVALENT.

## 51. Nut check: thickness-symmetry rejection removed; ring fallback when no hexagon is accepted (both machines) (2026-11-04)
- **Thickness symmetry (removed, user decision):** `calculate_perpendicular_thickness_score` x
  `calculate_radial_thickness_score` x 100 = how evenly thick the nut face is in all directions (outer hexagon edge to
  a fixed 25 px disc round the centre of mass, along the 6 perpendiculars and the 6 radials). The rejection
  "low_thickness (score < `vision.nut.min.thickness.score` 45)" is removed from `HexagonNutDetector.process_masks_cpu`
  (the score is still computed and reported); key removed from all three properties files and the manifest. Bearings
  it was meant to catch are now rejected by the size limits (sections 47-50). DOST_nut_02 (6408) now passes the nut
  step with the stand-in outline (it failed on 44.3 < 45).
- **`CheckTopBearing.findRing(anImage)`:** the top bearing ring finder (same branches, refinement, edge profile)
  without the bearing size limits and the hub brightness comparison; returns centre, radii, outer diameter, width,
  mean diameter (picture coordinates) or None. On the 23 bearing pictures it gives exactly the check's ring (0.000 px).
- **Ring fallback (`CheckNutAndPlateWasher._nut_from_ring`):** when the nut detector accepts no hexagon, `findRing` on
  the nut picture; a ring with outer diameter `<vendor>.vision.nutwasher.nut.ring.outer.diameter.min/max` (70 / 86)
  and mean diameter <= `.nut.ring.mean.diameter.max` (74) is accepted as the nut (`.nut.ring.fallback` True). The
  nut mask for the washer comparison is the ring's disc, 2 px wider; the nut size check of section 47 is not applied
  (the ring ranges size it). Logged as "nut accepted by the ring fallback" or why not.
- **Measured on every picture:** nuts 18/18 accepted (ring outer diameter 75.4-81.8, mean 64.3-68.0); washer only 0/5
  (no ring); bearings 0/23 (92.3-101.7, mean 78.5-87.0); open bores 0/24 (no ring, or 117-122).
- **End to end, nut detector forced to fail:** Machine 2 DOST_nut_03, DOSTPLUS nuts OK through the fallback,
  DOST_nut_01/02 nut accepted then NOT OK on the washer diff_RB (+3.8 / +4.2; washer limits unchanged, user
  decision), washer-only and bearing pictures NOT OK; Machine 1 nuts OK (AB_nut_06/07 included, which the stand-in
  outline never detected). With the ring's disc as the nut mask, diff_RB is 1-2 lower than with the hexagon mask.
- Top bearing 26/26 both machines (unchanged). DryRun_NutWasher_Check shows the fallback (reason and the ring).
  Verifier: reference 4e04121 for CheckTopBearing, CheckNutAndPlateWasher, HexagonNutDetector; ALL EQUIVALENT.

## 52. Nut width limit removed (both machines) (2026-11-04)
- **Why (user):** on Machine 1, 6 of 96 pictures with a nut were NOT OK only on the nut width (13.2, 14.5 above 13;
  5.1 below 6) with outer diameter 75.5-80.0 and mean diameter 66.6-70.6 inside their ranges; the diameter checks are
  sufficient. `<vendor>.vision.nutwasher.nut.width.min/max` removed (code and all three properties files); the width
  is still measured and reported ("Nut size: ... width ..."). The top bearing's width range is unchanged.
- **Still rejected without it:** a whole bearing offered as a nut (outer diameter 99-101, mean 84-88) and a disc the size
  of a bearing's bore (mean 54.7-57.4 < 60).
- **Results:** the six Machine 1 cases accepted; Machine 1 nut pictures 10/10 OK; Machine 2 DOSTPLUS nuts and
  DOST_nut_03 OK, washer-only NOT OK, DOST_nut_01/02 NOT OK on the washer diff_RB (unchanged, user decision).
  Verifier: reference for CheckNutAndPlateWasher in `baseline_per_file` updated; ALL EQUIVALENT.

## 53. Nut / plate washer settings of both machines in sync (2026-11-04)
- The nut / washer logic changes (sections 47-52: size checks, bore search 0.6-0.9 R, failure reason kept,
  thickness-symmetry rejection removed, ring fallback, width limit removed) are in the shared code and apply to both
  machines; removed keys are removed for both. Compared every `<vendor>.vision.nut*` key: the only remaining
  difference besides the camera positions (`nutwasher.nut.detection.center`, `nut.reference.center.x/y`) was the nut
  outer diameter range, Machine 1 64-84 / Machine 2 66-87. Machine 2 set to 64-84 (= Machine 1 = code default;
  Machine 2 nuts measure 74.6-77.0).
- Machine 2 re-run: DOSTPLUS nuts and DOST_nut_03 OK, washer-only NOT OK, DOST_nut_01/02 NOT OK on the washer diff_RB
  (unchanged, user decision). Verifier ALL EQUIVALENT.

## 54. Washer comparison area strictly on the bearing race (both machines) (2026-11-04)
- **Found:** the area compared for the washer (diff_R, diff_RB) was the top bearing ring drawn as two circles with
  whole-pixel centre and radii. Measured against the race edges found along every 2 degrees: 92.1-98.4 % of it was on
  the race; the rest lay up to 3.4 px over the dark gap inside or the grease outside.
- **Change (user's construction, `_create_bearing_mask_in_annular_space`):** start from the bearing ring's measured
  edges (`inner_profile` / `outer_profile` of the top bearing geometry); a pixel is in the area when its distance from
  the ring centre is at least the inner edge + `<vendor>.vision.nutwasher.washer.mask.edge.margin` (1 px) and at most
  the outer edge - 1 px, both edges taken at the pixel's angle (interpolated between the rays): an annulus 2 px
  narrower than the race. The nut's mask is then removed from it (unchanged). Without an edge profile: the precise
  circles with the same margin. (A first version drawing the moved edges as polygons left 0.1-0.3 % of the pixels up
  to 1.5 px off where the edge steps between rays; replaced by the per-pixel test.)
- **Result:** every pixel of the area on the race, at least 1.0 px inside both edges, on all 23 bearing pictures (both
  machines); 68-89 % of the former area (about 2250-3300 px).
- **Washer differences:** Machine 2 DOST nut + washer pictures against 4 DOST bearing pictures: diff_RB DOST_nut_01
  +5.53..+6.38 (was +4.8..+5.4), DOST_nut_02 +5.36..+6.18 (was about +5.0), DOST_nut_03 +7.75..+8.56 (was +7.1..+7.6);
  DOSTPLUS nuts +13.8..+14.6 (was +13.6..+14.4); diff_R +44.7..+58.2. No washer (bearing against bearing of the same
  part): diff_R -2.5..+2.5, diff_RB -0.82..+0.81 (unchanged). Machine 1 nuts all OK (diff_RB +11.7..+31.0). Limits
  unchanged (0.55 / 5.57): DOST_nut_01 now passes against 3 of the 4 DOST bearing pictures, DOST_nut_02 against 1.
- Verifier: reference 8ece644 for CheckNutAndPlateWasher; ALL EQUIVALENT.

## 55. Washer R-B limit 5.57 -> 3.5 (both machines) (2026-11-04)
- `<vendor>.vision.nutwasher.washer.threshold.rb` = 3.5 in all three properties files (user decision; machines in sync,
  so the code default is 3.5 too - verifier reference 181790d). diff_R limit unchanged (0.55).
- Measured with the area of section 54: washer present diff_RB +5.36..+8.56 (Machine 2 DOST), +13.8..+14.6 (DOSTPLUS),
  +11.7..+31.0 (Machine 1); no washer -0.82..+0.81. Margins: 1.86 below the lowest washer value, 2.69 above the
  highest no-washer value.
- Machine 2 re-run: DOST_nut_01/02/03 OK against all 4 DOST bearing pictures (12/12), DOSTPLUS nuts OK, washer-only
  NOT OK. ALL EQUIVALENT.

## 56. NoBunk check: Machine 2 search centre (2026-11-04)
- **Checked:** `CheckNoBunk.checkNoBunk` only runs the hexagon nut detector (`HexagonNutDetector`) in a disc of
  `vision.nobunk.nut.detection.radius` (80) round `vision.nobunk.nut.detection.center` and returns whether a nut was
  found; no washer comparison. The hexagon tests and the nut reference point (`vision.nut.*`) are shared with the nut /
  washer check and were already set for Machine 2. Machine 1 uses the same centre for both checks (632,360).
- **Change:** `siemens.vision.nobunk.nut.detection.center` 632,360 (the Machine 1 value, never re-tuned) -> **708,358**,
  the Machine 2 nut / washer centre (all three properties files). With the old centre the nut (about 708,347) was 76 px
  from the centre of an 80 px search disc.
- **Test** (no NoBunk-stage pictures from Machine 2; the nut pictures stand in; nut outline from thresholding in place
  of MobileSAM, the detector's real loop and hexagon tests): old centre 0/8 nut pictures OK; new centre 8/8 OK, and 0/9
  washer-only / bearing pictures OK. Not added to NoBunk: the nut / washer check's ring fallback and size checks.

## 57. Cap check: per-machine settings, Machine 2 tuned (2026-11-04)
- **Found:** the cap check read global `cap.check.*` keys (no machine prefix), so Machine 2 used Machine 1's circle
  (635,360) - 72 px from the cap on the Siemens camera (caps at about 707,348). `Configuration.py` getters had mixed
  defaults (radius 70 vs 100, delta 70) and `int()` on float limits; `cap.check.delta.threshold.dost/dostplus` were read
  but never used; the mask cache was keyed on size and radius only (a new centre after a live reload was ignored).
- **How Step 1 gets its pictures (unchanged):** the hub, top bearing and nut / washer pictures of the same part are kept
  in memory by the camera server (`ImageProcessor.currentPictures`, set before each check, cleared at the knuckle step);
  a missing one makes the cap check NOT OK.
- **Change:** `CheckCap` reads `<vendor>.vision.cap.*` through VisionConfig (centre, radius per model, R-B limit per
  model, Step 1 limits hub / top / nut per model); `cap.check.*` keys and the `getCapCheck*` getters removed; the dead
  Step 1 comment block and the unused single delta limits removed; mask cache keyed on size, centre and radius.
  Machine 1 values = its former `cap.check.*` values (behaviour unchanged).
- **Machine 2 values** (20 Siemens cap pictures, 10 silver DOST / 10 brass DOSTPLUS, against the Machine 2 hub, top
  bearing and nut pictures in the dry-run folders; each limit midway between cap and no cap):

  | key (siemens.vision.cap.) | Machine 1 | Machine 2 | cap present (min) | no cap (max) |
  |---|---|---|---|---|
  | center | 635,360 | 707,348 | | |
  | delta.threshold.hub.dost | 30 | 40 | 52.4 | 28.0 |
  | delta.threshold.top.dost | 30 | 30 | 42.6 | 18.1 |
  | delta.threshold.nut.dost | 35 | 28 | 45.2 | 11.8 |
  | delta.threshold.hub.dostplus | 35 | 43 | 49.9 | 37.4 |
  | delta.threshold.top.dostplus | 50 | 30 | 37.2 | 22.5 |
  | delta.threshold.nut.dostplus | 35 | 20 | 36.3 | 6.1 |

  Radii (100 / 70) and R-B limits (-1 / 15) unchanged: R-B DOST -12.5..-17.2, DOSTPLUS +43.1..+44.7. With the Machine 1
  limits every brass cap would fail (top bearing brightening 37-41 < 50). "No cap" = a nut picture used as the cap
  picture. DOST R-B without a cap is -6..-8 (passes Step 2), so for DOST a missing cap is caught by Step 1 only.
- **Test:** `test/machine2_dryrun/DryRun_Cap_Check.py` (Machine 1 copy generated): Machine 2 20/20 caps OK, 8/8 no cap
  NOT OK; Machine 1 13 cap pictures Step 2 OK (no Machine 1 hub / bearing / nut pictures in the repo). Pictures saved in
  `cap_images/`. Verifier: reference 219eeb0 for CheckCap; ALL EQUIVALENT (Machine 1 manifest copy re-synced).

## 58. Cap check: identify the cap first (brass, silver, none), then the model and the comparisons (2026-11-04)
- **Why:** a missing DOST cap was caught only by the comparisons with the earlier pictures (Step 1): R-B without a cap
  (-6..-11) passes the DOST colour test (< -1), and R-B / R-G cannot tell silver from bare steel (they overlap at every
  radius from 30 to 100). Brightness near the centre can: without a cap the bore and shaft end are dark.
  Mean brightness (B+G+R)/3, Machine 2, silver cap lowest vs no-cap highest (13 nut / washer + 8 bearing pictures):
  r10 125 vs 61, r20 120 vs 78, **r25 122 vs 76**, r30 124 vs 80, r40 121 vs 112 (nut faces), r50 124 vs 98, r70 125 vs 102,
  r100 109 vs 108.
- **New order (user's design, plus the silver test):**
  1. which cap: **brass** if mean R-B in the DOSTPLUS circle (r70) > `rb.threshold.dostplus` (15); else **silver** if mean
     R-B in the DOST circle (r100) < `rb.threshold.dost` (-1) AND mean brightness within `cap.silver.brightness.radius`
     (25) >= `cap.silver.brightness.min`; else **none**;
  2. the cap must be the model's (QR): DOST silver, DOSTPLUS brass - otherwise "wrong cap";
  3. comparisons with the hub, top bearing and nut / washer pictures with the limits and circle of the cap found.
  OK = right cap and all three comparisons pass. Logs and the marked picture say which cap was found and which was needed.
- **Settings:** `<vendor>.vision.cap.silver.brightness.radius` = 25 (both); `.silver.brightness.min` = **100.0** on Machine 2
  (user's choice: 22 above the lowest silver cap, 24 below the highest no-cap picture), **0.0 = off** on Machine 1 (no
  Machine 1 no-cap pictures yet; its silver caps read 175-209). Masks now cached per (size, centre, radius).
- **Machine 1:** same results as before for any real picture. Brass / not-brass is the old DOSTPLUS colour test; with
  the silver test off, "silver" is the old DOST colour test. The only case decided differently - brass R-B at r70 but
  negative R-B at r100 on a DOST part - does not occur (brass caps read +20..+28 at r100).
- **Test** (`DryRun_Cap_Check.py`, now also wrong-cap and both-model no-cap rows; the check is run with the earlier
  pictures that make it hardest to give the right answer): Machine 2 - 20/20 caps OK; 20/20 caps as the other model NOT OK
  (wrong cap); 26/26 nut / washer pictures as DOST or DOSTPLUS NOT OK ("none": brightness 69-75). The cross-model case
  that the comparisons alone accepted (DOSTPLUS nut checked as DOST, 9/10 accepted before) is now rejected at step 1.
  Machine 1 - 13/13 caps identified correctly, 13/13 wrong-model NOT OK.
- **Not yet tested:** the real no-cap state at the cap station is after the split pin and washer; no such Machine 2
  pictures. A bright split pin / washer would raise the r25 brightness.
- Verifier: reference e9171d2 for CheckCap; ALL EQUIVALENT (both copies).

## 59. Bunk checks (states 13 and 24): Machine 2 centre and DOSTPLUS hole spacing (2026-11-04)
- **Reference points (unchanged code, `camera/BunkSegmenter.py`):** one centre, `vision.bunk.outer.disc.center`. The r250
  disc round it is segmented (MobileSAM), holes are kept only inside r110 round the same centre (area 325-1200 px,
  bounding box 15-45 px), and the same centre is the reference for the DOST square's angular-spacing score. DOST: 4 holes
  forming a square (sides within 20 % of the median, corners 65-115 deg); DOSTPLUS: exactly 2 holes,
  `dostplus.hole.pair.distance.min/max` apart. Siemens values were still Machine 1's (630,350; 75-100).
- **Measured** on 19 Machine 2 pictures of the new bungs (not yet blackened; holes not yet polished), saved in
  `test/machine2_dryrun/bunk_images/`:

  | bung | pictures | bung face rim centre | hole-pattern centre | holes |
  |---|---|---|---|---|
  | DOST (4 holes) | 6 seated | (709.5-716.3, 341.8-348.5), mean (713,345) | mean (715,344) | sides 71.6-77.3 px, 44-63 px from centre |
  | DOSTPLUS (2 holes) | 7 seated | (713.0-715.4, 336.3-338.2), mean (714,337) | mean (717,337) | pair 108.2-112.0 px apart, 49-62 px from centre |

  6 more DOSTPLUS pictures show the bung not seated (tilted or slid 20-25 px): left out of the centre and saved as
  `DOSTPLUS_bunk_not_seated_*`.
- **Change (siemens only, all three properties files):** `vision.bunk.outer.disc.center` 630,350 -> **715,341** (between the
  two bungs, which are 8 px apart; each within about 5 px of it); `dostplus.hole.pair.distance.min/max` 75/100 -> **95/125**
  (the Machine 2 DOSTPLUS bung's holes are 108-112 px apart, so the Machine 1 range would reject every one). One centre
  for both bungs: with it the farthest hole edge is about 85 px from the centre, inside the r110 search disc.
- **Not tested end-to-end:** MobileSAM is not available in this environment; the blackened / polished bungs will look
  different (dark holes instead of bright domes). Re-run the bunk dry run on the finished bungs. The check does not test
  whether the bung is seated: an unseated bung with both holes visible and correctly spaced can pass.
- Verifier: ALL EQUIVALENT (Machine 1 values unchanged).

## 60. Bunk check dry run (both machines) (2026-11-04)
- **Machine 1 has one bunk centre for both bungs** (`vision.bunk.outer.disc.center` is the only centre in the code and in
  its history), so Machine 2 keeps one too (715,341, section 59).
- **New:** `test/machine2_dryrun/DryRun_Bunk_Check.py` (Machine 1 copy generated). Runs the real `CheckBunk.checkBunk`
  (state 24) and / or `CheckNoCapBunk.checkNoCapBunk` (state 13) with this machine's settings on `<folder>/DOST_*` and
  `DOSTPLUS_*` pictures: each as its own model (must be OK) and as the other model (must be NOT OK); `*not_seated*` only
  reported. Prints the deciding gamma, the holes found (centres in the full picture), the pair distance or the 4 sides,
  and the distance of the holes' centre from the set centre; `--save` writes marked pictures (search circles, holes) to
  `<folder>/bunk_results/`. Options `--check bunk|nocap|both`, `--folder`. Needs MobileSAM (as the camera server).
- Tested here without MobileSAM (not installed): with a simple pin finder in place of the mask generator, everything
  else real - the script reads the Siemens settings (715,341; 95-125), runs both checks and reports as designed; the
  verdicts from that stand-in say nothing about MobileSAM's.
- **Found:** on the Machine 2 bungs the DOST holes' diagonals are 98.5-100.0 and 110.8-111.4 px; the DOSTPLUS pair is
  108-112 px. If only two diagonal holes of a DOST bung are found, the check as DOSTPLUS would pass it; distance cannot
  tell them apart. Protection is the hole count (4 holes found when 2 are expected = NOT OK, existing logic).

## 61. Bunk check dry run with a window (both machines) (2026-11-04)
- `test/machine2_dryrun/DryRun_Bunk_Check_UI.py` (Machine 1 copy generated): tkinter window - model drop-down
  (DOST / DOSTPLUS, applied to every picture in the folder), Folder button, seconds-between-pictures drop-down
  (0.5-20), check drop-down (CheckBunk state 24 / CheckNoCapBunk state 13), "Save marked pictures"
  (`<folder>/bunk_results/`), Start / Stop. Shows each picture with the segmented disc, the hole search disc and centre,
  the holes found and an OK / NOT OK banner with the hole count, spacing, offset from the set centre, deciding gamma and
  time; a list of all results below. Models load in the background when the window opens.
- Tested here without tkinter or MobileSAM (neither installable in this environment): the window's load / run /
  marking code with stand-ins (pin finder for MobileSAM) - Siemens settings read, both checks run, results and marked
  pictures produced. The window itself was not opened here.

## 62. Bunk check: 3 holes in a line - middle one ignored (both machines) (2026-11-04)
- **Case (user):** the hole detection sometimes finds 3 holes - on the DOSTPLUS bung the centre punch mark, which lies
  between the two holes, is taken for a third hole - and the check then failed (2 expected).
- **Change (`BunkSegmenter`, `drop_middle_of_three_in_line`, applied first in `_apply_cascade_geometric_validation`):**
  when exactly 3 holes are found, the middle one (the one not in the farthest-apart pair) is ignored and the other two are
  treated as the 2 holes if (1) they are more or less in a straight line - 180 deg minus the angle at the middle hole
  <= `vision.bunk.three.hole.max.bend` (10.0 deg) - and (2) the middle hole is more or less equally far from both -
  |d1 - d2| / mean <= `vision.bunk.three.hole.max.distance.difference` (0.25). The usual 2-hole test (pair distance)
  then decides. Otherwise the 3 holes are kept (as before: NOT OK for DOSTPLUS, next gamma for DOST). Logged either way.
  Same values on both machines (all three properties files).
- **Measured** on the 7 seated Machine 2 DOSTPLUS bung pictures (pins + punch mark): bend 0.6-7.7 deg, distance
  difference 9-20 % (the punch is not exactly central; the bright pins and the punch measured here, not MobileSAM's
  masks).
- **Test** (the real cascade on those positions): 7/7 holes + punch -> 2 holes, OK as DOSTPLUS. Still NOT OK: 3 holes in an
  L (3 of a DOST square), middle hole 40 % off-centre, middle hole 20 px off the line (bend 40 deg), a pair 80 px apart
  after dropping the middle (outside 95-125), DOSTPLUS holes + punch checked as DOST. Unchanged: DOST square, 2 holes.
- Verifier: reference f399967 for BunkSegmenter (its six settings first taken from inline numbers written inline there,
  manifest lines remapped); ALL EQUIVALENT (both copies).

## 63. runNuitka.bat: build for Machine 1 or Machine 2 (2026-11-04)
- **Found:** the exe reads everything relative to its own folder (`get_project_root()` = folder of the compiled
  `BaseUtils`, i.e. `MainProgram.dist`), but the build put in only `wavs` and `internalimages`:
  - `ApplicationConfiguration.properties` was not copied - and it is what selects the machine (`plc.vendor`: IOServer,
    `<vendor>.vision.*`, camera keys);
  - `costhetaio/siemens/IOServerProperties.yaml` (Siemens tag -> DB address map, read next to `plc_backend.py`) was not
    included: the Siemens IOServer could not have started from the exe;
  - `models/` (MobileSAM / YOLO weights, not in git) was not copied.
- **Change:** `runNuitka.bat 1|2` (asks when no argument). Before compiling: the machine's properties file must exist and
  contain `plc.vendor=allenbradley` / `siemens`; the yaml must exist; prints torch / CUDA version. Nuitka: adds the yaml
  as a data file and `--include-package` for `costhetaio`, `pyS7`, `pycomm3`, `yaml` (both IOServers are imported inside
  a function). After compiling: copies `ApplicationConfiguration.Machine<N>.properties` to
  `MainProgram.dist\ApplicationConfiguration.properties`, `models\`, the sklearn data files; then checks the vendor line,
  the yaml, both MobileSAM weight files, `wavs`, `internalimages` and reports anything missing. CRLF line endings.
- **Known limit of the exe (unchanged):** live reload of `<vendor>.vision.*` re-reads the constants by parsing each
  vision module's source (`VisionConfig._declarations`, `inspect.getsource`); the exe has no .py sources, so there it
  finds nothing - a vision value changed in the properties file takes effect after a restart. Other settings read
  through `getValue` still reload live.
- Not run here (Windows / Nuitka / CUDA build).

## 64. Default machine is now Machine 2 (2026-11-04)
- `ApplicationConfiguration.properties` = `ApplicationConfiguration.Machine2.properties` (`plc.vendor=siemens`): running
  `python MainProgram.py` from the repo starts the Siemens IOServer and uses the `siemens.vision.*` settings (user's
  decision). Machine 1 from source: `COSTHETA_PLC_VENDOR=allenbradley`, or copy `ApplicationConfiguration.Machine1.properties`
  over it. The exe build is unaffected (`runNuitka.bat 1|2` copies the machine's own file).
- `DryRun_Logging_And_Live_Config.py` (both copies), check L9: compares `plc.vendor` with the value in the live file
  instead of a fixed "allenbradley". L9 passes. Verifier ALL EQUIVALENT.

## 65. runNuitka.bat takes the PLC vendor (2026-11-04)
- `runNuitka.bat allenbradley` (Machine 1, `ApplicationConfiguration.Machine1.properties`) or `runNuitka.bat siemens`
  (Machine 2, `ApplicationConfiguration.Machine2.properties`); case-insensitive. No default and no prompt: with no
  argument, or any other, it prints both commands in full (and the PowerShell form) and exits with code 1 - nothing is
  built or deleted. Replaces the `1` / `2` argument of section 63; everything else unchanged.

## 66. runNuitka.bat: Nuitka crash in torch._dynamo (2026-11-04)
- **Found (user's build log + nuitka-crash-report.xml):** Nuitka 2.7.10 / Python 3.12 / torch 2.11 crashed optimizing
  `torch\_dynamo\pgo.py` (NuitkaOptimizationError at an assignment expression inside `with dynamo_timed(...)`). The
  report lists all 59 `torch._dynamo` modules with reason "Root module": they were compiled only because
  `--include-package=torch` forces every torch submodule into the build. No project module imports `torch._dynamo`
  (torch.compile / TorchDynamo is not used).
- **Change:** removed `--include-package=torch` and `--include-package=torchvision` (the torch modules the program uses
  are followed from its imports), the deprecated `--enable-plugin=numpy` / `--enable-plugin=torch` (Nuitka warns: built
  in now) and `--follow-imports` (default in standalone mode). Prints `python -m nuitka --version` before compiling.
- Not run here (Windows build). If Nuitka still reaches `torch._dynamo` through torch's own imports, upgrade Nuitka
  (2.7.10 predates torch 2.11).

## 67. runNuitka.bat: networkx / pandas / seaborn left out of the build (2026-11-04)
- **Checked (user, in the venv):** importing torch, ultralytics and the vision checks loads sympy, scipy and matplotlib;
  networkx, pandas and seaborn are not loaded. In the project, outside test/ and endpoints/, nothing imports networkx,
  pandas or seaborn; pandas is only in `endpoints/AshokLeyland_MI_Light.py`, started by `OtherServices.py`, not by
  MainProgram (its web-service / MI processes are commented out).
- **Change:** `--nofollow-import-to=networkx`, `pandas`, `seaborn`. Kept: scipy (used directly by HubAndBearingSegmenter
  and ImageNormalisationWithMask - hub, top bearing, nut checks), sympy (loaded by torch), matplotlib (loaded by ultralytics).
- Risk: an optional code path of torch (torch.compile) or ultralytics (training plots / `Results.pandas()`) would raise
  ImportError in the exe; MainProgram uses neither.

## 68. runNuitka.bat: C compiler out of heap; more unused packages left out (2026-11-04)
- **Found (user's build with Nuitka 4.3.4 / Python 3.12.3 / MSVC, crash report):** the Python stage now completes (6305
  modules); the C stage failed with `fatal error C1060/C1002: compiler is out of heap space` on
  `sympy.series.formal` and `sympy.polys.polyquinticconst` - very large generated C files compiled with one job per
  core. sympy is needed (loaded by torch).
- **Report analysis - in the build but not used by MainProgram:**
  | package | modules | why it was in |
  |---|---|---|
  | sklearn | 278 | `--include-package=sklearn.externals` (no code imports sklearn) |
  | pandas, networkx, seaborn (+ xlsxwriter) | 626+ | torch / ultralytics optional imports (already excluded in section 67, this build predates it) |
  | torchaudio | 74 | optional import inside torch |
  | boto3, botocore, s3transfer | ~115 | `PostgresBackupUtility` S3 backup, imported in try/except; backups are local |
  | pymodbus + `IOServer_ModbusTCP` | 39 | `--include-package=costhetaio` made the unused Modbus IOServer a root module |
  | `costhetaio.siemens.s7_*` | 3 | the same; command-line PLC test tools |
- **Kept** (used): torch (1563), sympy + mpmath (torch), scipy, timm + huggingface_hub (MobileSAM's encoder), torchvision,
  ultralytics, matplotlib (ultralytics), PIL, cv2, Xlib (imported unconditionally by `frontend/CosThetaMonitorDimensions`),
  ffmpeg-python + future/past (camera/RTSPCam), FreeSimpleGUI / tkinter (start-up popups).
- **Change:** `--jobs=4` (fewer parallel compiler processes, each gets more memory); `--nofollow-import-to` sklearn,
  torchaudio, boto3, botocore, s3transfer, pymodbus, `costhetaio.allenbradley.IOServer_ModbusTCP`, the three
  `costhetaio.siemens.s7_*`; removed `--include-package=sklearn.externals` and the sklearn data-file copy;
  `--module-parameter=torch-disable-jit=yes` (Nuitka's default, made explicit to silence its warning).
- Not run here (Windows build).

## 69. runNuitka.bat: models\ no longer copied into the build (2026-11-05)
- User: the MobileSAM / YOLO `models\` folder is very large and already on the target machine. Removed its copy into
  `MainProgram.dist`, the start-up warning and the two weight-file checks. The end of the build now states that the exe
  needs `models\MobileSAM\weights\mobile_sam.pt` and `models\MobileSAM\MobileSAMv2\weight\ObjectAwareModel.pt` next to
  MainProgram.exe (`get_project_root()` = the exe's folder; `ModelManager` / `HexagonNutDetector` read `<root>\models\...`).

## 70. runNuitka.bat: low-memory mode and a compilation report (2026-11-05)
- **Found (user's build, crash report):** with `--jobs=4` the build is 5031 modules / 4374 compiled (was 6305 / 5252);
  `sympy.series.formal` now compiles, but `sympy.polys.polyquinticconst` still fails with `C1002: compiler is out of heap
  space in pass 2`. It cannot be left out: `sympy.polys.polyroots` imports it at load time (line 25), and torch imports
  sympy (torch itself and torch._dynamo / _export / _functorch / _inductor, all followed from torch's own imports).
- **Change:** `--jobs=4` -> `--low-memory` (one C compiler process at a time; objects compiled in earlier runs come from
  clcache, so a re-run mostly compiles what failed); `--report=compilation-report.xml` (user).
- The `--nofollow-import-to` warnings for `IOServer_ModbusTCP` and the `s7_*` tools are expected: they are the exclusions
  taking effect over `--include-package=costhetaio`.

## 71. runNuitka.bat: build with clang-cl (2026-11-05)
- User's choice after `C1002 out of heap` on `sympy.polys.polyquinticconst` with MSVC cl: `--clang` (clang-cl from the
  Visual Studio installation, component "C++ Clang Compiler for Windows"), `--jobs=4` (replaces `--low-memory`; cl's
  cached objects are not reused by clang, so the first clang build compiles all ~4400 files). Before compiling, the
  script finds clang-cl with vswhere and stops with the component to install if it is missing. `--report` kept.

## 72. runNuitka.bat: networkx back in the build (2026-11-05)
- **First exe built** (Nuitka 4.3.4, clang-cl, 5031 modules, `completion="yes"`). On start the camera server failed:
  `Failed to load MobileSAM: Module 'networkx' was actively excluded from Nuitka compilation`, then
  `NameError: name 'SamPredictor' is not defined` in `HexagonNutDetector` (class body uses the failed import) and
  `HubAndBearingSegmenter not available` for the same reason.
- **Cause (compilation report):** networkx is imported at module level by
  `torch._functorch._activation_checkpointing.graph_info_provider`, reached through `torch._functorch.partitioners`
  (imported by `torch._functorch.aot_autograd`, `torch._inductor.pattern_matcher`, `functorch.compile`, ...). In the exe
  this chain is reached while loading MobileSAM; the earlier check in the venv (`networkx` not in `sys.modules`) did not
  show it.
- **Change:** `--nofollow-import-to=networkx` removed (networkx included again, ~280 modules). pandas / seaborn / sklearn /
  torchaudio / boto3 / pymodbus stay excluded: the report shows their remaining imports are inside functions or
  try/except (Nuitka's "excluded" error is an ImportError, e.g. the boto3 try/except at import of PostgresBackupUtility
  passed).
- Rest of that run as expected on the build laptop: QR scanner COM3 absent, Siemens PLC 192.168.1.10 unreachable,
  Redis / DB / frontend / heartbeat up, IOServer (Siemens S7) started.

## 73. runNuitka.bat: exclusions re-checked against library source - pandas and sklearn no longer excluded (2026-11-05)
- User: keep an exclusion only if it is certainly not needed (a build takes 100 min). Checked every import of an
  excluded package recorded in the last compilation report (34) against the source of the pinned versions (torch 2.11.0,
  ultralytics 8.0.200, tqdm 4.67.1, and pandas, scikit-learn, networkx, scipy, matplotlib, sympy, timm, huggingface_hub,
  mobile_sam and the other ~70 packages in the build, searched for imports or look-ups by name), and Nuitka 4.3.4's loader.
- **Nuitka detail that matters:** for a module excluded with `--nofollow-import-to`, the exe raises ImportError not only on
  `import` but also on `importlib.util.find_spec()` (`MetaPathBasedLoader.c`, `_nuitka_loader_find_spec`), where Python
  returns None. A package that is simply not in the build still returns None.
- **No longer excluded:**
  - **pandas** - required: every YOLO `predict` (hub, bunk and nut checks) runs `AutoBackend.__init__` ->
    `_model_type()` -> `ultralytics.engine.exporter.export_formats()` -> `import pandas` (and uses the DataFrame).
    The earlier venv check imported the modules but ran no prediction, so it did not show this.
  - **sklearn** - `torch._dynamo.trace_rules` calls `find_spec("sklearn")` (also "pandas", "networkx") at module level,
    outside any try; excluded, that raises in the exe. Not excluded and imported by nothing, it is simply absent and
    find_spec returns None.
- **Still excluded (certain):** seaborn (only `plot_labels` / `ConfusionMatrix.plot`, training/validation, never called -
  no `.train/.val/.tune/.export` in MainProgram code), torchaudio (torch datapipes audio decoder, try/except ImportError),
  boto3 / botocore / s3transfer (PostgresBackupUtility try/except ImportError; pandas only for s3:// URLs, try/except),
  pymodbus + `IOServer_ModbusTCP` and the `s7_*` tools (nothing imports them). No library looks any of them up by name.

## 74. One IOServer for both PLCs: costhetaio.commonplc (PLCIOServer, PLCDataIO) (2026-11-05)
- **Why (user):** `costhetaio/siemens/IOServer.py` subclassed and imported `costhetaio/allenbradley/IOServer.py`: the
  machine logic lived in one vendor's package and the other vendor depended on it. Branch `machine2-io-fe-db-cam`
  (from `machine2-frontend-db`).
- **Design (agreed):** the machine is vendor-neutral; only reading and writing tags depends on the PLC make. One server
  class holds two connections (reads, writes) behind an abstract interface, chosen by a factory from `plc.vendor`. No
  vendor subclasses of the server: nothing vendor-specific is left in it (tag names and settings already come from the
  vendor's configuration keys).

  | Module | What |
  |---|---|
  | `costhetaio/commonplc/PLCIOServer.py` | `PLCIOServer` (was `allenbradley/IOServer.py`, `git mv`, 80 % similar): state machine, handshakes, threads (read, write, heartbeat, connection monitor, emergency, ...), resets with verified writes, restart detection, Redis messages, audit; `startIOServer()`; `UpdateTagsToDefaultProcessor`, `DelayedJobScheduler` |
  | `costhetaio/commonplc/PLCDataIO.py` | abstract `PLCDataIO` + `PLCIOError` |
  | `costhetaio/commonplc/PLCDataIOFactory.py` | `createPLCDataIO()` / `plcDataIOClass()` from `plc.vendor` (or `COSTHETA_PLC_VENDOR`); vendor modules imported only when chosen |
  | `costhetaio/allenbradley/AllenBradleyPLCDataIO.py` | pycomm3 `LogixDriver` |
  | `costhetaio/siemens/SiemensPLCDataIO.py` | pyS7 through `PyS7PLCBackend` (`plc_backend.py`, unchanged in function; the s7 tools still use it) |
  | `costhetaio/commonplc/MockPLCDataIO.py`, `MockPLC.py` | in-memory PLC for `mockInstance=True` (MockPLC moved from `allenbradley/`, `git mv`) |
  | removed | `costhetaio/siemens/IOServer.py` (the subclass); its methods moved: tag-map check -> `SiemensPLCDataIO.checkTagConfiguration`, `get_tag_address` -> `addressFor`, `find_attributes` -> `printTagMap`, `tagNamesUsedByIOServer` -> `PLCIOServer.tagNamesUsed()` |

- **PLCDataIO contract:** `open()` (raises on failure), `close()` (never raises), `.connected`, `read(tag)` -> value,
  `write(tag, value)`, `readMany(tags)`. A read or write that fails raises `PLCIOError` - also when the driver puts the
  error in its result instead of raising (pycomm3 does) - so a failure is never taken for a value. Each object has its
  own lock round the driver call (pycomm3 and pyS7 are not thread-safe). `checkTagConfiguration(tagNames)` before
  connecting (Siemens: every tag has an address in `IOServerProperties.yaml`, no clashes - start-up refuses otherwise,
  as before); `missingTags(names)` after connecting (Allen-Bradley: names not in the controller's tag list - logged once
  as an error, start-up continues; program-scoped `Program:` names not checked).
- **Behaviour changes (deliberate):**
  - Allen-Bradley: a write that pycomm3 reports as failed (`Tag.error`) now counts as failed (retried, audited as an
    error). Before, it was taken as written.
  - Allen-Bradley: reads and writes are serialised per connection by the PLCDataIO's lock. Before, only the server's
    `readingLock` / `writingLock` round some calls (removed: the lock is now in the connection).
  - Connection messages name the PLC: "Connected to Siemens PLC at 192.168.1.10 for Reads", "... Allen-Bradley PLC at
    ...", "MockPLC".
  - `read_multiple` returns `{tag: value}` (was pycomm3's list of Tags); not used by the IOServer.
- **Kept (user: well-defined methods stay even if unused):** `read_single`, `read_multiple`, `read_array`,
  `read_array_slice`, `find_pids`, `find_attributes`, `get_tag_address` on `PLCIOServer` (they delegate to the
  PLCDataIO where the PLC supports it); Allen-Bradley `readRaw`, `readArray`, `readArraySlice`, `findPIDs`,
  `printDataTypeAttributes`; Siemens `findPIDs` (empty), `printTagMap`, `addressFor`. `IOServer_ModbusTCP.py` stays in
  `allenbradley/` (standalone, unused, still left out of the build).
- **Callers:** MainProgram starts `costhetaio.commonplc.PLCIOServer.startIOServer` for both PLCs; `s7_tag_check.py` uses
  `PLCIOServer.tagNamesUsed()`; the IOServer test windows import `PLCIOServer` and `costhetaio.commonplc.MockPLC`.
  `runNuitka.bat` unchanged (`--include-package=costhetaio` includes the new modules).
- **Dry runs (both folders):** `_plc_sim.py` replaces only the driver under the vendor's PLCDataIO (pycomm3's
  `LogixDriver` in `AllenBradleyPLCDataIO`, pyS7's `S7Client` in `plc_backend`), so the real PLCDataIO code runs.
  New `COSTHETA_WRITE_TRACE=<file>`: every PC write appended to the file in order.
- **Verified - same PLC writes before and after:** fault scenarios and two full cycles run on the code before the change
  and after it, on both vendors, with the write trace; the traces compared write by write (the connection-status
  toggle left out: it is written on a timer).

  | Run | Allen-Bradley | Siemens |
  |---|---|---|
  | Fault scenarios | 113/113 (before and after) | 114/114 (before and after) |
  | Two full cycles: PC writes | identical (221 writes) | identical (221 writes) |
  | Fault scenarios: PC writes | each tag's writes identical, in order; the order between tags differs only where a delayed reset (its own 2 s timer) lands among the next cycle's writes, and one delayed reset at the very end lands before or after the run stops | same |
  | Logging and live configuration | 37/37 | 37/37 |
  | Mock PLC (`mockInstance=True`) start, QR handshake, settings | 7/7 | 7/7 |
  | `s7_tag_check.py` (simulated S7) | | PASS (46 tags, watch tag) |

  PLCDataIO contract checked on its own: not connected, a pycomm3 error in the result (read and write), unknown tag on
  MockPLC -> PLCIOError; `missingTags`. Not run here: the integrated all-servers run (the frontend needs tkinter, not
  available in this container; the frontend is not touched by this change).
- **Before production:** `s7_tag_check.py` on the plant PC and one real cycle with an exe built from this branch.

## 75. Siemens command-line PLC tools moved to costhetaio/siemens/test (2026-11-05)
- `s7_read_all_tags.py`, `s7_smoke_test.py`, `s7_tag_check.py` moved (`git mv`) into the new package
  `costhetaio/siemens/test/`. They are test / commissioning tools only: nothing in the application imports them (they
  import `plc_backend`, not the other way round; `plc_backend.py` holds copies of the pyS7 calls first proven in
  `s7_smoke_test.py`).
- Run from the project root: `python costhetaio/siemens/test/s7_tag_check.py`, `python
  costhetaio/siemens/test/s7_read_all_tags.py [--ip ...] [--repeat N --interval s]`, `python
  costhetaio/siemens/test/s7_smoke_test.py`. Project-root path in the first two adjusted for the extra folder level.
- `runNuitka.bat`: the three exclusions replaced by `--nofollow-import-to=costhetaio.siemens.test`.
- Verified from another working directory: both tools find the project; `s7_tag_check` PASS against the simulated S7.

## 76. Dry runs for the commonplc change: DryRun_PLCDataIO; failing tags in the PLC simulator (2026-11-05)
- **Existing dry runs:** already pointed at `PLCIOServer` in section 74 (`_plc_sim`, fault scenarios, test windows);
  remaining wording updated (comments naming "the Siemens IOServer", the mock window's "IOServer.py", the logging dry
  run's description, `s7_smoke_test.py` now under `costhetaio/siemens/test/`).
- **`_plc_sim.ERRORS`** (both folders): tag names the simulated driver fails on, the way each driver does - pycomm3
  returns a result with `.error` set and does not raise; pyS7 raises. Empty unless a test fills it.
- **New `test/*_dryrun/DryRun_PLCDataIO.py`** (37 checks, either vendor via `COSTHETA_PLC_VENDOR`; Redis and PostgreSQL):
  - P: the `PLCDataIO` contract on fake drivers - Allen-Bradley: not connected, error in the result on read and on
    write, no value -> `PLCIOError`; `missingTags`; `findPIDs`; no two driver calls at once from 8 threads; close.
    Siemens: by name through the DB address, LREAL, unknown tag, `checkTagConfiguration`. Mock: unknown tag, shared
    MockPLC. Factory: vendor -> class, mock, unknown vendor -> ValueError.
  - S: `PLCIOServer` on the simulated PLC with refused tags - write_bool False and nothing written, verified write gives
    up, read_bool not expected, `_readBoolOrNone` None, read_float FALSE_FLOAT_VALUE, recovery once accepted, link down.
  - M: `PLCIOServer(mockInstance=True)` in its own process - MockPLCDataIO, CheckQRCode armed, QR handshake with the
    settings written, state moves on.
- **Mutation-tested:** pycomm3 write error ignored again (the behaviour before section 74) -> P4 and S4 fail; the
  PLCDataIO lock removed -> P9 fails.
- **Fixed the same day (found on the laptop, no PLC connected, `COSTHETA_PLC_VENDOR` not set):** `_plc_sim.setup()`
  defaulted to allenbradley and simulated only pycomm3, while `PLCIOServer` takes the vendor from `plc.vendor`
  (siemens in the repo's file) - so the server used the real `SiemensPLCDataIO` and tried the real PLC
  (192.168.1.10). Hidden before section 74 because the simulator returned the Allen-Bradley IOServer class itself.
  `setup()` now takes the vendor from `Configuration.getPlcVendor()` (variable, else `plc.vendor`) and pins it in the
  environment, so the simulated driver and the server's PLCDataIO are always the same make; affects every IOServer dry
  run started without the variable. `DryRun_PLCDataIO`: new S0 - the server must be connected to the simulated PLC
  (watch tag 1234), else the S checks are skipped and S0 fails (before, S4-S9 passed with nothing connected, since every
  read and write fails then); S4 / S7 also require the connection. 38 checks. Verified: with no variable (siemens from
  the file), with allenbradley, and with the old default put back (S0 fails, S skipped); two full cycles with no
  variable on the simulated Siemens PLC.

## 77. IOServer test windows after the commonplc change; two window dry runs (2026-11-06)
- **Which windows depend on the IOServer code:** only `Test_IOServer_With_Mock_PLC_UI.py` imports it (it starts
  `PLCIOServer(mockInstance=True)` in its own process). `Test_IOServer_With_Real_PLC_UI.py`,
  `Test_IOServer_With_Simulated_QR_And_Camera_UI.py` and `Test_Integrated_All_Servers_Running_UI.py` talk to a running
  IOServer over Redis only (QR / camera / fe2io queues, publishTags / receiveTags); those messages did not change.
- **Fixed in the Mock-PLC window (both folders):**
  - **Would not start (since section 74):** three uses of the old class name `IOServer` (`getattr(IOServer, ...)` at
    module level and in `_tag()`, a type hint) were missed by the rename, which caught only `IOServer.<attribute>`:
    NameError at import. Now `PLCIOServer`; checked with an AST scan of every test window and dry run for unbound names.
  - **Auto Cycle could report a good cycle as FAILED (older):** the state tracer kept its "last state seen" outside the
    lock; when Auto Cycle restarted the trace before the tracer had seen the current state, the tracer recorded it a
    second time ([2, 2, 3, ...]). The tracer and the restart now share it under the lock.
  - **Rejected QR:** after sending the rejection the window waits until the IOServer has taken it off qrcode2io.q, as a
    real second scan would.
- **Found, not changed (production):** `readDataInIOServerFromQRCodeServer` reads one message, then `xtrim(qrcode2io.q, 0)`:
  a second QR message already queued behind it can be deleted with it. In production the QR server sends nothing for a
  bad scan (section 9), so this needs two messages within one read interval; same xtrim pattern as section 29.
- **New dry runs (both folders):** `DryRun_IOServer_Mock_PLC_Window.py` (14 checks) and `DryRun_IOServer_Real_PLC_Window.py`
  (7 checks; the window against `PLCIOServer` on the simulated PLC) - see the README. They need a display (Linux:
  `xvfb-run`).
- **Verified (Python 3.12 + Tk under Xvfb here):** Mock window 14/14 in 12 of 12 runs (3 x both folders x both vendors;
  before the tracer / QR fixes 3 of 4 runs failed one way or another); Real-PLC window 7/7 on both folders and vendors.

## 78. PLC handshake rules (a)-(d); knuckle NOT OK stall and hub retake loop fixed; costhetaio review; class diagram (2026-11-06)
- **Field issues (Machine 2), reproduced in the simulator before any change** (two PLC behaviours, camera NOT OK at
  the knuckle):
  1. **NOT OK -> neither forward nor back (Emergency needed).** After a NOT OK the IOServer ignored the trigger and,
     `plc.vision.result.reset.delay.sec` (0.6 s) later, wrote Check = False. A 2HB press within that time raised Check
     again, was ignored, and was then cleared by the PC: the PLC waited for a result, the IOServer for a trigger.
     Reproduced: 2HB pressed 0.3 s after the NOT OK -> stall.
  2. **Wrong hub -> pictures every 1-2 s without a 2HB press.** With a PLC that keeps Check True (2HB held, or a
     program holding it), the PC's Check = False was overwritten at once and every reset meant another picture.
     Reproduced: 7 pictures in 8 s from one press (one every 1.2 s).
  3. **State on a failure:** correct (a NOT OK goes back to the take-picture state); the stall was the trigger, not
     the state.
- **Rules, one setting `plc.handshake.reset.delay.sec` = 0.4 (all three properties files; replaces
  `plc.vision.result.reset.delay.sec`; read at each use).** Every delayed reset is a job of the one scheduler thread
  ("PLC Handshake Reset Scheduler", section 36), queued the moment the result is written / the value read.
  - **(a) Check-Done-OK, NOT OK** (9 vision checks; QR code when its settings could not be written): OK = False,
    Check = False, then Done = True (the trigger is cleared before Done, so any trigger the PLC raises after seeing
    Done is a new 2HB press); Done = False after the delay. QR code: CheckQRCode = True again after that (the PC arms
    the QR read, as at the end of a cycle) so the operator can re-scan.
  - **(b) Check-Done-OK, OK** (9 vision checks and the QR code): OK = True, Done = True; after the delay Check, Done,
    OK = False, in that order (Done before OK: the PLC never sees Done = True with OK = False).
  - **(c) Set-Value** (Station 2 torque, both readings; Station 3 torque): after the value was read, Set = False,
    Value = 0.0 (Station 2 also RotationDuringTighteningDone = False) after the delay (was 1.5 / 2.0 s).
  - **(d) Done** (component press, cap press): after it was read, Done = False after the delay (was never reset).
  - A new result for an operation whose previous reset is still pending: that reset is done first (the PLC sees Done
    go False before the new Done = True).
  - End of a completed cycle: pending resets run at their time (resetForNextCycle(keepPendingResets=True) leaves
    their tags to them); an abort / start / PLC restart cancels them and resets every tag at once, as before.
- **One picture per 2HB press:** after a NOT OK the take-picture state acts on the trigger only after the read loop
  has itself read it False. A read-back right after the PC's write is no proof (the PLC can set it again within one
  scan; tried first and failed the held-trigger case). If the trigger is still True 3 s after the NOT OK the IOServer
  logs once that the PLC must raise it once per 2HB press, not hold it.
- **Camera answer for another picture:** the write loop now checks that the camera's answer is for this check's
  picture; an answer for another picture (late, from an earlier request) is ignored and audited. Before, it was taken
  as this check's result (a stale OK would have moved the cycle on).
- **Camera down / no pictures -> stall (camera/CameraProcessorServer.py):** TakePictures answered with INVALID_STATE
  when the camera was not connected or the pictures could not be taken; the IOServer cannot tell that from "no answer
  yet" and waited in the WRITE state for ever. It now keeps the requested picture state, so the check is NOT OK (and
  the PLC gets its NOT OK handshake). Checked through the real TakePictures and ConverterScalerAndProcessor (camera
  stand-in): before, INVALID_STATE; now the knuckle state, NOT OK. (CameraProcessorServer is not one of the vision
  files the equivalence verifier compares.)
- **Review of costhetaio (all modules), other findings:**
  - `allenbradley/IOServer_ModbusTCP.py` refers to 11 machine states that no longer exist (an older sequence): it would
    fail when built. Unused, not in the build; kept (not fixed).
  - `PLCIOServer.launchThreadForUpdatingCombinedHeartbeat`: `currentStatus = True` ("WHILE TESTING"), so a lost server
    connection never aborts the cycle. Unchanged (section 34); to be switched back for production.
  - The QR reader can drop a second queued QR message (section 77). Unchanged.
  - `UpdateTagsToDefaultProcessor` / `addTagUpdateRequest`: no longer used (the torque resets are scheduler jobs);
    kept. The `TIME_WAIT_BEFORE_RESETTING_TORQUE*` constants removed.
  - `IOServerProperties.yaml` header named the deleted `siemens/IOServer.py`; corrected.
  - Everything else (PLCDataIO and its three implementations, the factory, plc_backend, MockPLC, the s7 tools): no
    defects found; the Siemens PLC IP has one source (`siemens.plc.ip`; the yaml has no `plc.address`).
- **Class diagram:** `docs/costhetaio_classes.mmd` (Mermaid source), `.svg`, `.png`: commonplc, allenbradley,
  siemens - inheritance, composition, the factory's choice by vendor, the drivers.
- **Dry runs:** fault scenarios G rewritten for the rules and the field issues (G1-G7); the harness's NOT OK retry
  follows rule (a); `COSTHETA_DRYRUN_SCENARIOS=G` (or F,G ...) runs only those groups.
- **Verified:**

  | Run | Allen-Bradley | Siemens |
  |---|---|---|
  | Fault scenarios (A-I, new G) | 118/118 | 119/119 |
  | Field reproduction, after the fix | pulse PLC: 2HB 0.3 s after the NOT OK acted on, cycle moves on; held trigger: 1 picture in 8 s (was 7) | |
  | Mutations of scenario G (Siemens): Check not cleared with the NOT OK / no edge gate / no camera-answer check / OK reset without Check | | caught: G2-G3 / G2 / G6 / G1 |
  | Two full cycles | exact | exact |
  | DryRun_PLCDataIO (both folders) | 38/38 | 38/38 |
  | Logging and live configuration | 37/37 | 37/37 |
  | Mock-PLC window / Real-PLC window (both folders) | 14/14, 7/7 | 14/14, 7/7 |
  | Integrated all-servers cycle (Python 3.12 + Tk + PySide6 under Xvfb) | DB row DOSTPLUS/RHS/2.5T, 19.08/19.03/2.41, all OK, 30/15/19/1/1, rotation True/False | identical |

- **PLC side, to confirm on the machine:** the PLC must raise each Check once per 2HB press and let the PC's False
  stand. A PLC that keeps Check True after a NOT OK now gets one picture (not a picture every 1-2 s) and the log line
  above; the PLC program then has to be changed.

## 79. NOT OK of a vision check: OK, Done, then Check; state back only after that (2026-11-06)
- **Order (user):** a NOT OK is written OK = False, Done = True, then Check = False, and only after that is the machine
  state set back to the take-picture (READ_...) state, so that when the read loop next reads the trigger it is already
  False. Section 78 wrote OK = False, Check = False, then Done = True. The QR code's NOT OK (settings not written) has
  the same order. Done = False after `plc.handshake.reset.delay.sec` and the rest of section 78 are unchanged.
- `perform_write_state_action` now writes the trigger last, retried until written (as OK and Done are), and returns only
  after it; the write loop sets the state back after it returns. The "read False first" safeguard stays, for a PLC that
  sets the trigger True again at once.
- **Fault scenario G2:** expects OK=False, Done=True, Check=False in that order, and a new check that the state goes back
  only after Check=False was written (spy on the state change). Mutation: Check written 0.3 s after the state went back
  -> G2 order and state checks fail, and the standing trigger retakes the picture at once (field issue 2 again).
- `plc.handshake.reset.delay.sec` comment in the three properties files: the order.
- **Verified:** fault scenarios Siemens 120/120, Allen-Bradley 119/119; field reproduction: pulse PLC retry acted on, cycle
  moves on; hold-to-trigger PLC one picture per press; held trigger 1 picture in 8 s; Mock-PLC window 14/14 on both vendors.

## 80. Result handshake as one sequence before the state changes; "trigger read False first" rule removed (2026-11-06)
- **Sequence (user), vision checks and the QR code, each write retried until it succeeds, all before the state changes:**

  | Step | NOT OK | OK |
  |---|---|---|
  | 1 | OK = False | OK = True |
  | 2 | pause `plc.sleeptime.between.ok.and.done` = 0.05 s (was 0.2) | same |
  | 3 | Check = False | Check = False |
  | 4 | Done = True | Done = True |
  | 5 | pause `plc.sleeptime.between.done.and.re_read` = 0.1 s (new) | pause `plc.sleeptime.between.done.and.reset_done` = 0.4 s (new) |
  | 6 | Done = False | Done = False |
  | 7 | Check = False again | OK = False |
  | 8 | state back to the take-picture state (Check and Done both False there) | next state |

  QR code NOT OK (settings not written) only: after step 7 CheckQRCode = True again (the PC arms the QR read), then
  back to READ_QR_CODE. The sequence stops if the cycle is aborted meanwhile.
- `perform_write_state_action` writes the whole sequence; the write loop changes the state after it returns. The
  delayed Check-Done-OK resets of sections 78/79 (`scheduleVisionResultReset`) are no longer called (kept);
  `plc.handshake.reset.delay.sec` (0.4 s) now only times the torque Set/Value and press Done resets, unchanged.
- **"Trigger read False first" rule removed (user):** after a NOT OK the take-picture state acts on the trigger as soon as
  it reads True. Its methods (`triggerIgnoredUntilReset`, `_setAwaitingTriggerReset`, `_noteTriggerStillHeld`) are kept,
  no longer called. **PLC requirement this leaves:** the PLC must raise each Check once per 2HB press and let the PC's
  False stand. A PLC that sets Check True again by itself after the PC cleared it (2HB still held when the NOT OK
  arrives, or a program that holds Check while the step is active) gets the picture retaken at once; with a held Check,
  every 0.5-1 s (field issue 2).
- **PLC restart detection: fallback reference (user's choice).** With these sequences the PC clears every trigger before
  the state moves on, so between steps no PLC-raised tag is True and the rule of section 74+ (the last PLC_PC tag the
  PLC raised, still True after the reconnect = no restart) had nothing to read: every connection loss between steps
  was taken for a PLC restart and aborted the cycle (found by fault scenario I1). Now, when no PLC-raised tag is known
  to be True during a cycle, `PC_PLC_LH_RH_Selection` decides: the PC records the side it wrote in state 2 (1 / 2); after
  the reconnect, the same value -> the PLC kept its memory, the cycle carries on; another value (0 after a restart) ->
  restart, abort and reset as before. **PLC requirement:** DB101 (Siemens) must not be retentive, so that a restart
  sets the side back to 0; on the Allen-Bradley, Logix controller tags normally keep their values through a power cycle,
  so there a restart between steps is not detected by this fallback.
- **Fault scenario G rewritten:** G1 OK and G2 NOT OK check the order, how long Done stays True (0.4 / 0.1 s), that the
  state changes only after the last write, and (G2) no picture without a 2HB press, one per press; G3-G7 unchanged.
  The held-trigger check was removed with the rule. Scenario I: I1-I3 expect the fallback reference (I1 side still 1 ->
  carry on; I3 side reads 0 -> restart detected).
- **Verified:**

  | Run | Allen-Bradley | Siemens |
  |---|---|---|
  | Fault scenarios A-I | 118/118 | 119/119 |
  | Mutations of G (Siemens): Check after Done / Done never False / NOT OK holding Done 0.4 s | | caught (G1-G2 / abort / G2 timing) |
  | Scenario I without the fallback (before it was added) | I1 failed: connection loss between steps aborted the cycle | same |
  | Two full cycles; DryRun_PLCDataIO; logging | exact; 38/38; 37/37 | exact; 38/38; 37/37 |
  | Mock-PLC / Real-PLC windows (both folders) | 14/14, 7/7 | 14/14, 7/7 |
  | Integrated all-servers cycle | DB row unchanged (DOSTPLUS/RHS/2.5T, 19.08/19.03/2.41, all OK, 30/15/19/1/1, True/False) | identical |
  | Field reproduction (rule removed) | PLC raising Check once per press: retry acted on, cycle moves on. 2HB still held when the NOT OK arrives: one extra picture during the hold. PLC holding Check: 13 pictures in 8 s | |

## 81. Servers down, camera answer timeout, vision check deadline, settings fallback, process restart; knuckle retake fixed (2026-11-06)
- **How the IOServer learns the other servers' status.** Before: one combined ALIVE / DEAD on `hb2io.q`, and that read
  was commented out (`currentStatus = True`). Now the heartbeat server also sends each server's status (camera, QR
  code, IO, DB, frontend) on `hb2io.q` (`sendCombinedHeartbeatFromHeartbeatServerToIOServer(..., serverStatuses=)`),
  read by `readServerStatusesInIOServerFromHeartbeatServer` (newest message, older ones dropped). The heartbeat
  server itself is taken as down after `ioserver.heartbeat.server.silence.sec` (8 s) without a message - once it has
  sent one: an IOServer run on its own (commissioning, the IOServer test windows) has no heartbeat server and works
  as before. `PC_PLC_ConnectionStatus` keeps toggling whatever the other servers' status.
- **Rules per server (user), `PLCIOServer._applyServerStatuses`:**

  | Server down | IOServer |
  |---|---|
  | Camera | the picture the write loop waits for is reported NOT OK at once (normal NOT OK handshake) |
  | QR code | nothing (the CheckQRCode tag is left alone) |
  | Database | the cycle in progress is finished ("Database down: continue till end of cycle", flashing, top right); no new cycle until it is back ("next cycle on hold"): the QR code server is not asked to read; while waiting for a scan, the read is stopped (ABORT to the QR code server) and the state goes back to READ_QR_CODE |
  | Frontend | the cycle is aborted (QR code server and camera server told, not the frontend); no new cycle until it is back |
  | Heartbeat server | the cycle is aborted (frontend and camera server told); no new cycle until it is back |

- **PLC told of a PC-side abort (optional).** There was no such tag: an abort only reset the handshake tags and armed
  CheckQRCode. New `pc.plc.cycle.aborted.tagname` (`PC_PLC_CycleAborted`, bool): True after a PC-side abort
  (frontend abort, frontend / heartbeat server down, PLC restart), False when the next QR code is accepted. Empty in
  all three properties files until the PLC has the tag (Siemens: also needs its address in `IOServerProperties.yaml`).
- **Camera answer timeout (option A, user) and request ids.** Every picture request carries an id (`<cycle>-<n>`),
  echoed by the camera server in its answers to the IOServer and the frontend. No answer within
  `plc.camera.answer.timeout.sec` (20 s; longer than the 12 s check timeout plus the pictures) -> NOT OK to the PLC,
  the request taken off the camera queue, a NOT OK sent to the frontend with the replacement id `<id>:timeout`, notice
  "Picture NOT OK: no answer from the camera server in 20 s". An answer to an earlier request is ignored (IOServer:
  id mismatch; frontend: `camera:activeRequest` - `readDataInFEServerFromCameraServer` drops results of other
  requests). Answers without an id (test tools) are taken as before, by picture state. The camera readers now delete
  only the message read (they relied on an approximate `xtrim` that did nothing, section 29).
- **Vision check deadline (user: 12 s, killable).** `<vendor>.vision.check.timeout.sec` = 12. With
  `<vendor>.vision.check.in.worker.process` = True (default) the checks run in one worker process
  (`camera/VisionCheckWorker.py`, models loaded there, a warm-up check on a blank picture before it reports ready):
  the camera server keeps the cycle's state (QR code, earlier pictures, bearing ring) and sends it with each job; a
  check over the deadline is NOT OK ("check timed out after 12 s", written on the picture and shown as a notice), the
  worker process is stopped and a new one started; until it is ready a check is NOT OK at once ("vision check worker
  starting"). A stopped check changes nothing of the cycle's state, so its retake has the earlier pictures and the
  ring. Flags in the checks (cooperative cancellation) were not added: they cannot stop a call that hangs inside
  torch / MobileSAM, and would have meant changing the four verified vision files. `False`: the checks run in a thread
  of the camera server with the same deadline; a check over it is NOT OK, but its thread cannot be stopped.
  The check code itself is unchanged; `ConverterScalerAndProcessor.processItem`'s dispatch moved, unchanged, into
  `assessPictures()`. Vote mode (knuckle, split pin, cap): the `with ThreadPoolExecutor` (whose exit waited for a stuck
  thread whatever the timeouts) is now shut down without waiting; a picture not assessed in time counts as NOT OK.
- **Knuckle retake after a NOT OK was always NOT OK (found here; fixed).** Every knuckle check dropped the QR code and
  fetched it again from the QR code server's queue, but the code had been read at the first knuckle check: a retake
  waited 5 s per picture (6 pictures in series, 33.9 s measured on the old code) and was NOT OK for "QR code is None",
  so after a knuckle NOT OK the cycle could only be aborted. Now `ImageProcessor.prepareForKnuckleCheck()`: a new QR
  code on the queue = a new cycle (state dropped, the newest code used); none = a retake, the cycle's QR code kept;
  none at all = wait up to 5 s, as before. The end-of-cycle message from the IOServer now also drops the QR code and
  the pictures, not only the bearing ring.
- **Settings write fails 3 times (user): carry on with the earlier settings.** The torque setpoints, RPM and rotation
  counts: one that cannot be written (3 attempts), or no machine settings from the database -> logged, audited,
  notice "Machine settings not updated: previous settings used", QR OK, the cycle goes ahead; the record gets the
  values last written (None if not known). The side (`PC_PLC_LH_RH_Selection`) stays required: it changes with every
  part, so if it cannot be written the QR code is NOT OK (as before).
- **NOT OK Done pulse 0.1 -> 0.3 s (user):** `plc.sleeptime.between.done.and.re_read=0.3` (all three files; getter
  default 0.3).
- **Process restart (`utils/ProcessSupervisor.py`, started by MainProgram).** A server process that dies is restarted
  (same function and arguments) unless a stop command is on the stop queue; at most `supervisor.max.restarts.per.hour`
  (10) per server, then it is left down (logged); the pid list used at shutdown is kept up to date;
  `supervisor.restart.dead.processes=False` turns it off. A restarted IOServer tells the frontend and the camera
  server that the cycle was aborted (only on a restart: Redis key `ioserver:started`, cleared by MainProgram at start;
  sending it at the first start too left a stale abort that the integrated dry run's frontend acted on).
- **Write loop:** restarted (logged with the traceback) if an unexpected exception ever ends it; before, the thread
  would have stopped while the IOServer kept reporting ALIVE.
- **Frontend:** flashing notice label at the top right of the mode bar (`FrontendNoticeWorker`, Redis keys
  `feNotice:<name>` with an expiry, `setFrontendNotice` / `readFrontendNotices`): database, camera, check timed out,
  settings, heartbeat server, PC abort.
- **Dry runs (both folders).** `_plc_sim` starts a simulated heartbeat server (`HEARTBEAT[...]` = False plays a server
  down, `HEARTBEAT["silent"]`); the fault-scenario camera echoes request ids and can stay silent or answer late.
  Fault scenarios: E3 rewritten (no settings -> only the side written, cycle goes ahead, record = last written), new
  E4 (a setting refused -> cycle goes ahead), new J (J1 no answer -> NOT OK after the timeout, frontend NOT OK with the
  replacement id, a late result dropped by the frontend reader; J2 late NOT OK of the first request ignored, the
  retry's OK taken; J3 camera down -> NOT OK at once; J4 DB down mid-cycle -> cycle finished, next held, notices;
  J5 DB down while waiting for the scan; J6 frontend down -> abort, no emergency to it, `PC_PLC_CycleAborted`
  True / False (Allen-Bradley simulator); J7 heartbeat server silent -> abort, frontend and camera told). Harness flake
  fixed: the simulated PLC set CheckQRCode False 0.3 s after Done, overwriting the PC's re-arm after a QR NOT OK (E2's
  re-scan then never came; failed once in the baseline run here). FE dry run: F9 notices (shown, flashing, two
  together, hidden, expiry), F10 a late result dropped, the IOServer's NOT OK shown, the retake's result shown. New
  `DryRun_Vision_Check_Timeout.py` (W worker, T in-thread mode, K knuckle retake in the real pipeline with the real
  CheckKnuckle, R timeout in the pipeline) and `DryRun_Process_Supervisor.py`.
- **Mutations caught:** camera-down rule off -> J3 fails; request-id check off -> J2 fails; the old knuckle code ->
  retake NOT OK after 33.9 s (K2's case).
- **Verified (Linux; Python 3.13 for the IOServer / camera, 3.12 + Tk + PySide6 under Xvfb for the frontend and the
  windows; torch 2.15 CPU and MobileSAM installed, no model weights):**

  | Run | Allen-Bradley | Siemens |
  |---|---|---|
  | Fault scenarios A-J | 151/151 | 150/150 |
  | Two full cycles | exact | exact |
  | DryRun_PLCDataIO; logging; heartbeat / start-up | 38/38; 37/37; 16/16 | 38/38; 37/37; 16/16 |
  | Mock-PLC window; Real-PLC window | 14/14; 7/7 | 14/14; 7/7 |
  | Integrated all-servers cycle | DB row unchanged (DOSTPLUS/RHS/2.5T, 19.08/19.03/2.41, all OK, 30/15/19/1/1, True/False) | identical |
  | FE dry run; DB dry run; vision verifier | | 75/75; 49/49; ALL EQUIVALENT |
  | Vision check timeout; process supervisor | | 22/22; 6/6 |

- **Not run here:** the Nuitka exe (the worker is a spawned child of the camera server - also a spawned process -
  as MainProgram's servers are; if it misbehaves in the exe, `<vendor>.vision.check.in.worker.process=False`), the
  model-based checks on real pictures with the weights, a real camera, PLC or Windows.
- **For the PLC programmer:** an address for `PC_PLC_CycleAborted` if the PLC is to know of PC-side aborts; whether a
  two-hand-button press made while Check is already True is discarded (not queued).

## 82. Review follow-up: emergency documented, image folder per day, QR scan no longer re-creates the database, REINDEX per insert removed, settings read back, code clean-up, old files archived (2026-11-06)
- **Emergency button held down (documented, user):** the button is pushed and released; held longer, the PLC keeps
  `PLC_PC_EmergencyAbort` True and the IOServer aborts once per read (about every 1.7 s) for as long as it is held:
  several aborts one after the other. Harmless - each resets the same tags and arms the QR read; the frontend acts on
  the first only (it is at the QR code for the later ones) and sends one abort record. Docstring of
  `launchEmergencyMonitoringThread`.
- **PLC restart detection (user):** both PLCs set every value back to its default on a restart / reboot, so the
  fallback reference of section 80 (the side written in state 2 reads 0 after a restart) works on the Allen-Bradley
  too; the note in section 80 that Logix tags keep their values does not apply to this machine's programme.
- **Threads that must not stop:** the write loop already restarts after an exception (section 81); the emergency
  thread and the PLC connection check now also catch and log any exception per pass (before, an exception in an
  abort or a reconnect would have ended them).
- **Image folders follow the day (`DBServer.watchForNewDay`):** the folders were worked out once at DB server start,
  so on a PC left on, images after midnight went into the start day's folder. A thread wakes every 60 s, keeps the
  date of its last wake-up, and when the date has changed makes the new day's folders; images saved from then on go
  there.
- **QR code server no longer re-creates the database on every scan:** `createDisplayString` (the duplicate check of
  each scan) called `setDatabaseName(mode)`, which runs createDatabase / createSchema / createAllTables /
  ensureIndexesExist / seed rows every time. Now `createDB=False` there and in the QR server's start-up (MainProgram
  creates the databases before it starts the servers; the DB server still makes sure once at its start).
- **REINDEX after every insert removed; duplicate indexes dropped.** PostgreSQL updates every index of a table inside
  the insert's own transaction: the new QR code is found through `idx_qr_code` as soon as the insert is committed, so
  the duplicate check needs no REINDEX. REINDEX only rebuilds an index (corruption, heavy bloat); while it runs it
  blocks inserts and the reads that would use the index, and its cost grows with the table. Found on the way:
  `ensureIndexesExist` created a second index on each of qr_code / model_name / created_on (long names, cut to 63
  characters) next to `idx_*` from the DDL, so every insert maintained 7 indexes instead of 4. The duplicates are
  dropped at start-up (`DROP INDEX IF EXISTS`); `reindexIndices()` is kept for maintenance by hand.
- **State-2 settings read back:** every settings write is read back from the PLC (INT exactly, REAL / LREAL within
  1e-3); a value the PLC does not hold counts as not written (3 attempts), then the rule of section 81 (the cycle goes
  ahead with the PLC's settings; the side stays required).
- **Code clean-up (production code; test/ and archive/ not touched, except as noted):**
  - all 249 bare `except:` -> `except Exception:` (KeyboardInterrupt / SystemExit are no longer swallowed; none of the
    `try` bodies exits or re-raises on purpose - checked). The 5 that only passed now log through `noteSuppressed()`,
    except in the vision files (equivalence verifier) and logutils (logging about logging).
    `Verify_Vision_Config_Equivalence.py` treats `except:` and `except Exception:` as the same on both sides (the
    only normalisation added; ALL EQUIVALENT).
  - 240 unused imports removed in 34 files, and 182 unused `except ... as e` names. Left in place on purpose: any
    import in a module that another file star-imports (`from X import *` re-exports it), any name another file
    imports from that module or reaches as `module.name` (tests included), imports inside try / if / functions /
    classes (availability checks), plain `import x` of anything but well-known side-effect-free modules, a
    `from <project module> import ...` whose every name is unused (importing it may be what runs that module's
    set-up), multi-line imports with comments, the vision files, `__init__.py`; and the other 43 unused variables
    (their assignments may call something). Checked: no undefined names anywhere (ruff F821 over the whole repo),
    every changed module imports as before (54 of 56; the 2 that do not, did not before either: `TestCheckHub.py`,
    and `frontend/widgets/CosThetaGeltecResultsContainer.py` which imports a module that is not in the repo).
  - Pre-existing bug fixed in three test windows (`Test_FEServer_Mock_With_Image_Files_UI`,
    `Test_FEServer_With_Live_Camera_UI`, `Test_IOServer_With_Real_PLC_UI`, both folders): a lambda used `e` after
    its `except` block had ended (Python deletes it), so showing the error raised NameError.
- **Old files archived:** 50 files moved with `git mv` to `archive/<original path>` (see `archive/README.md`): the
  `_Old` / `_v0.x` versions (`VerifyIntegrity_v0.9_...`, `runNuitka_v0.6.bat`, `frontend/SimplePopups_Old.py`, 13 in
  test/) and general experiments not about this machine (Python / Qt trials, other projects' hardware: KEMS, capsule
  counting, Adam-6052, Micro850, webcam). Nothing imports or runs them (checked). Machine-related tools stay in test/.
- **Not done (user):** torque limits (the PLC tightens to the torque); one dry-run runner; vision reload in the exe;
  the QR reader keeping only the newest message (intentional) and `taskkill /im cmd.exe` (the PC is for this machine
  only) stay as they are; bung checks to be tested later.
- **Heartbeat abort debouncing, re-evaluated:** not needed as a separate step. A server is reported DEAD only after
  `min.continuous.disconnections.to.send.alarm` (3) missed checks of 2 s (about 6 s), and the heartbeat server itself
  after 8 s of silence - that is the debounce; the frontend's heartbeat is already one of the five statuses
  (frontend down -> abort).
- **Dry runs:** fault scenarios E5 (a setting the PLC does not hold after the write); new
  `DryRun_DB_Indexes_And_Day_Folder.py` (X1 one index per column, duplicates dropped; X2 no REINDEX, the new QR code
  found at once, idx_qr_code usable; X3 a scan's duplicate check does not re-create the database - mutation: the old
  call -> X3 fails; D1 the folders move to the new day). Both folders.

## 83. Redis health check; PLC interface specification, HLD and LLD in docs/ (2026-11-06)

- **Redis health check (architecture review item 7):** `checkRedisHealth()` (utils/RedisUtils.py) writes a token to
  `redis.healthcheck.q` (a stream used by nothing else, maxlen 10), reads that entry back, compares and deletes it.
  - Heartbeat server: a thread runs it every `redis.health.check.interval.sec` (new, 60 s; 10 s while failing). On
    failure: console message, file log, siren, reconnect. The result goes to the frontend (`redisStatus`: notice
    "Redis is not storing data: restart the PC") and to the IOServer (`redis` in the server statuses: the next cycle
    is held with "Redis not storing data"; a QR read in progress is aborted back to READ_QR_CODE, as for the other
    holds).
  - MainProgram: runs it once after clearing the queues; the application does not start if Redis does not store.
  - Limitation (honest): when Redis stops storing, the messages that carry "redis down" to the frontend and the
    IOServer cannot be delivered either. What the check really adds is the refusal to start and the heartbeat
    server's siren and console message; the hold and the notice work only if Redis is partly working (for example
    out of memory with `noeviction`, where small writes may still pass).
- **docs/PLC_Interface_Spec.md** (review item 1): the full tag table with Siemens addresses and writers; protocol
  v1 as built (handshake order, timings and the properties that set them, value / event states, the PC writes to
  PLC_PC tags, abort, restarts, server-down rules); protocol v2 proposal (every tag has one writer, four-phase
  request / acknowledge, acknowledge tags for torque and press events, optional request number, timeouts on both
  sides, proposed addresses for the 7 new tags to be confirmed by the PLC programmer, migration behind
  `plc.handshake.protocol`); sign-off table. v2 is not implemented.
- **docs/HLD.md** and **docs/LLD.md**: processes, queues, Redis keys, the cycle, failure handling, configuration,
  data model, IOServer threads and state table, message schemas, tests.
- Docstring of `perform_write_state_action`: the NOT OK hold is 0.3 s (the property value), not 0.1 s.
- **Dry runs:** `DryRun_Heartbeat_And_Startup.py` R1-R5 (healthy check; check through the heartbeat server; Redis
  out of memory -> failure with the reason; recovery; MainProgram runs the check before starting any process);
  fault scenario J8 (redis down -> next cycle held at READ_QR_CODE; redis back -> cycle starts). `_plc_sim.py`
  heartbeat includes redis. Both folders.

## 84. Review fixes: stale camera frames, abort during a handshake write, shutdown backup, toggle thread, timeouts (2026-11-07)

From the code review of 2026-11-07 (architecture items not included).

- **No check on an old camera frame (a1).** `RTSPCam.getLatestFrame()` returned the last frame however old: while the
  stream stalled (ffmpeg alive, no frames) the last frame was kept through the 5 s read timeouts until the 15 s
  watchdog, so a picture taken then showed the part where it was before (an earlier station), and the three pictures
  of a 2-of-3 vote could be the same old frame - a possible false OK.
  - `RTSPCam` keeps `latestCamFrameAndTime` (frame, arrival time, set together); new `getFrameAfter(afterTime,
    maxWaitSecs)` returns only a frame that arrived after `afterTime`. `getLatestFrame()` is unchanged (other callers).
  - `TakePictures`: each picture is a frame that arrived after the request and after the previous picture, waiting at
    most `camera.frame.max.wait.sec` (new, 1.0 s; about 20 frames a second normally). None in time: the answer
    carries `noPictureReason` ("camera stream stalled: no new frame in 1 s (second, third picture)"); camera not
    connected: "camera not connected".
  - `ConverterScalerAndProcessor`: with a `noPictureReason`, or no picture at all, the vision check is NOT run;
    NOT OK to the IOServer and the frontend with the request id, the reason on the picture and in the "check" notice.
- **Abort during a handshake write (a2).** `perform_write_state_action` checked the state, then wrote: an abort
  (emergency, frontend, heartbeat) could reset every tag in between, and the write in flight landed after the reset
  (e.g. Done=True left set for the next cycle). `_writeBoolUntilWritten` now checks the state and writes while holding
  `_abortLock` (abortCycle holds it for the whole abort); the lock is released between retries, so an abort waits at
  most for one write in progress, never for the retries while the PLC is unreachable.
- **Database backup at shutdown (a3).** `stopProcesses()` ended with `sys.exit(1)`; called from the `monitorStop`
  thread that only ended the thread (SystemExit is not an Exception), so the `doBackup()` after it never ran -
  backups were made only at start. New `exitAfterwards` (default True; monitorStop passes False); the backup is
  logged and guarded. The Redis connection is closed once, after the loop (it was closed inside the loop).
- **PC_PLC_ConnectionStatus toggle in its own thread (a5).** It was toggled by the server-status thread, which also
  runs the aborts for a server that is down (abortCycle: about 1-4 s), so the toggle stopped for that long.
- **Camera answer timeout at least the vision check timeout + 8 s (a6).** `getPlcCameraAnswerTimeout()` raises a
  smaller `plc.camera.answer.timeout.sec` to `<vendor>.vision.check.timeout.sec` + 8 s (stopping a timed-out worker
  up to 2 x 3 s, and the pictures), printed once. 12 + 8 = 20: the present values are unchanged.
- **Vision checks timing out in a row (a7).** `VisionCheckWorker`: from the 3rd timeout in a row the reason (shown
  on the frontend) and a critical log say "vision checks too slow on this PC - check the GPU, then restart the
  application"; a check that answers in time resets the count. (Restarting the worker cannot help then.)
- **Smaller items:**
  - QR code validation: `re.fullmatch` on the stripped code (`re.match` accepted trailing characters, or two scans
    run together); the date dots in the 6 patterns are escaped (`\.`; `.` matched any character). The sample codes
    in the repo still match. Check with a few real scans on the plant PC.
    **Wrong - reverted in section 85:** real codes go on after the pattern ("...$1$SPDL ASSY-KNULH$SNPL-MAT$"), so
    `fullmatch` rejected every one; the samples checked here had been cut at the space. The escaped dots stay.
  - `checkIfQRCodeExists`: `connect_timeout=5` and the connection closed (psycopg2's `with connection` does not close).
  - `Configuration.py`: `getPcPlcNoOfRotation1CCWTagName` / `...CWTagName` defined once (the earlier, shadowed copy
    removed; the one in use is unchanged).
  - `PC_PLC_CapCheckOK` is the attribute's name (was `PC_PLP_CapCheckOK`, kept as an alias); test windows and dry
    runs use the new name.
  - Comments: NOT OK Done hold is 0.3 s (three comments still said 0.1 s).
  - `dump.rdb` (a Redis snapshot) and `rendered.html` (a rendered report page) no longer tracked; in `.gitignore`.
    A `git pull` deletes them from the laptop's working folder; nothing uses them.
- **Dry runs (both folders):**
  - New `DryRun_Camera_Stale_Frames.py` (22 checks): `getFrameAfter` (new frame only, old one refused, nothing while
    the camera is unavailable), `TakePictures` (live: three different new frames; stalled; stall after the first
    picture; not connected), the pipeline (NOT OK with the reason, check not run; with pictures run as before), the
    timeouts-in-a-row reason and its reset, the camera answer timeout raised.
  - Fault scenarios K1 (abort while Done=True is being written: Done False afterwards) and K2 (the toggle goes on
    during a 5 s abort). With the old code K1 and K2 fail.
  - `DryRun_Heartbeat_And_Startup.py` H10: the stop command in monitorStop ends the processes and then backs up the
    database. With the old code it fails (no backup).

## 85. Robustness items of the review; second review pass: QR validation fix, request ids, DB connect timeout (2026-11-07)

Architecture items not included (user).

- **QR code validation: back to matching the start of the code (a bug of section 84, fixed first, `4816b13`).**
  Real codes go on after the pattern ("...$1$SPDL ASSY-KNULH$SNPL-MAT$"); `re.fullmatch` rejected every one. The
  samples checked in section 84 had been cut at the space. `re.match` again, on the stripped code; the escaped date
  dots stay (the dates use dots). New `DryRun_QRCode_Validation.py`: every QR code written in the tests (7 distinct,
  the real format) accepted, also with the scanner's line end; side and model read from each; empty / wrong / a date
  with dashes / a code cut short refused. With `fullmatch` V1 fails.
- **Heartbeats follow the work threads (review b1).** Each server's heartbeat was sent by a thread of its own and
  said ALIVE as long as that thread ran, whatever the threads doing the work did. New `utils/WorkerHealth.py`
  (`WorkerWatch`): a server registers its work threads; its heartbeat is DEAD while one is not running, or has not
  gone round its loop for `heartbeat.work.loop.max.silence.sec` (new, 60 s; at least the camera answer timeout + 10 s,
  so that a check that is timing out is never taken for a stuck camera server). A change is logged once.
  - `GenericQueueProcessor`: `lastLoopAt`, set at each pass of the work loop (also while waiting for items).
  - Camera server: MonitorGetPicQueue, TakePictures, ConverterScalerAndProcessor (`cameraWorkers`). IOServer: the read
    and write loops (`lastReadLoopAt` / `lastWriteLoopAt`; a retried handshake write counts as going round). DB server:
    `doWork`. QR code server: the three threads; waiting for a scan counts as going round. Frontend: a 1 s timer of
    the GUI thread; the heartbeat thread sends DEAD when it has not run for 60 s.
  - Frontend menu dialogs (FreeSimpleGUI: Machine Settings, reports, user accounts, the manual-abort confirmation) run
    their own event loop on the GUI thread, which stops the Qt timers: `_runDialog` counts them open, and the heartbeat
    stays ALIVE meanwhile (otherwise an administrator in Machine Settings for a minute would abort the running cycle).
  - What a DEAD server then causes is unchanged (the server-down rules of section 81).
- **Worker transfer cost measured (b2).** Sending the six pictures (three 1280 x 720 frames, each twice) to the vision
  check worker and the result back: about 0.01 s here (worst 0.09 s). The worker now returns its own check time; each
  check is logged as "<STATE>: check S s in the worker, transfer T s", a warning when T > 1 s, so the plant PC's logs
  give both.
- **Plant PC verification (b3):** `docs/plant-pc-verification.md` - the worker inside the exe, check times against the
  timeout (from the log on the plant PC; with the new `PlantPC_Vision_Timing.py` where the source is), killing each
  server and watching the supervisor restart it (what to expect per server), a frozen screen, real QR codes; results
  table. `PlantPC_Vision_Timing.py` runs every stored test picture through its check with the real models in the
  worker and prints median / largest time, transfer and margin per check (exit 1 above 80 % of the timeout); here
  only exercised with a stand-in worker (no weights).
- **Code clean-up (b4), production code outside camera/ and the vision utilities (the equivalence verifier):**
  - 219 f-strings without placeholders made plain strings (ruff F541; same text).
  - Dead assignments removed only where nothing depends on them: three `resp = redisConnection.xadd(...)` (the call
    stays), `result = "Not OK"` in `CosThetaOutcomeContainer.setText`, `flushAfter` in CentralLoggers. Kept on purpose
    (35): `timeOfMessage = newData[timeKeyString]` and the like (the lookup rejects a message without the field),
    calls whose result is unused, `gfe = AshokLeylandFrontEnd(...)` (keeps the window alive). Found while doing it:
    the same `result = "Not OK"` in three other methods of that class is NOT dead (it is passed on) - left.
  - Star imports (2,661 names) not replaced: which names a module really takes from another is only safe to work out
    together with splitting the large modules (architecture item 5).
- **Second review pass, fixed:**
  - **Heartbeat server: the Redis health check thread died at the first recovery** (section 83). It printed
    "storing data again" with `printBoldGreen`, which no import of the module provides (ruff cannot see it through
    the star imports): NameError, the thread ended, and a later Redis failure was never seen. Imported explicitly.
    Found by checking every name the production modules take from star imports: each module imported, each such
    name looked up in it (ruff F405 list, 2,661 uses). Two did not resolve: this one, and `Qt` in the unused
    `frontend/widgets/CosThetaRoundButton.py` (imported now). Modules that cannot be imported at all were checked
    for use: `processors/image/*`, `camera/CameraRecorderServer.py`, `PhotoCamera.py`, `VideoCamera.py` fail on
    configuration getters that no longer exist, and nothing in the application imports them (legacy, left).
    New heartbeat dry-run check R6 (the check keeps running after a recovery and sees a second failure); without the
    import R6 fails.
  - Camera request ids are `<pid>.<cycle>-<n>`: a restarted IOServer starts its cycle and request counters at 0
    again, so a late camera answer to the old IOServer's request could have matched a new request.
  - Every PostgreSQL connection gives up after 5 s if the server does not answer (`PGCONNECT_TIMEOUT`, set in
    `Persistence.py`; libpq reads it; an explicit `connect_timeout` still wins). Before, a PostgreSQL that hung held
    the caller for good (QR duplicate check, IOServer machine settings, DB server, reports). Checked against a socket
    that accepts and never answers: gives up after the timeout.
  - `VerifyIntegrity.py`: invalid escape sequence in a docstring (a SyntaxWarning now, an error in a later Python).
- **Second review pass, looked at and left (not defects):** image file names (millisecond time stamps, one folder per
  check; a PNG write takes longer than 1 ms); log and fault files opened without `with` (kept open on purpose);
  `replaceChar=[...]` default (never changed); the frontend record built 0.75 s after the last result (no scan can
  start a cycle in that time); `VerifyIntegrity.py` globals (licensing code, not called).
- **Repository:** commits `3e09521` and `ab42d6e` contained 24 test pictures written by a dry run on Linux under a
  folder literally named `C:` (`C:/Temp/TrialImages/...`); removed in `8b231c8` and `C:/` added to `.gitignore`. A
  Windows checkout of those two commits fails ("invalid path"); a pull straight to `8b231c8` or later is fine.
- **Dry runs (both folders):** new `DryRun_Work_Thread_Health.py` (17 checks: WorkerWatch, GenericQueueProcessor
  tick, camera / QR / DB heartbeats DEAD and back), `DryRun_QRCode_Validation.py` (9); fault scenarios L (IOServer
  heartbeat DEAD while the read / write loop is stuck, ALIVE again, then a full cycle); frontend F11 (DEAD while the
  GUI thread is frozen, ALIVE again, ALIVE while a menu dialog is open); `DryRun_Camera_Stale_Frames.py` A3 (check
  and transfer times recorded).

## 86. IOServer: emergency and toggle threads watched; the two IOServer test windows corrected (2026-11-07)

- **IOServer heartbeat DEAD when the emergency monitoring or connection-status toggle thread dies or is stuck**, as
  for the read and write loops (section 85): both threads are stored (`emergencyMonitoringThread`,
  `connectionStatusToggleThread`), each loop records `lastEmergencyLoopAt` / `lastToggleLoopAt` at the top of every
  round, and both are in the IOServer's `WorkerWatch` (stuck after `heartbeat.work.loop.max.silence.sec`). Before, a
  dead emergency thread meant the PC never saw the PLC's emergency abort; a dead toggle thread, the PLC saw the PC
  connection drop while the IOServer went on reporting ALIVE. The supervisor still restarts only exited processes; a
  DEAD heartbeat is shown and logged. Fault scenarios L3 / L4 (emergency read / toggle write blocked: DEAD, then
  ALIVE when it goes round again); L 11/11, Siemens and Allen-Bradley, both folders.
- **`Test_IOServer_With_Mock_PLC_UI.py`**: no longer sends "combined ALIVE" heartbeat-server messages (since section
  81 the IOServer takes them for a heartbeat server, and aborted / held cycles when the window closed); new box
  "Camera answer timeout (s)" (the IOServer's `plc.camera.answer.timeout.sec` for this run, read thread-safely); OK /
  NOT OK pressed after the IOServer already answered NOT OK itself says "Too late ... press Trigger again" instead of
  "press Trigger first"; Reset All now aborts the IOServer's cycle (`abortCycle`, queues cleared) instead of forcing
  its state.
- **`Test_IOServer_With_Real_PLC_UI.py`**: no heartbeat sender (same reason); OK / NOT OK are sent only while the
  IOServer is in that step's WRITE state (read over Redis, in a worker thread), else not sent and the reason shown (PLC
  has not asked yet, or the camera answer timeout passed; before, an early answer waited in the queue and was taken
  for the next request). The docstring says to run the IOServer on its own: MainProgram also starts the camera and
  heartbeat servers, which answer the pictures themselves. New `Run_IOServer_Alone.py` (logging server + IOServer).
- Window dry runs: Mock 15/15 (new U11 camera answer timeout), Real 9/9 (new R4b early OK not sent, R4c no
  heartbeat), Siemens and Allen-Bradley, both folders. Work-thread health 17/17, heartbeat 26/26, two full cycles OK.
- **Follow-up (same day, from the first run on the laptop):**
  - The IOServer's heartbeat thread now starts last. Started before the emergency and toggle threads, it logged
    "heartbeat DEAD: emergency monitoring: thread not running; connection-status toggle: thread not running" at every
    start (a false DEAD for one heartbeat; introduced by the first item of this section).
  - `Test_IOServer_With_Mock_PLC_UI.py` and `Run_IOServer_Alone.py` clear Redis before the IOServer starts, as
    MainProgram does. A heartbeat-server status left in Redis by an earlier MainProgram run made the IOServer take a
    heartbeat server as present, then down 8 s later, and hold every cycle: "Send LHS QRCode" did nothing.
  - Mock window: when the IOServer does not take the QR code, it says so (still on qrcode2io.q: not being read;
    taken but not acted on: another IOServer on the same Redis), instead of printing the MockPLC's untouched default
    settings (all 0) as "State-2 cycle settings".

## 87. Large picture window keeps the picture's aspect ratio; Real-PLC test window fixes (2026-11-07)

- **Frontend, large picture window (green / red background):** the picture was stretched to fill the box
  (`resize_pixmap`, IgnoreAspectRatio). Now scaled with its aspect ratio kept: as large as fits, touching one pair of
  opposite sides, the green / red (or white) background filling the rest. Checked: 900 x 300 -> 925 x 308 in a
  925 x 505 box (touches the sides), 300 x 900 -> 168 x 505 (touches top and bottom), 400 x 400 -> 505 x 505. The 14
  result tiles are unchanged. FE dry run 80/80, both folders.
- **`Test_IOServer_With_Real_PLC_UI.py`:**
  - One tag-dump request at a time (lock). The state checks, Get Tag Values, the signal monitor, Run Full Cycle and
    the abort confirmation each deleted `receiveTags` and waited on it, so two at once took or deleted each other's
    reply: "IOServer did not answer" / "Timeout". New check R3b (six requests at once); without the lock 3 of 6 fail.
  - Send LHS / RHS QRCode and Reject QR are sent only while the IOServer is in WRITE_QR_CODE (the PLC has asked:
    CheckQRCode), else not sent and the reason shown; the window moves to step 1 only when the IOServer has accepted
    the code (knuckle picture state); a QR NOT OK to the PLC (side not written, cycle on hold) or no reaction in 10 s
    is shown. Before, it moved to step 1 at once, whatever the IOServer did. Checks R3c, R4a.
  - No `flushdb()` at start or on "Clear Redis": the IOServer is already running (Redis is cleared when it starts)
    and a flush took its log lines on their way to the logging server. Only the window's own queues are cleared.
  - Real window dry run 12/12, Siemens and Allen-Bradley, both folders.

## 88. Own reset delays for the torque values and the presses (2026-11-07)

- New `plc.handshake.torquevalues.reset.delay.sec=2.0`: Station 2 torque 1 and 2 (Set False, Value 0.0,
  RotationDuringTighteningDone False) and Station 3 free-rotation torque (Set False, Value 0.0) are reset 2.0 s after
  the PC has read them (was 0.4 s, `plc.handshake.reset.delay.sec`).
- New `plc.handshake.componentpress.reset.delay.sec=1.5`: component press Done and cap press Done reset 1.5 s after
  the read (was 0.4 s).
- Both PLCs (all three properties files); only these resets changed. `plc.handshake.reset.delay.sec` (0.4) stays
  for the other delayed resets. Getters `getPlcTorqueValuesResetDelay()` / `getPlcComponentPressResetDelay()`, read at
  each use. Fault scenarios G 16/16 (G4 2.00 s, G5 1.50 s) and two full cycles, Siemens and Allen-Bradley.

## 89. Frontend: larger fonts on the status and result tiles; free-rotation torque shown 2 s (2026-11-07)

- Connection status tiles (4): 13 -> 26 pt. Tile titles (black bar, 14 tiles): 12 -> 24 pt. "Result Awaited" / OK /
  Not OK: twice the application's default size (it had no font of its own). At double size, 3 of the 4 "... not
  connected" texts and 9 of the 14 titles did not fit their tile on a 1920 x 1080 screen (cut off on both sides), so
  each of these labels is shown at the double size, or the largest size at which its text fits (recomputed when the
  tile is resized; `fitLabelFontToWidth` in CosThetaLabelUtils). A status tile fits both its texts, so its size does
  not change with the status. The four status tiles no longer share one QFont object.
- New `frontend.free.rotation.torque.display.sec=2.0` (all three properties files): after the last tile gets the
  free-rotation torque, it stays on screen this long before "Component Successfully Done" / "Not Successfully Done"
  and the record (was 0.75 s). Aborts keep 0.75 s. An emergency abort (or Ctrl+Alt+Z) arriving in those 2 s, when the
  cycle has already completed and the IOServer is back at the QR code, no longer turns the completed component into
  an "aborted" screen; it is recorded as completed.
- Checked: screenshots at 1920 x 1080 before / after; FE dry run 80/80, both folders (the 2.0 s measured in its log).
- Cross-checked, no change needed: the IOServer's emergency thread reads PLC_PC_EmergencyAbort every 1 s; on True,
  abortCycle: ABORT to the QR code server (scan stopped), the camera / QR queues cleared, the emergency message to the
  frontend, PLC_PC_EmergencyAbort reset, state READ_QR_CODE, the handshake tags reset and CheckQRCode armed. The
  frontend's emergency worker (started at start-up) checks every `frontend.emergency.status.sleepinterval.sec` (1 s):
  "Component Processing Aborted" screen, the record sent to the DB server with the results so far and the overall
  result Not OK, then back to awaiting the QR code. The record has no field saying it was an abort.

## 90. Abort recorded in the database; one font size per tile group; emergency abort shown at the QR code (2026-11-07)

- **Abort columns:** `aborted boolean NOT NULL DEFAULT false` and `abort_reason varchar(255)` in
  hub_and_disc_assembly_data (CREATE TABLE, and `ADD COLUMN IF NOT EXISTS` for existing databases: added on the next
  start). The frontend sends them with the record (`abortedKeyString` / `abortReasonKeyString` on fe2db.q; a record
  without them reads as not aborted); reasons "emergency button pressed on the machine" and "manual abort
  (Ctrl+Alt+Z) in the frontend". The report header and the three report SELECTs include them (58 columns, aligned).
- **Fonts, one fixed size per group, worked out once after the first render** (replaces section 89's per-label
  fitting): the 4 connection tiles take the largest size at which "QRCode Scanner not connected" fits its tile; the
  14 tile titles the largest at which "Free Rotation Torque" fits; the 14 result labels the largest at which
  "Result Awaited" fits (1920 x 1080: 20 / 16 / 22 pt). `largestFittingPointSize` (width and height),
  `calculateAndSetIdealFontForConnectionStatus`, `calculateAndSetIdealFontsForTitlesAndResults`, called with the other
  after-render font calculations.
- **Emergency abort while waiting for the QR code:** the IOServer sends the emergency message in every state (its
  abortCycle always notifies the frontend). The frontend ignored it at the QR code; it now shows "Component Processing
  Aborted" (until the next scan) and makes no record. Same for Ctrl+Alt+Z there. During the 2 s free-rotation torque
  display of a completed cycle: the abort screen is shown instead of "Component Successfully Done", and the
  completed component is recorded as completed (not aborted). In process: as before, plus `aborted` / reason.
- Checked: FE dry run 96/96 both folders (new F4 aborted + reason, F4b at the QR code: screen, no record, F4c during
  the torque display, F8 manual abort reason; every full record "not aborted"); DB 49/49, endpoints 43/43, no record
  loss, FE -> DB pipeline; columns added to the existing test database and stored (True / reason; False / NULL);
  screenshot at 1920 x 1080.

## 91. Darker tiles; processing time kept; machine settings per model, rotation counts 1 - 4; bung holes per model; tiles no longer squeezed (2026-11-08)

- **Colours:** connected status tiles in a much darker green (`connectionOkLabelStylesheet`; the OK result labels keep
  `okLabelStylesheet`); "Result Awaited" much darker (`noResultLabelStylesheet`). OK / Not OK unchanged.
- **Processing time (bottom left):** counts from the QR code scan, stops when the cycle ends (free-rotation torque, or
  an abort) and stays on screen until the next scan, which restarts it from 0. Before, it fell to 0 at once.
- **Machine settings per model:** `machine_settings.model_name`; the models are taken from `qrcode.partmapping.N`
  (`getModelNames()`: DOST, DOSTPLUS; the properties are `qrcode.partmapping.*`, not `qrcode.mapping.*`). When the
  database is set up, every model without settings gets a copy of the earlier settings for all models (else the
  defaults). The IOServer writes the settings of the QR code's model (`getMachineSettings(db, model_name=)`). Machine
  Settings dialog: first line a Model dropdown; the values shown follow the model chosen; Save stores them for that
  model only. `insertNewMachineSettings(..., model_name=None)` saves for every model (as before, one set).
- **Rotation counts 1 CCW / 1 CW:** dropdowns 1 - 4 (were 1 / 2); limits and the database CHECK constraints 1 - 4
  (existing databases: constraints replaced at start).
- **Bung holes per model:** `DOST.NoOfHolesInBung=4`, `DOSTPLUS.NoOfHolesInBung=2` (all three properties files;
  `getNoOfHolesInBung(model)`); CheckBunk and CheckNoCapBunk take the count from there (were hard-coded DOST 4,
  DOSTPLUS 2). The count chooses BunkSegmenter's routine (2: pair of holes at the configured distance, 4: square). A
  model without the property: the bung check is NOT OK. The values are the existing ones: the request said DOST 2,
  DOSTPLUS 4, but the code, the stored test pictures (DOST bung: 4 holes in a square; DOSTPLUS: 2) and the vision
  constants all say DOST 4, DOSTPLUS 2 - to be confirmed on the machine; swapping is a properties change.
- **Tiles squeezed by a picture:** a picture was scaled to the picture label's size, and a QLabel holding a pixmap asks
  the layout for the pixmap size plus its border and padding, so the picture row grew with each picture (224 -> 242 px
  measured) and squeezed the title and result rows. The picture label's size policy is now Ignored (the rows keep
  1 : 5 : 1) and pictures are scaled to its contents rectangle. After repeated pictures: no row changes; all 14 tiles
  the same title / result heights.
- Checked: FE 98/98 both folders (F4d time frozen, restarted by the scan; F6 count 5 refused); DB 54/54 both folders
  (per model, copy, counts 3 / 4, history per model); fault scenarios D 65/65 Siemens, 64/64 Allen-Bradley, both
  folders (D6: each model's cycle writes its own settings); the Machine Settings dialog driven (model switch, 1 - 4,
  save for one model); bung routing (DOST 4, DOSTPLUS 2, missing property NOT OK, swapped property swaps the routine;
  segmenter stubbed - no model weights here); Mock 15/15, Real 12/12, endpoints 43/43, PLCDataIO 38/38, indexes 8/8,
  vision verifier ALL EQUIVALENT; screenshot 1920 x 1080.

## 92. Web service and MI dashboard: aborted cycles (2026-11-08)

- **Filters (EndpointsDB, shared by both):** `aborts` = include (default: every record, as before) / only / exclude /
  emergency (only aborts with the machine's emergency button) / manual (only manual aborts, Ctrl+Alt+Z); aliases
  all, with, none, no, without, no_aborts, aborted, completed. `result` = all (default) / ok / notok. Together, e.g.
  aborts exclude + result notok = completed cycles that failed a check (rejects). Unknown values: HTTP 400 naming the
  choices. The exported records already carry `aborted` and `abort_reason` (section 90).
- **Web service:** every records method (API and page names) takes `aborts` / `result` in the body (also
  abort_filter / abortFilter, result_filter / resultFilter). Query page: "Aborted cycles" and "Result" selectors that
  apply to every query; the downloaded file name carries the filters when not the defaults. API reference and
  examples updated.
- **MI dashboard:** "Aborted cycles" selector (the same five choices) applied to every chart and count; the
  per-day / per-week counts are now OK / NOT OK / ABORTED (an aborted cycle was counted as NOT OK); the summary shows
  ABORTED; new tab "Aborted Cycles": per day, by reason (emergency button, manual). `/data?aborts=`.
- Endpoints dry run 77/77, both folders (was 43): seeded aborted records (emergency and manual); 13 filter
  combinations by dates against SQL (default, include, only, exclude, emergency, manual, ok, notok, exclude + notok,
  exclude + ok, only + ok = none, two aliases); every records method with aborts exclude; the page route; bad filter
  values 400; the dashboard's three series, summary, Aborted Cycles tab and its filter against SQL.

## 93. Frontend abort (Ctrl+Alt+Z): password-protected, frontend only; PC_PLC_CycleAborted named, optional (2026-11-08)

- **Use (user):** only for when the machine's cycle has been aborted and the frontend did not follow. It must not
  affect the other processes (IOServer, camera server) or the machine, so no emergency response.
- **Frontend:** Ctrl+Alt+Z asks for a password (fixed: `admin123`, `FE_ABORT_PASSWORD`) instead of the earlier
  confirmation. Wrong: "Wrong password - nothing aborted", nothing done; cancelled: nothing done. Right: the abort
  screen, the record with aborted / reason "manual abort (Ctrl+Alt+Z) in the frontend", back to the QR code - as
  before; but no ABORT to the QR code server and no queues cleared any more, and instead of an abort the IOServer gets
  a request (`FrontendAbortRequest` on fe2io.q). At the QR code (nothing in process): the abort screen only, nothing
  sent, no record.
- **IOServer (`handleFrontendAbortRequest`):** tries `PC_PLC_CycleAborted` = True (written and read back, 3 attempts).
  Only if that succeeds: the cycle is aborted (handshake tags reset, QR code server told, queues cleared; the flag goes
  False at the next accepted QR code, as before). No tag name, no address / tag on the PLC, PLC not connected, or the
  write fails: one log line (console, file, audit), nothing else - the cycle goes on. `Abort` on fe2io.q (test tools:
  the Real-PLC window's Reset to Step 0) still aborts at once.
- **The tag is named, without an address:** `pc.plc.cycle.aborted.tagname=PC_PLC_CycleAborted` in all three properties
  files (with a comment saying why it must stay); no address in IOServerProperties.yaml. It is an optional tag
  (`OPTIONAL_TAG_ATTRS`): the Siemens start-up address check (`requiredTagNames`) leaves it out - otherwise the
  IOServer would refuse to start; where the PLC has no address (Siemens) or no such tag (Allen-Bradley, from the
  controller's tag list at the first connection) it is not written at all (logged once), so the per-cycle "flag False"
  write and the other PC aborts (frontend / heartbeat server down, PLC restart) produce no error. In the tag dump it is
  shown as None until it exists.
- **Allen-Bradley (asked):** yes. pycomm3 writes by name; a controller without the tag answers an error, which
  write_bool turns into False (3 attempts, then logged); and the controller's tag list read at the first connection
  marks it as missing, so it is skipped from then on. When the PLC programmer adds a BOOL PC_PLC_CycleAborted, it is
  used with no change here (Siemens: add its address to IOServerProperties.yaml).
- Checked: new fault scenarios M (Siemens 6/6, Allen-Bradley 12/12, both folders: no address / no tag on the PLC, no
  tag name, write fails after exactly 3 attempts -> no abort, no QR code server message, cycle completes; tag present
  -> flag True, cycle aborted, QR code server told, flag False at the next QR code); FE 102/102 both folders (F8 wrong
  password / cancel: nothing; admin123: only the request, nothing to the QR code or camera server, abort record; at
  the QR code: screen only); full fault scenarios Siemens 186/186, Allen-Bradley 193/193; Mock 15/15, Real 12/12
  both PLCs.


## 94. Machine Setting per model: Bypass Component Press and Second Tightening (2026-11-13)

- **Use (user):** for a model, skip the bung check before the component press, the component press, the bung removal
  check and the second tightening (states 13–19). The PLC branches; the PC tells it.
- **PLC tag `PC_PLC_Skip_Second_Tightening`** (bool, PC writes): `pc.plc.skip.second.tightening.tagname` in all three
  properties files, **no Siemens address yet** (PLC programmer to assign). Optional tag (`OPTIONAL_TAG_ATTRS`): the
  IOServer starts without it.
- **IOServer, state 2 (`writeCycleSettingsToPLC` → `_writeSkipSecondTightening`):** after the side, the model's setting
  is written True / False and read back (3 attempts), before QR OK / Done; every cycle, so the PLC never keeps the
  previous component's value. The Torque 2 setpoint is still written.
  - No tag name, no address (Siemens) or not on the PLC (Allen-Bradley): nothing written, full run (logged; frontend
    notice "Bypass not possible … full run" when the setting asked for it).
  - Tag present but not written (True or False): QR code NOT OK — the PLC's sequence would not be known.
  - After the Torque 1 value (state 12), when True was written: the frontend gets moveToState = 20 with the value and
    the IOServer goes to 20 (`SKIP_TO_STATE_AFTER_TORQUE_1`). `cycleSettings:<QR>` carries
    `bypass_press_and_tightening2: true` only when bypassed. The tag is in the tag dump; not reset on abort.
- **Frontend:** the 4 skipped tiles show "Bypassed" (grey) from the knuckle result (`cycleSettings:<QR>`); Torque 1
  with moveToState = split pin picture → straight to the split pin picture. Overall result: the skipped steps are left
  out, not failed. Record: 'Bypassed' results, no datetimes (NULL), no pictures, torque 2 -1.0, rotation done NULL,
  `bypassedPressAndTightening2` = True.
- **Machine Settings dialog:** checkbox "Bypass Component Press and Second Tightening" per model. Save asks for the
  password (`admin123`, `MACHINE_SETTINGS_PASSWORD`), then Yes / No listing the changes; wrong password / No: nothing
  saved, the dialog stays open.
- **Database:** `machine_settings.bypass_press_and_tightening2` and record column `bypassed_press_and_tightening2`
  (boolean, default false; added by ALTER on existing databases); in the CSV exports and reports.
- **Web service / MI:** `"bypassed"` filter (include / only / exclude) next to the abort filter; selectors on both pages.
  The MI's torque 2 chart already leaves out -1.0.
- **Mock-PLC window:** the auto cycle follows the PLC's branch (MockPLC holds the tag) and skips steps 6–9.
- **For the PLC programmer:** add BOOL `PC_PLC_Skip_Second_Tightening`; when True after the first tightening, go
  straight to CheckSplitPinAndWasher. Siemens: then add its address to IOServerProperties.yaml.
- **DB server:** a bung check without a picture keeps the result 'Bypassed' (otherwise NOT OK, as before);
  `rationaliseOK_NotOK` keeps 'Bypassed' (otherwise stored as 'Not Checked').
- Checked: new fault scenarios N (Allen-Bradley 14/14: no tag -> full run; True written before QR OK, Torque 2
  setpoint written, moveToState 20 to the frontend, states 13-19 skipped, no bung pictures; other model -> False, full
  run; tag not taken -> QR NOT OK, re-scan -> bypass; Siemens 4/4: no address -> full run); full fault scenarios
  Allen-Bradley 207/207 (C re-run after classifying the tag), Siemens 190/190; FE 120/120 both folders (F12); DB 62/62
  both folders (6b, 9); endpoints 91/91 both folders; Mock window 17/17 both PLCs and machine 1 (U12, U13); Real
  window 12/12 both PLCs.
- **Test windows (follow-up, 2026-11-13), both folders:**
  - FE windows (`Test_FEServer_Simulated_Inputs_UI`, `_Mock_With_Image_Files_UI`, `_With_Live_Camera_UI`): a "Bypass
    Component Press and Second Tightening" tick box, taken when the QR code is sent. The window then plays the IOServer
    of a bypassed cycle: publishes `cycleSettings:<QR>` with the bypass, sends Torque 1 with moveToState = split pin,
    skips the 4 steps (marked "Bypassed"). Unticked: the key is removed (full run). Tk variables now bound to their
    window (they had taken the first Tk created in the process).
  - IOServer Real-PLC window: `PC_PLC_Skip_Second_Tightening` shown with the cycle settings; when the IOServer goes from
    Torque 1 to the split pin picture, steps 6-9 are marked "Bypassed" and the window goes on at step 10; Run Full
    Cycle skips their answers. Mock-PLC window: the tag shown with the cycle settings; skipped steps marked.
  - Dry runs: Real window R8/R9 (Allen-Bradley 15/15: bypassed cycle through the window; Siemens 13/13: no address, full
    run), Mock window 17/17 both PLCs and machine 1, FE 125/125 both folders (F13: the simulated-inputs window drives a
    whole bypassed cycle, record OK and bypassed; the image-files window publishes the bypass and jumps with the
    frontend). F13 needs a display (xvfb-run on Linux).

## 95. Machine 2 camera moved again: knuckle check re-placed; side and presence by differences (2026-11-13)

- **Camera change** (old pictures of 3 Nov against new ones of 13 Nov):
  - **On the plate** (both pin seats, both top holes, the block hole and both screws, as fitted circles): zoom
    x1.049, rotation +0.95 deg, old (705, 353) -> (641.5, 351.2). Leaving out any one feature changes the zoom by at
    most 0.003, the rotation by at most 0.06 deg and the centre by under 1 px.
  - **On the same knuckles** (5 old / new pairs, ECC alignment): zoom x1.037-1.041, rotation +0.84..+0.98 deg, old
    (705, 353) -> (640.2-641.0, 345.3-349.2). The knuckle face sits higher than the plate (closer to the camera), so its
    zoom changes slightly less. The knuckle regions were moved with the mean of these: x1.039, +0.89 deg,
    (705, 353) -> (640.7, 347.1).
  - **The fixture changed too:** the plate is now bright (120-165 where it was dark), and the bottom bar and the
    bracket below the hub are different parts.
- **Moved by that transform:** side boxes, B, C, D, E with their alternates, the step 2 / step 3 model polygons,
  and the (now unused) rectangle A with its alternate. All 19 knuckles are then read with the right side and model;
  only A failed. A and its alternate sat on the top edge of the casting between the bosses, and on the LHS knuckles that
  edge is lower, so they caught the bright plate.
- **`siemens.vision.knuckle.common.check.threshold` 84 -> 100** (user): the casting reads about 40-70 and the bare
  plate 120-165.
- **Side and presence by differences (user's design; `CheckKnuckle`, new keys, both machines' files):**
  - `<vendor>.vision.knuckle.side.by.difference` (siemens True, allenbradley False); `.side.difference.threshold` 10
    (user: 15 -> 10 for small camera / part movements).
  - **Contours A / A'** (`.common.check.a.polygon.points`, `.common.check.a.alt.polygon.points`): left / right of the
    hub between the bosses, top at y 199 and sides as the moved rectangle A' (A: its mirror about the hub, x 581-613).
    Each one's bottom sits 3 px above the highest casting edge measured on the knuckles of the other side (A: the RHS
    knuckles, A': the LHS knuckles), with a point every 4 px. The LHS knuckle's casting reaches higher on the left and
    darkens A; the RHS knuckle's reaches higher on the right and darkens A'. A' - A > 10 -> LHS, A - A' > 10 -> RHS,
    otherwise NOT OK (the empty fixture shows bare plate in both).
  - **Side boxes D1 / D2** (the existing `lhs.crop` / `rhs.crop`, mean grey level): D1 - D2 > 10 -> RHS (arm on D2),
    D2 - D1 > 10 -> LHS, otherwise NOT OK.
  - **Both must give the same side**, which must match the QR code. In this mode the rectangle A, its alternate and
    `lhs.rhs.darkness.threshold` are not used. A polygon `0,0;0,0;0,0` = not set (Machine 1).
  - The two new polygons are plain `vPoints` constants, so a live configuration reload applies them. The step 2 / step 3
    polygons, wrapped in `np.array`, are still not reloaded; that limitation is older than this change.
- **Measured (19 knuckles; the margin is beyond the threshold):**
  - **Contours:** A' - A is +26.9..+42.6 on the LHS knuckles and -25.2..-40.4 on the RHS ones (smallest margin 15.2).
    With the picture moved +/-3 px the smallest difference is 13.4. Empty fixture: -1.7.
  - **Side boxes:** the arm reads 37.5-48.9, the bare bar 85.1-99.1 (smallest margin 33.9). The empty fixture
    differs by 11-16 on its own (the bar is unevenly rusty), and the contours reject it.
  - **B, C, D, E:** margin to 100 at least 38.8 (alternates at least 30.2).
  - **Model polygons:** DOST at most 94.9, DOSTPLUS at least 223.7 (limit 160).
- **Pictures:** `test/machine2_dryrun/knuckle_images/` replaced by the 13 Nov set: DOST LHS x5 (one with a bearing on the
  spindle), DOST RHS x3, DOSTPLUS LHS x6, DOSTPLUS RHS x5, empty fixture x1 (labels confirmed by the user). The old set
  is in git history. Figure: `docs/knuckle_regions_machine2.jpg`.
- **DryRun_CheckKnuckle_Images** (both folders, same file):
  - The margins of both differences are printed and must be at least 10 beyond the threshold.
  - Paint over the darker contour -> NOT OK; over the brighter one -> OK.
  - The rectangle A is not paint-tested in this mode.
  - New for both machines: the picture moved +/-3 px (8 shifts) must still be OK with its own QR code. This is a check
    on Machine 2 and reported only on Machine 1, where circle C reads 88.6 (> 84) on DOSTPLUS_RHS_4 at +3 / +3.
    Machine 1 is not re-tuned.
  - Results: Machine 2 155/155; Machine 1 30/30, plus that one NOTE.
- **Machine 1 unchanged:** old and new `CheckKnuckle` with the Machine 1 settings on all 43 knuckle pictures in the repo
  (Machine 1, old and new Machine 2 sets) x 9 positions x 4 QR codes: 1548 verdicts, 0 different.
- **Verifier:** reference commit 635215e (the new `CheckKnuckle` with its values written in) in `baseline_per_file`;
  both copies ALL EQUIVALENT (190 parameters).
- **Not yet done:** the other stations (hub, top bearing, nut / washer, no bunk, cap, bunk) need the same work on their
  new pictures.
- **Knuckle check window (follow-up, user):** new `test/machine2_dryrun/DryRun_Knuckle_Check_UI.py` (and the machine1
  copy).
  - **What it does:** choose a folder or one picture. Each picture goes through the real `CheckKnuckle` with this
    machine's settings and each of the four QR codes, and the window shows DOST / DOSTPLUS LHS / RHS, or NEITHER with
    the check's first reasons (more than one match would show AMBIGUOUS).
  - **Markings:** the regions with their mean grey levels. A / A' and D1 / D2 are highlighted only when the darker one
    is darker by more than the threshold.
  - **Controls:** seconds between pictures 1 - 10 (3 at start), read at each picture so a change applies during the
    run; Stop; a count per knuckle at the end; optional saving of the marked pictures to
    `<folder>/knuckle_check_results/`.
  - **Runs on its own:** the check's log messages are collected by the window, so no Redis, models, PLC or camera are
    needed.
  - **Checked:** driven under Xvfb, 9/9 on each machine (Machine 2: the 20 pictures identified as labelled, the empty
    fixture NEITHER with its reason; Machine 1: its 4 pictures, three times each). Covers the seconds changed during
    the run, a single picture, Stop part-way, an unreadable file reported with the run going on, and saving.
- **Side boxes D1 / D2 moved to the middle of the leg resting on the bar (follow-up, user):**
  - **Why they were where they were:** the 3 Nov positions (on the old bar, where the arm lay) moved by the camera
    transform; no measurement placed them, and D2 ended up near the corner of the arm's tip.
  - **How the new positions were found:** the leg on the bar was found on every picture by colour (the bar is rusty
    brown, R-B 31-39; the leg is neutral, R-B about -3..-6), each row filled between the leg's edges, and the area
    covered on every LHS / RHS picture taken. Each box is centred on its deepest point: (420, 672) and (853, 669),
    16-17 px from the nearest leg or bar edge.
  - **Change:** `siemens.vision.knuckle.lhs.crop` 389,663 -> **409,661**, `rhs.crop` 855,677 -> **842,658** (still
    23 x 23; all three properties files).
  - **Arm box / bare box:** LHS 30.0-30.9 / 79.2-87.1 (was 37.4-43.6 / 85.1-89.3); RHS 37.8-42.2 / 89.5-94.1 (was
    44.1-48.9 / 94.1-99.1).
  - **Smallest difference:** as photographed 49.1 (was 43.9); with the picture moved +/-3 px 46.5 (was 28.2). The
    empty fixture differs by 9.2 (largest with shifts 13.7), and the contours reject it.
  - **Checked:** DryRun_CheckKnuckle_Images Machine 2 155/155 (side-box margin 39.1 beyond 10), Machine 1 30/30; the
    window 9/9; verifier ALL EQUIVALENT. Figure `docs/knuckle_regions_machine2.jpg` redrawn (by the window's own
    drawing).


## 96. Machine 2 camera moved again: hub check re-centred (2026-11-13)

- **Code checked:** the hub check's only position is `<vendor>.vision.hub.segment.center`. It sets:
  - the crop (the ring `segment.inner/outer.radius` round it);
  - the grouping of the shapes found by their distance from it;
  - the envelope filter (the centre must lie inside each group's outline);
  - the even-spacing test round it.

  Everything else is a size (ring radii 116 / 253, mask areas 121-3630, the 10 / 20 / 40 px shape-size steps) or
  independent of position (the gamma order from the mean brightness of the whole picture). The "no bearing in the
  bore" circle has its own centre (`centre.check.x/y`; off in production).
- **Old pictures (3 Nov, 6 uploaded):** hole circle centred at (707.2-708.2, 350.8-353.2), r 143.3-143.8 (configured
  707,352); studs r 179 (DOST) / 186 (DOSTPLUS), their circle about 6 px higher (the stud tops stand above the flange).
- **New pictures (13 Nov, 10: DOST x5, DOSTPLUS x5):** hole circle (643.5-645.0, 335.9-342.6), r 147.9-149.0 (x1.035);
  studs r 181-196, about 7 px higher. The parts sat less consistently than on 3 Nov (DOST about y 336, DOSTPLUS
  340-342). Holes and studs stay well inside the 116-253 ring and the area window: sizes unchanged.
- **Geometry test:** the real `segment_holes` post-processing (shape classification, area-ratio and shape filters,
  grouping, envelope, even spacing, model match) on masks at the holes and studs found on each picture, in place of
  MobileSAM, with the centre swept.
  - **New pictures:** every picture passes with its own model and is rejected as the other model for centres in
    x 628-659, y 322-346. The middle, **(645, 334)**, is 9.8 px from the nearest centre that fails. (644, 340), the
    hole-circle median, was only 4.2 px from it.
  - **Old pictures:** all 6 pass for centres in x 690-727, y 330-369; middle (709, 352), 14.0 px from the nearest
    failing centre. This confirms the old setting.
  - **New pictures with the old centre:** 0 of 10 pass.
- **Change (all three properties files, siemens only):** `segment.center` 707,352 -> **645,334**; `centre.check`
  707,352 -> **644,340** (the hole circle's centre; the check runs only when enforceBearingAbsenceCheck=True).
- **A stand-in tried first and dropped:** dark / bright blobs given to the check as MobileSAM masks. It failed 8 of
  19 old pictures with the old, proven centre (holes merged with dark surroundings), so it could not judge a centre.
- **Pictures:** `test/machine2_dryrun/hub_images/` replaced by the 13 Nov set (DOST_01-05, DOSTPLUS_01-05); the old set
  is in git history. Verifier ALL EQUIVALENT.
- **To confirm on the laptop (needs the models):** `DryRun_Hub_Check.py` on `hub_images`, i.e. the MobileSAM step itself.

## 97. Machine 2 camera moved again: top bearing check re-centred; sizes and brightness limit checked (2026-11-13)

- **Code checked:** the only position in the top bearing check is `<vendor>.vision.topbearing.annular.center`: the
  search disc is cut round it, the two start points (`center.offsets` 0,0 and 0,-11) are relative to it, and the
  brightness comparison cuts the hub picture of the same cycle (same camera) round it. Everything else is a size
  (search disc 132, detection mask 66, prompt radius 40 with edge windows 0.65-1.2 / 0.8-1.6 of it, ring thickness
  15 / 16, refinement spans, bearing limits) or a brightness limit.
- **Old pictures (3 Nov, 4 uploaded), old settings:** all OK; ring centres (707.1-707.7, 350.9-354.4), outer
  diameter 98.0-101.7, width 14.0-14.5, mean diameter 83.5-87.0, brighter than the hub by +51.7..+57.9.
- **New pictures (13 Nov: DOST x2, DOSTPLUS x4), real check, OpenCV only (no models), against new open-bore hub
  pictures:**
  - Old centre: no ring found on any of them.
  - **Centre sweep:** every centre within +/-15 px of (642, 340) in x and y (3 px grid) gives OK on all 6, so the
    exact centre is not critical.
  - **Setting:** 3 px below the rings' median centre (641, 342), as on 4 Nov: **642,345**. The start points (y 345
    and 334) bracket both models' rings (DOST about y 335-336, DOSTPLUS 342-344; the DOST parts sat higher, as on
    the hub pictures).
- **At 642,345:** all 6 OK. Rings: outer diameter 102.6-107.1 (limits 86-113), width 15.0-15.5 (9-21), mean diameter
  87.6-92.1 (>= 75), brighter than the hub by +82.7..+105.5 (limit 30). The 10 new open-bore hub pictures given as
  bearing pictures: all NOT OK (no ring).
- **Sizes not changed:** the rings grew by x1.035-1.05 and stay inside every limit, with 5.9 px to the outer-diameter
  maximum. Rejecting a nut offered as a bearing rests on the outer-diameter minimum 86 (nuts measured 75-81 before
  the move, so expect about 78-84 now) and the mean-diameter minimum 75; to be re-checked on the new nut pictures.
- **Brightness limit (user: lower it for greasy bearings, to the Allen-Bradley value):** `final.gamma.threshold` is
  30 on both machines and was 30 in the original production code (`FINAL_GAMMA_THRESHOLD = 30.0`), so the
  Allen-Bradley value is the one already in use. Unchanged.
- **Pictures:** `test/machine2_dryrun/top_bearing_images/` replaced by the 13 Nov set (DOST_bearing_01-02,
  DOSTPLUS_bearing_01-04); the old set is in git history. Verifier ALL EQUIVALENT.

## 98. Machine 2 camera moved again: nut / washer and no-bunk centre; nut sizes; bearing-or-nut limits; washer test on diff_R alone (2026-11-13)

- **Code checked:** positions are `<vendor>.vision.nutwasher.nut.detection.center` and `.nobunk.nut.detection.center`
  (the search disc); the hexagon tests (`vision.nut.*`, `nut.reference.center` in crop coordinates) and the washer
  comparison (the top bearing ring of the same cycle) are relative and unchanged.
- **Measured (13 Nov, thresholded nut outline standing in for MobileSAM):** nut centre (643.6-644.5, 328.5-336.6) -
  DOST about y 329, DOSTPLUS 336.5 (old pictures (707.4-708.5, 348.2-350.9), detection centre 708,358). The nut grew
  x1.09 across / x1.19 in area (it sits higher, nearer the camera; the hub only x1.035): outline 4830-5370 px (window
  max was 5500), nut outer diameter 77.4-81.7 (max 84), mean diameter 69.4-70.9 (max 72); as a ring (fallback and top
  bearing ring finder) outer 83.6-85.2, mean 71.6-72.3, inner 57.6-59.2.
- **Changes (siemens, all three properties files; user approved):**

  | key (siemens.vision.) | was | now |
  |---|---|---|
  | nutwasher.nut.detection.center, nobunk.nut.detection.center | 708,358 | **644,344** |
  | nut.min/max.mask.area | 3000 / 5500 | **3500 / 6500** |
  | nutwasher.nut.outer.diameter.min/max | 64 / 84 | **70 / 92** |
  | nutwasher.nut.mean.diameter.min/max | 60 / 72 | **65 / 80** |
  | nutwasher.nut.ring.outer.diameter.min/max | 70 / 86 | **76 / 94** |
  | nutwasher.nut.ring.mean.diameter.max | 74 | **80** |
  | topbearing.outer.diameter.min | 86 | **94** |
  | topbearing.mean.diameter.min | 75 | **80** |
  | topbearing.inner.diameter.min (new) | - | **65** |
  | nutwasher.washer.threshold.r | 0.55 | **33** |
  | nutwasher.washer.check.rb (new) | - | **False** |

- **Centre 644,344:** hexagon accepted on 11/11 nut pictures with the centre moved +/-8 px in x or y; symmetry 75-89
  (minimum 55).
- **Bearing or nut (user: the 84.9 vs 86 margin was too small):** midpoints of the measured gaps. Nuts 83.6-85.2 /
  71.6-72.3 / 57.6-59.2 against bearings 102.6-107.6 / 87.6-92.1 / 71.6-77.1 (outer / mean / inner diameter).
  **New code:** `CheckTopBearing.BEARING_INNER_DIAMETER_MIN` (`<vendor>.vision.topbearing.inner.diameter.min`,
  0 = not checked; allenbradley 0.0).
- **Washer test (user decision):** diff_RB no longer separates under the new light. A DOST washer alone reads diff_RB
  -0.6, below the no-washer values; DOST nut + washer 2.3-4.8 failed the 3.5 limit. diff_R separates. **New code:**
  `CheckNutAndPlateWasher.WASHER_CHECK_RB` (`<vendor>.vision.nutwasher.washer.check.rb`; siemens False, allenbradley
  True: Machine 1 unchanged); washer present = diff_R > threshold.r [and diff_RB > threshold.rb when on].
- **Results (real checks, OpenCV only; nut outline by thresholding in place of MobileSAM):**
  - **Nut + washer:** OK 67/67 (every nut picture against every bearing picture of the same part; DOST 2 parts,
    DOSTPLUS 1), diff_R 48.8-64.7.
  - **Washer only:** 0/20 OK (no nut).
  - **No washer** (bearing against bearing of the same part, 24 pairs, 3 parts): diff_R -6.6..+24.8, all under 33.
    The 24.8 comes from exposure differences between two pictures of the same bearing.
  - **Top bearing:** 12/12 bearings OK; 21 nut pictures and 10 open bores NOT OK.
- **OpenCV in the sandbox:** it had OpenCV 5.0, whose `HoughLinesP` returns (N, 4) instead of (N, 1, 4); the hexagon
  detector then crashed in `cluster_lines_by_angle`. The project pins 4.11 (requirements.txt), so the sandbox was
  set to 4.11 and every check of sections 95-97 was re-run: knuckle 155/155 and 30/30, knuckle window 9/9, hub 10/10,
  top bearing as above. If OpenCV is ever upgraded to 5.x, `cluster_lines_by_angle` needs `np.reshape(lines, (-1, 4))`.
- **Verifier:** reference commit 8d7cdd5 for CheckTopBearing (previous reference 4e04121 + this change; 9 manifest
  lines moved) and CheckNutAndPlateWasher; both copies ALL EQUIVALENT (192 parameters).
- **Pictures:**
  - `nut_washer_images/` replaced: DOST_nut_01-05 (part 1, use DOST_bearing_01/03/04), DOST_nut_06-07 (part 2,
    use DOST_bearing_02/05/06), DOST_washer_01-02, DOSTPLUS_nut_01-04 and DOSTPLUS_washer_01-02 (use
    DOSTPLUS_bearing_02-04).
  - `top_bearing_images/` gains DOST_bearing_03-06.
  - DryRun_NutWasher_Check shows "diff_RB (not checked)" when the R-B test is off.
- **To confirm on the laptop (models):** DryRun_NutWasher_Check on `nut_washer_images` - the MobileSAM nut mask must
  fall inside 3500-6500 and the nut sizes inside the new ranges.
- **Follow-up (user's run of DryRun_NutWasher_Check with MobileSAM on the laptop):**
  - **Real nuts, 22 pictures, as MobileSAM measures them:** outer diameter 84.2-85.0, mean 71.9-72.7, width
    11.8-12.5, bore radius 29.8-30.5, symmetry 85-96, diff_R +56.2..+71.3. The thresholded stand-in had read the
    outer diameter 3-7 px small.
  - **False accept:** DOSTPLUS_bearing_03 (a bearing, no washer, no nut) gave "nut found". MobileSAM took the
    bearing's inner ring for the nut: outer diameter 70.3 (just over the 70 minimum), mean 71.1, width 3.6,
    symmetry 62. The washer comparison then read diff_R +36.8 against the window's bearing reference, over 33; the
    same-part pairs measured here had reached only +24.8.
  - **Change:** `siemens.vision.nutwasher.nut.outer.diameter.min` 70 -> **77**, midway between 70.3 and 84.2 (6.7 px
    each side). The ring fallback cannot accept that picture either: its ring measures 103 against the fallback
    maximum of 94. Verifier ALL EQUIVALENT.
  - **Bearing-ring guard (user: both machines):** `CheckNutAndPlateWasher.BEARING_RING_GUARD`
    (`<vendor>.vision.nutwasher.bearing.ring.guard`, True for both machines). Before the nut is looked for, the top
    bearing ring finder runs on the nut picture; a ring within that machine's top bearing size limits means the
    bearing is uncovered -> NOT OK ("[Guard] Bearing ring visible ... no nut and washer on the bearing"; the ring drawn
    in red).
    - **Machine 2:** a bearing-sized ring on 10/10 bearing pictures, and none on the nut + washer (11, ring outer
      83.6-85.2, mean 71.6-72.3, inner 57.6-59.2), washer-only (4, no ring) or open-bore (10, no ring) pictures.
    - **Machine 1** (limits outer >= 86, mean >= 75, no inner minimum): no Machine 1 nut / bearing pictures are in
      the repo, so not run here. From the measurements recorded in sections 47-49, nuts found as a ring measured
      outer 75-81 and mean 64-68, bearings 92-98 and 78.5-83.3. The guard would reject bearings and pass nuts, with
      5 px (outer) and 7 px (mean) margin on the nut side. To confirm on Machine 1 pictures.
    - **Cost:** about 1.1-1.4 s per nut / washer check on the sandbox CPU (the ring finder); about 2.4-2.7 s for a
      whole check here. The check timeout is 12 s.
    - Results: the full nut / washer run unchanged (67/67 nut + washer OK, 0/20 washer only, top bearing as before);
      all 10 bearing pictures offered as the nut picture NOT OK by the guard. Verifier reference aa18044; both copies
      ALL EQUIVALENT (193 parameters).
  - **`siemens.vision.nutwasher.washer.threshold.r` 33 -> 38 (user):**
    - **Washer present:** lowest 48.8 with the stand-in outline (67 pairs), 56.2 with MobileSAM (22 pictures, the
      user's run).
    - **No washer:** bearing against bearing of the same model, 42 pairs, highest 24.3 (24.8 in the same-part
      pairs).
    - **Margins at 38:** 10.8 / 18.2 below the washer values, 13.7 above the no-washer values.
    - The window's +36.8 came from a bearing picture taken for a nut picture (MobileSAM's mask on the bearing's inner
      ring); the guard now stops that case before the washer comparison.
  - **Guard in parallel with the nut detector (user: it added about 1 s):** the ring finder is started in its own
    thread (`CheckNutAndPlateWasher._guard_executor`, one worker kept for the process) before the nut detector runs,
    and its result is taken after the detector returns. A bearing-sized ring then means NOT OK, as before. The ring
    fallback reuses that ring instead of running the ring finder a second time.
    - **Time measured:** nut detector played by a 1.2 s step (MobileSAM's time on the GPU, which releases the GIL):
      the whole check took 1.24 s with the guard on and 1.24 s with it off; the ring finder alone took 1.20 s.
    - **On the plant PC:** the overlap depends on free CPU while MobileSAM runs, since the ring finder is OpenCV on the
      CPU; the camera server's log of each check's time shows it.
    - Results unchanged: 67/67, 0/20, 24 no-washer pairs under 38, top bearing 12/12 with 0/31, guard 10/10.
      Verifier reference a29234b; both copies ALL EQUIVALENT.
  - **Guard moved to the camera server process (user, after measuring 2.0-2.2 s per nut check on the laptop):** a thread
    beside the nut detector gave no real overlap. Both are mostly Python-level work, and Python runs one thread's
    Python code at a time (the GIL). Measured: ring finder 1.03-1.18 s alone, Python work 1.20 s alone, the two in
    threads 1.91-2.02 s, in two processes 1.20 s. A process pool inside the check is impossible: the checks run in
    the vision check worker, a daemon process, which may not start children ("daemonic processes are not allowed to
    have children", tested).
    - **Now:** the camera server, which only waits while the worker runs a check (it talks to the worker over a
      multiprocessing Pipe), runs the ring finder in a thread of its own process on a copy of the nut picture
      (`VisionCheckWorker._startBearingRingGuard`, in `assess` right after the job is sent). After the worker's
      verdict it takes the ring (`_finishBearingRingGuard`, at most 5 s more, `GUARD_MAX_WAIT_SECS`). A bearing-sized
      ring makes the verdict NOT OK, with the ring drawn in red and the reason "bearing ring visible ...". Logged
      each time with the ring finder's time and the wait after the check.
    - **In the worker process** `_workerMain` sets `CheckNutAndPlateWasher.GUARD_DONE_BY_CALLER = True`, so the check
      itself skips the guard. In-thread mode (`check.in.worker.process=False`) and the dry-run windows keep the guard
      inside the check (correct, about 1 s slower). The decision is one function for both,
      `CheckNutAndPlateWasher.bearingRingVerdict`.
    - **Checked:** new `DryRun_Bearing_Ring_Guard.py` (both folders) 9/9: every nut check 1.21 s against a 1.2 s
      worker, waited 0.00 s after the check. The 11 nut + washer pictures OK, the 4 washer-only without a bearing ring,
      the 10 bearings NOT OK "bearing ring visible"; other states and the guard switched off unaffected.
      DryRun_Vision_Check_Timeout 22/22. The in-check path as before (67/67, 0/20, guard 10/10). Verifier reference
      9e2812a; ALL EQUIVALENT.


## 99. Machine 2 camera moved again: cap check re-centred, re-measured, and a seating step for skewed caps (2026-11-14)
- **What depends on the camera position:** the centre (all three circles: r DOST, r DOSTPLUS, the r25 brightness
  circle) and the two radii. The six brightening limits compare the cap picture with the hub, top bearing and nut
  pictures of the same part, so they were re-measured against the new pictures of those three. The R-B and brightness
  limits are colour / lighting, re-checked.
- **Old settings confirmed** on 18 pictures from the earlier position (13 already in `cap_images/`): cap rim fitted at
  (704-710, 344-352) r 97-100 (setting 707,348, r100); brass insert r 81-84 (r70 inside it); brass R-B(r70) +43.1..+44.7,
  silver R-B(r100) -13.8..-18.0, silver brightness(r25) 111-168.
- **New pictures:** 20 (11 silver, 9 brass), `cap_images/` replaced. 10 of them are caps placed skewed (pushed
  off-centre or tilted): `<MODEL>_skewed_NN`; seated ones `<MODEL>_cap_NN`. The user's labels; DOST_skewed_01 (first
  thought "unsure") measured skewed, DOSTPLUS_cap_05 (likewise) measured seated, and both confirmed by the user.
- **Machine 2 values** (`siemens.vision.cap.*`, all three properties files; Machine 1 unchanged):

  | key | was | now | measured |
  |---|---|---|---|
  | center | 707,348 | 644,340 | hub bore centre (hub recess fitted r 106-111 at 642-648, 336-344) |
  | radius.dost / .dostplus | 100 / 70 | 109 / 76 | x1.09: the cap sits nearer the camera, like the nut |
  | rb.threshold.dostplus | 15 | 35 | brass R-B(r76) 66.9+; silver 1.5 at most, no cap 4.5 at most |
  | rb.threshold.dost | -1 | 20 | silver R-B(r109) -2.2 at most (a skewed cap lets rusty flange into the circle; -1 left 1.2 margin), brass 44.4+. Bare steel without a cap now reads +0.8..+7: the brightness test rejects it |
  | silver.brightness.min | 100 | 125 | silver caps 170-238 (lighting brighter), no cap 60-83 |
  | delta.threshold hub / top / nut .dost | 40 / 30 / 28 | 64 / 50 / 50 | lowest with a cap 84.5 / 65.0 / 68.0, highest without (nut / washer pictures as either model) 44.6 / 36.3 / 32.1 |
  | delta.threshold hub / top / nut .dostplus | 43 / 30 / 20 | 61 / 42 / 37 | 74.6 / 53.8 / 52.7 vs 48.7 / 31.6 / 23.1 |

  Each limit is midway between the two. Every value holds with the centre anywhere within +-10 px of 644,340, except
  the old silver R-B limit (-1), which is why it moved. Lighting is the same across the four picture sets (outer disc
  band 103-120 in each), so the brightening values are real, not a lighting change.
- **Skewed caps passed the check before:** colour, brightness and brightening ask only whether there is enough bright,
  cap-coloured metal in the circle; a skewed cap has plenty. Tried and rejected (measured on the 20 pictures):
  - fixed-centre tests (dark gap in a ring, cap centre by colour, rim edge at the seated radius): brass separates,
    silver does not - some skewed silver caps move only 3-7 px, as much as the hub's own placement on the fixture
    (bore centre over 10 hub pictures of different parts: 641-646, 334-342);
  - oval shape: does not separate;
  - the cap outline against the bore of a hub picture (`currentPictures`): needs the same part's hub picture; with
    other parts' hubs a seated cap read 9.4 px;
  - outline template matching: separates, but needs a stored picture, and all pictures are one hub per model.
- **Step 4, seating (`CheckCap.checkSeating`; on for Machine 2, `vision.cap.seating.check`):** MobileSAM, one point
  prompt at `cap.center`, three masks back (small to large), picked by radius:
  - **brass:** the cap face (`seating.brass.radius` 75-95) and the bore round it (`seating.bore.radius` 104-120), both
    from the cap picture; cap centre to bore centre <= `seating.brass.offset.max` (9). Seated 0.8-1.7 px, skewed
    16.0-17.6;
  - **silver:** the cap outline (`seating.silver.radius` 95-112), then its top face (box = outline centre +-
    `seating.face.box.scale` 0.8 x its radius, point at that centre, the smallest mask). A tilted cup keeps its body in
    the bore but its top slides over and the side wall shows on one side. Top face centre to outline centre <=
    `seating.silver.face.offset.max` (9). Seated 1.6-4.3 px, skewed 14.4-20.3.
  Both are self-referenced (no fixed centre, no other picture), so the hub's placement on the fixture does not matter.
  Run only when the right cap is found and the brightening passes; no mask found = NOT OK. The marked picture shows
  the bore (blue), the cap (green / red), the top face (yellow) and "CAP: NOT SEATED" with the offset. Logged.
  MobileSAM through `ModelManager` (shared model, one `SamPredictor` per thread, as the nut detector); about 1.3 s on
  the CPU here, less on the GPU. No extra warm-up: the worker's warm-up already runs the model through the hub check.
- **The hub picture is not used by the seating step** (user's question). It is kept for the cap check's brightening
  step in `ImageProcessor.currentPictures` (camera server, in memory), for all later checks of the component, and
  set to None by `ImageProcessor.endOfCycle()` - on the IOServer's end-of-cycle message (cycle completed or aborted)
  and at the knuckle check of the next component. Unchanged.
- **Tool:** `test/SAMSegmentation_CapSeating.py`, derived from `SAMSegmentation_v0.996` (the user's): MobileSAM cap
  and bore segmentation and the two seating measures, with editable limits; tkinter window (cap pictures, optional hub
  pictures of the same parts, CSV of a folder) or `--batch <folder> [--hub ...] [--csv] [--save]`. No YOLO needed.
- **Checked:** `DryRun_Cap_Check.py` (both folders; now also skewed rows, each NOT OK with seating on and OK with it
  off, and the seating offset printed), with the real MobileSAM model: Machine 2 - 10 seated caps OK, 10 caps as the
  other model NOT OK, 10 skewed caps NOT OK only through seating, 30 no-cap rows NOT OK: all as expected. Machine 1
  (seating off) - 13 caps identified, 13 wrong-model NOT OK, as before. DryRun_Vision_Check_Timeout 22/22. Verifier
  reference 8a38061 for CheckCap; both copies ALL EQUIVALENT.
- **To check on the laptop:** `DryRun_Cap_Check.py` (ModelManager loads MobileSAM and YOLO); the seating step's time
  on the GPU in the camera server log.
- **Cap check window (follow-up, user):** new `test/machine2_dryrun/DryRun_Cap_Check_UI.py` (and the machine1 copy).
  - **What it does:** choose a folder or one picture, the component type (DOST / DOSTPLUS, or from the file name) and
    the seconds between pictures (1 - 10, 3 at start); type and seconds can be changed during the run (they apply from
    the next picture); Stop. Each picture goes through the real `CheckCap` with this machine's settings; the window
    shows the check's own marked picture (cap found, comparisons, seating outlines and offset) with a banner CAP OK /
    CAP NOT OK and the check's reason (no cap, wrong cap, comparisons failed, cap not seated); counts at the end;
    optional saving to `<folder>/cap_check_results/`.
  - **Earlier pictures:** the first hub, top bearing and nut picture of the component type from this machine's dry-run
    folders, or from a folder chosen with "Earlier pictures..." (Machine 1 has none in the repo: NOT OK, said so).
  - **MobileSAM:** loaded in a background thread when the window opens (seating on); the loading thread only sets a
    flag, the window polls it (Tk may only be used from its own thread). `--sam <mobile_sam.pt>` loads the model
    directly instead of through ModelManager.
  - **Checked:** driven under Xvfb with the real MobileSAM model, 14/14 on Machine 2: model loaded in the background;
    the 20 pictures (type from the file name) - 10 seated OK, 10 skewed NOT OK "not seated" with the offset; seconds
    set to 10 during the run holds the next picture; brass caps checked as DOST: wrong cap; type changed during the run
    applies from the next picture; a single picture, Stop part-way, an unreadable file, earlier pictures missing;
    saving. Machine 1 copy: allenbradley settings, seating off, no model loaded.
- **No text on the picture; times in the window; a cap pushed off the bore (follow-up, user, 2026-11-14):**
  - **Picture for the frontend:** `CheckCap._create_annotated_image` now draws shapes only - the comparison circle
    (green OK / red NOT OK), the brightness circle, and with seating the bore (blue), the cap (green / red) and a
    silver cap's top face (yellow). The text it wrote before (CAP: PRESENT / NOT DETECTED, cap found / needed, R-B,
    brightness, the three brightening values - all from the original code - and the seating line) is gone. Every
    value goes to one log line per check: "CheckCap summary: OK / NOT OK - model, cap found, R-B r76 / r109,
    brightness r25, brightening hub/top/nut, seating: ...".
  - **Window:** no banner or text on the picture (it shows exactly what the frontend gets). Each picture's line in the
    list gives the verdict, the time the check took and the reason; the check's summary line follows it; both are also
    printed in the terminal. Horizontal scrollbar for the long lines.
  - **Picture flagged as a correct cap by the user (silver cap pushed right off the bore, now
    `cap_images/DOST_skewed_07.jpg`):** with the committed settings it is NOT OK twice over - step 1 finds no cap (the
    cap's outline centre is at 706,342, 62 px from the bore, so the r25 circle sees the dark bore: brightness 109 <
    125) and seating measures the top face 16.5 px off the outline (> 9). With the old Machine 2 settings (centre
    707,348, no seating) the cap sits right in the check's circles and passes: most likely what the user ran.
  - **Guard added anyway (it was rejected as "no cap", not as "not seated"):** `seating.silver.outline.offset.max`
    (siemens 20, allenbradley 0 = not checked): a silver cap's outline centre within 20 px of `cap.center`. Seated
    caps 7.4-8.6 (the tall cap's top is seen a little above the bore; the hub's placement on the fixture moves it about
    +/-4 more), skewed 6.4-62.0 - catches DOST_skewed_05 (33.4) and _07 (62.0) whatever the face test says.
  - **Other checks that write text on the frontend's picture (not changed; asked):** hub (a model-mismatch line and the
    segmenter's hole count), bunk (hole count), the camera server's "NOT OK: <reason>" on timeouts / stalled camera.
  - **Checked:** DryRun_Cap_Check Machine 2 all as expected (skewed rows now must be NOT OK; the result with seating
    off is shown as information - DOST_skewed_07 is rejected before seating); Machine 1 as before; the window 15/15
    (new: every line has the time and a values line follows); the marked pictures carry no text. Verifier reference
    15bb871 for CheckCap; both copies ALL EQUIVALENT.
- **Is MobileSAM really used? (user: the check is fast on the laptop):** new `test/machine2_dryrun/Diag_Cap_Seating.py`
  - seating on / centre, CUDA and GPU, the SAM model's device, then per picture the embedding time, the seating time,
  the mask radii and the verdict, through ModelManager. Here (CPU): embedding 0.94-1.28 s, seating 1.05-1.35 s,
  verdicts as expected (DOST_cap_01 4.3 / 7.9, DOST_skewed_03 16.1, DOSTPLUS_cap_01 1.2, DOSTPLUS_skewed_02 16.3).
  On a GPU the embedding should take tens of ms. MobileSAM is not run when the cap already failed steps 1-3 (about
  0.01 s), or when seating is off (old settings).

## 100. Machine 2 camera moved again: bung checks (states 13 and 24) re-centred; seating to follow (2026-11-14)
- **Centre:** `siemens.vision.bunk.outer.disc.center` 715,341 -> **646,328** (all three properties files; Machine 1
  unchanged). Measured with MobileSAM on the 13 Nov pictures: seated bung top face DOST 646,325, DOSTPLUS 647,330 (old
  pictures, 3 Nov: about 711-716, 335-344). Holes 54-57 px from the face centre, inside the r110 search disc. Other
  bunk settings unchanged (user: the holes must be found by MobileSAM as they are). Verifier ALL EQUIVALENT.
- **Pictures:** the 60 pictures of 13 Nov in `test/machine2_dryrun/bunk_images_20261113/`, named as uploaded: A01-A20,
  B01-B20, C01-C20 (DOST / DOSTPLUS, seated and not seated, 3 with a hand in the frame). `bunk_images/` (4 Nov) kept
  until the new set is labelled.
- **DryRun_Bunk_Check_UI** (both folders): default folder `bunk_images_20261113` (Folder... to choose another); model
  "Both" (each picture checked as DOST and as DOSTPLUS) added and the default; no text on the picture (green / red
  border, disc, search circle and holes only); per picture and model a terminal line
  `HOLES <picture> as <model> <n> holes OK / NOT OK <time> <spacing, centre offset, gamma>` and the same in the list
  (scrollable). Checked under Xvfb with real MobileSAM and YOLO stubbed (no YOLO weights here): window, default folder,
  both models, terminal lines. The hole counts need YOLO: the user runs it on the laptop.
- **Hole counts on the laptop (user's run, all 60 as DOST and as DOSTPLUS):** DOST 4 holes OK on A01, A07-A16 (seated)
  and B06, B09 (not seated); DOSTPLUS 2 holes OK on A03, A04, B11-B20 (seated) and 16 not seated (A05, A06, A17, C03,
  C05-C07, C09-C11, C13-C15, C17-C19); no pattern for either model on the rest (all not seated or a hand in the frame).
  So the hole check alone passes 18 bungs that are not seated.
- **Seating step (user: holes first, then MobileSAM; `BunkSegmenter.check_bung_seating`, called by CheckBunk and
  CheckNoCapBunk only when the hole pattern passed):**
  - **A. hole-pattern centre** (from the hole step) to the model's seated position
    (`seating.seated.centre.dost` 646,325 / `.dostplus` 647,330) <= `seating.hole.centre.offset.max` (15 px). On the 41
    pictures that pass the hole check: seated 1.0-9.4, not seated 21.2-79.6;
  - **B. top-face mask** (MobileSAM, one point prompt at the hole-pattern centre; the smallest mask of radius
    `seating.face.radius.min/max` 100-145): roundness (minor / major axis of the fitted ellipse) >=
    `seating.roundness.min.dost` 0.952 / `.dostplus` 0.96. Seated DOST 0.956-0.978, DOSTPLUS 0.978-0.999; not seated
    DOST 0.889-0.948, DOSTPLUS 0.825-0.944. A tilted or lifted bung shows its side wall, so the mask is oval.
  - Seated = A and B; each alone separates the 41. Not seated = NOT OK; no face mask = NOT OK; error = NOT OK. Logged
    ("Seating: SEATED / NOT SEATED - ..." and one summary line per check); the face outline drawn green / red.
  - `vision.bunk.seating.check` siemens True, allenbradley False (Machine 1 unchanged; its seated centres = defaults).
  - Margins: A about 6 px each side (fixed centre: the hub's placement on the fixture moves about +/-4 px); B wide for
    DOSTPLUS, thin for DOST (B09 0.948 vs 0.956), but B09 fails A at 47.5 px.
  - Checked: `check_bung_seating` on the 41 pictures with the user's hole centres: 41/41 as labelled. The whole check
    through the window under Xvfb (real MobileSAM; YOLO replaced by circle proposals, which finds fewer holes than
    YOLO): hole pattern -> seating -> NOT SEATED -> NOT OK (C05), "not run" after a failed hole check.
- **No text on the hole picture:** `paint_bunk_masks_on_image` no longer writes "COUNT: n" (the count is logged).
- **DryRun_Bunk_Check_UI:** after each `HOLES` line a `SEATING` line (SEATED / NOT SEATED with both values, or "not run
  (hole check failed)") and a `RESULT` line (OK / NOT OK, time); the status line shows the seating settings.
- Verifier: reference 04e5e6e for BunkSegmenter (the earlier reference f399967 plus this change; 6 manifest lines
  moved); both copies ALL EQUIVALENT. DryRun_Vision_Check_Timeout 22/22.
- **Labels used** (by eye; the hole step agrees): seated A01, A03, A04, A07-A16, B11-B20; hand A02, B04, C02; the rest
  not seated.

## 101. Positions relative to the hub centre; knuckle segmentation study with MobileSAM (2026-11-14)

First steps towards vision checks that do not depend on the camera staying exactly where it is.

- **`docs/vision_positions_machine2.md`**: every Machine 2 vision position and size (knuckle regions and polygons, hub
  centre check, top bearing, nut / washer, no-bunk, bung incl. seated positions, cap) in 1280 x 720 picture coordinates
  and as offsets from the hub segmentation centre (645, 334), with the measured feature positions behind them and the
  zoom of the 13 Nov move. Written from the properties file, not by hand.
- **`test/machine2_dryrun/Study_Knuckle_Segmentation.py`** (study tool, nothing in production changes): runs the
  MobileSAM automatic mask generator over a knuckle picture and names the parts among the masks by size, shape and
  position relative to the spindle: SPINDLE (round, r 35-65, within 80 px of 642,360), BOSS_L / BOSS_R (the 2 machined
  areas on the upper arm: round fill >= 0.70, r 28-55, grey >= 80, 100-240 px above and 130-290 px beside the spindle),
  UPPER_ARM_L / R (dark mask touching the boss), LOWER_ARM (dark, long and bent - rotated-box fill < 0.75 -, starting
  20-150 px below the spindle and reaching more than 220 px below it; holes filled) and the side from the lower arm.
- **Results on the 20 knuckle pictures (grid 24 x 24)**: on all 19 knuckles both bosses and the whole lower arm found,
  side right on all 19 (11 LHS / 8 RHS); EMPTY_01: nothing (the fixture's locating pin is not taken for the spindle).
  Upper arm on the side without the long arm sometimes only a part (DOSTPLUS RHS); no rule depends on it.
  Measures: spindle (641.1-645.4, 357.1-361.0) r 48.6-53.8 (the hub centre 645,334 is 26 px higher: the hub top is
  nearer the camera); bosses from the spindle DOST (-195..-200, -160..-163) and (+195..+203, -158..-161), r 40-42;
  DOSTPLUS (-188..-190, -156..-160) and (+195..+196, -151..-155), r 45-48. Boss-to-boss distance DOST LHS 393.5-395.4,
  DOST RHS 398.7-399.7, DOSTPLUS 383.6-387.7 px; angle +0.04..+1.23 deg. Lower arm about 19,000-20,000 px, tip from
  the spindle LHS (-216, +342), RHS (+200..+205, +347..+351).
- One point prompt on a boss gives the boss as the middle of SAM's 3 masks (DOST_RHS_01: r 45, fill 0.82) in about 1 s
  on the CPU - a cheap way for a check to find the bosses once the study has settled the rules.
- Speed: the generator takes about 90 s per picture at 24 x 24 on the sandbox CPU (a few seconds on the laptop GPU);
  16 x 16 is too coarse (misses the spindle and a boss).
- **Fast method (follow-up, user: the automatic generator took 11 s per picture on the laptop; aim under 2 s):** the
  generator runs 1,024 point prompts (32 x 32) through the decoder, then filters them. Now the default method,
  "Prompted (fast)": one image embedding; the spindle by one point at its expected place; then, placed relative to the
  spindle found, in two batched decoder calls:
  - each **boss**: a 104 px box round its centre plus 4 points on the machined ring (26 px from the centre, on the
    diagonals); SAM's 3 candidates, the roundest that passes the boss rule kept;
  - each possible **lower arm** (ARM_L for an LHS knuckle, ARM_R for an RHS one): 4 points down the arm's centreline,
    from the knuckle body to the part resting on the bar; one mask each. On a knuckle of the other side that spot is
    bare plate / bar and the mask fails the arm rule, so the side is the one arm that passes.
  - The prompt points were picked as the common core of each part's mask over the 19 pictures (each point 20-34 px
    inside the part on every picture).
  - **First try failed on 8 bosses:** 4 points 14 px from the boss centre fell on the rim of the boss's bore, and SAM
    returned the bore or part of the face. Tried on the 19 pictures with every prompt moved +-8 px (114 per boss):
    points at 26 px 222/228, box 225/228, both with a single mask; with the 3 candidates and the rule picking, box +
    points 228/228 (lowest fill 0.83 against the 0.70 limit), box alone 228/228 (0.78), points alone 228/228 (0.73).
  - **YOLO not used:** the project's YOLOv8 (MobileSAMv2's `ObjectAwareModel.pt`) proposes boxes round any object; the
    parts' places are already known, so it would add a detector pass for nothing. A fallback if the fixed prompts ever
    prove too fragile.
  - **Results (sandbox CPU, no GPU):** 19/19 knuckles with spindle, both bosses, the lower arm and the right side; EMPTY_01
    nothing. 0.99-1.42 s per picture (embedding 0.74-1.01 s, prompts 0.36-0.52 s); on the laptop GPU the embedding
    should be far quicker. Arm prompts moved +-10 px: 80/80. Measures as with the automatic method (bosses from the
    spindle DOST (-199..-195, -162..-160) / (+196..+203, -161..-159), DOSTPLUS (-190..-188, -161..-158) /
    (+193..+196, -154..-151); lower arm 18,901-19,889 px).
- **More boss points, more measures per mask (follow-up, user: 16 points per boss; area, centroid, perimeter and what
  else helps place the later checks):**
  - **Boss points option** 4 / 8 / 16 (window "Boss points", batch `--boss-points`); 8 / 16 sit evenly on a ring 24 px
    from the boss centre (it stays 9.8 px or more inside every boss of the 19 pictures; at 30 px only 3.6 px). Tested
    with every prompt moved +-8 px (266 boss masks each, box kept, best of 3): 4 points (26 px, diagonals) 266/266,
    lowest fill 0.83, centroid moving median 0.34 / worst 1.39 px; 16 points (24 px) 258/266, 0.73, 0.72 / 1.82 px;
    8 + 8 (22 / 26 px) 258/266, 0.74, 0.75 / 1.85 px. With more points a shifted prompt puts several on the bore rim or
    off the boss and SAM tries to cover them all. **Default stays 4.** Unshifted, all three find both bosses on 19/19
    and the type on 19/19.
  - **Per mask** (one MASK line each, CSV columns): area, centroid (and from the spindle), perimeter, equivalent
    diameter, circularity 4 pi A / P^2, enclosing circle (centre, r, fill), fitted ellipse (centre, axes, ratio, angle),
    holes inside the mask (count, area), box, rotated-box fill, grey, SAM score.
  - **Per picture:** TYPE from boss area x1000 / distance^2 (limit 34.5; DOST 29.5-31.2, DOSTPLUS 38.0-41.1 - zoom does
    not change it), cross-checked by boss radius / distance (limit 0.111; DOST 0.104-0.107, DOSTPLUS 0.116-0.122);
    REFERENCE spindle - the mask when its radius is 46-52 (48.1-49.9 measured), else from the boss midpoint minus the
    type / side's usual midpoint offset (DOST_LHS_02, bearing on the spindle, mask r 53.9: now 642.9, 358.9 against the
    usual 643.2, 359.8); SHIFT from the reference spindle 642.2, 359.1 (mean of the user's 27 knuckles without a bearing,
    13 Nov) and the hub centre it implies (645, 334 + shift); zoom = boss distance / the type and side's usual, rotation
    = boss angle - its usual (DOST LHS 393.9 px / 0.06 deg, DOST RHS 398.5 / 0.10, DOSTPLUS LHS 383.8 / 1.25,
    DOSTPLUS RHS 383.7 / 0.63); BOSS_MID. Type 19/19.
  - Not yet confirmed: that the hub centre stays at a fixed offset from the spindle - needs the knuckle and hub pictures
    of the same components.
- **Bosses from a 4 x 4 grid of 2-point prompts (follow-up, user: account for small camera movements):** new
  `test/machine2_dryrun/Study_Boss_Grid_Masks.py`.
  - Per boss a 4 x 4 grid at the 2 x 2 prompt's spacing (36.8 px), centred on the boss's usual place (444,198) /
    (842,198) (or from the spindle, `--centre spindle`): it covers about +-55 px. Each of the 24 neighbouring pairs
    (12 across, 12 down) is one 2-point prompt, 3 masks each: 72 masks per boss, one embedding, one batched decoder call
    per boss.
  - **Boss signature** (radial grey profile from a boss centre, DOST_LHS_01): 5-48 inside 14 px (bore with the fixture's
    pin), 129-140 at 20-32 px (machined ring), about 108 at the boss's edge; the empty fixture has the dark seat and pin
    but no bright ring. Each mask gets its shape measures plus the DARK CORE (the dark blob inside its outline nearest
    its centre, darker than min(70, ring - 40)): centre ("pin centre"), area, radius, grey; RING grey (mask outside the
    core), contrast, concentricity (core to mask centre). Masks with IoU >= 0.85 are one group; votes = group size.
  - **Candidate:** radius 30-60, circularity >= 0.75, fill >= 0.70, ring grey >= 80, contrast >= 40, core radius 6-22,
    concentricity <= 8. **Score** = votes x circularity x fill x min(contrast,120)/120 x (1 - concentricity/20); best =
    highest.
  - **Results (20 pictures, sandbox CPU):** every knuckle boss found, each as exactly one candidate group (5-9 votes of
    72); EMPTY_01 none (its masks are the dark seats, ring grey 46-49). Type from area x1000 / pin distance^2: DOST
    29.0-30.5, DOSTPLUS 36.8-39.4 (limit 34.5): 19/19. **Pin centres are much steadier than mask centres:** DOST LHS L
    (443.7-444.0, 191.8-192.7) R (844.2-844.4, 197.1-197.3); DOST RHS L (443.7, 190.6) R (843.7-843.9, 195.3); DOSTPLUS
    LHS L (446.4-446.7, 197.2-197.5) R (840.6-840.9, 202.0-202.1); DOSTPLUS RHS L (446.3-446.4, 197.3-197.5) R
    (840.8-841.0, 202.6-202.7). Pin-to-pin distance DOST 400.1-400.6, DOSTPLUS 394.2-394.7 px; angle +0.64..+0.79 deg
    for all four type / side groups (the mask-centre angle varied 0.0-1.3 deg by group) - a rotation reference that does
    not depend on the type. Where a boss mask leaks along the arm (DOSTPLUS RHS right boss, r 48-51) the pin centre is
    unaffected.
  - Output: `boss_grid_masks.csv` (2,880 masks for the 20 pictures, every measure, group, votes, candidate / why not,
    score, best), `boss_grid_summary.csv`, `boss_grid_masks.xlsx` (xlsxwriter, already in requirements.txt).
  - Time: 7-8 s per picture on the sandbox CPU, 5.5 s of it MobileSAM's decoder on 48 prompts (embedding 0.7-1.1 s,
    measuring about 1 s); on the GPU the decoder takes milliseconds.
  - **Speed (user's laptop: 13.35 s per picture with the GPU):** per-stage times now printed. Laptop: embedding 0.02 s,
    decoder 0.37 s on cuda:0, to CPU 0.02 s, **measuring 12.40 s**, grouping 0.49 s (sandbox CPU: measuring 0.71 s).
    Most of the 144 masks are 100,000-480,000 px of background, each measured in full (contours, holes, ellipse,
    dark core). Now a mask is measured only when its area is 1,979-11,310 px (`AREA_GATE`: above pi x 60^2 its
    enclosing radius is over 60, below 0.7 x pi x 30^2 its radius is under 30 or its fill under 0.70 - so it could not
    pass the candidate rule anyway; and masks with IoU >= 0.85 have areas within 15 %, so no vote of a possible boss
    changes); the others are listed with area and box, why_not "area outside ... (not measured)". Grouping skips pairs
    whose areas differ by more than 15 %. Sandbox: measuring 0.71 -> 0.05 s; 7-20 of 72 masks per boss measured; the
    summary of all 20 pictures identical in every field. `--measure-all` keeps the old behaviour.
- **Grid bosses in the study window (follow-up, user: show the final masks - 2 bosses, spindle, the arm on the back
  rest):** `Study_Knuckle_Segmentation.py`, fast method, "Boss points" = **grid** (new default; 4 / 8 / 16 kept).
  - The bosses come from `Study_Boss_Grid_Masks.grid_bosses()` (split out of the grid program, no change to its
    results: all 20 summaries identical) on the window's own image embedding; the spindle and arm prompts are as
    before. The window opens on "Named parts": spindle, both bosses with their pin centres (red dots), the lower arm;
    the grid points as dots; no text.
  - With pins: distance and angle between the pins; type by boss area x1000 / pin distance^2 (limit 34.5; DOST
    29.0-30.5, DOSTPLUS 36.9-39.4) cross-checked by boss radius / pin distance (own limit 0.1095: DOST 0.102-0.105,
    DOSTPLUS 0.114-0.124 - the 0.111 of the box method sat 0.003 from DOSTPLUS here); the spindle fallback from the pin
    midpoint; zoom / rotation against `NOMINAL_PINS`; new **PIN_MID** line: the pin midpoint against its usual place for
    the type and side = an estimate of the camera's movement that does not depend on where the part sits.
  - `NOMINAL_PINS` (user's laptop run, 19 knuckles joined with the spindles of his study run): pin distance DOST LHS
    400.35, DOST RHS 400.17, DOSTPLUS LHS 394.26, RHS 394.64 px; angle 0.65 / 0.68 / 0.69 / 0.77 deg; pin midpoint
    from the spindle (+0.9, -164.8), (+2.6, -164.2), (+1.7, -159.5), (+1.4, -160.2) - each within +-0.5 px over its
    group; pin midpoints (644.15, 194.93), (643.75, 192.95), (643.70, 199.65), (643.62, 200.00).
  - Checked (sandbox CPU): type, side and both bosses right on 20/20 (empty: nothing); pin shift within 0.4 px of zero
    on every picture (same camera position); DOST_LHS_02 (bearing on the spindle) spindle from the pins 643.0, 359.3
    against the usual 643.2, 359.8; window under Xvfb.


## 102. Machine 2 knuckle check by MobileSAM; later checks' centres from the knuckle (camera movement) (2026-11-15)

User: the laptop run of the study window (31 pictures: 28 knuckles + 3 empty) - (a) time; (b) replace the knuckle check
with this logic for type and LHS / RHS; (c) from the bosses, the spindle and the lower arm, work out the centres of
all later checks; (d) build that into the later checks instead of the properties' centres, cleared at cycle end / abort.

- **(a) Time on the laptop GPU (study window, grid):** 0.34-0.41 s per knuckle picture (embedding 0.02-0.03 s,
  prompts 0.32-0.38 s; first picture 0.52 s; one empty fixture 1.29 s). Type and side right on all 28 knuckles.
- **`camera/KnuckleLocator.py` (new):** the study's method in production form, same rules and numbers: one embedding;
  spindle by one point; per boss the 4 x 4 grid / 24 pairs (one decoder call per boss), boss = bright machined ring
  round the dark core, best group by score, the pin = dark core centre; the two lower-arm prompts; type by boss area x
  1000 / pin distance^2 (34.5) cross-checked by boss radius / pin distance (0.1095). Uses the shared predictor
  (ModelManager). Two changes from the study: the grid is placed relative to the spindle mask (the usual picture
  place when there is none; retried at the usual place if a boss is missing), so it follows a moved camera; and the
  result carries the frame. Found in the moved-camera runs and added: a boss missing while the other was found is
  looked for again with its grid placed from the found pin by the pin vector (397, 4.8); when no mask of a boss passes,
  its masks are opened with a 21 px disc and judged again (DOST_LHS_02 with a bearing, moved 30 px + zoom 0.98: all
  right-boss masks took in a strip beside the boss - fill 0.61-0.65, radius 51-52 - and the boss was missed; only
  used when nothing passed, so the other pictures' bosses are unchanged); the type is decided by the area ratio, the radius ratio only vetoes
  when beyond 0.1095 +- 0.0025 (DOST_RHS_03 moved: radius ratio 0.1097 against DOST's usual 0.102-0.105 - the
  enclosing circle follows one bump of the mask - while its area ratio 31.0 was clearly DOST). `draw()`: spindle, bosses (pin dots) and lower arm outlined, green OK / red NOT OK, no text.
- **`camera/VisionFrame.py` (new) - (c):** camera rotation and zoom from the pin line against `NOMINAL_PINS` of the
  type / side, translation from the pin midpoint (the pins do not move with the part: camera only); anchor = where
  the pins put the usual spindle of that type / side. The spindle mask is the cross-check (within +-1 px on the
  pictures; over 6 px apart is logged). First version anchored on the mask: on the moved pictures its centre moved
  2-3.5 px with the zoom / re-sampling (one centre 3.46 px off), while the pin midpoint stays within 0.5 px; the
  knuckle sits on the pins, so it cannot be off them. Every later centre: `anchor + zoom * R(rotation) * (reference - (642.2, 359.1))`. Reference centres
  (`CENTRES`, in the code): hub segmentation (645, 334), hub centre check (644, 340), top bearing (642, 345), nut / washer
  and no-bunk nut (644, 344), bung disc (646, 328), bung seated DOST (646, 325) / DOSTPLUS (647, 330), cap (644, 340) -
  the 13 Nov values (docs/vision_positions_machine2.md, new section with the offsets from the spindle). Radii unchanged.
- **(d) Into the checks:** `VisionFrame.applyToChecks(frame)` writes the placed centres into the checks' constants
  (HubAndBearingSegmenter incl. its single instance's annular centre, CheckHubAndBottomBearing, CheckTopBearing,
  CheckNutAndPlateWasher, CheckNoBunk, BunkSegmenter, CheckCap); with no frame it puts back the properties values
  (the old behaviour - Machine 1, method regions, or the camera server restarted mid-cycle). Called at the start of
  every assessment, when the worker process restores the cycle state and when the camera server applies a check's
  changes (so the bearing-ring guard thread uses it too).
  - `ImageProcessor.visionFrame` is cycle state (`_STATE_ATTRIBUTES`: sent to and back from the worker process), set by
    the knuckle check only with OK. **Cleared** in `endOfCycle()` (the IOServer's end of cycle: completed **or aborted**,
    and a new QR code at the knuckle check), at a knuckle retake, and at the start of every knuckle check.
- **(b) `CheckKnuckle`:** new `vision.knuckle.method` - `segmentation` for Machine 2 (`siemens.vision.knuckle.method`
  in the three properties files), `regions` by default (Machine 1 unchanged). `checkKnuckle()` dispatches;
  `checkKnuckleBySegmentation()` is OK when both bosses with pins, the lower arm and a clear type are found, type and
  side match the QR code and the camera movement is within `vision.knuckle.frame.max.shift` 40 px / `.max.rotation`
  3 deg / `.max.zoom.change` 0.05 (else NOT OK "... - set the camera again"); a missing spindle mask alone does not
  fail it. The grey-level method is kept whole as `checkKnuckleByRegions()` (renamed, not commented out: Machine 1 runs
  the same code and uses it). In the camera server the knuckle state runs once on one picture (no 3-picture vote).
- **Results (`DryRun_Knuckle_Frame.py`, sandbox CPU, 161/161):**
  - Every knuckle picture OK with its own QR code only (19/19); the empty fixture NOT OK with all four.
  - Pictures as taken: camera shift 0.0-0.7 px, rotation within 0.12 deg, scale 0.9997-1.0007; every later centre
    within 4 px of its properties value (DOST RHS knuckles sit 2-3 px higher, their centres with them); spindle mask
    within 0.98 px of where the pins put the spindle on the 18 pictures without a bearing; DOST_LHS_02 (bearing on
    the spindle, mask r 53.9) placed from the pins.
  - Camera moved (all 19 knuckles x 5 moves: (+25, -15), (-20, +20) + 1.5 deg, -1 deg + zoom 1.02, (+30, +10) + 0.8
    deg + zoom 0.98, (-12, -25) - 2 deg + zoom 1.01): all OK, type and side right, every centre within 1.16 px of where
    the move takes it. 55 px: NOT OK "camera moved 55.0 px (limit 40) - set the camera again".
  - The centres in the check modules (hub segmenter instance too), the properties values back without a frame, the
    frame in the state sent to the worker and applied when restored, dropped at the end of the cycle and at a retake.
  - Time: 7-9 s per check on the sandbox CPU; on the laptop GPU the same prompts took 0.34-0.41 s (point (a)).
- **Dry runs:** new `test/machine2_dryrun/DryRun_Knuckle_Frame.py` (above); `DryRun_Vision_Check_Timeout.py`
  part K runs the real pipeline with segmentation when the weights are there (frame kept after OK, dropped at the end
  of the cycle), regions otherwise; `DryRun_CheckKnuckle_Images.py` and `DryRun_Knuckle_Check_UI.py` pin
  `K.METHOD = "regions"` (they test that method: 155/155, Machine 1 copy 30/30).
- **Verifier:** CheckKnuckle reference commit 6846f37 (the previous reference plus this logic, hard-coded values) in
  `baseline_per_file` of both manifests; `allenbradley.vision.knuckle.method=regions` and the `frame.max.*` keys
  added for Machine 1. Both machines: ALL EQUIVALENT.
- **Per-type displacement of the later centres (follow-up, user):** the user's view was that the DOST bosses' machined
  faces sit higher (nearer the camera) than the DOSTPLUS ones and the rest on one plane. The pins agree with the first
  part: DOST pin distance 1.5 % larger, so about 2.4 px of the DOST pins' 5-7 px higher position is perspective; the
  per-type `NOMINAL_PINS` already hold it, and with the camera moved the plane difference costs about 0.5 px for a
  30 px shift. The later pictures (same parts through the stations, user) do not agree with the second part:
  - Measured here per picture (top bearing and nut by the ring finder, hub inner bore and caps by a MobileSAM point
    prompt): DOST features 4-5 px higher than DOSTPLUS. Per part: hub inner bore DOSTPLUS y 338.7-340.0; DOST part A
    333.0-334.3, part B 337.1-337.5. Top bearing DOSTPLUS 342.6-344.0; DOST A 335.2-336.4, B 340.3-341.4. Nut DOSTPLUS
    337.9-338.7; DOST A 330.6-331.4, B 337.1-337.4. The two DOST parts sat 3-7 px apart at every station.
  - `VisionFrame.CENTRES` now holds a reference centre per type, and the type's mean spindle (`TYPE_SPINDLE`: DOST
    (642.35, 358.45), DOSTPLUS (642.2, 359.7)); each centre's displacement from the spindle is per type
    (`displacement()`), placed as anchor + zoom * R(rotation) * displacement. Type means over parts (each part weighted
    equally): DOST - DOSTPLUS hub (+0.6, -3.9), bearing (+0.2, -4.9), nut (+0.9, -4.4), bung (-1.0, -5.0). Each earlier
    single centre is split by half its feature's difference (DOST = centre + diff / 2, DOSTPLUS = centre - diff / 2;
    hub difference for the hub centre check and the cap); the seated bung positions were already per type. Values and
    displacements: docs/vision_positions_machine2.md.
  - Effect on the margins: hub, bearing and nut search centres now about 2 px nearer each type's own features; the
    DOST silver cap outline 5.8 px from the cap centre (was 7.7; limit 20), the DOSTPLUS brass bore 2.4 px.
  - Open: whether the 3-7 px between the two DOST parts was their knuckles' spindles (side) - followed by the frame -
    or the hub's own seating; needs knuckle + hub + cap pictures of the same components.
  - `DryRun_Knuckle_Frame.py` (part B now against each type's reference centres, within 2.5 px): 161/161; camera
    moved: every centre within 1.17 px of where the move takes it.

## 103. docs/ brought in line with the code (2026-11-15)

Checked against the code of `b371775` (CHANGES 102); no code changed.

- **HLD:** QR scanner on serial (COM3, 38400), not USB; start-up order (Redis check, backup, mode, login, then the
  servers); `io2camera.q` END_OF_CYCLE, `alarm.q` from the camera server, `publishTags` / `receiveTags`; bypass in the
  cycle; new §5a vision checks (method per state, pictures used, the knuckle frame, where the reference values live);
  failure table: PLC restart rule (last PLC-raised tag, then the side), camera moved, no frame in the cycle, worker not
  ready; configuration (vision keys through VisionConfig, live reload of vision values not in the exe, the build
  copies); logs (per-server levels in the properties); tests.
- **LLD:** MainProgram steps as coded; IOServer threads (tag dump listener, the reset scheduler and its delays,
  `UpdateTagsToDefaultProcessor` unused); state table reset times (2.0 s torques, 1.5 s presses); PLC restart rule;
  `abortCycle` as coded (order, `pcAbort`, `PC_PLC_CycleAborted` True / False, callers, restarted IOServer);
  `resetForNextCycle`; camera server: `prepareForKnuckleCheck`, `assessPictures` per state (single picture / 2-of-3 vote
  with 6 threads and a 5 s wait), the cycle state sent to the worker, the knuckle frame (KnuckleLocator, VisionFrame),
  the bearing-ring guard, the seating steps; `PLCDataIO` contract; MockPLC is for the Mock-PLC window, not TEST mode;
  message fields (END_OF_CYCLE, emergency, fe2db); configuration keys (Siemens PLC address, optional tags, vision
  switches); tests (knuckle frame, cap, guard, windows, study tools; KnuckleLocator / VisionFrame not in the verifier's
  manifest).
- **PLC_Interface_Spec:** `PC_PLC_CycleAborted` (named, optional, no address yet; True after a PC-side abort, False at
  the next accepted QR code); rotation counts 1 to 4, settings per model; §5.4 reset times and the emergency reset;
  §5.5 abort sources and order (frontend abort only if the tag can be written; a restarted IOServer does not abort the
  PLC's cycle); §5.6 restart rule (last PLC-raised tag first, side as the fallback; DB500 and DB101 not retentive).
- **plant-pc-verification:** new step 6, the knuckle check by segmentation and its frame on the machine.
- **README (docs/):** `vision_positions_machine2.md` and the figures listed. **machine2-plant-pc-setup:** the note on
  restart detection corrected (the last-tag rule still holds; the side is the fallback).
- **test/machine*_dryrun/README.md:** the torque / press reset delays and the frontend abort (F8) described as coded.
- Checked here (Linux, Python 3.13, CPU, MobileSAM weights): `DryRun_Knuckle_Frame.py --quick` 86/86; vision verifier
  ALL EQUIVALENT (216 parameters); `DryRun_QRCode_Validation` 9/9; fault scenarios (Siemens) 190/190.

## 104. Passwords in the properties; bearing-ring guard NOT OK when it cannot decide; trial-picture notice; no text on pictures; camera server state across a restart; all-checks window (2026-11-15)

- **Passwords (user):** `frontend.abort.password` (Ctrl+Alt+Z), `machine.settings.password` (Save in Machine
  Settings) and `machine.settings.bypass.password` (asked in addition when "Bypass Component Press and Second Tightening"
  changes; wrong: "Wrong bypass password: machine settings not changed", nothing saved). All three `admin123`, in all
  three properties files; read at each use (`getFrontendAbortPassword`, `getMachineSettingsPassword`,
  `getMachineSettingsBypassPassword`). The fixed `FE_ABORT_PASSWORD` / `MACHINE_SETTINGS_PASSWORD` constants are gone.
- **Bearing-ring guard:** when it cannot decide - the ring finder fails, or no answer `GUARD_MAX_WAIT_SECS` (5 s) after
  the worker's verdict - the nut / washer check is NOT OK with the reason ("bearing-ring guard: ring finder failed
  (...)" / "... no answer in 5 s after the check"), logged as an error. Before, the guard was not applied and the
  verdict stood. Same inside the check (thread mode, dry-run windows): a failing ring finder returns NOT OK.
- **Start-up notice (user):** with `save.all.pictures.because.its.in.trial.mode=True`, after "Running application in
  <mode> mode", "Images will ALSO be saved in <absolute picture.saving.dir.for.mode.trial>" for 2 s
  (`flashSimpleMessage`, the DarkBlue15 style of the other start-up messages); also printed.
- **No text on the frontend's pictures (user):** the hub check's "COUNT: n" (now a log line "Hub check: n holes in the
  winning group") and "MISMATCH: Expected ..., Got ..." (already logged) removed; the camera server's NOT OK picture
  for a check without a verdict (timeout, stalled camera, worker starting) has a red border instead of "NOT OK:
  <reason>"; the reason is logged ("Picture NOT OK: ..."), sent as the answer's reason and shown as the "check" notice.
- **Camera server state across a restart (user's design):** new `camera/CameraStateFile.py`.
  `ImageProcessor.persistState()` writes the cycle's state (QR code, model, side, top bearing ring, knuckle frame, the
  earlier pictures as PNG - each encoded once) to `<project root>/Temp/camera_server_state.pkl` after every check and
  at every end of cycle (then empty); temporary file + rename. `startCameraServer(mode, stateFile=None)` /
  `CameraServer(mode, stateFile)`: MainProgram starts the camera server with None (a new run starts empty and writes an
  empty state); `ProcessSupervisor.add(..., restartArgs=)` restarts it with the file's path, and it takes the state back
  (`loadPersistedState`, which applies the frame to the checks) before it starts its threads. A file older than
  `camera.state.file.max.age.sec` (new, 3600; 0 = any age) or unreadable is not used. `Temp/` in .gitignore.
  Measured here: 0.12 s and 5.5 MB for two pictures the first time, 0.002 s for a write with no new picture.
- **New `test/machine*_dryrun/DryRun_All_Checks_UI.py` (user):** a Load button per vision check; Run All takes the
  component through every loaded check in the machine's order with the production code (`assessPictures`, the cycle's
  state in `ImageProcessor`, the knuckle frame), listing per check OK / NOT OK, time, the centre it used and its log
  lines, with the check's picture; cap / split pin as in production (2-of-3 vote) or once; "Fill from dry-run
  folders"; Save results; `--auto` without a window.
- **Verifier:** reference commit 3d91a76 (the three vision files with these logic changes, values hard-coded) for
  CheckNutAndPlateWasher, CheckHubAndBottomBearing and HubAndBearingSegmenter (now in `baseline_per_file`); both
  copies ALL EQUIVALENT.
- **Dry runs, both folders:** FE 130/130 (F12b: the bypass password, the properties' passwords); Bearing ring guard
  13/13 (G7 ring finder fault, G8 no answer, G9 inside the check; the Machine 1 copy now uses the Machine 2 pictures:
  it had none and stopped at the first picture); Process supervisor 7/7 (S6 restart arguments); Camera state file
  19/19 (new); Heartbeat / start-up 28/28 (H11 the notice); Vision check timeout 23/23 and 22/22 (R1: red border, no
  text); Camera stale frames 23/23; all-checks window: driven under Xvfb 10/10 (camera modules stood in: no torch for
  that Python), and `--auto` with the production code and real MobileSAM (no YOLO weights here: hub NOT OK as expected;
  knuckle, top bearing, nut, no bung, cap OK, with the frame's centres).
- Found while testing: `test/machine1_dryrun/DryRun_Vision_Check_Timeout.py` does not set `COSTHETA_PLC_VENDOR` itself;
  without it the live properties' vendor (siemens in the repo) is used. Run it with `COSTHETA_PLC_VENDOR=allenbradley`.

## 105. Cap check: one MobileSAM run; frame and locator reference values in the properties; vision check in the worker process only (2026-11-15)

- **Cap check (user, option a):** new `_assessCap` in `CameraProcessorServer.assessPictures` for
  `READ_TAKE_PICTURE_FOR_CHECKING_CAP`, in place of the 2-of-3 vote (6 threads, 5 s wait). Steps 1-3 (which cap, right
  cap, brightening against the hub / top bearing / nut pictures; no MobileSAM) on each of the three pictures
  (`CheckCap.checkCap(..., runSeating=False)`, new parameter, default True); at least 2 of 3 must pass (all, with fewer
  than 3 pictures), else NOT OK with no MobileSAM run. Then the whole check with the seating step once, on the first
  picture that passed; its verdict is the result. Logged: "CheckCap: steps 1-3 passed on n of m" and the picture the
  seating ran on. Why: through the vote, six MobileSAM runs in parallel took 5.02 s on CPU and the cap came out NOT OK
  on the 5 s wait; one run takes 1.2-1.5 s. Split pin and the regions knuckle keep the vote.
- **Frame and locator reference values in the properties (user):** 52 keys per vendor after
  `<vendor>.vision.knuckle.frame.max.zoom.change`:
  - `vision.frame.reference.spindle`, `vision.frame.type.spindle.<dost|dostplus>`,
    `vision.frame.nominal.pins.<type>.<lhs|rhs>` (distance, angle, midpoint, spindle), `vision.frame.spindle.mask.agreement.px`,
    `vision.frame.centre.<hub.segment|hub.check|top.bearing|nut.washer|no.bunk|bunk.disc|bunk.seated|cap|...>.<dost|dostplus>`;
  - `vision.knuckle.locator.*`: spindle (expected, max shift, radii, fill), boss offsets and shape limits, grid
    spacing, lower-arm points and limits, the type rule (area ratio, radius ratio and band), pin vector, open kernel.
  `VisionFrame` reads them at use (`nominalPins()`, `typeSpindle()`, `centres()`); the module's `NOMINAL_PINS`,
  `TYPE_SPINDLE`, `CENTRES` stay as the values at import. Values unchanged (Machine 2, 13 Nov 2026).
  **allenbradley carries the same values, not measured for Machine 1:** on the Machine 1 knuckle pictures in the repo
  the pins are ~362 px apart (Machine 2: ~400), the spindle is not found and the DOSTPLUS bosses are missed. Measure
  before setting `allenbradley.vision.knuckle.method=segmentation`.
- **Reference commit** f3aa0ed (CheckCap, KnuckleLocator, VisionFrame with literal values); both manifests point to
  it; `Verify_Vision_Config_Equivalence`: ALL EQUIVALENT, 268 parameters in 11 files (both machines).
- **Vision check in the worker process only (user):** `_assessInThread`, `getVisionCheckInWorkerProcess` and
  `<vendor>.vision.check.in.worker.process` removed; `runAssessment` always uses `VisionCheckWorker`. A check stuck in
  torch is always stoppable. plant-pc-verification step 1 no longer offers the thread fallback.
- **Dry runs:** new `DryRun_Cap_Production_Path.py` (both folders; Machine 1 skips, seating off): 12/12 on Machine 2,
  1.23-1.52 s per cap check on CPU, one seating run per check, none when the cap is wrong or missing.
  `DryRun_Vision_Check_Timeout.py`: thread-mode section replaced by T1 (worker always used, no getter, no key);
  23/23 (Machine 2), 22/22 (Machine 1). `DryRun_All_Checks_UI.py`: "as in production" relabelled (three pictures).
  Guard 13/13, state file 19/19 (both); `DryRun_Knuckle_Frame.py --quick` unchanged results.

## 106. All-checks window: Process Images at the top, a pause between checks, window fits the screen (2026-11-15)

- **User:** after loading the pictures there was no button to process them. There was one ("Run All"), in a bar at the
  bottom, never on screen: the picture area was a `tk.Label` with `width=960, height=540`, which Tk counts in
  characters until the label holds a picture - the window opened 6305 x 6392 px (measured under Xvfb). Now the label
  holds a blank picture of the view's size from the start, the view is sized to what the screen leaves beside the rows
  (at most 960 x 540), and the log box takes the view's width (its default 80 characters made the window 1400 px wide
  on a 1366 px screen). Measured: 1336 x 604 on 1366 x 768, 1251 x 556 on 1280 x 720, 1699 x 808 on 1920 x 1080.
- **Controls at the top:** Fill from dry-run folders, **Process Images** (was Run All), **Seconds between checks**
  (1 - 10, 3 at start; read before each pause, so a change applies during the run), Stop (also ends a pause), Save
  results; the status line below them.
- **During the run:** loaded rows show "waiting", the running check "... checking"; when a check finishes its marked
  picture (the check's own outlines of what it found, green OK / red NOT OK, no values - what the frontend gets) and
  log lines are shown with "<check>: OK / NOT OK in n s - next check in 3 s", and stay for the pause; no pause after
  the last check. Nothing loaded: said so, nothing run.
- `runChecks(..., onStart=, gapSecs=)`; `--auto --gap <s>` (default 0). The pause is kept in a plain value updated on
  the window's thread (a Tk variable read from the check thread can hang Tk).
- **Checked:** the real checks (MobileSAM; YOLO stubbed here) with a 2 s pause: the machine's order, 2.00-2.01 s between
  a check's end and the next start, Stop during a 10 s pause ends it in 0.3 s and runs no further check, every check's
  picture carries its outlines (hub: nothing found without YOLO); the window (Python 3.12 + Tk, camera modules stood
  in) 15/15 at 1366 x 768, 1920 x 1080 and 1280 x 720 - button on screen, window within the screen, checks one after
  the other with the pause, statuses, Show, vote on / off.

## 107. CLAUDE.md; plant PC verification steps 1 and 2a recorded (2026-11-15)

- **Plant PC (user):** the checklist's step 1 (the vision check worker inside the Nuitka exe) and step 2a (check
  times on the plant PC's GPU against the 12 s timeout) were run on the Machine 2 PC and passed; response times are
  fast. Recorded in the results table of `docs/plant-pc-verification.md` (no per-check times were given) and in
  `CLAUDE.md`. Steps 3, 5 and 6 not reported yet.
- **New `CLAUDE.md`** at the repo root: what the system is, where it runs (laptop / plant PC / sandbox), the
  processes, configuration (the three properties files, vision keys, reference commits), the rules for changing code
  (no text on pictures, keep unused methods, dry-run copies in both folders, the reference-commit workflow), docs and
  history to keep in step, the plant PC verification status, open items, testing (what needs Redis / PostgreSQL /
  models, sandbox limits) and how the user likes to work.

## 108. State sync Plan A: the IOServer publishes its state, the frontend compares and logs; Plan B documented; PLC handshake v2 dropped (2026-11-15)

- **Plan A (user), `docs/state_sync.md`.** The IOServer is the master; the frontend keeps walking its own states as
  before and only logs where they differ, so that a few weeks of production show whether (and when) the two drift.
  - **IOServer:** `_onStateChange` first calls `_publishState`: the change on the new stream `io2festate.q` (one
    producer, one consumer, capped at about 500) and in the key `io:state` (latest snapshot), JSON `{pid, seq, cycleId,
    qr, old, oldName, new, newName, reason, time}`. `seq` goes up per change, also when Redis refuses it (a lost
    message shows as a gap). Reasons: `IOServer start`, `abort: <reason>` (set by `abortCycle`), `cycle completed`,
    `reset`, `NOT OK: back to the check (retake)`, `OK: next step`, `PLC request: waiting for the answer`, `next step`.
    Redis with 1 s timeouts; a failure logged once per outage. The audit line now reads
    `STATE A -> B  (seq N, published | NOT published)`; the state at start is published and audited too.
  - **Frontend:** new `StateSyncWorker` (own thread, every 250 ms): `io:state` once at start, then the stream, compared
    by the new `utils/StateSync.StateSyncMonitor` with the frontend's state. For each IOServer state the frontend
    states that agree with it (`FRONTEND_STATES_FOR`: the frontend is one step ahead while the IOServer finishes a
    handshake, and after value / press events); a difference must last longer than the new
    `frontend.state.sync.grace.sec` (5.0, all three properties files) - plus the free-rotation display time + 1 s at
    the end of a cycle. Nothing in the frontend's behaviour changes.
  - **Log lines** (all start with `STATE-SYNC`, one search finds them): `START`, `MISMATCH #n` (both states, the
    frontend states expected, cycle, QR, IOServer seq and pid, since when each side is in its state, the IOServer's
    reason), `RESOLVED #n after x s`, `GAP` (k missing), `IOSERVER-RESTART` (pid a -> b), `CYCLE <id> completed |
    aborted in <state>: in sync | n MISMATCH(ES), longest x s`, `BAD-MESSAGE`. How to read them on the plant PC:
    `docs/state_sync.md` §2 and plant-pc-verification step 7.
- **Plan B (designed, not built):** `docs/state_sync.md` §3 - the frontend takes its state from the IOServer, results
  filed by the state in their message, resync from `io:state`; decided after the Plan A logs.
- **PLC handshake v2 dropped (user):** the PLC design is fixed around the present reads, writes and timings. Part B
  removed from `docs/PLC_Interface_Spec.md` (status line, ownership note, §5.4 note and sign-off table adjusted), and
  from HLD §10, LLD, docs/README and CLAUDE.md.
- **Found while testing:** `DryRun_All_Servers_Integrated_Cycle.py` (both folders) had produced no DB row since CHANGES
  89: its stand-in for `doFinalProcessing` did not take the new `delayMsec` argument, so the record was never sent.
  Fixed (`*args, **kwargs`); it now also prints the frontend's state-sync result.
- **Checked:**
  - new `DryRun_State_Sync_Monitor.py` (both folders) 15/15: a realistic cycle and a bypassed one in sync; short
    difference nothing, long one MISMATCH once then RESOLVED; end-of-cycle allowance; GAP / RESTART; open mismatch in
    the cycle summary; table complete;
  - fault scenarios O (new, IOServer side) 11/11 Siemens and Allen-Bradley; full fault scenarios Siemens 200/201 and
    Allen-Bradley 217/218 before O1 was made independent of the stream cap (it read seq 1 from a stream that ~350 state
    changes had trimmed by then; now taken right after start), O re-run 11/11 both;
  - frontend dry run 141/141 both folders (new F14: the real frontend's worker on published states - a whole cycle in
    sync, MISMATCH / RESOLVED / CYCLE / GAP / RESTART lines, behaviour unchanged);
  - two full cycles both vendors as before; integrated all-servers cycle both vendors: DB row as in CHANGES 81 and
    `STATE-SYNC CYCLE 1 completed: in sync` with the real IOServer and frontend.

## 109. Star imports replaced by explicit imports in 44 production files, each name proven by identity (2026-11-15)

- **User:** "go through with a fine tooth comb and try it; when in doubt, keep the * imports."
- **Tool:** `test/star_imports/startool.py` (+ `dumpmod.py`), run on a copy of the repo:
  1. *before*: every production module imported (Python 3.13, else 3.12 - the same one afterwards), each global name's
     object recorded, and for each `from S import *` which names it provided, proven by identity
     (`vars(S)[n] is vars(M)[n]`), and all star sources giving that very object, in order;
  2. a static scan of production code and tests: every name each module reads (functions, classes, decorators,
     defaults, f-strings, quoted annotations, `global`), what other files take from it (`from M import x`, `M.x`
     including assignments such as tests' patches, `patch("M.x")`), and who star-imports whom;
  3. the rewrite, at the star import's own line: each name from the first star import that gave that very object;
     standard-library / third-party names from their own module (`import os`, `from redis import Redis`; identity
     checked); project classes and functions from the module that defines them, on a line after the star source's
     (modules imported in the same order as before); a name already imported explicitly from the same module is not
     repeated; a star import that provided nothing used becomes `import X  # noqa: F401 - ...` (its import-time effects
     kept);
  4. kept (doubt): a file another file still star-imports, a star import inside a block, a vision file in the
     verifier's manifest, a package `__init__`, a module that cannot be imported here, any name read at import time
     while an earlier star import gave a different object, any name exported by a star source but rebound outside
     module level;
  5. rounds: a file whose only star importers were converted is converted in the next round; three rounds, the
     fourth converted nothing.
- **Converted (44):** round 1 - MainProgram, OtherServices, camera/CameraProcessorServer, CheckBunk, CheckNoCapBunk,
  CheckSplitPinAndWasher, HandDetector, ModelManager, costhetaio/commonplc/PLCIOServer, qrcodescanner/
  QRCodeScanningServer, endpoints/ShowScreen, frontend/frontendutils/FrontEndLogger, 2 frontend/newwidgets and 15
  frontend/widgets modules, logutils/CentralLoggers, CosThetaLogger, StartMasterLoggers,
  monitorAllConnections/HeartbeatAndAlarmServer, statemachine/StateMachine, utils/ClearQueues; round 2 - camera/RTSPCam,
  frontend/CosThetaMonitorDimensions, frontend/newwidgets/CosThetaOutcomeContainer, frontend/widgets/
  CosThetaResultsContainer, CosThetaRoundButtonWithColorGradient, persistence/PostgresBackupUtility,
  utils/QRCodeHelper; round 3 - frontend/widgets/CosThetaOutcomeContainer, logutils/AbstractSlaveLogger.
- **Kept (32):** 17 still star-imported by other files (Configuration, logutils/SlaveLoggers and Logger,
  processors/GenericQueueProcessor and four processors/image modules, the frontend image / label utilities and three
  frontend widgets, camera/PhotoCamera and VideoCamera, VerifyIntegrity, frontend/SimplePopups, video/
  AssessmentResults); 4 with star imports inside `try` blocks (CheckCap, CheckNoBunk, CheckNutAndPlateWasher,
  CheckTopBearing); 2 vision files in the verifier's manifest (CheckHubAndBottomBearing, CheckKnuckle); 9 that cannot
  be imported here (legacy modules that call configuration getters which no longer exist, Windows-only, scripts that
  open windows - see CHANGES 85).
- **Caught on the way:** MainProgram uses `CosThetaConfigurator` at import (line 45) right after
  `from Configuration import *`; a later star import re-exports the same object. Taking each name from the LAST star
  import that gave it (the final value) left line 45 without it - ruff F821 found it. Rule changed to the FIRST star
  import giving that object (the later ones give the same object), with the different-object case as a doubt.
- **Found, not changed (a behaviour change, for a separate decision):** MainProgram's `printBoldRed` /
  `printBoldGreen` were, through the star chain, `utils.CosThetaFileUtils`'s plain console printers, not
  `utils.CosThetaPrintUtils`'s, which also write to the log file. So MainProgram's red / green messages - including the
  supervisor's "has died; restarting it" lines - have only ever gone to the console. The explicit imports keep that
  (`from utils.CosThetaFileUtils import printBoldGreen, printBoldRed`), now visible.
- **Checked:**
  - after the rewrite all 132 importable production modules import, and the 2,653 global names they read (plus every
    name other files take from them) are the same objects as before (fingerprints: module / function / class by
    module and qualified name, values by repr, instances by type and the modules holding the same object);
    ruff F821: no undefined name in production code or tests;
  - Siemens / Allen-Bradley: fault scenarios 201/201 and 218/218; frontend 141/141 both; integrated all-servers cycle
    both (DB row as in CHANGES 81, state sync "in sync"); two full cycles; heartbeat / start-up 28/28; logging 37/37;
    PLCDataIO 38/38; process supervisor 7/7; camera stale frames 23/23; camera state file 19/19; bearing-ring guard
    13/13; vision check timeout 23/23 and 22/22; QR validation 9/9; work-thread health 17/17; camera heartbeat 4/4;
    DB server 62/62; no record loss; DB indexes 8/8; FE -> DB pipeline (row and picture stored); endpoints 91/91;
    Mock-PLC window 17/17; Real-PLC window 13/13 and 15/15; state sync monitor 15/15; cap production path 12/12
    (Machine 1: skipped, seating off); vision verifier ALL EQUIVALENT both;
  - all-checks `--auto` with real MobileSAM (YOLO stubbed - no weights here): as before (knuckle, top bearing, nut, no
    bung, cap OK; hub NOT OK without YOLO); both bung checks: the same verdicts and log lines as the unconverted code.

## 110. Code review fixes: picture clean-up, DB retries and heartbeat, frontend counts off the GUI thread, camera end of cycle, worker back-off, state-change listener outside the lock, invalid QR notice, tidy-up (2026-11-16)

- **Review (fine-tooth comb, 2026-11-16) and Uddi's decisions.** Two findings were dropped as not possible: a QR code
  rejected by the IOServer after the frontend took it (the QR code server, IOServer, frontend and camera server use
  the same validation; the only path left is the side / skip tag write failing on the PLC, which validation does not
  cause) and an UNKNOWN model reaching the checks (`isValidQRCode` refuses UNKNOWN model, side and tonnage, so the QR
  code server never sends such a code). Everything below was asked for.
- **Picture clean-up** (`utils/PictureRetention.py`, new): MainProgram starts it in a background thread right after the
  logging server. Picture files (png, jpg, jpeg, bmp, tif, tiff, webp; any case) older than
  `picture.retention.days.trial` (30) under `picture.saving.dir.for.mode.trial` (`C:/Temp/TrialImages/`) and older than
  `picture.retention.days.main` (60) under `basefolder.for.images` `Test/` and `Production/`
  (`C:/AshokLeylandImages/...`) are deleted, all sub-folders; other files (FailedRecords JSON) stay. Age = file
  modification time. An empty folder is removed only if it was already older than the limit before the run:
  `makeFoldersForImages` makes today's empty date folder at the same time, and removing it would have broken that
  day's picture saves (found while testing the first version, which removed every empty folder). 0 turns a clean-up
  off; one "Picture clean-up:" line per folder. Keys added to the three properties files (in step).
- **DB server** (`persistence/DBServer.py`):
  - a failed insert is retried after `INSERT_RETRY_GAP_SECS` (5 s; was back to back, so a PostgreSQL blip of a second
    or two sent the record to FailedRecords);
  - the pictures are saved once per record (`savedPictureFiles`), a retry reuses the files (each attempt saved them
    again before); a picture save that fails (disk full) counts as a failed attempt, with the gap (it used to go to the
    outer handler and spin on the same record with no gap); if the FailedRecords file cannot be written either, the
    record stays and is tried again after the gap (no spin);
  - the FailedRecords JSON has `_pictureFiles` (column -> file);
  - the insert error messages said `data_table {id}` (the built-in `id` function); now the QR code;
  - heartbeat: `SELECT 1` on one kept-open connection (`Persistence.PersistentDBConnection`: reconnects once when the
    connection broke, `statement_timeout` 5 s, TCP keepalives), made by the heartbeat thread itself; was a new
    connection every 0.5 s (about 170,000 a day).
- **Frontend production counts** (`AshokLeylandFrontEnd`): `TimeDurationUpdateWorker` reads today / week / month in its
  own thread, in one query (`Persistence.getProductionCounts`, same filters as the three `getComponentsProduced*`
  functions, which stay) on a kept-open connection, and sends them with the signal. Before, the slot ran three queries
  in the GUI thread, each on a new connection: a slow PostgreSQL froze the screen up to 3 x 5 s. On a database error the
  last counts stay shown (they used to drop to 0), logged once until the counts can be read again.
- **Camera end of cycle** (`camera/CameraProcessorServer.py`): `MonitorGetPicQueue` passes the IOServer's END_OF_CYCLE
  down the same queues (`endOfCycleCommand` item; `TakePictures` passes it on without pictures) and
  `ConverterScalerAndProcessor` drops the cycle state when it reaches it - after any check queued or running. Before,
  `MonitorGetPicQueue` dropped it at once on its own thread, and a check still running then wrote the old cycle's state
  back (`applyStateChanges`) and into the state file.
- **Vision check worker back-off** (`camera/VisionCheckWorker.py`): a worker that dies before it reports ready is
  started again after 1, 2, 4 ... at most 60 s (`RESTART_BACKOFF_*`, on a timer thread, never on the check thread);
  checks meanwhile NOT OK at once ("vision check worker starting"); `failedStarts` reset when a worker gets ready; a
  restart after a worker that had been ready (timeout, crash during a check) stays immediate. Measured with a stand-in
  worker that exits at start: 178 starts in 8 s before, 4 with the pause.
- **State-change listener outside the lock** (`statemachine/StateMachine.py`): the listener (`PLCIOServer._onStateChange`:
  state publish, audit line, END_OF_CYCLE to the camera; Redis calls with 1 s timeouts plus a retry) ran inside the
  state machine's lock, so a slow Redis held up every IOServer thread changing the state. Changes are now queued under
  the lock and delivered after the thread's outermost state-changing call has released it, in order, by one thread at a
  time (a thread finding another delivering returns at once; that thread delivers its change too). Published order and
  `seq` unchanged.
- **Completed-cycle reset under `_abortLock`** (`PLCIOServer.process_current_read_state`): as the abort's reset is; and
  skipped (audited) when an abort happened while that read was being processed (`abortCount` changed - the abort has
  reset and armed CheckQRCode itself). Before, the two resets could run at the same time and CheckQRCode was armed
  twice.
- **RTSPCam**: watchdog, FFmpeg process check and reconnect-loop pacing on `time.monotonic()` (a clock change could fire
  or hide the 15 s watchdog). The manual test functions at the end of the file keep wall-clock times (reports).
- **Invalid QR code** (`QRCodeScanningServer.GetAndValidateQRCode`): an invalid scan sets `feNotice:qrcode` = "QR Code not
  valid" (10 s; `FRONTEND_NOTICE_ORDER` has `qrcode`, shown by the existing `FrontendNoticeWorker`) and sends the alarm
  signal on `alarm.q` as a vision NOT OK does (`sendAlarmSignalToHeartbeatServer`, producer QRCodeServer: the heartbeat
  server sounds the siren). The read goes on; the next valid scan clears the notice. `alarm.q` now has two producers
  (HLD and CLAUDE.md say so); only the newest message is kept, and every message means the same.
- **MainProgram printers**: `printBoldGreen` / `printBoldRed` from `utils/CosThetaPrintUtils` (were `CosThetaFileUtils`'s
  console-only printers; CHANGES 109 "found, not changed"), so MainProgram's red / green lines - supervisor restarts
  included - also reach the log file. No import loop: `CosThetaPrintUtils` imports the logger lazily inside `_mirror`,
  behind a re-entry guard; checked by importing MainProgram, `setLogProcess("mainprogram")`, printing red and green:
  both returned at once and both lines were on the `filelogging` stream.
- **Tidy-up** (pyflakes F811 / F841, 120 findings in production code; 64 left, all on purpose):
  - removed: duplicate imports (RTSPCam `datetime`, `time` x2, `cv2`, local `json` / `sys`; CheckBunk / CheckNoCapBunk
    local `time` / `Path`; VerifyIntegrity's 8 local `from Crypto import Cipher`; CosThetaImageUtils `cv2`; SendMail
    `MIMEText`; test-numba `random`; PLCIOServer local `json` and the never-called `clearQueues` import, which
    `abortCycle`'s parameter of that name shadowed); where the two imports gave different objects, the earlier, unused
    one was removed so the binding in use stays (`QtCore.Qt` vs `QtGui.Qt` in 5 widgets; `getCurrentTime` in
    CentralLoggers; `app` / `monitorWidth` / `monitorHeight` imported and set again); stylesheet names imported twice
    (checked: the same objects); dead locals (MainProgram `lastUpdateOfStartingTime`, ModelManager `head_index`,
    CosThetaImageUtils `max_line_width`, CosThetaLogger `timeTuple` / `t`, Persistence `record` / `done`, an unused
    colour choice in `CosThetaOutcomeContainer.setBackgroundAndResult`, three in the legacy Hikvision module);
    the camera server's local `profile`, which shadowed the imported `profile` decorator, renamed `ringProfile`;
  - fixed on the way: `video/ImageProcessingFunctions.showNDArray` never showed the picture (`cv2.imshow` missing);
  - kept on purpose: the four `getResultImage` / `getImage` definitions in the outcome containers are `@multimethod`
    overloads, not duplicates (marked `noqa`); `gfe = AshokLeylandFrontEnd(...)` and `app = ImageProcessingGUI(root)`
    keep their windows alive (marked); RedisUtils' 24 (`timeOfMessage = newData[...]` before the `xdel` acts as a check
    that the message has a time; `timeTaken` feeds commented timing prints); values read only by commented-out debug
    lines (Persistence `rows`, `indexes`; MainProgram `value`; Hikvision `tz` / `year` / `hour`, `streamSetup1`);
    GenericImageProcessor's second star import (it may rebind names two other star imports overwrote - when in
    doubt, keep); the 4 vision files in the verifier's manifest (CheckTopBearing, HexagonNutDetector,
    HubAndBearingSegmenter, CheckNutAndPlateWasher: 22 dead locals, mostly `*_text` left from when values were drawn
    on pictures) - changing them needs the reference-commit procedure for no effect.
  - checked: every touched module imports (Python 3.13; frontend on 3.12; MainProgram on 3.12 with Tk), except those
    that never import in this sandbox (VerifyIntegrity: winreg; ImageProcessingGUI, SendMail, test-numba,
    non_PTZ_Hikvision: modules not installed here) - compiled; pyflakes undefined names unchanged in every touched file.
- **Dry runs added / extended** (both folders): `DryRun_Picture_Retention.py` (P1-P6), `DryRun_Camera_End_Of_Cycle_Order.py`
  (E1-E4), `DryRun_Vision_Worker_Backoff.py` (B1-B3); `DryRun_DBServer_No_Record_Loss.py` (b: gaps, pictures once,
  picture files in the JSON; d: one failure then success; e: heartbeat connection); `DryRun_FEServer_Cycles.py` F7b
  (counts in the worker thread; a 3 s database read stalls the GUI thread 0.02 s; last counts kept);
  `DryRun_QRCode_Validation.py` V4.
- **Caught while testing**: `DryRun_Work_Thread_Health` D1-D3 failed - it builds `DataPersistence` without `__init__`,
  so the heartbeat connection attribute did not exist and `sendHeartbeat` sent nothing; the connection is now made by
  the heartbeat thread itself (it owns it), 17/17 again.
- **Run** (sandbox, Redis + PostgreSQL; Siemens unless said):
  - new / extended: picture retention 10/10 (both folders); QR validation 13/13 (both); camera end-of-cycle order 6/6
    (both); worker back-off 6/6 (both); DB no record loss: a, b, c, d, e all pass (both; gaps 5.0-5.1 s, 3 attempts,
    9 picture files in the JSON, all on disk, one save per record); frontend 145/145 (both; F7b: longest GUI-thread
    stall 0.021 s during a 3 s database read);
  - fault scenarios 201/201, Allen-Bradley 218/218 (`COSTHETA_PLC_VENDOR=allenbradley`; the Machine 1 copy runs
    Siemens without it); two full cycles (28 states each); integrated all-servers cycle both vendors (DB row as before,
    state sync "in sync"); PLCDataIO 38/38; state sync monitor 15/15; camera state file 19/19; camera stale frames
    23/23; vision check timeout 23/23, Allen-Bradley 22/22; bearing-ring guard 13/13; cap production path 12/12;
    knuckle frame `--quick` 86/86 (run again after a run that overlapped a 15 s `git stash` of the working tree);
    work-thread health 17/17 (both, after the fix above); process supervisor 7/7; heartbeat / start-up 28/28; camera
    heartbeat 4/4; logging 37/37; DB server 62/62; DB indexes 8/8; FE -> DB pipeline (row and picture stored);
    Mock-PLC window 17/17; endpoints 91/91; vision verifier ALL EQUIVALENT both;
  - not run: `DryRun_Cap_Check.py` stops at `ModelManager.load_models` without the YOLO weights (the same on the code
    before this change); all-checks `--auto` and the windows (no vision logic changed).

## 111. A stop command left in Redis by an earlier run no longer stops the loggers; "Slave Console Logger stopped" once (2026-11-16)

- **Reported (laptop, `DryRun_Cap_Check.py`):** "Slave Console Logger stopped" printed 9-13 times per picture.
- **Diagnosis:** `getStopCommandFromQueue` reads `stopcommandQ` from its start (`xread` from id 0) and never removes
  the message - on purpose, every process must see the one stop. So a stop left by an earlier run (MainProgram's
  shutdown: MainProgram flushes Redis only at its own next start) was taken by every later process on that Redis:
  each slave logger stopped within 5 s of starting, and `SlaveConsoleLogger.logMessage` then printed the line at
  every log call (dry runs and tools do not flush Redis). Reproduced: a stop sent by an earlier process, then a new
  process logging 5 messages -> logger stopped, 5 lines. Clearing Redis would only hide it until the next stale stop.
- **Fix:** `utils/RedisUtils.getStopCommandFromQueue` ignores a stop command sent before the reading process started
  (`_PROCESS_STARTED_AT`, set when the module is imported - at the start of every process; the message carries its
  send time), noted once per process ("Stop command from an earlier run ignored ..."). Nothing legitimate needs such
  a stop: MainProgram flushes Redis before it starts the servers, and its stop is sent after they started; the
  supervisor does not restart a server after a stop command. `SlaveConsoleLogger.logMessage` prints "Slave Console
  Logger stopped" once, not at every call.
- **Run:** new `DryRun_Stale_Stop_Command.py` (both folders) 5/5 (old code 1/5: S1 logger stopped with 5 lines, S2 5
  lines); heartbeat / start-up 28/28 (H10 shutdown); process supervisor 7/7; logging 37/37; DB no record loss (c: a
  stop with 3 records pending - drained, then stopped; b, d, e pass); integrated all-servers cycle (DB row as before,
  state sync in sync); work-thread health 17/17 (one run 16/17, C2 timing; 3/3 again after, 2/2 on the old code).

## 112. Review pass: a QR code split across two serial reads was lost (and since §110 sounded the siren); the abort's state-sync reason travels with its change (2026-11-16)

- **Review (user: make sure there are no outstanding errors):** ruff bug rules over the 154 production files (no
  undefined names; bare `except`, `== None` and the like only in legacy modules nothing in the application imports;
  mutable defaults only read, never changed); the supervisor, the live configuration reload and every real stop
  command against §111; the three properties files in step; no TODO / FIXME left; today's §110 / §111 changes
  re-read for side effects. Two defects found, both fixed below.
- **QR code split across two reads (since the original code; made worse by §110):** the scanner port is opened with a
  0.1 s timeout and `readline()` returns whatever has arrived when it expires. A code still arriving at that moment
  came back in two pieces, each refused: the scan was lost (the operator had to scan again), and since §110 each piece
  also sounded the siren and showed "QR Code not valid". Estimated from the timing (86 bytes at 38400 baud, about
  22 ms; the reader's 0.1 s read + 0.1 s pause): about 11 % of scans, if the scanner sends one burst ending in a line
  feed - a USB adapter's buffering changes the figure, not the fact. **Fix:** `QRCodeLineAssembler` collects the bytes
  across reads; a line is checked only when complete - at a CR or LF, or after 0.3 s with no new byte
  (`QR_LINE_QUIET_SECS`, for a scanner without a line end). Cleared at the start of every read request.
- **Abort reason in the state sync (a race, wider since §110):** `abortCycle` set a shared `_stateReasonNext` and the
  next published state change took it; with the listener outside the state lock (§110) a change made by another
  thread at that moment could take the abort's reason (only the `reason` text of the STATE-SYNC snapshot and audit is
  affected). Now `setCurrentState(state, reason=...)`: the reason is queued with that change and passed to the
  listener with it (`_onStateChange(old, new, reason)`); `_stateReasonNext` removed.
- Looked at and left: `_writeStateSince` (stamped by the listener, so possibly a little late now) is only the fallback
  when there is no picture request - the camera answer timeout uses the request's own send time; a late stamp only
  lengthens that fallback wait.
- **Run:** `DryRun_QRCode_Validation.py` 17/17 both folders (new V5: split across two reads with LF, CR or no line
  end - taken whole, no alarm, no notice; a bad scan then a split good one - one alarm, the good code taken; the old
  code never takes the split scan and the run hangs); new `DryRun_State_Change_Delivery.py` 4/4 both folders (D1
  another thread not held up by a slow listener, D2 every change once and in order, D3 a reason only with its own
  change, D4 nested calls); fault scenarios Siemens 201/201 (O4: the abort's state change published with its reason),
  Allen-Bradley 218/218; two full cycles (28 states); state sync monitor 15/15; frontend 145/145; integrated
  all-servers cycle (DB row as before, state sync in sync); work-thread health 17/17; DB indexes 8/8.

## 113. runNuitka.bat: the properties copy and the checks after the build never ran (2026-11-16)

- **Reported (laptop build of 16 Nov):** the build succeeded, but `MainProgram.dist\ApplicationConfiguration.properties`
  did not exist; the build output ended at "Compilation report written" (no "Copying run-time files", no checks, no
  "Ended:").
- **Cause:** the build line called `nuitka --standalone ...`. In a Windows venv `nuitka` is `.venv\Scripts\nuitka.cmd`,
  a batch file, and a batch file run from another batch file without `call` never returns: everything after it in
  `runNuitka.bat` (the copy of the machine's properties file, the dist checks, the models note, "Ended:", `pause`) was
  skipped. The version line already used `python -m nuitka`.
- **Fix:** `python -m nuitka --standalone ...` (returns normally; also certain to use the venv's Python). The file keeps
  its CRLF line ends (checked: a 3-line diff).
- Not run here (Windows only): the next `runNuitka.bat siemens` on the laptop must print the copy line, the checks
  (`plc.vendor=siemens`, OK for the yaml, wavs, internalimages) and "SUCCESS!".

## 114. runNuitka.bat: the copy and the checks catch their errors; `copyonly` (2026-11-16)

- **User:** put the copy into runNuitka.bat and catch any error from it (the copy and checks were there, but had never
  run - §113).
- **Copy:** `copy /Y ... >nul 2>&1`, then `if errorlevel 1` -> "ERROR: could not copy ... - is it open, or
  MainProgram.exe running?" (counted as a problem) or the copy line. Before: the copy's result was not looked at, and
  the copy line was printed either way. No `MainProgram.dist`: said so, nothing copied. No properties file: said so.
- **Checks:** MainProgram.exe; in the copied properties `plc.vendor=<vendor>`, `picture.retention.days.trial=` and
  `.main=` (each printed `OK: <line>` or `MISSING ...`); the yaml, `wavs\`, `internalimages\`. Every command's own
  error output suppressed; the end says SUCCESS or "SOMETHING ABOVE FAILED OR IS MISSING - fix it before copying to
  the plant PC".
- **`runNuitka.bat siemens copyonly`** (or `allenbradley copyonly`): no build - only the copy and the checks on the
  existing `MainProgram.dist`. Any other second argument shows the usage.
- `%CD%` no longer echoed inside parenthesised blocks (a `)` in the folder path would close the block early).
- CRLF line ends kept. Not run here (no Windows `cmd` in the sandbox): checked by reading - labels, `if errorlevel` at
  run time inside blocks, no unescaped parentheses in the echoed texts.

## 115. Bung check: holes found whatever the bung's angle; size filter before merging; seating by face oval (2026-11-17)

- **User:** CheckBung sometimes NOT OK for well-placed correct bungs; turning the bung got it through. Asked for: inner
  disc r125 (configurable), mask area 325-1300 (configurable), a denser point grid always instead of the sparse grid
  "if fewer than 5 masks", size filter before merging, wrong bung only on a real 4-hole square, seating tolerance
  15 -> 20 px, an accurate bung-face mask with an ellipse-fit test (98-102%) and a "part outside the defined oval"
  test (> 2% rejected, configurable), a rotation test, one log line per gamma.
- **Cause (code + measurements):** the holes were found only through YOLO box proposals. The fallback grid (one point
  every 120 px over the 500 px crop, only when YOLO gave fewer than 5 masks) put 1 point in the r110 disc, on the bung
  centre, and none on the hole ring (holes 40-66 px from the centre). A hole without a YOLO box was lost; YOLO's boxes
  depend on the highlights, which move when the bung is turned. Made worse by: DOSTPLUS needing exactly 2 candidates;
  4+ candidates when 2 expected ending the search as NOT OK; merging before the size filter; hole areas (670-1130 px)
  close to the 1200 px limit.
- **Grid step (user asked for 60 px):** measured with MobileSAM on the 60 pictures of 13 Nov, gamma 2.0, grid only
  (no YOLO here): a 60 px grid over the crop has 13 points inside the disc and found 0-4 holes (all holes on 1 of the 23
  seated bungs); a hole is about 36 px across, so a 60 px grid hits a given hole about 1 time in 4. A 15 px grid over
  the inner disc (about 220 points, about 4 per hole) found all holes on all 23: 11 DOST 4-hole squares, 12 DOSTPLUS
  pairs, and each NOT OK as the other model at gamma 2.0. Two cases on the 60 px grid (A01, A13) would have passed a
  DOST bung as DOSTPLUS (two diagonal holes found, the diagonal is as long as the DOSTPLUS pair). So the step is
  `vision.bunk.hole.grid.step` = 15 (both machines), not 60.
- **BunkSegmenter (both bung checks):**
  1. masks: YOLO boxes as before + one mask per point of the grid over the inner disc, always (decoded in batches of
     32, masks outside min/max.mask.area dropped at once); the sparse grid code is kept, unused;
  2. size filter before any merging (`_keep_masks_in_inner_disc`): each mask cut to the inner disc, 325-1300 px;
  3. one mask per blob (`_one_mask_per_blob`): masks overlapping > 15% of the smaller form a blob; the mask found most
     often (IoU > 0.5) is kept, never a union (a hole is not joined to a highlight); `_merge_overlapping_masks` kept,
     unused;
  4. shape filter as before (records why each component is dropped);
  5. pattern (`_apply_cascade_geometric_validation`): 3 in a line -> middle dropped; >= 4 -> best square (definitive;
     wrong bung only on a real square); 2 expected -> the pair 95-125 px apart whose midpoint is nearest the disc
     centre, leaving out a pair that a third candidate makes into 3 corners of a square (3 of a DOST's 4 holes);
     otherwise next gamma;
  6. one INFO line per gamma: `[Bung] <model> gamma <g>: masks <n> YOLO + <n> grid (of <points> points every 15 px,
     <n> masks outside 325-1300 px); part in r125 325-1300 px: <kept>, <too small>, <too large>; <n> blob(s); shape
     filter dropped (x,y) <why>; candidates <n>: (x,y) d<px from the disc centre> a<area> ...; <decision>`.
- **Seating (`check_bung_seating`):** face mask as before (MobileSAM at the hole-pattern centre, smallest mask of
  radius 100-145 - on all 43 pictures with a hole pattern it was the top face); seated = all of: hole-pattern centre
  <= `seating.hole.centre.offset.max` (siemens 15 -> 20); roundness as before; new: face area (holes filled) / fitted
  ellipse area within `seating.ellipse.fit.area.ratio.min/max` 0.98-1.02; new: at most
  `seating.outside.reference.oval.max` 2% of the face outside `seating.reference.oval.<model>` (cv2 rotated box
  w,h,angle, centred on the seated position, so it follows the frame) = mean seated face + 3 px all round: DOST
  241,249,122, DOSTPLUS 232,230,0. The reason text has all four values.
- **Measured (13 Nov, 43 pictures with a hole pattern, production `check_bung_seating`):** 43/43 as labelled. Seated:
  offset 0.3-9.9 px, fit 1.005-1.008, outside 0.0-0.7%. Not seated: offset 20.7-91.2, fit 0.986-1.008, outside
  4.1-39.4%. So the ellipse fit checks that the mask is one clean oval, not the seating (a tilted face is an oval
  too); the reference oval separates seated / not seated by itself (C14: 4.1%, offset 20.7, roundness 0.944 - caught
  by all three).
- **Properties (all three files in step):** siemens `bunk.inner.disc.radius` 110 -> 125, `bunk.max.mask.area` 1200 ->
  1300, `bunk.seating.hole.centre.offset.max` 15 -> 20; new keys (both machines, Machine 1 = the Machine 2 values
  until measured; Machine 1's seating check stays off): `bunk.hole.grid.step`, `bunk.seating.ellipse.fit.area.ratio.
  min/max`, `bunk.seating.reference.oval.dost/.dostplus`, `bunk.seating.outside.reference.oval.max`.
- **Reference commit 093a5fb** (new logic, values hard-coded); `baseline_per_file` of both manifests points at it,
  the 6 extracted-literal lines moved; verifier ALL EQUIVALENT on both machines.
- **New dry run `DryRun_Bung_Rotation.py`** (both folders): the 23 seated pictures turned about the seated centre in
  15 degree steps through `CheckBunk.checkBunk`; the hole pattern must pass as the own model and fail as the other at
  every angle; seating shown, not judged (the picture's light and the hub turn with it). `--no-yolo`, `--pictures`,
  `--step`, `--own-only`, `--verbose`.
- **Run here (CPU, no YOLO weights, so `--no-yolo`):** A01 (DOST) and B11 (DOSTPLUS) at 45 degree steps as the own
  model and at 90 degree steps as the other model: hole patterns 24/24 as expected (all 4 / 2 holes at every angle;
  NOT OK as the other model at every angle). The seating step said NOT SEATED for A01 at 180 and 225 degrees: face
  roundness 0.946 / 0.940 < 0.952 (0.971 unturned) - the turned picture changes the face mask; not judged, but it
  shows the DOST roundness minimum has little margin (seated 0.956-0.978 on the real pictures). Not changed (user's
  setting); the reference oval now separates the 13 Nov pictures alone.
  Verifier both ALL EQUIVALENT; DryRun_Vision_Check_Timeout 23/23 (Machine 2), 22/22 (Machine 1); DryRun_Knuckle_Frame
  --quick 86/86.
- **Not run here:** anything with YOLO (the real production mix of YOLO + grid), times on a GPU. On the laptop:
  `DryRun_Bung_Rotation.py` (all 23, both models: 1104 checks) and `DryRun_Bunk_Check_UI.py`; on the plant PC: the
  check time (about 220 point prompts per gamma) - `docs/plant-pc-verification.md` step 9.

## 116. Bung check faster: hole grid only where the holes can be; low-resolution area check first (2026-11-17)

- **User's laptop run of DryRun_Bung_Rotation (§115):** correct so far (96/96 hole patterns after 2 pictures), but about
  1.5 s per gamma: a good bung about 2 s, a bung failing every gamma about 7-8 s against the 12 s timeout. The run of
  1104 checks would take about an hour (DOSTPLUS checked as DOST runs all 5 gammas at every angle) - not said in §115.
- **Cause:** about 220 point prompts per gamma over the whole r125 disc, each mask brought to full size (500 x 500)
  before its area was looked at.
- **Change (`MobileSAMv2AutomaticMaskGenerator`):** grid points only in the band `vision.bunk.hole.grid.radius.min` -
  `.max` (20-90 px; hole centres 40-66 px from the disc centre on the 13 Nov pictures, a hole about 36 px across):
  108 points. Each batch is decoded at the model's low resolution (256 x 256); a mask's area is estimated there, and
  only masks within 0.7 x min - 1.3 x max area are brought to full size and checked exactly as before.
- **Run here (CPU, no YOLO, gamma 2.0):** A01 (DOST) and B11 (DOSTPLUS): the same candidates (positions, areas) and
  decisions as §115; 16 s per gamma on this CPU (about 30 s before). The GPU time is the user's to measure.
  Reference commit 0866373 (both manifests); verifier ALL EQUIVALENT on both machines. Nothing else run (user: fast).

## 117. Bung disc centre 3 px left / 6 px down, hole disc r135 (2026-11-17)

- **User's DryRun_Bunk_Check_UI run on the 13 Nov pictures ("should be all OK"):** the 11 seated DOST and 12 seated
  DOSTPLUS pictures OK, each NOT OK as the other model. NOT OK as their own model: 16 pictures with a hole pattern
  that failed seating (hole-pattern centre 20.7-48.2 px from the seated position, 4.0-18.9 % of the face outside the
  reference oval, roundness 0.857-0.948 on all but two) and 5 with no hole pattern (1 or 0 holes, the one found 68-92 px
  from the disc centre). These are the pictures labelled "not seated" in CHANGES 100.
- **Change (user; siemens, all three properties files):** `bunk.outer.disc.center` 646,328 -> **643,334**; the frame
  centres `frame.centre.bunk.outer.disc.center.dost/.dostplus` moved the same (642.5,331.5 / 643.5,336.5);
  `bunk.inner.disc.radius` 125 -> **135**; `bunk.hole.grid.radius.max` 90 -> **110** (so the grid reaches holes out
  to the larger disc). Seated positions, reference ovals and seating limits unchanged. Verifier ALL EQUIVALENT
  (both); nothing else run (user: fast).

## 118. Bung seating limits relaxed (2026-11-17)

- **User (siemens, all three properties files):** `bunk.seating.outside.reference.oval.max` 0.02 -> **0.06**;
  `bunk.seating.roundness.min.dost` 0.952 -> **0.925**, `.dostplus` 0.96 -> **0.925**.
- **Effect on the 13 Nov pictures (from the measured values, not re-run):** no verdict changes. The 16 pictures labelled
  not seated that have a hole pattern still fail the hole-centre limit (20 px): 15 at 27-48 px; the 16th (C14) at
  20.7 px, which now passes the oval test (4.0 %) and roundness (0.944) - so the hole-centre offset alone rejects
  it, with a 0.7 px margin. The seated pictures keep passing; in the rotation run the DOST roundness of turned
  seated pictures (0.929 at least) now passes. Verifier ALL EQUIVALENT (both).

## 119. Bung picture: reference oval and hole-pattern centre drawn (2026-11-17)

- **User:** mark the hole-pattern centre with a thick blue cross and the reference oval with a thick contour.
- `CheckBunk.drawSeatingMarks` (also used by CheckNoCapBunk), called whenever the seating step ran: the model's
  reference oval centred on its seated position (yellow, 3 px) and the hole-pattern centre (blue cross, 30 px, 4 px
  thick), beside the face outline (green / red). Shapes only, no text. Not in BunkSegmenter, so no reference commit.
- Run: the helper on a blank picture (oval and cross drawn; CheckNoCapBunk uses the same function). Nothing else run.
- **Follow-up (user: a picture with 4 holes showed neither mark):** the marks were drawn only when the seating step
  ran, and seating runs only after the hole pattern passed for the model checked - so not for a DOST bung checked as
  DOSTPLUS (4 holes, wrong bung), nor when no pattern was found. Now the reference oval is drawn on every bung
  picture, and the hole-pattern centre whenever holes were found (from the holes of the deciding gamma). Checked:
  A01 (DOST) checked as DOSTPLUS - NOT OK, oval and cross on the picture.

## 120. DOSTPLUS seated position and oval; hole grid over the whole disc (2026-11-17)

- **User (siemens, all three properties files):** DOSTPLUS seated position 5 px down: `bunk.seating.seated.centre.dostplus`
  647,330 -> **647,335**, and the frame's `frame.centre.bunk.seating.seated.centre.dostplus` 647.0,330.0 -> 647.0,335.0
  (the reference oval is centred there); `bunk.seating.reference.oval.dostplus` 232,230,0 -> **227,225,0** (each axis
  5 px smaller).
- **Grid over the whole r135 disc (user: about 30 px apart, "0 % chance of missing a 40 x 40 hole"):**
  `hole.grid.radius.min` 20 -> **0**, `.max` 110 -> **135**, `hole.grid.step` 15 -> **20** (not 30). On a square grid
  the point farthest from every grid point is step / sqrt 2 away (30 px: 21.2 px), so a hole is certain to hold a point
  only if its radius is at least that - 42.4 px across at 30 px. The holes measure 29-38 px across (areas 670-1130 px,
  boxes 35-37 px; 13 Nov). Estimated chance that a hole holds no point at 30 px: about 5 % for a 36 px hole, about
  21 % for a 30 px one (and a point near a hole's edge gives a poor mask). 20 px: 14.1 px worst case - every hole of
  29 px or more holds a point. Points: 145 over the disc (30 px: 69; the band of CHANGES 116: about 108). Back to 30 =
  one properties line.
- Verifier ALL EQUIVALENT (both). Nothing else run.

## 121. PC_PLC_Skip_Second_Tightening has its Siemens address: DB500,X48.0 (2026-11-17)

- **PLC programmer (via user):** DB500, 48.0. `costhetaio/siemens/IOServerProperties.yaml`: `PC_PLC_Skip_Second_Tightening:
  { address: "DB500,X48.0", type: bool, dir: w }` (no overlap: the nearest tag below ended at R44). The bypass now runs
  on Machine 2 (state 2 writes True / False with read-back every cycle; True -> states 13-19 skipped).
- Fault scenarios N (both folders): N1 marks the tag missing on both vendors; N2-N4 now run on Siemens too. Not run
  (user: fast).

## 122. Trial pictures saved for every check, not only the 2-of-3 checks and the cap (2026-11-17)

- **User:** with `save.all.pictures.because.its.in.trial.mode=True` only cap and split pin pictures reach
  `C:/Temp/TrialImages/`.
- **Cause:** the trial save lived only in the 2-of-3 vote helpers (`ImageProcessor.checkKnuckle`, `checkSplitPinAndWasher`,
  ...) and in `_assessCap`. Every single-picture check runs its own branch in `assessPictures` and never saved: the
  knuckle since it moved to segmentation (CHANGES 102), and hub, top bearing, nut / washer, both bung checks and no-bung
  always.
- **Fix:** `assessPictures` saves the picture of every single-picture check to `<trial dir>/<Check>/` (Knuckle,
  HubAndBottomBearing, TopBearing, NutAndPlateWasher, BunkForComponentPress, BunkForCapPress, NoBunk) before the check
  runs; the vote checks and the cap keep their own saving (no double save). A failed save is logged, the check goes on.
- Run: the top bearing state through `assessPictures` with saving on - the picture written to `<dir>/TopBearing/`.
  Needs a rebuilt exe (code change).

## 123. End of cycle: "Component Successfully Done" at once; the values and pictures stay 5 s, then are cleared (2026-11-17)

- **User:** after the free-rotation torque, the values and pictures disappeared and only then did "Component
  Successfully Completed" come; show the box at once and keep the values / pictures 5 s, independently.
- **Cause:** both were one step. `_handleRotationTorque1` -> `doFinalProcessing(delay = frontend.free.rotation.torque.
  display.sec, 2 s)` -> after the delay `setInProcessDefaultDisplay` showed the box, sent the record AND cleared the tiles.
- **Now** (`frontend/AshokLeylandFrontEnd.py`): `doFinalProcessing` calls `finishCycle()` at once - result box
  (Successfully Done / Not Successfully Done / Aborted), record to the DB server, data points reset, back to the QR code
  and its read requested - and schedules `clearFinishedCycleTiles` after `frontend.free.rotation.torque.display.sec`
  (now **5.0**, all three properties files; code default 5.0), which clears the tiles and the QR code line. A new QR code
  during the hold clears them at once before the new component starts; a numbered pending clear makes a stale timer do
  nothing. The same applies to aborts (box at once, tiles 5 s; before: both after 0.75 s).
  `setInProcessDefaultDisplay` is kept (does both at once). `finalDisplayPending` / `emergencyAfterCompletion` are gone:
  the frontend is back at the QR code at once, so an emergency / manual abort during the hold takes the existing
  "waiting for the QR code" path (abort screen, no record); the finished component is already recorded as it was.
- `utils/StateSync.py`: the end-of-cycle allowance no longer comes into play (comment updated, logic unchanged).
- Dry run `DryRun_FEServer_Cycles.py` (machine2, both copies updated): F4c rewritten for the new order; **F15** new:
  box 0.45 s after the torque, record at once, state READ_QR_CODE at once, tiles and QR line still shown at 1 s, cleared
  at 5.4 s, once; F15b: a QR code 1 s into the hold clears at the scan and the old timer leaves the new component's tiles
  alone. **165/165 PASS.**

## 124. Machine Settings: password to open, password again to save (it is the confirmation); no Yes / No (2026-11-17)

- **User:** password when the menu item is chosen; at Save the password again with a message that entering it confirms
  the change; drop the Yes / No.
- `frontend/SimplePopups.py`: `askMachineSettingsOpenPassword()` (machine.settings.password) before the dialog opens;
  wrong / cancelled -> not opened. `confirmMachineSettingsChange` asks the same password with the changes listed and
  "Entering the password confirms this change."; if the bypass switch changed, the bypass password follows, worded the
  same way (kept from 2026-11-15). Wrong password -> nothing saved, the dialog stays open. `machineSettingsChanges()`
  builds the change list. `getJustPassword` now wraps each line on its own (it joined the lines of a multi-line message).
- Dry run F12 / F12b (both copies) rewritten: open right / wrong / cancelled, wrong password stops
  `updateMachineSettingsDialog` before the dialog, Save prompts carry the change list and the confirmation text, no
  Yes / No asked. In the 165 above.

## 125. Bunk -> Bung everywhere (code, files, folders, properties, database, frontend, logs, docs) (2026-11-17)

- **User:** change Bunk to Bung throughout - DB, frontend, file and folder names, logs, properties - without breaking
  anything.
- **Done with one case-preserving text change** (`BUNK`/`Bunk`/`bunk` -> `BUNG`/`Bung`/`bung`) over every tracked text
  file except `archive/`, `bokeh/` and this file's earlier sections (history stays as written), plus `git mv`:
  `camera/BungSegmenter.py`, `CheckBung.py`, `CheckNoBung.py`, `CheckNoCapBung.py`; `DryRun_Bung_Check(_UI).py` (both
  folders); `test/machine2_dryrun/bung_images/` and `bung_images_20261113/` (and the picture names in them). This
  covers class / function / variable names, the state names (`READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS`, ...),
  Redis field names, properties keys (`<vendor>.vision.bung.*`, `vision.frame.centre.bung.*`, `plc.pc.check.bung.tagname`,
  `images.bung.and.no.bung.folder` = **`7-BUNGAndNoBUNG/`**, ...), operator instructions ("Place BUNG on CAP ..."), log
  lines, frontend tiles, trial-picture folders (`BungForComponentPress/`, `BungForCapPress/`, `NoBung/`), reports.
- **Kept on purpose:** the PLC tag names - the values of the 9 `*.tagname` keys (`PLC_PC_CheckBunkForComponentPress`,
  `PC_PLC_BunkForComponentPressCheckOK/Done`, `PLC_PC_CheckNoBunk`, `PC_PLC_NoBunkCheckOK/Done`,
  `PLC_PC_CheckBunkForCapPress`, `PC_PLC_BunkForCapPressCheckOK/Done`), the matching keys of
  `costhetaio/siemens/IOServerProperties.yaml` and the defaults in `Configuration.py`. They are the PLC program's names
  (Allen-Bradley reads tags by these names; the yaml maps them to Siemens addresses), so changing them needs the PLC
  programmer. `costhetaio/allenbradley/IOServer_ModbusTCP.py` (legacy, one tag string) is unchanged. The Python
  attributes that hold them (`IOServer.PLC_PC_CheckNoBung`, ...) are renamed.
- **Nothing breaks on the plant PC:**
  - *Database:* `createAllTables` (run at every start) renames the 9 old columns (`check_bunk_for_component_press_*`,
    `check_no_bunk_*`, `check_bunk_cap_press_*`) to their `bung` names if they exist and the new ones do not - data kept,
    a no-op afterwards. Stored picture paths keep their old folder names.
  - *Properties:* `CosThetaConfigurator._addLegacyBungKeys` - at every load / live reload each old `bunk` key whose
    `bung` spelling is missing is added under the new spelling (one warning). An old properties file gives exactly the
    new values except the picture folder (it stays `7-BUNKAndNoBUNK/` until the file is replaced). Checked: the
    pre-change Machine 2 file loads with 92 keys mapped, every `bung` key equal to the new file's.
  - *Pictures:* new ones go to `7-BUNGAndNoBUNG/<date>/`; the picture clean-up walks the whole picture root, so the old
    folders are still cleaned up.
  - *Camera state file / Redis:* field names changed; MainProgram flushes Redis at start, and a state file from the
    old build simply has no pictures under the new names (ignored).
- **Vision verifier:** the renamed vision files no longer match their reference commits, so a new reference commit
  `0ff5470` holds `camera/BungSegmenter.py` (= 0866373 BunkSegmenter), `camera/CheckNoBung.py` (= 5728def CheckNoBunk)
  and `camera/VisionFrame.py` (= f3aa0ed) with the same text change; both manifests point at it (keys and file names in
  `extracted` renamed too). **ALL EQUIVALENT** on both machines.
- Runs (sandbox): verifier both folders ALL EQUIVALENT; every file compiles; `DryRun_FEServer_Cycles` 165/165;
  `DryRun_IOServer_Fault_Scenarios` (Siemens) 211/211; `DryRun_State_Sync_Monitor` 15/15;
  `DryRun_IOServer_Two_Full_Cycles`, `DryRun_FEServer_To_DBServer_Pipeline`, `DryRun_All_Servers_Integrated_Cycle`
  (DB row all OK, state sync in sync) completed; `DryRun_DBServer_And_Persistence` 66/66 with a new section 10 (a
  database with the old column names: renamed at set-up, the old row's values kept, a second set-up changes nothing; the
  sandbox's own Test database, made before the rename, was migrated the same way); `DryRun_DB_Indexes_And_Day_Folder`
  8/8 (one earlier run failed D1: the test checks all 7 day folders right after the first one moves - a race in the
  test, not in the rename; passed on the rerun).
- Needs a rebuilt exe and the new properties file (both in the new `MainProgram.dist`).

## 126. Camera movement: every check follows the knuckle frame (audit); the bung reference ovals now follow its zoom and turn (2026-11-17)

- **User:** make sure every vision check is placed relative to the knuckle (slight camera movement), especially the
  bungs after CHANGES 115-120.
- **Audit (code):** every absolute position a Machine 2 check uses is one of the frame's 18 targets and is read at call
  time: hub segmentation centre (+ the segmenter instance's `annular_center`), hub centre check, top bearing ring, nut /
  washer, no-bung nut, bung disc centre (`OUTER_DISC_CENTER`), both bung seated positions, cap. Everything else is
  relative to those (the bung hole grid and inner disc in the disc crop; the hole-pattern centre, the seating offset and
  the reference oval centred on the placed seated position; `drawSeatingMarks` reads the module at draw time;
  `CheckBung` / `CheckNoCapBung` hold the module, not copies; the nut reference centre is in crop coordinates; the
  top bearing offsets are relative). The frame is applied in the worker process before every check (`restoreState` and
  `assessPictures`), kept only with a knuckle OK (OK needs a frame), dropped at cycle end, abort and knuckle retake.
  The properties are in step: CHANGES 117 / 120 moved both the plain bung keys and the `frame.centre.bung.*` keys
  (disc 643,334 = the mean of 642.5,331.5 / 643.5,336.5; seated 646,325 / 647,335), and each plain centre is the mean
  of its two per-type frame centres.
- **The one gap: sizes.** The frame moves centres but scaled nothing ("radii not changed", scale within 0.1 % on the 13
  Nov pictures), while it accepts up to 5 % zoom and 3 deg. Hole areas, spacing and radii have wide bands, but the
  bung seating oval does not: seated faces read up to 3.9 % outside it against the 6 % limit. Measured here (B12, a
  seated DOSTPLUS, outside 4.4 % as taken): 2 % zoom -> 6.0 %, 3 % zoom -> 7.3 % = NOT OK with an unscaled oval.
- **Fix** (`camera/VisionFrame.py`): `_OVAL_TARGETS` + `placeOval` - with a frame, `SEATING_REFERENCE_OVAL_DOST /
  DOSTPLUS` = (width x scale, height x scale, angle + rotation) from the properties values; without one, the
  properties values (a stale value can never compound: the base is always the registry's). Reference commit `2376cf9`
  (= 0ff5470's VisionFrame with this logic), manifests updated; verifier ALL EQUIVALENT (both).
- **Runs:**
  - `DryRun_Knuckle_Frame.py --quick` (CPU, MobileSAM from ChaoningZhang/MobileSAM): A-C 82/83 - every knuckle OK
    only with its own QR code, empty fixtures NOT OK, centres within 2.5 px of the references, camera moves placed
    within 1.13 px worst, 55 px move NOT OK; the one FAIL was the sandbox missing `ffmpeg-python` for the
    camera-server part; after installing it, part D 14/14 including two new checks (ovals follow the frame's zoom and
    turn; without a frame the properties ovals come back). 7.6 s median per knuckle check on CPU.
  - Oval unit check (no SAM): 5 frames (none; 15 / -10 px; -12 / 8 px + 1.5 deg; 3 % zoom; 5 / 5 px - 2 deg x0.98):
    ovals exact, disc and seated centres placed; applying the same frame twice does not compound; no frame -> properties.
  - **New `DryRun_Bung_Frame.py`** (both folders; `--no-yolo` here, no YOLO weights in the sandbox): B12 (seated
    DOSTPLUS), A08 (seated DOST), C05 (DOSTPLUS not seated) as taken and moved (15 / -10 px; -12 / 8 px with 1.5 deg;
    3 % zoom), frame from the pins moved the same way: **30/30** - same verdict every time, hole-pattern centre where
    the move puts it (0.0-0.5 px), seating offset within 1.2 px, outside the oval within 0.5 points (B12 4.4 % at 3 %
    zoom, unchanged). `--fixed-ovals` (the behaviour before this change) on B12: 3 % zoom NOT OK (7.3 %).
- **Seen, not changed:** MobileSAM's top-face mask is not translation-invariant: the face roundness moved by up to
  0.056 for a few pixels of camera shift (C05 0.896 -> 0.952, B12 0.995 -> 0.964), with nothing else changing. The
  roundness test (limit 0.925) is the least stable of the four seating tests; the verdicts held because a not-seated
  bung also fails the offset and the oval. The test prints roundness without judging it.
- Needs a rebuilt exe (code change).

## 127. Aborts clear the tiles after 0.75 s again; MI torque charts leave out an aborted cycle's unreached torques (2026-11-17)

- **Aborts (user, on CHANGES 123):** the "Aborted" box still comes at once, but an aborted cycle's tiles are cleared
  after 0.75 s again (`AshokLeylandFrontEnd.ABORT_TILES_CLEAR_MSEC` = 750, passed by both abort paths - emergency
  button and Ctrl+Alt+Z); a completed cycle's stay 5 s. Frontend dry run (both copies) new F15c: box 0.50 s after the
  emergency message, tiles cleared 1.23 s after it (the emergency worker polls every second, then 0.75 s).
- **MI dashboard review (user: anything to change for the DB changes since it was last touched, §94):**
  - The only schema change since is CHANGES 125 (9 bung columns renamed). The dashboard reads none of them; the web
    service exports `Persistence.all_columns_record_header`, so its JSON / CSV follow the new names by themselves.
    **For anyone consuming the web service:** the 9 field names changed (`check_bunk_*` / `check_no_bunk_*` ->
    `check_bung_*` / `check_no_bung_*`); nothing in this repo reads them by the old names.
  - **Found, fixed:** the torque charts kept every value >= 0, so each aborted cycle put up to three 0.0 values into
    them - the frontend records a step not reached with torque 0.0 and the placeholder time 1970-01-01 (CHANGES 90).
    `AshokLeyland_MI_Light.loadFrame` now reads the three torques' `_datetime` columns and treats a torque whose time
    is before 1971 as not measured; -1.0 (not measured, bypassed torque 2) stays out as before; NULL times (bypassed,
    older records) are left to that rule. Counts, summary and the other tabs unchanged.
  - `DryRun_Endpoints.py` (both copies): the seeded aborted cycles now look like real ones (steps not reached 0.0 /
    1970; reached and normal torques carry the cycle's time); torque 1 count = SQL with that rule; per model and chart,
    no 0.0 of an unreached step drawn. 98/98 both copies; with the old dashboard 94/98 (the 0.0 values drawn).
- Frontend dry run 167/167. Needs a rebuilt exe; the MI dashboard (`OtherServices.py`) needs a restart.

## 128. Brass cap seating by fitted circles: seated brass caps no longer rejected (2026-11-17)

- **User:** 117 trial pictures of 17 Nov (+ 9 from the DB server's folders), nearly all correctly placed caps; the cap
  window rejected most of them (attachment: almost every DOSTPLUS picture "NOT SEATED - brass cap or bore not found by
  the segmenter").
- **Cause (measured, MobileSAM point prompt at the cap centre, as in production):** the brass cap face (SAM's first
  mask) is found on every picture (radius 80-85). The "bore" was SAM's second mask, which on the 13 Nov pictures was the
  bore (r 109-114); on the 17 Nov pictures it is the cap plus the dark gap round it, its outline leaking into the
  bearing: radius 86-103 against the 104-120 window -> "bore not found" on 31 of 36 brass cycles. Where it did fall in
  the window its centre was pulled by the leak (up to 8 px). SAM's second mask simply is not a bore detector.
- **Now (`CheckCap.brassCapAndBore`, used by `checkSeating` for brass):** the picture unwrapped round `cap.center`
  (1 degree x 1 px); per angle the strongest fall of R-B in `seating.brass.edge.band` (55-112: brass -> gap) and of grey
  in `seating.bore.edge.band` (106-140: the bore edge); a circle fitted to each by RANSAC (fixed seed, tolerance
  `seating.circle.fit.tolerance` 2 px) and refined; the cap needs `seating.brass.fit.min` 0.5 of the angles on its
  circle and a radius in `seating.brass.radius` (75-95), the bore `seating.bore.fit.min` 0.3 and `seating.bore.radius`
  (104-120). Seated = cap centre to bore centre <= `seating.brass.offset.max`, siemens 9.0 -> **10.5** (midway between
  the largest seated value 7.6 and the smallest skewed 13.5). No MobileSAM for brass: 24 ms per check (CPU).
  Silver is unchanged (MobileSAM).
- **Measured:** cap r 82.9-84.3, bore r 107.9-115.5 on every brass picture. 17 Nov (98 brass pictures, all three per
  cycle, user: seated): 98/98 OK, offset 0.6-7.6 px. 13 Nov seated 5/5 OK (3.5-6.5); 13 Nov skewed 4/4 NOT OK
  (13.5-23.1).
- New keys (both machines, all three files; allenbradley = the code defaults, seating stays off on Machine 1).
  Reference commit 5bf40a3 (f3aa0ed's CheckCap + this logic, values written in); both manifests; verifier ALL
  EQUIVALENT on both machines.
- `cap_images/DOSTPLUS_cap_06-10.jpg`: five 17 Nov brass caps that were rejected before (incl. two where SAM's first
  mask was only part of the face). `DryRun_Cap_Production_Path.py` 12/12 (10 seated brass OK, 4 skewed NOT OK; silver as
  before).
- **Silver not solved here** (see the reply of 2026-11-17): on the 17 Nov silver pictures 3 of 7 seated cycles fail the
  top-face test (top face centre to outline centre 9.1, 9.1-9.4, 12.6-12.7 px against 9.0); MobileSAM's top-face mask is
  irregular on the embossed face, and no edge-based measure tried here separated them from the 13 Nov skewed caps.


## 129. Silver cap seating by two rules: hub centre uncovered, top face edge on one circle (2026-11-18)

- **User:** the cap check should look at the cap against the hub centre; a silver cap is wider than the hub centre, so
  any visible part of the hub centre means not seated - plus another rule if needed. No pictures of a hub and its own
  cap exist; do the best possible for silver. Brass: keep a rule that separates seated from skewed.
- **Brass: unchanged.** CHANGES 128 already separates them (cap centre to bore centre, same picture: seated 0.6-7.6 px,
  skewed 13.5-23.1, limit 10.5). Measured today for comparison: whole-cap MobileSAM mask (8-point ring at r 55 plus a
  box; round, whole-cap masks on 112/112 brass pictures) to bore 1.1-5.1 / 11.3-20.3; against the hub circle of the
  uploaded hub pictures 0.4-3.5 / 11.7-15.8. Same margin, so no change (no MobileSAM, no hub picture needed).
- **Silver before:** top face centre to outline centre <= 9 px (MobileSAM face mask). Rejected seated 17 Nov caps: the
  embossed face gives ragged masks, and a seated cap's face already sits about 8 px from its outline centre (the camera
  looks a little from one side), varying per cap.
- **Measured (37 silver pictures: 25 of 17 Nov + 5 of 13 Nov seated, 7 of 13 Nov skewed; 38 hub pictures of 13 Nov,
  uploaded today, none of the same parts):**
  - Whole silver cap: MobileSAM point prompt finds it on 36/36 (r 101-104, roundness >= 0.96).
  - Hub circle in the hub pictures: MobileSAM, 12 points on a ring of r 95 plus a box: 38/38, r 110-116, roundness
    >= 0.948, centre spread 1-2 px per model. (A polar edge fit was up to 6-10 px off it: not used.)
  - The hub circle is hidden under a silver cap in the cap picture (found on 18/36), so rule 1 takes it from the cycle's
    hub + bottom bearing picture (already in `currentPictures`; the cap check uses it for step 3).
  - Annulus / crescent between cap and hub circle as the test (user's first idea) does not work alone: the shape changes
    at offset = hub radius - cap radius (brass ~27 px: skewed brass still a full annulus; silver ~9-10 px: seated
    silver already crescents).
  - Top face edge on one circle (polar fit round the outline centre): seated 0.68-0.88 (30), skewed 0.46-0.59 (6 with
    an outline) - separates all, including DOST_skewed_02 (tilted, bore fully covered, uncovered 0 px). Face centre to
    outline centre overlaps (seated 4.8-13.7, skewed 9.0-21.4): logged only.
  - Hub centre uncovered (hub picture of another part): seated 0-150 px, skewed 0-2493 (only the cap pushed off the
    bore, DOST_skewed_07, above 1000).
- **Now (`CheckCap.checkSeating` for silver, new `hubCircle`, `hubCentreVisible`, `silverFaceCircle`; the old face test
  kept as `silverFaceBySegmenter`, unused):** NOT SEATED if (1) the hub centre (hub circle less 12 px) has more than
  400 px dark (HSV value < 60), outside the cap mask, after an opening of 9 px (removes the shadow line round a seated
  cap); or (2) less than 0.64 of the face edge lies on one circle; or (3) as before, the outline centre more than 20 px
  from `cap.center`. No hub picture or no hub circle: rule 1 is not applied and the reason says so. `checkSeating` takes
  the hub picture as a third argument (`checkCap` passes it). Marked picture: hub circle blue, face circle yellow.
  The reason line now reads e.g. `top face edge on one circle 0.72 >= 0.64 (face r 81.7, 12.2 px from outline centre);
  hub centre uncovered 0 px <= 400 (hub r 113.4, cap outline 8.6 px from hub centre); cap outline centre to cap centre
  7.9 px <= 20.0`.
- **Limits, honestly:** 0.64 sits between 0.59 and 0.68 (6 skewed caps only). Rule 1's 400 px is set wide because the
  hub picture was never of the same part: in production (same part) a seated cap should leave 0 px. The log lines give
  both numbers every cycle; tighten 400 (and check 0.64) from a shift's lines. Silver seating now runs MobileSAM on two
  pictures: 2.0-2.8 s per check on the sandbox CPU (GPU on the plant PC).
- New keys (both machines, all three files; allenbradley = the code defaults; seating stays off on Machine 1):
  `cap.seating.hub.{ring.radius, box.half, radius.min, radius.max, centre.inset, dark.level, dark.open, visible.max}`,
  `cap.seating.silver.face.{band.min, edge.margin, fit.min}`. `silver.face.offset.max` and `face.box.scale` are no
  longer used (kept with the old test).
- Reference commit 892041d (8ecef55's CheckCap + this logic, values written in); both manifests; verifier ALL
  EQUIVALENT on both machines.
- `cap_images/DOST_cap_06-09.jpg` (Machine 2): four 17 Nov seated silver caps.
- Dry runs: new `DryRun_Cap_Silver_Seating.py` (both copies) 6/6 on Machine 2 (Machine 1: skipped, seating off);
  `DryRun_Cap_Production_Path.py` 12/12 (9 seated silver OK, 7 skewed silver NOT OK; brass as before);
  `DryRun_Cap_Check.py` all as expected on both machines; `DryRun_All_Checks_UI.py --auto`: cap OK (3.1 s; the YOLO
  checks cannot run in the sandbox). `checkSeating` callers in the dry runs updated (silver prints the face fit).
- Plant PC verification step 10 added. Needs a rebuilt exe and the properties file.
- **Follow-up (user's laptop run):** `DryRun_Cap_Silver_Seating.py` printed timm FutureWarnings and MobileSAM "Overwriting
  tiny_vit_* in registry" UserWarnings. It imported `mobile_sam` before anything had imported `camera.ModelManager`,
  whose import sets those two warning filters (the other dry runs import it first through the camera server). Now it
  imports `camera.ModelManager` first (both copies); production unchanged. 6/6, no warnings.

## 130. Hub centre mask study window (2026-11-18)

- **User:** a dry-run window to see the MobileSAM mask round the hub centre on the uploaded hub pictures, prompted by
  points 50 and 60 px from the centre, every 30 degrees.
- **New `test/machine*_dryrun/DryRun_Hub_Centre_Mask_UI.py`** (study tool; nothing in production changes): prompts =
  one point every "angle step" degrees on each ring (defaults 50, 60 px and 30 deg: 24 positive points, one decoder
  call, 3 masks) round "centre" (default `cap.center`, Machine 2 644,340); centre, radii and step editable, applied from
  the next picture. Outlines only on the picture (mask 1 cyan, 2 magenta, 3 yellow; points blue dots; centre blue
  cross); per mask score, area, centroid, fitted-ellipse centre, mean radius, roundness and the centroid's distance
  from the centre in the list / terminal; time per picture; seconds between pictures, Stop, Save; `--batch` without a
  window.
- **Run:** `--batch` on the 38 uploaded hub pictures (13 Nov): all processed, 1.0-1.2 s each on the sandbox CPU; the
  three masks' centres within about 6 px of 644,340; mask 2 the roundest (radius about 70-74, roundness 0.96-0.99).
  The window itself was not driven here (the sandbox's Tk Python has no torch; the run with a stand-in model was
  stopped at the user's request) - only compiled.

## 131. Hub centre mask window: gamma spinner (2026-11-18)

- **User:** a gamma spinner in `DryRun_Hub_Centre_Mask_UI.py`, default 1.0, steps of 0.2, at most 6.
- "Gamma" spinbox 1.0 - 6.0, increment 0.2, start 1.0 (both copies); `--gamma` in `--batch`. The picture is brightened
  before MobileSAM sees it, as the checks do (255 x (v / 255) ^ (1 / gamma); 1.0 = as taken); the marked picture shows
  it brightened; the gamma is in each picture's line. Applies from the next picture; an entry outside 1.0 - 6.0 is
  logged and 1.0 used (batch: refused).
- **Run:** `--batch` with `--gamma 2.4` on one hub picture (masks found, gamma in the line); `--gamma 6.2` refused.
  Window under Xvfb (Python 3.12, MobileSAM stood in): default 1.0, range 1.0 - 6.0, 3 steps up = 1.6, stops at 6.0,
  a run at 2.4 logs "gamma 2.4".

## 132. Hub centre mask window: masks all over the picture (2026-11-18)

- **User:** a tick box "Find masks only around the centre" - ticked: as now; unticked: masks all over the picture, in
  different colours. (Asked too: the masks are found on the picture after the gamma - yes, in both modes.)
- `DryRun_Hub_Centre_Mask_UI.py` (both copies): the tick box (ticked at start); unticked = MobileSAM's automatic mask
  generator (`SamAutomaticMaskGenerator`, 32 x 32 point grid) on the gamma-adjusted picture, with its own filters at
  their defaults - predicted IoU >= 0.88, stability score >= 0.95, overlapping boxes merged at IoU 0.7, no minimum
  area - stated in each picture's line. Each mask outlined in its own colour (no text); listed largest first with
  predicted IoU, stability, area, centroid, ellipse, distance from the centre. `--whole` in `--batch`.
- **Run:** `--batch --whole` on hub picture 151821100: 79 masks in 170.7 s on the sandbox CPU (the laptop GPU should
  take about 10 s). Found: the disc, the hub flange (radius 249, 1.8 px from the centre), studs, holes, the spindle tip,
  fixture parts; no mask of the hub's centre opening survived the generator's filters. Window under Xvfb with
  stand-ins: ticked at start; unticked gives the whole-picture line and its masks; ticked again back to ring prompts.

## 133. Hub centre mask window: whole mode works on a disc round the centre (2026-11-18)

- **User:** cut a disc of radius 300 round the centre first, then do everything else on that disc alone.
- Unticked mode (both copies): `cutDisc` takes the square round the disc (clipped to the picture) and blacks out
  everything outside the disc; the automatic generator runs on that crop only (32 x 32 points over the square, same
  default filters); each mask is kept to the disc and placed back in the picture; a mask less than half inside the
  disc (the black corners) is dropped and counted in the line. "Disc radius" box (default 300; a bad entry logged,
  300 used), `--disc` in batch. The disc is drawn as a thin white circle (no text).
- **Run:** `--batch --whole` on 151821100: 17 masks kept, 5 dropped (corners), 157 s on the sandbox CPU. Kept: the disc
  itself (r 298), the hub flange (r 248, 2.6 px from the centre), studs, holes, small marks; still no mask of the hub's
  centre opening from the generator. Window under Xvfb with stand-ins: the disc line, a corner mask dropped, ticked
  mode unchanged.

## 134. Hub centre mask window: YOLO box prompts, adjustable filters, faster grid, window stays live (2026-11-18)

- **User:** (a) the whole-disc mode is far too slow and the time per picture is not seen; (b) at gamma 3 the cap mask
  is sometimes missing; (c) the hub's centre opening is never found. Then: build the four proposed fixes, with the
  YOLO box prompts as in the hub check, and a stability spinner (default 0.90, step 0.02, 0.70 - 1.00).
- **Found (sandbox, real MobileSAM):**
  - (a) about 95 % of the generator's time is the decoder run once per grid point (32 x 32 = 1,024 prompts); the
    picture's embedding about 1 s. The window also did the work on its own thread, so it froze until the picture was
    done.
  - (c) single-point prompts inside the bore do give the opening's mask (r about 75), but with stability 0.93 - 0.94;
    the generator's default stability limit 0.95 dropped every one, at every gamma. With 0.90 it comes through.
  - (b) not reproduced on DOST_cap_06 / 01 / 08 at gamma 3 (the cap mask came from many grid points and survived
    every filter, e.g. 101 cap-shaped masks before box merging, 2 after, on cap_06); the cap masks' stability is
    0.96 - 0.99, so the 0.95 limit is the likely loss on pictures where gamma 3 weakens the cap's edge.
- **Now (`DryRun_Hub_Centre_Mask_UI.py`, both copies; a study tool, production unchanged):**
  1. Each picture is worked on in a background thread; the status line shows the picture and its time so far, the
     list its time when done; Stop takes effect after the picture in work.
  2. Grid points: "Points per side" spinner 16 - 64 (step 8, default 32); only grid points inside the disc are used
     (`point_grids`; 208 of 256 at 16 x 16); 256 points per decoder pass on a GPU (64 on a CPU).
  3. Filters as spinners, used by both prompt modes and written in each picture's line: "Min stability" 0.70 - 1.00,
     step 0.02, default 0.90; "Min predicted IoU" 0.50 - 1.00, step 0.02, default 0.88; "Box merge IoU" 0.30 - 1.00,
     step 0.05, default 0.70.
  4. "Prompts: YOLO boxes" (MobileSAMv2, as `MobileSAMv2AutomaticMaskGenerator` of the hub check): the object-aware YOLO
     model from `ModelManager` proposes boxes on the cut-out disc ("YOLO confidence" 0.05 - 0.95, default 0.25; its box
     NMS 0.7); boxes centred outside the disc are left out; the boxes go to MobileSAM as box prompts, 64 per decoder
     pass (`predict_torch`, one mask per box, logits kept); SAM's stability score and predicted IoU filter them and
     overlapping boxes are merged (torchvision `batched_nms`), as for the grid. The line gives the boxes found, on the
     disc, passing the filters and after merging, and YOLO's own time.
  `--batch`: `--prompts grid|yolo --pps --stability --iou --nms --conf`.
- **Run:** grid 16 x 16 on hub 151821100: 33 s on the sandbox CPU (157 s at 32 x 32 before); with stability 0.90 the
  opening is found (mask r 76.0, 3.5 px from the centre, roundness 0.966); with 0.94 it is not. YOLO path with a
  stand-in detector (the sandbox has no YOLO weights) and the real MobileSAM: 1.2 - 1.4 s per picture on CPU; a box
  round the bore gives the opening (r 76.3); a box round the cap on DOST_cap_06 at gamma 3 gives the cap (r 103.4);
  a box centred off the disc left out. Window under Xvfb with stand-ins 6/6: stability spinner 0.90 / 0.70 - 1.00 /
  step 0.02; the window stays live with the time ticking; the generator built with the spinner values and the points
  inside the disc; an error in YOLO mode goes to the list and the window carries on.
- **Not run here:** the real YOLO model (weights on the laptop only) - its boxes, their number and the GPU time.

## 135. Hub centre mask window: the user's settings as defaults; YOLO warmed up at start; time split (2026-11-18)

- **User (laptop run, 4.3 s for one picture, "the fastest I could get"):** make those settings the defaults; is this
  the same as the hub check, which often takes 1.25 s or less?
- **Defaults now** (window and `--batch`): "Find masks only around the centre" unticked; Prompts YOLO boxes; gamma 3.0;
  disc radius 300; YOLO confidence 0.25; points per side 64 (grid only); min stability 0.70; min predicted IoU 0.50;
  box merge IoU 0.50; 1 s between pictures.
- **Same method as the hub check?** The same idea - `MobileSAMv2AutomaticMaskGenerator`: one MobileSAM embedding, the
  object-aware YOLO's boxes as box prompts, one mask per box. Differences: (1) the hub check's models are loaded and
  warmed up once in the vision check worker at start, so its 1.25 s is the steady state, while the window's first
  picture also paid for loading YOLO through `ModelManager` and its first predict - the user's 4.3 s was a run of one
  picture; (2) the hub check decodes one box at a time and keeps masks by area (121-3630 px); the window batches 64
  boxes and keeps them by SAM's stability / predicted IoU, then merges overlapping boxes; (3) the hub check works on
  its ring crop (r 253, grey picture per gamma), the window on the r 300 disc.
- **Changed:** `HubCentreSegmenter.warmUp()` runs at load: YOLO loaded (through `ModelManager`) and both models run
  once on a blank disc, as the vision check worker does; the status line says "YOLO ready; warm-up n s" or why YOLO is
  not available (grid and ring modes still work). The YOLO line now splits the time: YOLO, SAM embedding, decoder +
  filters, and the total with measuring and drawing.
- **Run:** with a stand-in YOLO (no weights here) and the real MobileSAM on the sandbox CPU: warm-up 1.0 s; two
  pictures in a row 1.25 - 1.26 s each (embedding 0.94 - 0.96 s, decoder + filters 0.29 - 0.31 s, measuring and drawing
  0.01 - 0.02 s). Without YOLO the note says so and the window starts. Window defaults read back under Xvfb as above.

## 136. Hub centre mask window: cap-finding defaults, min area, refinement from the first mask's outline, CSV of every value (2026-11-18)

- **User (laptop, fast now):** the settings of the screenshot find the cap's outline almost perfectly - make them the
  defaults; add a minimum area to drop small masks; from the first mask's outline make fresh point prompts (not YOLO)
  that pin the cap mask down better; print every detail for a laptop run.
- **Defaults:** gamma 2.0, unticked, disc radius 160, YOLO boxes, YOLO confidence 0.15, min stability 0.70, min predicted
  IoU 0.50, box merge IoU 0.50, 3 s between pictures; new "Min area" 1000 px (masks kept to the disc and smaller than
  this are dropped and counted).
- **Refinement (`HubCentreSegmenter.refine`, ticked at start):** from the largest kept mask's fitted-ellipse centre and
  radius: positive points on rings at 0.5 and 0.8 x the radius and negative points on a ring at 1.15 x (inside the
  crop), every 30 deg, plus the mask's bounding box grown by 4 px; one decoder call on the same embedding (no new
  embedding in YOLO mode); MobileSAM's 3 masks. Each listed with score, area, fitted circle, the outline's distance
  from that circle (RMS / max), IoU with the first mask and how far the circle centre moved; the highest-scoring one
  drawn white, positive points blue, negative black. Rings, negative ring, angle step and the box are editable.
  `measure()` now also fits a least-squares circle to every outline (centre, radius, RMS / max off it).
- **Details files:** every run writes `<folder>/hub_centre_mask_results/details_<time>.csv` (fixed 75 columns: settings,
  YOLO boxes found / on the disc / passing / after merging, the times, the first mask, the 3 refined masks) and
  `run_<time>.txt` (every line of the list); batch the same. `--batch`: `--min-area`, `--no-refine`, `--refine-rings`,
  `--refine-neg`, `--refine-step`, `--refine-no-box`.
- **Run (sandbox, real MobileSAM, a stand-in YOLO box round the cap - no YOLO weights here), gamma 2.0:** 4 seated silver
  caps (DOST_cap_01-04): first mask r 102.5-103.6, outline off its circle RMS 0.79-2.10 px; refined masks within
  0.1-1.7 px of its centre, IoU 0.95-0.99; refined mask 2 (the middle one) had the smallest RMS on 3 of 4 (0.65-2.01 px);
  the highest score always picked mask 3 (r about 1 px larger, RMS like the first). Skewed caps 01-03: refined masks
  moved 0.6-5.9 px and shrank (DOST_skewed_02: masks 1-2 r 100.0-100.6, 5.6-5.9 px off the first mask, RMS 1.44-1.54 px
  against 3.77) - worth watching on the laptop data. Refine 0.09-0.16 s on CPU. Window under Xvfb with stand-ins:
  defaults as above, a small mask dropped by the min area, refine lines, 2 CSV rows of 75 columns, the log file.

## 137. Hub centre mask window: the disc's own edge dropped; cap mask by colour (brass: refined mask 1, silver: first mask) (2026-11-18)

- **User's laptop run (29 cap pictures, real YOLO, defaults of §136):** "the white mask is slightly worse than the
  original". Checked from the run's lines:
  - on 6 of 29 the "first mask" was the cut-out disc itself (r 158.9: blacking out everything outside the disc makes
    its edge an object); the refinement then worked on the disc, not the cap;
  - on the other 23, the white mask (refined mask with the highest SAM score) was rougher than the first mask on 7 of
    11 silver and 7 of 12 brass caps (outline off its fitted circle, mean RMS silver 2.56 vs 2.27 px, brass 2.13 vs
    1.78; worst point 8.1 vs 7.1 and 6.6 vs 5.4 px). Silver: no refined mask consistently better (mask 2 2.21 vs
    2.27). Brass: refined mask 1 the roundest on 7 of 12 (mean RMS 1.48), r about 83.2 - 83.5 on the seated brass caps -
    as the brass edge fit of CHANGES 128 (82.9 - 84.3) - where the first mask (r 85.7 - 86.3) takes in a little of the
    gap / rim. (Roundness is not proof of accuracy: a tilted cap is not round.)
- **User's decision:** fix the disc mask; brass: refined mask 1.
- **Now (both copies):** a mask whose fitted circle has a radius above 0.9 x the disc's is dropped and counted ("the
  disc's own edge"). The cap's colour from the mean R-B inside the first mask on the picture as taken (before the
  gamma), against the cap check's brass limit (`CheckCap.RB_THRESHOLD_DOSTPLUS`, 35 on Machine 2): brass -> the cap
  mask is refined mask 1; silver -> the first mask; drawn white and named in a line with its fitted circle and RMS.
  The pick by highest score is gone; the 3 refined masks are still listed. CSV: `dropped_disc_edge`, `cap_rb`,
  `cap_colour`, `cap_mask` (78 columns).
- **Run (sandbox, real MobileSAM, stand-in YOLO boxes):** a disc-sized mask dropped as the disc's edge, the cap then the
  first mask; DOST_cap_01 / 06 R-B -9.4 / -15.6 -> silver, first mask (r 102.5 / 103.0); DOSTPLUS_cap_01 / 06 R-B 70.6 /
  72.3 -> brass, refined mask 1 (r 83.3 / 83.8, RMS 0.40 / 0.67 px against the first mask's r 87.7 / 85.9).

## 138. Hub centre mask window: the second set of prompts dropped; two-stage ellipse fit to the cap mask's outline (2026-11-18)

- **User (second laptop run, 58 pictures: the disc's own edge now dropped on 12; cap mask the first mask on 32, refined
  mask 1 on 26):** drawing the second mask does not help - drop that step; instead fit an ellipse to the first mask's
  points: first by least squares (sum of squared distances between the points and the ellipse), then refined to have
  the most points within 2 - 3 px of it.
- **Now (both copies):** the refinement by point prompts is out of the flow, the window and the CSV
  (`HubCentreSegmenter.refine` kept, unused, as are its settings keys); the cap mask is always the first mask (its
  R-B colour still reported). New "Fit an ellipse to the cap mask" (ticked at start), on the mask's outline (its
  largest outer contour, every pixel):
  1. least squares: start `cv2.fitEllipseDirect`, then scipy `least_squares` (Levenberg-Marquardt) on each point's
     true distance to the ellipse (`ellipseDistances`: the closest point by Newton on the ellipse's angle parameter);
  2. most points within the "inlier tolerance" (default 2.5 px, 0.5 - 5): the stage-1 ellipse and 300 RANSAC ellipses
     ("RANSAC tries", fixed seed; each through 5 outline points) compete for the most points within the tolerance;
     the winner is refitted by least squares to its inliers while the count rises (at most 10 rounds).
  Each stage listed (centre, semi-axes, ratio, angle, RMS distance of all points, points within the tolerance; stage 2
  also the RMS of its inliers and how far its centre is from stage 1); stage 1 drawn thin black, stage 2 white. CSV:
  `ell1_*`, `ell2_*` (centre, semi-axes, angle, RMS, inliers, fraction), `ell2_inlier_rms`, `ell2_rounds`,
  `ell_centre_shift`, `t_ellipse`. `--batch`: `--no-ellipse`, `--tolerance`, `--tries`.
- **Run:** synthetic ellipse (centre 300,300, semi-axes 103 / 99, 30 deg; 650 points, 1 px noise, a 40-deg stretch
  pushed 8 px out): stage 1 centre (299.5, 301.6), 102.8 / 101.1, 55.9 deg (pulled by the bulge); stage 2 (300.0,
  299.9), 103.1 / 99.0, 29.5 deg, 571 of 650 within 2.5 px. Cap pictures (real MobileSAM, stand-in YOLO box; 0.11 -
  0.14 s per fit on CPU), points within 2.5 px stage 1 -> stage 2: DOST_cap_01 99.0 -> 99.5 %, DOST_cap_03 90.5 ->
  91.3 %, DOST_skewed_02 61.5 -> 76.9 % (centre 3.8 px from stage 1), DOST_skewed_03 94.4 % both, DOSTPLUS_cap_01
  75.6 -> 86.4 %, DOSTPLUS_skewed_01 77.0 -> 81.9 %. RANSAC ellipses normalised (a >= b, angle of a in 0 - 180) after
  a first run showed ratio 1.08. Window under Xvfb with stand-ins: the controls and defaults, no refine controls.

## 139. Cap mask method frozen: YOLO boxes on the disc, first mask, two-stage ellipse fit (2026-11-18)

- **User (laptop run of CHANGES 138, 29 cap pictures of 13 Nov):** the fit finds the cap's outline almost exactly in
  most cases; one stray case (the 14th picture, `..._20261113152502157`) where the cap was so tilted that the first
  mask, and so the fit, were wrong. Freeze this as the method for the cap mask unless there is a further improvement.
- **Run, read from the log:** 16 silver (R-B -17.0 to -1.0), 13 brass (66.3 to 82.6); 0.40 - 0.54 s for the masks,
  0.11 - 0.14 s per ellipse fit (0.52 s on the first: scipy warming up), 0.52 - 0.89 s per picture in all. Points
  within 2.5 px, stage 1: 91 - 100 % on 21 pictures; 84 - 88 % on 3 (the 9th, 11th, 27th); 76 - 79 % on 2 (the
  15th, 21st); low on 3: the 12th (`452377`, 40.3 -> 59.4 % in stage 2, mask area 27807 against about 33000, ratio 0.92),
  the 14th (`502157`, 45.3 -> 61.4 %, 739 outline points against about 600, mask stability 0.896, roundness 0.878) and
  the 16th (`516872`, 52.1 -> 71.0 %, stage-2 centre 6.1 px from stage 1). Stage 2 equals stage 1 on 16 of 29; it
  gains up to 19 points of share only where the outline is not one ellipse.
- **Why no further step:** on the 14th the mask itself is wrong; an ellipse fit cannot correct a wrong mask, and
  prompting SAM again from it is the step dropped in CHANGES 138. A mask that is not one ellipse shows as a low share
  of points within the tolerance (and a low mask stability / roundness), which a seating rule can use directly: a
  badly tilted cap is NOT OK whichever way its mask goes wrong. That rule needs the pictures labelled seated / skewed
  (the raw camera names carry no label) before a limit is set.
- **Now:** no change of logic. The window's docstring (both copies) records the frozen method and its values, and its
  stale defaults are corrected (gamma 2.0, disc 160, 64 points per side, YOLO confidence 0.15). Production
  (`camera/CheckCap.py`) is unchanged; moving the method there is a vision logic change (reference commit, both
  manifests, verifier).
- **Run:** both copies compile and are identical apart from paths.

## 140. Hub centre mask window: min area 15000, new max area filter (2026-11-18)

- **User:** min area to 15000; add a max area filter (20000 - 150000 in steps of 10000), applied after the disc's own
  edge is dropped.
- **Now (both copies):** `WHOLE_DEFAULTS` `minArea` 15000, new `maxArea` 50000. In `processWhole` the order is: less
  than half inside the disc, smaller than the min area, the disc's own edge (radius > 0.9 x the disc's), then larger
  than the max area (counted as "larger than the max area" in the picture's line; CSV `max_area`, `dropped_large`).
  Window: "Max area (px)" spinner 20000 - 150000, step 10000, beside Min area. `--batch`: `--max-area`.
  `wholeSettings` refuses a max area not above the min area. Default 50000, not 20000: the caps in the CHANGES 139 run
  are 22112 - 23660 px (brass) and 27807 - 33873 px (silver), so 20000 would drop every cap.
- **Run:** stand-in segmenter, five discs on a 160 disc (areas about 5000, 20000, 33000, 49000 and the disc's edge):
  max 50000 keeps 3 (1 small, 1 edge, 0 large), max 40000 keeps 2 (1 large), max 150000 keeps 3; min 30000 / max
  20000 refused. Window under Xvfb (Python 3.12, stand-ins), both copies, 4/4: min area 15000, max area 50000, range
  20000 - 150000 step 10000 (up -> 60000, top 150000, bottom 20000), both reach the settings. Both copies identical
  apart from paths. MobileSAM / YOLO not re-run (filter only).

## 141. Hub centre mask window: min stability and min predicted IoU down to 0.20 (2026-11-18)

- **User:** allow min stability down to 0.2 (default 0.7) and min predicted IoU down to 0.2 (default 0.5).
- **Now (both copies):** spinners and `wholeSettings` checks 0.20 - 1.00 for both (step 0.02 unchanged); defaults
  unchanged (0.70, 0.50). Both prompt paths take the values as set (grid: `pred_iou_thresh` /
  `stability_score_thresh` of the generator; YOLO boxes: the keep mask in `_yoloMasks`); no other floor.
- **Run:** window under Xvfb (Python 3.12, stand-ins), both copies, 4/4: stability default 0.70, bottom 0.20, top 1.00;
  IoU default 0.50, bottom 0.20, top 1.00; 0.20 / 0.20 reach the settings; 0.18 refused. Copies identical apart from
  paths. MobileSAM / YOLO not re-run (limits only).

## 142. DryRun_Cap_Centre_Mask_UI: the frozen cap mask method in its own window (2026-11-18)

- **User:** copy the frozen cap mask window as `DryRun_Cap_Centre_Mask_UI.py`; the hub centre window goes on as a
  study for the hub's centre (his laptop settings there: gamma 4.0, YOLO confidence 0.05, box merge 0.40, area
  12000 - 40000: a mask on every picture, close on DOST, less so on DOSTPLUS).
- **Now (both copies):** `DryRun_Cap_Centre_Mask_UI.py` = `DryRun_Hub_Centre_Mask_UI.py` as of CHANGES 141 (code
  unchanged: defaults gamma 2.0, disc 160, YOLO confidence 0.15, stability 0.70, IoU 0.50, merge 0.50, area 15000 -
  50000, ellipse fit 2.5 px / 300 tries), with its own name in the docstring and usage lines, default folder
  `test/machine2_dryrun/cap_images`, results in `cap_centre_mask_results/`, window title "Cap centre masks". README
  rows added.
- **Run:** both copies compile, identical apart from paths; window under Xvfb (Python 3.12, stand-ins) 4/4 on both
  (the CHANGES 141 spinner checks).

## 143. Cap seating: the cap's centre against the hub's centre from the cycle's hub check (2026-11-18)

- **User:** take the hub's centre from `checkHub()` (centre of its 4 / 5 holes or bolts), keep it in the camera server
  for the later checks (cap, later the bung); find the cap mask by the frozen method and reject the cap when its centre
  is more than 4 - 5 px from the hub's centre; the colour steps unchanged. Use the stage-2 ellipse centre, not the mask
  centroid. The part leaves station 1 between the hub and cap pictures but comes back to exactly the same place (servo,
  sensors); the differences between parts are LHS / RHS seating and the fixture's +/- 0.2 mm, and the rotation.
- **Hub centre** (`CheckHubAndBottomBearing`): new `hubCentreFromGroup(group, annularCentre, outerRadius)` - the
  least-squares circle through the winning group's mask centres, moved from the segmenter's cropped picture to full
  picture pixels by the crop's corner (as `extract_annular_region` cuts it); `kind` 'bolts' when the circle radius is
  at least `vision.hub.centre.bolt.min.distance` (165 px: holes about 147, bolts about 182 px on DOST and DOSTPLUS
  pictures, measured by hand), else 'holes'; `count`, `spread`. Set as `lastHubCentre` by a check that passes (None at
  the start of every check); logged `[CheckHubAndBottomBearing] hub centre (x, y) from N holes|bolts (circle r, spread)`.
  The check's verdict is unchanged.
- **Camera server:** `ImageProcessor.hubCentre` (new state attribute: in the snapshot sent to the worker, the changes
  sent back, the state file - a restarted camera server takes it back -, its restart line); set from `lastHubCentre`
  after a hub check that passes, None before every hub check, after one that fails and at the end of a cycle. Passed to
  `CheckCap.checkCap(..., hubCentre=)` on both cap paths (`_assessCap`, `ImageProcessor.checkCap`).
- **Cap mask** (new `camera/CapMaskFinder.py`): the method of `DryRun_Cap_Centre_Mask_UI.py` (CHANGES 139 - 142) for
  production - gamma 2.0, disc r 160 round `cap.center` (which follows the knuckle frame), YOLO boxes (ModelManager's
  object-aware model, conf 0.15) centred on the disc as box prompts through CheckCap's MobileSAM predictor, stability >=
  0.70, predicted IoU >= 0.50, box merge 0.50, kept to the disc, dropped: less than half inside / under 15000 px / the
  disc's own edge (circle r > 0.9 x 160) / over 50000 px; the largest left; two-stage ellipse fit (least squares, then
  most outline points within 2.5 px: 300 RANSAC tries, fixed seed, refits). Keys `vision.cap.mask.*`.
- **Seating rule** (`CheckCap.hubCentreDistance`, both cap colours, after the colour rules; `checkSeating` = the colour
  rules (`_seatingByColour`, the former body, unchanged) AND this rule): with `vision.cap.seating.hub.distance.check` and
  a hub centre, distance from the stage-2 ellipse centre to hub centre + `seating.hub.distance.offset.holes` / `.bolts`
  must be <= `seating.hub.distance.max` (5.0 px). No cap mask -> NOT seated (reason logged); no hub centre -> rule not
  applied (said in the reason). The seating line gives both centres, the distance, dx / dy, the offset and every mask
  value. Marked picture: the cap ellipse green / red, the hub centre a blue cross; no text. Machine 2 on, Machine 1 off
  (its seating is off anyway). Offsets 0, 0 and the 5 px limit are not measured: no hub and cap pictures of the same part.
- **Reference commits:** `5250ceb` (CheckCap, CapMaskFinder, CheckHubAndBottomBearing with literal values) and
  `1d199cd` (CheckHubAndBottomBearing with its five hub-centre-check values inline, as its baseline had them, so the
  extracted entries still match; their `old_line` moved to 187 / 188 / 199). Both manifests point at them.
- **Run:** `Verify_Vision_Config_Equivalence.py` ALL EQUIVALENT on both machines (308 parameters, 12 files). New
  `DryRun_Cap_Hub_Distance.py` (both copies): Machine 2 10/10 (H1-H5 hub centre, S1-S2 state, C0 the production cap
  mask equals the frozen window's on all 30 cap pictures, C1 the rule, C2 checkCap through `assessPictures`: hub centre
  on the cap OK, 10 px away NOT OK, none OK); Machine 1 8/8 (C1 / C2 skipped, seating off). Stand-in YOLO boxes in the
  sandbox (no YOLO weights here), so C0 proves the port, not YOLO; findCap took about 4 - 5 s per picture on this CPU.
  Unchanged: `DryRun_Cap_Production_Path` 12/12, `DryRun_Cap_Silver_Seating` 6/6, `DryRun_Cap_Check` all as expected
  (none of them sets a hub centre: rule not applied), `DryRun_Camera_State_File` 19/19 both, `DryRun_Vision_Check_Timeout`
  23/23. Not run: the real hub check (needs YOLO), `DryRun_Knuckle_Frame.py --quick` (stopped at 500 s on this CPU).
- **Known before the first run:** CHANGES 96 measured the stud (bolt) circle's centre about 7 px above the hole
  circle's (the bolt tips are nearer the camera), and the seated cap outlines of CHANGES 99 sit a few px above the
  hole circles too. With both offsets 0, a cycle whose hub check won on the other group than the cap face lines up
  with can be off by about 7 px, above the 5 px limit: the offsets have to be measured (hub check line + seating line
  of the same cycles) before the rule is relied on.

## 144. Cap ellipse independent of where the outline lies; DryRun_Cap_Hub_Distance_UI (2026-11-18)

- **User (laptop, CUDA, real YOLO):** `DryRun_Cap_Hub_Distance.py` 9/10 - C0 failed on DOST_cap_08: same cap mask (33099
  px) but stage-2 centre (642.58, 339.12) in production against (641.93, 339.40) in the frozen window. Verifier ALL
  EQUIVALENT on both machines; `DryRun_Cap_Production_Path` 12/12 (0.47 - 0.94 s per cap check on CUDA). Asked for a
  window: pick a hub picture, run the hub check, get the hub centre; pick a caps folder, run the cap check on each
  with that hub's centre and the model chosen; print everything needed to set the parameters.
- **Cause of the C0 failure:** the window fits the ellipse to the outline in the cut-out disc's coordinates, the cap
  check in the full picture's (the same outline shifted by the disc's corner). `cv2.fitEllipse` works in float32, so
  the 5-point RANSAC ellipses come out slightly different at the two offsets, and where two candidates have nearly the
  same count of points within 2.5 px a different one wins (DOST_cap_08: 562 of 588 either way, centres 0.7 px apart).
  Reproduced: of 60 ragged synthetic ellipses, 15 gave a different stage-2 ellipse in the disc and in the full picture.
- **Fix** (`CapMaskFinder.fitCapEllipse` and the `fitCapEllipse` of both centre-mask windows, both copies): the outline
  points are moved to a whole-pixel origin near their middle (`floor(mean)`) before both stages and the ellipses moved
  back: the same outline gives the same ellipse wherever it lies (0 of 60 differ). Reference commit `fb8f11f`, both
  manifests point at it.
- **New `DryRun_Cap_Hub_Distance_UI.py`** (both copies; window and `--batch`): Model (DOST / DOSTPLUS, the QR code both
  checks get); "Hub picture..." runs the real hub check (marked picture shown with the hub centre as a blue cross; the
  centre's x, y and holes / bolts filled in, editable or typed without a hub picture); "Caps folder..." + Start runs the
  real `CheckCap.checkCap` as the camera server calls it (seating on, hub centre given; the step-3 earlier pictures:
  the chosen hub picture and the model's stored top bearing / nut pictures, or chosen ones). "Max distance" and the
  holes / bolts offsets can be changed between pictures. Per cap: verdict, time, the colour rules' seating verdict
  (copied before the distance rule is added), the distance (cap centre, hub centre + offset, dx / dy), mask area /
  stability / IoU, outline points within 2.5 px, ratio, RMS and every log line of the check; a cap the check stopped
  before seating still gets the distance measured ("measured only"). At the end, from the caps the colour rules call
  seated (names without "skewed"): dx / dy statistics per hub kind with the offset taken out, the suggested offset
  (median) and max (largest deviation + 1.5 px), and each skewed cap against them. `run_<time>.txt` and
  `details_<time>.csv` in `<caps folder>/cap_hub_distance_results/`; marked pictures with "Save marked pictures".
- **`DryRun_Cap_Hub_Distance.py`:** new C0b - the same outline in the disc and in the full picture gives the same
  stage-2 ellipse (20 synthetic outlines).
- **Run:** verifier ALL EQUIVALENT on both machines. `DryRun_Cap_Hub_Distance_UI.py --batch` (sandbox: real MobileSAM,
  stand-in YOLO boxes, hub centre typed 644.0, 333.0, holes - no hub check here, it needs YOLO) on the 16 DOST cap
  pictures: OK 6, NOT OK 10; 9 seated by the colour rules; against one fixed hub centre the seated caps' dy spreads from
  -4.7 to +6.5 px (different parts: what the per-part hub centre is for), so the suggested max came out 8.6 px - not a
  value to use, the pairing is wrong; skewed 04 / 05 / 06 at 10 - 18 px, 01 / 02 / 03 within 1 - 3 px (caught by the
  colour rules, not by the distance). Window under Xvfb (Python 3.12, checks stood in), both copies, 7/7: models loaded
  and the defaults from the cap settings, the hub check fills the centre, 4 caps checked with that centre / max /
  offsets, elapsed time shown, run file with hub line, caps and suggested offset / max, CSV rows, list.
  `DryRun_Cap_Hub_Distance.py` (sandbox, stand-in YOLO): Machine 2 11/11 (C0 on all 30 cap pictures, C0b), Machine 1 9/9
  (C1 / C2 skipped, seating off).

## 145. Hub-centre offsets set from the laptop run of DryRun_Cap_Hub_Distance_UI (2026-11-18)

- **User (laptop, real hub check and YOLO):** the window on the 29 cap pictures of 13 Nov, once as DOST with hub picture
  `..._20261113151843774` (hub centre (644.5, 334.4) from 4 bolts) and once as DOSTPLUS with `..._20261113153023353`
  ((643.7, 342.0) from 5 holes). Cap check 0.78 - 1.03 s (silver), 0.49 - 0.56 s (brass) per picture on CUDA.
- **Seated caps (colour rules SEATED), cap centre - hub centre:** DOST, bolts, 6 caps: dx -0.18 .. +1.81, dy -4.11 ..
  -2.25 (median +0.26, -2.73); DOSTPLUS, holes, 6 caps: dx -0.42 .. +0.69, dy -3.58 .. -0.75 (median +0.35, -2.67).
  Both models give the same offset, about (0.3, -2.7): the cap centre is seen about 2.7 px above the hub centre.
- **With offset (0.3, -2.7) and max 5.0:** seated 0.2 - 2.0 px (all OK); skewed silver 8.0 - 65.4 px (NOT OK) except
  `..._152441953` at 1.7 px (tilted in place: caught by the face rule, 0.50 < 0.64); skewed brass 14.3 - 18.4 px (all
  NOT OK, as by the bore rule). The window's own suggestion (largest seated deviation + 1.5 = 3.2 / 3.4 px) is not
  taken: the 6 seated caps of each model are consecutive pictures (15:24:12 - 15:24:25, 15:34:46 - 15:35:20), probably
  one to three parts, and each model had one hub picture, not of the same part - the part-to-part spread is not yet
  measured. 5.0 keeps 3 px of room over the seated caps and still lies far below the nearest pushed-aside cap (8.0).
- **Now:** `siemens.vision.cap.seating.hub.distance.offset.holes` and `.offset.bolts` = 0.3,-2.7 in the three
  properties files (max stays 5.0; allenbradley unchanged). Holes and bolts get the same offset because this run had
  bolts only on DOST and holes only on DOSTPLUS; CHANGES 96 measured the stud circle about 7 px above the hole circle
  on the same hub, so a model whose hub check wins on the other group may need its own value - to be seen in the hub
  check's `from N holes|bolts` lines with the seating lines of the same cycles.
- **Run:** verifier ALL EQUIVALENT on both machines; the rule recomputed from the two CSVs with the new offset (above).

## 146. Holes offset 0.1,-1.4 from 105 brass caps against two DOSTPLUS hub pictures (2026-11-18)

- **User (laptop):** `DryRun_Cap_Hub_Distance_UI` as DOSTPLUS with hub picture `..._20261113153828283` (centre (644.2,
  339.4) from 5 holes) on (a) the 29 cap pictures of 13 Nov and (b) 117 production pictures of 17 Nov (folder "Good Cap
  - BUT MOST Not OK", three per cap check).
- **Results:** (b) 93 of 93 brass caps OK (31 checks x 3 pictures); the 24 NOT OK are 21 silver caps checked as DOSTPLUS
  (wrong cap, correct) and one check without a cap (3 pictures). (a) the 6 seated brass caps OK, the 7 skewed NOT OK.
- **What the second hub picture shows:** the two DOSTPLUS hub pictures put the hub centre 2.6 px apart in y ((643.7,
  342.0) in CHANGES 145, (644.2, 339.4) now), and the same 6 seated caps of 13 Nov came out at median cap - hub (0.35,
  -2.67) against the first and (-0.15, -0.07) against the second. Against one fixed hub picture the 93 seated caps of
  17 Nov scatter dx -2.5 .. +1.2, dy -2.7 .. +2.1. So the offset of CHANGES 145 (0.3, -2.7) was mostly the first hub
  picture's own position; the true part-to-part behaviour still needs hub and cap pictures of the same cycle.
- **Now:** `siemens.vision.cap.seating.hub.distance.offset.holes` = 0.1,-1.4 (between the two hub pictures; three
  properties files). Largest seated distance over the three runs: 2.2 / 3.3 / 3.5 px (with 0.3,-2.7: 2.0 / 4.6 / 4.8 px,
  too near the 5 px limit). Bolts stay 0.3,-2.7 (DOST data from one hub picture only). Max stays 5.0.
- **Run:** verifier ALL EQUIVALENT on both machines; distances recomputed from the three CSVs (above).

## 147. Hub-centre offsets per hub kind and model; DOSTPLUS bolts -0.6,2.2 (2026-11-18)

- **User (laptop):** `DryRun_Cap_Hub_Distance_UI` as DOSTPLUS with hub picture `..._20261113153829194` (centre (644.0,
  336.9) from 5 bolts) on the 117 production pictures of 17 Nov: 46 of 93 brass caps OK, 47 NOT OK - all 47 SEATED by
  the colour rules, failed only by the distance (5.2 .. 7.2 px) against the bolts offset 0.3,-2.7.
- **Found:** raw cap - hub (offset taken out) for the 93 seated brass caps: dx -2.30 .. +1.43, dy -0.21 .. +4.57 (median
  +0.61, +2.38). The DOST silver caps of CHANGES 145 against a DOST bolts hub: dy -2.25 .. -4.11. One bolts offset
  cannot hold both: the best common centre (0.0, 0.25) leaves 4.4 px of the 5.0 for spread; -0.6,2.2 would put the DOST
  caps at up to 6.5 px. The silver and brass cap faces (and the DOST / DOSTPLUS bolts) are not at the same heights, so
  the camera's slight angle shifts them differently.
- **Changed:** `CheckCap` offsets per hub kind and model - keys `vision.cap.seating.hub.distance.offset.holes.dost`,
  `.holes.dostplus`, `.bolts.dost`, `.bolts.dostplus` (replacing `.offset.holes` / `.offset.bolts`); `CheckCap.hubOffset
  (kind, model)`; `hubCentreDistance(anImage, hubCentre, model=DOST)`, called by `checkSeating` with the model found on
  the cap; the seating line names the model. Reference commit `cc8a42c` (both manifests).
- **Values (siemens, three properties files):** DOSTPLUS bolts -0.6,2.2 (the centre that minimises the largest distance
  of the 93 caps), DOSTPLUS holes 0.1,-1.4 (CHANGES 146), DOST bolts 0.3,-2.7 (CHANGES 145), DOST holes 0.3,-5.2 - not
  measured: DOST bolts minus the 2.5 px by which the bolt circle sits above the hole circle on the DOSTPLUS hub pair of
  13 Nov. Allenbradley: all 0,0 (check off). Max stays 5.0.
- **Measured with the new value:** the 93 seated brass caps of this run at 0.46 .. 2.95 px from the target, all OK; the
  24 NOT OK left are the 21 silver caps checked as DOSTPLUS and the check without a cap, as before.
- **Caveat:** each bolts value comes from one hub picture, and two hub pictures of the same DOSTPLUS hub differed by
  2.6 px (CHANGES 146); hub and cap pictures of the same cycle are still needed to separate this from part-to-part spread.
- **Dry runs:** `DryRun_Cap_Hub_Distance_UI.py` sets the chosen model's pair, its offset boxes follow the Model choice,
  `--offset-holes` / `--offset-bolts` default to the model's values, the summary names the key per model;
  `DryRun_Cap_Hub_Distance.py` C1 also checks that DOSTPLUS takes its own holes offset and not its bolts offset; C2
  now puts the hub at the cap centre minus the configured DOST holes offset (it had assumed an offset near zero and
  failed with 0.3,-5.2).
- **Run:** verifier ALL EQUIVALENT on both machines; `DryRun_Cap_Hub_Distance` Machine 2 11/11, Machine 1 9/9;
  `DryRun_Cap_Production_Path` 12/12 (Machine 2); `DryRun_Camera_State_File` 19/19 on both; `DryRun_Cap_Check` Machine 1
  all as expected (Machine 2 needs the YOLO weights: laptop); window test of `DryRun_Cap_Hub_Distance_UI` 8/8 on both
  (xvfb, checks stood in). Distances recomputed from `details_20261118-163312.csv` (above).

## 148. DOST holes offset measured: 0.3,0.5 (the 0.3,-5.2 of CHANGES 147 was wrong) (2026-11-18)

- **User (laptop):** `DryRun_Cap_Hub_Distance_UI` as DOST, hub picture `..._20261113151830811` (centre (643.8, 338.8)
  from 4 holes), the 117 production pictures of 17 Nov. 8 OK, 109 NOT OK. Silver caps (all seated, user): 21 pictures;
  colour rules SEATED on all 21; 8 OK, 13 NOT OK by the distance alone (5.2 .. 6.7 px against offset 0.3,-5.2). The
  other NOT OK are brass caps checked as DOST (wrong cap, correct) and one cycle without a cap.
- **Found:** raw cap - hub for the 21 silver caps: 18 at dx -1.5 .. +2.1, dy -0.8 .. +1.3; 3 (cycle 19:19:43) at
  dy -9.6 .. -10.8. So the DOST holes offset is about (0.25, 0.55), not (0.3, -5.2). The CHANGES 147 value was worked out
  from the DOST bolts offset (one hub picture) and the DOSTPLUS holes-vs-bolts gap, and was wrong by about 5.7 px.
- **The 3 pictures of 19:19:43:** their own colour rules put the cap outline 10.8 - 11.0 px from `cap.center`
  (1.1 - 2.5 px on the other 18), with the hub uncovered 0 px and the face fit 0.75 - 0.77: the whole part sat about
  10 px higher in that cycle, so that cycle's own hub picture would be too. Against a hub picture of another part
  they cannot be judged. They stay NOT OK in this pairing (9.9 - 11.1 px). Making them OK here would need max 11 px
  (or an offset 5 px off the other 18), which would let skewed caps through. Not changed. To check: run the window
  with the hub picture of that cycle (TrialImages `HubAndBottomBearing`, 17 Nov about 19:19).
- **Now:** `siemens.vision.cap.seating.hub.distance.offset.holes.dost` = 0.3,0.5 (three properties files). The 18
  seated silver caps are at 0.07 - 2.0 px from the target, all OK; max stays 5.0.
- **Run:** verifier ALL EQUIVALENT on both machines; distances recomputed from `details_20261118-171917.csv`;
  `DryRun_Cap_Hub_Distance.py` Machine 2 11/11 (C2 uses the configured offset).

## 149. DOST bolts offset measured: -0.4,4.9; two seating positions of the DOST part in the cap pictures (2026-11-18)

- **User (laptop):** `DryRun_Cap_Hub_Distance_UI` as DOST with two bolts hub pictures (`..._20261113151835968`,
  centre (644.3, 334.5); `..._20261113151833451`, (644.5, 334.4)) on the 117 production pictures of 17 Nov. Only the 3
  silver caps of cycle 19:19:43 OK; the other 18 seated silver caps NOT OK (6.0 - 8.6 px against offset 0.3,-2.7).
- **Found:** raw cap - hub for the 18: dx -2.2 .. +1.6, dy +3.3 .. +5.7, the same in both runs. Best offset (smallest
  largest distance over both runs): (-0.35, 4.9); with -0.4,4.9 they lie 0.2 - 2.0 px from the target.
- **Cross-check with the holes run (CHANGES 148):** holes offset dy +0.5, bolts +4.9: 4.4 px apart; on the DOST hub
  pictures the bolt circle's centre sits 4.3 px above the hole circle's ((643.8, 338.8) vs (644.3, 334.5)). Both
  offsets put the same caps on the same point.
- **Two positions of the DOST part.** Cycle 19:19:43 (dy -6.5 .. -5.2 here, 3 pictures) and the 6 seated silver caps of
  13 Nov used for CHANGES 145 (dy -4.1 .. -2.3 against hub `..._151843774`) show the cap about 10 px higher than the 18
  caps of 17:04 - 18:44: the cap's own seating line puts its outline 10.8 - 11.0 px from `cap.center` (1.1 - 2.5 px for
  the 18). So the DOST part sat in two places (LHS / RHS, or two parts on the fixture; CHANGES 102 measured DOST parts
  3 - 7 px apart), and every hub picture available is of one of them. That is why CHANGES 145 got 0.3,-2.7 (13 Nov caps
  of the high position against a hub of the low one) and why one offset per hub picture cannot pass both groups.
  In production the cap is compared with the hub picture of its own cycle, so the part's position cancels out; the
  offset only has to hold the camera-angle part, which is what the 18 measure (their hub pictures and caps are both
  of the low position: the cap outline 1.1 - 2.5 px from `cap.center`, as the hub centre is near it).
  Not proven yet: needs a hub picture and cap pictures of the same cycle for the high position (19:19:43).
- **Now:** `siemens.vision.cap.seating.hub.distance.offset.bolts.dost` = -0.4,4.9 (three properties files). Max 5.0.
  The 3 caps of 19:19:43 and the 13 Nov caps now read 7 - 11 px against these hub pictures (pairing, see above).
- **Run:** verifier ALL EQUIVALENT on both machines; distances recomputed from `details_20261118-175424.csv` and
  `details_20261118-175837.csv`. `DryRun_Cap_Hub_Distance` not re-run: C1 / C2 set their own offsets or use the DOST
  holes offset, which did not change.

## 150. DOST holes runs 180531 / 181057 explained; cap / hub window pairs each cap with its own cycle's hub picture (2026-11-18)

- **User (laptop):** DOST, holes, one hub picture (`..._20261113152706609`, centre (644.1, 341.9)), offset 0.3,0.5.
  Run 180531 (17 Nov caps): all silver caps should be OK. Run 181057 (13 Nov `Machine 2/Cap`): the first 6 silver caps
  OK, all next ones NOT OK.
- **Found:**
  - 180531: the 18 seated caps of 17:04 - 18:44 are OK (3.05 - 4.77 px); the 3 of 19:19:43 are NOT OK (13.2 - 14.4 px).
  - 181057: the 6 seated caps are NOT OK by the distance (10.2 - 12.1 px, raw dy -9.75 .. -11.6); the other 10 are
    NOT OK by the colour rules already (face fit 0.45 - 0.59, or no cap found), so they need nothing from the distance.
  - Against this one hub picture the 27 seated caps spread over 12 px: the smallest circle holding them all has radius
    6.12 at (-1.2, -7.9). The skewed cap `..._152441953` is 4.6 px from that point - inside it. So no offset and max
    give the expected verdicts on both files with one hub picture.
  - Cause: the high / low positions of the DOST part (CHANGES 149). The 13 Nov caps and cycle 19:19:43 are of the high
    position, the hub picture and the 18 caps of the low one. The hub pictures of different parts also differ by
    about 3 px (centres 338.8 vs 341.9 on DOST holes). In production every cap is measured against the hub picture of
    its own cycle, so the part's position cancels out; the window did not do that.
- **Changed:** `DryRun_Cap_Hub_Distance_UI.py` (both folders) - pairing by cycle:
  - window button "Hub folder (pair by time)..."; batch `--hub-folder DIR [--pair-max-secs 300]`;
  - each cap picture gets the latest picture in the hub folder taken before it and at most 300 s before it, by the
    time in the file names (trial `2026-11-17-17-04-00-749671-0.png`, camera `<ip>_01_20261113152706609.jpeg`);
  - the hub check runs once per hub picture (kept); its centre is the cap's hub centre and the picture is the cap
    check's step-3 hub picture; a cap with no hub picture of its cycle gets no distance (line says why);
  - csv columns `hub_picture`, `hub_seconds_before`; the run file has a `paired hub:` line per cap;
  - the summary also lists the caps the colour rules said NOT SEATED against the suggested max, and says how many caps
    were paired.
  No change to the offsets (DOST holes stays 0.3,0.5) or to `camera/`.
- **Run:** pairing unit test 15/15 per folder (name times, latest-before choice, 300 s limit, one hub check per
  picture, fallback to the single hub centre without pairing, summary); window test under Xvfb 11/11 per folder
  (single-hub run unchanged; pairing run passes the pairer to every cap, no single hub line). Real checks need YOLO
  (laptop): not run here.

## 151. The hub centre from the cap picture itself: seated DOST caps of both positions agree (2026-11-18)

- **User (laptop, run 183611):** DOST, holes, one hub picture (`..._20261113151830811`, (643.8, 338.8)), the 29 caps of
  13 Nov: the first 6 silver caps (seated) should be OK, the other silver caps NOT OK; all 29 NOT OK. "You have not been
  able to fix that."
- **Found:** the run used one hub picture for every cap (no pairing; `hub_picture` empty). The 6 seated caps are SEATED
  by the colour rules and fail only the distance: raw cap - hub dx +0.5 .. +2.5, dy -8.5 .. -6.7 (7.2 - 9.0 px against
  offset 0.3,0.5). Against the same hub picture the 18 seated caps of 17 Nov (CHANGES 148) are at dy -0.8 .. +1.3: the
  same hub reference, two groups 8 px apart (the two positions of the DOST part, CHANGES 149). The 7 other silver caps
  are already NOT OK by the colour rules (face fit 0.45 - 0.59; 152449349 also hub uncovered and outline 55 px off; two
  more pictures have no cap). So only the distance rule is wrong here, and any one offset that passes the 13 Nov
  group fails the 17 Nov group. Widening max to about 9.5 px would give the expected verdicts on these runs, but then
  the distance rule would add almost nothing; not done.
- **Root cause and the better reference:** the hub picture is a different picture from the cap picture, often of
  another part, and the part does not sit in one place. The hub's 4 holes (r about 151) and 4 studs are clearly
  visible round the cap in the cap picture (cap r about 103; the hub check's ring is r 116 - 253). So the hub centre
  can be taken from the cap picture itself: hub and cap in the same picture, the part's position and any camera move
  cancel, no pairing needed.
- **Measured (new `Study_Hub_Holes_In_Cap_Picture.py`; holes by dark blobs at r 128 - 168 and a circle through their
  centroids, cap centre by the production `CapMaskFinder` with stand-in YOLO boxes - no YOLO weights here), the 16 DOST
  pictures of `cap_images/`:** hole circle found on all 16 (r 149.9 - 152.6); its centre moves y 328.5 - 340.4 between
  pictures (where the part sat). Cap - hub in the same picture: the 9 seated caps (01 - 05 of 13 Nov, 06 - 09 of 17 Nov)
  dx -1.8 .. +3.0, dy -2.0 .. +2.8, 0.9 - 3.5 px from the hub centre with no offset; skewed 04 / 05 / 06 at 16.4 / 9.5 /
  15.5 px; skewed 01 - 03 (tilted in place) 1.1 - 2.0 px - caught by the colour rules (face fit), as before; skewed 07
  has no cap mask (pushed off the bore).
- **Changed (dry-run window only; production unchanged):** `DryRun_Cap_Hub_Distance_UI.py` (both folders): tick box
  "Hub from the cap picture" (batch `--hub-from-cap`): the real hub check runs on each cap picture and its centre is
  that cap's hub centre (`CapPictureHub`); the step-3 hub picture (brightening) stays the chosen / stored one. The
  per-cap line `hub from the cap picture itself: ...`, `hub_picture` = the cap picture, a summary line.
- **Next (decision for Uddi):** if the laptop run with the real hub check confirms the study, production should take
  the hub centre for the distance rule from the cap picture (the hub segmentation run in the cap check; a vision logic
  change with a reference commit; about one hub check more per cap check on the GPU), and the offsets be measured
  again that way (the study suggests about (+1, 0) for DOST holes with max 5).
- **Run:** pairing / cap-picture unit test 19/19 per folder; window under Xvfb 13/13 per folder (single hub, pairing,
  hub from the cap picture); the study on the 16 DOST pictures (above). Not run: the real hub check on cap pictures
  (needs YOLO).

## 152. Cap / hub distance: the hub centre from the holes round the cap in the cap picture itself (2026-11-18)

- **User:** fix the DOST holes / bolts offsets with the hub picture `..._20261113151833451` (laptop hub check: 4
  bolts, (644.5, 334.4), CHANGES 149) - the first 6 silver caps of MixedCaps (the 29 pictures of 13 Nov) OK, the other
  10 NOT OK; every silver cap of Cap_Pictures (the 117 of 17 Nov) OK. Then, on the diagnosis below: take the holes from
  the cap picture itself; what does a 4 px limit do; how is it ensured that the holes, never the bolts, are found; the
  window must no longer need a hub picture.
- **Diagnosis (sandbox: production colour rules and cap mask, real MobileSAM, stand-in YOLO boxes; the hub check
  itself needs YOLO and was not run here):**
  - Against that one hub picture the 27 seated silver caps lie in three groups: 13 Nov dy -3.9 .. +1.0, 17 Nov
    17:04 - 18:44 +3.6 .. +6.2, 19:19:43 -6.0 .. -5.8; the smallest circle holding them has radius 6.3 px. With max 5
    no offset passes all of them - the back and forth of CHANGES 145 / 148 / 149.
  - Cause: where each part sat. The hub's hole circle measured in each cap picture is at y about 333 (13 Nov), 339 -
    340 (17:04 - 18:44), 328 (19:19:43); in the hub picture 331.8. The cap sits on its own hub; the hub moves.
  - So the DOST offsets of CHANGES 148 / 149 carry about 7.7 px of part-to-part position (measured against hub
    pictures of other parts). For source `hubcheck` (each cap against its own cycle's hub check) they would have been
    about bolts.dost (0.0, -2.8) and holes.dost (0.6, -7.2) (from the hub-check-vs-blob bias on the same part; the
    holes value assumes `..._151830811` is the same part as `..._151833451`). Not changed - that source is now off.
- **Now (`CheckCap`, `vision.cap.seating.hub.source=cap`):** `hubHoles(picture, model)` finds the hub's holes round
  the cap in the cap picture: dark blobs - the darkest `hub.holes.dark.percentiles` 6 % of the band
  `hub.holes.band` 128 - 168 px round `cap.center` (9 %, 12 % tried when the holes are not all found), opened 5 px,
  area 150 - 2500 px, the 8 largest - and of them the model's count (`hub.holes.count.dost` 4 / `.dostplus` 5) most
  evenly spaced; accepted only if the circle through them has a radius in `hub.holes.radius` 140 - 160, a spread
  (sd of the distances) <= 3 px and every angle gap within 15 deg of 360 / count; then once more with the band round
  the circle found (a part 12 px off `cap.center` cut holes at the band's edge: synthetic 12 / -9 px move, centre
  3 px off before, 0.04 px after). Not found -> NOT seated ("the hub's 4 holes round the cap not found (...)").
  `hubCentreDistance` with source `cap`: target = hole circle centre + `hub.distance.offset.cap.<dost|dostplus>`; the
  hub check's centre is not used (kept for source `hubcheck`, with the holes / bolts offsets, unchanged).
  The seating line reads `... (hub from 4 cap holes (circle r 150.9, spread 0.6, gap 3 deg), DOST offset (0.2, -0.1),
  ...)`; the hole circle is drawn thin blue on the marked picture.
- **Holes, never the bolts:** the holes are dark at r 147 - 154 from the hub centre; the studs are bright tops at r 182 -
  196. The finder takes dark blobs only, searches a band that ends at 168 px, and refuses any circle whose radius is
  outside 140 - 160 - a bolt circle cannot pass even if a stud's shadow were dark. Measured: holes found on 37 of 37
  silver pictures except 2 NOT OK caps that hide holes (pushed off the bore, tilted far: NOT OK either way), 106 / 106
  brass, every cap and hub picture of the dry-run folders (H6, 40 pictures), the uploaded hub picture; circle r 147.3
  - 154.0; never found with the other model's count. Synthetic (H7): dark holes at r 150 found, bright bolts at r 183
  not; dark discs at the bolts' radius alone never taken.
- **Offsets and limit (cap centre - hole circle centre, the smallest circle round the seated caps):** DOST 27 seated
  silver caps (13 and 17 Nov, all three positions): within 3.25 px of (+0.17, -0.14) -> `offset.cap.dost` = 0.2,-0.1;
  DOSTPLUS 99 seated brass caps (13 Nov seated by the bore rule, 17 Nov all): within 2.97 px of (-0.56, -5.31) ->
  `offset.cap.dostplus` = -0.6,-5.3 (brass cap centres with stand-in boxes round the brass cap circle - the fixed
  stand-in squares make MobileSAM take the bore on some brass caps). Max stays 5.0:
  | | largest seated | room at 5.0 | room at 4.0 | nearest NOT OK by distance |
  |---|---|---|---|---|
  | DOST | 3.25 | 1.75 | 0.75 | 9.5 (pushed aside) |
  | DOSTPLUS | 2.99 | 2.01 | 1.01 | 12.0 |
  4.0 gives the same verdicts on all this data; it leaves 0.75 px on DOST, measured on caps from about 9 parts.
  Tilted-in-place silver caps (152433983, 152438344, 152441953) are 1.3 - 2.1 px from the target: the distance does not
  catch them, the face rule does (fit 0.46 - 0.57 < 0.64), as before.
- **Verdicts, production `checkSeating` with source cap (hub picture `..._151833451` for silver rule 1):** DOST 37 / 37
  as expected (MixedCaps: first 6 SEATED, other 10 NOT; Cap_Pictures: all 21 SEATED; the no-cap cycle 18:39:30 left
  out: no cap at step 1), DOSTPLUS 106 / 106 (93 + 6 seated, 7 skewed NOT). Time (CPU) DOST 5.7 s, DOSTPLUS 1.3 s
  median; the hole finder itself a few ms.
- Reference commit `e81a162` (CheckCap with the new logic, values written in), both manifests; new keys in the three
  properties files (allenbradley = the code defaults, offsets 0,0; seating stays off on Machine 1). Verifier ALL
  EQUIVALENT on both machines.
- **`DryRun_Cap_Hub_Distance_UI.py` (both folders):** "Hub source" box - `cap holes` (default: the machine's
  `hub.source`) needs only the model and a caps folder, no hub picture; "cap offset dx,dy" follows the model; per cap the
  hole circle (centre, r, spread, gap) and the distance; holes not found said; summary suggests `offset.cap.<model>`.
  `hub check` = everything before. Batch: `--hub-source cap|hubcheck`, `--offset-cap dx,dy`. The step-3 hub picture
  is the model's stored one unless one is chosen.
- **Dry runs:** `DryRun_Cap_Hub_Distance.py` new H6 / H7 (holes, Machine 2 only), C3 (DOST, source cap: 9 seated within,
  pushed-aside 04 - 06 not), C4 (camera server path, no hub-check centre: seated OK, holes hidden NOT OK); C1 / C2 run
  with source `hubcheck`; new `CircleBoxYolo` stand-in (sandbox only). `DryRun_Cap_Production_Path.py` and
  `DryRun_Cap_Silver_Seating.py` now give the cap mask a YOLO (ModelManager's, else the stand-in): the distance rule
  now runs on every seating (before, without a hub centre it was skipped there).

## 153. Bung seating: the seated position from the hub's holes in the bung picture itself (2026-11-19)

- **User:** the cap check works with the hub centre from the holes in the same picture (CHANGES 152); do the same for
  the bung checks, on the current checkBung() method; 31 "Good Bung" and 36 "Bad Bung" pictures (13 Nov) attached.
- **What the bung check used:** no hub picture - the hole-pattern centre was compared with a fixed seated position per
  model (`seating.seated.centre.<model>`, placed by the knuckle frame), and the reference oval was centred there. So
  where the part sat on the fixture and how the hub sat on it went straight into the offset (seated bungs 3.9 - 14.4 px).
- **Now (`BungSegmenter.seated_position`, `vision.bung.seating.position.source=hubholes`):** the seated position is the
  circle through the hub's 4 (DOST) / 5 (DOSTPLUS) holes round the bung in the bung picture - `CheckCap.hubHoles`, the
  cap check's finder unchanged (dark blobs at r 128 - 168 round `cap.center`, gates on radius 140 - 160, spread and
  angle gaps; then once more round the circle found) - plus `vision.bung.seating.hub.offset.<dost|dostplus>`. The
  hub's holes not found (a bung lying over them) -> NOT seated, "seated position unknown: the hub's 4 holes round the
  bung not found (...)". The four seating tests are unchanged (hole-pattern centre <= 20 px, roundness >= 0.925, clean
  oval, <= 6 % outside the reference oval - now centred on this seated position). `fixed` = before. The seating line
  names the hub circle and the offset; the bung picture shows the hub's hole circle (thin blue) and the oval on the new
  position (`CheckBung.drawSeatingMarks`, also when the seating step did not run).
- **Measured (production hole step with the point grid alone - no YOLO weights here, 18 - 160 s per picture on this
  CPU - then the production seating step):** 60 of the 67 pictures are the `bung_images_20261113/` set (renamed); 7 are
  new. The hub's holes were found on every good picture with the model's count, never with the other model's count.
  Hole-pattern centre - hub hole centre on the 25 good bungs that sit centred: DOST (+2.4..+7.4, -17.9..-9.6), DOSTPLUS
  (-0.2..+8.7, -19.9..-11.3) -> offsets **4.9,-13.7 / 4.2,-16.0** (centre of the smallest circle round them, radius 4.4
  / 5.9; the spread is the hole pattern turning with the bung - the user turned them between pictures).
- **Verdicts with the new position:**
  - the 25 centred good bungs: SEATED, offset 2.5 - 6.0 px (was 3.9 - 14.4 with the fixed position), outside the oval
    0.7 - 4.0 %;
  - all 36 bad: NOT OK - 16 have no hole pattern, 7 the hub's holes covered (seated position unknown), 13 NOT SEATED
    (offset 29.5 - 84.8 px, outside 10.8 - 36.3 %, roundness 0.825 - 0.954);
  - **6 good not OK:** 153611663 (C16) and 153625563 have no hole pattern here (grid only; C16 had none with YOLO either
    in the laptop run of CHANGES 100); 15340549 (A05), 153549580 (C14), 153622275, 153628901: the bung sits off the
    hub centre inside the bore (offset 18.0 / 41.9 / 34.2 / 26.9 px, outside 6.3 / 18.4 / 18.2 / 13.5 %, roundness 0.944 /
    0.923 / 0.930 / 0.933). They measure like the bad ones (e.g. 153628901 vs bad 153358747: 26.9 / 29.9 px, 13.5 / 12.9 %,
    0.933 / 0.896) and look alike; CHANGES 100 labelled A05, C14, C16 not seated. Left NOT OK: a limit that passes them
    passes bad bungs too. Question to the user.
- Properties (three files): `<vendor>.vision.bung.seating.position.source=hubholes`, `.seating.hub.offset.dost/.dostplus`
  (siemens 4.9,-13.7 / 4.2,-16.0, allenbradley 0,0; bung seating stays off on Machine 1). Reference commit `95967fc`
  (0ff5470's BungSegmenter + this logic, values written in); both manifests (the 6 extracted lines moved by 10).
  Verifier ALL EQUIVALENT on both machines.
- **Dry runs:** new `DryRun_Bung_Hub_Holes.py` (both folders) with `bung_images_20261113/hole_centres.json` (the
  hole-pattern centres and the user's labels): Machine 2 6/6 (H1 hub holes, S1 23 good SEATED, S2 20 bad with a hole
  pattern NOT SEATED, S3 holes hidden NOT SEATED, S4 picture moved 12 / -9 px - seated position and offset move with
  it, S5 source fixed), Machine 1 skipped (seating off). `DryRun_Bung_Frame.py` (both folders): new check that the
  seated position moves with the picture; the share outside the oval may now change by up to 4 points (was 2): with the
  oval centred on the face, B12's share went 0.9 -> 3.7 % on a 15 / -10 px move (fixed position: 4.4 -> 4.7 %) because
  MobileSAM's face mask changes with the picture (roundness 0.995 -> 0.964, CHANGES 126) - verdicts unchanged. The
  seated position may be 2.5 px from where the move puts it: A08 zoomed 3 % gives 1.9 px (the hole finder on a
  resampled picture, and the offset, which does not scale with the zoom). Machine 2, MobileSAM on the CPU, no YOLO:
  **39 / 39**. `Verify_Vision_Config_Equivalence.py`: ALL EQUIVALENT on both machines. Not run here:
  `DryRun_Bung_Check.py` / `DryRun_Bung_Check_UI.py` (need `models/` with YOLO - laptop), `DryRun_Bung_Rotation.py`
  (judges the hole pattern only, which did not change).
- **Margin to watch:** the oval test (6 %) has the least room: seated up to 4.0 % here, and a moved picture added up to
  2.8 points on B12. Not changed; the seating lines of a shift will show it.

## 154. Named tuning constants moved into the properties files; code review (report only) (2026-11-19)

- **User:** review the whole codebase for errors; no constants in the Python programs - as far as possible in the
  properties files. Scope chosen by the user: **named tuning constants only** (not bare literals in function bodies,
  and not names: properties-key strings, state numbers, queue names, labels, SQL column lists); the review **reported
  first**, nothing fixed without a decision.
- **Inventory:** 583 named constants in 159 production files; 353 of them are the key-name strings of
  `Configuration.py`, the rest mostly state numbers, log levels, audit labels, queue names and sentinels. About 50 are
  tuning values; all of them are moved.
- **Non-vision code:** new `CosThetaConfigurator.getTuning(key, default, minimum=None)` - typed like the default (bool,
  int, float, str), the default when the key is missing or unreadable, `minimum` as a floor. Each constant keeps its
  name and is now initialised from it when its module is imported (a change applies after a restart; the dry runs that
  patch these attributes keep working). Keys (plain, both machines; block "TUNING VALUES (CHANGES 154)" of the three
  properties files, values = the old code values):
  - PLCIOServer: `plc.settings.write.attempts` 3, `plc.frontend.abort.write.attempts` 3,
    `plc.vision.result.reset.attempts` 5, `plc.reset.write.attempts` 5, `plc.reference.read.attempts` 3,
    `plc.write.retry.interval.sec` 0.2, `plc.reset.float.tolerance` 0.001, `plc.trigger.held.warning.sec` 3.0;
  - QR code server: `qrcode.invalid.notice.sec` 10.0, `qrcode.line.quiet.sec` 0.3;
  - vision check worker: `camera.worker.ready.timeout.sec` 600, `.stop.timeout.sec` 3, `.timeouts.in.a.row.for.alarm`
    3, `.restart.backoff.base.sec` 1, `.restart.backoff.max.sec` 60, `.transfer.warning.sec` 1.0,
    `camera.bearing.ring.guard.max.wait.sec` 5.0;
  - DB: `db.idle.poll.sec` 1.0, `db.insert.max.attempts` 3, `db.insert.retry.gap.sec` 5.0, `db.day.check.interval.sec`
    60, `db.statement.timeout.ms` 5000;
  - frontend: `frontend.status.stale.after.intervals` 3, `frontend.abort.tiles.clear.msec` 750,
    `frontend.state.sync.poll.interval.msec` 250, `frontend.outcome.second.line.point.size` 13 / `.min.point.size` 8;
  - logging, Redis, supervisor: `log.slave.queue.size` 5000, `log.slave.pending.limit` 20000, `log.master.file.batch`
    200, `log.master.stop.check.interval.sec` 0.5, `log.suppressed.repeat.every` 100 (read at first use:
    `SuppressedExceptions` is imported before `Configuration` by many modules), `redis.io.state.stream.maxlen` 500,
    `supervisor.grace.sec` 2.0;
  - web service / MI / show-screen: `endpoints.webservice.port` 8000, `endpoints.mi.port` 8050,
    `endpoints.showscreen.http.port` 8100 / `.ws.port` 8099 / `.interval.sec` 1.0 / `.resize.scale` 0.6;
  - debug print switches of `CheckBung` / `CheckNoCapBung`: `debug.print.checkbung.*` / `debug.print.checknocapbung.*`
    (plain keys: as `vision.*` keys the verifier would require the two files in its manifest).
- **Vision files** (`<vendor>.vision.*`, both vendors = the code value, wrapped with `vBool` / `vInt` / `vFloat` / `vStr`
  - the verifier substitutes the default, so these need no reference commit): BungSegmenter debug prints (5),
  `bung.hole.duplicate.iou` 0.5, `bung.hole.blob.overlap` 0.15; CheckTopBearing `topbearing.ransac.seed` 0, debug
  prints (6), pipeline switches `apply.contrast.norm`, `alter.gamma`, `apply.bilateral`, `gamma.first`,
  `invert.channels` (True) / `remove.inner.region` (False), `use.channel` Blue; HexagonNutDetector `nut.debug.print.detail`
  False (it switches all six debug flags on), `nut.enable.vram.monitoring` True; CheckCap `cap.polar.radius` 170 /
  `cap.polar.angles` 360; CapMaskFinder `cap.mask.box.batch` 64; KnuckleLocator `knuckle.locator.grid.n` 4;
  CheckKnuckle `knuckle.debug.image` False. HubAndBearingSegmenter's hole-spacing ranges (the only values of
  `MODEL_DISTANCE_CONFIG` the live code reads) became named constants first - reference commit `7f04479` (3d91a76's
  file + the named constants, values hard-coded; 8 extracted-literal entries moved by 10 lines) - then
  `vision.hub.spacing.<dost|dostplus>.<1|2>.<min|max>` (215-245 / 175-200 / 190-210 / 145-170).
- **Left in the code on purpose:** `BungSegmenter.GAMMA_SEQUENCE` (only the LUTs built at start; `_preprocess_image`
  needs a LUT for every gamma that `get_gamma_sequence_for_model` returns, so a configurable list could make every bung
  check fail with a KeyError - and the order actually used is that function's inline list, a bare literal), the six
  `DEBUG_*` flags of HexagonNutDetector (set by `PRINT_DETAIL`; the verifier cannot wrap a name assigned twice),
  `SYMMETRY_THRESHOLDS` and `ALL_SPACING_CONFIGURATIONS` (not read by the live code), sentinels (`FALSE_FLOAT_VALUE`,
  `NO_CONNECTION`, `WATCH_TAG_VALUE`), the PLC side codes 1 / 2, file-format and Windows API numbers.
- **Review (report, not fixed):** ruff bug rules and pyflakes over the production code (no undefined names, no new
  defects; the remaining F841 / F811 are the kept ones of CHANGES 82 / 110 and legacy modules); every production module
  compiles; every `CosThetaConfigurator.get...()` called exists (except in legacy modules nothing imports and commented
  lines); every `*_KEY` read by a getter in use is in the properties file or has a deliberate default. Read in detail:
  ProcessSupervisor, StateMachine, VisionCheckWorker, the camera server's `assessPictures` / `_assessCap`, the bung
  seating step, the QR line assembler, the DB server's insert loop, MainProgram's start / stop, the IOServer's read and
  write loops and its reconnect check. Findings, for decision:
  1. *Confirmed:* a record on `fe2db.q` without the `timestamp` field (or any field whose decoding raises outside the
     per-field `try`s) blocks the queue for good: `readDataInDatabaseServerFromFEServer` raises (KeyError, checked here),
     the DB server catches it without a message id, never deletes or counts the record, and reads it again every second
     - no later record is stored. Only the frontend writes there (always with the field), so it needs a bug elsewhere.
  2. *By reading:* a record still on `fe2db.q` at Exit can be lost: MainProgram sends the stop, waits 2 s and ends every
     server, while the DB server only then starts draining (a failed insert waits 5 s before retrying); MainProgram's
     next start flushes Redis.
  3. The IOServer's read loop only prints an exception (`printBoldRed`); the write loop also logs and audits it.
  4. `endpoints/ShowScreen.py` prints `HTTP_PORT`, not the free port actually used (`find_free_port`).
  5. `getFastestPossibleTimeBetweenTakePicRequests` (used by the camera server) reads a key that is in no properties
     file (default 1.0 s); the queue getters of `utils/ClearQueues.py` likewise use defaults (legacy queues).
  6. `test/machine1_dryrun/DryRun_Bung_Frame.py` decides the vendor by `"machine2" in str(HERE)`: in a checkout whose path
     contains `machine2` in lower case (the sandbox's) the Machine 1 copy runs as Siemens. The other dry runs use the
     folder name.
- **New dry run** `DryRun_Tuning_Keys.py` (both folders): every `getTuning` key in all three properties files, value =
  code default, the type kept, one default per key, a missing key gives the default.
- **Run (sandbox; Redis and PostgreSQL started, Python 3.13):** `Verify_Vision_Config_Equivalence.py` ALL EQUIVALENT on
  both machines (362 parameters, was 326); `DryRun_Tuning_Keys` 201/201 on both vendors; every vision module imported on
  both vendors with the old values (checked one by one); QR validation 17/17, process supervisor 7/7, state change
  delivery 4/4, stale stop command 5/5, vision worker back-off 6/6, picture retention 10/10, camera state file 19/19,
  work-thread health 17/17, DB no record loss (a-e), DB indexes / day folder 8/8, DB server and persistence 66/66,
  heartbeat / start-up 28/28, state sync monitor 15/15, camera end-of-cycle order 6/6, camera stale frames 23/23,
  bearing-ring guard 13/13, vision check timeout 22/22 (regions knuckle: no `models/` here); fault scenarios Siemens
  211/211, Allen-Bradley 218/218; frontend (Python 3.12, Xvfb) 172/172; endpoints 98/98. Not run: the integrated
  all-servers cycle, the windows, model-based vision runs (no vision logic changed; values identical).
- **Repository slip, corrected:** commit `c142412` also added `src/mobile-sam` (the sandbox's MobileSAM clone, as a
  gitlink - no files); removed in `f5709d3`. A `git pull` past both leaves nothing behind.
- **Follow-up (laptop run of `DryRun_Tuning_Keys.py`):** it seemed to hang after a page of SyntaxWarnings: it parsed
  every `.py` under the project folder, which on the laptop includes `.venv` (all of site-packages) and the Nuitka
  build folders; the warnings came from those third-party files. It now reads only the files git tracks (`git ls-files`;
  without git, a walk that skips `.venv`, `models`, `src`, the build folders and the other non-production folders), with
  warnings silenced while parsing. Here: 201/201 in 0.7 - 2.8 s on both vendors, also without git on the PATH; a planted
  `.venv/lib/site-packages/bad.py` with an invalid escape is not read.

## 155. Review findings of CHANGES 154 acted on (2026-11-19)

User's decisions on the six findings:
1. **Malformed `fe2db.q` record:** the only writer is `RedisUtils.sendDataFromFEServerToDatabaseServer` (called once, by
   the frontend), which sends through `sendData`; `sendData` sets `timestamp` and `producer` on every message it sends,
   overwriting whatever the caller passed - checked: no other `xadd` to `fe2db.q` in the production code. Nothing changed.
2. **Records lost at Exit:** MainProgram waits `mainprogram.stop.wait.sec` (new, **6.0 s**, was a fixed 2 s) after the
   stop command before ending the servers: the DB server drains `fe2db.q` after the stop, and a failed insert waits
   5 s before its next attempt (the next start flushes Redis).
3. **IOServer read-loop exceptions only printed:** left as is (user).
4. **ShowScreen:** prints the HTTP and WebSocket ports actually used. Found on the way and fixed with it: the page
   connected to a hard-coded `:8099` whatever WebSocket port was in use (now the port found free, `_wsPort`), and the two
   free-port searches could return the same port (probing does not bind: 8099 busy -> the WebSocket search took 8100,
   which the HTTP search had just chosen); the second search now skips the first one's port. Checked with 8099 held:
   HTTP 8100, WebSocket 8101, the page uses 8101.
5. **`getFastestPossibleTimeBetweenTakePicRequests`:** correction to CHANGES 154 - it is NOT used by the running camera
   server: its only call is in commented-out code (`CameraProcessorServer.py` lines around 1537 / 1591; the
   finding's grep matched the comment). Not critical: the key `fastest.possible.time.between.takepic.requests=1.0` is now
   in the three properties files (the getter was already in `Configuration.py`). **`utils/ClearQueues`** asked the
   configuration for an earlier project's queues (recording, actuators, tower lamps, bottle release ...) whose keys are
   in no properties file; it now clears the queues the application uses: new `RedisUtils.applicationQueues()` (the list
   MainProgram's `clearQueues` already used, now shared by it and `reportMessageCount`, which had left out
   `io2qrcodeabort.q` and `io2festate.q`) plus `getLoggingQueues()` (file and console logging) - 25 streams. The old
   getters stay in `Configuration.py` (unused).
6. **Vendor of a dry-run copy:** `DryRun_Bung_Frame.py` (both copies) now takes the vendor from its own folder's name
   (`HERE.name.startswith("machine2")`), as the other dry runs do - the root cause was matching "machine2" anywhere in the
   full path. The folders are not renamed: with the folder name used, `machine1_dryrun` / `machine2_dryrun` are
   unambiguous, and a rename would touch every README, document and laptop habit.
- **Run:** `DryRun_Tuning_Keys` 205/205 both vendors (51 keys); heartbeat / start-up 28/28 (H10: the stop and backup);
  DB no record loss (a - e); work-thread health 17/17 (one earlier run 16/17, C2 - the timing flake noted in CHANGES 111;
  the code before this change gave 17/17 and so did the rerun); `ClearQueues`: 25 queues, `fe2db.q` emptied, counts
  reported; ShowScreen ports and page as above.

## 156. Frontend in English, Kannada, Tamil and Hindi (2026-11-19)

User's request: the database and the logs stay English; only the frontend changes, and only the main screen and its
dialogs (the screens before it - splash, mode choice, login - stay English). A dropdown on the main screen offers
English, Kannada, Tamil and Hindi; the choice is kept in a file in `Internal/`; an industrial-strength i18n package;
messages in the database or in a file in `Internal/`. Choices (asked): gettext catalogues through Babel; main screen +
its dialogs; part names translated / transliterated (नकल हब बेयरिंग वॉशर नट स्प्लिट पिन, ...).

- **Catalogues:** `Internal/locale/<kn|ta|hi>/LC_MESSAGES/frontend.po` (GNU gettext, read with Babel 2.18.0) and the
  compiled `frontend.mo` beside each (read with the standard `gettext` module if Babel cannot read the .po);
  `Internal/locale/frontend.pot` is the template. The English text is the message id, so English needs no catalogue and
  a text without a translation is shown in English. `Internal/`, not `internal/`: the folder exists (`cd.bin`) and a
  second one differing only in case would clash on Windows. **244 texts**, all translated in the three languages.
  **The translations are drafts (written by Claude) and need a native speaker's review** - mainly the operator
  instructions and the tile titles; correcting a .po needs no code change (`--compile` after editing).
- **`test/i18n/update_frontend_catalogues.py`:** extracts every text the frontend shows (`tr`, `trf`, `N_`, `bind`, the
  first argument of the popup helpers, plus `window.title` and every `state.<N>.instruction` of the properties file),
  writes the template, updates the three .po files (new texts added empty, unused ones dropped, translations kept) and
  compiles them; reports the texts without a translation. Run it after changing a text in the frontend.
- **`frontend/FrontendI18n.py` (new):** `tr` / `trf` (named placeholders; positional-only message, so `{text}` works;
  the English format if a translation's placeholders do not match) / `N_` (marks a text for extraction) / `bind(widget,
  text, setter, fmt)` (sets the text now and again on every change of language; weak references) / listeners /
  `setLanguage` / `useSavedLanguage`. The choice is written to **`Internal/frontend_language.txt`** (temporary file +
  `os.replace`; git-ignored, so each PC keeps its own) and read when the frontend starts.
  `installIndicFonts`: the tiles use Latin fonts (Courier, Segoe UI); without a substitution Qt fell back to a font that
  cannot shape Indic scripts (Tamil vowel signs drawn in the wrong place - seen in the sandbox screenshots). Each of the
  frontend's families now has the fallbacks of the new key **`frontend.indic.fonts`** (Nirmala UI - Windows 8 and later
  cover all three scripts -, Noto Sans Kannada / Tamil / Devanagari, Tunga, Latha, Mangal).
- **`frontend/FrontendNotices.py` (new):** notices written by the other servers (`frontend:notice:*`, English, also
  logged) are translated in the frontend: whole notices, and patterns with a variable part (Picture NOT OK: <reason>,
  Cycle aborted: <reason>, Bypass not possible (<tag> ...), no answer in <n> s, PLC restarted (<why>) ...), reasons
  nested; a tag name stays as it is; an unknown notice or reason stays English.
- **Main screen:** the language dropdown (names in their own script) beside the notice; on a change every bound text
  (tile titles, hints, results, connection status, group titles, menus, window title, instruction) is shown again, the
  texts built at run time (mode, creator, QR code, date / counts line, notices, torque tiles' second line) are rebuilt,
  and every font is fitted again. `calculateAndSetIdealFontsForTitlesAndResults` now fits **every** title and every
  result text (Result Awaited, OK, Not OK, Bypassed) and takes the smallest size: it used the text with the most
  characters, which in Kannada is not the widest (two titles overflowed). The About text described an earlier product
  (capsules and bottles) - corrected.
- **What stays English inside:** result values (`getResult` returns `OK` / `Not OK` / ... as before - the record sent to
  `fe2db.q` and the database are unchanged), FreeSimpleGUI button keys (the event names; only the label is translated),
  the log, the MainProgram process (login, mode choice), and every server other than the frontend.
- **Dialogs (`frontend/SimplePopups.py`):** user administration, passwords, roles, reports, machine settings (labels
  marked `N_`, the entry checks' messages with named placeholders), confirmations. Typos fixed on the way ("Create
  New Account", "The 2 new passwords do not match"); a comment typo "newwidgets user" -> "new user" in three camera files.
- **Packaging:** `requirements.txt` + `babel==2.18.0`; `runNuitka.bat` includes `Internal/locale`, the `babel` package
  and its data, and the dist check reports `Internal\locale\` (MISSING otherwise).
- **New dry run `DryRun_Frontend_Languages.py`** (both folders, 63 checks): catalogues complete with the same
  placeholders, .mo = .po; `tr` / `trf` / `bind` / listeners / the file / reload; notices; the real frontend (PySide6)
  in each language - dropdown, tiles, results English inside, instruction, notice, mode title, menu, window title,
  connection status, every title within its tile at 1920 x 1050 -, back to English, a Hindi start;
  `--screenshots DIR` saves the main screen in each language. (The window is set to 1920 x 1050 in the test: the
  offscreen platform clamps to 800 x 800 and Xvfb has no window manager to maximise - both clip English as well.)
- **Run:** `DryRun_Frontend_Languages` 63/63 (machine2, Xvfb and offscreen) and 63/63 (machine1); FE cycles 167/167 (the
  same 167 check names as HEAD before this change; it found the `{text}` placeholder clash in `trf`, fixed);
  `DryRun_Tuning_Keys` 205/205; verifier ALL EQUIVALENT on both machines; IOServer fault scenarios (StateMachine gained
  `getAllInstructions`) 211/211 Siemens, 218/218 Allen-Bradley. Screenshots in the four languages looked at (Kannada, Tamil, Hindi
  shaped correctly after the font fallbacks; nothing clipped). Not run: the exe (Nuitka) on Windows, and the fonts on the
  plant PC - Nirmala UI is expected there.

## 157. Review findings of 2026-11-19 acted on: DB drain at Exit, hole checks per combination, font fits by measured width, configuration problems logged (2026-11-19)

User's decisions on the review of this morning (report in the chat, nothing changed then):

- **Exit with records on `fe2db.q` (root fix of CHANGES 155's fixed wait):**
  - DB server (`persistence/DBServer.py`): after the stop command a failed insert goes to `FailedRecords/` at once
    (`handleFailedInsert`: no retry, reason "... (after the stop command: written to disk without a retry)"); once one
    has failed, the records still queued go there without an attempt ("not tried: an insert already failed after the
    stop command"); the retry gap ends at the stop command; a `FailedRecords/` file that cannot be written then waits
    0.5 s (not 5 s) between tries. When the queue is drained: `reportDrained` sends the stopped response
    (`RedisUtils.sendStoppedResponseFromDatabaseServer`, producer `DBServer`, on the existing `stoppedResponse.q`,
    which nothing read before).
  - MainProgram `stopProcesses`: `utils/ShutdownWait.waitForDatabaseServer` - at least `mainprogram.stop.min.wait.sec`
    **2.0** (the other servers act on the stop; the wait before CHANGES 155), then until the DB server's response
    (`RedisUtils.databaseServerStoppedSince`, sent since MainProgram started; other producers' responses left in
    place), at most `mainprogram.db.drain.wait.max.sec` **20.0**, earlier when the DB server process is no longer
    running (`supervisor.process("DB server")`, else the one started). Prints `drained` / `not running` / `timed out`
    with the seconds. Replaces `mainprogram.stop.wait.sec` (6.0).
  - **Why 20 s:** one record with PostgreSQL *hanging* (the slow case; a stopped PostgreSQL refuses at once):
    duplicate check (5 s connect timeout, `PGCONNECT_TIMEOUT`) + insert (5 s connect + up to 5 s statement timeout) +
    the pictures (~1 s) = about 16 s, plus margin. After the first failure the rest go to disk without an attempt, so
    several records do not add 16 s each. Normal Exit: about 2 s (the minimum); the DB server drains in well under it.
- **Hub holes (`CheckCap.hubHoles`):** `vision.cap.seating.hub.holes.gate.each.combination` (both vendors **True**):
  the radius / spread / angle-gap checks on every combination of blobs, the most evenly spaced one that passes taken;
  **False** = CHANGES 152's route (the most evenly spaced first, then checked). Synthetic: 4 holes at r 150 (spread
  0.6 px, gaps 2 deg) plus 4 evenly spaced dark spots at r 132: True finds the holes, False reports "circle r 134.4
  outside 140 - 160". Real pictures: the 100 cap, hub and bung pictures of the dry-run folders give the same circle
  both ways; time 34.6 vs 33.8 ms per picture (the whole finder; the extra checks under 2 ms with 56 combinations).
  **Reason:** when no percentile finds the holes, the result of the most informative try is returned - a circle failing
  the fewest checks, then any circle, then the most blobs - instead of the last percentile's ("0 dark blobs" at 12 %
  hid that 6 % had found a circle at the wrong radius). With the switch on, a failing reason starts "none of the N
  combinations of M blobs passes; most evenly spaced: ...".
- **Bung marks:** `CheckBung.drawSeatingMarks(..., source_rgb=)` - when the seating step did not run (wrong hole count,
  segmenter error, no pattern) the hub's holes are looked for on the camera picture as taken (`anImage_rgb`), not on
  the result picture with the bung masks painted (both bung checks).
- **Font fits by measured width (frontend):** new `CosThetaLabelUtils.fittingPointSize(widget, texts, font, widthShare,
  heightShare, ...)`: the largest size at which every text of a family fits, each measured with `QFontMetrics.size`
  (drawn width and lines; binary search); `largestFittingPointSize` now calls it (kept). Every family: instruction (all
  28 instructions, 95 %), connection status (all 8 messages), tile titles, results (Result Awaited / OK / Not OK /
  Bypassed), **tile hints** (new: one size for the 14 hints, inside 90 % of the picture area, at most the designed
  18 pt - in Tamil the hint "Component Press" had run past its tile), mode, QR code (now also refitted on a change of
  language), date / time, creator, the torque tiles' second line. Then `language.<english|kannada|tamil|hindi>.fontsize.
  adjustment` (0 / 0 / 0 / **-1**; `FrontendI18n.fontSizeAdjustment` / `adjustedPointSize`; read at each fit). The key
  names are `language.<name>.fontsize.adjustment` (the request wrote `fontsize,adjustment`; a comma cannot be part of a
  key here). Tamil catalogue: the "Place / Bung for / Component Press" hint broken over three lines like the English one
  (a 25-letter first line forced the hints to 10 pt; now 18).
  Measured at 1920 x 1050 (Xvfb): instruction 19 / 20 / 13 / 24 pt (en / kn / ta / hi; Hindi fit 25 - 1), connection
  20 / 20 / 13 / 33, titles 16 / 16 / 13 / 18, hints 18 / 18 / 18 / 17; nothing cut off.
- **Dialogs in Indic scripts:** checked by the user on the plant PC - shown correctly (plant check 11, dialogs passed).
- **Configuration problems logged:** `CosThetaConfigurator.reportConfigurationProblem(key, message)` - printed and in the
  log file (`[Configuration] ...`), once per process for each key and problem; only for a key that is missing or a
  value outside its expected set. `getTuning`: missing key -> default (logged); bool other than true/false/yes/no/on/off/
  1/0, an int key that is not a whole number ("3.7"; "4.0" is accepted), a float key that is not a number -> the default
  (logged; before, "Ture" read as False and "3.7" as 3); a value from the file below its minimum -> the minimum (logged).
  `VisionConfig`: a missing vision key and an invalid value now go through the same report (invalid ones were printed
  only). `expectOneOf` (Configuration and VisionConfig): `vision.cap.seating.hub.source` (cap / hubcheck) and
  `vision.bung.seating.position.source` (hubholes / fixed) checked where they are used - an unknown value is logged and
  keeps its old meaning (hubcheck / fixed). Every vision module imported on both vendors and every tuning key read: no
  report (all keys present and valid).
- **Change User Role:** Enter in the role box submits (the event was compared with `"_role-_Enter"`).
- **Small points:** `addListener(self.onLanguageChanged)` right after `initializeUI()` (it sat between `setFixedWidth`
  and `setFixedHeight`, after everything that could fail in that block); `trf`: if even the English text cannot be
  filled (a code error), it is shown unfilled and printed instead of raising into the screen's code.
- **Bung check, user's answer to CHANGES 153's question:** on the 6 good-labelled pictures that come out NOT OK the hole
  is not visible or the label was wrong - NOT OK is right. The check is to be strict: the bung must sit straight so
  that it does not fall or unseat on the way from Station 1 to Station 4, where the 2.5 t hydro-pneumatic press can
  damage the machine on a badly seated bung. Nothing changed; noted in CLAUDE.md §8.
- **Reference commits:** `d8a33f8` CheckCap (e81a162 + this logic, values written in; the switch written in as True),
  `8eb97d9` BungSegmenter (95967fc + the `expectOneOf` line); both manifests point at them. `547f29a` was a first attempt
  that kept the configurable values in CheckCap / BungSegmenter (the verifier needs them written in) - superseded, left in
  the history (it also carries this change's Configuration / VisionConfig code).
- **Run (sandbox, Python 3.13 with torch CPU and MobileSAM; frontend on Python 3.12 under Xvfb; Redis, PostgreSQL):**
  `Verify_Vision_Config_Equivalence.py` ALL EQUIVALENT on both machines; `DryRun_Tuning_Keys` 227/227 both vendors (new
  T6: 13 checks of missing / wrong-type / below-minimum / valid values and `expectOneOf`, logged once; T7 the language
  adjustments); `DryRun_DBServer_No_Record_Loss` both copies (a)-(f) PASS (new (f): stop with the database failing, 3
  records - 1 attempt, 3 files, 2 not tried, drained and the response in 1.3 s; (c) now also checks the response);
  `DryRun_DBServer_And_Persistence` 66/66; `DryRun_Heartbeat_And_Startup` 29/29 both copies (H10 asks for the drain; new
  H10b the wait: 2 s minimum, ends when drained, 20 s maximum, 2 s when the process is gone);
  `DryRun_Frontend_Languages` 79/79 both copies (new L6 per language: instructions, connection messages, tile hints fit
  as drawn; sizes = fit + adjustment); `DryRun_Cap_Hub_Distance` 15/15 (H6 holes on all 40 cap / hub pictures, C3 / C4
  with the switch on); `DryRun_Bung_Hub_Holes` 6/6; `DryRun_Bung_Frame --no-yolo` 39/39 (the bung marks path);
  `DryRun_Cap_Production_Path` 12/12; `DryRun_Cap_Silver_Seating` 6/6. Not run: the windows, YOLO-based runs (laptop),
  the integrated all-servers cycle.

## 158. Font adjustments -2 for Kannada and Hindi; QR code line fitted in English; creator line fitted to its label (2026-11-19)

User (with screenshots of the main screen in the four languages): (1) font size adjustment -2 in Hindi and in Kannada;
(2) the QR code widget's font from "QR Code :" plus 30 English characters, in English only, used for every language;
(3) the "Created by CosTheta Technologies and Ashok Leyland" line a little bigger in every language - fixed or
fitted? - and fitted dynamically.

- **(1)** `language.kannada.fontsize.adjustment=-2`, `language.hindi.fontsize.adjustment=-2` (three files; English and
  Tamil 0). The code defaults are now the same values (`FrontendI18n.DEFAULT_FONT_SIZE_ADJUSTMENTS`; before, every
  language's default was 0, so Hindi's -1 differed from its default).
- **(2)** `calculateAndSetIdealFontForQRCodeLabel`: fitted to `"   QR CODE : ABCDEFGHIJKLMNOPQRSTUVWXYZ0123   "`
  (`QR_CODE_SAMPLE`, 30 characters) in 95 % of the label, in English, **without** the language's adjustment; fitted
  once (the first call, after the window is shown) and the same font set again on every change of language. The label
  shares its row with the notice, which comes and goes: the fit uses the label's width without the notice (a notice
  shown at that moment is added back), so the size does not depend on what was on screen at start. Before: fitted to
  the text shown at that moment - "QR CODE : " alone, so the QR code then ran past the widget - and refitted per
  language. At 1920 x 1050: 23 pt in all four languages (the sample 858 px of the label's 1013 px). A notice shown
  beside a long QR code narrows the label to about 805 px: a 30-character code at 23 pt could then lose its ends.
- **(3)** It was already fitted to its widget (CHANGES 157; before that a scale factor of the same kind), but measured
  wrongly: `_fitBold` took `self.creatorStatus.font()`, the font of the `SimpleMessageContainer` itself - the
  application's default font, not the label's Segoe UI - and then set that default font on the label; and it filled
  0.8 of the widget with three spaces each side. Now a `SimpleMessageContainer` is measured on its inner label
  (`label()`, new) with the label's own font (also the date / time line), and the creator line is fitted to
  `frontend.creator.fill.share` (**0.95**, new key) of the label less its border + padding (5 px each side), no
  padding spaces, then the language's adjustment. At 1920 x 1050: English 13 pt (was 10), Kannada 11 (fit 13 - 2),
  Tamil 10 (was 7), Hindi 15 (fit 17 - 2). Found on the way: the line was meant to be italic (`QFont(..., italic=True)`),
  but its stylesheet's `font: bold` resets the style, so it has always been shown upright - not changed.
- Run (Python 3.12 + PySide6 under Xvfb; Python 3.13): `DryRun_Frontend_Languages` 88/88 both copies (new L6: the QR
  code line at its English fit in every language, one size, the sample within the label's width without the notice;
  the creator line's size = fit at 0.95 + adjustment, larger than the old fit; the other families with -2 in Kannada
  and Hindi); `DryRun_Tuning_Keys` 231/231 both vendors (T7: 0 / -2 / 0 / -2 = the code defaults; the new key).
  Sizes at 1920 x 1050 (en / kn / ta / hi): instruction 19 / 18 / 13 / 23, connection 20 / 18 / 13 / 32, titles 16 /
  14 / 13 / 17, hints 18 / 16 / 18 / 16, QR code 23 everywhere.

## 159. QR code line fitted once (40 characters) and kept; fitted fonts carried in the style sheets; font log line (2026-11-19)

User (screenshots of the laptop run after CHANGES 158): (1) "Created by CosTheta ..." still very small; (2) the QR code
line is right at start in English, but after a change of language it goes back to the large font: work it out once,
in English, at the start and never change it; 40 characters instead of 30, so that it also fits beside a notice.

- **Not reproduced here, diagnosis by reading.** In the sandbox (Python 3.12, PySide6 6.9.1 - the version of
  `requirements.txt` - Linux, Xvfb) the QR code line kept its start size through every change of language and the creator
  line followed its fit (13 / 11 / 10 / 15 pt). Two things fit the laptop screenshots, where the creator line has the same
  small size in all four languages (not its fit, which differs per language) and the QR code line returns to its
  initial large size:
  1. the labels' style sheets carry `font: bold`, and Qt gives a style sheet's font precedence over `setFont()`: on a
     re-polish (a new style sheet - `setQRCodeContainerDisplay` sets one at every change of language and QR code - or
     a style change) the font can come back to the one the label had when the sheet was applied (the QR label's
     initial size, the creator's initial 12 pt). Not reproduced on Linux; the Windows style may behave otherwise.
  2. the start fits ran right after `showMaximized()` + one `processEvents()`; on Windows the maximise can land later,
     so the widgets did not yet have their final size, and the QR font (fitted once) was fitted to that.
- **Now:**
  - `CosThetaLabelUtils.styleSheetWithFont(sheet, font)` / `applyFont(label, font)`: the fitted font is set with
    `setFont()` **and** written into the label's style sheet (`font: bold <n>pt "<family>"`, the sheet's own `font:` replaced;
    the base sheet kept in the property `_baseStyleSheet`). Used for the QR code, mode, creator and date lines;
    `setQRCodeContainerDisplay` adds the QR font to each new sheet. A sheet and the font can no longer disagree.
  - QR code line: `QR_CODE_SAMPLE` 40 characters; fitted once in `fitFontsOnceShown` (`QTimer.singleShot(0)` after the
    window got its fixed size at the end of `__init__`, which also refits every other family); `onLanguageChanged`
    no longer touches it. At 1920 x 1050: 19 pt (23 with 30 characters), the same in all four languages.
  - `logFittedFonts`: console / log line "Frontend fonts (start | language changed, <lang>, window W x H): QR n pt / w px,
    mode ..., creator ..., date ..., instruction ..., connection ..." - "(drawn n)" when Qt draws another size than set.
    If the laptop still shows a wrong size, these lines show which.
- Run (Python 3.12, PySide6 6.9.1, Xvfb): `DryRun_Frontend_Languages` 89/89 both copies (L6 now: 40 sample characters;
  the size in the QR code and creator lines' style sheets and drawn at it; new: a QR code shown, cleared and the
  language changed twice - the QR size stays its start fit); `DryRun_Tuning_Keys` 231/231. Creator at 1920 x 1050:
  13 / 11 / 10 / 15 pt (en / kn / ta / hi) on a 534 px label. Not run: Windows - the two causes above are not proven
  there; the font line will show it.

## 160. Frontend dry runs: offscreen only on Linux (2026-11-19)

- **Found from the laptop log of 2026-11-19:** `DryRun_Frontend_Languages.py` set `QT_QPA_PLATFORM=offscreen` whenever
  `DISPLAY` was not set - always on Windows, which has no such variable. The offscreen platform has no system fonts
  ("QFontDatabase: Cannot find font directory ...", "This plugin does not support propagateSizeHints()"), so on the
  laptop every measured font size was wrong (instruction fit 10 pt there against 19 pt in the sandbox). Not a fault of
  the application, which runs on the normal Windows platform. Now offscreen only on Linux without a display (the
  sandbox). `DryRun_FEServer_Cycles.py` skipped its Tk check F13 on Windows for the same reason: now only on Linux
  without a display. Both copies.
- The same log showed both `git pull`s aborted by a local change to `ApplicationConfiguration.properties`, so the
  laptop was still at CHANGES 157 when the screenshots after CHANGES 158 / 159 were taken; after the pull the user
  reports the three fixes visible.
- Run (sandbox): `DryRun_Frontend_Languages` 89/89 both copies under Xvfb, and 89/89 without a display (offscreen
  still chosen on Linux); `DryRun_FEServer_Cycles` compiled (the change is the condition only), not run.

## 161. Result box texts translated; "Done" fits its tile; number displays no longer widen their column (2026-11-19)

- **(a) User:** "Component Successfully Done", "Component Not Successfully Done", "Component Processing Aborted" under
  i18n. They were passed through `tr()` in `LargeImageWindow._resultImage`, but as plain strings at the call sites, so the
  catalogue extraction never found them and they stayed English. Now marked `N_(...)`; catalogues updated (247 texts),
  Kannada / Tamil / Hindi drafts added (ಕಾಂಪೊನೆಂಟ್ ಯಶಸ್ವಿಯಾಗಿ ಮುಗಿದಿದೆ / ... ಮುಗಿದಿಲ್ಲ / ಕಾಂಪೊನೆಂಟ್ ಪ್ರಕ್ರಿಯೆ ರದ್ದಾಗಿದೆ; காம்போனென்ட்
  வெற்றிகரமாக முடிந்தது / ... முடியவில்லை / காம்போனென்ட் செயலாக்கம் ரத்து செய்யப்பட்டது; कंपोनेंट सफलतापूर्वक पूर्ण / ... पूर्ण नहीं
  हुआ / कंपोनेंट प्रोसेसिंग रद्द हुई) - for the native speakers' review. Searched the frontend for other shown literals
  not marked: none on the main screen (the "AL" / "CT" logo fallbacks, the language tooltip and the legacy
  `frontend/widgets/` are not translated texts).
- **(b) User:** in Kannada and Tamil the "Done" of Component Press and Cap Press ran past both edges of the tile.
  **Cause:** the number display's style sheet had `min-width: 10em`; with the display's 36 pt font that is about 556 px,
  so whenever a number display was shown (a torque, "Done") its label demanded 556 px. The column widened at the cost
  of the other six (measured at 1920 px: 560 px against 225; equal columns are 273), and the label, wider than the tile
  shown, was cut at both edges. The display font was also fixed at 36 pt whatever the text.
  **Now:** `min-width` removed from `numberDisplayStylesheet` (the 7 columns stay 273 px with "Done" and torques shown);
  `CosThetaOutcomeContainer._showDisplayText` / `_displayFont`: the display's text fitted to 90 % of the display, at
  most the designed 36 pt (a number keeps 36), the language adjustment applied when it had to be made smaller, set with
  setFont and in the style sheet; used by `setText`, `setTextAndResult`, `setResultAndText` and on a change of language.
- Run (Python 3.12, PySide6 6.9.1, Xvfb): `DryRun_Frontend_Languages` 100/100 both copies (new L7: the three result
  texts translated in kn / ta / hi; "Done" within 90 % of both press displays in all four languages, size in the style
  sheet; refitted on a change of language; a text too wide at 36 pt made smaller until it fits; a torque keeps 36 pt;
  the row's 7 tiles one width with "Done" and a torque shown).

## 162. Split pin and washer check: washer, blue-marked legs, legs turned round the nut (2026-11-19)

- **User:** an algorithm for state 20 from the OK / NOT OK pictures: the washer must cover the nut (no washer = NOT OK);
  each leg of the split pin is marked blue; good = both legs firmly turned round so that the pin locks the nut; the pin
  goes in either way. Until now the check was a placeholder (OK whenever a QR code was known).
- **Pictures:** 19 OK, 60 NOT OK (Machine 2 camera 192.168.250.64, 1280 x 720, 2026-11-19), after the user's relabelling
  (2026111911464227, 2026111911361513, 113449396, 112729124, 113433320 moved to OK). They are bursts of a few parts:
  the 19 OK are 3 parts; many NOT OK frames are the same part seconds before its OK frame. Kept in
  `test/machine2_dryrun/splitpin_images/OK` and `NOT_OK` (13 MB).
- **Found while measuring:** a leg that is straight, half bent, standing up or bent outwards ends beyond the washer's
  edge; a leg turned round lies on the washer. A circle at the scallop tips misses legs lying in the valleys between
  scallops, so the distance is measured from the washer's own outline. The bluish dark ring round the washer (S 72 - 104,
  V 44 - 67) passed a loose blue threshold; the marker is taken at S >= 90, V >= 55.
- **Check** (`camera/CheckSplitPinAndWasher.py`, `measure` / `markedPicture` / `checkSplitPinAndWasher`), on the part of
  the picture within 222 px of `vision.splitpin.centre` (644,345):
  1. washer = largest region with V >= 160, S <= 70 within 100 px of the centre (closed 9 x 9); circle fitted to its
     outline. NOT OK if missing or smaller than 2,500 px (washer not found), tip radius (95th percentile of the outline)
     outside 44 - 56 px (outline not round: out of place, or something pale - an unsplit pin - across it), or seen round
     less than 90 % of the circle (does not cover the nut);
  2. blue marker: hue 100 - 130, S >= 90, V >= 55, within 120 px of the washer centre, opened 2 x 2; fewer than 100 px
     -> NOT OK (legs not found);
  3. more than 2 marker pixels farther than 8.0 px outside the washer (distance transform of the filled washer) -> NOT OK
     (a leg not turned round the nut).
  Picture: washer outline green / red, the blue beyond the washer outlined red; values and reason in the log line
  `CheckSplitPinAndWasher: OK|NOT OK in <s> - <reason>; washerArea ..., centre ..., tipRadius ..., cover ...,
  markerPixels ..., outsidePixels ..., outsideMax ...`. No QR code or no picture -> NOT OK as before. The camera server's
  vote (2 of 3 pictures) is unchanged.
- **Measured:** OK pictures: washer region 3,689 - 4,640 px, tip radius 49.2 - 51.4, cover >= 0.986, marker >= 204 px,
  at most 2 marker px beyond 8 px (furthest 8.6 px). NOT OK: 58 of 60 NOT OK. The 2 that pass, 113431189 and
  113432153, are the same scene as the OK picture 113433320 taken 1 - 2 s later: mean difference 2.0 grey levels on a
  blurred crop, the same as two frames of a still part (two different parts: about 26) - they cannot be told apart by
  any picture rule; please confirm their label. **The margin is small:** the nearest NOT OK pictures (113448348,
  113447333) have 3 and 5 pixels beyond 8 px against at most 2 on the OK ones, and are themselves 2.5 - 3 grey levels
  from the OK frame 113449396. Strict on purpose (as the bung check): a doubtful picture is NOT OK. The values come
  from 3 OK parts; more OK parts (one picture each, both directions) and a picture without a washer would firm them up.
  Time: about 8 ms per picture on the sandbox CPU (the whole picture: 32 ms; same results and pictures).
- **Configurable:** 17 keys `<vendor>.vision.splitpin.*` (centre, search radius, washer V / S / close kernel / area /
  tip radius / cover, marker hue / S / V / search radius / pixels, leg outside distance / pixels) in the three
  properties files; the allenbradley values are the Machine 2 values until Machine 1 is measured. Reference commits
  996071e (logic) and 1e87cf6 (the part round the nut), `baseline_per_file` of both manifests at 1e87cf6.
- **Dry run:** `DryRun_SplitPin_Check_UI.py` (both folders; the Machine 1 copy uses the Machine 2 pictures): window with
  the check's own picture (zoom on the nut, on at start), verdict, expected verdict from OK/ / NOT_OK/, check time,
  reason and values; Only mismatches; Save marked pictures; `--auto [folder]` without a window, exit 0 when every
  labelled picture matched apart from the two named above. `DryRun_All_Checks_UI.py` now loads an OK split pin picture
  from the dry-run folder by default.
- Run: `DryRun_SplitPin_Check_UI.py --auto` both copies: 19/19 OK right, 58/60 NOT OK right, the 2 known, PASS; window
  driven under Xvfb (Python 3.12). `Verify_Vision_Config_Equivalence.py` both: ALL EQUIVALENT (380 parameters, 13
  files). `DryRun_All_Checks_UI.py --auto` (Machine 2): split pin OK in the vote; the other checks there need the model
  weights, not in this sandbox. `DryRun_Tuning_Keys` 231/231, `DryRun_Logging_And_Live_Config` 37/37,
  `DryRun_Camera_State_File` 19/19, `DryRun_Vision_Check_Timeout` 22/22 - each on both copies.

## 163. Split pin and washer check: washer looked for round the hub's holes; two pictures relabelled OK (2026-11-19)

- **User:** (1) instead of looking for the washer round a fixed point (644,345), find the hub's holes first, take the
  hub centre from them, and place the washer by an offset from it; (2) 113431189 and 113432153 are OK (moved to
  `splitpin_images/OK`: now 21 OK, 58 NOT OK; `KNOWN_DISAGREEMENTS` in the dry run is empty).
- **Now** (`CheckSplitPinAndWasher.expectedCentre`, `vision.splitpin.centre.source=hubholes`): the hub's holes in the
  split pin picture by `CheckCap.hubHoles` - the cap (CHANGES 152) and bung (153) checks' method: the model's 4 (DOST) /
  5 (DOSTPLUS) holes, dark blobs in the band 128 - 168 px round `cap.center` (moved by the knuckle frame), on a circle
  r 140 - 160, spread <= 3, angle gaps within 15 deg. Expected washer centre = hole circle centre +
  `vision.splitpin.hub.offset.<dost|dostplus>`; the washer is looked for within 100 px of it (and the picture part
  worked on is centred there). Holes not found -> NOT OK, "the hub's N holes not found (...)". Model from the QR code
  (unknown -> DOST, as CheckCap); so a DOST hub under a DOSTPLUS QR code is NOT OK too (5 holes not found).
  `centre.source=fixed` = CHANGES 162 (centre `vision.splitpin.centre`). Log line adds `expected (x, y), expectedFrom hub
  from 4 holes (x, y) r .., DOST offset (..)` and `offsetFromExpected` (washer centre found to expected, logged only);
  the picture adds the hole circle thin blue.
- **Measured:** the 4 holes found in all 79 pictures (all DOST; radius 149 - 151). Washer centre - hole circle centre:
  part of 11:27 (+1.5 .. +3.5, -5.8 .. -1.4), 11:34 - 11:36 (+0.8 .. +3.4, +3.6 .. +5.2), 11:46 (-0.1 .. +2.4, +3.6 ..
  +4.4) - the washer sits a few px differently on each nut. Offset = mean of the 3 parts' means (1.8, 1.6); DOSTPLUS not
  measured (no pictures), the same value. Washer found 2.1 - 4.8 px from the expected centre on the OK pictures.
- **Result:** 21/21 OK and 58/58 NOT OK as labelled (with `fixed`: also 79/79). Check time about 35 - 40 ms (the hole
  search about 30 ms of it). Holes painted over -> NOT OK with the reason; DOSTPLUS model on these DOST pictures -> NOT OK.
- 3 new keys in the three properties files (`centre.source`, `hub.offset.dost`, `hub.offset.dostplus`). Reference
  commit 3d39916; both manifests point at it. Dry-run window: header shows the source and offsets, zoom 180 px so the
  hole circle shows.
- Run: `DryRun_SplitPin_Check_UI.py --auto` both copies PASS (79 labelled, 79 right); window under Xvfb;
  `Verify_Vision_Config_Equivalence.py` both ALL EQUIVALENT (383 parameters, 13 files); `DryRun_All_Checks_UI.py --auto`
  split pin OK as DOST_LHS, NOT OK as DOSTPLUS_LHS (holes); the other checks there need the model weights (not in this sandbox).

## 164. Hub holes by YOLO + MobileSAM for the cap, bung and split pin checks (FindHubHoles) (2026-11-19)

- **User:** the dark-hole method (`CheckCap.hubHoles`) used by the cap (§152), bung (§153) and split pin (§163) checks
  must give way to YOLO + MobileSAM as in `checkHub()`: writing on the hub face can fall anywhere, and no morphology gets
  past it. Move `CheckCap.hubHoles` to `FindHubHoles.findHolesWithMorphology()`; new `FindHubHoles.findHolesWithSAM()`;
  use what `checkHub()` knows - its centre for this cycle - to cut an annulus in which the holes are certain and the
  bolts at most partly visible, then `checkHub()`'s logic to settle the 4 / 5 holes. `checkHub()` itself not to change
  (it works; run as is it can take the holes or the bolts, even one or the other on the same picture).
- **Found first** (asked in the turn before): the 6 hub pictures sent (all DOST) and the 10 of `hub_images/` gave the
  right holes with the dark-blob finder, but their writing lies at r ~210, outside its band; the ink is as dark as some
  holes (grey 65 - 70 against 58 / 64 on hub_1), so the spacing rules, not the darkness, kept it out.
- **Geometry** (morphology centres, 16 hub pictures + split pin / cap ones): holes on a circle of r 150 (DOST) / 148
  (DOSTPLUS), hole radius about 10 / 14.5 px -> holes span 140 - 161 / 133.5 - 163; stud tops on r 178 - 187 / 193 - 197,
  inner edge from about 169 / 183. Stud-top centres (highlights) sit -3 .. +4.5 px off the hole circle's centre.
- **New `camera/FindHubHoles.py`:**
  - `findHolesWithMorphology` = the old `CheckCap.hubHoles`, `_holesAround`, `_holesGates`, `_informativeness` moved
    unchanged with their values (same keys, `vision.cap.seating.hub.holes.*`; centre still `cap.center`).
  - `findHolesWithSAM(picture, model, hubCentre)`: seed = this cycle's hub centre from `checkHub()` (`ImageProcessor.
    hubCentre`; the cap check passes it, the bung and split pin checks get it through `FindHubHoles.hubCentreProvider`,
    set by the camera server - the worker restores it with each job). A centre `checkHub()` took from the bolts is moved
    by `vision.hubholes.sam.seed.bolts.offset.<model>` (-0.7,4.4 / -0.7,3.6 = holes - bolts from the CHANGES 147
    offsets; CHANGES 96 saw the stud circle's centre about 7 px off the holes' - left unmoved, a DOST annulus would cut
    the far holes and take in stud slivers). Without a hub centre (dry runs): one pass over `checkHub()`'s own annulus
    (`hub.segment.center`, r 116 - 253; holes or bolts), taken only within `sam.wide.seed.max` 15 px of
    `hub.segment.center` (the parts sit within 13 px). Then the
    annulus `vision.hubholes.sam.annulus.inner` 130 - `.outer.dost` 166 / `.dostplus` 172 px round the seed and
    `checkHub()`'s steps, by calling `HubAndBearingSegmenter`'s own methods (`preprocess_image`, `segment_holes`:
    YOLO boxes + grid prompts to MobileSAM, shape / area-ratio filters, distance and size groups, the equispacing filter;
    `select_best_group`; gammas in `determine_smart_gamma_order`) on a per-thread object (own `SamPredictor` and mask
    generator; YOLO shared behind a lock) - `checkHub()`, `CheckHubAndBottomBearing.py` and `HubAndBearingSegmenter.py`
    are not changed. The first gamma wins whose group has the model's count and whose circle has r in `sam.radius`
    140 - 165, spread <= `sam.spread.max` 3, gaps within `sam.gap.max` 15 deg and centre within `sam.seed.distance.max`
    8 px of the seed; a gamma the equispacing step calls the other model is noted and the next tried. Results of the
    last `sam.cache.size` 12 pictures kept (the split pin check's 6 pictures are 3 pictures twice).
  - `findHoles` runs `vision.hubholes.method` (`sam` both machines; `morphology` = the old finder); its reason names the
    gamma / darkest percentile; `describe` for log lines.
- **Callers:** `CheckCap.hubCentreDistance` (source `cap`: `findHoles(picture, model, hubCentre)`), `BungSegmenter.
  seated_position`, `CheckSplitPinAndWasher.expectedCentre`; their log lines add the method. `CheckCap` loses the moved
  methods and values. Reference commits a74230e (all four files), 333763d (FindHubHoles: gates), 7c322df (bung / split pin docstrings) and
  3179679 (FindHubHoles: bolts seed offset, wide seed limit); both manifests.
  12 new keys `vision.hubholes.*` in the three properties files.
- **Sandbox (no YOLO weights):** with YOLO returning no boxes (grid prompts only) nothing was found. With a stand-in that
  puts a 30 px box every 14 px over the annulus (it knows nothing of holes; about 40 s a gamma on this CPU):
  DOSTPLUS hubs / caps found at gamma 1 - 4, centres steady (morphology - SAM about (-0.6, +1.0), sd ~0.4); DOST found
  on 20 of 21 split pin OK pictures and the hubs, but the small DOST holes gave unsteady centres - the 7 frames of one
  still part ranged 7 x 12 px, sd 3 px - and on DOST_03 four look-alike masks on the bare face made a perfect square
  round a centre 15 px off (the seed-distance gate added for that; the inner radius raised from 125 to 130 because the
  stand-in filled r 126 - 137 with edge fragments). With the gates: DOST_03, DOST_01, DOSTPLUS_02, split pin 113449396
  (missed before), 114643724, 114641234, DOSTPLUS_cap_01 found; asked for the other model, none of the 7 found. Without a
  seed (stride 26, 36 px boxes; the 14 / 30 stand-in ran out of memory on the hub check's wide annulus): DOSTPLUS_cap_01
  and DOST_04 found; on split pin 113613847 the wide pass took a "4 bolts" group 18 px off (the thin pass then made a
  square round it - hence `wide.seed.max`), with it 15 px: the next wide group was 12 px off and nothing passed - NOT
  found. The no-seed path depends on the wide pass; production has the hub check's centre. These stand-in numbers
  say nothing about the real YOLO's steadiness: **the cap / bung / split pin offsets stay the dark-blob ones until the
  laptop run**.
- **Before the exe (laptop, plant-pc step 14):** `DryRun_Hub_Holes_SAM.py --wrong-model` (new, both folders: every
  picture of hub / split pin / cap / bung sets, SAM against morphology, the mean difference per check and model and the
  offsets that keep each target - `vision.cap.seating.hub.distance.offset.cap.*`, `vision.bung.seating.hub.offset.*`,
  `vision.splitpin.hub.offset.*`; its sd decides whether the cap's 5 px rule holds) -> put its offsets in the
  properties; `--seed hubcheck` (hub pictures: the real `checkHub()` as the seed, as in production) prints SAM holes -
  hub check centre per model and kind -> `vision.hubholes.sam.seed.bolts.offset.<model>`; then the cap / bung / split
  pin dry runs.
- **Dry runs:** `--holes morphology` (`_hub_holes_option.py`, both folders) runs the old finder with the offsets
  measured with it, for runs without the YOLO weights: `DryRun_SplitPin_Check_UI`, `DryRun_Bung_Hub_Holes`,
  `DryRun_Cap_Hub_Distance` (H6 / H7 now test `findHolesWithMorphology`), `DryRun_Cap_Hub_Distance_UI`.
- Run: `Verify_Vision_Config_Equivalence` both ALL EQUIVALENT (395 parameters, 14 files); with `--holes morphology`:
  `DryRun_SplitPin_Check_UI --auto` both PASS 79/79, `DryRun_Bung_Hub_Holes` 6/6, `DryRun_Cap_Hub_Distance` 15/15
  (Machine 2); `DryRun_Tuning_Keys` 231/231, `DryRun_Camera_State_File` 19/19, `DryRun_Vision_Check_Timeout` 22/22 both;
  the SAM path with the stand-in as above. Not run: anything with the real YOLO (laptop).

## 165. Hub holes by SAM: offsets from the laptop run; DOST hole circle 140 - 155; bolts seed moved in the dry runs too (2026-11-19)

- **User's laptop runs (real YOLO):** `DryRun_Hub_Holes_SAM.py --wrong-model` - 161 pictures compared; asked for the
  other model, never found; SAM did not find the holes on 7 pictures where the dark blobs did: bung A02, A17, A18, B04,
  B05, B10 (all labelled bad - a bung off its place covering holes; NOT OK is the right verdict there) and split pin
  112450874 (a NOT OK picture). Every good picture: found. Times 0.9 - 2 s (wide pass + thin pass), 0.5 s with a seed.
  `--seed hubcheck` (the real hub check as the seed, as in production): 10/10, SAM holes - hub check centre
  (+0.1, -0.4) DOST holes, (0.0, -0.3) DOSTPLUS holes, (-0.65, +3.65) DOSTPLUS bolts (one picture).
- **Found in the numbers:** DOSTPLUS SAM centres are steady (sd 0.2 - 0.6 px against the dark blobs). DOST groups came
  in three circle radii, each with its own centre: r ~147 (26 split pin pictures, morph - SAM dy -6.9), r ~149 (39,
  -5.2), r ~161 (13, -3.0) - for one still part (11:46, 6 frames) the SAM centre moved 3.4 px in y while the dark blobs
  moved 0.7. r ~147 went with seeds from the bolts (41 of 46; the dry run's wide-pass seed from bolts was not moved by
  the bolts offset - seed 4.5 - 5.8 px off), r ~149 with good seeds (holes seeds, or bolts seeds 2 - 4.5 px off), r ~161
  is not the holes' circle (holes 147 - 151) - most likely stud slivers at the annulus edge.
- **Changed:** `vision.hubholes.sam.radius` 140 - 165 -> 140 - 155 (the r ~161 groups refused, the next gamma tried);
  the dry-run seed from the hub check's annulus moved by the bolts offset when that pass took the bolts, as production
  moves the hub check's; `sam.seed.bolts.offset.dostplus` -0.7,3.6 -> -0.7,3.7 (measured). Reference commit c18d18f
  (FindHubHoles, CheckSplitPinAndWasher); both manifests.
- **Offsets for the SAM centres** (Machine 2; from the r ~149 groups = good seeds, morph - SAM mean added to the dark-blob
  offsets): cap 0.2,-0.1 -> **0.4,-4.8** (7 pictures, sd 0.6 / 1.2) and -0.6,-5.3 -> **-0.7,-4.4** (14, sd 0.2 / 0.4);
  bung 4.9,-13.7 -> **5.2,-17.9** (4, sd 0.6 / 1.3) and 4.2,-16.0 -> **4.5,-15.8** (29, sd 0.4 / 0.6); split pin
  1.8,1.6 -> **0.9,-3.6** (39, sd 1.1 / 0.7) and DOSTPLUS -> **1.9,2.1** (no DOSTPLUS split pin pictures: the DOST washer
  offset with the DOSTPLUS SAM / dark-blob shift of the cap, bung and hub pictures). Machine 1 (allenbradley) cap / bung
  offsets stay 0,0; split pin and hubholes values follow Machine 2 (code defaults).
- **Not settled:** the DOST cap and bung offsets rest on 7 and 4 pictures; the DOST spread with good seeds is still
  about 1.2 px sd in y - the cap allows 5 px. To be confirmed by a laptop re-run after this change: DOST groups at r ~149
  only, `--seed hubcheck` holes - hub check about 0, and the split pin parts' SAM centres steady within a part.
- Run: verifier both ALL EQUIVALENT (395 parameters); `DryRun_SplitPin_Check_UI --auto --holes morphology` both PASS
  79/79; `DryRun_Tuning_Keys` both 231/231. Not run: anything with YOLO (sandbox).

## 166. Hub holes by SAM: each hole refined by a MobileSAM box prompt; the dry run's offset suggestion fixed (2026-11-19)

- **User's laptop re-run after 165 (all dry runs):** every good picture found, the other model never; ALL EQUIVALENT;
  the other dry runs PASS. SAM missed 7 where the dark blobs found holes: bad bungs (A02, A17, A18, B04, B05, B10 as
  before, and A19 - labelled bad, `hole_centre` null: the bung covers the holes, NOT OK is right) and NOT OK split pin
  112450874.
- **Found 1 - the dry run's suggested offsets were wrong:** it added mean(morphology - SAM) to the live offsets, which
  are SAM offsets since 165 - the shift counted twice (e.g. cap.dost 0.2,-11.0, splitpin 0.5,-9.5, bung.dost 5.4,-22.4
  printed). The real shifts of that run: split pin DOST (-0.36, -5.86), cap DOST (-0.19, -6.21), cap DOSTPLUS
  (-0.02, +0.95), bung DOST (+0.18, -4.52), bung DOSTPLUS (+0.30, +0.13). Fixed: base = `MORPHOLOGY_OFFSETS`
  (`_hub_holes_option.py`), the live value printed as "now". The 165 offsets were computed by hand from that base; they stand.
- **Found 2 - DOST centres still in two modes with good seeds:** r ~147.4 and ~149, ~2 px apart in y; on one still part
  (11:34 - 11:36, 12 frames) the SAM centre jumped 346.6 <-> 348.5 in y. The masks of `checkHub()`'s steps are either a
  hole's whole outline or its darker lower part. `checkHub()` itself is not changed.
- **Changed (`camera/FindHubHoles.py`):** a group that passes the gates is refined: each hole a MobileSAM box prompt of
  +-18 (DOST) / 24 (DOSTPLUS) px on the picture as taken (a crop round the holes, the per-thread predictor), three
  masks, the largest with an area 60 - 1200 / 60 - 2000 px taken, re-centred, prompted once more; a hole that moves more
  than 6 px = not the holes (next gamma); the refined circle passes the gates again. Gates moved into `_failedGates`.
  Reference commit fb1faf5; both manifests; 7 keys `vision.hubholes.sam.refine*` (both vendors, three files).
- **Measured (sandbox, MobileSAM; YOLO stand-in boxes):** refinement prototype on DOST split pin frames of one still part:
  sd (0.28, 0.09) px against SAM 346.6 <-> 348.5; 19 frames of the 11:20 - 11:28 part sd (0.63, 0.22) (dark blobs
  1.26, 0.74); DOST caps 11 pictures sd (0.17, 0.18), refined - morph y +4.5 (165's cap shift -4.7: the r ~149 mode);
  box shifted +-3 px: same centres within 0.1 px (2 iterations); DOSTPLUS caps no change (sd 0.6 / 0.9 vs dark blobs
  0.5 / 0.7 - DOSTPLUS was steady already). In `FindHubHoles` with the stand-in: split pin DOST found r 148.2 - 148.3,
  spread 0 - 0.9, y 346.7 - 347.1; a stand-in junk group that passed the gates before (r 146) now refused (a hole moved
  16.8 px); two split pin pictures not found with the stand-in (the laptop's YOLO found them; stand-in only).
- **Offsets:** unchanged (165's came from the r ~149 groups, which the refinement keeps). **Laptop re-run needed:**
  `DryRun_Hub_Holes_SAM.py --wrong-model` - DOST centres in one mode, per-part sd, the printed offsets (now right) into
  the properties if they differ by more than ~0.5 px.
- Run: verifier both ALL EQUIVALENT (402 parameters, 14 files); `DryRun_Tuning_Keys` 231/231; with `--holes morphology`:
  `DryRun_SplitPin_Check_UI --auto` PASS 79/79, `DryRun_Bung_Hub_Holes` 6/6, `DryRun_Cap_Hub_Distance` 15/15;
  `DryRun_Vision_Check_Timeout` 23/23, `DryRun_Camera_State_File` 19/19; `DryRun_Hub_Holes_SAM --tiled-yolo` as above.
  `DryRun_Cap_Production_Path` 8/12 in the sandbox - same before this change (b4787b9): the stand-in boxes there sit on
  the cap, not the holes. Not run: anything with the real YOLO (laptop).

## 167. Cap, bung and split pin windows show the hub's holes (2026-11-19)

- **Asked:** the three dry-run windows to check the hub holes visually.
- **Changed (test only):** `_hub_holes_option.HubHolesRecorder` (both folders) records every `FindHubHoles.findHoles`
  result while a window runs; `DryRun_Cap_Check_UI`, `DryRun_Bung_Check_UI` and `DryRun_SplitPin_Check_UI` ring each hole
  found (blue) and cross the hole circle's centre on the picture shown (outlines only), and print a `HUBHOLES` line
  (`FindHubHoles.describe`: centre, radius, spread, gap, method, gamma, seed, or why not found) in the list / terminal.
  `--holes morphology` now on the cap and bung windows too. No vision file changed.
- Run (sandbox): `DryRun_SplitPin_Check_UI --auto --holes morphology` PASS 79/79 with HUBHOLES lines; the recorder and
  drawing on a DOSTPLUS cap picture (5 holes) and bung A01 (4 holes) by the dark blobs - rings on the holes. The cap and
  bung windows' own check paths need the YOLO weights (cap mask, bung holes): not run here; the windows themselves
  need tkinter (not in the sandbox's torch Python) - parsed, not opened.

## 168. Code graph of the live code; must-read in CLAUDE.md (2026-11-20)

- **Asked:** go through the code, draw a detailed graph, store it in docs and make it a must-read in CLAUDE.md.
- **Added (docs only, no code changed):** `docs/code_graph/CODE_GRAPH.md` with six diagrams (Mermaid source `*.mmd`,
  rendered `*.svg`): runtime topology, Redis wiring (every stream / key with writer and reader), camera server and the
  dependencies between vision checks, IOServer threads and PLC layer, the 28-state cycle, import graph of the live
  modules. Generator scripts in `docs/code_graph/tools/` (`graph.py` AST reachability from `MainProgram` /
  `OtherServices`, `mermaid_modules.py`, `redisgraph.py` send/read call sites, `smoke.py` import test, `build_doc.py`).
  CLAUDE.md: must-read note at the top; `docs/README.md`: new first row.
- **Found:** 87 of 379 modules are live. Redis call sites agree with HLD §4, with two details now written down: the
  IOServer also writes `camera2fe.q` (NOT OK when the camera is down or late, `_pictureNotOkWithoutAnswer`), and
  `cycleSettings:<QR>` is written only by the IOServer (FE and DB read it).
- **Run (sandbox):** Python 3.11 venv from `requirements.txt` without the Windows / CUDA-only packages (torch 2.11 from
  PyPI, MobileSAM from GitHub): 73 of 85 live modules import; the other 12 need tkinter (11 frontend files) or
  `winsound` (heartbeat server). All six diagrams render with mermaid-cli 11. `6_modules.mmd` regenerates identically.

## 169. Hub holes: new findHolesWithSAM - wide annulus round the hub-check centre, candidate sets, holes by radius and mask area (2026-11-20)

- **Why:** Uddi ran the CHANGES 164 - 166 finder in the windows: slow and completely wrong results. His design
  (2026-11-20), built as given: (a) the hub centre from this cycle's `checkHub()` (`ImageProcessor.hubCentre`); (b) an
  annulus round it holding the holes and the bolts, `checkHub()`'s routine up to the sets that pass its equidistant /
  equal-angle filters, the top 2 - 3 sets, the holes told from the bolts (bolt masks larger); no holes -> NOT OK;
  (c) everything after the holes exactly as at `8a57f85` (CHANGES 163), in the same order.
- **Checked against `8a57f85`:** the cap, bung (states 24 and 13) and split pin code after the hole step is that
  commit's (only the hole call and its log text differ; the camera server's `_assessCap` and 2-of-3 logic unchanged);
  the siemens values are that commit's except the three offsets changed in 165.
- **Changed (`camera/FindHubHoles.py`):** `findHolesWithSAM` -> `findHolesWithSAM_Slow` (method `sam_slow`, kept). New
  `findHolesWithSAM` (method `sam`): (1) the hub-check centre as it is (given / `hubCentreProvider` / dry runs'
  `hubCentreFromPicture`), none -> not found; (2) annulus 116 - 253 round it, per gamma in checkHub()'s smart order
  checkHub()'s steps (`_candidateSets`: the mask generator, shape classification, area-ratio and shape filters, distance
  and size groups, small / off-centre groups dropped, 4- and 5-polygon candidates per group through the equidistant
  (strict, else relaxed) and equal-angle filters, as `filter_subgroups_by_equispacing` steps 1 - 3) without the final
  pick; (3) of the model's count, the best set of each group, the 3 best; among those with circle r 135 - 165 and total
  mask area <= `holes.area.max.<model>`, the smallest area = the holes; else the next gamma; none -> not found.
  Reference commit bf52a06; both manifests; 6 keys (`vision.hubholes.annulus.inner/outer`, `.candidates.max`,
  `.holes.area.max.dost/.dostplus`, `.holes.circle.radius`) in the three properties files; `method` takes `sam_slow`.
- **Holes or bolts - the one departure from "smallest total mask area" alone, and why:** in the sandbox (MobileSAM, YOLO
  replaced by boxes of 36 px every 26 px, CPU) DOST hole sets had total area 2045 - 2891 and bolt sets 3022 - 4342, but
  DOSTPLUS hole sets 3028 - 3495 and bolt sets 3027 - 3434 (a DOSTPLUS hole, r ~14.5, is as large as a stud top), and
  at some gammas the three best sets were all bolts. So the sets are first restricted to a hole-circle radius (holes
  ~148, bolts 178 - 196: the part's geometry), and the "best set" is taken per group (holes and bolts are different
  distance groups; before, several 5-subsets of the bolt group filled the top 3). The area cutoffs (3400 / 4500) are
  provisional: `DryRun_Hub_Holes_SAM.py` prints the laptop's ranges.
- **Sandbox runs (stand-in boxes; say nothing about the real YOLO's masks or speed):** hub pictures DOST_01 - 05
  found (r 146 - 150.5, at gamma 1 except DOST_05 / DOST_01 at gamma 3), asked as DOSTPLUS never found; DOSTPLUS_01
  and _03 found (r 150.9 / 148.3, gamma 3), DOSTPLUS_02 not found (only bolt-radius sets at every gamma); 27 - 340 s a
  picture on this CPU with ~300 stand-in boxes. The run was stopped before the cap / split pin / bung pictures (CPU).
  The SAM centres sat 6 - 7 px below the dark-blob centres on DOST (outline vs shadowed inside, as in 165): the
  offsets must be measured for this finder.
- **Dry runs:** `DryRun_Hub_Holes_SAM.py --method sam|sam_slow` (default sam), `--seed hubcheck` default for every set
  (the real checkHub() on the picture), per-gamma sets printed, area ranges per model at the end; `_hub_holes_option`
  installs `hubCentreFromPicture` (real checkHub() on the picture, cached) for the windows and dry runs.
- Code graph: `3_camera.mmd` (FindHubHoles methods, bung edge labelled) and its SVG, `CODE_GRAPH.src.md` note; CODE_GRAPH.md rebuilt.
- Run: verifier both ALL EQUIVALENT (408 parameters, 14 files); `DryRun_Tuning_Keys` both 231/231; with `--holes
  morphology`: `DryRun_SplitPin_Check_UI --auto` both PASS 79/79, `DryRun_Bung_Hub_Holes` 6/6, `DryRun_Cap_Hub_Distance`
  15/15; `DryRun_Camera_State_File` 19/19. Not run: anything with the real YOLO (laptop) - the new finder's results,
  speed, area cutoffs and the offsets all come from the laptop run.

## 170. Hub holes: the new finder on the laptop - area cutoffs and offsets set (2026-11-20)

- **Uddi's laptop run** (`DryRun_Hub_Holes_SAM.py --wrong-model`, real YOLO, 163 pictures compared):
  - time 0.5 - 0.7 s a picture (found at gamma 1 on all but 5); 3 - 4 s when nothing is found (all 6 gammas);
  - asked for the other model: never found;
  - total mask area: DOST hole sets 1633 - 3005 (median 2585), bolt sets 6786 - 8029; DOSTPLUS holes 2871 - 3896
    (3491), bolts 8064 - 10260 - the area separates holes and bolts on both models (the sandbox's stand-in boxes had
    suggested otherwise for DOSTPLUS, §169); hole circles r 145 - 153.4, bolts 180 - 196;
  - every good picture found (hub 10, split pin OK and NOT OK frames but one, cap 30 incl. skewed, good bungs 24);
  - not found: split pin 112450874 (a NOT OK picture), bad bungs A18, B05, A20, B02, B03, B07, C18 - C20 (holes
    covered: NOT OK is right); A02, B01, B04, B06, C02 (bad): no centre because the dry run's checkHub() on the bung
    picture did not pass - in production the centre comes from the state-5 hub picture.
- **Set (siemens, three files):** `holes.area.max` 3400 / 4500 -> **4900 / 6000** (between the ranges); offsets from the
  printed means (morphology offset + mean(morphology - SAM)): split pin DOST 0.9,-3.6 -> **1.3,-3.6** (78 pictures, sd
  1.0 / 1.1); cap 0.4,-4.8 -> **0.3,-4.7** (16, sd 0.6 / 0.8) and -0.7,-4.4 -> **-0.7,-4.5** (14, sd 0.2 / 0.3); bung,
  good-labelled pictures only (the printed bung means included the bad ones, sd 2.1 / 3.0): DOST 5.2,-17.9 -> **4.6,-16.8**
  (11, sd 0.2 / 0.3), DOSTPLUS 4.5,-15.8 -> **4.7,-16.3** (12; A05, C14, C16 left out - settled as wrongly labelled good,
  2026-11-19). Split pin DOSTPLUS 1.9,2.1 unchanged (no DOSTPLUS pictures). allenbradley unchanged (code defaults).
- **Seen, not changed (put to Uddi):** (1) bad bungs A17 (r 145.2, spread 4.5, gap 7), A06, C15, C17 (spread 2.9 - 3.1)
  gave hole sets although a bung lies over a hole; every good picture of every set had spread <= 1.8 (good bungs <= 0.4).
  The seating step then measures against a wrong seated position. (2) DOST_skewed_03: two hole-radius sets, (1725,
  r 147.4) and (2874, r 149.0); the smallest area took the first, its centre ~2 px off the others' (seed d 5.1 vs ~3).
- Run: verifier both ALL EQUIVALENT; `DryRun_Tuning_Keys` both 231/231; `DryRun_SplitPin_Check_UI --auto --holes
  morphology` both 79/79 (the morphology dry runs set their own offsets). Not run: anything with YOLO (sandbox).

## 171. Dry runs give the new hole finder a hub centre (2026-11-20)

- **Uddi's laptop runs:** `DryRun_Bung_Hub_Holes` 6/6 (23 good bungs SEATED at 2.3 - 6.2 px from the seated position,
  20 bad NOT SEATED; disputed A05 42.8 px, C14 16.8 px with face 0.944 failing, C16 no pattern), `DryRun_Cap_Hub_Distance`
  15/15 (H6: holes on all 40 cap / hub pictures, r 147.3 - 152.7), but `DryRun_Cap_Production_Path` 8/12: P1 (all
  seated caps, both models) and P5 "2 of 3 -> OK" NOT OK.
- **Cause:** that dry run calls the camera server's `assessPictures` with the cycle's earlier pictures but no hub check
  ran, so `ImageProcessor.hubCentre` was None and the new finder (§169: none -> not found) gave "holes not found" ->
  NOT seated. The thin finder (§164) had looked for a centre itself when there was none, so it had passed before. Not a
  production fault: in a cycle state 5's hub check sets the centre. `DryRun_Cap_Check`, `DryRun_Bung_Check`,
  `DryRun_Bung_Rotation` and `DryRun_Bung_Frame` had the same gap (not run in this round).
- **Changed (dry runs only, both folders):** `DryRun_Cap_Production_Path` runs the real `checkHub()` on the model's
  first hub picture and sets `ImageProcessor.hubCentre` as state 5 does (and turns off the dry-run fallback, so the
  centre comes only from that hub check, as in production); the other four install `_hub_holes_option` (the real
  `checkHub()` on each picture) and take `--holes sam|sam_slow|morphology`. `hubCheckOnPicture` reports a failing hub
  check instead of raising.
- Run (sandbox): `DryRun_Cap_Production_Path --holes morphology` 12/12 (was 8/12 here); without `--holes` 8/12 here
  (no YOLO: the hub check cannot run - said so in its output); Machine 1 copy: seating off, skipped. `DryRun_Cap_Check`
  and `DryRun_Bung_Check` need the YOLO weights even with `--holes morphology` (cap mask, bung holes): laptop.

## 172. Bung window: where each check's time goes (2026-11-20)

- **Uddi's Bung window run** (67 pictures, each as DOST and DOSTPLUS): no picture OK as both models; 12 OK as DOST, 15
  as DOSTPLUS, all SEATED at 2.0 - 7.0 px; the pictures matching A05 / C14 (42.7 / 16.8 px), A17 (99.0) and B10 (63.7)
  NOT OK. Times: OK 2.4 - 2.9 s (4.6 s the first), NOT OK up to 9.3 s.
- **Sequence in the code** (as at `8a57f85`): the bung's hole pattern of the model's count, gamma by gamma (2.0, 7.75,
  4.0, 6.5, 1.0) until a definitive answer (a square on a DOSTPLUS part = NOT OK); only when it passed, the seating step:
  the hub's holes -> not found NOT SEATED -> seated position, face rules. One step outside that order: when the seating
  step did not run (pattern failed, or no pattern at any gamma), `CheckBung.drawSeatingMarks` still looks for the hub's
  holes, only to draw the reference oval on the NOT OK picture (CHANGES 157). With the dark blobs that took
  milliseconds; with YOLO + MobileSAM up to all 6 gammas (3 - 4 s when the holes are covered), in production too. In
  the dry run the hub check it runs on the picture first (not in production) adds to it.
- **Changed (dry run only):** the Bung window prints a TIMES line per check: bung holes (and how many gammas), hub
  holes in the seating step, the seating step's face part, hub holes for the marks only, the rest, and the dry run's
  own hub check (not in production) separately.
- Not run here: the window (tkinter / YOLO) - parsed only.

## 173. Bung: no hub-hole search for the marks of a NOT OK picture; skip.splitpin.and.washer.check (2026-11-20)

- **Bung (Uddi: "Skip this. Not required at all"):** `CheckBung.drawSeatingMarks` no longer looks for the hub's holes
  when the seating step did not run (hole pattern failed or no pattern at any gamma); it draws only what the check
  found (no reference oval on such a picture). From CHANGES 157 to 172 it ran `seated_position` there only to draw the
  oval - with YOLO + MobileSAM up to all 6 gammas (3 - 4 s when the holes are covered), in production too. Verdicts
  unchanged: the decision sequence is (a) the bung's pattern of the model's count, (b) only then the hub's holes, (c)
  not found -> NOT SEATED, (d) seated position and face rules. `CheckNoCapBung` uses the same function. `CheckBung` is
  not in the verifier's manifest (no vision keys of its own).
- **Split pin:** new key `skip.splitpin.and.washer.check` (tuning block, three properties files, delivered False),
  read through `getTuning` at every check: True -> `checkSplitPinAndWasher` returns OK with the picture as taken and
  logs "check skipped" (the camera server still takes the pictures and runs its 2-of-3 vote, all OK); False -> the
  check runs and its result decides. Reference commit 2484023 (CheckSplitPinAndWasher), both manifests.
- **Dry runs:** `DryRun_SplitPin_Check_UI --auto` checks the switch (True: a NOT OK picture -> OK, picture unchanged;
  False: NOT OK). `DryRun_Tuning_Keys` picks the new key up by itself.
- Run: verifier both ALL EQUIVALENT (408 parameters); `DryRun_Tuning_Keys` both 235/235; `DryRun_SplitPin_Check_UI
  --auto --holes morphology` both PASS 79/79 + switch; `DryRun_Bung_Hub_Holes --holes morphology` 6/6. Not run: the
  bung window / bung checks with YOLO (laptop).

## 174. Start-up: the database backup beside the login; the launcher tries Redis every 2 s (2026-11-20)
- **Why:** the plant PC is slow to start (Uddi). `startup_diag.txt` of 2026-11-20: firmware 11.9 s, Windows 35 - 84 s
  (to the desktop 21 - 46 s); the launcher waited 0 s for Redis on 8 of 10 logons (3, 11, 17 s on the others). After
  the launcher, MainProgram ran `doBackup()` (pg_dump + compress + validate of the production database) in the main
  thread before the mode and login dialogs, which waited for it - a time that grows with the database.
- **Backup in a thread** (`MainProgram.startDatabaseBackupThread`, non-daemon): starts before the camera / PLC ping and
  runs while the operator chooses the mode and logs in; "Database backup at start-up done / NOT done in N s".
  `waitForDatabaseBackup(thread, before)` waits for it (and logs "Waited N s ...") before the DB server starts and
  before each early exit (no mode, login cancelled, 5 failed logins), so no half-written backup is left.
- **Found on the way - why the order had to change:** `setDatabaseName(createDB=True)` runs `ALTER TABLE ... ADD COLUMN
  IF NOT EXISTS` (a no-op, but it still takes the table's exclusive lock), which waits for pg_dump's ACCESS SHARE
  locks; a read does not (measured on PostgreSQL: the ALTER waited the full 5 s of a held lock, a SELECT 0.03 s).
  Left where it was (after the mode dialog) it would have frozen the start-up after the mode choice until the backup
  ended; the DB server runs the same call at its start, and a waiting ALTER also holds up every later query on the
  table. So: both databases are now set up **before** the backup starts (the same two calls, before the mode dialog
  instead of after it); after the mode only `setDatabaseName(mode, createDB=False)` selects the name; the DB server
  (P5, started last) waits for the backup. The camera, QR, IO, frontend and heartbeat servers start without waiting.
- **What the start-up backup now holds:** the database after the start-up schema step (normally a no-op; a new build's
  column changes would be in it). The data are the same as before; the previous run's shutdown backup is from before
  that step.
- **Launcher** (`Start-Hub-And-Disc-application - Production.vbs`): Redis / PostgreSQL tried every 2 s (`POLL_MS`; 3 s
  before). Each try also starts a hidden PowerShell (~0.3 - 1 s), so MainProgram starts on average ~0.5 s sooner when
  Redis is late - small, as asked; the launcher log showed Redis late on 3 of 10 logons only. The file must be copied
  into `shell:startup` by hand (not part of the exe).
- **Dry run:** `DryRun_Startup_Backup.py` (both folders, 16 checks): B1 returns at once, non-daemon; B2 the wait; B3 an
  exception in `doBackup`; B4 the order in `main()` (AST); B5 the PostgreSQL locks above; B6 the real `doBackup` in the
  thread writes a file.
- Docs: LLD start-up steps 3, 4, 6; code graph (runtime node, start order); plant-pc-setup (§76); plant-pc-verification
  §15; READMEs; CLAUDE.md (§5 rule, §7 row).
- Run: `DryRun_Startup_Backup` 16/16 on both machines; `DryRun_Heartbeat_And_Startup` 29/29 on both. Not run: the
  launcher (Windows only; VBScript syntax checked by eye), the real start-up on the plant PC.

## 175. Start-up check of the model files: missing -> message, MainProgram exits before starting anything (2026-11-20)

- **Asked (Uddi):** the behaviour without `models` was odd - the camera server was restarted again and again. Check the
  models at the start of MainProgram, before any process is launched; if anything is missing, a message on the screen
  that the models directory is missing and needed, wait 5 s, close MainProgram without launching anything.
- **Found:** `models/` is not in the exe (`runNuitka.bat`); `camera/ModelManager.py` loads
  `<exe folder>/models/MobileSAM/weights/mobile_sam.pt` and `.../MobileSAMv2/weight/ObjectAwareModel.pt`. On the laptop
  the exe in `MainProgram.dist` had no `models` there, so `CameraProcessorServer.CameraServer` stopped itself
  (`verifyVisionDependencies` -> `sys.exit(1)`, exit code 1), the process supervisor started it again every ~10 s, and
  the reason never reached the log: it was handed to the slave logger's daemon thread just before `sys.exit`. The
  `mobile_sam` code itself is compiled into the exe (the laptop exe printed "MobileSAM loaded" without `models`), so
  the two weights files are all that is read from `models` at run time.
- **Changed:** `utils/ModelsCheck.py` (new): `missingModelFiles(root)` - every file of `models.required.files` missing or
  empty; `missingModelsMessages` - one paragraph for the screen (the error popup re-wraps its text), each full path for
  the console. `MainProgram.checkModelFilesPresent()`: `main()`'s first step after `setLogProcess`, before clearing
  Redis and before P1; missing -> printed, `showErrorMessage(..., 5)`, the rest of the 5 s waited if OK is pressed
  sooner, `sys.exit(1)`; present -> "Model files present: 2 of 2 under <root>/models". The console window is left open
  (the message stays readable there). New tuning keys in all three properties files: `models.required.files` (the two
  paths, relative to the exe folder) and `models.missing.message.sec` (5).
- **Not changed:** the camera server's own `verifyVisionDependencies` exit stays for what MainProgram does not check
  (a weights file that is there but cannot be loaded, an import that fails) - such a case still restarts in a loop.
- Docs: LLD §1 step 1; CLAUDE.md (§5 rule, §7 row 16); plant-pc-verification §16; both dry-run READMEs; code graph
  (runtime node, import graph - 88 live modules of 389).
- Run: `DryRun_Models_Check` 20/20 on both machines (new: M1 the files = what ModelManager loads, M2 present, M3 missing /
  empty / no folder, M4 message, popup and 5 s, M5 `main()` exits 1 before Redis is cleared or any process created,
  check first after `setLogProcess` (AST), M6 this clone); `DryRun_Tuning_Keys` 243/243, `DryRun_Startup_Backup` 16/16,
  `DryRun_Heartbeat_And_Startup` 29/29 on both. Not run: the popup itself on Windows (stood in; FreeSimpleGUI), the exe.

## 176. Frontend crash in Kannada / Hindi in the exe: Qt's messages to the log, not the C stderr; model file sizes (2026-11-20)

- **Seen (Uddi, plant PC and laptop exe):** with `kn` (and `hi`, chosen on the main screen) the frontend ended at once,
  restarted by the supervisor every ~10 s; `en` fine; PyCharm fine in every language. Event Viewer: `ucrtbase.dll`,
  exception 0xc0000409, fault offset 0x11858 (same each time); `logs\faults\frontend-<pid>.log` empty (0 bytes).
- **Diagnosis:** 0xc0000409 in ucrtbase is the C runtime's fast fail - `abort()` or an invalid parameter. `abort()`
  raises SIGABRT first, which `faulthandler` (on in every server, LogSettings) would have dumped: the empty files rule it
  out, leaving a C-runtime call that the runtime rejected. The one C-runtime writer in the frontend that runs only with an
  Indic language is Qt itself: without a message handler Qt writes its messages to the C stderr, and laying out Kannada /
  Tamil / Devanagari in a font without those scripts (the tiles use Courier) gives `qt.text.font.db: OpenType support
  missing for "Courier", script 18` (11 / 16 / 18); English gives no Qt message at all (measured: the language dry run
  printed 339 / 174 / 172 such lines per font for the Indic languages, none for English). In the exe, Nuitka's console
  attach (`HelpersConsole.c`, `--windows-console-mode=attach`) reopens the C stdout / stderr on CONOUT$; in PyCharm stderr
  is a pipe. **Confirmed on the plant PC (Uddi, 2026-11-20):** the exe built before this change, started with
  `QT_LOGGING_RULES=*=false` (Qt emits no message at all), ran without a crash and switched through all four languages.
- **Changed:** `frontend/QtMessages.py` (new): `installQtMessageHandler()` - every Qt message to Python: each distinct
  (type, category, text) logged once through `logBoth`, repeats counted (`messageCounts()`); critical / fatal also
  written to Python's `sys.stderr` (WriteConsoleW in the exe, not the C runtime), a fatal one with the Python stack (found
  on the way: with only the logger, a fatal "no Qt platform plugin" message was not on the screen when the logging server
  was not up). `CosThetaMonitorDimensions.getAppInstance()` installs it before the QApplication exists.
- **Model files (CHANGES 175, from Uddi's listing of the Machine 2 PC):** `models.required.sizes=40728226,140395126`
  (tuning block, all three properties files): a required file of another size (incomplete copy) is reported like a
  missing one; empty key = sizes not checked. The listing also shows ~2.8 GB nothing reads: `models\MobileSAM\MobileSAM\`
  (a second copy of the clone), `MobileSAMv2\weight\sam_vit_h.pt` (2.5 GB), `l2.pt`, `MobileSAMv2\weight\mobile_sam.pt`,
  `yolo_models\yolov8m.pt` (only the unused `camera/HandDetector.py`) - left in place.
- Docs: LLD §1 step 1, §5.1; CLAUDE.md (§4 note, §5 rule, §7 row); plant-pc-verification §11.7; READMEs; code graph
  (89 live modules of 392).
- Run: `DryRun_Qt_Messages` 15/15 on both machines (new; child processes with fd 2 captured: Indic laid out in Courier -
  6 distinct Qt messages in the handler, none on fd 2, each logged once; English - none; without the handler Qt writes
  them to fd 2; qWarning from Python; a fatal message on the console with the stack). `DryRun_Frontend_Languages` 100/100
  on both (no raw `qt.` line on stderr now; about 3,500 before); `DryRun_Models_Check` 23/23, `DryRun_Tuning_Keys` 247/247 on
  both; `DryRun_FEServer_To_DBServer_Pipeline` passes; `DryRun_FEServer_Cycles` 166/167 - F1 ("Rotation Not Done" fits
  its tile) fails the same with this change reverted (sandbox fonts), not caused by it. Not run: the exe on Windows.

## 177. Camera reboot from Machine Settings; hub picture and model in the cap / bung / split pin windows; Qt messages dry run on Windows (2026-11-20)

- **Camera reboot (Uddi, 2026-11-20).** Hikvision cameras reboot on the ISAPI call `PUT /ISAPI/System/reboot` with HTTP
  digest authentication - the same authentication as the camera server's keepalive (`RTSPCam`, `GET
  /ISAPI/System/deviceInfo`). `utils/CameraReboot.py` (new): `rebootCamera()` sends it to `vision.camera.ip` / `.port`
  with `vision.camera.uid` / `.pwd`, waits `camera.reboot.timeout.sec` (10 s, tuning block, all three properties
  files); accepted = HTTP 200 and statusCode 1 (or none); 401 / other codes / no answer reported, never raises, logged.
  Machine Settings (`frontend/SimplePopups.py`): a Reboot Camera button under Save / Reset / Cancel - after the
  dialog's own password; `rebootCameraFromMachineSettings`: only when the frontend says no component is in the cycle
  (`cycleIdle` = state READ_QR_CODE, passed by `AshokLeylandFrontEnd`; unknown or raising = not allowed - a vision check
  during the 1 - 2 min restart would be NOT OK), then `machine.settings.password` once more with the camera's IP and
  "Entering the password is the final confirmation to reboot the camera" (no Yes / No, as Save), the state checked
  again, then the call; the outcome shown and logged ("Camera reboot asked by <user> from Machine Settings: ...").
  The camera server is not told: its reconnect thread takes the stream back. Seven new texts, kn / ta / hi drafts
  (native-speaker review pending, as the others). Code graph: the frontend -> camera ISAPI edge (the only one).
- **Split pin pictures of 2026-11-20 (Uddi: "failed", with a hub picture).** 9 uploads = 5 distinct pictures (four sent
  twice), all DOSTPLUS (5 bolts / 5 holes). Cause of the failure: `DryRun_SplitPin_Check_UI` checked every picture as DOST
  (fixed DOST QR code; `measure(image)` defaults to DOST) - all five "the hub's 4 holes not found". Production takes the
  model from the QR code: not affected. Now `--model DOST|DOSTPLUS` / Model in the window. As DOSTPLUS (sandbox,
  `--holes morphology`; no YOLO weights here): 5 holes found in all five, centre (644, 351), within 1 px of the hub
  picture's (645.3, 351.1) - the hub picture is usable as their hub centre. Verdicts: 508d02c5, 5e1e2765, 3d64a6c4 show
  the plain hex nut (no castle nut, pin or washer) -> NOT OK "washer not found", right. 59fbc4cc: straight pin lying
  across the washer, legs not turned -> NOT OK ("washer outline not round", tip radius 59.2 > 56), right by the strict
  rule. be498f5c: pin in, legs turned down along the washer, blue marking visible lower left -> NOT OK "split pin legs
  not found (8 blue px < 100)". Not tuned: the paint is dark navy here (median S 68, V 52 in the strip; the DOST OK
  pictures have 204 - 364 marker px), and in this lighting the dark hub interior itself is in the hue range - the
  unmarked straight pin (59fbc4cc) has more raw "blue" px (218 at S >= 90, V >= 55) than be498f5c (124), so lowering the
  colour limits would pass unmarked pins. The DOSTPLUS offset (1.9,2.1) and the limits were never measured on DOSTPLUS
  OK parts: needs labelled DOSTPLUS pictures (plant-pc-verification §13.4).
- **Hub picture in the cap / bung / split pin windows** (`_hub_holes_option.py`, both dry-run folders): `--hub-image
  <picture>`, or "Hub picture..." / "No hub picture" in each window. The real `checkHub()` runs once per model on that
  picture; its centre is passed to `FindHubHoles.findHoles` as the hub check's ("from the hub check (given)"), as
  production passes `ImageProcessor.hubCentre`; the hub check failing on it = no centre = holes not found = NOT OK, as in
  production (`HUBPICTURE` line says which). Without a hub picture: `checkHub()` on each checked picture (CHANGES 171).
  `findHoles` is wrapped once in `applyHolesOption`; production code unchanged.
- **`DryRun_Qt_Messages` on Windows** (laptop: 12/15, Q2 / Q3 / Q4): the children ran with `QT_QPA_PLATFORM=offscreen`,
  which on Windows has no system fonts ("QFontDatabase: Cannot find font directory") - no Indic font messages, and that
  warning counted against Q3. Now Qt's native platform where there is a display (offscreen only on Linux without one);
  Q3 counts only `qt.text.font.db` messages; Q2 also checks that every font message Q4 saw on fd 2 without the handler
  arrives in the handler. 16 checks.
- Docs: LLD §5 (frontend passwords / camera reboot), CLAUDE.md (§4, §7 row 17, module count), plant-pc-verification §13.4,
  §17 and results row, both READMEs, code graph (90 live modules of 395).
- Run (sandbox): `DryRun_Camera_Reboot` 26/26 on both (new; stand-in camera with real digest auth); `DryRun_Qt_Messages`
  16/16 on both offscreen and 16/16 on xcb under Xvfb (native branch); `DryRun_SplitPin_Check_UI --auto --holes
  morphology` 79/79 labelled (21 OK / 58 NOT OK, unchanged); `DryRun_Tuning_Keys` 251/251, `DryRun_Models_Check` 23/23,
  `DryRun_Frontend_Languages` 100/100, `Verify_Vision_Config_Equivalence` ALL EQUIVALENT on both (it first reported 10
  baselines missing - the sandbox clone was shallow; unshallowed). Hub-picture control smoke-tested under Xvfb. Not run:
  the cap / bung windows (torch and Tk not in one Python here), the SAM path with the YOLO weights, Windows, a real camera.

## 178. Split pin dry run: an unreadable --hub-image stops it; no pictures is not a PASS (2026-11-20)

- Uddi's laptop run with an empty `C:\Temp\splitpin_dostplus` and no `Hub.png` there printed "Hub picture could not be
  read - hub centre from each checked picture itself" and then "0 pictures: RESULT: PASS". Both hid the mistake.
- `_hub_holes_option.installHubPictureOption`: a `--hub-image` given on the command line that cannot be read stops the
  run (exit 1, "could not be read - stopped"); in the windows a bad choice is still reported and the window goes on.
  `DryRun_SplitPin_Check_UI --auto`: no pictures in the folder (nor in `OK\` / `NOT_OK\`), or no such folder ->
  "RESULT: FAIL (no pictures)", exit 1. Both dry-run folders.
- Run: empty folder -> FAIL, exit 1; missing hub picture -> stopped, exit 1; `--auto --holes morphology` 79/79 PASS.

## 179. Split pin dry run: the hub picture is not checked as a split pin picture; DOSTPLUS laptop run (2026-11-20)

- Uddi's laptop run of 2026-11-20 (`--model DOSTPLUS --hub-image ...\Hub.png --auto`, YOLO + MobileSAM): the hub check
  passed on the hub picture (centre 645.2, 349.9 from 5 holes); every split pin picture's own 5 holes found at about
  (644.5, 349.1) r 149.0, spread 0.3 - within 1 px of the hub picture's; ~0.5 s per picture. Verdicts as in CHANGES 177
  (hex nut only -> washer not found; straight pin across the washer -> outline not round; 18-00-19-5 -> 8 blue px).
  `Hub.png`, lying in the same folder, was checked too ("washer not found"): `picturesIn` now leaves out the
  `--hub-image` file. Both dry-run folders.
- DOSTPLUS washer offset: washer centre - hole-circle centre = (-0.4, -2.2) and (+1.4, -2.6) on the two pictures with
  a washer, against `vision.splitpin.hub.offset.dostplus` 1.9,2.1 (derived, never measured; DOST 1.3,-3.6). Not changed:
  two pictures, none labelled OK; the offset only places the 100 px search (offsetFromExpected is logged, not judged).
- Run: the 5 DOSTPLUS pictures with Hub.png in their folder -> 5 checked, Hub.png left out; `--auto` 79/79 PASS.

## 180. Study: the split pin washer and legs by MobileSAM (+ YOLO boxes) and a fitted washer profile (2026-11-21)

- Uddi's design of 2026-11-21: after the hub's holes, find the spindle centre relative to them per model, get the washer
  by MobileSAM prompts, fit a mathematical profile, then the eye, the half opposite it, its leg-free area and the share
  the legs cover; a cut-off from labelled pictures. Labels of the 78 DOSTPLUS plant pictures of 2026-11-20: up to and
  including `17-28-12-134071-5` NOT OK, after it OK; unpainted but correctly turned legs are OK.
- `test/machine*_dryrun/Study_Washer_SAM.py` (new, study): per picture the holes; the castle nut (3 prompt sets, RANSAC
  circle on each candidate) and the washer (5 prompt sets), every candidate printed and one picked; the outline fitted to
  the washer mask (circle + 12 scallops + perspective, robust); eye; legs half; coverage (not bright washer, and not in
  the mask); legs standing out; the split pin from the eye (4 prompt sets); the legs from a point grid over the legs half
  and from YOLO boxes (production's prompting; every box, its mask and kind printed); summaries, the spindle / washer
  centre against the hole circle per model, best cut-offs (colour coverage, point-grid legs, YOLO legs) with the
  pictures each gets wrong; pictures with outlines only. Runs on the laptop (GPU, YOLO weights); the log is for tuning.
- Sandbox run (CPU, holes by the dark-blob finder, no YOLO; 79 DOST + 78 DOSTPLUS pictures):
  - washer: MobileSAM returns washer + nut as one disc (IoU 0.85 - 0.92 with the expected disc); the outline fitted to it
    r 46.1 - 47.9 DOST / 45.8 - 47.1 DOSTPLUS (sd ~0.35), 12-scallop amplitude ~1 px; the hex nut r 42, 12-fold 0.32,
    6-fold 2.4 - the washer is told from the hex nut by its profile;
  - washer centre - hole circle centre: DOST (2.6, 3.3) sd (1.2, 1.1), DOSTPLUS (1.3, -1.7) sd (0.2, 0.5) (dark-blob
    hole centres; one DOSTPLUS part);
  - nut by MobileSAM: circle residual 0.7 px but radius 24.5 - 34 and centre sd ~2 px - partial masks, not reliable yet;
  - legs: the washer disc includes what lies on it (not-in-mask coverage 0.00 - 0.06); colour coverage does not separate
    (DOST OK 0.34 - 0.47 / NOT OK 0.19 - 0.49; best joint cut 74 / 79 DOST, 48 / 72 DOSTPLUS), brass legs pass as washer
    colour; a point on the eye gives the eye loop only (the shank is under the nut); single-point prompts on the legs half
    mostly return the whole disc. The YOLO-box step is untested here (no weights; its code path run with a stand-in).

## 181. Study_Washer_SAM_UI: the washer study in a window, every profile and centre drawn (2026-11-21)

- Uddi: a window over the study - choose a folder, run every picture with a configurable gap (3 s), the profiles and
  centres drawn so he can see what happens and give feedback; extensive terminal logging kept.
- `test/machine*_dryrun/Study_Washer_SAM_UI.py` (new): Folder / Model / Hub picture / Seconds between pictures (0 - 20,
  3 at start) / View (washer zoom, hub holes wide) / Save marked pictures / Start / Stop; the analysis runs in a worker
  thread, the window shows each picture, its values, the log and a list; the summary at the end. Outlines and markers
  only on the pictures, the legend beside them (CLAUDE.md §5).
- `Study_Washer_SAM.py`: `analyse` returns its geometry at every step (holes, estimated centre, nut, washer outline and
  centre) so a picture is drawn as far as it got; `drawFull` draws on the whole picture (zoom or wide, hub holes and
  their circle included); `picturesIn`, `setUp`, `processPicture` shared by the batch run and the window.
- Run (sandbox): the analysis and drawing on CPU (3 pictures, holes by the dark-blob finder); the window under Xvfb on
  Python 3.12 fed those results (torch and Tk are not in one Python here): 3 pictures shown, list, panel, log, both
  views, summary. Not run: the window with the models (laptop).

## 182. Hub holes: the wide annulus centred from the picture being checked itself (2026-11-21)

- Uddi's laptop study runs of 2026-11-21: 25 of 57 DOST split pin pictures without the hub's holes. Cause: the hub
  picture chosen (13 Nov, another part) put the hub-check centre at (644.3, 334.5); the holes of those pictures sit
  round (645.3, 347.3) - 13 px. checkHub()'s steps group masks by distance from the given centre within 15 %: from 13 px
  off, the 4 holes (r ~149) lie 136 - 162 px away (ratio 1.19) and split into groups of fewer than 4; the bolts (r ~190)
  stay in one group - every failure listed a bolt set (r ~181) only. A wider annulus would not help (the holes are
  inside 116 - 253 already). Decision (Uddi): take the centre from the same picture always - a little slower, but a
  proper result every time; the holes must be got accurately.
- `camera/FindHubHoles.py`: `vision.hubholes.centre.source` (`picture` on both machines; `hubcheck` = before). Pass 1
  `_centreFromPicture` / `_centreInWideAnnulus`: checkHub()'s steps (`_candidateSets`, on the per-thread segmenter
  object - the split pin check runs its pictures in threads, so not the shared checkHub()) in the wide annulus round
  `hub.segment.center` (where checkHub() looks; moved by the knuckle frame), per gamma until a gamma gives sets; of the
  model's count the best set of each group; a set passing the holes' rule (circle r 135 - 165, total mask area <=
  `holes.area.max.<model>`, the smallest area) gives the centre, else the bolts' set (circle r in
  `vision.hubholes.bolts.circle.radius` 165 - 210, least distance variance) + `sam.seed.bolts.offset.<model>`; kept per
  picture. Pass 2 = the wide-annulus search of CHANGES 169 round that centre, unchanged. Pass 1 finding no set -> the
  cycle's hub-check centre (`hubCentre` given / `hubCentreProvider` / dry runs' `hubCentreFromPicture`), the reason in
  the seed text. Cap, bung and split pin checks alike (all through `findHoles`). Reference commit with the values
  hard-coded (4f080a0), both manifests pointed at it; verifier ALL EQUIVALENT on both machines.
- `DryRun_Hub_Holes_Centre_Source` (new, both folders): 8 checks with checkHub()'s candidate sets stood in.
- Docs: CLAUDE.md §5 (hub holes (a)), LLD (hub holes), plant-pc-verification §14.8, READMEs, `_hub_holes_option` text,
  code graph (diagram 3).
- Run (sandbox): `DryRun_Hub_Holes_Centre_Source` 8/8 both; `Verify_Vision_Config_Equivalence` ALL EQUIVALENT both;
  `DryRun_SplitPin_Check_UI --auto --holes morphology` 79/79 both (morphology does not use the centre). Not run: the
  YOLO + MobileSAM path (no YOLO weights here; a CPU run with stand-in boxes did not finish in 30 min) - the laptop's
  `DryRun_SplitPin_Check_UI --auto`, `DryRun_Hub_Holes_SAM --wrong-model`, `DryRun_Cap_Hub_Distance`, `DryRun_Bung_Hub_Holes`
  decide.

## 183. Hub holes refined for the split pin check: one outline per hole (2026-11-21)

- Uddi's laptop runs of 2026-11-21 (Study_Washer_SAM_UI, 57 NOT OK + 21 OK DOST split pin pictures): the holes found in
  all 78 since CHANGES 182, but in two outlines - the whole hole (circle r ~149, spread <= 0.5) or its darker lower part
  (r 147.5 - 148.3, spread 0.7 - 1.5; 8 + 6 pictures). The washer did not move, the hole centre did: washer centre -
  hole circle centre dy -0.8 to -2.1 in the first mode, -2.8 to -3.5 in the second (sd 0.88 in y against 0.25 in x).
  The same two modes as the thin finder of CHANGES 165, which 166 cured by refining each hole.
- Decision (Uddi): refine each hole by a box prompt before the circle fit; re-measure the offset for the split pin only.
- `camera/FindHubHoles.py`: `findHoles(..., check=None)`; `vision.hubholes.refine.checks` (`splitpin` on both
  machines) names the checks whose holes are refined (`refineFor`). For those, `_holesInWideAnnulus` refines the chosen
  holes set (`_refinedSet`: `_refine` of CHANGES 166 with its `sam.refine.*` values - per hole a MobileSAM box prompt,
  the largest mask in `refine.area`, centred again and prompted once more - then the circle fitted again and gated:
  radius in `holes.circle.radius`, `sam.spread.max`, `sam.gap.max`). No outline mask, or a hole moved more than
  `sam.refine.move.max` (6 px), rejects that set: the next holes set, else the next gamma. The result keeps `refined`,
  `refineMoved`, `unrefined` (centre, radius, spread); `describe()` writes them into the log line; the cache key holds
  the refine flag. Cap and bung pass no check and stay unrefined: their offsets were measured on unrefined centres.
- `camera/CheckSplitPinAndWasher.py`: `findHoles(..., check="splitpin")`. Reference commit 760b7da (both files, values
  hard-coded), both manifests pointed at it; verifier ALL EQUIVALENT on both machines.
- Dry runs: `DryRun_Hub_Holes_Refine` (new, both folders, 8 checks, sets and box prompt stood in);
  `DryRun_SplitPin_Check_UI` prints `SPLITPIN OFFSET <model>` at the end (mean washer centre - hole circle centre over
  the pictures checked OK; the value for `vision.splitpin.hub.offset.<model>`), window and `--auto`;
  `Study_Washer_SAM` asks for the split pin's refined holes; `DryRun_Hub_Holes_SAM` refines the splitpin set's holes;
  the `findHoles` wrappers of `_hub_holes_option` and `DryRun_Bung_Check_UI` pass `check` on.
- The offset itself is not changed here (`siemens.vision.splitpin.hub.offset.dost` 1.3,-3.6, dostplus 1.9,2.1): it
  must come from the refined centres on the laptop (YOLO weights) - the SPLITPIN OFFSET line.
- Measured (sandbox, real MobileSAM, no YOLO): `_refine` on the 79 split pin pictures of the repo, started from the
  dark-blob hole centres (stand-in for the YOLO + SAM ones): a mask for every hole of all 79; refined circle r 147.3 -
  148.9 (sd 0.27), spread <= 1.1 (dark blobs: r 148.5 - 151.4, spread up to 1.6). The dark-blob centres sit higher, so
  they moved up to 7.9 px - more than the 6 px gate; from the SAM centres (whose two modes are ~2 px apart) the move is
  expected to stay well under it. 1.6 - 5.7 s per set on the CPU (4 holes, 2 prompts each); on the GPU a few tenths.
- Run (sandbox): `DryRun_Hub_Holes_Refine` 8/8 both; `DryRun_Hub_Holes_Centre_Source` 8/8 both;
  `Verify_Vision_Config_Equivalence` ALL EQUIVALENT both; `DryRun_SplitPin_Check_UI --auto --holes morphology` 79/79
  both (morphology is not refined); `DryRun_Tuning_Keys` 251/251. Not run: the refine on YOLO + MobileSAM sets (no YOLO
  weights here) - the laptop's `DryRun_SplitPin_Check_UI --auto` and `Study_Washer_SAM_UI` decide.

## 184. Hub-hole refinement switched off (2026-11-21)

- Uddi's laptop runs with CHANGES 183 (Study_Washer_SAM_UI, 21 OK + 57 NOT OK DOST): washer profile centre - hole
  circle centre sd OK 0.59 / 0.41 px (unrefined 0.25 / 0.88), NOT OK 0.78 / 0.85 (0.47 / 0.82): the y split shrank, x
  got worse - no more repeatable overall; `t_holes` median 1.15 - 1.23 s against ~0.92. Decision (Uddi): stay with the
  unrefined holes - same accuracy, faster, which matters on the shop floor.
- `camera/FindHubHoles.py`: `vision.hubholes.refine.checks` is a `vStr` now, default and properties `none` (an empty
  value would count as a missing key and log a `[Configuration]` line); `refineFor` splits it on commas, `none` / empty
  = no check. The refine code (`_refinedSet`, `_refine`) and `findHoles(..., check=)` stay; the split pin check still
  names itself, so `refine.checks=splitpin` turns it back on. Reference commit f9ab748, both manifests; verifier ALL
  EQUIVALENT on both machines.
- `DryRun_Hub_Holes_Refine`: R6 sets the switch to `splitpin` itself; R6b = the default refines nothing (9 checks).
- Run (sandbox): `DryRun_Hub_Holes_Refine` 9/9 both; `Verify_Vision_Config_Equivalence` ALL EQUIVALENT both; the
  siemens properties give `'none'`, `refineFor('splitpin')` False, no `[Configuration]` line.

## 185. Study_Legs_Length_UI: the split pin legs as a length over the washer (2026-11-21)

- Uddi's design of 2026-11-21: (b) the washer's profile, washer present True / False; (c) the pin head, the legs on the
  opposite half (no head found: find that half directly); (d) the legs' imprint over the washer mask; (e) holes filled,
  skeleton, the length of each leg; (f) the total against a cut-off; (g) without the blue marker if possible.
- `test/machine*_dryrun/_legs_length.py` (new): on Study_Washer_SAM's washer step - washer present = fitted outline r in
  44 - 50 px; pin head = the largest bright unpainted blob just outside the outline, else the half with the most
  non-washer pixels on it; the imprint = not washer-coloured pixels inside the completed outline and beyond the
  washer's inner edge (median first washer radius round the nut - the dark ring where washer meets nut was traced as a
  leg on every picture before this), on the legs half, pieces touching the spindle edge kept; holes filled, Zhang-Suen
  thinning (numpy, no new library), length = straight steps + sqrt 2 diagonal steps per line. Also the length of what
  sticks out past the outline on the legs half (information). `--legs-colour none|blue|both`.
- `test/machine*_dryrun/Study_Legs_Length_UI.py` (new): window as Study_Washer_SAM_UI (folder, model, hub picture,
  seconds, save; panel, log, list) and `--auto`. The picture: the washer mask filled light green, the pin head and the
  legs' lines red, the length of each leg and the total written on it - text on the picture asked for by Uddi for this
  study window (CLAUDE.md keeps it off production and check windows).
- Sandbox runs (MobileSAM, `--holes morphology`, colour none):
  DOST (21 OK / 58 NOT OK, all washers found): total OK 78 - 185, NOT OK 4 - 194; best cut on the total alone (>= 84)
  1 OK called NOT OK and 18 NOT OK called OK; with the length past the outline (<= 11) 5 and 2.
  DOSTPLUS (66 OK / 12 NOT OK as labelled 2026-11-21): the 6 hex nut pictures washer NOT present (r 42.1 - 42.8), all 66
  OK present; total OK 32 - 184, straight pin 53 - 94 - the total does not separate; past the outline OK 0 on every
  picture, straight pin 18 - 26 - that separates all 78.
  Time 1.7 - 9.5 s per picture on the CPU (the washer step; the legs' length ~0.1 - 0.3 s).
- Window built and closed under Xvfb (Python 3.12, no models); the measuring path run on 3.11 with the models.

## 186. Study_Legs_Length_UI: the washer ring, its dark share and dark length (2026-11-21)

- Uddi, 2026-11-21 11:06: the washer is a ring round the spindle, not the filled outline. (b) centroid of the outer
  profile; (c) inner edge estimated from the width from the outline's outermost limits (19 px) and from its innermost
  limits (11 px), averaged; (d) Otsu inside the outline, lowered by 10; the inner edge = the circle within 3 - 4 px of the
  estimate whose share of light pixels jumps most over the two next smaller circles; (e) the ring filled light green;
  (f) dark share in the ring; (g) the ring laid flat, the dark length; (h) both together.
- `_legs_length.ring()`: centroid by the moments of the outline polygon; tips / valleys = 98th / 2nd percentile of
  the outline's radius round the centroid; circles every 0.5 px from estimate - 5 to + 4; jump = share - mean of the
  two smaller circles. Dark share = not-light pixels / ring pixels, for the whole ring and the legs half (opposite the
  pin head). Dark length = degrees (one column per degree from the side opposite the legs axis) with >= 25 % of their
  ring pixels dark, / 360 (legs half: / 180). The picture shows the ring instead of MobileSAM's disc, and the two
  values. Summary: best single cut on each value (either direction) and the best pair.
- Sandbox runs (MobileSAM, `--holes morphology`, the 79 DOST and 78 DOSTPLUS pictures; ring found on all 157):
  DOSTPLUS (66 OK / 6 straight pin; the 6 hex nut out by washer present): dark share, legs half, OK 0.32 - 0.45,
  straight pin 0.22 - 0.29 -> cut 0.317 separates all; dark length, legs half, OK 0.62 - 0.81, straight pin 0.39 - 0.55
  -> cut 0.617 separates all (the DOSTPLUS pictures come in identical pairs: 33 OK parts' worth, 3 straight-pin).
  DOST (21 OK / 58 NOT OK): dark share, legs half, OK 0.32 - 0.47, NOT OK 0.15 - 0.45 -> best cut 0.354: 3 OK called
  NOT OK, 4 NOT OK called OK; dark length, legs half, OK 0.53 - 0.86, NOT OK 0.13 - 0.82 -> best 0.600: 2 and 12; both
  (share >= 0.345, length >= 0.594): 4 and 3. Whole-ring values separate less (DOST share 8 + 3, length 15 + 0).

## 187. Study_Legs_Length_UI: 2 x 2 grid, Otsu adjustment dropdown, legs from the binarised ring (2026-11-21)

- Uddi, 2026-11-21 12:01: show (1) the final washer mask alone, light green; (2) the Otsu-binarised washer mask,
  everything else black; (3) the legs' skeleton as seen in that binarised picture; (4) the picture with the green mask
  and the red legs. A dropdown -25 .. +50 (step 5) added to the computed Otsu value.
- `_legs_length.drawn()` returns the 2 x 2 grid (each tile the washer enlarged 3x, captions and values on the tiles);
  `OTSU_ADJUST` (-10, as before) replaces `OTSU_LOWER_BY`, `ring(..., otsuAdjust)` / `measure(..., otsuAdjust)`.
  The legs' imprint is now the dark pixels of the binarised ring on the legs half, pieces starting within 4 px of the
  ring's inner edge (no ring: as before); colour blue / both add the blue marker inside the ring.
- `Study_Legs_Length_UI.py`: "Otsu +" dropdown (and `--otsu-adjust`); a change applies from the next picture and redraws
  the picture shown (or selected) at once from its kept geometry (`redraw`, REDRAW log line); canvas 900 x 900.
- Sandbox runs at -10 (MobileSAM, `--holes morphology`): dark share / length unchanged from CHANGES 186 (DOSTPLUS legs
  half both 72 / 72; DOST share 3 + 4, both 4 + 3). The legs' total from the binarised ring separates less than the
  colour imprint of 185 on DOST (best cut >= 166: 12 OK called NOT OK, 5 NOT OK called OK; with past the outline <= 11:
  5 + 4); DOSTPLUS: total >= 38 calls 4 of 6 straight-pin pictures OK.
- Window built under Xvfb (3.12); the dropdown redraw run on a cached picture at -25 and +50; `--auto` both sets.

## 188. Study_Legs_Length_UI: closing after Otsu, Otsu dropdown from -50, the Otsu value on the picture (2026-11-21)

- Uddi, 2026-11-21 13:29: after Otsu, a closing with a circular kernel (dropdown 3 / 5 / 7 / 9, 5 at start); the Otsu
  adjustment dropdown from -50 (to +50); the original binarisation value top left of the picture.
- `_legs_length`: `CLOSE_KERNEL` (5); `ring(..., closeKernel)` closes the light (binarised) picture with an elliptical
  kernel and keeps it inside the outline; the inner-edge search, dark share, dark length and the legs all use the
  closed picture. `measure(..., closeKernel)`. Tile (1) starts with "Otsu (original) N"; tile (2) shows the closing.
- `Study_Legs_Length_UI.py`: "Close" dropdown and `--close-kernel`; the Otsu dropdown -50 .. +50; either change redraws
  the picture shown; the setting in the SET / summary lines.
- Sandbox runs at Otsu -10, close 5 (MobileSAM, `--holes morphology`):
  DOST: total >= 33: 2 OK called NOT OK, 15 NOT OK called OK; with past the outline <= 29: 1 + 5; dark share legs half
  >= 0.247: 2 + 8; dark length legs half >= 0.450: 4 + 5; both: 4 + 3.
  DOSTPLUS: the closing fills the straight pin's dark pixels too - dark share / length no longer separate it (4 - 6 of
  6 called OK; without the closing, 186: all 72); past the outline still separates all.
- Window built under Xvfb (3.12) with both dropdowns, redraw run at (-50, 3) and (0, 9) on a cached picture.

## 189. Study_Legs_Length_UI: a second, fixed closing of 3; the Close dropdown starts at 3 (2026-11-21)

- Uddi, 2026-11-21 14:00: after the closing set by the dropdown, a second closing with a fixed circular kernel of 3; the
  dropdown's initial value 3 (`CLOSE_KERNEL`). Issued without a run here, at Uddi's request - he runs it on the laptop.

## 190. Study_Legs_Length_UI: Close dropdown 3 / 4 / 5 / 6 / 7 (2026-11-21)

- Uddi, 2026-11-21 14:04: the Close dropdown offers 3, 4, 5, 6, 7 (was 3, 5, 7, 9). Issued without a run here, at his
  request. His note: the fixed second closing of 3 makes no visible difference - expected: a closing is idempotent, so a
  closing with a 3 kernel after one with a kernel of 3 or more adds almost nothing, and OpenCV's 3 x 3 "ellipse" is a
  plus shape that only fills single-pixel gaps.

## 191. Study_Legs_Length_UI: Otsu adjustment 0, first closing 4, second closing from a dropdown 3 / 4 (2026-11-21)

- Uddi, 2026-11-21 14:09: the Otsu adjustment starts at 0 (`OTSU_ADJUST`), the first closing at 4 (`CLOSE_KERNEL`); the
  second closing's kernel from a dropdown ("then", 3 / 4, 4 at start; `CLOSE_KERNEL_2`, `--close-kernel-2`); either
  change redraws the picture shown. Issued without a run here, at his request (both files parse).

## 192. Study_Red_Legs_UI: the split pin legs by their red marking (2026-11-21)

- Uddi, 2026-11-21 15:02: go for the red marking (67 new DOST pictures, legs painted maroon: 31 OK / 36 NOT OK); keep
  the blue method in its own method; (a) the washer mask as before, (b) the legs by their red, (c) the washer mask
  binarised - legs black, the rest white, (d) the black share on the legs half and the black length across the whole
  washer; a window and `--auto`.
- `_legs_length.measureBlue` = the blue / not-washer-colour method (unchanged). `_legs_red.py` (new): `redPaint` (hue
  <= 15 or >= 155 of 180, S >= 35, V >= 30, opened 2 x 2 - the paint sits at hue 155 - 180 with S 30 - 170 in these
  JPEGs; the seal and washer have S < 35 above V 30), `measureRed` (ring from `_legs_length.ring`, legs side = the 180
  deg with most red on the ring and up to 45 px past the outline, else opposite the pin head; black = red in the ring;
  black share on the legs half and whole; black length = degrees of the flat ring with >= 25 % red, whole and legs
  half; reach = furthest red px past the outline on the legs side, in pieces touching the outline), `drawnRed` (2 x 2:
  washer mask; legs black / washer white; red found, magenta past the outline; result with values).
- `Study_Red_Legs_UI.py` (new): window (folder, model, hub picture, seconds, "Red S >=" 20 - 70 / "V >=" 15 - 60
  dropdowns that redraw the picture, save) and `--auto`; OK / NOT_OK or "Not OK" sub-folders; holes not found -> the
  washer step from `vision.splitpin.centre` (HOLES line), so the legs can still be studied; summary with the cuts on
  each value and on pairs.
- Sandbox run (`--holes morphology`: the dark-blob finder found no holes on these new pictures, so every picture went
  through the fixed centre - on the laptop the YOLO + MobileSAM holes give the centre): washer present 66 / 67 (the NOT
  OK 124757553: outline r 50.6). Best pair: black share legs half >= 0.037 and reach <= 18.9 px -> 1 OK called NOT OK,
  1 NOT OK called OK (66 pictures with a washer). Reach alone <= 16.8: 1 + 10 (the 7 NOT OK without red pass on reach,
  fail on share). The share / length alone do not separate (12 - 13 OK called NOT OK: on the 12:56 parts the ring
  from the fixed centre does not lie under the legs). Margins thin (reach: OK up to 18.9, a NOT OK at 18.1); fitted
  on these pictures.
- Window built under Xvfb (3.12), the red dropdown redraw run; `--auto` on the 67 pictures (3.11, MobileSAM).

## 193. Study_Red_Legs_UI: Uddi's RGB rule for the red (2026-11-21)

- Uddi, 2026-11-21 15:38: under the washer mask, grey > 148 = white; of the rest, R > G, R > B and R - B > 20 = black
  (the legs), else white.
- `_legs_red.redPaintRGB` (the rule, applied to the whole crop so the reach past the outline uses it too; opened
  2 x 2), `RED_METHOD` "rgb" (default) / "hsv" (192), `GREY_WHITE` 148, `RGB_R_MINUS_B` 20. Window: "Red by" (rgb / hsv),
  "grey >" (108 - 188) and "R-B >" (0 - 50) dropdowns, redraw; `--red-method`, `--grey-white`, `--rb`.
- The two pictures the 192 rule got wrong (share legs half >= 0.037, reach <= 18.9): OK 125618252 (share 0.035) and
  NOT OK 112450399 (legs folded on the washer like an OK part - label to be checked).
- Sandbox runs (fixed centre, as 192):
  R - B > 20: the dark maroon over the seal is lost (on these JPEGs it is purple-ish, R only 5 - 15 above B); best pair
  black share legs half >= 0.005 and reach <= 7: 6 OK called NOT OK, 3 NOT OK called OK - worse than hsv.
  R - B > 5: best pair black share legs half >= 0.049 and reach <= 16.2 px: 0 OK called NOT OK, 1 NOT OK called OK
  (112450399 again). Margins thin: OK reach up to 16, NOT OK from 18 (the low-reach NOT OK caught by share 0.042 -
  0.044 against the cut 0.049).

## 194. Study windows: Start button off the screen (2026-11-21)

- Uddi, 2026-11-21 16:26: no Start button in Study_Red_Legs_UI. Cause: the picture area was a Tk Label created with
  width / height but no picture - Tk then counts them in characters, so the window was several screens wide (8640 px
  under Xvfb) and the button bar's Start / Stop lay far off to the right. Fix: the Label gets a black picture of that
  size at once (Study_Red_Legs_UI, Study_Legs_Length_UI, Study_Washer_SAM_UI); in Study_Red_Legs_UI the red limits move
  to a second row, Save / Start / Stop stay on the first. Under Xvfb: windows 1703 / 1988 px wide, Start at x 1505 /
  1790.
- Labels: 112450399 is OK (Uddi): with it, the 192 HSV rule (share >= 0.037, reach <= 18.9) gets 66 of 67 right - the
  one left, OK 125618252, has the paint pressed off by the pliers (to be repainted by the operator).

## 195. Study_Red_Legs_UI: only the washer and the red legs (2026-11-21)

- Uddi, 2026-11-21 16:40: 5 s per picture - the red study ran every step of the washer study (nut prompts, eye,
  coverage, 4 pin prompt sets, the leg grid, YOLO + a mask per box); keep only what finds the washer and the red legs.
- `Study_Washer_SAM.STEPS` (new): `nut` and `legs_study` (eye, coverage, pin, leg grid, YOLO boxes, blue share), True
  by default (the 180 / 181 study unchanged). With `nut` off the centre is holes + offset; with `legs_study` off
  `analyse` returns after the washer prompts and the outline fit. `Study_Red_Legs_UI` turns both off (`--all-steps`
  turns them on again). The red step itself (ring, red paint, values) was 0.01 - 0.02 s.
- Sandbox (CPU): 1.8 - 2.1 s per picture instead of 4.8 - 6.0 (holes 0.1 s with the dark-blob finder; nearly all the rest
  is MobileSAM's image embedding on the CPU, a small part of that on the GPU).

## 196. Study_Red_Legs_UI: pipelines "bilateral" and "guided" (2026-11-21)

- Uddi, 2026-11-21 18:26: on the final washer mask, the two halves (pin head, legs) filtered independently with values
  from inside each half only - grey first, bilateral d = -1, sigmaColor 50, sigmaSpace 15 (the project's masked
  bilateral, `utils/ImageNormalisationWithMask.bilateral_filter_masked`), or a guided filter; then two openings with
  circular kernels 4 and 4, Otsu, two closings 4 and 4; that picture decides. Issued without a run here, at his request
  (the files parse).
- `_legs_filtered.py` (new): `bilateralInMask`, `guidedInMask` (cv2.ximgproc.guidedFilter is in opencv-contrib only,
  not in the project's opencv-python 4.11: the same filter written with cv2.boxFilter, self-guided, box means weighted
  by the half's mask; radius 4, eps 650 = (0.1 x 255)^2), `measureFiltered` (measureRed for the ring, legs side and red
  reach; the black values from the filtered picture, the red ones kept as red_*), `drawnFiltered` (2 x 2: mask,
  filtered and opened ring, binary, result).
- `Study_Red_Legs_UI.py`: "Pipeline" dropdown red / bilateral / guided and open / close kernel dropdowns (3 - 6); every
  change redraws the picture shown; `--pipeline`, `--open-1`, `--open-2`, `--close-1`, `--close-2`.

## 197. Study_Red_Legs_UI: sigmaColor / sigmaSpace boxes, guided filter from them, further refinement of the inner edge (2026-11-21)

- Uddi, 2026-11-21 18:42: (a) the filters with sigmaColor 100 / sigmaSpace 20 from integer text boxes; (b) the washer's
  inner edge refined: on the circle of the inner edge the grey values split dark / light by Otsu, the changes counted;
  the radius grows by 1 px until a circle has a long run of light; that is the new inner edge; a check box "Further
  refine inner boundary of washer". Issued without a run here, at his request (the files parse).
- `_legs_filtered`: `SIGMA_COLOR` 100, `SIGMA_SPACE` 20 for both filters. The guided filter's own parameters are a
  radius and eps; from the bilateral's: radius = round(1.5 x sigmaSpace) (the window cv2.bilateralFilter takes with
  d = -1) = 30, eps = sigmaColor^2 = 10000 (`GUIDED_RADIUS` / `GUIDED_EPS` override).
- `_legs_length.ring`: `REFINE_INNER` (off), `REFINE_INNER_STEPS` 12 px, `REFINE_LIGHT_RUN` 0.25 of the circle,
  `REFINE_SAMPLES` 720; stops when a circle lies mostly past the outline; keeps the first edge when no circle qualifies;
  `rInnerJump`, `refineLog` (r, Otsu, changes, longest light run share), `refined`.
- `Study_Red_Legs_UI`: sigmaColor / sigmaSpace entries (Return or leaving the box applies them and redraws), the check
  box, an INNER log line per picture; `--sigma-color`, `--sigma-space`, `--refine-inner`.

## 198. Study_Red_Legs_UI: the filtered pipeline in Uddi's order (2026-11-22)

- Uddi, 2026-11-22 00:33: washer mask -> grey -> guided filter -> Otsu with an adjustment, separately on the legs half
  and the pin head half -> open twice -> close twice -> area and length. 196 / 197 opened the grey picture before Otsu,
  ran one Otsu over the whole ring and had no adjustment.
- `_legs_filtered.measureFiltered`: the filter per half (as before), then Otsu + `OTSU_ADJUST` per half, then the two
  openings and the two closings on the binary picture, inside the ring. Tile (2) shows the filtered grey, tile (3)
  the two Otsu values, the adjustment and the kernels. `Study_Red_Legs_UI`: "Otsu +" dropdown (-50 .. +50, 0) and
  `--otsu-adjust`. Issued without a run here (the files parse).

## 199. Study_Red_Legs_UI: the filter on the whole washer mask (2026-11-22)

- Uddi, 2026-11-22 00:41: the guided / bilateral filter runs once on the whole washer mask, using only the grey values
  inside it - not per half. Otsu + adjustment stays per half. `_legs_filtered.measureFiltered` changed accordingly
  (the mask-weighted guided filter / the masked bilateral with the ring as the mask). Issued without a run (parses).

## 200. Study_NoColour_Legs_UI: the filtered pipeline with no colour (2026-11-22)

- Uddi, 2026-11-22 00:46: in the filtered pipeline (196 - 199), do red / blue play a part? Read through: yes, three
  places - `_legs_filtered.measureFiltered` started from `_legs_red.measureRed`, which (1) took the legs side from the
  half with the most red, (2) found the pin head as a bright blob that is *not paint* (blue hue or red), and (3)
  measured the reach past the outline on red pixels; also the washer prompts' bright points used saturation (V >= 130
  and S <= 110). The black share / length themselves came from the grey filter, Otsu and morphology only.
- `_legs_nocolour.py` (new): grey only - washer ring and washer present as before; filter once on the whole mask; legs
  side opposite the pin head (largest blob of grey >= 110 just past the outline), else the darkest half of the filtered
  ring; Otsu + adjustment per half; open x2, close x2; black share legs half, black length; reach by grey (pieces of
  grey >= 75 past the outline touching it - bright legs only). `Study_Washer_SAM.STEPS["no_colour"]`: the washer prompts'
  bright points by grey >= 130.
- `Study_NoColour_Legs_UI.py` (new): a copy of `Study_Red_Legs_UI` with the red controls removed, pipeline guided
  (default) / bilateral. The red window is unchanged.
- Run here: `measureNoColour` on two cached DOST pictures (guided and bilateral), the window built under Xvfb.

## 201. Working branch now `main` (2026-11-22)

- Uddi, 2026-11-22: the repository's history moved to `main` (the branch `machine2-io-fe-db-cam` is gone on GitHub; the
  real properties files are kept local, sanitised `*.example.properties` in git - his commits 4e4b8ef, ed25c2b,
  3818fe5). CLAUDE.md §1, §6, §10, README.md §9 and docs/LLD.md now name `main`. Records of where something was traced
  or measured (CODE_GRAPH, vision_positions_machine2, the history above) keep the old branch name.

## 202. No black trial pictures from the vision worker's warm-up (2026-11-27)

- Uddi, 2026-11-27: arbitrary and completely dark pictures in the sub-folders of `C:/Temp/TrialImages`.
- Cause of the dark pictures: `VisionCheckWorker._warmUp` runs `assessPictures` on a black 1280 x 720 picture for the
  hub, nut + washer, bung (component press) and no-bung states, and `assessPictures` saves the first picture of these
  single-picture checks when `save.all.pictures.because.its.in.trial.mode` is on (since 2026-11-17). So every worker
  start - the camera server's start and every restart after a timed-out check - wrote one black picture into
  `HubAndBottomBearing`, `NutAndPlateWasher`, `BungForComponentPress` and `NoBung`.
- Fix: `_warmUp` turns `ImageProcessor.saveTrialPictures` off for the warm-up and puts it back after (also when a
  check raises). Run here (stand-in `cps` with the real save rule): before - 4 black pictures in those 4 folders; after -
  none, saving on again afterwards, left off when it was off.
- Pictures in the wrong step folder: not explained by the saving code - each picture is saved once, in the folder of
  the state of the request it was taken for (`assessPictures` / `_assessCap` / `ImageProcessor.check*`). Being
  checked with `Diag_Trial_Pictures.py` (new, both dry-run folders; read only): all trial pictures in time order across
  folders, dark ones marked, and pictures that show the same scene as a picture in another folder within 2 minutes.

## 203. Study_Cap_Annulus: a simpler cap seating measure (2026-11-27)

- Uddi, 2026-11-27: DOST (silver) caps that are seated are refused (top-face fit 0.57 - 0.64 < 0.64 on 37 of 39 OK
  pictures; the 5 px hub-distance rule on some). The check has become too complex; study a simpler measure first.
- `Study_Cap_Annulus.py` (new, both dry-run folders; production unchanged): steps 1 - 2 of CheckCap (R-B, brightness
  r 25, production helpers round `vision.cap.center`; no brightening step); the hub centre H and radius R of the circle
  through the hole centres from `FindHubHoles.findHoles(check="cap")`; the cap mask by MobileSAM from 24 positive points
  round H (r 30 / 60 / 90 - Uddi's r 33 / 66 / 99 brought in to leave room for a pushed-aside cap - x 0 - 315 deg every
  45), one mask, the part connected to H; the two-stage ellipse (`CapMaskFinder.fitCapEllipse`). Annulus between the cap
  outline and the hole circle; 12 diameters through H at 15, 30, ..., 180 deg (Uddi's list had 175 - taken as 165);
  on each the length in the annulus on both sides, ratio longer / shorter; score = sum of the 12 ratios (12 = centred),
  with the ellipse and with the mask's own outline. The top face is not used.
- Run here on Uddi's 39 OK DOST pictures of 2026-11-26 with the dark-blob hole finder (`--holes morphology`, no YOLO
  weights here) and the MobileSAM weights: all 39 steps 1 - 2 OK, a mask and an ellipse on every picture, ~1 s each on
  CPU; score (ellipse) 12.29 - 16.05, median 13.18; (mask) 12.45 - 15.74. Not meaningful for a cut-off: the dark-blob
  centres of the same part move up to 10 px and R 151 - 156 (the laptop's SAM finder gave r 149.2 +- 0.1 and a centre
  within ~1 px on these pictures). Uddi runs it on the laptop on the OK and NOT OK folders.
- Note: a seated cap's centre is not on the hole circle's centre - `offset.cap.dost` (0.3, -4.7) - so seated caps
  will not score 12 by construction (about 1.2 on the vertical diameter alone).

## 204. Study_Cap_Annulus_UI: the annulus measure in a window (2026-11-27)

- Uddi, 2026-11-27: the study as a window, to see what happens on each picture.
- The measure moved to `_cap_annulus.py` (unchanged), shared by `Study_Cap_Annulus.py` (no window) and the new
  `Study_Cap_Annulus_UI.py` (PySide6 - the sandbox has no tkinter, so the window could be driven here): folder, model,
  seconds between pictures, SAM gamma, Save, Start / Stop; whole picture and a 2x view round the hub centre, outlines
  only; a table row per picture with its check time, click to see it again; every diameter's L+ / L- and ratio in the
  text box; summary and csv at the end. Both dry-run folders.
- Run here: `Study_Cap_Annulus.py` on 5 of the OK pictures after the move - the same scores as before it (12.294,
  12.558, 12.823, 13.751, 13.317 ellipse); the window driven offscreen on the same 5 (dark-blob holes, MobileSAM on CPU):
  5 rows, the same scores, the saved pictures and the csv written, ~1.1 s a picture after the first (model load).

## 205. Study_Cap_Annulus: the cap mask by the production cap mask's steps (2026-11-27)

- Uddi, 2026-11-27: in the window the green mask outline did not follow the cap (the 24-point MobileSAM prompt); the
  earlier code found the cap's mask well - use its logic.
- `_cap_annulus.capMaskByBoxes` (new; default `--mask boxes`, window "Cap mask: YOLO boxes (cap check)"): the steps of
  `CapMaskFinder.findCap` (CHANGES 139 - 142) with its own functions and values - gamma 2.0, disc r 160, the YOLO boxes
  on the disc as MobileSAM box prompts, IoU / stability filters, merging, then outside / small / disc edge / large
  dropped and the largest mask kept - with the disc cut round the hub centre H instead of `vision.cap.center`. findCap
  returns no mask, so its steps are repeated in the study; `CapMaskFinder` is not changed. The 24 points stay as
  `--mask points`. The mask's YOLO / filter counts, predicted IoU and stability are in the text box; `mask_method` in the csv.
- Run here on the 39 OK pictures with the stand-in YOLO boxes of `DryRun_Cap_Hub_Distance` (squares round the disc
  centre; no YOLO weights here) and the dark-blob holes: a mask on every picture, one mask left after merging (area
  ~35000, predicted IoU ~0.96), the green outline on the cap's rim with the ellipse on it (4 pictures looked at, the two
  that were wrong with the points among them); score (ellipse) 12.10 - 14.54, median 12.87; (mask) 12.54 - 14.35, median
  13.02 (points: 12.29 - 16.05 / 12.45 - 15.74). ~3.5 s a picture on CPU. The window driven offscreen on 5 pictures:
  the same scores. Cut-offs still wait for the laptop runs (real YOLO, the SAM hole finder) on OK and NOT OK.

## 206. Study_Cap_Annulus_UI: third panel - every mask in the hole circle (2026-11-27)

- Uddi, 2026-11-27: a third panel with all the masks inside the circle through the holes, coloured, the largest painted
  first, then the next, and so on.
- `_cap_annulus.capMaskByBoxes` keeps every mask MobileSAM gave on the disc (after its predicted IoU / stability filters
  and box merging - the masks the cap mask is chosen from); `masksInCircle` keeps those with at least half their area
  inside the hole circle, cut to it, largest first; `masksPicture` paints them in distinct colours, largest first and
  each smaller one over it, blended 60 / 40 with the picture, a thin dark edge round each, the hole circle in blue - no
  values. Points method: its one mask. The window's views are now 640 x 360 + two 360 x 360 (2x round the hub centre:
  outlines; masks). The count and areas (largest first) in the text box, `masks_in_circle` in the csv; `--save` also
  writes `<name>_masks.jpg` (window and no-window script).
- Run here: the window offscreen on 5 OK pictures with richer stand-in YOLO boxes (squares round the disc centre and a
  6 x 6 grid of small boxes, so that MobileSAM gives several masks; the real YOLO is on the laptop): 10 - 13 masks in
  the circle per picture (largest ~72,000 px = the hub face round the cap, then the cap ~35,000, the top face ~13,500,
  small pieces), the panel drawn as asked; the cap mask chosen as before.

## 207. Study_Cap_Annulus: YOLO confidence, MobileSAM minimums and merging as controls (2026-11-27)

- Uddi, 2026-11-27: on the laptop only 2 or 6 masks per picture in the third panel (2 large, 4 small in the holes);
  lower the confidence, as a value he can change.
- Where masks are lost in the boxes method (CapMaskFinder's steps): YOLO boxes below `vision.cap.mask.yolo.confidence`
  (0.15); boxes not centred on the disc; masks below MobileSAM's predicted IoU (0.50) or stability (0.70); masks merged
  when their boxes overlap above 0.50 (one per object kept); then the panel's own rule (half the area in the circle).
- `_cap_annulus.setMaskParameters` (study process only - the properties and production are not touched): sets
  CapMaskFinder's YOLO_CONFIDENCE / STABILITY_MIN / IOU_MIN / MERGE_IOU, which it reads at each call. Window: a second
  row - YOLO confidence, Stability >=, Predicted IoU >=, Merge above overlap (1.0 = no merging), In-circle share - set at
  Start, the properties values at start; the table's new boxes/disc/pass/merged column shows which stage cuts the
  masks; the values used in the text box and the csv. No-window script: `--conf --stability --iou --merge --share`;
  window `--conf` for the start value. These values change the cap mask too (it is the largest mask left).
- Run here (window offscreen, 5 OK pictures, the richer stand-in boxes of 206 - the YOLO confidence cannot be tried here,
  the stand-in has no scores): merge 0.5 / stability 0.7: 10 - 13 masks in the circle; merge 1.0 / stability 0.5: 21 - 25.
  With merging off the largest mask on picture 1 was the cap plus pieces of the hub (43,262 px against ~35,000), and its
  mask score rose from 12.8 to 15.1 - looser values can spoil the cap mask the score is computed on.

## 208. Study_Cap_Annulus: YOLO NMS / max det, MobileSAM 3 masks per box, over-detection preset (2026-11-27)

- Uddi, 2026-11-27 09:09: MobileSAM and YOLOv8 settings to over-detect - pred_iou_thresh 0.70 - 0.75, stability_score_thresh
  0.75 - 0.80, box_nms_thresh 0.85 - 0.90, crop_n_layers 1 - 2, crop_nms_thresh 0.85, min_mask_region_area 0 - 10; YOLO
  conf 0.10, iou 0.80, max_det 1000; MobileSAM multimask_output=True per box.
- How they map onto the box-prompt pipeline (YOLO boxes -> SamPredictor, not SamAutomaticMaskGenerator):
  pred_iou / stability -> the predicted IoU / stability minimums (the cap mask's are already lower, 0.50 / 0.70: 0.72 /
  0.78 keep fewer masks than production, not more); box_nms -> the merge overlap (0.50 -> 0.88); YOLO conf / iou /
  max_det as given (production: 0.15 / 0.7 hard-coded / 300 default); multimask: 3 masks per box. crop_n_layers,
  crop_nms_thresh and min_mask_region_area belong to the automatic grid generator, which the cap path does not use
  (no crops; no minimum region area before the panel - area.min 15,000 applies only to choosing the cap mask).
- `_cap_annulus.yoloSamMasksTunable` (new): CapMaskFinder.yoloSamMasks' steps with every setting variable (YOLO conf,
  NMS IoU, max_det; MobileSAM multimask - the B x 3 masks flattened into one list; IoU / stability minimums; merge,
  skipped at 1.0). At the defaults (production's values) it is production's steps. `CapMaskFinder` is not changed.
  Window: YOLO NMS IoU, max det, "3 masks per box", and "Production values" / "Over-detect" buttons; the stage column is
  now boxes/disc/sam/pass/merged. No-window script: `--yolo-iou --max-det --multimask --overdetect`.
- Run here (5 OK pictures; stand-in boxes, so conf / NMS / max det have no effect here): defaults - the same scores
  as before (12.652, 12.404, 12.561, 14.313, 14.536); over-detect with the richer stand-in boxes: 111 MobileSAM masks (3 x
  37), 46 - 48 past the filters, 30 - 32 after merging, 21 - 23 in the circle (production values: 37 / 15 - 20 / 15 - 19 /
  10 - 13); the cap mask still on the rim (score ellipse 12.96 - 13.85).

## 209. Study_Cap_Annulus: "Annulus only" mask, two gammas, controls in rows (2026-11-27)

- Uddi, 2026-11-27 09:47: (a) a choice that gets a mask of ONLY the annulus - prompts along a contour 5 px inside the
  hole circle, avoiding the holes; (b) two gammas - for the picture used to find the holes and for the picture used for
  the masks; the in-circle share control removed; YOLO controls on one line, MobileSAM controls on another, named
  pred_iou_thresh / stability_score_thresh; box_nms_thresh / crop_nms_thresh if they exist.
- `_cap_annulus.annulusMask` (method "annulus"): positive prompts every 5 deg on the circle 5 px inside the hole circle,
  none within 28 px of a hole centre (`FindHubHoles` result `points`); MobileSAM, one mask; annulus = that mask inside the
  hole circle; cap = the region inside the hole circle not in the annulus and connected to H (outline = the annulus's
  inner edge), then the same ellipse and scores. With one negative prompt at H MobileSAM took hub and cap as one mask
  (99 % of the area within r 90 of H covered, 3 OK pictures); with 17 negatives on the cap (H, r 30 / 60 x every 45 deg)
  it covered 0 % of it and 94 - 96 % of the ring (tried here: also r 0/30/60/90 - the same; 3 masks with one negative -
  two of them cover the cap). The dark gap between the cap's rim and the hub face falls on the cap side (seen at the
  bottom of the cap on these pictures).
- Gammas: `measure(..., holesGamma, maskGamma)` - holes: the picture given to `FindHubHoles.findHoles` (1 = as taken; it
  still tries its own gammas); masks: every method (boxes until now took CapMaskFinder.GAMMA 2.0 - still the default;
  the points method's default moves from 1.0 to 2.0). No-window script `--holes-gamma --mask-gamma` (`--gamma` and
  `--share` gone); the third panel keeps masks with at least half their area in the circle (fixed).
- Window rows: 1 folder / model / cap mask (YOLO boxes, 24 points, Annulus only) / seconds / Save / Start / Stop;
  2 YOLO conf, iou, max_det; 3 MobileSAM multimask_output, pred_iou_thresh, stability_score_thresh, box_nms_thresh
  (= the merge overlap), Production values / Over-detect; 4 gamma for the holes / for the masks. There is no
  crop_nms_thresh: no crop layers in this pipeline (one disc round the hub centre). Negative prompts drawn red.
- Run here (5 OK pictures, dark-blob holes, stand-in boxes): boxes at the defaults - the same scores as before (12.652,
  12.404, 12.561, 14.313, 14.536); annulus - annulus ~37,700 px, cap ~36,200 px, score (ellipse) 12.24 - 13.54, (mask)
  12.52 - 13.73, ~1 s a picture on CPU; the window driven offscreen with "Annulus only": 5 rows, the same scores.

## 210. Study_Cap_Annulus: annulus only drawn, Uddi's values as defaults, inner ellipse by most points, ANNULUS lines (2026-11-27)

- Uddi, 2026-11-27 11:00 (window on his Not OK folder): with Annulus chosen the other masks were still drawn; make the
  values in his window the defaults; fit the annulus's inner perimeter as an ellipse with the most inner points of the
  mask on it (not least squares); print the annulus details per picture on the terminal to send back.
- Annulus only: the cap region is no longer drawn (green outline, third-panel fill) or scored - only the annulus (its
  edges magenta, third panel the annulus alone) and its inner ellipse (yellow).
- Defaults (window and no-window script): method annulus, 1 s, conf 0.05, iou 0.95, max_det 300, multimask on,
  pred_iou_thresh 0.15, stability_score_thresh 0.25, box_nms_thresh 0.90, gamma holes 1.0 / masks 2.0 (`MASK_STUDY`;
  window "Study defaults"; script `--production` for the cap check's values).
- Inner ellipse (`maxInliersEllipse`): the annulus pixels touching the region it encloses are the inner edge points
  (pieces of the mask inside the cap give points too - outliers); candidates the least-squares ellipse of all and 1,500
  ellipses through 5 random points (fixed seed), the one with the most points within 2 px; then a local search on centre,
  axes and angle (1 px / 2 deg steps halved to 0.05 px / 0.1 deg) taking any move that puts more points on it (ties: the
  smaller mean distance). Scores: "ellipse" on it; "mask" now on the ring's inner edge along each ray (walking in from the
  hole circle, gaps within 45 px of it - the holes - passed over, then the first pixel off the mask; first version stopped
  at the ring's thin edge round a hole: widths of 3 px and ring scores of 26 - 46 on OK pictures - fixed).
- `ANNULUS file=... key=value ...` per picture on the terminal (window and script): R-B, brightness, hub, R, prompts,
  SAM score, annulus area, inner edge points, inliers / share / mean distance, ellipse centre / axes / angle / axis ratio,
  ellipse - hub dx / dy / distance, mask pieces well inside the ellipse (> 3 px), ring width min / max / mean / sd and every
  10 deg, both scores and largest ratios, time; the same in the csv.
- Run here (39 OK pictures, dark-blob holes): inliers 65 - 79 % of 909 - 920 points, mean distance ~0.95 px; ellipse - hub
  0.6 - 6.3 px; ring width 35 - 57 px, sd 1.8 - 4.9; score ellipse 12.22 - 14.54, ring 12.43 - 14.46 (the two agree within
  ~0.3). Uddi's Not OK run of 11:00 (before this change): score ellipse 12.67 - 27.40, median 16.58. The window driven
  offscreen at the new defaults: annulus only drawn, ANNULUS lines on the terminal.

## 211. Study_Cap_Annulus: Uddi's verdict rule, VERDICT lines, OK / NOT OK on the third panel, beep (2026-11-27)

- Uddi, 2026-11-27 11:29 / 11:34: the rule - if centre offset > 7.0 or score_ellipse > 16.0 or islands > 230 or
  w_opp_max > 17 or ring width p90 > 50 or width_max > 52 then NOT OK; else if inlier_share > 0.70 or width_sd < 3.5 or
  islands < 200 or w_opp_max < 11 or p90 < 47 or width_max < 48.25 then OK; else NOT OK. Add it; print the conditions
  that led to the verdict; write OK in green / NOT OK in red on the third panel; a sound for red.
- Checked on his csv files of 2026-11-27 11:09 / 11:11 (39 OK, 33 NOT OK pictures of 26 Nov, mask gamma 2.0): 0 errors -
  every NOT OK picture in stage 1, every OK picture in stage 2 (3 - 6 of the stage-2 conditions met); none of the NOT OK
  pictures meets a stage-2 condition. Thin spots: part 18-26-29 (3 pictures) is caught by islands > 230 alone (378 -
  423; width_max 50.5 - 50.8 < 52, p90 48.5 - 49 < 50, w_opp_max 14 < 17); one picture of 18-25-59 has islands 220 (caught
  by offset and widths). The two sets were taken in different sessions (hub y 344.8 / 341.9, brightness 164 - 189 /
  158 - 230), 13 parts each.
- `_cap_annulus`: `width_p90` and `w_opp_max` / `w_opp_mean` (|width(t) - width(t + 180)| over the 18 pairs) added to the
  annulus values (csv, ANNULUS line); `RULE_NOT_OK`, `RULE_OK`, `verdict(res)` -> (OK / NOT OK, "stage n: conditions
  met with their values"); a picture without annulus values (no holes / annulus / ellipse) is NOT OK with the reason.
  Window: a verdict column (green / red), the verdict written on the third panel (Uddi's request - the one text on a
  picture in the study window; values stay in the table and text box), a beep for NOT OK (winsound on Windows, else
  Qt's), `VERDICT file: ...` on the terminal (also the no-window script), verdict counts in the summary.
- Run here: `verdict` on the rows of the two csv files - 0 errors (39 stage 2 / 33 stage 1); the window offscreen on 5 OK
  pictures - 3 OK, 2 NOT OK (width_max 56 / 56.5: the sandbox's dark-blob hole circle on those two is 4 px off, R 156
  against 149 on the laptop - not the rule).

## 212. Silver (DOST) cap seating by the annulus in production; islands limit 450; AND rule recorded (2026-11-27)

What changed (Uddi, 2026-11-27):
- **Rule:** the islands NOT OK limit of the study's verdict goes from 230 to 450, all else as in CHANGES 211. On Uddi's 116
  distinct pictures (13, 17 and 26 Nov; 71 OK, 45 NOT OK, 2 of them without annulus values) this gives 0 errors:
  192.168.250.64_01_20261113152414604 (islands 351, otherwise clean) becomes OK; part 18-26-29 now reaches stage 2,
  meets none of its six conditions and is NOT OK at stage 3.
- **Production:** new `camera/CapAnnulus.py` holds the method of the study (CHANGES 209 - 211): the prompts, the annulus
  mask, the inner ellipse with the most edge points on it, ring widths, islands, diameter score, and the verdict.
  `CheckCap.checkSeating` sends a silver cap (DOST) to `CheckCap.silverSeatingByAnnulus` when
  `vision.cap.seating.silver.method=annulus` (the default). That function finds the hub's holes in the cap picture
  (`FindHubHoles.findHoles`, check cap; if they are not found, the cap is NOT seated), then calls CapAnnulus. The log
  line gives the stage, the conditions met and every value. The marked picture shows the hole circle and the annulus
  edges in thin blue, the inner ellipse in green or red, and the hub-centre cross; it has no text. `colour` keeps the
  earlier silver rules (CHANGES 129, 143, 152). Brass is unchanged.
- **One source:** `_cap_annulus.py` (used by `Study_Cap_Annulus.py` and `Study_Cap_Annulus_UI.py`) now calls CapAnnulus
  for the prompts, the mask, the edge points, the ellipse, the widths and the rule limits, so the study and the cap check
  measure alike. The study compares the values as printed (rounded); production compares them unrounded.
- **AND alternative, recorded but not used:** it is in the docstring of CapAnnulus, the header of
  `Study_Cap_Annulus_UI.py` and `_cap_annulus.RULE_AND_OK` / `verdictAnd`. The study prints it as an `AND file: ...`
  line. The rule: OK if offset < 6.5, score < 15.0, w_opp_max < 13.8, width p90 < 47.7 and width_max < 49.6 (optionally
  also islands < 1000 and width_min > 25); else NOT OK. It also gives 0 errors on the same pictures. Worst OK / closest
  NOT OK:

  | Value | Worst OK | Closest NOT OK |
  |---|---|---|
  | offset | 5.32 | 8.46 |
  | score | 14.27 | 15.50 |
  | w_opp_max | 13.5 | 14.0 |
  | p90 | 46.9 | 48.4 |
  | width_max | 48.8 | 50.5 |

  Every NOT OK picture fails at least 2 of the 5 conditions.
- **Keys:** `<vendor>.vision.cap.seating.silver.method=annulus` and 22 keys `<vendor>.vision.cap.annulus.*` (prompt.inset,
  prompt.step.deg, hole.clearance, negative.radii, mask.gamma, inner.tolerance, inner.tries, width.step.deg, hole.zone,
  score.floor, notok.{offset, score, islands, opposite, width.p90, width.max} and ok.{inlier.share, width.sd, islands,
  opposite, width.p90, width.max}). They are in the three example properties files, with the same values for both
  machines.
- **Reference commit:** 2d5e7d0 (values hard-coded). `baseline_per_file` of CheckCap.py and CapAnnulus.py points at it
  in both manifests.
- **Dry runs:** `DryRun_Cap_Silver_Seating.py` now sets the method to `colour`, the rules it checks.

Found (sandbox): **a known miss.** `cap_images/DOST_skewed_03.jpg` is an old staged picture of a tilted silver cap: the
top face is shifted up-left and the side wall shows at the bottom right, but the rim stays round and centred in the bore.
Both rules pass it: offset 1.36, score 12.47, islands 89, width_sd 3.09, w_opp_max 12.2, p90 47.5, width_max 49.0. The
annulus sees the rim, not the face. The colour method's face rule caught it. None of Uddi's 45 NOT OK pictures is like
it.

Run here (sandbox: dark-blob hole finder, `--holes morphology`; no YOLO weights; MobileSAM from the clone):
- `Verify_Vision_Config_Equivalence.py`, both machines: ALL EQUIVALENT (CapAnnulus.py and CheckCap.py against 2d5e7d0).
- The study on 5 OK pictures: the ANNULUS and VERDICT lines before and after its switch to CapAnnulus are identical.
  `CheckCap.checkSeating(DOST)` on the same pictures gives the same verdicts and values; the marked picture is drawn.
- `DryRun_Cap_Check.py --holes morphology`: 4 unexpected results. Three are DOSTPLUS caps 03, 04 and 08; they fail the
  same way at 7275b39, before this change (stand-in YOLO boxes in the sandbox). The fourth is DOST_skewed_03, now OK:
  the miss above.
- `DryRun_Cap_Production_Path.py --holes morphology`: 11 / 12. P2 DOST fails on DOST_skewed_03.
- `DryRun_Cap_Silver_Seating.py` (colour): 5 / 6, the same as at 7275b39. S1 fails in the sandbox because there is no
  YOLO for the hole finder.

## 213. Vision key names: machine first, then the model - one name per value (2026-11-27)

What changed (Uddi, 2026-11-27: "no ambiguities should remain"):
- **Naming rule:** every vision key now reads `vision.<vendor>.<check>...` when both models use the value, and
  `vision.<vendor>.<dost|dostplus>.<check>...` when it belongs to one model. `vendor` is `siemens` (Machine 2) or
  `allenbradley` (Machine 1). Before, the keys were `<vendor>.vision.<check>...`, with the model at the end
  (`cap.radius.dost`), in the middle (`frame.nominal.pins.dost.lhs`), or only implied.
  - The model was implied by the cap's colour for 14 keys (`cap.seating.silver.*`, `cap.seating.brass.*`,
    `cap.silver.brightness.*`; silver = DOST, brass = DOSTPLUS). These now carry the model.
  - It was implied by the only method that reads them for 31 more keys. Checked in CheckCap / CapAnnulus:
    - `cap.seating.bore.*`: the brass method → `dostplus`;
    - `cap.seating.hub.ring/box/radius/centre.inset/dark/visible` and `cap.seating.face.box.scale`: the silver hub and
      face rules → `dost`;
    - `cap.annulus.*` → `dost`.
  - Keys read by both methods stay without a model: `cap.seating.circle.fit.tolerance`, `cap.polar.*`, `cap.mask.*`,
    `cap.seating.hub.distance/source/holes.*`, `cap.center`, `cap.seating.check`.
  - 120 of the 461 keys per machine changed beyond the vendor move; there are no collisions. The full table is in
    `docs/vision_key_renames_213.md`.
- **One rule, in `utils/VisionKeyNames.py`:**
  - `newCodeKey` is the rule; `propertyKey(codeKey, vendor)` inserts the vendor after `vision.`.
  - `renameInText` renames keys in properties, code and docs, including the `<vendor>.vision.x` placeholders.
  - `convertPropertiesFile` / `python utils/VisionKeyNames.py <file>` converts a properties file in place and keeps
    the original as `<file>.before213`.
- **Code:** keys are written without the vendor (`vision.dost.cap.radius`); the code text minus the vendor is the
  properties text.
  - `CosThetaConfigurator.visionPropertyKey` builds the property name, used by `getVisionValue` and the per-machine
    camera / image keys.
  - VisionConfig's messages, `describeVisionParameters` and the live-reload filter use `propertyName()`.
  - The camera server's live check of the camera connection keys uses `vision.<vendor>.camera.`.
  - VisionFrame's centre and oval names now carry the model first (`dost.bung.seating.seated.centre`,
    `dost.bung.seating.reference.oval`).
  - 18 code files were renamed by the rule; the `<vendor>.vision.x` placeholders in docstrings and comments were rewritten in 38 files.
- **Old names are not read** (zero ambiguity). A file that still has them is reported once:
  `[Configuration] ... has N old-style vision key(s) ... convert the file with 'python utils/VisionKeyNames.py <file>'`.
  Each value then falls back to its code default (the Machine 1 value) and is reported as missing.
- **Properties:** the three example files were converted; each has 922 vision keys (461 × 2) and no old names left.
  Machine1 = Machine2 apart from `plc.vendor`. The header now explains the scheme.
- **Verifier** (both copies):
  - The properties keys are `vision.<vendor>.<rest>`.
  - It reports old-style keys, keys without a machine, and an old-style name in the code (`newCodeKey(key) != key`).
  - The rule is applied to the baseline's strings before the AST comparison (`_Normalise(renameKeys=True)`, also for
    VisionFrame's names without `vision.`), so a renamed key is not a logic change.
  - The manifests' `extracted` keys were renamed (2 each).
- **Docs:** CLAUDE.md §4 (the naming rule), the LLD §10 note, the HLD, the plant-pc and vision-positions docs, both
  READMEs and the code graph were renamed by the same rule. Shorthand such as `seating.brass.offset.max` and the
  `.<model>` placeholders were expanded to full new names. The CHANGES history keeps the old names.

Found and fixed in the dry run: **a start-up deadlock with an old-style file.** The first version of the report logged
from inside the configuration load. `getInstance()` holds its singleton lock (and `_loadConfig` its `_reloadLock`) while
the file loads, and the logger import calls `getInstance()` again. The program hung after the first line (6.7 min wall
time, 0.2 s CPU). Now `_oldVisionKeys` only collects under the locks; `_reportOldVisionKeys` runs after the reload lock
is released, and during the first `getInstance()` the report is held back until its lock is released. Result: 0.28 s,
one old-keys line, then the missing keys.

Run here (sandbox):
- `Verify_Vision_Config_Equivalence.py`, both machines: ALL EQUIVALENT, 434 parameters. Deliberate faults were caught:
  an old name in CheckCap (4 problems) and a wrong model in a renamed key (AST difference plus missing keys).
- All 19 vision modules imported: 434 keys read, every one from the properties file. CheckCap values as tuned
  (`dost.cap.seating.hub.distance.offset.cap` 0.3,-4.7; method annulus); camera IP read.
- The pre-213 Machine 2 file run through `utils/VisionKeyNames.py`: 922 keys renamed, identical (comments aside) to the
  converted repo file.
- `DryRun_Logging_And_Live_Config.py` 37/37 (live vision-key reload); `DryRun_Knuckle_Frame.py --quick` 88/88
  (VisionFrame's centres and ovals under the new names); `DryRun_CameraServer_Heartbeat.py` 4/4;
  `DryRun_Camera_State_File.py` 19/19; `DryRun_Tuning_Keys.py` 251/251.
- `DryRun_Cap_Production_Path.py --holes morphology`: 11/12, as before (DOST_skewed_03, CHANGES 212).

## 214. Vision check time targets recorded; production does not import the test package (2026-11-27)

What changed (Uddi, 2026-11-27):
- **Time targets:** a vision check should take 1.5 s or less (ideal) and 2 s at most on average.
  `vision.<vendor>.check.timeout.sec` (12 s) is only the deadline after which the worker is stopped and the check
  counted NOT OK; it is not a target. In CHANGES 212 I reported the DOST cap check's 2.06 - 2.34 s as "well inside the
  12 s limit", which was wrong against these targets. Uddi accepts that time for now.
  - Recorded in CLAUDE.md §5 and §7, the LLD (VisionCheckWorker), `docs/plant-pc-verification.md` (step 2 rewritten:
    the targets, not 80 % of the timeout; the bung, cap and hub-holes time notes) and both dry-run READMEs.
  - `PlantPC_Vision_Timing.py` (both copies) now prints each check's mean, median and largest time and marks it as
    ideal (largest <= 1.5 s), within average (mean <= 2 s) or OVER. The margin to the timeout is shown for
    information only. Exit code 1 if a check's mean is over 2 s (before: largest over 80 % of 12 s). It also now prints
    the timeout's key under its CHANGES 213 name.
- **Import boundary:** no production file imports from the test package.
  - Checked: 173 production files (git, outside `test/` and `archive/`); every import, including those inside
    functions, `importlib.import_module`, `runpy`, `__import__` and `sys.path` entries. Found: none; the only mentions
    of test files are three docstrings naming a dry run.
  - To keep it so: new `test/machine*_dryrun/DryRun_Import_Boundaries.py` FAILs when a production file reaches a module
    under `test/`.

Run here: `DryRun_Import_Boundaries.py` PASS on both copies (173 production files, 212 test files); with a planted
`from test.machine2_dryrun import _plc_sim` in `utils/ShutdownWait.py`: 1 violation found (then reverted).
`PlantPC_Vision_Timing.py` compiled only: it needs the YOLO weights and the GPU, so it was not run here.
