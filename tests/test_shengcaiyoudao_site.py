import json
from pathlib import Path

import crawler


TARGETS_PATH = Path(__file__).resolve().parents[1] / "targets.json"


def test_shengcaiyoudao_12_numbers_are_scoped_to_its_bottom_block():
    target = {
        "url": "https://fbbqydh.nysch-a51ru-wsclir.work:17466/",
        "name": "生财有道",
        "keywords": [],
        "count": 12,
        "region": "bottom",
        "anchor": "生财有道®绝杀12码",
        "stop_anchor": "往下划，不后退，本站资料不收费！",
        "issue_position_window": 10,
        "allowed_source_types": ["decoded_script"],
    }
    content = """
其他栏目
240期[01.02.03.04.05.06.07.08.09.10.11.12]
生财有道®绝杀12码
231期[26.22.41.34.44.28.29.17.38.32.31.06]
232期[33.26.03.35.07.19.25.41.31.22.13.28]
233期[28.02.44.19.04.48.10.32.24.46.40.06]
234期[34.11.18.25.09.04.26.16.33.02.37.40]
237期[14.23.29.38.31.40.32.28.10.11.46.42]
238期[02.12.25.37.27.36.13.24.26.34.09.44]
239期[20.37.18.40.41.35.36.05.16.24.29.15]
240期[26.13.22.06.20.01.19.02.34.31.10.49]开:0000准
往下划，不后退，本站资料不收费！
241期[49.48.47.46.45.44.43.42.41.40.39.38]
"""

    assert crawler.parse_target_content(content, target, ["240"]) == {
        "240": ["26", "13", "22", "06", "20", "01", "19", "02", "34", "31", "10", "49"]
    }
    assert crawler.parse_target_content(content, target, ["241"]) == {}


def test_shengcaiyoudao_is_in_formal_targets_as_12_number_bottom_site():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    matches = [target for target in targets if target.get("name") == "生财有道"]

    assert len(matches) == 1
    assert matches[0] == {
        "url": "https://fbbqydh.nysch-a51ru-wsclir.work:17466/",
        "name": "生财有道",
        "keywords": [],
        "count": 12,
        "region": "bottom",
        "anchor": "生财有道®绝杀12码",
        "stop_anchor": "往下划，不后退，本站资料不收费！",
        "issue_position_window": 10,
        "allowed_source_types": ["decoded_script"],
    }
