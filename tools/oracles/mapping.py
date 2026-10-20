"""Independent oracle for `mapping.interface` (spec §3.6, §6.1, §6.3).

Written from the specification text, not from any port. Widget decoding
tolerance (§3.5–§3.6) needs a full field schema the spec does not spell out,
so fixtures mark widgets that must fail decoding with the extra field
`"x-conformance-expect-skip": true` — ports ignore unknown fields (§3.6 pt 4);
only this oracle reads it. Unknown `type` values are skipped automatically.
"""

SUPPORTED = {
    "VALUE_V2", "VALUE_DOUBLE", "ON_OFF", "ON_OFF_DOUBLE", "DIMMER_V2", "LED",
    "CONTACT_SENSOR", "CONTACT_SENSOR_DOUBLE", "SLIDER", "MULTISENSOR",
    "ROLLER_SHUTTER", "ROLLER_SHUTTER_V3", "CAMERA", "SCENE",
    "SCENE_DOUBLE", "THERMOSTAT_V2",
}

# §3.5: known layout-only widgets — no device, not reported as skipped.
PRESENTATIONAL = {"HEADER"}

DEVICE_KIND = {
    "ON_OFF": "switch", "ON_OFF_DOUBLE": "switch",
    "DIMMER_V2": "dimmableLight", "LED": "hsbLight",
    "CONTACT_SENSOR": "contactSensor", "CONTACT_SENSOR_DOUBLE": "contactSensor",
    "VALUE_V2": "sensor", "VALUE_DOUBLE": "sensor", "MULTISENSOR": "multisensor",
    "SLIDER": "numberSetting", "ROLLER_SHUTTER": "cover", "ROLLER_SHUTTER_V3": "cover",
    "SCENE": "scene", "SCENE_DOUBLE": "scene", "CAMERA": "camera",
    "THERMOSTAT_V2": "thermostat",
}

# §6.3.6: suffix, channel field, canonical kind, unit — in table order.
MULTISENSOR_CHANNELS = [
    ("air_co2", "objectAirCo2", "co2", "ppm"),
    ("sound", "objectSound", "soundPressure", "dB"),
    ("air_voc", "objectAirVoc", "voc", "ppb"),
    ("light", "objectLight", "illuminance", "lx"),
    ("pressure", "objectPressure", "pressure", "hPa"),
    ("humidity", "objectHumidity", "humidity", "%"),
    ("temperature", "objectTemperature", "temperature", "°C"),
]


def value_ref(v):
    if v["callType"] == "VARIABLE":
        return {"callType": "VARIABLE", "cluId": v["cluId"], "index": v["index"]}
    return {"callType": "ATTRIBUTE", "cluId": v["cluId"], "objectName": v["objectName"], "index": v["index"]}


def action_ref(a):
    out = {"callType": a["callType"], "cluId": a["cluId"], "objectName": a["objectName"],
           "event": a["event"], "value": a["value"]}
    if a["callType"] != "SCRIPT":
        out["index"] = a["index"]
    return out


def first(actions, event):
    return next((a for a in actions if a["event"] == event), None)


def joined(labels):
    return " / ".join(l for l in labels if l)


def device_name(w):
    t = w["type"]
    if t in ("VALUE_V2", "DIMMER_V2", "CONTACT_SENSOR", "SLIDER", "MULTISENSOR", "ROLLER_SHUTTER_V3",
             "THERMOSTAT_V2"):
        return w["label"]
    if t in ("VALUE_DOUBLE", "CONTACT_SENSOR_DOUBLE"):
        return joined([w["componentLeft"]["label"], w["componentRight"]["label"]])
    if t == "LED":
        return next((c["label"] for c in w["components"] if c["type"] == "BUTTON_BISTABLE"), "")
    return joined([c["label"] for c in w["components"]])


def sensor(id, name, state, sensor_kind="generic", unit=None, grenton_unit=None, value_type=None, precision=None):
    return {"kind": "sensor", "id": id, "name": name, "state": value_ref(state), "sensorKind": sensor_kind,
            "unit": unit, "grentonUnit": grenton_unit, "valueType": value_type, "precision": precision}


def switch(wid, c):
    on, off = first(c["actions"], "ON"), first(c["actions"], "OFF")
    if not on or not off:
        return None  # §6.3.1: component skipped
    return {"kind": "switch", "id": f"{wid}_{c['rowId']}", "name": c["label"], "state": value_ref(c["state"]),
            "onAction": action_ref(on), "offAction": action_ref(off),
            "onIndication": c["onIndication"], "offIndication": c["offIndication"]}


