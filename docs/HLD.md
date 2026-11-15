# Machine 2 (Hub and Disc Assembly): High-Level Design

| | |
|---|---|
| Document | High-level design (HLD) |
| Companion documents | `LLD.md` (low-level design), `PLC_Interface_Spec.md` (PLC tags and handshakes), `../CHANGES_MACHINE2.md` (change history) |
| Machines | Machine 2 (Siemens S7-1515). The same code base runs Machine 1 (Allen-Bradley); the only difference is `plc.vendor` |

## 1. Purpose

The PC system checks each hub-and-disc assembly at Ashok Leyland, step by step, while the PLC runs the machine:

- It reads the component's QR code.
- It writes the cycle settings to the PLC.
- It answers the PLC's vision-check requests from camera pictures.
- It records torques and press events.
- It shows everything to the operator and stores one database row per component.

The PLC decides machine motion and torque OK or NOT OK. The PC decides vision OK or NOT OK and keeps traceability.

## 2. Context

```
                 ┌────────────── Operator ──────────────┐
                 │ (screen, QR scanner, emergency button)│
                 └───────────────┬───────────────────────┘
                                 │
 ┌──────────┐   S7 / EtherNet/IP  ┌──────────────────────────────────┐   RTSP/ONVIF   ┌────────┐
 │   PLC    │◀───────────────────▶│ Inspection PC (Windows, D:\AshokLeyland, exe)  │◀──────────────▶│ Camera │
 └──────────┘                     │  Redis (WSL)  ·  PostgreSQL  ·  GPU     │                └────────┘
                                  └──────────────────────────────────┘
                                                 │ serial (COM3, 38400 baud)
                                            QR code scanner
```

- **Deployment.** One PC per machine, dedicated to that machine. The application is built with Nuitka (`runNuitka.bat`) into an exe under D:\AshokLeyland. It is started by `Start-Hub-And-Disc-application - Production.vbs`.
- **Supporting services.** Redis runs in WSL. PostgreSQL runs on the PC.
- **Development.** Development is on a Windows laptop. The sandbox dry runs use simulators for the PLC, camera and QR reader.

## 3. Process architecture

`MainProgram.py` checks the environment, clears the Redis queues, checks that Redis stores data (§6), backs up the production database, asks for the mode and the login, and then starts seven processes. The ProcessSupervisor restarts any that die, up to `supervisor.max.restarts.per.hour`.

| # | Process | Module | Responsibility |
|---|---|---|---|
| P1 | Logging server | `logutils` | Central file and console logging, fed through Redis queues `filelogging` and `consolelogging` |
| P2 | Camera server | `camera/CameraProcessorServer.py` | Takes the picture on request. Runs the vision check in a VisionCheckWorker child process (12 s deadline). Keeps the cycle's state for the checks (QR code, earlier pictures, top bearing ring, the knuckle frame). Answers the IOServer and sends images to the frontend |
| P3 | QR code server | `costhetaio/qrcodescanner/QRCodeScanningServer.py` | Reads the scanner when the IOServer asks. Validates the code (and checks for a duplicate in the database). Sends it to the IOServer, frontend and camera |
| P4 | IOServer | `costhetaio/commonplc/PLCIOServer.py` | Owns the machine state machine (28 states) and all PLC reads and writes. Writes the settings. Applies the server-down rules |
| P5 | DB server | `persistence/DBServer.py` | Writes one row per component. Creates the day folders for images |
| P6 | Frontend | `frontend/AshokLeylandFrontEnd.py` (PySide6) | Operator screen with 15 steps, settings, reports, abort. Passwords (abort, machine settings, bypass) from the properties file |
| P7 | Heartbeat and alarm server | `monitorAllConnections/HeartbeatAndAlarmServer.py` | Collects heartbeats. Sends the per-server status to the IOServer and frontend. Sounds the siren. Checks Redis every minute |

All processes are Python `multiprocessing.Process` children of MainProgram. They share nothing in memory. **Redis is used only for communication between processes**; data used inside one process stays in memory variables.

## 4. Communication

### 4.1 Redis streams (queues)

Each queue has one producer and one consumer.

