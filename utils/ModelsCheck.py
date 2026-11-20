# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""
ModelsCheck - MainProgram's start-up check of the vision model files (CHANGES 175).

The vision checks need the MobileSAM and YOLO weights under <folder of MainProgram.exe>/models/ (not in the exe:
runNuitka.bat leaves models/ out). Without them the camera server stopped itself at start (sys.exit(1) in
CameraProcessorServer.CameraServer), the process supervisor started it again every ~10 s, and the reason never
reached the log. MainProgram now checks the files before it starts any server process; if one is missing it
shows a message and exits without starting anything.

The files: models.required.files (tuning block of the properties file), paths relative to the project root
(the folder of MainProgram.exe, or of MainProgram.py when run from the source). A file counts as present when it
exists and is not empty. Only the weights files are read at run time (camera/ModelManager.py); the mobile_sam
code itself is compiled into the exe.
"""
import os
from typing import List, Tuple

from Configuration import CosThetaConfigurator

REQUIRED_FILES_DEFAULT = ("models/MobileSAM/weights/mobile_sam.pt,"
                          "models/MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt")
REQUIRED_FILES = CosThetaConfigurator.getTuning("models.required.files", REQUIRED_FILES_DEFAULT)
MESSAGE_SEC = CosThetaConfigurator.getTuning("models.missing.message.sec", 5, minimum=1)


def requiredModelFiles() -> List[str]:
    """The model files the application needs, relative to the project root."""
    return [f.strip().replace("\\", "/") for f in str(REQUIRED_FILES).split(",") if f.strip()]


def missingModelFiles(root: str) -> List[Tuple[str, str]]:
    """(relative path, why) of every required file that is missing or empty under root; [] when all are there."""
    problems = []
    for relative in requiredModelFiles():
        path = os.path.join(root, *relative.split("/"))
        if not os.path.isfile(path):
            problems.append((relative, "missing"))
        elif os.path.getsize(path) == 0:
            problems.append((relative, "empty (0 bytes)"))
    return problems


def missingModelsMessages(root: str, problems: List[Tuple[str, str]]) -> Tuple[str, str]:
    """(screen text, console text) when files are missing. The screen text is one paragraph: the error popup
    (SimplePopups.showErrorMessage) re-wraps its text and drops line breaks; the console text lists each file."""
    folder = os.path.join(root, "models")
    names = ", ".join(f"{relative.split('/')[-1]} ({why})" for relative, why in problems)
    screen = (f"The models directory is missing or incomplete and is needed for the application: {folder} - "
              f"{names}. The application closes in {MESSAGE_SEC} seconds.")
    lines = "\n".join(f"  - {os.path.join(root, *relative.split('/'))}  ({why})" for relative, why in problems)
    console = (f"The models directory is missing or incomplete; it is needed for the application (vision checks):\n"
               f"{lines}\nCopy the models folder next to MainProgram.exe and start the application again. "
               f"Nothing was started; closing in {MESSAGE_SEC} s.")
    return screen, console
