# Machine 2 (Hub and Disc Assembly): PC–PLC Interface Specification

| | |
|---|---|
| Document | PLC interface specification |
| Applies to | Machine 2. Siemens S7-1515 (pyS7) and Allen-Bradley (pycomm3) |
| PC side | `costhetaio/commonplc/PLCIOServer.py` through `PLCDataIO` |
| Tag source | `costhetaio/siemens/IOServerProperties.yaml` (Siemens addresses); tag names in `ApplicationConfiguration.properties` (`*.tagname`) |
| Status | Part A (protocol v1) describes the protocol as built and as running. Part B (protocol v2) is a proposal and needs PLC programmer sign-off |

---

## 1. Purpose and scope

This document defines every signal exchanged between the inspection PC and the machine PLC: the tags, who writes them, the order of writes, the timings, the timeouts, and what happens on abort, restart and loss of connection.

Out of scope: the vision algorithms, the database, and the frontend. See `HLD.md` and `LLD.md`.

## 2. Conventions

- **Tag prefix shows the main writer.**
  - `PLC_PC_*` is written by the PLC and read by the PC.
  - `PC_PLC_*` is written by the PC and read by the PLC.
- **v1 exceptions to ownership.** In v1 the PC also writes some `PLC_PC_*` tags to reset or re-arm them (§5.4). v2 removes these exceptions.
- **Types.** bool; int (Siemens INT, 16-bit); float (Siemens REAL, 32-bit); lreal (Siemens LREAL, 64-bit).
- **Addresses.** Siemens addresses are written `DBn,<type><byte>.<bit>`, for example `DB500,X2.5` is DB500 byte 2 bit 5. On Allen-Bradley the same tag names are controller tags.
- **Times.** All times are in seconds. The property that sets each time is named next to it, so a time can be changed without changing code.
- **Retried write.** A "retried write" is written again until the PLC accepts it, as long as the cycle has not been aborted meanwhile (`_writeBoolUntilWritten`).

## 3. Tag table

### 3.1 Supervision tags

| Tag | Siemens address | Type | Writer | Meaning |
|---|---|---|---|---|
| `PC_PLC_ConnectionStatusTag` | DB500,X0.0 | bool | PC | PC alive. The PC toggles it every 2 s. The PLC should alarm if it does not change for more than 6 s |
| `PLC_PC_WatchTag` | DB500,I6 | int | PLC | Must read 1234. The PC uses it to confirm it is talking to the right DB |
| `PLC_PC_EmergencyAbort` | DB500,X3.4 | bool | PLC | Emergency button. The PC polls it every 1 s, so the PLC must hold it True for at least 1.2 s. While it is True, every cycle is aborted (a held button can give several aborts; this is accepted) |
| `pc.plc.cycle.aborted.tagname` | (none) | bool | PC | Optional. Empty by default. If configured, the PC pulses it when the PC aborts a cycle |

### 3.2 Cycle settings (written in state 2 before the QR result)

| Tag | Siemens address | Type | Limits | Meaning |
|---|---|---|---|---|
| `PC_PLC_LH_RH_Selection` | DB101,I246 | int | 1 = LHS, 2 = RHS | Side from the QR code. **The PLC sets it to 0 on its restart** (both vendors); that is the PC's signal that the PLC restarted |
| `PC_PLC_TighteningTorque1Setpoint` | DB101,R200 | float | > 0 and < 20 Nm | Station 2 first tightening |
| `PC_PLC_TighteningTorque2Setpoint` | DB101,R204 | float | > 0 and < 20 Nm | Station 2 second tightening |
| `PC_PLC_RotationRPM` | DB101,LR96 | lreal | 20–40 | Rotation speed |
| `PC_PLC_NoOfRotation1CW` | DB101,LR120 | lreal | 1 or 2 | Clockwise rotations |
| `PC_PLC_NoOfRotation1CCW` | DB101,LR144 | lreal | 1 or 2 | Counter-clockwise rotations |

The settings come from the latest row of `machine_settings`.

- Each setting is written and then read back (`_settingReadBack`).
- If a write fails, the settings written in the earlier cycle stay in force. The cycle goes ahead, but the side must still be written.
- Torque OK or NOT OK is judged by the PLC against these setpoints. The PC only records the values.

