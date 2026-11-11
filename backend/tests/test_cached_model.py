from pathlib import Path

import pytest


def test_cached_model_resolution_never_requests_download(tmp_path, monkeypatch):
    from app.config import settings
    from app.rag.embedder import cached_model_path
    import huggingface_hub
    captured = {}
    def resolve(repo, **kwargs):
        captured.update(kwargs)
        return str(tmp_path)
    monkeypatch.setattr(huggingface_hub, 'snapshot_download', resolve)
    monkeypatch.setattr(settings, 'embedding_model', 'example/model')
    assert cached_model_path() == tmp_path
    assert captured['local_files_only'] is True


def test_missing_model_has_actionable_preparation_error(monkeypatch):
    from app.config import settings
    from app.rag.embedder import cached_model_path
    import huggingface_hub
    from huggingface_hub.errors import LocalEntryNotFoundError
    def missing(*args, **kwargs):
        assert kwargs['local_files_only'] is True
        raise LocalEntryNotFoundError('not cached')
    monkeypatch.setattr(huggingface_hub, 'snapshot_download', missing)
    monkeypatch.setattr(settings, 'embedding_model', 'example/model')
    with pytest.raises(FileNotFoundError, match='prepare-medrag.cmd'):
        cached_model_path()


def test_local_model_directory_does_not_need_hub(tmp_path, monkeypatch):
    from app.config import settings
    from app.rag.embedder import cached_model_path
    monkeypatch.setattr(settings, 'embedding_model', str(tmp_path))
    assert cached_model_path() == Path(tmp_path).resolve()
