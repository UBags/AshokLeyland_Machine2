# Machine 2: frontend, database, Redis and IOServer changes

- **Branch:** `machine2-frontend-db`
- **Base:** `main` @ `5633458`
- **Date:** 2026-10-30
- **PLC:** still Allen-Bradley (`pycomm3.LogixDriver`). The Siemens port comes later.

## Why

Machine 2 has the same sequence as Machine 1 except for one difference. The free rotation now happens *during* tightening at station 2, so Machine 1's separate state 13 (`READ_FREE_ROTATIONS_DONE`) is gone. Machine 1 states 14–29 become states 13–28, and the cycle wraps to state 1 after state 28.

In state 2, the PC now writes side, model, tightening-torque setpoints and rotation RPMs. The rotation counts are no longer written.

---

## 1. Database: `persistence/`

| File | Change |
|---|---|
| `Persistence.py`: DDL (`create_tables_string`) | Removed `free_rotations_done` and `free_rotations_datetime` from `hub_and_disc_assembly_data`. |
| `Persistence.py`: DDL migration | Added `ALTER TABLE … DROP COLUMN IF EXISTS free_rotations_done / free_rotations_datetime`. It runs on every start through `setDatabaseName()` → `createAllTables()`. A database created by Machine 1 code loses these two columns; all other data is kept. On a new database the statement does nothing. |
| `Persistence.py`: `insertData()` | Removed the two parameters, their result normalisation, the `(result, datetime)` pair in `step_data`, and the two columns in the INSERT. |
| `Persistence.py`: report queries | Removed the two columns from `all_columns_record_header` and from every SELECT behind the frontend reports: `getDataByModelName`, `getDataByModelNameAndDateLimits`, `getDataOfTodayByModelNumber`, `getDataByDateLimits` and `getAllDataOfToday`. These now produce a 49-column CSV. |
| `Persistence.py`: legacy template `insert_table_data` | Removed the two columns. |
| `DBServer.py` | Removed the two fields from the unpacking of `readDataInDatabaseServerFromFEServer()` and from the `insertData()` call. |
| `Test_DBServer_Persistence.py` | Removed the two fields from its test records. |

The `machine_settings` table and the frontend's **Machine Settings** dialog are left as they are. On Machine 2 they no longer drive the PLC (see §4).

## 2. Redis messaging: `utils/RedisUtils.py`

| Link | Change |
|---|---|
| **FEServer → DBServer** (`fe2db.q`) | `sendDataFromFEServerToDatabaseServer()` lost the `freeRotationDone` and `freeRotationDatetime` parameters and stream fields. `readDataInDatabaseServerFromFEServer()` no longer reads them, and its return tuple (all 3 return paths) is 2 elements shorter. The key constants `freeRotationDoneKeyString` and `freeRotationDatetimeKeyString` were removed. The record now has 44 fields. |
| **IOServer → FEServer** (`io2frontend.q`) | No format change. The IO server no longer sends a `READ_FREE_ROTATIONS_DONE` message, and the frontend no longer waits for one. States travel as their integer values, and both ends use the same renumbered enum. |
| **FEServer → IOServer** (`fe2io.q`) | No change. The queue is still unused by the frontend. |
| **MainProgram ↔ FEServer** (`stopcommandQ` / `stoppedresponseQ`) | No change. Neither side refers to state numbers or to the removed fields. |
| **Monitoring/heartbeat server ↔ all** (`*2hb.q`, `hb2fe.q`, `hb2io.q`, `alarm.q`) | No change. Payloads are ALIVE/DEAD and alarm only. |

## 3. State machine and configuration

| File | Change |
|---|---|
| `statemachine/StateMachine.py` | `MachineState` now has the 28 Machine 2 states. `READ_FREE_ROTATIONS_DONE` was removed and later states renumbered (13 = bunk for component press … 28 = free-rotation torque). The instruction map is now built from the enum, using `state.<value>.instruction`, instead of 29 hand-written lines. `MAX_SECONDS_TO_WAIT_FOR_CAMERA_FEEDBACK` moved out of the IntEnum; inside it, it had silently become an alias of state 5. Wrap and clamp logic still keys off `READ_FREE_ROTATION_TORQUE_1`, now 28. |
| `ApplicationConfiguration.properties` | `state.13.instruction` (free rotation) removed; 14–29 renumbered to 13–28. Per-state image sizes renumbered from `machine.state.{14,17,21,23,25}` to `{13,16,20,22,24}`. Machine 1 rotation-count tag names (`NoOfRotation*`, `RotationUnitRPM`) replaced by the Machine 2 tags and setpoints below. |
| `Configuration.py` | `MACHINE_STATE_{14,17,21,23,25}_TARGET_*_KEY` renamed to `{13,16,20,22,24}`. New getters for the Machine 2 tag names and setpoints. Setpoints default to **-1**, as do missing or invalid values. |

New properties (Allen-Bradley tag names are placeholders; change them to match the PLC):

```properties
pc.plc.lh.rh.selection.tagname=LH_RH_Selection              # INT  1=LHS, 2=RHS
pc.plc.model.selection.tagname=ModelSelection               # INT  1=DOST, 2=DOSTPLUS
pc.plc.tightening.torque1.setpoint.tagname=TighteningTorque1Setpoint   # REAL Nm
pc.plc.tightening.torque2.setpoint.tagname=TighteningTorque2Setpoint   # REAL Nm
pc.plc.rotation1.rpm.tagname=RotationRPM1                   # INT
pc.plc.rotation2.rpm.tagname=RotationRPM2                   # INT
tightening.torque1.setpoint.nm=19.0      # -1 = do not write
tightening.torque2.setpoint.nm=19.0      # -1 = do not write
rotation1.rpm=-1                         # -1 = do not write  <-- set the real value (5..30)
rotation2.rpm=-1                         # -1 = do not write  <-- set the real value (5..30)
```

