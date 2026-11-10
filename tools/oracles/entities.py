"""Bound entities, written from spec §6.1–§6.3 (conformance.md §4e).

Takes the entities of the mapping oracle (tools/oracles/mapping.py) and a
filled state store, and computes what an entity reads and which actions an
intent sends. Never derived from a port.

The store maps a ValueRef (as in the mapping projection) to a Python value:
None for a stored null, bool, int, float or str. A key absent from the store
has never been reported.
"""
import json
import math
import re
import struct

INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1


class Reject(Exception):
    """The entity refused the input; nothing is sent (§6.3)."""


# ---------- tagged values (conformance.md §3) ----------

def from_tagged(t):
    kind = t["type"]
    if kind == "null":
        return None
    if kind in ("bool", "string"):
        return t["value"]
    if kind == "int":
        return int(t["value"])
    if kind == "double":
        return struct.unpack(">d", bytes.fromhex(t["bits"]))[0]
    raise ValueError(kind)


def tagged_double(x):
    if x is None:
        return None
    x = float(x)
    return {"type": "double", "bits": struct.pack(">d", x).hex(), "decimal": repr(x)}


def tagged_int(i):
    return None if i is None else {"type": "int", "value": str(i)}


def to_tagged(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "bool", "value": v}
    if isinstance(v, int):
        return tagged_int(v)
    if isinstance(v, float):
        return tagged_double(v)
    return {"type": "string", "value": v}


def ref_key(ref):
    return json.dumps(ref, sort_keys=True)


class Store:
    MISSING = object()

    def __init__(self, values):
        self.values = {ref_key(v["key"]): from_tagged(v["value"]) for v in values}

    def raw(self, ref):
        """The stored value, or MISSING when never reported."""
        if ref is None:
            return Store.MISSING
        return self.values.get(ref_key(ref), Store.MISSING)

    def get(self, ref):
        """The stored value; None both for a stored null and for unknown."""
        v = self.raw(ref)
        return None if v is Store.MISSING else v


# ---------- §6.1–§6.2 primitives ----------

def map_range(a, b, c, d, v):
    # mapRange((a,b) → (c,d), v) = c + (v−a)/(b−a) · (d−c);  when b−a ≤ 0 → c
    span = b - a
    if span <= 0:
        return float(c)
    return c + (v - a) / span * (d - c)


def clamp(v, lo, hi):
    return min(max(v, lo), hi)


def bool_from_on_off(v):
    # §6.2, the reference's own expression; None is unknown.
    if v is None:
        return None
    return v == "ON" or v == True  # noqa: E712


def truthy(v):
    return None if v is None else bool(v)


def number(v):
    # §6.2: int/double directly; a string is not re-parsed; bool is not a number.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def integer(v):
    # §6.2 integer reads: int directly; a double truncated toward zero
    # (Python int()); non-finite or outside Int64 → unknown; others unknown.
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        t = math.trunc(v)
        return t if INT64_MIN <= t <= INT64_MAX else None
    return None


def fmt(x, precision):
    # §6.3 `format(round(x, p))`, §10 item 3: Python's str(round(x, p)).
    return str(round(float(x), precision))


def with_value(action, value):
    out = dict(action)
    out["value"] = value
    return out


# ---------- §6.3.13 schedule rules ----------

def parse_cron(cron):
    fields = cron.split()
    if len(fields) != 5 or fields[2] != "*" or fields[3] != "*":
        return None
    minute, hour = fields[0], fields[1]
    if not (minute.isascii() and minute.isdigit() and hour.isascii() and hour.isdigit()):
        return None
    minute, hour = int(minute), int(hour)
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        return None
    if fields[4] == "*":
        return hour, minute, list(range(7))
    days = set()
    for item in fields[4].split(","):
        bounds = item.split("-")
        if len(bounds) > 2 or not all(b.isascii() and b.isdigit() for b in bounds):
            return None
        lo, hi = int(bounds[0]), int(bounds[-1])
        if not (0 <= lo <= 7 and 0 <= hi <= 7 and lo <= hi):
            return None
        days.update(0 if d == 7 else d for d in range(lo, hi + 1))
    return hour, minute, sorted(days)


def rule(rid, enabled, cron):
    parsed = parse_cron(cron)
    return {"id": rid, "isEnabled": enabled == 1, "cron": cron,
            "hour": parsed[0] if parsed else None, "minute": parsed[1] if parsed else None,
            "days": parsed[2] if parsed else None}


WS = " \t\r\n"
INTEGER = re.compile(r"-?[0-9]+")


def parse_row(row):
    """`{id, enabled, "cron"}`: two integers and a quoted text, comma-separated,
    whitespace allowed outside the quotes (§6.3.13)."""
    row = row.strip(WS)
    if not (row.startswith("{") and row.endswith("}")):
        return None
    inner = row[1:-1]
    q1, q2 = inner.find('"'), inner.rfind('"')
    if q1 < 0 or q2 <= q1 or inner[q2 + 1:].strip(WS) != "":
        return None
    head = [p.strip(WS) for p in inner[:q1].split(",")]
    if len(head) != 3 or head[2] != "":
        return None
    if not all(INTEGER.fullmatch(h) for h in head[:2]):
        return None
    return rule(int(head[0]), int(head[1]), inner[q1 + 1:q2])


