import re
from urllib.parse import parse_qs, urlencode, urlparse

from kill_numbers.acquisition.http_client import fetch_json
from kill_numbers.text_utils import clean_name, normalize_keyword, origin


def _query_value(url: str, key: str) -> str:
    values = parse_qs(urlparse(url).query).get(key) or []
    return str(values[0]).strip() if values else ""


def _configured_user_id(target: dict) -> str:
    page_url = str(target.get("url") or "")
    configured = str(
        target.get("article_user_id") or _query_value(page_url, "userId")
    ).strip()
    url_value = _query_value(page_url, "userId")
    if configured and url_value and configured != url_value:
        raise ValueError(
            f"来财资料配置用户 ID 与主页 URL 不一致：{configured} / {url_value}"
        )
    if not configured:
        raise ValueError("来财资料用户主页缺少用户 ID")
    return configured


def _title_matches_issue(title: str, issue: str) -> bool:
    return re.search(rf"(?<!\d)0?{re.escape(str(issue))}\s*期", title) is not None


def _issue_from_title(title: str) -> str:
    match = re.search(r"(?<!\d)0?(\d{1,3})\s*期", title)
    if not match:
        raise ValueError("来财资料目标帖子标题缺少期数")
    return match.group(1).lstrip("0") or "0"


def _title_matches_column(title: str, target: dict) -> bool:
    keywords = [
        normalize_keyword(keyword)
        for keyword in (target.get("title_keywords") or [])
        if keyword
    ]
    normalized = normalize_keyword(title)
    return not keywords or all(keyword in normalized for keyword in keywords)


def _release_payload(data: object) -> dict:
    if not isinstance(data, dict) or data.get("status") != 20000:
        status = data.get("status") if isinstance(data, dict) else None
        raise ValueError(f"来财资料用户帖子接口状态异常：{status}")
    payload = data.get("data")
    if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
        raise ValueError("来财资料用户帖子接口缺少列表")
    return payload


def select_discuss_user_release(
    data: object,
    target: dict,
    issues: list[str],
) -> dict:
    if len(issues) > 1:
        raise ValueError("来财资料用户主页专属采集一次只允许一个指定期数")
    issue = (str(issues[0]).strip().lstrip("0") or "0") if issues else ""
    user_id = _configured_user_id(target)
    lottery_type = int(target.get("lottery_type") or 2)
    eligible = []
    for item in _release_payload(data)["list"]:
        if not isinstance(item, dict):
            continue
        if str(item.get("user_id") or "").strip() != user_id:
            continue
        if int(item.get("lotteryType") or 0) != lottery_type:
            continue
        title = str(item.get("title") or "")
        if not _title_matches_column(title, target):
            continue
        if not str(item.get("id") or "").strip():
            continue
        eligible.append(item)
    window = int(target.get("issue_position_window") or 10)
    matches = (
        [eligible[0]]
        if not issue and eligible
        else [
            item
            for item in eligible[:window]
            if _title_matches_issue(str(item.get("title") or ""), issue)
        ]
    )
    identities = {str(item.get("id")) for item in matches}
    if len(identities) > 1:
        raise ValueError(f"来财资料 {issue}期目标帖子不唯一：{','.join(sorted(identities))}")
    if not matches:
        raise ValueError(f"来财资料用户主页没有找到 {issue}期目标栏目帖子")
    return matches[0]


def discuss_user_release_api_url(target: dict, page: int) -> str:
    base = str(target.get("release_api_url") or "").strip()
    if not base:
        base = "https://tu.118tapi.com/api/v1/user/release"
    params = {
        "lotteryType": int(target.get("lottery_type") or 2),
        "id": _configured_user_id(target),
        "type": int(target.get("release_type") or 1),
        "page": page,
        "keyword": "",
    }
    return f"{base}?{urlencode(params)}"


def discuss_user_detail_api_url(target: dict, article_id: str) -> str:
    base = str(target.get("detail_api_url") or "").strip()
    if not base:
        base = "https://tu.118tapi.com/api/v1/discuss/detail"
    return f"{base}?{urlencode({'id': article_id})}"


def validate_discuss_user_release_detail(
    data: object,
    target: dict,
    selected: dict,
    issue: str,
) -> None:
    payload = _detail_payload(data)
    selected_id = str(selected.get("id") or "").strip()
    if str(payload.get("id") or "").strip() != selected_id:
        raise ValueError(
            f"来财资料详情文章 ID 不一致：期望 {selected_id}，实际 {payload.get('id')}"
        )
    user_id = _configured_user_id(target)
    if str(payload.get("user_id") or "").strip() != user_id:
        raise ValueError(
            f"来财资料详情用户 ID 不一致：期望 {user_id}，实际 {payload.get('user_id')}"
        )
    if int(payload.get("lotteryType") or 0) != int(target.get("lottery_type") or 2):
        raise ValueError("来财资料详情彩种不一致")
    title = str(payload.get("title") or "")
    if not _title_matches_issue(title, issue):
        raise ValueError(f"来财资料详情标题期数不一致：期望 {issue}期")
    if not _title_matches_column(title, target):
        raise ValueError("来财资料详情标题缺少目标栏目字段")
    if not str(payload.get("content") or "").strip():
        raise ValueError("来财资料详情正文为空")


