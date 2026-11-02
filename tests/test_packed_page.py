import base64
import json
import zlib

import pytest

from kill_numbers.acquisition.strategies import packed_page
from kill_numbers.parsing.registry import parse_target_content


def test_page_referenced_packed_script_keeps_bottom_issue_and_source(monkeypatch):
    page_url = "https://example.test/article.html"
    script_url = "https://api.llepai.com/js-111-10097?v=1"
    content = (
        "作者:大包大揽楼主\n"
        "269期:❀稳杀10码❀⇨\n[01.02.03.04.05.06.07.08.09.10]\n"
        "270期:❀稳杀10码❀⇨\n[04.05.08.21.25.29.34.39.47.48]\n"
        "帖子页尾部"
    )
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    block = compressor.compress(content.encode()) + compressor.flush()
    payload = json.dumps({"_v54gOM": [base64.b64encode(block).decode()]})
    script = f"var __jGr = '{payload}';var obj = JSON.parse(__jGr);"
    page = f'<script src="{script_url}"></script>'
    monkeypatch.setattr(
        packed_page,
        "fetch_text",
        lambda url: page if url == page_url else script if url == script_url else None,
    )

    document = packed_page.fetch_packed_page_document(
        page_url, "https://api.llepai.com/js-111-10097"
    )
    assert document.url == script_url
    assert document.parent_url == page_url
    assert parse_target_content(
        document.content,
        {
            "keywords": ["稳杀10码"],
            "count": 10,
            "region": "bottom",
            "anchor": "作者:大包大揽楼主",
            "stop_anchor": "帖子页尾部",
            "issue_position_window": 10,
        },
        ["270"],
    ) == {"270": ["04", "05", "08", "21", "25", "29", "34", "39", "47", "48"]}
    with pytest.raises(ValueError, match="唯一引用"):
        packed_page.fetch_packed_page_document(page_url, "https://api.llepai.com/other")
