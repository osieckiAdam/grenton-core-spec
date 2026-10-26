# grenton-core-spec

Language-agnostic specification of **Grenton Core**, a portable library for the
Grenton smart-home "mobile interface" protocol, together with the
**conformance suite** every implementation must pass.

The specification covers provisioning from Object Manager, the encrypted UDP
protocol of CLU controllers, and a canonical device model.

| | |
|---|---|
| [`spec/grenton-core-design.md`](spec/grenton-core-design.md) | The specification |
| [`spec/conformance.md`](spec/conformance.md) | Conformance contract: case format, value encoding, runner rules |
| [`conformance/`](conformance/) | The suite: `manifest.json`, `cases/`, `fixtures/`, `schemas/` |
| [`tools/suite.py`](tools/suite.py) | Validates cases and rebuilds the manifest (Python 3, stdlib only) |
| [`tools/oracles/`](tools/oracles/) | Independent reference derivations used to check expected values |

## Implementations

| Port | Suite version | Profiles |
|---|---|---|
| [grenton-core-swift](https://github.com/osieckiAdam/grenton-core-swift) ([documentation](https://osieckiadam.github.io/grenton-core-swift/)) | 0.13.1 | `stateless`, `mapping`, `session`, `project` |

## Conformance suite

Test cases are data. Each file in `conformance/cases/` names an operation,
its input and the expected outcome:

```json
{
  "schemaVersion": 1,
  "id": "provisioning.url.pin-space-stripped",
  "specRefs": ["provisioning.pin"],
  "description": "The grouping space OM displays is removed.",
  "provenance": "spec §4.1",
  "operation": "provisioning.url",
  "input": { "host": "192.168.1.5", "pin": "149 853" },
  "expect": { "outcome": "ok", "value": { "url": "http://192.168.1.5:9998/api/v1/interface/hash/149853" } }
}
```

A port adds a thin runner that loads every case listed in
`conformance/manifest.json`, calls its production API and compares the result.
Expected values never come from a port. They come from the spec text,
`openssl`, the reference implementation's semantics, or an independent
Python derivation written from the spec (`tools/oracles/`).
`tools/suite.py check` re-derives them wherever such an oracle exists.

Version 0.14.0 contains four profiles, 254 cases in total:

- `stateless` (182 cases): AES-CBC, frames, payloads (registration, one-shot
  read, actions, reports), value casting, the Object Manager URL and PIN,
  coercions, number formatting.
- `mapping` (7 cases on 7 interface fixtures): `data.json` → devices and
  entities, completeness rules, widget-level tolerance, normative order,
  thermostats and double scenes, and devices grouped by the CLU object
  they operate.
- `session` (51 scenarios): one CLU session against a CLU played by the
  runner on a virtual clock — correlation and timeouts, keep-alive,
  sequential chunked registration on one socket, report routing by
  subscription number, unregistering, lifecycle,
  recovery, actions, and the periodic ping and re-registration loops.
- `project` (14 cases on 11 project fixtures): importing a mobile interface
  from an Object Manager project file (`.omp`) — interface selection,
  key, CLUs and push objects, the editor-to-interface conversion of every
  widget type (stand-in ids for widgets never sent to a phone), checked
  through the same device mapping
  as a downloaded interface; and broken archives.

The `provisioning` profile is planned. See
[`spec/conformance.md`](spec/conformance.md) §8 for the open decisions.

```sh
python3 tools/suite.py check      # validate all cases
python3 tools/suite.py manifest   # rebuild the manifest after editing cases
```

## License

[MIT](LICENSE).
