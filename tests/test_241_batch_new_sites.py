import json
from pathlib import Path


TARGETS_PATH = Path(__file__).resolve().parents[1] / "targets.json"

EXPECTED = {
    "致谢妖怪": "https://jtrmhar.cwdc3-r5vqn-qzqasa.work:17455/topic/741205.html",
    "索引僵尸": "https://jtrmhar.cwdc3-r5vqn-qzqasa.work:17455/topic/741204.html",
    "量子永生": "https://jtrmhar.cwdc3-r5vqn-qzqasa.work:17455/topic/741189.html",
    "指鹿为马": "https://fkwtjva.d7i0f-wgmak-ivbywl.work:17477/topic/726510.html",
    "十二生肖": "https://yowwqvn.m2le0-0deh3-mriunz.work:17488/",
    "素贞谒塔": "https://yefnnuh.ib0fp-7bi03-skjneo.work:17477/topic/702145.html",
    "此发彼应": "https://niqyf.77cz0-qzyks-ofhmfh.work/topic/206370.html",
    "易洗鲨鱼": "https://w4shq9.wc5vp-jujau-joiqes.cyou/topic/300219.html",
}

REJECTED_DUPLICATES = {"爬虫谣曲", "两全其美", "云岫裁诗", "地大物博"}

ROTATING_SCRIPT_SITES = {
    "澳彩预测", "大获全胜", "状元红", "结草衔环", "心旷神怡",
    "致谢妖怪", "索引僵尸", "量子永生", "指鹿为马", "十二生肖",
    "素贞谒塔", "此发彼应", "易洗鲨鱼", "门主立邦", "小时了了",
    "百里挑一", "聚宝十二", "聚宝十", "人在江湖", "人非土木",
    "片羽吉光",
}


def test_verified_241_batch_is_in_formal_config_without_rejected_duplicates():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))

    for name, url in EXPECTED.items():
        matches = [target for target in targets if target.get("name") == name and not target.get("disabled")]
        assert len(matches) == 1
        target = matches[0]
        assert target["url"] == url
        assert target["region"] == "top"
        assert target["issue_position_region"] == "top"
        assert target["count"] == 10
        assert target["issue_position_window"] == 10
        assert target["allowed_source_types"] == ["decoded_script"]
        assert "source_url_pattern" not in target

    active_names = {target.get("name") for target in targets if not target.get("disabled")}
    assert REJECTED_DUPLICATES.isdisjoint(active_names)


def test_rotating_script_sites_use_stable_content_identity_not_script_hashes():
    targets = {
        target["name"]: target
        for target in json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
        if not target.get("disabled")
    }

    assert ROTATING_SCRIPT_SITES <= targets.keys()
    for name in ROTATING_SCRIPT_SITES:
        target = targets[name]
        assert target["allowed_source_types"] == ["decoded_script"]
        assert target.get("anchor")
        assert "source_url_pattern" not in target
