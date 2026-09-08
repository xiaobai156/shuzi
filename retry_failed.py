"""只重抓指定失败 TXT 中的站点；不读取或修改缓存。"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import crawler
from kill_numbers.infrastructure.file_store import atomic_write_text
from run_lock import exclusive_run_lock

LINE = re.compile(r"^(?:\[[^]]+\]\s+)?(?P<name>.+?)\s+(?:(?P<region>top|bottom)\s+)?(?P<url>https?://\S+)\s+.*$")


def retry_failed_file(failure_file: Path, issue: str) -> tuple[int, int]:
    lines = failure_file.read_text(encoding="utf-8").splitlines() if failure_file.exists() else []
    targets = crawler.load_targets()
    selected = []
    for index, line in enumerate(lines):
        match = LINE.match(line.strip())
        if not match:
            continue
        found = [t for t in targets if t.get("enabled", True) and str(t.get("name", "")).strip() == match["name"].strip()
                 and str(t.get("url", "")).strip() == match["url"].strip()
                 and crawler.normalize_region(t.get("region")) == match["region"]]
        if len(found) == 1:
            selected.append((index, found[0]))

    if not selected:
        return 0, 0
    keep = set(range(len(lines)))
    successes: list[str] = []
    for index, target in selected:
        try:
            results, failure = crawler.crawl_one(target, [issue])
            if failure or not results:
                continue
            successes.extend(f"{','.join(r.numbers)} {r.name}" for r in crawler.dedupe_results(results))
            keep.discard(index)
        except Exception:
            continue

    result_file = crawler.RESULTS_DIR / f"{issue}期-杀数字-成功.txt"
    if successes:
        old = result_file.read_text(encoding="utf-8").splitlines() if result_file.exists() else []
        for line in successes:
            if line not in old:
                old.append(line)
        atomic_write_text(result_file, "\n".join(old) + "\n")
    if len(keep) < len(lines):
        atomic_write_text(failure_file, "\n".join(lines[i].rstrip("\r\n") for i in sorted(keep)) + ("\n" if keep else ""))
    return len(selected), len(successes)


def main() -> int:
    parser = argparse.ArgumentParser(description="只重抓失败TXT中的站点")
    parser.add_argument("issue", type=int, help="期数，例如 245")
    args = parser.parse_args()
    issue = str(args.issue)
    failure_file = crawler.RESULTS_DIR / f"{issue}期-杀数字-失败.txt"
    with exclusive_run_lock(Path(crawler.SCRIPT_DIR) / ".crawler-and-duplicates.lock"):
        selected, succeeded = retry_failed_file(failure_file, issue)
    print(f"定向重抓：{selected} 个，成功：{succeeded} 个，缓存：未修改")
    return 0 if selected and succeeded == selected else (1 if selected else 2)


if __name__ == "__main__":
    raise SystemExit(main())

