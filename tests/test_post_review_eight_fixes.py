from dataclasses import replace
import hashlib
import json
import sys
import time
from pathlib import Path
from threading import Event

import pytest

import check_duplicates
import crawler
from kill_numbers.acquisition import documents as document_module
from kill_numbers.application.crawl_service import run_formal_crawl_target
from kill_numbers.acquisition.browser_pool import BrowserPool
from kill_numbers.acquisition.documents import make_source_document, parseable_documents
from kill_numbers.domain.periods import previous_issue_for_target, rollover_seam_indices
from kill_numbers.parsing.common import extract_issue_numbers
from kill_numbers.parsing.dedicated.site_parsers import (
    normalize_identity_article_current_placeholder,
)
from kill_numbers.parsing.errors import (
    AmbiguousSourceError, NoCandidateError, SourceContractError,
)
from kill_numbers.parsing import registry


def number_row(issue: int, start: int = 1, count: int = 3) -> str:
    values = " ".join(f"{value:02d}" for value in range(start, start + count))
    return f"{issue}期 专属栏目 {values} 开:??"


def target(**extra):
    return {
        "url": "https://example.test/topic/1",
        "name": "测试栏目",
        "keywords": ["专属栏目"],
        "count": 3,
        "region": "top",
        "anchor": "栏目起点",
        "stop_anchor": "栏目终点",
        "issue_position_window": 4,
        **extra,
    }


def source(content: str, *, url: str, priority: int = 100):
    return make_source_document(
        kind="page",
        url=url,
        content=content,
        priority=priority,
        metadata={"parseable": True},
    )


def test_decoded_components_are_independent_and_cannot_form_a_number_row(monkeypatch):
    fragments = [
        "251期 绝杀10码 01 02 03 04 05",
        "06 07 08 09 10 开:??",
    ]
    monkeypatch.setattr(document_module, "decode_strdecode_payloads", lambda _value: fragments)
    docs = document_module._embedded_documents(
        "<html><body>empty</body></html>",
        "https://example.test/page",
        parent_url="",
        visible_priority=100,
        decoded_priority=80,
        inline_priority=70,
        visible_kind="page",
    )
    decoded = [doc for doc in docs if doc.kind.startswith("decoded_")]
    assert [doc.content for doc in decoded] == fragments
    assert all(doc.metadata.get("parseable") for doc in decoded)
    assert not any(doc.kind.endswith("_stream") for doc in docs)

    parsed = crawler.parse_target_document_results(
        parseable_documents(docs),
        {
            "url": "https://example.test/page",
            "name": "解码测试",
            "keywords": ["绝杀10码"],
            "count": 10,
            "region": "top",
        },
        ["251"],
    )
    assert parsed.issue_map == {}


def test_source_contract_failure_cannot_fall_back_to_weaker_document():
    high = source(
        "栏目起点\n" + number_row(215),
        url="https://example.test/high",
        priority=100,
    )
    weak = source(
        "栏目起点\n" + number_row(215, 4) + "\n栏目终点",
        url="https://example.test/weak",
        priority=70,
    )
    with pytest.raises(SourceContractError, match="结束锚点"):
        crawler.parse_target_document_results([high, weak], target(), ["215"])


def test_multiple_documents_merge_per_issue_with_exact_provenance():
    first_issues = list(range(215, 210, -1))
    second_issues = list(range(210, 205, -1))
    first = source(
        "栏目起点\n"
        + "\n".join(number_row(issue, 1) for issue in first_issues)
        + "\n栏目终点",
        url="https://example.test/first",
    )
    second = source(
        "栏目起点\n"
        + "\n".join(number_row(issue, 4) for issue in second_issues)
        + "\n栏目终点",
        url="https://example.test/second",
    )
    history_target = target(_history_discovery=True, _history_depth=10)
    requested = [str(issue) for issue in first_issues + second_issues]
    parsed = crawler.parse_target_document_results(
        [first, second], history_target, requested
    )

    assert list(parsed.issue_map) == requested
    assert all(parsed.source_documents[str(issue)] is first for issue in first_issues)
    assert all(parsed.source_documents[str(issue)] is second for issue in second_issues)
    assert parsed.primary_document is first



