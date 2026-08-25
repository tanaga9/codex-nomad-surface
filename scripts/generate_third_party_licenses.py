#!/usr/bin/env python3
"""Generate bundled frontend dependency notices from npm lockfiles."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "components.toml"
LICENSES_DIR = ROOT / "components" / "licenses"
OUTPUT_PATH = LICENSES_DIR / "THIRD_PARTY_LICENSES.md"
TLDRAW_LICENSE_PATH = LICENSES_DIR / "TLDRAW_LICENSE.md"
LICENSE_NAMES = ("license", "licence", "copying", "copyright")
TLDRAW_LICENSE_POINTER = "This code is licensed under the [tldraw license]"
LICENSE_FILE_FALLBACKS = {
    # Published without its repository license file; the sibling package is
    # from the same repository and carries the shared upstream MIT notice.
    "react-remove-scroll-bar": "react-remove-scroll",
}


class LicenseGenerationError(RuntimeError):
    """Raised when a bundled dependency cannot be attributed safely."""


@dataclass(frozen=True, order=True)
class PackageNotice:
    name: str
    version: str
    declared_license: str
    license_text: str


def _component_frontends() -> tuple[Path, ...]:
    try:
        registry = tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LicenseGenerationError(
            f"Unable to read {REGISTRY_PATH.name}: {exc}"
        ) from exc

    entries = registry.get("components")
    if registry.get("version") != 1 or not isinstance(entries, list):
        raise LicenseGenerationError("components.toml must contain a version 1 registry.")

    frontends: list[Path] = []
    for entry in entries:
        frontend_value = entry.get("frontend") if isinstance(entry, dict) else None
        if not isinstance(frontend_value, str) or not frontend_value:
            raise LicenseGenerationError("Each component must define its frontend path.")
        frontend = (ROOT / frontend_value).resolve()
        try:
            frontend.relative_to(ROOT)
        except ValueError as exc:
            raise LicenseGenerationError(
                f"Component frontend leaves the repository: {frontend_value}"
            ) from exc
        frontends.append(frontend)
    return tuple(frontends)


def _license_text(
    package_dir: Path, *, frontend: Path, package_name: str, package: str
) -> str:
    candidates = sorted(
        path
        for path in package_dir.iterdir()
        if path.is_file()
        and any(path.name.casefold().startswith(name) for name in LICENSE_NAMES)
    )
    fallback_name = LICENSE_FILE_FALLBACKS.get(package_name)
    if not candidates and fallback_name:
        fallback_dir = frontend / "node_modules" / fallback_name
        candidates = sorted(
            path
            for path in fallback_dir.iterdir()
            if path.is_file()
            and any(path.name.casefold().startswith(name) for name in LICENSE_NAMES)
        )
    if not candidates:
        raise LicenseGenerationError(f"No license file found for {package}.")
    try:
        text = candidates[0].read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise LicenseGenerationError(
            f"Unable to read the license for {package}: {exc}"
        ) from exc
    if TLDRAW_LICENSE_POINTER in text:
        try:
            return TLDRAW_LICENSE_PATH.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise LicenseGenerationError(
                f"Unable to read {TLDRAW_LICENSE_PATH.name}: {exc}"
            ) from exc
    return text


def collect_notices() -> tuple[PackageNotice, ...]:
    notices: set[PackageNotice] = set()
    for frontend in _component_frontends():
        lock_path = frontend / "package-lock.json"
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LicenseGenerationError(f"Unable to read {lock_path}: {exc}") from exc
        packages = lock.get("packages")
        if not isinstance(packages, dict):
            raise LicenseGenerationError(f"{lock_path} does not contain package metadata.")

        for relative, locked in sorted(packages.items()):
            if (
                not relative.startswith("node_modules/")
                or not isinstance(locked, dict)
                or locked.get("dev") is True
            ):
                continue
            package_dir = frontend / relative
            package_json_path = package_dir / "package.json"
            try:
                package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LicenseGenerationError(
                    f"Unable to read installed metadata for {relative}: {exc}"
                ) from exc
            name = package_json.get("name")
            version = package_json.get("version")
            declared_license = package_json.get("license", locked.get("license", "UNKNOWN"))
            if not isinstance(name, str) or not isinstance(version, str):
                raise LicenseGenerationError(f"Invalid package metadata for {relative}.")
            if not isinstance(declared_license, str):
                declared_license = json.dumps(declared_license, sort_keys=True)
            notices.add(
                PackageNotice(
                    name=name,
                    version=version,
                    declared_license=declared_license,
                    license_text=_license_text(
                        package_dir,
                        frontend=frontend,
                        package_name=name,
                        package=f"{name}@{version}",
                    ),
                )
            )
    return tuple(sorted(notices))


def render_notices(notices: tuple[PackageNotice, ...]) -> str:
    groups: dict[str, list[PackageNotice]] = defaultdict(list)
    for notice in notices:
        groups[notice.license_text].append(notice)

    lines = [
        "# Third-Party Software Notices",
        "",
        "Codex Nomad Surface bundles the production frontend dependencies listed",
        "below. The project itself remains licensed under the root `LICENSE` file.",
        "The tldraw license is also included verbatim as `TLDRAW_LICENSE.md`.",
        "",
        "This file is generated by `scripts/generate_third_party_licenses.py`.",
        "",
        "## Bundled packages",
        "",
    ]
    for notice in notices:
        lines.append(
            f"- `{notice.name}@{notice.version}` — {notice.declared_license}"
        )

    lines.extend(("", "## License texts", ""))
    ordered_groups = sorted(
        groups.items(), key=lambda item: tuple(notice.name for notice in item[1])
    )
    for index, (license_text, packages) in enumerate(ordered_groups, start=1):
        package_labels = ", ".join(
            f"`{notice.name}@{notice.version}`" for notice in packages
        )
        lines.extend(
            (
                f"### License group {index}",
                "",
                f"Applies to: {package_labels}",
                "",
                license_text,
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the checked-in notice file is not current.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        rendered = render_notices(collect_notices())
        args = parse_args()
        if args.check:
            try:
                current = OUTPUT_PATH.read_text(encoding="utf-8")
            except OSError as exc:
                raise LicenseGenerationError(
                    f"Unable to read {OUTPUT_PATH.name}: {exc}"
                ) from exc
            if current != rendered:
                raise LicenseGenerationError(
                    f"{OUTPUT_PATH.name} is stale; run {Path(__file__).name}."
                )
            print(f"Verified {OUTPUT_PATH.relative_to(ROOT)}")
        else:
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT_PATH.write_text(rendered, encoding="utf-8")
            print(f"Generated {OUTPUT_PATH.relative_to(ROOT)}")
        return 0
    except LicenseGenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