def split_top(inner):
    rows, depth, in_text, start = [], 0, False, 0
    for i, ch in enumerate(inner):
        if ch == '"':
            in_text = not in_text
        elif in_text:
            continue
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            rows.append(inner[start:i])
            start = i + 1
    if depth != 0 or in_text:
        return None
    rows.append(inner[start:])
    return rows


def schedule_rules(text):
    """The rules of a `ruleList`, `nextRule` or `currentRule` text, in the
    CLU's order; None when no rules are known (§6.3.13)."""
    t = text.strip(WS)
    if not (t.startswith("{") and t.endswith("}")):
        return None
    inner = t[1:-1].strip(WS)
    if inner.startswith("{"):
        rows = split_top(inner)
        if rows is None:
            return None
        parsed = [parse_row(r) for r in rows]
        return parsed if parsed and all(parsed) else None
    one = parse_row(t)
    return [one] if one else None


def schedule_cron(hour, minute, days):
    """The CRON text addRule sends; None when out of range (§6.3.13)."""
    if not (0 <= hour <= 23 and 0 <= minute <= 59) or not days or any(not 0 <= d <= 6 for d in days):
        return None
    days = set(days)
    field = "*" if len(days) == 7 else ",".join(str(d) for d in [1, 2, 3, 4, 5, 6, 0] if d in days)
    return f"{minute} {hour} * * {field}"


# ---------- reads (§6.3) ----------

def channel(ch, store, lo, hi):
    v = number(store.get(ch["state"]))
    if v is None:
        return None
    return clamp(map_range(ch["range"]["min"], ch["range"]["max"], lo, hi, v), lo, hi)


def text_rules(store, ref):
    v = store.get(ref)
    return schedule_rules(v) if isinstance(v, str) else None


def read(entity, store):
    k = entity["kind"]
    g = store.get
    if k == "switch":
        return {"isOn": bool_from_on_off(g(entity["state"]))}
    if k == "dimmableLight":
        v = number(g(entity["state"]))
        return {"isOn": None if v is None else v > 0,
                "brightness": tagged_double(None if v is None else
                                            clamp(map_range(entity["min"], entity["max"], 0, 1, v), 0, 1))}
    if k == "hsbLight":
        return {"isOn": truthy(g(entity["powerState"])),
                "hue": tagged_double(channel(entity["hue"], store, 0, 360)),
                "saturation": tagged_double(channel(entity["saturation"], store, 0, 1)),
                "brightness": tagged_double(channel(entity["brightness"], store, 0, 1))}
    if k == "contactSensor":
        t = truthy(g(entity["state"]))
        return {"isActive": None if t is None else t != entity["reversed"]}
    if k == "sensor":
        raw = store.raw(entity["state"])
        return {"value": None if raw is Store.MISSING else to_tagged(raw)}
    if k == "numberSetting":
        return {"value": tagged_double(number(g(entity["state"])))}
    if k == "cover":
        p = integer(g(entity["position"]))
        position = None if p is None else clamp(p, 0, 100)
        motion = {0: "idle", 1: "opening", 2: "closing"}.get(integer(g(entity["motionState"])), "unknown")
        tilt = None
        if entity["tilt"] is not None:
            lamel = integer(g(entity["tilt"]["position"]))
            if lamel is not None:
                tilt = clamp(int(100 - map_range(0, 90, 0, 100, lamel)), 0, 100)
        return {"position": tagged_int(position), "isClosed": None if position is None else position == 0,
                "motion": motion, "tilt": tagged_int(tilt)}
    if k == "stateSensor":
        return {"state": {0: "stopped", 1: "opening", 2: "closing", 3: "locked"}.get(
            integer(g(entity["state"])), "unknown")}
    if k == "thermostat":
        opt = lambda ref: None if ref is None else number(g(ref))  # noqa: E731
        mode = None
        if entity["mode"] is not None:
            mode = {0: "manual", 1: "holiday", 2: "schedule"}.get(integer(g(entity["mode"]["state"])))
        return {"currentTemperature": tagged_double(number(g(entity["currentTemperature"]))),
                "targetTemperature": tagged_double(number(g(entity["targetTemperature"]))),
                "minTemperature": tagged_double(opt(entity["minTemperature"])),
                "maxTemperature": tagged_double(opt(entity["maxTemperature"])),
                "output": tagged_double(opt(entity["output"])),
                "mode": mode,
                "isOn": None if entity["power"] is None else truthy(g(entity["power"]["state"]))}
    if k == "eventSchedule":
        opt_int = lambda ref: None if ref is None else integer(g(ref))  # noqa: E731
        first = lambda ref: (text_rules(store, ref) or [None])[0] if ref is not None else None  # noqa: E731
        return {"isActive": truthy(g(entity["state"])),
                "rules": text_rules(store, entity["ruleList"]),
                "ruleCount": tagged_int(opt_int(entity["ruleCount"])),
                "freeRuleSlots": tagged_int(opt_int(entity["ruleSpace"])),
                "nextRule": first(entity["nextRule"]),
                "currentRule": first(entity["currentRule"])}
    raise ValueError(f"entity kind {k} has no state")


