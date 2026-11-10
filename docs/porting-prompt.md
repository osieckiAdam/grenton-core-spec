# Prompt: a new Grenton Core port

The prompt for an agent that starts a new language port of this spec. The
lessons it relies on are in [`implementing.md`](implementing.md); a person
porting by hand should read that file and §1–§5 below.

How to use: fill in the parameters, then paste everything from
"You are implementing" to the end into a **fresh** agent session started in
the folder that holds the sibling checkouts (`grenton-core-spec`,
`grenton-core-swift`). The prompt assumes nothing from earlier sessions.

## Parameters

| Parameter | Meaning | Example (Go) |
|---|---|---|
| `{{LANG}}` | Language of the port | Go |
| `{{REPO}}` | Repository and folder name | `grenton-core-go` |
| `{{PACKAGE_ID}}` | Module / package coordinates | `github.com/osieckiAdam/grenton-core-go` |
| `{{TOOLCHAIN}}` | Minimum toolchain | Go 1.26 (the oldest supported release; vCLU builds with it) |
| `{{PLATFORMS}}` | Where the library must run | Linux (incl. arm64), macOS, Windows |
| `{{DEPENDENCIES}}` | Dependency policy | standard library only, unless the user approves an exception |
| `{{CONSUMERS}}` | Who will use the library first, so the API fits them | vCLU, a Go automation hub that will use the library as its client of real CLUs |
| `{{USER_LANGUAGE}}` | Language for talking to the user | Polish |

---

You are implementing **{{REPO}}**, the {{LANG}} port of the Grenton Core
specification. Grenton Core is a library for the Grenton smart-home "mobile
interface" protocol: it downloads the interface from Object Manager or imports
it from an `.omp` project file, keeps encrypted UDP sessions with the CLU
controllers, and maps widgets to a canonical device model. One port exists
already, `grenton-core-swift`; you are the second (or later) one, and part of
your job is to show where the spec is not yet language-neutral.

- Repository: `{{REPO}}`, a sibling of `grenton-core-spec` on disk.
- Module coordinates: `{{PACKAGE_ID}}`.
- Minimum toolchain: {{TOOLCHAIN}}.
- Platforms: {{PLATFORMS}}.
- Dependencies: {{DEPENDENCIES}}.
- First consumers: {{CONSUMERS}}. Check your public API against what they
  will need, and ask the user when that is unclear.

Talk to the user in {{USER_LANGUAGE}}. Write code, comments, commit messages
and repository documents in English, matching the existing repos.

## 1. What "done" means

1. The port passes **every case of all four conformance profiles**
   (`stateless`, `mapping`, `session`, `project`) of the current suite, with
   an empty `skips` list, run by a runner that follows
   `spec/conformance.md` §5 to the letter.
2. The full gate (§8 below) is green locally and in CI.
3. Read-only examples have run against the user's live installation
   (download or import, list devices, watch state). Commands that change the
   house come later and only with the user's go-ahead.
4. The public API is documented for application developers, and each
   example compiles against the public API only.
5. Every spec gap you found is either fixed in the spec (spec first, §9)
   or listed in your final report with a proposed rule.

Passing the suite is necessary, not sufficient. The suite does not cover
transports, Object Manager HTTP, entity intents or the system facade.
Those you test yourself, against the spec text.

## 2. Sources and their authority

Highest first. When two disagree, the higher one wins and you report the
disagreement.

1. `grenton-core-spec/spec/grenton-core-design.md`: the normative rules.
   Read §1–§10 in full before writing code.
2. `grenton-core-spec/spec/conformance.md` and `conformance/`: the case
   format, the projections, the runner duties, and the cases. The spec
   says: "in case of a discrepancy the suite decides."
3. `grenton-core-spec/docs/decisions.md`: why the rules are what they are,
   with the measurements. Read it before you "simplify" a rule.
   `grenton-core-spec/docs/implementing.md`: where the first port went
   wrong, and how to test for each. Read it in full (see §6).
