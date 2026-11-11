r"""Assemble the reviewed MedRAG submission inputs without publishing anything.

Run after committing the text inputs and reviewing the rendered PDF, for example:
    .venv\Scripts\python.exe submission/build_package.py --date 20261111 --pdf-pages 11

Pass --repository-verified-commit only after independently reading that full SHA
from the intended remote. Local origin configuration is not upload evidence.
Add --repository-public-read-verified only when that external read was anonymous.
An optional --review-archive is retained byte-for-byte outside Git source/history;
--demo-video and --video-verification add an externally verified recording.

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
PACKAGE_PREFIX = "MedRAG-Final-"
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
    "submission/team-profile.json": "team-profile.json",
    "submission/01-提交清单.md": "02-技术文档/01-提交清单.md",
    "submission/02-演示视频脚本.md": "03-演示视频/演示视频脚本.md",
    "submission/03-复现与提交指南.md": "01-源码/复现与提交指南.md",
    "submission/04-验证记录.md": "04-验证与资料/验证记录.md",
    "submission/05-最终交付与待办.md": "05-最终交付与待办.md",
    "submission/06-运行包核验说明.md": "04-验证与资料/06-运行包核验说明.md",
    "submission/demo-storyboard.json": "03-演示视频/demo-storyboard.json",
    "submission/demo-video-summary.json": "03-演示视频/demo-video-summary.json",
    "submission/runtime-audit.json": "04-验证与资料/runtime-audit.json",
    "submission/07-引用语义人工复核表.md": "04-验证与资料/07-引用语义人工复核表.md",
    "submission/08-原创与AI辅助开发说明.md": "04-验证与资料/08-原创与AI辅助开发说明.md",
    "docs/06-参赛完善与演示.md": "04-验证与资料/06-参赛完善与演示.md",
    "docs/07-启动修复与评审检查.md": "04-验证与资料/07-启动修复与评审检查.md",
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
    if any(part in {".git", ".local", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                    "runtime", "deliverables", "output", "tmp"}
           for part in lowered):
        return True
    if any(part == ".env" or (part.startswith(".env.") and part != ".env.example") for part in lowered):
        return True
    if filename in {"id_rsa", "id_ed25519", "credentials.json"} or filename.endswith((".pem", ".key")):
        return True
    if re.search(r"\.(?:db|sqlite3?)(?:-wal|-shm)?$", filename):
        return True
    if filename.endswith((".pdf", ".pyc", ".log", ".zip", ".bundle", ".7z", ".rar", ".mp4", ".webm", ".wav")):
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
    url = repository.get("url", "") if isinstance(repository, dict) else repository if isinstance(repository, str) else ""
    url = url or team.get("repository_url", "")
    parsed = urlsplit(url) if isinstance(url, str) else urlsplit("")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    repository_url_is_specific = (parsed.scheme in {"https", "http"}
                                  and parsed.hostname in {"github.com", "www.github.com", "gitee.com", "www.gitee.com"}
                                  and len(parts) == 2 and parts[0] not in {"new", "login", "settings", "features"})
    review = team.get("team_review", {})
    reviewed = isinstance(review, dict) and provided(review.get("reviewed_by")) and provided(review.get("reviewed_at"))
    assignment = team.get("division_assignment", {})
    assignment_confirmed = (isinstance(assignment, dict)
                            and assignment.get("status") == "confirmed_by_user_delegation")
    return {"information_complete": not missing, "missing_fields": missing,
            "specific_repository_url_provided": repository_url_is_specific,
            "reviewer_access_verified_by_package": False, "team_final_review_recorded": reviewed,
            "division_assignment_confirmed": assignment_confirmed,
            "division_assignment_status": "confirmed_by_user_delegation" if assignment_confirmed else "not_confirmed",
            "instructors_count": len(instructors)}


def validate_no_registration_reference(value: Any) -> None:
    """Public submission metadata must not retain account/team-page references."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in {"team_id", "teamid", "team_page_url", "registration_url", "registration_page_url"}:
                if item not in (None, "", [], {}):
                    raise PackageError("对外团队资料仍含报名参考信息，已停止；不会输出其值。")
            validate_no_registration_reference(item)
    elif isinstance(value, list):
        for item in value:
            validate_no_registration_reference(item)
    elif isinstance(value, str) and value.startswith(("https://", "http://")):
        if re.search(r"(?:^|&)(?:teamid|team_id)=", urlsplit(value).query, re.I):
            raise PackageError("对外材料链接仍含报名团队标识，已停止。")


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


