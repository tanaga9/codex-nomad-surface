#!/usr/bin/env python3
"""Project-wide development commands for Codex Nomad Surface."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "components.toml"
VENV_DIR = ROOT / ".venv"
RUNTIME_ROOT = ROOT / "codex_nomad_surface" / "ui_components" / "generated"
PYTHON_BUILD_DIR = ROOT / "build"
PACKAGE_OUTPUT_DIR = ROOT / "dist"
PACKAGE_METADATA_DIR = ROOT / "codex_nomad_surface.egg-info"
MINIMUM_PYTHON = (3, 12)
COMPONENT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class DevCommandError(RuntimeError):
    """An actionable development command error."""


@dataclass(frozen=True)
class Component:
    name: str
    frontend: Path
    build_dir: Path
    runtime_dir: Path
    entry_js: str
    entry_css: str


def _resolve_repo_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise DevCommandError(f"Component field {field!r} must be a path string.")
    path = Path(value)
    if path.is_absolute():
        raise DevCommandError(
            f"Component field {field!r} must be relative to the repository."
        )
    resolved = (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise DevCommandError(f"Component field {field!r} leaves the repository.") from exc
    return resolved


def load_components() -> tuple[Component, ...]:
    try:
        registry = tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DevCommandError(f"Unable to read {REGISTRY_PATH.name}: {exc}") from exc

    if registry.get("version") != 1:
        raise DevCommandError("components.toml must declare version = 1.")
    entries = registry.get("components")
    if not isinstance(entries, list) or not entries:
        raise DevCommandError(
            "components.toml must contain at least one [[components]] entry."
        )

    components: list[Component] = []
    names: set[str] = set()
    runtime_dirs: set[Path] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise DevCommandError("Each [[components]] entry must be a table.")
        name = entry.get("name")
        if not isinstance(name, str) or not COMPONENT_NAME_PATTERN.fullmatch(name):
            raise DevCommandError(
                "Each component name must contain lowercase letters, numbers, and hyphens."
            )
        if name in names:
            raise DevCommandError(f"Duplicate component name: {name}")

        component = Component(
            name=name,
            frontend=_resolve_repo_path(entry.get("frontend"), field="frontend"),
            build_dir=_resolve_repo_path(entry.get("build_dir"), field="build_dir"),
            runtime_dir=_resolve_repo_path(entry.get("runtime_dir"), field="runtime_dir"),
            entry_js=str(entry.get("entry_js", "")),
            entry_css=str(entry.get("entry_css", "")),
        )
        if component.runtime_dir in runtime_dirs:
            raise DevCommandError(f"Duplicate runtime directory: {component.runtime_dir}")
        if component.build_dir == component.runtime_dir:
            raise DevCommandError(f"{name}: build_dir and runtime_dir must differ.")
        try:
            build_relative = component.build_dir.relative_to(component.frontend)
        except ValueError as exc:
            raise DevCommandError(f"{name}: build_dir must be inside frontend.") from exc
        if build_relative == Path("."):
            raise DevCommandError(f"{name}: build_dir must not be the frontend directory itself.")
        _assert_safe_runtime_dir(component)
        if not component.entry_js or not component.entry_css:
            raise DevCommandError(f"{name}: entry_js and entry_css are required.")
        entry_patterns = (
            ("entry_js", component.entry_js),
            ("entry_css", component.entry_css),
        )
        for field, pattern in entry_patterns:
            if Path(pattern).name != pattern or ".." in Path(pattern).parts:
                raise DevCommandError(f"{name}: {field} must be a filename glob.")
        if not (component.frontend / "package.json").is_file():
            raise DevCommandError(
                f"{name}: package.json was not found in {component.frontend}"
            )

        names.add(name)
        runtime_dirs.add(component.runtime_dir)
        components.append(component)
    return tuple(components)


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _run(command: list[str], *, cwd: Path = ROOT) -> None:
    print(f"[{cwd.relative_to(ROOT) or Path('.')}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def _npm_command() -> str:
    npm = shutil.which("npm")
    if npm is None:
        raise DevCommandError("npm was not found. Install Node.js and run this command again.")
    return npm


def _matching_entry(directory: Path, pattern: str, *, component: str) -> Path:
    matches = sorted(path for path in directory.glob(pattern) if path.is_file())
    if len(matches) != 1:
        raise DevCommandError(
            f"{component}: expected exactly one {pattern!r} file in {directory}, "
            f"found {len(matches)}."
        )
    return matches[0]


def _assert_safe_runtime_dir(component: Component) -> None:
    try:
        relative = component.runtime_dir.relative_to(RUNTIME_ROOT)
    except ValueError as exc:
        raise DevCommandError(
            f"{component.name}: runtime_dir must be inside {RUNTIME_ROOT.relative_to(ROOT)}."
        ) from exc
    if relative == Path("."):
        raise DevCommandError(f"{component.name}: runtime_dir must name a component directory.")


def verify_component(component: Component, *, runtime: bool = True) -> None:
    directories = [component.build_dir]
    if runtime:
        directories.append(component.runtime_dir)
    for directory in directories:
        if not directory.is_dir():
            raise DevCommandError(
                f"{component.name}: generated directory is missing: {directory}"
            )
        _matching_entry(directory, component.entry_js, component=component.name)
        _matching_entry(directory, component.entry_css, component=component.name)


def sync_component(component: Component) -> None:
    _assert_safe_runtime_dir(component)
    verify_component(component, runtime=False)
    if component.runtime_dir.exists():
        shutil.rmtree(component.runtime_dir)
    component.runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(component.build_dir, component.runtime_dir)
    verify_component(component)


def build_component(component: Component) -> None:
    _run([_npm_command(), "run", "build"], cwd=component.frontend)
    sync_component(component)
    print(f"Built {component.name}", flush=True)


def clean_python_package_outputs() -> None:
    allowed_names = {"build", "dist", "codex_nomad_surface.egg-info"}
    directories = (PYTHON_BUILD_DIR, PACKAGE_OUTPUT_DIR, PACKAGE_METADATA_DIR)
    for directory in directories:
        resolved = directory.resolve()
        if resolved.parent != ROOT.resolve() or resolved.name not in allowed_names:
            raise DevCommandError(f"Refusing to remove unsafe package path: {directory}")
        if directory.exists():
            shutil.rmtree(directory)


def _verify_package_entries(
    names: tuple[str, ...],
    components: tuple[Component, ...],
    *,
    artifact: str,
) -> None:
    runtime_prefixes: list[str] = []
    for component in components:
        runtime_prefix = component.runtime_dir.relative_to(ROOT).as_posix() + "/"
        runtime_prefixes.append(runtime_prefix)
        expected_names = {
            path.relative_to(ROOT).as_posix()
            for path in component.runtime_dir.rglob("*")
            if path.is_file()
        }
        component_names = {name for name in names if name.startswith(runtime_prefix)}
        missing = sorted(expected_names - component_names)
        extra = sorted(component_names - expected_names)
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if extra:
                details.append(f"unexpected: {', '.join(extra)}")
            raise DevCommandError(
                f"{component.name}: {artifact} component assets differ from the "
                f"generated directory ({'; '.join(details)})."
            )
        for pattern in (component.entry_js, component.entry_css):
            matches = [
                name
                for name in component_names
                if fnmatch.fnmatchcase(Path(name).name, pattern)
            ]
            if len(matches) != 1:
                raise DevCommandError(
                    f"{component.name}: {artifact} must contain exactly one {pattern!r} "
                    f"entry, found {len(matches)}."
                )

    ui_root = "codex_nomad_surface/ui_components/"
    allowed_prefixes = (f"{ui_root}assets/", *runtime_prefixes)
    unexpected = sorted(
        name
        for name in names
        if name.startswith(ui_root)
        and name != f"{ui_root}__init__.py"
        and not name.startswith(allowed_prefixes)
    )
    if unexpected:
        joined = ", ".join(unexpected)
        raise DevCommandError(
            f"{artifact} contains unregistered UI component assets: {joined}"
        )

    package_manifest = "codex_nomad_surface/pyproject.toml"
    if package_manifest not in names:
        raise DevCommandError(
            f"{artifact} does not contain the Streamlit component manifest: "
            f"{package_manifest}"
        )


def verify_package_wheel(
    wheel_path: Path, components: tuple[Component, ...]
) -> None:
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            names = tuple(name for name in archive.namelist() if not name.endswith("/"))
    except (OSError, zipfile.BadZipFile) as exc:
        raise DevCommandError(f"Unable to inspect wheel {wheel_path}: {exc}") from exc
    _verify_package_entries(names, components, artifact="Wheel")


def verify_package_sdist(
    sdist_path: Path, components: tuple[Component, ...]
) -> None:
    try:
        with tarfile.open(sdist_path, "r:gz") as archive:
            archived = tuple(member.name for member in archive.getmembers() if member.isfile())
    except (OSError, tarfile.TarError) as exc:
        raise DevCommandError(f"Unable to inspect sdist {sdist_path}: {exc}") from exc

    names = tuple(name.partition("/")[2] for name in archived if "/" in name)
    component_sources = sorted(name for name in names if name.startswith("components/"))
    if component_sources:
        joined = ", ".join(component_sources)
        raise DevCommandError(f"Sdist contains frontend component sources: {joined}")
    _verify_package_entries(names, components, artifact="Sdist")


def build_package(components: tuple[Component, ...]) -> None:
    python = _venv_python()
    if not python.is_file():
        raise DevCommandError("The virtual environment is missing. Run setup first.")
    clean_python_package_outputs()
    _run([str(python), "-m", "build", "--outdir", str(PACKAGE_OUTPUT_DIR)])
    wheels = sorted(PACKAGE_OUTPUT_DIR.glob("*.whl"))
    sdists = sorted(PACKAGE_OUTPUT_DIR.glob("*.tar.gz"))
    if len(wheels) != 1:
        raise DevCommandError(
            f"Expected exactly one wheel in {PACKAGE_OUTPUT_DIR}, found {len(wheels)}."
        )
    if len(sdists) != 1:
        raise DevCommandError(
            f"Expected exactly one sdist in {PACKAGE_OUTPUT_DIR}, found {len(sdists)}."
        )
    verify_package_wheel(wheels[0], components)
    verify_package_sdist(sdists[0], components)
    print(
        f"Built and verified {wheels[0].relative_to(ROOT)} and "
        f"{sdists[0].relative_to(ROOT)}",
        flush=True,
    )


def select_component(components: tuple[Component, ...], name: str) -> Component:
    for component in components:
        if component.name == name:
            return component
    available = ", ".join(component.name for component in components)
    raise DevCommandError(f"Unknown component {name!r}. Available components: {available}")


def setup(components: tuple[Component, ...]) -> None:
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(map(str, MINIMUM_PYTHON))
        raise DevCommandError(f"Python {required} or newer is required.")
    if not _venv_python().is_file():
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])

    npm = _npm_command()
    for component in components:
        _run([npm, "ci"], cwd=component.frontend)

    _run([str(_venv_python()), "-m", "pip", "install", "-e", ".[dev]"])
    print("Development environment is ready.", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("setup", help="Prepare Python and all frontend components.")
    subparsers.add_parser("build-components", help="Build every frontend component.")
    subparsers.add_parser(
        "build-package", help="Clean and build verified Python distributions."
    )
    one = subparsers.add_parser("build-component", help="Build one frontend component.")
    one.add_argument("name")
    subparsers.add_parser("check-components", help="Verify generated component assets.")
    subparsers.add_parser("list-components", help="List registered frontend components.")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        components = load_components()
        if args.command == "setup":
            setup(components)
        elif args.command == "build-components":
            for component in components:
                build_component(component)
        elif args.command == "build-package":
            build_package(components)
        elif args.command == "build-component":
            build_component(select_component(components, args.name))
        elif args.command == "check-components":
            for component in components:
                verify_component(component)
                print(f"OK {component.name}")
        elif args.command == "list-components":
            for component in components:
                print(component.name)
        return 0
    except (DevCommandError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
