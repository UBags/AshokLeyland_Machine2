# One source of truth for the cycle state: frontend and IOServer

| | |
|---|---|
| Status | **Plan A built** (CHANGES 108, 2026-11-15): the IOServer publishes its state, the frontend compares and only logs. **Plan B designed, not built**: decided after a few weeks of Plan A logs on the plant PC |
| Code | `utils/StateSync.py` (comparison), `utils/RedisUtils.py` (`publishIOServerState`, `readIOServerStates`, `readIOServerStateSnapshot`), `PLCIOServer._publishState`, `AshokLeylandFrontEnd.StateSyncWorker` |
| Tests | `DryRun_State_Sync_Monitor.py` (M1-M6), fault scenarios O (IOServer side), frontend dry run F14 |

## 1. The problem

Two processes walk the cycle's states:

- the **IOServer** (`MachineStateMachine`, 28 states), from the PLC handshakes: the real state of the machine;
- the **frontend** (its own `MachineStateMachine`), which moves itself on: on a camera OK to the next tile, on a
  NOT OK it stays, on a torque / press value to the next step; the IOServer tells it where to go only with
  `moveToState` (bypass) and the emergency abort.

They can drift apart: the frontend moves on when the camera's OK reaches it, the IOServer only after the PLC
handshake (a handshake that fails, or an abort in between, leaves the frontend ahead); one lost or late message leaves
the frontend waiting in the wrong state, ignoring every message that does not match it. Nobody knows today how often
that happens on the machine - Plan A finds out.

The IOServer is the master: it is the only process that sees the PLC. A separate state-machine server would only
copy what the IOServer tells it (one more process, one more Redis hop, one more way to fail) and was not chosen.

## 2. Plan A (built): publish, compare, log

**IOServer.** Every state change (`MachineStateMachine` calls `PLCIOServer._onStateChange`) is published first, by
`_publishState`. Since CHANGES 110 the state machine calls `_onStateChange` after it has released its lock, in the
order of the changes and from one thread at a time (a change made while another thread is delivering is delivered by
that thread), so a slow Redis no longer holds up the other IOServer threads; the published order and `seq` are as
before:

- on the stream `io2festate.q` (one producer, one consumer; capped at about 500 entries) and in the key `io:state`
  (the latest snapshot, 24 h expiry), as JSON:
  `{pid, seq, cycleId, qr, old, oldName, new, newName, reason, time}`;
- `seq` goes up by one per change, also when Redis does not take the message, so a lost message shows as a gap;
- `reason`: `IOServer start`, `abort: <reason>` (set by `abortCycle`), `cycle completed`, `reset`,
  `NOT OK: back to the check (retake)`, `OK: next step`, `PLC request: waiting for the answer`, `next step`;
- Redis connection with 1 s timeouts: a state change never hangs on Redis; a failure is logged once per outage
  (`STATE-SYNC IOServer could not publish state ...`, then `... publishes states again`);
- the audit trail's state line carries the number: `STATE A -> B  (seq N, published | NOT published)`; at start:
  `STATE at start READ_QR_CODE  (seq 1, published)`.

**Frontend.** `StateSyncWorker` (its own thread, every 250 ms): reads `io:state` once at start, then the stream, and
gives both to `StateSyncMonitor` with the frontend's own state. **It changes nothing**: the frontend's behaviour is
exactly as before.

`StateSyncMonitor` compares them:

- for each IOServer state, the frontend states that agree with it (`FRONTEND_STATES_FOR`): the frontend shows only
  the READ state of a check and is one step ahead while the IOServer finishes a handshake or after a value / press
  event (e.g. IOServer `WRITE_RESULT_OF_CHECKING_KNUCKLE` -> frontend knuckle or hub);
- a difference counts only when it lasts longer than `frontend.state.sync.grace.sec` (5 s); at the end of a cycle
  (frontend still showing the free-rotation torque, IOServer back at the QR code) the display time
  `frontend.free.rotation.torque.display.sec` + 1 s is added.