def review_entry_index(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Inspect names only. Runtime data is allowed here, never in source/history."""
    entries: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    for item in archive.infolist():
        name = item.filename
        parts = name.rstrip("/").split("/")
        mode = (item.external_attr >> 16) & 0o170000
        if ("\x00" in item.orig_filename or "\\" in name or name.startswith("/")
                or any(part in {"", ".", ".."} or ":" in part for part in parts)
                or mode == 0o120000 or item.flag_bits & 1):
            raise PackageError("运行 ZIP 含越界路径、链接或加密条目，已停止。")
        folded = name.rstrip("/").casefold()
        if folded in seen:
            raise PackageError("运行 ZIP 含大小写冲突或重复条目，已停止。")
        seen.add(folded)
        if item.is_dir():
            continue
        lowered = [part.casefold() for part in parts]
        relative = "/".join(lowered[1:])
        basename = lowered[-1]
        if (any(part in {".git", ".local", ".venv", "node_modules"} for part in lowered)
                or relative == "submission/team-info.json"
                or basename == "incomplete-do-not-submit.txt"
                or basename.endswith((".log", ".pid", ".db-wal", ".db-shm"))):
            raise PackageError("运行 ZIP 含个人配置、历史缓存或未完成标记，已停止。")
        if basename == ".env" and relative != "backend/.env":
            raise PackageError("运行 ZIP 含非约定位置的环境配置，已停止。")
        if basename.startswith(".env.") and basename != ".env.example":
            raise PackageError("运行 ZIP 含额外环境配置文件，已停止。")
        if re.search(r"\.(?:db|sqlite3?)$", basename) and relative not in {
            "backend/app/data/medrag.db", "runtime/vector-seed/chroma.sqlite3",
        }:
            raise PackageError("运行 ZIP 含非约定的数据库，已停止。")
        entries[name] = item
    return entries


def small_zip_json(archive: zipfile.ZipFile, item: zipfile.ZipInfo) -> tuple[Any, bytes]:
    if item.file_size > 1024 * 1024:
        raise PackageError("运行包元数据 JSON 过大，已停止。")
    try:
        data = archive.read(item)
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError("运行包元数据 JSON 无法解析。") from exc
    validate_json_privacy(value)
    return value, data


def inspect_review_zip(archive: zipfile.ZipFile, implementation: str, package_commit: str) -> tuple[dict[str, Any], bytes, bytes]:
    entries = review_entry_index(archive)
    matches = [name for name in entries if name.endswith("/runtime/review-package-manifest.json")]
    if len(matches) != 1:
        raise PackageError("运行 ZIP 必须含唯一的 runtime/review-package-manifest.json。")
    manifest_name = matches[0]
    prefix = manifest_name.removesuffix("runtime/review-package-manifest.json")
    if len(PurePosixPath(prefix).parts) != 1 or any(not name.startswith(prefix) for name in entries):
        raise PackageError("运行 ZIP 必须使用单一顶层目录。")
    verification_name = prefix + "runtime/verification.json"
    if verification_name not in entries:
        raise PackageError("运行 ZIP 缺少 runtime/verification.json 验证记录。")
    runtime, runtime_bytes = small_zip_json(archive, entries[manifest_name])
    verification, verification_bytes = small_zip_json(archive, entries[verification_name])
    if not isinstance(runtime, dict) or not isinstance(verification, dict):
        raise PackageError("运行包元数据应为 JSON 对象。")
    validate_no_registration_reference(runtime)
    validate_no_registration_reference(verification)
    revision = runtime.get("source_commit", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise PackageError("运行包未记录完整实现提交号。")
    revision = revision.casefold()
    if revision != implementation.casefold():
        raise PackageError("运行包 source_commit 与 --implementation-commit 不一致，不能混合版本。")
    git(["merge-base", "--is-ancestor", revision, "HEAD"])
    personal_tables = runtime.get("empty_personal_tables")
    if (runtime.get("sources_from_committed_revision") is not True
            or runtime.get("python_included") is not True
            or runtime.get("actual_user_env_copied") is not False
            or runtime.get("personal_database_copied") is not False
            or not isinstance(personal_tables, list) or not all(isinstance(name, str) for name in personal_tables)
            or not {"conversation", "message", "feedback"}.issubset(set(personal_tables))):
        raise PackageError("运行包缺少独立运行或个人数据隔离声明，已停止。")
    if (verification.get("documents_ready") != runtime.get("num_documents")
            or verification.get("chunks") != runtime.get("num_chunks")):
        raise PackageError("运行包元数据与验证记录的资料数量不一致。")
    frontend = runtime.get("frontend_sha256")
    if not isinstance(frontend, dict) or not frontend:
        raise PackageError("运行包缺少预构建网页哈希清单。")
    for relative, expected in frontend.items():
        if not isinstance(relative, str):
            raise PackageError("运行包网页哈希清单路径类型不正确。")
        name = prefix + "frontend/dist/" + relative
        if name not in entries or not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
            raise PackageError("运行包网页哈希清单格式或条目不正确。")
        digest = hashlib.sha256()
        with archive.open(entries[name]) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected.casefold():
            raise PackageError("运行包网页资源与记录哈希不一致。")
    example_name = prefix + "submission/team-info.example.json"
    if example_name in entries:
        example, _ = small_zip_json(archive, entries[example_name])
        validate_team_example(example)
        validate_no_registration_reference(example)
    # Verify decompression/CRC without extracting. The allowed seed .env is not
    # opened or decoded: its content remains untouched inside the original ZIP.
    checked = 0
    for name, item in entries.items():
        if name.casefold() == (prefix + "backend/.env").casefold():
            continue
        with archive.open(item) as stream:
            while stream.read(1024 * 1024):
                pass
        checked += 1
    return {
        "source_commit": revision, "source_bundle_commit": package_commit,
        "original_package_label": prefix.rstrip("/"),
        "original_runtime_generated_at": runtime.get("created_at"),
        "original_runtime_manifest_schema_version": runtime.get("schema_version"),
        "runtime_commit_is_ancestor_of_source_bundle_head": True,
        "runtime_and_source_bundle_same_commit": revision == package_commit.casefold(),
        "source_worktree_changes_at_runtime_build": runtime.get("source_worktree_changes"),
        "python_included": True, "python_version": runtime.get("python_version"),
        "num_documents": runtime.get("num_documents"), "num_chunks": runtime.get("num_chunks"),
        "archive_file_entries": len(entries), "crc_checked_entries_excluding_seed_env": checked,
        "environment_config_content_read": False,
        "runtime_manifest_reported_file_count": runtime.get("file_count_excluding_manifest"),
        "verification": verification, "original_archive_preserved": True,
        "registered_team_reference_included": False,
    }, runtime_bytes, verification_bytes


def review_archive_metadata(path: Path, implementation: str, package_commit: str) -> tuple[dict[str, Any], bytes, bytes]:
    if path.suffix.casefold() != ".zip" or not path.is_file():
        raise PackageError("--review-archive 必须指向项目内已存在的 ZIP 文件。")
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    print("正在校验评审运行 ZIP 的路径、记录和 CRC，不提取文件或读取环境配置值…", flush=True)
    with zipfile.ZipFile(path) as archive:
        result, runtime_bytes, verification_bytes = inspect_review_zip(archive, implementation, package_commit)
    result.update({"filename": path.name, "bytes": before[0], "sha256": sha256(path)})
    if (path.stat().st_size, path.stat().st_mtime_ns) != before:
        raise PackageError("校验期间运行 ZIP 发生变化，已停止。")
    return result, runtime_bytes, verification_bytes


def project_input(path: Path) -> Path:
    return inside_repo(path if path.is_absolute() else ROOT / path)


def video_metadata(video_path: Path, verification_path: Path, subtitles_path: Path | None) -> dict[str, Any]:
    """Check the supplied probe/operation record; this function does not invent a recording."""
    if not video_path.is_file() or video_path.suffix.casefold() != ".mp4":
        raise PackageError("--demo-video 必须指向项目内已有的 MP4。")
    before = (video_path.stat().st_size, video_path.stat().st_mtime_ns)
    with video_path.open("rb") as stream:
        header = stream.read(64)
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise PackageError("演示视频缺少 MP4 文件头。")
    verification_before = (verification_path.stat().st_size, verification_path.stat().st_mtime_ns)
    verification = read_json(verification_path)
    validate_no_registration_reference(verification)
    if not isinstance(verification, dict):
        raise PackageError("视频核验 JSON 必须是对象。")
    video = verification.get("video", {})
    narration = verification.get("narration", {})
    capture = verification.get("capture", {})
    if not all(isinstance(item, dict) for item in (video, narration, capture)):
        raise PackageError("视频核验记录缺少 video / narration / capture 对象。")
    duration = video.get("duration_seconds")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not 300 <= duration <= 480 or video.get("width") != 1920 or video.get("height") != 1080
            or not provided(video.get("video_codec")) or not provided(video.get("audio_codec"))
            or not isinstance(video.get("audio_streams"), int) or video["audio_streams"] < 1):
        raise PackageError("视频核验记录不满足 300-480 秒、1920×1080 画面和音轨要求。")
    if (narration.get("language") != "zh-CN" or narration.get("type") != "synthetic_mandarin"
            or narration.get("speech_present") is not True or not provided(narration.get("voice"))):
        raise PackageError("视频核验记录缺少普通话合成讲解声明或有效语音记录。")
    scenes = capture.get("scenes", [])
    logs = capture.get("logs", [])
    if (capture.get("type") != "actual_browser_interaction"
            or capture.get("personal_history_included") is not False
            or capture.get("api_key_visible") is not False
            or not isinstance(scenes, list) or not scenes
            or not all(isinstance(scene, dict) and provided(scene.get("title"))
                       and isinstance(scene.get("actual_steps"), list) and scene["actual_steps"] for scene in scenes)
            or not isinstance(logs, list) or not logs):
        raise PackageError("视频缺少真实浏览器操作日志或个人信息隔离记录。")
    digest = sha256(video_path)
    if not isinstance(video.get("sha256"), str) or video["sha256"].casefold() != digest:
        raise PackageError("MP4 与视频核验记录的 SHA256 不一致。")
    if (video_path.stat().st_size, video_path.stat().st_mtime_ns) != before:
        raise PackageError("校验期间视频发生变化，已停止。")
    verification_hash = sha256(verification_path)
    if (verification_path.stat().st_size, verification_path.stat().st_mtime_ns) != verification_before:
        raise PackageError("校验期间视频核验 JSON 发生变化，已停止。")
    subtitles_hash = None
    if subtitles_path is not None:
        if not subtitles_path.is_file() or subtitles_path.suffix.casefold() != ".srt":
            raise PackageError("字幕必须是项目内已有的 SRT 文件。")
        data = subtitles_path.read_bytes()
        if contains_secret(data) or "-->" not in data.decode("utf-8-sig"):
            raise PackageError("SRT 字幕格式异常或含疑似凭据。")
        subtitles_hash = hashlib.sha256(data).hexdigest()
    return {
        "status": "generated_verified", "filename": video_path.name, "bytes": before[0], "sha256": digest,
        "duration_seconds": duration, "width": 1920, "height": 1080,
        "video_codec": video["video_codec"], "audio_codec": video["audio_codec"],
        "narration": narration, "actual_browser_operation_scenes": len(scenes),
        "actual_browser_operation_logs": len(logs), "team_final_review_required": True,
        "verification_basis": "external_media_probe_and_actual_operation_record_plus_local_file_header_and_sha256",
        "media_probe_performed_by_packager": False, "subtitles_included": subtitles_path is not None,
        "verification_sha256": verification_hash, "subtitles_sha256": subtitles_hash,
    }


def copy_verified_binary(source: Path, destination: Path, expected_hash: str) -> None:
    before = (source.stat().st_size, source.stat().st_mtime_ns)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise PackageError("目标材料文件已存在，拒绝覆盖。")
    shutil.copy2(source, destination)
    if (source.stat().st_size, source.stat().st_mtime_ns) != before or sha256(destination) != expected_hash:
        raise PackageError("复制期间大文件发生变化或哈希不一致，保留输出供检查。")


def index_markdown(commit: str, implementation: str, pdf_pages: int, status: dict[str, Any], repository: dict[str, Any],
                   review: dict[str, Any] | None = None, video: dict[str, Any] | None = None) -> str:
    team_line = "队名、学校、组别、成员均已提供。" if status["information_complete"] else "待填写：" + "、".join(status["missing_fields"]) + "。"
    assignment_line = ("实名分工已按队伍负责人授权确定，不等于个人贡献签名或全文终审。"
                       if status["division_assignment_confirmed"] else "仍需确认成员分工。")
    instructor_line = f"已提供 {status['instructors_count']} 位指导老师。" if status["instructors_count"] else "如有指导老师，请补充真实信息。"
    review_line = (f"[原始评审运行 ZIP]({review['package_path']})：{review['bytes']:,} 字节，SHA256 `{review['sha256']}`。"
                   f"运行包实现提交为 `{review['source_commit']}`；它是本包源码历史 HEAD 的祖先，原 ZIP 按字节保留。"
                   if review else "本包未收录评审运行 ZIP；请按复现指南准备运行环境。")
    video_line = (f"[演示 MP4]({video['package_path']})：{video['duration_seconds']:g} 秒、1920×1080、含音轨和普通话合成讲解；"
                  "已校验外部媒体核验记录、操作日志、文件头与 SHA256，仍需团队审看内容。"
                  if video else "[演示视频脚本](03-演示视频/演示视频脚本.md)：录制分镜；**本包没有 MP4 成品**。")
    video_action = ("团队审看已生成的演示视频，确认讲解、隐私、实际操作与材料版本，再用于正式提交。"
                    if video else "录制并核验 5-8 分钟、至少 1080p、普通话讲解的 MP4。")
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

本包整理了本地作品、技术 PDF、源码与开发历史、演示材料及验证记录；**本包尚不能证明已完成官网提交**。

## 文件入口

- [干净源码 ZIP](01-源码/MedRAG-source.zip)：按归档提交生成，不含实际密钥、教材 PDF、数据库、索引或依赖缓存。
- [开发历史 bundle](01-源码/MedRAG-history.bundle)：当前 HEAD 可达历史，已经过 bundle verify 和独立 bare clone 校验；不等同于 GitHub / Gitee 托管。
- [复现与提交指南](01-源码/复现与提交指南.md)：安装、恢复历史、运行和提交说明。
- [技术文档 PDF](02-技术文档/MedRAG-技术文档.pdf)：{pdf_pages} 页，已生成，内容仍需团队最终确认。
- [可编辑技术正文](02-技术文档/technical-report.md)：技术 PDF 的可编辑正文。
- [提交清单](02-技术文档/01-提交清单.md)、[正式团队资料](team-profile.json)：逐项要求及本轮授权确定的署名和分工。
- {video_line}
- {review_line}
- `04-验证与资料/`：测试记录、资料许可清单、评测题集、摘要和可用的界面截图。
- [最终交付与待办](05-最终交付与待办.md)：团队终审、人工医学核验和官方提交安排。
- `PACKAGE-MANIFEST.json`、`SHA256SUMS.txt`：版本、材料状态和文件完整性信息。

源码 ZIP 与 Git bundle 不含本机 `submission/team-info.json`；公开模板和本轮正式 `team-profile.json` 均不得含报名页链接或团队报名标识。本包的 `02-技术文档/team-info.json` 使用已清理的公开团队资料。若原评审运行 ZIP 中留有旧版团队说明，以本包根目录 `team-profile.json` 和技术 PDF 为准；原 ZIP 不作改写，哈希可与原交付核对。运行 ZIP 仅收录在总材料包，不会重复放进源码 ZIP 或 Git 历史。

## 仓库与上传核验

实际仓库：[{REPOSITORY_URL}]({REPOSITORY_URL})。

{repository_line}

本记录由调用者提供实际远端读取结果，匿名公开读取仅在显式传入已核验标志时记录。打包脚本只校验 origin 目标与本地提交关系，不联网读取或推送，不修改默认分支、仓库可见性或评审权限。

## 仍需完成

1. 团队资料：{team_line}{assignment_line}{instructor_line}技术正文、视频与医学引用的内容终审仍需团队完成。
2. {repository_followup(repository)}。
3. {video_action}
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
    profile = read_json(ROOT / "submission/team-profile.json")
    validate_no_registration_reference(team)
    validate_no_registration_reference(profile)
    validate_team_example(read_json(ROOT / "submission/team-info.example.json"))
    status = team_status(team)
    review_path = project_input(args.review_archive) if getattr(args, "review_archive", None) else None
    review, runtime_bytes, runtime_verification = (review_archive_metadata(review_path, implementation, commit)
                                                  if review_path else (None, None, None))
    video_path = project_input(args.demo_video) if getattr(args, "demo_video", None) else None
    video_verification_path = (project_input(args.video_verification)
                               if getattr(args, "video_verification", None) else None)
    subtitles_path = project_input(args.demo_subtitles) if getattr(args, "demo_subtitles", None) else None
    if bool(video_path) != bool(video_verification_path) or (subtitles_path and not video_path):
        raise PackageError("视频需同时提供 --demo-video 与 --video-verification；字幕不能单独提供。")
    if video_path and subtitles_path is None:
        for candidate in (video_path.with_suffix(".srt"), video_path.parent / "中文字幕.srt"):
            if candidate.is_file():
                subtitles_path = project_input(candidate)
                break
    video = video_metadata(video_path, video_verification_path, subtitles_path) if video_path else None
    remote_configured = bool(git(["remote", "-v"]).strip())  # Never output remote URL values.
    raw_path = inside_repo(ROOT / RAW_EVALUATION)
    raw = public_evaluation(raw_path) if raw_path.is_file() else None
    screenshots = {name: destination for name, destination in SCREENSHOTS.items()
                   if inside_repo(ROOT / name).is_file()}
    existing_parent = output_root
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    large_file_bytes = sum(item["bytes"] for item in (review, video) if item)
    if large_file_bytes and shutil.disk_usage(existing_parent).free < large_file_bytes * 2 + 64 * 1024 * 1024:
        raise PackageError("磁盘剩余空间不足以同时保留大文件副本和总 ZIP，已在创建输出前停止。")

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
    binary_hashes: dict[str, str] = {}
    stored_binaries: set[Path] = set()
    if review is not None:
        review["package_path"] = "05-评审运行包/" + review_path.name
        destination = target / review["package_path"]
        print("正在原样复制评审运行 ZIP…", flush=True)
        copy_verified_binary(review_path, destination, review["sha256"])
        binary_hashes[review["package_path"]] = review["sha256"]
        stored_binaries.add(destination)
        write_new(target / "04-验证与资料/review-package-manifest.json", runtime_bytes)
        write_new(target / "04-验证与资料/runtime-verification.json", runtime_verification)
    if video is not None:
        video["package_path"] = "03-演示视频/" + video_path.name
        destination = target / video["package_path"]
        copy_verified_binary(video_path, destination, video["sha256"])
        binary_hashes[video["package_path"]] = video["sha256"]
        stored_binaries.add(destination)
        verification_data = video_verification_path.read_bytes()
        if hashlib.sha256(verification_data).hexdigest() != video["verification_sha256"]:
            raise PackageError("视频核验 JSON 在预检后发生变化，已停止。")
        write_new(target / "03-演示视频/video-verification.json", verification_data)
        if subtitles_path is not None:
            video["subtitles_path"] = "03-演示视频/" + subtitles_path.name
            subtitle_data = subtitles_path.read_bytes()
            if hashlib.sha256(subtitle_data).hexdigest() != video["subtitles_sha256"]:
                raise PackageError("字幕在预检后发生变化，已停止。")
            write_new(target / video["subtitles_path"], subtitle_data)
    if raw is not None:
        write_new(target / "04-验证与资料/真实中文题集结果.json",
                  (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    write_new(target / "00-材料索引.md", index_markdown(commit, implementation, args.pdf_pages, status, repository, review, video).encode("utf-8"))
    if video is None:
        write_new(target / "03-演示视频/尚缺视频成品.txt",
                  "本目录只有录制脚本，尚无 MP4。需团队录制 5-8 分钟、至少 1080p、普通话讲解的视频。\n".encode("utf-8"))
    payload = [{"path": path.relative_to(target).as_posix(), "bytes": path.stat().st_size,
                "sha256": binary_hashes.get(path.relative_to(target).as_posix()) or sha256(path)}
               for path in sorted(target.rglob("*")) if path.is_file()]
    team_action = ("已授权确定实名分工及指导老师；团队终审正式署名、材料正文和实际贡献表述"
                   if status["division_assignment_confirmed"] and status["instructors_count"]
                   else "团队补齐缺失信息并确认成员分工；内容和贡献表述另行终审")
    manifest = {
        "schema_version": 2, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
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
            "public_final_profile": "team-profile.json",
            "registration_references_included": False,
            "distribution_note": "本轮团队文件使用已清理报名参考信息的公开资料；根目录正式资料优先于原运行 ZIP 内的旧版说明，原 ZIP 未改写。",
        },
        "reviewer_repository_access": (
            "externally_verified_public_anonymous_read_at_package_head" if repository["package_head_public_read_confirmed"]
            else "external_anonymous_read_verified_to_ancestor_only" if repository["external_anonymous_read_verified"]
            else "not_verified_by_this_packager"
        ),
        "review_runtime": review,
        "demo_mp4": video["status"] if video else "missing", "demo_video": video,
        "online_demo": "optional_not_publicly_deployed",
        "platform_registration_payment_submission": "unknown_not_performed_by_this_package",
        "raw_public_evaluation_included": raw is not None,
        "raw_evaluation_privacy_checks": "fixed_public_questions_known_source_fingerprints_and_no_credential_fields" if raw else "not_included",
        "screenshots_included": len(screenshots),
        "semantic_citation_accuracy": None,
        "requires_user_action": [
            team_action,
            repository_followup(repository),
            "团队审看已核验 MP4 的讲解、操作和隐私，确认后再正式提交" if video else "录制和核验符合规格的 MP4，脚本与截图不能替代视频",
            "团队终审并同步技术 PDF、README、视频与最终仓库链接",
            "本人核对实际轮次与官方通知，完成上传并保留回执",
        ],
        "recommended_quality_review": ["人工逐条核验医学结论与引用", "在另一目录或电脑复现安装与完整学习流程"],
        "checksum_scope": "files lists payload only; SHA256SUMS also hashes this manifest but excludes itself",
        "files": payload,
    }
    write_new(target / "PACKAGE-MANIFEST.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    payload_hashes = {item["path"]: item["sha256"] for item in payload}
    checksums = [(payload_hashes.get(path.relative_to(target).as_posix()) or sha256(path)) + "  " + path.relative_to(target).as_posix()
                 for path in sorted(target.rglob("*")) if path.is_file()]
    write_new(target / "SHA256SUMS.txt", ("\n".join(checksums) + "\n").encode("utf-8"))
    commit_time = int(git(["show", "-s", "--format=%ct", "HEAD"]).strip())
    zip_date = datetime.fromtimestamp(commit_time, timezone.utc)
    date_time = (min(2107, max(1980, zip_date.year)), zip_date.month, zip_date.day,
                 zip_date.hour, zip_date.minute, zip_date.second)
    with zipfile.ZipFile(total_zip, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                if path in stored_binaries:
                    # archive.write streams from disk and preserves ZIP64 support.
                    # Recompressing a runtime ZIP or MP4 wastes time and memory.
                    archive.write(path, target.name + "/" + path.relative_to(target).as_posix(), compress_type=zipfile.ZIP_STORED)
                    continue
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
            "review_runtime": review,
            "mp4_status": video["status"] if video else "missing", "official_submission": "not_performed_by_packager"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default="20261111", help="材料日期 YYYYMMDD；默认 20261111")
    parser.add_argument("--output-root", type=Path, default=ROOT / "deliverables", help="项目内部的绝对输出目录")
    parser.add_argument("--pdf-pages", type=int, required=True, help="已渲染核验的实际 PDF 页数，1-30")
    parser.add_argument("--implementation-commit", default="b8c593f", help="运行包应用实现基线；默认 b8c593f")
    parser.add_argument("--review-archive", type=Path, help="项目内既有评审运行 ZIP；原样复制，校验实现提交、CRC 与 SHA256")
    parser.add_argument("--demo-video", type=Path, help="项目内真实浏览器操作演示 MP4；需同时提供视频核验 JSON")
    parser.add_argument("--video-verification", type=Path, help="视频媒体探测结果、普通话合成声明与实际操作日志 JSON")
    parser.add_argument("--demo-subtitles", type=Path, help="可选 UTF-8 SRT；未指定时查找 MP4 同名或同目录中文字幕.srt")
    parser.add_argument("--repository-verified-commit", type=full_commit_sha, help="已从上述真实仓库读取并核验的完整 40 位 SHA；仍会核对 origin 和祖先关系")
    parser.add_argument("--repository-public-read-verified", action="store_true", help="调用者已匿名读取并核验上述远端 SHA；必须同时提供 --repository-verified-commit")
    args = parser.parse_args()
    if args.repository_public_read_verified and not args.repository_verified_commit:
        parser.error("--repository-public-read-verified 必须与 --repository-verified-commit 一同使用。")
    if bool(args.demo_video) != bool(args.video_verification) or (args.demo_subtitles and not args.demo_video):
        parser.error("--demo-video 与 --video-verification 必须一同使用；--demo-subtitles 不能单独使用。")
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