4. `grenton-core-spec/CHANGELOG.md`: history and evidence. Context only.
5. `grenton-core-swift`: a working port. Read it to see **how** a problem
   was solved: the conformance runner, the session harness with a virtual
   clock, the strict JSON parser, the bounded ZIP reader, the transports. It
   never decides **what** is correct. Never copy an expected value, a
   threshold or a format from it; if it differs from the spec, the spec wins
   and you note the difference.

Other Grenton projects on disk (`vclu`, BSL 1.1; `grenton-native`, GPL-3.0;
`homeassistant-grenton`, the reference): take **facts only**, never code.
The spec already carries every fact you need.

## 3. Rules you never bend

- **Expected values never come from your port.** If a case fails, assume the
  port is wrong. If you are sure the case is wrong, stop and raise it as a
  spec issue (§9). Never edit the vendored suite. Never add a skip just to
  turn the run green. Never change a `reject` expectation into `ok`.
- **The runner calls only the production API.** It serializes observations;
  it does not cast, map, normalize PINs or decide errors. If the runner needs
  something the API does not offer, the API is missing it.
- **No identifiers of the user's real installation** anywhere in the repo,
  commits, test names or fixtures: object names, widget ids, labels, page
  names, CLU names, IPs, keys, counts. Use synthetic ones (`THE1001`,
  `CLU_1`, "Lamp A"). Live artifacts (a downloaded interface, logs) go into
  a git-ignored `dist/`. Copy the installation-data gate from the spec repo
  (`tools/check_no_installation_data.py`) and run it in the gate.
- **The rules live in the four Core modules** (Model, Provisioning, Runtime,
  Devices), with the dependency direction of spec §2. A helper or umbrella
  module may compose them; it holds no rule.
- **Nothing goes to `main` directly.** Work on a branch, commit per
  milestone, and push or open a PR only when the user asks.
- **Writes to the house need the user's go-ahead**, every time: switching,
  heating, scenes, schedules.

## 4. Phase 0: read, then plan (no code yet)

1. Vendor the spec: add `grenton-core-spec` as a git submodule at
   `vendor/grenton-core-spec`, pinned to the latest tag. Note its version.
   That number is also your port's version (see §8).
2. Read the sources in the order of §2. For the Swift port, read at least:
   `docs/grenton-core-swift-port.md` (the plan; Polish), `CLAUDE.md`,
   `.claude/skills/grenton-change/SKILL.md`, `CHANGELOG.md`, and
   `Tests/GrentonConformanceTests/` (runner and session harness).
3. Write `docs/{{REPO}}-port.md`, the port plan. It turns the spec into
   {{LANG}} decisions, and it says *what to use*, never *what to do*, since
   the behaviour is the spec's. Cover at least:
   - the module/package layout and how the §2 dependency direction is
     enforced (build system, internal packages, visibility);
   - `GrentonValue` (64-bit int, double, string, bool, null) and the tagged
     unions (widget DTOs, `ValueRef`, `ActionRef`);
   - each port-contract interface of §7 (socket factory, cipher, HTTP + ZIP,
     clock/timers, random, logger, async execution) and its production
     implementation, including **both** UDP modes of §7.1 (peer-filtered and
     any-source);
   - the concurrency model of §5.6 in {{LANG}} terms: who owns the session
     state, how operations are serialized, how cancellation and shutdown
     work, how a dropped session releases its socket;
   - how the conformance runner gets a virtual clock and a scripted transport,
     and **how it detects that the session is idle** (the barrier of
     conformance.md §4b). Prefer a deterministic signal over the 200 ms
     real-time quiet window: the Swift port's negatives rest on real-time
     windows, which makes them slow and only probabilistic;
   - the error types and their mapping to the §8 taxonomy and the suite's
     error codes;
   - a name map from the spec's reference names to yours (spec §2 allows
     idiomatic names, as long as they are documented);
   - how public docs and examples are checked (compiled snippets, examples
     built against the public API only);
   - the {{LANG}} traps of §7 below and how you avoid each.
