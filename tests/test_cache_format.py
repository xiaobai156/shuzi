import json
from types import SimpleNamespace

import pytest

from check_duplicates import load_cache_state, validate_cache_data

from kill_numbers.infrastructure.cache_format import (
    build_cache_document,
    failures_from_cache_document,
    records_from_cache_document,
)
from kill_numbers.infrastructure.cache_repository import update_recent_duplicate_cache


def test_build_cache_document_groups_records_by_site_and_keeps_style_metadata():
    records = [
        {
            "name": "告别那时",
            "url": "https://example.test/list.aspx?id=79",
            "issue": "224",
            "numbers": "01,02,03,04",
        },
        {
            "name": "告别那时",
            "url": "https://example.test/list.aspx?id=79",
            "issue": "225",
            "numbers": "05,06,07,08",
        },
        {
            "name": "私房一肖",
            "url": "https://example.test/list.aspx?id=79",
            "issue": "225",
            "numbers": "09,10,11,12",
        },
    ]
    targets = [
        {
            "name": "告别那时",
            "url": "https://example.test/list.aspx?id=79",
            "region": "top",
            "browser_fallback": True,
            "position": "first",
        },
        {
            "name": "私房一肖",
            "url": "https://example.test/list.aspx?id=79",
            "region": "bottom",
        },
    ]

    data = build_cache_document(
        records,
        [],
        recent_count=10,
        targets=targets,
        updated_at="2026-08-13T22:00:00",
    )

    assert data["base_period"] == 225
    assert data["periods"] == 10
    assert data["updated_at"] == "2026-08-13T22:00:00"
    assert data["version"] == 1
    assert len(data["sites"]) == 2
    assert data["sites"][0]["pick"] == "top"
    assert data["sites"][0]["browser"] is True
    assert data["sites"][0]["click_first"] is True
    assert len(data["sites"][0]["contract"]) == 64
    assert data["sites"][0]["fingerprint"] == {
        "225": "05,06,07,08",
        "224": "01,02,03,04",
    }
    assert data["sites"][1]["fingerprint"] == {"225": "09,10,11,12"}

    flattened = records_from_cache_document(data)
    assert sorted(
        (item["name"], item["issue"], item["numbers"])
        for item in flattened
    ) == sorted(
        (item["name"], item["issue"], item["numbers"])
        for item in records
    )


def test_cache_reader_accepts_legacy_records_and_preserves_failures():
    legacy = {
        "version": 1,
        "generated_at": "2026-08-13T22:00:00",
        "recent_count": 10,
        "records": [
            {
                "name": "旧格式站点",
                "url": "https://example.test/topic/1.html",
                "issue": "225",
                "numbers": "01,02,03",
            }
        ],
        "failures": [
            {
                "name": "失败站点",
                "url": "https://example.test/topic/2.html",
                "issue": "225",
                "status": "failed",
                "reason": "页面无当期",
            }
        ],
    }

    assert records_from_cache_document(legacy) == legacy["records"]
    assert failures_from_cache_document(legacy) == legacy["failures"]


def test_realtime_cache_update_writes_new_site_style(tmp_path):
    cache_path = tmp_path / "recent_10_cache.json"
    result = SimpleNamespace(
        name="样式站点",
        url="https://example.test/topic/225.html",
        issue="225期",
        numbers=["01", "02", "03"],
    )

    update_recent_duplicate_cache(
        cache_path,
        [result],
        ["225"],
        recent_count=10,
        targets=[
            {
                "name": "样式站点",
                "url": "https://example.test/topic/225.html",
                "region": "top",
                "count": 3,
            }
        ],
    )

    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "sites" in data
    assert "records" not in data
    assert data["sites"][0]["fingerprint"] == {"225": "01,02,03"}


def test_cache_validation_rejects_changed_target_contract(tmp_path):
    records = [
        {
            "name": "测试站",
            "url": "https://example.test/topic/1.html",
            "issue": "225",
            "numbers": "01,02,03,04,05,06,07,08,09,10",
        }
    ]
    original_target = {
        "name": "测试站",
        "url": "https://example.test/topic/1.html",
        "region": "top",
        "anchor": "原锚点",
        "keywords": ["绝杀10码"],
        "count": 10,
        "special_parser": "identity_article_top_10",
    }
    data = build_cache_document(records, [], 10, targets=[original_target])
    changed_target = {
        **original_target,
        "region": "bottom",
        "anchor": "新锚点",
        "special_parser": "identity_article_bottom_10",
    }

    with pytest.raises(ValueError, match="配置指纹不匹配"):
        validate_cache_data(data, tmp_path / "cache.json", 10, [changed_target])


def test_cache_validation_accepts_matching_target_contract(tmp_path):
    target = {
        "name": "测试站",
        "url": "https://example.test/topic/1.html",
        "region": "top",
        "anchor": "测试站",
        "keywords": ["绝杀10码"],
        "count": 10,
    }
    records = [
        {
            "name": target["name"],
            "url": target["url"],
            "issue": "225",
            "numbers": "01,02,03,04,05,06,07,08,09,10",
        }
    ]
    data = build_cache_document(records, [], 10, targets=[target])

    assert validate_cache_data(data, tmp_path / "cache.json", 10, [target]) == records


