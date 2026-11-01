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
