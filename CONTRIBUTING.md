# Contributing

Bug reports and pull requests are welcome. A few ground rules so the
project stays small and honest.

## What belongs here

- New match operators with tests and docs, as long as evaluation stays
  deterministic and dependency-light.
- Better approval flows (Slack/Teams notifiers, multi-approver rules),
  as long as the file-backed store stays the default.
- Docs fixes and clearer guidance.

## What does not

- Anything that calls a model or the network at evaluation time.
  Deterministic is the whole point.
- "Smart" matching that guesses intent. If a rule cannot be explained in
  one sentence, it does not belong in a policy file.
- Broadening a default to allow without a very good reason. Deny by
  default is the contract.

## How to contribute

1. Fork, branch off `develop`, and keep the change focused.
2. Add tests in `tests/` and a labeled case in `benchmarks/cases.jsonl`
   if you touch matching. Run `pytest -q` and
   `python -m agent_policy_kit.benchmark`; both must be green, and the
   README benchmark table must match the new numbers.
3. Run `ruff check src tests` and `ruff format --check src tests`.
4. Document new match conditions in `docs/policies.md` with an example.
5. Open a PR against `develop` with a plain description of what changed
   and why.

## Style

- No em-dashes anywhere. Not in code, not in docs, not in commit messages.
- Line length 100, enforced by ruff.
- Reasons and explanations are written in plain language. A denial is
  read during an incident; write it like one.
