import json
from pathlib import Path

import crawler


GOLDEN_FILE = Path(__file__).resolve().parent / "tests" / "golden" / "formal_behavior_210_211.json"


def load_golden() -> dict:
    return json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))


def test_golden_manifest_matches_current_target_inventory():
    golden = load_golden()
    all_targets = json.loads(crawler.TARGETS_FILE.read_text(encoding="utf-8"))
    active_targets = crawler.load_targets()

    assert golden["schema_version"] == 1
    assert len(all_targets) == golden["total_target_count"]
    assert len(active_targets) == golden["active_target_count"]
    assert set(golden["results"]).issubset({target["name"] for target in active_targets})


def test_identity_article_golden_rows_keep_original_order():
    golden = load_golden()
    targets = {target["name"]: target for target in crawler.load_targets()}
    identity_names = set(golden["results"]) - {"师太"}

    for name in identity_names:
        target = targets[name]
        rows = []
        for issue in golden["issues"]:
            numbers = ".".join(golden["results"][name][issue])
            row_issue = "水" if name == "铭记于心" and issue == "211" else issue
            identity = f"《{name}》" if name == "铭记于心" else f"『{name}』"
            rows.append(f"{row_issue}期:{identity}绝杀10码开:00准\n[{numbers}]")
        content = f"{name}\n211期:[绝杀10码]黄金基线\n" + "\n".join(rows)
        found = crawler.extract_identity_article_bottom_10_numbers(
            content,
            golden["issues"],
            target,
        )

        assert found == golden["results"][name]


def test_shita_golden_rows_stop_at_next_document():
    golden = load_golden()
    target = next(target for target in crawler.load_targets() if target["name"] == "师太")
    rows = []
    for issue in reversed(golden["issues"]):
        numbers = ".".join(golden["results"]["师太"][issue])
        rows.append(f"{issue}期:『师太』绝杀十码开:0000准\n[{numbers}]")
    content = (
        "大家发(绝杀10码)\n"
        "最给力的资料,尽在大家发站点735558.com\n"
        + "\n".join(rows)
        + "\ndocument.writeln(\"next document\")\n"
        + "211期:『其他栏目』绝杀十码开:0000准\n"
        + "[02.03.04.05.06.07.08.09.10.11]\n"
    )

    found = crawler.extract_dedicated_ten_numbers(
        content,
        golden["issues"],
        target,
        "shita_top_10",
    )

    assert found == golden["results"]["师太"]