def test_formal_pipeline_preserves_per_issue_document_evidence():
    first_issues = [str(issue) for issue in range(215, 210, -1)]
    second_issues = [str(issue) for issue in range(210, 205, -1)]
    first = source(
        "栏目起点\n"
        + "\n".join(number_row(int(issue), 1) for issue in first_issues)
        + "\n栏目终点",
        url="https://example.test/first",
    )
    second = source(
        "栏目起点\n"
        + "\n".join(number_row(int(issue), 4) for issue in second_issues)
        + "\n栏目终点",
        url="https://example.test/second",
    )
    requested = first_issues + second_issues
    history_target = target(
        url="https://example.test/list",
        _history_discovery=True,
        _history_depth=10,
    )
    dependencies = replace(
        crawler._crawl_dependencies(),
        fetch_target_documents=lambda _target, _issues: (
            history_target["name"],
            [first, second],
        ),
    )

    results, failure = run_formal_crawl_target(
        dependencies, history_target, requested
    )

    assert failure is None
    assert [result.issue for result in results] == requested
    assert all(
        result.evidence and result.evidence.source_url == first.url
        for result in results[:5]
    )
    assert all(
        result.evidence and result.evidence.source_url == second.url
        for result in results[5:]
    )

def test_history_depth_expands_only_after_current_window_is_proven():
    issues = list(range(215, 205, -1))
    document = source(
        "栏目起点\n"
        + "\n".join(number_row(issue) for issue in issues)
        + "\n栏目终点",
        url="https://example.test/history",
    )
    history_target = target(_history_discovery=True, _history_depth=10)
    available, selected = crawler.available_issues_for_documents(
        [document], history_target
    )
    assert available == [str(issue) for issue in issues]
    assert selected is document
    parsed = crawler.parse_target_document_results(
        [document], history_target, available
    )
    assert list(parsed.issue_map) == available



def test_user_sequence_history_keeps_current_window_separate_from_history_depth():
    documents = []
    for index, issue in enumerate(range(215, 205, -1)):
        document = source(
            "栏目起点\n" + number_row(issue) + "\n栏目终点",
            url=f"https://example.test/topic/{issue}",
        )
        documents.append(
            document.__class__(
                **{
                    **document.__dict__,
                    "metadata": {
                        **document.metadata,
                        "region_sequence": "topics",
                        "region_index": index,
                    },
                }
            )
        )

    history_target = target(_history_discovery=True, _history_depth=10)
    available, _selected = crawler.available_issues_for_documents(
        documents, history_target
    )
    assert available == [str(issue) for issue in range(215, 205, -1)]

    ranked = crawler._directional_parseable_documents(documents, history_target)
    assert len(ranked) == 10
    assert sum(
        bool(document.metadata.get("current_allowed_row_starts"))
        for document in ranked
    ) == 4
    assert all(document.metadata.get("current_window_verified") for document in ranked)

def test_rebuild_history_requires_cycle_before_any_network(monkeypatch):
    monkeypatch.setattr(
        check_duplicates,
        "fetch_target_snapshots",
        lambda *_args, **_kwargs: pytest.fail("network must not start"),
    )
    monkeypatch.setattr(sys, "argv", ["check_duplicates.py", "--rebuild-history"])
    with pytest.raises(SystemExit) as exc:
        check_duplicates._main_unlocked()
    assert exc.value.code == 2