**Bypass of the component press and second tightening (per model, 2026-11-13).**

| Tag | Siemens address | Type | Meaning |
|---|---|---|---|
| `PC_PLC_Skip_Second_Tightening` | **none yet** (PLC programmer to assign) | bool | True = this component skips the bung check, component press, bung removal check and second tightening (states 13–19); False = full cycle |

- Written in state 2, after the side and with the other settings, then read back; before QR OK / Done. Written every
  cycle (True or False), so the PLC never keeps the previous component's value.
- The value is the model's Machine Setting "Bypass Component Press and Second Tightening" (frontend, password-protected).
- The Torque 2 setpoint is still written when bypassed.
- **The PLC branches on it:** with True, after the first tightening torque value (state 12) the next PLC request is
  `CheckSplitPinAndWasher` (state 20); it must not raise CheckNoCapBunk, ComponentPressDone, CheckNoBunk or the second
  Station2TorqueValueSet.
- Until the PLC has the tag (Siemens: no address in IOServerProperties.yaml; Allen-Bradley: not in the controller's tag
  list), the PC writes nothing and runs the full cycle, whatever the setting (logged, shown on the frontend).
- Once the tag exists: if it cannot be written (3 attempts, read back), the QR code is answered NOT OK.

### 3.3 Check handshakes (trigger / OK / Done)

| State (read → write) | Trigger `PLC_PC_*` | OK `PC_PLC_*` | Done `PC_PLC_*` |
|---|---|---|---|
| 1 → 2 QR code | CheckQRCode X2.5 | QRCodeCheckOK X0.1 | QRCodeCheckDone X0.2 |
| 3 → 4 Knuckle | CheckKnuckle X3.3 | KnuckleCheckOK X1.7 | KnuckleCheckDone X2.0 |
| 5 → 6 Hub and bottom bearing | CheckHub X2.2 | HubCheckOK X0.4 | HubCheckDone X0.3 |
| 7 → 8 Top bearing | CheckBearing X2.1 | BearingCheckOK X0.7 | BearingCheckDone X1.0 |
| 9 → 10 Nut and plate washer | CheckNutAndPlateWasher X2.3 | NutAndPlateWasherCheckOK X1.1 | NutAndPlateWasherCheckDone X1.2 |
| 13 → 14 Bunk for component press | CheckBunkForComponentPress X4.0 | BunkForComponentPressCheckOK X4.1 | BunkForComponentPressCheckDone X4.2 |
| 16 → 17 No bunk | CheckNoBunk X4.3 | NoBunkCheckOK X4.5 | NoBunkCheckDone X4.4 |
| 20 → 21 Split pin and washer | CheckSplitPinAndWasher X3.0 | SplitPinAndWasherCheckOK X0.6 | SplitPinAndWasherCheckDone X0.5 |
| 22 → 23 Cap | CheckCap X3.1 | CapCheckOK X1.3 | CapCheckDone X1.4 |
| 24 → 25 Bunk for cap press | CheckBunkForCapPress X2.4 | BunkForCapPressCheckOK X1.5 | BunkForCapPressCheckDone X1.6 |

All of these tags are in DB500. The split pin check is on hold until production pictures exist; its handshake is still in place.

### 3.4 Value and event tags (written by the PLC)

| State | Tag | Address | Type | Meaning |
|---|---|---|---|---|
| 11 | `PLC_PC_Station2TorqueValueSet` | DB500,X2.6 | bool | Torque 1 value is ready |
| 12 | `PLC_PC_Station2TorqueValue` | DB500,R8 | float | Torque 1 (Nm) |
| 12 | `PLC_PC_Station2RotationDuringTighteningDone` | DB500,X3.6 | bool | Read and logged only |
| 15 | `PLC_PC_ComponentPressDone` | DB500,X3.5 | bool | Component press finished |
| 18 | `PLC_PC_Station2TorqueValueSet` | DB500,X2.6 | bool | Torque 2 value is ready (same tag as state 11) |
| 19 | `PLC_PC_Station2TorqueValue` | DB500,R8 | float | Torque 2 (Nm) |
| 26 | `PLC_PC_Station4CapPressDone` | DB500,X3.2 | bool | Cap press finished |
| 27 | `PLC_PC_Station3TorqueValueSet` | DB500,X2.7 | bool | Free-rotation torque value is ready |
| 28 | `PLC_PC_Station3TorqueValue` | DB500,R12 | float | Free-rotation torque (Nm). The cycle ends here |

