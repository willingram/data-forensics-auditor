# Contributing

Contributions are welcome. Use Python 3.10 or newer and the frozen uv
environment:

```sh
uv sync --extra dev --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
```

Tests must be deterministic and offline by default. Use the smallest fictional
CSV, TSV, workbook, or manifest fixture that reproduces the condition. Never
commit private datasets or reports, credentials, local absolute paths, virtual
environments, caches, or generated audit/build output.

Before proposing a distributable change, start with an empty `dist/` directory
and run the non-publishing artifact checks:

```sh
uv run --frozen python -m build
uv run --frozen twine check --strict dist/*
uv run --frozen python scripts/inspect_distribution.py dist
```

The canonical commands and module entry point, manifest fields and their
semantics, supported input handling, finding classification and loudness,
failure-threshold behavior, report filenames and fields, and exit behavior are
public-contract areas. Changes to those areas need focused regression tests and
corresponding updates to `README.md`, `DESIGN.md`, and `CHANGELOG.md`.