4. Ask the user only what is theirs to decide: repository visibility and
   licence (the other repos are public, MIT), the toolchain floor and
   platforms if the parameters leave doubt, any dependency beyond
   {{DEPENDENCIES}}. Everything else, decide yourself and record it in the
   plan.
5. Show the plan in a few lines and continue. Don't wait for a review of the
   whole document unless the user asked for one.

## 5. Milestones

Each milestone ends with green tests and a commit. After each one, get an
independent review of that milestone's code against the spec sections it
covers (a second model or a fresh agent that sees the code, not your
conclusions), verify every finding against the spec before acting on it, fix,
re-run, and commit. Report to the user in two or three lines per milestone.

| # | Scope | Spec | Exit |
|---|---|---|---|
| M1 | Repo skeleton, submodule, **conformance runner** with manifest checks, `port.json` declaring no profile yet | conformance.md §2, §5 | the runner fails closed on a missing submodule, a manifest mismatch, an unknown operation and a stale skip — prove each with a test |
| M2 | Model: values, casting, DTOs, tolerant widget parsing, `ValueRef`/`ActionRef` | §3 | `value.cast.*`, `payload.action` parse cases |
| M3 | Cipher, frames, payloads, report and values parsing, coercions, number format, schedule rules, OM URL/PIN | §4.1, §5.1–§5.3, §6.2, §6.3.13, §10.3 | **`stateless` profile** green |
| M4 | Device descriptors: widget → device/entity mapping, order, sections, icons, physical objects | §6.1, §6.3, §6.6 | **`mapping` profile** green |
| M5 | `.omp` import: bounded ZIP, XML without DTDs, strict JSON, interface selection, editor-to-interface conversion | §4.5 | **`project` profile** green |
| M6 | CLU session and state store: correlation, chunked registration, report routing, keep-alive, loops, recovery, lifecycle, diagnostics | §5.4–§5.6 | **`session` profile** green, also under the race detector or equivalent, repeated ≥20 times without a flake |
| M7 | Transports: peer-filtered and any-source UDP on loopback; Object Manager client over HTTP with the §4.2–§4.3 limits | §4.1–§4.3, §7 | your own tests: a datagram from a foreign source port reaches the any-source socket; redirects are not followed; an oversized body or entry is `invalidData` while streaming |
| M8 | Bound devices and entities (state getters, intents, update signals), the system facade, the umbrella module | §6.2–§6.5, §2 | your own tests on a scripted CLU, including the round trips of §9.5 item 6 |
| M9 | Examples and the live run (read-only first) | — | the user ran them; findings recorded |
| M10 | Public docs, README, CHANGELOG, gate, CI, release workflow | — | §8 gate green in CI |

Declare each profile in `port.json` as soon as it passes, so a regression
fails the run from then on.

## 6. What the first port learned

Read `grenton-core-spec/docs/implementing.md` in full before the port plan,
and again before M5 (`.omp`), M6 (session) and M8 (devices). It lists every
place the Swift port went wrong or nearly did, with the spec section and how
to test for it, and what the suite does not cover yet. The plan must say,
for each item, how your design avoids it. The items that bite every port:

- the cast grammar and number format are the spec's, not the language's;
- a wire double never traps on the way to an integer;
- ZIP limits are counted while inflating, never taken from the header;
- the PIN never reaches an error text or a log;
- one reader per socket, and `start`/`register`/`stop` serialized as whole
  operations;
- any-source mode is one unconnected socket;
- an entity's update signal is a flag that cannot be lost;
- every timer goes through the injected clock.

## 7. Language traps to check in {{LANG}}

Go through each item for {{LANG}} in the port plan, and write down how you
avoid it:

- lenient number parsing and formatting (see §6);
- a JSON decoder that silently accepts duplicate keys, big integers as
  floats, or `NaN`, or that aborts an array on the first bad element;
