# Design decisions and compatibility contract

## Product boundary

One invocation loads one local manifest, reads the listed local tabular files,
runs an in-memory audit, and writes two reports. DFA makes no network requests
and does not modify source datasets. It is a screening tool for
analyst-visible forensic signals, not a formal statistical package or a
declaration that data is genuine or fabricated.

The supported entry points are `dfa`, `data-forensics-auditor`, and
`python -m data_forensics_auditor`. They share one parser and report behavior.
`--manifest` and `--out` are required for an audit; `--fail-on` accepts
`standard` (the default) or `glance`.

## Manifest and input boundary

The manifest is a UTF-8 mapping with these recognized top-level fields:

- `files`: file entries containing `path`, optional `date_col`, and a `columns`
  mapping;
- `spec_limits`: optional numeric `lsl` and `usl` values;
- `calendar`: optional `workdays` and `holidays` declarations;
- `regimes`: accepted string or mapping entries;
- `intended_seams`: text used by the heuristic intended-finding classifier; and
- `cross_checks`: declared `exact` or `noisy` relationships between two
  `file:column` references joined by a key.

Relative dataset paths resolve from the manifest directory. Absolute paths are
also accepted. DFA does not confine input paths to the manifest directory, so a
manifest must be trusted with the authority to read files available to the
running user.

The installed runtime has a deliberately small YAML-subset parser. If PyYAML is
already available, the loader instead uses `yaml.safe_load`. The portable
manifest contract is therefore the mapping, list, quoted/unquoted scalar, and
inline collection forms shown in `README.md`; richer YAML features are not a
compatibility guarantee. The loader constructs Python objects but does not
validate against a published schema. Unknown fields may be ignored, and some
malformed values fail before an audit report can be produced.

CSV and TSV input is delegated to pandas. `.xlsx`, `.xlsm`, and `.xls` suffixes
are delegated to `pandas.read_excel`; the base installation supplies openpyxl
for modern workbooks. Legacy `.xls` reading depends on an engine available in
the environment and is not guaranteed by the base dependency set. Other
suffixes produce a hard input finding.

Column declarations guide checks rather than forming a strict schema:

- `date_col` selects the column parsed and ordered as dates; a `date` role alone
  does not do so.
- `category` may split time-series checks using the first suitable declared
  category.
- `subgroup_id` and `measurement` enable within/overall variance, capability,
  reset, and control-chart checks.
- `ignore`, `sample_id`, `subgroup_id`, `sequence`, and `id` are excluded from
  ordinary time-series checks. `ignore` also skips the ordinary per-column
  distribution and digit checks, but roles are not a general information-flow
  barrier for every cross-column check.
- `declared_resolution` drives digit/quantization checks. Other metadata such
  as `unit` is retained only in the manifest input and is not currently used by
  the auditor.

`regimes` are normalized by the loader but are not currently consumed by an
audit check. Their presence must not be taken as evidence of regime-aware
analysis.

## Check and classification pipeline

Files are processed in manifest order. A readable, non-empty table enters the
file checks if it contains at least one declared column or if no columns were
declared. Those checks cover duplicate rows; dates/calendars; numeric
time-order, distribution, and digit patterns; variance structure; and
synthetic-data heuristics. Declared cross-file checks run after all readable
files. Findings are then compared heuristically with `intended_seams`,
classified, sorted, and reported.

A finding is `UNINTENDED` unless the text/semantic-bucket matcher reaches its
current confidence threshold for a declared intended seam. A match records the
seam and confidence in evidence and changes the classification to `INTENDED`.
This is a heuristic label, not a proof of analyst intent.

Loudness values have a tested visibility order of `GLANCE` (3), `STANDARD` (2),
and `DEEP` (1). They express expected analyst visibility; they are not severity
or statistical-confidence levels. The threshold behavior is:

- the default `STANDARD` threshold fails unintended `GLANCE` and `STANDARD`
  findings;
- the `GLANCE` threshold fails unintended `GLANCE` findings only; and
- `DEEP` findings do not fail either current CLI threshold.

Hard input failures are independent of classification, loudness, and the
selected threshold and always make the controlled audit result exit `2`.
Argparse constrains CLI threshold values; internal calls with an unknown
loudness or threshold fail closed.

## Reports and exits

DFA creates the requested output directory and replaces
`audit_report.json` and `audit_report.md` within it. The caller chooses this path;
there is no containment check or transactional multi-file publication.

The JSON report contains the resolved manifest path, a summary, and ordered
finding objects. Each finding exposes `classification`, `loudness`, `check`,
`title`, `detail`, optional location fields, `hard_fail`, and `evidence`. The
Markdown report presents the same findings grouped by classification.
Filenames, fields, JSON key ordering, and finding sorting are stable contract
areas. Byte-for-byte equality across pandas/numpy/Python versions or operating
systems is not promised because numeric and parser behavior comes from those
dependencies.

A completed audit returns `0` when its findings do not meet the current failure
rules and `2` for a hard failure or threshold-matching unintended finding.
Argparse also returns `2` for command-line usage errors. Manifest loading,
unexpected internal exceptions, and report-writing failures are not converted
to structured findings; they may produce a traceback and the interpreter's
non-zero exit rather than reports.

## Distribution contract

The release artifacts are one pure-Python wheel and one source distribution.
The wheel contains runtime package code, metadata, entry points, and the
license. The source distribution additionally contains the repository
documentation, tests, and distribution inspector so it can rebuild and
validate independently. `scripts/inspect_distribution.py` enforces required
members, metadata and entry points, portable member paths, and absence of
common credentials, local path markers, and build/test residue.

Both artifacts publish exact project links for the homepage, source repository,
issue tracker, and changelog. Those four labels and destinations are validated
as package metadata rather than inferred from the checkout.

Artifact inspection is a release preflight, not a general archive-security or
malware scan. Building and validating distributions does not publish them.

## Security and resource limitations

Input tables and manifests may be malformed, adversarial, or very large. DFA
has no file-size, row-count, workbook-complexity, memory, CPU-time, or parser
timeout limits. Dataset parsing and most analysis happen in memory.

DFA does not deliberately execute spreadsheet formulas or macros, evaluate
active content, or fetch workbook links. It also does not sanitize workbooks,
prove that parser dependencies are vulnerability-free, escape all
manifest-derived Markdown, sandbox parsing, or scan for malware. Open
untrusted files and generated reports with the same care as other untrusted
local content. See `SECURITY.md` for the reporting policy and threat model.

Future schemas, bounded parsing, transactional report writes, or additional
check families are design possibilities, not current guarantees.
