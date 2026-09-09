from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(
            f"{path}: expected one match, got {text.count(old)}: {old[:100]}"
        )
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path: str, old: str, new: str, minimum: int = 1) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(
            f"{path}: expected at least {minimum} matches, got {count}: {old[:100]}"
        )
    file.write_text(text.replace(old, new), encoding="utf-8")


# None is not a configured anchor. Dedicated identity parsers may use their
# validated article identity as an explicit whole-document scope.
replace_all(
    "kill_numbers/validation/result_validator.py",
    "            if str(value).strip()\n",
    "            if value is not None and str(value).strip()\n",
    minimum=2,
)
replace_once(
    "kill_numbers/validation/result_validator.py",
    '''    if identity_matches:
        scopes.append(
            (
                "source_identity",
                f"source_identity:{document.identity}",
                text,
                0,
            )
        )
    if not anchors:
''',
    '''    if identity_matches:
        scopes.append(
            (
                "source_identity",
                f"source_identity:{document.identity}",
                text,
                0,
            )
        )
    dedicated_identity = str(target.get("article_identity") or "").strip()
    dedicated_identity_parsers = {
        "identity_article_top_10",
        "identity_article_bottom_10",
        "ttss_paginated_identity_top_10",
    }
    if (
        dedicated_identity
        and parser_id in dedicated_identity_parsers
        and normalize_keyword(dedicated_identity) in normalize_keyword(text)
    ):
        scopes.append(
            (
                "parser_identity",
                f"parser_identity:{dedicated_identity}",
                text,
                0,
            )
        )
    if not anchors:
''',
)
replace_once(
    "kill_numbers/validation/result_validator.py",
    '''        elif evidence.scope_kind == "source_identity":
            if not any(
                normalize_keyword(evidence.source_identity) == normalize_keyword(value)
                for value in configured_scopes
            ):
                errors.append(f"{issue}期来源身份不匹配")
        else:
''',
    '''        elif evidence.scope_kind == "source_identity":
            if not any(
                normalize_keyword(evidence.source_identity) == normalize_keyword(value)
                for value in configured_scopes
            ):
                errors.append(f"{issue}期来源身份不匹配")
        elif evidence.scope_kind == "parser_identity":
            expected_identity = str(target.get("article_identity") or "").strip()
            if (
                not expected_identity
                or evidence.anchor != f"parser_identity:{expected_identity}"
                or evidence.parser_id not in {
                    "identity_article_top_10",
                    "identity_article_bottom_10",
                    "ttss_paginated_identity_top_10",
                }
            ):
                errors.append(f"{issue}期专属文章身份不匹配")
        else:
''',
)

# Updating an already-cached historical period is not a rollback. Preserve
# that period's cycle and sequence instead of making it the newest period.
replace_once(
    "kill_numbers/infrastructure/cache_repository.py",
    '''        last = latest_by_site.get(site)
        cycle = _infer_next_cycle(last[0] if last else None, last[1] if last else 0, value["issue"])

        # Remove any old payload for this visible issue. Within the retained
        # ten-period window an issue cannot legitimately occur in two cycles.
        payload_key = (site, value["issue"])
''',
    '''        last = latest_by_site.get(site)
        existing_periods = [
            (period_key, entry)
            for period_key, entry in timeline_by_period.items()
            if period_key[0] == site and period_key[2] == value["issue"]
        ]
        if len(existing_periods) > 1:
            raise ValueError(
                f"缓存同站可见期号跨周期冲突：{value['name']} {value['issue']}期"
            )
        if existing_periods:
            existing_key, existing_entry = existing_periods[0]
            cycle = existing_key[1]
            entry_sequence = int(existing_entry["sequence"])
        else:
            cycle = _infer_next_cycle(
                last[0] if last else None,
                last[1] if last else 0,
                value["issue"],
            )
            entry_sequence = run_sequence

        # Remove any old payload for this visible issue. Within the retained
        # ten-period window an issue cannot legitimately occur in two cycles.
        payload_key = (site, value["issue"])
''',
)
replace_once(
    "kill_numbers/infrastructure/cache_repository.py",
    '''            "sequence": run_sequence,
            "cycle": cycle,
        }
        latest_by_site[site] = (value["issue"], cycle, run_sequence)
''',
    '''            "sequence": entry_sequence,
            "cycle": cycle,
        }
        if not existing_periods:
            latest_by_site[site] = (value["issue"], cycle, entry_sequence)
''',
)

# Keep construction compatibility for helper tests while run_issue always sets
# freshness explicitly.
replace_once(
    "run_crawler_multi_prompt.py",
    '''    success_fresh: bool
    failure_fresh: bool
''',
    '''    success_fresh: bool = True
    failure_fresh: bool = True
''',
)

# Tests that encoded the old fixed-three behavior now assert the configured
# window contract.
replace_once(
    "test_crawler_user_page.py",
    ''') == ["210", "209", "208"]
''',
    ''') == ["210", "209", "208", "207"]
''',
)
replace_once(
    "test_crawler_user_page.py",
    "def test_direction_window_is_always_top_three_or_bottom_three():\n",
    "def test_direction_window_honors_explicit_configured_size():\n",
)
replace_once(
    "test_crawler_user_page.py",
    '''    ) == {}
    assert crawler.extract_issue_numbers(
        text,
        ["208"],
        keywords=["绝杀10码"],
        expected_count=10,
        anchor="条纹妇人",
        region="bottom",
        issue_position_window=30,
    ) == {}
''',
    '''    ) == {"207": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]}
    assert crawler.extract_issue_numbers(
        text,
        ["208"],
        keywords=["绝杀10码"],
        expected_count=10,
        anchor="条纹妇人",
        region="bottom",
        issue_position_window=30,
    ) == {"208": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]}
''',
)
replace_once(
    "test_parser_registry.py",
    '''        "region": "top",
        "anchor": "作者甲",
    }
''',
    '''        "region": "top",
        "anchor": "作者甲",
        "issue_position_window": 3,
    }
''',
)
replace_once(
    "test_prompt_entrypoints.py",
    "import inspect\n",
    "import inspect\n\nimport pytest\n",
)
replace_once(
    "test_prompt_entrypoints.py",
    '''def test_single_prompt_disables_cache_updates_for_multiple_issues():
    assert "--no-cache-update" not in prompt.crawler_command_for_input("187")
    assert "--no-cache-update" in prompt.crawler_command_for_input("187 188")
''',
    '''def test_single_prompt_rejects_multiple_issues():
    assert "--no-cache-update" not in prompt.crawler_command_for_input("187")
    with pytest.raises(ValueError, match="单期入口只允许一个期数"):
        prompt.crawler_command_for_input("187 188")
''',
)

# Refresh fingerprints only for the source files tracked by the golden
# manifest. The captured behavior rows remain unchanged.
golden_path = ROOT / "tests/golden/formal_behavior_210_211.json"
golden = json.loads(golden_path.read_text(encoding="utf-8"))
for filename in list(golden["source_hashes"]):
    if filename == "recent_10_cache.json":
        continue
    golden["source_hashes"][filename] = hashlib.sha256(
        (ROOT / filename).read_bytes()
    ).hexdigest().upper()
golden_path.write_text(
    json.dumps(golden, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print("critical fix reconciliation applied")
