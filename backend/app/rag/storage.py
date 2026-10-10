"""Keep native HNSW files on ASCII Windows paths; SQLite still stays in the project."""
import hashlib
import os
from pathlib import Path


def vector_storage_path(requested):
    requested = Path(requested).resolve()
    if os.name != 'nt' or str(requested).isascii():
        return requested
    identifier = hashlib.sha256(str(requested).casefold().encode('utf-8')).hexdigest()[:24]
    for variable in ('LOCALAPPDATA', 'PROGRAMDATA'):
        root = os.environ.get(variable)
        if root and str(root).isascii():
            target = Path(root) / 'MedRAG' / 'vector-cache' / identifier
            try:
                target.mkdir(parents=True, exist_ok=True)
                return target
            except OSError:
                continue
    raise ValueError('Windows 向量索引需要可写的英文路径，请将 CHROMA_DIR 设为不含中文的目录。')
