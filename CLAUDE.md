# NKQA — rules for working in this repo

Product: CLI-first AI QA teammate. Design: docs/ARCHITECTURE.md. Current work: docs/PHASE-1-PLAN.md.
This repo is the product, NOT the browser-use library (that comes from pip, see venv/).

## Coding style & guidelines
- Write code like a pragmatic startup developer, not an enterprise architect.
- Keep components and functions compact; avoid bloated boilerplate.
- Avoid obvious comments; let the code speak for itself.
- Do not explain code in chat unless asked.
- Prefer explicit over implicit, but keep it brief.
- Tabs, single quotes, fully typed (pyright strict). `./check.sh fix` settles all style debates.

## Architecture boundaries
- `cli.py` is dispatch-only — no business logic.
- `execution/` never imports `cli`.
- All filesystem paths come from `workspace.py`; never hardcode them elsewhere.

## Security invariants (non-negotiable)
- Secrets are never written to disk or logs; the `<secret>key</secret>` placeholder contract is untouchable.
- Anything irreversible in a browser run goes through `request_permission`.

## Change discipline
- No new dependencies without explicit user approval. Current: browser-use, pyyaml (from Phase 1); dev: ruff, pyright, pytest.
- Preserve CLI exit codes (0 pass, 1 fail, 2 usage) — CI depends on them.
- Behavior changes to recording/replay require updating docs/.
- Run `./check.sh` before any commit (pre-commit hook enforces it; reinstall after fresh clone: `cp scripts/pre-commit .git/hooks/`).
