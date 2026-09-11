"""定位 scutio/scripts 目录（与宿主无关）。

优先环境变量，其次本包所在的 skill scripts 目录。
另含缓存路径段安全化、PDF/HTML 旁路 ``.txt`` 写入（announcements / research 共用）。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

__all__ = [
    "scripts_dir_candidates",
    "find_scripts_dir",
    "ensure_on_syspath",
    "ensure_toolkit_for_skill_script",
    "default_scutio_home",
    "default_python_path",
    "config_dir",
    "state_dir",
    "cache_dir",
    "default_toolkit_scripts",
    "safe_cache_seg",
    "document_cache_filename",
    "atomic_write_bytes",
    "atomic_write_text",
    "write_text_sidecar",
]


def config_dir() -> Path:
    """Persistent preferences and credentials; a custom directory isolates credentials."""
    return Path(
        os.environ.get("SCUTIO_CONFIG_DIR") or default_scutio_home() / "config"
    ).expanduser()


def state_dir() -> Path:
    """Cross-process coordination and health state, independent of disposable caches."""
    return default_scutio_home() / "state"


def cache_dir() -> Path:
    """Re-fetchable API responses and downloaded source documents."""
    return default_scutio_home() / "cache"


def safe_cache_seg(value, fallback: str = "_unknown") -> str:
    """缓存路径段：仅保留字母数字._-，其它变下划线（保留前导 _ 如 ``_misc``）。"""
    s = re.sub(r"[^\w.\-]+", "_", str(value or "").strip(), flags=re.UNICODE)
    s = s.strip(".") or fallback
    if not s or s in ("_", "-"):
        s = fallback
    return s[:64]


def document_cache_filename(stem: str, *, identity: dict, suffix: str) -> str:
    """Readable label plus source identity; truncating a title cannot merge documents."""
    if not re.fullmatch(r"\.[A-Za-z0-9]+", suffix):
        raise ValueError("invalid document suffix")
    digest = hashlib.sha256(
        json.dumps([suffix.lower(), identity], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:32]
    label = re.sub(r'[\\/:*?"<>|\s\x00-\x1f]+', "_", str(stem)).strip("._")
    # Keep Unicode filenames within filesystem byte limits, reserving room for sidecars.
    label = label.encode("utf-8")[:160].decode("utf-8", errors="ignore").rstrip("._")
    return f"{label or 'document'}_{digest}{suffix.lower()}"


def atomic_write_bytes(path: Union[str, Path], content: bytes) -> Path:
    """同目录临时文件 + ``os.replace``，避免并发读到半文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".%s." % target.name,
            suffix=".tmp",
            dir=str(target.parent),
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        return target
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(path: Union[str, Path], content: str, *, encoding: str = "utf-8") -> Path:
    """文本版原子写入。"""
    return atomic_write_bytes(path, str(content).encode(encoding))