| Queue | From → to | Carries |
|---|---|---|
| `io2qrcode.q`, `io2qrcodeabort.q` | IO → QR | Arm the QR read; abort it |
| `qrcode2io.q`, `qrcode2fe.q`, `qrcode2camera.q` | QR → IO / FE / camera | QR code and status |
| `io2camera.q` | IO → camera | Take picture for state N, with request id; END_OF_CYCLE (completed / aborted) when the machine returns to READ_QR_CODE |
| `camera2io.q`, `camera2fe.q` | Camera → IO / FE | Result (and images, to FE), with request id |
| `io2frontend.q` | IO → FE | State, move-to state, torque values, results |
| `fe2io.q` | FE → IO | Frontend abort request (password-protected Ctrl+Alt+Z): the IOServer aborts only if it can set `PC_PLC_CycleAborted`; `Abort` (test tools): abort at once |
| `fe2db.q` | FE → DB | Completed component record |
| `*2hb.q` | Each server → heartbeat server | Heartbeats (ALIVE / DEAD) |
| `hb2fe.q`, `hb2io.q` | Heartbeat server → FE / IO | Per-server status, including redis |
| `emergency.q` | IO → FE | Emergency abort (also sent by a restarted IOServer) |
| `alarm.q` | Camera → heartbeat server | Bad component: the siren / bad-component sound |
| `stopcommandQ`, `stoppedresponseQ` | MainProgram ↔ all | Orderly stop |
| `publishTags`, `receiveTags` | Test windows ↔ IO | Dump of every PLC tag's value (diagnostics) |

### 4.2 Redis keys (not queues)

| Key | Writer → reader | Use |
|---|---|---|
| `cycleSettings:<QR>` | FE/IO | Settings written for that component (2-day expiry) |
| `log:cycleContext` | IO → loggers | Cycle context in log lines (6 h expiry) |
| `camera:activeRequest` | IO → camera, FE | The one picture request that is current |
| `feNotice:<name>` | IO → FE | Top-right notices such as "Continue till end of cycle" |
| `ioserver:started` | IO | Lets a restarted IOServer know it is a restart |
| `redis.healthcheck.q` | Heartbeat server, MainProgram | Write and read-back check. Not used by anything else |

### 4.3 PLC

The PLC is reached only by the IOServer, through `PLCDataIO` (Siemens pyS7 or Allen-Bradley pycomm3, chosen by `plc.vendor`). Tags, handshakes and timings are in `PLC_Interface_Spec.md`.

## 5. The cycle

```
QR read ─▶ settings to PLC ─▶ knuckle ─▶ hub+bottom bearing ─▶ top bearing ─▶ nut+plate washer
   ─▶ torque 1 ─▶ bunk for component press ─▶ component press ─▶ no bunk ─▶ torque 2
   ─▶ split pin+washer ─▶ cap ─▶ bunk for cap press ─▶ cap press ─▶ free-rotation torque ─▶ DB row ─▶ QR read
```

- The IOServer walks 28 states: a read state and a write state for each check, plus the value and event states.
- The frontend shows 15 operator steps.
- A vision NOT OK goes back to the same check, and the PLC asks for a retake. The cycle does not end on a NOT OK.
- The record written at the end carries every result, image file, torque, the settings used and the user.
- **Bypass (per model, Machine Settings):** with `PC_PLC_Skip_Second_Tightening` True written in state 2, the PLC goes
  from torque 1 straight to the split pin check; the PC skips states 13–19 and records those steps as `Bypassed`.

## 5a. Vision checks

All pictures are resized to 1280 × 720 before a check. The camera server takes three pictures per request; each check
runs in the vision check worker process (`camera/VisionCheckWorker.py`) within `<vendor>.vision.check.timeout.sec`
(12 s). Check modules are in `camera/`; their parameters are the `<vendor>.vision.*` keys of the properties file.

