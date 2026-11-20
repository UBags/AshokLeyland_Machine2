> **Notes of 2026-11-05.** Section numbers 74–80 here are this document's own and are not the same as §74–80 of `../CHANGES_MACHINE2.md`. §79 below (restart detection by the last PLC_PC tag raised) still holds; when no PLC-raised tag is known to be True, the side written in state 2 is now the fallback reference (CHANGES §80 and §82, `PLC_Interface_Spec.md` §5.6).

# Machine 2 plant PC: set-up and start-up (2026-11-05)

Continues `claude/machine2-changes-log.md` (sections 1-73). Branch `machine2-frontend-db`. Install folder on the PC: `D:\AshokLeyland` (exe in `MainProgram.dist`). PC name `Costheta-AshokLeyland-Machine2`, Windows user `Admin`.

## 74. Windows updates off (`ChangeWindowsUpdateSettings/`, commit b2389fe)
- `DisableAllUpdates.ps1` / `EnableAllUpdates.ps1` (admin PowerShell; first line of each file = run command). They re-run themselves as SYSTEM through a one-off scheduled task, because the Update Medic service and the UpdateOrchestrator tasks can't be changed by an administrator.
- **What is turned off:**
  - Windows Update policies, with the update server pointed at `http://windows-update-blocked.invalid`.
  - Services: wuauserv, UsoSvc, WaaSMedicSvc, uhssvc, sedsvc, edgeupdate, edgeupdatem.
  - Scheduled tasks: WindowsUpdate, UpdateOrchestrator, WaaSMedic, rempl, InstallService\ScanForUpdates*, MicrosoftEdgeUpdateTask*.
  - Driver and device-metadata downloads, Store auto-updates, Edge updates.
  - Defender is not touched.
- **State saved and restored:** the state found before the first run goes to `C:\ProgramData\UpdateControl\state.json`, which Enable restores (Windows 11 defaults if the file is missing). Logs are in the same folder.
- **Not yet confirmed:** whether these scripts were run on the PC.

