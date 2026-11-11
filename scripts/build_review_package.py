"""Build a Windows review runtime without copying keys or personal history.

Run with the project's venv Python. The new destination is never overwritten.
The normal source/history submission remains separate from this prepared runtime.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MODEL = 'BAAI/bge-small-zh-v1.5'
EXTRA_SOURCE = (
    'scripts/build_review_package.py', 'scripts/start.ps1', 'scripts/setup.ps1',
    'scripts/prepare_model.py', 'scripts/restore_vector_seed.py',
    'scripts/check_environment.py', 'scripts/check.ps1',
    'start-medrag.cmd', 'stop-medrag.cmd', 'prepare-medrag.cmd', 'check-medrag.cmd',
    'docs/07-启动修复与评审检查.md',
)


class PackageError(RuntimeError):
    pass


def digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source_allowed(name: str) -> bool:
    path = PurePosixPath(name.replace('\\', '/'))
    parts = tuple(part.casefold() for part in path.parts)
    if not parts or path.is_absolute() or '..' in parts or ':' in parts[0]:
        return False
    if any(part in {'.git', '.local', '.venv', 'runtime', 'node_modules', '__pycache__',
                    '.pytest_cache', 'deliverables', 'output', 'tmp'} for part in parts):
        return False
    filename = parts[-1]
    if filename == '.env' or (filename.startswith('.env.') and filename != '.env.example'):
        return False
    if filename.endswith(('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.log', '.pyc', '.pdf')):
        return False
    if '/'.join(parts) == 'submission/team-info.json':
        return False
    if parts[:2] == ('frontend', 'dist') or parts[:3] == ('backend', 'app', 'data'):
        return filename == '.gitkeep'
    return True


def git_output(*args: str) -> bytes:
    result = subprocess.run(['git', *args], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise PackageError('无法读取 Git 文件清单；没有输出可能包含认证信息的 Git 错误详情。')
    return result.stdout


def source_files(revision: str = 'HEAD') -> list[str]:
    names = set(git_output('ls-tree', '-r', '--name-only', '-z', revision).decode('utf-8').split('\0'))
    if not set(EXTRA_SOURCE).issubset(names):
        raise PackageError('请先提交启动修复，再从明确的提交生成运行包。')
    result = []
    for name in sorted(filter(None, names)):
        if not source_allowed(name):
            continue
        source = ROOT / name
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise PackageError('源码清单含越界链接，已停止。')
        if not source.is_file():
            raise PackageError('源码清单含尚未保存或已删除的文件，已停止。')
        result.append(name)
    return result


def copy_tree(source: Path, target: Path, *, ignored: set[str] | None = None) -> None:
    """Materialize safe HF links; never bring a link to the recipient's machine."""
    ignored = ignored or set()
    source_root = source.resolve()
    if not source.is_dir():
        raise PackageError('运行所需目录缺失，请先完成环境准备。')
    for directory, directories, filenames in os.walk(source, followlinks=False):
        directories[:] = [name for name in directories if name not in ignored and name != '__pycache__']
        current = Path(directory)
        for name in directories:
            if (current / name).is_symlink():
                raise PackageError('运行资源包含目录链接，需先展开后再构建。')
        destination = target / current.relative_to(source)
        destination.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            if name in ignored or name in {'pyvenv.cfg', '.env'} or name.endswith(('.pyc', '.log')):
                continue
            original = current / name
            if not original.resolve().is_relative_to(source_root):
                raise PackageError('运行资源存在越界文件链接，已停止。')
            shutil.copy2(original, destination / name)


@contextmanager
def read_only_database(path: Path):
    if not path.is_file():
        raise PackageError('本机演示教材数据库不存在，请先准备并建立索引。')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def approved_books(books_dir: Path) -> dict[str, dict]:
    approved = {}
    for name in ('eval/open-books.json', 'eval/chinese-books.json'):
        for book in json.loads((ROOT / name).read_text(encoding='utf-8'))['books']:
            filename = book['pdf_name']
            if Path(filename).name != filename:
                raise PackageError('公开教材清单中的文件名不安全。')
            source = books_dir / filename
            if source.is_symlink() or source.resolve().parent != books_dir.resolve():
                raise PackageError('公开教材路径越界，已停止。')
            if source.is_file():
                if digest(source) != book['sha256']:
                    raise PackageError('公开教材指纹与许可清单不符，未打包任何未知版本。')
                approved[filename] = book
    if not approved:
        raise PackageError('没有找到许可清单中的公开教材，已停止。')
    return approved


