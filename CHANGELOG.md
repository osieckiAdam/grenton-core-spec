# Changelog

## Spec 0.13.0, suite 0.13.0 — 2026-10-26

A device knows the section it is shown under. The first app built on the Swift port (a macOS menu-bar app) wanted the layout of the myGrenton app — page, section header, widgets — and found it only in the interface DTO, beside the device model.

Spec:

- §6.1: every device carries `section` — `{id, title}` of the nearest `HEADER` widget above it on the same page, `null` above the first header or on a page without one; a header that does not parse starts no section.
- §3.5: a `HEADER` is its `id` and the first component's `label`; without either it is skipped like any widget.
- conformance.md §4a: `section` in the device projection.

Suite (254 cases, expectations re-derived): every mapping case and the `.omp` project cases now carry `section`; the fixtures with headers (`thermostat-scene-double-header`, `widget-tolerance`, the two-interfaces project) cover a device before the first header, under a header, and after a second one.

## Spec 0.12.2, suite 0.12.2 — 2026-10-26

The rule-list shape of §6.3.13, pinned after a review of the Swift port found its parser accepting rows with a missing separator, an empty field or a trailing field, and weekday lists with empty items. No rule changed in intent; the text now says a row is exactly `{int, int, "text"}` with whitespace allowed outside the quotes, and that an empty item in the day field makes the rule unsupported.

Suite (254 cases): `schedule.rules.{whitespace,reject-malformed-row,reject-trailing-field,reject-empty-field,unsupported-day-list}`.

## Spec 0.12.1, suite 0.12.1 — 2026-10-25

The `schedule.rules` projection lists `days` ascending (0–6); the `list` case had one rule's days in another order. No rule changed.

## Spec 0.12.0, suite 0.12.0 — 2026-10-25

The two widget types v1 left out are mapped: `EVENT_SCHEDULER` (the EventScheduler virtual object: up to 16 CRON rules) and `TV_REMOTE_CONTROL` (a remote: a set of button actions). Finding them live showed a protocol fact the parser did not handle.

Evidence (one installation; the Grenton manual "The EventScheduler virtual object"; the myGrenton app on a phone): a scheduler's `RuleList` reaches the client as a **Lua table serialized into a quoted string**, `"{{2, 1, "0 20 * * *"},{1, 1, "0 23 * * 6,0"}}"`, with quotes and commas inside the quotes. The 0.9.0 rules read the inner quotes as text boundaries, so a weekday list with a comma made the value count wrong and the whole reply was rejected — a one-off read of any scheduler with a rule on chosen days failed. The app writes Sunday as `0` and Saturday as `6` (`6,0`), Monday to Friday as the list `1,2,3,4,5` (the manual's example uses the range `1-5`), minutes freely; the CLU lists rules by their next firing time, not by id, answers `N/A` for `CurrentRule` when nothing runs, and `AddRule` returns the new id.

Spec:

- §5.3.2 pt 2: text beginning with `"{` is a quoted table — it extends to the brace that balances its first one, ignoring quotes in between, and must be followed by `"`; without such a brace the plain rule applies. §5.3.2 pt 4 and §10 pt 2 narrowed accordingly.
- §3.5: `EVENT_SCHEDULER` (required `state`, `ruleList`, `addRuleAction`, `deleteRuleAction`) and `TV_REMOTE_CONTROL` (`vodLabel` and a map of up to 21 named button actions, possibly empty) join the catalog; 18 supported types.
- §6.1: kinds `eventSchedule` and `tvRemote`; both take the widget `label` as the name.
- §6.3.13 `EventSchedule`: subscribed states, the CRON subset Core reads and writes (`minute hour * * days`, weekdays 0–6 with 0 = Sunday, ranges and 7 accepted on read, unsupported rules keep their text), the rule-list parsing (`nil` when no rules are known), `isActive`, `rules`, `ruleCount`, `freeRuleSlots`, `nextRule`, `currentRule`, and the intents `setActive`, `addRule(hour, minute, days)` (sent quoted, as the app does), `deleteRule`, `enableRule`, `disableRule`.
- §6.3.14 `TvRemote`: the entity exists even without buttons; `buttons`, `press(button)`; not grouped into physical objects (§6.6); `eventSchedule` groups by `state`.
- conformance.md: operations `schedule.rules` and `schedule.cron`; projection fields of the two kinds; requirement id `devices.schedule`.

