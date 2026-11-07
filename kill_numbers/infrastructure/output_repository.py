import shutil
import time
from pathlib import Path
from collections.abc import Callable, Iterable


def output_files_for_issues(
    results_dir: str | Path,
    issues: Iterable[str],
    normalize_issue: Callable[[str], str],
    result_file: str,
    failed_file: str,
) -> tuple[str, str, str]:
    normalized = [normalize_issue(issue) for issue in issues if normalize_issue(issue)]
    directory = Path(results_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if not normalized:
        return (
            str(directory / result_file),
            str(directory / failed_file),
            str(directory / "当期报告.txt"),
        )
    if len(normalized) > 1:
        # 一期一文件是硬约束：成功TXT的行格式不含期数，多期写同一文件会让
        # 行无法区分期数。多期必须由上层逐期调用，而不是在这里合并。
        raise ValueError(
            f"一次只能对应一个期数，收到 {len(normalized)} 个：{', '.join(normalized)}"
        )

    prefix = normalized[0]
    return (
        str(directory / f"{prefix}期-杀数字-成功.txt"),
        str(directory / f"{prefix}期-杀数字-失败.txt"),
        str(directory / f"{prefix}期报告.txt"),
    )


def cleanup_old_backups(path: str | Path, keep: int = 10) -> None:
    target = Path(path)
    backups = sorted(
        target.parent.glob(f"{target.name}.*.bak"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[keep:]:
        try:
            old_backup.unlink()
        except OSError:
            continue


def backup_existing_outputs(*paths: str | Path, keep: int = 10) -> None:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    for path in paths:
        target = Path(path)
        try:
            shutil.copy2(target, f"{target}.{timestamp}.bak")
        except FileNotFoundError:
            continue
        cleanup_old_backups(target, keep=keep)


def remove_stale_file(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
