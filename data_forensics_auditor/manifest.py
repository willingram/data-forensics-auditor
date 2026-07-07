from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import FileSpec, Manifest

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - exercised only in lean runtimes
    yaml = None


def load_manifest(path: str | Path) -> Manifest:
    manifest_path = Path(path).resolve()
    with manifest_path.open("r", encoding="utf-8") as handle:
        text = handle.read()
    raw = yaml.safe_load(text) if yaml is not None else _load_yaml_subset(text)
    raw = raw or {}

    base_dir = manifest_path.parent
    files: list[FileSpec] = []
    for entry in raw.get("files", []):
        raw_path = Path(str(entry["path"]))
        resolved = raw_path if raw_path.is_absolute() else (base_dir / raw_path)
        files.append(
            FileSpec(
                path=resolved.resolve(),
                display_path=str(entry["path"]),
                date_col=entry.get("date_col"),
                columns=entry.get("columns", {}) or {},
            )
        )

    spec_limits = raw.get("spec_limits")
    if spec_limits is not None:
        spec_limits = {k: float(v) for k, v in spec_limits.items()}

    return Manifest(
        path=manifest_path,
        files=files,
        spec_limits=spec_limits,
        calendar=raw.get("calendar", {}) or {},
        regimes=_normalise_regimes(raw.get("regimes", []) or []),
        intended_seams=[str(value) for value in raw.get("intended_seams", []) or []],
        cross_checks=raw.get("cross_checks", []) or [],
    )


def _normalise_regimes(regimes: list[Any]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for item in regimes:
        if isinstance(item, str):
            normalised.append({"date": item})
        elif isinstance(item, dict):
            normalised.append(dict(item))
    return normalised


def _load_yaml_subset(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        lines.append((len(line) - len(line.lstrip(" ")), line.strip()))
    index = 0

    def parse_block(indent: int) -> Any:
        nonlocal index
        if index >= len(lines):
            return {}
        is_list = lines[index][1].startswith("- ")
        if is_list:
            result: list[Any] = []
            while (
                index < len(lines)
                and lines[index][0] == indent
                and lines[index][1].startswith("- ")
            ):
                item_text = lines[index][1][2:].strip()
                index += 1
                if not item_text:
                    result.append(parse_block(indent + 2))
                elif _looks_like_key_value(item_text):
                    key, value = _split_key_value(item_text)
                    item: dict[str, Any] = {key: _parse_value(value)}
                    if index < len(lines) and lines[index][0] > indent:
                        nested = parse_block(indent + 2)
                        if isinstance(nested, dict):
                            item.update(nested)
                    result.append(item)
                else:
                    result.append(_parse_value(item_text))
            return result

        result: dict[str, Any] = {}
        while (
            index < len(lines)
            and lines[index][0] == indent
            and not lines[index][1].startswith("- ")
        ):
            key, value = _split_key_value(lines[index][1])
            index += 1
            if value == "":
                result[key] = parse_block(indent + 2)
            else:
                result[key] = _parse_value(value)
        return result

    return parse_block(0)


def _looks_like_key_value(text: str) -> bool:
    in_quote: str | None = None
    for char in text:
        if char in {'"', "'"}:
            in_quote = None if in_quote == char else char
        elif char == ":" and in_quote is None:
            return True
    return False


def _split_key_value(text: str) -> tuple[str, str]:
    in_quote: str | None = None
    for idx, char in enumerate(text):
        if char in {'"', "'"}:
            in_quote = None if in_quote == char else char
        elif char == ":" and in_quote is None:
            return _parse_key(text[:idx].strip()), text[idx + 1 :].strip()
    raise ValueError(f"Expected key/value line: {text}")


def _parse_key(text: str) -> str:
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    return text


def _parse_value(text: str) -> Any:
    text = text.strip()
    if text == "":
        return ""
    if text in {"[]", "{}"}:
        return [] if text == "[]" else {}
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        if not inner:
            return {}
        result: dict[str, Any] = {}
        for part in _split_inline(inner):
            key, value = _split_key_value(part)
            result[key] = _parse_value(value)
        return result
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_value(part) for part in _split_inline(inner)]
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        if any(char in text for char in [".", "e", "E"]):
            return float(text)
        return int(text)
    except ValueError:
        return text


def _split_inline(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_quote: str | None = None
    for char in text:
        if char in {'"', "'"}:
            in_quote = None if in_quote == char else char
        elif in_quote is None and char in "[{":
            depth += 1
        elif in_quote is None and char in "]}":
            depth -= 1
        if char == "," and depth == 0 and in_quote is None:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts
