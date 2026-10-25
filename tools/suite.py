#!/usr/bin/env python3
"""Maintenance tool for the conformance suite (stdlib only).

    python3 tools/suite.py check      validate every case, verify the manifest
    python3 tools/suite.py manifest   rewrite conformance/manifest.json
    python3 tools/suite.py bump X.Y.Z  set the spec and suite version everywhere
    python3 tools/suite.py fixtures   rebuild conformance/fixtures/projects/*.omp
                                      from conformance/sources/projects/*/

`check` also re-derives expected values that have an independent oracle:
AES cases through the `openssl` CLI, `number.format` through Python's
round() (the reference semantics), `mapping.interface` through
tools/oracles/mapping.py (written from spec §6), `provisioning.omp` through
tools/oracles/omp.py (written from spec §4.5) plus the mapping oracle, every
.omp fixture against its sources, and every double's
`decimal` against its `bits`. It never uses a port's output as the source
of truth.
"""
import base64
import hashlib
import io
import datetime
import json
import os
import re
import struct
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFORMANCE = os.path.join(ROOT, "conformance")
CASES = os.path.join(CONFORMANCE, "cases")
PROJECT_SOURCES = os.path.join(CONFORMANCE, "sources", "projects")
FIXTURES = os.path.join(CONFORMANCE, "fixtures")
MANIFEST = os.path.join(CONFORMANCE, "manifest.json")

SCHEMA_VERSION = 1
SUITE_VERSION = "0.12.1"
SPEC_VERSION = "0.12.1"

# operation -> (profile, required input keys, optional input keys)
OPERATIONS = {
    "crypto.encrypt": ("stateless", {"keyBase64", "ivBase64", "plaintextHex"}, set()),
    "crypto.decrypt": ("stateless", {"keyBase64", "ivBase64", "ciphertextHex"}, set()),
    "wire.request": ("stateless", {"msgId", "payload"}, set()),
    "wire.response": ("stateless", {"frame"}, set()),
    "payload.register": ("stateless", {"sessionId", "keys"}, set()),
    "payload.action": ("stateless", {"action"}, set()),
    "payload.report": ("stateless", {"payload"}, set()),
    "payload.fetch": ("stateless", {"keys"}, set()),
    "payload.values": ("stateless", {"payload"}, set()),
    "value.cast": ("stateless", {"text"}, set()),
    "provisioning.url": ("stateless", {"host", "pin"}, {"port"}),
    "coercion.boolFromOnOff": ("stateless", {"value"}, set()),
    "coercion.truthy": ("stateless", {"value"}, set()),
    "number.format": ("stateless", {"value", "precision"}, set()),
    "schedule.rules": ("stateless", {"text"}, set()),
    "schedule.cron": ("stateless", {"hour", "minute", "days"}, set()),
    "mapping.interface": ("mapping", {"fixture"}, set()),
    "provisioning.omp": ("project", {"fixture"}, {"interfaceId"}),
    "session.scenario": ("session", {"clu", "responseTimeoutMs", "steps"}, {"pingIntervalMs", "registerIntervalMs", "registerFrameLimitBytes"}),
}

# Stable requirement ids -> spec sections (see spec/conformance.md).
REQUIREMENTS = {
    "crypto.aes-cbc": "§5.1", "crypto.padding": "§5.1", "crypto.key-material": "§5.1",
    "wire.request": "§5.2", "wire.response": "§5.2", "wire.msg-id-case": "§5.2",
    "payload.register": "§5.3.2", "payload.report": "§5.3.2", "payload.action": "§5.3.4",
    "payload.fetch": "§5.3.2", "payload.values": "§5.3.2", "session.fetch": "§5.4.2",
    "model.cast": "§3.1", "model.action-ref": "§3.3, §3.6",
    "provisioning.url": "§4.1", "provisioning.pin": "§4.1",
    "provisioning.omp": "§4.5", "provisioning.omp-selection": "§4.5.2", "provisioning.omp-archive": "§4.5.1",
    "devices.coercion": "§6.2", "devices.number-format": "§10 pt 3", "devices.schedule": "§6.3.13",
    "model.widget-tolerance": "§3.6", "devices.mapping": "§6.1, §6.3", "devices.order": "§6.1", "devices.physical-objects": "§6.6",
    "session.correlation": "§5.4.1", "session.chunking": "§5.4.2, §5.3.2",
    "session.report-routing": "§5.4.3, §5.3.3", "session.keepalive": "§5.3.1",
    "session.lifecycle": "§5.4.5", "session.actions": "§5.3.4, §3.3",
    "session.loops": "§5.4.4",
}

