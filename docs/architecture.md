> **Baseline of 2026-10-30, commit `5633458` (the Machine 1 code before the Machine 2 work), kept for reference.** It describes the system as it was then: Allen-Bradley only, 29 states, and findings R1–R17, most of which have since been fixed. For the current design see `HLD.md`, `LLD.md` and `PLC_Interface_Spec.md`; for what changed, `../CHANGES_MACHINE2.md`.

# AshokLeyland_Machine2 — System Architecture (as built)

Traced from `MainProgram.py` at commit `5633458` (2026-10-30). All references are `file:line` in repo `UBags/AshokLeyland_Machine2`.

How to read the tags:
- **[V]** — read in the code.
- **[I]** — inferred from the code.
- **[PLC?]** — depends on PLC-side logic, which is not in this repo.

---

## 1. What the machine does

The station assembles a wheel hub onto a steering knuckle for Ashok Leyland **DOST** and **DOSTPLUS** (LHS and RHS variants). One RTSP camera inspects the part at nine points in the sequence. Between those inspections the operator and the PLC do the physical work:
- two tightening-torque rounds at station 2;
- free rotation and its torque reading at station 3;
- a component press and a cap press.

The PC runs the vision checks and records everything. The PLC drives the actuators and owns the step sequencing.

## 2. Process topology

`MainProgram.py` uses `multiprocessing` spawn on Windows. All processes talk only through **Redis streams** on `localhost:6379`. The exceptions are PostgreSQL and the PLC.

| # | Process | Entry point | Role |
|---|---|---|---|
| P1 | Logging server | `logutils/CentralLoggers.startLoggers` | Central console and file logs |
| P2 | Camera server | `camera/CameraProcessorServer.startCameraServer` | Grabs 3 frames from RTSP, runs the vision check for the current state |
| P3 | QR server | `costhetaio/QRCodeScanningServer.startQRCodeServer` | Serial scanner on COM3 at 38400 baud; validates and broadcasts the component QR |
| P4 | IO server | `costhetaio/IOServer.startIOServer` | **Owns the machine state machine.** Talks to the **Allen-Bradley** PLC via `pycomm3.LogixDriver` |
| P5 | DB server | `persistence/DBServer.startDBServer` | Writes one row per cycle; saves the images as files |
| P6 | Frontend | `frontend/AshokLeylandFrontEnd.startFrontEnd` | PySide operator HMI |
| P7 | Heartbeat and alarm | `monitorAllConnections/HeartbeatAndAlarmServer` | Liveness of P2–P6; plays the siren and bad-component WAVs |

`OtherServices.py` is launched separately, not by MainProgram:
- Flask web service on port 8000;
- MI dashboard on port 8050;
- ShowScreen screen-share on ports 8099 and 8100.

`siemens/` is an unwired S7-1500 port. `costhetaio/IOServer_ModbusTCP.py` and `MockPLC.py` are prototypes. None of these are used.

### Startup sequence (`MainProgram.py`)

1. Redis connect: 3 attempts, 10 s apart, then exit. Then `flushdb` and `clearQueues` (:46-93).
2. Start the logger (P1).
3. Splash screen, then `doBackup()` (pg_dump of the **production DB only**).
4. Ping the camera and PLC IPs. The result is a warning only (:131).
5. `chooseMode()` (Test or Production). Then `setDatabaseName(...)` for **both** databases, which creates the DB, schema, tables, default settings and seed users (:346-354).
6. Login: 5 tries, against the selected mode's database.
7. Start the processes in this order: Camera, QR, IO, Frontend, Heartbeat, DB.
8. `monitorStop` thread: polls `stopcommandQ` every 10 s. On STOP it waits for the end screen, sends STOP again, SIGTERMs every PID, then calls `doBackup()` (:151-228).

### Redis queues (stream names from `ApplicationConfiguration.properties`)

Every reader does `xtrim(q, 1)` before it reads. So **only the newest message per queue survives**: queues act as latest-value mailboxes, not FIFOs [V] (`utils/RedisUtils.py`, e.g. :783).

