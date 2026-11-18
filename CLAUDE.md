# CLAUDE.md — AshokLeyland Machine 2 (Hub and Disc Assembly)

Working notes for Claude (and anyone else) on this repository. Read this first; the design is in `docs/HLD.md` and
`docs/LLD.md`, the PLC interface in `docs/PLC_Interface_Spec.md`, the history in `CHANGES_MACHINE2.md`.

## 1. What this is

PC software for CosTheta's hub-and-disc assembly SPM at Ashok Leyland. The PLC runs the machine; the PC reads the
component's QR code, writes the cycle settings to the PLC, answers the PLC's vision-check requests from camera
pictures, records torques and press events, shows everything to the operator and stores one database row per
component.

- **Machine 2:** Siemens S7-1515 (pyS7). **Machine 1:** Allen-Bradley (pycomm3). One code base; the only build-time
  difference is `plc.vendor`. Per-machine values live under `siemens.` / `allenbradley.` prefixes in the properties.
- Repo: `https://github.com/UBags/AshokLeyland_Machine2.git` (use exactly this URL), working branch
  `machine2-io-fe-db-cam`.
- The code this repo started from differs slightly from what runs in production.

## 2. Where things run

| Place | What is there |
|---|---|
| Laptop (`C:\PythonProjects\AshokLeyland_Machine2`, Windows, PyCharm) | The git repo, the models, the dry runs; builds the exe (`runNuitka.bat siemens` / `allenbradley`) |
| Machine 2 PC (`D:\AshokLeyland\MainProgram.dist\MainProgram.exe`) | The exe only - no repo, no git. Auto-started at logon by `Start-Hub-And-Disc-application - Production.vbs`; `Startup.bat` on the desktop for manual starts. Redis in WSL Ubuntu-22.04 (started with WSL; a scheduled task starts WSL after boot); PostgreSQL on the PC; NVIDIA GPU |
| Claude's sandbox (Linux) | A clone for development and synthetic dry runs (see §9) |

So: repo commands (pull, build) are for the laptop; the plant PC gets a copied exe. The Machine 2 PC runs nothing
else (killing every `cmd.exe` at shutdown is intended) and is not run 24x7.

## 3. Architecture in one screen

`MainProgram.py` checks Redis, backs up the database, asks for the mode and login, then starts seven processes,
supervised by `utils/ProcessSupervisor.py` (restart on death, rate-limited):

| Process | Module |
|---|---|
| Logging | `logutils` |
| Camera server (+ `VisionCheckWorker` child process for every vision check, 12 s deadline) | `camera/CameraProcessorServer.py` |
| QR code server | `costhetaio/qrcodescanner/QRCodeScanningServer.py` |
| IOServer (28-state machine, every PLC read / write) | `costhetaio/commonplc/PLCIOServer.py` |
| DB server | `persistence/DBServer.py` |
| Frontend (PySide6) | `frontend/AshokLeylandFrontEnd.py` |
| Heartbeat and alarm server | `monitorAllConnections/HeartbeatAndAlarmServer.py` |

- **Redis is only for communication between processes** (streams with one producer and one consumer - except
  `alarm.q`, written by the camera and QR code servers; a few keys).
  Data used inside one process stays in memory variables. Queue and key list: HLD §4.
- Send / read functions per queue are in `utils/RedisUtils.py` (`send<What>From<A>To<B>`, `read<What>In<B>From<A>`).
- The camera server keeps the cycle's vision state in `ImageProcessor` (QR code, earlier pictures, top bearing ring,
  knuckle frame), sends it to the worker with each job, and writes it to `<root>/Temp/camera_server_state.pkl` so a
  restarted camera server takes it back.

## 4. Configuration

- One live file, `ApplicationConfiguration.properties`, read by `Configuration.py` (`CosThetaConfigurator`); some
  keys apply live. `ApplicationConfiguration.Machine1.properties` / `.Machine2.properties` are the build copies
  (`runNuitka.bat <vendor>` copies one in). **Keep the three in step**: Machine1 and Machine2 differ only in
  `plc.vendor`; the live file equals Machine2.