## 4. IOServer, Allen-Bradley: `costhetaio/IOServer.py`

- **State 13 removed.** The `READ_FREE_ROTATIONS_DONE` branches are gone from `getCurrentTagsToBeRead()` and `process_current_read_state()`. The class attribute `PLC_PC_Station3RotationDone` and its entries in `resetAll()` and `getAllTagValues()` are removed as well. As a result, the station-3 tag (`PLC_PC_Station3ValueSet`) is now used only in state 27, and the old shared-tag hazard between states 13 and 28 disappears.
- **State 2 writes**: `writeRotationSettingsToPLC()` is replaced by `writeCycleSettingsToPLC()`.
  - `LH_RH_Selection` (1/2) and `ModelSelection` (1 = DOST, 2 = DOSTPLUS) come from the QR code.
  - The torque setpoints are written as REAL and the RPMs as INT. **Any setpoint of -1 is skipped.**
  - An unknown side or model logs an error and writes nothing, which is the same behaviour as Machine 1 for an unknown side.
  - Machine settings are no longer read from the database, and the `getMachineSettings` import was dropped.
- **Cycle-time T4** now runs from the nut/plate-washer trigger to the reading of torque 1. Previously it ended at the "free rotation done" step, which no longer exists.
- No other changes. The handshake, emergency abort, heartbeat, watchdog and torque reset logic are untouched.

## 5. Frontend: `frontend/`

| File | Change |
|---|---|
| `newwidgets/CosThetaResultsContainer.py` | "Free Rotations" tile removed. New **"Free Rotation Torque"** tile added for the station-3 reading; Machine 1 squeezed this value into the Cap Press tile. Tiles are re-ordered by the Machine 2 sequence. Row 1: Knuckle, Hub, Top Bearing, Nut & Washer, Torque 1, **Bunk - Comp Press** (renamed), Component Press. Row 2: Remove bunk, Torque 2, Split Pin & Washer, Cap, Bunk Check, Cap Press Result, Free Rotation Torque. Matching setters and defaults updated. |
| `AshokLeylandFrontEnd.py` | Free-rotation state, handlers and reset removed. After torque 1, the frontend goes straight to the bunk-for-component-press picture. The free-rotation torque goes to its own tile, and "Cap Press Result" now shows only "Done". The overall OK/NOT OK result no longer includes free-rotation-done, and the DB record no longer carries it. |
| `AshokLeylandFrontEnd.py`: **bug fix** | Torques were truncated with `int(value*100)/100`, so 19.08 became **19.07** on screen *and in the database* because of float error. This was found in the dry run and replaced with `round(value, 2)` for torque 1, torque 2 and the free-rotation torque. |

## 6. Verification (synthetic dry runs; harnesses in `test/machine2_dryrun/`)

| Run | Result |
|---|---|
| Migration: database built with `main` (Machine 1) code plus one Machine 1 row, then Machine 2 code started on it | 52 → 50 columns. Only `free_rotations_done` and `free_rotations_datetime` were dropped, and the existing row survived. A second start makes no further change. |
| FE record → `fe2db.q` → real DBServer → PostgreSQL | Row stored with correct model, side, torques and results; image files written. |
| All five report queries plus the three counters | Run without errors. CSVs are a consistent 49 columns with no `free_rotations*` columns. |
| IOServer with a simulated Allen-Bradley PLC, two full cycles | The state trace is exactly `…26, 27, 28, 1, 2, 3 … 28`. In state 2 it wrote `LH_RH_Selection=1`, `ModelSelection=1`, and both torque setpoints at 19.0; the RPMs were correctly **not** written (-1). IO→FE messages: torque 1, component press, torque 2, cap press, then free-rotation torque with `moveAheadToNextComponent`. |
| Real frontend rendered at 1920×1080 (offscreen), fed a full cycle over Redis | Layout matches the agreed mock-up. The frontend walked the 15 operator-facing states in order and emitted a 44-field DB record. |
| **Integrated**: simulated PLC, camera and QR, with the real IOServer, frontend, DBServer and heartbeat server, for one DOSTPLUS RHS cycle | All four connection tiles green. State 2 wrote side=2, model=2. The DB row reads DOSTPLUS / RHS / 2.5T / 19.08 / 19.03 / 2.41 / all OK. |

## 7. Not changed, and open items

- **Not updated:**
  - `siemens/IOServer.py`, the Siemens port. It will be redone for the S7-1515 in the next phase.
  - The Machine 1 test harnesses `costhetaio/TestIOServerWithPLC.py`, `TestIOServerWithMockPLC.py` and `MockPLC.py`.
  - Everything under `test/`.

  These still refer to the old state 13 and will fail if run as-is.
- `Configuration.getPlcPcStation3RotationDoneTagName()` and the `plc.pc.station3.rotation.done.tagname` property are now unused. They are left in place.
- `ApplicationConfiguration - Production - 29-01-2026.properties` (the Machine 1 snapshot) is left untouched. **Do not deploy it on Machine 2.**
- **Machine Settings menu** (rotation counts and RPM, held in the database): it still opens and saves, but it no longer affects the PLC on Machine 2. Decide whether to remove it or repurpose it.
- **To set before commissioning:**
  - `rotation1.rpm` and `rotation2.rpm`;
  - confirm the 19.0 Nm torque setpoints;
  - the actual PLC tag names for the six state-2 tags.
- The reporting web services (`endpoints/`, started by `OtherServices.py`) still query the old Machine 1 schema and remain broken. This is a pre-existing issue, unrelated to this change.
