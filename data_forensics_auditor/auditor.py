from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .io import read_table
from .models import LOUDNESS_ORDER, Finding, Manifest

STOP_WORDS = {
    "with",
    "from",
    "that",
    "this",
    "the",
    "and",
    "for",
    "between",
    "within",
    "overall",
    "change",
    "changes",
    "declared",
    "intended",
}


TIME_SERIES_SKIP_ROLES = {"ignore", "sample_id", "subgroup_id", "sequence", "id"}


class Auditor:
    def __init__(self, manifest: Manifest):
        self.manifest = manifest
        self.tables: dict[str, pd.DataFrame] = {}
        self.findings: list[Finding] = []

    def run(self) -> list[Finding]:
        for file_spec in self.manifest.files:
            try:
                df = read_table(file_spec.path)
            except Exception as exc:
                self._add(
                    Finding(
                        check="input",
                        title="File could not be read",
                        detail=f"{file_spec.display_path}: {exc}",
                        loudness="GLANCE",
                        file=file_spec.display_path,
                        hard_fail=True,
                    )
                )
                continue

            self.tables[file_spec.display_path] = df
            self._audit_file(file_spec, df)

        self._audit_cross_checks()
        self._classify_findings()
        self.findings.sort(key=lambda item: item.sort_key(), reverse=True)
        return self.findings

    def write_reports(self, out_dir: str | Path, fail_on: str = "STANDARD") -> None:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        findings = [item.to_json() for item in self.findings]
        payload = {
            "manifest": str(self.manifest.path),
            "summary": {
                "findings": len(findings),
                "fail_on": fail_on,
                "hard_failures": sum(1 for item in self.findings if item.hard_fail),
                "unintended_at_or_above_threshold": sum(
                    1
                    for item in self.findings
                    if item.classification == "UNINTENDED"
                    and _fails_threshold(item.loudness, fail_on)
                ),
            },
            "findings": findings,
        }
        (out_path / "audit_report.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        (out_path / "audit_report.md").write_text(self._markdown_report(payload), encoding="utf-8")

    def exit_code(self, fail_on: str = "STANDARD") -> int:
        if any(finding.hard_fail for finding in self.findings):
            return 2
        for finding in self.findings:
            if finding.classification == "UNINTENDED" and _fails_threshold(
                finding.loudness, fail_on
            ):
                return 2
        return 0

    def _audit_file(self, file_spec, df: pd.DataFrame) -> None:
        if len(df) == 0:
            self._add(
                Finding(
                    check="input",
                    title="Empty table",
                    detail="The file contains headers but no rows.",
                    loudness="GLANCE",
                    file=file_spec.display_path,
                    hard_fail=True,
                )
            )
            return

        missing_declared = sorted(set(file_spec.columns) - set(df.columns))
        found_declared = sorted(set(file_spec.columns) & set(df.columns))
        if file_spec.columns and not found_declared:
            self._add(
                Finding(
                    check="input",
                    title="No declared columns found",
                    detail=(
                        "The file was readable, but none of the manifest-declared "
                        "columns were present."
                    ),
                    loudness="GLANCE",
                    file=file_spec.display_path,
                    hard_fail=True,
                    evidence={"declared_columns": sorted(file_spec.columns)},
                )
            )
            return

        if missing_declared:
            self._add(
                Finding(
                    check="input",
                    title="Declared columns missing",
                    detail=(
                        f"Manifest declares columns that are absent: {', '.join(missing_declared)}."
                    ),
                    loudness="GLANCE",
                    file=file_spec.display_path,
                    evidence={"missing_columns": missing_declared},
                )
            )

        duplicate_fraction = float(df.duplicated().mean())
        if duplicate_fraction >= 0.05 and len(df) >= 20:
            self._add(
                Finding(
                    check="digit_bookkeeping",
                    title="Exact duplicate rows",
                    detail=f"{duplicate_fraction:.1%} of rows are byte-for-byte duplicates.",
                    loudness="GLANCE" if duplicate_fraction >= 0.20 else "STANDARD",
                    file=file_spec.display_path,
                    evidence={"duplicate_fraction": duplicate_fraction},
                )
            )

        date_series = self._date_series(file_spec, df)
        order = self._analysis_order(date_series, len(df))
        self._calendar_checks(file_spec, df, date_series)

        numeric_cols = [
            col
            for col in df.columns
            if pd.api.types.is_numeric_dtype(df[col]) and df[col].notna().sum() >= 8
        ]

        for col in numeric_cols:
            role = file_spec.columns.get(col, {}).get("role")
            if str(role).lower() == "ignore":
                continue
            values = pd.to_numeric(df[col], errors="coerce")
            if values.notna().sum() < 8:
                continue
            ordered = values.iloc[order].reset_index(drop=True)
            if not self._should_skip_time_series(file_spec, col, values):
                for segment, segment_values, ordered_dates in self._time_series_segments(
                    file_spec, df, col, date_series
                ):
                    self._time_order_checks(
                        file_spec, col, segment_values, ordered_dates, segment=segment
                    )
            self._distribution_checks(file_spec, col, ordered, role)
            self._digit_checks(file_spec, col, ordered)

        self._variance_structure_checks(file_spec, df, numeric_cols)
        self._synthetic_rng_checks(file_spec, df, numeric_cols)

    def _date_series(self, file_spec, df: pd.DataFrame) -> pd.Series | None:
        if not file_spec.date_col or file_spec.date_col not in df.columns:
            return None
        parsed = pd.to_datetime(df[file_spec.date_col], errors="coerce")
        bad = int(parsed.isna().sum())
        if bad:
            self._add(
                Finding(
                    check="calendar",
                    title="Unparseable dates",
                    detail=f"{bad} rows in {file_spec.date_col} could not be parsed as dates.",
                    loudness="STANDARD" if bad / len(df) < 0.05 else "GLANCE",
                    file=file_spec.display_path,
                    column=file_spec.date_col,
                    evidence={"bad_rows": bad},
                )
            )
        if parsed.notna().sum() < 2:
            return None
        if not parsed.dropna().is_monotonic_increasing:
            self._add(
                Finding(
                    check="calendar",
                    title="Rows are not in chronological order",
                    detail="The declared date column is not monotonically increasing.",
                    loudness="STANDARD",
                    file=file_spec.display_path,
                    column=file_spec.date_col,
                )
            )
        return parsed

    def _analysis_order(self, dates: pd.Series | None, n: int) -> list[int]:
        if dates is None:
            return list(range(n))
        sortable = pd.DataFrame({"row": np.arange(n), "date": dates.reset_index(drop=True)})
        sortable = sortable.sort_values(["date", "row"], na_position="last")
        return [int(item) for item in sortable["row"].tolist()]

    def _should_skip_time_series(self, file_spec, col: str, values: pd.Series) -> bool:
        role = str(file_spec.columns.get(col, {}).get("role", "")).lower()
        if role in TIME_SERIES_SKIP_ROLES:
            return True
        clean = values.dropna()
        if len(clean) < 8:
            return True
        arr = clean.to_numpy(dtype=float)
        if np.nanstd(arr) <= 1e-12:
            return True
        if not _is_integer_like(arr):
            return False
        unique = np.unique(arr)
        if len(unique) >= max(8, len(arr) * 0.7) and (
            np.all(np.diff(arr) >= 0) or np.all(np.diff(arr) <= 0)
        ):
            return True
        return _is_small_repeating_cycle(arr)

    def _time_series_segments(
        self, file_spec, df: pd.DataFrame, col: str, dates: pd.Series | None
    ) -> list[tuple[str, pd.Series, pd.Series | None]]:
        category_cols = [
            name
            for name in file_spec.role_columns("category")
            if name in df.columns and 1 < df[name].nunique(dropna=True) <= 10
        ]
        if not category_cols:
            order = self._analysis_order(dates, len(df))
            ordered_dates = dates.iloc[order].reset_index(drop=True) if dates is not None else None
            return [
                (
                    "all",
                    pd.to_numeric(df[col], errors="coerce").iloc[order].reset_index(drop=True),
                    ordered_dates,
                )
            ]

        category_col = category_cols[0]
        segments: list[tuple[str, pd.Series, pd.Series | None]] = []
        for category_value, group in df.groupby(category_col, dropna=False, sort=False):
            if len(group) < 20:
                continue
            group_dates = dates.loc[group.index] if dates is not None else None
            order = self._analysis_order(group_dates, len(group))
            values = pd.to_numeric(group[col], errors="coerce").iloc[order].reset_index(drop=True)
            ordered_dates = (
                group_dates.iloc[order].reset_index(drop=True) if group_dates is not None else None
            )
            segments.append((f"{category_col}={category_value}", values, ordered_dates))
        return segments

    def _calendar_checks(self, file_spec, df: pd.DataFrame, dates: pd.Series | None) -> None:
        if dates is None or not self.manifest.calendar:
            return
        clean_dates = dates.dropna().dt.normalize()
        if clean_dates.empty:
            return

        counts = clean_dates.value_counts().sort_index()
        if len(counts) >= 10:
            mode_count = int(counts.mode().iloc[0])
            off_mode = counts[counts != mode_count]
            edge_dates = {counts.index.min(), counts.index.max()}
            material = off_mode[~off_mode.index.isin(edge_dates)]
            if len(material) >= 3 and len(material) / len(counts) > 0.10:
                self._add(
                    Finding(
                        check="calendar",
                        title="Rows per day are irregular",
                        detail=(
                            f"Most days have {mode_count} rows, but "
                            f"{len(material)} interior days differ."
                        ),
                        loudness="STANDARD",
                        file=file_spec.display_path,
                        column=file_spec.date_col,
                        evidence={
                            "mode_count": mode_count,
                            "irregular_days": [d.strftime("%Y-%m-%d") for d in material.index[:10]],
                        },
                    )
                )

        workdays = self.manifest.calendar.get("workdays")
        if workdays:
            expected = self._expected_workdays(clean_dates.min(), clean_dates.max())
            missing = sorted(set(expected) - set(clean_dates.unique()))
            if missing:
                self._add(
                    Finding(
                        check="calendar",
                        title="Missing declared workdays",
                        detail=f"{len(missing)} declared workdays have no rows.",
                        loudness="STANDARD" if len(missing) <= 3 else "GLANCE",
                        file=file_spec.display_path,
                        column=file_spec.date_col,
                        evidence={
                            "missing_dates": [
                                pd.Timestamp(d).strftime("%Y-%m-%d") for d in missing[:20]
                            ]
                        },
                    )
                )

    def _expected_workdays(self, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
        workdays = str(self.manifest.calendar.get("workdays", "Mon-Fri"))
        if workdays != "Mon-Fri":
            return []
        holidays = {
            pd.Timestamp(item).normalize()
            for item in self.manifest.calendar.get("holidays", []) or []
        }
        days = pd.date_range(start.normalize(), end.normalize(), freq="D")
        return [day for day in days if day.weekday() < 5 and day.normalize() not in holidays]

    def _time_order_checks(
        self,
        file_spec,
        col: str,
        values: pd.Series,
        ordered_dates: pd.Series | None,
        segment: str = "all",
    ) -> None:
        clean = values.dropna().astype(float)
        n = len(clean)
        if n < 20:
            return
        arr = clean.to_numpy()
        if np.nanstd(arr) <= 1e-12:
            return

        x = np.arange(n)
        cp = self._best_changepoint(arr)
        step_found = False
        if cp is not None:
            idx, effect, p_value, before_mean, after_mean = cp
            if abs(effect) >= 1.0 and p_value < 0.001:
                step_found = True
                rows = f"before/after row {idx + 1}"
                if ordered_dates is not None and ordered_dates.notna().sum() == len(ordered_dates):
                    date_at = pd.Timestamp(ordered_dates.iloc[idx]).strftime("%Y-%m-%d")
                    rows = f"near {date_at}"
                rows = _with_segment(segment, rows)
                self._add(
                    Finding(
                        check="time_order",
                        title="Step change or changepoint",
                        detail=(
                            f"{col} shifts from mean {before_mean:.4g} to {after_mean:.4g} "
                            f"(effect={effect:.2f} pooled SD, p={p_value:.2g})."
                        ),
                        loudness=_changepoint_loudness(effect),
                        file=file_spec.display_path,
                        column=col,
                        rows=rows,
                        evidence={
                            "index": int(idx),
                            "effect_sd": float(effect),
                            "p_value": float(p_value),
                            "before_mean": float(before_mean),
                            "after_mean": float(after_mean),
                            **_segment_evidence(segment),
                        },
                    )
                )

        if not step_found:
            tau, p_value = _kendall_tau(x, arr)
            if np.isfinite(tau) and np.isfinite(p_value) and p_value < 0.01 and abs(tau) >= 0.25:
                self._add(
                    Finding(
                        check="time_order",
                        title="Time-ordered trend",
                        detail=f"{col} has a Mann-Kendall trend (tau={tau:.2f}, p={p_value:.2g}).",
                        loudness="GLANCE" if abs(tau) >= 0.45 else "STANDARD",
                        file=file_spec.display_path,
                        column=col,
                        rows=_with_segment(segment, None),
                        evidence={
                            "tau": float(tau),
                            "p_value": float(p_value),
                            **_segment_evidence(segment),
                        },
                    )
                )

            for lag in range(1, min(8, n // 4 + 1)):
                left = arr[:-lag]
                right = arr[lag:]
                corr = np.corrcoef(left, right)[0, 1]
                if np.isfinite(corr) and abs(corr) >= 0.65:
                    self._add(
                        Finding(
                            check="time_order",
                            title=f"Strong lag-{lag} autocorrelation",
                            detail=f"{col} has autocorrelation r={corr:.2f} at lag {lag}.",
                            loudness="GLANCE" if abs(corr) >= 0.85 else "STANDARD",
                            file=file_spec.display_path,
                            column=col,
                            rows=_with_segment(segment, None),
                            evidence={
                                "lag": lag,
                                "correlation": float(corr),
                                **_segment_evidence(segment),
                            },
                        )
                    )
                    break

        spectral = self._periodicity(arr)
        if spectral is not None:
            period, dominance = spectral
            self._add(
                Finding(
                    check="time_order",
                    title="Periodic pattern",
                    detail=f"{col} has a dominant period near {period:.1f} rows.",
                    loudness="STANDARD" if dominance < 0.65 else "GLANCE",
                    file=file_spec.display_path,
                    column=col,
                    rows=_with_segment(segment, None),
                    evidence={
                        "period_rows": float(period),
                        "dominance": float(dominance),
                        **_segment_evidence(segment),
                    },
                )
            )

        role = file_spec.columns.get(col, {}).get("role")
        resets = (
            self._sawtooth_resets(arr) if role == "measurement" and not _is_integer_like(arr) else 0
        )
        if resets >= 3:
            self._add(
                Finding(
                    check="time_order",
                    title="Sawtooth or periodic reset pattern",
                    detail=(f"{col} has {resets} sharp negative resets after gradual rises."),
                    loudness="GLANCE" if resets >= 5 else "STANDARD",
                    file=file_spec.display_path,
                    column=col,
                    rows=_with_segment(segment, None),
                    evidence={"reset_count": resets, **_segment_evidence(segment)},
                )
            )

    def _best_changepoint(self, arr: np.ndarray) -> tuple[int, float, float, float, float] | None:
        n = len(arr)
        start = max(5, int(n * 0.15))
        end = min(n - 5, int(n * 0.85))
        best: tuple[int, float, float, float, float] | None = None
        best_score = 0.0
        for idx in range(start, end):
            left = arr[:idx]
            right = arr[idx:]
            left_sd = np.std(left, ddof=1)
            right_sd = np.std(right, ddof=1)
            pooled = math.sqrt(
                ((len(left) - 1) * left_sd**2 + (len(right) - 1) * right_sd**2) / (n - 2)
            )
            if pooled <= 1e-12:
                continue
            effect = (np.mean(right) - np.mean(left)) / pooled
            p_value = _welch_ttest_pvalue(left, right)
            score = abs(effect) * -math.log10(max(float(p_value), 1e-300))
            if score > best_score:
                best_score = score
                best = (
                    idx,
                    float(effect),
                    float(p_value),
                    float(np.mean(left)),
                    float(np.mean(right)),
                )
        return best

    def _periodicity(self, arr: np.ndarray) -> tuple[float, float] | None:
        n = len(arr)
        if n < 30:
            return None
        centered = arr - np.mean(arr)
        if np.std(centered) <= 1e-12:
            return None
        freqs, power = _periodogram(centered)
        if len(power) <= 3:
            return None
        power = power[1:]
        freqs = freqs[1:]
        total = float(np.sum(power))
        if total <= 0:
            return None
        idx = int(np.argmax(power))
        dominance = float(power[idx] / total)
        if dominance < 0.40 or freqs[idx] <= 0:
            return None
        period = float(1 / freqs[idx])
        if period < 3 or period > n / 2:
            return None
        return period, dominance

    def _sawtooth_resets(self, arr: np.ndarray) -> int:
        diffs = np.diff(arr)
        if len(diffs) < 12 or np.std(diffs) <= 1e-12:
            return 0
        big_drop = np.mean(diffs) - 1.75 * np.std(diffs)
        resets = 0
        for idx in range(3, len(diffs)):
            previous = diffs[max(0, idx - 4) : idx]
            if diffs[idx] < big_drop and np.mean(previous) > 0:
                resets += 1
        return resets

    def _variance_structure_checks(
        self, file_spec, df: pd.DataFrame, numeric_cols: list[str]
    ) -> None:
        subgroup_cols = file_spec.role_columns("subgroup_id")
        measurement_cols = [
            col
            for col in numeric_cols
            if file_spec.columns.get(col, {}).get("role") == "measurement"
        ]
        if not subgroup_cols or not measurement_cols:
            return
        subgroup_col = subgroup_cols[0]
        if subgroup_col not in df.columns:
            return
        for col in measurement_cols:
            work = df[[subgroup_col, col]].dropna()
            if len(work) < 20:
                continue
            grouped = work.groupby(subgroup_col)[col]
            sizes = grouped.size()
            if (sizes >= 2).sum() < 5:
                continue
            within_vars = grouped.var(ddof=1).dropna()
            within_sigma = math.sqrt(
                float(np.average(within_vars, weights=sizes.loc[within_vars.index] - 1))
            )
            overall_sigma = float(work[col].std(ddof=1))
            between_sigma = float(grouped.mean().std(ddof=1))
            if overall_sigma <= 1e-12:
                continue
            ratio = within_sigma / overall_sigma
            if ratio < 0.50:
                self._add(
                    Finding(
                        check="variance_structure",
                        title="Within-subgroup sigma is much smaller than overall sigma",
                        detail=(
                            f"{col}: within sigma {within_sigma:.5g}, between subgroup sigma "
                            f"{between_sigma:.5g}, overall sigma {overall_sigma:.5g}."
                        ),
                        loudness="GLANCE" if ratio < 0.25 else "STANDARD",
                        file=file_spec.display_path,
                        column=col,
                        evidence={
                            "within_sigma": within_sigma,
                            "between_sigma": between_sigma,
                            "overall_sigma": overall_sigma,
                            "within_over_overall": ratio,
                        },
                    )
                )

            if self.manifest.spec_limits and within_sigma > 0:
                lsl = self.manifest.spec_limits.get("lsl")
                usl = self.manifest.spec_limits.get("usl")
                if lsl is not None and usl is not None:
                    mean = float(work[col].mean())
                    cpk = min((usl - mean) / (3 * within_sigma), (mean - lsl) / (3 * within_sigma))
                    ppk = min(
                        (usl - mean) / (3 * overall_sigma), (mean - lsl) / (3 * overall_sigma)
                    )
                    gap = cpk - ppk
                    if gap >= 0.35:
                        self._add(
                            Finding(
                                check="variance_structure",
                                title="Cpk/Ppk gap",
                                detail=(
                                    f"{col}: Cpk={cpk:.2f} using within sigma, "
                                    f"Ppk={ppk:.2f} using overall sigma."
                                ),
                                loudness="GLANCE" if gap >= 0.75 else "STANDARD",
                                file=file_spec.display_path,
                                column=col,
                                evidence={"cpk": cpk, "ppk": ppk, "gap": gap},
                            )
                        )

            subgroup_means = grouped.mean().dropna()
            mean_resets = self._sawtooth_resets(subgroup_means.to_numpy())
            if mean_resets >= 3:
                self._add(
                    Finding(
                        check="variance_structure",
                        title="Sawtooth or periodic reset pattern",
                        detail=(
                            f"{col} subgroup means show {mean_resets} sharp "
                            "negative resets after gradual rises."
                        ),
                        loudness="GLANCE" if mean_resets >= 5 else "STANDARD",
                        file=file_spec.display_path,
                        column=col,
                        evidence={"reset_count": mean_resets, "series": "subgroup_means"},
                    )
                )

            self._control_chart_checks(file_spec, subgroup_col, col, grouped)

    def _control_chart_checks(self, file_spec, subgroup_col: str, col: str, grouped) -> None:
        means = grouped.mean().dropna()
        if len(means) < 10:
            return
        center = float(means.mean())
        sigma = float(means.std(ddof=1))
        if sigma <= 1e-12:
            return
        z = (means - center) / sigma
        beyond_3 = z[abs(z) > 3]
        if len(beyond_3):
            self._add(
                Finding(
                    check="variance_structure",
                    title="Subgroup mean control-chart violation",
                    detail=f"{len(beyond_3)} subgroup means exceed 3 sigma.",
                    loudness="GLANCE",
                    file=file_spec.display_path,
                    column=col,
                    rows=f"{subgroup_col}: {', '.join(map(str, beyond_3.index[:6]))}",
                    evidence={"subgroups": [str(x) for x in beyond_3.index[:20]]},
                )
            )
        signs = np.sign(z.to_numpy())
        run = 1
        max_run = 1
        for idx in range(1, len(signs)):
            if signs[idx] != 0 and signs[idx] == signs[idx - 1]:
                run += 1
                max_run = max(max_run, run)
            else:
                run = 1
        if max_run >= 8:
            self._add(
                Finding(
                    check="variance_structure",
                    title="Long run of subgroup means on one side of centerline",
                    detail=(
                        f"At least {max_run} consecutive subgroup means sit on "
                        "the same side of centerline."
                    ),
                    loudness="STANDARD",
                    file=file_spec.display_path,
                    column=col,
                    evidence={"max_run": max_run},
                )
            )

    def _distribution_checks(
        self, file_spec, col: str, values: pd.Series, role: str | None
    ) -> None:
        clean = values.dropna().astype(float)
        n = len(clean)
        if n < 20:
            return
        arr = clean.to_numpy()
        sd = float(np.std(arr, ddof=1))
        if sd <= 1e-12:
            return
        z = np.abs((arr - np.mean(arr)) / sd)
        outliers = int(np.sum(z > 4))
        if outliers and outliers / n >= 0.02:
            self._add(
                Finding(
                    check="distribution",
                    title="Outlier cluster",
                    detail=f"{outliers} values are more than 4 standard deviations from the mean.",
                    loudness="STANDARD" if outliers / n < 0.08 else "GLANCE",
                    file=file_spec.display_path,
                    column=col,
                    evidence={"outliers_gt_4sd": outliers, "row_fraction": outliers / n},
                )
            )

        if role == "measurement" and n >= 30:
            skew = float(_skew(arr))
            kurt = float(_excess_kurtosis(arr))
            if abs(skew) > 0.75 or abs(kurt) > 2.5:
                self._add(
                    Finding(
                        check="distribution",
                        title="Non-normal measurement distribution",
                        detail=f"{col}: skew={skew:.2f}, excess kurtosis={kurt:.2f}.",
                        loudness="STANDARD",
                        file=file_spec.display_path,
                        column=col,
                        evidence={"skew": skew, "excess_kurtosis": kurt},
                    )
                )

            modes = self._histogram_modes(arr)
            if modes >= 2:
                self._add(
                    Finding(
                        check="distribution",
                        title="Possible multimodality",
                        detail=f"{col} has {modes} separated histogram peaks.",
                        loudness="STANDARD",
                        file=file_spec.display_path,
                        column=col,
                        evidence={"peak_count": modes},
                    )
                )

    def _histogram_modes(self, arr: np.ndarray) -> int:
        if len(arr) < 40:
            return 0
        counts, _ = np.histogram(arr, bins="fd")
        if len(counts) < 5:
            return 0
        smoothed = np.convolve(counts, np.ones(3) / 3, mode="same")
        threshold = max(2, smoothed.max() * 0.15)
        peaks = 0
        for idx in range(1, len(smoothed) - 1):
            if smoothed[idx] <= smoothed[idx - 1] or smoothed[idx] <= smoothed[idx + 1]:
                continue
            left_valley = min(smoothed[max(0, idx - 3) : idx + 1])
            right_valley = min(smoothed[idx : min(len(smoothed), idx + 4)])
            if smoothed[idx] - max(left_valley, right_valley) >= threshold:
                peaks += 1
        return peaks

    def _digit_checks(self, file_spec, col: str, values: pd.Series) -> None:
        clean = values.dropna().astype(float)
        if len(clean) < 20:
            return
        arr = clean.to_numpy()
        resolution = file_spec.declared_resolution(col)
        if resolution is not None and resolution > 0:
            granularity = self._observed_granularity(arr)
            if granularity is not None:
                ratio = granularity / resolution
                if ratio >= 2.5:
                    self._add(
                        Finding(
                            check="digit_quantization",
                            title="Observed granularity is coarser than declared resolution",
                            detail=(
                                f"{col} declares resolution {resolution:g}, but "
                                f"observed steps are about {granularity:g}."
                            ),
                            loudness="GLANCE" if ratio >= 5 else "STANDARD",
                            file=file_spec.display_path,
                            column=col,
                            evidence={
                                "declared_resolution": resolution,
                                "observed_granularity": granularity,
                            },
                        )
                    )
                elif ratio <= 0.45:
                    self._add(
                        Finding(
                            check="digit_quantization",
                            title="Observed granularity is finer than declared resolution",
                            detail=(
                                f"{col} declares resolution {resolution:g}, but "
                                f"observed steps are about {granularity:g}."
                            ),
                            loudness="STANDARD",
                            file=file_spec.display_path,
                            column=col,
                            evidence={
                                "declared_resolution": resolution,
                                "observed_granularity": granularity,
                            },
                        )
                    )
            self._last_digit_uniformity(file_spec, col, arr, resolution)

        unique_fraction = len(np.unique(arr)) / len(arr)
        if unique_fraction < 0.08 and len(arr) >= 50 and resolution is not None:
            self._add(
                Finding(
                    check="digit_quantization",
                    title="Heavy value reuse",
                    detail=f"Only {unique_fraction:.1%} of {col} values are unique.",
                    loudness="DEEP",
                    file=file_spec.display_path,
                    column=col,
                    evidence={"unique_fraction": unique_fraction},
                )
            )

    def _observed_granularity(self, arr: np.ndarray) -> float | None:
        unique = np.unique(np.round(arr.astype(float), 10))
        if len(unique) < 3:
            return None
        diffs = np.diff(np.sort(unique))
        diffs = diffs[diffs > 1e-10]
        if len(diffs) == 0:
            return None
        return float(np.percentile(diffs, 10))

    def _last_digit_uniformity(
        self, file_spec, col: str, arr: np.ndarray, resolution: float
    ) -> None:
        if len(arr) < 50:
            return
        scaled = np.rint(arr / resolution).astype(int)
        digits = np.abs(scaled) % 10
        counts = np.bincount(digits, minlength=10)
        expected = np.ones(10) * len(digits) / 10
        chi2 = float(np.sum((counts - expected) ** 2 / expected))
        if chi2 >= 35 and counts.max() >= expected[0] * 2.5:
            self._add(
                Finding(
                    check="digit_quantization",
                    title="Last digits are non-uniform",
                    detail=(
                        f"{col} last digits at declared resolution are uneven "
                        f"(chi-square={chi2:.1f})."
                    ),
                    loudness="DEEP",
                    file=file_spec.display_path,
                    column=col,
                    evidence={"counts": counts.tolist(), "chi_square": chi2},
                )
            )

    def _synthetic_rng_checks(self, file_spec, df: pd.DataFrame, numeric_cols: list[str]) -> None:
        if len(df) < 40:
            return
        non_constant_cols: list[str] = []
        for col in numeric_cols:
            values = pd.to_numeric(df[col], errors="coerce").dropna().astype(float)
            if len(values) < 40:
                continue
            arr = values.to_numpy()
            if np.nanstd(arr) <= 1e-12:
                role = str(file_spec.columns.get(col, {}).get("role", "")).lower()
                if role not in {"ignore", "sample_id", "subgroup_id", "sequence", "id"}:
                    self._add(
                        Finding(
                            check="synthetic_rng",
                            title="Constant numeric column",
                            detail=f"{col} is constant across all non-null rows.",
                            loudness="GLANCE",
                            file=file_spec.display_path,
                            column=col,
                            evidence={"constant_value": float(arr[0]) if len(arr) else None},
                        )
                    )
                continue
            non_constant_cols.append(col)
            window = min(12, max(6, len(arr) // 8))
            variances = pd.Series(arr).rolling(window).var().dropna()
            if len(variances) >= 20 and variances.mean() > 0:
                cv = float(variances.std(ddof=1) / variances.mean())
                if cv < 0.02:
                    self._add(
                        Finding(
                            check="synthetic_rng",
                            title="Rolling variance is implausibly stable",
                            detail=f"{col} rolling variance coefficient of variation is {cv:.3f}.",
                            loudness="STANDARD",
                            file=file_spec.display_path,
                            column=col,
                            evidence={"rolling_variance_cv": cv, "window": window},
                        )
                    )

        usable = [
            col
            for col in non_constant_cols
            if pd.to_numeric(df[col], errors="coerce").notna().sum() >= 20
        ]
        for i, left in enumerate(usable):
            for right in usable[i + 1 :]:
                a = pd.to_numeric(df[left], errors="coerce")
                b = pd.to_numeric(df[right], errors="coerce")
                both = pd.concat([a, b], axis=1).dropna()
                if len(both) < 20:
                    continue
                if both.iloc[:, 0].std(ddof=1) <= 1e-12 or both.iloc[:, 1].std(ddof=1) <= 1e-12:
                    continue
                corr = float(both.iloc[:, 0].corr(both.iloc[:, 1]))
                if np.isfinite(corr) and abs(corr) > 0.999:
                    self._add(
                        Finding(
                            check="synthetic_rng",
                            title="Nearly identical numeric columns",
                            detail=f"{left} and {right} correlate at r={corr:.5f}.",
                            loudness="STANDARD",
                            file=file_spec.display_path,
                            column=f"{left}, {right}",
                            evidence={"correlation": corr},
                        )
                    )

    def _audit_cross_checks(self) -> None:
        for check in self.manifest.cross_checks:
            kind = check.get("kind", "exact")
            left_ref = check.get("left")
            right_ref = check.get("right")
            key = check.get("key")
            if not left_ref or not right_ref or not key:
                continue
            try:
                left_file, left_col = str(left_ref).split(":", 1)
                right_file, right_col = str(right_ref).split(":", 1)
                left = self.tables[left_file][[key, left_col]].rename(columns={left_col: "left"})
                right = self.tables[right_file][[key, right_col]].rename(
                    columns={right_col: "right"}
                )
            except Exception as exc:
                self._add(
                    Finding(
                        check="cross_file",
                        title="Cross-check could not be evaluated",
                        detail=f"{left_ref} vs {right_ref}: {exc}",
                        loudness="STANDARD",
                    )
                )
                continue
            joined = left.merge(right, on=key, how="inner")
            if joined.empty:
                self._add(
                    Finding(
                        check="cross_file",
                        title="Cross-check has no matched keys",
                        detail=f"{left_ref} and {right_ref} have no shared {key} values.",
                        loudness="STANDARD",
                    )
                )
                continue
            equal = joined["left"].eq(joined["right"])
            if kind == "exact" and not bool(equal.all()):
                mismatches = int((~equal).sum())
                self._add(
                    Finding(
                        check="cross_file",
                        title="Declared exact cross-file identity does not tie",
                        detail=(
                            f"{left_ref} and {right_ref} mismatch on "
                            f"{mismatches} of {len(joined)} matched rows."
                        ),
                        loudness="GLANCE" if mismatches / len(joined) > 0.05 else "STANDARD",
                        evidence={"mismatches": mismatches, "matched_rows": len(joined)},
                    )
                )
            if kind == "noisy" and bool(equal.all()) and len(joined) >= 10:
                self._add(
                    Finding(
                        check="cross_file",
                        title="Declared noisy cross-file relation ties exactly",
                        detail=(
                            f"{left_ref} and {right_ref} agree exactly across "
                            f"{len(joined)} matched rows."
                        ),
                        loudness="GLANCE",
                        evidence={"matched_rows": len(joined)},
                    )
                )

    def _classify_findings(self) -> None:
        if not self.manifest.intended_seams:
            return
        seam_profiles = [
            (text, _tokens(text), _semantic_buckets(_tokens(text), text))
            for text in self.manifest.intended_seams
        ]
        for finding in self.findings:
            role = self._finding_column_role(finding)
            for raw_seam, seam_tokens, seam_buckets in seam_profiles:
                confidence = self._intended_match_confidence(
                    finding, role, raw_seam, seam_tokens, seam_buckets
                )
                if confidence >= 0.75:
                    finding.classification = "INTENDED"
                    finding.evidence["matched_intended_seam"] = raw_seam
                    finding.evidence["match_confidence"] = round(confidence, 2)
                    break

    def _finding_column_role(self, finding: Finding) -> str | None:
        if not finding.file or not finding.column:
            return None
        column = finding.column.split(",", 1)[0].strip()
        for file_spec in self.manifest.files:
            if file_spec.display_path == finding.file:
                role = file_spec.columns.get(column, {}).get("role")
                return str(role).lower() if role is not None else None
        return None

    def _intended_match_confidence(
        self,
        finding: Finding,
        role: str | None,
        raw_seam: str,
        seam_tokens: set[str],
        seam_buckets: set[str],
    ) -> float:
        text = _finding_text(finding)
        if raw_seam.lower() in text:
            return 1.0

        tokens = _tokens(text)
        finding_buckets = _semantic_buckets(tokens, text)
        bucket_overlap = finding_buckets & seam_buckets
        token_overlap = tokens & seam_tokens
        column_tokens = _tokens(finding.column or "")
        named_in_seam = bool(column_tokens & seam_tokens)
        role_is_measurement = role in {"measurement", "measure", "count", "rate"}

        if finding.check in {
            "time_order",
            "variance_structure",
            "distribution",
            "digit_quantization",
        }:
            if not role_is_measurement and not named_in_seam:
                return 0.0

        if bucket_overlap == {"periodic"}:
            return 0.0

        if finding.check == "variance_structure" and bucket_overlap & {
            "variance",
            "capability",
            "control",
            "reset",
        }:
            return 0.9

        if finding.check == "time_order":
            if bucket_overlap & {"step", "drift", "reset", "autocorrelation"}:
                return 0.85
            if "periodic" in finding_buckets and seam_buckets & {"reset", "drift"}:
                return 0.78

        if finding.check == "digit_quantization" and bucket_overlap & {"digit"}:
            return 0.85

        if bucket_overlap and len(token_overlap) >= 3:
            return 0.8

        if named_in_seam and bucket_overlap:
            return 0.78

        return 0.0

    def _add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def _markdown_report(self, payload: dict[str, Any]) -> str:
        lines = [
            "# Data Forensics Audit Report",
            "",
            f"Manifest: `{payload['manifest']}`",
            "",
            "## Summary",
            "",
            f"- Findings: {payload['summary']['findings']}",
            f"- Failure threshold: {payload['summary']['fail_on']}",
            f"- Hard input failures: {payload['summary']['hard_failures']}",
            "- Unintended findings at or above threshold: "
            f"{payload['summary']['unintended_at_or_above_threshold']}",
            "",
        ]
        for bucket in ["UNINTENDED", "INTENDED"]:
            items = [finding for finding in self.findings if finding.classification == bucket]
            lines.extend([f"## {bucket.title()} Findings", ""])
            if not items:
                lines.extend(["None.", ""])
                continue
            for idx, finding in enumerate(items, 1):
                location = ", ".join(
                    part for part in [finding.file, finding.column, finding.rows] if part
                )
                lines.append(f"### {idx}. [{finding.loudness}] {finding.title}")
                lines.append("")
                if location:
                    lines.append(f"Location: `{location}`")
                    lines.append("")
                lines.append(finding.detail)
                lines.append("")
                lines.append(f"Check: `{finding.check}`")
                if finding.evidence:
                    lines.append("")
                    lines.append("Evidence:")
                    for key, value in finding.evidence.items():
                        lines.append(f"- `{key}`: {value}")
                if finding.hard_fail:
                    lines.append("")
                    lines.append("Hard failure: yes")
                lines.append("")
        return "\n".join(lines)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_%-]+", text.lower())
        if (len(token) >= 4 or token in {"cpk", "ppk"}) and token not in STOP_WORDS
    }


def _finding_text(finding: Finding) -> str:
    return " ".join(
        str(part)
        for part in [
            finding.title,
            finding.detail,
            finding.check,
            finding.column,
            json.dumps(finding.evidence, sort_keys=True),
        ]
        if part is not None
    ).lower()


def _semantic_buckets(tokens: set[str], text: str) -> set[str]:
    buckets: set[str] = set()
    if tokens & {"step", "changepoint"}:
        buckets.add("step")
    if tokens & {"trend", "drift", "wear"}:
        buckets.add("drift")
    if tokens & {"reset", "resets", "sawtooth"}:
        buckets.add("reset")
    if tokens & {"periodic", "period", "dominant"}:
        buckets.add("periodic")
    if tokens & {"autocorrelation", "lag-1", "lag-2", "lag-3", "lag-4", "lag-5"}:
        buckets.add("autocorrelation")
    if tokens & {"cpk", "ppk", "capability", "capable"}:
        buckets.add("capability")
    if tokens & {"sigma", "within-sigma", "within-subgroup", "overall", "between"}:
        buckets.add("variance")
    if tokens & {"control", "x-bar", "centerline", "limits"}:
        buckets.add("control")
    if tokens & {"composition", "scuff", "clip", "scrap", "counts", "rate", "volume"}:
        buckets.add("composition")
    if tokens & {"resolution", "quantization", "granularity", "digits", "digit"}:
        buckets.add("digit")
    if "cpk/ppk" in text:
        buckets.add("capability")
    return buckets


def _segment_evidence(segment: str) -> dict[str, str]:
    return {} if segment == "all" else {"segment": segment}


def _with_segment(segment: str, rows: str | None) -> str | None:
    if segment == "all":
        return rows
    return f"{segment}; {rows}" if rows else segment


def _is_integer_like(arr: np.ndarray) -> bool:
    return bool(np.all(np.isfinite(arr)) and np.all(np.abs(arr - np.rint(arr)) < 1e-9))


def _is_small_repeating_cycle(arr: np.ndarray) -> bool:
    n = len(arr)
    if n < 12:
        return False
    unique_count = len(np.unique(arr))
    if unique_count < 2 or unique_count > min(12, n // 2):
        return False
    for cycle_len in range(2, min(12, n // 2) + 1):
        pattern = arr[:cycle_len]
        if len(np.unique(pattern)) < 2:
            continue
        expected = np.resize(pattern, n)
        if np.all(np.abs(arr - expected) < 1e-9):
            return True
    return False


def _changepoint_loudness(effect: float) -> str:
    magnitude = abs(effect)
    if magnitude >= 2.0:
        return "GLANCE"
    if magnitude >= 1.5:
        return "STANDARD"
    return "DEEP"


def _fails_threshold(loudness: str, fail_on: str) -> bool:
    try:
        return LOUDNESS_ORDER[loudness] >= LOUDNESS_ORDER[fail_on]
    except KeyError:
        # CLI choices are validated; internal callers fail closed on unknown values.
        return True


def _kendall_tau(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    n = len(x)
    concordant = 0
    discordant = 0
    for i in range(n - 1):
        dx = x[i + 1 :] - x[i]
        dy = y[i + 1 :] - y[i]
        signs = np.sign(dx * dy)
        concordant += int(np.sum(signs > 0))
        discordant += int(np.sum(signs < 0))
    denom = n * (n - 1) / 2
    tau = (concordant - discordant) / denom if denom else 0.0
    variance = 2 * (2 * n + 5) / (9 * n * (n - 1)) if n > 1 else 1.0
    z = tau / math.sqrt(variance) if variance > 0 else 0.0
    p_value = math.erfc(abs(z) / math.sqrt(2))
    return float(tau), float(p_value)


def _welch_ttest_pvalue(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, dtype=float)
    right = np.asarray(right, dtype=float)
    if len(left) < 2 or len(right) < 2:
        return 1.0
    var_left = float(np.var(left, ddof=1))
    var_right = float(np.var(right, ddof=1))
    denom = math.sqrt(var_left / len(left) + var_right / len(right))
    if denom <= 1e-12:
        return 1.0
    t_stat = abs((float(np.mean(left)) - float(np.mean(right))) / denom)
    return float(math.erfc(t_stat / math.sqrt(2)))


def _periodogram(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = len(arr)
    fft = np.fft.rfft(arr)
    power = (np.abs(fft) ** 2) / max(n, 1)
    freqs = np.fft.rfftfreq(n)
    return freqs, power


def _skew(arr: np.ndarray) -> float:
    centered = arr - np.mean(arr)
    sd = np.std(centered)
    if sd <= 1e-12:
        return 0.0
    return float(np.mean((centered / sd) ** 3))


def _excess_kurtosis(arr: np.ndarray) -> float:
    centered = arr - np.mean(arr)
    sd = np.std(centered)
    if sd <= 1e-12:
        return 0.0
    return float(np.mean((centered / sd) ** 4) - 3)
