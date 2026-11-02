import json
from pathlib import Path


TARGETS_PATH = Path(__file__).resolve().parents[1] / "targets.json"

EXPECTED = {
    "澳彩预测": {
        "url": "https://guqzklf.1dd96-rjak5-sofyun.work:17466/",
        "region": "top",
        "issue_position_region": "top",
        "count": 10,
    },
    "大获全胜": {
        "url": "https://wmdhoki.6su07-7lpw6-gudkzl.xyz:16677/topic/727504.html",
        "region": "top",
        "issue_position_region": "top",
        "count": 10,
    },
    "状元红": {
        "url": "https://zexytkv.y0zsf-hpscd-qblttr.work:17466/topic/451818.html",
        "region": "bottom",
        "issue_position_region": "top",
        "count": 10,
    },
    "结草衔环": {
        "url": "https://uifesgo.1szw5-owiw3-hyunhb.work:17466/topic/766005.html",
        "region": "top",
        "issue_position_region": "top",
        "count": 7,
    },
    "心旷神怡": {
        "url": "https://uifesgo.1szw5-owiw3-hyunhb.work:17466/topic/766007.html",
        "region": "top",
        "issue_position_region": "bottom",
        "count": 10,
    },
}


def test_verified_240_241_sites_are_in_formal_config():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))

    for name, expected in EXPECTED.items():
        matches = [target for target in targets if target.get("name") == name and not target.get("disabled")]
        assert len(matches) == 1
        target = matches[0]
        for key, value in expected.items():
            assert target[key] == value
        assert target["allowed_source_types"] == ["decoded_script"]
        assert "source_url_pattern" not in target
        assert target["issue_position_window"] == 10
