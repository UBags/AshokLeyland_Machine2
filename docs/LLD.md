# Machine 2 (Hub and Disc Assembly): Low-Level Design

| | |
|---|---|
| Document | Low-level design (LLD) |
| Read with | `HLD.md` (overview), `PLC_Interface_Spec.md` (PLC tags and timings) |
| Code at | Branch `machine2-io-fe-db-cam` |

---

## 1. MainProgram (`MainProgram.py`)

`main()` runs these steps in order:

1. Parse mode, user and role.
2. Connect to Redis, run `clearQueues`, then run `checkRedisHealth`. If the check fails, print the reason and `sys.exit(1)`.
3. Start P1 (the logging server).
4. Start P2 camera, P3 QR, P4 IO, P6 FE, P7 HB and P5 DB, each through `startProcess(target, args)`. Every process first calls `setLogProcess(<name>)`, which sets the per-process log levels, crash capture and live configuration.
5. Start `ProcessSupervisor` (`utils/ProcessSupervisor.py`).
6. Wait for `stopcommandQ`, then send the stop and collect each `stoppedresponseQ`. `monitorStop` then backs up the database (`doBackup`; before CHANGES §84 this never ran, because `stopProcesses` ended the thread with `sys.exit`).

**ProcessSupervisor.** Every `supervisor.check.interval.sec` (2 s), `checkOnce()` restarts each registered process that has died. This is enabled by `supervisor.restart.dead.processes`. Restarts are limited to `supervisor.max.restarts.per.hour` (10) for each process; above that the process is left down and logged. Supervision stops once `isStopping()` is true.

## 2. IOServer (`costhetaio/commonplc/`)

### 2.1 Modules

| File | Content |
|---|---|
| `PLCDataIO.py` | Abstract tag I/O (read/write of bool, int, float, lists of tags) |
| `PLCDataIOFactory.py` | Picks the Siemens or Allen-Bradley implementation from `plc.vendor` |
| `MockPLC.py`, `MockPLCDataIO.py` | In-process PLC used in TEST mode |
| `PLCIOServer.py` | `PLCIOServer`, `UpdateTagsToDefaultProcessor` and `DelayedJobScheduler` |
| `../siemens/IOServerProperties.yaml` | Siemens connection and tag addresses |

### 2.2 Threads in `PLCIOServer`

| Thread | Method | Period | Job |
|---|---|---|---|
| Read loop | `launchReadLoopThread` | `plc.read.interval` 0.2 s | Reads the tags of the current read state (`getCurrentTagsToBeRead`) and calls `process_current_read_state` |
| Write loop | `launchWriteTagsLoopThread` | `plc.write.interval` 0.1 s | Write states: waits for the camera or QR answer (20 s timeout) and runs `perform_write_state_action` |
| Connection | `launchConnectionMonitoringThread` | 1 s | `_connectionCheckStep`; reconnects; checks WatchTag |
| PC heartbeat | `launchHeartbeatThread` | 0.5 s | Sends `io2hb.q`: DEAD while the PLC is not connected, or the read or write loop is not running or has not gone round for `heartbeat.work.loop.max.silence.sec` (60 s) |
| Connection status toggle | started by `launchThreadForUpdatingCombinedHeartbeat` | 2 s | Toggles `PC_PLC_ConnectionStatusTag` (own thread since CHANGES §84, so an abort on the status thread does not stop it) |
| Server statuses | `launchThreadForUpdatingCombinedHeartbeat` | on message | Reads `hb2io.q`, then `_applyServerStatuses` |
| Emergency | `launchEmergencyMonitoringThread` | 1 s | `PLC_PC_EmergencyAbort`, then `abortCycle` |
| FE abort | `launchFrontendAbortListenerThread` | blocking read | `fe2io.q`: `Abort` -> `abortCycle`; `FrontendAbortRequest` -> `handleFrontendAbortRequest` (writes `PC_PLC_CycleAborted` = True, 3 attempts; aborts only if written, else logs) |
| Stop | `launchMonitorStopCommandThread` | blocking read | Orderly stop |
| Tag defaults | `UpdateTagsToDefaultProcessor` | queue | Default writes requested from other threads |
| Delayed jobs | `DelayedJobScheduler` | timers | Resets 0.4 s after a read (Set/Done tags) and the end of handshakes |

The state is held in `MachineState` (`statemachine/StateMachine.py`). A change of state goes through `_onStateChange`, which audits it, records cycle times and sets `log:cycleContext`.

### 2.3 State table

The trigger in each state is the tag read in that state; the write states write the OK/Done of that check.

