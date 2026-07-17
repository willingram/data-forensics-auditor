"""Validate DFA wheel and source-distribution contents without publishing them."""

from __future__ import annotations

import argparse
import configparser
import csv
import io
import ntpath
import re
import stat
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - DFA requires Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

PACKAGE = "data_forensics_auditor"
EXPECTED_ENTRY_POINTS = {
    "data-forensics-auditor": "data_forensics_auditor.cli:main",
    "dfa": "data_forensics_auditor.cli:main",
}
EXPECTED_PROJECT_URLS = {
    "Homepage": "https://github.com/willingram/data-forensics-auditor",
    "Repository": "https://github.com/willingram/data-forensics-auditor",
    "Issues": "https://github.com/willingram/data-forensics-auditor/issues",
    "Changelog": ("https://github.com/willingram/data-forensics-auditor/blob/main/CHANGELOG.md"),
}
SDIST_REQUIRED_ROOT_FILES = {
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "DESIGN.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "scripts/inspect_distribution.py",
}

FORBIDDEN_COMPONENTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "audit_output",
    "build",
    "dist",
    "report_dir",
    "venv",
}
FORBIDDEN_EXACT_FILENAMES = {
    ".env",
    ".pypirc",
    "audit_report.json",
    "audit_report.md",
    "credentials",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
}
FORBIDDEN_FILENAME_PREFIXES = (".env.", "secrets.")
FORBIDDEN_SUFFIXES = (".key", ".p12", ".pem", ".pfx", ".pyc", ".pyo")
FORBIDDEN_CONTENT = (
    b"C:" + b"\\Users\\",
    b"C:" + b"\\Code\\",
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----",
)


@dataclass(frozen=True)
class Archive:
    path: Path
    kind: str
    names: tuple[str, ...]
    files: dict[str, bytes]
    unsafe_members: tuple[str, ...] = ()


def normalized_name(name: str, *, wheel: bool = False) -> str:
    separator = "_" if wheel else "-"
    return re.sub(r"[-_.]+", separator, name).lower()