ERROR_CODES = {"crypto.invalidKeyMaterial", "provisioning.connection", "payload.unresolvedPlaceholder",
               "provisioning.invalidData", "provisioning.interfaceNotFound", "provisioning.interfaceAmbiguous"}
CASE_KEYS = {"schemaVersion", "id", "specRefs", "description", "provenance", "operation", "input", "expect"}
ID_RE = re.compile(r"^[a-z0-9]+(\.[A-Za-z0-9-]+)+$")


def json_files(top):
    for dirpath, _, files in os.walk(top):
        for name in sorted(files):
            if name.endswith(".json"):
                yield os.path.join(dirpath, name)


def fixture_files():
    for dirpath, _, files in os.walk(FIXTURES):
        for name in sorted(files):
            if name.endswith((".json", ".omp")):
                yield os.path.join(dirpath, name)


def project_source_files(name):
    """Every file of conformance/sources/projects/<name>/, as archive paths in
    archive order: the order listed in its ORDER file, else sorted. ORDER
    itself is not part of the archive."""
    top = os.path.join(PROJECT_SOURCES, name)
    out = {}
    for dirpath, _, files in os.walk(top):
        for f in files:
            if f.startswith(".") or (dirpath == top and f == "ORDER"):
                continue
            path = os.path.join(dirpath, f)
            out[os.path.relpath(path, top).replace(os.sep, "/")] = path
    order_file = os.path.join(top, "ORDER")
    if os.path.isfile(order_file):
        with open(order_file, encoding="utf-8") as f:
            order = [line.strip() for line in f if line.strip()]
        if sorted(order) != sorted(out):
            raise SystemExit(f"sources/projects/{name}/ORDER does not list exactly its files")
        return {n: out[n] for n in order}
    return dict(sorted(out.items()))