Suite (249 cases): `payload.values.quoted-table` (the live frame shape), `payload.report.quoted-table`, `payload.values.quoted-table-fallback`, `payload.report.reject-quoted-table-unclosed`; `schedule.rules.{list,single,unsupported,none,empty-table}`; `schedule.cron.{weekend,weekdays,every-day,reject-hour,reject-no-days,reject-day}`; `mapping.interface.scheduler-tv-remote` on a new fixture; `provisioning.omp.select-by-id` re-derived (its scheduler and remote are now devices; the remote's unassigned buttons drop out). The mapping and `.omp` oracles map both types.

## Spec 0.11.3, suite 0.11.3 — 2026-10-25

The specification and the conformance contract are now in English; no rule changed and the suite is unchanged (233 cases). Section numbers, identifiers, wire text and the Δ markers are the same, so references from ports and cases still hold. The version tools and the release workflow read the English headers (`| **Version** |`, `| **Suite version** |`). Fixture labels keep their Polish names: they are test data of Polish installations.

## Spec 0.11.2, suite 0.11.2 — 2026-10-25

Two rules the Swift port's review and a new toolchain showed to be missing.

Evidence: in the Swift port a report that arrived while `stop()` was draining its sends still reached the session and set the diagnostic times `stop()` had just cleared (review finding); and the project-file JSON parser accepted 512 levels of nesting, which a crafted `.omp` used to overflow the stack of a secondary thread in a debug build before the limit was reached (found when the test suite first ran under Swift 6.4).

Spec:

- §5.4.5: a `stopped` session accepts no datagram — nothing that arrives during or after the teardown changes the state, the store or the diagnostic snapshot.
- §4.5.1 pt 5: nesting of a node's JSON beyond the port's limit is `invalidData`; the limit is between 32 and 1024 levels (real nodes have about ten), and the parser may not depend on a stack that grows with the input beyond it — ten thousand levels end in `invalidData`, not a crash.

Suite (233 cases; 14 project cases on 11 fixtures): `provisioning.omp.deep-nesting` (a node of 10 000 nested arrays). The `.omp` oracle rejects nesting beyond 64 levels before parsing.

## Spec 0.11.1, suite 0.11.1 — 2026-10-22

Wording of the three 0.11.0 rules, after a review (Codex, 8 findings, all taken). No rule changed; the suite is unchanged (232 cases; 51 session scenarios).

- §6.5: the entity signal guarantee now says when it holds (per active subscription, from its activation, while the subscriber keeps receiving), that the store already holds the write when the signal is observed, and that a change after a delivered signal needs another one.
- §5.5 Δ: (c) states the property — other keys' changes cannot lose a pending notification of a subscribed key, over the whole delivery path — instead of prescribing where to filter; (b) says the raw stream guarantees no complete history rather than calling it lossy.
- §5.4.5: the snapshot's fields are defined one by one — last reply counts at correlation (even if the payload then fails the chunk), last notification counts only accepted reports (a registration reply is not one), requested key count is the full list of the last started `register`, the re-registration flag's set and clear conditions, the timeout streak without a socket and after `stop()`; synchronous access is no longer required, a single snapshot is consistent, and idempotent `start()` or a socket swap does not reset the times.
- §2: helper modules may depend on any subset of the four, a simulation module depending on Devices alone is an example, UI stays outside Core (§1.2).
- 0.11.0 changelog, corrected: the old §5.5 Δ (b) recommended a bounded newest-wins buffer, not its size nor filtering after it; the Swift port lost entity signals when the producer outran the consumer; the diagnostic times are also cleared by `stop()`.

## Spec 0.11.0, suite 0.11.0 — 2026-10-22

Three rules the Swift port's API review (Codex) showed the spec did not state. Text only: no profile covers store events or module layout, so the suite is unchanged (232 cases; 51 session scenarios).

Evidence: in the Swift port, the per-entity update signal was a filter on top of the shared change stream, whose bounded buffer (64 newest) is filled by a single registration reply of more keys than that — an entity whose key came early in the list never got its signal and its view stayed stale until the next change of the same value; §5.5 Δ (b) recommended exactly that buffer and nothing required the signal to survive. The port's device module had also come to depend on the provisioning module (for a "download and start" convenience), against the table in §2. And a health read-out for apps needed one definition of "last reply" so that ports agree.

Spec:

- §6.5: an entity's update signal is a "changed" flag, not a history — changes may coalesce, but at least one signal follows every change of any of the entity's references, however many other keys changed meanwhile. §5.5 Δ: a key-filtered subscription filters before buffering; the raw change stream is lossy and never replaces reading the store.
- §5.4.5: a session exposes a synchronous, immutable diagnostic snapshot in every state (state, sampling time, last accepted correlated reply, last accepted notification, last ping round-trip, requested key count, whether a re-registration is pending, the consecutive-ping-timeout streak of the current socket); times restart with `start()`; snapshots of several CLUs are not one consistent picture.
- §2: a port may add a fifth umbrella module that depends on all four and holds cross-boundary conveniences and re-exports; no rule of §3–§6 lives there, and the four modules never depend on it.

## Spec 0.10.1, suite 0.10.1 — 2026-10-22

Two gaps a test audit of the Swift port found: a rule the port followed that the spec did not state, and leftovers no scenario could see.

Spec:

- §5.4.5: a registration with a failed chunk leaves the session to be recovered even while it stays `ready`; the next answered ping registers the whole list again (values through the report path), until a registration has no failed chunk. A failed chunk never changes the state; only pings make a session `degraded`.
- conformance.md §4b: a scenario ends with an implicit `expectQuiet` and `expectNoReport` before the runner stops the session, so a datagram or report no step checked fails the run; the stop's own `clientDestroy` comes after that check.

Suite (232 cases; 51 session scenarios): `a-failed-chunk-is-re-registered-by-the-next-ping`.

## Spec 0.10.0, suite 0.10.0 — 2026-10-21

The `.omp` import now converts every widget type of §3.5, plus the new `TV_REMOTE_CONTROL`, and imports widgets never sent to a phone.

Evidence: a second interface of the same project with one widget of each type that was not yet confirmed, saved after Object Manager sent it to a phone, against the interface downloaded for it. The converter reproduces it field for field (10 widgets, the CLUs, the key). Rules settled by the comparison: CAMERA's `cameraStream.url` becomes the component's `value`; LED is its bistable button followed by the hue, saturation and brightness sliders, each slider's `min`/`max` becoming `range`; contact sensors and the slider use the widget `object` like the V2 widgets (their `clickAction` template drops out); the multisensor's seven channels keep only existing references, and the editor spells the VOC channel `objectAirVco` while the mobile interface has `objectAirVoc`; the legacy roller shutter's `buttonBistableClick` becomes a `BUTTON_BISTABLE` component that keeps `indication`. A new type, `TV_REMOTE_CONTROL` (a TV remote: `vodLabel`, `icon`, `label` and an `object` of button actions), exists in Object Manager; Core does not map it.

Object Manager assigns a widget's `dtoId` only when it sends the interface to a phone, and writes it into the project only when the project is saved afterwards: a project saved before the first send has no `dtoId` on its new widgets. Under 0.8.0 the import skipped them. Now `omp-` plus the decimal text of the editor's integer `id` stands in (the prefix keeps it from ever equalling a real `dtoId`); the id, and with it the device and entity ids, changes once the interface has been sent and the project saved. Two widgets with one id after conversion fail the import.

Spec:

- §4.5.6: the conversion of `CAMERA`, `LED`, `CONTACT_SENSOR`, `CONTACT_SENSOR_DOUBLE`, `SLIDER`, `MULTISENSOR`, `ROLLER_SHUTTER` and `TV_REMOTE_CONTROL` (new structures S, R, K); the stand-in id and when it applies; duplicate widget ids → `invalidData`; stubs keep the id.
- §3.5: `TV_REMOTE_CONTROL` is known and not supported, like `EVENT_SCHEDULER`.

Suite (231 cases, expectations re-derived): `provisioning.omp.select-by-id` gains a third page with every newly confirmed type, a `TV_REMOTE_CONTROL`, a widget without `dtoId`, an LED missing a slider, an LED whose slider kinds are swapped (parsed as a union by `type`), and two widgets parsing skips (a roller whose `indication` is not a string, a camera whose component is not `CAMERA_STREAM`); new `provisioning.omp.duplicate-widget-id`. The oracle's approximation of §3.6 checks the fields and component types §3.5 names for the new types, per component kind. Reviewed by Codex; its 5 findings are reflected here.

## Spec 0.9.1, suite 0.9.1 — 2026-10-21

Lifecycle rules a port could otherwise get wrong, found by a code audit of the Swift port.

Evidence: in the Swift port, `ping()` before `start()` opened the socket and made the session `ready` without any maintenance loop, after which `start()` was a silent no-op; a CLU that stopped answering with several chunks registered spent N × 5 s in timeouts, so the 30 s loop fired back-to-back while the ping-loop recovery registered the same keys again; and a transport that lost its network could hold the socket in a state where sends never finish, so that no ping ever replaced it.

Spec:

- §5.4.5: `ping()` and `register()` in `idle` or `stopped` send nothing, fail (`register` with `failed` slots) and remember nothing; only `start()` opens a session.
- §5.4.5: three consecutive unanswered pings close the main socket (pending requests end with `ConnectionClosed`); the next ping opens a new one, and the first successful ping recovers the subscriptions on it. The streak belongs to the socket: it counts pings on the wire (the initial one of `start()` included, once per attempt whoever waits for it) since the socket opened or the last reply, and a new socket starts at zero (review finding).
- §5.4.1 pt 3: the response timeout bounds the whole attempt from the hand-over to the transport, whether or not the transport ever finishes the send, and retiring a socket waits at most one timeout for stuck sends (review finding; the scenario vocabulary has no stuck-send step, so this is text only).
- §5.4.4: the re-registration loop starts no renewal while the session is `degraded`, and a deadline missed there is not caught up; a renewal already running finishes its chunks (review finding). The recovery after a successful ping registers and restarts the wait.

Suite (230 cases; 50 session scenarios): `ping-before-start-sends-nothing`, `register-before-start-yields-failed-slots` (idle and stopped), `three-ping-timeouts-replace-the-main-socket` (with a subscription recovered on the new socket), `ping-timeouts-count-per-socket`, `no-loop-re-registration-while-degraded`, `a-running-renewal-finishes-after-degrading`.

## Spec 0.9.0, suite 0.9.0 — 2026-10-21

Report parsing that respects quotes and nesting, the one-shot read `fetchValues`, and notes from the mobile app.

Evidence (one installation, two CLU firmware generations; an analysis of the behaviour of the official Android app 1.17.8, a packet capture of the iOS app, and live probes in the Swift port's `Examples/probe-register`, phases J and K):

- The app splits a report on commas outside quotes and keeps a nested `{…}` block whole; the reference splits on every comma, which the 0.8.0 text had copied as an unfixable limitation. Only a quote inside a string stays unrecoverable.
- `SYSTEM:fetchValues({keys})` is one of the three `SYSTEM:` commands the app has (with `clientRegister` and `mqttRegister`; it has no `clientDestroy`). The app uses it to read one widget's values when a schedule editor opens. Live, on both firmware generations: the reply is `values:{…}` with no subscription number, 30 keys answer, an unknown object gives `nil`, an empty list gives the malformed `values:}`, no notification follows for 20 s, and a second read after a wall switch returns the new value.
- The app sends numeric action values bare, with a space after the comma; the reference quotes them. Live, on a dimmer method: `execute(3,"120.0")`, `execute(3, 60.0)`, `execute(3,"90")`, `execute(3,150)`, `"33.33"` and `66.66` are all accepted with the same `nil` reply, the value lands the same way and fractions are truncated by the CLU; `set` on the method's index replies `nil` and changes nothing.
- The app waits 5 s for a ping and 15 s for a command; live, actions answered within tens of milliseconds.
- The app reads `pushNotifications` to match FCM pushes whose `encryptedData` it decrypts with the interface key; its cloud mode is MQTT with the same frame bytes, authenticated by a certificate shipped in the app.
- The iOS and Android apps differ on the wire (empty vs filled ip field, msg_id case, subscription number 1 vs a constant, ~31 vs 40 s renewal) and the CLU accepts both; none of this changes the session rules of 0.7.0.

Spec:

- §5.3.2 pt 2–4: elements are split on commas outside quotes and outside nested braces; a nested block is one string element; an unclosed string, an unclosed inner brace or a closing brace at depth 0 rejects the payload, while a third part without its outer `{` or `}` is an empty list as before. §10 pt 2 narrowed to the quote. (Review findings: reversed nesting and a missing outer brace were ambiguous.)
- §5.3.2 (new paragraph): `SYSTEM:fetchValues({keys})` → `values:{…}`; at least one key, the registration frame limit, `nil` for an unknown key, no subscription.
- §5.4.2 (new paragraph): `fetch(keys) → slots`, chunked and sequential like `register`, serialized with the other operations, touching neither chunks nor loops; without a main socket it sends nothing and yields `failed`. §5.4.5: `stop()` during a fetch fails the chunk in flight and the unsent ones, keeps the slots already read, and sends nothing more (review finding).
- §5.3.4 Δ: the app's unquoted values; Core keeps the reference form; `set`/`execute` are decided by `callType`. §10 pt 3: a quote around a number is irrelevant to the CLU.
- §5.4.1 pt 3 Δ: the app's 15 s command timeout; Core keeps 5 s.
- §5.3.5: a `fetchValues` exchange in the example.
- §10 pt 8: what `pushNotifications` and the cloud mode are, and why both stay out of scope.

Suite (224 cases; 44 session scenarios):

- `payload.report`: `comma-inside-quotes`, `nested-braces`, `reject-unbalanced-quote`, `reject-unbalanced-brace`, `reject-reversed-braces`, `missing-closing-brace`;
- new operations `payload.fetch` (2 cases) and `payload.values` (5 cases: a list, `nil`, quotes, no prefix, the malformed empty reply); requirement ids `payload.fetch`, `payload.values`, `session.fetch`;
- `session.scenario` gains the `fetch` call with a `{slots}` result; scenarios: a fetch answered and not followed by a report, a timeout and a client report in place of `values:` both failing the slots, a fetch between registrations leaving session 1 and its 30 s wait alone, a fetch before `start()` sending nothing, a fetch split by a 50 B frame limit with its second chunk unanswered, and `stop()` during a fetch failing the rest without sending it; `tools/suite.py check` holds fetch requests to the greedy split as it does registrations;
- the key list schema is a named definition (`subscription.keys`) shared by `payload.register` and `payload.fetch`.

## Spec 0.8.0, suite 0.8.0 — 2026-10-20

Import of the mobile interface from an Object Manager project file (`.omp`), for the widget types whose editor format is confirmed.

Evidence (one installation; its project file and the interface downloaded from Object Manager for the same project): an `.omp` holds every mobile interface of the project in Object Manager's editor format (`nodes/{n}/data.json`), plus the project key (`properties.xml`) and the CLUs and objects (`system.xml`). A converter written from these rules reproduced the downloaded `data.json` field for field — 18 pages, 254 widgets of 11 types, 5 CLUs, the key, the push objects — except `version`, an export counter the project does not store. The same converter ran on 15 project files saved by Object Manager 1.10.1, 1.11.1 and 1.12.1 without an error and with every widget accounted for. Details the comparison settled: the CLU list holds only the CLUs the interface uses, in `system.xml` order; `serialNumber` is the hex serial from `system.xml` written in decimal (it can differ from the number in the CLU name after a CLU swap); `theme` and scene colours are upper-cased; `PSEUDO_METHOD` actions become `ATTRIBUTE`; action fields of the V2/V3 widgets gain an `Action` suffix; references and actions without an object are editor templates and are dropped; a push object's type is `PUSH-{typeId in decimal}-{typeVersion}`. Unpacked, a real project is about 16 MB, `system.xml` alone 6.5 MB.

Not yet confirmed (no project with them was available): `LED`, `CONTACT_SENSOR`, `CONTACT_SENSOR_DOUBLE`, `SLIDER`, `MULTISENSOR`, `ROLLER_SHUTTER`, `CAMERA`. Until their editor format is seen, the import passes them on as `{id, type}`, which parsing skips with a diagnostic.

Spec:

- §4.5 (new): the archive and its limits (64 MiB per used entry and in total, 10 000 entries, recommended; no DTD in any encoding; strict JSON; duplicate entry names, element ids or interface ids rejected), XML references (transitive; dangling or cyclic rejected), text trimming and the hex grammar, choosing an interface (by id; without an id only when there is exactly one), error precedence, the root fields (`version` is 0), CLUs (document order, only the ones used after conversion), push objects (strict ancestor; incomplete entries skipped), and the conversion of pages and the 11 confirmed widget types with exact required, copied and dropped fields. Reviewed by Codex; its 15 findings on ambiguity and malformed input are reflected in this text and the suite.
- §8: `ProvisioningError.interfaceNotFound` and `interfaceAmbiguous`.
- §1.1, §2: the second Provisioning source.

Suite (205 cases):

- new profile `project` and operation `provisioning.omp` (conformance.md §4c): the interface list, the root of the chosen interface with the `{id, type}` of every widget that parses, and the `mapping.interface` projection of the import;
- 12 cases on 9 synthetic `.omp` fixtures: every confirmed type and its edge cases (an explicit `…Action` winning over a bare name, a CLU used only by an action, one used only by a stub and so not listed, document order against first use, a serial read through an XML reference, a push node reached twice and listed once, archive order of the interfaces), the second interface (`LOCAL_ONLY`), selection without an id (none, one and two interfaces), an unknown id, and broken archives (not a ZIP, no `system.xml`, a `DOCTYPE` in UTF-8 and in UTF-16, invalid JSON in a node that is not the interface, a non-finite number);
- fixtures are built by `tools/suite.py fixtures` from readable sources in `conformance/sources/projects/` (an `ORDER` file sets the entry order); `check` compares every archive with its sources (names, order, content), re-derives every expectation with `tools/oracles/omp.py` (written from §4.5) and the mapping oracle with type-aware JSON equality, and rejects fixtures whose converted widgets break the §3.5 enumerations;
- the mapping oracle reports a widget marked as not parsing as skipped before the `HEADER` rule, so a malformed `HEADER` is reported like any widget §3.6 skips (no existing case changes);
- new error codes `provisioning.invalidData`, `provisioning.interfaceNotFound`, `provisioning.interfaceAmbiguous`.

## Spec 0.7.0, suite 0.7.0 — 2026-10-19

One socket per session, chunks as subscription numbers, re-registration every 30 s, `clientDestroy`.

Evidence (one installation, two CLU firmware generations, 2026-10-01 and 2026-10-19 — probe program in the Swift port's `Examples/probe-register`, a packet capture of the official mobile app, and two third-party clients run or read against the same CLUs):

- The arguments of `clientRegister` are `(ip, port, session, keys)`. `0,0` means "report to the sender's address and port"; the number given as `session` is echoed in the reply and in every notification; two sessions registered from one socket both push and are distinguishable; re-registering the same `(ip, port, session)` replaces the key list; `clientDestroy(0,0,session)` stops the pushes. The 0.6.0 text read the second argument as a "session id that must be 0" and the number in a notification as unreliable — both were wrong, and the socket-per-chunk layout rested on that reading.
- A registration pushes for about 56–60 s and then stops; pings do not renew it, only a registration with the same triple does. The reference's 45 s interval therefore leaves one renewal per lease. The official mobile app re-registers every ~31 s on one socket per CLU with the same triple; a third-party hub does the same every 30 s and documents that a lost renewal left wrong state for days.
- With new sockets on every renewal and no `clientDestroy` (the 0.6.0 layout), each old registration kept pushing to a closed port for the rest of its lease — a third of all pushes wasted at 45 s, all of them doubled at 30 s.
- The size limit of a registration is in bytes, not keys. A binary search on one CLU stopped at the same edge in three series — 129 short keys, 71 long keys, 115 interface keys: a 1472 B datagram (frame ≤ 1471 B) is answered, a 1488 B one is not. 1472 B is the UDP payload of one IP packet at MTU 1500; that CLU generation does not reassemble IP fragments. A CLU of the other generation answered 9200 B datagrams (fragmented), the most the test host could send. Thirty long variable names already exceed 1471 B, so a key count never was a safe limit. Duplicate keys in one registration are accepted; replies and pushes larger than one packet arrive fine.
- With `ip = 0` and a non-zero `port`, one firmware generation sends no notifications at all and the others send them to the source port; only `0,0` behaves the same everywhere. Measured on six CLUs of three firmware generations.
- `clientDestroy` replies with the subscription number, and `nil` when there is no such subscription (seen after the lease had already run out).
- CLUs drop registrations sent as a burst; chunks already go one after another.

Spec:

- §5.3.1: a ping does not renew the subscription.
- §5.3.2: `clientRegister(0,0,{session},…)`, the triple semantics, why only `0,0`, the ~1 min expiry, the size limit (frame ≤ 1471 B instead of 30 keys), and `clientDestroy(0,0,{session})` sent without waiting for a reply (what its reply means is noted). The number in a registration reply is still not checked (the reply is correlated by `msg_id`).
- §5.3.3, §5.4.3: a notification carries the session number; the report path routes by it and drops an unknown number or a wrong value count.
- §5.4.2 pt 1: chunks are split by frame size — each chunk is the longest run of keys whose frame, with the number counted as six digits so the split does not depend on numbering, fits the limit (default 1471 B; a port may lower it, never raise it); a key larger than the limit is a chunk of its own and is sent anyway, its result decided by the CLU like any chunk. Numbers are capped at 999999 to match the six-digit reservation (otherwise a long run could push an unchanged list over the limit; review finding); needing 1000000 destroys every chunk and starts over on a new main socket, so no number returns with another list on the same port.
- §5.4, §5.4.2: one socket; every chunk is registered from the main socket under its own subscription number. A chunk keeps its number only while its key list is unchanged; a changed list gets a fresh number from a per-start counter and the old number is destroyed, because reusing a number with other keys would let a late notification of the old list, or a lost request with the new one, land values on the wrong keys when the counts match (review finding). A chunk remembers its keys from the moment its request is sent; numbers no longer used → `clientDestroy`; `register([])` destroys all.
- §5.4.4: re-registration 30 s after the previous one was *started* (its first chunk sent), same numbers, same socket. Counting from the start keeps every chunk about 30 s from its own previous registration however long the sequence took (counting from the end let a slow eight-chunk sequence renew chunk 1 after the lease; review finding). 30 + 5 s timeout + 5 s ping recovery fit before the ~56 s expiry.
- §5.4.5: `stop()` destroys every remembered chunk before closing the socket; a new main socket after a failure means new subscriptions.
- §7.1, §7.5, §9.1, §9.4, §10 pt 4 (the byte limit) and 6 (pt 7, the expiry, is new; later points renumbered), §11: wording follows.

Suite (38 session scenarios, 193 cases in total):

- renamed and rewritten for the main socket: `register-one-chunk-as-session-1`, `notifications-are-routed-by-session` (unknown number and wrong count dropped), `re-registration-reuses-the-socket-and-session`, `re-registration-runs-30-s-after-the-previous-one`, `a-registration-restarts-the-30-s-wait`;
- second chunks are session 2 (`chunks-are-registered-one-after-another`, `unanswered-chunk-yields-failed-slots`); `loops-end-with-stop` and `stop-during-registration-fails-the-chunk` expect the `clientDestroy`; the remaining scenarios only change socket labels or 45 s → 30 s;
- `registrations-do-not-interleave` and `a-registration-restarts-the-30-s-wait`: the second, different key list is session 2 and session 1 is destroyed;
- new: `a-changed-key-list-gets-a-new-session`, `fewer-chunks-keep-their-numbers-and-destroy-the-surplus`, `a-notification-for-a-replaced-number-is-dropped`, `stop-destroys-the-sessions`, `empty-registration-destroys-all-sessions`;
- `payload.register`: the input's `sessionId` is the third argument; expected payloads updated.
- conformance.md §4b: `session.scenario` takes an optional `registerFrameLimitBytes`; omitted means the default 1471 B. New `default-frame-limit-fits-1471-bytes` and `default-frame-limit-splits-at-1472-bytes` test the default to the byte; the five other multi-chunk scenarios set a 309 B limit so that exactly 30 of their keys fit, and their expectations are unchanged.
- `tools/suite.py check` re-derives chunking: it splits the last registered key list greedily from the first key and requires every expected `clientRegister` to be the next chunk of that split, or chunk 1 when a new registration starts (the first version matched any run anywhere, rejecting valid duplicate keys and accepting overlapping chunks; review finding). `registerFrameLimitBytes` is checked against 1…1471 in the schema and the checker; the stdlib schema checker learned `maximum`.

## Spec 0.6.0, suite 0.6.0 — 2026-09-30

- §5.4.4, the maintenance loops, is now precise:
  - the next loop ping goes 5 s after the previous loop ping ends (reply or timeout);
  - re-registration runs 45 s after ANY registration ends: explicit, recovery or the loop itself;
  - the ping loop starts when `start()` ends, even when the session is `degraded`, and both loops end with `stop()`;
  - the loop's re-registration values reach the upper layer through the report path;
  - the intervals are defaults that a port may make configurable.
- conformance.md §4b:
  - `session.scenario` takes optional `pingIntervalMs` and `registerIntervalMs`. An omitted interval means the port's default;
  - `advance` jumps in one step, and a scenario crosses at most one deadline per timer and never two timers at the same instant;
  - the scenarios that do not test the loops set both intervals to an hour.
- New requirement id `session.loops`, and six loop scenarios (31 in total):
  - ping cadence counted from completion;
  - the ping loop retries a degraded session;
  - re-registration 45 s after completion, with a report;
  - an explicit registration restarts the wait;
  - no re-registration without keys;
  - nothing sent after `stop()`.

## Spec 0.5.1, suite 0.5.1 — 2026-09-30

Fixes from the review of the `session` profile.

- §5.4.5: `execute` without an open main socket (`idle`, `stopped`, or `degraded` after the socket was lost) fails with `SessionError.notConnected` and sends nothing. A registration cut short by `stop()` ends like a failed chunk. The suite already expected both; the spec text did not say so.
- §5.4.1 pt 4: a main-socket failure outside `stop()` makes the session `degraded` without a main socket, and the next ping opens a new one.
- conformance.md §4b:
  - the barrier includes report delivery to the stream the runner subscribes to before the first step;
  - `expectQuiet` and `expectNoReport` watch a quiet window of at least 200 ms of real time. The text says plainly that this is a probabilistic guarantee;
  - new step `fail` (the transport reports a socket failure);
  - `advance` takes ms > 0;
  - a runner rejects a step it does not recognise.
- `tools/suite.py check` validates every case against `case.schema.json` with a stdlib checker for the subset of JSON Schema the file uses. The schema and the cross-step checks no longer disagree (`advance: 0`).
- Six new scenarios (25 in total):
  - idempotent start/stop;
  - start after stop;
  - register waits for start;
  - registrations do not interleave;
  - stop during registration;
  - main-socket failure.
- Known gap: the maintenance loops of §5.4.4 (ping every 5 s, re-registration every 45 s) are not covered yet.

## Spec 0.5.0, suite 0.5.0 — 2026-09-30

- New conformance profile `session` (conformance.md §4b). The operation `session.scenario` runs one CLU session against a CLU played by the runner, step by step, on a virtual clock. It has 19 scenarios covering request/response correlation, keep-alive, sequential chunked registration with sid 0, report routing by socket, lifecycle and recovery, and actions with their error codes.
- New requirement ids: `session.correlation`, `session.chunking`, `session.report-routing`, `session.keepalive`, `session.lifecycle`, `session.actions`.
- `case.schema.json` describes the `session.scenario` input and its steps. `tools/suite.py check` also checks cross-step rules: awaits refer to earlier calls, socket labels and `$` captures are bound before use, and every call is awaited.
- §5.4.1 pt 3: a reply that arrives after its timeout has no pending entry and is dropped.
- §5.4.5: the values from the recovery re-registration reach the upper layer through the report path, and a failed chunk contributes `failed` at its positions.

## Spec 0.4.6, suite 0.4.5 — 2026-09-30

- `manifest.json` carries `requirements`, the requirement ids with their spec sections, so a runner can validate `specRefs` without parsing prose.
- conformance.md §5: the runner rejects a case whose `id` differs from its file name, a case outside `cases/`, and unknown `specRefs`.
- §9 no longer names a suite version ("0.4.0" had gone stale). It points to conformance.md and the manifest.
- conformance.md: the profile table column is "Od wersji zestawu" (since which suite version), and the `port.json` example no longer hard-codes a version.

## Spec 0.4.5, suite 0.4.4 — 2026-09-30

- §3.3, §8: an action whose `value` is exactly `$value$` fails with `PayloadError.unresolvedPlaceholder` when its payload is built, so nothing is sent. This also applies to low-level raw execution. Before, the rule only said such an action "must not be executed", and a port's raw path sent the placeholder literally.
- New case `payload.action.error-unresolved-placeholder` and error code `payload.unresolvedPlaceholder`.
- conformance.md §5: the runner validates each case's shape and enforces the port's declared profiles. Cases of undeclared profiles are reported, not counted as passed.
- conformance.md §8 is no longer titled "0.1.0". `tools/check_versions.py` also checks the conformance.md version header, which was left at 0.4.3.

## Spec 0.4.4 — 2026-09-30 (suite unchanged, 0.4.3)

- The spec text states rules, not a verification log.
  - Dates, runs, counts and "verified live" notes are removed from §5.3.2, §5.4.2, §6.2, §6.3.12, §6.6 and §10. Their evidence lives in this changelog and in commit messages.
  - Short rationales stay where a rule would otherwise look arbitrary.
- The open thermostat questions (modes 1/2, writing Mode directly) move to §10 as known limitations.

## 0.4.3 — 2026-09-30

- Removed identifiers of the installation used for live testing from the spec text and fixtures. That covers CLU names, object names, labels and counts. The evidence stays, described generically. Fixture labels and object names are now synthetic.

## 0.4.2 — 2026-09-30

- **§5.4.2: subscription chunks are registered one at a time, not in parallel (Δ vs the reference).**
  - With parallel chunks, the two CLUs needing 4–5 chunks timed out on some of them in every live run, while single-chunk CLUs never did.
  - With sequential chunks there were no timeouts in three consecutive runs.
- No case changes. The session profile that would test this does not exist yet.

## 0.4.1 — 2026-09-30

- **Fix, §6.2 `boolFromOnOff`:** the number 1 (`int` 1, `double` 1.0) now means on.
  - The reference's `value == True` is also true for 1 and 1.0 in Python.
  - The spec had translated it as "boolean true only", so every relay reporting `1` read as off.
  - Found live on a switched-on light.
- Corrected cases `int-one` and `double-one`. New cases `int-zero`, `int-two`, `double-one-and-a-half` and `string-one`.
- `tools/suite.py check` now re-derives both coercions with the reference's own Python expressions. The wrong cases had been derived from the spec text alone, with no oracle to catch them.

## 0.4.0 — 2026-09-30

- New §6.6, physical objects. Devices stay per widget, and Core also groups them by the CLU object each entity operates.
  - The key is `{cluId, kind: object|variable|script, name, index}`. The feature index keeps the channels of one LED module apart.
  - Every object lists the widgets that show it, each with its name, page and entities.
  - Motivation: in a live installation every thermostat and most shutters appear on two pages under different names.
- Devices carry `page` (`{index, name}`).
- The mapping projection gains `physicalObjects` and `devices[].page`. All mapping cases are re-derived.
- New case `shared-objects`.

## 0.3.0 — 2026-09-30

- New widget types, modelled on a live Object Manager export:
  - `THERMOSTAT_V2` → `thermostat` device (§6.3.12). Feature semantics follow Grenton's Thermostat manual.
  - `SCENE_DOUBLE` → `scene` device. Entities are suffixed by list position because both components carry `rowId` 0.
- `HEADER` is a known layout widget: it produces no device and is not reported as skipped. This is a behaviour change from 0.2.0.
- `EVENT_SCHEDULER` is documented as known but unsupported.
- §3.3 documents the `$value$` placeholder in exported set actions.
- New mapping case `thermostat-scene-double-header`. The `widget-tolerance` fixture uses `FUTURE_WIDGET_V9` as its unknown type.

## 0.2.0 — 2026-09-29

- New conformance profile `mapping` with 4 cases on 4 interface fixtures:
  - every widget type
  - completeness rules
  - widget-level tolerance
  - root-level rejection
- Expected values come from `tools/oracles/mapping.py`, written independently from the spec.
- Spec §6.1 now defines, as normative:
  - device fields `id`, `widgetType`, `kind`, `name`
  - the device-kind table
  - the name rule
  - device and entity order
- Spec §3.6: skipped widgets must be observable as per-page type lists, and a root-level error rejects the whole interface.
- Spec §6.3.1: when there are several ON/OFF actions, the first one wins.
- Spec §6.3.9: the first component must have an action.
- Manifest lists fixtures with SHA-256.

## 0.1.0 — 2026-09-28

- Specification moved here from `grenton-core-swift`.
- Spec clarifications, all matching the reference implementation:
  - §3.1: every leading and trailing quote is stripped.
  - §4.1: the PIN whitespace set and path-segment encoding are defined, and a URL-building failure maps to `provisioning.connection`.
  - §5.3.5: the example exchange uses sid 0.
- Conformance suite 0.1.0, `stateless` profile, 144 cases:
  - AES-CBC
  - frames
  - register, action and report payloads
  - value casting
  - Object Manager URL and PIN
  - coercions
  - number formatting
