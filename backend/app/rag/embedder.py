from functools import lru_cache
from pathlib import Path
from ..config import settings
from .storage import vector_storage_path


def cached_model_path():
    """Resolve the local snapshot without contacting the model host at boot."""
    direct = Path(settings.embedding_model)
    if direct.is_dir():
        return direct.resolve()
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    try:
        return Path(snapshot_download(settings.embedding_model, cache_dir=str(settings.model_cache_dir),
                                      local_files_only=True))
    except LocalEntryNotFoundError as exc:
        raise FileNotFoundError('本地检索模型未准备好。请联网运行 prepare-medrag.cmd，完成后重新启动。') from exc


@lru_cache(maxsize=1)
def model():
    local_model = cached_model_path()
    if settings.embedding_device == 'cpu':
        import torch
        torch.set_num_threads(settings.embedding_cpu_threads)
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(str(local_model), device=settings.embedding_device,
                               local_files_only=True, trust_remote_code=False)


def embed(texts: list[str], query=False):
    if query:
        texts = ['为这个句子生成表示以用于检索相关文章：' + text for text in texts]
    return model().encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False).tolist()


@lru_cache(maxsize=1)
def client():
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(path=str(vector_storage_path(settings.chroma_dir)), settings=Settings(anonymized_telemetry=False))


@lru_cache(maxsize=1)
def collection():
    return client().get_or_create_collection('med_chunks', metadata={'hnsw:space': 'cosine'}, embedding_function=None)


def reset_collection():
    import chromadb.errors
    collection.cache_clear()
    try:
        client().delete_collection('med_chunks')
    except chromadb.errors.NotFoundError:
        pass
    collection()


def delete_document(document_id: int):
    if settings.embedding_backend == 'bge':
        collection().delete(where={'document_id': document_id})
