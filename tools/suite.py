#!/usr/bin/env python3
"""Maintenance tool for the conformance suite (stdlib only).

    python3 tools/suite.py check      validate every case, verify the manifest
    python3 tools/suite.py manifest   rewrite conformance/manifest.json

`check` also re-derives expected values that have an independent oracle:
AES cases through the `openssl` CLI, `number.format` through Python's
round() (the reference semantics), `mapping.interface` through
tools/oracles/mapping.py (written from spec §6), and every double's
`decimal` against its `bits`. It never uses a port's output as the source
of truth.
"""
import base64
import hashlib
import json
import os
import re
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFORMANCE = os.path.join(ROOT, "conformance")
CASES = os.path.join(CONFORMANCE, "cases")
FIXTURES = os.path.join(CONFORMANCE, "fixtures")
MANIFEST = os.path.join(CONFORMANCE, "manifest.json")

SCHEMA_VERSION = 1
SUITE_VERSION = "0.5.0"
SPEC_VERSION = "0.5.0"

# operation -> (profile, required input keys, optional input keys)
OPERATIONS = {
    "crypto.encrypt": ("stateless", {"keyBase64", "ivBase64", "plaintextHex"}, set()),
    "crypto.decrypt": ("stateless", {"keyBase64", "ivBase64", "ciphertextHex"}, set()),
    "wire.request": ("stateless", {"msgId", "payload"}, set()),
    "wire.response": ("stateless", {"frame"}, set()),
    "payload.register": ("stateless", {"sessionId", "keys"}, set()),
    "payload.action": ("stateless", {"action"}, set()),
    "payload.report": ("stateless", {"payload"}, set()),
    "value.cast": ("stateless", {"text"}, set()),
    "provisioning.url": ("stateless", {"host", "pin"}, {"port"}),
    "coercion.boolFromOnOff": ("stateless", {"value"}, set()),
    "coercion.truthy": ("stateless", {"value"}, set()),
    "number.format": ("stateless", {"value", "precision"}, set()),
    "mapping.interface": ("mapping", {"fixture"}, set()),
    "session.scenario": ("session", {"clu", "responseTimeoutMs", "steps"}, set()),
}

# Stable requirement ids -> spec sections (see spec/conformance.md).
REQUIREMENTS = {
    "crypto.aes-cbc": "§5.1", "crypto.padding": "§5.1", "crypto.key-material": "§5.1",
    "wire.request": "§5.2", "wire.response": "§5.2", "wire.msg-id-case": "§5.2",
    "payload.register": "§5.3.2", "payload.report": "§5.3.2", "payload.action": "§5.3.4",
    "model.cast": "§3.1", "model.action-ref": "§3.3, §3.6",
    "provisioning.url": "§4.1", "provisioning.pin": "§4.1",
    "devices.coercion": "§6.2", "devices.number-format": "§10 pt 3",
    "model.widget-tolerance": "§3.6", "devices.mapping": "§6.1, §6.3", "devices.order": "§6.1", "devices.physical-objects": "§6.6",
    "session.correlation": "§5.4.1", "session.chunking": "§5.4.2, §5.3.2",
    "session.report-routing": "§5.4.3, §5.3.3", "session.keepalive": "§5.3.1",
    "session.lifecycle": "§5.4.5", "session.actions": "§5.3.4, §3.3",
}

ERROR_CODES = {"crypto.invalidKeyMaterial", "provisioning.connection", "payload.unresolvedPlaceholder"}
CASE_KEYS = {"schemaVersion", "id", "specRefs", "description", "provenance", "operation", "input", "expect"}
ID_RE = re.compile(r"^[a-z0-9]+(\.[A-Za-z0-9-]+)+$")


def json_files(top):
    for dirpath, _, files in os.walk(top):
        for name in sorted(files):
            if name.endswith(".json"):
                yield os.path.join(dirpath, name)


