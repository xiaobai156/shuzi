import re
from collections.abc import Iterable

from kill_numbers.text_utils import (
    as_list,
    clean_name,
    compact_text,
    html_to_text,
    normalize_issue,
    normalize_keyword,
    unique_keep_order,
)


# Direction is a business rule.  Generic parsing uses the configured
# issue_position_window when present and falls back to the project default.
CANDIDATE_REGION_WINDOW = 3
BROAD_KEYWORD_MARKERS = (
    "杀",
    "码",
    "十码",
    "10码",
    "五码",
    "六码",
    "七码",
)

# 站方偶尔把期数标签的「期」误写成形近字（心旷神怡 278 期写作 `278棋`）。
# 只放宽“期数标签”这一个字符；号码正文、关键词与开奖标记都不受影响。
ISSUE_MARKER_CHARS = "期棋"
ISSUE_MARKER_CLASS = f"[{ISSUE_MARKER_CHARS}]"

# 边界识别不负责解析合法期号：任意位数的期/棋标签都要截断上一期，
# 即使标签超出 1–999，也不能让其后的号码借入前一期。
ROW_LABEL_PATTERN = re.compile(rf"(?<!\d)\d+\s*{ISSUE_MARKER_CLASS}")

# 行首伪标签：`299:` 漏写「期」。只在行首成立，避免把正文里的
# 时间戳（如「发表于 10月27日 09:00」）误判成期号。这里只识别边界，
# 不把冒号标签作为合法期数参与捕获。
PSEUDO_LABEL_PATTERN = re.compile(r"^\s*\d+\s*[:：]")

NEXT_ISSUE_BOUNDARY_PATTERN = (
    rf"(?:(?<!\d)\d+\s*{ISSUE_MARKER_CLASS}|\n[ \t]*\d+[ \t]*[:：])"
)

# 相邻两行期号回升超过该幅度，判定为跨年接缝（如 001→365、005→350）。
# 正常同一轮内相邻行期号差为 1~2；跨年时最小回升是 365-002=363。
CYCLE_SEAM_JUMP = 300


def target_issue_region(target: dict) -> str | None:
    """Return the row-window direction without changing the page position."""
    return target.get("issue_position_region") or target.get("region")


def target_keywords(target: dict) -> list[str]:
    value = target.get("keywords") or []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]

def has_broad_keywords(target: dict) -> bool:
    normalized = "".join(compact_text(keyword) for keyword in target_keywords(target))
    return any(marker in normalized for marker in BROAD_KEYWORD_MARKERS)

def manual_risk_reason(target: dict) -> str:
    reasons = []
    if not target.get("anchor"):
        reasons.append("缺少 anchor")
    if not target.get("anchor") and has_broad_keywords(target) and not target.get("special_parser"):
        reasons.append("宽关键词")
    if target.get("allow_ambiguous"):
        reasons.append("allow_ambiguous")
    return "、".join(reasons)

def candidate_anchor_values(text: str, target: dict) -> list[str]:
    name = clean_name(target.get("name") or "")
    candidates = []
    if name and name != "未命名":
        candidates.extend(
            [
                name,
                f"作者:{name}",
                f"作者：{name}",
                f"作者\n{name}",
                f"{name} 发表于",
                f"[{name}]楼主",
                f"【{name}】",
            ]
        )

    text_value = html_to_text(text)
    compact_name = compact_text(name)
    for line in text_value.splitlines():
        compact_line = compact_text(line)
        if compact_name and compact_name in compact_line and len(compact_line) <= max(8, len(compact_name) + 16):
            candidates.append(line.strip())

    return unique_keep_order(candidates)

