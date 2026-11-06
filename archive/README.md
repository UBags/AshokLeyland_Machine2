# archive/

Files moved out of the main tree on 2026-11-06 (CHANGES_MACHINE2 section 82), with `git mv`, so each keeps its
history (`git log --follow archive/<path>`). Each keeps its original path under `archive/`
(e.g. `test/SAMSegmentation_v0.996.py` -> `archive/test/SAMSegmentation_v0.996.py`).

Nothing in the application, the build (`runNuitka.bat`) or the dry runs imports or runs any of them: this was
checked before the move (imports, `.bat` / `.vbs` / `.md` references). They are kept for reference, not maintained, and
may not run against the current code.

What was moved:

- **Older versions** (`_Old`, `-Old`, `_v0.x`, `_v1`, `_v3`, `_v_0.7`): `VerifyIntegrity_v0.9_ToBeTestedForIssues.py`,
  `runNuitka_v0.6.bat`, `frontend/SimplePopups_Old.py`, and in `test/`: `QRCodeServer_Old`, the `RecordVideo*_Old*` /
  `-Old` recorders, `RemoveScribblesFromHub_v0.5/0.6`, `SAMSegmentation_v0.996 / v1_Count_Hub_Bolts / v3_FindNut`,
  `Test_ProcessBolts_v0.6`, `TopBearingSegmentation_v0.82`, `BungSegmentation_GUI_v_0.7`,
  `IdentifyNutsInBatch_v0.90_BestSoFar_Refactored`.
- **General experiments, not about this machine**: Python / Qt / library trials (`BreakingOutOfLoops`, `TestMetaclass`,
  `TestGetAttr`, `TestInnerFunc`, `Test_Inner`, `TestClassFunctions`, `TestFunctionCallByVariableName` +
  `FunctionNamesForTestingCall`, `TestStringAsList`, `TestNumbers`, `TestTimeFormat`, `TestTimeFunctions`,
  `TestingClassAndStaticVariables`, `TestingThreading`, `TestExecutorWithQueue`, `TimeForDeepCopy`, `Encoding`, `Fonts`,
  `TestSizing1`, `ComboDyamicUpdation`, `OpeningMultiWindowsInPyside`, `PSG_Tests`, `PDFTest`, `PDFTest1`, `Chat_GUI`,
  `Slide-Maker`), other projects' hardware (`KEMS FE`, `TestKEMSCamera`, `TestKEMSCameraRecording`,
  `TestCountCapsulesInRightAndLeftDisc`, `TestAdam6052`, `TestMicro850`) and webcam trials (`TestWebcamFunctionality`,
  `TestV4L2`).

Left in `test/` on purpose (about this machine; decide later): the vision development tools (nut, bunk, hub, top
bearing, split pin prototype `Test_FindSplitPin.py`), the camera / RTSP / FFmpeg diagnostics, `Test_DBServer_And_Persistence`,
`Test_LoggingServer`, `Test_CameraServer_Live_Camera`, `InsertSyntheticDataIntoProduction`, the abort / heartbeat
simulators and the current `RecordVideo*` scripts.
