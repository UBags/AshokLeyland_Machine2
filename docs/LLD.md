# Machine 2 (Hub and Disc Assembly): Low-Level Design

| | |
|---|---|
| Document | Low-level design (LLD) |
| Read with | `HLD.md` (overview), `PLC_Interface_Spec.md` (PLC tags and timings) |
| Code at | Branch `machine2-io-fe-db-cam` |

---

## 1. MainProgram (`MainProgram.py`)

At import, MainProgram connects to Redis (3 attempts, 10 s apart, then exit). It does not flush Redis there: on Windows
every server process re-imports MainProgram (spawn). `main()` then runs these steps in order:

1. `setLogProcess("mainprogram")`; `clearQueues` (flushes the Redis db, trims the queues), then `checkRedisHealth`. If the check fails, print the reason and `sys.exit(1)`.
2. Start P1 (the logging server), then the picture clean-up in a background thread (`utils/PictureRetention.py`,
   CHANGES §110): picture files older than `picture.retention.days.trial` (30) under `picture.saving.dir.for.mode.trial`
   and older than `picture.retention.days.main` (60) under `basefolder.for.images` `Test/` and `Production/` are
   deleted, with every sub-folder; other files (FailedRecords JSON) are left; an empty folder is removed only if it
   was already older than the limit (today's folder, made at the same time, stays). One "Picture clean-up:" line per
   folder (count, MB, time). 0 turns a clean-up off.
3. Start-up screen; `doBackup()` (pg_dump of the production database); ping the camera and PLC addresses (a warning only).
   After the login: "Running application in <mode> mode", then, when `save.all.pictures.because.its.in.trial.mode=True`, "Images will ALSO be saved in <picture.saving.dir.for.mode.trial>" for 2 s (`showTrialPictureSavingNotice`).
4. `chooseMode()` (Test / Production); `setDatabaseName` for both databases (creates the database, schema, tables, indexes, default machine settings and users), then for the chosen one.
5. Login: up to 5 tries.
6. Start P2 camera, P3 QR, P4 IO, P6 FE, P7 HB and P5 DB, in that order. Every process first calls `setLogProcess(<name>)`, which sets the per-process log levels, crash capture and live configuration.
7. Start `ProcessSupervisor` (`utils/ProcessSupervisor.py`), which restarts a dead process through `startProcess(target, args)`; the camera server is started with `stateFile=None` and restarted with the state file (`supervisor.add(..., restartArgs=...)`).
MainProgram's red / green / blue / yellow printers are `utils/CosThetaPrintUtils`'s (since CHANGES §110; red and
green were `CosThetaFileUtils`'s console-only printers before), so its lines also go to the log file once
`setLogProcess` has run.

8. `monitorStop` waits for `stopcommandQ` (read from its start and never trimmed, so every process sees the one stop; `getStopCommandFromQueue` ignores a stop sent before the reading process started - one left by an earlier run, CHANGES §111), then sends the stop and collects each `stoppedresponseQ`, ends the processes and backs up the database (`doBackup`; before CHANGES §84 this never ran, because `stopProcesses` ended the thread with `sys.exit`).

**ProcessSupervisor.** Every `supervisor.check.interval.sec` (2 s), `checkOnce()` restarts each registered process that has died. This is enabled by `supervisor.restart.dead.processes`. Restarts are limited to `supervisor.max.restarts.per.hour` (10) for each process; above that the process is left down and logged. Supervision stops once `isStopping()` is true.

## 2. IOServer (`costhetaio/commonplc/`)

### 2.1 Modules

| File | Content |
|---|---|
| `PLCDataIO.py` | Abstract tag I/O: `open`, `close`, `connected`, `read`, `write`, `readMany`; a failed read or write raises `PLCIOError`. Implementations `costhetaio/allenbradley/AllenBradleyPLCDataIO.py` (pycomm3) and `costhetaio/siemens/SiemensPLCDataIO.py` (pyS7 through `plc_backend.py`) |
| `PLCDataIOFactory.py` | Picks the Siemens or Allen-Bradley implementation from `plc.vendor` |
| `MockPLC.py`, `MockPLCDataIO.py` | In-memory PLC for `PLCIOServer(mockInstance=True)` (the Mock-PLC test window); TEST mode uses the real PLC |
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
| Tag dump | `launchTagRequestListenerThread` | blocking read | `publishTags` → every tag's value on `receiveTags` (test windows) |
| Tag defaults | `UpdateTagsToDefaultProcessor` | queue | Kept; no longer used (the torque and press resets are scheduler jobs) |
| Delayed jobs | `DelayedJobScheduler` ("PLC Handshake Reset Scheduler") | one thread, time-ordered jobs | Torque Set / Value reset `plc.handshake.torquevalues.reset.delay.sec` (2.0 s) after the read; press Done `plc.handshake.componentpress.reset.delay.sec` (1.5 s) after the read |

The state is held in `MachineState` (`statemachine/StateMachine.py`). A change of state goes through `_onStateChange`, which first publishes it for the frontend (`_publishState`: `io2festate.q` and `io:state`, sequence number per change, reason; `docs/state_sync.md`), then audits it (`STATE A -> B  (seq N, published)`), records cycle times and sets `log:cycleContext`. The state at start is published too (reason `IOServer start`).

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
| 12 | READ_TIGHTENING_TORQUE_1 | Station2TorqueValue (float), RotationDuringTighteningDone | Value to FE; Set, Value and RotationDuringTighteningDone reset 2.0 s later → 13. Bypassed (`PC_PLC_Skip_Second_Tightening` True written in state 2): to FE with moveTo = 20 → 20 (13–19 skipped) |
| 13/14 | Bung for component press | CheckBungForComponentPress | Handshake. OK → 15 |
| 15 | READ_COMPONENT_PRESS_DONE | ComponentPressDone | To FE; reset after 1.5 s → 16 |
| 16/17 | No bung | CheckNoBung | Handshake. OK → 18 |
| 18 | READ_TIGHTENING_TORQUE_2_DONE | Station2TorqueValueSet | → 19 |
| 19 | READ_TIGHTENING_TORQUE_2 | Station2TorqueValue, RotationDuringTighteningDone | To FE; reset after 2.0 s → 20 |
| 20/21 | Split pin and washer | CheckSplitPinAndWasher | Handshake. OK → 22 |
| 22/23 | Cap | CheckCap | Handshake. OK → 24 |
| 24/25 | Bung for cap press | CheckBungForCapPress | Handshake. OK → 26 |
| 26 | READ_CAP_PRESS_DONE | Station4CapPressDone | To FE; reset after 1.5 s → 27 |
| 27 | READ_FREE_ROTATION_TORQUE_1_DONE | Station3TorqueValueSet | → 28 |
| 28 | READ_FREE_ROTATION_TORQUE_1 | Station3TorqueValue | To FE with moveTo = 1; Set and Value reset after 2.0 s; `resetForNextCycle("cycle completed", keepPendingResets=True)` → 1 |

### 2.4 Key methods

**`writeCycleSettingsToPLC(qr)`**

1. Reads the side and model from the QR code, and that model's latest `machine_settings` row.
2. Writes each value with `_writeVerified`, then `_settingReadBack`.
3. `_writeSkipSecondTightening`: the model's `bypass_press_and_tightening2`, written to `PC_PLC_Skip_Second_Tightening`
   (read back, 3 attempts). No tag name / no address / not on the PLC: nothing written, full run. Tag present but
   not written: returns False (QR NOT OK). Sets `_skipSecondTighteningThisCycle` only if True was written.
4. Stores `cycleSettings:<QR>` for the FE and DB (`bypass_press_and_tightening2: true` added only when bypassed).
5. Returns False if the side, or an existing skip tag, could not be written.

**`perform_write_state_action(tags, result)`**

Writes the handshake of spec §5.1 (steps 1–8). Each write is retried by `_writeBoolUntilWritten`, and the sequence stops if the state changes because of an abort. Each attempt checks the state and writes while holding `_abortLock`, which `abortCycle` holds for the whole abort, so a write of the old cycle cannot land after the abort's reset (CHANGES §84). The lock is released between attempts. The caller changes state only after it returns True.

**Picture handling**

- `_requestPicture(state)` creates the request id `<pid>.<cycle>-<n>` (the IOServer's pid, so a restarted IOServer's ids never match the old one's), sets `camera:activeRequest` and sends `io2camera.q`.
- `_dropCameraRequest()` clears the request.
- `_pictureNotOkWithoutAnswer(state, reason)` gives NOT OK after the 20 s timeout, or when the camera is down. The timeout is at least `<vendor>.vision.check.timeout.sec` + 8 s (`getMinimumPlcCameraAnswerTimeout`); a smaller value is raised.

**Trigger after a NOT OK**

`triggerIgnoredUntilReset`, `_setAwaitingTriggerReset` and `_noteTriggerStillHeld` are kept but no longer called (CHANGES §80). After a NOT OK, the read state acts on the trigger as soon as it reads True.

**PLC restart after a reconnect**

While the link is down, PLC writes and the read loop are held until the PLC has been checked. The reference is the
last PLC_PC tag the PLC raised that is still known to be True (the IOServer keeps an ordered list): still True after
the reconnect means no restart, and the cycle carries on in the same state. If no PLC-raised tag is known to be True,
the PC compares `PC_PLC_LH_RH_Selection` with the side it wrote in state 2. The same value means no restart. Any other
value (0 after a restart) means the PLC restarted: `abortCycle(..., pcAbort=True)` during a cycle, only the reset at
READ_QR_CODE.

**Delayed resets**

- `scheduleTagReset` and `_scheduleStation2TorqueReset` reset tags after they are read: torque values after `plc.handshake.torquevalues.reset.delay.sec` (2.0 s), press Done after `plc.handshake.componentpress.reset.delay.sec` (1.5 s).
- `completePendingResultReset` runs a pending reset at once, before the next handshake of the same check.
- `cancelVisionResultReset` cancels one.

**`abortCycle(reason, notifyQRCodeServer, notifyFrontend, clearQueues, pcAbort)`**

Holds `_abortLock` throughout. In order:

1. ABORT to the QR code server (scan stopped), if asked; the camera / QR code queues cleared, if asked.
2. The emergency message to the frontend (abort screen and record), unless the abort came from the frontend.
3. `PLC_PC_EmergencyAbort` := False.
4. State := READ_QR_CODE (which sends END_OF_CYCLE "aborted" to the camera server, through `_onStateChange`); the camera request dropped.
5. `resetForNextCycle(...)`: pending delayed resets cancelled, every per-cycle tag to its default with read-back, CheckQRCode armed.
6. `pcAbort` (frontend abort request that could write the tag, frontend down, heartbeat server down, PLC restart): `PC_PLC_CycleAborted` := True where the PLC has it (it goes False when the next QR code is accepted), and a frontend notice. The emergency button is not a PC abort.

Callers: the emergency thread, `_applyServerStatuses` (frontend / heartbeat server down), the frontend abort
listener (`Abort` from the test tools; `FrontendAbortRequest` through `handleFrontendAbortRequest`, which aborts only
if `PC_PLC_CycleAborted` = True could be written and read back, 3 attempts) and the PLC restart check. A restarted
IOServer (Redis key `ioserver:started` already set) sends the emergency message to the frontend and END_OF_CYCLE to
the camera server at start.

**`resetForNextCycle(reason, keepPendingResets)`**

The one reset routine: IOServer start, every abort, and the end of a cycle. The end-of-cycle reset (in
`process_current_read_state`) holds `_abortLock`, as the abort's does, and is skipped when an abort happened while that
read was being processed (`abortCount` changed: the abort has reset and armed CheckQRCode already; CHANGES §110). Writes every per-cycle tag (`perCycleTags()`: bool False, float 0.0) and reads it back (`RESET_WRITE_ATTEMPTS` 5), then arms `PLC_PC_CheckQRCode`. The state-2 settings, the watch tag and the connection toggle are left alone. A reset that cannot be verified is redone after the next reconnect, only while the machine is at the QR code. After a completed cycle (`keepPendingResets=True`) the pending resets (for example the free-rotation Set) are left to run at their time.

**`_applyServerStatuses()`**

Applies the rules of spec §5.7 to the camera, qrcode, db, frontend, heartbeat and redis statuses:

- It keeps `_dbDown`, `_redisDown`, and so on.
- `newCycleHoldReason()` returns the first reason that holds the next cycle.
- On a change, it logs and audits, and sets `feNotice:*` through `_notice`.

**State changes and their listener** (`statemachine/StateMachine.py`, CHANGES §110): every state change is queued under
the state lock and the listener (`_onStateChange`: state publish for the frontend, audit line, END_OF_CYCLE to the
camera server) is called after the outermost state-changing call of the thread has released the lock, in the order of
the changes, by one thread at a time. A reason given with the change (`setCurrentState(state, reason=...)`, the
abort's `abort: <reason>`) travels with that change to the listener (CHANGES §112; it was a shared "next reason"
that another thread's change could take). Before, the listener ran under the lock, and its Redis calls (1 s timeouts) held
up every other thread that changed the state.

**Audit**

`_audit`, `_auditWrite`, `_auditRead` and `_auditStateChange` write one line for every PLC write, every PLC read that changes something, and every state change, for later analysis.

## 3. Camera server (`camera/`)

**Thread chain** (`GenericQueueProcessor` threads):

```
MonitorGetPicQueue ─▶ TakePictures ─▶ ConverterScalerAndProcessor ─▶ ImageProcessor
```

1. `MonitorGetPicQueue` reads `io2camera.q`. It ignores any request whose id is not `camera:activeRequest`. An END_OF_CYCLE message is passed down the same queues as an item (`endOfCycleCommand`); `ConverterScalerAndProcessor` calls `ImageProcessor.endOfCycle()` when it reaches it, so after any check queued or running (CHANGES §110; before, `MonitorGetPicQueue` dropped the state at once and a check still running wrote the old cycle's state back).
2. `TakePictures` takes three pictures with `RTSPCam.getFrameAfter`: each a frame that arrived after the request and after the previous picture, waiting at most `camera.frame.max.wait.sec` (1 s). None in time (stalled stream) or camera not connected: the answer carries `noPictureReason` (CHANGES §84).
3. `ConverterScalerAndProcessor` resizes each picture to the state's target size (`<vendor>.vision.machine.state.N.target.*`, 1280 × 720 for every state) and passes the six (each of the three twice) to the vision check in the worker process (`VisionCheckWorker`; the only mode since 2026-11-15, CHANGES §105), within `<vendor>.vision.check.timeout.sec`. Before the knuckle check, `ImageProcessor.prepareForKnuckleCheck()` decides between a new cycle (a new QR code on `qrcode2camera.q`: the cycle's state dropped) and a retake (the QR code kept, the knuckle picture and frame dropped). With a `noPictureReason`, or no picture, the check is not run: NOT OK with the reason. A NOT OK also sends `alarm.q`.
4. The answer goes to `camera2io.q` with the request id and to `camera2fe.q` with both images and the reason.

**`assessPictures(state, pictures)`** (`CameraProcessorServer.py`): first `VisionFrame.applyToChecks(ImageProcessor.visionFrame)`, then one of:

| State | Pictures | Notes |
|---|---|---|
| Knuckle, `vision.knuckle.method=segmentation` | the first | `CheckKnuckle.checkKnuckleBySegmentation` → (picture, OK, frame); `ImageProcessor.setVisionFrame(frame if OK else None)` |
| Hub, top bearing, nut / washer, both bung checks, no bung | the first | Top bearing: OK saves the ring geometry (`saveBearingProfile`), NOT OK drops it; the nut / washer and bung checks get it with `getBearingProfile()` (current component only) |
| Cap (`_assessCap`, CHANGES §105) | the three different ones (`[0::2]`) | `CheckCap.checkCap(..., runSeating=False)` (which cap, right cap, brightening) on each; at least 2 of 3 must pass, else NOT OK with no MobileSAM run; then the whole check with the seating step once, on the first picture that passed, decides. One MobileSAM run per cap check (about 1.2-1.5 s on CPU) |
| Knuckle (`regions`), split pin | all six, in a `ThreadPoolExecutor` (6 workers) | 2 of the 3 pictures must pass; a picture not assessed within 5 s counts as NOT OK; the executor is shut down without waiting |

**Cycle state in `ImageProcessor`** (`_STATE_ATTRIBUTES` plus `currentPictures`): QR code, model, side, the top bearing
ring (`bearingCentreAndRadii`, `bearingProfileQRCode`), the knuckle frame (`visionFrame`) and the earlier pictures. In
worker mode the camera server sends a snapshot with each job (`snapshotState`); the worker restores it
(`restoreState`, which applies the frame) and returns what changed (`applyStateChanges`, which applies the frame in the
camera server too). `endOfCycle()` drops all of it.

**State file** (`camera/CameraStateFile.py`): `ImageProcessor.persistState()` writes that state to
`<project root>/Temp/camera_server_state.pkl` (pictures as PNG, each encoded once; temporary file then rename) after
every check and at every `endOfCycle()`. `CameraServer(mode, stateFile)`: with a path (the supervisor's restart) it
first calls `loadPersistedState(path)` (restores the state and applies the frame; a file older than
`camera.state.file.max.age.sec`, 3600 s, or unreadable is not used); with None (MainProgram's start) it starts empty
and writes an empty state. Either way it writes to the default file from then on.

**Knuckle frame** (`camera/KnuckleLocator.py`, `camera/VisionFrame.py`; Machine 2):

1. `KnuckleLocator.locate(picture)`: one MobileSAM embedding (the shared predictor of `ModelManager`); the spindle by one point prompt at (642, 360); each boss from a 4 × 4 grid of 24 two-point prompts placed relative to the spindle (3 masks each; a boss = round mask with a bright machined ring round a dark core; the pin = the core's centre); a missing boss looked for again at the usual place or from the other pin; the lower arm by 4 points down each side. Type: mean boss area × 1000 / pin distance² ≥ 34.5 → DOSTPLUS, cross-checked by boss radius / pin distance (0.1095 ± 0.0025). Side: the one lower arm found.
2. `VisionFrame.buildFrame(type, side, pins, spindleMask)`: rotation and scale from the pin line against `nominalPins()[type, side]` (`vision.frame.nominal.pins.*`), translation from the pin midpoint; anchor = where the pins put that type / side's usual spindle (the spindle mask is a cross-check, logged when over 6 px away).
3. `CheckKnuckle.checkKnuckleBySegmentation`: OK when both pins, the lower arm and a clear type are found, type and side match the QR code, and `VisionFrame.withinLimits` (`vision.knuckle.frame.max.shift` 40 px, `.max.rotation` 3°, `.max.zoom.change` 0.05). The frame is returned only with OK.
4. `VisionFrame.applyToChecks(frame)`: for each entry of `centres()` (`vision.frame.centre.*`) (hub segmentation, hub centre check, top bearing, nut / washer, no bung, bung disc, bung seated positions, cap) writes `anchor + scale · R(rotation) · (reference[type] − TYPE_SPINDLE[type])` into the check module's constant (and the hub segmenter instance's `annular_center`); with no frame, the properties values (from VisionConfig's registry). Radii are not scaled, except the bung seating reference ovals (`vision.bung.seating.reference.oval.dost/dostplus`): with a frame both axes × scale and the angle + rotation (`placeOval`, CHANGES §126).

**Bearing-ring guard** (nut / washer, `<vendor>.vision.nutwasher.bearing.ring.guard`): in worker mode the camera
server runs `CheckTopBearing.findRing` on a copy of the nut picture in its own thread while the worker runs the check
(`VisionCheckWorker._startBearingRingGuard`), waits at most 5 s after the verdict (`GUARD_MAX_WAIT_SECS`) and makes
the verdict NOT OK when the ring is bearing-sized (`CheckNutAndPlateWasher.bearingRingVerdict`). If the guard has no
answer in time or the ring finder fails, the verdict is NOT OK too, with the reason (since 2026-11-15; before, the
guard was not applied). In the dry-run windows the check runs the guard itself (a failing ring finder: NOT OK).

**Seating steps (Machine 2):** cap (`vision.cap.seating.check`): brass (CHANGES §128, no MobileSAM): the picture unwrapped
round the cap centre, per angle the strongest fall of R-B in `seating.brass.edge.band` (55-112 px: the cap edge) and of
grey in `seating.bore.edge.band` (106-140 px: the bore edge), a RANSAC circle fitted to each (fit share and radius
checked); cap centre to bore centre ≤ `seating.brass.offset.max` (10.5 px); silver (CHANGES §129): the cap outline by
MobileSAM (point prompt at the cap centre, radius 95-112), then two rules - (1) the hub centre must not be left
uncovered: the hub circle is found in this cycle's hub + bottom bearing picture (MobileSAM, 12 points on a ring of
`seating.hub.ring.radius` 95 px plus a box of ±`seating.hub.box.half` 125 px, roundest mask of radius 100-125); the hub
centre is that circle less `seating.hub.centre.inset` (12 px); in the cap picture its pixels darker than
`seating.hub.dark.level` (HSV value 60), outside the cap mask, after an opening of `seating.hub.dark.open` (9 px, removes
the shadow line round a seated cap), must be ≤ `seating.hub.visible.max` (400 px); no hub picture or no hub circle: rule
1 not applied, said in the reason; (2) the top face edge on one circle: round the outline centre, per angle the
strongest fall of brightness outward between `seating.silver.face.band.min` (60) and the outline radius less
`seating.silver.face.edge.margin` (6), a RANSAC circle fitted; the share of the 360 angles on it ≥
`seating.silver.face.fit.min` (0.64) - a tilted cap shows its side wall on one side and its face edge stops being one
circle; and the outline centre ≤ 20 px from the cap centre. The face-centre offset is logged only (a seated cap's face
already sits ~8 px from the outline centre: camera angle). Marked picture: hub circle blue, cap green / red, face circle
yellow. Then, brass and silver (CHANGES §143, `vision.cap.seating.hub.distance.check`): the hub check's centre - the
least-squares circle through the winning group's 4 / 5 mask centres (`CheckHubAndBottomBearing.hubCentreFromGroup`,
cropped-picture centres moved by the segmenter's crop corner; circle radius ≥ `vision.hub.centre.bolt.min.distance` 165 px
= bolts, else holes), kept as `ImageProcessor.hubCentre` (state attribute: sent to the worker, in the state file, dropped
at the end of a cycle and at a hub check that fails) - plus `seating.hub.distance.offset.<holes|bolts>.<dost|dostplus>` (per hub kind and model, CHANGES 147; (0, 0)) must lie
within `seating.hub.distance.max` (5 px) of the cap's centre: the stage-2 ellipse centre of the cap mask found by
`camera/CapMaskFinder.py` (the method frozen in CHANGES §139-142: gamma 2.0, disc r 160 round `cap.center`, YOLO boxes
conf 0.15 as box prompts, stability ≥ 0.70, predicted IoU ≥ 0.50, box merge 0.50, area 15000-50000 px after the disc's
own edge, largest mask, ellipse fit 2.5 px / 300 RANSAC tries, round a whole-pixel origin near the outline's middle
(CHANGES §144); keys `vision.cap.mask.*`). No cap mask -> NOT seated; no
hub centre -> rule not applied (said in the reason). Marked picture: the cap ellipse green / red, the hub centre a blue
cross. Bung (`vision.bung.seating.check`, after the hole pattern passed): hole-pattern centre ≤ 20 px from
the model's seated position, the top-face mask's roundness ≥ 0.952 (DOST) / 0.96 (DOSTPLUS), the face mask a clean
oval (area with holes filled / fitted ellipse area 0.98-1.02) and at most 2% of it outside the model's reference oval
(`seating.reference.oval.<model>`, centred on the seated position). On the bung picture: the face outline (green /
red), the reference oval (thick yellow) and the hole-pattern centre (thick blue cross; CHANGES 119). Each uses its own
`SamPredictor` per thread on the shared model.

**Bung holes (`BungSegmenter`, both bung checks; since CHANGES 115).** Per gamma (2.0, 7.75, 4.0, 6.5, 1.0, until a
definite answer), on the r250 disc round `bung.outer.disc.center`:
1. masks: one per YOLO box, plus one per point of a grid every `hole.grid.step` (15) px in the band
   `hole.grid.radius.min`-`max` (20-90 px, about 110 points; CHANGES 116), always - decoded in batches of 32 at the
   model's low resolution; only masks whose low-resolution area can pass `min/max.mask.area` (325-1300 px, 30 % slack)
   are brought to full size and checked exactly;
2. size filter before any merging: each mask cut to the inner disc, kept if 325-1300 px remain;
3. one mask per blob (masks overlapping > 15% of the smaller): the one found most often (IoU > 0.5 with others), never
   a union - so a hole is not joined to a highlight next to it;
4. shape filter (box 15-45 px, fill ≥ 40%);
5. pattern: 3 in a line -> middle dropped; ≥ 4 -> best square (definitive: DOST OK, or wrong bung when 2 are expected
   - decided only on a real square); 2 expected -> of the pairs 95-125 px apart, the one whose midpoint is nearest the
   disc centre, leaving out a pair that a third candidate makes into 3 corners of a square (a DOST bung's diagonal is
   as long as the DOSTPLUS pair); otherwise the next gamma.
One INFO line per gamma, `[Bung] <model> gamma <g>: masks ... candidates n: (x,y) d<px from the disc centre> a<area>
...; <decision>`, so a NOT OK can be traced from the log alone.

**VisionCheckWorker** (`VisionCheckWorker.py`):

- A child process that loads the models once (`ModelManager`, MobileSAM, segmenters) and runs one check per request.
- If a check does not finish in 12 s, the parent kills the child and starts a new one. The check counts as NOT OK. From the 3rd timeout in a row the reason says the checks are too slow on this PC (check the GPU); a check in time resets the count.
- A worker that dies before it reports ready is started again after a pause of 1, 2, 4 ... at most 60 s
  (`RESTART_BACKOFF_*`, on a timer thread; `failedStarts`, reset when a worker gets ready); checks meanwhile are NOT
  OK at once ("vision check worker starting"). A restart after a worker that had been ready stays immediate
  (CHANGES §110; before, a worker failing at start was respawned in a tight loop - 178 starts in 8 s measured).
- The worker warms up at start.

**RTSPCam** timings (watchdog, process check, reconnect loop) use `time.monotonic()` (CHANGES §110).

**Heartbeat** (`TakePictures.launchThreadForHeartbeat`): DEAD while the camera is not connected, or one of the three work threads (`cameraWorkers`, `utils/WorkerHealth.py`) is not running or has not gone round its loop for `heartbeat.work.loop.max.silence.sec`.

**Checks.** There is one module per check: `CheckKnuckle`, `CheckHubAndBottomBearing`, `CheckTopBearing`, `CheckNutAndPlateWasher`, `CheckNoCapBung` (bung for component press), `CheckNoBung`, `CheckSplitPinAndWasher`, `CheckCap` and `CheckBung` (bung for cap press); with the engines `HubAndBearingSegmenter`, `HexagonNutDetector`, `BungSegmenter`, `KnuckleLocator` and `ModelManager` (MobileSAM, YOLO). Their parameters are `<vendor>.vision.*` keys read through `VisionConfig.py` (`vInt`, `vFloat`, `vTuple`, ... with the Machine 1 value as default); `KnuckleLocator` and `VisionFrame` too (`vision.knuckle.locator.*`, `vision.frame.*`, since CHANGES §105). The pictures sent to the frontend carry outlines only, no values or words; a picture NOT OK without a check's verdict (timeout, stalled camera, worker starting) gets a red border (`_withReason`), and the reason goes to the log, the answer's `reason` and the "check" notice.

## 4. QR code server (`costhetaio/qrcodescanner/QRCodeScanningServer.py`)

**Thread chain:**

```
MonitorGetQRCodeQueue (io2qrcode.q) ─▶ GetAndValidateQRCode ─▶ QRCodeDispatcher
```

- `evaluateQRCode` checks the format and the side.
- The serial port is read with a 0.1 s timeout; `readline()` returns what has arrived when it expires, so the bytes
  are collected across reads (`QRCodeLineAssembler`) and a line is checked only when complete: at a CR or LF, or
  after 0.3 s with no new byte for a scanner without a line end (CHANGES §112; before, a code split across two reads
  was refused twice and lost).
- An invalid scan (CHANGES §110): `feNotice:qrcode` = "QR Code not valid" (10 s) for the frontend and the alarm signal
  on `alarm.q` (as a vision NOT OK: the heartbeat server sounds the siren); the read goes on, and the next valid scan
  clears the notice.
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
| `StateSyncWorker` | `io:state` at start, then `io2festate.q` (every 250 ms) | Nothing: compares the frontend's state with the IOServer's (`utils/StateSync.StateSyncMonitor`) and logs `STATE-SYNC MISMATCH / RESOLVED / GAP / IOSERVER-RESTART / CYCLE` lines (state sync phase A, `docs/state_sync.md`) |
| `HeartbeatSenderWorker` | — | `fe2hb.q`: DEAD while the GUI thread has not run its 1 s tick for 60 s, unless a menu dialog (FreeSimpleGUI, `_runDialog`) is open |
| `TimeDurationUpdateWorker` | PostgreSQL, one kept-open connection (`PersistentDBConnection`), in its own thread | Cycle timer and the production counts (today / week / month OK, one query, `getProductionCounts`), sent with the signal; a database error keeps the counts shown (CHANGES §110; before, three queries ran in the GUI thread, each on a new connection) |

At the end of the cycle (state 28; also at an abort), `doFinalProcessing` -> `finishCycle()` at once: the result box ("Component Successfully Done" / "Not Successfully Done" / "Aborted"), the record (with `cycleSettings:<QR>`) on `fe2db.q`, the data points reset, back to READ_QR_CODE with the QR code read requested. The tiles and the QR code line keep the finished cycle's values and pictures for `frontend.free.rotation.torque.display.sec` (5 s) after a completed cycle, 0.75 s after an abort (`ABORT_TILES_CLEAR_MSEC`, CHANGES §127), and are then cleared by `clearFinishedCycleTiles` (numbered, so a stale timer does nothing); a new QR code during that time clears them at once (CHANGES §123). An emergency or manual abort during those seconds takes the waiting-for-the-QR-code path (abort screen, no record). The frontend abort (Ctrl+Alt+Z, password `frontend.abort.password`) stays in the frontend (no message to the QR code or camera server, no queues cleared) and sends `fe2io.q` `{action: FrontendAbortRequest, data: reason}`; at the QR code it sends nothing. Machine settings are edited by the Administrator or Supervisor, with the limits of §8. Passwords, read from the properties file at each use: `frontend.abort.password` (Ctrl+Alt+Z); `machine.settings.password` to open Machine Settings and again at Save, where the prompt lists the changes and says that entering the password confirms them (no Yes / No since CHANGES §124); in addition at Save when the bypass switch changes, `machine.settings.bypass.password`, worded the same way (all `admin123` at delivery).

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

1. Reads `fe2db.q` (without deleting).
2. Saves the images in the day folder (`save*Image`), once per record (`savedPictureFiles`): a retry reuses the files.
3. Inserts one row; on success the message is deleted.
4. On failure (insert, or the pictures could not be saved), `handleFailedInsert` keeps the message and waits
   `INSERT_RETRY_GAP_SECS` (5 s) before the next attempt; after `MAX_INSERT_ATTEMPTS` (3) the record goes to
   `<basefolder><Test|Production>/FailedRecords/<time>_<QR>.json` with `_pictureFiles` (column -> picture file) and is
   removed from the queue. If that file cannot be written either, the record stays and is tried again after the gap.
   (CHANGES §110: before, the attempts ran back to back, the pictures were saved again at each, and the JSON had no
   picture paths.)

**Other threads**

- `watchForNewDay` (every 1 min): when the date has changed, creates the new day's image folders; images saved from then on go there.
- `sendHeartbeat` sends `db2hb.q`: DEAD while PostgreSQL is not reachable or `doWork` is not running or stuck (60 s).
  PostgreSQL is checked with `SELECT 1` on one kept-open connection (`PersistentDBConnection`: reconnects once when
  the connection broke; each statement limited to 5 s; CHANGES §110 - before, a new connection every 0.5 s).

Every PostgreSQL connection gives up after 5 s if the server does not answer (`PGCONNECT_TIMEOUT`, set in `Persistence.py`).

## 8. Data model (PostgreSQL, schema `al_hub_and_disc_assembly_schema`)

### `hub_and_disc_assembly_data`

One row per component. Primary key `record_id`, a sequence.

| Group | Columns |
|---|---|
| Identity | `qr_code`, `model_name`, `lhs_rhs`, `model_tonnage`, `component_assembly_start_datetime` |
| Each vision check | `check_<name>_imagefile`, `check_<name>_result` (default 'Not Checked'), `check_<name>_datetime`, for knuckle, hub_and_bottom_bearing, top_bearing, nut_and_platewasher, bung_for_component_press, no_bung, splitpin_and_washer, cap and bung_cap_press |
| Torques | `nut_tightening_torque_1/2`, `free_rotation_torque_1` (float, default −1), each with `_result` and `_datetime`; `nut_tightening_torque_1/2_rotation_done` (bool) |
| Abort | `aborted` (bool, default false), `abort_reason` (varchar 255): emergency button on the machine or manual abort (Ctrl+Alt+Z); no record for an abort while waiting for the QR code |
| Bypass | `bypassed_press_and_tightening2` (bool, default false). When true: the 4 skipped steps have result `Bypassed`, NULL datetimes, no picture file, `nut_tightening_torque_2` = -1.0, rotation done NULL; they do not count against the overall result |
| Presses | `component_press_done`, `cap_press_done`, each with `_datetime` |
| Settings used | `setting_rotation_rpm`, `setting_tightening_torque1_setpoint`, `setting_tightening_torque2_setpoint`, `setting_no_of_rotation1_ccw`, `setting_no_of_rotation1_cw` |
| Overall | `ok_notok_result`, `username` (FK to `users`), `remarks`, `created_on` |

An aborted cycle's record has, for every step not reached, result NOT OK, time 1970-01-01 00:00:00 and torque 0.0; the MI dashboard leaves such torques out of its torque charts (as it leaves out -1.0, not measured; CHANGES §127).

Indexes: `idx_qr_code`, `idx_model_name`, `idx_created_on`, plus the primary key (older duplicate indexes are dropped at start-up). Databases made before 2026-11-17 had `bunk` in the 9 bung column names; `createAllTables` (every start) renames each old column that exists to its `bung` name, keeping the data (CHANGES §125). The earlier REINDEX after every insert has been removed.

### `machine_settings`

Append-only, per model (index `idx_machine_settings_model`): the latest `setting_id` of a model (`model_name`; the models of `qrcode.partmapping.N`) is
in force for it. Rows without a model (before 2026-11-08) are copied to every model that has none when the database
is set up; a model with no row falls back to the latest such row.

| Column | Check |
|---|---|
| `rotation_rpm` | 20–40 |
| `tightening_torque1_setpoint`, `tightening_torque2_setpoint` | > 0 and < 20 |
| `no_of_rotation1_ccw`, `no_of_rotation1_cw` | 1 to 4 |
| `bypass_press_and_tightening2` | boolean, default false: skip the bung check, component press, bung removal and second tightening |
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
| `io2camera.q` | Take picture: `currentMachineState`, `action`, `requestId`. End of cycle: `currentMachineState` = READ_QR_CODE, `action` = "End Of Cycle", `data` = completed / aborted |
| `camera2io.q` | `result`, `currentMachineState`, `requestId` |
| `camera2fe.q` | `originalImage`, `processedImage`, `result`, `currentMachineState`, `requestId`, `reason` |
| `io2frontend.q` | `currentMachineState`, `moveToState`, `action`, `value`, `result`, `rotationDone` |
| `fe2io.q` | `action` = FrontendAbortRequest (frontend) or Abort (test tools), `data` = reason |
| `emergency.q` | `emergencystatus` |
| `io2festate.q` (and key `io:state`) | `snapshot`: JSON `{pid, seq, cycleId, qr, old, oldName, new, newName, reason, time}` |
| `fe2db.q` | The cycle record: every result, image, datetime and torque, the rotation-done bits, `aborted`, `abortReason`, `bypassedPressAndTightening2`, overall result |
| `hb2io.q` | Per-server status: `camera`, `qrcode`, `db`, `frontend`, `heartbeat`, `redis` |
| `hb2fe.q` | Per-server lamps, plus `redisstatus` |

The sending and reading functions for each queue are in `utils/RedisUtils.py` and are named `send<What>From<A>To<B>` and `read<What>In<B>From<A>`.

## 10. Configuration keys (selection)

Keys spelled with `bunk` before 2026-11-17 are now `bung` (`images.bung.and.no.bung.folder`, `plc.pc.check.bung.tagname`, `<vendor>.vision.bung.*`, ...). A properties file with the old spelling still works: at every load `CosThetaConfigurator._addLegacyBungKeys` adds each old key under its new spelling (a warning is printed). The PLC tag names themselves (`PLC_PC_CheckBunkForCapPress`, `PC_PLC_NoBunkCheckOK`, ... - the values of the `*.tagname` keys and the keys of `IOServerProperties.yaml`) are the PLC program's names and keep their spelling (CHANGES §125).

| Key | Default | Section |
|---|---|---|
| `plc.vendor` | siemens (Machine 1: allenbradley) | 2 |
| `plc.ip` / `siemens.plc.ip` | 192.168.1.1 (Allen-Bradley) / 192.168.1.10 (Siemens) | 2 |
| `plc.read.interval` / `plc.write.interval` | 0.2 / 0.1 | 2.2 |
| `plc.sleeptime.between.ok.and.done` | 0.05 | Spec 5.1 |
| `plc.sleeptime.between.done.and.reset_done` / `…re_read` | 0.4 / 0.3 | Spec 5.1 |
| `plc.handshake.reset.delay.sec` | 0.4 | Fallback delay of `scheduleTagReset` (every current caller passes its own) |
| `plc.handshake.torquevalues.reset.delay.sec` | 2.0 | Spec 5.3: torque Set / Value |
| `plc.handshake.componentpress.reset.delay.sec` | 1.5 | Spec 5.3: press Done |
| `plc.camera.answer.timeout.sec` | 20 (at least the vision check timeout + 8) | 2.4 |
| `camera.frame.max.wait.sec` | 1.0 | 3 |
| `pc.plc.cycle.aborted.tagname` | `PC_PLC_CycleAborted` (optional tag; no Siemens address yet) | 2.4 |
| `pc.plc.skip.second.tightening.tagname` | `PC_PLC_Skip_Second_Tightening` (optional tag; Siemens `DB500,X48.0`, CHANGES §121) | 2.4 |
| `cycle.settings.source` | database | 2.4 |
| `supervisor.restart.dead.processes` / `.check.interval.sec` / `.max.restarts.per.hour` | True / 2 / 10 | 1 |
| `redis.health.check.interval.sec` | 60 | 6 |
| `heartbeat.work.loop.max.silence.sec` | 60 | 3, 5, 7 |
| `frontend.free.rotation.torque.display.sec` | 5.0 (seconds the finished cycle's values stay in the tiles; the result box comes at once) | 5 |
| `frontend.state.sync.grace.sec` | 5.0 (a state difference lasting longer is logged; logging only) | 5 |
| `frontend.abort.password` / `machine.settings.password` / `machine.settings.bypass.password` | admin123 / admin123 / admin123 | 5 |
| `camera.state.file.max.age.sec` | 3600 | 3 |
| `save.all.pictures.because.its.in.trial.mode` / `picture.saving.dir.for.mode.trial` | True / C:/Temp/TrialImages/ | 1, 3 |
| `picture.retention.days.trial` / `picture.retention.days.main` | 30 / 60 (0 = no clean-up) | 1 |
| `<vendor>.vision.check.timeout.sec` | 12 (the check always runs in the worker process) | 3 |
| `<vendor>.vision.frame.*` (reference spindle, type spindles, nominal pins per type and side, 18 centres per type), `<vendor>.vision.knuckle.locator.*` (spindle, bosses, lower arm, type rule) | Machine 2 values, measured 13 Nov 2026 (allenbradley: the same, not measured for Machine 1) | 3 |
| `<vendor>.vision.knuckle.method` | segmentation (Machine 1: regions) | 3 |
| `<vendor>.vision.knuckle.frame.max.shift` / `.max.rotation` / `.max.zoom.change` | 40 / 3.0 / 0.05 | 3 |
| `<vendor>.vision.nutwasher.bearing.ring.guard` | True (both machines) | 3 |
| `<vendor>.vision.cap.seating.check` / `<vendor>.vision.bung.seating.check` | True / True (Machine 1: False / False) | 3 |
| `<vendor>.vision.cap.seating.hub.distance.check` / `.max` / `.offset.<holes\|bolts>.<dost\|dostplus>` | True / 5.0 / Machine 2: holes 0.3,0.5 (DOST) and 0.1,-1.4 (DOSTPLUS), bolts -0.4,4.9 (DOST) and -0.6,2.2 (DOSTPLUS) (Machine 1: False, all 0,0) | 3 |
| `*.tagname` | see the spec | Spec 3 |

## 11. Tests (`test/machine2_dryrun`, mirrored in `test/machine1_dryrun`)

| Dry run | Covers |
|---|---|
| `DryRun_IOServer_Fault_Scenarios.py` | Handshakes, retakes, aborts, PLC loss, server-down rules (J1–J8, including redis), abort during a handshake write and the toggle during an abort (K1–K2), IOServer heartbeat DEAD while a loop is stuck (L), both vendors |
| `DryRun_Work_Thread_Health.py` | Heartbeats follow the work threads: `WorkerWatch`, the GenericQueueProcessor tick, camera / QR / DB heartbeats (frontend: FE dry run F11) |
| `DryRun_QRCode_Validation.py` | Every QR code in the tests (real format) accepted, side and model read; wrong codes refused; an invalid scan gives the "QR Code not valid" notice and the alarm, the next valid scan clears it (V4) |
| `DryRun_Picture_Retention.py` | The picture clean-up at start: old pictures deleted in every sub-folder, young ones and other files kept, empty old folders removed, today's kept; the folders and limits from the properties (P1-P6) |
| `DryRun_Camera_End_Of_Cycle_Order.py` | END_OF_CYCLE passed down the camera server's queues and handled after the check in progress (E1-E4) |
| `DryRun_Vision_Worker_Backoff.py` | A worker failing at start restarted with a growing pause, checks NOT OK at once meanwhile, the pause reset by a worker that gets ready (B1-B3) |
| `DryRun_State_Change_Delivery.py` | The state machine's listener outside the lock: another thread not held up by a slow listener, changes delivered once and in order, a reason only with its own change, nested calls (D1-D4) |
| `DryRun_Stale_Stop_Command.py` | A stop command from an earlier run ignored; a stop during the run honoured, its notice printed once (S1-S2) |
| `DryRun_Camera_Stale_Frames.py` | Only new frames are inspected; stalled stream / camera not connected → NOT OK with the reason; timeouts in a row; camera answer timeout raised |
| Integrated cycle | All processes with simulators, down to the DB row |
| Frontend / DB / index and day-folder | FE workers (F7b: production counts read off the GUI thread, a 3 s database read does not stall it, the last counts kept when the database is down); inserts; record loss (b: 3 attempts 5 s apart, pictures saved once, the picture files in the FailedRecords JSON; d: one failure then success; e: the heartbeat's one kept-open connection, replaced when broken); indexes; day folder |
| `DryRun_Heartbeat_And_Startup.py` | Heartbeat rules; Redis health check R1–R6 (R6: the check survives a recovery); database backup at shutdown (H10) |
| Knuckle, vision timeout, supervisor, endpoints | Knuckle retake; 12 s kill and restart; process restart |
| `DryRun_Knuckle_Frame.py` | Knuckle by segmentation on every knuckle picture with each QR code; the frame on the pictures as taken and with simulated camera moves; the centres written into the checks, restored, dropped at cycle end and retake (MobileSAM weights) |
| `DryRun_CheckKnuckle_Images.py` | The `regions` knuckle method (Machine 1; Machine 2 before CHANGES 102) |
| `DryRun_Bearing_Ring_Guard.py` | The guard in the camera server beside the worker; NOT OK when it cannot decide (G7-G9) |
| `DryRun_Camera_State_File.py` | The camera server's cycle state across a restart (C1-C5) |
| `DryRun_State_Sync_Monitor.py`; fault scenarios O; frontend dry run F14 | State sync phase A: the comparison and its log lines (M1-M6); every IOServer state change published (O); the frontend's worker on real messages, behaviour unchanged (F14) |
| `DryRun_All_Checks_UI.py` | Window (or `--auto`): one picture per check; Process Images runs them in order as in one cycle, a configurable pause (3 s) between checks, each check's marked picture and log lines shown; the frame's centres listed |
| `DryRun_Cap_Check.py` | `CheckCap` on the cap pictures, seated / skewed / wrong cap / no cap (MobileSAM weights) |
| `DryRun_Cap_Production_Path.py` | The cap check as the camera server runs it (`assessPictures`, three pictures): seated OK with one seating run, skewed / wrong cap / no cap NOT OK, 2 of 3, time per check |
| `DryRun_Cap_Silver_Seating.py` | Silver cap seating, two rules (CHANGES §129): seated SEATED, skewed NOT SEATED, face fit alone separates them, hub centre uncovered on the cap pushed off the bore, no / blank hub picture -> rule 1 not checked |
| `DryRun_Cap_Hub_Distance.py` | Cap seating by the hub-centre distance (CHANGES §143): hub centre from 4 / 5 holes or bolts, kept with the cycle (worker, state file, end of cycle), production cap mask = the frozen window's, the rule within / not within, checkCap OK / NOT OK by the hub centre |
| `DryRun_Cap_Hub_Distance_UI.py` | Window / `--batch` (CHANGES §144): hub check on a chosen hub picture -> hub centre; cap check with it on every picture of a caps folder; every value and the suggested offset / max from the seated caps |
| Windows: `DryRun_Knuckle_Check_UI`, `DryRun_Hub_Check`, `DryRun_TopBearing_Check`, `DryRun_NutWasher_Check`, `DryRun_Cap_Check_UI`, `DryRun_Bung_Check_UI` | The real check on a folder of pictures, shown one at a time |
| `DryRun_Bung_Rotation` | Seated bung pictures turned about the seated centre in 15° steps: OK as their own model and NOT OK as the other at every angle (`--no-yolo`: the point grid alone) |
| `Study_Knuckle_Segmentation.py`, `Study_Boss_Grid_Masks.py`, `Diag_Cap_Seating.py`, `PlantPC_Vision_Timing.py` | Study and diagnostic tools (not tests) |
| `Verify_Vision_Config_Equivalence` | AST equality of vision code before and after refactors (`KnuckleLocator.py` and `VisionFrame.py` in its manifest since CHANGES §105, reference f3aa0ed) |

`_plc_sim.py` simulates the PLC side of the handshake.
