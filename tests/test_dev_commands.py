from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev.py"
SPEC = importlib.util.spec_from_file_location("nomad_dev_commands", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
dev = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dev
SPEC.loader.exec_module(dev)


def test_registry_contains_canvas() -> None:
    components = dev.load_components()

    assert [component.name for component in components] == ["nomad-canvas"]
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