def find_dedicated_anchor_candidate(text: str, target: dict, issues: list[str]) -> str | None:
    try:
        base_found = extract_issue_numbers(
            text,
            issues,
            keywords=target.get("keywords"),
            expected_count=target.get("count"),
            position=target.get("position", "first"),
            strict_ambiguous=True,
            allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
            anchor=target.get("anchor"),
            stop_anchor=target.get("stop_anchor"),
            region=target_issue_region(target),
            issue_position_window=target.get("issue_position_window"),
            prefer_pending=target.get("prefer_pending_candidate", False),
        )
    except Exception:
        return None
    if not base_found:
        return None

    for anchor in candidate_anchor_values(text, target):
        if find_anchor_index(html_to_text(text), anchor) < 0:
            continue
        try:
            anchored_found = extract_issue_numbers(
                text,
                issues,
                keywords=target.get("keywords"),
                expected_count=target.get("count"),
                position=target.get("position", "first"),
                strict_ambiguous=True,
                allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
                anchor=anchor,
                stop_anchor=target.get("stop_anchor"),
                region=target_issue_region(target),
                issue_position_window=target.get("issue_position_window"),
            )
        except Exception:
            continue
        if anchored_found and anchored_found == base_found:
            return anchor
    return None

def valid_number(token: str) -> bool:
    try:
        number = int(token)
    except ValueError:
        return False
    return 1 <= number <= 49

def expand_number_tokens(tokens: list[str], relaxed: bool) -> list[str] | None:
    """把一段号码文本的原始 token 规范成两位数号码。

    严格模式只接受本来就被分隔好的两位数，任何异形 token 直接判整组无效。
    容错模式（relaxed）只多认两种站方排版疏漏，其余异形一律判无效：
      - 少标一个分隔符，把两个号粘成 4 位（`28.4002.14` → 28,40,02,14）；
      - 单个号漏印前导 0（`48.46.1` → 48,46,01）。
    两种都只在本段号码文本内重排，不跨块、不跨期，也不引入文本里没有的数字。
    """
    expanded: list[str] = []
    for token in tokens:
        if len(token) == 2 and valid_number(token):
            expanded.append(token)
            continue
        if not relaxed:
            return None
        if len(token) == 1 and valid_number(token):
            expanded.append(token.zfill(2))
            continue
        if len(token) == 4:
            head, tail = token[:2], token[2:]
            if valid_number(head) and valid_number(tail):
                expanded.extend([head, tail])
                continue
            return None
        return None
    return expanded


def find_number_groups(segment: str, relaxed: bool = False) -> list[list[str]]:
    before_open = re.split(r"[=＝]?\s*开\s*[:：]?", segment, maxsplit=1)[0]
    before_open = before_open.replace("杀", " ")

    def collect(source: str) -> list[list[str]]:
        found = []
        # 时间尾数不能跨行借给号码组；用非分隔符保留边界，避免把时间两侧的号码拼起来。
        source = re.sub(
            r"(?<!\d)\d{1,2}(?:[ \t]*[:：][ \t]*\d{2}){1,2}(?!\d)",
            "\x00",
            source,
        )
        # 日期同理：`2026-10-27` 的末段 27 会与下一行号码粘连。
        # 只遮蔽带 4 位年份的完整日期，2 位号码组（01-02-03）不受影响，
        # 因为号码组要求每段都是 2 位，4 位年份本来就不可能构成合法号码组。
        source = re.sub(
            r"(?<!\d)\d{4}[./\-年]\d{1,2}[./\-月]\d{1,2}日?(?!\d)",
            "\x00",
            source,
        )
        # 每个 match 都是一组独立号码。禁止把多组、开奖号、日期或其他栏目拼接来凑 count。
        # 优先抓一串 2 位数，分隔符可以是点、空格、逗号、顿号、加号等。
        # 分隔符必须存在，避免把日期 2020-10-02 19:41:06 当成号码。
        for match in re.finditer(
            r"(?<!\d)\d+(?:[\s.,，。、;；|/\\+\-]+\d+){2,}(?!\d)",
            source,
        ):
            nums = re.findall(r"\d+", match.group(0))
            expanded = expand_number_tokens(nums, relaxed)
            if expanded is not None:
                found.append(expanded)
        return found

    groups = collect(before_open)
    if not groups:
        # 号码可能整块落在“开”之后。开奖号防线按开奖内容分两种情况：
        #  - 开:0000 / 开:?? 属占位（本期未开奖），其后号码就是本期杀号，允许取；
        #  - 开:猪08 / 开:蛇14 属真实开奖结果：**同一行内**其后的号码是开奖号，
        #    必须丢弃；但换行之后的号码属于下一条记录，仍可正常取用。
        # 两种都只在本段内取，且截到下一个期号标签之前，避免跨期借值。
        after_open = re.split(r"[=＝]?\s*开\s*[:：]?", segment, maxsplit=1)
        if len(after_open) > 1:
            remainder = after_open[1]
            pending = bool(re.match(r"\s*(?:[?？0]{2,}|[?？]|0(?=\s|$))", remainder))
            open_line, _, following = remainder.partition("\n")
            if pending:
                # 占位：开所在行与其后都可能是本期号码。
                # 占位符本身（0000 / ?00 / ??）必须先剥掉，否则它会和紧跟的号码
                # 被同一个正则匹配成一组，长度校验把整组判废，本期号码全丢。
                boundary = len(remainder)
                for label in ROW_LABEL_PATTERN.finditer(remainder):
                    boundary = label.start()
                    break
                cleaned = re.sub(r"^\s*(?:[?？0]{2,}|[?？]|0(?=\s|$))", " ", remainder[:boundary])
                groups = collect(cleaned.replace("杀", " "))
            elif following.strip():
                # 真实开奖号：跳过开奖行本身，只看换行之后的内容
                boundary = len(following)
                for label in ROW_LABEL_PATTERN.finditer(following):
                    boundary = label.start()
                    break
                groups = collect(following[:boundary].replace("杀", " "))

    groups.sort(key=len, reverse=True)
    return groups


