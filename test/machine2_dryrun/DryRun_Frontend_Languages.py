"""
DryRun_Frontend_Languages - the frontend's languages (CHANGES 156): English, Kannada, Tamil, Hindi.

  L1  catalogues: every text extracted from the code (Internal/locale/frontend.pot) has a translation in kn, ta and
      hi, with the same {placeholders}; Babel reads the .po and the compiled .mo gives the same messages (the fallback).
  L2  FrontendI18n: tr / trf / bind / listeners; the choice kept in Internal/frontend_language.txt and read back; an
      unknown code refused; a missing text shown in English; a wrong placeholder in a translation falls back to English.
  L3  notices: whole notices and notices with a variable part (Picture NOT OK: <reason>, Cycle aborted: <reason>,
      Bypass not possible (<tag> ...)) in each language; an unknown notice stays English.
  L4  the real frontend (PySide6, offscreen): the language dropdown with the four languages; choosing each one shows the
      tiles' titles and results, the instruction, the connection status, the group titles, the menus, the window title
      and a notice in that language, keeps the choice in the file, and fits the fonts (no text wider than its tile);
      results stay English inside (getResult) and the record sent to the database is unchanged; back to English.
  L5  a frontend started with Hindi saved starts in Hindi.
  --screenshots DIR saves the main screen in each language (needs fonts with Kannada, Tamil and Devanagari).

Needs Redis and PostgreSQL (as DryRun_FEServer_Cycles). Headless:
    QT_QPA_PLATFORM=offscreen python test/machine2_dryrun/DryRun_Frontend_Languages.py [--screenshots DIR]
The language file is put back as it was at the end.
"""
import argparse
import os
import pathlib
import string
import sys
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")
if not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.modules.setdefault('winsound', types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0,
                                                         Beep=lambda *a: None))
ap = argparse.ArgumentParser()
ap.add_argument("--screenshots", default=None, help="folder for a picture of the main screen in each language")
args = ap.parse_args()

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)


import frontend.FrontendI18n as I                      # noqa: E402
from frontend.FrontendNotices import translateNotice  # noqa: E402

languageFile = pathlib.Path(I.languageFile())
savedBefore = languageFile.read_text(encoding="utf-8") if languageFile.exists() else None
if languageFile.exists():
    languageFile.unlink()

# ---------------------------------------------------------------- L1 catalogues
from babel.messages.pofile import read_po              # noqa: E402
import gettext                                          # noqa: E402

with open(ROOT / "Internal" / "locale" / "frontend.pot", "rb") as f:
    template = [m.id for m in read_po(f) if m.id]
fields = lambda s: sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)
check("L1 template has the frontend's texts", len(template) > 200, f"{len(template)} texts")
for code in ("kn", "ta", "hi"):
    folder = ROOT / "Internal" / "locale" / code / "LC_MESSAGES"
    with open(folder / "frontend.po", "rb") as f:
        po = {m.id: m.string for m in read_po(f) if m.id}
    missing = [t for t in template if not po.get(t)]
    check(f"L1 {code}: every text translated", not missing, f"{len(missing)} missing: {missing[:3]}")
    badFields = [t for t in template if po.get(t) and fields(po[t]) != fields(t)]
    check(f"L1 {code}: placeholders kept", not badFields, str(badFields[:2]))
    with open(folder / "frontend.mo", "rb") as f:
        mo = {k: v for k, v in gettext.GNUTranslations(f)._catalog.items() if k}
    check(f"L1 {code}: compiled .mo = .po", all(mo.get(t) == po[t] for t in template), "recompile: "
          "python test/i18n/update_frontend_catalogues.py --compile")

# ---------------------------------------------------------------- L2 FrontendI18n
check("L2 English at start (no file)", I.readSavedLanguage() == "en" and I.useSavedLanguage() == "en")
check("L2 tr in English = the text", I.tr("Knuckle Check") == "Knuckle Check")


class Holder:
    def __init__(self): self.text = None
    def setText(self, t): self.text = t