---

# Part A — Protocol v1 (as built)

## 4. Timing properties

| Property | Value | Use |
|---|---|---|
| `plc.read.interval` | 0.2 | Read loop period |
| `plc.write.interval` | 0.1 | Write loop period |
| `plc.connection.check.interval` | 1.0 | Connection check |
| `plc.sleeptime.between.ok.and.done` | 0.05 | Gap between writing OK and writing Done |
| `plc.sleeptime.between.done.and.reset_done` | 0.4 | Time Done is held True after an OK result |
| `plc.sleeptime.between.done.and.re_read` | 0.3 | Time Done is held True after a NOT OK result |
| `plc.handshake.torquevalues.reset.delay.sec` | 2.0 | Delay before the PC resets a torque Set / Value it has read |
| `plc.handshake.componentpress.reset.delay.sec` | 1.5 | Delay before the PC resets a press Done it has read |
| `plc.camera.answer.timeout.sec` | 20 | No camera answer in this time means NOT OK. At least the vision check timeout + 8 s; a smaller value is raised |
| Vision worker deadline | 12 | A check still running at 12 s is killed and counts as NOT OK |

## 5. Sequences

### 5.1 Check handshake (states 1/2, 3/4, … 24/25)

```
PLC                                  PC
Trigger := 1  ───────────────────▶  (read state sees Trigger = 1)
                                     request picture / QR result
                                     … camera answer (≤ 20 s), or NOT OK on timeout
                     ◀────────────  OK := result                       (1)
                                     wait 0.05 s                        (2)
                     ◀────────────  Trigger := 0       [PC writes a PLC_PC tag]   (3)
                     ◀────────────  Done := 1                          (4)
PLC latches OK on Done rising        hold 0.4 s (OK) or 0.3 s (NOT OK)  (5)
                     ◀────────────  Done := 0                          (6)
                     ◀────────────  OK := 0 (after OK) | Trigger := 0 (after NOT OK)  (7)
                     ◀────────────  QR NOT OK only: CheckQRCode := 1   (8)
                                     state changes: OK → next read state; NOT OK → same read state
```

Rules:

1. Every write is a retried write (§2). If the cycle is aborted meanwhile, the sequence stops and nothing more is written for the old cycle. The state check and the write are one step that an abort cannot come between: a write already on its way when an abort starts lands first, and the abort's reset then sets the tag back (CHANGES §84).
2. **The PLC must read OK only on the rising edge of Done.** OK is valid from step (1) to step (7).
3. After a NOT OK result, the PC goes back to the read state of the same check. **To ask for a retake, the PLC raises the Trigger again.** The PC acts on the Trigger as soon as it reads it True (the "read False first" rule was removed in CHANGES §80). **The PLC must therefore raise each Trigger once per two-hand-button press and let the PC's False stand.** A PLC that sets the Trigger True again by itself, or holds it, gets the picture retaken at once.
4. QR code NOT OK means the cycle settings could not be written or the QR code was rejected. The PC re-arms CheckQRCode (step 8) so that the operator can scan again.

### 5.2 Settings (state 2)

Before step (1) of the QR handshake, the PC:

1. Writes side, torque setpoints, RPM and rotation counts (§3.2), each read back.
2. Writes `PC_PLC_Skip_Second_Tightening` (§3.2) if the PLC has it, read back.
3. Writes QR OK = True if the side (and the skip tag, where it exists) was written, otherwise NOT OK.

### 5.3 Value and event states (11/12, 15, 18/19, 26, 27/28)

1. The PLC sets `…ValueSet` (or `…PressDone`) to 1. For the value states the value must already be in the value tag.
2. The PC reads the value, sends it to the frontend, and moves to the next state.
3. After `plc.handshake.torquevalues.reset.delay.sec` (2.0 s; presses: `plc.handshake.componentpress.reset.delay.sec`, 1.5 s) the PC writes `…ValueSet` (or `…PressDone`) := 0. **This is a PC write to a PLC_PC tag.**
4. The PLC must not set the same tag to 1 again for the next event until it has seen it go to 0.

