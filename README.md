# Data Forensics Auditor

Offline command-line tool for checking tabular datasets for analyst-visible
forensic tells. It reads a manifest describing the files, runs statistical and
bookkeeping checks, and writes both Markdown and JSON reports.

The tool is intended to catch analyst-visible seams such as time-order artefacts,
implausible variance structure, cross-file mismatches, digit/rounding tells, and
unexpected input problems. It is a screening tool, not a formal statistical package.
It works best on process-style data with timestamps, measurements, counts, rates,
subgroups, categories, or cross-file reconciliation points. Manufacturing quality
data is a natural use case, but the checks are mostly domain-neutral.

## Install

Requires Python 3.10 or newer.

### With uv

```sh
git clone <repo-url>
cd data-forensics-auditor
uv sync --extra dev
```

Run the CLI from the managed environment:

```sh
uv run dfa --manifest manifest.yaml --out audit_output
```

The long-form command also works:

```sh
uv run data-forensics-auditor --manifest manifest.yaml --out audit_output
```

### With pip

macOS, Linux, or WSL:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Then use any supported invocation:

```sh
dfa --manifest manifest.yaml --out audit_output
data-forensics-auditor --manifest manifest.yaml --out audit_output
python -m data_forensics_auditor --manifest manifest.yaml --out audit_output
```

## Usage

```sh
dfa --manifest manifest.yaml --out audit_output
```

Outputs:

- `audit_output/audit_report.md`
- `audit_output/audit_report.json`

Default behaviour:

- exits `0` when no unintended finding reaches the failure threshold
- exits `2` when any `UNINTENDED` finding is `STANDARD` or louder
- always exits non-zero for hard input failures

Use `--fail-on glance` to fail only on the most immediately visible `GLANCE`
findings:

```sh
dfa --manifest manifest.yaml --out audit_output --fail-on glance
```

Hard input failures are independent of `--fail-on`. They include unreadable files,
empty tables, and readable files where none of the manifest-declared columns are
present.

## Manifest

Relative file paths are resolved relative to the manifest file.

```yaml
files:
  - path: line2_cmm_study.csv
    date_col: date
    columns:
      date: {role: date}
      measurement: {role: measurement, unit: mm, declared_resolution: 0.001}
      subgroup: {role: subgroup_id}
      sample: {role: sample_id}
      shift: {role: category}

spec_limits: {lsl: 11.94, usl: 12.06}

calendar:
  workdays: Mon-Fri
  holidays: [2026-04-03, 2026-04-06]

regimes: []

intended_seams:
  - "between-day drift with periodic resets; within-subgroup sigma << overall sigma"

cross_checks:
  - {kind: exact, left: "fileA.csv:col", right: "fileB.csv:col", key: date}
```

Tabular input handling:

- `.csv`
- `.tsv`
- `.xlsx`, `.xlsm` (supported by the base installation through openpyxl)
- `.xls` is recognized and delegated to pandas, but requires a separately
  installed compatible engine and is not guaranteed by the base dependencies

Common column roles:

- `measurement`: numeric value to audit as the main measured signal
- `count` or `rate`: numeric process output to audit
- `date`: date/time column
- `category`: grouping column, such as shift or line
- `subgroup_id`, `sample_id`, `sequence`, `id`: structural columns skipped for
  time-series anomaly checks
- `ignore`: column to leave out of time-series anomaly checks

## Checks

Current check families:

- Time order: trends, changepoints, autocorrelation, periodicity, sawtooth/reset
  patterns.
- Variance structure: within-vs-overall sigma, Cpk/Ppk gap, subgroup mean control
  signals.
- Distribution: normality, multimodality, outlier clusters.
- Digits and quantization: declared-vs-observed resolution, last-digit imbalance,
  duplicate rows, value reuse.
- Calendar/bookkeeping: date parseability, chronological order, rows-per-day
  regularity, declared workday gaps.
- Synthetic tells: implausibly stable rolling variance and near-identical numeric
  columns.
- Cross-file checks: declared exact identities and declared noisy relationships.

Findings are classified as `INTENDED` when they match an `intended_seams` entry
with sufficient confidence. Other findings remain `UNINTENDED`.

## Development

With uv:

```sh
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

To validate the publishable artifacts without uploading them, start with no
stale files in `dist/`, then run:

```sh
uv sync --extra dev --frozen
uv run --frozen python -m build
uv run --frozen twine check --strict dist/*
uv run --frozen python scripts/inspect_distribution.py dist
```

These commands build and inspect a wheel and source distribution locally. They
do not upload to PyPI or any other package index. CI additionally installs the
wheel in an isolated environment outside the source tree and smoke-tests every
supported command.

With pip:

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m ruff format .
```

The smoke tests can also be run directly:

```sh
python tests/smoke_input_hard_fail.py
python tests/smoke_shape_c.py
```

## Repository Notes

- See `CONTRIBUTING.md` for development and validation guidance.
- See `DESIGN.md` for current contracts, architecture, and limitations.
- See `SECURITY.md` for supported versions, private reporting guidance, and the
  threat model.
- See `CHANGELOG.md` for notable unreleased changes.
- Generated files such as `__pycache__`, build artifacts, virtual environments,
  and local audit reports are ignored by Git.
- Licensed under the MIT License. See `LICENSE`.