@pytest.mark.parametrize(
    ("numbers", "reason"),
    [
        ("01,02,03", "号码数量 3"),
        ("01,02,03,04,05,06,07,08,09,50", "超出01至49"),
        ("01,02,03,04,05,06,07,08,09,09", "号码有重复"),
    ],
)
def test_cache_validation_rejects_numbers_outside_target_contract(
    tmp_path,
    numbers,
    reason,
):
    target = {
        "name": "测试站",
        "url": "https://example.test/topic/1.html",
        "region": "top",
        "anchor": "测试站",
        "keywords": ["绝杀10码"],
        "count": 10,
    }
    data = build_cache_document(
        [{"name": target["name"], "url": target["url"], "issue": "225", "numbers": numbers}],
        [],
        10,
        targets=[target],
    )

    with pytest.raises(ValueError, match=reason):
        validate_cache_data(data, tmp_path / "cache.json", 10, [target])


def test_cache_validation_rejects_base_period_that_disagrees_with_records(tmp_path):
    target = {
        "name": "测试站",
        "url": "https://example.test/topic/1.html",
        "region": "top",
        "anchor": "测试站",
        "count": 3,
    }
    data = build_cache_document(
        [{"name": target["name"], "url": target["url"], "issue": "225", "numbers": "01,02,03"}],
        [],
        10,
        targets=[target],
    )
    data["base_period"] = 999

    with pytest.raises(ValueError, match="base_period 与实际期数不一致"):
        validate_cache_data(data, tmp_path / "cache.json", 10, [target])


def test_cache_validation_allows_explicit_single_issue_count_exception(tmp_path):
    target = {
        "name": "特例站",
        "url": "https://example.test/topic/1.html",
        "region": "top",
        "anchor": "特例站",
        "count": 10,
        "cache_issue_exceptions": {
            "229": {
                "approved": True,
                "count": 8,
                "reason": "用户明确授权该期直接写入8码",
            }
        },
    }
    records = [
        {
            "name": target["name"],
            "url": target["url"],
            "issue": "229",
            "numbers": "10,11,12,22,23,24,34,46",
        }
    ]
    data = build_cache_document(records, [], 10, targets=[target])

    assert validate_cache_data(data, tmp_path / "cache.json", 10, [target]) == records


def test_cache_validation_accepts_failure_only_active_site(tmp_path):
    target = {
        "name": "失败站",
        "url": "https://example.test/topic/1.html",
        "region": "top",
        "anchor": "失败站",
        "count": 10,
    }
    data = build_cache_document(
        [],
        [
            {
                "name": target["name"],
                "url": target["url"],
                "issue": "225",
                "status": "failed",
                "reason": "页面无当期",
            }
        ],
        10,
        targets=[target],
    )

    assert validate_cache_data(data, tmp_path / "cache.json", 10, [target]) == []


def test_cache_validation_rejects_success_and_failure_for_same_site_issue(tmp_path):
    target = {
        "name": "冲突站",
        "url": "https://example.test/topic/1.html",
        "region": "top",
        "anchor": "冲突站",
        "count": 3,
    }
    data = build_cache_document(
        [
            {
                "name": target["name"],
                "url": target["url"],
                "issue": "225",
                "numbers": "01,02,03",
            }
        ],
        [
            {
                "name": target["name"],
                "url": target["url"],
                "issue": "225",
                "status": "failed",
                "reason": "页面无当期",
            }
        ],
        10,
        targets=[target],
    )

    with pytest.raises(ValueError, match="同时存在成功号码和失败状态"):
        validate_cache_data(data, tmp_path / "cache.json", 10, [target])


def test_disabled_site_history_is_skipped_instead_of_rejecting_cache(tmp_path):
    """停用站点的旧条目历史不能把整份 --from-cache 判死。"""
    enabled = {"name": "在跑站点", "url": "https://a.test/1", "region": "top"}
    archived = {"name": "已停用站点", "url": "https://b.test/2", "region": "bottom"}
    data = build_cache_document(
        [
            {
                "name": "在跑站点",
                "url": enabled["url"],
                "issue": "225",
                "numbers": "01,02,03,04",
            },
            {
                "name": "已停用站点",
                "url": archived["url"],
                "issue": "225",
                "numbers": "05,06,07,08",
            },
        ],
        [
            {
                "name": "已停用站点",
                "url": archived["url"],
                "issue": "226",
                "status": "failed",
                "reason": "页面无当期",
            }
        ],
        10,
        targets=[enabled, archived],
        updated_at="2026-11-06T20:00:00",
    )
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    loaded = validate_cache_data(data, path, 10, [enabled])
    assert [item["name"] for item in loaded] == ["在跑站点"]

    kept, problems = load_cache_state(path, 10, [enabled])
    assert [item.name for item in kept] == ["在跑站点"]
    assert problems == []


def test_cache_validation_still_requires_every_enabled_target(tmp_path):
    enabled = {"name": "在跑站点", "url": "https://a.test/1", "region": "top"}
    data = build_cache_document(
        [{"name": "在跑站点", "url": enabled["url"], "issue": "225", "numbers": "01,02,03,04"}],
        [],
        10,
        targets=[enabled],
        updated_at="2026-11-06T20:00:00",
    )

    with pytest.raises(ValueError, match="缺少启用目标"):
        validate_cache_data(
            data,
            tmp_path / "cache.json",
            10,
            [{"name": "没缓存站点", "url": "https://c.test/3", "region": "top"}],
        )
