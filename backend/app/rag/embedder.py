from functools import lru_cache
from ..config import settings


@lru_cache(maxsize=1)
def model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.embedding_model, device=settings.embedding_device,
                               cache_folder=str(settings.model_cache_dir), trust_remote_code=False)


def embed(texts: list[str], query=False):
    if query:
        texts = ['为这个句子生成表示以用于检索相关文章：' + text for text in texts]
    return model().encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False).tolist()


@lru_cache(maxsize=1)
def collection():
    import chromadb
    from chromadb.config import Settings
    client = chromadb.PersistentClient(path=str(settings.chroma_dir), settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection('med_chunks', metadata={'hnsw:space': 'cosine'}, embedding_function=None)


def delete_document(document_id: int):
    if settings.embedding_backend == 'bge':
        collection().delete(where={'document_id': document_id})
