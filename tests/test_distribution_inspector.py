from pathlib import Path

from scripts.inspect_distribution import (
    EXPECTED_PROJECT_URLS,
    SDIST_REQUIRED_ROOT_FILES,
    Archive,
    path_errors,
    project_url_errors,
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


def test_project_url_contract_accepts_only_the_exact_four_urls() -> None:
    payload = _metadata_with_urls(EXPECTED_PROJECT_URLS)

    assert project_url_errors(payload, "wheel METADATA") == []
    assert project_url_errors(payload, "sdist PKG-INFO") == []


def test_project_url_contract_rejects_missing_wrong_and_extra_urls() -> None:
    missing = dict(EXPECTED_PROJECT_URLS)
    missing.pop("Issues")
    wrong = dict(EXPECTED_PROJECT_URLS)
    wrong["Repository"] = "https://example.invalid/wrong"
    extra = {**EXPECTED_PROJECT_URLS, "Documentation": "https://example.invalid/docs"}

    for urls in (missing, wrong, extra):
        errors = project_url_errors(_metadata_with_urls(urls), "METADATA")
        assert any("Project-URLs are" in error for error in errors)


def test_project_url_contract_rejects_malformed_and_duplicate_labels() -> None:
    exact_lines = [f"Project-URL: {label}, {url}" for label, url in EXPECTED_PROJECT_URLS.items()]
    payload = (
        "\n".join(
            [
                "Metadata-Version: 2.4",
                *exact_lines,
                exact_lines[0],
                "Project-URL: malformed",
                "",
                "",
            ]
        )
    ).encode()

    errors = project_url_errors(payload, "METADATA")

    assert any("duplicate Project-URL label" in error for error in errors)
    assert any("malformed Project-URL" in error for error in errors)


def _metadata_with_urls(urls: dict[str, str]) -> bytes:
    lines = [
        "Metadata-Version: 2.4",
        *(f"Project-URL: {label}, {url}" for label, url in urls.items()),
        "",
        "",
    ]
    return "\n".join(lines).encode()