holder, calls = Holder(), []
I.bind(holder, "Hub Check")
I.addListener(lambda: calls.append(I.currentLanguage()))
check("L2 unknown language refused", I.setLanguage("fr") is False and I.currentLanguage() == "en")
I.setLanguage("kn")
check("L2 Kannada: tr / bound text / listener", I.tr("Hub Check") == "ಹಬ್ ಪರಿಶೀಲನೆ" and holder.text == "ಹಬ್ ಪರಿಶೀಲನೆ"
      and calls[-1:] == ["kn"], f"{holder.text}, {calls}")
check("L2 Kannada kept in Internal/frontend_language.txt", I.readSavedLanguage() == "kn")
check("L2 trf fills the placeholders", I.trf("Report saved to {file}", file="x.csv") == "ವರದಿ ಇಲ್ಲಿ ಉಳಿಸಲಾಗಿದೆ: x.csv")
check("L2 trf takes a placeholder called {text} (positional-only message)",
      I.trf("{name}: '{text}' is not a valid number", name="RPM", text="x") == "RPM: 'x' ಮಾನ್ಯ ಸಂಖ್ಯೆಯಲ್ಲ")
check("L2 a text not in the catalogue shown in English", I.tr("no such text 123") == "no such text 123")
I._catalogues["kn"]["Broken {a}"] = "ಮುರಿದ {b}"
check("L2 a wrong placeholder in a translation: English", I.trf("Broken {a}", a=1) == "Broken 1")
I.setLanguage("ta", save=False)
check("L2 save=False does not change the file", I.readSavedLanguage() == "kn" and holder.text == "ஹப் ஆய்வு")
I.reload()
check("L2 reload reads the catalogues again", I.tr("Hub Check") == "ஹப் ஆய்வு" and "Broken {a}" not in I.catalogue("kn"))

# ---------------------------------------------------------------- L3 notices
I.setLanguage("hi", save=False)
check("L3 Hindi whole notice", translateNotice("Database down: next cycle on hold") == "डेटाबेस बंद: अगली साइकिल रोकी गई")
check("L3 Hindi notice with a known reason",
      translateNotice("Picture NOT OK: camera server down") == "चित्र ठीक नहीं: कैमरा सर्वर बंद है",
      translateNotice("Picture NOT OK: camera server down"))
check("L3 Hindi notice with a number", translateNotice("Picture NOT OK: no answer from the camera server in 20 s")
      == "चित्र ठीक नहीं: 20 सेकंड में कैमरा सर्वर से कोई उत्तर नहीं")
check("L3 Hindi abort notice, nested reason",
      translateNotice("Cycle aborted: abort from the frontend (manual abort (Ctrl+Alt+Z) in the frontend)")
      == "साइकिल रद्द हुई: फ्रंटएंड से रद्द (फ्रंटएंड में मैनुअल रद्द (Ctrl+Alt+Z))",
      translateNotice("Cycle aborted: abort from the frontend (manual abort (Ctrl+Alt+Z) in the frontend)"))
check("L3 tag name kept", "PC_PLC_Skip_Second_Tightening" in translateNotice(
    "Bypass not possible (PC_PLC_Skip_Second_Tightening not on the PLC): full run"))
check("L3 an unknown reason stays English inside the translated frame",
      translateNotice("Picture NOT OK: something new") == "चित्र ठीक नहीं: something new")
check("L3 an unknown notice stays English", translateNotice("Something else") == "Something else")
I.setLanguage("en", save=False)
check("L3 English unchanged", translateNotice("Picture NOT OK: camera server down") == "Picture NOT OK: camera server down")
languageFile.unlink()

# ---------------------------------------------------------------- L4 the real frontend
from redis import Redis                                   # noqa: E402
from utils.RedisUtils import clearQueues, setFrontendNotice   # noqa: E402
import persistence.Persistence as P                       # noqa: E402

r = Redis('localhost', 6379)
clearQueues(r)
P.setDatabaseName("Test")
from PySide6.QtWidgets import QApplication, QWidget       # noqa: E402
from PySide6.QtGui import QFontMetrics                    # noqa: E402
from frontend.AshokLeylandFrontEnd import AshokLeylandFrontEnd as FE   # noqa: E402

