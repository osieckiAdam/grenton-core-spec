"""Oracle for `provisioning.omp`: import of an Object Manager project file,
written from the spec text (spec §4.5), not from a port.

    import_project(omp_bytes, interface_id=None) -> data.json-shaped dict
    project_interfaces(omp_bytes) -> [{"id", "name"}]

Errors raise OmpError with the canonical code (`provisioning.invalidData`,
`provisioning.interfaceNotFound`, `provisioning.interfaceAmbiguous`).
Widgets passed on as stubs (§4.5.6) carry CONFORMANCE_SKIP so the mapping
oracle treats them as skipped by §3.6.
"""

import io
import json
import math
import re
import zipfile
import xml.parsers.expat
from xml.etree import ElementTree as ET

MIB = 1024 * 1024
LIMIT = 64 * MIB          # §4.5.1 pt 3, recommended
MAX_ENTRIES = 10_000
NODE_RE = re.compile(r"nodes/[0-9]+/data\.json")
HEX_RE = re.compile(r"[0-9a-fA-F]{1,8}")
CLU_PORT = 1234
CONFORMANCE_SKIP = "x-conformance-expect-skip"


class OmpError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def invalid():
    return OmpError("provisioning.invalidData")


# MARK: - Archive (§4.5.1)

class Archive:
    def __init__(self, data):
        if len(data) > LIMIT:
            raise invalid()
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, ValueError, OSError):
            raise invalid()
        names = [i.filename for i in infos]
        if len(infos) > MAX_ENTRIES or len(set(names)) != len(names):
            raise invalid()
        self.infos = {i.filename: i for i in infos}
        self.names = names
        self.used = [n for n in names if n in ("properties.xml", "system.xml") or NODE_RE.fullmatch(n)]
        if sum(self.infos[n].file_size for n in self.used) > LIMIT:
            raise invalid()

    def read(self, name):
        info = self.infos.get(name)
        if info is None:
            return None
        if info.file_size > LIMIT:
            raise invalid()
        try:
            return self.zip.read(info)
        except (zipfile.BadZipFile, ValueError, OSError, EOFError, RuntimeError):
            raise invalid()


def reject_doctype(raw):
    """§4.5.1 pt 4: the XML parser itself (encoding-aware) reports a DOCTYPE."""
    parser = xml.parsers.expat.ParserCreate()

    def found(*_):
        raise invalid()
    parser.StartDoctypeDeclHandler = found
    parser.EntityDeclHandler = found
    try:
        parser.Parse(raw, True)
    except xml.parsers.expat.ExpatError:
        raise invalid()


def parse_xml(raw):
    if raw is None:
        raise invalid()
    reject_doctype(raw)       # before ElementTree ever sees the document
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise invalid()
    by_id = {}
    for el in root.iter():
        key = el.get("id")
        if key is not None:
            if key in by_id:
                raise invalid()
            by_id[key] = el
    return XmlDoc(root, by_id)


class XmlDoc:
    def __init__(self, root, by_id):
        self.root, self.by_id = root, by_id

    def resolve(self, el):
        """Follow `reference` chains (§4.5.1 pt 6)."""
        seen = set()
        while el is not None and el.get("reference") is not None:
            target = el.get("reference")
            if target in seen or target not in self.by_id:
                raise invalid()
            seen.add(target)
            el = self.by_id[target]
        return el

    def child(self, el, tag):
        found = el.find(tag)
        return self.resolve(found) if found is not None else None

    def text(self, el, tag):
        """§4.5.1 pt 7: trimmed text; empty means absent."""
        found = self.child(el, tag)
        if found is None or found.text is None:
            return None
        value = found.text.strip()
        return value or None


def hex_number(text):
    return int(text, 16) if text is not None and HEX_RE.fullmatch(text) else None


