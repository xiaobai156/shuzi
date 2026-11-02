"""Fetch HTML packed by a page-referenced pako inflateRaw script."""

import base64
import html
import json
import re
import zlib
from urllib.parse import urljoin, urlsplit

from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.acquisition.http_client import fetch_text


def fetch_packed_page_document(page_url: str, expected_script_url: str):
    expected = urlsplit(expected_script_url)
    if expected.scheme != "https" or not expected.netloc or not expected.path:
        raise ValueError("压缩正文脚本来源配置无效")

    page = fetch_text(page_url)
    sources = [
        urljoin(page_url, html.unescape(match.group(1)))
        for match in re.finditer(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", page, re.I)
    ]
    matches = [
        source for source in sources
        if (urlsplit(source).scheme, urlsplit(source).netloc, urlsplit(source).path)
        == (expected.scheme, expected.netloc, expected.path)
    ]
    if len(matches) != 1:
        raise ValueError("页面没有唯一引用配置的压缩正文脚本")

    script = fetch_text(matches[0])
    payload = re.search(r"var __jGr\s*=\s*'([^']+)';\s*var obj", script, re.S)
    if not payload:
        raise ValueError("压缩正文脚本缺少数据块")
    blocks = json.loads(payload.group(1)).get("_v54gOM")
    if not isinstance(blocks, list) or not blocks or len(blocks) > 1000:
        raise ValueError("压缩正文数据块无效")

    parts = []
    remaining = 2_000_000
    for block in blocks:
        if not isinstance(block, str) or remaining < 1:
            raise ValueError("压缩正文数据块无效或过大")
        decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        part = decoder.decompress(base64.b64decode(block, validate=True), remaining + 1)
        if not decoder.eof or len(part) > remaining:
            raise ValueError("压缩正文数据块未完整解码或过大")
        parts.append(part)
        remaining -= len(part)
    content = b"".join(parts).decode("utf-8")
    return make_source_document(
        kind="packed_script_html",
        url=matches[0],
        parent_url=page_url,
        content=content,
        identity=expected.path,
        priority=100,
        metadata={"parseable": True},
    )
