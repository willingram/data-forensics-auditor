# Security policy

## Supported versions

Until versioned releases are published, security fixes are made on the latest
maintained code on `main`. If versioned releases are published, fixes will
target the latest released minor version.

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the repository owner. Do
not put private datasets or reports, credentials, personal data, exploit
details, or other sensitive material in a public issue. Include the smallest
fictional manifest and dataset that reproduce the problem where possible.

No response-time or remediation-time service level is promised.

## Threat model

DFA crosses local trust boundaries when it reads a manifest and the filesystem
paths that manifest names, parses CSV/TSV/workbook content, and writes reports
to a caller-selected output directory.

- Relative inputs resolve from the manifest directory, but absolute and
  escaping paths are accepted. A manifest can therefore direct DFA to any file
  readable by the running account.
- CSV and TSV files are parsed by pandas; workbook files are parsed through
  pandas and an installed spreadsheet engine such as openpyxl. Malformed files
  and parser-library vulnerabilities remain possible.
- DFA does not deliberately execute formulas or macros, evaluate active
  workbook content, or fetch external resources referenced by workbooks. It
  does not sanitize formulas, macros, links, or embedded objects for later use
  in other applications.
- Inputs are loaded and analyzed in memory without enforced byte, row, workbook,
  CPU-time, or memory bounds. Large or adversarial inputs can exhaust local
  resources.
- The selected output directory is created if needed. The fixed report
  filenames within it are replaced, and report publication is not
  transactional.
- Reports contain the resolved manifest path and may contain manifest-derived
  filenames, column names, and evidence. Review reports before sharing them;
  they can disclose local paths or dataset structure and may contain unescaped
  text.

DFA is not a sandbox, malware scanner, content sanitizer, or safe file viewer.
Treat untrusted manifests, datasets, workbooks, and generated reports
accordingly, and run the tool with only the filesystem permissions it needs.
