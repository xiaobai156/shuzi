import pytest

import crawler
from kill_numbers.acquisition.strategies.admin_article import (
    validate_identity_article_api_data,
)


URL = (
    "https://knfoaep.ivqs8-1depw-yoirtw.xyz:29444/"
    "article/manager/6a15bde5806b655fd89b3fbd?url=lf"
)
API_URL = (
    "https://knfoaep.ivqs8-1depw-yoirtw.xyz:29444/"
    "api/proxy/manager-articles/6a15bde5806b655fd89b3fbd"
)


def target() -> dict:
    return next(item for item in crawler.load_targets() if item.get("url") == URL)


def fixture() -> str:
    return """日积月累
215期：【绝杀⑩码】〓 高手研究
205期:『日积月累』绝杀10码开:龙03准
[05.06.07.08.09.12.13.14.16.19]
206期:『日积月累』绝杀10码开:猴47准
[23.26.29.31.33.34.35.37.39.43]
207期:『日积月累』绝杀10码开:鼠31准
[02.11.12.13.15.18.21.22.26.27]
208期:『日积月累』绝杀10码开:鼠19错
[02.03.05.07.08.13.14.19.21.26]
209期:『日积月累』绝杀10码开:猪08准
[16.17.19.21.26.28.29.31.33.34]
210期:『日积月累』绝杀10码开:马49准
[02.05.08.14.17.18.22.23.26.28]
211期:『日积月累』绝杀10码开:马01准
[04.05.08.09.12.13.14.17.19.26]
212期:『日积月累』绝杀10码开:牛06准
[05.07.09.12.22.23.25.26.27.28]
213期:『日积月累』绝杀10码开:猴35准
[01.07.13.15.16.17.18.19.22.25]
214期:『日积月累』绝杀10码开:兔04准
[23.27.29.31.34.35.41.44.46.48]
215期:『日积月累』绝杀10码开:0000准
[02.03.04.06.07.08.10.11.13.17]
"""


def test_rijiyuele_is_registered_with_manager_article_identity_contract():
    configured = target()

    assert configured == {
        "url": URL,
        "api_url": API_URL,
        "name": "日积月累",
        "keywords": ["绝杀十码"],
        "count": 10,
        "region": "bottom",
        "anchor": "日积月累",
        "article_identity": "日积月累",
        "issue_position_window": 10,
        "special_parser": "identity_article_bottom_10",
        "onboarding_exception": {
            "type": "invalid_duplicate_baseline",
            "approved": True,
            "scope": "用户明确特例添加；仅放宽正式判重基线无效门禁",
            "validated_issues": [
                "206",
                "207",
                "208",
                "209",
                "210",
                "211",
                "212",
                "213",
                "214",
                "215",
            ],
        },
    }


def test_rijiyuele_bottom_window_extracts_215_and_excludes_205():
    configured = target()

    assert crawler.extract_identity_article_bottom_10_numbers(
        fixture(), ["205", "215"], configured
    ) == {
        "215": ["02", "03", "04", "06", "07", "08", "10", "11", "13", "17"]
    }


def test_rijiyuele_api_identity_accepts_circled_ten_title():
    validate_identity_article_api_data(
        {
            "authorNickname": "日积月累",
            "title": "215期：【绝杀⑩码】〓 高手研究",
        },
        target(),
    )


def test_rijiyuele_rejects_wrong_direction():
    configured = dict(target())
    configured["region"] = "top"

    with pytest.raises(ValueError, match="只允许 bottom"):
        crawler.extract_identity_article_bottom_10_numbers(
            fixture(), ["215"], configured
        )