| State | Check | Method (Machine 2) | Pictures used |
|---|---|---|---|
| 3 | Knuckle (`CheckKnuckle`) | `vision.knuckle.method=segmentation`: MobileSAM (`KnuckleLocator`) finds the spindle, the two machined bosses with the fixture pins in them and the lower arm on the back rest; type from the boss size against the pin distance, side from the lower arm; both must match the QR code. Builds the cycle's **frame** (below). Machine 1: `regions` (grey levels of fixed regions) | one picture (Machine 1: 2 of 3) |
| 5 | Hub and bottom bearing (`CheckHubAndBottomBearing`) | MobileSAMv2 (YOLO + SAM) shapes in a ring round the hub centre; 4 evenly spaced holes = DOST, 5 = DOSTPLUS, must match the QR code. The "no bearing in the bore" test is off in production | one |
| 7 | Top bearing (`CheckTopBearing`) | OpenCV ring finder (gamma branches, RANSAC circle fit, ring refinement); bearing size limits; ring brighter than in the hub picture. The ring is kept for the nut / washer check | one |
| 9 | Nut and plate washer (`CheckNutAndPlateWasher`) | Hexagon nut (MobileSAM masks, hexagon and size tests; ring fallback); washer by the colour change on the bearing race against the top bearing picture (Machine 2: red only). Bearing-ring guard: a bearing-sized ring on the nut picture = NOT OK (run in the camera server beside the worker) | one |
| 13, 24 | Bung for component press / cap press (`CheckNoCapBunk`, `CheckBunk`) | YOLO + MobileSAM holes: DOST 4 in a square, DOSTPLUS 2 at the configured spacing; then (Machine 2) seating: hole-pattern centre at the seated position and a round top-face mask | one |
| 16 | No bung (`CheckNoBunk`) | Nut found by the hexagon nut detector | one |
| 20 | Split pin and washer | Placeholder: OK whenever a QR code is known (on hold until production pictures exist) | 2 of 3 |
| 22 | Cap (`CheckCap`) | Which cap (brass / silver / none) by colour and brightness; must be the model's; brightening against the hub, top bearing and nut pictures; then (Machine 2) seating by MobileSAM | steps before seating on 3, 2 must pass; seating once |

**Frame (Machine 2, `camera/VisionFrame.py`).** From the two pins the knuckle check works out the camera's shift,
rotation and zoom against the reference position (13 Nov 2026), and places every later check's centre (hub, top
bearing, nut, no-bung, bung, cap) for this cycle, per type (DOST / DOSTPLUS). A camera movement over
`vision.knuckle.frame.max.*` (40 px, 3°, 5 %) makes the knuckle check NOT OK ("set the camera again"). Without a frame
(Machine 1, or the regions knuckle method) the centres in the properties file are used. A camera server restarted
by the supervisor during a cycle takes the frame back from its state file (§6). The frame is
dropped at the end of every cycle (completed or aborted), at a new cycle's knuckle check and at a knuckle retake.
The reference values (pins, spindles and centres per type: `<vendor>.vision.frame.*`; the locator's rules:
`<vendor>.vision.knuckle.locator.*`) are in the properties file since CHANGES §105 (allenbradley carries the Machine 2
values until Machine 1 is measured). Positions and offsets: `vision_positions_machine2.md`.

No values or words are written on the pictures sent to the frontend (outlines only; a picture NOT OK without a check's verdict - timeout, stalled camera - gets a red border); the values and reasons go to the log.

## 6. Failure handling (summary)

| Failure | Detected by | Behaviour |
|---|---|---|
| PLC unreachable | IOServer connection check (1 s) | Reads and writes stop; reconnect; frontend shows disconnected |
| PLC restart | After a reconnect: the last PLC_PC tag the PLC raised is no longer True; when none is known to be True, the side tag no longer matches the value written in state 2 (the PLC sets it to 0) | Abort the cycle and reset |
| Emergency button | `PLC_PC_EmergencyAbort` | Abort the cycle, go to the QR read |
| Camera down or slow | Heartbeat; 20 s answer timeout; 12 s worker deadline | NOT OK; the worker is restarted; 3 timeouts in a row are reported as checks too slow on this PC |
| Camera stream stalled | No frame newer than the picture request within `camera.frame.max.wait.sec` (1 s) | NOT OK, "camera stream stalled"; the check is not run on the old frame |
| QR server down | Heartbeat | Nothing is done to the PLC |
| DB server down | Heartbeat | Finish the cycle; hold the next one, with a notice |
| Frontend or heartbeat server down | Heartbeat / combined heartbeat | Abort and hold |
| A process dies | ProcessSupervisor | Restart it (rate limited). A restarted IOServer aborts the frontend and camera |
| A work thread dies or is stuck (camera assessment, DB inserts, IOServer loops, frontend GUI, QR reader) | That server's heartbeat: DEAD after `heartbeat.work.loop.max.silence.sec` (60 s) | As for that server down |
| Redis not storing | Heartbeat server, every 60 s (10 s while failing); MainProgram at start | Siren and console message; hold the next cycle; refuse to start |
| Settings write fails | Read-back | The earlier settings stay in force; the side (and the skip tag, where the PLC has it) is still required |
| Camera moved | Knuckle check: camera movement from the pins beyond `vision.knuckle.frame.max.*` | Knuckle NOT OK, "set the camera again" |
| Camera server restarted during a cycle | ProcessSupervisor | The new camera server takes the cycle's state (QR code, earlier pictures, top bearing ring, knuckle frame) from `<project root>/Temp/camera_server_state.pkl`, written after every check; a file older than `camera.state.file.max.age.sec` is not used |
| Vision check worker not ready / stopped | `VisionCheckWorker` | NOT OK ("vision check worker starting") until the new worker is ready |
| Bearing-ring guard cannot decide (ring finder fails, or no answer 5 s after the check) | Camera server | Nut / washer NOT OK with the reason |

**Limitation of the Redis check.** When Redis stops storing, the status messages that carry "redis down" cannot be delivered either. What the check really gives is:

- the refusal to start;
- the heartbeat server's siren and console log;
- the hold, if Redis comes back partly working.

## 7. Configuration

- **One live properties file**, `ApplicationConfiguration.properties`, read by `Configuration.py` (`CosThetaConfigurator`). Some keys apply live without a restart. In the repo it is a copy of the Machine 2 file.
- **`ApplicationConfiguration.Machine1.properties` and `.Machine2.properties`** are build-time copies used by `runNuitka.bat allenbradley|siemens`, which copies the machine's file into the build as `ApplicationConfiguration.properties`. They differ only in `plc.vendor`; per-machine values are kept under `allenbradley.` / `siemens.` prefixes.
- **`costhetaio/siemens/IOServerProperties.yaml`** holds the Siemens rack, slot and PDU size, and the tag addresses.
- **Vision parameters** are the `<vendor>.vision.*` keys, read through `camera/VisionConfig.py` into each check module's constants (the module's defaults are the Machine 1 values). In the exe a changed vision value takes effect after a restart (live reload of vision values needs the .py sources). The knuckle frame's reference values are in the code (§5a).