def read_library(path: Path, approved: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    # These are the only production tables ever read. No user conversation is read.
    with read_only_database(path) as connection:
        connection.execute('BEGIN')
        documents = [dict(row) for row in connection.execute('SELECT * FROM document')]
        if set(row['file_name'] for row in documents) != set(approved):
            raise PackageError('当前库包含清单外资料或缺少公开教材，请用独立公开演示库构建。')
        if any(row['status'] != 'ready' or row['indexed_backend'] != 'bge' for row in documents):
            raise PackageError('公开教材尚未全部完成 BGE 入库，请等待索引完成后再构建。')
        chunks = [dict(row) for row in connection.execute('SELECT * FROM chunk')]
    counts = Counter(row['document_id'] for row in chunks)
    if any(row['file_hash'] != approved[row['file_name']]['sha256']
           or counts[row['id']] != row['num_chunks'] for row in documents):
        raise PackageError('教材指纹或分块数量不一致，已停止。')
    if set(counts) != {row['id'] for row in documents}:
        raise PackageError('资料库存在不属于公开教材的分块，已停止。')
    return documents, chunks


def export_database(path: Path, documents: list[dict], chunks: list[dict], approved: dict[str, dict]) -> None:
    from sqlmodel import SQLModel, create_engine
    from app import models  # Register all tables, including empty history tables.

    if path.exists():
        raise PackageError('新数据库目标已存在，拒绝覆盖。')
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f'sqlite:///{path.as_posix()}')
    try:
        SQLModel.metadata.create_all(engine)
        safe_documents = []
        for row in documents:
            book = approved[row['file_name']]
            safe = {name: row[name] for name in models.Document.model_fields if name in row}
            safe.update({name: book.get(name, '') for name in ('title', 'author', 'source', 'license', 'source_url')})
            safe['file_path'] = (PurePosixPath('app/data/books') / row['file_name']).as_posix()
            safe['error'] = ''
            safe['created_at'] = datetime.now(timezone.utc).isoformat()
            safe_documents.append(safe)
        safe_chunks = [{name: row[name] for name in models.Chunk.model_fields if name in row} for row in chunks]
        with engine.begin() as connection:
            connection.execute(models.Document.__table__.insert(), safe_documents)
            if safe_chunks:
                connection.execute(models.Chunk.__table__.insert(), safe_chunks)
        with engine.connect() as connection:
            for name in ('conversation', 'message', 'feedback'):
                if connection.exec_driver_sql(f'SELECT COUNT(*) FROM {name}').scalar_one() != 0:
                    raise PackageError('新库历史清空核验失败，已停止。')
    finally:
        engine.dispose()


def copy_vector_seed(source: Path, target: Path, documents: list[dict], chunks: list[dict]) -> dict:
    expected = {row['id']: row['num_chunks'] for row in documents}
    with read_only_database(source / 'chroma.sqlite3') as connection:
        actual = dict(connection.execute(
            "SELECT int_value, COUNT(*) FROM embedding_metadata WHERE key='document_id' GROUP BY int_value"
        ).fetchall())
        total = connection.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0]
        if actual != expected or total != len(chunks):
            raise PackageError('向量库含额外资料或与公开教材不一致，未复制。')

        # SQLite backup captures committed WAL data; raw copying the file would not.
        target.mkdir(parents=True, exist_ok=False)
        with sqlite3.connect(target / 'chroma.sqlite3') as destination:
            connection.backup(destination)
    files = [path for path in source.rglob('*') if path.is_file()
             and path.name != 'chroma.sqlite3' and not path.name.endswith(('-wal', '-shm', '.lock'))]
    before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
    for original in files:
        if original.is_symlink() or not original.resolve().is_relative_to(source.resolve()):
            raise PackageError('向量索引含越界链接，未复制。')
        destination = target / original.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
    if any((path.stat().st_size, path.stat().st_mtime_ns) != signature for path, signature in before.items()):
        raise PackageError('复制过程中向量文件变化，请停止入库后重新构建一个新目录。')
    # Detect an indexing transaction during the file copy as well.
    with read_only_database(source / 'chroma.sqlite3') as connection:
        after = dict(connection.execute(
            "SELECT int_value, COUNT(*) FROM embedding_metadata WHERE key='document_id' GROUP BY int_value"
        ).fetchall())
        if after != expected:
            raise PackageError('复制过程中资料库变化，请停止入库后重试。')
    return {
        'documents': [{'id': row['id'], 'file_hash': row['file_hash'], 'num_chunks': row['num_chunks']}
                      for row in sorted(documents, key=lambda item: item['id'])],
        'num_chunks': len(chunks),
        'sha256': {path.relative_to(target).as_posix(): digest(path)
                   for path in sorted(target.rglob('*')) if path.is_file()},
    }