fe = FE(mode="Test", username="admin", role="Administrator")
app = QApplication.instance()


def pump(secs=0.5):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


# the plant screen: offscreen clamps to 800 x 800 and Xvfb has no window manager to maximise, both clip English too
fe.showNormal()
fe.setMinimumSize(0, 0)
fe.setMaximumSize(16777215, 16777215)   # the frontend fixes its size after showMaximized
fe.setGeometry(0, 0, 1920, 1050)
fe.setFixedSize(1920, 1050)
pump(1.5)
fe._qrCodeFont = None       # the QR code line is fitted once: again for the plant screen's size (CHANGES 158)
fe.onLanguageChanged()      # refits the fonts to the new size
pump(0.5)
from frontend.frontendutils.CosThetaLabelUtils import fittingPointSize as _fit   # noqa: E402
from PySide6.QtGui import QFont as _QFont                                        # noqa: E402
_qrBold = _QFont(fe.qrCodeLabel.font()); _qrBold.setWeight(_QFont.Weight.Bold)
qrStartFit = _fit(fe.qrCodeLabel, [f"   QR CODE : {FE.QR_CODE_SAMPLE}   "], font=_qrBold, widthShare=0.95,
                  heightShare=0.95, hMargin=-fe.noticeLabel.width() if fe.noticeLabel.isVisible() else 0)
tiles = fe.resultsContainer.outcomeContainers()
selector = fe.languageSelector
check("L4 dropdown with English, Kannada, Tamil, Hindi (own scripts)",
      [selector.itemData(i) for i in range(selector.count())] == ["en", "kn", "ta", "hi"]
      and selector.itemText(1) == "ಕನ್ನಡ" and selector.currentData() == "en")
englishTitles = [t.title_label.text() for t in tiles]
check("L4 English at start", englishTitles[0] == "Knuckle Check" and tiles[0].result_label.text() == "Result Awaited")
setFrontendNotice(r, "db", "Database down: next cycle on hold", ttlSecs=30)
pump(2.5)
EXPECT = {"kn": ("ನಕಲ್ ಪರಿಶೀಲನೆ", "ಫಲಿತಾಂಶಕ್ಕೆ ಕಾಯಲಾಗುತ್ತಿದೆ", "ಡೇಟಾಬೇಸ್ ಸ್ಥಗಿತ: ಮುಂದಿನ ಸೈಕಲ್ ತಡೆಹಿಡಿಯಲಾಗಿದೆ", "ಮೋಡ್", " ಕ್ರಿಯೆಗಳು "),
          "ta": ("நக்கிள் ஆய்வு", "முடிவுக்காகக் காத்திருக்கிறது", "தரவுத்தளம் செயலிழந்தது: அடுத்த சுழற்சி நிறுத்தி வைக்கப்பட்டது", "பயன்முறை", " செயல்கள் "),
          "hi": ("नकल जाँच", "परिणाम की प्रतीक्षा", "डेटाबेस बंद: अगली साइकिल रोकी गई", "मोड", " क्रियाएँ ")}
menuActions = [a for a in fe.menuBar().actions()]
from frontend.frontendutils.CosThetaLabelUtils import fittingPointSize, largestFittingPointSize   # noqa: E402
from PySide6.QtGui import QFont                                    # noqa: E402


