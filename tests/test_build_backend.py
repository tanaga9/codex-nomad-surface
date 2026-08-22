from __future__ import annotations

from pathlib import Path

import pytest

import nomad_build_backend as backend


def _test_component(tmp_path: Path) -> backend.BuildComponent:
    frontend = tmp_path / "frontend"
    runtime_dir = tmp_path / "generated" / "test_component"
    nested_dir = runtime_dir / "assets"
    frontend.mkdir()
    nested_dir.mkdir(parents=True)
    (runtime_dir / "index-example.js").write_text("entry js", encoding="utf-8")
    (runtime_dir / "index-example.css").write_text("entry css", encoding="utf-8")
    (nested_dir / "worker.js").write_text("worker", encoding="utf-8")
    return backend.BuildComponent(
        name="test-component",
        frontend=frontend,
        runtime_dir=runtime_dir,
        entry_js="index-*.js",
        entry_css="index-*.css",
    )


def test_prepare_components_uses_sdist_assets_without_frontend_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = _test_component(tmp_path)
    (tmp_path / "PKG-INFO").write_text("Metadata-Version: 2.4\n", encoding="utf-8")
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    monkeypatch.setattr(backend, "_load_components", lambda: (component,))
    monkeypatch.setattr(backend, "_write_package_manifest", lambda components: None)

    def unexpected_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("The frontend build must not run for an sdist.")

    monkeypatch.setattr(backend.subprocess, "run", unexpected_run)

    backend._prepare_components()


def test_prepare_components_rejects_missing_source_in_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = _test_component(tmp_path)
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    monkeypatch.setattr(backend, "_load_components", lambda: (component,))
    monkeypatch.setattr(backend, "_write_package_manifest", lambda components: None)

    with pytest.raises(RuntimeError, match="source tree is incomplete"):
        backend._prepare_components()


def test_package_manifest_is_generated_from_project_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = _test_component(tmp_path)
    package_root = tmp_path / "codex_nomad_surface"
    component = backend.BuildComponent(
        name=component.name,
        frontend=component.frontend,
        runtime_dir=package_root / "ui_components" / "generated" / "test_component",
        entry_js=component.entry_js,
        entry_css=component.entry_css,
    )
    project_config = tmp_path / "pyproject.toml"
    package_manifest = package_root / "pyproject.toml"
    package_root.mkdir()
    project_config.write_text(
        """\
[project]
name = "test-project"
version = "1.2.3"

[[tool.streamlit.component.components]]
name = "test_component"
asset_dir = "ui_components/generated/test_component"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    monkeypatch.setattr(backend, "PROJECT_CONFIG_PATH", project_config)
    monkeypatch.setattr(backend, "PACKAGE_MANIFEST_PATH", package_manifest)

    backend._write_package_manifest((component,))

    generated = package_manifest.read_text(encoding="utf-8")
    assert 'name = "test-project"' in generated
    assert 'name = "test_component"' in generated
    assert 'asset_dir = "ui_components/generated/test_component"' in generated


def test_package_manifest_rejects_duplicate_component_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "codex_nomad_surface"
    first = backend.BuildComponent(
        name="first",
        frontend=tmp_path / "first",
        runtime_dir=package_root / "ui_components" / "generated" / "first",
        entry_js="index-*.js",
        entry_css="index-*.css",
    )
    second = backend.BuildComponent(
        name="second",
        frontend=tmp_path / "second",
        runtime_dir=package_root / "ui_components" / "generated" / "second",
        entry_js="index-*.js",
        entry_css="index-*.css",
    )
    project_config = tmp_path / "pyproject.toml"
    project_config.write_text(
        """\
[project]
name = "test-project"
version = "1.2.3"

[[tool.streamlit.component.components]]
name = "duplicate"
asset_dir = "ui_components/generated/first"

[[tool.streamlit.component.components]]
name = "duplicate"
asset_dir = "ui_components/generated/second"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    monkeypatch.setattr(backend, "PROJECT_CONFIG_PATH", project_config)
    monkeypatch.setattr(
        backend, "PACKAGE_MANIFEST_PATH", package_root / "pyproject.toml"
    )

    with pytest.raises(RuntimeError, match="names must be unique"):
        backend._write_package_manifest((first, second))