def number_groups_for_count(segment: str, expected_count: int | None = None) -> list[list[str]]:
    """取符合配置数量的号码组；严格切分拿不到时，才回退到容错切分。

    只有「本段严格切分一个够数的号码组都没有」时才启用容错，正常站点结果不变。
    """
    def keep(groups: list[list[str]]) -> list[list[str]]:
        return [
            group
            for group in groups
            if (not expected_count or len(group) == expected_count)
            and all(valid_number(number) for number in group)
        ]

    strict = keep(find_number_groups(segment))
    if strict or not expected_count:
        return strict
    return keep(find_number_groups(segment, relaxed=True))

def has_duplicate_numbers(numbers: list[str]) -> bool:
    return len(numbers) != len(set(numbers))

def has_pending_open_marker(segment: str) -> bool:
    return bool(re.search(r"开\s*[:：]?\s*(?:[?？]{1,2}|0{2,4})", segment))

def select_candidate(
    candidates: list[tuple[list[str], str]],
    position: str = "first",
    strict_ambiguous: bool = False,
    issue: str = "",
    allow_duplicate_numbers: bool = False,
    prefer_pending: bool = False,
) -> list[str] | None:
    if allow_duplicate_numbers:
        valid_candidates = candidates
    else:
        valid_candidates = [
            (numbers, segment)
            for numbers, segment in candidates
            if not has_duplicate_numbers(numbers)
        ]
    if not valid_candidates:
        return None

    distinct: list[tuple[list[str], str]] = []
    seen = set()
    for numbers, segment in valid_candidates:
        key = tuple(numbers)
        if key not in seen:
            seen.add(key)
            distinct.append((numbers, segment))

    if len(distinct) == 1:
        return distinct[0][0]

    pending = [
        (numbers, segment)
        for numbers, segment in distinct
        if has_pending_open_marker(segment)
    ]
    if prefer_pending and len(pending) == 1:
        # 站点把上一期记录重复标成当期时会出现同期号两个候选；
        # 只有当期未开奖记录带 开:0000/开:?? 标记，据此唯一确定当期号码。
        return pending[0][0]

    if strict_ambiguous:
        preview = " | ".join(",".join(numbers) for numbers, _ in distinct[:5])
        issue_text = f"{issue}期" if issue else "该期"
        raise ValueError(f"{issue_text} 候选不唯一，已停止输出避免抓错：{preview}")

    if len(pending) == 1:
        return pending[0][0]

    return distinct[-1][0] if position == "last" else distinct[0][0]