def test_rebuild_history_rejects_diagnostic_modes_before_network(monkeypatch):
    monkeypatch.setattr(
        check_duplicates,
        "fetch_target_snapshots",
        lambda *_args, **_kwargs: pytest.fail("network must not start"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_duplicates.py",
            "--rebuild-history",
            "--cycle-id",
            "2026",
            "--issues",
            "215",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        check_duplicates._main_unlocked()
    assert exc.value.code == 2


def test_cli_cycle_cannot_override_a_target_cycle_contract():
    with pytest.raises(ValueError, match="cycle_id.*冲突"):
        check_duplicates.effective_targets_with_cycle_contract(
            [
                {
                    "url": "https://example.test",
                    "name": "周期测试",
                    "cycle_id": "2025",
                }
            ],
            "2026",
        )


def test_cycle_length_contracts_cannot_silently_override_each_other():
    with pytest.raises(ValueError, match="期数上限冲突"):
        check_duplicates.effective_targets_with_cycle_contract(
            [
                {
                    "url": "https://example.test",
                    "name": "周期测试",
                    "cycle_lengths": {"2025": 365},
                }
            ],
            "2026",
            {"2025": 366},
        )

def test_non_365_rollover_uses_explicit_period_contract():
    period_target = {
        "cycle_id": "2027",
        "cycle_lengths": {"2026": 362},
    }
    assert previous_issue_for_target("1", period_target) == "362"
    assert rollover_seam_indices(["360", "361", "362", "1", "2"], period_target) == [3]
    assert rollover_seam_indices(["360", "361", "365", "1"], period_target) == []


def test_current_cycle_parser_accepts_explicit_non_365_rollover():
    content = """测试文章 209期
不可或缺 发表于 07月28日
208期:[准杀八码]12.04.42.10.36.11.43.20开鸡09准
209期:[准杀八码]06.19.08.05.36.38.25.16开蛇13准
362期:[准杀八码]28.24.49.11.35.30.02.25开鼠42准
001期:[准杀八码]01.02.03.04.05.06.07.08开鼠42准
208期:[准杀八码]08.20.47.02.35.09.17.27开兔15准
209期:[准杀八码]23.27.08.30.02.14.09.19开蛇13准
上一篇:
"""
    parser_target = {
        "url": "https://example.test/article",
        "name": "不可或缺",
        "anchor": "不可或缺",
        "article_title_anchor": "测试文章",
        "stop_anchor": "上一篇:",
        "keywords": ["准杀八码"],
        "count": 8,
        "region": "top",
        "issue_position_window": 5,
        "cycle_id": "2027",
        "cycle_lengths": {"2026": 362},
    }
    assert crawler.extract_top_article_history_current_cycle_numbers(
        content, ["208", "209"], parser_target
    ) == {
        "208": ["12", "04", "42", "10", "36", "11", "43", "20"],
        "209": ["06", "19", "08", "05", "36", "38", "25", "16"],
    }


def test_issue_one_placeholder_uses_explicit_previous_cycle_length():
    value = normalize_identity_article_current_placeholder(
        """铭记于心
1期:[绝杀10码]测试
362期:《铭记于心》绝杀10码开:49准
[01.02.03.04.05.06.07.08.09.10]
水期:《铭记于心》绝杀10码开:0000准
[11.12.13.14.15.16.17.18.19.20]
""",
        {
            "article_identity": "铭记于心",
            "cycle_id": "2027",
            "cycle_lengths": {"2026": 362},
        },
    )
    assert "1期:《铭记于心》" in value


def test_file_cycles_come_from_each_filename_not_one_global_value(tmp_path):
    old = tmp_path / "2026周期-365期-杀数字-成功.txt"
    new = tmp_path / "2027周期-1期-杀数字-成功.txt"
    old.write_text("01,02,03 甲\n", encoding="utf-8")
    new.write_text("04,05,06 甲\n", encoding="utf-8")
    records, bad = check_duplicates.read_records([old, new])
    assert bad == []
    assert [(record.cycle_id, record.issue) for record in records] == [
        ("2026", "365期"),
        ("2027", "1期"),
    ]


def test_unprefixed_success_file_can_take_cycle_from_verified_manifest(tmp_path):
    success = tmp_path / "215期-杀数字-成功.txt"
    success.write_text("01,02,03 甲\n", encoding="utf-8")
    digest = hashlib.sha256(success.read_bytes()).hexdigest()
    (tmp_path / "215期-杀数字-运行.json").write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": "run",
                "issue": "215",
                "cycle_id": "2026",
                "outputs_finalized": True,
                "files": [{"path": str(success), "sha256": digest}],
                "results": [],
                "failures": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    records, bad = check_duplicates.read_records([success])
    assert bad == []
    assert records[0].cycle_id == "2026"


def test_one_global_cycle_cannot_label_a_rollover_file_set():
    records = [
        check_duplicates.Record("365.txt", 1, "01,02,03", "甲", "365期", ""),
        check_duplicates.Record("1.txt", 1, "01,02,03", "甲", "1期", ""),
    ]
    with pytest.raises(ValueError, match="不能用一个 --cycle-id"):
        check_duplicates.apply_default_cycle_to_unlabelled_records(records, "2027")


def test_browser_pool_times_out_and_retires_hung_worker():
    release = Event()

    def blocked_renderer(_state, _url, _timeout):
        release.wait(5)
        return "body", "html", "https://example.test"

    pool = BrowserPool(
        workers=1,
        renderer=blocked_renderer,
        operation_grace=0.02,
        close_timeout=0.02,
    )
    old_worker = pool.workers[0]
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="浏览器渲染超过"):
            pool.render("https://example.test", 20)
        assert time.monotonic() - started < 1
        assert pool.workers[0] is not old_worker
        pool.close()
    finally:
        release.set()
        old_worker.thread.join(timeout=1)



def test_multiple_anchor_aliases_cannot_hide_conflicting_blocks():
    content = (
        "栏目甲\n" + number_row(215, 1) + "\n栏目终点\n"
        "栏目乙\n" + number_row(215, 4) + "\n栏目终点\n"
    )
    with pytest.raises(AmbiguousSourceError, match="多个不同候选区块"):
        extract_issue_numbers(
            content,
            ["215"],
            keywords=["专属栏目"],
            expected_count=3,
            anchor=["栏目甲", "栏目乙"],
            stop_anchor="栏目终点",
            region="top",
            issue_position_window=4,
        )


def test_browser_pool_close_uses_one_global_deadline_for_retired_workers():
    release = Event()

    def blocked_renderer(_state, _url, _timeout):
        release.wait(2)
        return "body", "html", "https://example.test"

    pool = BrowserPool(
        workers=4,
        renderer=blocked_renderer,
        operation_grace=0.01,
        close_timeout=0.05,
    )
    try:
        from concurrent.futures import ThreadPoolExecutor

        def render_once(_index):
            with pytest.raises(TimeoutError):
                pool.render("https://example.test", 20)

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(render_once, range(4)))
        started = time.monotonic()
        pool.close()
        assert time.monotonic() - started < 0.25
    finally:
        release.set()
        for worker in pool.retired_workers:
            worker.thread.join(timeout=1)