def write_text_sidecar(
    path: Union[str, Path],
    *,
    content: bytes = b"",
    file_format: str = "pdf",
    notice_content: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """在 ``path`` 旁写同名 ``.txt``。返回 ``(text_path, text_error)``。

    优先 ``notice_content``，再从 ``content`` / 磁盘文件抽取（经原文处理模块）。
    抽空时仍写空壳说明，便于发现缺失。
    """
    from scutio_data._documents.text import (
        extract_text_from_bytes,
        extract_text_from_path,
    )

    path = Path(path)
    text = (notice_content or "").strip()
    err = None
    if not text and content:
        try:
            text = extract_text_from_bytes(content, file_format=file_format) or ""
        except Exception as exc:
            err = str(exc)
            text = ""
    if not text and path.is_file():
        try:
            text = extract_text_from_path(path) or ""
        except Exception as exc:
            err = str(exc)
            text = ""
    text_path = path.with_suffix(".txt")
    if text:
        atomic_write_text(text_path, text)
        return str(text_path), None
    if err and err.startswith("suspect_pdf_text_encoding:"):
        # 缓存 TXT 可能来自先前错误的字体映射，不能让本地检索继续读取乱码。
        atomic_write_text(text_path, "# text extraction unreadable\n# error: %s\n" % err)
    elif not text_path.exists():
        atomic_write_text(text_path, "# text extraction empty\n# source: %s\n" % path.name)
    return str(text_path), err or "empty_text"


def default_scutio_home() -> Path:
    """用户级根目录：``SCUTIO_HOME``，默认 ``~/.scutio``。"""
    raw = os.environ.get("SCUTIO_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".scutio"


def default_python_path() -> Path:
    """默认 ``SCUTIO_PYTHON``（平台相关 venv 路径）。"""
    explicit = os.environ.get("SCUTIO_PYTHON")
    if explicit:
        return Path(explicit).expanduser()
    venv = os.environ.get("SCUTIO_VENV")
    root = Path(venv).expanduser() if venv else default_scutio_home() / ".venv"
    if sys.platform == "win32":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def default_toolkit_scripts() -> Path:
    """含 ``scutio_data`` 包的目录（其父级才应进模块搜索路径的「包根」）。

    优先级：``SCUTIO_TOOLKIT_SCRIPTS`` →
    ``$SCUTIO_SKILLS_DIR/scutio/scripts`` → 当前包所在 ``scripts``。
    """
    explicit = os.environ.get("SCUTIO_TOOLKIT_SCRIPTS")
    if explicit:
        return Path(explicit).expanduser()
    skills = os.environ.get("SCUTIO_SKILLS_DIR")
    if skills:
        p = Path(skills).expanduser()
        if p.name == "scripts":
            return p
        if p.name == "scutio":
            return p / "scripts"
        return p / "scutio" / "scripts"
    return Path(__file__).resolve().parent.parent


def _env_skills_roots() -> Iterable[Path]:
    """从环境变量收集可能的 skills 根目录。"""
    skills = os.environ.get("SCUTIO_SKILLS_DIR")
    if skills:
        p = Path(skills).expanduser()
        # 接受 …/skills 或 …/skills/scutio
        if p.name == "scutio":
            yield p.parent
        else:
            yield p


def scripts_dir_candidates() -> List[Path]:
    """按优先级返回可能的 scripts 目录候选。"""
    out: List[Path] = []
    # 显式包根（含 scutio_data 的目录）
    try:
        out.append(default_toolkit_scripts())
    except Exception:
        pass
    for root in _env_skills_roots():
        out.append(root / "scutio" / "scripts")
        # 环境变量可能已直接指向 scripts/
        if root.name == "scripts":
            out.append(root)

    # 本文件位于 scutio_data/paths.py → scripts/
    here = Path(__file__).resolve().parent.parent
    out.append(here)

    # 去重且保持顺序
    seen = set()
    uniq: List[Path] = []
    for p in out:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def find_scripts_dir() -> Optional[Path]:
    """返回首个包含 ``scutio_data`` 的已存在 scripts 目录。"""
    for p in scripts_dir_candidates():
        if (p / "scutio_data").is_dir():
            return p
    return None


def ensure_on_syspath(prepend: bool = True) -> Optional[str]:
    """确保 toolkit scripts 在 ``sys.path`` 上；返回路径字符串或 None。"""
    found = find_scripts_dir()
    if found is None:
        return None
    s = str(found)
    if s in sys.path:
        if prepend:
            sys.path.remove(s)
            sys.path.insert(0, s)
        return s
    if prepend:
        sys.path.insert(0, s)
    else:
        sys.path.append(s)
    return s


def ensure_toolkit_for_skill_script(skill_script_file) -> Optional[str]:
    """供研究/复核等内部 CLI 定位当前 skill 内的 scripts。

    ``skill_script_file`` 为调用方 ``__file__``。优先沿父目录查找
    ``scutio_data``，再走显式环境变量与当前包位置。
    """
    candidates: List[Path] = []
    try:
        script_path = Path(skill_script_file).resolve()
        for parent in script_path.parents:
            candidates.append(parent)
    except (TypeError, ValueError):
        pass
    candidates.extend(scripts_dir_candidates())

    seen = set()
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if (p / "scutio_data").is_dir():
            s = str(p)
            if s in sys.path:
                sys.path.remove(s)
            sys.path.insert(0, s)
            return s
    return None
