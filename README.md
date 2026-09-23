# Computer-Use Automation System

An LLM discovers how to complete a goal against a live web app; the successful run is recorded as a
typed, versioned **capability artifact**; that artifact is then replayed **deterministically, with no
LLM in the loop**, handling runtime errors, business outcomes, and human escalation explicitly.

See **REPORT.md** for the design write-up (architecture, artifact schema, determinism/error handling,
heterogeneity/multi-tenant story, escalation & handoff, safety, cuts). This file is only setup + demo.

## What's here

- `src/target_app/` -- a small mock "core banking" app (Flask, server-rendered, table-based layout, no
  test IDs, one iframe) standing in for the real thing. See its docstrings / REPORT.md section 4.
- `src/agent/` -- the discovery loop: observe (accessibility-style snapshot + screenshot) -> decide
  (Claude tool-calling) -> act (Playwright).
- `src/artifact/` -- the capability artifact schema + the curated interrupt-rule library for this app.
- `src/replay/` -- the deterministic replay engine (no LLM).
- `src/safety/` -- allowlist enforcement, risk classification, redaction.
- `src/handoff/` -- human-in-the-loop escalation and live-session control transfer.
- `src/common/` -- the perception/targeting layer shared by discovery and replay (see REPORT.md section 4
  for why this is the seam that lets the design extend to other surfaces).
- `artifacts/` -- the two capability artifacts produced by real discovery runs (committed so replay can
  be exercised without an API key).
- `evidence/` -- logs, screenshots, and results from real discovery and replay runs. See
  `evidence/README.md` for an index of what each run demonstrates.
- `tests/` -- unit tests (schema, templating, redaction, allowlist) and integration tests (replay against
  the live app, using the committed artifacts).

## Setup

Requires Python 3.11+.

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium   # skip if already installed on your system
```

Start the mock bank app (leave running in its own terminal):

```bash
python3 -m src.target_app.app
# -> http://127.0.0.1:5055 (demo login: operator / demo-pass-1234)
```

### Running without live services

Everything except the discovery run works with no external network access -- `src/target_app` is fully
self-contained. **Replay never calls an LLM**, so `python3 -m pytest tests/test_integration.py` and
`python3 -m src.cli replay ...` against the committed `artifacts/*.json` work offline once the app above
is running.

Only `python3 -m src.cli discover ...` needs an LLM. Export a key first:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Demo path

With the app running (above), from the repo root:

```bash
# 1. Run a real LLM-driven discovery and save the resulting artifact.
python3 -m src.cli discover --capability lookup_member_balance --param member_id=12345

# 2. Replay it deterministically -- no LLM involved.
python3 -m src.cli replay --capability lookup_member_balance --param member_id=12345

# 3. Replay the *same* artifact against a different member -- proves it's a reusable
#    capability, not a hardcoded script.
python3 -m src.cli replay --capability lookup_member_balance --param member_id=12346

# 4. Replay against a business outcome (no such member) and a recoverable transient error.
python3 -m src.cli replay --capability lookup_member_balance --param member_id=00000
python3 -m src.cli replay --capability lookup_member_balance --param member_id=50000

# 5. Replay against a genuinely unhandled state -- triggers human handoff on the SAME
#    live session. (member_id=66666 hits a "supervisor override" gate that is
#    deliberately NOT in the interrupt library.)
python3 -m src.cli replay --capability lookup_member_balance --param member_id=66666 \
    --handoff interactive
#   (or --handoff scripted --operator-script demo/operator_override.txt for a non-interactive run)

# 6. Discover and replay the second, higher-stakes capability (irreversible action).
python3 -m src.cli discover --capability open_member_sub_account \
    --param member_id=12345 --param account_type=Savings --param initial_deposit=500

python3 -m src.cli replay --capability open_member_sub_account \
    --param member_id=12346 --param account_type=Checking --param initial_deposit=250
#   -> refused: "confirmation_required" (irreversible step, not confirmed)

python3 -m src.cli replay --capability open_member_sub_account \
    --param member_id=12346 --param account_type=Checking --param initial_deposit=250 \
    --confirm-irreversible
#   -> succeeds, returns the new account_number
```

`evidence/README.md` maps each of these to the exact run captured for submission, if you'd rather read
the results than reproduce them.

## Demo member IDs

The mock app has a handful of deterministic "trigger" member IDs, so every runtime condition in
REPORT.md section 3 is reproducible on demand instead of waiting for it to happen by chance:

| Member ID | Behavior |
|---|---|
| `12345`, `12346`, `12347` | Normal members with balances |
| `12348`–`12359` | Additional synthetic roster (varied names/balances/tiers/tenure, a couple with pre-existing sub-accounts) -- richer demo data, no special behavior |
| `12345`–`12359` | All of these also carry realistic read-only detail: transaction history, linked cards, beneficiaries (where applicable), and alert preferences -- presentational only, no new automation capability |
| `00000` | Business outcome: member not found |
| `40403` | Business outcome: permission denied |
| `50000` | Recoverable: "system busy" on first load, succeeds on retry |
| `90001` | Recoverable: unexpected "additional verification" interstitial |
| `77777` | Hard failure: simulated session expiry |
| `66666` | Deliberately **unhandled**: needs a supervisor to enter the override code (`sup-override-9911`, a dummy demo credential -- see `SUPERVISOR_OVERRIDE_CODE` in `src/target_app/app.py`) and click "I have authorized access" -- not in the interrupt library, so it always escalates to a human |

## Tests

```bash
python3 -m pytest tests/                 # unit tests always run; integration tests skip if the app isn't up
```

## Configuration

- `config/allowlist.yaml` -- the safety allowlist (domains, routes, permitted action types, and which
  routes are classified irreversible). See REPORT.md section 6.
- `ANTHROPIC_MODEL` (env var, optional) -- overrides the discovery agent's model
  (default `claude-sonnet-4-5-20250929`).
- Escalation notification (env vars, all optional -- see REPORT.md section 5 and
  `src/handoff/notifier.py`): when `HandoffController.request_intervention` fires, it sends an actual
  email (or, via a carrier's email-to-SMS gateway, a text) rather than only printing to the terminal.
  None of these set? The demo runs fully offline -- the notice is written to
  `evidence/<run>/notifications/<step_id>.txt` instead, and that file is what a reviewer without SMTP
  access should look at.
  - `ESCALATION_EMAIL_TO` -- recipient. For a real text message instead of email, set this to your
    carrier's SMS gateway address, e.g. `5551234567@vtext.com` (Verizon), `@txt.att.net` (AT&T),
    `@tmomail.net` (T-Mobile) -- same code path, no third-party SMS API needed.
  - `ESCALATION_SMTP_HOST` / `ESCALATION_SMTP_PORT` (default `587`) -- your SMTP provider.
  - `ESCALATION_SMTP_USER` / `ESCALATION_SMTP_PASSWORD` -- SMTP auth, e.g. a Gmail app password.
  - `ESCALATION_EMAIL_FROM` (default `automation@sterling-core.local`), `ESCALATION_SMTP_USE_TLS`
    (default `true`).

## What's mocked / cut

See REPORT.md section 7 for the full list and reasoning. Briefly: the operator console is a scripted CLI
(not a web UI), multi-tenant/desktop surfaces are designed for but not built, and login/session
establishment is handled by the harness rather than recorded as part of a capability.
