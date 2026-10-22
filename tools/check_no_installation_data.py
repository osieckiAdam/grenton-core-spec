#!/usr/bin/env python3
"""Gate: no identifiers of a real installation in the repository.

    scripts/check-no-installation-data.py --staged        lines added in the index
    scripts/check-no-installation-data.py --all           every tracked file
    scripts/check-no-installation-data.py --message FILE  a commit message

Identifiers come from a real interface archive, read locally and never
stored: object names, widget ids, page names, script names, CLU ids, names,
serial numbers and addresses, distinctive labels, and the AES key and IV.
The archive is, in order: $GRENTON_INTERFACE, `git config grenton.interface`,
then dist/interface.zip of grenton-core-swift. A project file adds its CLU
and object names and its key: $GRENTON_PROJECT or `git config grenton.project`.
ZIP and .omp files in the repository are scanned entry by entry.

Without an archive the check fails rather than pass unchecked. Someone with
no installation declares that once per clone:
    git config grenton.interface none
"""
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
def git_config(key):
    result = subprocess.run(["git", "config", "--get", key], capture_output=True, text=True)
    return result.stdout.strip()


DECLARED = os.environ.get("GRENTON_INTERFACE", "") or git_config("grenton.interface")
PROJECT = os.environ.get("GRENTON_PROJECT", "") or git_config("grenton.project")
TEXT_ENTRY = re.compile(r"\.(json|xml|lua|txt|csv|md|html|js|css)$", re.I)
CANDIDATES = [DECLARED,
              HERE.parent / "dist" / "interface.zip",
              HERE.parent.parent / "grenton-core-swift" / "dist" / "interface.zip"]
GENERIC = {"sufit", "kinkiet", "temperatura", "gniazdo", "gniazda", "roleta", "salon", "kuchnia",
           "sypialnia", "łazienka", "korytarz", "garaż", "kotłownia", "pralnia", "wiatrołap",
           "gabinet", "stop", "alarm", "gate", "http", "led", "steruj", "parter", "pietro",
           "piętro", "modbus",
           # Object Manager's default labels, which every project carries.
           "contact sensor", "object manager", "mygrenton"}