# ---------- intents (§6.3) ----------

def intent(entity, store, name, args):
    """The actions the intent sends, in order; raises Reject."""
    k = entity["kind"]
    a = args or {}
    if k == "switch":
        return [{"turnOn": entity["onAction"], "turnOff": entity["offAction"]}[name]]
    if k == "dimmableLight":
        if name in ("turnOn", "turnOff"):
            return [entity["onAction" if name == "turnOn" else "offAction"]]
        b = clamp(a["brightness"], 0, 1)
        return [with_value(entity["setValueAction"],
                           fmt(map_range(0, 1, entity["min"], entity["max"], b), entity["precision"]))]
    if k == "hsbLight":
        def set_channel(ch, lo, hi, v):
            return with_value(ch["setAction"],
                              fmt(map_range(lo, hi, ch["range"]["min"], ch["range"]["max"], clamp(v, lo, hi)), 2))
        if name in ("turnOn", "turnOff"):
            return [entity["onAction" if name == "turnOn" else "offAction"]]
        if name == "setColor":
            return [set_channel(entity["hue"], 0, 360, a["hue"]),
                    set_channel(entity["saturation"], 0, 1, a["saturation"])]
        return [set_channel(entity["brightness"], 0, 1, a["brightness"])]
    if k == "numberSetting":
        v = clamp(a["value"], entity["min"], entity["max"])
        return [with_value(entity["setValueAction"], fmt(v, entity["precision"]))]
    if k == "cover":
        def position(p):
            return [with_value(entity["setPositionAction"], str(clamp(p, 0, 100)))]

        def tilt(t):
            if entity["tilt"] is None:
                return []
            t = clamp(t, 0, 100)
            return [with_value(entity["tilt"]["setAction"], str(int(90 - map_range(0, 100, 0, 90, t))))]
        if name == "open":
            return position(100)
        if name == "close":
            return position(0)
        if name == "setPosition":
            return position(a["position"])
        if name == "stop":
            return [] if entity["stopAction"] is None else [entity["stopAction"]]
        if name == "openTilt":
            return tilt(100)
        if name == "closeTilt":
            return tilt(0)
        return tilt(a["tilt"])
    if k == "trigger":
        return [entity["action"]]
    if k == "sceneTrigger":
        if "parameter" in a:
            return [with_value(entity["action"], a["parameter"])]
        return [entity["action"]]
    if k == "thermostat":
        if name == "setTargetTemperature":
            t = a["temperature"]
            lo = None if entity["minTemperature"] is None else number(store.get(entity["minTemperature"]))
            hi = None if entity["maxTemperature"] is None else number(store.get(entity["maxTemperature"]))
            if lo is not None and hi is not None and lo <= hi:
                t = clamp(t, lo, hi)
            return [with_value(entity["setTargetTemperatureAction"], fmt(float(t), 1))]
        if name == "setMode":
            if entity["mode"] is None:
                return []
            return [with_value(entity["mode"]["setAction"],
                               {"manual": "0", "holiday": "1", "schedule": "2"}[a["mode"]])]
        if entity["power"] is None:
            return []
        return [with_value(entity["power"]["setAction"], "1" if name == "turnOn" else "0")]
    if k == "eventSchedule":
        if name == "setActive":
            act = entity["startAction"] if a["active"] else entity["stopAction"]
            return [] if act is None else [act]
        if name == "addRule":
            cron = schedule_cron(a["hour"], a["minute"], a["days"])
            if cron is None:
                raise Reject()
            return [with_value(entity["addRuleAction"], cron)]
        field = {"deleteRule": "deleteRuleAction", "enableRule": "enableRuleAction",
                 "disableRule": "disableRuleAction"}[name]
        act = entity[field]
        return [] if act is None else [with_value(act, str(a["id"]))]
    if k == "tvRemote":
        act = entity["buttons"].get(a["button"])
        return [] if act is None else [act]
    raise ValueError(f"entity kind {k} has no intents")


def find_entity(mapping, entity_id):
    for device in mapping["devices"]:
        for e in device["entities"]:
            if e["id"] == entity_id:
                return e
    return None


def case_value(mapping, inp, op):
    """The expected outcome of an entity.* case."""
    entity = find_entity(mapping, inp["entityId"])
    if entity is None:
        raise LookupError(inp["entityId"])
    store = Store(inp["values"])
    if op == "entity.read":
        return {"outcome": "ok", "value": {"state": read(entity, store)}}
    try:
        actions = intent(entity, store, inp["intent"], inp.get("args"))
    except Reject:
        return {"outcome": "reject"}
    return {"outcome": "ok", "value": {"actions": actions}}
