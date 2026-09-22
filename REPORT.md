# Design Report

## 1. Architecture

A single Python process, two modes, one shared perception/targeting layer:

```
src/target_app/   mock core-banking app (proxy target)
src/agent/        discovery: observe -> decide -> act, LLM in the loop
src/replay/       replay: same actions, no LLM
src/common/       browser_surface (perception) + locator_resolver (targeting) -- shared by both
src/artifact/     the capability schema + a curated interrupt-rule library
src/safety/       allowlist, risk classification, redaction
src/handoff/      human escalation and live-session control transfer
```

The central decision: **discovery and replay execute actions through the exact same primitives**
(`src/common/locator_resolver.resolve/click/fill/...`). Discovery doesn't click a raw Playwright element
handle the LLM points at; it converts the chosen element into the same ranked list of candidate locators
replay will later use, and resolves through the same code path. This means a recorded step's locators are
*proven to work* at the moment they're recorded, not just plausible guesses -- and it means a bug in
targeting shows up once, not twice. (It also means bugs found during development, like the two below,
get fixed for both discovery and replay in one place.)

Single process, sync Playwright, no queues or services: this is a "vertical slice" project, not
production infrastructure, and the brief explicitly discourages building scaling machinery prematurely.
The seams that *would* let this scale (see section 4) are architectural, not infrastructural.

**Two real capabilities were discovered and replayed**, both goals taken directly from the brief's own
examples: `lookup_member_balance` (read-only) and `open_member_sub_account` (irreversible). Using two
instead of one was worth the extra discovery run because they exercise very different parts of the
system -- the safe capability stresses extraction/generalization, the risky one stresses the safety
gating and human-approval path.

## 2. Artifact schema

An artifact (`src/artifact/schema.py`) is not a step list -- it's a callable contract:

- **Steps** carry a *ranked list* of candidate locators (`TEST_ID > ROLE_NAME > LABEL_TEXT >
  ROW_VALUE_TEXT > TABLE_CELL > TEXT_EXACT > TEXT_CONTAINS > CSS > COORDINATES`), not one selector. Replay
  tries each in order and records which one worked. This is deliberately the same shape a code reviewer
  would want: "what's the best way to find this control, and what do we fall back to if the app changes
  slightly." `TABLE_CELL` (row position + `<th>` column header) was added after a real bug -- see section 3
  bug #3 -- exposed that `ROW_VALUE_TEXT`'s "the row's other cell" is only unambiguous for exactly two
  columns.
- **Values are templated** (`{{member_id}}`), never literal. The discovery agent is prompted to tag which
  typed/selected values correspond to declared input parameters; nothing about *this run's* concrete data
  is baked into the artifact.
- **Typed input/output params** (`ParamSpec`: name, type, required, `sensitive`, description) are the
  capability's actual function signature. A `sensitive=True` flag is structural, not a convention --
  `src/safety/redaction.py` reads it directly.
- **Interrupt rules are declared once per artifact, not per step** (`InterruptRule`: a detector locator, a
  classification, an optional dismiss action). A "session expired" or "system busy" page can appear after
  almost any step in a legacy app; encoding that per-step would mean duplicating the same handling
  everywhere. Checking a small rule set opportunistically after every step -- and when a step's expected
  target can't be found -- is what turns "replay crashed" into "replay handled it." These rules are
  authored once per `app_key` (`src/artifact/interrupts_library.py`) and attached to every capability
  recorded against that app, the way a real team would maintain a shared "known error pages for this
  vendor product" library rather than rediscovering it per capability.
- **Checkpoint** is a single locator proving the *goal state*, not the goal's data. This sounds obvious in
  hindsight but the first real discovery run got it wrong (see section 3) -- worth calling out because
  it's exactly the kind of subtle correctness bug the brief is testing for.

The schema is Pydantic, versioned (`schema_version`, per-artifact `version`), and JSON-serializable --
`artifacts/*.json` are meant to be opened and read directly, not just deserialized by the replay engine.

## 3. Determinism & error handling

