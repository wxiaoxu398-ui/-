"""Read-only, offline checks for a prepared MedRAG environment.

No database, conversation, API credential or HTTP endpoint is inspected. Heavy
ML libraries are checked through import specifications and package metadata;
this script never constructs a Torch model or downloads missing files.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from html.parser import HTMLParser
import importlib
from importlib import metadata, util
import io
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
CORE_DEPENDENCIES = (
    ("fastapi", "fastapi"), ("uvicorn", "uvicorn"),
    ("sse-starlette", "sse_starlette"), ("sqlmodel", "sqlmodel"),
    ("pydantic-settings", "pydantic_settings"), ("python-dotenv", "dotenv"),
    ("openai", "openai"), ("rank-bm25", "rank_bm25"), ("jieba", "jieba"),
    ("PyMuPDF", "pymupdf"), ("python-multipart", "python_multipart"),
    ("httpx", "httpx"),
)
VECTOR_DEPENDENCIES = (
    ("chromadb", "chromadb"), ("sentence-transformers", "sentence_transformers"),
    ("torch", "torch"), ("transformers", "transformers"),
    ("huggingface-hub", "huggingface_hub"), ("tokenizers", "tokenizers"),
    ("numpy", "numpy"),
)


def result(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "ok": bool(ok), "message": message}


def check_python() -> dict:
    version = sys.version_info
    supported = (3, 11) <= version[:2] <= (3, 13)
    current = ".".join(str(number) for number in version[:3])
    return result("python", supported, f"Python {current}；" + (
        "版本受支持。" if supported else "需要 Python 3.11–3.13，请运行 prepare-medrag.cmd 准备环境。"
    ))


def check_dependency(distribution: str, module: str, *, import_module: bool) -> dict:
    try:
        version = metadata.version(distribution)
        if util.find_spec(module) is None:
            raise ImportError
        if import_module:
            importlib.import_module(module)
    except Exception:
        # Import/validation errors can contain local environment values. Do not
        # echo an exception or a traceback into a distributable check report.
        return result("dependency:" + distribution, False,
                      f"{distribution} 缺失或无法使用，请运行 prepare-medrag.cmd 修复依赖。")
    return result("dependency:" + distribution, True, f"{distribution} {version} 已安装。")


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("src", "href") and value and value.startswith("/assets/"):
                self.paths.add(unquote(urlsplit(value).path))


def check_frontend(root: Path) -> dict:
    dist = root / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return result("frontend", False,
                      "缺少已构建的网页 frontend/dist/index.html；请运行 prepare-medrag.cmd，或重新解压完整评审运行包。")
    try:
        parser = AssetReferences()
        parser.feed(index.read_text(encoding="utf-8-sig"))
        if not parser.paths:
            raise ValueError
        asset_root = (dist / "assets").resolve()
        for reference in parser.paths:
            asset = (dist / reference.lstrip("/")).resolve()
            if not asset.is_relative_to(asset_root) or not asset.is_file() or not asset.stat().st_size:
                raise ValueError
    except (OSError, UnicodeError, ValueError):
        return result("frontend", False,
                      "网页引用的静态资源缺失、为空或路径异常；请重新准备网页，或重新解压完整评审运行包。")
    return result("frontend", True, f"网页和 {len(parser.paths)} 个直接引用的静态资源完整，无需 Node.js。")


def model_files_complete(path: Path) -> bool:
    """Check the files used by the bundled BGE model, without loading weights."""
    def present(relative: str) -> bool:
        file = path / relative
        return file.is_file() and file.stat().st_size > 0

    try:
        if not (present("config.json") and present("tokenizer.json") and
                (present("model.safetensors") or present("pytorch_model.bin"))):
            return False
        json.loads((path / "config.json").read_text(encoding="utf-8"))
        modules = path / "modules.json"
        if modules.is_file():
            for entry in json.loads(modules.read_text(encoding="utf-8")):
                child = entry.get("path", "")
                if child:
                    resolved = (path / child).resolve()
                    if not resolved.is_relative_to(path.resolve()):
                        return False
                    # Normalize has no parameters and is legitimately saved
                    # without a directory. Pooling does require its config.
                    if entry.get("type", "").endswith(".Pooling") and not present(child + "/config.json"):
                        return False
        return True
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        return False


def check_model(model_name: str, cache_dir: Path, *, root: Path) -> dict:
    configured_path = Path(model_name)
    if not cache_dir.is_absolute():
        cache_dir = root / "backend" / cache_dir
    # The launcher starts the backend in root/backend. Match that resolution
    # rather than accepting an unrelated model directory elsewhere in the pack.
    candidates = [configured_path] if configured_path.is_absolute() else [root / "backend" / model_name]
    for candidate in candidates:
        if candidate.is_dir() and model_files_complete(candidate):
            return result("model", True, "本地模型目录的配置、分词器与权重完整，可供离线加载。")
    try:
        from huggingface_hub import snapshot_download
        snapshot = Path(snapshot_download(repo_id=model_name, cache_dir=str(cache_dir), local_files_only=True))
    except Exception:
        return result("model", False,
                      "未找到完整的本地 BGE 模型缓存；检查不会联网下载。请联网运行 prepare-medrag.cmd，或使用包含模型的评审运行包。")
    if not model_files_complete(snapshot):
        return result("model", False,
                      "本地 BGE 模型缓存不完整，缺少配置、分词器或权重；请重新准备模型或解压完整评审运行包。")
    return result("model", True, "本地模型缓存的配置、分词器与权重完整；本次检查未联网。")


def retrieval_configuration(root: Path) -> tuple[str, str, Path]:
    backend = str(root / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app.config import settings
    # The application owns configuration loading. Inspect only these three
    # retrieval values; never serialize settings or inspect an .env file.
    return settings.embedding_backend, settings.embedding_model, Path(settings.model_cache_dir)


def run_checks(root: Path = ROOT) -> dict:
    checks = [check_python()]
    checks.extend(check_dependency(dist, module, import_module=True) for dist, module in CORE_DEPENDENCIES)
    checks.append(check_frontend(root))
    try:
        backend, model_name, cache_dir = retrieval_configuration(root)
        if backend not in ("bge", "lexical"):
            raise ValueError
        checks.append(result("retrieval_settings", True,
                             "本地检索配置可读取；不检查或展示 API 密钥。"))
    except Exception:
        checks.append(result("retrieval_settings", False,
                             "无法读取有效的检索配置，请运行 prepare-medrag.cmd 检查环境。"))
    else:
        if backend == "bge":
            checks.extend(check_dependency(dist, module, import_module=False) for dist, module in VECTOR_DEPENDENCIES)
            checks.append(check_model(model_name, cache_dir, root=root))
        else:
            checks.append(result("model", True, "当前明确配置为 BM25 轻量检索，无需 BGE 模型。"))
    return {"ready": all(check["ok"] for check in checks), "checks": checks}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="离线、只读检查 MedRAG 启动环境，不检查 API 密钥或私人会话。")
    parser.add_argument("--json", action="store_true", help="输出 ASCII 转义的 JSON，供启动器判断结果")
    args = parser.parse_args(argv)
    # Optional libraries occasionally print warnings at import time. Keep the
    # machine-readable report clean and avoid displaying unreviewed errors.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            report = run_checks()
        except Exception:
            report = {"ready": False, "checks": [result("environment", False,
                      "环境检查未完成，请运行 prepare-medrag.cmd，或查看 docs/07-启动修复与评审检查.md。")]}
    if args.json:
        print(json.dumps(report, ensure_ascii=True))
    else:
        print("MedRAG 启动环境检查（只读、离线）\n")
        for check in report["checks"]:
            print(("[通过] " if check["ok"] else "[未通过] ") + check["message"])
        print("\n" + ("启动所需文件检查通过。可双击 start-medrag.cmd。" if report["ready"] else
                       "环境尚未就绪。请按未通过项目处理；详细说明见 docs/07-启动修复与评审检查.md。"))
        print("本检查不等于已验证实际重启、模型运行或 DeepSeek 连通性。")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
