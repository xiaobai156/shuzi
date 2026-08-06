import subprocess
import sys
from pathlib import Path

import crawler


BASE_DIR = Path(__file__).resolve().parent
CRAWLER_FILE = BASE_DIR / "crawler.py"
CACHE_FILE = "recent_10_cache.json"
CRAWLER_WORKERS = 16


def latest_issue_from_input(raw_issues: str, default_issues: str) -> str:
    issues = crawler.parse_issues(raw_issues or default_issues)
    if not issues:
        raise ValueError("没有可用期数")
    return issues[-1]


def crawler_command_for_input(raw_issues: str) -> list[str]:
    cmd = [sys.executable, str(CRAWLER_FILE), "--workers", str(CRAWLER_WORKERS)]
    if raw_issues:
        cmd.extend(["--issues", raw_issues])
        if len(crawler.parse_issues(raw_issues)) > 1:
            cmd.append("--no-cache-update")
    return cmd


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    configure_output_encoding()
    print("请输入要爬取的期数，多个期数用逗号分隔，例如：124 或 123,124")
    print("直接回车则使用 crawler.py 默认期数。")
    issues = input("期数：").strip()
    crawl_issues = crawler.parse_issues(issues or crawler.DEFAULT_ISSUES)
    latest_issue = latest_issue_from_input(issues, crawler.DEFAULT_ISSUES)

    cmd = crawler_command_for_input(issues)

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
    print(f"{CACHE_FILE} 已由 crawler.py 同步：最新期 {latest_issue}，保留近10期。")

    print()
    print("运行结束，请查看 N期-杀数字-成功.txt；如有失败，再查看 N期-杀数字-失败.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