| # | State | Tag read | On the event |
|---|---|---|---|
| 1 | READ_QR_CODE | CheckQRCode | Arm the QR server (`io2qrcode.q`) → 2 |
| 2 | WRITE_QR_CODE | (QR answer) | `writeCycleSettingsToPLC`, then the QR handshake. OK → 3; NOT OK → 1 |
| 3/4 | Knuckle | CheckKnuckle | Picture request → result handshake. OK → 5; NOT OK → 3 |
| 5/6 | Hub and bottom bearing | CheckHub | Same. OK → 7 |
| 7/8 | Top bearing | CheckBearing | Same. OK → 9 |
| 9/10 | Nut and plate washer | CheckNutAndPlateWasher | Same. OK → 11 |
| 11 | READ_TIGHTENING_TORQUE_1_DONE | Station2TorqueValueSet | → 12 |
| 12 | READ_TIGHTENING_TORQUE_1 | Station2TorqueValue (float) | Value to FE; Set is reset 0.4 s later → 13 |
| 13/14 | Bunk for component press | CheckBunkForComponentPress | Handshake. OK → 15 |
| 15 | READ_COMPONENT_PRESS_DONE | ComponentPressDone | To FE; reset after 0.4 s → 16 |
| 16/17 | No bunk | CheckNoBunk | Handshake. OK → 18 |
| 18 | READ_TIGHTENING_TORQUE_2_DONE | Station2TorqueValueSet | → 19 |
| 19 | READ_TIGHTENING_TORQUE_2 | Station2TorqueValue | To FE; reset → 20 |
| 20/21 | Split pin and washer | CheckSplitPinAndWasher | Handshake. OK → 22 |
| 22/23 | Cap | CheckCap | Handshake. OK → 24 |
| 24/25 | Bunk for cap press | CheckBunkForCapPress | Handshake. OK → 26 |
| 26 | READ_CAP_PRESS_DONE | Station4CapPressDone | To FE; reset → 27 |
| 27 | READ_FREE_ROTATION_TORQUE_1_DONE | Station3TorqueValueSet | → 28 |
| 28 | READ_FREE_ROTATION_TORQUE_1 | Station3TorqueValue | To FE with moveTo = 1; `resetForNextCycle("cycle completed")` → 1 |

### 2.4 Key methods

**`writeCycleSettingsToPLC(qr)`**

1. Reads the side and model from the QR code, and that model's latest `machine_settings` row.
2. Writes each value with `_writeVerified`, then `_settingReadBack`.
3. Stores `cycleSettings:<QR>` for the FE and DB.
4. Returns False only if the side could not be written.

**`perform_write_state_action(tags, result)`**

Writes the v1 handshake of spec §5.1 (steps 1–8). Each write is retried by `_writeBoolUntilWritten`, and the sequence stops if the state changes because of an abort. Each attempt checks the state and writes while holding `_abortLock`, which `abortCycle` holds for the whole abort, so a write of the old cycle cannot land after the abort's reset (CHANGES §84). The lock is released between attempts. The caller changes state only after it returns True.

**Picture handling**