- Vision parameters: `<vendor>.vision.*`, declared in each check module through `camera/VisionConfig.py`
  (`vInt`, `vFloat`, `vTuple`, `vPoints`, ...) with the Machine 1 value as the code default. Since CHANGES 105 the
  knuckle frame and locator reference values are keys too (`vision.frame.*`, `vision.knuckle.locator.*`); the
  allenbradley copies hold the Machine 2 values until Machine 1 is measured.
- Named tuning values outside the vision checks (retry counts and gaps, timeouts, poll intervals, ports, font sizes,
  debug switches of the bung checks) are plain keys read through `CosThetaConfigurator.getTuning(key, default)` when
  their module is imported (CHANGES 154; block "TUNING VALUES" of the properties files); the code default equals the
  properties value - `DryRun_Tuning_Keys.py` checks it. Names, keys, states, queue names and labels stay in the code.
- `costhetaio/siemens/IOServerProperties.yaml`: Siemens rack / slot / PDU and tag addresses.
- Frontend languages (CHANGES 156): English, Kannada, Tamil, Hindi on the main screen and its dialogs only; catalogues
  `Internal/locale/<kn|ta|hi>/LC_MESSAGES/frontend.po` (Babel; English text = message id); the choice in
  `Internal/frontend_language.txt` (not in git). Mark new frontend texts with `tr` / `trf` / `N_` / `bind`, then run
  `test/i18n/update_frontend_catalogues.py` and translate the new entries; results, button keys, logs and the DB stay
  English. The Indic translations are drafts awaiting a native speaker's review.
- `cycle.settings.source=database` on both machines: state-2 settings come from `machine_settings` (per model).
- Plaintext credentials (camera, PostgreSQL, default users, passwords `admin123`) are accepted in this private repo.
  Do not flag them or propose moving them.

## 5. Rules for changing code

- **Root-cause fixes, not workarounds.** Diagnose first, say what was found, then fix.
- **Do not delete well-defined methods because they are unused**; keep them (they may be used later).
- **No text or values on vision pictures** sent to the frontend or shown in the test windows - outlines only (green
  OK / red NOT OK); values and reasons go to the log, the terminal or the window's scrollable text box. Test windows
  show each picture's check time.
- **Vision logic changes need a reference commit.** `test/machine*_dryrun/Verify_Vision_Config_Equivalence.py` proves
  that moving values into the properties changed nothing: each vision file is compared (AST) with its reference
  commit after every `vX(...)` call is replaced by its default, and the allenbradley value must equal the default.
  So when a vision file's *logic* changes:
  1. commit the new logic with the values hard-coded (the "reference commit");
  2. point that file's entry in `baseline_per_file` of **both** `vision_config_manifest.json` files at it;
  3. make the values configurable; the verifier must say ALL EQUIVALENT on both machines.
- **Dry-run copies live in both** `test/machine1_dryrun` and `test/machine2_dryrun` (identical apart from paths;
  most set `COSTHETA_PLC_VENDOR` from their folder name; `DryRun_Vision_Check_Timeout.py` and
  `DryRun_IOServer_Fault_Scenarios.py` on Machine 1 do not - run them with `COSTHETA_PLC_VENDOR=allenbradley`, else
  they run as Siemens).
- "Bung", not "bunk", everywhere (CHANGES 125). The only "Bunk" left on purpose: the PLC's own tag names (values of
  the 9 `*.tagname` keys, the keys of `IOServerProperties.yaml`, their defaults in `Configuration.py`), the legacy
  `IOServer_ModbusTCP.py`, `archive/`, and the history in `CHANGES_MACHINE2.md` before §125. Old `bunk` properties keys
  are still read (`_addLegacyBungKeys`); old `bunk` DB columns are renamed at start-up.
- PLC tag prefixes `PC_PLC_` / `PLC_PC_` show the main writer, not exclusive ownership. The handshake (`docs/PLC_Interface_Spec.md`) is fixed by
  the PLC design: the PC also resets some PLC_PC tags, with set timings. Do not propose changing it (a single-writer
  request / acknowledge "v2" was dropped on 2026-11-15).
- Torque OK / NOT OK is the PLC's decision; the PC only records the values.
- The split pin + washer check stays a placeholder (OK whenever a QR code is known) until production pictures exist.
- IOServer reset rule: at start, cycle end and every abort one routine resets every per-cycle tag (not the settings)
  with retries and read-back, then arms `PLC_PC_CheckQRCode`. After a lost PLC connection: the last PLC_PC tag the
  PLC raised still True means carry on; otherwise (fallback: the side tag no longer as written) the PLC restarted ->
  reset. Both PLCs reset all values on restart.
