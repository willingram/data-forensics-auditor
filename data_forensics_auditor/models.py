from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOUDNESS_ORDER = {"GLANCE": 3, "STANDARD": 2, "DEEP": 1}


@dataclass
class Finding:
    check: str
    title: str
    detail: str
    loudness: str
    file: str | None = None
    column: str | None = None
    rows: str | None = None
    classification: str = "UNINTENDED"
    hard_fail: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def sort_key(self) -> tuple[int, int, str]:
        unintended = 1 if self.classification == "UNINTENDED" else 0
        return (unintended, LOUDNESS_ORDER.get(self.loudness, 0), self.title)

    def to_json(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "loudness": self.loudness,
            "check": self.check,
            "title": self.title,
            "detail": self.detail,
            "file": self.file,
            "column": self.column,
            "rows": self.rows,
            "hard_fail": self.hard_fail,
            "evidence": self.evidence,
        }


@dataclass
class FileSpec:
    path: Path
    display_path: str
    date_col: str | None = None
    columns: dict[str, dict[str, Any]] = field(default_factory=dict)

    def role_columns(self, role: str) -> list[str]:
        return [
            name for name, meta in self.columns.items() if str(meta.get("role", "")).lower() == role
        ]

    def declared_resolution(self, column: str) -> float | None:
        meta = self.columns.get(column, {})
        value = meta.get("declared_resolution")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


@dataclass
class Manifest:
    path: Path
    files: list[FileSpec]
    spec_limits: dict[str, float] | None = None
    calendar: dict[str, Any] = field(default_factory=dict)
    regimes: list[dict[str, Any]] = field(default_factory=list)
    intended_seams: list[str] = field(default_factory=list)
    cross_checks: list[dict[str, Any]] = field(default_factory=list)
