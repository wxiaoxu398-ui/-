r"""Assemble the reviewed MedRAG submission inputs without publishing anything.

Run after committing the text inputs and reviewing the rendered PDF, for example:
    .venv\Scripts\python.exe submission/build_package.py --date 20261111 --pdf-pages 11

Pass --repository-verified-commit only after independently reading that full SHA
from the intended remote. Local origin configuration is not upload evidence.
Add --repository-public-read-verified only when that external read was anonymous.

The output must not already exist. No existing directory is deleted or replaced.
Only explicitly listed materials are copied; the actual .env and user database
are never read. A fresh bare clone used to verify the bundle remains in .local.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_URL = "https://www.boxuegu.com/matchTrack/detail/?id=10037"
REPOSITORY_URL = "https://github.com/wxiaoxu398-ui/-"
PACKAGE_PREFIX = "MedRAG-Submission-"
SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{24,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{50,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(rb"https?://[^\s/:]+:[^\s/@]+@", re.I),
)
SECRET_KEYS = {
    "api_key", "llm_api_key", "authorization", "access_token", "refresh_token",
    "password", "client_secret", "private_key", "secret_key",
}

# The two archives are built from HEAD, rather than a recursive workspace copy.
REQUIRED_COPIES = {
    "output/pdf/MedRAG-技术文档.pdf": "02-技术文档/MedRAG-技术文档.pdf",
    "submission/technical-report.md": "02-技术文档/technical-report.md",
    "submission/team-info.json": "02-技术文档/team-info.json",
    "submission/team-info.example.json": "02-技术文档/team-info.example.json",
    "submission/01-提交清单.md": "02-技术文档/01-提交清单.md",
    "submission/02-演示视频脚本.md": "03-演示视频/演示视频脚本.md",
    "submission/03-复现与提交指南.md": "01-源码/复现与提交指南.md",
    "submission/04-验证记录.md": "04-验证与资料/验证记录.md",
    "docs/06-参赛完善与演示.md": "04-验证与资料/06-参赛完善与演示.md",
    "eval/chinese-books.json": "04-验证与资料/eval/chinese-books.json",
    "eval/open-books.json": "04-验证与资料/eval/open-books.json",
    "eval/chinese-book-questions.json": "04-验证与资料/eval/chinese-book-questions.json",
    "eval/open-book-questions.json": "04-验证与资料/eval/open-book-questions.json",
    "eval/questions.json": "04-验证与资料/eval/questions.json",
    "eval/competition-benchmark.json": "04-验证与资料/eval/competition-benchmark.json",
    "eval/chinese-retrieval-benchmark.json": "04-验证与资料/eval/chinese-retrieval-benchmark.json",
    "eval/retrieval-benchmark.json": "04-验证与资料/eval/retrieval-benchmark.json",
}
SCREENSHOTS = {
    ".local/medrag-competition-project.png": "04-验证与资料/截图/medrag-competition-project.png",
    ".local/medrag-competition-study.png": "04-验证与资料/截图/medrag-competition-study.png",
    ".local/medrag-competition-mobile.png": "04-验证与资料/截图/medrag-competition-mobile.png",
}
RAW_EVALUATION = ".local/acceptance/evaluation-zh-live.json"
LOCAL_ONLY_INPUTS = {"submission/team-info.json"}


class PackageError(RuntimeError):
    """A safe-to-display error; never embed unredacted Git stderr or key values."""


def full_commit_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise argparse.ArgumentTypeError("必须使用完整的 40 位十六进制提交 SHA。")
    return value.casefold()


def inside_repo(path: Path, *, allow_root: bool = False) -> Path:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(ROOT)
    except ValueError as exc:
        raise PackageError("路径解析后位于项目之外，已停止。") from exc
    if not allow_root and not relative.parts:
        raise PackageError("必须使用项目内的子目录，不能将项目根目录作为输出目录。")
    return resolved


def git(args: list[str], *, cwd: Path = ROOT, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args], cwd=cwd,
        input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        # Some Git errors can include a configured remote URL with credentials.
        raise PackageError("Git 操作失败：" + args[0] + "；未输出可能包含敏感信息的错误详情。")
    return result.stdout


def contains_secret(data: bytes) -> bool:
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        return True
    # Detect the same markers in UTF-16 text without printing any matched value.
    if b"\x00" in data:
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                decoded = data.decode(encoding).encode("utf-8")
            except (UnicodeError, LookupError):
                continue
            if any(pattern.search(decoded) for pattern in SECRET_PATTERNS):
                return True
    return False


def validate_json_privacy(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in SECRET_KEYS and item not in (None, "", [], {}):
                raise PackageError("JSON 材料包含非空凭据字段，已停止；未输出字段值。")
            validate_json_privacy(item)
    elif isinstance(value, list):
        for item in value:
            validate_json_privacy(item)
    elif isinstance(value, str):
        if contains_secret(value.encode("utf-8")):
            raise PackageError("JSON 材料包含疑似凭据，已停止；未输出匹配内容。")
        if value.startswith(("https://", "http://")):
            parsed = urlsplit(value)
            if parsed.username or parsed.password:
                raise PackageError("材料链接包含认证信息，已停止。")


def forbidden_source_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = path.parts
    if not parts or path.is_absolute() or ".." in parts or ":" in parts[0]:
        return True
    lowered = tuple(part.casefold() for part in parts)
    filename = lowered[-1]
    if "/".join(lowered) in LOCAL_ONLY_INPUTS:
        return True
    if any(part in {".git", ".local", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
           for part in lowered):
        return True
    if any(part == ".env" or (part.startswith(".env.") and part != ".env.example") for part in lowered):
        return True
    if filename in {"id_rsa", "id_ed25519", "credentials.json"} or filename.endswith((".pem", ".key")):
        return True
    if re.search(r"\.(?:db|sqlite3?)(?:-wal|-shm)?$", filename):
        return True
    if filename.endswith((".pdf", ".pyc", ".log")):
        return True
    if lowered[:2] == ("frontend", "dist"):
        return True
    if lowered[:3] == ("backend", "app", "data"):
        if len(lowered) >= 4 and lowered[3] in {"chroma", "models"}:
            return True
        if len(lowered) >= 4 and lowered[3] == "books" and filename != ".gitkeep":
            return True
    return False


def audit_head_history() -> dict[str, Any]:
    """Inspect every reachable tree/path and blob, without reading workspace .env."""
    commits = git(["rev-list", "HEAD"]).decode("ascii").splitlines()
    blobs: set[str] = set()
    paths: set[bytes] = set()
    for commit in commits:
        entries = git(["ls-tree", "-rz", "--full-tree", commit]).split(b"\x00")
        for entry in entries:
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            name = raw_path.decode("utf-8", errors="strict")
            if forbidden_source_path(name):
                raise PackageError("当前 HEAD 历史含禁止发布的运行数据或敏感路径，已停止。")
            if mode == b"120000" or kind != b"blob":
                raise PackageError("源码历史含符号链接或子模块，需要单独审阅后才能打包。")
            paths.add(raw_path)
            blobs.add(oid.decode("ascii"))
    # --batch avoids launching a process for every historical copy of a file.
    ordered = sorted(blobs)
    batch = git(["cat-file", "--batch"], input_data=("\n".join(ordered) + "\n").encode("ascii"))
    offset = 0
    for expected in ordered:
        end = batch.find(b"\n", offset)
        if end < 0:
            raise PackageError("Git 历史对象读取不完整。")
        fields = batch[offset:end].split()
        if len(fields) != 3 or fields[0].decode("ascii") != expected or fields[1] != b"blob":
            raise PackageError("Git 历史对象格式不正确。")
        size = int(fields[2])
        offset = end + 1
        content = batch[offset:offset + size]
        if len(content) != size or contains_secret(content):
            raise PackageError("Git 历史包含疑似凭据或对象不完整，已停止；不会重写历史。")
        offset += size + 1
    return {"commits_checked": len(commits), "unique_paths_checked": len(paths),
            "unique_blobs_checked": len(blobs), "result": "passed_pattern_and_path_checks"}


def read_json(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError("JSON 输入无法读取或格式不正确。") from exc
    validate_json_privacy(value)
    return value


def public_evaluation(path: Path) -> dict[str, Any]:
    """Allow only fixed public test questions and known open-book fingerprints."""
    value = read_json(path)
    chinese = read_json(ROOT / "eval/chinese-book-questions.json")
    english = read_json(ROOT / "eval/open-book-questions.json")
    allowed_questions = {row["question"] for row in chinese["questions"]}
    allowed_questions.update(english["refusal_questions"])
    books = {}
    for filename in ("chinese-books.json", "open-books.json"):
        for book in read_json(ROOT / "eval" / filename)["books"]:
            books[book["sha256"]] = book
    if not isinstance(value, dict) or value.get("dataset") != "zh" or not isinstance(value.get("items"), list):
        raise PackageError("原始评估结果不是预期的公开中文题集，未复制。")
    if set(value) - {"created_at", "dataset", "context", "note", "items"}:
        raise PackageError("原始评估含未预期的额外字段，需要先人工审阅，未复制。")
    ids = []
    for item in value["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            raise PackageError("原始评估题目结构不正确。")
        if item.get("question") not in allowed_questions:
            raise PackageError("原始评估含题集外问题，未复制，以免混入私人会话。")
        ids.append(item.get("id"))
        for source in item.get("sources", []):
            if not isinstance(source, dict) or not isinstance(source.get("file_hash"), str):
                raise PackageError("原始评估来源结构不正确。")
            book = books.get(source.get("file_hash"))
            if not book or source.get("title") != book["title"]:
                raise PackageError("原始评估含未批准版本或标题的来源，未复制。")
    if len(ids) != len(set(ids)) or len(ids) > len(allowed_questions):
        raise PackageError("原始评估题目数量或编号异常。")
    return value


def provided(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() not in {
        "", "tbd", "todo", "待填写", "待团队填写", "未提供", "待确认",
    }


def team_status(team: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(team, dict):
        raise PackageError("团队资料 JSON 必须是一个对象。")
    for key in ("members", "instructors", "advisors"):
        if key in team and not isinstance(team[key], list):
            raise PackageError("团队资料中的成员和指导老师字段必须使用数组。")
    missing = []
    for title, keys in (("队名", ("team_name",)), ("学校", ("school",)),
                        ("参赛组别", ("competition_group", "group", "division"))):
        if not any(provided(team.get(key)) for key in keys):
            missing.append(title)
    members = [member for member in team.get("members", [])
               if isinstance(member, dict) and provided(member.get("name"))]
    if not 1 <= len(members) <= 3:
        missing.append("1-3 位真实成员")
    if members and any(not any(provided(member.get(key)) for key in ("division", "role", "responsibility"))
                       for member in members):
        missing.append("成员已确认分工")
    instructors = [item for item in team.get("instructors", team.get("advisors", []))
                   if isinstance(item, dict) and provided(item.get("name"))]
    if len(instructors) > 2:
        missing.append("指导老师数量应不超过 2 位")
    repository = team.get("repository", {})
    url = repository.get("url", "") if isinstance(repository, dict) else ""
    url = url or team.get("repository_url", "")
    parsed = urlsplit(url) if isinstance(url, str) else urlsplit("")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    repository_url_is_specific = (parsed.scheme in {"https", "http"}
                                  and parsed.hostname in {"github.com", "www.github.com", "gitee.com", "www.gitee.com"}
                                  and len(parts) == 2 and parts[0] not in {"new", "login", "settings", "features"})
    review = team.get("team_review", {})
    reviewed = isinstance(review, dict) and provided(review.get("reviewed_by")) and provided(review.get("reviewed_at"))
    return {"information_complete": not missing, "missing_fields": missing,
            "specific_repository_url_provided": repository_url_is_specific,
            "reviewer_access_verified_by_package": False, "team_final_review_recorded": reviewed,
            "division_proposals_are_not_confirmed_members": True}


def normalize_repository_url(value: str) -> str | None:
    """Map common GitHub HTTPS/SSH forms to a safe public URL, never credentials."""
    value = value.strip()
    scp = re.fullmatch(r"(?:[^@/\s]+@)?github\.com:([^\s?#]+)", value, flags=re.I)
    if scp:
        path = scp.group(1)
    else:
        try:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError:
            return None
        scheme = parsed.scheme.casefold()
        allowed_ports = {"https": {None, 443}, "http": {None, 80}, "ssh": {None, 22}, "git": {None, 9418}}
        if (host not in {"github.com", "www.github.com"} or scheme not in allowed_ports
                or port not in allowed_ports[scheme] or parsed.query or parsed.fragment):
            return None
        path = parsed.path
    path = path.strip("/")
    if path.casefold().endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        return None
    if any(part in {".", ".."} for part in parts):
        return None
    return "https://github.com/" + "/".join(part.casefold() for part in parts)


def repository_status(package_commit: str, verified_commit: str | None, public_read_verified: bool = False) -> dict[str, Any]:
    """Validate a caller's remote-read record; this function never contacts/pushes a remote."""
    if public_read_verified and not verified_commit:
        raise PackageError("--repository-public-read-verified 必须与 --repository-verified-commit 一同使用。")
    try:
        origin_url = git(["config", "--get", "remote.origin.url"]).decode("utf-8").strip()
    except PackageError:
        origin_url = ""
    origin_matches = normalize_repository_url(origin_url) == normalize_repository_url(REPOSITORY_URL)
    if verified_commit:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", verified_commit):
            raise PackageError("--repository-verified-commit 必须是完整的 40 位十六进制提交 SHA。")
        verified_commit = verified_commit.casefold()
        if not origin_matches:
            raise PackageError("origin 不匹配已提供的 MedRAG 仓库，不能记录该远端提交核验。")
        try:
            if git(["cat-file", "-t", verified_commit]).strip() != b"commit":
                raise PackageError("核验 SHA 不是提交对象。")
            git(["merge-base", "--is-ancestor", verified_commit, "HEAD"])
        except PackageError as exc:
            raise PackageError("远端核验 SHA 不是当前 HEAD 的有效祖先提交，已停止。") from exc
    same_head = bool(verified_commit and verified_commit == package_commit.casefold())
    return {
        "provided_repository_url": REPOSITORY_URL,
        "origin_matches_provided_repository": origin_matches,
        "remote_read_verified_commit": verified_commit,
        "verified_repository_url": REPOSITORY_URL if verified_commit else None,
        "package_head_confirmed_on_remote": same_head,
        "external_anonymous_read_verified": bool(public_read_verified),
        "package_head_public_read_confirmed": bool(same_head and public_read_verified),
        "upload_status": "package_head_confirmed" if same_head else "verified_to_ancestor_only" if verified_commit else "not_verified",
        "verification_basis": "caller_supplied_remote_read_sha_checked_against_origin_and_local_ancestry" if verified_commit else "no_remote_read_record_provided",
        "remote_read_performed_by_packager": False,
        "reviewer_access_verified_by_packager": False,
        "anonymous_read_performed_by_packager": False,
        "remote_configuration_alone_is_upload_evidence": False,
    }


