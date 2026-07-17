# Changelog

All notable changes use a Keep a Changelog-style structure. The project follows
semantic versioning for the CLI and Python package.

No versioned Git releases have been published from this repository. Current
work is recorded under `Unreleased` without implying publication to PyPI.

## [Unreleased]

### Added

- Offline audit CLI for manifest-declared tabular datasets, with deterministic
  Markdown and JSON report structures.
- Statistical, time-order, variance, distribution, digit, calendar,
  bookkeeping, synthetic-data, and declared cross-file checks.
- Intended-seam classification, finding loudness, configurable failure
  threshold, and hard input-failure reporting.
- Cross-platform CI on Python 3.10 and 3.13, plus frozen dependency resolution.
- Wheel and source-distribution builds, strict metadata validation, adversarial
  artifact-content inspection, and isolated installed-command smoke tests.
- Contribution, design, security, and changelog documentation.
- Exact package metadata links for the project homepage, source repository,
  issue tracker, and changelog.

### Changed

- Standardized the supported invocations on `dfa`,
  `data-forensics-auditor`, and `python -m data_forensics_auditor`.
- Added `--version` consistently to all supported invocation forms.
- Kept development and test material in the source distribution while retaining
  a lean runtime wheel.
- Clarified that modern `.xlsx` and `.xlsm` workbooks use the base openpyxl
  dependency, while legacy `.xls` files require a separately installed
  compatible engine.

### Fixed

- Treat unreadable files, empty tables, and readable tables containing none of
  their declared columns as hard failures.
- Prevent a column declared with the `ignore` role from entering its ordinary
  time-series, distribution, and digit-check path.
- Align the documented command names, installed entry points, and displayed
  argparse program name.
- Apply finding failure thresholds by visibility rank: the default `STANDARD`
  threshold now also fails louder `GLANCE` findings, while `GLANCE` fails only
  `GLANCE`.

### Security

- Reject release artifacts containing unsafe archive paths, links or special
  members, common credential material, local path markers, or development
  residue during distribution validation.
