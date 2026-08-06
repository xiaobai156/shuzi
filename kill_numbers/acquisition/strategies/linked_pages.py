import re
from urllib.parse import urlparse

from kill_numbers.acquisition.discovery import script_urls
from kill_numbers.acquisition.http_client import fetch_bytes, fetch_text
from kill_numbers.text_utils import html_to_text, normalize_issue, remove_fragment

def topic_links(page: str) -> list[tuple[str, str]]:
    links = []
    for match in re.finditer(
        r"<a\b[^>]*\bhref\s*=\s*['\"]([^'\"]+)['\"][^>]*>(.*?)</a\s*>",
        page,
        re.I | re.S,
    ):
        label = html_to_text(match.group(2)).strip()
        if label:
            links.append((match.group(1), label))
    return links


def exact_issue_link(
    links: list[tuple[str, str]],
    issue: str,
    *keywords: str,
) -> tuple[str, str]:
    normalized = normalize_issue(issue)
    issue_pattern = re.compile(rf"(?<!\d)0?{re.escape(normalized)}\s*期(?!\d)")
    matches = [
        link
        for link in links
        if issue_pattern.search(link[1]) and all(keyword in link[1] for keyword in keywords)
    ]
    if len(matches) != 1:
        raise ValueError(f"{normalized}期专属链接不唯一或不存在")
    return matches[0]
def fetch_unique_script_content(
    page_url: str,
    path_suffix: str,
    *,
    error_label: str = "",
) -> str:
    normalized_url = remove_fragment(page_url)
    landing = fetch_text(normalized_url)
    matching_urls = [
        script_url
        for script_url in script_urls(landing, normalized_url)
        if urlparse(script_url).path.lower().endswith(path_suffix.lower())
    ]
    if len(matching_urls) != 1:
        prefix = f"{error_label}" if error_label else ""
        raise ValueError(
            f"{prefix}没有找到唯一 {path_suffix.rsplit('/', 1)[-1]} 专属脚本："
            f"{len(matching_urls)}"
        )
    return fetch_text(matching_urls[0])


def fetch_encoded_page(url: str, encoding: str) -> str:
    return fetch_bytes(remove_fragment(url)).decode(encoding, errors="replace")