def build_project(name):
    """A deterministic .omp: sorted entries, fixed timestamps, deflated."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, path in project_source_files(name).items():
            info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as f:
                archive.writestr(info, f.read())
    return buffer.getvalue()


def project_names():
    return sorted(d for d in os.listdir(PROJECT_SOURCES)
                  if os.path.isdir(os.path.join(PROJECT_SOURCES, d))) if os.path.isdir(PROJECT_SOURCES) else []


def check_projects(errors):
    """Each .omp fixture holds exactly its sources (compared by content, so a
    different zlib cannot fail it)."""
    for name in project_names():
        path = os.path.join(FIXTURES, "projects", name + ".omp")
        if not os.path.isfile(path):
            errors.append(f"fixtures/projects/{name}.omp is missing: run `python3 tools/suite.py fixtures`")
            continue
        with zipfile.ZipFile(path) as archive:
            built = [(n, archive.read(n)) for n in archive.namelist()]
        sources = []
        for arcname, src in project_source_files(name).items():
            with open(src, "rb") as f:
                sources.append((arcname, f.read()))
        if built != sources:   # names, order and content; not bytes (zlib may differ)
            errors.append(f"fixtures/projects/{name}.omp differs from its sources: run `python3 tools/suite.py fixtures`")


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
    "fail": ({"fail"}, set()),
}
CALLS = {"start", "stop", "ping", "register", "fetch", "execute"}
STATES = {"idle", "starting", "ready", "degraded", "stopped"}
EXECUTE_ERRORS = {"session.timeout", "session.notConnected", "session.connectionClosed",
                  "payload.unresolvedPlaceholder"}


DEFAULT_REGISTER_FRAME_LIMIT = 1471   # spec §5.3.2: UTF-8 frame bytes; the datagram is then ≤ 1472 B
REGISTER_RE = re.compile(r"^SYSTEM:clientRegister\(0,0,(\d+),\{(.*)\}\)$")
FETCH_RE = re.compile(r"^SYSTEM:fetchValues\(\{(.*)\}\)$")


def render_key(key):
    return f'"{key["name"]}"' if key["type"] == "variable" else f'{{{key["objectName"]},{key["index"]}}}'


def register_frame_bytes(rendered):
    """Spec §5.4.2 pt 1: the frame with the number counted as six digits."""
    return len(f"req::00000000:SYSTEM:clientRegister(0,0,999999,{{{','.join(rendered)}}})".encode("utf-8"))


def fetch_frame_bytes(rendered):
    """Spec §5.4.2, `fetch`: the same split with the fetchValues frame text."""
    return len(f"req::00000000:SYSTEM:fetchValues({{{','.join(rendered)}}})".encode("utf-8"))


def greedy_chunks(rendered, limit, frame_bytes=register_frame_bytes):
    """Spec §5.4.2 pt 1, from the first key: each chunk is the longest run that
    fits; a key larger than the limit alone is a chunk of its own."""
    chunks, i = [], 0
    while i < len(rendered):
        j = i + 1
        while j < len(rendered) and frame_bytes(rendered[i:j + 1]) <= limit:
            j += 1
        chunks.append(",".join(rendered[i:j]))
        i = j
    return chunks


def check_chunking(doc, errors):
    """Every expected clientRegister is the next chunk of the greedy split of
    the last registered key list. A registration (explicit, renewal or
    recovery) always starts again from the first chunk."""
    where = doc["id"]
    limit = doc["input"].get("registerFrameLimitBytes", DEFAULT_REGISTER_FRAME_LIMIT)
    if not isinstance(limit, int) or not 1 <= limit <= DEFAULT_REGISTER_FRAME_LIMIT:
        errors.append(f"{where}: registerFrameLimitBytes must be 1…{DEFAULT_REGISTER_FRAME_LIMIT}")
        return
    chunks, nxt = [], 0                 # the registered list, renewed by the loop
    fetch, fetch_nxt = [], 0            # the last fetch, sent once
    for n, step in enumerate(doc["input"]["steps"]):
        if step.get("call") == "register":
            chunks, nxt = greedy_chunks([render_key(k) for k in step["keys"]], limit), 0
            continue
        if step.get("call") == "fetch":
            fetch, fetch_nxt = greedy_chunks([render_key(k) for k in step["keys"]], limit, fetch_frame_bytes), 0
            continue
        sent = step.get("expectSent")
        payload = sent.get("payload") if isinstance(sent, dict) else None
        if not isinstance(payload, str):
            continue
        if match := FETCH_RE.match(payload):
            if fetch_nxt < len(fetch) and match.group(1) == fetch[fetch_nxt]:
                fetch_nxt += 1
            else:
                errors.append(f"{where}: step {n}: expected chunk {fetch_nxt + 1} of the {limit} B fetch split")
            continue
        match = REGISTER_RE.match(payload)
        if not match:
            continue
        body = match.group(2)
        if nxt < len(chunks) and body == chunks[nxt]:
            nxt += 1
        elif chunks and body == chunks[0]:
            nxt = 1
        else:
            errors.append(f"{where}: step {n}: expected chunk {nxt + 1} of the {limit} B split, or chunk 1 of a new registration")


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
            if (step["call"] in ("register", "fetch")) != ("keys" in step) or (step["call"] == "execute") != ("action" in step):
                errors.append(f"{at}: register and fetch take keys, execute takes action, nothing else does")
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
                if kind in ("register", "fetch") and (not isinstance(result, dict) or set(result) != {"slots"}):
                    errors.append(f"{at}: {kind} result is {{slots}}")
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
        elif verb in ("expectClosed", "fail") and step[verb] not in labels:
            errors.append(f"{at}: socket {step[verb]} is not bound yet")
        elif verb == "expectState" and step["expectState"] not in STATES:
            errors.append(f"{at}: unknown state {step['expectState']}")
        elif verb == "advance" and not (isinstance(step["advance"], int) and step["advance"] > 0):
            errors.append(f"{at}: advance takes a positive number of milliseconds")
    for name in set(calls) - awaited:
        errors.append(f"{where}: call {name} is never awaited")
    for label in labels:
        if not (isinstance(label, str) and label.startswith("@")):
            errors.append(f"{where}: socket labels start with @: {label}")


SCHEMA_PATH = os.path.join(CONFORMANCE, "schemas", "case.schema.json")
SCHEMA_KEYWORDS = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "const", "enum",
                   "pattern", "minimum", "maximum", "required", "properties", "additionalProperties", "items",
                   "minItems", "allOf", "oneOf", "if", "then"}


def schema_errors(value, schema, root, path="$"):
    """The subset of JSON Schema that case.schema.json uses, stdlib only.
    Yields one message per violation; an unknown keyword is an error, so the
    schema cannot outgrow this checker unnoticed."""
    if isinstance(schema, bool):
        if not schema:
            yield f"{path}: not allowed"
        return
    unknown = set(schema) - SCHEMA_KEYWORDS
    if unknown:
        raise ValueError(f"case.schema.json uses {sorted(unknown)}, which tools/suite.py does not check")
    if "$ref" in schema:
        name = schema["$ref"].removeprefix("#/$defs/")
        yield from schema_errors(value, root["$defs"][name], root, path)
    types = {"object": dict, "array": list, "string": str, "boolean": bool}
    t = schema.get("type")
    if isinstance(t, list):
        if all(any(schema_errors(value, {"type": x}, root, path)) for x in t):
            yield f"{path}: expected one of {t}"
            return
        t = None
    if t == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            yield f"{path}: expected an integer"
            return
    elif t in types and (not isinstance(value, types[t]) or (t != "boolean" and isinstance(value, bool))):
        yield f"{path}: expected {t}"
        return
    if "const" in schema and value != schema["const"]:
        yield f"{path}: must be {schema['const']!r}"
    if "enum" in schema and value not in schema["enum"]:
        yield f"{path}: must be one of {schema['enum']}"
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        yield f"{path}: does not match {schema['pattern']}"
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        yield f"{path}: must be ≥ {schema['minimum']}"
    if "maximum" in schema and isinstance(value, (int, float)) and value > schema["maximum"]:
        yield f"{path}: must be ≤ {schema['maximum']}"
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                yield f"{path}: missing {key}"
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                yield from schema_errors(item, props[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                yield f"{path}: unexpected {key}"
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            yield f"{path}: needs at least {schema['minItems']} item(s)"
        if "items" in schema:
            for n, item in enumerate(value):
                yield from schema_errors(item, schema["items"], root, f"{path}[{n}]")
    for sub in schema.get("allOf", []):
        yield from schema_errors(value, sub, root, path)
    if "oneOf" in schema:
        matching = sum(1 for sub in schema["oneOf"] if not any(schema_errors(value, sub, root, path)))
        if matching != 1:
            yield f"{path}: matches {matching} of the oneOf alternatives, needs exactly 1"
    if "if" in schema and not any(schema_errors(value, schema["if"], root, path)):
        yield from schema_errors(value, schema.get("then", {}), root, path)


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
        check_chunking(doc, errors)
        if exp != {"outcome": "ok", "value": {"completed": True}}:
            errors.append(f"{where}: a scenario expects {{outcome: ok, value: {{completed: true}}}}")
    if op == "provisioning.omp":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from oracles.omp import OmpError
        path = os.path.join(CONFORMANCE, inp["fixture"])
        if not os.path.isfile(path):
            errors.append(f"{where}: fixture {inp['fixture']} does not exist")
            return
        with open(path, "rb") as f:
            raw = f.read()
        got = omp_case_value(raw, inp.get("interfaceId"), OmpError)
        try:
            from oracles.omp import import_project
            errors.extend(f"{where}: {p}" for p in converted_widget_problems(import_project(raw, inp.get("interfaceId"))))
        except OmpError:
            pass
        if not json_equal(got, exp):
            errors.append(f"{where}: the .omp oracle gives {json.dumps(got)[:300]}")
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


def json_equal(a, b):
    """Type-aware: Python's False == 0 and 1 == 1.0 must not pass."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# The enumerations §3.5 names: a converted widget that breaks them would be
