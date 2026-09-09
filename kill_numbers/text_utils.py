import html
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlparse


KEYWORD_NORMALIZATION_TABLE = str.maketrans(
    {
        "碼": "码",
        "殺": "杀",
        "絕": "绝",
        "穩": "稳",
        "準": "准",
        "開": "开",
        "零": "0",
        "〇": "0",
        "一": "1",
        "二": "2",
        "两": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
        "十": "10",
    }
)


def remove_fragment(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def origin(url: str) -> str:
    parsed = urlparse(remove_fragment(url))
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_issue(issue: str) -> str:
    text = unicodedata.normalize("NFKC", str(issue)).strip()
    if not re.fullmatch(r"[0-9]{1,4}(?:\s*期)?", text):
        raise ValueError(f"期数格式无效：{issue!r}")
    number = int(text.removesuffix("期").strip())
    if not 1 <= number <= 999:
        raise ValueError("期数必须是1至999的正整数")
    return str(number)


def parse_issues(raw: str) -> list[str]:
    issues = []
    for part in re.split(r"[,，\s]+", raw.strip()):
        if part:
            issues.append(normalize_issue(part))
    return issues


def fullwidth_to_halfwidth(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    table = str.maketrans(
        "０１２３４５６７８９（）【】［］，．：　",
        "0123456789()[][],.: ",
    )
    return text.translate(table)


def html_to_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|tr|td|h[1-6])\s*>", "\n", value)
    value = re.sub(r"(?is)<script.*?</script>", "\n", value)
    value = re.sub(r"(?is)<style.*?</style>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)
    value = fullwidth_to_halfwidth(value)
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t\xa0]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{2,}", "\n", value)
    return value.strip()


def unique_keep_order(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def normalize_keyword(keyword: str) -> str:
    keyword = fullwidth_to_halfwidth(keyword or "")
    keyword = keyword.translate(KEYWORD_NORMALIZATION_TABLE)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", keyword)


def compact_text(value: str) -> str:
    return normalize_keyword(value)


def as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]


def clean_name(name: str) -> str:
    name = html_to_text(name)
    name = re.sub(r"^(作者|楼主|昵称)\s*[:：]\s*", "", name)
    name = re.sub(r"\s+", "", name)
    name = re.sub(r"(已更新|公开|精准|准|错|对)$", "", name)
    return name[:20] or "未命名"


def extract_name_from_text(text: str, fallback: str = "未命名") -> str:
    clean = html_to_text(text)
    author_patterns = [
        r"作者\s*[:：]\s*([^\n<]+)",
        r"楼主\s*[:：]\s*([^\n<]+)",
        r"发帖人\s*[:：]\s*([^\n<]+)",
    ]
    for pattern in author_patterns:
        match = re.search(pattern, clean)
        if match:
            return clean_name(match.group(1))

    bracket_patterns = [
        r"0?\d{2,3}\s*期\s*[:：]?\s*[【\[]([^】\]]{2,20})[】\]]",
        r"0?\d{2,3}\s*期\s*[:：]?\s*[《〈]([^》〉]{2,20})[》〉]",
        r"0?\d{2,3}\s*期\s*[:：]?\s*[『「]([^』」]{2,20})[』」]",
    ]
    bad_words = ("杀", "码", "稳", "绝", "期", "开", "公开")
    for pattern in bracket_patterns:
        for match in re.finditer(pattern, clean):
            name = clean_name(match.group(1))
            if name and not any(word in name for word in bad_words):
                return name

    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if title:
        title_name = clean_name(title.group(1))
        if title_name and title_name not in {"17图库", "未命名"}:
            return title_name
    return clean_name(fallback)