def crawl_discuss_user_release_page(
    url: str,
    target: dict,
    issues: list[str],
) -> tuple[str, str, str, str]:
    if len(issues) > 1:
        raise ValueError("来财资料用户主页专属采集一次只允许一个指定期数")
    issue = (str(issues[0]).strip().lstrip("0") or "0") if issues else ""
    page_limit = int(target.get("release_page_limit") or 1)
    selected: dict | None = None
    last_page = 1
    for page in range(1, page_limit + 1):
        listing = fetch_json(discuss_user_release_api_url(target, page))
        payload = _release_payload(listing)
        try:
            current = select_discuss_user_release(
                listing,
                target,
                [issue] if issue else [],
            )
        except ValueError as exc:
            if "没有找到" not in str(exc):
                raise
            current = None
        if current is not None:
            if not issue:
                issue = _issue_from_title(str(current.get("title") or ""))
            if selected is not None and str(selected.get("id")) != str(current.get("id")):
                raise ValueError(
                    f"来财资料 {issue}期目标帖子跨页不唯一："
                    f"{selected.get('id')},{current.get('id')}"
                )
            selected = current
        last_page = int(payload.get("last_page") or 1)
        if page >= last_page:
            break
    if selected is None:
        raise ValueError(
            f"来财资料用户主页前 {min(page_limit, last_page)} 页没有找到 {issue}期目标栏目帖子"
        )

    article_id = str(selected["id"])
    detail_url = discuss_user_detail_api_url(target, article_id)
    detail = fetch_json(detail_url)
    validate_discuss_user_release_detail(detail, target, selected, issue)
    return (
        clean_name(target.get("name") or "来财资料"),
        discuss_detail_dict_to_content(detail, target),
        article_id,
        detail_url,
    )


def discuss_detail_api_url(url: str, target: dict | None = None) -> str:
    configured = str((target or {}).get("api_url") or "").strip()
    if configured:
        return configured
    article_id = _query_value(url, "id")
    if not article_id:
        raise ValueError("来财资料地址缺少文章 ID")
    return f"{origin(url)}/api/v1/discuss/detail?id={article_id}"


def _detail_payload(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("来财资料接口返回格式异常")
    if data.get("status") != 20000:
        raise ValueError(f"来财资料接口状态异常：{data.get('status')}")
    payload = data.get("data")
    if not isinstance(payload, dict):
        raise ValueError("来财资料接口缺少文章对象")
    return payload


def validate_discuss_detail_api_data(data: object, target: dict) -> None:
    payload = _detail_payload(data)
    page_url = str(target.get("url") or "")
    configured_id = str(target.get("article_id") or _query_value(page_url, "id")).strip()
    url_id = _query_value(page_url, "id")
    if configured_id and url_id and configured_id != url_id:
        raise ValueError(f"来财资料配置文章 ID 与页面 URL 不一致：{configured_id} / {url_id}")
    if not configured_id:
        raise ValueError("来财资料缺少文章 ID")
    if str(payload.get("id") or "").strip() != configured_id:
        raise ValueError(
            f"来财资料接口文章 ID 不一致：期望 {configured_id}，实际 {payload.get('id')}"
        )

    configured_user_id = str(
        target.get("article_user_id") or _query_value(page_url, "userId")
    ).strip()
    url_user_id = _query_value(page_url, "userId")
    if configured_user_id and url_user_id and configured_user_id != url_user_id:
        raise ValueError(
            f"来财资料配置用户 ID 与页面 URL 不一致：{configured_user_id} / {url_user_id}"
        )
    if not configured_user_id:
        raise ValueError("来财资料缺少用户 ID")
    if str(payload.get("user_id") or "").strip() != configured_user_id:
        raise ValueError(
            f"来财资料接口用户 ID 不一致：期望 {configured_user_id}，实际 {payload.get('user_id')}"
        )

    title = str(payload.get("title") or "")
    title_keywords = [
        normalize_keyword(keyword)
        for keyword in (target.get("title_keywords") or [])
        if keyword
    ]
    normalized_title = normalize_keyword(title)
    if title_keywords and not any(keyword in normalized_title for keyword in title_keywords):
        raise ValueError("来财资料接口标题缺少目标栏目字段")
    if not str(payload.get("content") or "").strip():
        raise ValueError("来财资料接口正文为空")


def discuss_detail_dict_to_content(data: object, target: dict) -> str:
    payload = _detail_payload(data)
    name = clean_name(target.get("name") or "来财资料")
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    return "\n".join(part for part in (name, title, content) if part)


def crawl_discuss_detail_page(url: str, target: dict) -> tuple[str, str]:
    data = fetch_json(discuss_detail_api_url(url, target))
    validate_discuss_detail_api_data(data, target)
    return clean_name(target.get("name") or "来财资料"), discuss_detail_dict_to_content(data, target)
