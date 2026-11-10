# Implementing a port: what the first port learned

The spec states the rules. `decisions.md` keeps the measurements behind
them. This file is for the person, or the agent, who writes the next port.
It lists the places where the first port (`grenton-core-swift`) went wrong,
or nearly did, even though the rule was already in the spec or was added
because of the failure.

Each item names the spec section, what happened, and how to test for it.
Several of these are not covered by the conformance suite (see the last
section), so a port that passes the suite can still have them.

## Values and parsing

- **The cast grammar is the spec's, not the language's** (§3.1). Library
  number parsers accept `inf`, `NaN`, hex floats, underscores, a leading
  `+` or locale separators, and §3.1 accepts none of them. An integer that
  overflows 64 bits falls back to double. *Test:* the `value.cast` cases,
  plus your parser's own extensions fed as wire text.
- **A double from the wire never traps on the way to an integer.** The
  Swift port converted a wire double with `Int(d)`. A value beyond Int64
  (which the cast yields as a double) crashed the process, and the crash
  was reachable from cover and thermostat entities. *Test:* feed `1e300`,
  `-1e300` and the largest double below 2^63 to every entity that reads a
  number.
- **Number formatting reproduces Python's `str(round(v, p))`** (§10 item 3):
  round half to even on the binary value, and precision 0 gives `"5.0"`.
  `printf("%.*f")` rounds the binary value too, but it drops the `.0`. *Test:*
  the `number.format` cases.
- **One bad widget is skipped, and its neighbours survive** (§3.6). A
  decoder that cannot step over a failed array element either aborts the
  page or misaligns the next widget. In the Swift port this needed an
  explicit skip of the element (Codable does not advance after a failed
  decode). *Test:* an unknown widget type and a widget with a missing field
  in the middle of a page. The `mapping` fixtures do this.
- **`.omp` node JSON is strict, and its depth is bounded** (§4.5). Duplicate
  keys, `NaN`, integers beyond Int64 and non-finite doubles are
  `invalidData`. Most standard decoders accept duplicate keys silently (the
  last one wins). A recursive parser overflowed the stack of a secondary
  thread in a debug build before it reached a depth limit of 512, so the
  Swift port uses 64 (the spec allows 32–1024). Pick a limit your parser
  survives on its smallest thread stack. *Test:* `deep-nesting.omp`, plus a
  duplicate-key node.
- **Report and `values:` payloads split outside quotes and braces**
  (§5.3.2), and a quoted Lua table (`"{…}"`) is read to its balancing brace.
  The 0.9.0 splitter cut a scheduler's rule list at its inner commas, and
  every value of that reply came out `failed`. *Test:* the `payload.report`,
  `payload.values` and `schedule.rules` cases.

## Provisioning

- **Size limits are enforced while decompressing** (§4.3, §4.5.1). An early
  check trusted the uncompressed size that the ZIP header declares. An
  archive that lies about it (a zip bomb) went straight through. Count the
  bytes the inflater actually produces, stop at the limit, and reject an
  entry whose final size differs from its header. Bound the number of
  entries. Match the entry path exactly, without normalizing it.
- **XML: no DTD, detected in the right encoding** (§4.5). Reject any
  `DOCTYPE`. Look for it after decoding the prolog in the encoding the
  parser itself would choose. A byte-level search misses a UTF-16 document
  (`utf16-doctype.omp`). Some parsers do not resolve entities but pass the
  declaration through as a token that is easy to ignore by mistake.
- **The PIN never appears in an error or a log** (§4.1). It is part of the
  URL, and many HTTP stacks put the URL into their error text. Go's
  `url.Error` and Swift's `URLError` both do. Rebuild transport errors
  without the URL. *Test:* a connection failure, then search the error text
  and the log for the PIN.
- **No redirects.** A 3xx is not followed. 401 is `authenticationFailed`,
  every other non-200 status is `connection`, and an unreadable body or
  archive is `invalidData`.

## Session

- **Exactly one reader per socket** (§5.4). One router decrypts, parses and
  dispatches every datagram: replies by `msg_id` to the pending
  requests, `clientReport`s by subscription number to the chunk table. No
  operation reads the socket itself. With two readers on one stream, each
  datagram goes to one of them, so the other one loses it.
- **Whole operations are serialized, not only mutations** (§5.4.5, §5.6).
  Making each state change atomic is not enough. When an operation can be
  suspended halfway (an actor awaiting a reply, a goroutine waiting on a
  channel), two registrations can interleave and mix up chunk numbers, and a
  `stop` can cut a `register` in half. Use a gate around `start`, `register`
  and `stop`.
- **Nothing before `start()`; nothing after `stop()`.** `ping()` or
  `register()` before `start()` once left a session that had no loops. A
  datagram that arrives during teardown must not bring times or values back
  into a stopped session. The suite has scenarios for both.
- **A dropped session cleans up after itself.** Swift needed teardown in
  `deinit` and weak references from the router back to the session, or an
  application that dropped a system without calling `stop()` kept a socket
  and a receive loop alive for good. In a language without destructors,
  make `Close`/`Stop` mandatory and documented, and test for leaked tasks,
  threads or goroutines after it.
