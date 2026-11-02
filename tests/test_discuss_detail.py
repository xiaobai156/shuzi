import pytest

from kill_numbers.acquisition.strategies import discuss_detail


USER_TARGET = {
    "url": "https://h6.118t118.com:8443/user?userId=273880",
    "name": "来财资料",
    "article_user_id": "273880",
    "lottery_type": 2,
    "release_type": 1,
    "title_keywords": ["杀10码中特"],
    "issue_position_window": 10,
}


def release(article_id="224", title="224期【杀10码中特】", **changes):
    item = {
        "id": article_id,
        "user_id": "273880",
        "lotteryType": 2,
        "title": title,
    }
    item.update(changes)
    return item


def release_page(items, last_page=1):
    return {"status": 20000, "data": {"list": items, "last_page": last_page}}


def detail(article_id="224", title="224期【杀10码中特】", **changes):
    payload = {
        "id": article_id,
        "user_id": "273880",
        "lotteryType": 2,
        "title": title,
        "content": "224期 杀码【01 02 03 04 05 06 07 08 09 10】",
    }
    payload.update(changes)
    return {"status": 20000, "data": payload}


def test_release_selection_filters_identity_column_and_invalid_rows():
    data = release_page(
        [
            "not-a-record",
            release("wrong-user", user_id="999"),
            release("wrong-lottery", lotteryType=1),
            release("wrong-column", title="224期其他资料"),
            release("", title="224期【杀10码中特】"),
            release(),
        ]
    )

    assert discuss_detail.select_discuss_user_release(data, USER_TARGET, ["224"])[
        "id"
    ] == "224"
    assert discuss_detail.select_discuss_user_release(data, USER_TARGET, [])["id"] == "224"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"status": 50000}, "接口状态异常"),
        ({"status": 20000, "data": {}}, "接口缺少列表"),
    ],
)
def test_release_selection_rejects_invalid_payload(data, message):
    with pytest.raises(ValueError, match=message):
        discuss_detail.select_discuss_user_release(data, USER_TARGET, ["224"])


def test_release_selection_rejects_multiple_issues_duplicate_ids_and_window_miss():
    with pytest.raises(ValueError, match="一次只允许一个"):
        discuss_detail.select_discuss_user_release(release_page([]), USER_TARGET, ["224", "223"])

    duplicate = release_page([release("a"), release("b")])
    with pytest.raises(ValueError, match="目标帖子不唯一"):
        discuss_detail.select_discuss_user_release(duplicate, USER_TARGET, ["224"])

    window_target = {**USER_TARGET, "issue_position_window": 1}
    with pytest.raises(ValueError, match="没有找到 224期"):
        discuss_detail.select_discuss_user_release(
            release_page([release("235", "235期【杀10码中特】"), release()]),
            window_target,
            ["224"],
        )


def test_user_and_article_api_urls_are_explicit_and_identity_bound():
    assert discuss_detail.discuss_user_release_api_url(USER_TARGET, 2) == (
        "https://tu.118tapi.com/api/v1/user/release?"
        "lotteryType=2&id=273880&type=1&page=2&keyword="
    )
    assert discuss_detail.discuss_user_detail_api_url(USER_TARGET, "abc") == (
        "https://tu.118tapi.com/api/v1/discuss/detail?id=abc"
    )
    custom = {
        **USER_TARGET,
        "release_api_url": "https://api.test/releases",
        "detail_api_url": "https://api.test/detail",
    }
    assert discuss_detail.discuss_user_release_api_url(custom, 1).startswith(
        "https://api.test/releases?"
    )
    assert discuss_detail.discuss_user_detail_api_url(custom, "abc") == (
        "https://api.test/detail?id=abc"
    )

    with pytest.raises(ValueError, match="用户 ID 与主页 URL 不一致"):
        discuss_detail.discuss_user_release_api_url(
            {**USER_TARGET, "article_user_id": "999"}, 1
        )
    with pytest.raises(ValueError, match="主页缺少用户 ID"):
        discuss_detail.discuss_user_release_api_url({"url": "https://example.test/user"}, 1)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "wrong"}, "详情文章 ID 不一致"),
        ({"user_id": "wrong"}, "详情用户 ID 不一致"),
        ({"lotteryType": 1}, "详情彩种不一致"),
        ({"title": "223期【杀10码中特】"}, "详情标题期数不一致"),
        ({"title": "224期其他栏目"}, "详情标题缺少目标栏目字段"),
        ({"content": ""}, "详情正文为空"),
    ],
)
def test_selected_release_detail_rejects_identity_or_content_drift(changes, message):
    data = detail(**changes)
    with pytest.raises(ValueError, match=message):
        discuss_detail.validate_discuss_user_release_detail(
            data, USER_TARGET, release(), "224"
        )


