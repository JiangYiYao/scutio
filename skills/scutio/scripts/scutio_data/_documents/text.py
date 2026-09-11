"""PDF 与 HTML 文本提取；不查询证券或披露列表。"""

from __future__ import annotations

import html as html_lib
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import List


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"):
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        t = data.strip()
        if t:
            self._chunks.append(t + " ")

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = html_lib.unescape(raw)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _html_to_text(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                s = raw.decode(enc)
                break
            except UnicodeDecodeError:
                s = None
        if s is None:
            s = raw.decode("utf-8", errors="replace")
    else:
        s = raw
    parser = _HTMLTextExtractor()
    try:
        parser.feed(s)
        parser.close()
        out = parser.text()
        if len(out) > 40:
            return out
    except Exception:
        pass
    # fallback strip tags
    plain = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", s)
    plain = re.sub(r"(?s)<[^>]+>", " ", plain)
    plain = html_lib.unescape(plain)
    return re.sub(r"\s+", " ", plain).strip()


def _suspect_pdf_encoding(text: str) -> bool:
    """识别缺失字体映射造成的大量音标与藏文字母混排，不等同于完整质量检测。"""
    letters = sum(char.isalpha() for char in text)
    ipa = sum("\u0250" <= char <= "\u02ff" for char in text)
    tibetan = sum("\u0f00" <= char <= "\u0fff" for char in text)
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    return (
        ipa >= 30
        and tibetan >= 30
        and ipa + tibetan > letters * 0.2
        and cjk < max(10, letters * 0.01)
    )


def extract_text_from_bytes(content: bytes, *, file_format: str = "") -> str:
    """PDF / HTML / 纯文本 bytes → 文本；已识别的 PDF 编码乱码抛出 ValueError。"""
    if not content:
        return ""
    fmt = (file_format or "").lower()
    if not fmt:
        if content.startswith(b"%PDF"):
            fmt = "pdf"
        elif content.lstrip()[:1] in (b"<", b"\xef") or b"<html" in content[:200].lower():
            fmt = "html"
        else:
            fmt = "txt"
    if fmt in ("html", "htm", "xhtml"):
        return _html_to_text(content)
    if fmt == "pdf" or content.startswith(b"%PDF"):
        suspect_encoding = False
        # pypdf
        try:
            from io import BytesIO

            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
            text = "\n".join(parts).strip()
            if text:
                suspect_encoding = _suspect_pdf_encoding(text)
                if not suspect_encoding:
                    return text
        except Exception:
            pass
        # pdftotext if present
        pdftotext = shutil.which("pdftotext")
        if pdftotext:
            try:
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                    tmp.write(content)
                    tmp.flush()
                    proc = subprocess.run(
                        [pdftotext, "-layout", "-enc", "UTF-8", tmp.name, "-"],
                        capture_output=True,
                        timeout=120,
                    )
                    if proc.returncode == 0 and proc.stdout:
                        text = proc.stdout.decode("utf-8", errors="replace").strip()
                        if text and not _suspect_pdf_encoding(text):
                            return text
                        suspect_encoding = suspect_encoding or _suspect_pdf_encoding(text)
            except Exception:
                pass
        if suspect_encoding:
            raise ValueError(
                "suspect_pdf_text_encoding: extracted text has unreadable font mappings; "
                "inspect the original PDF or use another official language edition"
            )
        return ""
    return content.decode("utf-8", errors="replace").strip()


def extract_text_from_path(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    data = p.read_bytes()
    suf = p.suffix.lower().lstrip(".")
    if suf in ("htm", "html", "xhtml"):
        return extract_text_from_bytes(data, file_format="html")
    if suf == "pdf":
        return extract_text_from_bytes(data, file_format="pdf")
    if suf in ("txt", "text", "md"):
        return data.decode("utf-8", errors="replace")
    return extract_text_from_bytes(data)
