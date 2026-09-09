"""只重抓指定失败 TXT 中的站点；不读取或修改缓存。"""
from __future__ import annotations
import argparse, re, os, tempfile
from pathlib import Path
import crawler
from kill_numbers.infrastructure.file_store import atomic_write_text
from run_lock import exclusive_run_lock
from kill_numbers.validation.result_validator import validate_crawl_results
from kill_numbers.infrastructure.run_manifest import (
    read_run_manifest_for_retry,
    refresh_run_manifest_after_retry,
)

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
    expected = re.fullmatch(r"(\d+)期-杀数字-失败\.txt", failure_file.name)
    if not expected or expected.group(1) != str(issue):
        print("失败文件与期数不一致"); return 0, 0, 2
    raw = failure_file.read_bytes() if failure_file.exists() else None
    if raw is None: print(f"失败文件不存在：{failure_file}"); return 0, 0, 2
    result_file = crawler.RESULTS_DIR / f"{issue}期-杀数字-成功.txt"
    manifest_file = crawler.manifest_path_for_issue(issue)
    manifest = None
    if manifest_file.is_file():
        try:
            manifest = read_run_manifest_for_retry(manifest_file, issue)
        except ValueError as exc:
            print(f"运行清单已失效，停止重抓以免继续破坏文件证据：{exc}")
            return 0, 0, 2
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
            if original.strip(): unmatched += 1
    if not jobs: print("没有可重抓的失败站点"); return 0, 0, 2 if unmatched else 0
    keep = set(range(len(lines))); completed = 0; success_count = 0; removed = False; original_debug = crawler.save_debug_page
    successful_for_manifest = []
    crawler.save_debug_page = lambda *a, **k: None
    try:
        for indexes, target in jobs.values():
            try:
                results, failure = crawler.crawl_one(target, [issue])
                results, validation_error = validate_crawl_results(target, [issue], results, failure)
                if validation_error:
                    print(f"[{target.get('name')}] 本轮校验失败：{validation_error}")
                    continue
                if failure or not results:
                    if failure: print(f"[{target.get('name')}] 本轮失败：{failure.reason}")
                    continue
                old_bytes = result_file.read_bytes() if result_file.exists() else b""
                old = old_bytes.decode("utf-8-sig").splitlines() if old_bytes else []
                conflict = False
                added = []
                deduped_results = crawler.dedupe_results(results)
                for result in deduped_results:
                    line = f"{','.join(result.numbers)} {result.name}"
                    other = [x for x in old if x.endswith(f" {result.name}") and x != line]
                    if other: print(f"同名号码冲突，保留失败：{result.name}"); conflict = True; continue
                    if line not in old: old.append(line); added.append(line)
                if not conflict:
                    if added:
                        ending = b"\r\n" if b"\r\n" in old_bytes else b"\n"
                        separator = ending if old_bytes and not old_bytes.endswith((b"\r", b"\n")) else b""
                        if result_file.exists() and result_file.read_bytes() != old_bytes:
                            raise OSError("成功TXT在处理期间被修改，停止覆盖")
                        _write_bytes(result_file, old_bytes + separator + ending.join(x.encode() for x in added) + ending)
                    success_count += 1
                    successful_for_manifest.extend(deduped_results)
                    for index in indexes: keep.discard(index)
                    removed = True
                    completed += 1
            except Exception as exc: print(f"重抓失败：{target.get('name')}：{exc}")
    finally: crawler.save_debug_page = original_debug
    if removed:
        if raw != failure_file.read_bytes():
            print("失败 TXT 在处理期间被外部修改，停止写回"); return len(jobs), completed, 1
        try:
            _write_bytes(failure_file, (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + "".join(lines[i] for i in sorted(keep)).encode())
        except OSError as exc:
            print(f"成功 TXT 已保存，失败 TXT 仍保留；写入失败：{exc}")
            return len(jobs), completed, 1
        if manifest is not None:
            try:
                refresh_run_manifest_after_retry(
                    manifest_file,
                    manifest,
                    issue,
                    successful_for_manifest,
                    targets,
                    result_file,
                    failure_file,
                )
            except (OSError, ValueError) as exc:
                print(f"TXT 已更新，但运行清单同步失败；文件模式将拒绝旧证据：{exc}")
                return len(jobs), completed, 1
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

if __name__ == "__main__":
    try: raise SystemExit(main())
    except RuntimeError as exc:
        print(f"锁或运行错误：{exc}"); raise SystemExit(2)
    except OSError as exc:
        print(f"文件读写错误：{exc}"); raise SystemExit(2)
