import json

import pytest

from kill_numbers.domain.models import CrawlResult
from kill_numbers.infrastructure.cache_format import records_from_cache_document
from kill_numbers.infrastructure.cache_repository import update_recent_duplicate_cache
from kill_numbers.infrastructure.debug_repository import save_debug_page
from kill_numbers.infrastructure.file_store import atomic_write_text
from kill_numbers.infrastructure.output_repository import output_files_for_issues, remove_stale_file
from kill_numbers.infrastructure.target_repository import (
    merge_active_target_data,
    read_target_data,
    write_target_data,
)
from kill_numbers.text_utils import normalize_issue


def test_target_repository_merge_keeps_disabled_entry_in_original_position(tmp_path):
    path = tmp_path / "targets.json"
    write_target_data(
        path,
        [
            {"url": "https://one.test", "name": "一"},
            {"url": "https://off.test", "name": "停用", "disabled": True},
            {"url": "https://two.test", "name": "二"},
        ],
    )

    merge_active_target_data(
        path,
        [
            {"url": "https://one.test", "name": "一新"},
            {"url": "https://two.test", "name": "二新"},
        ],
    )

    assert read_target_data(path) == [
        {"url": "https://one.test", "name": "一新"},
        {"url": "https://off.test", "name": "停用", "disabled": True},
        {"url": "https://two.test", "name": "二新"},
    ]


def test_recent_cache_preserves_existing_order_and_never_rolls_back_latest_issue(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-07-31T00:00:00",
                "recent_count": 10,
                "records": [
                    {"name": "甲", "url": "https://a.test", "issue": "209", "numbers": "01,02"},
                    {"name": "乙", "url": "https://b.test", "issue": "209", "numbers": "03,04"},
                    {"name": "甲", "url": "https://a.test", "issue": "210", "numbers": "05,06"},
                    {"name": "甲", "url": "https://a.test", "issue": "211", "numbers": "07,08"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    update_recent_duplicate_cache(
        path,
        [CrawlResult("https://a.test", "甲", "210", ["11", "12"])],
        ["210"],
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    records = records_from_cache_document(data)
    assert {
        (item["name"], item["url"], item["issue"], item["numbers"])
        for item in records
    } == {
        ("甲", "https://a.test", "209", "01,02"),
        ("乙", "https://b.test", "209", "03,04"),
        ("甲", "https://a.test", "210", "11,12"),
        ("甲", "https://a.test", "211", "07,08"),
    }
    assert data["base_period"] == 211


def test_debug_repository_writes_single_atomic_evidence_document(tmp_path):
    path = save_debug_page(
        tmp_path,
        {"url": "https://example.test/topic/1", "name": "测试"},
        ["211"],
        "测试",
        "原始正文",
        "解析失败",
        normalize_issue,
        atomic_write_text,
    )

    assert path is not None
    assert path.parent == tmp_path
    assert "reason: 解析失败" in path.read_text(encoding="utf-8")


def test_output_repository_keeps_single_and_multi_issue_filenames(tmp_path):
    single = output_files_for_issues(tmp_path, ["211"], normalize_issue, "success.txt", "failed.txt")
    multiple = output_files_for_issues(tmp_path, ["210", "211"], normalize_issue, "success.txt", "failed.txt")

    assert single == (
        str(tmp_path / "211期-杀数字-成功.txt"),
        str(tmp_path / "211期-杀数字-失败.txt"),
        str(tmp_path / "211期报告.txt"),
    )
    assert multiple == (
        str(tmp_path / "210-211期-杀数字-成功.txt"),
        str(tmp_path / "210-211期-杀数字-失败.txt"),
        str(tmp_path / "210-211期报告.txt"),
    )


def test_remove_stale_file_does_not_hide_delete_failure(tmp_path, monkeypatch):
    path = tmp_path / "old-failure.txt"
    path.write_text("旧失败", encoding="utf-8")

    def deny_delete(self, *args, **kwargs):
        raise PermissionError("文件被占用")

    monkeypatch.setattr(type(path), "unlink", deny_delete)

    with pytest.raises(PermissionError, match="文件被占用"):
        remove_stale_file(path)