def strict_json(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise invalid()
            out[k] = v
        return out

    def constant(_):
        raise invalid()

    def number(text):
        value = float(text)
        if math.isinf(value):            # 1e400 would silently become inf
            raise invalid()
        return value

    def integer(text):
        value = int(text)
        if not -2**63 <= value < 2**63:
            raise invalid()
        return value
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant,
                          parse_float=number, parse_int=integer)
    except (UnicodeDecodeError, ValueError):
        raise invalid()


# MARK: - Interfaces (§4.5.2)

def interfaces(archive):
    found = []
    for name in archive.used:
        if not NODE_RE.fullmatch(name):
            continue
        doc = strict_json(archive.read(name))
        if isinstance(doc, dict) and isinstance(doc.get("pages"), list):
            if not isinstance(doc.get("id"), str) or not isinstance(doc.get("label"), str):
                raise invalid()
            found.append(doc)
    ids = [d["id"] for d in found]
    if len(set(ids)) != len(ids):
        raise invalid()
    return found


def select(found, interface_id):
    if interface_id is not None:
        match = [d for d in found if d["id"] == interface_id]
        if not match:
            raise OmpError("provisioning.interfaceNotFound")
        return match[0]
    if len(found) == 1:
        return found[0]
    if not found:
        raise OmpError("provisioning.interfaceNotFound")
    raise OmpError("provisioning.interfaceAmbiguous")


# MARK: - Pages and widgets (§4.5.6)

def present(obj, key):
    """Copy-unchanged rule: pass the field when present, even as null."""
    return {key: obj[key]} if key in obj else {}


class Missing(Exception):
    """A field the conversion needs is absent: the widget becomes a stub."""


def need(obj, key, kind=None):
    if not isinstance(obj, dict) or obj.get(key) is None:
        raise Missing()
    value = obj[key]
    if kind is dict and not isinstance(value, dict):
        raise Missing()
    return value


def exists(r):
    return isinstance(r, dict) and isinstance(r.get("objectName"), str) and r["objectName"] != ""


def ref(r):
    if not exists(r):
        return None
    out = {}
    for k in ("cluId", "objectName", "index", "callType"):
        out.update(present(r, k))
    return out


def is_action(v):
    return isinstance(v, dict) and "valueFormattedForDto" in v


def action(a):
    if not exists(a):
        return None
    out = {}
    out.update(present(a, "cluId"))
    out.update(present(a, "objectName"))
    if a.get("index") is not None:
        out["index"] = a["index"]
    if "type" in a:
        out["event"] = a["type"]
    out.update(present(a, "value"))
    if "callType" in a:
        out["callType"] = "ATTRIBUTE" if a["callType"] == "PSEUDO_METHOD" else a["callType"]
    return out


def with_ref(out, key, value):
    converted = ref(value)
    if converted is not None:
        out[key] = converted
    return out


def bistable(b):
    out = {"unit": "UNKNOWN"}
    for k in ("image", "label", "onIndication", "offIndication", "type", "rowId"):
        out.update(present(b, k))
    with_ref(out, "state", b.get("state"))
    out["actions"] = [x for x in (action(b.get("actionOn")), action(b.get("actionOff"))) if x]
    return out


def monostable(m):
    out = {"unit": "UNKNOWN"}
    for k in ("label", "type", "rowId"):
        out.update(present(m, k))
    out["actions"] = [x for x in (action(m.get("actionClick")),) if x]
    return out


def add_background(out, w):
    b = w.get("background")
    if isinstance(b, dict):
        bg = {}
        if "color" in b:
            bg["color"] = b["color"].upper() if isinstance(b["color"], str) else b["color"]
        if "image" in b:
            bg["name"] = b["image"]
        out["background"] = bg


def value_fields(c):
    out = {}
    for k in ("unit", "valueType", "precision", "icon", "label"):
        out.update(present(c, k))
    out["object"] = with_ref({}, "value", c.get("value"))
    return out