**What is logged** (frontend log; every line starts with `STATE-SYNC`):

| Line | When |
|---|---|
| `STATE-SYNC START  IOServer pid P, seq N, state S; grace 5 s` | first IOServer state seen |
| `STATE-SYNC MISMATCH #n  frontend <F> vs IOServer <I> for 5.0 s (frontend should be one of [...]); cycle C, QR Q; IOServer seq N pid P, in that state since hh:mm:ss.mmm (came from <old>, <reason>); frontend in its state since hh:mm:ss.mmm` | a difference outlasted the grace period (once) |
| `STATE-SYNC RESOLVED #n  after x s: frontend <F>, IOServer <I> (seq N)` | in step again |
| `STATE-SYNC GAP  IOServer state messages seq a -> b: k missing` | messages lost (the IOServer audit says whether they were published) |
| `STATE-SYNC IOSERVER-RESTART  pid a -> b` | the IOServer restarted |
| `STATE-SYNC CYCLE C completed: in sync` / `... aborted in <state>: n MISMATCH(ES), longest x s` | every end of cycle |
| `STATE-SYNC BAD-MESSAGE ...` | a snapshot that could not be read |

**Reading the logs after a few weeks** (plant PC, `D:\AshokLeyland\...\logs`):

    findstr /C:"STATE-SYNC MISMATCH" /C:"STATE-SYNC GAP" /C:"STATE-SYNC IOSERVER-RESTART" logs\*.log
    findstr /C:"STATE-SYNC CYCLE" logs\*.log | find /c "in sync"
    findstr /C:"STATE-SYNC CYCLE" logs\*.log | find /c "MISMATCH"

For a MISMATCH, the IOServer side is in the audit trail at the same time: search `seq N` from the line.

## 3. Plan B (designed, not built): the frontend follows the IOServer

To be decided from the Plan A logs. If there are no mismatches in weeks of production, Plan B is optional (a
robustness change); if there are, it removes their cause.

1. **The frontend's state comes from the IOServer.** The `StateSyncWorker` (already reading the stream) sets the
   frontend's state to the one it maps from the IOServer's state: for a READ state of a check, that check; for its
   WRITE state, the same check until the camera result for it has been shown; value / event states as in
   `FRONTEND_STATES_FOR`. The ~40 `setCurrentState` calls in the result handlers are removed (the handlers keep
   showing results and filling the record).
2. **Results are filed by the state in the message, not the frontend's state.** Camera results and torque / press
   values already carry their state (camera results also the request id): a result that arrives just before or just
   after the state change (different streams, no order between them) goes to its own tile either way. The present
   "ignore a message for another state" rule goes.
3. **Resync.** At frontend start, after a `GAP` or an `IOSERVER-RESTART`, the frontend reads `io:state` and jumps to
   it. A frontend restarted by the supervisor mid-cycle comes back on the right step (today the IOServer aborts the
   cycle when the frontend goes down; that rule stays).
4. **Unchanged:** the emergency screen and the abort record (the IOServer's `abort: ...` state change also reaches
   the frontend); the bypass (just another state change - `moveToState` becomes redundant but is kept); Ctrl+Alt+Z
   (its use - the frontend not following the machine - mostly goes away; kept as it is); the camera server (works
   from request ids).
5. **Logging stays:** after the switch, the monitor still compares (now the frontend's displayed step with the
   IOServer's state) and logs as in Plan A, so a fault in the mapping shows up the same way.
6. **Tests:** fault scenarios, frontend dry run, integrated cycle; new cases: a state message dropped (resync), a
   camera result after the state change, frontend restart mid-cycle, abort during a handshake, a bypassed cycle,
   two cycles back to back with the end-of-cycle display.
7. **Effort:** mostly the frontend (the result handlers and the record filling); the IOServer side is done (Plan A).