| Queue | From → To | Payload |
|---|---|---|
| `io2qrcode.q` | IO → QR | `readqrcode` |
| `io2qrcodeabort.q` | IO/FE → QR | ABORT |
| `qrcode2io.q` / `qrcode2fe.q` / `qrcode2camera.q` | QR → IO / FE / Cam | QR code, status (always OK), display string |
| `io2camera.q` | IO → Cam | `takepicture` + `currentMachineState` |
| `camera2io.q` | Cam → IO | ok/notok + state |
| `camera2fe.q` | Cam → FE | original and processed PNG + ok/notok + state |
| `io2frontend.q` | IO → FE | state, value (torque), result (always ok), action |
| `emergency.q` | IO → FE | emergency abort |
| `fe2db.q` | FE → DB | full cycle record (9 PNGs, results, torques, datetimes, overall) |
| `*2hb.q`, `hb2fe.q`, `hb2io.q` | ↔ Heartbeat | ALIVE/DEAD |
| `alarm.q` | Cam → HB | bad component, which plays a sound |
| `stopcommandQ` / `stoppedresponseQ` | any → all | shutdown |
| `publishTags` / `receiveTags` | any ↔ IO | diagnostic dump of all PLC tags (`IOServer.py:1813-1844`) |

---

## 3. Machine state machine

**Definitions.** `statemachine/StateMachine.py` defines `MachineState` (IntEnum, states 1–29) and `MachineStateMachine`:
- The machine is linear: `incrementState` wraps from 29 back to 1, and `decrementState` floors at 1.
- Each state has an operator instruction, `state.N.instruction`, in the properties file.

**There are two copies of the state machine** [V]:
- **IO server.** This is the authoritative copy. PLC tags drive it.
- **Frontend.** It keeps its own `MachineStateMachine` and advances it with hard-coded handlers (`AshokLeylandFrontEnd.py:1164-1398`). It **ignores** the `moveToState` and `action` fields that IO sends. It also does not use the state the camera reports: it attributes each image to whatever state the frontend itself is in (:1700).
- **Nothing resynchronises the two copies.** They agree only because the sequence is linear and every message arrives.

### Cycle sequence

Read the columns as follows:
- **Trigger**: the PLC→PC tag the IO read loop polls in that state.
- **PC action**: what IO does when the trigger is True.
- **Write state**: IO's write loop then waits for the camera result and writes OK and Done back.

| Step | States | Trigger (tag name in properties) | PC action | Result back to PLC |
|---|---|---|---|---|
| 0 QR | 1 → 2 | `PLC_PC_CheckQRCode` | QR server reads the scanner (blocks until a valid code arrives) | `PC_PLC_QRCodeCheckOK`, `…Done`; **plus rotation settings** (see §4) |
| 1 Knuckle | 3 → 4 | `PLC_PC_CheckKnuckle` | Camera: CheckKnuckle | `PC_PLC_KnuckleCheckOK/Done` |
| 2 Hub and bottom bearing | 5 → 6 | `PLC_PC_CheckBearing` | Camera: CheckHubAndBottomBearing | `PC_PLC_BearingCheckOK/Done` |
| 3 Top bearing | 7 → 8 | `PLC_PC_CheckHubAndSecondBearing` | Camera: CheckTopBearing | `PC_PLC_HubAndSecondBearingCheckOK/Done` |
| 4 Nut and plate washer | 9 → 10 | `PLC_PC_CheckNutAndPlateWasher` | Camera: CheckNutAndPlateWasher | `…OK/Done` |
| 5 Torque round 1 | 11, 12 | `PLC_PC_Station2TorqueValueSet`, then float `PLC_PC_Station2TorqueValue` | Send the torque to FE; after 1.5 s, reset both tags | — |
| 6 Free rotations | 13 | `PLC_PC_Station3ValueSet` | Notify FE; after 2.0 s, reset `Station3ValueSet` and `Station3TorqueValue` | — |
| 7 Bunk before component press | 14 → 15 | `PLC_PC_CheckNoCapBung` | Camera: CheckNoCapBunk | `PC_PLC_NoCapBungCheckOK/Done` |
| 8 Component press | 16 | `PLC_PC_ComponentPressDone` | Notify FE | — |
| 9 Bunk removed | 17 → 18 | `PLC_PC_CheckNoBung` | Camera: CheckNoBunk | `PC_PLC_NoBungCheckOK/Done` |
| 10 Torque round 2 | 19, 20 | same Station2 tags as step 5 | As step 5 | — |
| 11 Split pin and washer | 21 → 22 | `PLC_PC_CheckSplitPinAndWasher` | Camera: CheckSplitPinAndWasher (**stub**) | `…OK/Done` |
| 12 Cap | 23 → 24 | `PLC_PC_CheckCap` | Camera: CheckCap | `PC_PLC_CapCheckOK/Done` |
| 13 Bunk before cap press | 25 → 26 | `PLC_PC_CheckBung` | Camera: CheckBunk | `PC_PLC_BungCheckOK/Done` |
| 14 Cap press | 27 | `PLC_PC_CapPressDone` | Notify FE | — |
| 15 Free-rotation torque | 28, 29 | `PLC_PC_Station3ValueSet` (**same tag as step 6**), then `PLC_PC_Station3TorqueValue` | Send the torque to FE with `moveAheadToNextComponent`, then wrap to state 1 | — |