- an XML parser that resolves entities or passes DTD declarations through as
  tokens you might ignore;
- a ZIP reader that trusts declared sizes;
- HTTP clients that follow redirects by default, or put the URL (with the
  PIN) into errors;
- unconnected UDP sockets on your platforms: binding, `sendto`, receiving
  from any source, and the error each OS gives when local-network access is
  denied;
- AES-CBC with PKCS#7: the standard library may give only the block mode,
  so padding and its validation (a bad pad is `reject`, never a crash) are
  yours;
- timers and sleeps that bypass the injected clock (one stray real-time
  sleep makes the session suite hang or flake);
- leaks of tasks, goroutines or threads after `stop()`, and races between a
  timer firing and `stop()`. Run the race detector in the gate if the
  language has one;
- integer width: `int` that is 32-bit on some targets, unsigned ports
  (`UInt16`), subscription numbers up to 999 999.

## 8. Gate, CI, versioning

Mirror the Swift port's discipline, translated to {{LANG}}:

- One script (`scripts/check.sh` or the language's equivalent) runs:
  installation-data gate, formatting/lint, build, unit tests plus the
  conformance suite (with the race detector where available), public-API
  docs check, doc snippets/examples compiled, and a spec-sync check (the
  submodule is at a spec tag, and `port.json` `suiteVersion` equals the
  manifest's). Git hooks run the quick part on commit and all of it on push.
  Never bypass them.
- CI runs the same script on every pull request, on at least Linux and
  macOS, with `git config grenton.interface none` (CI has no installation).
- **The port's version is the spec version it implements.** A port-only fix
  after a tag needs a spec bump. Every merge into `main` is a release: the
  release workflow tags the version from `port.json` and fails if it is
  already tagged. Keep a `CHANGELOG.md` headed by that version.
- README: what the library does, install, a quick start that runs against
  a real installation (download with the PIN, or import a `.omp`), where to
  get the PIN, the conformance table (suite version and profiles), and
  local-network permission notes for each platform.
- When the port is released, the spec repo's README "Implementations" table
  gets a row: that is a spec-repo change, so it goes through the spec's PR
  flow.

## 9. When the spec is wrong, silent or language-bound

You will find places where the spec assumes the Swift port's shape:
async/await, actors, `AsyncStream`, Apple frameworks. Or places where it
says nothing and the Swift port quietly decided. For each one:

1. Write it down in `docs/spec-feedback.md`: the section, the problem, what
   Swift does, what you propose.
2. If it blocks you, or two readings would produce different wire
   behaviour, stop and ask the user before choosing. Otherwise choose the
   reading closest to the spec's intent, record it in the plan, and go on.
3. Changing the spec follows the spec repo's rules (its `CLAUDE.md` and the
   `grenton-change` skill, step 2): normative text, cases with independent
   expectations, a version bump, a CHANGELOG entry, a PR. Spec first, then
   every port follows. Don't open spec PRs on your own. Propose them in your
   report.

## 10. Live runs

- Ask the user where and how to run against the installation. Object
  Manager serves the interface only while its "send to mobile device" dialog
  is open. The PIN is the token shown there.
- On macOS, local-network privacy silently denies UDP to the LAN for
  binaries launched from an IDE or an agent's shell (`EHOSTUNREACH`,
  "network is down" or similar, while HTTP to the same host works). The
  user runs live examples in Terminal.app. Linux has no such prompt.
- Read-only first: fetch or import, list devices, watch state for a while,
  and read values once. Ask before any example that sends a command.
- Save the interface archive to `dist/` (git-ignored); it contains the CLU
  key.

## 11. Final report

End with: the suite version and profiles passed (case counts), gate status,
what ran live and what it showed, the spec feedback list with a proposal
for each item, deviations from the Swift port and why, and what is left
(with a recommendation for the next step). Keep it short. Details belong in
the repo.