def case_files():
    return json_files(CASES)


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_tagged(value, where, errors):
    t = value.get("type") if isinstance(value, dict) else None
    keys = set(value) if isinstance(value, dict) else set()
    if t == "null" and keys == {"type"}:
        return
    if t == "bool" and keys == {"type", "value"} and isinstance(value["value"], bool):
        return
    if t == "int" and keys == {"type", "value"} and re.fullmatch(r"-?[0-9]+", str(value["value"])):
        if not isinstance(value["value"], str):
            errors.append(f"{where}: int value must be a decimal string")
        return
    if t == "string" and keys == {"type", "value"} and isinstance(value["value"], str):
        return
    if t == "double" and keys == {"type", "bits", "decimal"}:
        if not re.fullmatch(r"[0-9a-f]{16}", value["bits"]):
            errors.append(f"{where}: double bits must be 16 lowercase hex digits")
            return
        from_bits = struct.unpack(">d", bytes.fromhex(value["bits"]))[0]
        if struct.pack(">d", float(value["decimal"])).hex() != value["bits"]:
            errors.append(f"{where}: decimal {value['decimal']} does not match bits ({from_bits!r})")
        return
    errors.append(f"{where}: malformed tagged value {value!r}")


def walk_tagged(node, where, errors):
    """Every object with a `type` key inside input/expect is a tagged value
    (register keys use `variable`/`attribute` and are checked separately)."""
    if isinstance(node, dict):
        if node.get("type") in {"null", "bool", "int", "string", "double"}:
            check_tagged(node, where, errors)
            return
        for k, v in node.items():
            walk_tagged(v, f"{where}.{k}", errors)
    elif isinstance(node, list):
        for n, v in enumerate(node):
            walk_tagged(v, f"{where}[{n}]", errors)


def openssl(decrypt, key_b64, iv_b64, data):
    key = base64.b64decode(key_b64)
    cipher = {16: "aes-128-cbc", 24: "aes-192-cbc", 32: "aes-256-cbc"}[len(key)]
    cmd = ["openssl", "enc", "-" + cipher, "-K", key.hex(), "-iv", base64.b64decode(iv_b64).hex()]
    if decrypt:
        cmd.append("-d")
    return subprocess.run(cmd, input=data, capture_output=True).stdout


def python_value(tagged):
    """Tagged GrentonValue → the Python value the reference would hold."""
    t = tagged["type"]
    if t == "null":
        return None
    if t == "int":
        return int(tagged["value"])
    if t == "double":
        return struct.unpack(">d", bytes.fromhex(tagged["bits"]))[0]
    return tagged["value"]


# conformance.md §4b: step verb -> (required keys, optional keys)
STEPS = {
    "call": ({"call", "as"}, {"keys", "action"}),
    "await": ({"await", "result"}, set()),
    "expectSent": ({"expectSent"}, set()),
    "expectQuiet": ({"expectQuiet"}, set()),
    "respond": ({"respond"}, set()),
    "deliver": ({"deliver"}, set()),
    "advance": ({"advance"}, set()),
    "expectState": ({"expectState"}, set()),
    "expectReport": ({"expectReport"}, set()),
    "expectNoReport": ({"expectNoReport"}, set()),
    "expectClosed": ({"expectClosed"}, set()),
}
CALLS = {"start", "stop", "ping", "register", "execute"}
STATES = {"idle", "starting", "ready", "degraded", "stopped"}
EXECUTE_ERRORS = {"session.timeout", "session.notConnected", "session.connectionClosed",
                  "payload.unresolvedPlaceholder"}


