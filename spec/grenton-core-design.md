# Grenton Core — technical design of a portable library

| | |
|---|---|
| **Status** | Design (for implementation) |
| **Version** | 0.11.3 |
| **Date** | 2026-10-25 |
| **Reference implementation** | `homeassistant-grenton` (Python, Home Assistant integration) |
| **Purpose** | Language-agnostic specification — the basis for ports (first: Swift) |
| **Conformance suite** | `conformance/` + the contract in [`conformance.md`](conformance.md) |

This document specifies **Grenton Core**: a portable library implementing the "mobile interface" protocol of the Grenton system — from fetching the configuration from the Object Manager, through encrypted UDP communication with the CLU controllers, to a canonical device model. The document is technology-agnostic: it describes types, algorithms, wire formats and contracts, not the syntax of a particular language. All protocol facts come from the reference implementation (reverse engineering of the myGrenton app's behavior); wherever Core deliberately departs from the reference implementation, the deviation is marked with the symbol **Δ**.

---

## 1. Purpose and scope

### 1.1 What the library does

1. **Provisioning** — fetches and parses the mobile interface definition from the Grenton Object Manager (HTTP + ZIP + JSON) or reads it from an `.omp` project file (§4.5).
2. **Runtime** — maintains encrypted UDP sessions with the CLU controllers: keep-alive, subscriptions to state changes, execution of actions.
3. **Canonical device model** — maps interface widgets onto devices with normalized state and intents (turn on, set brightness, set position…), so that platform adapters are thin.

### 1.2 What the library does not do

- It contains no entities/components of any platform (Home Assistant, HomeKit, SwiftUI…).
- It contains no UI and no configuration flows.
- It persists nothing — the configuration comes in as a value; user settings (device class, units) are opaque adapter metadata (§6.4).
- It does not support Grenton's cloud mode (the `connectionType` field is parsed, but Runtime always connects locally over UDP).
- It does not support push notifications (`pushNotifications` in the JSON — parsed, unused).

### 1.3 System context

```
┌──────────────────┐  HTTP (ZIP with JSON) ┌─────────────────────┐
│ Object Manager   │◄──────────────────────│ Provisioning        │
│ (PC, LAN, :9998) │    once, at setup     │                     │
└──────────────────┘                       │   G r e n t o n     │
┌──────────────────┐   UDP + AES-CBC       │      C o r e        │
│ CLU #1..N        │◄─────────────────────►│                     │
│ (LAN, cfg port)  │  continuously, runtime│ Runtime + Devices   │
└──────────────────┘                       └─────────┬───────────┘
                                                     │ canonical devices,
                                                     │ state events, intents
                                           ┌─────────▼───────────┐
                                           │ Platform adapter    │
                                           │ (HA / Swift / …)    │
                                           └─────────────────────┘
```

The Object Manager is needed **only during the provisioning phase** and must then be running in the same network. The entire runtime talks directly to the CLUs.

---

## 2. Module architecture

One library, four modules with enforced dependency boundaries:

```
Provisioning ──► Model ◄── Runtime ◄── Devices
   (optional)     (pure types, zero I/O)
```

| Module | Responsibility | Dependencies | I/O |
|---|---|---|---|
| **Model** | Interface DTO types, domain types (CLU, ValueRef, ActionRef), `GrentonValue`, parsing/casting | none | none |
| **Provisioning** | Object Manager client: HTTP GET → ZIP → JSON → `MobileInterface`; import of an `.omp` project file → the same `MobileInterface` | Model | HTTP |
| **Runtime** | Cipher, frame format, message catalog, CLU sessions (sockets, chunking, loops), state store | Model | UDP |
| **Devices** | Widget→device mapping, state normalization, intents, events | Model, Runtime | none (through Runtime) |

Rules:

- **Runtime takes a parsed `MobileInterface` as input** and does not know where it came from. Provisioning is one of the possible sources (others: a file, the application's backend, a QR code); it itself has two: fetching from the OM (§4.1–§4.3) and the project file (§4.5).
- All side effects (network, time, randomness, logs) enter through the **port contract** (§7) — interfaces implemented per platform. This makes the logic deterministically testable.
- The API names in this document (e.g. `CluSession`, `StateStore`, `GrentonValue`) are **reference names for the purposes of the specification and the conformance tests** — what is binding is the semantics and behavior, not the wording of the names; a port may use names idiomatic to its language, documenting the mapping.
- A port **may** add helper modules — including an umbrella module — that depend on the required subset of the four Core modules. They compose and re-export the API and add conveniences that cross module boundaries (e.g. "fetch the interface and start the system" — Provisioning plus Devices) or serve the application (e.g. a system simulation depending only on Devices, for application tests). The rules of §3–§6 live in the modules from the table, never in a helper module; none of the four Core modules depends on a helper module, so an application can take Devices alone or Provisioning alone. Platform adapters and UI remain outside Core (§1.2); this point does not limit the number of modules in the port.

---

## 3. The Model module

### 3.1 `GrentonValue` — the value type

Values in the Grenton system are dynamically typed. The canonical sum type:

```
GrentonValue = string | bool | int | double | null
```

**Algorithm for casting a wire string** (`castString(s) -> GrentonValue`), applied to values from `clientReport` — the order is significant:

1. Trim whitespace on both sides; then strip **all** leading and trailing double quotes `"` (like Python's `strip('"')`: `""ON""` → `ON`).
2. If the result, case-insensitively, is `nil` → `null`.
3. If `true` → `true`; if `false` → `false` (case-insensitive).
4. If it matches the grammar `^-?[0-9]+$` **and fits in Int64** → `int` (64-bit signed); on Int64 overflow → try as `double`.
5. If it matches the grammar `^-?([0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)([eE][+-]?[0-9]+)?$` **with a dot or exponent required** (the pure integer form was caught in point 4) and yields a finite value → `double`. Forbidden: `inf`, `nan`, hexadecimal form, separators (`_`).
6. Otherwise → `string` (already without the quotes).

**Δ** The reference implementation uses Python's `int()`/`float()` (arbitrary-precision int, acceptance of `1_0`, `inf`). Core specifies the strict ASCII grammar of points 4–5 and the Int64 width; ports must not inherit the leniency of their language's parser.

### 3.2 State references — `ValueRef`

A reference says *where* to read a value from on a given CLU. JSON:

```json
{ "cluId": "CLU_501234567", "objectName": "DOU8272", "index": "0", "callType": "ATTRIBUTE" }
```

| `callType` | Meaning | Subscription key on the wire (§5.3.2) |
|---|---|---|
| `VARIABLE` | a CLU global variable; `index` is its name | `"{index}"` (in double quotes) |
| `ATTRIBUTE` | an object feature; `objectName` + feature number `index` | `{{objectName},{index}}` |

Canonical types: `ValueRef.variable(cluId, name)` and `ValueRef.attribute(cluId, objectName, index)`. The `objectName` field is present in the JSON also for `VARIABLE` (logically ignored, but parsed).

### 3.3 Actions — `ActionRef`

An action says *what* to send to the CLU. JSON:

```json
{ "cluId": "...", "objectName": "DOU8272", "index": "0", "value": "1",
  "event": "ON", "callType": "ATTRIBUTE" }
```

- `event ∈ {CLICK, ON, OFF}` — the role of the action in the widget (after which UI event to invoke it).
- `callType ∈ {VARIABLE, ATTRIBUTE, METHOD, SCRIPT}` — the execution variant; `SCRIPT` **has no** `index` field.
- `value` — the argument; for sliders it is overridden on the fly with the value chosen by the user, for scenes it may be overridden by a parameter (§6.3.10).
- **The `$value$` placeholder:** the Object Manager exports setter actions (e.g. a dimmer's `setValueAction`, a roller shutter's `setPositionAction`, all of a thermostat's `set*Action`) with `value` equal to exactly `$value$`. An intent that overrides the value **replaces the whole `value`** — for `$value$` the result is the same as substitution. An action whose `value` is exactly `$value$` cannot be sent without an override: building its payload (§5.3.4) fails with the error `PayloadError.unresolvedPlaceholder` and nothing reaches the CLU. This also applies to low-level execution of a raw action; in the catalog of §6.3 every such action is used exclusively by an intent carrying a value.

Wire payloads of actions — see §5.3.4.

### 3.4 Mobile interface schema (`data.json`)

Root:

| Field | Type | Notes |
|---|---|---|
| `id` | string | unique interface identifier (identity of the configuration) |
| `version` | int | interface version |
| `name`, `icon` | string | presentational metadata |
| `theme` | enum | `GRENTON`, `RED`, … (15 values; presentational) |
| `encryption` | object | `{ "key": <base64>, "iv": <base64> }` — shared by all CLUs of the interface |
| `clus` | array | see below |
| `pages` | array | UI pages with widgets |
| `pushNotifications` | array | `{cluId:int, objectId, objectType}` — parsed, unused |

CLU:

| Field | Type |
|---|---|
| `id` | string (e.g. `CLU_501234567`) |
| `serialNumber` | string |
| `name` | string |
| `ip` | string |
| `port` | int (UDP port of the CLU protocol) |
| `connectionType` | enum: `LOCAL_ONLY`, `CLOUD_CAPABLE`, `CLOUD` |

Page (`page`): `name`, `icon`, `isFullscreenWidget: bool`, `widgets: []`.

### 3.5 Widget catalog

Every widget has an `id: string` and a `type` discriminator. 16 supported types (plus `HEADER`, below):

| `type` | Key fields |
|---|---|
| `VALUE_V2` | `label`, `icon`, `unit`, `valueType` (`STRING\|FLOAT\|INTEGER`), `precision:int`, `object.value: ValueRef` |
| `VALUE_DOUBLE` | `componentLeft` and `componentRight`, each: `label, icon, unit, valueType, precision, object.value` |
| `ON_OFF` | `components[]` of type `BUTTON_BISTABLE`: `label, rowId:int, unit, state: ValueRef, actions: ActionRef[], onIndication, offIndication` |
| `ON_OFF_DOUBLE` | `components[]` of type `BUTTON_BISTABLE_V2` (fields as above) |
| `DIMMER_V2` | `label, icon, min:double, max:double, precision:int`, `object: {value, setValueAction, onAction, offAction}` |
| `LED` | `components[]` — a union over `type`: `BUTTON_BISTABLE` (`image, onIndication, offIndication`), `SLIDER_HUE`, `SLIDER_SATURATION`, `SLIDER_BRIGHTNESS` (each slider: `range:{min:int,max:int}`); all have `state` and `actions[]` |
| `CONTACT_SENSOR` | `label, icon, onIndication, offIndication, reverseState:bool`, `object: {value, clickAction?}` |
| `CONTACT_SENSOR_DOUBLE` | `componentLeft/Right`: `label, icon, onIndication, offIndication, reverseState, object` |
| `SLIDER` | `label, icon, unit, min:double, max:double, precision:int`, `object: {value, setValueAction, clickAction?}` |
| `MULTISENSOR` | `label, icon, labelVisible:bool` + 7 channel objects: `objectAirCo2, objectSound, objectAirVoc, objectLight, objectPressure, objectHumidity, objectTemperature`, each `{value, minValue?, maxValue?, threshold?, sensitivity?}` |
| `ROLLER_SHUTTER` (legacy) | `components[]` of type `BUTTON_BISTABLE`: `image, state, indication` (`ON_OFF\|OPEN_CLOSED\|OPEN_LOCKED\|UP_DOWN`), `actions[]` |
| `ROLLER_SHUTTER_V3` | `icon, label`, `object` with ~30 fields; required: `state, position, lamelPosition, setPositionAction, setLamelPositionAction, moveUpAction, moveDownAction, startAction, setRollerBlockedAction`; optional, among others: `stopAction?, lamelMoveTimeout?, up?, down?, holdAction?, calibrationAction?` |
| `CAMERA` | `components[]` of type `CAMERA_STREAM`: `label, rowId, unit, image, value` (**`value` = stream URL**, e.g. RTSP) |
| `SCENE` | `components[]` of type `BUTTON_MONOSTABLE`: `label, rowId, unit, actions: ActionRef[]` |
| `SCENE_DOUBLE` | like `SCENE` (usually 2 components, **both with `rowId` 0**); optional `background` (presentational) |
| `THERMOSTAT_V2` | `label`, `icon?`, `no_of_fan_speeds?`, `object`: required `currentTemperature`, `targetTemperature`, `setTargetTemperatureAction`; optional `minTemperature`, `maxTemperature`, `mode` + `setModeAction`, `state` + `setStateAction`, `controlOutValue`, `setMin/MaxTemperatureAction`, `scheduleData` + `setScheduleDataAction` |

**Presentational widgets:** `HEADER` (a section header: `components[]` with only a `label`) is recognized, **does not create a device and is not reported as skipped** (§3.6) — it is UI layout, not an error.

**Known, unsupported in v1:** `EVENT_SCHEDULER` (event scheduler: a list of rules, add/remove) and `TV_REMOTE_CONTROL` (TV remote: `vodLabel`, `icon`, `label`, `object` with button actions) — skipped like an unknown type.

Common base of list components: `label: string`, `rowId: int`, `unit: enum {UNKNOWN, PERCENT, DEGREE}`.

### 3.6 Parsing rules

1. **Discriminated unions.** Widgets are discriminated by `type`, LED components by `type`, `ValueRef` by `callType`, `ActionRef` by `callType`. An unknown discriminator value = a parsing error of the given element.
2. **Tolerance at the widget level.** A widget that does not parse (unknown `type`, missing field, unknown enum value) is **skipped with a debug-level log** — the page and the whole interface continue to parse. The skip is **observable**: for every page the implementation exposes the list of `type` values of the skipped widgets in order of occurrence (`null` when the widget has no `type`). The tolerance applies to widgets only — an error at the root level (e.g. missing `encryption`) rejects the whole interface. This is a forward-compatibility guarantee: new widget types in the myGrenton app must not crash the library.
3. **Strictness below the widget.** Inside a widget, a missing required field = rejection of the whole widget (not a "partial" device).
4. Unknown (extra) fields are ignored at every level.

---
## 4. Provisioning module

### 4.1 Endpoint

```
GET http://{host}:{port}/api/v1/interface/hash/{pin}
```

- `host`, `port` — the address of the computer running Object Manager (default port **9998**).
- `pin` — the PIN of the myGrenton interface (the same one used to pair the mobile app). OM displays it as "Token" in the "Send to mobile devices" window, grouped with a space (`149 853`). Before building the URL the implementation **removes all whitespace** from the PIN — every character with the Unicode `White_Space` property (space, tab, line breaks, NBSP, U+3000…), in any position (`149 853` → `149853`). Characters outside `White_Space` (e.g. U+200B) are kept.
- The PIN is **a single path segment**: characters outside `unreserved` / `sub-delims` / `:` / `@` (RFC 3986) are percent-encoded as UTF-8; in particular `/` → `%2F`, `?` → `%3F`, `#` → `%23`, `%` → `%25`.
- The URL cannot be built (PIN empty after whitespace removal, port outside 1–65535) → `ProvisioningError.connection`.
- An IPv6 host is given as a bare literal (`fe80::1`) or in brackets (`[fe80::1]`); in the URL it is always in brackets.
- Plain HTTP without TLS (this is how OM works; the channel is local).

### 4.2 Responses

| Status | Meaning | Canonical error |
|---|---|---|
| `200` | body = ZIP file | — |
| `401` | wrong PIN | `ProvisioningError.authenticationFailed` |
| other | — | `ProvisioningError.connection(status)` |
| network error | — | `ProvisioningError.connection(cause)` |

### 4.3 ZIP processing

1. Open the response body as a ZIP archive; format error → `ProvisioningError.invalidData("bad zip")`.
2. Required entry: **`mygrenton/data.json`**; missing → `invalidData`.
3. Read as UTF-8, parse the JSON → `MobileInterface` (Model, §3); JSON/schema error → `invalidData`.
4. **Resource limits:** the size of the response body and the size of `data.json` after decompression are bounded (recommended: 10 MB each) — exceeding them → `invalidData` (protection against a zip bomb/garbage response); the entry path is matched exactly, without interpreting `..`.

### 4.4 Identity and reconfiguration

`interface.id` identifies the configuration. The library consumer should:
- on the first download, store `id` as the identity of the installation,
- on a repeated download (reconfiguration), reject an interface with a different `id` (protection against attaching someone else's installation),
- compare `version` to detect changes.

Core only supplies the data; the storage policy belongs to the adapter.

### 4.5 Import from the project file (`.omp`)

A second source of the same `MobileInterface`: the Object Manager project file. The project stores the mobile interfaces in the OM editor format; the import translates them into the schema of §3.4, just as OM does when sending to a phone, so nothing further on (§3, §6) depends on the source. It requires neither a PIN nor a running OM.

All import errors are `ProvisioningError` (§8). Order of checking when there are several errors: archive (§4.5.1) → `properties.xml` and `system.xml` as XML → interface list and selection (§4.5.2) → key (§4.5.3) → CLU (§4.5.4).

#### 4.5.1 Archive

1. The `.omp` file is a ZIP archive; a format error, a read or decompression error of any used entry, two entries with the same name → `invalidData`.
2. Used entries (name matched in full): **`properties.xml`**, **`system.xml`** and every entry named exactly `nodes/{n}/data.json`, where `{n}` is a non-empty string of ASCII digits. Missing `properties.xml` or `system.xml` → `invalidData`. The remaining entries (scripts, diagnostics) are ignored — they are not read.
3. **Resource limits:** the file size, the size of each used entry after decompression and the total size of the used entries after decompression — each at most 64 MiB (recommended); the number of archive entries at most 10,000. Exceeding them → `invalidData`. The limit is higher than in §4.3 because the `system.xml` of a large installation is several megabytes.
4. **XML without DTD.** A document with a document type declaration (`<!DOCTYPE …>`) in any encoding → `invalidData`; the encoding is determined by the XML parser (the `encoding` declaration, BOM), not by searching bytes. Without a DTD there are no entities, so there is no XXE and no "billion laughs". XML syntax error → `invalidData`.
5. **Strict JSON.** The entry `nodes/{n}/data.json` is read as UTF-8; invalid UTF-8, invalid JSON, values outside JSON (`NaN`, `Infinity`), an integer outside the signed 64-bit range, a number that is not finite as a double (`1e400`), or a repeated key in an object → `invalidData`, even when the entry turns out not to be an interface. **Nesting** of arrays and objects deeper than the port's limit → `invalidData`; the limit is at least 32 and at most 1024 levels (real nodes have about ten), and the parser must not depend on a stack growing with the input depth beyond this limit: an entry with ten thousand levels ends in `invalidData`, not in a process crash.
6. **XML references** (OM serialization). An element with the attribute `reference="{r}"` denotes the element with the attribute `id="{r}"`. Every element read in §4.5.4–§4.5.5 — `TreeObject`, `specificObject`, `children` and text fields — that has `reference` is replaced by its target; a target with its own `reference` is resolved further. A missing target, a reference cycle or two elements with the same `id` → `invalidData`.
7. **The text of an XML field** is the element's content with leading and trailing whitespace trimmed; an empty element or one containing only whitespace is treated as a missing field. **A hexadecimal number** is 1–8 digits `0-9a-fA-F`, without sign, prefix or separators.

#### 4.5.2 Interface selection

- Every used entry `nodes/{n}/data.json` whose value is a JSON object with a `pages` field that is an array is an interface. The import exposes a list `{id, name}` (from the `id` and `label` fields) of all interfaces, in the order of their entries in the archive. An interface whose `id` or `label` is not a string, and two interfaces with the same `id` → `invalidData`.
- Selection by `id`: no interface with that `id` → `ProvisioningError.interfaceNotFound`.
- Without a given `id`: an archive with exactly one interface → that interface; with several → `ProvisioningError.interfaceAmbiguous` (a project may have, e.g., a separate interface for integrations; guessing would be dangerous); with none → `interfaceNotFound`.

#### 4.5.3 Interface root

| Field §3.4 | Source |
|---|---|
| `id` | the interface's `id` |
| `name` | `label` |
| `icon` | `icon` |
| `theme` | `theme` converted to upper case (the editor writes `blue`, the schema has `BLUE`) |
| `version` | **`0`** — the project does not store the OM export counter; change detection (§4.4) for this source compares content |
| `encryption` | `{key, iv}` — the text (§4.5.1 item 7, base64 without further changes) of the first `keyBytes` element and the first `ivBytes` element in document order in `properties.xml`; either missing → `invalidData` |
| `clus` | §4.5.4 |
| `pages` | §4.5.6 |
| `pushNotifications` | §4.5.5 |

`icon` and `theme` must be strings; otherwise → `invalidData`.

#### 4.5.4 CLU

The project's CLUs are the elements of `system.xml` that have **direct children `nameOnCLU` and `ipAddress`** (the element itself is not a resolved reference here), in document order; a repeated occurrence of the same `nameOnCLU` text is skipped. `clus` receives **only the CLUs referenced by the selected interface** — the `cluId` of any state reference or action of its pages **after the transformation of §4.5.6** (widget skeletons have no references) — in document order, not in order of first use.

| Field §3.4 | Source |
|---|---|
| `id` | the text of `nameOnCLU` |
| `name` | the text of the child `name` |
| `ip` | the text of `ipAddress` |
| `serialNumber` | the text of the child `serialNumber` as a hexadecimal number (§4.5.1 item 7), written in decimal (`0bebc201` → `"200000001"`) |
| `port` | `1234` — the project does not store the port; CLUs always listen on it |
| `connectionType` | `LOCAL_ONLY` when the interface has `blockCloud: true`, otherwise `CLOUD` (the field is parsed, not used — §1.2) |

A missing `name`, `ipAddress` or valid `serialNumber` on a CLU referenced by the interface → `invalidData`. A `cluId` that matches no CLU of the project is not an import error (device mapping does not need it).

#### 4.5.5 Push notifications

A depth-first traversal of the `TreeObject` tree: it starts from the direct `TreeObject` children of the root of `system.xml`, and the children of a node are the `TreeObject` elements that are direct children of its first `children` element. Every node is read after reference resolution (§4.5.1 item 6), and its object is the first `specificObject` (also after resolution). Order: depth-first, node before children, children in document order. **Every node is visited at most once** — on the first arrival; subsequent arrivals (through a reference or a cycle) are skipped, so the size of the result will not exceed the number of nodes, and the implementation should not rely on recursion as deep as the tree.

A node whose object has `typeName` = `Push` yields an entry `{cluId, objectId, objectType}`:

- `cluId` — the `serialNumber` (§4.5.1 item 7) of the object of the nearest **ancestor** (excluding the node itself) whose object has `serialNumber` and `ipAddress`;
- `objectId` — the text of `nameOnCLU`;
- `objectType` — `PUSH-{typeId}-{typeVersion}`, where `typeId` is a hexadecimal number written in decimal (`31` → `49`), and `typeVersion` is text.

An entry that lacks a CLU ancestor with a valid `serialNumber`, lacks `nameOnCLU`, a valid `typeId` or `typeVersion` is skipped. The field is parsed, not used (§1.2); therefore incomplete data skips the entry instead of rejecting the import.

#### 4.5.6 Pages and widgets

The interface's `pages` is an array of pages; a page that is not an object with strings `label` and `icon` and an array `widgets` → `invalidData`. Page: `{name: label, icon, isFullscreenWidget: false, widgets}`; the order of pages and widgets is preserved. The widget `id` of §3.4 is the **`dtoId`** field of the editor widget. OM assigns `dtoId` only when the interface is sent to a phone, so a widget that has never been sent does not have one. When `dtoId` is not a non-empty string (missing, `null`, another type), `id` is **`omp-` followed by the decimal text of the editor's `id`**, provided that it is a non-negative JSON integer (no fractional part or exponent; `true`/`false` are not numbers) — a stand-in id, e.g. `omp-5009`. The prefix ensures it will never equal any `dtoId` assigned by OM. Two widgets of the interface with the same `id` after the transformation → `invalidData`. After the interface is first sent from OM and the project is saved, such a widget receives a `dtoId`, so its `id` — and with it the device and entity identifiers (§6) — will change; the adapter should account for this, e.g. by warning the user.

**A required field** is a field that is present and different from `null`. **An object** is a JSON object; a value of another kind in the place of an object is treated as a missing field. **Fields copied without changes** pass through only when they are present (including with the value `null`) — the import does not add fields that were not there and does not check their types; the parsing of §3.6 does that.

**A state reference** (`ValueRef`) exists when it is an object whose `objectName` is a non-empty string; it passes through as `{cluId, objectName, index, callType}` with those of the four fields that it has. A non-existent reference (the editor keeps empty templates of unused features) is **skipped** — it passes neither under its name nor as `null`.

**An action** (`ActionRef`) exists on the same principle and passes through as:

| Field §3.3 | Source |
|---|---|
| `cluId`, `objectName`, `value` | without changes (if present) |
| `index` | without changes; omitted when missing or `null` (scripts) |
| `event` | `type` (if present) |
| `callType` | `PSEUDO_METHOD` → `ATTRIBUTE`; any other value without changes (if present) |

`valueFormattedForDto` is omitted. A non-existent action is skipped.

Widgets by `type` (fields in **bold** are required for the transformation):

| `type` | Source in the editor → fields of §3.5 |
|---|---|
| `HEADER` | **`text`** (object) → `components: [{unit: "UNKNOWN", label, type, rowId}]` (from `text`, without changes) |
| `ON_OFF` | **`buttonBistable`** → `components: [P]` |
| `ON_OFF_DOUBLE` | **`buttonBistableFirst`**, **`buttonBistableSecond`** → `components: [P₁, P₂]` |
| `SCENE` | **`buttonMonostable`** → `components: [M]`; `background` → B |
| `SCENE_DOUBLE` | **`buttonMonostableFirst`**, **`buttonMonostableSecond`** → `components: [M₁, M₂]`; `background` → B |
| `VALUE_V2` | `unit, valueType, precision, icon, label` without changes; reference `value` → `object: {value}` |
| `VALUE_DOUBLE` | **`componentLeft`**, **`componentRight`** (objects) → each like `VALUE_V2` (without `id`, `type`) |
| `DIMMER_V2` | `min, max, precision, icon, label` without changes; **`object`** → O |
| `THERMOSTAT_V2` | `icon, label` without changes; `noOfFanSpeeds` → `no_of_fan_speeds`; **`object`** → O |
| `ROLLER_SHUTTER_V3`, `EVENT_SCHEDULER` | `icon, label` without changes; **`object`** → O |
| `CAMERA` | **`cameraStream`** → `components: [{unit: "UNKNOWN", value: url}]` plus, without changes, `image, label, type, rowId` (the editor field `url` passes through as `value`) |
| `LED` | **`buttonBistable`**, **`sliderHue`**, **`sliderSaturation`**, **`sliderBrightness`** → `components: [P, S_hue, S_saturation, S_brightness]` in this order |
| `CONTACT_SENSOR` | `icon, label, onIndication, offIndication, reverseState` without changes; **`object`** → O |
| `CONTACT_SENSOR_DOUBLE` | **`componentLeft`**, **`componentRight`** (objects) → each like `CONTACT_SENSOR` (without `id`, `type`) |
| `SLIDER` | `unit, min, max, precision, icon, label` without changes; **`object`** → O |
| `MULTISENSOR` | `icon, label, labelVisible` without changes; channels → K |
| `ROLLER_SHUTTER` | **`buttonBistableClick`** → `components: [R]` |
| `TV_REMOTE_CONTROL` | `vodLabel, icon, label` without changes; **`object`** → O |

- **P** (bistable button, object): `{unit: "UNKNOWN", actions: [actionOn, actionOff]}` plus, without changes, `image, label, onIndication, offIndication, type, rowId` and the reference `state` — actions in this order, non-existent ones omitted; the editor field `indication` is omitted.
- **M** (monostable button, object): `{unit: "UNKNOWN", actions: [actionClick]}` plus, without changes, `label, type, rowId`.
- **S** (LED slider, object): `{range: {min, max}, actions: [actionClick]}` (from the editor's `min`, `max`, if present) plus, without changes, `unit, label, type, rowId` and the reference `state`.
- **R** (legacy roller shutter button, object): `{unit: "UNKNOWN", actions: [actionClick]}` plus, without changes, `image, label, indication, rowId` and the reference `state`; the `type` `BUTTON_BISTABLE_CLICK` passes through as `BUTTON_BISTABLE`, any other without changes. The `status` field of the editor widget is omitted.
- **K** (multisensor channels): the editor fields `objectAirCo2, objectSound, objectAirVco, objectLight, objectPressure, objectHumidity, objectTemperature`, each being an object, pass through as objects containing those of their fields that are existing references, under the mobile names `objectAirCo2, objectSound, objectAirVoc, objectLight, objectPressure, objectHumidity, objectTemperature` (the editor writes the VOC channel as `objectAirVco`).
- **B** (scene background, when `background` is an object): `{color, name: image}`, where a `color` that is a string is converted to upper case; without a `background` object the `background` field is omitted.
- **O** (widget object): every field of the editor's `object` that is an object with a `valueFormattedForDto` field is an action and passes through under the name with the suffix `Action`, unless the name already ends with it (`setPosition` → `setPositionAction`, `setModeAction` unchanged); every other field that is an object — as a state reference under the same name; non-existent ones are omitted. When an action without the suffix (`set`) targets the name of a field that is already in `object` (`setAction` — an action, a reference or a template), that field wins and the action without the suffix is omitted — regardless of the order in the JSON.

A widget of another `type`, a widget that is not an object, and a widget from the table without a field required for the transformation (a bold one or the identifier — `dtoId` or a numeric `id`) passes through **only with the fields `id` (as above) and `type`, if it has them** (`type` — when it is a string). Without the remaining fields it does not parse, so the parsing of §3.6 item 2 skips it with a diagnostic, like any invalid widget. The editor fields of an unknown type are not copied, because they could accidentally parse as another widget.

The result then goes through the ordinary parsing of §3.6, like an interface downloaded from OM.

---
## 5. Runtime module

### 5.1 Cryptography

- **Algorithm:** AES in **CBC** mode, **PKCS#7** padding (16 B block).
- **Key and IV:** from `interface.encryption` (base64). The key must be 16/24/32 B (AES-128/192/256; in practice 128), the IV exactly 16 B — otherwise `CryptoError.invalidKeyMaterial` when the session is created.
- **Every UDP datagram is, in its entirety, a single ciphertext**: `ciphertext = AES-CBC-PKCS7(key, iv, utf8(frame))`. Receive: decrypt, **verify the padding strictly** (length 1–16, all padding bytes equal to the length; otherwise reject the datagram), decode UTF-8, trim whitespace.
- **The IV is static** — the same for every message. This is a property of the Grenton protocol, not a choice of the library. Consequences: (a) encryption is deterministic, which enables cross-port golden test vectors (§9.1); (b) confidentiality is weak (identical plaintexts ⇒ identical ciphertexts); (c) **the scheme provides no integrity or authentication whatsoever** — CBC without a MAC does not protect against modification, and padding validation rejects only some of the corrupted ciphertexts. The library treats the cipher solely as obfuscation; this must not be "fixed" on the client side — the CLU expects exactly this scheme. The receive path does not distinguish externally between kinds of decryption error (padding vs. size) — all end in the same silent rejection.
- A datagram that cannot be decrypted/unpadded/decoded is **silently rejected with a warning log** (garbage on the network must not break the session).

### 5.2 Frame format

Frames are textual (UTF-8), fields separated by colons, split into **at most 4 parts** (the first three colons; the payload may contain further colons).

**Request** (client → CLU):

```
req::{msg_id}:{payload}
```

Note: between `req` and `msg_id` there is an **empty field** (hence the double colon).

**Response / notification** (CLU → client):

```
resp:{clu_ip}:{msg_id}:{payload}
```

Receive validation: must start with `resp:` and have exactly 4 parts; otherwise ignore with a debug log.

**`msg_id`:** 8 hex characters (4 random bytes), **normalized to lowercase** when sending and comparing (the CLU may reply in uppercase).

**Notification** = a `resp` frame with `msg_id == "00000000"`. The only known notification is `clientReport` (§5.3.3).

### 5.3 Message catalog

#### 5.3.1 Ping (keep-alive)

- Request: payload `checkAlive()`.
- The ping's `msg_id` = **`keepAliveId`** — a constant identifier of the CLU session, drawn once (4 hex bytes) when the session is created. Subsequent pings use the same id (this is what myGrenton does; at most one ping is in flight at a time, so the correlation remains unambiguous).
- Response: any `resp` frame with a matching `msg_id`; **the response payload is ignored** — the mere fact of a response means success.
- The ping detects whether the CLU is responding (§5.4.5). It does **not** extend the subscription: the CLU stops sending `clientReport` about a minute after the last registration, regardless of pings; only re-registration renews the subscription (§5.4.4).

#### 5.3.2 Subscription registration (`clientRegister`)

Request (fresh random `msg_id`):

```
SYSTEM:clientRegister(0,0,{session},{K1,K2,...,Kn})
```

The arguments are `(ip, port, session, keys)`:

- `ip`, `port` — the address to which the CLU is to send notifications. **`0,0` means the source address and port of the socket from which the registration arrived** — Core always sends `0,0`. Other forms (explicit address and port) exist, but Core does not use them: notifications are to come back on the session socket. The form `ip = 0` with a non-zero `port` is not portable: some CLUs then send notifications to the source port, and some do not send them at all — only `0,0` works everywhere. **Δ** The upstream `main` of the reference (PR #12) sends a random value in the `port` field, as a result of which the CLU sends no notifications; release V0.2.3 sends `0`.
- `session` — **the subscription number, a positive integer**; in Core the session assigns it per chunk (§5.4.2). The CLU distinguishes subscriptions by the triple `(ip, port, session)`: a repeated registration with the same triple **replaces** the key list; a different triple establishes a separate subscription, also on the same socket. The number comes back in the response and in every notification (§5.3.3).
- The key `Ki` for `ValueRef.variable(name)` → `"{name}"` (in quotes); for `ValueRef.attribute(obj, idx)` → `{{obj},{idx}}`, e.g. `{DOU8272,0}`.
- **Size limit: the request frame (UTF-8 text before encryption, §5.2) is at most 1471 B**, i.e. the datagram is at most 1472 B (§5.1: PKCS#7 pads to a multiple of 16) — one IP packet at an MTU of 1500. To a larger request some CLUs do not respond at all (they do not reassemble IP fragments). The limit concerns bytes, not the number of keys; splitting into chunks — §5.4.2 (the only exception: a single key larger than the limit).
- **The subscription expires** about a minute after registration unless it is renewed with the same triple (§5.4.4).

Response — payload in the *client report* format:

```
clientReport:{session}:{v1,v2,...,vn}
```

The values `vi` are in the order of the request's keys; they are the **initial values** of the subscribed states. Parsing the payload:

1. Prefix `clientReport:`; the second field = int — the subscription number. The response to a registration is correlated by `msg_id` (§5.4.1), so the number in the response **is not checked**.
2. The third part must start with `{` and end with `}` (the outer braces; absence of either → item 3). The content between them is split into elements **on commas outside quotes and outside nested braces**: `"` opens and closes text (no escaping), `{` and `}` outside text increase and decrease the depth, and elements are separated only by a comma outside text at depth 0. A nested `{…}` block is a single element and after `castString` remains a string with the braces (e.g. `{0,{1,2},3}` → `0`, `{1,2}`, `3`). Each element: trim + strip quotes + `castString` (§3.1). Text not closed at the end of the content, a depth other than 0 at the end, or a `}` at depth 0 (the depth never drops below zero) → payload rejected as in item 3.
3. **Semantics of parse errors:** a payload without the prefix / with an invalid number / with unclosed text or an unbalanced brace inside the content → payload rejected (for a registration response: **the chunk is considered failed**, §5.4.2; for a notification — ignored with a log). A correct prefix and number, but a third part without `{` at the start or without `}` at the end (including a truncated one, e.g. `{1,2`) or with empty content → **an empty value list**: for a registration response the chunk failed (value count ≠ key count), for a notification — the report is ignored with a log, with no state update.
4. **Known protocol limitation:** no escaping — a string value containing a quote will derail positional parsing (a comma inside text and nested braces are handled by item 2). Recorded as a limitation (§10).

**Deregistration** (`clientDestroy`), fresh random `msg_id`:

```
SYSTEM:clientDestroy(0,0,{session})
```

The same triple as at registration; the CLU stops sending notifications of that subscription. The request is sent **without waiting for a response** — the response, if it arrives, is discarded like any unexpected frame (§5.4.1 item 2). (The CLU responds with the subscription number, and with `nil` when no such subscription exists — e.g. it has already expired.) Core sends it when a subscription is no longer needed (§5.4.2 item 6, §5.4.5); without it the CLU would stop sending after expiry anyway.

**One-off read** (`fetchValues`), fresh random `msg_id`:

```
SYSTEM:fetchValues({K1,K2,...,Kn})
```

Keys as in `clientRegister`, **at least one** (to an empty list the CLU responds with a malformed payload, so Core never sends one), within the same frame limit (§5.4.2; in the split, the text of the `fetchValues` frame counts). Response:

```
values:{v1,v2,...,vn}
```

Prefix `values:`, **no subscription number**; the value list according to item 2 above, in key order; a key unknown to the CLU yields `nil`, not an error. A payload without the `values:` prefix or not satisfying item 2 → rejected. The read **does not establish a subscription**: the CLU sends nothing afterwards, nothing expires and nothing needs to be deregistered. Core uses it where a one-off, complete snapshot without a subscription is needed (§5.4.2, `fetch`).

#### 5.3.3 `clientReport` notification

After registration the CLU sends — unprompted, to the address and **source port of the socket from which the registration arrived** — frames:

```
resp:{clu_ip}:00000000:clientReport:{session}:{v1,...,vn}
```

on every change of any subscribed state (the report always contains the **complete set of values** of the subscription, in registration order). The `session` field is the number from the registration (§5.3.2) — by it the session matches the report to the chunk's key list (§5.4.3). A notification is a single datagram with no acknowledgement and no retry; a lost report is repaired only by re-registration (§5.4.4).

#### 5.3.4 Actions

Fresh random `msg_id`; payload depends on the `ActionRef` variant:

| Variant | Payload |
|---|---|
| `VARIABLE` | `setVar("{index}","{value}")` |
| `ATTRIBUTE` | `{objectName}:set({index},"{value}")` |
| `METHOD` | `{objectName}:execute({index},"{value}")` |
| `SCRIPT` | `{objectName}({value})` — `value` **verbatim** (Lua syntax: `"abc"`, `42`, empty → `name()`) |

Response: a `resp` frame with a matching `msg_id`; payload ignored (success = a response arrived). Timeout = action failure.

**Δ Quotes around the value.** The myGrenton app inserts attribute and method values verbatim, without quotes and with a space after the comma (`set(0, 1)`, `execute(3, 22.0)`), and quotes only text values (schedules, `setVar`). The CLU accepts both forms identically: `execute(3,"120.0")` and `execute(3, 120.0)` yield the same value, and the CLU itself truncates the fractional part where the feature is integer. Core stays with the reference's quoted form. Regardless of the form, the CLU does not report a wrong kind of call — `set` on a method index responds `nil` and changes nothing; `set`/`execute` is decided by the `callType` from the interface (§3.3).

**Interpolation without escaping:** the `index`/`objectName`/`value` fields enter the payload verbatim — a quote or a protocol separator in a value changes the meaning of the payload (de facto an injection into the CLU's Lua). Values generated by Core (numbers from device intents) are safe by construction; values passed in from the user (a scene parameter) are **deliberately verbatim** — the caller's responsibility, noted in the API.

#### 5.3.5 Example exchange

```
→ req::a1b2c3d4:checkAlive()
← resp:192.168.1.10:a1b2c3d4:...

→ req::0f3e9a11:SYSTEM:clientRegister(0,0,1,{"AnalogIn1",{DOU8272,0}})
← resp:192.168.1.10:0f3e9a11:clientReport:1:{21.5,"ON"}
→ req::4d81c0e7:SYSTEM:clientRegister(0,0,2,{{DOU8273,0}})
← resp:192.168.1.10:4d81c0e7:clientReport:2:{"OFF"}
   … later, unprompted, on the same socket:
← resp:192.168.1.10:00000000:clientReport:1:{22.0,"OFF"}
← resp:192.168.1.10:00000000:clientReport:2:{"ON"}

→ req::7c2d91aa:DOU8272:set(0,"1")
← resp:192.168.1.10:7c2d91aa:...

   … one-off read, without a subscription:
→ req::b3e1a9c0:SYSTEM:fetchValues({{DOU8272,0},{NOPE_0000,0}})
← resp:192.168.1.10:b3e1a9c0:values:{1,nil}

   … when the session stops, without waiting for a response:
→ req::9e02b7f3:SYSTEM:clientDestroy(0,0,1)
→ req::c57a1d20:SYSTEM:clientDestroy(0,0,2)
```

### 5.4 CLU session — `CluSession`

One instance per CLU. Session state:

- `clu` (id, ip, port), `cipher` (the interface's key+IV — shared by all CLUs),
- `keepAliveId` (constant, §5.3.1),
- **main socket** — the session's only socket: pings, actions, registrations and receipt of notifications,
- **chunk list** — `{session, keys}` in key order, each within the frame limit (§5.4.2), and the **subscription number counter** (§5.4.2),
- map of pending requests `msg_id → pending` (response correlation),
- callback/stream of reports to the layer above.

#### 5.4.1 Request–response correlation

1. Send: record `pending[msg_id]`, encrypt, `sendto(clu.ip, clu.port)`.
2. Datagram receipt: decrypt → validate the frame → `msg_id`:
   - present in `pending` → remove it, deliver the response to the waiter;
   - not in `pending` and the frame is a `clientReport` (msg_id `00000000` + payload prefix) → report path (§5.4.3);
   - otherwise → debug log, discard.
3. **Response timeout: 5 s** — remove from `pending`, return a `TimeoutError`. A response that arrives later no longer has a `pending` and is discarded like any unexpected frame. The deadline counts from handing the datagram to the transport and bounds **the whole attempt**, also when the transport never completes the send (a socket that has lost the network): the request ends in a timeout regardless of the send's state, and closing or replacing the socket does not wait for hung sends longer than one response timeout. **Δ** The myGrenton app waits 5 s for a ping, but 15 s for an action and a read; the CLU responds to actions within tens of milliseconds, so Core stays with 5 s for all requests, and the port may expose a separate action timeout.
4. Socket close/failure: all `pending` end with a `ConnectionClosed` error. A failure of the main socket outside `stop()` moves the session to `degraded` without a main socket; the next ping opens a new one (§5.4.5).

#### 5.4.2 Chunking and registration

`register(keys)`:

1. Split `keys` (in order!) into chunks: each chunk is **the longest run of consecutive keys for which the frame `req::{msg_id}:SYSTEM:clientRegister(0,0,999999,{K1,...,Km})` fits within the frame limit** — by default 1471 B UTF-8 (§5.3.2); the port may allow it to be reduced (e.g. with a VPN or PPPoE with a smaller MTU), not increased. The number counts as 6 digits regardless of the actual one, so that the split does not depend on the numbering. A key that by itself exceeds the limit forms its own chunk and is sent nonetheless; its result depends on the CLU like that of any chunk (item 4) — a CLU that does not reassemble IP fragments will not respond and the slot will be `failed`. Chunk `i` **keeps the number** of the existing chunk `i` if its key list is identical to it (same order); in every other case it gets a **new number** from the session counter (consecutive numbers from `1`, the counter reset in `start()`; the number has at most 6 digits, i.e. ≤ 999999, in line with the reserve in the split). When the number 1000000 would be needed, the session deregisters all chunks, closes the main socket, opens a new one and numbers from `1` — a new port gives new triples (§5.3.2), so no number comes back with a different list on the same port. A number is never reused with a different key list — otherwise a late notification of the old list or a lost request with the new one would land values on the wrong keys when their count is the same.
2. For each chunk **in turn** (the next only after the response or timeout of the previous one): send `clientRegister(0,0,{session},…)` **from the main socket**, wait for the response. At the moment of sending, the chunk remembers its `keys` — regardless of the outcome, because the CLU may have accepted the registration even though the response did not arrive.
3. A registration with a number that already exists on the CLU side **replaces** that list (§5.3.2); renewing an unchanged list therefore requires neither a new socket nor a deregistration.
4. Result: a list of **slots** positionally matching `keys`, where `Slot = failed | value(GrentonValue)`. A chunk that failed (timeout, parse error, wrong value count) contributes `failed` at its positions — these positions **do not overwrite** state (§5.5). **Δ** The reference encodes failure as `null`, as a result of which a genuine `nil` from the CLU is indistinguishable from a failure; Core separates these cases with an explicit slot type.
5. Incoming notifications are paired positionally with the `keys` of the chunk with the same number (§5.4.3).
6. After all chunks have been registered, send `clientDestroy(0,0,{session})` (§5.3.2, without waiting for a response) for every **number the new list no longer uses** — replaced by a new number, or surplus when there are fewer chunks — in ascending order, and forget those chunks. With `keys = []` deregister all.

**Δ Chunks sequentially, not in parallel.** The reference (upstream `main`, PR #12) sends all chunks at once. The CLU loses requests sent in bursts: with many chunks at once it does not respond to some of them in time — the entities of those chunks are left without state and without push until the next re-registration (§5.4.4). Registrations of **different** CLUs may proceed in parallel.

**Δ One socket, chunks as subscription numbers.** The reference opens a separate socket for every chunk and new ones on every re-registration, and does not deregister the old ones — every previous subscription then sends notifications to a closed port until it expires. Core registers all chunks from the main socket, each under its own number, and renews them with the same numbers.

**`fetch(keys)` — one-off read.** Splits `keys` like `register` (item 1, with the text of the `fetchValues` frame), sends the chunks **in turn** from the main socket as `SYSTEM:fetchValues({…})` (§5.3.2), each with a fresh `msg_id`, and returns slots positionally matching `keys` (item 4: a chunk without a response, with a rejected payload or with a wrong value count → `failed`). It does not change the chunk list, the numbers or the loops (§5.4.4) and deregisters nothing; with `keys = []` it sends nothing and returns an empty list; without an open main socket (`idle`, `stopped`, `degraded` without a socket) it sends nothing and returns all `failed`. It is serialized with `start`, `register` and `stop` (§5.4.5). The layer above may feed the `StateStore` with the returned slots (§5.5), just like registration slots.

#### 5.4.3 Report path

A `clientReport` datagram with `msg_id` `00000000` on the main socket → read the subscription number → the chunk with that number → parse the values → if their count equals the count of the chunk's `keys`, emit `(keys, values)` to the layer above (`StateStore` update + events). A number without a chunk (e.g. after deregistration or from a previous run of the session) or a value count different from the key count → debug log, discard.

#### 5.4.4 Maintenance loops

| Loop | Interval | Action |
|---|---|---|
| **Ping** | **5 s** from the completion of the loop's previous ping (response or timeout) | `checkAlive()`; failure → warning log (the session lives on — a single lost ping is normal for UDP) |
| **Re-registration** | **30 s** from the **start** of the previous registration (the sending of its first chunk), not earlier than after its completion, if the key list is non-empty | a full `register(keys)` from scratch: the same chunk numbers from the same socket (§5.4.2). It serves two functions: renewal of the subscription on the CLU side (it expires after about a minute, §5.3.2) and a periodic full resynchronization of values (self-repair after lost reports) |

Counting from the start means that every chunk is renewed about 30 s after its previous registration regardless of how long the whole chunk sequence took. The interval was chosen so that one lost renewal, its timeout (§5.4.1 item 3) and the repair by the ping loop (§5.4.5) — together 30 + 5 + 5 s — fit before the subscription expires.

The loops belong to the session: the ping loop starts after `start()` completes (also when the session is `degraded` — it then acts as a retry, §5.4.5), and both end in `stop()`. **In the `degraded` state the re-registration loop does not start a renewal** (a deadline that passed in `degraded` is forfeited — it is not made up after recovery): the CLU is not responding, so every chunk would end in a timeout, and with several chunks successive renewals would overlap; the registration after the CLU returns is performed by the recovery after a successful ping (§5.4.5), and that restarts the interval count. A renewal **already in progress** when the session becomes `degraded` finishes its chunks like any registration (§5.4.2: a chunk without a response → `failed`), because interrupting it halfway would leave the CLU with a partially renewed list; its failed positions are repaired by the recovery anyway. "The previous registration" is any registration of the session: an explicit `register(keys)`, the re-registration after connection recovery (§5.4.5) and the loop itself; each of them restarts the 30 s count. Values from the loop's re-registration reach the layer above via the report path (§5.4.3), as with connection recovery. The 5 s and 30 s intervals are defaults; the port may allow them to be configured (tests, power-saving modes).

**Δ** The reference renews every 45 s, i.e. with one renewal per subscription lifetime; its loop also has a minor sequencing defect (it checks the condition before `sleep`, as a result of which the first iteration may wait 45 s despite there being no work). Core specifies it simply: *30 s from the start of the previous registration, if the key list is non-empty*.

**Δ** Core adds: a change of the subscription set during the session's lifetime (devices added/removed) triggers an immediate re-registration, without waiting for the loop's tick. In the reference implementation the set is built once at startup.

#### 5.4.5 Lifecycle

The session is an explicit state machine:

```
idle ──start()──► starting ──► ready ◄──► degraded ──stop()──► stopped
                     │            ▲            │
                     └── error ───┴── success ─┘
```

| State | Meaning |
|---|---|
| `idle` | created, nothing open |
| `starting` | connect + initial ping + initial register in progress |
| `ready` | loops running, last ping/registration successful |
| `degraded` | loops running, but the CLU is not responding (timeouts) — automatic return to `ready` after a successful ping |
| `stopped` | closed; all pending ended with `ConnectionClosed` |

Rules:

- A session in the `stopped` state **accepts no datagram**: whatever arrives during cleanup or after it (e.g. a report from a subscription whose `clientDestroy` has not yet arrived) changes neither the state, nor the store, nor the diagnostic snapshot; the old socket is closed without reading the rest.
- `start()` and `stop()` are **idempotent**; after `stop()` it is permitted to call `start()` again on the same instance (a requirement of the background/foreground cycle on mobile).
- `stop()` first ends the pending requests with a `ConnectionClosed` error, then sends `clientDestroy(0,0,i)` for every remembered chunk (§5.3.2, without waiting for a response), closes the main socket and forgets the chunks. After a renewed `start()` the chunk list is empty — the layer above establishes the subscriptions anew via `register(keys)`.
- Multi-step operations (`start`, `register`, `stop`) are **mutually serialized** — they cannot interleave (e.g. two registrations mixing chunk numbers). A call during an operation in progress waits or is merged with the current one.
- A `connect`/first-ping error is not fatal for the whole system — the other CLUs keep working; the session enters `degraded`, and the ping loop acts as a retry (the next successful ping → re-registration → `ready`). Values from this re-registration reach the layer above via the report path (§5.4.3), as from any chunk; a chunk that failed contributes `failed` at its positions. If in `degraded` **the main socket does not exist** (its creation failed or the socket failed, §5.4.1 item 4), every ping tick first retries opening the main socket, and only then sends the ping. A new main socket is a new address for the CLU: subscriptions established from the old socket expire by themselves, and the re-registration after a successful ping establishes them anew.
- **A registration with a failed chunk leaves the session pending recovery**, also when the session remains `ready` (the CLU responded to pings but did not respond to one chunk): the CLU then has an incomplete list, so the nearest successful ping — explicit or from the loop — registers the whole list again as on recovery, with values via the report path, and only a registration without a failed chunk ends the retrying. A chunk that failed does not change the session state; `degraded` is decided by pings alone.
- `execute` without an open main socket (`idle`, `stopped`, `degraded` without a socket) ends with the error `SessionError.notConnected` and sends nothing. An action does not open the socket by itself; `start()` or a ping does.
- `ping()` and `register(keys)` in the `idle` or `stopped` state send nothing and do not change the session: `ping()` returns failure, `register` returns all `failed` and **does not remember the keys** nor start the loops. Only `start()` opens the session; a `register` call during `start()` waits for its completion (serialization) and then works normally.
- **Three consecutive unanswered pings** close the main socket: pending requests end with `ConnectionClosed`, the session is `degraded` without a socket, and the nearest ping opens a new one (rule above) and a successful ping triggers re-registration. The counter belongs to the main socket: it counts **attempts on the wire** (every sent `checkAlive()`, from the loop, explicit or the initial one from `start()`, counted once regardless of the number of callers waiting on it) that ended in a timeout since that socket was opened or since the last successful ping; a successful ping resets it, and a new socket (after replacement, after a failure, after `start()`) starts from zero, so it gets its own three attempts. Reason: a transport that has lost the network (e.g. Wi-Fi) may hold the socket in a state in which sends never complete; a new socket is a new port and clean subscriptions.
- A registration interrupted by `stop()` ends like a chunk that failed: `failed` at the positions of the unfinished chunks (§5.4.2 item 4). A chunk whose request has already gone out is deregistered like any other.
- **Diagnostic snapshot.** The session exposes in every state (also `idle`) an immutable snapshot, without sending anything and without waiting on protocol requests (synchronously or asynchronously — the port's choice). All fields come from a single logical observation point of the session; the contract guarantees the consistency of one snapshot of one session, not a shared observation point for the snapshots of different CLUs. Fields:
  - **state** (table above) and **time of capture** — always defined;
  - **time of the last response** — updated when a correctly decrypted frame is matched to a pending request (§5.4.1), also when the subsequent payload validation ends the operation in failure (e.g. a `failed` chunk); a late, unmatched or undecryptable response does not count;
  - **time of the last notification** — updated only after a report has been accepted (§5.4.3), regardless of whether its values changed the state store; a registration response is not a notification, even though its values go via the report path;
  - **round-trip time of the last successful ping** — from handing the frame to the transport to matching the response, monotonic clock; the response may arrive before the transport finishes the send;
  - **requested key count** — the length of the full list of the last `register(keys)` started in the active session, regardless of the progress and results of the chunks (not: confirmed keys); zero before the first registration and after `stop()`; a call waiting to start does not change it;
  - **pending re-registration** — set for a non-empty list when a chunk failed or the session entered `degraded`; it persists throughout the repair and clears after a successful registration of the whole current list, after it is replaced by an empty list, or after `stop()`; merely waiting for the periodic renewal does not set it;
  - **consecutive unanswered pings counter** — the counter of the current main socket (rule above); without a socket — the result of the most recently closed socket of this run, and when none existed — zero; a new socket starts from zero, the retained diagnostic value does not carry over into its counter; after `stop()` zero.

  The times of the last response and notification and the last round-trip time are absent (`nil`) in `idle`, after `stop()` and at the beginning of a new run, until the corresponding events occur; an idempotent `start()` of a running session does not reset them, nor does a socket replacement within the same run. Reason: the application shows the user whether the home is "responding" without reaching into frames; the field names are the port's business (§2).
- A `fetch` interrupted by `stop()` ends the same way: `stop()` does not wait for the read chunks — the request in flight ends with `ConnectionClosed` and yields `failed`, subsequent chunks are not sent and also yield `failed`, and the slots of chunks already received remain; `fetch` returns with that list.

### 5.5 State store — `StateStore`

Structure per CLU:

- `variables: map[name → GrentonValue]`
- `attributes: map[(objectName, index) → GrentonValue]`
- `subscriptionOrder: list of keys in registration order` — **the order is part of the protocol** (positional pairing).

Operations:

- `registerKey(ValueRef)` — idempotent (key already present → no-op); appends to `subscriptionOrder`. Initial value: "not yet known" (**Δ** the reference uses an empty string).
- `updateKeys(keys, slots)` — positional overwrite; `failed` slots are **skipped**, `value(v)` slots overwrite — **including `value(null)`** (a genuine `nil` from the CLU sets the state to `null`). **Δ** The reference skips every `None`, so `nil` never overwrote a value; thanks to the explicit slot type (§5.4.2) Core can distinguish this correctly. Values from `clientReport` notifications are always `value(…)` slots.
- `get(ValueRef) -> GrentonValue?` — read of the current value (`nil`/"unknown" distinguishable from `value(null)`).

**Δ Events.** The reference implementation emits a single global "something changed" (the HA coordinator's broadcast). Core specifies granular events: `StateChanged(cluId, key, oldValue, newValue)` emitted only for actual value changes. Requirements for the delivery mechanism: (a) **multiple independent subscribers** — a subscription creates its own stream/registration, not a shared single-consumer queue; (b) a defined and documented buffering policy for a slow subscriber (recommended: bounded buffer, newest wins), whereby the raw change stream **does not guarantee a complete history** — the consumer reads the current state from the store; (c) for a subscription **restricted to a set of keys**, changes outside that set must not cause the loss of a notification about a pending change of a key in that set — the requirement covers the whole delivery path (a registration response contributes as many changes at once as it has keys), while the manner of filtering, buffering and merging is the port's choice; for entity signals §6.5 applies; (d) cancelling a subscription cleans up resources. This enables cheap per-device binding in adapters.

### 5.6 Concurrency model (semantic requirements)

Technology-neutral (actor / serial queue / mutex — the port's choice):

1. Mutations of the state of one `CluSession` (the `pending` map, the chunk list) are serialized.
2. `StateStore` is safe under concurrent reads from multiple threads and writes from the session.
3. Events to the consumer are delivered **in order of occurrence** for a given CLU.
4. Cancellation (shutdown) is safe at any moment and is not lost (loops, pending requests).

---
## 6. Devices module — canonical model

### 6.1 General principles

- **Mapping pipeline — two stages:**
  1. **Descriptors** (a pure function over the DTO, with no dependency on the Runtime): `MobileInterface → pages[].widgets[] (flattened) → DeviceDescriptor[]` — a descriptor carries the id, kind, name, references (`ValueRef`), actions (`ActionRef`), ranges and completeness rules. Testable without a network.
  2. **Binding** (a factory in the Runtime/system): `DeviceDescriptor + StateStore + CluSession → live entities` with state reads and intents.
  The separation is mandatory — a consumer may use the descriptors alone (e.g. to display a list of devices without connecting to the CLU).
- Pages serve only as a container — Core does not model the UI layout.
- Every device has: `id` (= `widget.id`), `widgetType` (= `widget.type`), `kind` (the canonical kind), `name`, `page` (`{index, name}` of the page on which the widget lies) and a list of **canonical entities** (one device may have several, e.g. a double relay → 2 switches). Entity identifier: `{widget.id}_{suffix}` — the suffixes are given below per type (they are a **stable contract** — adapters build unique_id on them). Devices controlling the same CLU object are grouped by §6.6.
- **The device `kind`** says what the device is, independently of the entities it exposes (a contact sensor with a button is still a `contactSensor`). Fixed per widget type:

  | `widget.type` | `kind` |
  |---|---|
  | `ON_OFF`, `ON_OFF_DOUBLE` | `switch` |
  | `DIMMER_V2` | `dimmableLight` |
  | `LED` | `hsbLight` |
  | `CONTACT_SENSOR`, `CONTACT_SENSOR_DOUBLE` | `contactSensor` |
  | `VALUE_V2`, `VALUE_DOUBLE` | `sensor` |
  | `MULTISENSOR` | `multisensor` |
  | `SLIDER` | `numberSetting` |
  | `ROLLER_SHUTTER`, `ROLLER_SHUTTER_V3` | `cover` |
  | `SCENE`, `SCENE_DOUBLE` | `scene` |
  | `THERMOSTAT_V2` | `thermostat` |
  | `CAMERA` | `camera` |

- **The device `name`:** the widget's `label`, if the type has one (`VALUE_V2`, `DIMMER_V2`, `CONTACT_SENSOR`, `SLIDER`, `MULTISENSOR`, `ROLLER_SHUTTER_V3`, `THERMOSTAT_V2`). Otherwise the non-empty `label`s of the components in order, joined with `" / "` (`VALUE_DOUBLE`, `CONTACT_SENSOR_DOUBLE`: left, right; widgets with `components[]`: list order). Exception: `LED` → the `label` of the first `BUTTON_BISTABLE` component. No labels → `""`. Entity names are independent (§6.3).
- **Order is part of the contract:** devices in the order `pages[]` → `widgets[]` (skipped widgets do not create devices); a device's entities in the order given in §6.3 (components in list order; `CONTACT_SENSOR_DOUBLE`: left sensor, left button, right sensor, right button; `MULTISENSOR`: the order of the table in §6.3.6; sensor/slider before button).
- **Completeness rules:** a widget that does not meet the requirements of its mapping (missing action, missing component) produces a device **without entities** or omits the given entity — never an error for the whole.
- An entity reads state through `StateStore.get(valueRef)` and exposes a **normalized state**; commands translate canonical values into the device format and execute the `ActionRef` through the session. All of this translation (coercions, ranges, inversions of scale, rounding) lives in Core — adapters **do not reimplement** it.
- Normalization helper function:

```
mapRange((a,b) → (c,d), v) = c + (v−a)/(b−a) · (d−c);   when b−a ≤ 0 → c
```

- **Validation of intent inputs:** `mapRange` does not clamp — every intent that accepts a numeric value **first clamps it to the canonical range** (brightness/saturation to [0,1], hue to [0,360], position/tilt to [0,100], setValue to [min,max]); rounding of halves: half-to-even (consistent with the reference implementation's `round`).

### 6.2 Common coercions

- **`boolFromOnOff(v)`** (switches): `true` ⇔ `v` is the string `"ON"`, the bool `true` **or a number equal to 1** (`int` 1, `double` 1.0). Other values → `false` (including `"1"`, `2`, `"on"`); `null` → unknown state. Reference implementation: `value == "ON" or value == True` — in Python `1 == True` and `1.0 == True` are true. Relays (`DOU…`, feature 0) report their state as the number 0/1, so this branch decides the state of most switches.
- **`truthy(v)`** (binary sensors, LED on/off): standard truthiness (`0`/`0.0`/`""`/`false`/`null` → false); `null` → unknown state (before coercion).
- Numeric reads: `int`/`double` directly; a string is not re-parsed as a number (casting took place on receipt).

### 6.3 Device catalog

#### 6.3.1 `Switch` — from `ON_OFF` and `ON_OFF_DOUBLE`

- One entity per component; suffix: `_{rowId}`.
- **Requirement:** the component must have an action with `event==ON` **and** one with `event==OFF`; if either is missing → the component is skipped. With several actions for the same event the **first** on the list wins; `CLICK` actions are ignored.
- State: `isOn = boolFromOnOff(state)`. Intents: `turnOn()` → the ON action; `turnOff()` → the OFF action.

#### 6.3.2 `DimmableLight` — from `DIMMER_V2`

- One entity; suffix `_0`.
- Parameters: `min`, `max`, `precision` from the widget.
- State: `isOn = value > 0`; `brightness ∈ [0,1] = mapRange((min,max)→(0,1), value)`.
- Intents: `turnOn()` → onAction; `turnOff()` → offAction; `setBrightness(b∈[0,1])` → `setValueAction` with `value = format(round(mapRange((0,1)→(min,max), b), precision))`.

#### 6.3.3 `HsbLight` — from `LED`

- **Completeness requirement:** all 4 components (`BUTTON_BISTABLE`, `SLIDER_HUE`, `SLIDER_SATURATION`, `SLIDER_BRIGHTNESS`); if any is missing → a device without entities. ON and OFF actions on the button are required. Slider action: the first on the `actions` list.
- One entity; suffix `_0`. Each slider has its own `range{min,max}` and its own `ValueRef`.
- State: `isOn = truthy(button state)`; `hue ∈ [0,360] = mapRange(hueRange→(0,360))`; `saturation ∈ [0,1] = mapRange(satRange→(0,1))`; `brightness ∈ [0,1] = mapRange(briRange→(0,1))`.
- Intents: `setColor(h,s)` → two actions (hue, saturation), values mapped to the device ranges, rounded to 2 places; `setBrightness(b)` analogously; `turnOn()/turnOff()`. Order in a compound change: color → brightness → ON (as in the reference implementation).

#### 6.3.4 `ContactSensor` — from `CONTACT_SENSOR` and `CONTACT_SENSOR_DOUBLE`

- `CONTACT_SENSOR`: entity `_binary_sensor`; optionally entity `_button` (`Trigger`, §6.3.9) when a `clickAction` is present.
- `CONTACT_SENSOR_DOUBLE`: entities `_binary_sensor_left` and `_binary_sensor_right`; optionally `_button_left` / `_button_right` when the given component has a `clickAction` (suffixes consistent with the reference implementation — unique_id contract).
- State: `isActive = truthy(value) XOR reverseState`.
- Metadata: `onIndication`, `offIndication` (state labels for the adapter's UI).

#### 6.3.5 `Sensor` — from `VALUE_V2`, `VALUE_DOUBLE`

- `VALUE_V2`: entity `_0`. `VALUE_DOUBLE`: entities `_0` (componentLeft) and `_1` (componentRight) — suffixes consistent with the reference implementation (unique_id contract).
- State: `value: GrentonValue` (raw, after wire casting). Metadata: `unit` (Grenton enum), `valueType`, `precision` — the adapter decides on presentation; user config (§6.4) may override the class/unit.

#### 6.3.6 `Sensor` ×7 — from `MULTISENSOR`

Seven entities with fixed channels — the channel determines the **canonical kind and unit**:

| Entity suffix | Channel | Canonical kind | Unit |
|---|---|---|---|
| `_air_co2` | objectAirCo2 | `co2` | ppm |
| `_sound` | objectSound | `soundPressure` | dB |
| `_air_voc` | objectAirVoc | `voc` | ppb |
| `_light` | objectLight | `illuminance` | lx |
| `_pressure` | objectPressure | `pressure` | hPa |
| `_humidity` | objectHumidity | `humidity` | % |
| `_temperature` | objectTemperature | `temperature` | °C |

(The fields `minValue/maxValue/threshold/sensitivity` — unused for now; keep them in the DTO.)

#### 6.3.7 `NumberSetting` — from `SLIDER`

- Entity `_slider`: `value` (raw number), `min`, `max`, `step = 10^(−precision)`; intent `setValue(v)` → `setValueAction` with `value = format(round(v, precision))`.
- Optional entity `_button` (`Trigger`) when a `clickAction` is present.

#### 6.3.8 `Cover` — from `ROLLER_SHUTTER_V3`

- One entity; **id = widget.id without a suffix** (as in the reference implementation — keep for unique_id compatibility).
- Subscribed states: `state`, `position`, conditionally `lamelPosition`.
- **Slats (tilt) are present ⇔ the field `lamelMoveTimeout` occurs in the object** (the reference implementation's heuristic for "the shutter has slats"). `stop` is present ⇔ `stopAction` occurs.
- Normalized state:
  - `position ∈ [0,100]` — the raw `position` value (0 = closed, 100 = open);
  - `motion`: from `state` — `0 → idle`, `1 → opening`, `2 → closing`;
  - `isClosed = (position == 0)`;
  - `tilt ∈ [0,100] = 100 − mapRange((0,90)→(0,100), lamelPosition)` — the device reports 0–90 on a scale inverse to the canonical one.
- Intents: `open()` → setPosition("100"); `close()` → setPosition("0"); `setPosition(p)` → `setPositionAction` with an int 0–100; `stop()` → `stopAction` (if present); `setTilt(t∈[0,100])` → `setLamelPositionAction` with `int(90 − mapRange((0,100)→(0,90), t))`; `openTilt()` = setTilt(100) ⇒ device value 0; `closeTilt()` = setTilt(0) ⇒ 90.
- The remaining fields of the V3 object (moveUp/Down, hold, calibration…) — out of scope for v1; the DTO preserves them.

#### 6.3.9 `StateSensor` + `Trigger` — from `ROLLER_SHUTTER` (legacy)

- **Completeness requirement:** the **first** component has ≥1 action; otherwise a device without entities (**Δ** the reference implementation indexes `components[0]` without checking — an empty component crashes the mapping).
- Entity `_sensor`: an enumerated state from the first component: `0→stopped, 1→opening, 2→closing, 3→locked, other→unknown`.
- Entity `_button` (`Trigger`): executes the **first** action of the first component.
- `Trigger` is the general canonical "button" entity: a single intent `press()` that executes the assigned `ActionRef`.

#### 6.3.10 `SceneTrigger` — from `SCENE` and `SCENE_DOUBLE`

- One entity per component. Suffix: `SCENE` → `_{rowId}` (the reference implementation's unique_id contract); `SCENE_DOUBLE` → `_{position}` (0, 1… — the component's index on the list, also when an earlier component was skipped), because `rowId` repeats.
- **Requirement:** an action with `event==CLICK` and `callType==SCRIPT`; if missing → the component is skipped.
- Intents: `run()` — executes the script with the `value` from the widget; `run(parameter: string)` — executes with `value` **replaced literally** by the parameter (Lua syntax is the caller's responsibility: `"abc"`, `42`, `""` → `scene()`); the original `ActionRef` remains unmutated (copy when overriding).

#### 6.3.11 `Camera` — from `CAMERA`

- **Completeness requirement:** ≥1 `CAMERA_STREAM` component; otherwise a device without entities (**Δ** the reference implementation indexes `components[0]` without checking).
- Entity `_0`; static state: `streamUrl = components[0].value` (e.g. RTSP). Core does not touch the stream — playback is the adapter's concern.

#### 6.3.12 `Thermostat` — from `THERMOSTAT_V2`

The widget of the Grenton Thermostat virtual object. Feature semantics according to the Grenton manual ("Thermostat virtual object", Object Manager); **Δ** the reference implementation does not support this widget.

- One entity; suffix `_0`. Completeness: the required fields from §3.5 (if missing → the widget is skipped); the optional controls exist when **both** the feature **and** its action are present.
- State:
  - `currentTemperature`, `targetTemperature` — numbers (`targetTemperature` is the resulting `TargetTemp`, dependent on the mode);
  - `minTemperature`, `maxTemperature` — limits (optional);
  - `mode` from the Mode feature: `0 → manual`, `1 → holiday`, `2 → schedule`, other → unknown;
  - `isOn = truthy(state)` — the State feature: 0 = thermostat stopped (does not control the output), 1 = running;
  - `output` — `controlOutValue` raw (0/1 for a digital output, a level for an analog one).
- Intents (all actions have `$value$`, §3.3):
  - `setTargetTemperature(t)` → `setTargetTemperatureAction` (PointValue, manual setpoint); `t` clamped to `[min, max]` when both limits are known; `value = format(round(t, 1))` (§10 item 3);
  - `setMode(m)` → `setModeAction` with `"0"`/`"1"`/`"2"`;
  - `turnOn()` / `turnOff()` → `setStateAction` with `"1"` / `"0"`;
  - a missing control → the intent is a no-op (like a shutter's `stop()` without `stopAction`).
- Out of scope for v1: the schedule (`scheduleData`), changing the limits, fans (`no_of_fan_speeds`).
- Numeric values: temperatures arrive as `int` or `double` (`"20"` → `int`, `"19.8"` → `double`), including fractional setpoint temperatures (`21.5`) — the read treats both types as a number (§6.2). The `min/max` limits come from the CLU; do not assume the range from the manual.
- In manual mode `targetTemperature` takes the value written by `setTargetTemperature`.

### 6.4 User metadata (user config)

Adapters let the user refine the presentation (device class, unit, slider mode). Since Core **neither interprets nor persists** this data, **it does not store it at all**: the user configuration lives entirely on the adapter's side as its own `entityId → config` map, keyed by the stable entity identifier (§6.1). Core's only guarantee is the **stability of entity identifiers** across runs and ports. Thanks to this, HA configuration keys (e.g. `device_class`) do not leak into Core, another adapter (HomeKit) keeps its own — and Core's entities remain immutable.

### 6.5 Device events

Devices subscribes to the granular `StateChanged` from the Runtime (§5.5 Δ) and translates it into `DeviceStateChanged(deviceId, entityId)` — emitted only when the change concerns a `ValueRef` of the given entity. The adapter binds the entity to its stream and reads the fresh normalized state.

The entity's update signal is a **"state has changed" flag, not a history**. For every active subscription, every change of the value of any of the entity's references must be covered by a signal delivered after that change, as long as the subscriber is receiving and has not canceled the subscription. One signal may cover many changes from before its delivery; a change after delivery requires another signal. Before a signal becomes visible, the write corresponding to it is already visible in the state store (a read may also return a newer state). Changes to other keys must not void this guarantee (§5.5 Δ item c). The guarantee applies from the activation of the subscription and does not include replaying earlier changes: after activation the adapter reads the initial state from the store. Reason: a lost signal would leave the view stale until the next change of the same value, which the adapter has no way to detect.

### 6.6 Physical objects

A device corresponds to a **widget**, not to hardware: the same CLU object is sometimes shown by several widgets, on different pages and under different names (e.g. a thermostat on a room page as "Temperature" and on the aggregate heating page under the room's name). Device and entity identifiers remain per widget (the stable contract of §6.1), and Core **additionally** provides grouping by object, so that the adapter can show one thermostat instead of two.

- **Physical object** = what the entity controls on the CLU, identified by the key `{cluId, kind, name, index}`:
  - `object` — a feature/method of an object (`THE1001` / `14`, `ROL1001` / `…`), `name` = `objectName`, `index` = the feature index (or the method index for stateless entities). **The index is part of the key:** one LED module controls separate strips on separate channels (`LED1001` / `3` and `/ 4` are two different devices), while both thermostat widgets read the same feature (`THE1001` / `14`);
  - `variable` — a CLU global variable, `name` = the variable name, `index` = `null`;
  - `script` — a Lua script (scenes), `name` = the `objectName` of the `SCRIPT` action, `index` = `null`.
- **Every entity belongs to exactly one object** — by its primary reference: `switch`, `dimmableLight`, `contactSensor`, `sensor`, `numberSetting`, `stateSensor` → `state`; `hsbLight` → `powerState`; `cover` → `position`; `thermostat` → `currentTemperature`; stateless entities (`trigger`, `sceneTrigger`) → their action (`VARIABLE` → variable, `SCRIPT` → script, others → object). `camera` does not touch the CLU and is not grouped.
- A widget may therefore belong to several objects (`ON_OFF_DOUBLE` → two relays; a contact sensor with a button on a variable → the sensor's object and the variable) — with the appropriate entities at each.
- **Result:** a list of objects in order of first use (devices in the order of §6.1, entities in their order); each object: `key` and `widgets[]` in device order, each entry: `deviceId`, `widgetType`, `name` (the device name), `page`, `entityIds[]` (the entities of this widget belonging to the object).
- The choice of name and area (e.g. room page vs function page) belongs to the adapter — Core provides them all.

---
## 7. Port contract

Interfaces that **the platform must provide** (injected; canonical names):

### 7.1 `UdpSocketFactory` / `UdpSocket`

```
UdpSocketFactory.open(remoteHost, remotePort) -> UdpSocket   // bind on an ephemeral port
UdpSocket.send(datagram: bytes)                              // to the fixed peer
UdpSocket.incoming: stream/callback (bytes)                  // incoming datagrams
UdpSocket.close()
```

Requirements: the socket has **its own local ephemeral port** (this is the port the CLU sends notifications to, §5.3.2); reception of unicasts on that port; no multicast/broadcast requirement. Filtering the source down to the CLU address is permitted (**Δ** — the reference implementation does not do it), but the port implementation **must have a tested mode without filtering** (reception from any sender) as a fully supported fallback: a CLU with several network interfaces may send reports from a source address other than the one the registration was sent to.

### 7.2 `AesCbcCipher`

```
encrypt(plaintext: bytes) -> bytes   // PKCS7 + AES-CBC(key, staticIV)
decrypt(ciphertext: bytes) -> bytes? // null on a padding/size error
```

Constructed from the material of §5.1 with length validation.

### 7.3 `HttpClient` + `ZipReader` (Provisioning only)

```
HttpClient.get(url) -> (status, body: bytes)
ZipReader.entry(archive: bytes, path: string) -> bytes?
```

### 7.4 `Clock` / `TimerScheduler`

```
sleep(duration), now()   // cancellation-aware
```

Injected so that tests control time (the 5 s / 30 s loops, the 5 s timeout).

### 7.5 `RandomSource`

```
bytes(n) -> bytes        // msg_id, keepAliveId
int(in: 1...65535) -> int // unused by the session — subscription numbers are sequential (§5.4.2)
```

Injected for deterministic tests.

### 7.6 `Logger`

Levels `debug/info/warning/error`; Core logs through the facade, never directly.

### 7.7 Asynchronous execution

The port provides the primitives: starting a task, cancellation, serialization of mutations (an actor/queue). Semantics — §5.6.

---

## 8. Error taxonomy

| Category | Variants | When |
|---|---|---|
| `ProvisioningError` | `authenticationFailed`, `connection`, `invalidData`, `interfaceNotFound`, `interfaceAmbiguous` | §4; the last two — interface selection from the project file (§4.5.2) |
| `CryptoError` | `invalidKeyMaterial`, `encryptFailed`, `decryptFailed` | §5.1; `decryptFailed` on the receive path → drop the datagram, do not propagate |
| `ProtocolError` | `malformedFrame`, `malformedPayload` | frame/payload validation |
| `PayloadError` | `unresolvedPlaceholder` | an action with `value` = `$value$` without an override (§3.3) — nothing is sent |
| `TimeoutError` | — | no response within 5 s |
| `SessionError` | `notConnected`, `connectionClosed` | lifecycle, §5.4.5 |

Rule: errors on the receive path (a garbage datagram) are logged and swallowed; errors on the request path (an action, a registration) are returned to the caller; errors in the maintenance loops are logged and the loop keeps running.

---

## 9. Test strategy

The **conformance suite** (`conformance/`, contract: [`conformance.md`](conformance.md)) is the normative, machine-executable form of this section: language-independent JSON cases that every port runs with its own runner. The profiles and their scope are described in [`conformance.md`](conformance.md) §1; the current version of the suite is given by `conformance/manifest.json`. The tables below are illustrative; in case of a discrepancy the suite decides (and one or the other is corrected).

### 9.1 Crypto golden vectors

A static IV ⇒ deterministic encryption ⇒ identical vectors apply to every port. Test material:

```
key (b64): AAECAwQFBgcICQoLDA0ODw==        (hex 000102030405060708090a0b0c0d0e0f)
iv  (b64): EBESExQVFhcYGRobHB0eHw==        (hex 101112131415161718191a1b1c1d1e1f)
```

| Plaintext (UTF-8) | Ciphertext (hex) |
|---|---|
| `req::aabbccdd:checkAlive()` | `5eab3361763eb47aecf01654656faa9b4e31ef685544739464c5291048d6ab60` |
| `req::0f3e9a11:SYSTEM:clientRegister(0,12345,1,{"AnalogIn1",{DOU8272,0}})` | `32a53db2c8eb9ad8e14bd127c466c22f3ed8932f5614c2a355d3e57fee6e5aec934236153383944a680ec2c811803421df6a81a75c9be4370bfe85a669b47ffdb62893c77bc792803ca88917ac3fa61a` |
| `resp:192.168.1.10:00000000:clientReport:0:{21.5,"ON"}` | `9d53460871ebf060f96fe81c68fcd85ff81ed07fad264a4106ddeeb511329c8cafed3face4e8a334d20e5b307ddf62cb2ab676e0e1a0527242489ba39349b644` |

Bidirectional test: encrypt(plaintext) == ciphertext and decrypt(ciphertext) == plaintext.

The vector with `clientRegister(0,12345,…)` is solely a cipher fixture — the form of its arguments is not normative (the session sends `clientRegister(0,0,{session},…)`, §5.3.2).

### 9.2 Value casting table

| Input | Result |
|---|---|
| `nil` / `NIL` | null |
| `true` / `false` | bool |
| `"ON"` | string `ON` |
| `42` / `-7` | int |
| `21.5` / `-3e2` | double |
| `"21.5"` | double 21.5 (quotes are stripped before the cast) |
| ` tekst ` | string `tekst` |

### 9.3 Parsing fixtures

- A minimal `data.json` with ≥1 widget of every type + 1 widget of an unknown type (must be skipped) + 1 widget with a missing field (skipped).
- A shared fixture file in the Core repo; every port decodes it and asserts an identical list of devices/entities (ids, suffixes, completeness).

### 9.4 Fake CLU

A test UDP server on loopback, with the same cipher:

- answers `checkAlive()` and actions by echoing a `resp` frame,
- answers `clientRegister` with a `clientReport` carrying the configured initial values,
- can, at the test's request, emit a `clientReport` notification (msg_id `00000000`) to the source port of the registration,
- negative scenarios: no response (timeout), bad frame, bad padding.

The session integration tests run against it: chunked registration (a key list above the frame limit ⇒ ≥2 subscription numbers), routing of reports by number, re-registration, deregistration, state-store updates, actions.

### 9.5 Port conformance checklist

1. AES golden vectors (9.1) ✔ — suite: `crypto.*`
2. Casting table (9.2) ✔ — suite: `value.cast.*`
3. Interface fixture → identical set of devices/entities (9.3) ✔ — suite: `mapping.*`
4. Payload construction: register (var/attr keys), the 4 action variants — 1:1 string comparison ✔ — suite: `wire.*`, `payload.*`
5. Fake CLU scenarios (9.4) ✔
6. Devices normalizations: dimmer round-trip, LED HSB round-trip, inverted cover tilt, reverseState XOR ✔

---

## 10. Known limitations and decisions

1. **Static IV / no MAC** — imposed by the CLU protocol; cannot be fixed on the client side (§5.1).
2. **Positional pairing without escaping** — a string containing a quotation mark will derail the report (§5.3.2). A comma inside text and nested braces parse correctly since 0.9.0; the protocol does not escape the quotation mark, so there is no workaround here. The risk is real only for text variables.
3. **Number format in action payloads:** the reference implementation sends Python's `str(round(v, p))` (e.g. `5.0` at precision 0). **The default behavior of Core is the format matching the reference implementation** (the CLU accepts it, e.g. `"22.0"`). The format without `.0` is available only behind a flag — its acceptance by the CLU is not confirmed. Quotation marks around a number are irrelevant to the CLU (§5.3.4 Δ).
4. **Registration frame limit of 1471 B** (§5.3.2) — in bytes, not a number of keys: a key count does not protect against long variable names. It follows from the 1500 MTU, so the port may allow it to be lowered when a smaller MTU is on the path; it may not raise it, because some CLUs do not reassemble IP fragments.
5. **No verification of the datagram sender** in the reference implementation; Core permits filtering by the CLU address (Δ §7.1).
6. **One socket per session, chunks as subscription numbers** (§5.4.2). Earlier releases of this specification assumed that the number in a notification is unreliable and required a socket per chunk; that was a misreading of the `clientRegister` arguments (§5.3.2). Notifications arrive at the source port of the registration, so the main socket is also the receiver of reports.
7. **Subscription expiry** after about a minute is a property of the CLU, not of the client; it cannot be disabled, only renewed (§5.4.4).
8. **`interface.pushNotifications` and cloud mode — out of scope.** `pushNotifications` lists the objects the CLU notifies about via the Grenton cloud: the phone receives an FCM notification with `cluId` and an `encryptedData` field (hex), encrypted with the same AES-CBC and interface key as the CLU frames (§5.1), with `{title, message}` inside. Receiving them requires registering a token with Grenton's service, so Core does not implement this; `GrentonCipher` would decrypt such content if the application obtained it by another route. The application's cloud mode is MQTT (AWS IoT) with the same frame bytes as over UDP, authenticated with a certificate owned by the vendor — out of scope for the same reason.
9. Defects of the reference implementation that we **do not carry over**: the dead code `self.min = min` in the LED entity; an empty string as the initial value; Python's liberal number parsing; the sequence of the re-registration loop (§5.4.4).
10. **Thermostat — modes 1/2:** the semantics of the holiday and schedule modes come from Grenton's documentation; writing Mode directly through the feature (instead of the `HolidayModeStart`/`AutoModeStart` methods) is not confirmed on a CLU.

---

## 11. Glossary

| Term | Meaning |
|---|---|
| **CLU** | Grenton logic controller — the device on the LAN that the Runtime talks to over UDP |
| **Object Manager (OM)** | Grenton's configuration program for the PC; the source of the mobile interface |
| **Mobile interface** (Polish: *interfejs mobilny*) | The UI/device definition designed in OM for the myGrenton application; the JSON consumed by Core |
| **Widget** | An element of the mobile interface (type + components); the unit of mapping onto a device |
| **Component** (Polish: *komponent*) | A sub-element of a widget (button, slider, camera stream) |
| **ValueRef / ActionRef** | A state-read reference / an action definition on the CLU (§3.2, §3.3) |
| **Chunk** | A portion of subscription keys whose registration frame fits within the limit (§5.4.2), registered under its own subscription number (`session`) |
| **client report** | The payload with subscription values — both as the response to a registration and as a notification |
| **Canonical entity** (Polish: *encja kanoniczna*) | A normalized element of a device (switch, sensor…) exposed to adapters |