def repository_followup(status: dict[str, Any]) -> str:
    if status.get("package_head_public_read_confirmed"):
        return "提交时复核公开仓库入口、分支与版本，使官网填报与已核验的作品提交一致"
    if status["package_head_confirmed_on_remote"]:
        return "核验赛事评审访问该仓库的权限，并明确评审使用的分支或提交；已核验上传不等于权限已确认"
    if status["remote_read_verified_commit"]:
        if status.get("external_anonymous_read_verified"):
            return "匿名公开读取仅核验至历史提交，本包 HEAD 尚未确认上传；核验当前包提交后，再于提交时复核仓库入口与版本"
        return "仓库已核验至历史提交，但本包 HEAD 尚未确认上传；补传需要的提交后重新读取远端 SHA 核对，并确认评审访问权限"
    return "核验真实仓库的上传结果，记录远端读取的完整 SHA，并确认评审访问权限；不能只凭 origin 配置认定上传成功"


def validate_team_example(example: Any) -> None:
    """The public source template must not retain the local team-page reference."""
    if not isinstance(example, dict):
        raise PackageError("团队示例 JSON 必须是一个对象。")
    reference = example.get("team_reference", {})
    if not isinstance(reference, dict):
        raise PackageError("团队示例的 team_reference 字段格式不正确。")
    if reference.get("team_page_url") not in (None, "") or reference.get("team_id") not in (None, ""):
        raise PackageError("公开团队示例仍含用户特定报名页参考信息，已停止；未输出其值。")


