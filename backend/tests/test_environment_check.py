"""Environment diagnostics use disposable files and never inspect live data."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "medrag_environment_check", Path(__file__).resolve().parents[2] / "scripts/check_environment.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def frontend(root, reference="/assets/app.js?v=1"):
    dist = root / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(f'<script src="{reference}"></script>', encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    return dist


def model(root):
    root.mkdir(parents=True)
    for name in ("config.json", "tokenizer.json", "model.safetensors"):
        (root / name).write_bytes(b"{}")
    return root


def test_prebuilt_frontend_works_without_node_or_markers(tmp_path):
    frontend(tmp_path)
    assert checker.check_frontend(tmp_path)["ok"]
    (tmp_path / "frontend/dist/assets/app.js").unlink()
    assert not checker.check_frontend(tmp_path)["ok"]


@pytest.mark.parametrize("reference", ["/assets/../index.html", "/assets/%2e%2e/index.html"])
def test_assets_cannot_escape_the_built_asset_directory(tmp_path, reference):
    frontend(tmp_path, reference)
    assert not checker.check_frontend(tmp_path)["ok"]


def test_bge_normalize_needs_no_files_but_pooling_and_weights_do(tmp_path):
    snapshot = model(tmp_path / "snapshot")
    (snapshot / "modules.json").write_text(json.dumps([
        {"path": "", "type": "sentence_transformers.models.Transformer"},
        {"path": "1_Pooling", "type": "sentence_transformers.models.Pooling"},
        {"path": "2_Normalize", "type": "sentence_transformers.models.Normalize"},
    ]), encoding="utf-8")
    assert not checker.model_files_complete(snapshot)
    (snapshot / "1_Pooling").mkdir()
    (snapshot / "1_Pooling/config.json").write_text("{}", encoding="utf-8")
    assert checker.model_files_complete(snapshot)
    (snapshot / "model.safetensors").unlink()
    assert not checker.model_files_complete(snapshot)


def test_model_cache_lookup_is_explicitly_local_only(tmp_path, monkeypatch):
    snapshot = model(tmp_path / "cached-snapshot")
    calls = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        return snapshot

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download))
    cache = tmp_path / "model-cache"
    assert checker.check_model("BAAI/bge-small-zh-v1.5", cache, root=tmp_path)["ok"]
    assert calls == [{"repo_id": "BAAI/bge-small-zh-v1.5", "cache_dir": str(cache), "local_files_only": True}]


def test_relative_model_path_matches_backend_working_directory(tmp_path, monkeypatch):
    model(tmp_path / "backend" / "local-model")

    def no_download(**kwargs):
        raise AssertionError("local path must not need a cache lookup")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=no_download))
    assert checker.check_model("local-model", tmp_path / "cache", root=tmp_path)["ok"]


def test_unconfigured_runtime_model_does_not_hide_missing_cache(tmp_path, monkeypatch):
    model(tmp_path / "runtime" / "model")
    calls = []

    def no_cached_model(**kwargs):
        calls.append(kwargs)
        raise FileNotFoundError

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=no_cached_model))
    assert not checker.check_model("BAAI/bge-small-zh-v1.5", tmp_path / "cache", root=tmp_path)["ok"]
    assert calls and calls[0]["local_files_only"] is True


def test_heavy_dependency_check_does_not_import_torch(monkeypatch):
    monkeypatch.setattr(checker.metadata, "version", lambda name: "1.0")
    monkeypatch.setattr(checker.util, "find_spec", lambda name: SimpleNamespace())
    imports = []
    monkeypatch.setattr(checker.importlib, "import_module", lambda name: imports.append(name))
    assert checker.check_dependency("torch", "torch", import_module=False)["ok"]
    assert not imports


def test_json_report_is_ascii_and_omits_exception_details(monkeypatch, capsys):
    def fail():
        raise RuntimeError("private-value-must-not-appear")

    monkeypatch.setattr(checker, "run_checks", fail)
    assert checker.main(["--json"]) == 1
    output = capsys.readouterr()
    assert output.out.isascii() and not output.err
    assert "private-value-must-not-appear" not in output.out
    report = json.loads(output.out)
    assert set(report) == {"ready", "checks"}
    assert not report["ready"]
    assert set(report["checks"][0]) == {"name", "ok", "message"}
