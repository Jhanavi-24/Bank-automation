# Evidence index

Each subdirectory is one run (`discovery_<id>` or `replay_<id>`), containing:
- `log.jsonl` -- structured, ordered log of every decision/action/outcome
- `screenshots/` -- captured on every observation, plus extra shots on any error/interrupt/escalation
- `artifact.json` (discovery runs only) -- the exact capability artifact produced
- `result.json` -- the final structured outcome

## Discovery runs (real, LLM-driven -- see README.md "Setup" for how these were produced)

- `discovery_1789681939-cf2044` -- **lookup_member_balance**: goal "Look up member 12345 and read
  their current savings balance." Model: `claude-sonnet-4-5-20250929`. 4 steps recorded, checkpoint on
  the "Member Detail" heading.
- `discovery_1789681957-f488c7` -- **open_member_sub_account**: goal "open a new Savings sub-account
  ... with an initial deposit of 500 ... complete the confirmation step." 9 steps recorded; the confirm
  click was automatically classified `irreversible` / `requires_confirmation=True` by the safety
  allowlist (see src/safety/allowlist.py `irreversible_routes`), not hinted to the model.
- `discovery_1789749115-f92958` -- **lookup_member_balance, independent re-run** of the same goal on a
  separate day. Included as a reproducibility signal: the model independently recorded the same 4-step
  flow and chose the same "Member Detail" heading checkpoint. `artifacts/lookup_member_balance.json` is
  the artifact from the *first* run (`cf2044`) -- that's the one every replay below was executed against,
  so the discovery -> artifact -> replay chain in this directory is consistent end to end.

## Replay runs (deterministic, no LLM) -- using the artifacts above

| Evidence dir | Capability | Params | Demonstrates |
|---|---|---|---|
| `replay_...-5018f1` | lookup_member_balance | member_id=12345 | Success (same member as discovery) |
| `replay_...-61447d` | lookup_member_balance | member_id=12346 | Success on a **different** member -- proves the artifact is reusable, not hardcoded |
| `replay_...-ff8b13` | lookup_member_balance | member_id=00000 | **Business outcome**: MEMBER_NOT_FOUND |
| `replay_...-d46b53` | lookup_member_balance | member_id=50000 | **Recoverable**: transient "system busy" auto-retried once, then succeeds |
| `replay_...-33798f` | lookup_member_balance | member_id=66666 | **Hard failure -> human handoff**: an unhandled interrupt (deliberately not in the interrupt library) escalates to a human operator, who authorizes access on the *same live session*; replay then resumes and completes |
| `replay_...-236778` | open_member_sub_account | member_id=12346 | **Blocked**: irreversible step refused without `--confirm-irreversible` |
| `replay_...-e256cd` | open_member_sub_account | member_id=12346 | **Success**: same run, explicitly confirmed |
| `replay_...-9b4715` | open_member_sub_account | member_id=12347, deposit=-10 | **Business outcome**: INVALID_DEPOSIT_AMOUNT (server-side validation) |
| `replay_...-cb6387` / `...-0b1a4f` | open_member_sub_account | member_id=12345 | Blocked, then succeeded with confirmation (re-verification pair) |
| `replay_...-d2da45` | open_member_sub_account | member_id=12346 | **Irreversible step approved via human handoff** instead of the CLI flag |

To regenerate any of these, see the exact commands in README.md "Demo path". The scripted operator
commands used for the handoff runs are in `demo/operator_override.txt` and `demo/operator_approve.txt` --
in production these come from a person typing into the operator console in real time
(`--handoff interactive`); they're scripted here only so the evidence is reproducible.