def test_user_release_crawl_finds_exact_post_across_configured_pages(monkeypatch):
    target = {**USER_TARGET, "release_page_limit": 2}

    def fake_fetch_json(url):
        if "page=1" in url:
            return release_page([release("223", "223期【杀10码中特】")], last_page=2)
        if "page=2" in url:
            return release_page([release()], last_page=2)
        if "discuss%2Fdetail" in url:
            raise AssertionError("详情接口地址不应被二次编码")
        return detail()

    monkeypatch.setattr(discuss_detail, "fetch_json", fake_fetch_json)

    name, content, article_id, detail_url = discuss_detail.crawl_discuss_user_release_page(
        target["url"], target, ["224"]
    )

    assert name == "来财资料"
    assert article_id == "224"
    assert detail_url.endswith("?id=224")
    assert "224期" in content and "01 02 03" in content


def test_user_release_crawl_derives_latest_issue_and_rejects_cross_page_conflict(monkeypatch):
    monkeypatch.setattr(
        discuss_detail,
        "fetch_json",
        lambda url: release_page([release("latest", "0224期【杀10码中特】")])
        if "user/release" in url
        else detail("latest"),
    )
    assert discuss_detail.crawl_discuss_user_release_page(
        USER_TARGET["url"], USER_TARGET, []
    )[2] == "latest"

    target = {**USER_TARGET, "release_page_limit": 2}

    def conflicting_pages(url):
        if "page=1" in url:
            return release_page([release("a")], last_page=2)
        return release_page([release("b")], last_page=2)

    monkeypatch.setattr(discuss_detail, "fetch_json", conflicting_pages)
    with pytest.raises(ValueError, match="跨页不唯一"):
        discuss_detail.crawl_discuss_user_release_page(target["url"], target, ["224"])


def test_user_release_crawl_reports_bounded_search_failure(monkeypatch):
    target = {**USER_TARGET, "release_page_limit": 2}
    monkeypatch.setattr(
        discuss_detail,
        "fetch_json",
        lambda url: release_page([], last_page=2),
    )

    with pytest.raises(ValueError, match="前 2 页没有找到 224期"):
        discuss_detail.crawl_discuss_user_release_page(target["url"], target, ["224"])
    with pytest.raises(ValueError, match="一次只允许一个"):
        discuss_detail.crawl_discuss_user_release_page(
            target["url"], target, ["224", "223"]
        )


DETAIL_TARGET = {
    "url": "https://h6.118t118.com:8443/forum/details?id=224&userId=273880",
    "name": "来财资料",
    "article_id": "224",
    "article_user_id": "273880",
    "title_keywords": ["杀10码中特"],
}


def test_detail_api_url_defaults_to_same_origin_and_allows_explicit_api():
    assert discuss_detail.discuss_detail_api_url(DETAIL_TARGET["url"]) == (
        "https://h6.118t118.com:8443/api/v1/discuss/detail?id=224"
    )
    assert discuss_detail.discuss_detail_api_url(
        DETAIL_TARGET["url"], {"api_url": "https://api.test/exact"}
    ) == "https://api.test/exact"
    with pytest.raises(ValueError, match="地址缺少文章 ID"):
        discuss_detail.discuss_detail_api_url("https://example.test/details")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "返回格式异常"),
        ({"status": 50000}, "接口状态异常"),
        ({"status": 20000, "data": []}, "接口缺少文章对象"),
    ],
)
def test_detail_validation_rejects_invalid_payload(data, message):
    with pytest.raises(ValueError, match=message):
        discuss_detail.validate_discuss_detail_api_data(data, DETAIL_TARGET)


@pytest.mark.parametrize(
    ("target_changes", "data_changes", "message"),
    [
        ({"article_id": "wrong"}, {}, "配置文章 ID 与页面 URL 不一致"),
        ({"url": "https://example.test/details?userId=273880", "article_id": ""}, {}, "缺少文章 ID"),
        ({}, {"id": "wrong"}, "接口文章 ID 不一致"),
        ({"article_user_id": "wrong"}, {}, "配置用户 ID 与页面 URL 不一致"),
        ({"url": "https://example.test/details?id=224", "article_user_id": ""}, {}, "缺少用户 ID"),
        ({}, {"user_id": "wrong"}, "接口用户 ID 不一致"),
        ({}, {"title": "224期其他栏目"}, "接口标题缺少目标栏目字段"),
        ({}, {"content": ""}, "接口正文为空"),
    ],
)
def test_detail_validation_rejects_contract_drift(target_changes, data_changes, message):
    target = {**DETAIL_TARGET, **target_changes}
    data = detail(**data_changes)
    with pytest.raises(ValueError, match=message):
        discuss_detail.validate_discuss_detail_api_data(data, target)


def test_detail_crawl_validates_then_formats_exact_article(monkeypatch):
    calls = []
    monkeypatch.setattr(
        discuss_detail,
        "fetch_json",
        lambda url: calls.append(url) or detail(),
    )

    name, content = discuss_detail.crawl_discuss_detail_page(
        DETAIL_TARGET["url"], DETAIL_TARGET
    )

    assert calls == ["https://h6.118t118.com:8443/api/v1/discuss/detail?id=224"]
    assert name == "来财资料"
    assert content.startswith("来财资料\n224期")