def test_multiple_anchor_blocks_with_different_results_fail_closed():
    content = (
        "栏目起点\n" + number_row(215, 1) + "\n栏目终点\n"
        "栏目起点\n" + number_row(215, 4) + "\n栏目终点\n"
    )
    with pytest.raises(AmbiguousSourceError, match="多个不同候选区块"):
        extract_issue_numbers(
            content,
            ["215"],
            keywords=["专属栏目"],
            expected_count=3,
            anchor="栏目起点",
            stop_anchor="栏目终点",
            region="top",
            issue_position_window=4,
        )



def test_parser_error_classification_does_not_depend_on_message_text(monkeypatch):
    def broken_parser(_content, _issues, _target):
        raise ValueError("arbitrary parser failure text")

    monkeypatch.setitem(
        registry.PARSERS,
        "test_typed_boundary",
        registry.ParserAdapter(broken_parser),
    )
    configured = {
        "special_parser": "test_typed_boundary",
        "keywords": ["专属栏目"],
    }
    with pytest.raises(SourceContractError, match="arbitrary parser failure"):
        registry.parse_target_content(
            "215期 专属栏目 01 02 03",
            configured,
            ["215"],
        )
    with pytest.raises(NoCandidateError, match="arbitrary parser failure"):
        registry.parse_target_content(
            "完全无关的文档",
            configured,
            ["215"],
        )


def test_long_navigation_anchor_is_compared_with_real_section():
    content = (
        "欢迎访问本站 导航入口 栏目起点 更多推荐与友情链接信息\n"
        + number_row(215, 1)
        + "\n栏目终点\n"
        + "栏目起点\n"
        + number_row(215, 4)
        + "\n栏目终点\n"
    )
    with pytest.raises(AmbiguousSourceError, match="多个不同候选区块"):
        extract_issue_numbers(
            content,
            ["215"],
            keywords=["专属栏目"],
            expected_count=3,
            anchor="栏目起点",
            stop_anchor="栏目终点",
            region="top",
            issue_position_window=4,
        )