Note: states 11 and 18 both use `Station2TorqueValueSet`. The reset in step 3 is what stops torque 1 from triggering state 18 straight away.

### 5.4 PC writes to PLC-owned tags in v1

| Tag | When the PC writes it | Why |
|---|---|---|
| Every check trigger | Steps (3) and (7) of §5.1 | Ends the request |
| `PLC_PC_CheckQRCode` | QR NOT OK; end of cycle; reset for next cycle | Arms the QR read |
| `Station2/3TorqueValueSet`, `ComponentPressDone`, `Station4CapPressDone` | 0.4 s after reading | Ends the event |

These dual writes are why v1 depends on timings. If the PLC writes the same tag in the same scan in which the PC resets it, one of the two writes is lost. Part B removes this.

### 5.5 Abort

An abort can come from:

- the emergency tag;
- the frontend (operator, or frontend closed);
- the heartbeat server going down;
- a restart of the IOServer.

On an abort the PC:

1. Stops any handshake in progress (§5.1, rule 1).
2. Cancels pending delayed resets.
3. Resets all per-cycle `PC_PLC_*` tags to 0, and the PLC_PC tags listed in §5.4.
4. Arms CheckQRCode.
5. Goes to state 1.
6. Pulses the optional cycle-aborted tag, if one is configured.

### 5.6 Loss of connection and restarts

| Event | PC behaviour | PLC expectation |
|---|---|---|
| PLC unreachable | Reads and writes stop. The connection is retried every 1 s. The frontend shows the PLC as disconnected | PLC sees ConnectionStatusTag stop toggling |
| PLC restart | The PLC resets all its values and sets `PC_PLC_LH_RH_Selection` := 0. After the reconnect the PC reads the side: if it differs from the value the PC wrote in state 2, the PC treats it as a PLC restart, aborts the cycle and resets (§5.5). The same value means the PLC kept its memory, and the cycle carries on | Both vendors do this. On Siemens, DB101 must not be retentive |
| PC (IOServer) restart | The `ioserver:started` key exists, so the IOServer sends an abort to the frontend and camera, resets its tags, and starts in state 1 | PLC sees all PC_PLC tags at 0 |
| WatchTag ≠ 1234 | Treated as wrong DB or not connected | — |

### 5.7 Server-down rules (from the heartbeat server)

These rules apply only after the PC has seen the heartbeat server at least once.

| Server down | Effect on the PLC interface |
|---|---|
| Camera | Each check that is waiting is answered NOT OK at once |
| QR code | Nothing. The PC does not touch CheckQRCode |
| Database | The current cycle finishes. The next cycle is held in state 1, with the notice "Continue till end of cycle" |
| Frontend or heartbeat server | Abort (§5.5), and hold in state 1 |
| Redis (`redis` field) | Hold before the next cycle, reason "Redis not storing data" |

---

# Part B — Protocol v2 (proposal: single-owner tags with request/acknowledge)

## 6. Goals

1. Every tag has exactly one writer. The PC never writes a `PLC_PC_*` tag, and the PLC never writes a `PC_PLC_*` tag. The one exception is the side reset to 0 on PLC restart, which is kept as the restart signal (§6.4).
2. No correctness depends on a time. Each side moves on only after it **sees** the other side's acknowledgement. Times are used only for timeouts.
3. Every request can be traced. A request number lets both sides, and the logs, tell one request from the next.

### 6.1 Four-phase handshake (for checks)

```
          PLC                                     PC
 (idle)   Req=0                                   Done=0, OK=0
 1        Req := 1   ───────────────────────▶
 2                                                 OK := result; then Done := 1
 3        sees Done=1, latches OK; Req := 0 ───▶
 4                                                 sees Req=0: Done := 0, OK := 0
 5        sees Done=0 → may raise Req again (a retake) or go on
```

- The PC writes OK before Done, in a separate write. A write of both in the same S7 request is also acceptable, because both tags are in DB500 and the order within a request is kept. A separate write is simpler to reason about.
- **Retake after NOT OK.** The PLC lowers Req (phase 3), waits for Done = 0 (phase 5), then raises Req again. Because the PC answers only a rising edge of Req that follows Done = 0, a held Req can never be answered twice.
- **The PC never lowers Req.** v1 steps (3), (7) and (8) are removed.
- **QR NOT OK.** The PLC re-arms CheckQRCode after phase 5. The PC no longer arms it.