def copy_python(target: Path) -> None:
    if os.name != 'nt' or sys.prefix == sys.base_prefix:
        raise PackageError('完整运行包必须用 Windows 项目虚拟环境中的 Python 构建。')
    base = Path(sys.base_prefix)
    site = Path(sys.prefix) / 'Lib/site-packages'
    if not (base / 'python.exe').is_file() or not site.is_dir():
        raise PackageError('找不到可复制的完整 CPython 运行时或项目依赖。')
    copy_tree(base, target, ignored={'site-packages', 'Scripts', 'include', 'libs'})
    copy_tree(site, target / 'Lib/site-packages')
    # No pyvenv.cfg: the recipient uses this interpreter's own relative stdlib.
    if (target / 'pyvenv.cfg').exists():
        raise PackageError('运行时中不应包含虚拟环境的绝对解释器配置。')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--without-python', action='store_true', help='Only for packaging checks; not an independent runtime.')
    args = parser.parse_args()
    output = (args.output or ROOT / 'deliverables' / ('MedRAG-Review-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    if not output.is_absolute():
        output = ROOT / output
    output = output.resolve()
    if not output.is_relative_to((ROOT / 'deliverables').resolve()) or output == (ROOT / 'deliverables').resolve():
        raise PackageError('输出必须位于本项目 deliverables 下的全新子目录。')
    if output.exists():
        raise PackageError('输出目录已存在；不会覆盖或删除，请指定新的目录。')
    sys.path.insert(0, str(ROOT / 'backend'))
    from app.config import settings
    from app.rag.storage import vector_storage_path
    if settings.embedding_backend != 'bge' or settings.embedding_model != EXPECTED_MODEL:
        raise PackageError('本评审包仅支持已验证的 BGE 中文模型和公开教材库。')
    from huggingface_hub import snapshot_download
    snapshot = Path(snapshot_download(EXPECTED_MODEL, cache_dir=str(settings.model_cache_dir), local_files_only=True))
    if not all((snapshot / name).is_file() for name in ('config.json', 'tokenizer.json')):
        raise PackageError('本地模型缓存不完整，请先运行准备环境入口。')
    if not any((snapshot / name).is_file() for name in ('model.safetensors', 'pytorch_model.bin')):
        raise PackageError('本地模型权重缺失，请先运行准备环境入口。')
    approved = approved_books(settings.books_dir)
    documents, chunks = read_library(settings.db_path, approved)
    revision = git_output('rev-parse', 'HEAD').decode('ascii').strip()
    files = source_files(revision)
    if not (ROOT / 'frontend/dist/index.html').is_file():
        raise PackageError('未找到已构建网页，请先准备环境。')
    output.mkdir(parents=True, exist_ok=False)
    incomplete = output / 'INCOMPLETE-DO-NOT-SUBMIT.txt'
    incomplete.write_text('构建尚未完成，不能用于评审。请查看构建输出。\n', encoding='utf-8')

    print('Copying source and the prebuilt webpage...', flush=True)
    for name in files:
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(git_output('show', revision + ':' + name))
    copy_tree(ROOT / 'frontend/dist', output / 'frontend/dist')
    if not args.without_python:
        print('Copying the independent Python runtime and installed dependencies...', flush=True)
        copy_python(output / 'runtime/python')
    print('Copying approved public books and the cached local model...', flush=True)
    books_target = output / 'backend/app/data/books'
    books_target.mkdir(parents=True, exist_ok=True)
    for filename, book in approved.items():
        shutil.copy2(settings.books_dir / filename, books_target / filename)
        # Generate public metadata from the approved manifest, not arbitrary sidecars.
        write_json((books_target / filename).with_suffix('.json'),
                   {name: book.get(name, '') for name in ('title', 'author', 'source', 'license', 'source_url')})
    repo_name = 'models--' + EXPECTED_MODEL.replace('/', '--')
    copy_tree(settings.model_cache_dir / repo_name, output / 'backend/app/data/models' / repo_name,
              ignored={'.locks'})
    print('Creating a clean textbook-only database and a portable vector seed...', flush=True)
    export_database(output / 'backend/app/data/medrag.db', documents, chunks, approved)
    seed_manifest = copy_vector_seed(vector_storage_path(settings.chroma_dir), output / 'runtime/vector-seed', documents, chunks)
    write_json(output / 'runtime/vector-seed-manifest.json', seed_manifest)
    # Read only the committed empty template; the user's .env is never opened/copied.
    template = (ROOT / 'backend/.env.example').read_text(encoding='utf-8')
    if any(line.startswith('LLM_API_KEY=') and line.removeprefix('LLM_API_KEY=').strip() for line in template.splitlines()):
        raise PackageError('示例环境配置中的密钥字段非空，已停止。')
    (output / 'backend/.env').write_text(template, encoding='utf-8')
    note = '''# MedRAG 评审运行包

这是准备好网页、公开教材、模型和教材索引的 Windows 运行目录，不是旧版纯源码 ZIP。

1. 完整解压/复制此文件夹到可写目录。不要只复制 CMD 文件或留在 ZIP 预览中运行。
2. 双击 start-medrag.cmd，等待浏览器自动打开。不要先直接访问 127.0.0.1。
3. 再次双击启动入口会复用本目录服务；stop-medrag.cmd 可停止它。
4. check-medrag.cmd 可检查环境。失败信息保存在 .local/launcher.log 和 .local/server-error.log。
5. 讲解问答需要在页面填写评审方自己的 DeepSeek API Key 和可用网络；本包密钥为空。离线时可打开页面、教材和原文摘录。

包中仅含许可清单列出的公开教材及其索引，保留原 PDF 中的来源、译者、版权和许可例外。不同教材许可见 eval 清单与网页资料信息。
新数据库的会话、消息和反馈均为空；没有读取或复制实际用户历史。原环境的实际 .env、API 密钥、日志和 pyvenv.cfg 未打包。
独立运行时来自本机 Windows / CPython 3.12 环境。搬目录验证可证明本机不再依赖原项目路径，不能替代在另一台 Windows 电脑上的最终试跑。
本目录不是 Git 历史或官方提交回执。源代码历史、技术文档、视频等材料仍按比赛提交清单准备。
'''
    if args.without_python:
        note += '\n注意：此目录使用 --without-python 构建，仅供打包检查，没有独立解释器，不能作为离线独立运行包提交。\n'
    (output / '先读我-评审运行说明.md').write_text(note, encoding='utf-8')
    inventory = [path for path in output.rglob('*') if path.is_file() and path != incomplete]
    manifest = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_commit': revision,
        'sources_from_committed_revision': True,
        'source_worktree_changes': bool(git_output('status', '--porcelain', '--untracked-files=no').strip()),
        'python_included': not args.without_python,
        'python_version': sys.version.split()[0],
        'documents': [{'id': row['id'], 'file_name': row['file_name'], 'file_hash': row['file_hash'],
                       'num_pages': row['num_pages'], 'num_chunks': row['num_chunks']}
                      for row in documents],
        'num_documents': len(documents), 'num_chunks': len(chunks),
        'empty_personal_tables': ['conversation', 'message', 'feedback'],
        'actual_user_env_copied': False, 'personal_database_copied': False,
        'file_count_excluding_manifest': len(inventory),
        'bytes_excluding_manifest': sum(path.stat().st_size for path in inventory),
        'frontend_sha256': {path.relative_to(output / 'frontend/dist').as_posix(): digest(path)
                            for path in sorted((output / 'frontend/dist').rglob('*')) if path.is_file()},
    }
    write_json(output / 'runtime/review-package-manifest.json', manifest)
    incomplete.unlink()  # Only this build's single known marker file.
    print(json.dumps({'output': str(output), 'documents': len(documents), 'chunks': len(chunks),
                      'python_included': not args.without_python,
                      'size_gib': round(manifest['bytes_excluding_manifest'] / 1024**3, 2)}, ensure_ascii=True), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except PackageError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        # Third-party exception strings can contain paths or environment values.
        print('运行包构建失败；未输出可能包含配置值的异常详情。已有输出保留为未完成状态，请排查后使用新目录重试。', file=sys.stderr)
        raise SystemExit(1)
