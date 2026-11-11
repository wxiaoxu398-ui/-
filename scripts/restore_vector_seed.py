"""Install a review package's public-book index into its new ASCII cache."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def restore():
    from app.config import settings
    from app.rag.storage import vector_storage_path
    from app.db import engine
    from app.models import Document, Chunk
    from sqlmodel import Session, select

    seed = ROOT / 'runtime/vector-seed'
    manifest = json.loads((ROOT / 'runtime/vector-seed-manifest.json').read_text(encoding='utf-8'))
    with Session(engine) as session:
        documents = session.exec(select(Document)).all()
        actual = sorted((d.id, d.file_hash, d.num_chunks) for d in documents)
        chunks = session.exec(select(Chunk.id)).all()
    expected = sorted((d['id'], d['file_hash'], d['num_chunks']) for d in manifest['documents'])
    if actual != expected or len(chunks) != manifest['num_chunks']:
        print('Library has changed; preserving its current vector index.')
        return
    target = vector_storage_path(settings.chroma_dir).resolve()
    if target.exists() and any(target.iterdir()):
        print('Existing vector index retained.')
        return
    # Verify the complete seed before copying; never overwrite an existing index.
    for name, digest in manifest.get('sha256', {}).items():
        file = (seed / name).resolve()
        if not file.is_relative_to(seed.resolve()) or not file.is_file():
            raise ValueError('Invalid or missing vector seed file.')
        with file.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != digest:
                raise ValueError('Vector seed checksum mismatch.')
    allowed = [settings.chroma_dir.resolve()]
    for variable in ('LOCALAPPDATA', 'PROGRAMDATA'):
        if os.environ.get(variable):
            allowed.append((Path(os.environ[variable]) / 'MedRAG/vector-cache').resolve())
    if not any(target == root or target.is_relative_to(root) for root in allowed):
        raise ValueError('Vector cache target is outside its expected directory.')
    target.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(target.name + '.seed-' + uuid.uuid4().hex)
    if stage.resolve().parent != target.parent:
        raise ValueError('Invalid vector seed staging directory.')
    if any(file.is_symlink() for file in seed.rglob('*')):
        raise ValueError('Vector seeds must not contain links.')
    shutil.copytree(seed, stage)
    if any(target.iterdir()):
        raise RuntimeError('The index changed during startup; preserving both copies.')
    target.rmdir()  # Only the empty directory created by vector_storage_path.
    stage.rename(target)
    print('Prepared the review library index for this directory.')


if __name__ == '__main__':
    restore()
