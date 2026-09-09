import re
import uuid
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import crawler
from kill_numbers.infrastructure.file_store import atomic_write_text
from run_lock import exclusive_run_lock
from kill_numbers.infrastructure.run_manifest import read_run_manifest
from kill_numbers.infrastructure.cache_repository import target_signature
from kill_numbers.domain.periods import canonical_url, target_identity


BASE_DIR = Path(__file__).resolve().parent
CRAWLER_FILE = BASE_DIR / "crawler.py"
CRAWLER_WORKERS = 16


@dataclass
class IssueRun:
    issue: str
    returncode: int
    success_file: Path
    failed_file: Path
    manifest: dict | None = None
    manifest_error: str = ""


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def success_names(result_file: Path) -> set[str]:
    names = set()
    if not result_file.exists():
        return names
    for line in result_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            names.add(parts[1].strip())
    return names


def failure_reasons(failed_file: Path) -> dict[str, str]:
    reasons = {}
    if not failed_file.exists():
        return reasons
    pattern = re.compile(r"^\[[^\]]+\]\s+(.+?)\s+(https?://\S+)\s+(.*)$")
    for line in failed_file.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line.strip())
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
    by_url = {target_identity(t): t for t in targets}
    passed_names = set()
    reasons_by_issue = {}
    for run in runs:
        reasons = {}
        if run.manifest is not None:
            for result in run.manifest['results']:
                target = by_url.get(result.get('target_id'))
                if target and result['contract_hash'] == target_signature(target) and result['name'] == target['name']:
                    passed_names.add(target['name'])
            for failure in run.manifest['failures']:
                target = by_url.get(failure.get('target_id'))
                if target:
                    reasons[target['name']] = failure['reason']
        else:
            reasons = {name: run.manifest_error or '本轮输出未定稿，旧成功文件不参与汇总' for name in active_names}
        reasons_by_issue[run.issue] = reasons
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
    run_id = uuid.uuid4().hex
    manifest_file = crawler.manifest_path_for_issue(issue)
    cmd = [
        sys.executable,
        str(CRAWLER_FILE),
        "--workers",
        str(CRAWLER_WORKERS),
        "--issues",
        issue,
        "--no-cache-update",
        "--run-id", run_id,
        "--manifest", str(manifest_file),
    ]
    print()
    print(f"开始抓取 {issue}期")
    result = subprocess.run(cmd, cwd=BASE_DIR)
    result_file, failed_file, _report_file = crawler.output_files_for_issues([issue])
    manifest, manifest_error = None, ""
    try:
        if result.returncode not in (0, 1, crawler.CACHE_UPDATE_FAILED_EXIT_CODE):
            raise ValueError(f"子进程未完成（退出码 {result.returncode}）")
        manifest = read_run_manifest(manifest_file, run_id, issue)
    except ValueError as exc:
        manifest_error = str(exc)
    return IssueRun(
        issue=issue,
        returncode=result.returncode if manifest is not None else (result.returncode or 2),
        success_file=Path(result_file),
        failed_file=Path(failed_file),
        manifest=manifest, manifest_error=manifest_error,
    )


def _main_unlocked() -> int:
    configure_output_encoding()
    print("请输入多个期数，空格或逗号分隔，例如：187 188 189 190")
    try:
        raw = input("期数：").strip()
        issues = list(dict.fromkeys(crawler.parse_issues(raw)))
    except (ValueError, EOFError) as exc:
        print(f"期数输入无效：{exc}")
        return 2
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
    except RuntimeError as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
