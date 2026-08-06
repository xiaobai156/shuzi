import argparse
import json
import time
from pathlib import Path

import crawler
from kill_numbers.application.batch_service import run_ordered_batch


GOLDEN_FILE = Path(__file__).resolve().parent / "tests" / "golden" / "formal_behavior_210_211.json"


def load_golden() -> dict:
    data = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("黄金基线版本不支持")
    return data


def verify_site(
    target: dict,
    issues: list[str],
    attempts: int = 2,
) -> tuple[str, dict[str, list[str]], str]:
    actual: dict[str, list[str]] = {}
    reason = ""
    for attempt in range(attempts):
        results, failure = crawler.crawl_one(target, issues)
        actual = {crawler.normalize_issue(item.issue): item.numbers for item in results}
        reason = failure.reason if failure else ""
        if not reason:
            break
        if attempt + 1 < attempts:
            time.sleep(1)
    return target["name"], actual, reason


def main() -> int:
    parser = argparse.ArgumentParser(description="只读复验高风险站黄金结果，不写正式输出或缓存。")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers <= 0:
        raise SystemExit("--workers 必须大于 0")

    golden = load_golden()
    expected = golden["results"]
    issues = [crawler.normalize_issue(issue) for issue in golden["issues"]]
    targets = {target["name"]: target for target in crawler.load_targets()}
    missing_targets = [name for name in expected if name not in targets]
    if missing_targets:
        print("配置缺少黄金站点：" + "、".join(missing_targets))
        return 1

    original_save_debug = crawler.save_debug_page
    crawler.save_debug_page = lambda *_args, **_kwargs: None
    try:
        checked = run_ordered_batch(
            expected,
            lambda name: verify_site(targets[name], issues),
            min(args.workers, len(expected)),
        )
    finally:
        crawler.save_debug_page = original_save_debug

    failed = False
    for name, actual, reason in checked:
        wanted = expected[name]
        if reason or actual != wanted:
            failed = True
            print(f"[不一致] {name}")
            print(f"  期望：{wanted}")
            print(f"  实际：{actual}")
            if reason:
                print(f"  失败：{reason}")
        else:
            print(f"[通过] {name}")

    print(f"黄金复验：{'失败' if failed else '通过'} {len(checked)} 个站点")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
