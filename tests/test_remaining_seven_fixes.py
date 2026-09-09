import json
import sys
from pathlib import Path

import pytest

import check_duplicates
import crawler
import retry_failed
import run_crawler_prompt
from check_duplicates import Record
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.domain.models import CrawlFailure, CrawlResult
from kill_numbers.domain.periods import target_identity
from kill_numbers.infrastructure.cache_repository import target_signature
from kill_numbers.infrastructure.run_manifest import (
    read_run_manifest_for_retry,
    write_run_manifest,
)
from kill_numbers.validation.duplicate_gate import completeness_reasons


def site(name, url, **extra):
    return {
        "url": url,
        "name": name,
        "keywords": ["专属栏目"],
        "count": 3,
        "region": "top",
        "anchor": "专属栏目",
        "issue_position_window": 5,
        **extra,
    }


def records_for(target, start, end, cycle="2026"):
    return [
        Record(
            source_file="test",
            line_no=0,
            numbers="01,02,03",
            name=target["name"],
            issue=f"{issue}期",
            raw="",
            url=target["url"],
            cycle_id=cycle,
            target_id=target_identity(target),
        )
        for issue in range(start, end + 1)
    ]


def test_pairwise_completeness_requires_three_consecutive_common_periods():
    left = site("甲", "https://a.test")
    right = site("乙", "https://b.test")
    records = records_for(left, 1, 10) + records_for(right, 9, 18)
    reasons = completeness_reasons(records, [left, right])
    assert any("缺少连续3个共同期号" in reason for _name, _url, reason in reasons)


def test_three_consecutive_common_periods_are_enough_for_pair_comparison():
    left = site("甲", "https://a.test")
    right = site("乙", "https://b.test")
    records = records_for(left, 1, 10) + records_for(right, 8, 17)
    assert completeness_reasons(records, [left, right]) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_class", "content"),
        ("allowed_source_types", ["decoded_script"]),
        ("browser", True),
        ("browser_ready_selector", "#ready"),
        ("browser_fallback", True),
        ("position", "last"),
        ("section_id", "section-1"),
        ("pagination_next_text", "下一页"),
        ("max_response_bytes", 10 * 1024 * 1024),
    ],
)
def test_source_contract_fields_change_cache_signature(field, value):
    base = site("甲", "https://a.test")
    assert target_signature(base) != target_signature({**base, field: value})


def test_allowed_source_types_excludes_conflicting_page_candidate():
    page = make_source_document(
        kind="page",
        url="https://a.test",
        content="专属栏目\n215期 专属栏目 01 02 03",
        priority=100,
        metadata={"parseable": True},
    )
    decoded = make_source_document(
        kind="decoded_script_component",
        url="https://a.test/data.js#decoded-1",
        parent_url="https://a.test/data.js",
        content="专属栏目\n215期 专属栏目 04 05 06",
        priority=75,
        metadata={"parseable": True},
    )
    target = site(
        "甲",
        "https://a.test",
        allowed_source_types=["decoded_script"],
    )
    parsed = crawler.parse_target_document_results([page, decoded], target, ["215"])
    assert parsed.issue_map == {"215": ["04", "05", "06"]}
    assert parsed.source_documents["215"] is decoded


def test_browser_true_uses_only_real_browser_documents(monkeypatch):
    target = site("甲", "https://a.test", browser=True)
    browser_doc = make_source_document(
        kind="browser_body",
        url=target["url"],
        content="专属栏目\n215期 专属栏目 01 02 03",
        priority=60,
        metadata={"parseable": True},
    )
    monkeypatch.setattr(crawler, "render_page_documents", lambda _url: [browser_doc])
    monkeypatch.setattr(
        crawler,
        "discover_static_documents",
        lambda _url: pytest.fail("browser=true must not use static HTTP discovery"),
    )
    _name, documents = crawler.fetch_target_documents(target, ["215"])
    assert documents == [browser_doc]


