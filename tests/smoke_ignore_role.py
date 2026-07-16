from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_forensics_auditor.auditor import Auditor
from data_forensics_auditor.manifest import load_manifest


def _run(manifest_text: str, root: Path):
    manifest_path = root / "manifest.yaml"
    manifest_path.write_text(manifest_text.strip(), encoding="utf-8")
    auditor = Auditor(load_manifest(manifest_path))
    findings = auditor.run()
    return auditor.exit_code(), findings


def test_ignore_role_suppresses_distribution_checks() -> None:
    """A numeric column declared role: ignore must produce NO findings, even
    with an extreme outlier that would trip the 4-SD distribution check.
    (Regression: distribution/digit checks previously ran on ignored columns.)
    The positive control asserts the same data DOES fire when not ignored,
    so a conservative fix cannot hide a real miss as a pass."""
    rows = ["row_id,heterogeneous_metric"]
    rows += [f"{i},{1.0 + (i % 7) * 0.13:.2f}" for i in range(1, 31)]
    rows += ["31,26843.2"]  # extreme outlier, >4 SD from the mean
    csv_text = "\n".join(rows) + "\n"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "catalog.csv").write_text(csv_text, encoding="utf-8")

        # Positive control: role measurement -> outlier check must fire.
        code, findings = _run(
            """
files:
  - path: catalog.csv
    columns:
      row_id: {role: id}
      heterogeneous_metric: {role: measurement}
""",
            root,
        )
        control_titles = [f.title for f in findings if f.column == "heterogeneous_metric"]
        if not any("Outlier" in title for title in control_titles):
            raise AssertionError(
                "Positive control failed: outlier not detected on "
                f"measurement role ({control_titles})"
            )

        # Regression: role ignore -> the same column must produce no findings.
        code, findings = _run(
            """
files:
  - path: catalog.csv
    columns:
      row_id: {role: id}
      heterogeneous_metric: {role: ignore}
""",
            root,
        )
        ignored_findings = [f.title for f in findings if f.column == "heterogeneous_metric"]
        if ignored_findings:
            raise AssertionError(f"role: ignore column still produced findings: {ignored_findings}")
        if code != 0:
            raise AssertionError(f"Expected exit 0 with ignored column, got {code}")


if __name__ == "__main__":
    test_ignore_role_suppresses_distribution_checks()
    print("ok")