def project_metadata(repository: Path) -> tuple[str, str]:
    with (repository / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return str(project["name"]), str(project["version"])


def distribution_paths(directory: Path, name: str, version: str) -> tuple[Path, Path]:
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    errors: list[str] = []
    if len(wheels) != 1:
        errors.append(f"expected exactly one wheel in {directory}, found {len(wheels)}")
    if len(sdists) != 1:
        errors.append(f"expected exactly one sdist in {directory}, found {len(sdists)}")
    if errors:
        raise ValueError("; ".join(errors))

    wheel = wheels[0]
    sdist = sdists[0]
    wheel_prefix = f"{normalized_name(name, wheel=True)}-{version}-"
    wheel_tail = wheel.name.removeprefix(wheel_prefix).removesuffix(".whl")
    if not wheel.name.startswith(wheel_prefix) or not wheel.name.endswith(".whl"):
        errors.append(f"wheel filename does not identify {name} {version}: {wheel.name}")
    elif len(wheel_tail.split("-")) != 3:
        errors.append(
            f"wheel filename does not contain exactly three compatibility tags: {wheel.name}"
        )

    expected_sdist = f"{normalized_name(name, wheel=True)}-{version}.tar.gz"
    if sdist.name != expected_sdist:
        errors.append(f"expected sdist filename {expected_sdist}, found {sdist.name}")
    if errors:
        raise ValueError("; ".join(errors))
    return wheel, sdist


def read_archive(path: Path) -> Archive:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as handle:
            names = tuple(info.filename for info in handle.infolist())
            files = {
                info.filename: handle.read(info) for info in handle.infolist() if not info.is_dir()
            }
            unsafe = tuple(
                info.filename
                for info in handle.infolist()
                if stat.S_ISLNK(info.external_attr >> 16)
            )
        return Archive(path, "wheel", names, files, unsafe)

    if path.name.endswith(".tar.gz"):
        files: dict[str, bytes] = {}
        with tarfile.open(path, "r:gz") as handle:
            members = handle.getmembers()
            names = tuple(member.name for member in members)
            unsafe = tuple(
                member.name for member in members if not (member.isfile() or member.isdir())
            )
            for member in members:
                if member.isfile():
                    extracted = handle.extractfile(member)
                    if extracted is not None:
                        files[member.name] = extracted.read()
        return Archive(path, "sdist", names, files, unsafe)

    raise ValueError(f"unsupported distribution type: {path}")


def path_errors(names: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    seen: dict[str, str] = {}
    for name in names:
        if not name:
            errors.append("archive contains an empty member path")
            continue
        if "\\" in name:
            errors.append(f"member path uses a backslash: {name!r}")
        if name.startswith("/") or PurePosixPath(name).is_absolute():
            errors.append(f"member path is absolute: {name!r}")
        if ntpath.splitdrive(name)[0]:
            errors.append(f"member path is drive-qualified: {name!r}")

        comparable = name.rstrip("/")
        parts = PurePosixPath(comparable).parts
        if "//" in comparable or any(part in {"", ".", ".."} for part in parts):
            errors.append(f"member path contains traversal or non-portable segments: {name!r}")

        folded = comparable.casefold()
        previous = seen.get(folded)
        if previous is not None:
            if previous == comparable:
                errors.append(f"duplicate member path: {comparable!r}")
            else:
                errors.append(f"case-insensitive path collision: {previous!r} and {comparable!r}")
        else:
            seen[folded] = comparable
    return errors


def residue_errors(archive: Archive, sdist_root: str) -> list[str]:
    errors: list[str] = []
    expected_egg_info = f"{PACKAGE}.egg-info"
    for name, payload in archive.files.items():
        parts = PurePosixPath(name).parts
        relative_parts = parts[1:] if archive.kind == "sdist" else parts
        folded_parts = [part.casefold() for part in relative_parts]

        for index, component in enumerate(folded_parts):
            if component.endswith(".egg-info"):
                allowed = (
                    archive.kind == "sdist"
                    and index == 0
                    and component == expected_egg_info.casefold()
                )
                if not allowed:
                    errors.append(f"{archive.path.name} contains unexpected egg-info path: {name}")
            if component in FORBIDDEN_COMPONENTS:
                errors.append(f"{archive.path.name} contains forbidden development path: {name}")

        filename = folded_parts[-1] if folded_parts else ""
        if (
            filename in FORBIDDEN_EXACT_FILENAMES
            or filename.startswith(FORBIDDEN_FILENAME_PREFIXES)
            or filename.endswith(FORBIDDEN_SUFFIXES)
        ):
            errors.append(f"{archive.path.name} contains forbidden file: {name}")

        for marker in FORBIDDEN_CONTENT:
            if marker in payload:
                errors.append(
                    f"{archive.path.name} contains forbidden content marker {marker!r} in {name}"
                )

    if archive.kind == "sdist":
        prefix = f"{sdist_root}/"
        for name in archive.names:
            if name != sdist_root and not name.startswith(prefix):
                errors.append(f"sdist member is outside its single top-level directory: {name!r}")
    return errors


def source_files(repository: Path, directory: str) -> set[str]:
    root = repository / directory
    return {
        path.relative_to(repository).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }


def metadata_values(payload: bytes) -> tuple[str | None, str | None]:
    metadata = BytesParser().parsebytes(payload)
    return metadata.get("Name"), metadata.get("Version")


def project_url_errors(payload: bytes, metadata_name: str) -> list[str]:
    metadata = BytesParser().parsebytes(payload)
    actual: dict[str, str] = {}
    errors: list[str] = []
    for raw_value in metadata.get_all("Project-URL", []):
        label, separator, url = str(raw_value).partition(",")
        label = label.strip()
        url = url.strip()
        if not separator or not label or not url:
            errors.append(f"{metadata_name} contains malformed Project-URL: {raw_value!r}")
            continue
        if label in actual:
            errors.append(f"{metadata_name} contains duplicate Project-URL label: {label!r}")
            continue
        actual[label] = url
    if actual != EXPECTED_PROJECT_URLS:
        errors.append(
            f"{metadata_name} Project-URLs are {actual!r}, expected {EXPECTED_PROJECT_URLS!r}"
        )
    return errors


def wheel_errors(archive: Archive, repository: Path, name: str, version: str) -> list[str]:
    errors: list[str] = []
    dist_info = f"{normalized_name(name, wheel=True)}-{version}.dist-info"
    required = {
        f"{dist_info}/METADATA",
        f"{dist_info}/RECORD",
        f"{dist_info}/WHEEL",
        f"{dist_info}/entry_points.txt",
        f"{dist_info}/licenses/LICENSE",
    }
    missing = sorted(required - archive.files.keys())
    errors.extend(
        f"{archive.path.name} is missing required wheel member: {item}" for item in missing
    )

    for relative in sorted(source_files(repository, PACKAGE)):
        if relative not in archive.files:
            errors.append(f"{archive.path.name} is missing package source: {relative}")

    metadata_path = f"{dist_info}/METADATA"
    if metadata_path in archive.files:
        actual_name, actual_version = metadata_values(archive.files[metadata_path])
        if normalized_name(actual_name or "") != normalized_name(name):
            errors.append(f"wheel METADATA Name is {actual_name!r}, expected {name!r}")
        if actual_version != version:
            errors.append(f"wheel METADATA Version is {actual_version!r}, expected {version!r}")
        errors.extend(project_url_errors(archive.files[metadata_path], "wheel METADATA"))

    entry_points_path = f"{dist_info}/entry_points.txt"
    if entry_points_path in archive.files:
        parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
        parser.optionxform = str
        try:
            parser.read_string(archive.files[entry_points_path].decode("utf-8"))
            sections = set(parser.sections())
            actual = (
                dict(parser.items("console_scripts"))
                if parser.has_section("console_scripts")
                else {}
            )
        except (configparser.Error, UnicodeDecodeError) as exc:
            errors.append(f"wheel entry_points.txt cannot be parsed: {exc}")
        else:
            if sections != {"console_scripts"}:
                errors.append(
                    f"wheel entry-point groups are {sorted(sections)!r}, expected console_scripts"
                )
            if actual != EXPECTED_ENTRY_POINTS:
                errors.append(
                    f"wheel console entry points are {actual!r}, expected {EXPECTED_ENTRY_POINTS!r}"
                )

    record_path = f"{dist_info}/RECORD"
    if record_path in archive.files:
        try:
            rows = list(csv.reader(io.StringIO(archive.files[record_path].decode("utf-8"))))
        except (csv.Error, UnicodeDecodeError) as exc:
            errors.append(f"wheel RECORD cannot be parsed: {exc}")
        else:
            malformed = [row for row in rows if len(row) != 3]
            if malformed:
                errors.append(f"wheel RECORD contains {len(malformed)} malformed row(s)")
            recorded = [row[0] for row in rows if len(row) == 3]
            if len(recorded) != len(set(recorded)):
                errors.append("wheel RECORD contains duplicate paths")
            missing_from_record = sorted(set(archive.files) - set(recorded))
            extra_in_record = sorted(set(recorded) - set(archive.files))
            if missing_from_record or extra_in_record:
                errors.append(
                    "wheel RECORD does not exactly enumerate wheel files "
                    f"(missing={missing_from_record!r}, extra={extra_in_record!r})"
                )
    return errors


def sdist_errors(archive: Archive, repository: Path, name: str, version: str) -> list[str]:
    errors: list[str] = []
    root = f"{normalized_name(name, wheel=True)}-{version}"
    required = set(SDIST_REQUIRED_ROOT_FILES)
    required.update(source_files(repository, PACKAGE))
    required.update(source_files(repository, "tests"))

    for relative in sorted(required):
        member = f"{root}/{relative}"
        if member not in archive.files:
            errors.append(f"{archive.path.name} is missing required sdist member: {relative}")

    pkg_info = f"{root}/PKG-INFO"
    if pkg_info not in archive.files:
        errors.append(f"{archive.path.name} is missing PKG-INFO")
    else:
        actual_name, actual_version = metadata_values(archive.files[pkg_info])
        if normalized_name(actual_name or "") != normalized_name(name):
            errors.append(f"sdist PKG-INFO Name is {actual_name!r}, expected {name!r}")
        if actual_version != version:
            errors.append(f"sdist PKG-INFO Version is {actual_version!r}, expected {version!r}")
        errors.extend(project_url_errors(archive.files[pkg_info], "sdist PKG-INFO"))
    return errors


def inspect(directory: Path, repository: Path) -> list[str]:
    name, version = project_metadata(repository)
    try:
        wheel_path, sdist_path = distribution_paths(directory, name, version)
        archives = (read_archive(wheel_path), read_archive(sdist_path))
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return [str(exc)]

    sdist_root = f"{normalized_name(name, wheel=True)}-{version}"
    errors: list[str] = []
    for archive in archives:
        errors.extend(path_errors(archive.names))
        errors.extend(
            f"{archive.path.name} contains a link or special archive member: {name}"
            for name in archive.unsafe_members
        )
        errors.extend(residue_errors(archive, sdist_root))
        if archive.kind == "wheel":
            errors.extend(wheel_errors(archive, repository, name, version))
        else:
            errors.extend(sdist_errors(archive, repository, name, version))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing one wheel and one sdist")
    args = parser.parse_args(argv)

    repository = Path(__file__).resolve().parents[1]
    errors = inspect(args.directory.resolve(), repository)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Distribution inspection failed with {len(errors)} error(s).", file=sys.stderr)
        return 1

    print("Distribution inspection passed: one wheel and one sdist are complete and clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