def cycle_seam_floor(text: str) -> int | None:
    """返回跨年接缝的字符位置（该位置之后属于上一轮）。

    页面按新→旧排列时（299, 298 … 001, 365, 364 …），001→365 处出现一次
    期号大幅回升，那里之后是**上一轮**的记录。调用方据此判断：
    只有当目标期本身落在接缝之后（即目标期只存在于上一轮）时才拒绝，
    正常的新→旧页面目标期在接缝之前，不受影响。
    """
    labels: list[tuple[int, int]] = []
    for match in iter_all_issue_segment_matches(text):
        try:
            value = int(normalize_issue(match.group(1)))
        except (TypeError, ValueError):
            continue
        labels.append((value, match.start()))
    if len(labels) < 2:
        return None

    # 相邻两行期号大幅回升 => 跨年接缝，接缝就在这一行
    for index in range(1, len(labels)):
        previous, current = labels[index - 1][0], labels[index][0]
        if current - previous >= CYCLE_SEAM_JUMP:
            return labels[index][1]
    return None


def issue_segment_matches(text: str, issue: str) -> list[re.Match]:
    issue = normalize_issue(issue)
    pattern = re.compile(
        rf"(?<!\d)0?{re.escape(issue)}\s*{ISSUE_MARKER_CLASS}(?P<body>.*?)(?={NEXT_ISSUE_BOUNDARY_PATTERN}|$)",
        re.S,
    )
    return list(pattern.finditer(text))


def segment_number_blocks(segment: str, label_end: int, issue: str = "") -> list[str]:
    """把本期段落切成“可归属本期”的号码块，跨期借值防线。

    规则：号码块必须出现在本期期号标签之后，且块所在行不得含**其他期**的期号标签。
    同一期号的重复展示行（站点常把当期重复贴一次）属于本期，必须保留——
    否则会把“同期两候选”的冲突吞掉。
    """
    blocks: list[str] = []
    current = normalize_issue(issue) if issue else ""
    body = segment[label_end:]
    # 段首可能还剩本期标签所在行的行尾内容，单独处理：该行属于本期。
    first_line, _, rest = body.partition("\n")
    if first_line.strip():
        blocks.append(first_line)
    offset = 0
    for line in rest.splitlines(keepends=True):
        start = offset
        offset += len(line)
        if not line.strip():
            continue
        labels = ROW_LABEL_PATTERN.findall(line)
        if labels:
            # 这一行带期号标签：只有标签等于本期才允许继续
            normalized = set()
            invalid_label = False
            for value in labels:
                try:
                    normalized.add(normalize_issue(value.rstrip(ISSUE_MARKER_CHARS)))
                except ValueError:
                    # 超范围标签仍是边界，但不能被当成合法期号参与比较。
                    invalid_label = True
            if invalid_label or not current or current not in normalized:
                break
        elif PSEUDO_LABEL_PATTERN.match(line):
            # 行首 `299:` 这类漏写「期」的伪标签，同样视为下一期的边界
            break
        blocks.append(rest[start:offset])
    return blocks

def issue_segments(text: str, issue: str) -> list[str]:
    return [m.group(0) for m in issue_segment_matches(text, issue)]

def iter_all_issue_segment_matches(text: str) -> Iterable[re.Match]:
    pattern = re.compile(
        rf"(?<!\d)0?(\d{{1,3}})\s*{ISSUE_MARKER_CLASS}(?P<body>.*?)(?={NEXT_ISSUE_BOUNDARY_PATTERN}|$)",
        re.S,
    )
    matches = pattern.finditer(text)
    first_match = next(matches, None)
    if first_match is not None:
        yield first_match
        yield from matches
        return

    fallback = re.compile(
        rf"(?<!\d)0?(\d{{2,3}})\s*{ISSUE_MARKER_CLASS}(?P<body>.*?)(?=(?:{NEXT_ISSUE_BOUNDARY_PATTERN}|\n\s*\d{{1,3}}[.,、\s])|$)",
        re.S,
    )
    yield from fallback.finditer(text)

def all_issue_segment_matches(text: str) -> list[re.Match]:
    return list(iter_all_issue_segment_matches(text))

def find_anchor_index(text: str, anchor: str, start: int = 0) -> int:
    direct = text.find(anchor, start)
    if direct >= 0:
        return direct

    normalized_anchor = normalize_keyword(anchor)
    if not normalized_anchor:
        return -1

    offset = 0
    for line in text.splitlines(keepends=True):
        if offset >= start and normalized_anchor in normalize_keyword(line):
            return offset
        offset += len(line)
    return -1