def contact(id, label, part):
    return {"kind": "contactSensor", "id": id, "name": label, "state": value_ref(part["object"]["value"]),
            "reversed": part["reverseState"], "onIndication": part["onIndication"], "offIndication": part["offIndication"]}


def trigger(id, name, action):
    return {"kind": "trigger", "id": id, "name": name, "action": action_ref(action)}


def entities(w):
    t, wid = w["type"], w["id"]
    if t == "VALUE_V2":
        return [sensor(f"{wid}_0", w["label"], w["object"]["value"], grenton_unit=w["unit"],
                       value_type=w["valueType"], precision=w["precision"])]
    if t == "VALUE_DOUBLE":
        return [sensor(f"{wid}_{n}", c["label"], c["object"]["value"], grenton_unit=c["unit"],
                       value_type=c["valueType"], precision=c["precision"])
                for n, c in enumerate([w["componentLeft"], w["componentRight"]])]
    if t in ("ON_OFF", "ON_OFF_DOUBLE"):
        return [e for e in (switch(wid, c) for c in w["components"]) if e]
    if t == "DIMMER_V2":
        o = w["object"]
        return [{"kind": "dimmableLight", "id": f"{wid}_0", "name": w["label"], "state": value_ref(o["value"]),
                 "min": w["min"], "max": w["max"], "precision": w["precision"],
                 "onAction": action_ref(o["onAction"]), "offAction": action_ref(o["offAction"]),
                 "setValueAction": action_ref(o["setValueAction"])}]
    if t == "LED":
        by_type = {}
        for c in w["components"]:
            by_type.setdefault(c["type"], c)  # first of each type
        parts = [by_type.get(k) for k in ("BUTTON_BISTABLE", "SLIDER_HUE", "SLIDER_SATURATION", "SLIDER_BRIGHTNESS")]
        if None in parts:
            return []
        button, *sliders = parts
        on, off = first(button["actions"], "ON"), first(button["actions"], "OFF")
        if not on or not off or any(not s["actions"] for s in sliders):
            return []

        def channel(s):
            return {"state": value_ref(s["state"]), "setAction": action_ref(s["actions"][0]),
                    "range": {"min": s["range"]["min"], "max": s["range"]["max"]}}
        hue, sat, bri = sliders
        return [{"kind": "hsbLight", "id": f"{wid}_0", "name": button["label"], "powerState": value_ref(button["state"]),
                 "onAction": action_ref(on), "offAction": action_ref(off),
                 "hue": channel(hue), "saturation": channel(sat), "brightness": channel(bri)}]
    if t == "CONTACT_SENSOR":
        out = [contact(f"{wid}_binary_sensor", w["label"], w)]
        if w["object"].get("clickAction"):
            out.append(trigger(f"{wid}_button", w["label"], w["object"]["clickAction"]))
        return out
    if t == "CONTACT_SENSOR_DOUBLE":
        out = []
        for side in ("left", "right"):
            c = w["component" + side.capitalize()]
            out.append(contact(f"{wid}_binary_sensor_{side}", c["label"], c))
            if c["object"].get("clickAction"):
                out.append(trigger(f"{wid}_button_{side}", c["label"], c["object"]["clickAction"]))
        return out
    if t == "SLIDER":
        o = w["object"]
        out = [{"kind": "numberSetting", "id": f"{wid}_slider", "name": w["label"], "state": value_ref(o["value"]),
                "setValueAction": action_ref(o["setValueAction"]), "min": w["min"], "max": w["max"],
                "precision": w["precision"]}]
        if o.get("clickAction"):
            out.append(trigger(f"{wid}_button", w["label"], o["clickAction"]))
        return out
    if t == "MULTISENSOR":
        return [sensor(f"{wid}_{suffix}", w["label"], w[field]["value"], sensor_kind=kind, unit=unit)
                for suffix, field, kind, unit in MULTISENSOR_CHANNELS]
    if t == "ROLLER_SHUTTER":
        comps = w["components"]
        if not comps or not comps[0]["actions"]:
            return []  # §6.3.9 completeness
        c = comps[0]
        return [{"kind": "stateSensor", "id": f"{wid}_sensor", "name": c["label"], "state": value_ref(c["state"])},
                trigger(f"{wid}_button", c["label"], c["actions"][0])]
    if t == "ROLLER_SHUTTER_V3":
        o = w["object"]
        tilt = None
        if "lamelMoveTimeout" in o and o["lamelMoveTimeout"] is not None:
            tilt = {"position": value_ref(o["lamelPosition"]), "setAction": action_ref(o["setLamelPositionAction"])}
        stop = action_ref(o["stopAction"]) if o.get("stopAction") else None
        return [{"kind": "cover", "id": wid, "name": w["label"], "motionState": value_ref(o["state"]),
                 "position": value_ref(o["position"]), "setPositionAction": action_ref(o["setPositionAction"]),
                 "tilt": tilt, "stopAction": stop}]
    if t == "CAMERA":
        if not w["components"]:
            return []  # §6.3.11 completeness
        c = w["components"][0]
        return [{"kind": "camera", "id": f"{wid}_0", "name": c["label"], "streamUrl": c["value"]}]
    if t in ("SCENE", "SCENE_DOUBLE"):
        out = []
        for position, c in enumerate(w["components"]):
            script = next((a for a in c["actions"] if a["event"] == "CLICK" and a["callType"] == "SCRIPT"), None)
            if script:
                # SCENE: suffix rowId (reference contract); SCENE_DOUBLE: list position (rowIds repeat).
                suffix = c["rowId"] if t == "SCENE" else position
                out.append({"kind": "sceneTrigger", "id": f"{wid}_{suffix}", "name": c["label"],
                            "action": action_ref(script)})
        return out
    if t == "THERMOSTAT_V2":
        o = w["object"]

        def optional_ref(key):
            return value_ref(o[key]) if o.get(key) else None

        def control(state_key, action_key):
            if not o.get(state_key) or not o.get(action_key):
                return None
            return {"state": value_ref(o[state_key]), "setAction": action_ref(o[action_key])}
        return [{"kind": "thermostat", "id": f"{wid}_0", "name": w["label"],
                 "currentTemperature": value_ref(o["currentTemperature"]),
                 "targetTemperature": value_ref(o["targetTemperature"]),
                 "setTargetTemperatureAction": action_ref(o["setTargetTemperatureAction"]),
                 "minTemperature": optional_ref("minTemperature"), "maxTemperature": optional_ref("maxTemperature"),
                 "mode": control("mode", "setModeAction"), "power": control("state", "setStateAction"),
                 "output": optional_ref("controlOutValue")}]
    raise AssertionError(t)