**End of cycle.** When the state wraps to 1, IO does three things (`IOServer.py:1376-1382`):
1. logs a cycle-time breakdown (T1–T9 plus a fixed 8 s);
2. runs `resetAll()`, which writes False or 0.0 to **every** handshake tag;
3. **writes `PLC_PC_CheckQRCode = True` itself**, so the PC re-arms the next QR read rather than waiting for the PLC.

---

## 4. PC↔PLC communication (`costhetaio/IOServer.py`)

### Transport

- `pycomm3.LogixDriver` (EtherNet/IP, Allen-Bradley Logix) at `plc.ip=192.168.1.1`.
- There are **two connections**, one for reads and one for writes, each guarded by its own lock (:864-869).

### Threads (started at :1403-1437)

| Thread | Period | Job |
|---|---|---|
| Read loop | 0.2 s | Reads the trigger tag(s) for the current state (`getCurrentTagsToBeRead` :681). On True (or a float that is not the -1234 sentinel) it calls `process_current_read_state` (:1175) and increments the state. |
| Write loop | 0.1 s | In WRITE states it reads `camera2io.q` (500 ms block). On OK it calls `perform_write_state_action` and increments. On NOT OK it writes OK=False and Done=True, then **decrements back to the READ state** (:1160-1167). In `WRITE_QR_CODE` it reads `qrcode2io.q` instead and writes the rotation settings. |
| Connection monitor | 1.0 s | Checks that the read client is connected and `PLC_PC_WatchTag == 1234`; otherwise it reconnects both clients. |
| Emergency monitor | **1.0 s** | Polls `PLC_PC_EmergencyAbort` (see §5). |
| Combined heartbeat | 2 s | Toggles `PC_PLC_ConnectionStatus` (a square wave the PLC can watchdog). |
| IO heartbeat | 0.5 s | Sends ALIVE or DEAD to the heartbeat server. |
| Stop monitor | 2 s | Watches `stopcommandQ`. |
| `UpdateTagsToDefaultProcessor` | queue | Delayed tag resets for the torque tags. |
| Tag-dump listener | blocking | Serves `publishTags`. |

### Result handshake (`perform_write_state_action` :1386)

1. Write the `…CheckOK` result.
2. Sleep 0.2 s (`plc.sleeptime.between.ok.and.done`).
3. Write `…CheckDone = True`.

The ordering guarantees the PLC reads a valid OK by the time it sees Done.

**The PC does *not* reset the trigger (`PLC_PC_Check…`) or the Done tag after a step.** The docstring at :781-783 says the trigger is reset, but `perform_write_state_action` only writes `tags[0]` and `tags[1]`. Triggers and Done tags return to False only in `resetAll()`, which runs at cycle end or on abort. [PLC?] The PLC must therefore drop its own trigger and must treat a rising edge of Done as "result ready". On a NOT OK result, IO decrements to the READ state while the trigger may still be True. If it is, the read loop immediately re-requests a picture. **Whether this is the intended "retry until OK" behaviour depends on the PLC ladder.**

### Rotation settings (`writeRotationSettingsToPLC` :501)

These are written in state 2 for every QR code:
- **Source:** the latest `machine_settings` DB row, plus LHS/RHS taken from the QR part number.
- **LHS:** write the CW counts, set the CCW counts to 0, and set `LH_RH_Selection = 1`.
- **RHS:** write the CCW counts, set the CW counts to 0, and set `LH_RH_Selection = 2`.
- **RPM:** always written.
- **Tag names differ between the two properties files.** The current file uses `NoOfRotation1CW`. The Production 29-01-2026 file uses `PC_PLC_NoOfRotation1CW`. [PLC?] It is unclear which matches the PLC.

