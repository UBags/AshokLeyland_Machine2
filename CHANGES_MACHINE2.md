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
