from pathlib import Path

from scripts.inspect_distribution import (
    SDIST_REQUIRED_ROOT_FILES,
    Archive,
    path_errors,
    residue_errors,
)


def archive(kind: str, names: tuple[str, ...]) -> Archive:
    return Archive(Path("artifact"), kind, names, {name: b"" for name in names})


def test_member_paths_reject_traversal_absolute_backslash_drive_and_case_collision() -> None:
    errors = path_errors(
        (
            "../escape",
            "/absolute",
            "folder\\file",
            "C:/drive",
            "folder//file",
            "duplicate",
            "duplicate",
            "Package/module.py",
            "package/module.py",
        )
    )

    assert any("traversal" in error for error in errors)
    assert any("absolute" in error for error in errors)
    assert any("backslash" in error for error in errors)
    assert any("drive-qualified" in error for error in errors)
    assert any("non-portable segments" in error for error in errors)
    assert any("duplicate member path" in error for error in errors)
    assert any("case-insensitive path collision" in error for error in errors)


def test_setuptools_egg_info_is_allowed_only_at_sdist_root() -> None:
    root = "data_forensics_auditor-0.2.1"
    valid = archive("sdist", (f"{root}/data_forensics_auditor.egg-info/PKG-INFO",))
    misplaced = archive(
        "sdist",
        (f"{root}/nested/data_forensics_auditor.egg-info/PKG-INFO",),
    )

    assert residue_errors(valid, root) == []
    assert any("unexpected egg-info" in error for error in residue_errors(misplaced, root))


def test_wheel_rejects_egg_info_and_development_residue() -> None:
    candidate = archive(
        "wheel",
        (
            "data_forensics_auditor.egg-info/PKG-INFO",
            ".pytest_cache/state",
            "audit_output/audit_report.json",
        ),
    )

    errors = residue_errors(candidate, "unused")
    assert any("unexpected egg-info" in error for error in errors)
    assert sum("forbidden development path" in error for error in errors) == 2


def test_sdist_requires_single_top_level_directory() -> None:
    root = "data_forensics_auditor-0.2.1"
    candidate = archive("sdist", (f"{root}/README.md", "outside.txt"))

    assert any(
        "outside its single top-level directory" in error
        for error in residue_errors(candidate, root)
    )


def test_sdist_contract_requires_governance_documents() -> None:
    assert {
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "DESIGN.md",
        "SECURITY.md",
    } <= SDIST_REQUIRED_ROOT_FILES
