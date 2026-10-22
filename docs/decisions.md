# Decisions and the measurements behind them

The spec states rules; this file keeps the reasons that would otherwise
live in someone's head or in a session transcript. Dates are when the
measurement was made. Nothing here names a real installation's objects,
CLUs or addresses. Newest first within each section.

## The CLU protocol, as measured

Measured on one installation with CLUs of two firmware generations (the
5.78 series and the 2.03 series), with the probe program in the Swift
port (`Examples/probe-register`), packet captures of the official apps,
and two third-party clients run or read against the same CLUs.

- **`clientRegister(ip, port, session, keys)`** (2026-10-01). `0,0` means
  "report to the sender's address and port"; the number given as `session`
  is echoed in the reply and in every notification; two sessions
  registered from one socket both push and are told apart by the number;
  re-registering the same `(ip, port, session)` replaces the key list;
  `clientDestroy(0,0,session)` stops the pushes and answers with the
  number (`nil` when there is no such subscription). The explicit-address
  form `("ip", port, …)` works on both generations; `(0, port≠0, …)`
  pushes to the sender on 5.78 and nowhere on 2.03 — the form the
  reference integration's unreleased branch sends, which is why it gets
  no pushes.
- **Lease** (2026-10-01, 2026-10-19). A registration pushes for about
  56–60 s after it is made and then stops, with or without pings on any
  socket; only a registration with the same triple renews it. The same
  holds for the explicit-address form. Hence re-registration every 30 s
  (spec §5.4.4), counted from the start of the previous registration.
- **Registration size** (2026-10-01). A registration of 143 keys gets no
  reply at all; 30 works, 86 is accepted by the reference's layout. The
  spec limits the frame to 1471 B (one IP packet at MTU 1500), not a key
  count.
- **Bursts** (vCLU changelog, 2026). A CLU drops `clientRegister` packets
  sent back to back; chunks go one at a time, each after the previous
  reply or timeout.
- **`fetchValues`** (2026-10-20). `SYSTEM:fetchValues({keys})` answers
  `values:{…}` with no subscription number, `nil` for an unknown object,
  the malformed `values:}` for an empty list, and creates no
  subscription (no push for 20 s; a second read after a wall switch
  returns the new value). Same on both generations.
