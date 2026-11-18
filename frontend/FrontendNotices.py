# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
The top-right notices in the chosen language (CHANGES 156).

The notices are written in English by the IOServer, the camera server and the QR code server (feNotice:<name> keys)
and by the frontend itself; the log and the other servers keep the English text. translateNotice() shows one in the
frontend's language: a notice with a catalogue entry as a whole is translated as a whole; one built from a fixed text and
a variable part (a reason, a number, a tag name) is matched against the patterns below and its fixed part translated,
the variable part translated too where it is itself a known text (an abort or picture reason); anything else is shown
as it came (English).
"""

import re
from typing import List, Tuple

from frontend.FrontendI18n import N_, tr, trf

# (pattern on the English text, template msgid, the groups that are texts to translate in their turn)
_PATTERNS: List[Tuple["re.Pattern", str, Tuple[str, ...]]] = [
    (re.compile(r"^Picture NOT OK: (?P<reason>.+)$", re.S), N_("Picture NOT OK: {reason}"), ("reason",)),
    (re.compile(r"^Cycle aborted: (?P<reason>.+)$", re.S), N_("Cycle aborted: {reason}"), ("reason",)),
    (re.compile(r"^Bypass not possible \((?P<tag>.+) not on the PLC\): full run$"),
     N_("Bypass not possible ({tag} not on the PLC): full run"), ()),
    (re.compile(r"^no answer from the camera server in (?P<secs>[\d.]+) s$"),
     N_("no answer from the camera server in {secs} s"), ()),
    (re.compile(r"^check timed out after (?P<secs>[\d.]+) s$"), N_("check timed out after {secs} s"), ()),
    (re.compile(r"^check timed out after (?P<secs>[\d.]+) s \((?P<count>\d+) in a row: .*\)$", re.S),
     N_("check timed out after {secs} s ({count} in a row: vision checks too slow on this PC - check the GPU, then "
        "restart the application)"), ()),
    (re.compile(r"^camera stream stalled: no new frame in (?P<secs>[\d.]+) s \((?P<pictures>.+) picture\)$"),
     N_("camera stream stalled: no new frame in {secs} s ({pictures} picture)"), ("pictures",)),
    (re.compile(r"^abort from the frontend \((?P<reason>.+)\)$"), N_("abort from the frontend ({reason})"), ("reason",)),
    (re.compile(r"^PLC restarted while the connection was lost \((?P<why>.+)\)$", re.S),
     N_("PLC restarted while the connection was lost ({why})"), ()),
    (re.compile(r"^check failed \((?P<error>.+)\)$", re.S), N_("check failed ({error})"), ()),
    (re.compile(r"^bearing-ring guard: no answer in (?P<secs>[\d.]+) s after the check$"),
     N_("bearing-ring guard: no answer in {secs} s after the check"), ()),
    (re.compile(r"^bearing-ring guard: ring finder failed \((?P<error>.+)\)$", re.S),
     N_("bearing-ring guard: ring finder failed ({error})"), ()),
]

# Texts that only come as the variable part of a notice (so that the extraction finds them)
KNOWN_PARTS = (
    N_("camera server down"), N_("camera not connected"), N_("no picture from the camera"),
    N_("vision check worker starting"), N_("vision dependencies missing"),
    N_("vision check worker stopped; restarting"), N_("vision check worker stopped during the check; restarting"),
    N_("heartbeat server down"), N_("frontend down"), N_("manual abort (Ctrl+Alt+Z) in the frontend"),
    N_("no reason given"), N_("first"), N_("second"), N_("third"), N_("first, second"), N_("second, third"),
    N_("first, second, third"),
    # whole notices
    N_("Machine settings not updated: previous settings used"),
    N_("Heartbeat server down: cycle aborted; next cycle on hold"),
    N_("Database down: continue till end of cycle"), N_("Database down: next cycle on hold"),
    N_("QR Code not valid"), N_("Redis is not storing data: restart the PC"), N_("Wrong password - nothing aborted"),
)


def translateNotice(text: str) -> str:
    """The notice in the frontend's language (English where there is no translation)."""
    if not isinstance(text, str) or not text:
        return text
    translated = tr(text)
    if translated != text:
        return translated
    for pattern, template, textGroups in _PATTERNS:
        match = pattern.match(text)
        if match:
            values = {name: (translateNotice(value) if name in textGroups else value)
                      for name, value in match.groupdict().items()}
            return trf(template, **values)
    return text
