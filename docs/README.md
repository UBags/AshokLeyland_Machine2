# docs/

| File | What it is |
|---|---|
| `HLD.md` | High-level design: processes, queues, Redis keys, the cycle, failure handling, configuration |
| `LLD.md` | Low-level design: IOServer threads and state table, each server's internals, database schema, message fields, tests |
| `PLC_Interface_Spec.md` | PC–PLC tags, addresses, handshakes and timings. Part A is the protocol as built (v1); Part B is the v2 proposal, with a sign-off table |
| `machine2-sequence.md` | The 28 Machine 2 states against Machine 1's 29 (snapshot of 2026-10-30; the state-2 settings have changed since) |
| `vision_positions_machine2.md` | Every Machine 2 vision position and size after the camera move of 13 Nov 2026, as picture coordinates and as offsets from the hub centre; since CHANGES 102 also the per-type reference centres the knuckle frame places (`camera/VisionFrame.py`) |
| `plant-pc-verification.md` | Checks to run on the plant PC before production: the vision worker inside the exe, check times against the timeout, process restart, frozen screen, real QR codes, the knuckle frame; with a results table |
| `machine2-plant-pc-setup.md` | Plant PC set-up notes: Windows updates, restore points, start at logon, Redis on WSL, first start (2026-11-05) |
| `architecture.md` | Baseline review of the Machine 1 code at commit `5633458` (2026-10-30), with findings R1–R17 |
| `costhetaio_classes.mmd` / `.svg` / `.png` | Class diagram of `costhetaio` (commonplc, allenbradley, siemens) |
| `Machine Sequence - 1.jpg`, `- 2.jpg` | The machine sequence sheets |
| `knuckle_regions_machine2.jpg`, `nut_washer_comparison_fix.png`, `top_bearing_ring_refinement.png` | Figures referred to in `../CHANGES_MACHINE2.md` (§95, §41, §46) |

The change history is `../CHANGES_MACHINE2.md`.
