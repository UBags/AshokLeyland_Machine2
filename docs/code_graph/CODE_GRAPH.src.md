# Code graph — AshokLeyland Machine 2

**Must read before changing code.** Traced from branch `machine2-io-fe-db-cam` at commit `a33aad1` (2026-11-19,
CHANGES 167; module counts and diagrams 1 and 6 refreshed at CHANGES 175). Built from the code itself (AST import walk from the two entry points, and a scan of every
`send…` / `read…` call in `utils/RedisUtils.py`), then checked against `docs/HLD.md` §3–5 and `docs/LLD.md`.

Each diagram is below as Mermaid (GitHub renders it) and as an SVG in this folder (`docs/code_graph/*.svg`) for
zooming. The `.mmd` files are the sources.

| # | Diagram | Answers |
|---|---|---|
| 1 | Runtime topology | What starts what, which process touches which device / store |
| 2 | Redis wiring | Who sends what to whom (every stream and key) |
| 3 | Camera server and vision checks | Picture request → worker → which check, and what each check depends on |
| 4 | IOServer internals | The threads, the reset routine, the PLC access layer |
| 5 | Cycle state machine | The 28 states, retakes, the bypass |
| 6 | Module import graph | Every live module and its local imports |

## 0. Live code vs the rest

- **Entry points:** `MainProgram.py` (the station; built to `MainProgram.exe`) and `OtherServices.py` (web service, MI
  dashboard, screen share; built to `OtherServices.exe`). Both are started by
  `Start-Hub-And-Disc-application - Production.vbs`.
- **Live:** 88 of 389 Python modules are reachable from those two (imports at module level and inside functions).
- **Not reachable (301, as of CHANGES 175):** `test/machine2_dryrun` 70, `test/machine1_dryrun` 66, `archive/` 46, other `test/` 41,
  leftovers in `frontend/` (old widgets), `costhetaio/` (`IOServer_ModbusTCP.py`, `siemens/test`), `processors/`,
  `utils/`, `video/`, `camera/` (`CameraRecorderServer`, `HandDetector`, `VideoCamera`, `ONVIFCam`, `PhotoCamera`),
  `mail/`, root scripts (`main.py`, `definition.py`, `VerifyIntegrity.py`, `test-*.py`, `verify_installation.py`).
  Not reachable ≠ delete: the dry runs and several kept methods are deliberate (CLAUDE.md §5).

**Hub modules** — imported almost everywhere, so they are left out of diagram 6 to keep it readable:

| Module | Imported by (live modules) | Lines | Role |
|---|---|---|---|
| `Configuration` | 46 | 3,568 | `CosThetaConfigurator` singleton: properties file, `getTuning`, vendor prefixes |
| `BaseUtils` | 31 | 557 | Shared helpers |
| `logutils.SlaveLoggers` / `logutils.Logger` | 30 / 29 | 235 / 157 | Per-process loggers that push to the logging server through Redis |
| `utils.RedisUtils` | 21 | 2,898 | **All** inter-process messaging: `send<What>From<A>To<B>` / `read<What>In<B>From<A>`, queue names, keys |
| `utils.SuppressedExceptions` | 18 | 64 | Logged exception swallowing |
| `utils.CosThetaPrintUtils` | 16 | 255 | Coloured console output |
| `camera.VisionConfig` | 15 | 299 | `vInt` / `vFloat` / `vTuple` / `vPoints` ← `<vendor>.vision.*` keys |
| `utils.QRCodeHelper` | 13 | 106 | QR parsing, model / side / tonnage mapping |
| `logutils.LogSettings` | 11 | 211 | Log levels and process names |
| `utils.CosThetaFileUtils` | 10 | 422 | Paths and folders |
| `statemachine.StateMachine` | 6 | 260 | `MachineState` (28 states) and `MachineStateMachine` |
| `processors.GenericQueueProcessor` | 4 servers | 241 | Base class of the server threads (stop-queue polling) |

Largest live files: `HubAndBearingSegmenter` 3,734 · `PLCIOServer` 3,323 · `HexagonNutDetector` 2,952 ·
`Persistence` 2,985 · `RedisUtils` 2,898 · `utils.ImageNormalisationWithMask` 2,659 · `AshokLeylandFrontEnd` 2,453 ·
`CheckTopBearing` 2,216 · `BungSegmenter` 2,048 · `SimplePopups` 2,025.

## 1. Runtime topology

@@1_runtime@@

- MainProgram start order: logging → (backup, mode, login) → camera, QR, IO, frontend, heartbeat, DB; then
  `ProcessSupervisor` watches them (every 2 s; at most 10 restarts per server per hour; nothing restarted once a stop
  command is on the stop queue). A restarted camera server reloads `Temp/camera_server_state.pkl`.
- Only the IOServer talks to the PLC; only the camera server to the camera; only the QR server to the scanner.
- The DB is touched by several processes (diagram), but **cycle records are written only by the DB server**, from
  `fe2db.q`.

## 2. Redis wiring

@@2_redis@@

Derived from the call sites (comments ignored):

- One producer and one consumer per stream, except `alarm.q` (camera + QR) and `camera2fe.q`, which the **IOServer
  also writes** when the camera is down or misses its deadline (`PLCIOServer._pictureNotOkWithoutAnswer`, with a new
  request id so a late camera answer is not shown).