### Torque

- The float is accepted as soon as the `…ValueSet` bool is True. No range check is applied, and 0.0 is accepted.
- IO sends it to the frontend with the default `result=ok`. **No torque limits exist anywhere**: not in config, the database or the frontend. So a torque can never be recorded as NOT OK [V].

---

## 5. Emergency abort and stop propagation

### A. PLC E-stop (the only path that resets the IO state machine)

```
PLC sets PLC_PC_EmergencyAbort=True
  └─ IO emergency thread (polls every 1 s) (IOServer.py:996-1018)
       1. io2qrcodeabort.q ← ABORT        → QR server aborts any blocking scanner read
       2. clearKeyCommunicationQueuesOnAbort(): xtrim 0 on
          io2camera, io2qrcode, qrcode2io, qrcode2fe, qrcode2camera, camera2io, camera2fe
          (NOT io2frontend, fe2db, emergency)
       3. emergency.q ← ok                → FE
       4. reset PLC_PC_EmergencyAbort=False (the PC clears the PLC's flag)
       5. resetAll() (every handshake tag False/0), state := READ_QR_CODE
       6. write PLC_PC_CheckQRCode=True   (PC re-arms the QR read)
FE EmergencyStatusMonitoringWorker (polls every 3 s, :1630)
  └─ updateEmergencyAbortPressed → emergencyButtonPressed=True → doFinalProcessing
       → after 750 ms: show "Component Processing Aborted", **send the partial record to the DB**
         (overall NOT OK, unreached steps NOT OK / 1970 timestamps / torque 0.0), reset to QR state
```

**The camera server is never told about an abort.** Its pipeline (MonitorGetPicQueue → TakePictures → ConverterScalerAndProcessor) has no abort input. A check that is running when the E-stop is pressed finishes later and posts its result to `camera2io.q` and `camera2fe.q`, after those queues were cleared (see R3).

**Worst-case latency** is about 1 s at IO plus about 3.5 s at the frontend, before the 750 ms display delay.

### B. Frontend "Manual Abort" (menu, Ctrl+Alt+Z)

`AshokLeylandFrontEnd.py:1058-1071`:
- Asks for confirmation. **Closing the dialog counts as confirming** (`SimplePopups.py:281`).
- Sends ABORT to the QR server, clears the key queues, and runs final processing.
- **It sends nothing to IO or the PLC.** IO and the PLC stay mid-cycle while the frontend returns to the QR state. The dialog tells the operator to press the machine E-stop as well.
- `fe2io.q` exists but the frontend never writes to it.

### C. Heartbeat-driven abort (dead code today)

- IO would abort the cycle exactly as in path A if the heartbeat server's combined status were DOWN twice in a row (:970-980).
- **`currentStatus = True` is hard-coded "while testing"** (:960-965), so this path never runs and nothing reads `hb2io.q`.
- A dead camera, QR or DB server therefore never aborts a cycle. It only turns the frontend tile red and sounds the siren.

### D. Shutdown

1. Frontend Exit calls `sendStopCommand` (stopQ). This happens twice because `goToClose` and `closeEvent` both run the teardown.
2. Every `GenericQueueProcessor` and server thread polls stopQ. The message is read without being deleted, so every process sees it.
3. MainProgram SIGTERMs the processes, runs a backup, and runs `taskkill /im cmd.exe`.

---

## 6. Frontend (`frontend/AshokLeylandFrontEnd.py`)

### Layout (:359-564)

- **Mode bar:** mode and product name, plus the QR display (e.g. "DOST - LHS - 2.0T : … [- DUPLICATE]").
- **Connection tiles:** four tiles for Camera, QR Scanner, I/O and Database. There is no tile for the frontend itself.
- **Results grid:** 2×7 outcome tiles: Knuckle, Hub, Top Bearing, Nut & Washer, Torque 1, Free Rotations, Bunk/Component Press, Remove Bunk, Torque 2, Split Pin & Washer, Cap, Bunk Check, Cap Press. Each shows the processed image or a value, plus OK or Not OK. The final free-rotation torque is written into the Cap Press tile.
- **Instruction line:** text for the current state.
- **Info panel:** date, start and running time, counters for today, week and month (these count OK rows only), and user and role.
- **`LargeImageWindow`:** an ApplicationModal pop-up that shows each step's image on a green or red background. At end of cycle it shows Success, Not Successful or Aborted.