- The vision check always runs in the worker process (no thread fallback since CHANGES 105).

## 6. Documentation and history

- Every change gets a numbered section at the end of `CHANGES_MACHINE2.md` (`## N. Title (date)`), saying what
  changed, why, what was measured and what was run.
- Keep `docs/HLD.md`, `docs/LLD.md`, `docs/PLC_Interface_Spec.md`, `docs/state_sync.md`, `docs/plant-pc-verification.md`,
  `docs/vision_positions_machine2.md` and the two `test/machine*_dryrun/README.md` in step with the code.
- The claude.ai Project "Ashok Leyland - Machine 2" holds copies (`claude/HLD.md`, `claude/LLD.md`,
  `claude/PLC_Interface_Spec.md`, `claude/state_sync.md`, `claude/plant-pc-verification.md`,
  `claude/vision_positions_machine2.md`,
  `claude/machine2-changes-log-81-onwards.md` = CHANGES from §81 with a two-line header). Refresh them after a push.
- Commits end with the attribution lines the session asks for; push to `machine2-io-fe-db-cam`.

## 7. Plant PC verification (`docs/plant-pc-verification.md`)

| Step | Status |
|---|---|
| 1 Vision check worker inside the exe | **Passed** - run on the Machine 2 PC, reported by Uddi on 2026-11-15: the worker starts and answers inside the Nuitka exe |
| 2a Check times against the 12 s timeout (GPU) | **Passed** - same run: response times fast, well inside the timeout |
| 3 Process restart (each server killed), camera / frontend killed during a cycle | Not reported yet |
| 4 Frozen screen | Not to be provoked |
| 5 Real QR codes | Not reported yet |
| 6 Knuckle check by segmentation and the frame | Not reported yet |
| 7 State sync logs (after a few weeks of production) | Pending - Plan A went in on 2026-11-15 |
| 8 Picture clean-up lines, invalid QR notice + siren, MainProgram lines in the log, DB retry (CHANGES 110) | Not reported yet |
| 9 Bung check: rotation dry run on the laptop, failing bungs now OK, `[Bung]` lines, check time (CHANGES 115) | Not reported yet |
| 10 Cap seating: seated brass / silver OK, tilted / pushed-aside silver NOT OK, seating lines, check time; hub-centre distance lines (CHANGES 128, 129, 143) | Not reported yet |
| 11 Frontend languages: the four languages, restart keeps the choice, DB / log English, native-speaker review of the translations (CHANGES 156) | Not reported yet |

No per-check times were recorded with the report; when they are, put them in the results table of the document.

## 8. Open items (as of 2026-11-15)

- **One source of truth for the cycle state** (`docs/state_sync.md`): **Plan A built** (CHANGES 108, 2026-11-15) -
  the IOServer publishes every state change (`io2festate.q`, `io:state`), the frontend compares it with its own state
  and only logs (`STATE-SYNC MISMATCH / RESOLVED / GAP / IOSERVER-RESTART / CYCLE`). Uddi runs it for a few weeks,
  then sends the STATE-SYNC lines; **Plan B** (the frontend follows the IOServer) is designed in the same document
  and is built only if he decides so.
- **`PLCIOServer.py` (3,227 lines)**: split into modules later (a pure move, checked by the fault scenarios).
- **Star imports:** 44 of the 76 production files that had them now import explicitly (CHANGES 109, done with
  `test/star_imports/startool.py`, which proves every name by object identity). The other 32 keep them on purpose:
  17 are still star-imported by other files (hubs such as `Configuration`, `logutils/SlaveLoggers`,
  `processors/GenericQueueProcessor`, the frontend image / label utilities), 4 have them inside `try` blocks (cap,
  no-bung, nut / washer, top bearing checks), 2 are vision files in the verifier's manifest (hub, knuckle), and 9
  cannot be imported in the sandbox (legacy or Windows-only modules). Do not hand-edit imports of the converted files back to
  `*`; to convert more later, re-run the tool on a copy (its docstring has the steps).
