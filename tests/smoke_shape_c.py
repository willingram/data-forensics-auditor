from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_forensics_auditor.auditor import Auditor
from data_forensics_auditor.manifest import load_manifest


def test_shape_c_intended_findings_are_detected() -> None:
    rng = np.random.default_rng(42)
    rows = []
    start = pd.Timestamp("2026-03-02")
    workdays = [day for day in pd.date_range(start, periods=56, freq="D") if day.weekday() < 5]
    offset = -0.018
    for day_index, day in enumerate(workdays[:40]):
        if day_index and day_index % 8 == 0:
            offset -= 0.020
        offset += 0.0055
        for part in range(5):
            rows.append(
                {
                    "date": day.strftime("%Y-%m-%d"),
                    "subgroup": f"SG{day_index + 1:03d}",
                    "part": part + 1,
                    "measurement": round(12.000 + offset + rng.normal(0, 0.0012), 3),
                }
            )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        csv_path = root / "line2_cmm_study.csv"
        manifest_path = root / "manifest.yaml"
        intended_seam = (
            "between-day drift with periodic resets, step autocorrelation, "
            "out of control subgroup means, within-subgroup sigma << overall sigma, "
            "Cpk Ppk gap"
        )
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        manifest_path.write_text(
            f"""
files:
  - path: line2_cmm_study.csv
    date_col: date
    columns:
      date: {{role: date}}
      subgroup: {{role: subgroup_id}}
      part: {{role: sequence}}
      measurement: {{role: measurement, unit: mm, declared_resolution: 0.001}}
spec_limits: {{lsl: 11.94, usl: 12.06}}
intended_seams:
  - "{intended_seam}"
""".strip(),
            encoding="utf-8",
        )
        auditor = Auditor(load_manifest(manifest_path))
        findings = auditor.run()
        titles = {finding.title for finding in findings}
        required = {
            "Within-subgroup sigma is much smaller than overall sigma",
            "Cpk/Ppk gap",
            "Sawtooth or periodic reset pattern",
        }
        missing = required - titles
        if missing:
            raise AssertionError(f"Missing expected findings: {sorted(missing)}")
        if auditor.exit_code() != 0:
            for finding in findings:
                print(
                    f"{finding.classification} {finding.loudness}: "
                    f"{finding.title} / {finding.check}"
                )
            raise AssertionError(
                "All expected Shape-C smoke findings should be classified INTENDED"
            )


def main() -> int:
    test_shape_c_intended_findings_are_detected()
    print("Shape-C smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
