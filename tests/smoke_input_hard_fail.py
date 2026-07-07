from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_forensics_auditor.auditor import Auditor
from data_forensics_auditor.manifest import load_manifest


def _run(manifest_text: str, root: Path) -> tuple[int, list[str]]:
    manifest_path = root / "manifest.yaml"
    manifest_path.write_text(manifest_text.strip(), encoding="utf-8")
    auditor = Auditor(load_manifest(manifest_path))
    findings = auditor.run()
    return auditor.exit_code(), [finding.title for finding in findings if finding.hard_fail]


def test_missing_file_hard_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        code, hard_titles = _run(
            """
files:
  - path: missing.csv
    columns:
      value: {role: measurement}
""",
            root,
        )
        if code == 0 or "File could not be read" not in hard_titles:
            raise AssertionError("Missing file did not hard-fail")


def test_empty_table_hard_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        empty_path = root / "empty.csv"
        empty_path.write_text("value\n", encoding="utf-8")
        code, hard_titles = _run(
            """
files:
  - path: empty.csv
    columns:
      value: {role: measurement}
""",
            root,
        )
        if code == 0 or "Empty table" not in hard_titles:
            raise AssertionError("Empty table did not hard-fail")


def test_no_declared_columns_hard_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pd.DataFrame({"other": [1, 2, 3]}).to_csv(root / "wrong_columns.csv", index=False)
        code, hard_titles = _run(
            """
files:
  - path: wrong_columns.csv
    columns:
      value: {role: measurement}
""",
            root,
        )
        if code == 0 or "No declared columns found" not in hard_titles:
            raise AssertionError("Zero declared columns found did not hard-fail")


def main() -> int:
    test_missing_file_hard_fails()
    test_empty_table_hard_fails()
    test_no_declared_columns_hard_fails()
    print("Input hard-fail smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
