> **Snapshot of 2026-10-30, kept for reference.** The state table still holds. Parts of the state-2 section were later changed: the settings now come from the `machine_settings` table, there is no model write, and the rotation counts are written. See `PLC_Interface_Spec.md` §3.2 and `../CHANGES_MACHINE2.md` §17–24.

# Machine 2 (Siemens S7-1515): state sequence vs Machine 1 (AB Micro 850)

Agreed with Uddi on 2026-10-30. Machine 1's sequence is the one in `statemachine/StateMachine.py`, commit `5633458`.

## Summary of the change

Machine 2 has the same sequence as Machine 1, with **one state removed**: M1 state 13, `READ_FREE_ROTATIONS_DONE`.

Every M1 state from 14 to 29 moves down by one. Machine 2 therefore has 28 states. After state 28 completes, the machine returns to state 1.

## Machine 2 state table

| M2 | State | M1 | Operator / machine action |
|---|---|---|---|
| 1 | READ_QR_CODE | 1 | Place the knuckle and scan its QR code. The PC reads the code. |
| 2 | WRITE_QR_CODE | 2 | The PC writes side (LHS/RHS), model number, rotation counts and RPM to the PLC. |
| 3 | READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE | 3 | Press both buttons (2HB) for the knuckle check. The PLC locks the knuckle if it is OK. |
| 4 | WRITE_RESULT_OF_CHECKING_KNUCKLE | 4 | |
| 5 | READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING | 5 | Place the hub, with the bottom bearing already assembled. Press 2HB for the hub check. |
| 6 | WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING | 6 | |
| 7 | READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING | 7 | Place the bearing. Press 2HB for the bearing check. |
| 8 | WRITE_RESULT_OF_CHECKING_TOP_BEARING | 8 | |
| 9 | READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER | 9 | Place the washer and nut. Press 2HB for the nut-and-washer check. |
| 10 | WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER | 10 | |
| 11 | READ_TIGHTENING_TORQUE_1_DONE | 11 | Press 2HB to send the part to station 2. Automatic tightening runs with simultaneous rotation. |
| 12 | READ_TIGHTENING_TORQUE_1 | 12 | The PC reads torque 1. The part returns to station 1. |
| — | ~~READ_FREE_ROTATIONS_DONE~~ | 13 | **Removed in M2.** Rotation happens during the tightening at station 2. |
| 13 | READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS | 14 | Place the bung. Press 2HB for the bung check. |
| 14 | WRITE_RESULT_OF_CHECKING_BUNG_FOR_COMPONENT_PRESS | 15 | |
| 15 | READ_COMPONENT_PRESS_DONE | 16 | Press 2HB to send the part to station 4. The Mercury press presses the component and the part returns to station 1. |
| 16 | READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG | 17 | Remove the bung. Press 2HB for the no-bung check. |
| 17 | WRITE_RESULT_OF_CHECKING_NO_BUNG | 18 | |
| 18 | READ_TIGHTENING_TORQUE_2_DONE | 19 | Press 2HB to send the part to station 2. Tightening runs with simultaneous rotation. |
| 19 | READ_TIGHTENING_TORQUE_2 | 20 | The PC reads torque 2. The part returns to station 1. |
| 20 | READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER | 21 | Put on the split pin and washer. Press 2HB for the check. |
| 21 | WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER | 22 | |
| 22 | READ_TAKE_PICTURE_FOR_CHECKING_CAP | 23 | Place the cap. Press 2HB for the cap check. |
| 23 | WRITE_RESULT_OF_CHECKING_CAP | 24 | |
| 24 | READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS | 25 | Place the bung. Press 2HB for the bung check. |
| 25 | WRITE_RESULT_OF_CHECKING_BUNG_FOR_CAP_PRESS | 26 | |
| 26 | READ_CAP_PRESS_DONE | 27 | Press 2HB to send the part to station 4. The Mercury press presses the cap. |
| 27 | READ_FREE_ROTATION_TORQUE_1_DONE | 28 | The part goes to station 3 and free rotation is measured. |
| 28 | READ_FREE_ROTATION_TORQUE_1 | 29 | The PC reads the free-rotation value. The part returns to station 1 and the lock is released. **Then back to state 1.** |

The PC needs both tightening torques and the free-rotation value.

## State 2 (WRITE_QR_CODE): what the PC writes to the PLC (as agreed on 2026-10-30)

| Parameter | S7 type | Value / range | Notes |
|---|---|---|---|
| Side | INT | 1 = LHS, 2 = RHS | From the QR part number, as on M1 |
| Model number | INT | 1 = DOST, 2 = DOSTPLUS | **New for M2.** M1 never wrote it. |
| Tightening torque setpoint, round 1 | REAL | up to 45 Nm (nominal 19) | **-1 = do not write the tag** |
| Tightening torque setpoint, round 2 | REAL | up to 45 Nm (nominal 19) | -1 = skip. May differ from round 1. |
| Rotation RPM, round 1 | INT | 5–30 | **-1 = do not write the tag** |
| Rotation RPM, round 2 | INT | 5–30 | -1 = skip. May differ from round 1. |
| Rotation counts | — | — | **Dropped in M2.** M1's `NoOfRotation1/2CW/CCW` are not used. |

- **Setpoint source.** The four setpoints come from the **properties file**. In M1 the rotation settings came from the `machine_settings` DB table, edited through the frontend dialog. For M2 they are configuration, not DB data.
- **Torque handling.** The PC only records the torques it reads back: torque 1, torque 2 and free rotation. There is no pass/fail limit, exactly as on M1. The setpoint is a command to the PLC, not an acceptance criterion.
- **Knuckle lock and release.** These are handled entirely by the PLC. The PC neither commands nor monitors them, the same as on M1.

## Code that depends on M1 state numbers or on state 13

These all need to change for M2:

- **`statemachine/StateMachine.py`**
  - The enum values.
  - The instruction map, which uses `stateNumber=N`.
  - The wrap-around bound `READ_FREE_ROTATION_TORQUE_1`.
- **`ApplicationConfiguration.properties`**
  - `state.N.instruction` for N = 13 to 29 must be renumbered.
  - The per-state target image size, `MACHINE_STATE_{N}_TARGET_WIDTH/HEIGHT_KEY` (`Configuration.py:2887`), is keyed by the state's integer value.
- **IO server: state-13 handling**
  - The state-13 branch in `process_current_read_state`. It records the end of T4 and schedules the reset of the Station 3 tags (`IOServer.py:1225-1237`).
  - The tag list for state 13 (`:732`).
  - The cycle-time definition of T4 ("NutAndPlateWasher → Station3RotationDone").
- **Station 3 tag sharing**
  - In M1, state 13 and state 28 share the tag `PLC_PC_Station3ValueSet`.
  - With state 13 removed, only M2 state 27 uses that tag. This removes the shared-tag risk.
- **Frontend**
  - The hard-coded per-state handlers and the "Free Rotations" result tile (`AshokLeylandFrontEnd.py:1164-1398`).
  - The DB record fields `free_rotations_done` and `free_rotations_datetime`.
