"""PEP 517 backend that prepares frontend components before packaging."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from typing import Any

from setuptools import build_meta as _setuptools_backend


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "components.toml"
RUNTIME_ROOT = ROOT / "codex_nomad_surface" / "ui_components" / "generated"
PACKAGE_MANIFEST_PATH = ROOT / "codex_nomad_surface" / "pyproject.toml"
PROJECT_CONFIG_PATH = ROOT / "pyproject.toml"
COMPONENT_LICENSES_ROOT = ROOT / "components" / "licenses"
PACKAGE_LICENSES_ROOT = ROOT / "codex_nomad_surface" / "licenses"
COMPONENT_LICENSE_FILES = (
    "THIRD_PARTY_LICENSES.md",
    "TLDRAW_LICENSE.md",
)


@dataclass(frozen=True)
class BuildComponent:
    name: str
    frontend: Path
    runtime_dir: Path
    entry_js: str
    entry_css: str


def _load_components() -> tuple[BuildComponent, ...]:
    try:
        registry = tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"Unable to read {REGISTRY_PATH.name}: {exc}") from exc

    entries = registry.get("components")
    if registry.get("version") != 1 or not isinstance(entries, list) or not entries:
        raise RuntimeError(
            "components.toml must contain a version 1 component registry."
        )

    components: list[BuildComponent] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Each [[components]] entry must be a table.")
        name = entry.get("name")
        frontend_value = entry.get("frontend")
        runtime_value = entry.get("runtime_dir")
        entry_js = entry.get("entry_js")
        entry_css = entry.get("entry_css")
        if not all(
            isinstance(value, str) and value
            for value in (name, frontend_value, runtime_value, entry_js, entry_css)
        ):
            raise RuntimeError(
                "Each component must define name, frontend, runtime_dir, entry_js, "
                "and entry_css."
            )

        frontend = (ROOT / frontend_value).resolve()
        runtime_dir = (ROOT / runtime_value).resolve()
        try:
            frontend.relative_to(ROOT)
            relative_runtime = runtime_dir.relative_to(RUNTIME_ROOT)
        except ValueError as exc:
            raise RuntimeError(
                f"{name}: component path leaves its allowed root."
            ) from exc
        if relative_runtime == Path("."):
            raise RuntimeError(f"{name}: runtime_dir must name a component directory.")
        components.append(
            BuildComponent(
                name=name,
                frontend=frontend,
                runtime_dir=runtime_dir,
                entry_js=entry_js,
                entry_css=entry_css,
            )
        )
    return tuple(components)


def _verify_generated_components(components: tuple[BuildComponent, ...]) -> None:
    for component in components:
        files = tuple(
            path for path in component.runtime_dir.rglob("*") if path.is_file()
        )
        if not files:
            relative = component.runtime_dir.relative_to(ROOT)
            raise RuntimeError(
                f"Generated component assets are missing from {relative}. "
                "Run the project component build first."
            )
        for pattern in (component.entry_js, component.entry_css):
            matches = tuple(
                path for path in component.runtime_dir.glob(pattern) if path.is_file()
            )
            if len(matches) != 1:
                raise RuntimeError(
                    f"{component.name}: expected exactly one {pattern!r} generated "
                    f"entry, found {len(matches)}."
                )


def _write_package_manifest(components: tuple[BuildComponent, ...]) -> None:
    try:
        config = tomllib.loads(PROJECT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"Unable to read {PROJECT_CONFIG_PATH.name}: {exc}") from exc

    project = config.get("project")
    streamlit = config.get("tool", {}).get("streamlit", {})
    entries = streamlit.get("component", {}).get("components")
    if not isinstance(project, dict) or not isinstance(entries, list):
        raise RuntimeError(
            "pyproject.toml must define project and Streamlit components."
        )
    project_name = project.get("name")
    project_version = project.get("version")
    if not all(
        isinstance(value, str) and value for value in (project_name, project_version)
    ):
        raise RuntimeError("The project name and version must be static strings.")

    expected_asset_dirs = {
        component.runtime_dir.relative_to(ROOT / "codex_nomad_surface").as_posix()
        for component in components
    }
    manifest_entries: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Each Streamlit component entry must be a table.")
        name = entry.get("name")
        asset_dir = entry.get("asset_dir")
        if not all(isinstance(value, str) and value for value in (name, asset_dir)):
            raise RuntimeError("Each Streamlit component needs name and asset_dir.")
        manifest_entries.append((name, asset_dir))
    configured_asset_dirs = {asset_dir for _, asset_dir in manifest_entries}
    configured_names = {name for name, _ in manifest_entries}
    if (
        len(manifest_entries) != len(components)
        or len(configured_names) != len(manifest_entries)
        or len(configured_asset_dirs) != len(manifest_entries)
        or configured_asset_dirs != expected_asset_dirs
    ):
        raise RuntimeError(
            "Streamlit component names must be unique and asset_dir entries must "
            "match components.toml."
        )

    lines = [
        "# Generated by nomad_build_backend.py; do not edit.",
        "[project]",
        f"name = {json.dumps(project_name)}",
        f"version = {json.dumps(project_version)}",
    ]
    for name, asset_dir in manifest_entries:
        lines.extend(
            (
                "",
                "[[tool.streamlit.component.components]]",
                f"name = {json.dumps(name)}",
                f"asset_dir = {json.dumps(asset_dir)}",
            )
        )
    PACKAGE_MANIFEST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sync_component_licenses() -> None:
    PACKAGE_LICENSES_ROOT.mkdir(parents=True, exist_ok=True)
    for filename in COMPONENT_LICENSE_FILES:
        source = COMPONENT_LICENSES_ROOT / filename
        if not source.is_file():
            raise RuntimeError(f"Component license file is missing: {source}.")
        shutil.copyfile(source, PACKAGE_LICENSES_ROOT / filename)


def _prepare_components() -> None:
    components = _load_components()
    is_sdist = (ROOT / "PKG-INFO").is_file()
    source_markers = tuple(
        (component.frontend / "package.json").is_file()
        for component in components
    )
    if is_sdist:
        if any(source_markers):
            raise RuntimeError(
                "The source distribution unexpectedly contains frontend sources."
            )
    else:
        if not all(source_markers):
            missing_sources = [
                component.frontend.relative_to(ROOT)
                for component, present in zip(components, source_markers, strict=True)
                if not present
            ]
            joined = ", ".join(map(str, missing_sources))
            raise RuntimeError(
                f"The frontend component source tree is incomplete: {joined}."
            )
        missing_dependencies = [
            component.frontend.relative_to(ROOT)
            for component in components
            if not (component.frontend / "node_modules").is_dir()
        ]
        if missing_dependencies:
            joined = ", ".join(map(str, missing_dependencies))
            raise RuntimeError(
                f"Frontend dependencies are missing for: {joined}. "
                "Run python scripts/dev.py setup first."
            )
        script = ROOT / "scripts" / "dev.py"
        if not script.is_file():
            raise RuntimeError(
                "scripts/dev.py is required to build frontend components."
            )
        subprocess.run(
            [sys.executable, str(script), "build-components"],
            cwd=ROOT,
            check=True,
        )
        _sync_component_licenses()
    _verify_generated_components(components)
    _write_package_manifest(components)


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _prepare_components()
    return _setuptools_backend.build_wheel(
        wheel_directory, config_settings, metadata_directory
    )


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    _prepare_components()
    return _setuptools_backend.build_sdist(sdist_directory, config_settings)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _prepare_components()
    return _setuptools_backend.build_editable(
        wheel_directory, config_settings, metadata_directory
    )


def _prepare_package_manifest() -> None:
    _write_package_manifest(_load_components())


def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    _prepare_package_manifest()
    return _setuptools_backend.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    _prepare_package_manifest()
    return _setuptools_backend.get_requires_for_build_sdist(config_settings)


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    _prepare_package_manifest()
    return _setuptools_backend.prepare_metadata_for_build_wheel(
        metadata_directory, config_settings
    )


def get_requires_for_build_editable(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    _prepare_package_manifest()
    return _setuptools_backend.get_requires_for_build_editable(config_settings)


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    _prepare_package_manifest()
    return _setuptools_backend.prepare_metadata_for_build_editable(
        metadata_directory, config_settings
    )
