"""Serialization helpers for the grouped recent-cache JSON format.

The public cache is grouped by site for readability, while the crawler and
duplicate checker continue to operate on one flattened record per site/issue.
"""

from collections.abc import Iterable
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit

from kill_numbers.text_utils import normalize_issue


def _issue_key(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return normalize_issue(raw)
    except (TypeError, ValueError):
        return ""


def _site_key(name: object, url: object) -> tuple[str, str]:
    # Keep the URL spelling/path case used by the source record.  The formal
    # cache identity already normalizes the URL at its validation boundary;
    # the presentation migration must not rewrite URLs as a side effect.
    return (str(name or "").strip(), str(url or "").strip())


def target_contract_fingerprint(target: dict) -> str:
    payload = json.dumps(
        target,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _region(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return {
        "上": "top",
        "顶部": "top",
        "下": "bottom",
        "尾部": "bottom",
        "底部": "bottom",
    }.get(normalized, normalized if normalized in {"top", "bottom"} else "")


def _record_dict(item: object) -> dict[str, str]:
    if isinstance(item, dict):
        name = item.get("name", "")
        url = item.get("url", "")
        issue = item.get("issue", "")
        numbers = item.get("numbers", "")
    else:
        name = getattr(item, "name", "")
        url = getattr(item, "url", "")
        issue = getattr(item, "issue", "")
        numbers = getattr(item, "numbers", "")
    if isinstance(numbers, (list, tuple)):
        numbers = ",".join(str(value) for value in numbers)
    return {
        "name": str(name or "").strip(),
        "url": str(url or "").strip(),
        "issue": _issue_key(issue),
        "numbers": str(numbers or "").strip(),
    }


def _failure_dict(item: object) -> dict[str, str]:
    if isinstance(item, dict):
        name = item.get("name", "")
        url = item.get("url", "")
        issue = item.get("issue", "")
        status = item.get("status", "failed")
        reason = item.get("reason", "")
    else:
        name = getattr(item, "name", "")
        url = getattr(item, "url", "")
        issue = getattr(item, "issue", "")
        status = getattr(item, "status", "failed")
        reason = getattr(item, "reason", "")
    return {
        "name": str(name or "").strip(),
        "url": str(url or "").strip(),
        "issue": _issue_key(issue),
        "status": str(status or "failed").strip(),
        "reason": str(reason or "未提供失败原因").strip(),
    }


def _site_slug(url: str) -> str:
    parsed = urlsplit(url)
    source = parsed.path.strip("/") or parsed.netloc or "site"
    source = re.sub(r"\.html?$", "", source, flags=re.IGNORECASE)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", source).strip("_")
    return (slug or "site")[:64]


def _target_map(targets: Iterable[dict] | None) -> tuple[list[dict], dict[tuple[str, str], dict]]:
    ordered = [target for target in (targets or []) if isinstance(target, dict)]
    return ordered, {
        _site_key(target.get("name"), target.get("url")): target
        for target in ordered
        if _site_key(target.get("name"), target.get("url")) != ("", "")
    }


def _ordered_site_keys(
    records: list[dict[str, str]],
    failures: list[dict[str, str]],
    targets: list[dict],
) -> list[tuple[str, str]]:
    present = []
    seen = set()
    for target in targets:
        key = _site_key(target.get("name"), target.get("url"))
        if key in seen or key == ("", ""):
            continue
        if any(_site_key(item["name"], item["url"]) == key for item in records + failures):
            present.append(key)
            seen.add(key)
    for item in records + failures:
        key = _site_key(item["name"], item["url"])
        if key not in seen and key != ("", ""):
            present.append(key)
            seen.add(key)
    return present


def _site_id(index: int, name: str, url: str, target: dict | None) -> str:
    explicit = str((target or {}).get("id") or "").strip()
    if explicit:
        return explicit
    return f"s{index:03d}_{_site_slug(url)}"


def build_cache_document(
    records: Iterable[object],
    failures: Iterable[object] | None,
    recent_count: int,
    targets: Iterable[dict] | None = None,
    updated_at: str | None = None,
) -> dict:
    normalized_records = [_record_dict(item) for item in records]
    normalized_failures = [_failure_dict(item) for item in (failures or [])]
    for record in normalized_records:
        if not record["name"] or not record["url"] or not record["issue"] or not record["numbers"]:
            raise ValueError("缓存记录缺少 name、url、issue 或 numbers")
    for failure in normalized_failures:
        if not failure["name"] or not failure["url"] or not failure["issue"] or not failure["reason"]:
            raise ValueError("缓存失败状态缺少 name、url、issue 或 reason")
        if failure["status"] != "failed":
            raise ValueError("缓存失败状态 status 必须为 failed")

    ordered_targets, target_by_key = _target_map(targets)
    site_keys = _ordered_site_keys(normalized_records, normalized_failures, ordered_targets)
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for record in normalized_records:
        key = _site_key(record["name"], record["url"])
        issue = record["issue"]
        previous = grouped.setdefault(key, {}).get(issue)
        if previous is not None and previous != record["numbers"]:
            raise ValueError(f"缓存更新存在同站同期冲突：{record['name']} {issue}期")
        grouped[key][issue] = record["numbers"]

    sites = []
    used_ids: set[str] = set()
    for index, key in enumerate(site_keys, start=1):
        name, url = key
        target = target_by_key.get(key)
        site_id = _site_id(index, name, url, target)
        if site_id in used_ids:
            site_id = f"{site_id}_{index:03d}"
        used_ids.add(site_id)
        site = {
            "id": site_id,
            "name": name,
            "url": url,
            "contract": target_contract_fingerprint(target) if target else "",
            "pick": _region((target or {}).get("region")),
            "browser": bool((target or {}).get("browser", (target or {}).get("browser_fallback", False))),
            "click_first": bool(
                (target or {}).get("click_first", (target or {}).get("position") == "first")
            ),
            "fingerprint": {
                issue: grouped[key][issue]
                for issue in sorted(grouped.get(key, {}), key=lambda value: int(value), reverse=True)
            },
        }
        sites.append(site)

    all_issues = [item["issue"] for item in normalized_records + normalized_failures if item["issue"]]
    base_period = max((int(issue) for issue in all_issues), default=0)
    return {
        "base_period": base_period,
        "periods": recent_count,
        "updated_at": updated_at or datetime.now().isoformat(timespec="seconds"),
        "version": 1,
        "sites": sites,
        "failures": normalized_failures,
    }


def records_from_cache_document(data: object) -> list[dict]:
    if not isinstance(data, dict):
        return []
    if "sites" not in data:
        records = data.get("records")
        return records if isinstance(records, list) else []

    sites = data.get("sites")
    if not isinstance(sites, list):
        return []
    records = []
    for site in sites:
        if not isinstance(site, dict):
            continue
        name = str(site.get("name") or "")
        url = str(site.get("url") or "")
        fingerprint = site.get("fingerprint")
        if not isinstance(fingerprint, dict):
            continue
        for issue, numbers in fingerprint.items():
            records.append(
                {
                    "name": name,
                    "url": url,
                    "issue": str(issue),
                    "numbers": str(numbers or ""),
                }
            )
    return records


def failures_from_cache_document(data: object) -> list[dict]:
    if not isinstance(data, dict):
        return []
    failures = data.get("failures", [])
    return failures if isinstance(failures, list) else []