# §6.6: which field of each entity names the CLU object it operates.
PRIMARY_REF = {
    "switch": "state", "dimmableLight": "state", "hsbLight": "powerState", "contactSensor": "state",
    "sensor": "state", "numberSetting": "state", "cover": "position", "stateSensor": "state",
    "thermostat": "currentTemperature",
}
PRIMARY_ACTION = {"trigger": "action", "sceneTrigger": "action"}


def object_key(entity):
    """(cluId, kind, name, index) of what the entity operates, or None (camera)."""
    if entity["kind"] in PRIMARY_REF:
        ref = entity[PRIMARY_REF[entity["kind"]]]
        if ref["callType"] == "VARIABLE":
            return (ref["cluId"], "variable", ref["index"], None)
        return (ref["cluId"], "object", ref["objectName"], ref["index"])
    if entity["kind"] in PRIMARY_ACTION:
        a = entity[PRIMARY_ACTION[entity["kind"]]]
        if a["callType"] == "VARIABLE":
            return (a["cluId"], "variable", a["index"], None)
        if a["callType"] == "SCRIPT":
            return (a["cluId"], "script", a["objectName"], None)
        return (a["cluId"], "object", a["objectName"], a["index"])
    return None


def physical_objects(devices):
    order, members = [], {}
    for d in devices:
        per_key = {}
        for e in d["entities"]:
            k = object_key(e)
            if k is not None:
                per_key.setdefault(k, []).append(e["id"])  # dict keeps first-use order
        for k, ids in per_key.items():
            if k not in members:
                order.append(k)
                members[k] = []
            members[k].append({"deviceId": d["id"], "widgetType": d["widgetType"], "name": d["name"],
                               "page": d["page"], "entityIds": ids})
    return [{"key": {"cluId": k[0], "kind": k[1], "name": k[2], "index": k[3]}, "widgets": members[k]} for k in order]


def map_interface(data):
    devices, skipped = [], []
    for page_index, page in enumerate(data["pages"]):
        page_skipped = []
        for w in page["widgets"]:
            t = w.get("type") if isinstance(w, dict) else None
            if w.get("x-conformance-expect-skip") if isinstance(w, dict) else False:
                page_skipped.append(t)   # §3.6: a widget that does not parse, HEADER included
                continue
            if t in PRESENTATIONAL:
                continue
            if t not in SUPPORTED:
                page_skipped.append(t)
                continue
            devices.append({"id": w["id"], "widgetType": t, "kind": DEVICE_KIND[t], "name": device_name(w),
                            "page": {"index": page_index, "name": page["name"]}, "entities": entities(w)})
        skipped.append(page_skipped)
    return {"devices": devices, "physicalObjects": physical_objects(devices), "skippedWidgets": skipped}
