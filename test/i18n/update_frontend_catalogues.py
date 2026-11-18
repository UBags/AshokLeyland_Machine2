"""
update_frontend_catalogues - keeps the frontend's translation catalogues in step with the code (CHANGES 156).

  1. extracts every text the frontend shows - tr("..."), trf("..."), N_("..."), bind(obj, "..."), and the first argument of
     showErrorMessage / showMessage / flashSimpleMessage / getConfirmation / getJustPassword - from frontend/**.py
     (Babel), plus the texts that come from the properties file: window.title and every state.<N>.instruction;
  2. writes the template Internal/locale/frontend.pot;
  3. updates Internal/locale/<kn|ta|hi>/LC_MESSAGES/frontend.po: new texts added (empty: shown in English until
     translated), texts no longer used dropped, existing translations kept;
  4. compiles each .po to frontend.mo (the fallback the frontend reads if Babel cannot read the .po).
Reports, per language, how many texts have no translation.

Run from the project root (needs Babel: pip install babel):
    python test/i18n/update_frontend_catalogues.py            (extract, update, compile)
    python test/i18n/update_frontend_catalogues.py --compile  (only compile the .po files, e.g. after editing one)
"""
import argparse
import datetime
import io
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from babel.messages.catalog import Catalog                         # noqa: E402
from babel.messages.extract import extract_from_dir                # noqa: E402
from babel.messages.mofile import write_mo                          # noqa: E402
from babel.messages.pofile import read_po, write_po                 # noqa: E402

LOCALE = ROOT / "Internal" / "locale"
DOMAIN = "frontend"
LANGUAGES = ("kn", "ta", "hi")
KEYWORDS = {"tr": None, "trf": None, "N_": None, "bind": (2,), "showErrorMessage": None, "showMessage": None,
            "flashSimpleMessage": None, "getConfirmation": None, "getJustPassword": None}
# not shown on the main screen or its dialogs: event names of key bindings, the splash screens, the login and mode
# screens (before the main screen, always in English)
EXCLUDED_TEXTS = {"_Enter", "No users configured in database", "Cancelling login. The system will exit.",
                  "Incorrect login credentials. The system allows upto 5 consecutive unsuccessful login attempts.",
                  "Please choose a mode"}
EXCLUDED_FILES = ("SplashScreen",)
HEADER = "Frontend texts of the Ashok Leyland hub and disc assembly machine (CHANGES 156)"


def propertiesTexts():
    texts = []
    for line in (ROOT / "ApplicationConfiguration.properties").read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "window.title" or (key.startswith("state.") and key.endswith(".instruction")):
            texts.append((value, key))
    return texts


def template() -> Catalog:
    catalogue = Catalog(project="AshokLeyland_Machine2", header_comment=f"# {HEADER}",
                        creation_date=datetime.datetime(2026, 11, 19), fuzzy=False)
    methods = [("frontend/**.py", "python")]
    for filename, lineno, message, comments, context in extract_from_dir(
            str(ROOT), method_map=methods, keywords=KEYWORDS, comment_tags=("i18n:",), strip_comment_tags=True):
        if (isinstance(message, str) and message.strip() and message not in EXCLUDED_TEXTS
                and not any(part in filename for part in EXCLUDED_FILES)):
            catalogue.add(message, locations=[(f"frontend/{filename}" if not filename.startswith("frontend") else filename,
                                               lineno)], auto_comments=comments)
    for text, key in propertiesTexts():
        catalogue.add(text, locations=[("ApplicationConfiguration.properties", 0)], auto_comments=[key])
    return catalogue


def compileAll() -> None:
    for code in LANGUAGES:
        po = LOCALE / code / "LC_MESSAGES" / f"{DOMAIN}.po"
        if not po.exists():
            continue
        with open(po, "rb") as f:
            catalogue = read_po(f, locale=code)
        with open(po.with_suffix(".mo"), "wb") as f:
            write_mo(f, catalogue, use_fuzzy=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compile", action="store_true", help="only compile the .po files")
    args = ap.parse_args()
    if not args.compile:
        pot = template()
        LOCALE.mkdir(parents=True, exist_ok=True)
        with open(LOCALE / f"{DOMAIN}.pot", "wb") as f:
            write_po(f, pot, width=120, sort_output=False, omit_header=False)
        print(f"{len(pot)} texts in {LOCALE / (DOMAIN + '.pot')}")
        for code in LANGUAGES:
            po = LOCALE / code / "LC_MESSAGES" / f"{DOMAIN}.po"
            po.parent.mkdir(parents=True, exist_ok=True)
            if po.exists():
                with open(po, "rb") as f:
                    catalogue = read_po(f, locale=code)
            else:
                catalogue = Catalog(locale=code, project="AshokLeyland_Machine2", header_comment=f"# {HEADER}",
                                    creation_date=datetime.datetime(2026, 11, 19), fuzzy=False)
            catalogue.update(pot, no_fuzzy_matching=True)
            catalogue.obsolete.clear()
            with open(po, "wb") as f:
                write_po(f, catalogue, width=120, sort_output=False, ignore_obsolete=True)
    compileAll()
    for code in LANGUAGES:
        po = LOCALE / code / "LC_MESSAGES" / f"{DOMAIN}.po"
        if po.exists():
            with open(po, "rb") as f:
                catalogue = read_po(f, locale=code)
            total = sum(1 for m in catalogue if m.id)
            missing = sum(1 for m in catalogue if m.id and not m.string)
            print(f"{code}: {total} texts, {missing} without a translation ({po})")


if __name__ == "__main__":
    main()