Replay (`src/replay/engine.py`) never calls an LLM. Determinism comes from: locators resolved by the same
ranked-candidate mechanism every time; templated values substituted from typed params; and a fixed
interrupt rule set instead of ad hoc retry logic.

**Result taxonomy** (`ReplayStatus`): `SUCCESS` (outputs + verified checkpoint), `BUSINESS_OUTCOME` (a
legitimate answer -- "no such member," "invalid deposit amount" -- carried in `outcome_code`, not a
crash), `CONFIRMATION_REQUIRED` (an irreversible step was reached but not authorized), `DENIED_BY_OPERATOR`,
and `HARD_FAILURE` (with `failed_step_id`, `expected`, `observed`, and a screenshot). Interrupt rules
classify into the same three buckets (`business_outcome` / `recoverable` / `hard_failure`); a recoverable
one gets a bounded number of dismiss-and-retry attempts (`max_occurrences`), then escalates to hard
failure rather than looping forever.

**Three real bugs, found by testing against the live app before trusting the design on paper:**

1. The first real discovery run picked `role_name: cell|"$8214.53"` as its success checkpoint -- the
   balance's own text. That only proves success for *this* member; replaying for a different member ID,
   the checkpoint would never match. Fix: the system prompt now explicitly forbids checkpointing on an
   element whose text is one of the just-extracted outputs. (`src/agent/prompts.py`; re-run, the model
   correctly picked the "Member Detail" heading instead.) This is also why `ROW_VALUE_TEXT` exists as its
   own locator strategy: a value cell needs to be found by its *stable adjacent label* ("Savings
   Balance"), not by its own changing content -- the same bug pattern, fixed structurally.
2. `COORDINATES` was resolving unconditionally (any pixel has *something* under it), which meant
   confirmation-gating an irreversible click -- which checks "does the target genuinely resolve?" --
   would say yes even when the real page was showing a validation error, not the confirm screen. Fix:
   irreversible steps resolve with `allow_coordinates=False` (`src/common/locator_resolver.resolve`,
   `src/replay/engine._execute_step`). An irreversible action must never fire off a coordinate guess with
   no identity check behind it -- that's a safety property, not just a correctness one.
3. While experimenting with a third capability against a genuine 3-column data table (Date | Description |
   Amount -- ultimately not part of this submission, see Cuts), a latent bug in `ROW_VALUE_TEXT` itself
   surfaced: its XPath means "the row's other `<td>`, whichever one isn't the label" -- unambiguous for
   exactly two columns, but for three or more it silently resolves to the *first* non-label cell every
   time. A real discovery run extracted "Dividend Payment" (the Description column) when the target was
   the Amount column, twice in a row, because `ROW_VALUE_TEXT` never raised an error -- it just returned
   the wrong cell with full confidence. Fix: a new `LocatorStrategy.TABLE_CELL` (`row index among data
   rows` + `<th>` column header text) identifies a cell by actual column identity instead of "not the
   label," and is used in place of `ROW_VALUE_TEXT` whenever a table has a real header row
   (`src/common/browser_surface.py`, `src/common/locator_resolver.py`). Locked in by
   `tests/test_locator_resolver.py` against the live table. Both submitted capabilities are unaffected by
   this change either way -- neither's extraction targets a `<th>`-headed table -- but it's a real
   robustness fix to the shared targeting layer both of them depend on, kept in for that reason.

All three are documented where they were fixed rather than papered over, because they're the actual
argument for why this project builds its own mock app with reproducible trigger conditions instead of
hand-waving about error handling: the bugs only surfaced by actually running discovery/replay against real
runtime states.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** Everything above `src/common/browser_surface.py` and `locator_resolver.py` only
ever sees `ElementInfo` / `Locator` objects -- never raw DOM. `browser_surface.snapshot()` is the one
place that knows how to turn a live surface into that vocabulary (today: DOM query + accessible-name
heuristics + a legacy-table row-label heuristic, because this app has neither test IDs nor real
`<label for>` associations). Extending to a legacy web app with worse markup means adding locator
strategies to this one module (e.g. OCR'd text position, frame-relative XPath); extending to a desktop
app means replacing it with one that queries an OS accessibility API (Windows UIA / macOS AX) and yields
the same `ElementInfo` shape -- `role`, `name`, candidate locators, bounding box all have direct desktop
analogues. Nothing in the artifact schema, the discovery loop, or the replay engine would need to change;
`TargetSpec.surface_type` already distinguishes `web` / `legacy_web` / `desktop` for exactly this reason.

**Multi-tenant reuse.** `TargetSpec.fingerprint` (`app_key`, `vendor_version`, `landmark_text`) identifies
*which vendor product* an artifact targets, independent of which tenant is running it.
`TargetSpec.tenant_id` is `None` for a base artifact and set only when a tenant needs an override. The
intended flow (not built, but the schema doesn't block it): replay a base artifact against a new tenant;
if every locator's top candidate still resolves, it's reused as-is; if a locator only resolves via a
lower-ranked fallback (or not at all), that's a drift signal -- log which step/strategy degraded, and
either accept the fallback (if it's still within an acceptable robustness rank) or fork a `tenant_id`-scoped
override for just that step, inheriting the rest of the base artifact. The ranked-fallback-chain design in
section 2 is what makes this cheap: "did we have to fall back, and how far" is already a first-class,
per-step signal, not something that needs to be reconstructed after the fact. Canonicalizing dynamic
route segments (`/members/12345` -> `/members/:id`) was left as a stretch goal rather than built (see
Cuts) since one target app doesn't exercise it meaningfully.

## 5. Escalation & handoff

`src/handoff/controller.py`. A `HandoffController` holds a reference to the *same* `Page` object
discovery/replay is already driving -- it never opens a new browser or tab. `request_intervention()`
captures context (capability, step, reason, current URL, a screenshot), logs an "intervention requested"
event, and flips a two-state control marker (`AUTOMATION` / `HUMAN`). The operator surface is a small
command REPL (`click`, `fill`, `goto`, `screenshot`, `approve`, `resume`, `deny`) that runs commands
directly against that same page; every command is logged. `resume` means "I did what was needed manually,
continue"; `approve` means "go ahead and run the automated step now"; `deny` stops the run. This is
intentionally the minimal-but-real version the brief scopes for -- not a co-browsing console -- but the
control-transfer model (same session, explicit state, logged actions, resumable) is the real part, and a
web-based console would sit behind the identical `HandoffController` API.

**Routing the request to an actual human, not just a terminal.** The brief's phrase is "route an
intervention request to a human operator" -- a `print()` only reaches someone already watching that
exact terminal. `src/handoff/notifier.py` sends a real email (SMTP) when `request_intervention` fires,
or, pointed at a carrier's email-to-SMS gateway (`ESCALATION_EMAIL_TO=5551234567@vtext.com`), a real text
message, with zero paid third-party API and no credential committed to the repo (config is env-var only;
see README "Configuration"). Nothing configured -- the default, so the demo needs no external
services -- and the exact notice that would have been sent is written to
`evidence/<run>/notifications/<step_id>.txt` instead, which is what `evidence/README.md` points reviewers
at. An SMTP failure falls back to that same file rather than crashing the run it's trying to report on --
a notification is enrichment, not a dependency the core loop should ever block on.

Two trigger points are wired into `replay.engine`, matching the brief's two escalation cases exactly:

- **"A replay hits a condition it can't recover from."** When a step fails and no known interrupt rule
  matches (member ID `66666` in the demo -- deliberately excluded from the interrupt library, simulating
  a state the recorded artifact genuinely doesn't know how to handle), replay requests intervention
  instead of failing outright. The operator inspects the live page, performs whatever's needed (in the
  demo: authorizing a supervisor-override gate), and calls `resume`; replay retries the same step against
  the now-changed live state.
- **"A risky/irreversible step needs a person to decide."** `ConfirmationRequired` (raised only after the
  step's target has been positively resolved -- see section 3, bug #2) routes to the same controller when
  `--confirm-irreversible` wasn't passed. The operator can `approve` (let automation click it) or `deny`.

`evidence/` shows both, end to end, on the real artifacts, in `evidence/README.md`.

## 6. Safety

**Allowlist** (`config/allowlist.yaml`, `src/safety/allowlist.py`): explicit domains, route regexes, and
permitted action types; fail-closed (unlisted = denied). Checked *repeatedly* -- before every navigation
and after every click that might have navigated -- not just once at the top of a run, so a step that
somehow drifted outside the approved surface is caught immediately rather than after the fact.

**Risk classification**: `RiskLevel.SAFE` / `REVERSIBLE` / `IRREVERSIBLE`, assigned automatically during
discovery by checking the *destination* URL of each click against `irreversible_routes` in the allowlist
config -- not hinted to the model, and not something the model self-reports. In the real discovery run,
the "Confirm and Open Account" click was correctly flagged `irreversible` / `requires_confirmation=True`
purely from this mechanism. Irreversible steps are handled conservatively: replay refuses to execute them
unattended (`CONFIRMATION_REQUIRED`) unless explicitly confirmed via `--confirm-irreversible` or a human
operator approves through the handoff console -- and, per bug #2 above, never via a coordinate-only
locator match.

**Redaction** (`src/safety/redaction.py`): artifacts never contain literal values at all (section 2), so
there's structurally nothing to redact there. Evidence logs and screenshots *do* need to show what
happened, so every log line passes through redaction keyed off `ParamSpec.sensitive` plus a fixed list of
well-known secret-like field names, and a set of literal secret values (e.g. the demo login password) is
scrubbed from any free-text log field. Login/session establishment is handled by the harness, outside the
LLM loop and outside any recorded artifact, specifically so a credential never enters a model transcript
or an artifact in the first place (see Cuts).

**Limits**: the allowlist is a static YAML file reviewed by a human, not derived automatically -- a
capability that legitimately needs a new route requires a person to update it, which is the intended
friction. Risk classification is route-based, not action-semantics-based; a route that's irreversible for
reasons the URL alone doesn't reveal would need to be added to the config explicitly.

## 7. Cuts

- **A third capability was prototyped, not submitted.** `get_recent_transactions` (read the most recent
  entry from a member's transaction history) worked end to end after the `TABLE_CELL` fix above, but two
  capabilities already exercise the interesting problems the brief asks for -- a safe/read capability and
  an irreversible one -- and the brief is explicit that depth beats breadth ("we do not reward feature
  breadth"). Kept the bug fix (it strengthens the shared targeting layer both submitted capabilities use);
  cut the third capability itself.
- **Operator console is a CLI, not a web UI.** The brief scopes this explicitly ("mock the operator UI if
  needed... make the handoff mechanism and control-transfer model real"). `HandoffController` is UI-agnostic;
  a browser-based console would consume the same `request_intervention` / command API.
- **Multi-tenant and desktop are designed for, not built** (section 4), per the brief's explicit
  "we don't expect you to implement multi-tenant or desktop support."
- **No cross-tenant drift detection tooling** (a real CLI/report for "which artifacts degraded against
  which tenant") -- the per-step fallback-rank signal it would consume already exists; the tool around it
  doesn't.
- **Canonicalization of dynamic routes** (`/members/12345` -> `/members/:id`) -- stretch goal, skipped;
  one target app doesn't exercise it meaningfully.
- **No multi-run stability/flakiness scoring** -- stretch goal, skipped in favor of depth on the core
  four (schema, replay/error-handling, escalation, safety).
- **Numeric outputs are typed as strings** (e.g. `savings_balance: "$8214.53"`) rather than parsed into a
  currency/number type. Simple to add (a `ParamType.CURRENCY` with a parse step) but not load-bearing for
  what's being evaluated here.
- **Confirmation-required check applies per capability run, not persisted approvals** -- there's no
  "approved once, trusted going forward" state (the stretch-goal "confidence & approval" gate). Every
  unattended irreversible replay needs `--confirm-irreversible` explicitly, every time, which is the more
  conservative default.

**What I'd build next**: cross-tenant replay + drift reporting (section 4's design, actually run against a
second mock tenant with slightly different markup); a small approval-state gate per stretch goals; parsing
typed outputs instead of leaving them as display strings.
