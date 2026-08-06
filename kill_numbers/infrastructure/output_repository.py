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
        return str(directory / result_file), str(directory / failed_file), "当期报告.txt"

    prefix = normalized[0] if len(normalized) == 1 else f"{normalized[0]}-{normalized[-1]}"
    return (
        str(directory / f"{prefix}期-杀数字-成功.txt"),
        str(directory / f"{prefix}期-杀数字-失败.txt"),
        f"{prefix}期报告.txt",
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
    except OSError:
        pass
