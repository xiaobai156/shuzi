import json
import re
import time
from collections.abc import Callable
from urllib.parse import urljoin

from kill_numbers.acquisition.http_client import fetch_json
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.domain.models import SourceDocument
from kill_numbers.text_utils import clean_name, origin


IssueExtractor = Callable[[str], dict[str, list[str]]]
JsonFetcher = Callable[[str], object]


def parse_user_id(url: str) -> str | None:
    match = re.search(r"/users/(\d+)", url)
    return match.group(1) if match else None


def crawl_user_documents(
    url: str,
    *,
    fetch_json_value: JsonFetcher = fetch_json,
) -> tuple[str, list[SourceDocument]]:
    user_id = parse_user_id(url)
    if not user_id:
        raise ValueError("没有找到用户 ID")

    base = origin(url)
    profile_url = f"{base}/api/v1/users/{user_id}"
    user = fetch_json_value(profile_url)
    name = clean_name(user.get("nickname") or f"user_{user_id}")
    documents = [
        make_source_document(
            kind="user_profile_api",
            url=profile_url,
            content=json.dumps(user, ensure_ascii=False),
            identity=name,
            priority=0,
            metadata={"parseable": False, "user_id": user_id},
        )
    ]

    lt = None
    forum_sequence_index = 0
    for page_index in range(5):
        api_url = f"{base}/api/v1/users/{user_id}/forums"
        if lt:
            api_url += f"?lt={lt}"
        forums = fetch_json_value(api_url)
        if not isinstance(forums, list) or not forums:
            break
        for item_index, item in enumerate(forums):
            parts = [
                str(item.get("topic") or ""),
                str(item.get("content") or ""),
            ]
            content = "\n".join(part for part in parts if part)
            if not content.strip():
                forum_sequence_index += 1
                continue

            item_id = str(
                item.get("id")
                or item.get("topicId")
                or item.get("topic_id")
                or item.get("tid")
                or forum_sequence_index
            )
            item_url = str(
                item.get("url")
                or item.get("topic_url")
                or item.get("topicUrl")
                or item.get("href")
                or f"{api_url}#topic-{item_id}"
            )
            documents.append(
                make_source_document(
                    kind="user_forum_topic",
                    url=urljoin(api_url, item_url),
                    parent_url=api_url,
                    identity=name,
                    content=content,
                    priority=100 - page_index,
                    metadata={
                        "parseable": True,
                        "user_id": user_id,
                        "page_index": page_index,
                        "item_index": item_index,
                        "item_id": item_id,
                        "region_sequence": "user_forum_topics",
                        "region_index": forum_sequence_index,
                    },
                )
            )
            forum_sequence_index += 1
        lt = forums[-1].get("id")
        if not lt:
            break
        time.sleep(0.1)
    return name, documents


def crawl_user_page(
    url: str,
    issues: list[str],
    *,
    extract_issues: IssueExtractor,
    batch_after_forum_page: bool,
    fetch_json_value: JsonFetcher = fetch_json,
) -> tuple[str, str]:
    # Keep this legacy return shape for external callers, but parse one
    # independently acquired document at a time.  The formal pipeline uses
    # crawl_user_documents directly and never receives a merged text blob.
    _ = batch_after_forum_page
    name, documents = crawl_user_documents(
        url,
        fetch_json_value=fetch_json_value,
    )
    parseable = [
        document
        for document in documents
        if document.metadata.get("parseable", True) and document.content.strip()
    ]
    for document in parseable:
        try:
            found = extract_issues(document.content)
        except Exception:
            continue
        if issues and all(issue in found for issue in issues):
            return name, document.content
    return name, parseable[0].content if parseable else ""