## 75. Restore points every 72 h (`ChangeWindowsUpdateSettings/CreateRestorePoint.ps1`, commits 6026586, 925f386)
- **Replaces `CreateRestorePoint.bat`.** WMIC is gone in Windows 11 24H2, and the bat passed the arguments in the wrong order (type 100 / event 7); the correct call is type 12 / event 100.
- **`-Install`:**
  - copies the script to `C:\ProgramData\RestorePoint\`;
  - turns System Protection on for C: with 10% of the drive;
  - sets `SystemRestorePointCreationFrequency=0`, which removes the 24 h minimum gap;
  - registers the SYSTEM task "CreateRestorePoint" (5 min after start-up, and hourly; StartWhenAvailable).
- **Each run:** creates a restore point when the newest is 72 h old or more, then waits up to 180 s for it to be listed, matching its exact description.
- **Found on the PC:** the restore point appeared about 11 s after CreateRestorePoint returned. The first version checked at once and reported a false error.
- **Verified on the PC:** restore point #21 created; the task ran as SYSTEM with result 0 and logged a SKIP. Log: `C:\ProgramData\RestorePoint\CreateRestorePoint.log`.
- **Fast Startup:** with it on, Shut down + power on is not a real boot, so the start-up trigger doesn't fire. The hourly trigger covers that.

## 76. Start at logon (repo root `Startup.bat`, `Start-Hub-And-Disc-application - Production.vbs`, commit 395bae1)
- **The `.vbs` goes in `shell:startup`** (runs at logon, not at boot; MainProgram is a desktop app):
  - it waits up to 240 s each for Redis (6379) and PostgreSQL (5432) to accept connections, trying every 2 s (`POLL_MS`; 3 s before CHANGES 174) - each try also starts a hidden PowerShell (~0.3 - 1 s);
  - then it starts `cmd.exe /k MainProgram.exe` minimised, so the console with the logs stays visible;
  - it uses a WMI process check (no console flash), so the program is started only if it isn't already running;
  - OtherServices is started only if it is built;
  - if Redis or PostgreSQL never comes up, a message box says MainProgram was not started;
  - log: `D:\AshokLeyland\StartupLauncher.log`.
- **`Startup.bat` stays on the Desktop** for starting by hand. It uses an absolute path, refuses a second copy (a second MainProgram would flush Redis and compete for COM3), and shows the exit code.
- **Old launcher bugs:** the old `.vbs` used `D:\AshokLeyland\Startup.bat`, but the bat lives on the Desktop; the bat used a relative exe path.
- **`start-redis.vbs` is no longer used.**

## 77. Redis on the PC (checked 2026-11-05)
- **WSL:** Ubuntu-22.04 on WSL2, with systemd as PID 1. `redis-server` (Redis 8.10.2 with RedisJSON / search / timeseries) is enabled, with `Restart=always`, `supervised auto` and `daemonize no`; there is no pid file. This rules out the old stale pid lock.
- **Starting WSL:** the scheduled task "Start WSL Ubuntu-22.04" (user Admin, Interactive) runs `wsl.exe -d Ubuntu-22.04 --exec /bin/true`. `.wslconfig` has `[general] instanceIdleTimeout=-1`, so the distro never shuts down when idle.
- **Port 6379:** `wslrelay` listens on 127.0.0.1 and ::1. The Windows port exclusions are all at 50000 and above, so none covers 6379.
- **RDB snapshots turned off** (`save ""` appended to `/etc/redis/redis.conf`, `dump.rdb` deleted). A power cut can no longer corrupt the snapshot and block Redis from starting; MainProgram flushes Redis at start, so nothing is lost. AOF was already off.
- **No recovery step is needed in the launcher.**

## 78. First start on the PC with no devices connected; two fixes (commit 4eccbff)
- **Log (2026-11-05 13:49):**
  - all 7 processes started, and Redis and PostgreSQL were OK;
  - the test and production databases, indexes and default machine settings were created;
  - expected failures: COM3 missing, camera 192.168.1.64 and PLC 192.168.1.10 unreachable;
  - "BACKUP SKIPPED" appeared only because the production database didn't exist yet on this first start.
- **`camera/RTSPCam.py` keepalive:**
  - **The bug:** on a request exception, `response = Response.status_code = 400` set `response` to the int 400. That made the log say "'int' object has no attribute 'status_code'", and it also set `status_code` on the `requests.Response` class for the whole process.
  - **The fix:** the code now logs `ISAPI keepalive failed: <ExceptionType>: <message>` and skips the status check. The unused import is removed.
  - **Dry run:** the real thread, with an unreachable camera, HTTP 401 and HTTP 200; `requests.Response` is no longer changed.
- **Console colours:**
  - **The problem:** the cmd window showed the colour codes as text (`<-[32m<-[1m...`).
  - **The fix:** new `utils/WindowsConsole.enableAnsiColours()` turns on `ENABLE_VIRTUAL_TERMINAL_PROCESSING` for stdout and stderr. It is called once at import of `utils.CosThetaPrintUtils`, which every process imports before its first coloured print. It does nothing on other operating systems or when output is redirected.
  - **Dry run:** with a simulated kernel32 (normal, already on, redirected, SetConsoleMode fails, invalid handles).
  - **Takes effect after the exe is rebuilt.**
- **Seen, not changed:** MobileSAM is loaded twice (ModelManager and HexagonNutDetector each load their own copy).

## 79. IOServer: one verified reset routine, QR armed at start, PLC restart detection (commit d18e777)
- **`resetForNextCycle(reason)`** is now the one routine for the end of a cycle, every abort (PLC emergency, heartbeat, frontend) and the IOServer start. In order, it:
  1. cancels pending vision-result resets;
  2. writes every per-cycle tag to its default (`perCycleTags()`: bool False, float 0.0) and reads it back, with up to 5 attempts 0.2 s apart;
  3. arms `PLC_PC_CheckQRCode = True`, also read back.
- **Left alone:** the 6 state-2 settings, the watch tag and the connection-status toggle.
- **If something can't be written and verified:** it is logged and the reset is marked incomplete. The reset is redone after the next reconnect, but only while the machine is still at the QR code, so a running cycle is never reset without an abort.
- **IOServer start:** now arms CheckQRCode (user decision, 2026-11-05). Before, it waited for the PLC to raise it.
- **PLC restart detection (user's rule, 2026-11-05):** the reference is the last PLC_PC tag the PLC set to True.
  - **Tracking:** the IOServer keeps an ordered list of PLC_PC tags known to be True. A tag is added when the read loop sees the PLC raise it, or when the PC arms CheckQRCode. It is removed when the PC writes it False, or when the PLC is seen to drop it while connected (checked every second).
  - **On connection loss:** all PLC writes and the read loop are held until the PLC has been checked after the reconnect. A camera result that arrives during the outage is written after the check.
  - **Reference still True:** the PLC did not restart, and the cycle carries on in the same machine state.
  - **Reference False, or no tag known to be True:** the PLC restarted. A cycle in progress is aborted through `abortCycle`: the QR server, frontend and camera are told, the tags are reset and CheckQRCode is armed. At READ_QR_CODE there is only the reset.
  - The frontend ignores an abort while it is at READ_QR_CODE, so an idle restart creates no record.
- **Depends on:** DB500 / DB101 being non-retain, so that a PLC restart returns them to 0 / False.
- **If the real PLC drops its trigger after Done** (the dry-run PLC does this in scenarios A–H), the reference falls back to earlier tags. Once none is left, a link loss is treated as a restart, which aborts the cycle: the safe side, but a lost cycle. Not yet confirmed on the PLC: read the tags mid-cycle with `s7_read_all_tags.py --repeat`.
- **Dry runs:**
  - `_plc_sim` gains `LINK` (cut the link), `plc_restart()` and `STUCK` (writes ignored).
  - New fault scenario I (26 checks): arming at start, link loss without a restart (between steps, and during a camera result), the PLC dropping its own trigger, a restart mid-cycle and while idle, a reset while the link is down, and a tag the PLC does not take.
  - **Results:** fault scenarios Siemens 114/114, Allen-Bradley 113/113; two full cycles exact on both.
  - **Mutation test:** with restart detection disabled, I3 fails (101/106).
- **Test fix:** check G now waits for the picture state. Before, it used a fixed sleep of delay − 0.6 s, which is 0 s with the configured 0.6 s delay.
- **Known gap (pre-existing, not changed):** a vision-result reset or torque reset due while the link is down is tried 5 times and then dropped. After a reconnect without a restart, that check's OK / Done can stay True until the end-of-cycle reset.

## 80. CheckKnuckle: alternate area for each presence check A–E (yellow paint marks) (commits a2bf819, 097fbfd)
- **Why (user):** operators mark knuckles with yellow paint at random spots. A mark over one of the presence regions A–E makes it bright, and the knuckle was rejected.
- **Change:** each region A–E can have an alternate area (`<vendor>.vision.knuckle.common.check.<a..e>.alt.*`; a width, height or radius of 0 means none). The region passes if it OR its alternate reads below the threshold (84). Both values are logged.
- **Machine 2 alternates.** Chosen on the 14 knuckle pictures and 5 empty-fixture pictures. Each must be:
  - dark on every knuckle;
  - bright on every empty fixture (A–D only);
  - at least 15 px from its region, so one mark can't cover both;
  - clear of the paint marks seen in the pictures.

  Margins are given as photographed / with the part shifted 3 px:

  | Alternate | Position | Margin | Note |
  |---|---|---|---|
  | A | rect 729,210 31×24 | 15.1 / 11.2 | A itself: 20.6 / 8.1 |
  | B | rect 595,226 22×15 | 30.0 | nothing usable on the knuckle within 60 px of B; this one is 92 px away |
  | C | circle 525,510 r10 | 27.1 | |
  | D | circle 883,509 r7 | 29.6 | below D; the area left of D is where operators write the yellow "3" |
  | E | rect 641,598 36×14 | 36.7 | on the black bracket, like E; a full-size one doesn't stay dark |
- **Machine 1:** no alternates (all 0), so its behaviour is unchanged.
- **`DryRun_CheckKnuckle_Images`:**
  - For every picture and every region: a yellow mark over the region → OK; over the alternate only → OK; over both → NOT OK.
  - The alternates' margins are listed, with their own rule of 15. The A alternate is at 15.1; no spot near A offers 20.
  - **Results:** Machine 2 105/105, Machine 1 30/30.
- **Verifier:** reference a2bf819 (the previous reference 637ce4a with this logic, values hard-coded). ALL EQUIVALENT in both copies.
- **Figure:** `knuckle_check_regions_machine2_with_alternates.png` (session output).
- **Found on the way (not changed):** when Redis is down, every `logBoth` call blocks for about 1–3 s while it retries the connection (cycle-context lookup and log queue). One knuckle check then took about 26 s instead of a fraction of a second. If Redis goes down on the plant PC, the vision checks slow down the same way.
