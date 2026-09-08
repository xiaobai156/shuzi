"""只重抓指定失败 TXT 中的站点；不读取或修改缓存。"""
from __future__ import annotations
import argparse, re, os, tempfile
from pathlib import Path
import crawler
from kill_numbers.infrastructure.file_store import atomic_write_text
from run_lock import exclusive_run_lock

def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            tmp = f.name; f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path); tmp = None
    finally:
        if tmp: Path(tmp).unlink(missing_ok=True)

LINE = re.compile(r"^(?:\[[^]]+\]\s+)?(?P<name>.+?)\s+(?:(?P<region>top|bottom)\s+)?(?P<url>https?://\S+)\s+.*$")

def retry_failed_file(failure_file: Path, issue: str) -> tuple[int, int, int]:
    expected = re.search(r"(\d+)期-杀数字-失败\.txt$", failure_file.name)
    if not expected or expected.group(1) != str(issue):
        print("失败文件与期数不一致"); return 0, 0, 2
    raw = failure_file.read_bytes() if failure_file.exists() else None
    if raw is None: print(f"失败文件不存在：{failure_file}"); return 0, 0, 2
    try: text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        print("失败 TXT 编码错误"); return 0, 0, 2
    lines = text.splitlines(keepends=True)
    # 失败记录从站点行开始，到下一个站点行结束；续行随原记录保留/删除。
    blocks = []
    for i, line in enumerate(lines):
        if LINE.match(line.rstrip("\r\n")): blocks.append(i)
    block_for = {}
    for n, start in enumerate(blocks):
        end = blocks[n + 1] if n + 1 < len(blocks) else len(lines)
        for i in range(start, end): block_for[i] = list(range(start, end))
    targets = crawler.load_targets(); jobs = {}; unmatched = 0
    for i, original in enumerate(lines):
        m = LINE.match(original.rstrip("\r\n"))
        if not m:
            if original.strip(): unmatched += 1
            continue
        found = [t for t in targets if t.get("enabled", True) and str(t.get("name", "")).strip() == m["name"].strip() and str(t.get("url", "")).strip() == m["url"].strip()]
        if m["region"]: found = [t for t in found if crawler.normalize_region(t.get("region")) == m["region"]]
        if len(found) == 1:
            key = (found[0]["name"], found[0]["url"], crawler.normalize_region(found[0].get("region")))
            indexes = block_for.get(i, [i])
            if key in jobs: jobs[key][0].extend(x for x in indexes if x not in jobs[key][0])
            else: jobs[key] = (indexes, found[0])
        else:
            if original.strip() and i not in block_for: unmatched += 1
    if not jobs: print("没有可重抓的失败站点"); return 0, 0, 2 if unmatched else 0
    keep = set(range(len(lines))); completed = 0; success_count = 0; removed = False; original_debug = crawler.save_debug_page
    crawler.save_debug_page = lambda *a, **k: None
    try:
        for indexes, target in jobs.values():
            try:
                results, failure = crawler.crawl_one(target, [issue])
                if failure or not results:
                    if failure: print(f"[{target.get('name')}] 本轮失败：{failure.reason}")
                    continue
                result_file = crawler.RESULTS_DIR / f"{issue}期-杀数字-成功.txt"
                old_bytes = result_file.read_bytes() if result_file.exists() else b""
                old = old_bytes.decode("utf-8-sig").splitlines() if old_bytes else []
                conflict = False
                added = []
                for result in crawler.dedupe_results(results):
                    line = f"{','.join(result.numbers)} {result.name}"
                    other = [x for x in old if x.endswith(f" {result.name}") and x != line]
                    if other: print(f"同名号码冲突，保留失败：{result.name}"); conflict = True; continue
                    if line not in old: old.append(line); added.append(line)
                if not conflict:
                    if added:
                        ending = b"\r\n" if b"\r\n" in old_bytes else b"\n"
                        _write_bytes(result_file, old_bytes + ending.join(x.encode() for x in added) + ending)
                    success_count += 1
                    for index in indexes: keep.discard(index)
                    removed = True
                    completed += 1
            except Exception as exc: print(f"重抓失败：{target.get('name')}：{exc}")
    finally: crawler.save_debug_page = original_debug
    if removed:
        if raw != failure_file.read_bytes():
            print("失败 TXT 在处理期间被外部修改，停止写回"); return len(jobs), completed, 1
        _write_bytes(failure_file, (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + "".join(lines[i] for i in sorted(keep)).encode())
    remaining = sum(1 for i in keep if lines[i].strip())
    status = 0 if remaining == 0 else 1
    print(f"定向重抓：{len(jobs)} 个，成功：{completed} 个，仍失败：{remaining} 个，缓存：未修改")
    return len(jobs), success_count, status

def main() -> int:
    p = argparse.ArgumentParser(description="只重抓失败TXT中的站点"); p.add_argument("issue", type=int, nargs="?"); a = p.parse_args()
    if a.issue is None:
        try: a.issue = int(input("请输入期数：").strip())
        except (ValueError, EOFError): p.error("期数必须是正整数")
    if a.issue < 1: p.error("期数必须是正整数")
    with exclusive_run_lock(Path(crawler.SCRIPT_DIR) / ".crawler-and-duplicates.lock"):
        return retry_failed_file(crawler.RESULTS_DIR / f"{a.issue}期-杀数字-失败.txt", str(a.issue))[2]

if __name__ == "__main__": raise SystemExit(main())
