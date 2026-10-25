#!/usr/bin/env python3
"""Gate: versions agree, and every spec or suite change is recorded.

    python3 tools/check_versions.py            versions only
    python3 tools/check_versions.py --staged   also: staged spec/suite changes
                                               come with a staged CHANGELOG entry

The spec header (| **Version** |), the conformance.md header, SPEC_VERSION in
tools/suite.py and the newest CHANGELOG heading must name the same spec version; SUITE_VERSION must
match conformance/manifest.json (tools/suite.py check verifies the manifest).
"""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
problems = []

header = re.search(r"\| \*\*Version\*\* \| ([0-9.]+) \|", (ROOT / "spec/grenton-core-design.md").read_text())
suite_py = (ROOT / "tools/suite.py").read_text()
spec_version = re.search(r'SPEC_VERSION = "([0-9.]+)"', suite_py).group(1)
suite_version = re.search(r'SUITE_VERSION = "([0-9.]+)"', suite_py).group(1)
manifest = json.loads((ROOT / "conformance/manifest.json").read_text())
newest = re.search(r"^## (.+)$", (ROOT / "CHANGELOG.md").read_text(), re.M).group(1)

if not header or header.group(1) != spec_version:
    problems.append(f"spec header says {header.group(1) if header else '?'}, tools/suite.py SPEC_VERSION says {spec_version}")
if manifest["specVersion"] != spec_version or manifest["suiteVersion"] != suite_version:
    problems.append("manifest.json versions differ from tools/suite.py — run: python3 tools/suite.py manifest")
conformance_header = re.search(r"\| \*\*Suite version\*\* \| ([0-9.]+) \(specification ([0-9.]+)",
                               (ROOT / "spec/conformance.md").read_text())
if not conformance_header or conformance_header.groups() != (suite_version, spec_version):
    problems.append(f"spec/conformance.md header says {conformance_header.groups() if conformance_header else '?'}, "
                    f"expected suite {suite_version}, spec {spec_version}")
if spec_version not in newest:
    problems.append(f"newest CHANGELOG entry “{newest}” does not name spec {spec_version}")
readme = (ROOT / "README.md").read_text()
readme_line = re.search(r"Version ([0-9.]+) contains \w+ profiles, (\d+) cases in total", readme)
case_count = len(manifest["cases"])
if not readme_line or readme_line.group(1) != spec_version or int(readme_line.group(2)) != case_count:
    problems.append(f"README.md must say “Version {spec_version} contains … profiles, {case_count} cases in total”")

if "--staged" in sys.argv:
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout.split()
    touched = [f for f in staged if f.startswith(("spec/", "conformance/", "tools/oracles/"))]
    if touched and "CHANGELOG.md" not in staged:
        problems.append("spec or suite changed without a CHANGELOG entry: " + ", ".join(touched[:5]))

for p in problems:
    print("versions:", p)
print(f"versions: spec {spec_version}, suite {suite_version}, {'OK' if not problems else 'FAILED'}")
sys.exit(1 if problems else 0)
