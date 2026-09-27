# Publishing

Releases go to PyPI from a published GitHub Release, using trusted
publishing (no long-lived tokens).

## One-time setup

1. On PyPI, add this project as a trusted publisher for the GitHub
   repository `anushamukka9/agent-policy-kit` (workflow
   `.github/workflows/publish.yml`, environment `pypi`).
2. Do the same on TestPyPI with the `testpypi` environment, so manual
   runs can dry-run safely.

## Cutting a release

1. Bump `version` in `pyproject.toml` and `__version__` in
   `src/rag_canary/__init__.py`. They must match; `test_packaging.py`
   enforces it.
2. Run the full check suite locally: `pytest -q`,
   `python -m rag_canary.benchmark`, `ruff check src tests`,
   `ruff format --check src tests`.
3. If the benchmark numbers changed, update the README table to match.
   The table must always reflect what the runner prints.
4. Merge to `develop`, then tag and publish a GitHub Release from
   `develop`. The `publish.yml` workflow builds the sdist and wheel,
   checks them with twine, and publishes to PyPI.

## Dry runs

Trigger `publish.yml` manually with `workflow_dispatch`. Manual runs
publish to TestPyPI only, so you can verify the whole pipeline without
touching the real index.
