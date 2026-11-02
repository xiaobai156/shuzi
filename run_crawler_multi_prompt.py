import re
import json
import hashlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import crawler
from kill_numbers.infrastructure.cache_format import target_contract_fingerprint
from kill_numbers.infrastructure.file_store import atomic_write_text
from run_lock import exclusive_run_lock


BASE_DIR = Path(__file__).resolve().parent
CRAWLER_FILE = BASE_DIR / "crawler.py"
CRAWLER_WORKERS = 16


@dataclass
class IssueRun:
    issue: str
    returncode: int
    success_file: Path
    failed_file: Path
    current_successes: tuple[tuple[str, str, str], ...] | None = None


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def failure_reasons(failed_file: Path) -> dict[str, str]:
    reasons = {}
    if not failed_file.exists():
        return reasons
    pattern = re.compile(
        r"^(.+?)\s+(?:top|bottom|unknown)\s+(https?://\S+)\s+"
        r"原因：\[[^\]]+\]\s*(.*)$"
    )
    legacy_pattern = re.compile(r"^\[[^\]]+\]\s+(.+?)\s+(https?://\S+)\s+(.*)$")
    for line in failed_file.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line.strip())
        if not match:
            match = legacy_pattern.match(line.strip())
        if not match:
            continue
        name, _url, reason = match.groups()
        reasons[name.strip()] = reason.strip()
    return reasons


def multi_report_file(issues: list[str]) -> Path:
    if len(issues) == 1:
        prefix = f"{issues[0]}期"
    else:
        prefix = f"{issues[0]}-{issues[-1]}期"
    return crawler.RESULTS_DIR / f"{prefix}-杀数字-多期汇总失败.txt"


def write_multi_failure_report(runs: list[IssueRun], report_file: Path) -> list[str]:
    targets = crawler.load_targets()
    active_names = [target.get("name") or target["url"] for target in targets]
    identities = {
        (target.get("name") or target["url"], target["url"], target_contract_fingerprint(target))
        for target in targets
    }
    passed_names: set[str] = set()
    reasons_by_issue: dict[str, dict[str, str]] = {}

    for run in runs:
        if run.current_successes is not None:
            passed_names.update(identity[0] for identity in run.current_successes if identity in identities)
            reasons_by_issue[run.issue] = failure_reasons(run.failed_file)
        else:
            reasons_by_issue[run.issue] = {
                name: "本轮未取得完成凭据，旧成功/失败文件不作为本轮依据" for name in active_names
            }

    all_failed = [name for name in active_names if name not in passed_names]
    lines = [
        "多期汇总失败报告",
        "规则：指定多个期数时，目录只要任意一期成功就不列入本报告。",
        "本报告只列所有指定期数全部失败的目录。",
        "",
        f"指定期数：{', '.join(run.issue for run in runs)}",
        f"全部失败目录数：{len(all_failed)}",
        "",
    ]

    if not all_failed:
        lines.append("无")
    for name in all_failed:
        lines.append(name)
        for run in runs:
            reason = reasons_by_issue.get(run.issue, {}).get(
                name,
                "该期未出现在成功文件中，失败文件也无记录",
            )
            lines.append(f"  {run.issue}期：{reason}")
        lines.append("")

    atomic_write_text(report_file, "\n".join(lines).rstrip() + "\n")
    return all_failed


def run_issue(issue: str) -> IssueRun:
    issue = crawler.normalize_issue(issue)
    result_file, failed_file, _report_file = crawler.output_files_for_issues([issue])
    cmd = [
        sys.executable,
        str(CRAWLER_FILE),
        "--workers",
        str(CRAWLER_WORKERS),
        "--issues",
        issue,
        "--no-cache-update",
    ]
    print()
    print(f"开始抓取 {issue}期")
    current_successes = None
    with tempfile.TemporaryDirectory(prefix="kill-numbers-run-") as temporary:
        receipt = Path(temporary) / "result.json"
        result = subprocess.run([*cmd, "--run-result", str(receipt)], cwd=BASE_DIR)
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            records = payload["successes"]
            if (result.returncode in {0, 1} and payload["issue"] == issue
                    and payload['output_hashes'] == [
                        hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None
                        for path in (result_file, failed_file)
                    ]
                    and isinstance(records, list)
                    and all(isinstance(row, list) and len(row) == 3
                            and all(isinstance(value, str) for value in row) for row in records)):
                current_successes = tuple(tuple(row) for row in records)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if current_successes is None:
        print(f"{issue}期本轮完成凭据缺失或无效，不能使用旧成功文件判定成功。")
    return IssueRun(
        issue=issue,
        returncode=result.returncode if current_successes is not None else 2,
        success_file=Path(result_file),
        failed_file=Path(failed_file),
        current_successes=current_successes,
    )


def _main_unlocked() -> int:
    configure_output_encoding()
    print("请输入多个期数，空格或逗号分隔，例如：187 188 189 190")
    raw = input("期数：").strip()
    issues = crawler.parse_issues(raw)
    if not issues:
        print("没有输入有效期数。")
        return 2

    report_file = multi_report_file(issues)
    runs: list[IssueRun] = []
    all_failed: list[str] = []
    for issue in issues:
        runs.append(run_issue(issue))
        all_failed = write_multi_failure_report(runs, report_file)
        print(f"已刷新多期汇总失败报告：{report_file}", flush=True)

    print()
    print("多期抓取完成。")
    print(f"多期汇总失败报告：{report_file}")
    print(f"全部失败目录数：{len(all_failed)}")
    print("注意：多期模式不会更新 recent_10_cache.json。")
    return 1 if any(run.returncode != 0 for run in runs) else 0


def main() -> int:
    try:
        with exclusive_run_lock(BASE_DIR / ".crawler-multi.lock"):
            return _main_unlocked()
    except (RuntimeError, ValueError, OSError, EOFError) as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
