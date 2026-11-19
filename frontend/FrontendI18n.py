# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Languages of the frontend's main screen and the dialogs opened from it (CHANGES 156).

  * English is the source language: every text in the code is the English one, and it is the message id of the
    gettext catalogues. Kannada (kn), Tamil (ta) and Hindi (hi) come from
    Internal/locale/<lang>/LC_MESSAGES/frontend.po (GNU gettext format, read with Babel at start so that an edited .po
    applies without a compile step; if Babel cannot read it, the compiled frontend.mo next to it is read with the
    standard library's gettext). A message missing or empty in a catalogue is shown in English.
  * The language chosen on the main screen (dropdown at the top) is kept in Internal/frontend_language.txt (one line:
    en / kn / ta / hi) and used again at the next start. The login and mode screens before the main screen stay in
    English; the database, the logs and the PLC are not affected.
  * tr(text) translates one text; trf(text, **values) translates a text with {named} placeholders, then fills them in
    (translators may move the placeholders); N_(text) only marks a text for extraction (translated where it is shown).
  * bind(obj, text, setter) shows tr(text) through obj.setter now and again after every change of language;
    addListener(callback) for texts that are built at run time (the callback re-shows them).

Catalogue maintenance (laptop, project root):
    pybabel extract -F Internal/locale/babel.cfg -k tr -k trf -k N_ -o Internal/locale/frontend.pot .
    pybabel update -i Internal/locale/frontend.pot -d Internal/locale -D frontend
    pybabel compile -d Internal/locale -D frontend
"""

import gettext
import os
import threading
import weakref
from typing import Callable, Dict, List, Optional, Tuple

from BaseUtils import get_project_root

LANGUAGES: Tuple[Tuple[str, str], ...] = (("en", "English"), ("kn", "ಕನ್ನಡ"), ("ta", "தமிழ்"), ("hi", "हिन्दी"))
LANGUAGE_CODES = tuple(code for code, _ in LANGUAGES)
DEFAULT_LANGUAGE = "en"
DOMAIN = "frontend"

_lock = threading.RLock()
_catalogues: Dict[str, Dict[str, str]] = {}
_current: str = DEFAULT_LANGUAGE
_bindings: List[Tuple[weakref.ref, str, str, str]] = []
_listeners: List[Callable[[], None]] = []
_loadNotes: Dict[str, str] = {}


def localeDirectory() -> str:
    return os.path.join(str(get_project_root()), "Internal", "locale")


def languageFile() -> str:
    return os.path.join(str(get_project_root()), "Internal", "frontend_language.txt")


def _readCatalogue(code: str) -> Dict[str, str]:
    """msgid -> msgstr of one language (only translated, non-fuzzy entries)."""
    folder = os.path.join(localeDirectory(), code, "LC_MESSAGES")
    poPath, moPath = os.path.join(folder, f"{DOMAIN}.po"), os.path.join(folder, f"{DOMAIN}.mo")
    if os.path.exists(poPath):
        try:
            from babel.messages.pofile import read_po
            with open(poPath, "rb") as f:
                catalogue = read_po(f, ignore_obsolete=True)
            messages = {}
            for message in catalogue:
                if message.id and isinstance(message.id, str) and message.string and not message.fuzzy:
                    messages[message.id] = message.string
            _loadNotes[code] = f"{len(messages)} messages from {poPath}"
            return messages
        except Exception as e:
            _loadNotes[code] = f"{poPath} not read with Babel ({e}); trying {moPath}"
    if os.path.exists(moPath):
        try:
            with open(moPath, "rb") as f:
                translations = gettext.GNUTranslations(f)
            messages = {k: v for k, v in translations._catalog.items() if isinstance(k, str) and k and v}
            _loadNotes[code] = f"{len(messages)} messages from {moPath}"
            return messages
        except Exception as e:
            _loadNotes[code] = f"{moPath} not read ({e}): English shown"
    else:
        _loadNotes.setdefault(code, f"no catalogue for {code} in {folder}: English shown")
    return {}


def catalogue(code: str) -> Dict[str, str]:
    with _lock:
        if code == DEFAULT_LANGUAGE:
            return {}
        if code not in _catalogues:
            _catalogues[code] = _readCatalogue(code)
        return _catalogues[code]


def loadNote(code: str) -> str:
    """What was loaded for a language (for the log)."""
    catalogue(code)
    return _loadNotes.get(code, "English (the source language)")


def reload() -> None:
    """Read the catalogues again (an edited .po) and show the texts again."""
    with _lock:
        _catalogues.clear()
        _loadNotes.clear()
    _notify()


def tr(text):
    """The text in the current language (English when there is no translation). Non-text values pass through."""
    if not isinstance(text, str) or not text or _current == DEFAULT_LANGUAGE:
        return text
    return catalogue(_current).get(text) or text


def trf(message: str, /, **values) -> str:
    """tr() of a text with {named} placeholders, then the values filled in; the English text if the translation's
    placeholders do not match. The text is positional-only, so any placeholder name (even {text}) can be passed."""
    translated = tr(message)
    try:
        return translated.format(**values)
    except (KeyError, IndexError, ValueError):
        pass
    try:
        return message.format(**values)
    except (KeyError, IndexError, ValueError) as e:
        # the English text itself does not match the values (a code error): shown unfilled, never raised into the
        # screen's code, and printed so that it is found (CHANGES 157)
        print(f"[FrontendI18n] text {message!r} cannot be filled with {sorted(values)}: {type(e).__name__} {e}")
        return message


def N_(text: str) -> str:
    """Marks a text for extraction only; it is translated with tr() where it is shown."""
    return text


def currentLanguage() -> str:
    return _current


# The properties name of each language, for language.<name>.fontsize.adjustment (CHANGES 157)
PROPERTIES_NAMES: Dict[str, str] = {"en": "english", "kn": "kannada", "ta": "tamil", "hi": "hindi"}


def fontSizeAdjustment(code: Optional[str] = None) -> int:
    """Points added to every font size the main screen fits to its widgets, for the language shown (CHANGES 157):
    language.<english|kannada|tamil|hindi>.fontsize.adjustment in the properties file (Hindi -1, the others 0). Read
    at each fit, so a changed value applies at the next change of language or start."""
    name = PROPERTIES_NAMES.get(code or _current, "english")
    try:
        from Configuration import CosThetaConfigurator
        return int(CosThetaConfigurator.getTuning(f"language.{name}.fontsize.adjustment", 0))
    except Exception:
        return 0


def adjustedPointSize(size: int, minimum: int = 4) -> int:
    """A fitted font size with the language's adjustment, never below minimum."""
    return max(int(minimum), int(size) + fontSizeAdjustment())


def languageName(code: str) -> str:
    return dict(LANGUAGES).get(code, code)


def readSavedLanguage() -> str:
    """The language kept in Internal/frontend_language.txt (English if there is none or it is not one of the four)."""
    try:
        with open(languageFile(), "r", encoding="utf-8") as f:
            code = f.read().strip().lower()
        return code if code in LANGUAGE_CODES else DEFAULT_LANGUAGE
    except Exception:
        return DEFAULT_LANGUAGE


def saveLanguage(code: str) -> bool:
    try:
        os.makedirs(os.path.dirname(languageFile()), exist_ok=True)
        temporary = languageFile() + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            f.write(code + "\n")
        os.replace(temporary, languageFile())
        return True
    except Exception:
        return False


def setLanguage(code: str, save: bool = True) -> bool:
    """Switch the language (one of LANGUAGE_CODES); every bound text and listener is shown again. save: also keep it
    in Internal/frontend_language.txt. False if the code is not known."""
    global _current
    code = (code or "").strip().lower()
    if code not in LANGUAGE_CODES:
        return False
    with _lock:
        _current = code
        catalogue(code)
    if save:
        saveLanguage(code)
    _notify()
    return True


def useSavedLanguage() -> str:
    """At the start of the main screen: the saved language becomes the current one (not saved again)."""
    setLanguage(readSavedLanguage(), save=False)
    return _current


def bind(obj, text: str, setter: str = "setText", fmt: str = "{}"):
    """obj.<setter>(fmt.format(tr(text))) now and after every change of language (until obj is gone); fmt e.g. " {} "
    for the spaces round a menu text. Returns obj."""
    try:
        reference = weakref.ref(obj)
    except TypeError:
        reference = (lambda o=obj: o)
    with _lock:
        _bindings[:] = [(r, t, s, f) for r, t, s, f in _bindings
                        if r() is not None and not (r() is obj and s == setter)]     # one text per object and setter
        _bindings.append((reference, text, setter, fmt))
    _apply(obj, text, setter, fmt)
    return obj


def addListener(callback: Callable[[], None]) -> None:
    with _lock:
        _listeners.append(callback)


def removeListener(callback: Callable[[], None]) -> None:
    with _lock:
        if callback in _listeners:
            _listeners.remove(callback)


def _apply(obj, text: str, setter: str, fmt: str = "{}") -> None:
    try:
        getattr(obj, setter)(fmt.format(tr(text)))
    except RuntimeError:
        pass                                    # the Qt object behind it was deleted
    except Exception:
        pass


def _notify() -> None:
    with _lock:
        bindings = [(r(), t, s, f) for r, t, s, f in _bindings]
        _bindings[:] = [(r, t, s, f) for r, t, s, f in _bindings if r() is not None]
        listeners = list(_listeners)
    for obj, text, setter, fmt in bindings:
        if obj is not None:
            _apply(obj, text, setter, fmt)
    for callback in listeners:
        try:
            callback()
        except Exception:
            pass


# Texts shown from data (results, roles, mode names) rather than literals in the code: listed for the extraction
STANDARD_TEXTS = (N_("OK"), N_("Not OK"), N_("Bypassed"), N_("Done"), N_("Result Awaited"), N_("TEST"),
                  N_("PRODUCTION"), N_("Operator"), N_("Supervisor"), N_("Administrator"), N_("Yes"), N_("No"))


def missingMessages(code: str, messageIds) -> List[str]:
    """The message ids without a translation in a language's catalogue (for the dry run)."""
    messages = catalogue(code)
    return [m for m in messageIds if not messages.get(m)]


def installIndicFonts(families) -> List[str]:
    """Indic fallbacks for the frontend's font families (CHANGES 156). The tiles use Latin fonts (Courier, Segoe UI)
    that have no Kannada, Tamil or Devanagari glyphs; without a substitution Qt may fall back to a font that cannot
    shape the script (vowel signs in the wrong place). Nirmala UI (Windows 8+) covers all three; the Noto fonts and
    Tunga / Latha / Mangal are further fallbacks. Call once the QApplication exists. Returns the fallbacks set."""
    from PySide6.QtGui import QFont
    from Configuration import CosThetaConfigurator
    fallbacks = [f.strip() for f in CosThetaConfigurator.getTuning(
        "frontend.indic.fonts", "Nirmala UI,Noto Sans Kannada,Noto Sans Tamil,Noto Sans Devanagari,Tunga,Latha,Mangal"
    ).split(",") if f.strip()]
    for family in dict.fromkeys(f for f in families if f):
        QFont.insertSubstitutions(family, fallbacks)
    return fallbacks