- `stopcommandQ` is sent by MainProgram and by the frontend (`FrontEndLogger`), read by every server (mostly through
  `GenericQueueProcessor`); `stoppedresponseQ` back from camera, IO, QR and DB (the DB server only after draining
  `fe2db.q`).
- `fe2db.q` is FIFO with explicit acknowledgement (`acknowledgeRecordInDatabaseServer`); the others are read as
  newest-message mailboxes.
- On abort, `clearKeyCommunicationQueuesOnAbort` (IO) clears `io2camera`, `io2qrcode`, `qrcode2io`, `qrcode2fe`,
  `qrcode2camera`, `camera2io`, `camera2fe`.
- `cycleSettings:<QR>` is written only by the IOServer (`publishCycleSettingsForQRCode`); the frontend and the DB
  server read it. (HLD §4.2 says "FE/IO" as writer.)
- `log:cycleContext` and `ioserver:started` are set outside `RedisUtils`; listed from HLD §4.2.

## 3. Camera server and vision checks

@@3_camera@@

- Threads in `CameraProcessorServer`: `MonitorGetPicQueue` → `TakePictures` → `ConverterScalerAndProcessor`, each a
  `GenericQueueProcessor`. The check itself always runs in the `VisionCheckWorker` child process (no thread
  fallback since CHANGES 105); `assessPictures(state, images)` dispatches by `MachineState`.
- `S<n>` = Machine 2 state number of the READ state that asks for the picture.
- Cross-check coupling to know before editing:
  - `CheckNutAndPlateWasher` uses the top-bearing ring from `CheckTopBearing` (stored in `ImageProcessor`).
  - `CheckNoCapBung` imports `CheckBung`; both use `BungSegmenter`.
  - `FindHubHoles` (cap, bung, split pin) reuses `checkHub()`'s steps from `HubAndBearingSegmenter` on its own
    per-thread object — **do not change `checkHub()`** (CLAUDE.md §5). Its `sam` method (CHANGES 169) needs this
    cycle's hub-check centre (`ImageProcessor.hubCentre`, set by the hub check, read through `hubCentreProvider`).
  - Model users (`ModelManager`, lazy YOLO + MobileSAM): hub, nut / no-bung (`HexagonNutDetector`), knuckle locator,
    `FindHubHoles` (sam), cap.

## 4. IOServer internals

@@4_ioserver@@

- Thread names as in the code (`threading.Thread(name=…)`): Continuous Read Loop, Continuous Do Loop, IOServer
  Connection Keep Alive, Emergency Monitoring, PC Connection Status Toggle, IO Heartbeat, Combined Heartbeat Update,
  Frontend Abort Listener, Monitor Stop, Tag Request Listener; plus `UpdateTagsToDefaultProcessor` and the
  `DelayedJobScheduler` for timed resets.
- Vendor chosen by `plc.vendor` through `PLCDataIOFactory`; Siemens addresses in
  `costhetaio/siemens/IOServerProperties.yaml`. Tags and timings: `docs/PLC_Interface_Spec.md` (fixed by the PLC
  design). Class detail: `docs/costhetaio_classes.svg`.

## 5. Cycle state machine (Machine 2, 28 states)

@@5_cycle@@

- READ states wait for a PLC trigger (or a value); WRITE states write the result handshake (`…OK`, then `…Done`).
- A vision NOT OK returns to the READ state and the PLC asks for a retake; the cycle never ends on a NOT OK.
- With `PC_PLC_Skip_Second_Tightening` written True in state 2, states 13–19 are skipped and recorded `Bypassed`.
- At state 28 → 1: reset every per-cycle tag (retries + read-back), arm `PLC_PC_CheckQRCode`, `END_OF_CYCLE` to the
  camera; the frontend sends the record on `fe2db.q`. The frontend keeps its own copy of the state machine; Plan A
  state sync compares it with `io2festate.q` (`docs/state_sync.md`).

## 6. Module import graph (live modules, hubs omitted)

Solid arrow = module-level import; dotted = import inside a function (lazy / process entry). Lines = file length.

@@6_modules@@

## 7. Regenerating

From the repo root (needs only the standard library):

```
python3 docs/code_graph/tools/graph.py .              # reachability + edges -> docs/code_graph/tools/graph.json
python3 docs/code_graph/tools/mermaid_modules.py > docs/code_graph/6_modules.mmd
python3 docs/code_graph/tools/redisgraph.py .         # send/read function -> queues -> calling process
python3 docs/code_graph/tools/smoke.py                # import every live module (needs the venv; from repo root)
python3 docs/code_graph/tools/build_doc.py            # CODE_GRAPH.src.md + *.mmd -> CODE_GRAPH.md
```

Diagrams 1–5 are hand-written from the code; update them when a process, queue, thread, check or state changes.
SVGs: `npx @mermaid-js/mermaid-cli -i X.mmd -o X.svg -b white`.

Sandbox install check (2026-11-19, Linux, Python 3.11 venv from `requirements.txt` minus Windows / CUDA-only
packages, torch 2.11 from PyPI, MobileSAM from GitHub): 73 of the 85 importable live modules import; the other 12 are
the 11 tkinter users in `frontend/` (no tkinter in that Python) and `HeartbeatAndAlarmServer` (`winsound`,
Windows-only). `MainProgram` / `OtherServices` were not imported (they connect to Redis at import time).