def check_scenario(doc, errors):
    """Static checks: known verbs and fields, awaits refer to earlier calls,
    socket labels and captured ids are bound before they are used."""
    where = doc["id"]
    inp = doc["input"]
    if not isinstance(inp["steps"], list) or not inp["steps"]:
        errors.append(f"{where}: steps must be a non-empty list")
        return
    clu = inp["clu"]
    if not isinstance(clu, dict) or set(clu) != {"id", "ip", "port"}:
        errors.append(f"{where}: clu must be {{id, ip, port}}")
    calls, awaited, labels, captures = {}, set(), set(), set()
    for n, step in enumerate(inp["steps"]):
        at = f"{where}: step {n}"
        verb = next((v for v in STEPS if v in step), None)
        if verb is None:
            errors.append(f"{at}: unknown step {step}")
            continue
        required, optional = STEPS[verb]
        if not required <= set(step) or not set(step) <= required | optional:
            errors.append(f"{at}: {verb} takes {sorted(required | optional)}")
            continue
        if verb == "call":
            if step["call"] not in CALLS:
                errors.append(f"{at}: unknown call {step['call']}")
            if step["as"] in calls:
                errors.append(f"{at}: name {step['as']} used twice")
            if (step["call"] == "register") != ("keys" in step) or (step["call"] == "execute") != ("action" in step):
                errors.append(f"{at}: register takes keys, execute takes action, nothing else does")
            calls[step["as"]] = step["call"]
        elif verb == "await":
            name = step["await"]
            if name not in calls:
                errors.append(f"{at}: await of unknown call {name}")
            elif name in awaited:
                errors.append(f"{at}: {name} awaited twice")
            else:
                awaited.add(name)
                result, kind = step["result"], calls[name]
                if kind in ("start", "stop") and result is not None:
                    errors.append(f"{at}: {kind} has no result, use null")
                if kind == "ping" and (not isinstance(result, dict) or set(result) != {"ok"}):
                    errors.append(f"{at}: ping result is {{ok}}")
                if kind == "register" and (not isinstance(result, dict) or set(result) != {"slots"}):
                    errors.append(f"{at}: register result is {{slots}}")
                if kind == "execute" and not (result == {"ok": True} or (isinstance(result, dict) and set(result) == {"error"} and result["error"] in EXECUTE_ERRORS)):
                    errors.append(f"{at}: execute result is {{ok: true}} or {{error: <execute error>}}")
        elif verb == "expectSent":
            body = step["expectSent"]
            if set(body) != {"socket", "msgId", "payload"}:
                errors.append(f"{at}: expectSent takes socket, msgId, payload")
                continue
            labels.add(body["socket"])
            if body["msgId"].startswith("$"):
                captures.add(body["msgId"])
        elif verb in ("respond", "deliver"):
            body = step[verb]
            fields = {"socket", "msgId", "payload"} if verb == "respond" else {"socket", "frame", "encrypted"}
            if not fields <= set(body) or not set(body) <= fields | {"upper"}:
                errors.append(f"{at}: {verb} takes {sorted(fields)}")
                continue
            if body["socket"] not in labels:
                errors.append(f"{at}: socket {body['socket']} is not bound yet")
            if verb == "respond" and body["msgId"].startswith("$") and body["msgId"] not in captures:
                errors.append(f"{at}: {body['msgId']} is not captured yet")
        elif verb == "expectClosed" and step["expectClosed"] not in labels:
            errors.append(f"{at}: socket {step['expectClosed']} is not bound yet")
        elif verb == "expectState" and step["expectState"] not in STATES:
            errors.append(f"{at}: unknown state {step['expectState']}")
        elif verb == "advance" and not (isinstance(step["advance"], int) and step["advance"] > 0):
            errors.append(f"{at}: advance takes a positive number of milliseconds")
    for name in set(calls) - awaited:
        errors.append(f"{where}: call {name} is never awaited")
    for label in labels:
        if not (isinstance(label, str) and label.startswith("@")):
            errors.append(f"{where}: socket labels start with @: {label}")


def oracle(doc, errors):
    """Re-derive the expected value where an independent oracle exists."""
    op, inp, exp = doc["operation"], doc["input"], doc["expect"]
    where = doc["id"]
    if op in ("crypto.encrypt", "crypto.decrypt") and exp["outcome"] == "ok":
        if op == "crypto.encrypt":
            got = openssl(False, inp["keyBase64"], inp["ivBase64"], bytes.fromhex(inp["plaintextHex"])).hex()
            want = exp["value"]["ciphertextHex"]
        else:
            got = openssl(True, inp["keyBase64"], inp["ivBase64"], bytes.fromhex(inp["ciphertextHex"])).hex()
            want = exp["value"]["plaintextHex"]
        if got != want:
            errors.append(f"{where}: openssl gives {got}, case expects {want}")
    if op == "session.scenario":
        check_scenario(doc, errors)
        if exp != {"outcome": "ok", "value": {"completed": True}}:
            errors.append(f"{where}: a scenario expects {{outcome: ok, value: {{completed: true}}}}")
    if op == "mapping.interface":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from oracles.mapping import map_interface
        path = os.path.join(CONFORMANCE, inp["fixture"])
        if not os.path.isfile(path):
            errors.append(f"{where}: fixture {inp['fixture']} does not exist")
            return
        if exp["outcome"] == "ok":
            with open(path, encoding="utf-8") as f:
                got = map_interface(json.load(f))
            if got != exp["value"]:
                errors.append(f"{where}: the mapping oracle disagrees with the expected value")
    if op in ("coercion.boolFromOnOff", "coercion.truthy"):
        # The reference's own Python expressions, run on the decoded value.
        v = python_value(inp["value"])
        if op == "coercion.boolFromOnOff":
            got = None if v is None else (v == "ON" or v == True)  # noqa: E712 — the reference's comparison
        else:
            got = None if v is None else bool(v)
        if got != exp["value"]["result"]:
            errors.append(f"{where}: reference Python gives {got}, case expects {exp['value']['result']}")
    if op == "number.format":
        x = struct.unpack(">d", bytes.fromhex(inp["value"]["bits"]))[0]
        got = str(round(x, inp["precision"]))
        if got != exp["value"]["text"]:
            errors.append(f"{where}: str(round(x, p)) gives {got}, case expects {exp['value']['text']}")


