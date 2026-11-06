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
                                                 │ USB HID
                                            QR code scanner
```

- **Deployment.** One PC per machine, dedicated to that machine. The application is built with Nuitka (`runNuitka.bat`) into an exe under D:\AshokLeyland. It is started by `Start-Hub-And-Disc-application - Production.vbs`.
- **Supporting services.** Redis runs in WSL. PostgreSQL runs on the PC.
- **Development.** Development is on a Windows laptop. The sandbox dry runs use simulators for the PLC, camera and QR reader.

## 3. Process architecture

`MainProgram.py` checks the environment, clears the Redis queues, checks Redis (§7), and then starts seven processes. The ProcessSupervisor restarts any that die, up to `supervisor.max.restarts.per.hour`.

| # | Process | Module | Responsibility |
|---|---|---|---|
| P1 | Logging server | `logutils` | Central file and console logging, fed through Redis queues `filelogging` and `consolelogging` |
| P2 | Camera server | `camera/CameraProcessorServer.py` | Takes the picture on request. Runs the vision check in a VisionCheckWorker child process (12 s deadline). Answers the IOServer and sends images to the frontend |
| P3 | QR code server | `costhetaio/qrcodescanner/QRCodeScanningServer.py` | Reads the scanner when the IOServer asks. Validates the code (and checks for a duplicate in the database). Sends it to the IOServer, frontend and camera |
| P4 | IOServer | `costhetaio/commonplc/PLCIOServer.py` | Owns the machine state machine (28 states) and all PLC reads and writes. Writes the settings. Applies the server-down rules |
| P5 | DB server | `persistence/DBServer.py` | Writes one row per component. Creates the day folders for images |
| P6 | Frontend | `frontend/AshokLeylandFrontEnd.py` (PySide6) | Operator screen with 15 steps, settings, reports, abort |
| P7 | Heartbeat and alarm server | `monitorAllConnections/HeartbeatAndAlarmServer.py` | Collects heartbeats. Sends the per-server status to the IOServer and frontend. Sounds the siren. Checks Redis every minute |

All processes are Python `multiprocessing.Process` children of MainProgram. They share nothing in memory. **Redis is used only for communication between processes**; data used inside one process stays in memory variables.

## 4. Communication

### 4.1 Redis streams (queues)

Each queue has one producer and one consumer.

| Queue | From → to | Carries |
|---|---|---|
| `io2qrcode.q`, `io2qrcodeabort.q` | IO → QR | Arm the QR read; abort it |
| `qrcode2io.q`, `qrcode2fe.q`, `qrcode2camera.q` | QR → IO / FE / camera | QR code and status |
| `io2camera.q` | IO → camera | Take picture for state N, with request id |
| `camera2io.q`, `camera2fe.q` | Camera → IO / FE | Result (and images, to FE), with request id |
| `io2frontend.q` | IO → FE | State, move-to state, torque values, results |
| `fe2io.q` | FE → IO | Abort with reason |
| `fe2db.q` | FE → DB | Completed component record |
| `*2hb.q`, `alarm.q` | All → heartbeat server | Heartbeats; alarms |
| `hb2fe.q`, `hb2io.q` | Heartbeat server → FE / IO | Per-server status, including redis |
| `emergency.q` | IO → FE | Emergency abort |
| `stopcommandQ`, `stoppedresponseQ` | MainProgram ↔ all | Orderly stop |

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

## 6. Failure handling (summary)

| Failure | Detected by | Behaviour |
|---|---|---|
| PLC unreachable | IOServer connection check (1 s) | Reads and writes stop; reconnect; frontend shows disconnected |
| PLC restart | Side tag reset to 0 by the PLC | Cycle restarts from the PLC side |
| Emergency button | `PLC_PC_EmergencyAbort` | Abort the cycle, go to the QR read |
| Camera down or slow | Heartbeat; 20 s answer timeout; 12 s worker deadline | NOT OK; the worker is restarted |
| QR server down | Heartbeat | Nothing is done to the PLC |
| DB server down | Heartbeat | Finish the cycle; hold the next one, with a notice |
| Frontend or heartbeat server down | Heartbeat / combined heartbeat | Abort and hold |
| A process dies | ProcessSupervisor | Restart it (rate limited). A restarted IOServer aborts the frontend and camera |
| Redis not storing | Heartbeat server, every 60 s (10 s while failing); MainProgram at start | Siren and console message; hold the next cycle; refuse to start |
| Settings write fails | Read-back | The earlier settings stay in force; the side is still required |

**Limitation of the Redis check.** When Redis stops storing, the status messages that carry "redis down" cannot be delivered either. What the check really gives is:

- the refusal to start;
- the heartbeat server's siren and console log;
- the hold, if Redis comes back partly working.

## 7. Configuration

- **One live properties file**, `ApplicationConfiguration.properties`, read by `Configuration.py` (`CosThetaConfigurator`). Some keys apply live without a restart.
- **`ApplicationConfiguration.Machine1.properties` and `.Machine2.properties`** are build-time copies used by `runNuitka.bat`. They differ only in `plc.vendor`.
- **`costhetaio/siemens/IOServerProperties.yaml`** holds the Siemens rack, slot and PDU size, and the tag addresses.
- **`camera/VisionConfig.py`** holds the vision thresholds.

## 8. Data

- **PostgreSQL**, schema `al_hub_and_disc_assembly_schema`, with tables:
  - `hub_and_disc_assembly_data`: one row per component.
  - `machine_settings`: append-only; the latest row is in force.
  - `users` and `roles`.
- **Images** are stored in day folders under the images root. The DB server creates the folder each day.
- **Logs** go under `logs/`, with each process's level set in `logutils/LogSettings.py`.

## 9. Quality and testing

- Dry runs for each machine are in `test/machine1_dryrun` and `test/machine2_dryrun`. They use a PLC simulator (`_plc_sim.py`), a camera simulator and a QR simulator, with real Redis and PostgreSQL. They cover:
  - fault scenarios on both vendors;
  - the integrated cycle down to the DB row;
  - the frontend, DB, heartbeat and startup, knuckle, vision timeout and supervisor.
- `Verify_Vision_Config_Equivalence` proves that vision refactors do not change behaviour.

## 10. Planned architecture work

These items are under discussion; see the reply on the architecture review.

1. **PLC protocol v2.** Single-owner tags with request and acknowledge (`PLC_Interface_Spec.md` Part B).
2. **One source of truth for the cycle state.** The IOServer publishes `io:state` on every state change. The frontend maps the 28 states to its 15 steps and resyncs if it drifts.
3. **Correlation ids.** Limited to IOServer, frontend and camera: a cycle id plus the existing picture request id.
4. **Split PLCIOServer** (3 000 lines) into mixin modules under `costhetaio/commonplc/ioserver/`. This is a pure move, checked by the fault scenarios and a comparison of the write traces.