def fontChecks(code):
    """L6 (CHANGES 157): every instruction and every connection message fits its widget as drawn (QFontMetrics), and
    each family's size = the measured fit + the language's adjustment (Hindi -1)."""
    adjustment = I.fontSizeAdjustment(code)
    label = fe.resultsContainer.instructionLabel
    instructions = [f"  {I.tr(t)}  " for t in fe.machineState.getAllInstructions() if t]
    bold = QFont(label.font()); bold.setWeight(QFont.Weight.Bold)
    fitted = fittingPointSize(label, instructions, font=bold, widthShare=0.95, heightShare=0.95)
    metrics = QFontMetrics(label.font())
    tooWide = [t for t in instructions if metrics.size(0, t).width() > label.width() * 0.95]
    check(f"L6 {code}: all {len(instructions)} instructions fit the instruction label (each measured); size = fit "
          f"{fitted} + adjustment {adjustment}", not tooWide and label.font().pointSize() == max(4, fitted + adjustment),
          f"size {label.font().pointSize()}, too wide {tooWide[:1]}")
    statusTiles = [fe.cameraServerConnectionStatusContainer, fe.qrCodeServerConnectionStatusContainer,
                   fe.ioServerConnectionStatusContainer, fe.dbServerConnectionStatusContainer]
    messages = [(t, I.tr(x)) for t in statusTiles for x in (t._okText, t._notokText)]
    statusFit = min(largestFittingPointSize(t.label(), x) for t, x in messages)
    size = statusTiles[0].label().font().pointSize()
    wide = [x for t, x in messages if QFontMetrics(t.label().font()).horizontalAdvance(x) > t.label().width() - 16]
    check(f"L6 {code}: all 8 connection messages fit their tiles; size = fit {statusFit} + adjustment {adjustment}",
          not wide and size == max(4, statusFit + adjustment), f"size {size}, too wide {wide[:1]}")
    hintTooBig = [t.imageHint for t in tiles if QFontMetrics(t.image_label.font()).size(0, I.tr(t.imageHint)).width()
                  > t.image_label.width() * 0.9 or QFontMetrics(t.image_label.font()).size(0, I.tr(t.imageHint)).height()
                  > t.image_label.height() * 0.9]
    hintSizes = {t.image_label.font().pointSize() for t in tiles}
    check(f"L6 {code}: every tile hint fits 90 % of its picture area, one size for all ({sorted(hintSizes)})",
          not hintTooBig and len(hintSizes) == 1, str(hintTooBig[:2]))
    # CHANGES 158: the QR code line fitted to "QR CODE : " + 30 sample characters in English, no adjustment, the same
    # size in every language; the creator line measured on its label with its own font (Segoe UI),
    # filling 95 % of it, then the adjustment
    qrLabel = fe.qrCodeLabel
    qrSizes.add(qrLabel.font().pointSize())
    shown = f"   {I.tr('QR CODE : ')}{FE.QR_CODE_SAMPLE}   "
    withoutNotice = qrLabel.width() + (fe.noticeLabel.width() if fe.noticeLabel.isVisible() else 0)
    check(f"L6 {code}: QR code line at the English fit {qrStartFit} found at start (no adjustment); "
          f"'{I.tr('QR CODE : ')}' + 30 sample characters within the label's width without the notice",
          qrLabel.font().pointSize() == qrStartFit and len(FE.QR_CODE_SAMPLE) == 30
          and QFontMetrics(qrLabel.font()).horizontalAdvance(shown) <= withoutNotice,
          f"{qrLabel.font().pointSize()} pt, {QFontMetrics(qrLabel.font()).horizontalAdvance(shown)} of {withoutNotice} px "
          f"({qrLabel.width()} px beside the notice)")
    creator = fe.creatorStatus.label()
    creatorFont = creator.font()
    creatorBold = QFont(creatorFont); creatorBold.setWeight(QFont.Weight.Bold)
    creatorFit = fittingPointSize(creator, [creator.text()], font=creatorBold, widthShare=FE.CREATOR_FILL_SHARE,
                                  heightShare=FE.CREATOR_FILL_SHARE, hMargin=10, vMargin=10)
    oldFit = fittingPointSize(creator, [f"   {creator.text()}   "], font=creatorBold, widthShare=0.8, heightShare=0.8)
    check(f"L6 {code}: creator line {creatorFont.family()}, size fit {creatorFit} + adjustment {adjustment} "
          f"(0.8 share with padding gave {oldFit})",
          creatorFont.family() == "Segoe UI"
          and creatorFont.pointSize() == max(4, creatorFit + adjustment) and creatorFit > oldFit,
          str(creatorFont.pointSize()))
    titleFit = min(largestFittingPointSize(t.title_label, t.title_label.text()) for t in tiles)
    check(f"L6 {code}: tile titles at fit {titleFit} + adjustment {adjustment}",
          tiles[0].title_label.font().pointSize() == max(4, titleFit + adjustment),
          str(tiles[0].title_label.font().pointSize()))