# skipped by §3.6 in a port while the mapping oracle mapped it.
UNITS = {"UNKNOWN", "PERCENT", "DEGREE"}
VALUE_TYPES = {"STRING", "FLOAT", "INTEGER"}


def converted_widget_problems(converted):
    from oracles.omp import CONFORMANCE_SKIP
    problems = []
    for p in converted["pages"]:
        for w in p["widgets"]:
            if CONFORMANCE_SKIP in w:
                continue
            parts = [w] + w.get("components", []) + [w[k] for k in ("componentLeft", "componentRight") if k in w]
            for part in parts:
                if "unit" in part and part["unit"] not in UNITS:
                    problems.append(f"{w['id']}: unit {part['unit']!r} is not one of §3.5")
                if "valueType" in part and part["valueType"] not in VALUE_TYPES:
                    problems.append(f"{w['id']}: valueType {part['valueType']!r} is not one of §3.5")
    return problems


def omp_case_value(data, interface_id, OmpError):
    """The expected outcome of a `provisioning.omp` case (conformance.md §4c)."""
    from oracles.omp import import_project, project_interfaces
    from oracles.mapping import map_interface
    try:
        converted = import_project(data, interface_id)
        listed = project_interfaces(data)
    except OmpError as e:
        return {"outcome": "error", "code": e.code}
    header = {k: converted[k] for k in ("id", "version", "name", "icon", "theme", "encryption", "clus",
                                        "pushNotifications")}
    from oracles.omp import CONFORMANCE_SKIP
    from oracles.mapping import PRESENTATIONAL, SUPPORTED
    # Widgets §3.6 keeps (§3.5 catalog and HEADER), as {id, type}.
    header["pages"] = [{"name": p["name"], "icon": p["icon"], "isFullscreenWidget": p["isFullscreenWidget"],
                        "widgets": [{"id": w["id"], "type": w["type"]} for w in p["widgets"]
                                    if CONFORMANCE_SKIP not in w and w.get("type") in SUPPORTED | PRESENTATIONAL]}
                       for p in converted["pages"]]
    return {"outcome": "ok", "value": {"interfaces": listed, "interface": header,
                                       "mapping": map_interface(converted)}}


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
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    errors.extend(f"{rel}: schema: {e}" for e in schema_errors(doc, schema, schema))
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
    fixtures = [{"path": os.path.relpath(p, CONFORMANCE), "sha256": sha256(p)} for p in fixture_files()]
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