def find_last_anchor_index_before(text: str, anchor: str, end: int) -> int:
    direct = text.rfind(anchor, 0, end)
    if direct >= 0:
        return direct

    normalized_anchor = normalize_keyword(anchor)
    if not normalized_anchor:
        return -1

    candidate = -1
    offset = 0
    for line in text.splitlines(keepends=True):
        if offset >= end:
            break
        if normalized_anchor in normalize_keyword(line):
            candidate = offset
        offset += len(line)
    return candidate

def anchor_row_label_start(text: str, index: int) -> int:
    """把正文起点回退到锚点所在行的期数标签。

    站点常把最新一期写在锚点行的行首（如 `271期:屠龙宝刀 杀10码 《...》`）；
    若直接从锚点起截取，这一行自己的 `271期:` 标签会被裁掉，该期就永远匹配不到。
    只回退到标签本身，不纳入本行标签之前的任何文本。
    """
    if index <= 0:
        return index
    line_start = text.rfind("\n", 0, index) + 1
    matches = list(ROW_LABEL_PATTERN.finditer(text[line_start:index]))
    return line_start + matches[-1].start() if matches else index

def scope_text_by_anchor(
    text: str,
    anchors=None,
    stop_anchors=None,
) -> str:
    scoped, _start_index = scope_text_by_anchor_with_offset(text, anchors, stop_anchors)
    return scoped

def scope_text_by_anchor_with_offset(
    text: str,
    anchors=None,
    stop_anchors=None,
) -> tuple[str, int]:
    anchor_list = as_list(anchors)
    start_index = 0 if not anchor_list else -1
    for anchor in anchor_list:
        start_index = find_anchor_index(text, anchor)
        if start_index >= 0:
            break
    if start_index < 0:
        raise ValueError(f"没有找到正文锚点：{anchor_list}")
    start_index = anchor_row_label_start(text, start_index)

    end_index = len(text)
    for stop_anchor in as_list(stop_anchors):
        candidate = find_anchor_index(text, stop_anchor, start=start_index + 1)
        if candidate >= 0:
            end_index = min(end_index, candidate)
    if as_list(stop_anchors) and end_index == len(text):
        raise ValueError(f"没有找到正文结束锚点：{as_list(stop_anchors)}")

    return text[start_index:end_index], start_index

def normalize_region(region: str | None) -> str:
    value = normalize_keyword(region or "").lower()
    if value in {"top", "upper", "head", "first", "上", "顶部"}:
        return "top"
    if value in {"bottom", "lower", "tail", "last", "下", "尾部", "底部"}:
        return "bottom"
    return ""

def filter_candidates_by_region(
    candidates: list[tuple[list[str], str, int]],
    region: str | None,
    text_length: int | None = None,
    strict_window: bool = False,
    require_region: bool = False,
):
    region = normalize_region(region)
    if require_region and not region:
        return []
    if not region:
        return candidates

    ordered = sorted(candidates, key=lambda item: item[2])
    if region == "top":
        return ordered[:CANDIDATE_REGION_WINDOW]
    return ordered[-CANDIDATE_REGION_WINDOW:]

def needs_strict_region_window(candidates: list[tuple[list[str], str, int]]) -> bool:
    # Multiple candidates for one issue are always evaluated inside the same
    # fixed directional window before ambiguity is considered.
    return len(candidates) > 1