# Editor key -> mobile key; the editor spells the VOC channel `objectAirVco`.
MULTISENSOR_CHANNELS = [
    ("objectAirCo2", "objectAirCo2"), ("objectSound", "objectSound"), ("objectAirVco", "objectAirVoc"),
    ("objectLight", "objectLight"), ("objectPressure", "objectPressure"), ("objectHumidity", "objectHumidity"),
    ("objectTemperature", "objectTemperature"),
]


def refs_only(obj):
    """Every member that is an existing reference, under its own name."""
    out = {}
    for name, value in obj.items():
        converted = ref(value)
        if converted is not None:
            out[name] = converted
    return out


def led_slider(s):
    out = {}
    for k in ("unit", "label", "type", "rowId"):
        out.update(present(s, k))
    rng = {}
    for k in ("min", "max"):
        rng.update(present(s, k))
    out["range"] = rng
    with_ref(out, "state", s.get("state"))
    out["actions"] = [x for x in (action(s.get("actionClick")),) if x]
    return out


def contact_fields(c):
    out = {}
    for k in ("icon", "label", "onIndication", "offIndication", "reverseState"):
        out.update(present(c, k))
    out["object"] = object_block(need(c, "object", dict))
    return out


def object_block(obj):
    """O of §4.5.6. A field already named as its target (a reference, or an
    action ending in Action) wins over a bare-named action, whatever the
    member order."""
    out = {}
    for name, value in obj.items():          # pass 1: fields keeping their name
        if not isinstance(value, dict):
            continue
        if is_action(value):
            if name.endswith("Action"):
                converted = action(value)
                if converted is not None:
                    out[name] = converted
        else:
            converted = ref(value)
            if converted is not None:
                out[name] = converted
    for name, value in obj.items():          # pass 2: bare-named actions
        if isinstance(value, dict) and is_action(value) and not name.endswith("Action"):
            target = name + "Action"
            if target not in obj:                # a field already named so wins (§4.5.6 O)
                converted = action(value)
                if converted is not None:
                    out[target] = converted
    return out


def widget_id(w):
    """§4.5.6: `dtoId` when it is a non-empty string; otherwise (a widget
    never sent to a phone) `omp-` + the decimal text of the editor's integer
    `id`. The prefix keeps a stand-in from ever equalling a real dtoId."""
    if is_str(w.get("dtoId")) and w["dtoId"] != "":
        return w["dtoId"]
    if is_int(w.get("id")) and w["id"] >= 0:
        return f"omp-{w['id']}"
    return None