- **Chunks are sent one at a time** (§5.4.2). Each chunk waits for the
  previous one's reply or timeout. CLUs drop `clientRegister` packets sent
  back to back (see `decisions.md`).
- **The loops count from the right instant** (§5.4.4). Re-registration runs
  30 s from the *start* of the previous registration, no renewal starts
  while the session is degraded, three unanswered pings replace the socket,
  and a failed chunk is recovered by the next answered ping in any state.
- **Every timer goes through the injected clock** (§7.4). One stray
  real-time sleep (a retry delay, a debounce) makes the session suite hang
  on a virtual clock that never moves, or flake when the clock is real.

## Transports

- **Any-source mode is one unconnected socket** (§7.1). The Swift port first
  combined a listener with a connected socket on the same port
  (`SO_REUSEPORT`). For datagrams from a foreign source, the kernel picked
  one of the two sockets nondeterministically; the connected one discarded
  them, and about 20 % of runs lost a datagram. Reports come back to the
  registration's source port, so sending and receiving must share one
  socket: bind an ephemeral port, `sendto` the CLU, and receive from anyone.
  *Test:* on loopback, deliver a datagram from a second socket and check
  that the any-source session receives it, and that the filtered one does
  not.
- **Local-network privacy on macOS** fails without a prompt for binaries
  that are not app bundles (for example, ones launched from an IDE or an
  agent's shell). UDP to the LAN fails with `EHOSTUNREACH` or "network is
  down", while HTTP to the same host and pings to the gateway still work.
  That signature means permission, not a bug. Run live tests from
  Terminal.app, or from an app bundle that can show the prompt. iOS needs
  `NSLocalNetworkUsageDescription`. Linux has no such gate.

## State and devices

- **An entity's update signal cannot be lost** (§6.5). The Swift port first
  forwarded a bounded, shared change stream into each entity and filtered
  it there. A large registration reply (a burst of other keys' changes)
  could push out a key's only change before the entity's task ran, and the
  view stayed stale with nothing to tell it otherwise. The fix is a flag per
  subscriber, set under the store's lock when that subscriber's keys
  change. A channel of capacity 1 with a non-blocking send has the same
  semantics. The store already holds the value when the signal is seen. The
  raw change stream promises no complete history; document it as best
  effort.
- **Getters are synchronous and cheap.** Applications read entity state
  from UI code. The store is a lock-protected map read by any thread, not
  something behind the session's serialization.
- **Command templates are immutable.** A command with a value works on a
  copy of the `ActionRef`, so a scene parameter does not leak into the next
  plain run.
- **Entity ids are `{widgetId}_{suffix}` exactly** (§6.1). Applications
  store their settings by entity id.

## API surface

- **Plumbing stays internal from the start.** Payload builders, frame codecs,
  the binder and range helpers were public in Swift until an audit hid them
  before 1.0. What applications used: the two interface sources, the system
  facade, devices and entities (getters, intents, update signals), session
  state and a diagnostics snapshot (§5.4.5), values by key, and the CLU list.
- **Applications want a simulation.** The first application on the Swift
  port (a menu-bar app) needed devices over values a test sets, with every
  command recorded, for its tests, its previews and a demo mode. The spec
  allows it as a helper module (§2).
- **Applications want the layout.** Sections (§6.1) and icons came from that
  application: the icon chosen in Object Manager is the only hint of what a
  relay drives.

## Testing the port

- **The conformance runner comes first.** In the Swift port it drove every
  milestone. Declare a profile in the port's declaration as soon as it
  passes, so a regression fails the run from then on.
- **The session barrier.** conformance.md §4b allows a 200 ms real-time
  quiet window for negative assertions, or a deterministic idle check. The
  Swift runner uses the window, which makes negative scenarios slow and only
  probabilistic. Design the session so that the runner can tell it is idle
  (an executor you control, a count of pending work), and the barrier
  becomes exact.
- **Expected values never come from the port.** When a case fails, the port
  is wrong until shown otherwise. A wrong case is a spec change with an
  independent source, never an edit to the vendored copy or a quiet skip.
  An oracle once re-derived every expectation and turned a `reject` case
  into `ok`; `suite.py check` now refuses that.
- **Review per milestone.** The Swift port had each milestone reviewed by a
  second model before the next one began. The reviews found concurrency
  flaws and entity-id incompatibilities that the tests did not. Verify each
  finding against the spec before acting on it.
- **Live runs: read-only first**, and only the person who owns the house
  decides when a command is sent.

## What the suite does not cover yet

A port tests these itself, against the spec text:

- the Object Manager response path: HTTP status, body and ZIP limits
  (§4.2–§4.3). The `provisioning` profile is planned;
- entity reads and intents: the normalizations of §6.2–§6.3 (the dimmer
  and HSB round trips, inverted cover tilt, reversed contact state, the
  thermostat), and the action each intent sends (§9.5 item 6);
- the update signals of §6.5, and how several CLU sessions are combined
  behind one system;
- the real transports of §7.1, including the any-source mode.
