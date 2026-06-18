---
name: multi-coin-feature-rollout
description: Workflow command scaffold for multi-coin-feature-rollout in Mick-Trading-Bot.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /multi-coin-feature-rollout

Use this workflow when working on **multi-coin-feature-rollout** in `Mick-Trading-Bot`.

## Goal

Implements and documents multi-coin support across configuration, state migration, and user interface/documentation.

## Common Files

- `config/config.yaml`
- `scripts/migrate_*.py`
- `scripts/dashboard.html`
- `docs/superpowers/plans/*.md`
- `docs/superpowers/specs/*.md`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Update config/config.yaml for new coins or overrides
- Implement or update migration scripts (e.g., scripts/migrate_state_v2.py)
- Update dashboard or UI scripts (e.g., scripts/dashboard.html)
- Document the feature in docs/superpowers/plans/ and docs/superpowers/specs/

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.