def convert_widget(w):
    t, wid = need(w, "type"), widget_id(w)
    if not is_str(t) or wid is None:
        raise Missing()
    out = {"id": wid, "type": t}
    if t == "HEADER":
        text = need(w, "text", dict)
        component = {"unit": "UNKNOWN"}
        for k in ("label", "type", "rowId"):
            component.update(present(text, k))
        out["components"] = [component]
    elif t == "ON_OFF":
        out["components"] = [bistable(need(w, "buttonBistable", dict))]
    elif t == "ON_OFF_DOUBLE":
        out["components"] = [bistable(need(w, "buttonBistableFirst", dict)),
                             bistable(need(w, "buttonBistableSecond", dict))]
    elif t == "SCENE":
        out["components"] = [monostable(need(w, "buttonMonostable", dict))]
        add_background(out, w)
    elif t == "SCENE_DOUBLE":
        out["components"] = [monostable(need(w, "buttonMonostableFirst", dict)),
                             monostable(need(w, "buttonMonostableSecond", dict))]
        add_background(out, w)
    elif t == "VALUE_V2":
        out.update(value_fields(w))
    elif t == "VALUE_DOUBLE":
        out["componentLeft"] = value_fields(need(w, "componentLeft", dict))
        out["componentRight"] = value_fields(need(w, "componentRight", dict))
    elif t == "DIMMER_V2":
        for k in ("min", "max", "precision", "icon", "label"):
            out.update(present(w, k))
        out["object"] = object_block(need(w, "object", dict))
    elif t == "THERMOSTAT_V2":
        for k in ("icon", "label"):
            out.update(present(w, k))
        if "noOfFanSpeeds" in w:
            out["no_of_fan_speeds"] = w["noOfFanSpeeds"]
        out["object"] = object_block(need(w, "object", dict))
    elif t in ("ROLLER_SHUTTER_V3", "EVENT_SCHEDULER"):
        for k in ("icon", "label"):
            out.update(present(w, k))
        out["object"] = object_block(need(w, "object", dict))
    elif t == "CAMERA":
        c = need(w, "cameraStream", dict)
        component = {"unit": "UNKNOWN"}
        for k in ("image", "label", "type", "rowId"):
            component.update(present(c, k))
        if "url" in c:
            component["value"] = c["url"]
        out["components"] = [component]
    elif t == "LED":
        components = [bistable(need(w, "buttonBistable", dict))]
        for key in ("sliderHue", "sliderSaturation", "sliderBrightness"):
            components.append(led_slider(need(w, key, dict)))
        out["components"] = components
    elif t == "CONTACT_SENSOR":
        out.update(contact_fields(w))
    elif t == "CONTACT_SENSOR_DOUBLE":
        out["componentLeft"] = contact_fields(need(w, "componentLeft", dict))
        out["componentRight"] = contact_fields(need(w, "componentRight", dict))
    elif t == "SLIDER":
        for k in ("unit", "min", "max", "precision", "icon", "label"):
            out.update(present(w, k))
        out["object"] = object_block(need(w, "object", dict))
    elif t == "MULTISENSOR":
        for k in ("icon", "label", "labelVisible"):
            out.update(present(w, k))
        for editor_key, mobile_key in MULTISENSOR_CHANNELS:
            channel = w.get(editor_key)
            if isinstance(channel, dict):
                out[mobile_key] = refs_only(channel)
    elif t == "ROLLER_SHUTTER":
        b = need(w, "buttonBistableClick", dict)
        component = {"unit": "UNKNOWN"}
        for k in ("image", "label", "indication", "rowId"):
            component.update(present(b, k))
        with_ref(component, "state", b.get("state"))
        if "type" in b:
            component["type"] = "BUTTON_BISTABLE" if b["type"] == "BUTTON_BISTABLE_CLICK" else b["type"]
        component["actions"] = [x for x in (action(b.get("actionClick")),) if x]
        out["components"] = [component]
    elif t == "TV_REMOTE_CONTROL":
        for k in ("vodLabel", "icon", "label"):
            out.update(present(w, k))
        out["object"] = object_block(need(w, "object", dict))
    else:
        raise Missing()
    return out


VALUE_CALLS = {"VARIABLE", "ATTRIBUTE"}
ACTION_CALLS = {"VARIABLE", "ATTRIBUTE", "METHOD", "SCRIPT"}
EVENTS = {"CLICK", "ON", "OFF"}


def is_str(v):
    return isinstance(v, str)


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def valid_ref(r):
    """§3.2: four strings, callType VARIABLE or ATTRIBUTE."""
    return isinstance(r, dict) and all(is_str(r.get(k)) for k in ("cluId", "objectName", "index", "callType")) \
        and r["callType"] in VALUE_CALLS


def valid_action(a):
    """§3.3: strings, event and callType from their sets, index except for SCRIPT."""
    if not isinstance(a, dict) or not all(is_str(a.get(k)) for k in ("cluId", "objectName", "value", "event", "callType")):
        return False
    if a["event"] not in EVENTS or a["callType"] not in ACTION_CALLS:
        return False
    return a["callType"] == "SCRIPT" or is_str(a.get("index"))


UNITS = {"UNKNOWN", "PERCENT", "DEGREE"}
VALUE_TYPES = {"STRING", "FLOAT", "INTEGER"}

