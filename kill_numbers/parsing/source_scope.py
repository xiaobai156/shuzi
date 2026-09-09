"""Resolve a document's configured scope without silently searching the whole page."""
from urllib.parse import urlparse
import re

from kill_numbers.text_utils import normalize_keyword
from kill_numbers.parsing.errors import NoCandidateError


def target_for_document(target, document):
    value = dict(target)
    pattern = str(target.get("source_url_pattern") or "")
    if pattern:
        if not re.search(pattern, document.url, re.I):
            raise NoCandidateError("来源文档不符合专属 URL 契约")
        if target.get("source_anchor"):
            value["anchor"] = target["source_anchor"]
    metadata = document.metadata
    user_match = re.search(r"/users/(\d+)(?:\D|$)", str(target.get("url") or ""))
    # Only the per-user API adapter can replace an author text anchor. A file
    # basename or arbitrary SourceDocument.identity cannot authorize this.
    if (user_match and document.kind == "user_forum_topic"
            and metadata.get("identity_verified") is True
            and str(metadata.get("user_id")) == user_match.group(1)
            and metadata.get("identity_source") == (
                f"{urlparse(target['url']).scheme}://{urlparse(target['url']).netloc}"
                f"/api/v1/users/{user_match.group(1)}")
            and normalize_keyword(document.identity) == normalize_keyword(str(value.get("anchor") or ""))):
        value["anchor"] = ""
        value["_scope_kind"] = "source_identity"
        value["_source_identity"] = document.identity
    allowed_key = "allowed_row_starts"
    if (
        value.get("_history_discovery") is not True
        and "current_allowed_row_starts" in metadata
    ):
        allowed_key = "current_allowed_row_starts"
    if allowed_key in metadata:
        value["_allowed_row_starts"] = set(metadata[allowed_key])
    return value
