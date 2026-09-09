import json
from pathlib import Path

import pytest

import check_duplicates
import crawler
import run_crawler_prompt
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.domain.models import CrawlFailure, CrawlResult
from kill_numbers.infrastructure.cache_repository import update_recent_duplicate_cache
from kill_numbers.parsing.common import extract_issue_numbers, find_number_groups
from kill_numbers.validation.result_validator import (
    evidence_from_source_document,
    validate_crawl_results,
)


def _numbers(start: int, count: int = 3) -> list[str]:
    return [f"{value:02d}" for value in range(start, start + count)]


def test_configured_top_window_is_used_instead_of_fixed_three():
    content = "栏目起点\n" + "\n".join(
        f"{issue}期 专属栏目 {' '.join(_numbers(index * 3 + 1))}"
        for index, issue in enumerate([215, 214, 213, 212, 211])
    ) + "\n栏目终点"

    found = extract_issue_numbers(
        content,
        ["211"],
        keywords=["专属栏目"],
        expected_count=3,
        strict_ambiguous=True,
        anchor="栏目起点",
        stop_anchor="栏目终点",
        region="top",
        issue_position_window=5,
    )
    assert found["211"] == _numbers(13)

    missing = extract_issue_numbers(
        content,
        ["211"],
        keywords=["专属栏目"],
        expected_count=3,
        strict_ambiguous=True,
        anchor="栏目起点",
        stop_anchor="栏目终点",
        region="top",
        issue_position_window=4,
    )
    assert missing == {}


def test_invalid_token_invalidates_entire_number_group():
    assert find_number_groups("01 02 03 04 05 06 07 08 09 10 50") == []
    assert find_number_groups("00 01 02 03 04 05 06 07 08 09 10") == []


def test_configured_stop_anchor_is_required():
    with pytest.raises(ValueError, match="没有找到结束锚点"):
        extract_issue_numbers(
            "栏目起点\n211期 专属栏目 01 02 03",
            ["211"],
            keywords=["专属栏目"],
            expected_count=3,
            anchor="栏目起点",
            stop_anchor="栏目终点",
            region="top",
        )


def test_evidence_cannot_fall_back_to_whole_document_when_anchor_is_missing():
    target = {
        "url": "https://example.test/topic/1",
        "name": "测试站",
        "count": 3,
        "keywords": ["专属栏目"],
        "anchor": "不存在的栏目锚点",
        "region": "top",
    }
    document = make_source_document(
        kind="page",
        url=target["url"],
        content="211期 专属栏目 01 02 03",
        priority=100,
    )
    with pytest.raises(ValueError, match="来源锚点"):
        evidence_from_source_document(target, "211", ["01", "02", "03"], document)


def test_normalized_result_keeps_matching_evidence():
    target = {
        "url": "https://example.test/topic/1",
        "name": "测试站",
        "count": 3,
    }
    document = make_source_document(
        kind="page",
        url=target["url"],
        content="211期 01 02 03",
        priority=100,
    )
    evidence = evidence_from_source_document(target, "211", ["01", "02", "03"], document)
    accepted, reason = validate_crawl_results(
        target,
        ["211"],
        [CrawlResult(target["url"], target["name"], "211", ["1", "2", "3"], evidence)],
    )
    assert reason == ""
    assert accepted[0].numbers == ["01", "02", "03"]
    assert accepted[0].evidence is evidence


def test_cache_retention_is_per_site_not_global_issue(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    records = []
    timeline = []
    sequence = 0
    for name, url, first in [
        ("站点A", "https://a.test", 211),
        ("站点B", "https://b.test", 206),
    ]:
        for issue in range(first, first + 10):
            sequence += 1
            records.append(
                {"name": name, "url": url, "issue": str(issue), "numbers": "01,02,03"}
            )
            timeline.append(
                {
                    "name": name,
                    "url": url,
                    "issue": str(issue),
                    "status": "success",
                    "sequence": sequence,
                    "cycle": 0,
                }
            )
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "generated_at": "2026-09-09T00:00:00",
                "recent_count": 10,
                "run_sequence": sequence,
                "records": records,
                "failures": [],
                "timeline": timeline,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    update_recent_duplicate_cache(
        path,
        [CrawlResult("https://a.test", "站点A", "221", ["04", "05", "06"])],
        ["221"],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    b_issues = sorted(
        int(item["issue"]) for item in data["records"] if item["name"] == "站点B"
    )
    assert b_issues == list(range(206, 216))


def test_cache_success_clears_old_failure_and_same_run_conflict_is_rejected(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "generated_at": "2026-09-09T00:00:00",
                "recent_count": 10,
                "run_sequence": 1,
                "records": [],
                "failures": [
                    {
                        "name": "站点A",
                        "url": "https://a.test",
                        "issue": "211",
                        "status": "failed",
                        "reason": "旧失败",
                    }
                ],
                "timeline": [
                    {
                        "name": "站点A",
                        "url": "https://a.test",
                        "issue": "211",
                        "status": "failed",
                        "sequence": 1,
                        "cycle": 0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    update_recent_duplicate_cache(
        path,
        [CrawlResult("https://a.test", "站点A", "211", ["01", "02", "03"])],
        ["211"],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["failures"] == []
    assert data["records"][0]["numbers"] == "01,02,03"

    with pytest.raises(ValueError, match="同时返回成功和失败"):
        update_recent_duplicate_cache(
            path,
            [CrawlResult("https://a.test", "站点A", "212", ["01", "02", "03"])],
            ["212"],
            failures=[CrawlFailure("https://a.test", "站点A", "本轮失败")],
        )


def test_formal_cache_validation_requires_full_ten_periods(tmp_path):
    path = tmp_path / "cache.json"
    records = [
        {"name": "站点A", "url": "https://a.test", "issue": str(issue), "numbers": "01,02,03"}
        for issue in range(203, 212)
    ]
    timeline = [
        {
            "name": "站点A",
            "url": "https://a.test",
            "issue": str(issue),
            "status": "success",
            "sequence": index,
            "cycle": 0,
        }
        for index, issue in enumerate(range(203, 212), start=1)
    ]
    data = {
        "version": 2,
        "generated_at": "2026-09-09T00:00:00",
        "recent_count": 10,
        "run_sequence": 9,
        "records": records,
        "failures": [],
        "timeline": timeline,
    }
    with pytest.raises(ValueError, match="近10期不完整"):
        check_duplicates.validate_cache_data(
            data,
            path,
            expected_recent_count=10,
            targets=[
                {
                    "name": "站点A",
                    "url": "https://a.test",
                    "count": 3,
                    "region": "top",
                }
            ],
        )


def test_current_success_file_format_uses_issue_from_filename(tmp_path):
    path = tmp_path / "211期-杀数字-成功.txt"
    path.write_text("01,02,03 测试站\n", encoding="utf-8")
    records, bad = check_duplicates.read_records([path])
    assert bad == []
    assert len(records) == 1
    assert records[0].issue == "211期"
    assert records[0].name == "测试站"


def test_single_prompt_rejects_multiple_issues():
    with pytest.raises(ValueError, match="单期入口只允许一个期数"):
        run_crawler_prompt.crawler_command_for_input("211,212")