# Fields §3.5 names as present for each converted type (component fields for
# the list-based ones); the parser of §3.6 pt 3 rejects a widget without them.
REQUIRED = {
    "VALUE_V2": ("label", "icon", "unit", "valueType", "precision"),
    "CONTACT_SENSOR": ("label", "icon", "onIndication", "offIndication", "reverseState"),
    "SLIDER": ("label", "icon", "unit", "min", "max", "precision"),
    "MULTISENSOR": ("label", "icon", "labelVisible"),
    "DIMMER_V2": ("label", "icon", "min", "max", "precision"),
    "THERMOSTAT_V2": ("label",),
    "ROLLER_SHUTTER_V3": ("icon", "label"),
}
COMPONENT_REQUIRED = {
    "HEADER": ("label",),
    "ON_OFF": ("label", "rowId", "unit", "state", "actions", "onIndication", "offIndication"),
    "ON_OFF_DOUBLE": ("label", "rowId", "unit", "state", "actions", "onIndication", "offIndication"),
    "SCENE": ("label", "rowId", "unit", "actions"),
    "SCENE_DOUBLE": ("label", "rowId", "unit", "actions"),
    "CAMERA": ("label", "rowId", "unit", "image", "value"),
    "ROLLER_SHUTTER": ("label", "rowId", "unit", "image", "state", "indication", "actions"),
    "LED": ("label", "rowId", "unit", "state", "actions"),
}
# The component type §3.5 names for list-based widgets (LED: a union).
COMPONENT_TYPE = {"CAMERA": {"CAMERA_STREAM"}, "ROLLER_SHUTTER": {"BUTTON_BISTABLE"}}
LED_VARIANT_REQUIRED = {"BUTTON_BISTABLE": ("image", "onIndication", "offIndication")}
OBJECT_REQUIRED = {
    "VALUE_V2": ("value",),
    "DIMMER_V2": ("value", "setValueAction", "onAction", "offAction"),
    "THERMOSTAT_V2": ("currentTemperature", "targetTemperature", "setTargetTemperatureAction"),
    "ROLLER_SHUTTER_V3": ("state", "position", "lamelPosition", "setPositionAction", "setLamelPositionAction",
                          "moveUpAction", "moveDownAction", "startAction", "setRollerBlockedAction"),
    "CONTACT_SENSOR": ("value",),
    "SLIDER": ("value", "setValueAction"),
}
MULTISENSOR_KEYS = ("objectAirCo2", "objectSound", "objectAirVoc", "objectLight", "objectPressure",
                    "objectHumidity", "objectTemperature")
LED_TYPES = ("BUTTON_BISTABLE", "SLIDER_HUE", "SLIDER_SATURATION", "SLIDER_BRIGHTNESS")
INDICATIONS = {"ON_OFF", "OPEN_CLOSED", "OPEN_LOCKED", "UP_DOWN"}


