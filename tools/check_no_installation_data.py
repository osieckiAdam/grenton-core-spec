#!/usr/bin/env python3
"""Gate: no identifiers of a real installation in the repository.

    scripts/check-no-installation-data.py --staged        lines added in the index
    scripts/check-no-installation-data.py --all           every tracked file
    scripts/check-no-installation-data.py --message FILE  a commit message

Identifiers come from a real interface archive, read locally and never
stored: object names, widget ids, page names, script names, CLU ids, names,
serial numbers and addresses, and distinctive labels. The archive is, in
order: $GRENTON_INTERFACE, `git config grenton.interface`, then
dist/interface.zip of grenton-core-swift.

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
CANDIDATES = [DECLARED,
              HERE.parent / "dist" / "interface.zip",
              HERE.parent.parent / "grenton-core-swift" / "dist" / "interface.zip"]
GENERIC = {"sufit", "kinkiet", "temperatura", "gniazdo", "gniazda", "roleta", "salon", "kuchnia",
           "sypialnia", "łazienka", "korytarz", "garaż", "kotłownia", "pralnia", "wiatrołap",
           "gabinet", "stop", "alarm", "gate", "http", "led", "steruj", "parter", "pietro",
           "piętro", "modbus"}


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
    return {t for t in found if isinstance(t, str) and len(t) >= 5 and t.lower() not in GENERIC}


def texts(mode, argument):
    if mode == "--staged":
        diff = subprocess.run(["git", "diff", "--cached", "-U0"], capture_output=True, text=True).stdout
        file = None
        for line in diff.split("\n"):
            if line.startswith("+++ "):
                file = line[6:]
            elif line.startswith("+") and not line.startswith("+++"):
                yield file, line[1:]
    elif mode == "--all":
        for file in subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split("\n"):
            path = pathlib.Path(file)
            if file and path.is_file():
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
pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(t) for t in sorted(known, key=len, reverse=True)) + r")(?![\w])")
hits = []
for file, line in texts(sys.argv[1] if len(sys.argv) > 1 else "", sys.argv[2] if len(sys.argv) > 2 else ""):
    match = pattern.search(line)
    if match:
        hits.append(f"{file}: “{match.group(1)}” in: {line.strip()[:100]}")
for hit in hits:
    print("installation identifier:", hit)
print(f"installation data: {len(hits)} identifier(s) found ({len(known)} known from {archive.name})")
sys.exit(1 if hits else 0)