def issue_position_window_starts(
    text: str,
    keywords: list[str] | None,
    expected_count: int | None,
    region: str | None,
    issue_position_window: int | None,
    allow_duplicate_numbers: bool = False,
) -> set[int] | None:
    region = normalize_region(region)
    if not region:
        return None

    if (
        isinstance(issue_position_window, int)
        and not isinstance(issue_position_window, bool)
        and issue_position_window > 0
    ):
        window = issue_position_window
    else:
        window = CANDIDATE_REGION_WINDOW

    keyword_list = [normalize_keyword(keyword) for keyword in (keywords or []) if keyword]
    candidates: list[tuple[list[str], str, int]] = []
    candidate_indexes: dict[tuple[str, tuple[str, ...]], int] = {}
    seam_floor = cycle_seam_floor(text)
    for match in iter_all_issue_segment_matches(text):
        # 跨年借值防线：接缝之后是上一轮的记录，不参与本期窗口计算。
        if seam_floor is not None and match.start() >= seam_floor:
            continue
        segment = match.group(0)
        compact_segment = normalize_keyword(segment)
        if keyword_list and not any(keyword in compact_segment for keyword in keyword_list):
            continue
        label_end = match.start("body") - match.start()
        owned = "\n".join(
            segment_number_blocks(segment, label_end, match.group(1))
        )
        groups = [
            group
            for group in number_groups_for_count(owned, expected_count)
            if all(valid_number(number) for number in group)
            and (allow_duplicate_numbers or not has_duplicate_numbers(group))
        ]
        if groups:
            candidate = (groups[0], segment, match.start())
            key = (normalize_issue(match.group(1)), tuple(groups[0]))
            previous_index = candidate_indexes.get(key)
            if previous_index is not None:
                if region == "bottom":
                    candidates[previous_index] = candidate
                continue
            candidate_indexes[key] = len(candidates)
            candidates.append(candidate)
            # Top windows only depend on their first N valid rows.  Do not scan
            # the historical tail of a long user page once that boundary is known.
            if region != "bottom" and len(candidates) >= window:
                break

    ordered = sorted(candidates, key=lambda item: item[2])
    if region == "bottom":
        selected = ordered[-window:]
    else:
        selected = ordered[:window]
    return {start for _group, _segment, start in selected}

def extract_issue_numbers(
    text: str,
    issues: list[str],
    keywords: list[str] | None = None,
    expected_count: int | None = None,
    position: str = "first",
    strict_ambiguous: bool = False,
    allow_duplicate_numbers: bool = False,
    anchor=None,
    stop_anchor=None,
    region: str | None = None,
    issue_position_window: int | None = None,
    allowed_row_starts: set[int] | None = None,
    prefer_pending: bool = False,
) -> dict[str, list[str]]:
    found = {}
    full_text = html_to_text(text)
    text, scope_offset = scope_text_by_anchor_with_offset(full_text, anchor, stop_anchor)
    keyword_list = [normalize_keyword(keyword) for keyword in (keywords or []) if keyword]
    allowed_window_starts = issue_position_window_starts(
        text,
        keywords,
        expected_count,
        region,
        issue_position_window,
        allow_duplicate_numbers=allow_duplicate_numbers,
    )
    for issue in issues:
        candidates = []
        seam_floor = cycle_seam_floor(text)
        matches = issue_segment_matches(text, issue)
        # 跨年借值防线：如果目标期的全部匹配都落在接缝之后，说明本期只存在于
        # 上一轮（跨年借值），直接拒绝。新→旧页面上目标期在接缝之前，不受影响。
        if seam_floor is not None and matches and all(
            match.start() >= seam_floor for match in matches
        ):
            matches = []
        for match in matches:
            if allowed_window_starts is not None and match.start() not in allowed_window_starts:
                continue
            if allowed_row_starts is not None and scope_offset + match.start() not in allowed_row_starts:
                continue
            segment = match.group(0)
            compact_segment = normalize_keyword(segment)
            if keyword_list and not any(keyword in compact_segment for keyword in keyword_list):
                continue
            # 跨期借值防线：只在本期标签之后、且不含新期号标签的行里取号码。
            label_end = match.start("body") - match.start()
            owned = "\n".join(segment_number_blocks(segment, label_end, issue))
            groups = number_groups_for_count(owned, expected_count) if owned.strip() else []
            for group in groups:
                if expected_count and len(group) != expected_count:
                    # 数量不符直接拒绝，不能从其他组或其他位置补数字来凑够 count。
                    continue
                candidates.append((group, segment, scope_offset + match.start()))
        if candidates:
            strict_region = needs_strict_region_window(candidates)
            normalized_region = normalize_region(region)
            # Row positions have already been windowed. Do not truncate groups
            # again: that hides a fourth conflicting group in one valid row.
            candidate_position = position
            candidate_strict_ambiguous = strict_ambiguous
            if strict_region and normalized_region:
                candidate_position = "last" if normalized_region == "bottom" else "first"
            selected = select_candidate(
                [(group, segment) for group, segment, _ in candidates],
                position=candidate_position,
                strict_ambiguous=candidate_strict_ambiguous,
                issue=issue,
                allow_duplicate_numbers=allow_duplicate_numbers,
                prefer_pending=prefer_pending,
            )
            if selected:
                found[issue] = selected
    return found
