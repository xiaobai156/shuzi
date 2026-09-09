import subprocess
import sys
from pathlib import Path

import crawler


BASE_DIR = Path(__file__).resolve().parent
CRAWLER_FILE = BASE_DIR / "crawler.py"
CACHE_FILE = "recent_10_cache.json"
CRAWLER_WORKERS = 16


def latest_issue_from_input(raw_issues: str, default_issues: str = "") -> str:
    _ = default_issues  # Kept only for API compatibility; formal runs never default a period.
    issues = crawler.parse_issues(raw_issues)
    if not issues:
        raise ValueError("必须明确输入单一期数，禁止使用默认期数")
    return issues[-1]


def crawler_command_for_input(raw_issues: str) -> list[str]:
    issues = crawler.parse_issues(raw_issues)
    if not issues:
        raise ValueError("必须明确输入单一期数，禁止使用默认期数")
    if len(issues) != 1:
        raise ValueError("单期入口只允许一个期数；多个期数请使用多期入口")
    return [sys.executable, str(CRAWLER_FILE), "--workers", str(CRAWLER_WORKERS), "--issues", issues[0]]


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    configure_output_encoding()
    print("请输入要爬取的单一期数，例如：124；多个期数请使用多期入口")
    print("期数不能为空；为避免抓错期，不提供默认期数。")
    issues = input("期数：").strip()
    try:
        cmd = crawler_command_for_input(issues)
        crawl_issues = crawler.parse_issues(issues)
        latest_issue = crawl_issues[0]
    except ValueError as exc:
        print(exc)
        return 2

    print()
    print("正在启动...")
    result = subprocess.run(cmd, cwd=BASE_DIR)
    if result.returncode == crawler.CACHE_UPDATE_FAILED_EXIT_CODE:
        print()
        print("实时抓取结果已完成，但缓存更新未完成；成功/失败 TXT 已保留。")
        return result.returncode
    if result.returncode != 0:
        print()
        print("本轮存在实时抓取失败；已保留成功/失败 TXT，并记录可用缓存状态。")
        return result.returncode

    result_file = Path(crawler.output_files_for_issues(crawl_issues)[0])
    if not result_file.exists() or result_file.stat().st_size == 0:
        print()
        print(f"成功文件不存在或为空，已停止：{result_file}")
        return 1

    print()
    print("缓存状态以 crawler.py 输出为准；只有完整、周期明确的近10期缓存可用于正式判重。")

    print()
    print("运行结束，请查看 N期-杀数字-成功.txt；如有失败，再查看 N期-杀数字-失败.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