- **Machine 1 vision values for the segmentation knuckle method**: need Machine 1 pictures (knuckles of each type /
  side and an empty fixture; hub, bearing, nut, bung, cap of the same parts).
- PLC programmer: an address for `PC_PLC_CycleAborted` (optional tag, not written until it exists).
  `PC_PLC_Skip_Second_Tightening` has `DB500,X48.0` (CHANGES 121).
- **Cap seating by the hub-centre distance** (CHANGES 143, 152): the cap's centre (stage-2 ellipse of the cap mask,
  `camera/CapMaskFinder.py` = the method frozen in CHANGES 139 - 142) must be within 5 px of the hub centre + offset.
  Since CHANGES 152 the hub centre is the circle through the hub's 4 (DOST) / 5 (DOSTPLUS) holes round the cap in the
  cap picture itself (`CheckCap.hubHoles`, `vision.cap.seating.hub.source=cap`; holes not found -> NOT seated), offsets
  `offset.cap.dost` 0.2,-0.1 / `.dostplus` -0.6,-5.3 (27 seated silver / 99 seated brass caps, cap centres with stand-in
  YOLO boxes in the sandbox - confirm on the laptop). The hub check's centre (`ImageProcessor.hubCentre`) and the
  holes / bolts offsets stay for `source=hubcheck`; those offsets were measured against hub pictures of other parts and
  carry their position (CHANGES 152) - re-measure before using that source. Uddi sends the seating lines of a shift. A
  limit on the share of outline points within 2.5 px still needs the cap pictures labelled seated / skewed.
- **Bung seated position from the hub's holes** (CHANGES 153): `BungSegmenter.seated_position` = `CheckCap.hubHoles` in the
  bung picture + `vision.bung.seating.hub.offset.<model>` (4.9,-13.7 / 4.2,-16.0); `position.source=fixed` = before. The
  user's good / bad labels of 2026-11-18 call A05, C14, C16 (and 3 new pictures) good although the bung sits 18 - 42 px
  off the hub centre like the bad ones: open question (CHANGES 153). Confirm the hole step on the laptop (YOLO).

## 9. Testing

- Synthetic dry runs: `test/machine2_dryrun/README.md` (and the Machine 1 copy) lists every script and what it
  checks. They need Redis on localhost:6379; the DB ones PostgreSQL on 127.0.0.1:5432 (postgres / postgres). Run from
  the project root unless the README says otherwise (some write image folders relative to the current directory).
- Model-based vision runs need `models/` (MobileSAM nested clones and weights, about 5.8 GB, not in git): on the
  laptop. In the sandbox the MobileSAM weights come from `ChaoningZhang/MobileSAM` (`GIT_LFS_SKIP_SMUDGE=1` clone;
  the weights file is a normal file there); YOLO weights are not available, so the hub check (and bung hole counts)
  cannot be judged there - say so rather than guessing.
- Most used: `Verify_Vision_Config_Equivalence.py` (both folders), `DryRun_IOServer_Fault_Scenarios.py`,
  `DryRun_Knuckle_Frame.py --quick`, `DryRun_Vision_Check_Timeout.py`, `DryRun_Cap_Check.py`,
  `DryRun_Cap_Production_Path.py`, `DryRun_Bearing_Ring_Guard.py`, `DryRun_Camera_State_File.py`, `DryRun_Frontend_Languages.py`,
  `DryRun_All_Checks_UI.py` (window; `--auto` without one).
- Sandbox notes: Python 3.13 has torch / OpenCV but no tkinter; Python 3.12 has Tk (and PySide6 under Xvfb) but no
  torch - windows are driven on 3.12 with the camera modules stood in, the real checks on 3.13 without a window.
  OpenCV must stay 4.11 (requirements.txt); 5.x changes `HoughLinesP`'s output shape. Redis and PostgreSQL may need
  starting: `redis-server --daemonize yes --save ""; service postgresql start`.

## 10. How Uddi likes to work

- Terse replies; diagnosis first; honest uncertainty (say what was not run and why); catch and own mistakes.
- Root causes over workarounds; outline a design before building it when asked.
- Full git command blocks ready to paste, for the laptop (`git pull origin machine2-io-fe-db-cam`, the dry runs to
  run, `runNuitka.bat siemens`). Markdown docs need no separate download: `git pull` brings them.
- Run the relevant dry runs before shipping and report the counts.