### Workers (one QThread each)

| Worker | Period | Reads | Notes |
|---|---|---|---|
| QR | 0.5 s | `qrcode2fe.q` | — |
| Picture and result | 0.5 s | `camera2fe.q` | — |
| Torque, press and rotation | 0.5 s | `io2frontend.q` | — |
| Heartbeat display | 2 s | `hb2fe.q` | — |
| Emergency | 3 s | `emergency.q` | — |
| Counters | 5 s | DB | — |
| FE heartbeat | 0.5 s | — | sends to `fe2hb.q` |

The heartbeat, emergency and counter slots run on the **GUI thread** and do blocking 500 ms Redis reads and DB queries, which can stall the UI [I].

### Cycle record

- Results accumulate in class attributes.
- At state 29, `doFinalProcessing` (:1400) sets overall = all 15 step results OK.
- The record is sent to `fe2db.q` (:1465-1510).
- Because torques are always OK and every camera step must be OK to advance, **overall is effectively always OK unless the cycle was aborted** [I].

### Menus

- Exit, Change Password, Manual Abort.
- User admin.
- Activate or deactivate users.
- Five CSV reports.
- Machine Settings (inserts a `machine_settings` row).
- Help and About.
- An Operator cannot use admin or Machine Settings.
- There is no logout.

---

## 7. Visual checks (`camera/`)

### Pipeline

1. `MonitorGetPicQueue` picks up the take-picture message.
2. `TakePictures` grabs 3 frames, `time.between.pictures` apart.
3. `ConverterScalerAndProcessor` resizes the frames per state and dispatches the check.

There are two dispatch modes:
- **Vote mode.** Used for Knuckle, Split pin and Cap. The check runs on 3 frames (submitted twice each), and **2 of 3** must pass. The `gamma=index+1` argument is ignored by these checks.
- **Single-frame mode.** Used for Hub, Top bearing, Nut, NoCapBunk, NoBunk and Bunk. Only frame 1 is used.

A NOT OK result also sends `alarm.q`, which plays BadComponent.wav.

### State shared across checks

- The component QR code is fetched from `qrcode2camera.q` at the knuckle step. It waits up to 5 s.
- `currentPictures` keeps earlier steps' images for the differential checks.
- `bearingCentreAndRadii` is set by Top bearing and used by Nut.

### The checks

| Check | Verifies | Method | Pass criterion | QR use |
|---|---|---|---|---|
| Knuckle | Knuckle present; side and model match the QR | Classical: grey means of fixed ROIs | ROIs below `common.check.threshold`; side box detected equals the QR side; Step 2/3 polygon mean below or above `component.identification.darkness.threshold` gives DOST or DOSTPLUS. Machine 2 (`side.by.difference`, CHANGES 95): contours A / A' and side boxes D1 / D2 must each differ by more than `side.difference.threshold` and agree on the side | model and side |
| Hub and bottom bearing | Hub seated, bearing centre empty, stud pattern | Gamma 3 annulus mean below 144; then **YOLO ObjectAwareModel + MobileSAM** masks, filtered for a regular K-gon; smart gamma order 1–6 | 4 holes (DOST) or 5 holes (DOSTPLUS), matching the QR model | model |
| Top bearing | Top bearing placed | Classical: 10 gamma/offset branches, ray-trace plus RANSAC circle fit; brightness delta over 30 against the hub image | min score above 0.65, G above 77, delta above 30. **Outputs bearing geometry.** | none |
| Nut and plate washer | Hex nut present; washer covers the bearing ring | NutDetector (YOLO + MobileSAM, hexagon rules); washer by colour difference from the top-bearing image inside the bearing ring | Nut found **and** diffR above 0.55 and diffRB above 5.57 | none (parsed only) |
| NoCapBunk (state 14) / Bunk (state 25) | Bunk placed correctly | YOLO + MobileSAM hole segmentation | DOST: 4 holes in a square. DOSTPLUS: 2 holes 75–100 px apart | model |
| NoBunk (state 17) | Bunk removed | NutDetector only (a visible nut is taken to mean the bunk is gone) | Nut found | none |
| **Split pin and washer** | — | **Stub: returns True whenever a QR code exists** (`CheckSplitPinAndWasher.py:27-29`) | **Always passes** | — |
| Cap | Cap present and the right colour | Brightness delta against the hub, top-bearing and nut images; R−B colour | DOST deltas 30/30/35 and R−B below −1. DOSTPLUS deltas 35/50/35 and R−B above 15 | model |

