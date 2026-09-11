"""公告原文地址解析、下载、缓存校验与文本旁路文件。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from scutio_data._providers.sec import _SEC_UA
from scutio_data._runtime import http
from scutio_data._runtime.environment import UA
from scutio_data._runtime.results import result_err, result_ok
from scutio_data._runtime.symbols import normalize_code
from scutio_data._runtime.timeouts import operation, remaining, source
from scutio_data.paths import (
    atomic_write_bytes,
    atomic_write_text,
    default_scutio_home,
    document_cache_filename,
    safe_cache_seg,
    write_text_sidecar,
)


def resolve_file_url(item: Any) -> Tuple[str, str, dict]:
    """返回 (url, file_format, meta)。"""
    if isinstance(item, str):
        url = item.strip()
        fmt = "pdf" if url.lower().endswith(".pdf") or ".pdf?" in url.lower() else "html"
        return url, fmt, {}
    meta = dict(item or {})
    (meta.get("market") or "").lower()
    if meta.get("file_resolution") == "cninfo_detail" and not (
        meta.get("file_url") or meta.get("pdf_url")
    ):
        from scutio_data._providers.cninfo import resolve_cninfo_file

        meta = resolve_cninfo_file(meta)
    url = (meta.get("file_url") or meta.get("pdf_url") or "").strip()
    fmt = (meta.get("file_format") or "").lower()
    if not fmt:
        if url.lower().endswith(".pdf") or ".pdf?" in url.lower():
            fmt = "pdf"
        elif url:
            fmt = "html"
    return url, fmt, meta


@operation("download")
@source("download")
def download_filing_bytes(
    url: str,
    *,
    file_format: str = "pdf",
    referer: str = "",
    timeout: int = 180,
    single_attempt: bool = False,
) -> bytes:
    """下载原文；可选在第一次传输失败后立即返回。"""
    if not url:
        raise ValueError("empty url")
    ref = referer or (
        "https://data.eastmoney.com/"
        if "dfcfw.com" in url or "eastmoney.com" in url
        else "https://www.sec.gov/"
        if "sec.gov" in url
        else "https://www.hkexnews.hk/"
    )
    # curl first for eastmoney pdf
    if "dfcfw.com" in url or file_format == "pdf":
        curl = shutil.which("curl")
        if curl:
            cmd = [
                curl,
                "-fsSL",
                "--max-time",
                str(remaining("download", maximum=timeout)),
                "--connect-timeout",
                str(min(5, remaining("download"))),
                "--speed-time",
                "30",
                "--speed-limit",
                "1",
                "-A",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "-e",
                ref,
                url,
            ]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=remaining("download", maximum=timeout)
                )
                if result.returncode == 0 and result.stdout:
                    if file_format == "pdf" and result.stdout.startswith(b"%PDF"):
                        return result.stdout
                    if file_format != "pdf" and len(result.stdout) > 100:
                        return result.stdout
                    if result.stdout.startswith(b"%PDF"):
                        return result.stdout
                if single_attempt:
                    if result.stdout:
                        return result.stdout
                    detail = (result.stderr or b"").decode("utf-8", errors="replace").strip()
                    raise RuntimeError(
                        "curl filing download failed (exit=%s%s)"
                        % (
                            result.returncode,
                            ": " + detail[:300] if detail else "",
                        )
                    )
            except Exception as exc:
                if single_attempt:
                    raise RuntimeError("single filing download attempt failed: %s" % exc) from exc
    headers = {
        "User-Agent": _SEC_UA if "sec.gov" in url else UA,
        "Referer": ref,
        "Accept": "*/*",
    }
    r = http.get(url, headers=headers, timeout=min(30, timeout), download=True)
    r.raise_for_status()
    return r.content or b""


def _filing_code_from_meta(meta: dict, market: str) -> str:
    """从列表条目解析缓存用个股代码。"""
    meta = meta or {}
    if market == "us":
        raw = meta.get("ticker") or meta.get("sec_code") or ""
        if not raw and meta.get("cik"):
            raw = "CIK" + str(meta.get("cik"))
        return safe_cache_seg(str(raw).upper(), "UNKNOWN")
    if market == "hk":
        raw = meta.get("sec_code") or meta.get("code") or ""
        if str(raw).isdigit():
            raw = str(raw).zfill(5)
        return safe_cache_seg(raw, "00000")
    # A
    raw = meta.get("sec_code") or meta.get("code") or ""
    if str(raw).isdigit():
        raw = normalize_code(raw)
    return safe_cache_seg(raw, "000000")


def _default_filings_dir(market: Optional[str] = None, code: Optional[str] = None) -> Path:
    """``$SCUTIO_HOME/cache/documents/filings/{a|hk|us}/{code}/``（按市场+个股，无平铺兼容）。"""
    base = Path(default_scutio_home()) / "cache" / "documents" / "filings"
    mkt = market if market in ("a", "hk", "us") else "a"
    code_seg = safe_cache_seg(code, "_unknown")
    return base / mkt / code_seg


def extract_filing_text(path: Union[str, Path]) -> str:
    """本地 PDF/HTML/TXT → 纯文本（供搜索/无 PDF 阅读器环境）。"""
    from scutio_data._documents import text as filing_text

    return filing_text.extract_text_from_path(path)


def _write_sidecar_text(
    path: Path,
    *,
    content: bytes = b"",
    file_format: str = "pdf",
    notice_content: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """写同目录 ``.txt``（委托 ``paths.write_text_sidecar``）。"""
    return write_text_sidecar(
        path,
        content=content,
        file_format=file_format,
        notice_content=notice_content,
    )


def _valid_filing_content(content: bytes, file_format: str) -> bool:
    """拒绝 PDF 伪响应和常见 HTTP 200 风控 HTML。"""
    if not content:
        return False
    fmt = str(file_format or "").lower()
    if fmt == "pdf":
        return content.startswith(b"%PDF")
    if fmt == "html":
        if len(content) <= 500:
            return False
        sample = content[:200000].decode("utf-8", errors="ignore").lower()
        blocked = (
            "undeclared automated tool",
            "request rate threshold exceeded",
            "<title>access denied",
            "/captcha/",
            "cf-chl-",
            "<title>security verification",
        )
        if any(token in sample for token in blocked):
            return False
        return any(token in sample for token in ("<html", "<!doctype", "<xbrl", "<ix:"))
    return len(content) > 500


@operation("download")
def download_announcement_pdf(
    item: Union[dict, str],
    target_dir: Optional[str] = None,
    *,
    filename: Optional[str] = None,
    extract_text: bool = True,
    timeout: Optional[int] = None,
    single_attempt: bool = False,
) -> dict:
    """下载定期报告/公告原文，并默认再落一份 ``.txt``。

    Args:
        item: ``periodic_reports`` / ``stock_announcements`` 条目，
            或直接传 URL 字符串。
        target_dir: 目录；默认 ``$SCUTIO_HOME/cache/documents/filings/{a|hk|us}/{code}/``。
        filename: 可选基名；最终文件名附加文档身份摘要和原文格式扩展名。
        extract_text: True（默认）时写同名 ``.txt``（PDF/HTML 抽取或港股正文）。
        timeout: 可选的单次传输超时秒数。
        single_attempt: True 时下载失败后不切换第二种传输。

    Returns:
        result_ok：``path`` / ``text_path`` / ``file_format`` / ``bytes`` / ``cached``；
        港股若有 ``notice_content`` 会优先写入 txt。
    """

    try:
        url, file_format, meta = resolve_file_url(item)
    except Exception as exc:
        return result_err(str(exc), source="download_announcement_pdf", path=None)

    if not url and not (meta.get("notice_content") or "").strip():
        mkt_guess = (meta.get("market") or "").lower() or "a"
        from scutio_data._documents.hints import fallback_hint_for_market

        return result_err(
            "missing file_url/pdf_url",
            source="download_announcement_pdf",
            path=None,
            pdf_url=None,
            market=mkt_guess,
            fallback_hint=fallback_hint_for_market(mkt_guess),
            note=fallback_hint_for_market(mkt_guess),
        )

    market = (meta.get("market") or "").lower()
    if not market:
        if meta.get("art_code") or meta.get("cik"):
            market = "hk" if meta.get("art_code") else "us"
        else:
            market = "a"

    code_seg = _filing_code_from_meta(meta, market)
    out_dir = Path(target_dir) if target_dir else _default_filings_dir(market, code_seg)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 纯文本条目（无附件但有正文）
    notice = (meta.get("notice_content") or "").strip()
    identity = {
        "kind": "filing",
        "market": market,
        "code": code_seg,
        "document_id": str(
            meta.get("announcement_id")
            or meta.get("accession_number")
            or meta.get("art_code")
            or ""
        ),
        "url": url,
        "notice": notice,
    }
    if not url and notice:
        base = (
            Path(filename).stem
            if filename
            else "_".join(
                [
                    (meta.get("date") or "")[:10],
                    meta.get("sec_code") or meta.get("ticker") or "",
                    meta.get("title") or meta.get("announcement_id") or "filing",
                ]
            )
        )
        text_path = out_dir / document_cache_filename(base, identity=identity, suffix=".txt")
        atomic_write_text(text_path, notice)
        return result_ok(
            source="download_announcement_pdf",
            path=None,
            text_path=str(text_path),
            pdf_url=None,
            file_url=None,
            file_format="txt",
            bytes=len(notice.encode("utf-8")),
            cached=False,
            market=market,
            code=code_seg,
        )

    ext = ".pdf" if file_format == "pdf" else ".html" if file_format == "html" else ".bin"
    if filename:
        raw = Path(filename).stem
    else:
        raw = "_".join(
            [
                (meta.get("date") or "")[:10],
                meta.get("sec_code") or meta.get("ticker") or meta.get("cik") or "",
                meta.get("title") or meta.get("announcement_id") or "filing",
            ]
        )
    fname = document_cache_filename(raw, identity=identity, suffix=ext)
    path = out_dir / fname
    if path.resolve().parent != out_dir.resolve():
        return result_err(
            "unsafe filename outside target_dir",
            source="download_announcement_pdf",
            path=None,
            market=market,
        )

    try:
        cached = False
        content = b""
        if path.exists() and path.stat().st_size > 500:
            content = path.read_bytes()
            ok_cache = _valid_filing_content(content, file_format)
            if ok_cache:
                cached = True
            else:
                path.unlink(missing_ok=True)
                content = b""

        if not cached:
            content = download_filing_bytes(
                url,
                file_format=file_format or "pdf",
                referer=(
                    "https://www.cninfo.com.cn/"
                    if market == "a"
                    else "https://data.eastmoney.com/"
                    if market == "hk"
                    else "https://www.sec.gov/"
                ),
                timeout=(max(1, int(timeout)) if timeout is not None else 180),
                single_attempt=single_attempt,
            )
            if not _valid_filing_content(content, file_format):
                if not notice:
                    hint = fallback_hint_for_market(market)
                    return result_err(
                        "invalid filing response (format=%s magic=%r)" % (file_format, content[:8]),
                        source="download_announcement_pdf",
                        path=None,
                        pdf_url=url,
                        file_url=url,
                        market=market,
                        fallback_hint=hint,
                        note=hint,
                    )
            else:
                atomic_write_bytes(path, content)

        text_path = None
        text_error = None
        if extract_text:
            if (
                not path.exists()
                and content
                and file_format == "pdf"
                and content.startswith(b"%PDF")
            ):
                atomic_write_bytes(path, content)
            if path.exists() or notice:
                # 若 PDF 未写出但有正文
                if not path.exists() and notice:
                    stem = path.with_suffix("").name
                    text_path_p = out_dir / (stem + ".txt")
                    atomic_write_text(text_path_p, notice)
                    text_path = str(text_path_p)
                else:
                    text_path, text_error = _write_sidecar_text(
                        path if path.exists() else out_dir / fname,
                        content=content if path.exists() else b"",
                        file_format=file_format or "pdf",
                        notice_content=notice,
                    )

        return result_ok(
            source="download_announcement_pdf",
            path=str(path) if path.exists() else None,
            text_path=text_path,
            text_error=text_error,
            partial=bool(text_error),
            pdf_url=url if file_format == "pdf" else (meta.get("pdf_url") or None),
            file_url=url,
            file_format=file_format or "pdf",
            bytes=(path.stat().st_size if path.exists() else len(content or b"")),
            cached=cached,
            market=market,
            code=code_seg,
        )
    except Exception as exc:
        from scutio_data._documents.hints import fallback_hint_for_market

        return result_err(
            exc,
            source="download_announcement_pdf",
            path=None,
            pdf_url=url,
            file_url=url,
            market=market,
            fallback_hint=fallback_hint_for_market(market),
            note=fallback_hint_for_market(market),
        )
