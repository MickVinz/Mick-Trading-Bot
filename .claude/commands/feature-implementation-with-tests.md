---
name: feature-implementation-with-tests
description: Workflow command scaffold for feature-implementation-with-tests in Mick-Trading-Bot.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /feature-implementation-with-tests

Use this workflow when working on **feature-implementation-with-tests** in `Mick-Trading-Bot`.

## Goal

Implements a new feature or enhancement in the source code, accompanied by corresponding test scripts to ensure correctness.

## Common Files

- `src/*.py`
- `scripts/test_*.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Implement feature logic in src/ (e.g., src/config_utils.py, src/paper/journal.py, src/paper/paper_engine.py)
- Create or update corresponding test script in scripts/ (e.g., scripts/test_config_utils.py, scripts/test_journal_multicoin.py, scripts/test_paper_engine.py)

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.