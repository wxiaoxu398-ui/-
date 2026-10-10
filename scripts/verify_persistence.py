"""Regression: reopen >1000 Chroma vectors in a new process from a Unicode project."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def step(mode):
    import chromadb
    from app.rag.storage import vector_storage_path
    requested = ROOT / '.local/acceptance/persistence-测试/chroma'
    path = vector_storage_path(requested)
    collection = chromadb.PersistentClient(path=str(path)).get_or_create_collection('persistence_check', embedding_function=None, metadata={'hnsw:space': 'cosine'})
    if mode == 'write':
        collection.upsert(ids=[str(i) for i in range(1200)], embeddings=[[float(i % 37 + 1), float(i % 51 + 1), float(i % 11 + 1), 1.0] for i in range(1200)])
    assert collection.count() == 1200
    ids = collection.query(query_embeddings=[[1.0, 1.0, 1.0, 1.0]], n_results=2)['ids'][0]
    assert ids[0] == '0', ids
    print(json.dumps({'stage': mode, 'count': collection.count(), 'requested_has_unicode': not str(requested).isascii(), 'storage_is_ascii': str(path).isascii(), 'top_id': ids[0]}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--step', choices=['write', 'read'])
    args = parser.parse_args()
    if args.step:
        step(args.step)
    else:
        for mode in ('write', 'read'):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), '--step', mode], check=True)
