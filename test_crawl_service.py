from pathlib import Path

import crawler
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.application import crawl_service
from kill_numbers.application.crawl_service import CrawlDependencies, run_formal_crawl_target


def test_browser_fallback_rechecks_partial_multi_issue_result(monkeypatch):
    target = {
        "url": "https://example.test/topic/1",
        "name": "测试站",
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "anchor": "栏目标题",
        "browser_fallback": True,
    }
    initial = make_source_document(
        kind="page",
        url=target["url"],
        content="栏目标题\n211期 专属栏目 01 02 03 04\n",
        priority=100,
    )
    rendered = make_source_document(
        kind="browser_body",
        url=target["url"],
        content=(
            "栏目标题\n"
            "211期 专属栏目 01 02 03 04\n"
            "210期 专属栏目 05 06 07 08\n"
        ),
        priority=60,
    )
    monkeypatch.setattr(crawl_service, "render_page_documents", lambda _url: [rendered])

    dependencies = CrawlDependencies(
        fetch_target_documents=lambda _target, _issues: ("测试站", [initial]),
        parse_target_documents=crawler.parse_target_documents,
        crawl_zuojianzifu_link_chain=lambda _target, _issues: ("", "", {}, {}),
        contract_is_split_across_documents=lambda _documents, _target: False,
        save_debug_page=lambda _target, _issues, _name, _content, _reason: Path("debug.txt"),
    )

    results, failure = run_formal_crawl_target(dependencies, target, ["211", "210"])

    assert failure is None
    assert [(result.issue, result.numbers) for result in results] == [
        ("211", ["01", "02", "03", "04"]),
        ("210", ["05", "06", "07", "08"]),
    ]


def test_multi_issue_pipeline_keeps_each_issue_in_its_own_source_document():
    target = {
        "url": "https://example.test/users/1",
        "name": "测试站",
        "keywords": ["专属栏目"],
        "count": 4,
        "region": "top",
        "anchor": "栏目标题",
    }
    documents = [
        make_source_document(
            kind="user_forum_topic",
            url="https://example.test/topic/211",
            content="栏目标题\n211期 专属栏目 01 02 03 04",
            identity="测试站",
            priority=100,
            metadata={"parseable": True},
        ),
        make_source_document(
            kind="user_forum_topic",
            url="https://example.test/topic/210",
            content="栏目标题\n210期 专属栏目 05 06 07 08",
            identity="测试站",
            priority=100,
            metadata={"parseable": True},
        ),
    ]
    dependencies = CrawlDependencies(
        fetch_target_documents=lambda _target, _issues: ("测试站", documents),
        parse_target_documents=crawler.parse_target_documents,
        crawl_zuojianzifu_link_chain=lambda _target, _issues: ("", "", {}, {}),
        contract_is_split_across_documents=lambda _documents, _target: False,
        save_debug_page=lambda *_args: None,
    )

    results, failure = run_formal_crawl_target(dependencies, target, ["211", "210"])

    assert failure is None
    assert [(result.issue, result.numbers) for result in results] == [
        ("211", ["01", "02", "03", "04"]),
        ("210", ["05", "06", "07", "08"]),
    ]
    evidence = [result.evidence for result in results]
    assert all(item is not None for item in evidence)
    assert [item.source_url for item in evidence if item is not None] == [
        "https://example.test/topic/211",
        "https://example.test/topic/210",
    ]