### 6.2 Value and event handshake

```
 PLC: Value := v; ValueSet := 1
 PC : reads v; Ack := 1
 PLC: sees Ack=1; ValueSet := 0
 PC : sees ValueSet=0; Ack := 0
```

This removes the 0.4 s reset of v1 §5.3. It also makes the shared `Station2TorqueValueSet` safe: torque 2 cannot be seen until the PLC has lowered ValueSet and the PC has lowered Ack.

### 6.3 Request number (optional, recommended)

| Tag | Type | Writer | Meaning |
|---|---|---|---|
| `PLC_PC_RequestNo` | int | PLC | PLC adds 1 (wrapping at 32767) each time it raises any Req or ValueSet, before raising it |
| `PC_PLC_AckNo` | int | PC | PC copies RequestNo in phase 2, before Done or Ack |

With these tags:

- The PLC accepts Done or Ack only if AckNo = RequestNo.
- The PC logs RequestNo with each picture, so PLC and PC logs can be matched.

### 6.4 Restart and abort in v2

- **PLC restart.** As in v1: the PLC resets its values and writes side := 0. The PC treats side = 0 while in a cycle as "PLC restarted": it aborts the cycle and lowers all its tags. No session token is needed.
- **PC restart.** The PC starts with all its `PC_PLC_*` tags at 0. If the PLC holds Req = 1 on PC start, the PC treats it as a fresh request only for the QR read; for any other check it waits for Req = 0.
- **PC abort.** The PC lowers its own tags and pulses `PC_PLC_CycleAborted` (this becomes a required tag in v2). The PLC drops its requests and returns to waiting for a QR read.

### 6.5 Timeouts (both sides)

| Side | Waits for | Timeout | Action |
|---|---|---|---|
| PC | Camera answer | 20 s (existing) | OK := 0, Done := 1 (NOT OK) |
| PLC | Done after Req | 30 s (proposed) | Alarm; drop Req; treat as NOT OK |
| PLC | Done = 0 after Req = 0 | 5 s (proposed) | Alarm. The PC is not following; stop the cycle |
| PC | Req = 0 after Done = 1 | 5 s | Log warning; hold Done |
| PLC | ConnectionStatusTag toggle | 6 s (recommended; confirm what the PLC programme does today) | PC lost |

### 6.6 New tags needed

Addresses are a proposal: the free bits after the last used DB500 bit. **The PLC programmer must confirm them.**

| Tag | Proposed address | Type | Writer |
|---|---|---|---|
| `PC_PLC_Station2TorqueValueAck` | DB500,X4.6 | bool | PC |
| `PC_PLC_Station3TorqueValueAck` | DB500,X4.7 | bool | PC |
| `PC_PLC_ComponentPressDoneAck` | DB500,X5.0 | bool | PC |
| `PC_PLC_CapPressDoneAck` | DB500,X5.1 | bool | PC |
| `PC_PLC_CycleAborted` | DB500,X5.2 | bool | PC |
| `PLC_PC_RequestNo` | DB500,I14 | int | PLC |
| `PC_PLC_AckNo` | DB500,I16 | int | PC |

All existing check tags keep their addresses; only who writes them changes.

### 6.7 Migration

1. A property selects the protocol: `plc.handshake.protocol=v1|v2`. The default stays v1 until the PLC programme supports v2.
2. In v2 the PC code removes the PC writes listed in §5.4 and the timed resets, and adds the acknowledge waits.
3. Before going live:
   - Dry runs with the PLC simulator for both protocols, both vendors.
   - A trial on the machine with the PLC in single-step mode.
4. Machine 1 (Allen-Bradley) can move to v2 later with the same PC code, once its PLC programme is changed.

## 7. Sign-off

| Role | Name | Protocol | Date | Signature |
|---|---|---|---|---|
| PLC programmer (Siemens, Machine 2) | | v1 / v2 | | |
| PLC programmer (Allen-Bradley, Machine 1) | | v1 / v2 | | |
| PC software | | v1 / v2 | | |
| Customer (Ashok Leyland) | | v1 / v2 | | |
