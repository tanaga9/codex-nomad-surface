from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev.py"
SPEC = importlib.util.spec_from_file_location("nomad_dev_commands", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
dev = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dev
SPEC.loader.exec_module(dev)


@pytest.mark.parametrize(
    ("recorded", "version", "should_install"),
    [
        (["streamlit==1.66.1"], "1.66.1", False),
        (["streamlit==1.63.0"], "1.66.1", True),
        (["streamlit==1.66.1"], "1.63.0", True),
        (["streamlit==1.66.1", "removed-package>=1"], "1.66.1", True),
        (["streamlit==1.66.1", 'pytest>=8; extra == "test"'], "1.66.1", False),
    ],
)
def test_dependency_sync_checks_metadata_and_installed_versions(
    tmp_path, monkeypatch, recorded, version, should_install,
):
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "surface"\nversion = "0.1.0"\n'
        'dependencies = ["streamlit==1.66.1"]\n', encoding="utf-8",
    )
    monkeypatch.setattr(dev.metadata, "distribution", lambda name: SimpleNamespace(
        version="0.1.0", requires=recorded,
    ))
    monkeypatch.setattr(dev.metadata, "version", lambda name: version)
    commands = []
    monkeypatch.setattr(dev, "_run", lambda command: commands.append(command))

    dev.sync_dependencies()

    assert commands == (
        [[sys.executable, "-m", "pip", "install", "-e", "."]] if should_install else []
    )


def test_dependency_sync_installs_missing_package(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "surface"\nversion = "0.1.0"\n'
        'dependencies = ["missing-package>=1"]\n', encoding="utf-8",
    )
    monkeypatch.setattr(dev.metadata, "distribution", lambda name: SimpleNamespace(
        version="0.1.0", requires=["missing-package>=1"],
    ))
    def missing(name):
        raise dev.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(dev.metadata, "version", missing)
    commands = []
    monkeypatch.setattr(dev, "_run", lambda command: commands.append(command))

    dev.sync_dependencies()

    assert len(commands) == 1


def test_macos_launcher_stops_after_failed_dependency_sync(tmp_path):
    import shutil
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("macOS launcher requires zsh")
    launcher = tmp_path / "run.command"
    launcher.write_text((SCRIPT.parents[1] / "run.command").read_text())
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\necho "$*" >> calls.txt\nexit 7\n')
    python.chmod(0o755)

    result = subprocess.run([zsh, str(launcher)], capture_output=True, text=True)

    assert result.returncode == 7
    assert (tmp_path / "calls.txt").read_text().splitlines() == [
        "scripts/dev.py sync-dependencies"
    ]


def test_registry_contains_surface_components() -> None:
    components = dev.load_components()

    assert [component.name for component in components] == [
        "nomad-canvas",
        "nomad-text",
    ]
    assert components[0].runtime_dir.is_relative_to(dev.RUNTIME_ROOT)


def test_sync_component_replaces_stale_runtime_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "generated"
    monkeypatch.setattr(dev, "RUNTIME_ROOT", runtime_root)
    build_dir = tmp_path / "frontend" / "build"
    runtime_dir = runtime_root / "test-component"
    build_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (build_dir / "index-example.js").write_text("new js", encoding="utf-8")
    (build_dir / "index-example.css").write_text("new css", encoding="utf-8")
    (runtime_dir / "stale.js").write_text("stale", encoding="utf-8")
    component = dev.Component(
        name="test-component",
        frontend=build_dir.parent,
        build_dir=build_dir,
        runtime_dir=runtime_dir,
        entry_js="index-*.js",
        entry_css="index-*.css",
    )

    dev.sync_component(component)

    assert not (runtime_dir / "stale.js").exists()
    assert (runtime_dir / "index-example.js").read_text(encoding="utf-8") == "new js"
    assert (runtime_dir / "index-example.css").read_text(encoding="utf-8") == "new css"


