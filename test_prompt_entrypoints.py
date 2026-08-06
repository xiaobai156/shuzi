import inspect

import crawler
import run_crawler_multi_prompt as multi_prompt
import run_crawler_prompt as prompt


def test_single_prompt_no_longer_contains_a_second_cache_or_output_path():
    source = inspect.getsource(prompt)

    assert "sync_recent_cache_from_result_file" not in source
    assert "strip_issue_suffix_from_success_file" not in source
    assert "check_duplicates" not in source


def test_single_prompt_disables_cache_updates_for_multiple_issues():
    assert "--no-cache-update" not in prompt.crawler_command_for_input("187")
    assert "--no-cache-update" in prompt.crawler_command_for_input("187 188")


def test_multi_prompt_uses_atomic_report_write_and_keeps_blank_line_between_sites(tmp_path, monkeypatch):
    monkeypatch.setattr(
        crawler,
        "load_targets",
        lambda: [
            {"name": "甲", "url": "https://one.test"},
            {"name": "乙", "url": "https://two.test"},
            {"name": "丙", "url": "https://three.test"},
        ],
    )
    success_file = tmp_path / "211期-杀数字-成功.txt"
    failed_file = tmp_path / "211期-杀数字-失败.txt"
    report_file = tmp_path / "211期-杀数字-多期汇总失败.txt"
    success_file.write_text("01,02 甲\n", encoding="utf-8")
    failed_file.write_text(
        "[其他失败] 乙 https://two.test 原因乙\n\n"
        "[其他失败] 丙 https://three.test 原因丙\n",
        encoding="utf-8",
    )

    all_failed = multi_prompt.write_multi_failure_report(
        [multi_prompt.IssueRun("211", 1, success_file, failed_file)],
        report_file,
    )

    text = report_file.read_text(encoding="utf-8")
    assert all_failed == ["乙", "丙"]
    assert "原因乙\n\n丙" in text
    assert "atomic_write_text" in inspect.getsource(multi_prompt.write_multi_failure_report)
    assert ".crawler-multi.lock" in inspect.getsource(multi_prompt.main)