def write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)


def verify_source_zip(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise PackageError("源码 ZIP 校验失败。")
        count = 0
        for item in archive.infolist():
            if not item.filename.startswith("MedRAG/"):
                raise PackageError("源码 ZIP 缺少约定的目录前缀。")
            name = item.filename[len("MedRAG/"):]
            if not name or item.is_dir():
                continue
            if forbidden_source_path(name) or contains_secret(archive.read(item)):
                raise PackageError("源码 ZIP 含禁止路径或疑似凭据，已停止。")
            count += 1
        return count


def verify_history_clone(bundle: Path, commit: str, expected_count: int) -> None:
    # This is the only temporary directory. It is new, inside .local, and is
    # intentionally retained: the packager never recursively deletes a directory.
    local = inside_repo(ROOT / ".local")
    local.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="submission-history-check-", dir=local)).resolve()
    if temporary.parent != local:
        raise PackageError("历史校验目录解析异常。")
    clone = temporary / "repo.git"
    git(["clone", "--bare", "--quiet", str(bundle), str(clone)])
    restored = git(["rev-parse", "HEAD"], cwd=clone).decode("ascii").strip()
    count = int(git(["rev-list", "--count", "HEAD"], cwd=clone).strip())
    if restored != commit or count != expected_count:
        raise PackageError("Git bundle 克隆后提交或历史数量不一致。")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def index_markdown(commit: str, implementation: str, pdf_pages: int, status: dict[str, Any], repository: dict[str, Any]) -> str:
    team_line = "已填写，仍需团队确认。" if status["information_complete"] else "待填写：" + "、".join(status["missing_fields"]) + "。"
    verified = repository["remote_read_verified_commit"]
    if repository.get("package_head_public_read_confirmed"):
        repository_line = f"外部匿名读取已核验公开仓库提交 `{verified}`，与本包 HEAD 一致；可确认该归档提交已公开可读。仓库读取项不再列为未完成，提交时复核入口与版本即可；本记录不等于官网材料已经提交。"
    elif repository["package_head_confirmed_on_remote"]:
        repository_line = f"远端读取记录已核验提交 `{verified}`，与本包 HEAD 一致；可确认该归档提交已在上述仓库读取到，尚不代表评审权限或官网提交已确认。"
    elif verified:
        read_kind = "外部匿名公开读取" if repository.get("external_anonymous_read_verified") else "远端读取记录"
        repository_line = f"{read_kind}仅核验至历史提交 `{verified}`；本包 HEAD 为 `{commit}`，二者不同，不能声称本包 HEAD 已上传或已通过当前包的公开读取核验。"
    else:
        repository_line = "已提供具体仓库地址，但未提供远端读取 SHA 核验记录，不能确认本包 HEAD 已上传。origin 配置不是上传或评审访问的证明。"
    return f"""# MedRAG 参赛材料索引

本包整理了本地作品、技术 PDF、源码与开发历史、演示脚本及验证记录；**本包尚不能证明已完成官网提交**。

## 文件入口

- [干净源码 ZIP](01-源码/MedRAG-source.zip)：按归档提交生成，不含实际密钥、教材 PDF、数据库、索引或依赖缓存。
- [开发历史 bundle](01-源码/MedRAG-history.bundle)：当前 HEAD 可达历史，已经过 bundle verify 和独立 bare clone 校验；不等同于 GitHub / Gitee 托管。
- [复现与提交指南](01-源码/复现与提交指南.md)：安装、恢复历史、运行和提交说明。
- [技术文档 PDF](02-技术文档/MedRAG-技术文档.pdf)：{pdf_pages} 页，已生成，内容仍需团队最终确认。
- [可编辑技术正文](02-技术文档/technical-report.md)：技术 PDF 的可编辑正文。
- [提交清单](02-技术文档/01-提交清单.md)、[团队信息](02-技术文档/team-info.json)：逐项要求和待补信息。
- [演示视频脚本](03-演示视频/演示视频脚本.md)：录制分镜；**本包没有 MP4 成品**。
- `04-验证与资料/`：测试记录、资料许可清单、评测题集、摘要和可用的界面截图。
- `PACKAGE-MANIFEST.json`、`SHA256SUMS.txt`：版本、材料状态和文件完整性信息。

源码 ZIP 与 Git bundle 只应包含清空用户特定报名页参考信息的 `submission/team-info.example.json`，不包含本机实际 `submission/team-info.json`。实际团队文件单独放在本包 `02-技术文档/team-info.json`，可能保留团队提供的报名页参考链接，仅供本机交接及团队核对；不要未经审阅就把整个总 ZIP 上传到公开仓库。正式署名应同步到 README 和 PDF，建议分工不能当作成员已经确认。

## 仓库与上传核验

实际仓库：[{REPOSITORY_URL}]({REPOSITORY_URL})。

{repository_line}

本记录由调用者提供实际远端读取结果，匿名公开读取仅在显式传入已核验标志时记录。打包脚本只校验 origin 目标与本地提交关系，不联网读取或推送，不修改默认分支、仓库可见性或评审权限。

## 仍需完成

1. 团队资料：{team_line}实名建议分工与署名仍需团队终审；指导老师如有则补充。
2. {repository_followup(repository)}。
3. 录制并核验 5-8 分钟、至少 1080p、普通话讲解的 MP4。
4. 同步团队资料、最终仓库链接与技术 PDF，并由团队终审。医学结论仍应逐条核对，原页命中不能代替语义准确率。
5. 参赛者本人核对轮次、报名/缴费、提交入口和截止时间，完成官方上传并保存回执。

公网演示是官网的可选加分项；本项目按原需求仅本地运行，`127.0.0.1` 不是公网演示地址。

## 版本与范围

- 归档提交：`{commit}`
- 应用实现基线：`{implementation}`
- 要求来源：{OFFICIAL_URL}
- 此打包脚本不执行报名、缴费、代码推送、权限变更或官网材料上传；代码上传状态仅按上述远端核验记录表述，报名账户已有状态无法由本地文件核实。
- 模式与测试范围以验证记录为准；没有把自主 Agent、多模态、MCP 或人工医学核验列作已完成。
"""


