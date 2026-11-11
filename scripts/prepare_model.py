"""Explicit first-time model preparation; ordinary startup never downloads."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.config import settings
from app.rag.embedder import cached_model_path

if settings.embedding_backend == 'bge':
    required = ['config.json', 'tokenizer.json', 'modules.json', '1_Pooling/config.json']
    def complete(path):
        return all((path / name).is_file() for name in required) and any(
            (path / name).is_file() for name in ['model.safetensors', 'pytorch_model.bin']
        )
    try:
        location = cached_model_path()
    except FileNotFoundError:
        location = None
    if location is None or not complete(location):
        from huggingface_hub import snapshot_download
        location = Path(snapshot_download(settings.embedding_model, cache_dir=str(settings.model_cache_dir)))
    if not complete(location):
        raise RuntimeError('The local model is incomplete. Check the download connection and retry preparation.')
    print('Local retrieval model is prepared; normal startup needs no model-host connection.')
else:
    print('BM25 mode needs no embedding-model download.')