- **Action values** (2026-10-21, a dimmer's method). `execute(3,"120.0")`,
  `execute(3, 60.0)`, `execute(3,"90")`, `execute(3,150)`, `"33.33"` and
  `66.66` are all accepted with the same `nil` reply; the value lands the
  same way and fractions are truncated by the CLU. `set` on a method's
  index replies `nil` and changes nothing: `callType` decides, quoting
  does not. Core keeps the reference's quoted form.
- **Report values** (app analysis, 2026-10-20). The official app splits a
  report on commas outside quotes and keeps a nested `{…}` block whole;
  the reference splits on every comma. Spec 0.9.0 adopted the app's
  reading; only a quote inside a string stays unrecoverable.

## What the official apps do on the wire

- **iOS app** (packet capture, 2026-10-19): `req::<8 HEX UPPERCASE>:…`
  with an empty ip field; one socket per CLU for pings, registrations and
  actions; a ping tick every 5 s to every CLU with a constant msg_id per
  socket; re-registration every 6th tick (≈31 s) with the same triple on
  the same socket; only the keys of the screen on show are registered
  (8–20 per CLU, one register per CLU); no `clientDestroy` ever, not in
  the background, not on close — the CLU keeps pushing to a closed app
  until the lease ends (last push 59.8 s after the last register); a
  resume or a cold start opens new sockets; actions `OBJ:set(0,1)`, bare
  number, fresh lowercase msg_id, reply `nil`. A separate frame type
  `req_get_measurementkey::<id>` once per CLU after a start is the key of
  the cloud statistics service; 2.03 CLUs answer `nil`, 5.78 CLUs do not
  answer.
- **Android app 1.17.8** (static analysis with jadx, 2026-10-20; a
  different codebase from the iOS app): `req:<wlan ip>:<msgid lowercase>`,
  `clientRegister(0,0,69,…)` with a constant 69, re-registration every
  40 s on a new socket each time, ping 5 s with a 5 s timeout, 15 s
  timeout for commands, no retries, no `clientDestroy` anywhere, no key
  limit or chunking (2048-byte receive buffer), values unquoted,
  `fetchValues` used only by the schedule editors. Its cloud mode is MQTT
  (AWS IoT) carrying the same frame bytes, authenticated by a client
  certificate shipped inside the app; push notifications are FCM messages
  whose `encryptedData` is AES-encrypted with the interface key.

Both apps work against the same CLUs, so neither form is more official
than the other; the CLU accepts an empty or a filled ip field, upper- or
lowercase msg_ids, and any positive subscription number.

## Third-party clients looked at

- **grenton-native** (Python, HA integration, 2026-07): clean-room client
  keyed from the `.omp`; registers in the explicit-address form to a
  bound report port; never re-registers, so its subscriptions die after a
  minute (run live 2026-10-19: 59 pushes, then silence). Showed that the
  `.omp` is a usable source of the key and the CLU list.
- **vCLU** (Go hub that joins the project as a CLU, BSL 1.1 — facts only,
  no code): renews the same triple every 30 s on one report port,
  applies the renewal's reply to correct lost pushes, spaces renewals
  ≥50 ms apart after a production incident with bursts, batches 4 keys,
  never pings the real CLUs. Production evidence for the one-socket,
  same-triple design; our ping-driven recovery (≤5 s) is better than its
  wait-for-the-next-renewal.
- **Reference integration** (`homeassistant-grenton`): the origin of the
  wire formats and the device mapping; its unreleased branch sends a
  random second argument to `clientRegister` and therefore gets no pushes
  (reported upstream 2026-09-27).

## Out of scope, and why

- **Object Manager's discovery** (2026-10-21). OM finds CLUs with a
  broadcast encrypted with a key shipped inside Object Manager; the reply
  is a serial and a MAC; `SetKey` then pushes the project key. It yields
  only the CLU list — no objects, no interface, no key — and using that
  key would be the same as using the app's cloud certificate: a vendor
  secret, not interoperability. Not pursued. If CLU addresses ever change,
  sending `checkAlive()` with the project key to the subnet (broadcast or
  a scan) would find them without any vendor secret; whether a CLU answers
  a broadcast is untested.
- **Cloud mode and push notifications**: AWS IoT with the vendor's
  certificate, FCM through the vendor's service. Remote access is a VPN
  into the home network; the LAN path then works unchanged.
- **Reading the object list from a CLU** (`om.lua` over TFTP): unverified,
  and the `.omp` already gives more.

## Design choices in the spec

- One socket per session, chunks as subscription numbers, numbers kept
  only while a chunk's key list is identical (0.7.0): a late notification
  of an old list, or a lost request with a new one, must never land values
  on other keys when the counts match.
- Re-registration 30 s from the start of the previous registration (0.7.0,
  0.9.1): one lost renewal, its 5 s timeout and the ping-loop recovery fit
  before the ~56 s expiry; counting from the start keeps every chunk about
  30 s from its own previous registration however long the sequence took;
  no renewal starts while degraded.
- `clientDestroy` on stop and for numbers a new list no longer uses
  (0.7.0): the apps never send it and leave the CLU pushing to closed
  ports for a minute; the cost of sending it is one datagram.
- Three unanswered pings replace the socket (0.9.1): a transport that
  lost its network can hold a socket on which sends never finish.
- Action values stay quoted, the response timeout stays 5 s (0.9.0):
  measured as equivalent to the app's forms; the app's 15 s is a cloud
  allowance.
- Entity update signals are flags that other keys' changes cannot lose
  (0.11.0, 0.11.1): a bounded shared stream filtered afterwards lost a
  key's only change when the producer outran the consumer (a large
  registration reply does that), and a view then stays stale with no way
  to notice; the raw change stream promises no complete history, and the
  store already holds the write when a signal is seen.
- A port may add an umbrella module (0.11.0): the Swift port needed a
  home for "download and start" without making Devices depend on
  Provisioning, which §2 forbids.
- The `.omp` is the second provisioning source (0.8.0, 0.10.0): the
  converter reproduces the downloaded interface field for field on a real
  project; widgets never sent to a phone get `omp-<editor id>`.
