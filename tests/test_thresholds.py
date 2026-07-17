from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_forensics_auditor.auditor import Auditor, _fails_threshold
from data_forensics_auditor.models import Finding, Manifest


@pytest.mark.parametrize(
    ("loudness", "fail_on", "expected"),
    [
        ("GLANCE", "GLANCE", True),
        ("STANDARD", "GLANCE", False),
        ("DEEP", "GLANCE", False),
        ("GLANCE", "STANDARD", True),
        ("STANDARD", "STANDARD", True),
        ("DEEP", "STANDARD", False),
    ],
)
def test_ranked_failure_threshold_matrix(loudness: str, fail_on: str, expected: bool) -> None:
    assert _fails_threshold(loudness, fail_on) is expected


@pytest.mark.parametrize(
    ("loudness", "fail_on", "expected_exit"),
    [
        ("GLANCE", "GLANCE", 2),
        ("STANDARD", "GLANCE", 0),
        ("DEEP", "GLANCE", 0),
        ("GLANCE", "STANDARD", 2),
        ("STANDARD", "STANDARD", 2),
        ("DEEP", "STANDARD", 0),
    ],
)
def test_exit_code_uses_ranked_threshold(loudness: str, fail_on: str, expected_exit: int) -> None:
    auditor = _auditor_with(Finding("test", "title", "detail", loudness))

    assert auditor.exit_code(fail_on=fail_on) == expected_exit


@pytest.mark.parametrize(
    ("loudness", "fail_on", "expected_count"),
    [
        ("GLANCE", "GLANCE", 1),
        ("STANDARD", "GLANCE", 0),
        ("DEEP", "GLANCE", 0),
        ("GLANCE", "STANDARD", 1),
        ("STANDARD", "STANDARD", 1),
        ("DEEP", "STANDARD", 0),
    ],
)
def test_report_summary_uses_ranked_threshold(
    tmp_path: Path, loudness: str, fail_on: str, expected_count: int
) -> None:
    auditor = _auditor_with(Finding("test", "title", "detail", loudness))

    auditor.write_reports(tmp_path, fail_on=fail_on)

    report = json.loads((tmp_path / "audit_report.json").read_text(encoding="utf-8"))
    assert report["summary"]["unintended_at_or_above_threshold"] == expected_count


@pytest.mark.parametrize("loudness", ["GLANCE", "STANDARD", "DEEP"])
@pytest.mark.parametrize("fail_on", ["GLANCE", "STANDARD"])
def test_intended_findings_do_not_trigger_threshold(loudness: str, fail_on: str) -> None:
    auditor = _auditor_with(Finding("test", "title", "detail", loudness, classification="INTENDED"))

    assert auditor.exit_code(fail_on=fail_on) == 0


def test_hard_failure_is_independent_of_classification_loudness_and_threshold(
    tmp_path: Path,
) -> None:
    auditor = _auditor_with(
        Finding(
            "input",
            "hard failure",
            "detail",
            "DEEP",
            classification="INTENDED",
            hard_fail=True,
        )
    )

    assert auditor.exit_code(fail_on="GLANCE") == 2
    auditor.write_reports(tmp_path, fail_on="GLANCE")
    report = json.loads((tmp_path / "audit_report.json").read_text(encoding="utf-8"))
    assert report["summary"]["hard_failures"] == 1
    assert report["summary"]["unintended_at_or_above_threshold"] == 0


@pytest.mark.parametrize(
    ("loudness", "fail_on"),
    [
        ("UNKNOWN", "STANDARD"),
        ("STANDARD", "UNKNOWN"),
    ],
)
def test_unknown_loudness_or_threshold_fails_closed(loudness: str, fail_on: str) -> None:
    assert _fails_threshold(loudness, fail_on) is True


def _auditor_with(*findings: Finding) -> Auditor:
    auditor = Auditor(Manifest(path=Path("manifest.yaml"), files=[]))
    auditor.findings = list(findings)
    return auditor
