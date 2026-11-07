from pathlib import Path

import crawler
import pytest
from kill_numbers.domain.models import CrawlFailure, CrawlResult, RunStats
from run_crawler_multi_prompt import failure_reasons


def test_write_outputs_uses_name_region_url_category_and_reason(tmp_path, monkeypatch):
    shared_url = "https://example.test/topic/573668.html"
    monkeypatch.setattr(
        crawler,
        "TARGETS",
        [
            {"name": "另一栏目", "url": shared_url, "region": "top"},
            {"name": "赛码会", "url": shared_url, "region": "bottom"},
            {"name": "锚点站", "url": "https://example.test/topic/2.html", "region": "top"},
        ],
    )
    monkeypatch.setattr(crawler, "write_report", lambda *args, **kwargs: None)

    result_file = tmp_path / "235期-杀数字-成功.txt"
    failed_file = tmp_path / "235期-杀数字-失败.txt"
    report_file = tmp_path / "235期报告.txt"
    failures = [
        CrawlFailure(
            url=shared_url,
            name="赛码会",
            reason="专属解析未命中指定 235期，禁止回落通用解析",
        ),
        CrawlFailure(
            url="https://example.test/topic/2.html",
            name="锚点站",
            reason="没有找到正文锚点：['锚点站']",
        ),
    ]

    crawler.write_outputs(
        [],
        failures,
        str(result_file),
        str(failed_file),
        str(report_file),
        RunStats(2, 0, 0, 0, 1),
        skip_backup=True,
    )

    assert failed_file.read_text(encoding="utf-8").splitlines() == [
        (
            "赛码会 bottom https://example.test/topic/573668.html "
            "原因：[专属解析未命中] 专属解析未命中指定 235期，禁止回落通用解析"
        ),
        (
            "锚点站 top https://example.test/topic/2.html "
            "原因：[锚点失效] 没有找到正文锚点：['锚点站']"
        ),
    ]


def test_multi_issue_failure_reader_accepts_the_new_failure_txt_format(tmp_path: Path):
    failed_file = tmp_path / "235期-杀数字-失败.txt"
    failed_file.write_text(
        "赛码会 bottom https://example.test/topic/573668.html "
        "原因：[专属解析未命中] 专属解析未命中指定 235期，禁止回落通用解析\n",
        encoding="utf-8",
    )

    assert failure_reasons(failed_file) == {
        "赛码会": "专属解析未命中指定 235期，禁止回落通用解析"
    }


def test_write_outputs_only_appends_new_success_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler, "write_report", lambda *args, **kwargs: None)
    result_file = tmp_path / "238期-杀数字-成功.txt"
    failed_file = tmp_path / "238期-杀数字-失败.txt"
    report_file = tmp_path / "238期报告.txt"
    result_file.write_text("01,02 原有站点\n", encoding="utf-8")
    repaired = CrawlResult("https://repair.test", "修复站点", "238", ["03", "04"])

    for _ in range(2):
        crawler.write_outputs(
            [repaired],
            [],
            str(result_file),
            str(failed_file),
            str(report_file),
            RunStats(1, 1, 0, 0, 1),
            skip_backup=True,
        )

    assert result_file.read_text(encoding="utf-8").splitlines() == [
        "01,02 原有站点",
        "03,04 修复站点",
    ]


def test_write_outputs_never_creates_or_modifies_single_issue_report(tmp_path):
    result_file = tmp_path / "240期-杀数字-成功.txt"
    failed_file = tmp_path / "240期-杀数字-失败.txt"
    existing_report = tmp_path / "240期报告.txt"
    existing_report.write_text("原有报告保持不变\n", encoding="utf-8")

    crawler.write_outputs(
        [CrawlResult("https://example.test", "测试站", "240", ["01", "02"])],
        [],
        str(result_file),
        str(failed_file),
        str(existing_report),
        RunStats(1, 1, 0, 0, 1),
        skip_backup=True,
    )

    assert existing_report.read_text(encoding="utf-8") == "原有报告保持不变\n"

    absent_report = tmp_path / "241期报告.txt"
    crawler.write_outputs(
        [CrawlResult("https://example.test", "测试站", "241", ["03", "04"])],
        [],
        str(tmp_path / "241期-杀数字-成功.txt"),
        str(tmp_path / "241期-杀数字-失败.txt"),
        str(absent_report),
        RunStats(1, 1, 0, 0, 1),
        skip_backup=True,
    )

    assert not absent_report.exists()


def test_success_write_failure_keeps_existing_failure_file(tmp_path, monkeypatch):
    """成功 TXT 写失败时不能先把旧失败记录删掉：否则两边一起消失。"""
    result_file = tmp_path / "240期-杀数字-成功.txt"
    failed_file = tmp_path / "240期-杀数字-失败.txt"
    failed_file.write_text("旧失败记录\n", encoding="utf-8")
    real_write = crawler.atomic_write_text

    def flaky_write(path, text):
        if str(path) == str(result_file):
            raise PermissionError("文件被占用")
        return real_write(path, text)

    monkeypatch.setattr(crawler, "atomic_write_text", flaky_write)

    with pytest.raises(PermissionError):
        crawler.write_outputs(
            [CrawlResult("https://example.test", "测试站", "240", ["01", "02"])],
            [],
            str(result_file),
            str(failed_file),
            str(tmp_path / "240期报告.txt"),
            RunStats(1, 1, 0, 0, 1),
            skip_backup=True,
        )

    assert failed_file.read_text(encoding="utf-8") == "旧失败记录\n"


def test_write_outputs_skips_conflicting_site_without_losing_the_round(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(crawler, "write_report", lambda *args, **kwargs: None)
    result_file = tmp_path / "238期-杀数字-成功.txt"
    result_file.write_text("01,02 修复站点\n", encoding="utf-8")
    failed_file = tmp_path / "238期-杀数字-失败.txt"

    conflicts = crawler.write_outputs(
        [
            CrawlResult("https://repair.test", "修复站点", "238", ["03", "04"]),
            CrawlResult("https://healthy.test", "正常站点", "238", ["05", "06"]),
        ],
        [],
        str(result_file),
        str(failed_file),
        str(tmp_path / "238期报告.txt"),
        RunStats(2, 2, 0, 0, 1),
        skip_backup=True,
    )

    assert [item.name for item in conflicts] == ["修复站点"]
    assert "成功TXT已存在同站不同号码" in conflicts[0].reason
    assert crawler.failure_category(conflicts[0]) == "输出冲突"
    # 冲突站停止写入，同轮其它站点照常写入
    assert result_file.read_text(encoding="utf-8") == (
        "01,02 修复站点\n05,06 正常站点\n"
    )
    failure_text = failed_file.read_text(encoding="utf-8")
    assert "修复站点" in failure_text
    assert "[输出冲突]" in failure_text


def test_write_outputs_conflict_never_raises_and_keeps_existing_lines(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(crawler, "write_report", lambda *args, **kwargs: None)
    result_file = tmp_path / "239期-杀数字-成功.txt"
    result_file.write_text("01,02 修复站点\n", encoding="utf-8")

    conflicts = crawler.write_outputs(
        [CrawlResult("https://repair.test", "修复站点", "239", ["03", "04"])],
        [],
        str(result_file),
        str(tmp_path / "239期-杀数字-失败.txt"),
        str(tmp_path / "239期报告.txt"),
        RunStats(1, 1, 0, 0, 1),
        skip_backup=True,
    )

    assert len(conflicts) == 1
    assert result_file.read_text(encoding="utf-8") == "01,02 修复站点\n"
