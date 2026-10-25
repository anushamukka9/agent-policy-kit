"""Export the support bot policy to Rego and Cedar and print both.

Run from the repo root:

    python examples/export_example.py

The Rego export is faithful: same rule order, same three decisions.
The Cedar export is lossy in ways Cedar forces (no approve decision,
no rule order), and the script prints every warning so you can see
exactly what changed instead of finding out in production.
"""

from pathlib import Path

from agent_policy_kit import export_cedar, export_rego, load_policy

POLICY = Path(__file__).resolve().parent / "policies" / "support-bot.yaml"


def main() -> None:
    policy = load_policy(POLICY)

    rego = export_rego(policy)
    print(f"--- Rego export of {policy.name} ({len(rego.text.splitlines())} lines) ---")
    print(rego.text)
    print(f"Rego warnings: {rego.warnings or 'none'}")
    print()

    cedar = export_cedar(policy)
    print(f"--- Cedar export of {policy.name} ({len(cedar.text.splitlines())} lines) ---")
    print(cedar.text)
    print("Cedar warnings:")
    for warning in cedar.warnings:
        print(f"  - {warning}")


if __name__ == "__main__":
    main()