def test_sync_component_rejects_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "generated"
    monkeypatch.setattr(dev, "RUNTIME_ROOT", runtime_root)
    build_dir = tmp_path / "frontend" / "build"
    build_dir.mkdir(parents=True)
    component = dev.Component(
        name="test-component",
        frontend=build_dir.parent,
        build_dir=build_dir,
        runtime_dir=runtime_root,
        entry_js="index-*.js",
        entry_css="index-*.css",
    )

    with pytest.raises(dev.DevCommandError, match="must name a component directory"):
        dev.sync_component(component)


def test_clean_python_package_outputs_removes_only_generated_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_dir = tmp_path / "build"
    dist_dir = tmp_path / "dist"
    metadata_dir = tmp_path / "codex_nomad_surface.egg-info"
    preserved = tmp_path / "preserved.txt"
    build_dir.mkdir()
    dist_dir.mkdir()
    metadata_dir.mkdir()
    preserved.write_text("keep", encoding="utf-8")
    (build_dir / "stale.js").write_text("stale", encoding="utf-8")
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "PYTHON_BUILD_DIR", build_dir)
    monkeypatch.setattr(dev, "PACKAGE_OUTPUT_DIR", dist_dir)
    monkeypatch.setattr(dev, "PACKAGE_METADATA_DIR", metadata_dir)

    dev.clean_python_package_outputs()

    assert not build_dir.exists()
    assert not dist_dir.exists()
    assert not metadata_dir.exists()
    assert preserved.read_text(encoding="utf-8") == "keep"


def _component_with_nested_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dev.Component:
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    runtime_dir = (
        tmp_path
        / "codex_nomad_surface"
        / "ui_components"
        / "generated"
        / "test_component"
    )
    nested_dir = runtime_dir / "assets"
    nested_dir.mkdir(parents=True)
    (runtime_dir / "index-example.js").write_text("generated js", encoding="utf-8")
    (runtime_dir / "index-example.css").write_text("generated css", encoding="utf-8")
    (nested_dir / "worker.js").write_text("generated worker", encoding="utf-8")
    return dev.Component(
        name="test-component",
        frontend=tmp_path / "frontend",
        build_dir=tmp_path / "frontend" / "build",
        runtime_dir=runtime_dir,
        entry_js="index-*.js",
        entry_css="index-*.css",
    )


def _write_runtime_assets(
    archive: zipfile.ZipFile,
    component: dev.Component,
    *,
    omit: Path | None = None,
) -> None:
    for path in component.runtime_dir.rglob("*"):
        if path.is_file() and path != omit:
            archive.write(path, path.relative_to(dev.ROOT).as_posix())
    archive.writestr(
        "codex_nomad_surface/pyproject.toml",
        "[project]\nname = 'test'\nversion = '0.0.0'\n",
    )
    for filename in dev.REQUIRED_LICENSE_FILES:
        archive.writestr(f"test-0.0.0.dist-info/licenses/{filename}", "license")


def test_verify_package_wheel_rejects_missing_third_party_licenses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = _component_with_nested_asset(tmp_path, monkeypatch)
    wheel = tmp_path / "missing-licenses.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in component.runtime_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(dev.ROOT).as_posix())
        archive.writestr(
            "codex_nomad_surface/pyproject.toml",
            "[project]\nname = 'test'\nversion = '0.0.0'\n",
        )

    with pytest.raises(dev.DevCommandError, match="missing required license files"):
        dev.verify_package_wheel(wheel, (component,))


def test_verify_package_wheel_rejects_missing_nested_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = _component_with_nested_asset(tmp_path, monkeypatch)
    worker = component.runtime_dir / "assets" / "worker.js"
    wheel = tmp_path / "missing-asset.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        _write_runtime_assets(archive, component, omit=worker)

    with pytest.raises(dev.DevCommandError, match="component assets differ"):
        dev.verify_package_wheel(wheel, (component,))


def test_verify_package_wheel_rejects_unregistered_component_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = _component_with_nested_asset(tmp_path, monkeypatch)
    wheel = tmp_path / "package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        _write_runtime_assets(archive, component)
        archive.writestr(
            "codex_nomad_surface/ui_components/nomad_canvas/index-old.js",
            "stale js",
        )

    with pytest.raises(dev.DevCommandError, match="unregistered UI component assets"):
        dev.verify_package_wheel(wheel, (component,))
