import json
import sys

import check_duplicates
import crawler
import pytest
from kill_numbers.domain.models import CrawlFailure, CrawlResult
from kill_numbers.infrastructure.cache_format import build_cache_document, records_from_cache_document
from kill_numbers.infrastructure.cache_repository import update_recent_duplicate_cache


@pytest.mark.parametrize(
    ("total_targets", "success_targets", "expected"),
    [
        (20, 15, False),
        (20, 17, True),
        (100, 80, False),
        (100, 81, True),
    ],
)
def test_cache_update_requires_success_rate_above_80_percent(
    total_targets, success_targets, expected
):
    assert (
        crawler.should_update_cache_for_success_rate(total_targets, success_targets)
        is expected
    )


def test_failed_single_period_removes_stale_success_and_records_failure_state(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-08-04T00:00:00",
                "recent_count": 10,
                "records": [
                    {
                        "name": "失败站",
                        "url": "https://failed.test",
                        "issue": "215",
                        "numbers": "01,02",
                    },
                    {
                        "name": "成功站",
                        "url": "https://ok.test",
                        "issue": "215",
                        "numbers": "03,04",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    update_recent_duplicate_cache(
        path,
        [CrawlResult("https://ok.test", "成功站", "215", ["05", "06"])],
        ["215"],
        failures=[CrawlFailure("https://failed.test", "失败站", "指定期数缺失")],
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert records_from_cache_document(data) == [
        {
            "name": "成功站",
            "url": "https://ok.test",
            "issue": "215",
            "numbers": "05,06",
        }
    ]
    assert data["failures"] == [
        {
            "name": "失败站",
            "url": "https://failed.test",
            "issue": "215",
            "status": "failed",
            "reason": "指定期数缺失",
        }
    ]


def test_cache_validation_accepts_failure_state_without_exposing_it_as_numbers(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    data = {
        "version": 1,
        "generated_at": "2026-08-04T00:00:00",
        "recent_count": 10,
        "records": [
            {
                "name": "成功站",
                "url": "https://ok.test",
                "issue": "215",
                "numbers": "05,06",
            }
        ],
        "failures": [
            {
                "name": "失败站",
                "url": "https://failed.test",
                "issue": "215",
                "status": "failed",
                "reason": "指定期数缺失",
            }
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    records = check_duplicates.load_records_cache(path, expected_recent_count=10)

    assert [(record.name, record.issue, record.numbers) for record in records] == [
        ("成功站", "215期", "05,06")
    ]


def test_cache_state_exposes_saved_failures_as_problems(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    data = {
        "version": 1,
        "generated_at": "2026-08-04T00:00:00",
        "recent_count": 10,
        "records": [
            {
                "name": "成功站",
                "url": "https://ok.test",
                "issue": "215",
                "numbers": "05,06",
            }
        ],
        "failures": [
            {
                "name": "失败站",
                "url": "https://failed.test",
                "issue": "215",
                "status": "failed",
                "reason": "指定期数缺失",
            }
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    records, problems = check_duplicates.load_cache_state(path, expected_recent_count=10)

    assert [(record.name, record.issue) for record in records] == [("成功站", "215期")]
    assert problems == [
        check_duplicates.CrawlProblem(
            "失败站",
            "https://failed.test",
            "215期缓存失败：指定期数缺失",
        )
    ]


def test_cache_validation_rejects_failure_state_with_numbers(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-08-04T00:00:00",
                "recent_count": 10,
                "records": [
                    {
                        "name": "成功站",
                        "url": "https://ok.test",
                        "issue": "215",
                        "numbers": "05,06",
                    }
                ],
                "failures": [
                    {
                        "name": "失败站",
                        "url": "https://failed.test",
                        "issue": "215",
                        "status": "failed",
                        "reason": "指定期数缺失",
                        "numbers": "01,02",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        check_duplicates.load_records_cache(path, expected_recent_count=10)
    except ValueError as exc:
        assert "失败状态" in str(exc)
    else:
        raise AssertionError("带号码的失败状态应被拒绝")


def test_cache_write_failure_is_reported_without_replacing_realtime_result(tmp_path, monkeypatch):
    realtime_results = [CrawlResult("https://ok.test", "成功站", "215", ["05", "06"])]
    output = tmp_path / "215期-杀数字-成功.txt"
    output.write_text("05,06 成功站\n", encoding="utf-8")

    def fail_update(*_args, **_kwargs):
        raise OSError("磁盘不可写")

    monkeypatch.setattr(crawler, "update_recent_duplicate_cache", fail_update)

    cache_error = crawler.persist_cache_after_realtime_result(
        crawler.CACHE_FILE,
        realtime_results,
        [],
        ["215"],
    )

    assert realtime_results[0].numbers == ["05", "06"]
    assert cache_error == "磁盘不可写"
    assert output.read_text(encoding="utf-8") == "05,06 成功站\n"


def test_cache_update_rejects_stale_target_contract_without_overwriting(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    original_target = {
        "name": "测试站",
        "url": "https://example.test/topic/1",
        "count": 3,
        "region": "top",
        "anchor": "旧栏目",
    }
    path.write_text(
        json.dumps(
            build_cache_document(
                [
                    {
                        "name": original_target["name"],
                        "url": original_target["url"],
                        "issue": "215",
                        "numbers": "01,02,03",
                    }
                ],
                [],
                10,
                targets=[original_target],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()
    changed_target = {**original_target, "anchor": "新栏目"}

    with pytest.raises(ValueError, match="配置指纹不匹配"):
        update_recent_duplicate_cache(
            path,
            [CrawlResult(changed_target["url"], changed_target["name"], "216", ["04", "05", "06"])],
            ["216"],
            targets=[changed_target],
        )

    assert path.read_bytes() == before


def test_cache_update_rejects_invalid_historical_numbers_without_overwriting(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    target = {
        "name": "测试站",
        "url": "https://example.test/topic/1",
        "count": 3,
        "region": "top",
    }
    path.write_text(
        json.dumps(
            build_cache_document(
                [
                    {
                        "name": target["name"],
                        "url": target["url"],
                        "issue": "215",
                        "numbers": "01,02,50",
                    }
                ],
                [],
                10,
                targets=[target],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()

    with pytest.raises(ValueError, match="历史号码无效"):
        update_recent_duplicate_cache(
            path,
            [CrawlResult(target["url"], target["name"], "216", ["04", "05", "06"])],
            ["216"],
            targets=[target],
        )

    assert path.read_bytes() == before


def test_realtime_pipeline_finalizes_outputs_before_cache_update(tmp_path, monkeypatch):
    target = {
        "url": "https://example.test/topic/215",
        "name": "实时站",
        "keywords": ["专属栏目"],
        "count": 3,
        "region": "top",
        "anchor": "专属栏目",
    }
    result = CrawlResult("https://example.test/topic/215", "实时站", "215", ["01", "02", "03"])
    events = []

    def fake_crawl_targets(*_args, **_kwargs):
        return {0: [result]}, {0: None}

    def fake_write_outputs(*_args, **_kwargs):
        events.append("outputs")

    def fake_persist_cache(*_args, **_kwargs):
        events.append("cache")
        assert events == ["outputs", "cache"]
        return None

    monkeypatch.setattr(crawler, "TARGETS", [target])
    monkeypatch.setattr(crawler, "crawl_targets", fake_crawl_targets)
    monkeypatch.setattr(crawler, "write_outputs", fake_write_outputs)
    monkeypatch.setattr(crawler, "persist_cache_after_realtime_result", fake_persist_cache)
    monkeypatch.setattr(sys, "argv", ["crawler.py", "--issues", "215", "--workers", "1"])

    assert crawler._main_unlocked() == 0
    assert events == ["outputs", "cache"]


def test_realtime_pipeline_leaves_cache_unchanged_at_or_below_80_percent(monkeypatch):
    targets = [
        {
            "url": f"https://example.test/topic/{index}",
            "name": f"实时站{index}",
            "keywords": ["专属栏目"],
            "count": 3,
            "region": "top",
            "anchor": "专属栏目",
        }
        for index in range(20)
    ]
    success_results = {
        index: [
            CrawlResult(
                targets[index]["url"],
                targets[index]["name"],
                "215",
                ["01", "02", "03"],
            )
        ]
        for index in range(15)
    }
    failed_results = {
        index: CrawlFailure(
            targets[index]["url"],
            targets[index]["name"],
            "指定期数缺失",
        )
        for index in range(15, 20)
    }
    events = []

    def fake_crawl_targets(*_args, **_kwargs):
        return success_results, failed_results

    def fake_write_outputs(*_args, **_kwargs):
        events.append("outputs")

    def unexpected_cache_write(*_args, **_kwargs):
        events.append("cache")

    monkeypatch.setattr(crawler, "TARGETS", targets)
    monkeypatch.setattr(crawler, "crawl_targets", fake_crawl_targets)
    monkeypatch.setattr(crawler, "write_outputs", fake_write_outputs)
    monkeypatch.setattr(
        crawler,
        "persist_cache_after_realtime_result",
        unexpected_cache_write,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["crawler.py", "--issues", "215", "--workers", "1", "--retry-passes", "0"],
    )

    assert crawler._main_unlocked() == 1
    assert events == ["outputs"]
