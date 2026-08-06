import json
from pathlib import Path
from typing import Any

from kill_numbers.infrastructure.file_store import atomic_write_json


def read_target_data(path: str | Path) -> list[dict[str, Any]]:
    target_path = Path(path)
    if not target_path.exists():
        raise FileNotFoundError(f"目标配置文件不存在：{target_path}")
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"目标配置无法读取：{target_path}；{exc}") from exc
    if not isinstance(data, list):
        raise ValueError("targets.json 必须是目标列表")
    if any(not isinstance(item, dict) for item in data):
        raise ValueError("targets.json 目标条目必须是对象")
    return data


def write_target_data(path: str | Path, targets: list[dict[str, Any]]) -> None:
    atomic_write_json(path, targets)


def merge_active_target_data(
    path: str | Path,
    active_targets: list[dict[str, Any]],
) -> None:
    all_targets = read_target_data(path)
    active_iter = iter(active_targets)
    merged: list[dict[str, Any]] = []
    for item in all_targets:
        if item.get("disabled"):
            merged.append(item)
            continue
        try:
            merged.append(next(active_iter))
        except StopIteration as exc:
            raise ValueError("活动目标数量与 targets.json 不一致") from exc
    try:
        next(active_iter)
    except StopIteration:
        write_target_data(path, merged)
    else:
        raise ValueError("活动目标数量与 targets.json 不一致")