def has_required(w):
    t = w["type"]
    if any(w.get(k) is None for k in REQUIRED.get(t, ())):
        return False
    if t in COMPONENT_REQUIRED:
        comps = w.get("components")
        if not isinstance(comps, list) or not comps:
            return False
        if any(c.get(k) is None for c in comps for k in COMPONENT_REQUIRED[t]):
            return False
    if t in OBJECT_REQUIRED and any(k not in w.get("object", {}) for k in OBJECT_REQUIRED[t]):
        return False
    if t == "VALUE_DOUBLE":
        for side in ("componentLeft", "componentRight"):
            c = w.get(side, {})
            if any(c.get(k) is None for k in REQUIRED["VALUE_V2"]) or "value" not in c.get("object", {}):
                return False
    if t == "CONTACT_SENSOR_DOUBLE":
        for side in ("componentLeft", "componentRight"):
            c = w.get(side, {})
            if any(c.get(k) is None for k in REQUIRED["CONTACT_SENSOR"]) or "value" not in c.get("object", {}):
                return False
            if not isinstance(c.get("reverseState"), bool):
                return False
    if t == "CONTACT_SENSOR" and not isinstance(w.get("reverseState"), bool):
        return False
    if t == "MULTISENSOR":
        if not isinstance(w.get("labelVisible"), bool):
            return False
        if any(not valid_ref(w.get(k, {}).get("value")) for k in MULTISENSOR_KEYS):
            return False
    for c in w.get("components", []):
        if t in COMPONENT_TYPE and c.get("type") not in COMPONENT_TYPE[t]:
            return False
    if t == "LED":
        # a union by `type` (§3.6 pt 1): every component one of the four kinds,
        # each with its own fields; completeness is the mapping's concern (§6.3.3)
        for c in w.get("components", []):
            kind = c.get("type")
            if not is_str(kind) or kind not in LED_TYPES:
                return False
            if any(c.get(k) is None for k in LED_VARIANT_REQUIRED.get(kind, ())):
                return False
            if kind != "BUTTON_BISTABLE":
                rng = c.get("range")
                if not isinstance(rng, dict) or not is_int(rng.get("min")) or not is_int(rng.get("max")):
                    return False
    if t == "ROLLER_SHUTTER":
        for c in w.get("components", []):
            if not is_str(c.get("indication")) or c["indication"] not in INDICATIONS:
                return False
    if t == "MULTISENSOR":
        for k in MULTISENSOR_KEYS:
            channel = w.get(k)
            if isinstance(channel, dict) and not all(valid_ref(v) for v in channel.values()):
                return False
    return True


def parses(w):
    """The §3.5–§3.6 rules this spec states for the converted widget: an
    approximation (§3.5 is not a full schema), enough that a converted widget
    the oracle maps is one a port also parses."""
    if not is_str(w.get("id")) or not is_str(w.get("type")) or not has_required(w):
        return False
    for part in [w] + w.get("components", []) + [w[k] for k in ("componentLeft", "componentRight") if k in w]:
        for key in ("label", "icon", "image", "onIndication", "offIndication"):
            if key in part and not is_str(part[key]):
                return False
        for key in ("precision", "rowId"):
            if key in part and not is_int(part[key]):
                return False
        if "unit" in part and part["unit"] not in UNITS:
            return False
        if "valueType" in part and part["valueType"] not in VALUE_TYPES:
            return False
        if "state" in part and not valid_ref(part["state"]):
            return False
        if not all(valid_action(a) for a in part.get("actions", [])):
            return False
        if "object" in part:
            for name, value in part["object"].items():
                if not (valid_action(value) if name.endswith("Action") else valid_ref(value)):
                    return False
    for key in ("min", "max"):
        if key in w and not is_num(w[key]):
            return False
    return True


def widget(w):
    try:
        if not isinstance(w, dict):
            raise Missing()
        converted = convert_widget(w)
        if not parses(converted):
            converted[CONFORMANCE_SKIP] = True   # §3.6 skips it; its references still count (§4.5.4)
        return converted
    except Missing:
        stub = {}
        if isinstance(w, dict):
            if widget_id(w) is not None:
                stub["id"] = widget_id(w)
            if isinstance(w.get("type"), str):
                stub["type"] = w["type"]
        stub[CONFORMANCE_SKIP] = True
        return stub


def page(p):
    if not isinstance(p, dict) or not isinstance(p.get("label"), str) or not isinstance(p.get("icon"), str) \
            or not isinstance(p.get("widgets"), list):
        raise invalid()
    return {"isFullscreenWidget": False, "name": p["label"], "icon": p["icon"],
            "widgets": [widget(w) for w in p["widgets"]]}