- `_requestPicture(state)` creates the request id `<pid>.<cycle>-<n>` (the IOServer's pid, so a restarted IOServer's ids never match the old one's), sets `camera:activeRequest` and sends `io2camera.q`.
- `_dropCameraRequest()` clears the request.
- `_pictureNotOkWithoutAnswer(state, reason)` gives NOT OK after the 20 s timeout, or when the camera is down. The timeout is at least `<vendor>.vision.check.timeout.sec` + 8 s (`getMinimumPlcCameraAnswerTimeout`); a smaller value is raised.

**Trigger after a NOT OK**

`triggerIgnoredUntilReset`, `_setAwaitingTriggerReset` and `_noteTriggerStillHeld` are kept but no longer called (CHANGES §80). After a NOT OK, the read state acts on the trigger as soon as it reads True.

**PLC restart after a reconnect**

If no PLC-raised tag is known to be True, the PC compares `PC_PLC_LH_RH_Selection` with the side it wrote in state 2. The same value means no restart. Any other value (0 after a restart) means the PLC restarted: abort and reset.

**Delayed resets**

- `scheduleTagReset` and `_scheduleStation2TorqueReset` reset tags after they are read: torque values after `plc.handshake.torquevalues.reset.delay.sec` (2.0 s), press Done after `plc.handshake.componentpress.reset.delay.sec` (1.5 s).
- `completePendingResultReset` runs a pending reset at once, before the next handshake of the same check.
- `cancelVisionResultReset` cancels one.

**`abortCycle(reason, …)`**

1. Cancels delayed jobs and the camera request.
2. Resets the per-cycle tags (`perCycleTags`, `resetAll`).
3. Arms CheckQRCode.
4. Tells the QR server, FE and camera.
5. Pulses the cycle-aborted tag, if one is configured.
6. Goes to state 1.

**`resetForNextCycle(reason, keepPendingResets)`**

Resets at the end of a cycle. After a completed cycle, the pending resets (for example the free-rotation Set) are left to run at their time.

**`_applyServerStatuses()`**

Applies the rules of spec §5.7 to the camera, qrcode, db, frontend, heartbeat and redis statuses:

- It keeps `_dbDown`, `_redisDown`, and so on.
- `newCycleHoldReason()` returns the first reason that holds the next cycle.
- On a change, it logs and audits, and sets `feNotice:*` through `_notice`.

**Audit**

`_audit`, `_auditWrite`, `_auditRead` and `_auditStateChange` write one line for every PLC write, every PLC read that changes something, and every state change, for later analysis.

## 3. Camera server (`camera/`)

**Thread chain** (`GenericQueueProcessor` threads):

```
MonitorGetPicQueue ─▶ TakePictures ─▶ ConverterScalerAndProcessor ─▶ ImageProcessor
```

1. `MonitorGetPicQueue` reads `io2camera.q`. It ignores any request whose id is not `camera:activeRequest`.
2. `TakePictures` takes three pictures with `RTSPCam.getFrameAfter`: each a frame that arrived after the request and after the previous picture, waiting at most `camera.frame.max.wait.sec` (1 s). None in time (stalled stream) or camera not connected: the answer carries `noPictureReason` (CHANGES §84).
3. `ConverterScalerAndProcessor` normalises the image and sends it to the vision check. With a `noPictureReason`, or no picture, the check is not run: NOT OK with the reason.
4. The answer goes to `camera2io.q` with the request id and to `camera2fe.q` with both images and the reason.

**VisionCheckWorker** (`VisionCheckWorker.py`):

- A child process that loads the models once (`ModelManager`, MobileSAM, segmenters) and runs one check per request.
- If a check does not finish in 12 s, the parent kills the child and starts a new one. The check counts as NOT OK. From the 3rd timeout in a row the reason says the checks are too slow on this PC (check the GPU); a check in time resets the count.
- The worker warms up at start.

**Heartbeat** (`TakePictures.launchThreadForHeartbeat`): DEAD while the camera is not connected, or one of the three work threads (`cameraWorkers`, `utils/WorkerHealth.py`) is not running or has not gone round its loop for `heartbeat.work.loop.max.silence.sec`.

**Checks.** There is one module per check: `CheckKnuckle`, `CheckHubAndBottomBearing`, `CheckTopBearing`, `CheckNutAndPlateWasher`, `CheckNoCapBunk` (bunk for component press), `CheckNoBunk`, `CheckSplitPinAndWasher`, `CheckCap` and `CheckBunk` (bunk for cap press). The thresholds are in `VisionConfig.py`.

## 4. QR code server (`costhetaio/qrcodescanner/QRCodeScanningServer.py`)

**Thread chain:**

```
MonitorGetQRCodeQueue (io2qrcode.q) ─▶ GetAndValidateQRCode ─▶ QRCodeDispatcher
```

- `evaluateQRCode` checks the format and the side.
- `QRCodeDispatcher` sends to `qrcode2io.q`, `qrcode2fe.q` and `qrcode2camera.q`.
- The `lookForAbort` thread listens on `io2qrcodeabort.q` and calls `abortQRCodeRead`.
- The scanner keeps only the newest message. This is intended.
- The database connection uses `setDatabaseName(createDB=False)`.

## 5. Frontend (`frontend/AshokLeylandFrontEnd.py`)

`AshokLeylandFrontEnd(QMainWindow)` runs one `QObject` worker in a `QThread` for each input:

| Worker | Reads | Updates |
|---|---|---|
| `GetQRCodeWorker` | `qrcode2fe.q` | QR panel; starts the cycle record |
| `GetPictureAndResultWorker` | `camera2fe.q` | Image and result of the step. Ignores answers whose request id is not current |
| `GetTorqueReadingPlusCapPressAndResultWorker` | `io2frontend.q` | Torques, press events, move-to step |
| `EmergencyStatusMonitoringWorker` | `emergency.q` | Abort display; abort record (`aborted`, `abort_reason`) if a cycle was in process; at the QR code: display only |
| `ConnectionStatusUpdateWorker` | timer; the slot reads `hb2fe.q` without blocking | Connection lamps; Redis notice (`lastReportedRedisStatus`) |
| `FrontendNoticeWorker` | `feNotice:*` plus local notices | Notices at the top right |
| `HeartbeatSenderWorker` | — | `fe2hb.q`: DEAD while the GUI thread has not run its 1 s tick for 60 s, unless a menu dialog (FreeSimpleGUI, `_runDialog`) is open |
| `TimeDurationUpdateWorker` | — | Cycle timer |

At the end of the cycle (state 28), the FE builds the record, adding `cycleSettings:<QR>`, and sends it on `fe2db.q`. The frontend abort (Ctrl+Alt+Z, password `admin123`) stays in the frontend (no message to the QR code or camera server, no queues cleared) and sends `fe2io.q` `{action: FrontendAbortRequest, data: reason}`; at the QR code it sends nothing. Machine settings are edited by the Administrator or Supervisor, with the limits of §8.

## 6. Heartbeat and alarm server (`monitorAllConnections/HeartbeatAndAlarmServer.py`)

**`launchConnectionAlarmMonitoringThread`**

1. Reads all `*2hb.q` queues (`readAllHeartbeatsInHeartbeatServer`).
2. Debounces each server (`_update` with a counter).
3. Sends the per-server status to `hb2fe.q` (lamps) and `hb2io.q`, including the `redis` field.
4. Sounds the siren on a change to down.

**`launchThreadForBadComponentAlarm`** reads `alarm.q` and sounds the siren.

**`launchRedisHealthCheckThread`**

- Runs `checkRedisHealth` every `redis.health.check.interval.sec` (60 s), or every 10 s while it is failing.
- The check, in `utils/RedisUtils.py`:
  1. XADD a token to `redis.healthcheck.q` (maxlen 10).
  2. XRANGE that entry back.
  3. Compare the token.
  4. XDEL the entry.
- On failure it logs, prints, sounds the siren, reconnects and reports redis = down.

## 7. DB server (`persistence/DBServer.py`)

**`DataPersistence.doWork`**

1. Reads `fe2db.q`.
2. Saves the images in the day folder (`save*Image`).
3. Inserts one row.
4. On failure, `handleFailedInsert` keeps the message and logs it.

**Other threads**

- `watchForNewDay` (every 1 min) creates the next day's image folder.
- `sendHeartbeat` sends `db2hb.q`: DEAD while PostgreSQL is not reachable or `doWork` is not running or stuck (60 s).

Every PostgreSQL connection gives up after 5 s if the server does not answer (`PGCONNECT_TIMEOUT`, set in `Persistence.py`).

## 8. Data model (PostgreSQL, schema `al_hub_and_disc_assembly_schema`)

### `hub_and_disc_assembly_data`

One row per component. Primary key `record_id`, a sequence.

| Group | Columns |
|---|---|
| Identity | `qr_code`, `model_name`, `lhs_rhs`, `model_tonnage`, `component_assembly_start_datetime` |
| Each vision check | `check_<name>_imagefile`, `check_<name>_result` (default 'Not Checked'), `check_<name>_datetime`, for knuckle, hub_and_bottom_bearing, top_bearing, nut_and_platewasher, bunk_for_component_press, no_bunk, splitpin_and_washer, cap and bunk_cap_press |
| Torques | `nut_tightening_torque_1/2`, `free_rotation_torque_1` (float, default −1), each with `_result` and `_datetime`; `nut_tightening_torque_1/2_rotation_done` (bool) |
| Abort | `aborted` (bool, default false), `abort_reason` (varchar 255): emergency button on the machine or manual abort (Ctrl+Alt+Z); no record for an abort while waiting for the QR code |
| Presses | `component_press_done`, `cap_press_done`, each with `_datetime` |
| Settings used | `setting_rotation_rpm`, `setting_tightening_torque1_setpoint`, `setting_tightening_torque2_setpoint`, `setting_no_of_rotation1_ccw`, `setting_no_of_rotation1_cw` |
| Overall | `ok_notok_result`, `username` (FK to `users`), `remarks`, `created_on` |

Indexes: `idx_qr_code`, `idx_model_name`, `idx_created_on`, plus the primary key. The earlier REINDEX after every insert has been removed.

### `machine_settings`

Append-only, per model: the latest `setting_id` of a model (`model_name`; the models of `qrcode.partmapping.N`) is
in force for it. Rows without a model (before 2026-11-08) are copied to every model that has none when the database
is set up; a model with no row falls back to the latest such row.

| Column | Check |
|---|---|
| `rotation_rpm` | 20–40 |
| `tightening_torque1_setpoint`, `tightening_torque2_setpoint` | > 0 and < 20 |
| `no_of_rotation1_ccw`, `no_of_rotation1_cw` | 1 to 4 |
| `model_name` | the model (NULL: the one set for all models used before 2026-11-08) |
| `updated_by`, `updated_on_date` | — |

### `users` and `roles`

Login and role (Administrator, Supervisor, Operator).

## 9. Message schemas (Redis streams)

Every message also carries `timestamp` and `producer`.

| Queue | Fields |
|---|---|
| `qrcode2fe.q` | `qrCode`, `qrCodeDisplay`, `qrCodeStatus` |
| `qrcode2io.q` | `qrCodeStatus`, `qrCode` |
| `qrcode2camera.q` | `componentQRCode` |
| `io2camera.q` | `currentMachineState`, `action`, `requestId` |
| `camera2io.q` | `result`, `currentMachineState`, `requestId` |
| `camera2fe.q` | `originalImage`, `processedImage`, `result`, `currentMachineState`, `requestId`, `reason` |
| `io2frontend.q` | `currentMachineState`, `moveToState`, `action`, `value`, `result`, `rotationDone` |
| `fe2io.q` | `action` = FrontendAbortRequest (frontend) or Abort (test tools), `data` = reason |
| `hb2io.q` | Per-server status: `camera`, `qrcode`, `db`, `frontend`, `heartbeat`, `redis` |
| `hb2fe.q` | Per-server lamps, plus `redisstatus` |

The sending and reading functions for each queue are in `utils/RedisUtils.py` and are named `send<What>From<A>To<B>` and `read<What>In<B>From<A>`.

## 10. Configuration keys (selection)

| Key | Default | Section |
|---|---|---|
| `plc.vendor` | siemens (Machine 1: allenbradley) | 2 |
| `plc.ip` | 192.168.1.1 | 2 |
| `plc.read.interval` / `plc.write.interval` | 0.2 / 0.1 | 2.2 |
| `plc.sleeptime.between.ok.and.done` | 0.05 | Spec 5.1 |
| `plc.sleeptime.between.done.and.reset_done` / `…re_read` | 0.4 / 0.3 | Spec 5.1 |
| `plc.handshake.reset.delay.sec` | 0.4 | Default for delayed resets |
| `plc.handshake.torquevalues.reset.delay.sec` | 2.0 | Spec 5.3: torque Set / Value |
| `plc.handshake.componentpress.reset.delay.sec` | 1.5 | Spec 5.3: press Done |
| `plc.camera.answer.timeout.sec` | 20 (at least the vision check timeout + 8) | 2.4 |
| `camera.frame.max.wait.sec` | 1.0 | 3 |
| `pc.plc.cycle.aborted.tagname` | (empty) | 2.4 |
| `supervisor.restart.dead.processes` / `.check.interval.sec` / `.max.restarts.per.hour` | True / 2 / 10 | 1 |
| `redis.health.check.interval.sec` | 60 | 6 |
| `*.tagname` | see the spec | Spec 3 |

## 11. Tests (`test/machine2_dryrun`, mirrored in `test/machine1_dryrun`)

| Dry run | Covers |
|---|---|
| `DryRun_IOServer_Fault_Scenarios.py` | Handshakes, retakes, aborts, PLC loss, server-down rules (J1–J8, including redis), abort during a handshake write and the toggle during an abort (K1–K2), IOServer heartbeat DEAD while a loop is stuck (L), both vendors |
| `DryRun_Work_Thread_Health.py` | Heartbeats follow the work threads: `WorkerWatch`, the GenericQueueProcessor tick, camera / QR / DB heartbeats (frontend: FE dry run F11) |
| `DryRun_QRCode_Validation.py` | Every QR code in the tests (real format) accepted, side and model read; wrong codes refused |
| `DryRun_Camera_Stale_Frames.py` | Only new frames are inspected; stalled stream / camera not connected → NOT OK with the reason; timeouts in a row; camera answer timeout raised |
| Integrated cycle | All processes with simulators, down to the DB row |
| Frontend / DB / index and day-folder | FE workers; inserts; indexes; day folder |
| `DryRun_Heartbeat_And_Startup.py` | Heartbeat rules; Redis health check R1–R6 (R6: the check survives a recovery); database backup at shutdown (H10) |
| Knuckle, vision timeout, supervisor, endpoints | Knuckle retake; 12 s kill and restart; process restart |
| `Verify_Vision_Config_Equivalence` | AST equality of vision code before and after refactors |

`_plc_sim.py` simulates the PLC side of the v1 handshake. A v2 simulator is needed before v2 goes live.