## 8. Data

- **PostgreSQL**, schema `al_hub_and_disc_assembly_schema`, with tables:
  - `hub_and_disc_assembly_data`: one row per component.
  - `machine_settings`: append-only, per model; a model's latest row is in force for it. It includes the switch
    "Bypass Component Press and Second Tightening" (skips states 13–19; needs `PC_PLC_Skip_Second_Tightening` on the
    PLC, else full run).
  - `users` and `roles`.
- **Images** are stored in day folders under the images root. The DB server creates the folder each day.
- **Logs** go under `logs/` (rotated daily and by size), with each process's levels set by `log.file.level.<server>` / `log.console.level.<server>` in the properties file (applied by `logutils/LogSettings.py`). The PLC audit trail is written whatever the level.

## 9. Quality and testing

- Dry runs for each machine are in `test/machine1_dryrun` and `test/machine2_dryrun`. They use a PLC simulator (`_plc_sim.py`), a camera simulator and a QR simulator, with real Redis and PostgreSQL. They cover:
  - fault scenarios on both vendors;
  - the integrated cycle down to the DB row;
  - the frontend, DB, heartbeat and startup, knuckle, vision timeout and supervisor.
- Vision dry runs and windows run the real checks on the stored pictures (`knuckle_images/`, `hub_images/`, `top_bearing_images/`, `nut_washer_images/`, `cap_images/`, `bunk_images_20261113/`): `DryRun_Knuckle_Frame.py` (knuckle by segmentation and the frame, with simulated camera moves), `DryRun_Cap_Check.py`, `DryRun_Bearing_Ring_Guard.py` and the tkinter windows per check. The model-based ones need the MobileSAM (and YOLO) weights, which are not in git.
- `Verify_Vision_Config_Equivalence` proves that vision refactors do not change behaviour (each vision file against its reference commit, every configurable value replaced by its default).

## 10. Planned architecture work

These items are under discussion; see the reply on the architecture review.

1. **PLC protocol v2.** Single-owner tags with request and acknowledge (`PLC_Interface_Spec.md` Part B).
2. **One source of truth for the cycle state.** The IOServer publishes `io:state` on every state change. The frontend maps the 28 states to its 15 steps and resyncs if it drifts.
3. **Correlation ids.** Limited to IOServer, frontend and camera: a cycle id plus the existing picture request id.
4. **Split PLCIOServer** (3 000 lines) into mixin modules under `costhetaio/commonplc/ioserver/`. This is a pure move, checked by the fault scenarios and a comparison of the write traces.