def hooks_installed(errors):
    """The gates run from tools/hooks only once a clone has enabled them.
    CI has no hooks and runs the gates itself."""
    if os.environ.get("CI"):
        return
    result = subprocess.run(["git", "config", "--get", "core.hooksPath"], capture_output=True, text=True, cwd=ROOT)
    if result.stdout.strip() != "tools/hooks":
        errors.append("git hooks are not enabled in this clone: run `git config core.hooksPath tools/hooks`")


def bump(version):
    """Every place a spec/suite version lives, in one step; the CHANGELOG
    heading is printed, because its entry needs a human."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("usage: python3 tools/suite.py bump <major.minor.patch>")
    edits = [
        (os.path.join(ROOT, "spec", "grenton-core-design.md"),
         [(r"\| \*\*Version\*\* \| [0-9.]+ \|", f"| **Version** | {version} |"),
          (r"\| \*\*Date\*\* \| [0-9-]+ \|", f"| **Date** | {datetime.date.today().isoformat()} |")]),
        (os.path.join(ROOT, "spec", "conformance.md"),
         [(r"\| \*\*Suite version\*\* \| [0-9.]+ \(specification [0-9.]+,",
           f"| **Suite version** | {version} (specification {version},")]),
        (os.path.join(ROOT, "tools", "suite.py"),
         [(r'SUITE_VERSION = "[0-9.]+"', f'SUITE_VERSION = "{version}"'),
          (r'SPEC_VERSION = "[0-9.]+"', f'SPEC_VERSION = "{version}"')]),
    ]
    for path, pairs in edits:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for pattern, replacement in pairs:
            text, n = re.subn(pattern, replacement, text, count=1)
            if n != 1:
                raise SystemExit(f"{os.path.relpath(path, ROOT)}: pattern not found: {pattern}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"bumped {os.path.relpath(path, ROOT)}")
    print(f"now add to CHANGELOG.md, above the newest entry:\n\n"
          f"## Spec {version}, suite {version} — {datetime.date.today().isoformat()}\n\n"
          f"then run: python3 tools/suite.py manifest")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "bump":
        bump(sys.argv[2] if len(sys.argv) > 2 else "")
        return 0
    if command == "manifest":
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(build_manifest(), f, indent=2)
            f.write("\n")
        print(f"wrote {os.path.relpath(MANIFEST, ROOT)}")
        return 0
    if command == "fixtures":
        os.makedirs(os.path.join(FIXTURES, "projects"), exist_ok=True)
        for name in project_names():
            with open(os.path.join(FIXTURES, "projects", name + ".omp"), "wb") as f:
                f.write(build_project(name))
            print(f"wrote conformance/fixtures/projects/{name}.omp")
        return 0
    if command != "check":
        print(__doc__)
        return 2

    errors, ids, referenced = [], set(), set()
    hooks_installed(errors)
    for path in case_files():
        doc = validate(path, errors)
        if doc:
            if doc["id"] in ids:
                errors.append(f"duplicate id {doc['id']}")
            ids.add(doc["id"])
            if isinstance(doc.get("input"), dict) and "fixture" in doc["input"]:
                referenced.add(doc["input"]["fixture"])
    check_projects(errors)
    for path in fixture_files():
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
