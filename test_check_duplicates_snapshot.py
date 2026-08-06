from types import SimpleNamespace

import check_duplicates
import pytest
import crawler
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.domain.models import CrawlResult
from kill_numbers.validation.result_validator import evidence_from_source_document


def test_snapshot_is_parsed_once_before_becoming_duplicate_record(monkeypatch):
    calls = []
    snapshot = SimpleNamespace(url="https://example.test/topic/1")
    record = check_duplicates.Record(
        source_file="实时抓取缓存",
        line_no=0,
        numbers="01,02,03,04",
        name="测试站",
        issue="211期",
        raw="",
        url=snapshot.url,
    )

    def parse_once(received_snapshot, issues):
        calls.append((received_snapshot, issues))
        return [record], None

    monkeypatch.setattr(check_duplicates, "records_from_snapshot", parse_once)

    records, problems = check_duplicates.records_from_snapshots([snapshot], ["211"])

    assert records == [record]
    assert problems == []
    assert calls == [(snapshot, ["211"])]


def test_site_duplicate_matches_rejects_same_site_same_issue_conflict():
    records = [
        check_duplicates.Record(
            source_file="a.txt",
            line_no=1,
            numbers="01,02,03,04",
            name="测试站",
            issue="211期",
            raw="",
            url="https://example.test/topic/1",
        ),
        check_duplicates.Record(
            source_file="b.txt",
            line_no=1,
            numbers="05,06,07,08",
            name="测试站",
            issue="211期",
            raw="",
            url="https://example.test/topic/1",
        ),
    ]

    with pytest.raises(ValueError, match="同站同期冲突"):
        check_duplicates.site_duplicate_matches(records)


def test_multi_issue_file_records_reject_missing_site_issue():
    records = [
        check_duplicates.Record(
            source_file="211期-杀数字-成功.txt",
            line_no=1,
            numbers="01,02,03,04",
            name="测试站",
            issue="211期",
            raw="",
            url="https://example.test/topic/1",
        )
    ]

    problems = check_duplicates.multi_issue_completeness_problems(records, ["210", "211"])

    assert len(problems) == 1
    assert problems[0].reason == "指定多期数据不完整，缺少：210期"


def test_snapshot_chain_parser_reuses_formal_crawl_and_validator(monkeypatch):
    target = {
        "special_parser": "zuojianzifu_link_chain",
        "url": "https://example.test/listing.html",
        "name": "作茧自缚",
        "count": 2,
    }
    document = make_source_document(
        kind="article",
        url="https://example.test/article.html",
        content="211期 01 02",
        priority=100,
    )
    result = CrawlResult(
        target["url"],
        target["name"],
        "211",
        ["01", "02"],
        evidence_from_source_document(target, "211", ["01", "02"], document),
    )
    calls = []

    def fake_crawl(received_target, issues):
        calls.append((received_target, issues))
        return [result], None

    monkeypatch.setattr(crawler, "crawl_one", fake_crawl)
    snapshot = type(
        "Snapshot",
        (),
        {
            "target": target,
            "name": target["name"],
            "url": target["url"],
            "documents": [],
            "selected_content": "",
            "available_issues": [],
        },
    )()

    records, failure = check_duplicates.records_from_snapshot(snapshot, ["211"])

    assert failure is None
    assert records[0].numbers == "01,02"
    assert calls == [(target, ["211"])]
