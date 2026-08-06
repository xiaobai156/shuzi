import base64
import re
from collections.abc import Callable
from urllib.parse import parse_qs, quote, urlparse

from kill_numbers.acquisition.http_client import (
    fetch_json,
    fetch_text,
    is_http_404,
)
from kill_numbers.text_utils import (
    clean_name,
    extract_name_from_text,
    normalize_keyword,
    origin,
    remove_fragment,
)


ContentValidator = Callable[[str, dict | None, list[str] | None], bool]
PageRenderer = Callable[[str], str]


def parse_admin_article_id(url: str) -> str | None:
    match = re.search(r"/article/admin/([^/?#]+)", url)
    return match.group(1) if match else None


def site_url_param(url: str) -> str:
    parsed = urlparse(url)
    values = parse_qs(parsed.query).get("url") or []
    return values[0].strip() if values else ""


def find_admin_article_in_landing_data(data, article_id: str) -> dict | None:
    section_data = data.get("sectionData") if isinstance(data, dict) else None
    if not isinstance(section_data, dict):
        return None
    for section in section_data.values():
        if not isinstance(section, dict):
            continue
        articles = section.get("adminArticles")
        if not isinstance(articles, list):
            continue
        for article in articles:
            if isinstance(article, dict) and str(article.get("id") or "") == article_id:
                return article
    return None


def admin_article_api_url(url: str, target: dict | None = None) -> str:
    configured = (target or {}).get("api_url")
    if configured:
        return str(configured).strip()
    article_id = parse_admin_article_id(url)
    if not article_id:
        raise ValueError("没有找到文章 ID")
    return f"{origin(url)}/api/proxy/admin-articles/{article_id}"


def decode_possible_base64(value: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) < 8:
        return text
    candidate = text.replace("-", "+").replace("_", "/")
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", candidate):
        return text
    candidate = candidate + ("=" * ((4 - len(candidate) % 4) % 4))
    try:
        decoded = base64.b64decode(candidate, validate=True).decode("utf-8", errors="replace")
    except Exception:
        return text
    if "\ufffd" in decoded or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", decoded):
        return text
    return decoded


def admin_article_dict_to_content(data: dict, url: str) -> tuple[str, str]:
    if not isinstance(data, dict):
        raise ValueError("文章接口返回格式异常")

    name = clean_name(data.get("authorNickname") or data.get("author") or "")
    title = decode_possible_base64(str(data.get("title") or ""))
    body = decode_possible_base64(str(data.get("html") or data.get("content") or ""))
    combined = "\n".join(part for part in [name, title, body] if part)
    fallback = urlparse(url).netloc
    return name or extract_name_from_text(combined, fallback=fallback), combined


def validate_identity_article_api_data(data: dict, target: dict) -> None:
    identity = str(target.get("article_identity") or "").strip()
    author = clean_name(data.get("authorNickname") or data.get("author") or "")
    title = decode_possible_base64(str(data.get("title") or ""))
    if normalize_keyword(author) != normalize_keyword(identity):
        raise ValueError(f"{identity}接口作者身份不匹配")
    if not re.search(r"绝杀\s*(?:十|10|⑩)\s*码", normalize_keyword(title)):
        raise ValueError(f"{identity}接口标题不匹配：没有找到绝杀十码")


def crawl_configured_api_page(url: str, target: dict) -> tuple[str, str]:
    api_url = str(target.get("api_url") or "").strip()
    if not api_url:
        raise ValueError("站点缺少 api_url")
    data = fetch_json(api_url)
    if target.get("special_parser") == "identity_article_bottom_10":
        validate_identity_article_api_data(data, target)
    return admin_article_dict_to_content(data, url)


def fetch_admin_article_from_landing(url: str, article_id: str) -> dict:
    site_url = site_url_param(url)
    if not site_url:
        raise ValueError("文章接口失败且页面缺少 url 参数，无法读取 landing-page-data")
    landing_url = f"{origin(url)}/api/proxy/landing-page-data?url={quote(site_url)}"
    landing = fetch_json(landing_url)
    article = find_admin_article_in_landing_data(landing, article_id)
    if not article:
        raise ValueError("landing-page-data 里没有找到对应文章")
    return article


def crawl_admin_article_page(
    url: str,
    target: dict | None = None,
    issues: list[str] | None = None,
    *,
    content_matches: ContentValidator,
    render_page: PageRenderer,
) -> tuple[str, str]:
    article_id = parse_admin_article_id(url)
    if not article_id:
        raise ValueError("没有找到文章 ID")

    api_error = None
    try:
        data = fetch_json(admin_article_api_url(url, target))
        name, content = admin_article_dict_to_content(data, url)
        if content_matches(content, target, issues):
            return name, content
    except Exception as exc:
        api_error = exc

    try:
        data = fetch_admin_article_from_landing(url, article_id)
        name, content = admin_article_dict_to_content(data, url)
        if content_matches(content, target, issues):
            return name, content
    except Exception:
        if api_error and not is_http_404(api_error):
            raise api_error
        raise

    raw_page = fetch_text(remove_fragment(url))
    if content_matches(raw_page, target, issues):
        name = extract_name_from_text(raw_page, fallback=urlparse(url).netloc)
        return name, raw_page

    rendered = render_page(url)
    name = extract_name_from_text(rendered, fallback=urlparse(url).netloc)
    return name, rendered