def tokens(archive):
    with zipfile.ZipFile(archive) as z:
        data = json.load(io.TextIOWrapper(z.open("mygrenton/data.json"), encoding="utf-8"))
    found = {data["id"]}
    for clu in data["clus"]:
        found |= {clu["id"], clu["serialNumber"], clu["ip"]}
    labels = set()

    def walk(node):
        if isinstance(node, dict):
            if "objectName" in node:
                found.add(node["objectName"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    for page in data["pages"]:
        found.add(page["name"])
        for widget in page["widgets"]:
            found.add(widget["id"])
            walk(widget)
            for label in [widget.get("label")] + [c.get("label") for c in widget.get("components", [])]:
                if label:
                    labels.add(label)
    found |= {l for l in labels if " " in l and len(l) >= 6}
    secrets = {data.get("encryption", {}).get("key"), data.get("encryption", {}).get("iv")}
    return {t for t in found if isinstance(t, str) and len(t) >= 5 and t.lower() not in GENERIC} \
        | {s for s in secrets if isinstance(s, str) and s}


def project_tokens(archive):
    """Names, addresses, serials and the key of an Object Manager project."""
    import xml.etree.ElementTree as ET
    found = set()
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        for member in ("system.xml", "properties.xml"):
            if member not in names:
                continue
            for element in ET.fromstring(z.read(member)).iter():
                tag = element.tag.split("}")[-1]
                text = (element.text or "").strip()
                if not text:
                    continue
                # `name` is left out: the project names every feature and
                # module ("State", "Brightness"), which are not identifiers.
                if tag in ("nameOnCLU", "ipAddress", "serialNumber", "keyBytes", "ivBytes"):
                    found.add(text)
        for member in names:
            if re.fullmatch(r"nodes/\d+/data\.json", member):
                try:
                    doc = json.loads(z.read(member).decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(doc, dict) and isinstance(doc.get("pages"), list):
                    labels = {doc.get("label")}
                    found.add(doc.get("id"))
                    for page in doc["pages"]:
                        if not isinstance(page, dict):
                            continue
                        found.add(page.get("label"))
                        for widget in page.get("widgets") or []:
                            if isinstance(widget, dict):
                                found.add(widget.get("dtoId"))
                                labels.add(widget.get("label"))
                                for value in widget.values():
                                    if isinstance(value, dict):
                                        found.add(value.get("objectName"))
                                        labels.add(value.get("label"))
                    # Labels as for the interface: only distinctive, multi-word ones.
                    found |= {l for l in labels if isinstance(l, str) and " " in l and len(l) >= 6}
    return {t for t in found if isinstance(t, str) and len(t) >= 5 and t.lower() not in GENERIC}


def zip_lines(blob, label):
    """Text entries of a ZIP (an interface archive, a project), line by line."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for member in z.namelist():
                if not TEXT_ENTRY.search(member):
                    continue
                try:
                    text = z.read(member).decode("utf-8")
                except (UnicodeDecodeError, zipfile.BadZipFile, RuntimeError):
                    continue
                for line in text.split("\n"):
                    yield f"{label}:{member}", line
    except zipfile.BadZipFile:
        return


def texts(mode, argument):
    if mode == "--staged":
        diff = subprocess.run(["git", "diff", "--cached", "-U0"], capture_output=True, text=True).stdout
        file = None
        for line in diff.split("\n"):
            if line.startswith("+++ "):
                file = line[6:]
            elif line.startswith("+") and not line.startswith("+++"):
                yield file, line[1:]
        # Staged archives have no text diff: scan what is in the index.
        staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
                                capture_output=True, text=True).stdout.split()
        for file in staged:
            if file.lower().endswith((".zip", ".omp")):
                blob = subprocess.run(["git", "show", f":{file}"], capture_output=True).stdout
                yield from zip_lines(blob, file)
    elif mode == "--all":
        for file in subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split("\n"):
            path = pathlib.Path(file)
            if not file or not path.is_file():
                continue
            if file.lower().endswith((".zip", ".omp")):
                yield from zip_lines(path.read_bytes(), file)
                continue
            try:
                for line in path.read_text(encoding="utf-8").split("\n"):
                    yield file, line
            except UnicodeDecodeError:
                pass
    elif mode == "--message":
        for line in pathlib.Path(argument).read_text().split("\n"):
            if not line.startswith("#"):
                yield "commit message", line
    else:
        sys.exit(__doc__)


if DECLARED == "none":
    print("installation data: not checked (this clone declares no installation: grenton.interface=none)")
    sys.exit(0)
archive = next((pathlib.Path(c).expanduser() for c in CANDIDATES
                if c and pathlib.Path(c).expanduser().is_file()), None)
if archive is None:
    print("installation data: no interface archive to compare against.\n"
          "  Point to yours:   git config grenton.interface /path/to/interface.zip\n"
          "  Or, with none:    git config grenton.interface none")
    sys.exit(1)
known = tokens(archive)
project = pathlib.Path(PROJECT).expanduser() if PROJECT and PROJECT != "none" else None
if project is not None:
    if not project.is_file():
        print(f"installation data: project file {project} not found (grenton.project)")
        sys.exit(1)
    known |= project_tokens(project)
pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(t) for t in sorted(known, key=len, reverse=True)) + r")(?![\w])")
hits = []
for file, line in texts(sys.argv[1] if len(sys.argv) > 1 else "", sys.argv[2] if len(sys.argv) > 2 else ""):
    match = pattern.search(line)
    if match:
        hits.append(f"{file}: “{match.group(1)}” in: {line.strip()[:100]}")
for hit in hits:
    print("installation identifier:", hit)
sources = archive.name + (f" and {project.name}" if project is not None else "")
print(f"installation data: {len(hits)} identifier(s) found ({len(known)} known from {sources})")
sys.exit(1 if hits else 0)