qrSizes = set()
fontChecks("en")
for code in ("kn", "ta", "hi"):
    selector.setCurrentIndex(selector.findData(code))
    pump(1.0)
    fontChecks(code)
    title, awaited, notice, modeTitle, actionsMenu = EXPECT[code]
    check(f"L4 {code}: tile title and result", tiles[0].title_label.text() == title and
          tiles[0].result_label.text() == awaited, f"{tiles[0].title_label.text()} / {tiles[0].result_label.text()}")
    check(f"L4 {code}: results stay English inside", tiles[0].getResult() == "Result Awaited")
    check(f"L4 {code}: instruction", fe.resultsContainer.instructionLabel.text() ==
          I.tr(fe.machineState.getCurrentInstruction()) != fe.machineState.getCurrentInstruction())
    check(f"L4 {code}: notice", fe.noticeLabel.text() == notice, fe.noticeLabel.text())
    check(f"L4 {code}: mode title and menu", fe.centralGroupBox.title() == I.trf("IN {mode} MODE", mode=I.tr("TEST"))
          != "IN TEST MODE" and menuActions[0].text() == actionsMenu, f"{fe.centralGroupBox.title()} / {menuActions[0].text()}")
    check(f"L4 {code}: window title", QWidget.windowTitle(fe) == I.tr(FE.windowTitle) != FE.windowTitle)
    status = fe.cameraServerConnectionStatusContainer.label().text()
    check(f"L4 {code}: connection status", status in (I.tr("Camera connected"), I.tr("Camera not connected"))
          and status not in ("Camera connected", "Camera not connected"), status)
    check(f"L4 {code}: kept in the language file", I.readSavedLanguage() == code)
    tooWide = [t.title_label.text() for t in tiles
               if QFontMetrics(t.title_label.font()).horizontalAdvance(t.title_label.text()) > t.width() - 4]
    check(f"L4 {code}: every tile title fits its tile", not tooWide, str(tooWide[:2]))
    if args.screenshots:
        folder = pathlib.Path(args.screenshots)
        folder.mkdir(parents=True, exist_ok=True)
        fe.grab().save(str(folder / f"frontend_{code}.png"))
check("L6 the QR code line has one size in all four languages", len(qrSizes) == 1, str(sorted(qrSizes)))
selector.setCurrentIndex(selector.findData("en"))
pump(1.0)
check("L4 back to English", [t.title_label.text() for t in tiles] == englishTitles
      and fe.noticeLabel.text() == "Database down: next cycle on hold" and I.readSavedLanguage() == "en")
if args.screenshots:
    fe.grab().save(str(pathlib.Path(args.screenshots) / "frontend_en.png"))

# a result shown in Tamil, the record's value English
selector.setCurrentIndex(selector.findData("ta"))
pump(0.5)
tiles[0].setResult("OK")
check("L4 a result shown in Tamil, kept English", tiles[0].result_label.text() == "சரி" and tiles[0].getResult() == "OK")
tiles[4].setTwoLineTextAndResult("19.08", "Rotation Done", "OK")
check("L4 torque tile: second line in Tamil", "சுழற்சி முடிந்தது" in tiles[4].image_label.text())
selector.setCurrentIndex(selector.findData("hi"))
pump(0.5)
check("L4 torque tile follows a change of language", "रोटेशन पूर्ण" in tiles[4].image_label.text()
      and tiles[0].result_label.text() == "ठीक")

# ---------------------------------------------------------------- L5 start with the saved language
I.saveLanguage("hi")
I.setLanguage("en", save=False)
check("L5 the saved language is used at the next start", I.useSavedLanguage() == "hi" and I.tr("OK") == "ठीक")

# put the language file back
I.setLanguage("en", save=False)
if savedBefore is None:
    if languageFile.exists():
        languageFile.unlink()
else:
    languageFile.write_text(savedBefore, encoding="utf-8")
clearQueues(r)
passed = sum(1 for _, ok in RESULTS if ok)
failed = [n for n, ok in RESULTS if not ok]
print(f"RESULT {passed}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
os._exit(0 if not failed else 1)
