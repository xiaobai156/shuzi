import inspect

import crawler
import pytest
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.parsing import registry
from kill_numbers.parsing.dedicated import site_parsers


def test_every_configured_special_parser_is_registered():
    configured = {
        target.get("special_parser")
        for target in crawler.load_targets(crawler.TARGETS_FILE)
        if target.get("special_parser")
    }

    assert configured <= registry.VALID_SPECIAL_PARSERS
    assert configured - registry.ACQUISITION_ONLY_PARSERS <= set(registry.PARSERS)


def test_target_without_region_is_rejected_before_formal_crawl(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(
        '[{"url": "https://example.test/topic/1", "name": "缺方向"}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="必须配置合法 region"):
        crawler.load_targets(path)


def test_invalid_issue_position_window_is_rejected(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(
        '[{"url":"https://example.test/topic/1","region":"top",'
        '"issue_position_window":0}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="issue_position_window 必须是正整数"):
        crawler.load_targets(path)


def test_invalid_issue_position_region_is_rejected(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(
        '[{"url":"https://example.test/topic/1","region":"top",'
        '"issue_position_region":"middle"}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="issue_position_region 必须是 top 或 bottom"):
        crawler.load_targets(path)


def test_duplicate_active_names_are_rejected(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(
        '[{"url":"https://one.test","name":"重名","region":"top"},'
        '{"url":"https://two.test","name":"重名","region":"bottom"}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="启用目标名称重复：重名"):
        crawler.load_targets(path)


def test_dynamic_article_api_id_must_match_page_url(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(
        '[{"url":"https://example.test/article/manager/right-id?url=site",'
        '"api_url":"https://example.test/api/proxy/manager-articles/wrong-id",'
        '"name":"测试","region":"top"}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="文章 ID 不一致"):
        crawler.load_targets(path)


def test_generic_registry_preserves_strict_candidate_rules():
    target = {
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "anchor": "作者甲",
    }
    content = "作者甲\n211期 专属栏目 01 02 03 04\n"

    assert registry.parse_target_content(content, target, ["211"]) == {
        "211": ["01", "02", "03", "04"]
    }
    assert registry.available_issues_from_content(content, target) == ["211"]


def test_issue_position_region_is_independent_from_page_region():
    target = {
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "issue_position_region": "bottom",
        "anchor": "作者甲",
        "issue_position_window": 2,
    }
    content = """作者甲
208期 专属栏目 01 02 03 04
209期 专属栏目 05 06 07 08
210期 专属栏目 09 10 11 12
211期 专属栏目 13 14 15 16
"""

    assert registry.parse_target_content(content, target, ["211"]) == {
        "211": ["13", "14", "15", "16"]
    }
    assert registry.parse_target_content(content, target, ["208"]) == {}
    assert registry.available_issues_from_content(content, target) == ["210", "211"]


def test_unknown_special_parser_never_falls_back_to_generic_parser():
    target = {
        "special_parser": "not_registered",
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "anchor": "作者甲",
    }

    with pytest.raises(ValueError, match="未注册专属解析器"):
        registry.parse_target_content(
            "作者甲\n211期 专属栏目 01 02 03 04\n",
            target,
            ["211"],
        )


def test_document_boundaries_cannot_be_combined_to_create_a_candidate():
    target = {
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "anchor": "栏目标题",
    }
    documents = [
        make_source_document(
            kind="page",
            url="https://example.test/page",
            content="栏目标题\n211期 专属栏目\n",
            priority=100,
        ),
        make_source_document(
            kind="external_script",
            url="https://example.test/data.js",
            content="211期 01 02 03 04\n",
            priority=90,
        ),
    ]

    assert crawler.parse_target_documents(documents, target, ["211"]) == ({}, None)


def test_document_candidates_conflict_even_when_priorities_differ():
    target = {
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "anchor": "栏目标题",
    }
    documents = [
        make_source_document(
            kind="page",
            url="https://example.test/page",
            content="栏目标题\n211期 专属栏目 01 02 03 04\n",
            priority=100,
        ),
        make_source_document(
            kind="external_script",
            url="https://example.test/data.js",
            content="栏目标题\n211期 专属栏目 05 06 07 08\n",
            priority=90,
        ),
    ]

    with pytest.raises(ValueError, match="跨文档候选冲突"):
        crawler.parse_target_documents(documents, target, ["211"])

    with pytest.raises(ValueError, match="采集专属解析器不能直接进入正文解析"):
        registry.parse_target_content(
            "作者甲\n211期 专属栏目 01 02 03 04\n",
            {**target, "special_parser": "zuojianzifu_link_chain"},
            ["211"],
        )


def test_user_forum_topic_sequence_applies_direction_window_before_parsing():
    target = {
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "anchor": "作者甲",
    }
    documents = [
        make_source_document(
            kind="user_forum_topic",
            url=f"https://example.test/topic/{issue}",
            content=f"作者甲\n{issue}期 专属栏目 01 02 03 04\n",
            priority=100,
            metadata={"region_sequence": "user_forum_topics", "region_index": index},
        )
        for index, issue in enumerate((211, 210, 209, 208))
    ]

    issue_map, selected = crawler.parse_target_documents(documents, target, ["208"])
    assert issue_map == {}
    assert selected is None

    expanded_map, _selected = crawler.parse_target_documents(
        documents,
        {**target, "issue_position_window": 4},
        ["208"],
    )
    assert expanded_map == {"208": ["01", "02", "03", "04"]}

    bottom_map, _selected = crawler.parse_target_documents(
        documents,
        {**target, "region": "bottom"},
        ["211"],
    )
    assert bottom_map == {}


def test_invalid_user_topic_does_not_consume_direction_window():
    target = {
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "anchor": "作者甲",
    }
    contents = [
        "无关帖子",
        "作者甲\n211期 专属栏目 01 02 03 04",
        "作者甲\n210期 专属栏目 05 06 07 08",
        "作者甲\n209期 专属栏目 09 10 11 12",
    ]
    documents = [
        make_source_document(
            kind="user_forum_topic",
            url=f"https://example.test/topic/{index}",
            content=content,
            priority=100,
            metadata={"region_sequence": "user_forum_topics", "region_index": index},
        )
        for index, content in enumerate(contents)
    ]

    issue_map, _selected = crawler.parse_target_documents(documents, target, ["209"])

    assert issue_map == {"209": ["09", "10", "11", "12"]}


def test_dedicated_parser_module_does_not_import_entrypoint():
    source = inspect.getsource(site_parsers)

    assert "import crawler" not in source
    assert "from crawler" not in source


def test_crawler_compatibility_exports_use_registry_implementation():
    assert crawler.parse_target_content is registry.parse_target_content
    assert crawler.available_issues_from_content is registry.available_issues_from_content