def test_browser_content_class_ignores_plain_body_and_uses_dom():
    target = site(
        "甲",
        "https://a.test",
        browser=True,
        content_class="content",
        anchor="甲",
    )
    body = make_source_document(
        kind="browser_body",
        url=target["url"],
        content="甲\n215期 专属栏目 09 10 11",
        priority=60,
        metadata={"parseable": True},
    )
    dom = make_source_document(
        kind="browser_dom",
        url=target["url"],
        content='甲<div class="content">215期 专属栏目 01 02 03</div>',
        priority=50,
        metadata={"parseable": True},
    )
    parsed = crawler.parse_target_document_results([body, dom], target, ["215"])
    assert parsed.issue_map == {"215": ["01", "02", "03"]}
    assert parsed.source_documents["215"] is dom


def test_target_loader_rejects_unknown_source_type_and_string_browser(tmp_path):
    base = site("甲", "https://a.test")
    for broken in (
        {**base, "allowed_source_types": ["made-up-source"]},
        {**base, "browser": "true"},
    ):
        path = tmp_path / "targets.json"
        path.write_text(json.dumps([broken], ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError):
            crawler.load_targets(path)


def test_formal_single_period_entry_has_no_default_period(monkeypatch):
    assert crawler.DEFAULT_ISSUES == ""
    with pytest.raises(ValueError, match="必须明确输入"):
        run_crawler_prompt.crawler_command_for_input("")
    monkeypatch.setattr(sys, "argv", ["crawler.py"])
    assert crawler._main_unlocked() == 2


def test_retry_refreshes_manifest_hashes_and_site_state(tmp_path, monkeypatch):
    target = site("甲", "https://a.test", cycle_id="2026")
    monkeypatch.setattr(crawler, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(crawler, "load_targets", lambda: [target])

    success_file = tmp_path / "215期-杀数字-成功.txt"
    failure_file = tmp_path / "215期-杀数字-失败.txt"
    failure_file.write_text(
        f"[其他失败] {target['name']} {target['url']} 网络失败\n",
        encoding="utf-8",
    )
    manifest_file = tmp_path / "215期-杀数字-运行.json"
    write_run_manifest(
        manifest_file,
        "run-1",
        "215",
        [],
        [CrawlFailure(target["url"], target["name"], "网络失败")],
        [target],
        success_file,
        failure_file,
    )
    monkeypatch.setattr(crawler, "manifest_path_for_issue", lambda _issue: manifest_file)

    result = CrawlResult(
        url=target["url"],
        name=target["name"],
        issue="215",
        numbers=["01", "02", "03"],
    )
    monkeypatch.setattr(crawler, "crawl_one", lambda *_args: ([result], None))
    monkeypatch.setattr(
        retry_failed,
        "validate_crawl_results",
        lambda _target, _issues, results, _failure: (results, ""),
    )

    assert retry_failed.retry_failed_file(failure_file, "215") == (1, 1, 0)
    refreshed = read_run_manifest_for_retry(manifest_file, "215")
    assert [item["name"] for item in refreshed["results"]] == ["甲"]
    assert refreshed["failures"] == []
    assert refreshed["files"][0]["sha256"]


def _link_document(url, rows, next_href=None):
    links = "".join(
        f'<a href="/{issue}.html">{issue}期 {label}</a>'
        for issue, label in rows
    )
    if next_href:
        links += f'<a href="{next_href}">下一页</a>'
    return make_source_document(
        kind="page",
        url=url,
        content=links,
        priority=100,
        metadata={"parseable": True},
    )


def test_acquisition_only_history_discovers_zuojianzifu_links(monkeypatch):
    target = {
        **site("作茧自缚", "https://a.test/list"),
        "special_parser": "zuojianzifu_link_chain",
        "_history_discovery": True,
        "_history_depth": 10,
    }
    document = _link_document(
        target["url"],
        [(issue, "作茧自缚 杀特十码") for issue in range(215, 205, -1)],
    )
    monkeypatch.setattr(crawler, "discover_static_documents", lambda _url: ("列表", [document]))
    assert crawler.available_issues_for_acquisition_target(target) == [
        str(issue) for issue in range(215, 205, -1)
    ]


def test_acquisition_only_current_window_stays_strict(monkeypatch):
    target = {
        **site("作茧自缚", "https://a.test/list"),
        "special_parser": "zuojianzifu_link_chain",
        "issue_position_window": 3,
    }
    document = _link_document(
        target["url"],
        [(issue, "作茧自缚 杀特十码") for issue in range(215, 205, -1)],
    )
    monkeypatch.setattr(crawler, "discover_static_documents", lambda _url: ("列表", [document]))
    assert crawler.available_issues_for_acquisition_target(target) == ["215", "214", "213"]
    history_target = {**target, "_history_discovery": True, "_history_depth": 10}
    assert crawler.available_issues_for_acquisition_target(history_target) == [
        str(issue) for issue in range(215, 205, -1)
    ]


def test_acquisition_only_history_uses_current_ttss_identity_article(monkeypatch):
    target = {
        "url": "https://a.test/list?page=1",
        "name": "告别那时",
        "link_keywords": ["告别那时", "【绝杀10码】"],
        "keywords": ["绝杀10码"],
        "count": 10,
        "region": "top",
        "anchor": "告别那时",
        "article_identity": "告别那时",
        "issue_position_window": 5,
        "special_parser": "ttss_paginated_identity_top_10",
        "pagination_limit": 3,
        "pagination_next_text": "下一页",
        "_history_discovery": True,
        "_history_depth": 10,
    }
    article_url = "https://a.test/article?id=current"
    page1 = make_source_document(
        kind="page",
        url=target["url"],
        content=(
            "<a href='/article?id=current'>"
            "215期: 告别那时【绝杀10码】已免费公开</a>"
        ),
        priority=100,
        metadata={"parseable": True},
    )
    rows = "".join(
        f"<p>{issue}期 告别那时 : 【01,02,03,04,05,06,07,08,09,"
        f"{10 + (215 - issue):02d}】 开 ?? 准</p>"
        for issue in range(215, 205, -1)
    )
    article = make_source_document(
        kind="page",
        url=article_url,
        content="<h1>215期: 告别那时【绝杀10码】已免费公开</h1>" + rows,
        priority=100,
        metadata={"parseable": True},
    )
    pages = {target["url"]: page1, article_url: article}
    monkeypatch.setattr(
        crawler,
        "discover_static_documents",
        lambda url: ("列表", [pages[url]]),
    )

    assert crawler.available_issues_for_acquisition_target(target) == [
        str(issue) for issue in range(215, 205, -1)
    ]

def test_snapshot_for_acquisition_only_does_not_call_generic_fetch(monkeypatch):
    target = {
        **site("作茧自缚", "https://a.test/list"),
        "special_parser": "zuojianzifu_link_chain",
    }
    monkeypatch.setattr(
        crawler,
        "available_issues_for_acquisition_target",
        lambda _target: [str(issue) for issue in range(215, 205, -1)],
    )
    monkeypatch.setattr(
        check_duplicates,
        "fetch_target_content",
        lambda _target: pytest.fail("acquisition-only snapshots must not call generic fetch"),
    )
    snapshot = check_duplicates._snapshot_for_target(target)
    assert snapshot.documents == []
    assert snapshot.available_issues == [str(issue) for issue in range(215, 205, -1)]


def test_from_files_trims_each_site_to_its_own_recent_ten():
    left = site("甲", "https://a.test")
    right = site("乙", "https://b.test")
    records = records_for(left, 1, 12) + records_for(right, 3, 12)
    trimmed = check_duplicates.trim_records_to_recent_site_windows(records, 10)
    left_issues = [int(record.issue[:-1]) for record in trimmed if record.name == "甲"]
    right_issues = [int(record.issue[:-1]) for record in trimmed if record.name == "乙"]
    assert left_issues == list(range(3, 13))
    assert right_issues == list(range(3, 13))
    assert completeness_reasons(trimmed, [left, right]) == []


def test_add_urls_also_binds_stable_target_identity(monkeypatch):
    target = site("甲", "https://a.test")
    record = Record("x", 1, "01,02,03", "甲", "215期", "")
    monkeypatch.setattr(check_duplicates, "load_target_urls", lambda: [target])
    check_duplicates.add_urls([record])
    assert record.url == target["url"]
    assert record.target_id == target_identity(target)
