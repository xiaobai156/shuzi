import check_duplicates
import pytest
import crawler
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.domain.models import CrawlResult
from kill_numbers.validation.result_validator import evidence_from_source_document


def test_snapshot_is_parsed_once_before_becoming_duplicate_record(monkeypatch):
    calls = []
    snapshot = check_duplicates.TargetSnapshot(
        target={},
        name="测试站",
        url="https://example.test/topic/1",
        documents=[],
        selected_content="",
        available_issues=[],
    )
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
        "count": 3,
    }
    document = make_source_document(
        kind="article",
        url="https://example.test/article.html",
        content="211期 01 02 03",
        priority=100,
    )
    result = CrawlResult(
        target["url"],
        target["name"],
        "211",
        ["01", "02", "03"],
        evidence_from_source_document(target, "211", ["01", "02", "03"], document),
    )
    calls = []

    def fake_crawl(received_target, issues):
        calls.append((received_target, issues))
        return [result], None

    monkeypatch.setattr(crawler, "crawl_one", fake_crawl)
    snapshot = check_duplicates.TargetSnapshot(
        target=target,
        name=target["name"],
        url=target["url"],
        documents=[],
        selected_content="",
        available_issues=[],
    )

    records, failure = check_duplicates.records_from_snapshot(snapshot, ["211"])

    assert failure is None
    assert records[0].numbers == "01,02,03"
    assert calls == [(target, ["211"])]


def test_snapshot_history_parses_each_issue_from_its_own_document():
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
            priority=100,
            metadata={"parseable": True},
        ),
        make_source_document(
            kind="user_forum_topic",
            url="https://example.test/topic/210",
            content="栏目标题\n210期 专属栏目 05 06 07 08",
            priority=100,
            metadata={"parseable": True},
        ),
    ]
    snapshot = check_duplicates.TargetSnapshot(
        target=target,
        name=target["name"],
        url=target["url"],
        documents=documents,
        selected_content="",
        available_issues=["211", "210"],
    )

    records, failure = check_duplicates.records_from_snapshot(snapshot, ["211", "210"])

    assert failure is None
    assert [(record.issue, record.numbers) for record in records] == [
        ("211期", "01,02,03,04"),
        ("210期", "05,06,07,08"),
    ]


def test_acquisition_only_snapshot_uses_dedicated_issue_discovery(monkeypatch):
    target = {
        "url": "https://example.test/list",
        "name": "作茧自缚",
        "special_parser": "zuojianzifu_link_chain",
        "region": "top",
    }
    monkeypatch.setattr(
        crawler,
        "discover_acquisition_only_available_issues",
        lambda received: ["211", "210"] if received is target else [],
    )
    monkeypatch.setattr(
        crawler,
        "fetch_target_documents",
        lambda *_args: pytest.fail("采集专属站点不得进入通用抓取"),
    )

    snapshot = check_duplicates.snapshot_for_target(target)

    assert snapshot.available_issues == ["211", "210"]
    assert snapshot.documents == []
def test_incomplete_duplicate_report_never_claims_all_sites_have_no_duplicates(tmp_path):
    output = tmp_path / "report.txt"
    check_duplicates.write_report_v2(
        output,
        [],
        [],
        {},
        {},
        [],
        [],
        [check_duplicates.CrawlProblem("失败站", "https://failed.test", "网络失败")],
        [],
        ["236"],
        "236",
    )

    report = output.read_text(encoding="utf-8")
    assert "结论：检测未完成" in report
    assert "不能据此判断全部站点无重复" in report
    assert "没有发现达到连续3期以上的同网站重复风险" not in report
