"""只重抓指定失败 TXT 中的站点；不读取或修改缓存。"""
from __future__ import annotations
import argparse, re
from pathlib import Path
import crawler
from kill_numbers.infrastructure.file_store import atomic_write_text
from run_lock import exclusive_run_lock

LINE = re.compile(r"^(?:\[[^]]+\]\s+)?(?P<name>.+?)\s+(?:(?P<region>top|bottom)\s+)?(?P<url>https?://\S+)\s+.*$")

def retry_failed_file(failure_file: Path, issue: str) -> tuple[int, int, int]:
    raw = failure_file.read_bytes() if failure_file.exists() else None
    if raw is None: print(f"失败文件不存在：{failure_file}"); return 0, 0, 2
    text = raw.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    targets = crawler.load_targets(); jobs = {}; unmatched = 0
    for i, original in enumerate(lines):
        m = LINE.match(original.rstrip("\r\n"))
        if not m: unmatched += 1; continue
        found = [t for t in targets if t.get("enabled", True) and str(t.get("name", "")).strip() == m["name"].strip() and str(t.get("url", "")).strip() == m["url"].strip()]
        if m["region"]: found = [t for t in found if crawler.normalize_region(t.get("region")) == m["region"]]
        if len(found) == 1: jobs.setdefault((found[0]["name"], found[0]["url"]), (i, found[0]))
        else: unmatched += 1
    if not jobs: print("没有可重抓的失败站点"); return 0, 0, 2 if unmatched else 0
    keep = set(range(len(lines))); success_count = 0; original_debug = crawler.save_debug_page
    crawler.save_debug_page = lambda *a, **k: None
    try:
        for index, target in jobs.values():
            try:
                results, failure = crawler.crawl_one(target, [issue])
                if failure or not results: continue
                result_file = crawler.RESULTS_DIR / f"{issue}期-杀数字-成功.txt"
                old = result_file.read_text(encoding="utf-8-sig").splitlines() if result_file.exists() else []
                conflict = False
                for result in crawler.dedupe_results(results):
                    line = f"{','.join(result.numbers)} {result.name}"
                    other = [x for x in old if x.endswith(f" {result.name}") and x != line]
                    if other: print(f"同名号码冲突，保留失败：{result.name}"); conflict = True; continue
                    if line not in old: old.append(line); success_count += 1
                if not conflict:
                    atomic_write_text(result_file, "\n".join(old) + "\n")
                    keep.discard(index)
            except Exception as exc: print(f"重抓失败：{target.get('name')}：{exc}")
    finally: crawler.save_debug_page = original_debug
    if len(keep) < len(lines):
        prefix = "\ufeff" if raw.startswith(b"\xef\xbb\xbf") else ""
        atomic_write_text(failure_file, prefix + "".join(lines[i] for i in sorted(keep)))
    status = 0 if not keep else 1
    print(f"定向重抓：{len(jobs)} 个，成功：{success_count} 个，仍失败：{len(keep)} 个，缓存：未修改")
    return len(jobs), success_count, status

def main() -> int:
    p = argparse.ArgumentParser(description="只重抓失败TXT中的站点"); p.add_argument("issue", type=int); a = p.parse_args()
    if a.issue < 1: p.error("期数必须是正整数")
    with exclusive_run_lock(Path(crawler.SCRIPT_DIR) / ".crawler-and-duplicates.lock"):
        return retry_failed_file(crawler.RESULTS_DIR / f"{a.issue}期-杀数字-失败.txt", str(a.issue))[2]

if __name__ == "__main__": raise SystemExit(main())