### Models

- `models/MobileSAM/weights/mobile_sam.pt` and `models/MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt`.
- Uses CUDA if available, otherwise CPU.
- Loaded **lazily** by the `ModelManager` singleton, so the first hub check of the first cycle is slow.
- `HexagonNutDetector.py:123` imports `mobile_sam` **unguarded**: without `models/` the camera server will not start.
- Both `ModelManager` and `HexagonNutDetector` monkey-patch `torch.load(weights_only=False)` globally.

### QR format and model mapping

- The QR is `$`-separated, e.g. `7204838$400112VA1D$…`, and validated against `qrcode.regex.pattern.1-6`.
- The model, side and tonnage come from `qrcode.partmapping.N`, matched as a prefix:

| Prefix | Model | Side | Tonnage |
|---|---|---|---|
| 7204838$400112VA1D | DOST | LHS | 2.0T |
| 7204838$400102VA1D | DOST | RHS | 2.0T |
| 8201206$400112VA1C | DOSTPLUS | LHS | 2.5T |
| 8201206$400102VA1C | DOSTPLUS | RHS | 2.5T |

- Tonnage is not used by any check.

---

## 8. Database (`persistence/`)

### Databases and connection

- Two databases: `ashok_leyland_test` and `ashok_leyland_production`, both with schema `al_hub_and_disc_assembly_schema`.
- Connections use `postgres`@127.0.0.1:5432.
- `persistence/CreateTables.ddl` is stale and unused. The live DDL is in `Persistence.py:399-538`.

### Tables

- **`roles`**: Administrator, Supervisor, Operator.
- **`users`**: passwords stored base64-encoded, not hashed. There are 3 seeded admin users.
- **`hub_and_disc_assembly_data`**: one row per cycle.
  - Header: QR, model, side and tonnage.
  - For each of the 9 vision steps: image **file path**, result and datetime.
  - Torque 1 and torque 2, plus free-rotation torque (value, result, datetime).
  - Rotation, component press and cap press done flags.
  - `ok_notok_result`, `username`, and `remarks` ("DUPLICATE" when the QR was seen before).
  - `qr_code` has **no unique constraint**.
- **`machine_settings`**: append-only history of rotation counts (0–10) and RPM. The latest row wins. There are **no torque limits**.

### Writer (`DBServer.py`)

- Every 5 s it reads `fe2db.q`.
- It saves the 9 PNGs under `{basefolder}{Test|Production}/{N-Step}/{YYYY-MM-DD}/{model}/`. **The date is fixed when the process starts.**
- It inserts the row, then **REINDEXes all 3 indexes after every insert**.

### Other database access

- **IO server:** reads machine settings.
- **QR server:** runs a duplicate check, and **re-runs full DB setup (`setDatabaseName` with `createDB=True`) on every scan**.
- **Frontend:** login, users, reports, counters and settings.

### Backup

- `pg_dump` of the production database to `C:/DatabaseBackup/`, keeping the 5 newest.
- Runs at start and at stop.
- **Not covered:** the test database and the images.

### Endpoints (`OtherServices.py`)

The web service and the MI dashboard query the old `hub_and_disc_assembly_schema` and old column names. **Every data endpoint fails against the current database.**

---

## 9. Findings and risks (ranked)

