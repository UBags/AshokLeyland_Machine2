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
