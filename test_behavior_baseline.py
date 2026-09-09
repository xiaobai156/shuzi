import hashlib
import json
from pathlib import Path

import crawler
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.validation.result_validator import evidence_from_source_document


GOLDEN_FILE = Path(__file__).resolve().parent / "tests" / "golden" / "formal_behavior_210_211.json"


def load_golden() -> dict:
    return json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))


def test_golden_manifest_matches_current_target_inventory():
    golden = load_golden()
    all_targets = json.loads(crawler.TARGETS_FILE.read_text(encoding="utf-8"))
    active_targets = crawler.load_targets()

    assert golden["schema_version"] == 1
    migrated = {'摇钱树第二', '安身立命', '舒舒服服', '云海尘清', '人才辈出',
                '此发彼应', '易洗鲨鱼', '门主立邦', '小时了了', '百里挑一',
                '聚宝十二', '聚宝十', '人在江湖', '人非土木', '片羽吉光'}
    assert len(all_targets) == golden["total_target_count"] + len(migrated)
    assert len(active_targets) == golden["active_target_count"] + len(migrated)
    assert {t['name'] for t in active_targets if t['name'] in migrated} == migrated
    assert set(golden["results"]).issubset({target["name"] for target in active_targets})
    # Reverse only the reviewed 252 migrations and verify the unchanged
    # historical inventory semantically. Do not replace golden source hashes.
    legacy = [dict(t) for t in all_targets if t['name'] not in migrated]
    changed = {'无庸赘述': ('topic-content', 'document.writeln'),
               '天公作美': ('content', '上一篇：'),
               '六合稳杀十码': ('topic-content', 'document.writeln'),
               '六合稳杀七码': ('topic-content', 'document.writeln'),
               '一点朱砂': ('content', '上一篇：')}
    for t in legacy:
        if t['name'] in changed:
            cls, stop = changed[t['name']]
            assert t.pop('content_class') == cls
            assert 'stop_anchor' not in t
            t['stop_anchor'] = stop
            if t['name'] == '六合稳杀十码':
                assert t.pop('browser') is True
            else:
                assert t.pop('allowed_source_types') == ['decoded_script']
    canonical = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    # Canonical SHA256 of origin/main cb915d1's original 208 targets.
    assert hashlib.sha256(canonical.encode()).hexdigest() == 'daccdf324f1eb15b7e18f0486bb8cb284c927f939a038b6d3af9156421cadbe8'
    assert all(len(value) == 64 for value in golden["source_hashes"].values())


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
        source = make_source_document(kind="configured_api", url=target.get("api_url", target["url"]), content=content, priority=100)
        for issue, numbers in found.items():
            proof = evidence_from_source_document(target, issue, numbers, source)
            assert proof.numbers == tuple(numbers) and proof.parser_id == target["special_parser"]


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