| # | Severity | Finding | Where | Status |
|---|---|---|---|---|
| R1 | **High (safety/quality)** | The split pin and washer check is a stub that always passes | `camera/CheckSplitPinAndWasher.py:27-29` | V |
| R2 | **High** | Heartbeat-driven abort disabled (`currentStatus = True`); a dead camera, QR or DB server never stops the cycle | `IOServer.py:960-965` | V |
| R3 | **High** | Camera results are not matched to the state. IO accepts whatever is on `camera2io.q` (`readDataInIOServerFromCameraServer` ignores the returned state, `IOServer.py:1140`), and the camera is never told about an abort. A check still running at E-stop can post its result **after** the queue clear; that result is then consumed at the next WRITE state of the following cycle (the knuckle step) | `IOServer.py:1140-1167`, `RedisUtils.py:2375` | I (race; timing-dependent) |
| R4 | **High** | No timeout in WRITE states. If the camera sends nothing or a result tagged `INVALID_STATE` (e.g. camera disconnected: `TakePictures` returns an INVALID item, `CameraProcessorServer.py:257-259`), IO neither advances nor retries and waits forever. `MAX_SECONDS_TO_WAIT_FOR_CAMERA_FEEDBACK` is defined but unused (and, inside an IntEnum, it silently becomes an alias of state 5) | `IOServer.py:1161`, `StateMachine.py:38` | V |
| R5 | **High** | Torque is never judged: no limits anywhere, and `result` is always ok | `IOServer.py:1315,1334,1353` | V |
| R6 | High | The frontend's own state machine ignores `moveToState`/`action` and the camera-reported state, so it can drift from IO (Manual Abort guarantees drift) | `AshokLeylandFrontEnd.py:1058-1071, 1700, 1825` | V |
| R7 | High | Manual Abort from the frontend does not reach IO or the PLC; closing the confirm dialog counts as confirming | `AshokLeylandFrontEnd.py:1058`, `SimplePopups.py:281` | V |
| R8 | High | End-of-cycle screen tests the string `finalResult` for truthiness, so it shows "Success" for NOT OK too | `AshokLeylandFrontEnd.py:1456` | V |
| R9 | High | The camera server will not start without `models/` (unguarded import) | `HexagonNutDetector.py:123` | V |
| R10 | Medium | Steps 6 and 15 share `PLC_PC_Station3ValueSet`. Separation relies on the PC resetting it 2 s after step 6 [PLC?] | properties :229, :263; `IOServer.py:1231` | V/PLC? |
| R11 | Medium | Trigger and Done tags are never reset per step (only at cycle end or abort). Retry semantics on NOT OK depend on the PLC; docstring and code disagree | `IOServer.py:781-783, 1386` | V/PLC? |
| R12 | Medium | The washer check samples the top-bearing image in a frame centred on the bearing, but uses a mask built for the nut-centred frame (offset ≈ bearing centre − (632,360)) | `CheckNutAndPlateWasher.py:469-489` | V (impact I) |
| R13 | Medium | QR server re-runs DB create/seed on every scan; REINDEX runs after every insert; the image date folder is fixed at start | `QRCodeScanningServer.py:262`, `Persistence.py:1577`, `Persistence.py:365` | V |
| R14 | Medium | Every queue is latest-value only (`xtrim 1` before read), so bursts lose messages; `io2frontend.q` is not cleared on abort | `RedisUtils.py` | V |
| R15 | Medium | Emergency latency: IO polls every 1 s, FE every 3 s | `IOServer.py:1020`, properties :97 | V |
| R16 | Medium | The Production properties file uses `bung` keys and `PC_PLC_`-prefixed rotation tags; the current file uses `bunk` keys and unprefixed tags. Deploying either file blindly can pick up code defaults or wrong tag names | the two `.properties` files | V |
| R17 | Low | `C:/RHS_Images` debug dump on every RHS no-cap-bunk check; operator-hand detection disabled; FE NOT OK images use the OK display time; duplicate Ctrl+Alt+A/D shortcuts; double teardown on Exit; reporting endpoints broken | various | V |

## 10. Open questions (need the PLC programme or shop-floor knowledge)

1. On NOT OK, does the PLC drop `PLC_PC_Check…` and re-raise it for a retry? Or does it keep it high, so the PC re-shoots continuously? Does the PLC clear `…Done` itself?
2. Is `PLC_PC_Station3ValueSet` intentionally shared between "rotations done" and "torque value set", and is it a pulse or a level?
3. Which rotation tag names does the PLC actually have (with or without `PC_PLC_`)? Which properties file is deployed at Hosur?
4. Does the PLC watchdog `PC_PLC_ConnectionStatus` (the toggle) and stop on loss?
5. Is the split-pin stub deliberate, i.e. waiting for an algorithm such as the prototype in `test/Test_FindSplitPin.py`?