def validate(path, errors):
    rel = os.path.relpath(path, CONFORMANCE)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except ValueError as e:
        errors.append(f"{rel}: invalid JSON: {e}")
        return None
    if set(doc) != CASE_KEYS:
        errors.append(f"{rel}: keys must be exactly {sorted(CASE_KEYS)}")
        return None
    cid = doc["id"]
    if doc["schemaVersion"] != SCHEMA_VERSION:
        errors.append(f"{rel}: schemaVersion must be {SCHEMA_VERSION}")
    if not ID_RE.match(cid) or os.path.basename(path) != cid + ".json":
        errors.append(f"{rel}: id must be dotted lowercase and equal the file name")
    if not doc["specRefs"] or any(r not in REQUIREMENTS for r in doc["specRefs"]):
        errors.append(f"{rel}: specRefs must be known requirement ids")
    op = doc["operation"]
    if op not in OPERATIONS:
        errors.append(f"{rel}: unknown operation {op}")
        return doc
    if not cid.startswith(op.split(".")[0] + "."):
        errors.append(f"{rel}: id should start with the operation group")
    _, required, optional = OPERATIONS[op]
    keys = set(doc["input"])
    if not required <= keys or not keys <= required | optional:
        errors.append(f"{rel}: input keys {sorted(keys)} do not match {op}")
    exp = doc["expect"]
    outcome = exp.get("outcome")
    if outcome == "ok" and set(exp) == {"outcome", "value"}:
        pass
    elif outcome == "error" and set(exp) == {"outcome", "code"} and exp["code"] in ERROR_CODES:
        pass
    elif outcome == "reject" and set(exp) == {"outcome"}:
        pass
    else:
        errors.append(f"{rel}: malformed expect {exp!r}")
        return doc
    walk_tagged(doc["input"], f"{rel}:input", errors)
    walk_tagged(exp, f"{rel}:expect", errors)
    oracle(doc, errors)
    return doc


def build_manifest():
    entries = []
    for path in case_files():
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        entries.append({
            "id": doc["id"],
            "path": os.path.relpath(path, CONFORMANCE),
            "sha256": sha256(path),
        })
    entries.sort(key=lambda e: e["id"])
    fixtures = [{"path": os.path.relpath(p, CONFORMANCE), "sha256": sha256(p)} for p in json_files(FIXTURES)]
    fixtures.sort(key=lambda e: e["path"])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "suiteVersion": SUITE_VERSION,
        "specVersion": SPEC_VERSION,
        "profiles": {profile: sorted(op for op, (p, _, _) in OPERATIONS.items() if p == profile)
                     for profile in sorted({p for p, _, _ in OPERATIONS.values()})},
        "requirements": dict(sorted(REQUIREMENTS.items())),
        "cases": entries,
        "fixtures": fixtures,
    }


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "manifest":
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(build_manifest(), f, indent=2)
            f.write("\n")
        print(f"wrote {os.path.relpath(MANIFEST, ROOT)}")
        return 0
    if command != "check":
        print(__doc__)
        return 2

    errors, ids, referenced = [], set(), set()
    for path in case_files():
        doc = validate(path, errors)
        if doc:
            if doc["id"] in ids:
                errors.append(f"duplicate id {doc['id']}")
            ids.add(doc["id"])
            if isinstance(doc.get("input"), dict) and "fixture" in doc["input"]:
                referenced.add(doc["input"]["fixture"])
    for path in json_files(FIXTURES):
        rel = os.path.relpath(path, CONFORMANCE)
        if rel not in referenced:
            errors.append(f"{rel}: fixture is not used by any case")
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            on_disk = json.load(f)
        if on_disk != build_manifest():
            errors.append("manifest.json is stale: run `python3 tools/suite.py manifest`")
    except FileNotFoundError:
        errors.append("manifest.json is missing: run `python3 tools/suite.py manifest`")

    for e in errors:
        print("✗", e)
    print(f"{len(ids)} cases, {len(errors)} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
