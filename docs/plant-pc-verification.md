# Machine 2: checks to run on the plant PC before production

These checks cover what the sandbox dry runs cannot: the Nuitka exe, the real GPU, Windows process handling. Run them
once after each new exe is copied to `D:\AshokLeyland`, with the PLC and camera connected and the application
started as usual (TEST mode is enough). Each step says what to look for in the console window and in the log files
(`logs\`). Record the results in the table at the end.

The plant PC has the exe only, not the repo. Step 2b (`PlantPC_Vision_Timing.py`) needs the source: run it on the
laptop, or on any PC with the repo, the models and a GPU like the plant PC's.

## 1. The vision check worker runs inside the exe

The vision checks run in a child process of the camera server (`camera/VisionCheckWorker.py`). It is started with
multiprocessing "spawn", like the servers themselves, but it has not been run inside the exe before.

1. Start the application. In the console, within about 30 s of "Started Camera Server", look for:
   - `Vision check worker process started (pid N); loading the models`
   - `Vision check worker: warm-up of the model-based checks took X s`
   - `Vision check worker ready (pid N)`
2. In Task Manager (Details), there is one more `MainProgram.exe` than before, with that pid.
3. Run one cycle. Every picture check shows OK or NOT OK as usual, and the log has a line like
   `READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: check 3.10 s in the worker, transfer 0.01 s` for each.

**If the worker does not get ready** (no "ready" line, or every check NOT OK with "vision check worker starting"):
there is no fallback (the thread mode was removed, CHANGES §105): stop, and tell the developer what the console showed.

## 2. Vision check times against the 12 s timeout

**2a. On the plant PC (exe):** run 10 cycles, DOST and DOSTPLUS, LHS and RHS. In the log, the line
`<STATE>: check S s in the worker, transfer T s` gives each check's time. For every check:

- the largest S must be below 80 % of `siemens.vision.check.timeout.sec` (12 s, so below 9.6 s);
- T must be below 1 s (a warning "slow transfer to the worker process" is logged above that).

A check over 80 %: raise `siemens.vision.check.timeout.sec` (the camera answer timeout follows it automatically: it
is at least the check timeout + 8 s), or find out why the GPU is slow (`nvidia-smi`: is the GPU used, is another
program using it?). Three timeouts in a row are reported on the screen as "vision checks too slow on this PC".

**2b. With the source (laptop or a PC like the plant PC):**

    python test/machine2_dryrun/PlantPC_Vision_Timing.py

It runs every stored test picture through its check with the real models and prints, per check, the median and
largest time, the transfer time and the margin to the timeout. Exit code 1 if a check is over 80 % of the timeout.

## 3. A server process that dies is restarted

MainProgram's supervisor restarts a server process that dies (at most 10 times an hour per server). In the console
at start-up each process's pid is printed: `Camera process is N`, `QR Code process 1 is N`, `IO process is N`,
`DB process is N`, `Frontend process is N`, `Heartbeat Monitoring process is N`, `Logger process is N`.

For each server, one at a time, with the machine idle at the QR code scan (not during a cycle):

1. In a separate command window: `taskkill /PID N /F` (N = that server's pid).
2. Within about 5 s the console shows `Supervisor: <server> has died (exit code ...); restarting it`, then
   `Supervisor: <server> restarted (pid M; restart 1)`.
3. Within about 30 s (camera: up to a minute, the models load again) the server's tile on the screen is green again,
   and a full cycle runs.

What else to expect while the server is down (the rules of `PLC_Interface_Spec.md` §5.7):

| Server killed | While it is down |
|---|---|
| Camera | tile red, siren; a picture requested meanwhile is NOT OK at once |
| QR code | tile red, siren; no scan is read |
| IO | tile red, siren; the PLC sees the PC's connection-status toggle stop. The restarted IOServer resets its tags and arms the QR code read |
| DB | tile red, siren; "Database down: next cycle on hold" at the top right |
| Frontend | the screen closes and opens again (logged in as the same user) |
| Heartbeat | the tiles keep their last colour for a few seconds, then show no status; after 8 s the IOServer holds the next cycle ("Heartbeat server down") |
| Logging | log lines of the next seconds are kept and written when it is back |

Once **during a cycle**, after the top bearing check: kill the camera server. After its restart the console shows
`Camera server restarted: cycle state taken from ... - QR code ..., pictures [...], bearing ring yes, knuckle frame yes`,
and the nut / washer and later checks of that component work as before (they use the kept pictures, ring and frame).

Then once **during a cycle** (after the knuckle check): kill the frontend. The IOServer aborts the cycle (the PLC
gets the reset); after the frontend is back, the next cycle runs normally.

## 4. A frozen screen counts as frontend down

Not to be provoked on purpose. If the screen ever freezes for more than a minute, the frontend's heartbeat now
reports it as down (`Frontend: the GUI thread has not run for N s - heartbeat DEAD` in the console), and the cycle
in progress is aborted as for a closed frontend. A menu dialog left open (Machine Settings, a report, user accounts,
the manual-abort password prompt) does not count: the screen waits for the dialog, it is not frozen. The same holds for the work threads of the other servers (camera
assessment, DB inserts, IOServer read / write loops, QR code reader): stuck for `heartbeat.work.loop.max.silence.sec`
(60 s), the server is shown down.

## 5. QR codes

Scan five real parts (DOST and DOSTPLUS, LHS and RHS). Each is accepted and the side and model shown match the part.
A scan that is not accepted with a real code: send the code (from the log line `Invalid QRCode ...`) to the developer.

## 6. Knuckle check by segmentation and the frame (Machine 2, CHANGES 102)

The knuckle check (state 3) runs MobileSAM on one picture and gives the later checks their centres for the cycle.

1. Run 3-4 cycles, DOST and DOSTPLUS, LHS and RHS. For each knuckle check the camera server log has
   `CheckKnuckle: SUCCESS - <side> <type> matches the QR code; ... frame: ... camera shift (x, y), rotation ..., scale
   ...; centres: ...`, and before the next checks `Vision centres from the knuckle picture: ...`.
   - The camera shift should stay within about 1-2 px between cycles, the scale within 0.999-1.001.
   - "spindle mask N px away - MORE THAN 6 px: check the part" should not appear (except with a bearing on the spindle).
   - The check time (`READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: check S s in the worker`) about 0.5 s on the GPU.
2. A knuckle of the wrong model or side, and the empty fixture: NOT OK with the reason in the log.
3. After a completed and after an aborted cycle the log shows `Vision centres from the properties file (no knuckle
   frame in this cycle)` before the next knuckle check, or the next knuckle check builds a new frame.
4. Keep the knuckle, hub and cap pictures of the same components (saved by the DB server) for 3-4 cycles: they show
   whether the hub follows the spindle 1:1 (the open question of CHANGES 102).

If the knuckle check fails on good parts after the camera was touched ("camera moved N px (limit 40) - set the camera
again"), set the camera back. If it fails for another reason, `siemens.vision.knuckle.method=regions` brings back the
grey-level check of CHANGES 95 (its regions are for the camera position of 13 Nov) and the later checks use the
centres in the properties file.

## 7. Frontend and IOServer in step (state sync, CHANGES 108)

Nothing to do during the run: the frontend logs by itself whenever its state and the IOServer's differ for more than
`frontend.state.sync.grace.sec` (5 s). After a few weeks of production, in the logs folder:

    findstr /C:"STATE-SYNC MISMATCH" /C:"STATE-SYNC GAP" /C:"STATE-SYNC IOSERVER-RESTART" logs\*.log
    findstr /C:"STATE-SYNC CYCLE" logs\*.log | find /c "in sync"
    findstr /C:"STATE-SYNC CYCLE" logs\*.log | find /c "MISMATCH"

Send the lines found (and the IOServer audit lines around each `seq N` they name) to the developer: they decide
whether Plan B (`docs/state_sync.md` §3) is needed.

## 8. Picture clean-up, invalid QR code, DB retries (CHANGES 110)

1. **Picture clean-up.** At start, the console and the log have three lines `Picture clean-up: N trial pictures older
   than 30 days deleted from C:/Temp/TrialImages/ (X MB freed, ...)`, then the same for `Test pictures` and
   `Production pictures` under `C:/AshokLeylandImages/`. The first start after this exe may take a while on a full
   disk (it runs in the background; the application starts normally meanwhile). `C:\AshokLeylandImages\Production\
   FailedRecords\` keeps its files.
2. **Invalid QR code.** At the QR code step, scan a code that is not a component's (any other barcode): the screen
   shows "QR Code not valid" (top right, about 10 s) and the siren sounds as for a NOT OK picture; then scan the
   component's code: the cycle starts and the notice goes.
3. **MainProgram's lines in the log.** MainProgram's red and green console lines (for example `Connected successfully
   to Redis in MainProgram`, a supervisor's "has died; restarting it") are now in the log file too.
4. **DB retries (optional).** With a cycle running, stop the PostgreSQL service for about 5 s and start it again
   before the cycle ends: the record is stored (the DB server's log has `Insert attempt 1 failed ...; will retry in 5 s`
   and then the insert), with its pictures once in the day folder.

## 9. Bung check: holes whatever the bung's angle, new seating tests (CHANGES 115)

On the laptop first (it has the YOLO weights and a GPU), after `git pull`:
1. `python test/machine2_dryrun/DryRun_Bung_Rotation.py` - 23 seated pictures turned in 15 degree steps; the last line
   must say `RESULT: 1104/1104 hole patterns as expected`. The `s` marks (seating NOT SEATED on a turned picture) are
   information only (the picture's light and the hub turn with it).
2. `python test/machine2_dryrun/DryRun_Bung_Check_UI.py`, model Both, the 13 Nov pictures: seated bungs OK as their
   own model, NOT OK as the other; not seated NOT OK. Note the time per picture.

On the Machine 2 PC with the new exe:
3. **Bungs that failed before.** Put a correct bung the way that gave NOT OK before (and turn it through a full turn
   in steps over a few cycles): OK every time.
4. **Log.** Each bung check writes one `[Bung] <model> gamma <g>: ...` line per gamma (masks, candidates with their
   position, distance from the disc centre and area, what was dropped and why, the decision) and the `[CheckBung]
   Seating: ...` line with the four seating values. For any NOT OK on a correct bung, send these lines.
5. **Time.** The bung check's time (the `check time` in the camera server's log) - each gamma now decodes about 220
   point prompts; it must stay well inside the 12 s timeout (expected: well under a second per gamma on the GPU).

## 10. Cap seating: brass by fitted circles, silver by two rules, both by the hub-centre distance (CHANGES 128, 129, 143)

On the laptop after `git pull`: `DryRun_Cap_Production_Path.py` (12/12) and `DryRun_Cap_Silver_Seating.py` (6/6),
both with the MobileSAM weights.

On the Machine 2 PC with the new exe:
1. **Seated caps OK.** Run normal production for a shift: brass and silver caps placed correctly are OK.
2. **Tilted silver cap.** Press a silver cap in tilted (one side proud, the bore still covered) and one pushed aside
   (part of the bore visible): both NOT OK.
3. **Log.** Each seating writes `CheckCap seating: ...`. Silver: `top face edge on one circle <fit> >= 0.64`, `hub
   centre uncovered <n> px <= 400 (hub r .., cap outline .. px from hub centre)`; brass: `cap centre to bore centre`.
   Send these lines for a shift (seated caps) and for any wrong verdict - the silver limits were set without
   pictures of a hub and its own cap (the hub picture of the same cycle is used in production).
4. **Time.** Silver seating now runs MobileSAM on two pictures (cap and hub); the cap check's time must stay well
   inside the 12 s timeout.
5. **Hub-centre distance (CHANGES 143, 144).** Laptop: `DryRun_Cap_Hub_Distance.py` (11/11; with the YOLO weights it
   uses the real YOLO) and `DryRun_Cap_Hub_Distance_UI.py` (a hub picture and the caps of the same parts: the suggested
   offset and max). Machine 2 PC: the hub check writes `[CheckHubAndBottomBearing] hub centre (x, y) from 4 holes`
   (or bolts, circle r, spread); the cap check's seating line adds `cap centre (x, y) to hub centre (x, y) d px <= 5.0
   (... cap +dx / +dy px; ...)`. The 5 px limit and the zero offsets are not measured yet (no hub and cap pictures of
   the same part): send these lines for a shift of seated caps. If seated caps sit at a steady dx / dy (camera angle:
   holes, bolt tips and the cap face are at three heights), that goes into `siemens.vision.cap.seating.hub.distance.offset.holes`
   / `.bolts`; to stop the rule, `siemens.vision.cap.seating.hub.distance.check=False`.
   Expect the bolts case to differ from the holes case by about 7 px in y (CHANGES 96: the stud circle's centre sits
   about 7 px above the hole circle's): with both offsets 0, seated caps may be rejected on one of the two until the
   offsets are set.

## Results

| Step | Date | Exe build | Result | Notes |
|---|---|---|---|---|
| 1 Worker in the exe | 2026-11-15 | | Passed | Reported by Uddi: the worker runs inside the exe |
| 2a Check times (largest per check) | 2026-11-15 | | Passed | Reported by Uddi: response times fast (per-check times not recorded) |
| 3 Restart: camera / QR / IO / DB / frontend / heartbeat / logging | | | | |
| 3 Camera server killed during a cycle (state taken back) | | | | |
| 3 Frontend killed during a cycle | | | | |
| 5 Real QR codes | | | | |
| 6 Knuckle frame (shift, scale, check time; wrong part NOT OK) | | | | |
| 7 State sync after a few weeks (MISMATCH / GAP lines, cycles in sync) | | | | |
| 8 Picture clean-up lines; invalid QR notice + siren; MainProgram lines in the log; DB retry | | | | |
| 9 Bung: rotation dry run and Bung_Check_UI on the laptop; failing bungs now OK; [Bung] lines; check time | | | | |
| 10 Cap seating: seated brass / silver OK; tilted and pushed-aside silver NOT OK; seating lines; check time; hub-centre distance lines (CHANGES 143) | | | | |