def used_clus(pages):
    """cluIds of converted refs and actions; stubs carry none (§4.5.4)."""
    used = set()

    def add(r):
        if isinstance(r, dict) and isinstance(r.get("cluId"), str):
            used.add(r["cluId"])

    for p in pages:
        for w in p["widgets"]:
            for c in w.get("components", []):
                add(c.get("state"))
                for a in c.get("actions", []):
                    add(a)
            for side in ("componentLeft", "componentRight"):
                if side in w:
                    add(w[side]["object"].get("value"))
            for r in w.get("object", {}).values():
                add(r)
    return used


# MARK: - CLUs and push notifications (§4.5.4–§4.5.5)

def clus(doc, editor, used):
    out, seen = [], set()
    for el in doc.root.iter():
        if el.find("nameOnCLU") is None or el.find("ipAddress") is None:
            continue
        cid = doc.text(el, "nameOnCLU")
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        if cid not in used:
            continue
        name, ip, serial = doc.text(el, "name"), doc.text(el, "ipAddress"), hex_number(doc.text(el, "serialNumber"))
        if name is None or ip is None or serial is None:
            raise invalid()
        out.append({"id": cid, "name": name, "ip": ip, "serialNumber": str(serial), "port": CLU_PORT,
                    "connectionType": "LOCAL_ONLY" if editor.get("blockCloud") is True else "CLOUD"})
    return out


def push_notifications(doc):
    """§4.5.5: depth-first pre-order, each TreeObject visited once (the first
    time it is reached), iterative so neither depth nor shared references can
    blow up the walk."""
    out, visited = [], set()
    stack = [(top, None) for top in reversed(doc.root.findall("TreeObject"))]
    while stack:
        node, serial = stack.pop()
        node = doc.resolve(node)
        if id(node) in visited:
            continue
        visited.add(id(node))
        obj = doc.child(node, "specificObject")
        if obj is not None and doc.text(obj, "typeName") == "Push":
            object_id, type_id = doc.text(obj, "nameOnCLU"), hex_number(doc.text(obj, "typeId"))
            version = doc.text(obj, "typeVersion")
            if serial is not None and object_id is not None and type_id is not None and version is not None:
                out.append({"cluId": serial, "objectId": object_id, "objectType": f"PUSH-{type_id}-{version}"})
        here = serial
        if obj is not None and obj.find("serialNumber") is not None and obj.find("ipAddress") is not None:
            here = hex_number(doc.text(obj, "serialNumber"))   # the ancestor of the children
        children = doc.child(node, "children")
        if children is not None:
            stack.extend((sub, here) for sub in reversed(children.findall("TreeObject")))
    return out


# MARK: - Import (§4.5.3)

def project_interfaces(data):
    archive = Archive(data)
    parse_xml(archive.read("properties.xml"))
    parse_xml(archive.read("system.xml"))
    return [{"id": d["id"], "name": d["label"]} for d in interfaces(archive)]


def import_project(data, interface_id=None):
    archive = Archive(data)
    properties = parse_xml(archive.read("properties.xml"))
    system = parse_xml(archive.read("system.xml"))
    editor = select(interfaces(archive), interface_id)

    def first_text(tag):
        el = next((e for e in properties.root.iter() if e.tag == tag), None)
        return properties.text(el, ".") if el is not None else None
    key, iv = first_text("keyBytes"), first_text("ivBytes")
    if key is None or iv is None:
        raise invalid()
    if not isinstance(editor.get("icon"), str) or not isinstance(editor.get("theme"), str):
        raise invalid()
    pages = [page(p) for p in editor["pages"]]
    ids = [w["id"] for p in pages for w in p["widgets"] if "id" in w]
    if len(set(ids)) != len(ids):
        raise invalid()      # §4.5.6: two widgets with one id
    return {
        "id": editor["id"], "version": 0, "name": editor["label"], "icon": editor["icon"],
        "theme": editor["theme"].upper(),
        "encryption": {"key": key, "iv": iv},
        "clus": clus(system, editor, used_clus(pages)),
        "pages": pages,
        "pushNotifications": push_notifications(system),
    }