def create_package(args: argparse.Namespace) -> dict[str, Any]:
    try:
        datetime.strptime(args.date, "%Y%m%d")
    except ValueError as exc:
        raise PackageError("--date 必须是有效的 YYYYMMDD 日期。") from exc
    if len(args.date) != 8 or not 1 <= args.pdf_pages <= 30:
        raise PackageError("日期需为 8 位，--pdf-pages 必须介于 1 和 30。")
    if not args.output_root.is_absolute():
        raise PackageError("--output-root 必须是项目内部的绝对路径。")
    output_root = inside_repo(args.output_root)
    target = inside_repo(output_root / (PACKAGE_PREFIX + args.date))
    total_zip = inside_repo(output_root / (PACKAGE_PREFIX + args.date + ".zip"))
    if any(path.exists() or path.is_symlink() for path in (target, total_zip)):
        raise PackageError("目标材料目录或总 ZIP 已存在；不会覆盖。请使用新的项目内 --output-root。")
    if git(["status", "--porcelain", "--untracked-files=no"]).strip():
        raise PackageError("存在未提交的已跟踪文件变更。请先提交待归档文本，再生成材料包。")
    commit = git(["rev-parse", "HEAD"]).decode("ascii").strip()
    repository = repository_status(commit, getattr(args, "repository_verified_commit", None),
                                   getattr(args, "repository_public_read_verified", False))
    implementation = git(["rev-parse", args.implementation_commit + "^{commit}"]).decode("ascii").strip()
    git(["merge-base", "--is-ancestor", implementation, "HEAD"])
    history_audit = audit_head_history()
    for filename in REQUIRED_COPIES:
        path = inside_repo(ROOT / filename)
        if not path.is_file():
            raise PackageError("缺少必需的已审核输入：" + filename)
        if path.suffix != ".pdf":
            if contains_secret(path.read_bytes()):
                raise PackageError("待复制文本含疑似凭据，已停止。")
            if filename not in LOCAL_ONLY_INPUTS:
                git(["cat-file", "-e", "HEAD:" + filename])
    pdf = ROOT / "output/pdf/MedRAG-技术文档.pdf"
    if not pdf.read_bytes().startswith(b"%PDF-"):
        raise PackageError("技术文档不是有效的 PDF 文件头。")
    team = read_json(ROOT / "submission/team-info.json")
    validate_team_example(read_json(ROOT / "submission/team-info.example.json"))
    status = team_status(team)
    remote_configured = bool(git(["remote", "-v"]).strip())  # Never output remote URL values.
    raw_path = inside_repo(ROOT / RAW_EVALUATION)
    raw = public_evaluation(raw_path) if raw_path.is_file() else None
    screenshots = {name: destination for name, destination in SCREENSHOTS.items()
                   if inside_repo(ROOT / name).is_file()}

    # All preflight checks have passed. Existing outputs are never reused.
    output_root.mkdir(parents=True, exist_ok=True)
    if inside_repo(output_root) != output_root:
        raise PackageError("输出根目录解析发生变化。")
    target.mkdir(exist_ok=False)
    source_dir = target / "01-源码"
    source_dir.mkdir()
    source_zip = source_dir / "MedRAG-source.zip"
    write_new(source_zip, git(["archive", "--format=zip", "--prefix=MedRAG/", "HEAD"]))
    source_count = verify_source_zip(source_zip)
    bundle = source_dir / "MedRAG-history.bundle"
    git(["bundle", "create", str(bundle), "HEAD"])
    git(["bundle", "verify", str(bundle)])
    verify_history_clone(bundle, commit, history_audit["commits_checked"])
    for name, destination in {**REQUIRED_COPIES, **screenshots}.items():
        output = target / destination
        output.parent.mkdir(parents=True, exist_ok=True)
        with (ROOT / name).open("rb") as origin, output.open("xb") as copy:
            shutil.copyfileobj(origin, copy)
    if raw is not None:
        write_new(target / "04-验证与资料/真实中文题集结果.json",
                  (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    write_new(target / "00-材料索引.md", index_markdown(commit, implementation, args.pdf_pages, status, repository).encode("utf-8"))
    write_new(target / "03-演示视频/尚缺视频成品.txt",
              "本目录只有录制脚本，尚无 MP4。需团队录制 5-8 分钟、至少 1080p、普通话讲解的视频。\n".encode("utf-8"))
    payload = [{"path": path.relative_to(target).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
               for path in sorted(target.rglob("*")) if path.is_file()]
    manifest = {
        "schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "material_date": args.date, "official_requirements_url": OFFICIAL_URL,
        "source_commit": commit, "application_implementation_commit": implementation,
        "source_archive_files": source_count, "git_history_audit": history_audit,
        "bundle_verify": "passed", "bundle_bare_clone": "passed_same_HEAD_and_commit_count",
        "technical_pdf_pages": args.pdf_pages, "pdf_pages_source": "provided_after_external_render_review",
        "technical_pdf_status": "generated_requires_team_final_review",
        "team": status, "remote_configured": remote_configured,
        "repository": repository,
        "team_info_handling": {
            "source_template": "submission/team-info.example.json",
            "source_template_team_reference": "cleared_and_checked",
            "local_actual_file": "02-技术文档/team-info.json",
            "actual_file_in_source_or_history": False,
            "distribution_note": "实际团队 JSON 为本机交接材料，公开上传前需审阅其中的用户特定报名页参考信息。",
        },
        "reviewer_repository_access": (
            "externally_verified_public_anonymous_read_at_package_head" if repository["package_head_public_read_confirmed"]
            else "external_anonymous_read_verified_to_ancestor_only" if repository["external_anonymous_read_verified"]
            else "not_verified_by_this_packager"
        ),
        "demo_mp4": "missing", "online_demo": "optional_not_publicly_deployed",
        "platform_registration_payment_submission": "unknown_not_performed_by_this_package",
        "raw_public_evaluation_included": raw is not None,
        "raw_evaluation_privacy_checks": "fixed_public_questions_known_source_fingerprints_and_no_credential_fields" if raw else "not_included",
        "screenshots_included": len(screenshots),
        "semantic_citation_accuracy": None,
        "requires_user_action": [
            "团队终审实名建议分工与署名，指导老师如有则补充",
            repository_followup(repository),
            "录制和核验符合规格的 MP4，脚本与截图不能替代视频",
            "团队终审并同步技术 PDF、README、视频与最终仓库链接",
            "本人核对实际轮次与官方通知，完成上传并保留回执",
        ],
        "recommended_quality_review": ["人工逐条核验医学结论与引用", "在另一目录或电脑复现安装与完整学习流程"],
        "checksum_scope": "files lists payload only; SHA256SUMS also hashes this manifest but excludes itself",
        "files": payload,
    }
    write_new(target / "PACKAGE-MANIFEST.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    checksums = [sha256(path) + "  " + path.relative_to(target).as_posix()
                 for path in sorted(target.rglob("*")) if path.is_file()]
    write_new(target / "SHA256SUMS.txt", ("\n".join(checksums) + "\n").encode("utf-8"))
    commit_time = int(git(["show", "-s", "--format=%ct", "HEAD"]).strip())
    zip_date = datetime.fromtimestamp(commit_time, timezone.utc)
    date_time = (min(2107, max(1980, zip_date.year)), zip_date.month, zip_date.day,
                 zip_date.hour, zip_date.minute, zip_date.second)
    with zipfile.ZipFile(total_zip, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo((target.name + "/" + path.relative_to(target).as_posix()), date_time=date_time)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes(), compresslevel=9)
    with zipfile.ZipFile(total_zip) as archive:
        if archive.testzip() is not None:
            raise PackageError("总 ZIP 完整性检查未通过，保留输出供检查，不覆盖或删除。")
    return {"package_directory": str(target), "archive": str(total_zip),
            "archive_sha256": sha256(total_zip), "source_commit": commit,
            "pdf_pages": args.pdf_pages, "payload_files": len(payload),
            "missing_team_fields": status["missing_fields"], "remote_configured": remote_configured,
            "repository": repository,
            "mp4_status": "missing", "official_submission": "not_performed_by_packager"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default="20261111", help="材料日期 YYYYMMDD；默认 20261111")
    parser.add_argument("--output-root", type=Path, default=ROOT / "deliverables", help="项目内部的绝对输出目录")
    parser.add_argument("--pdf-pages", type=int, required=True, help="已渲染核验的实际 PDF 页数，1-30")
    parser.add_argument("--implementation-commit", default="587391d", help="应用实现基线；默认 587391d")
    parser.add_argument("--repository-verified-commit", type=full_commit_sha, help="已从上述真实仓库读取并核验的完整 40 位 SHA；仍会核对 origin 和祖先关系")
    parser.add_argument("--repository-public-read-verified", action="store_true", help="调用者已匿名读取并核验上述远端 SHA；必须同时提供 --repository-verified-commit")
    args = parser.parse_args()
    if args.repository_public_read_verified and not args.repository_verified_commit:
        parser.error("--repository-public-read-verified 必须与 --repository-verified-commit 一同使用。")
    try:
        result = create_package(args)
    except (PackageError, OSError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
        print("材料包未完成：" + str(exc), file=sys.stderr)
        print("已有文件不会被删除或覆盖；若留下部分输出，修复后请选择新的项目内输出目录。", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
