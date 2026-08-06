from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class TargetContract:
    url: str
    name: str = ""
    keywords: tuple[str, ...] = ()
    count: int | None = None
    region: str = ""
    anchor: str = ""
    stop_anchor: str = ""
    issue_position_window: int | None = None
    special_parser: str = ""
    article_identity: str = ""
    api_url: str = ""
    encoding: str = ""
    position: str = "first"
    allow_ambiguous: bool = False
    allow_duplicate_numbers: bool = False
    disabled: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TargetContract":
        known = {
            "url",
            "name",
            "keywords",
            "count",
            "region",
            "anchor",
            "stop_anchor",
            "issue_position_window",
            "special_parser",
            "article_identity",
            "api_url",
            "encoding",
            "position",
            "allow_ambiguous",
            "allow_duplicate_numbers",
            "disabled",
        }
        return cls(
            url=str(value.get("url") or ""),
            name=str(value.get("name") or ""),
            keywords=tuple(str(item) for item in (value.get("keywords") or ()) if item),
            count=value.get("count"),
            region=str(value.get("region") or ""),
            anchor=str(value.get("anchor") or ""),
            stop_anchor=str(value.get("stop_anchor") or ""),
            issue_position_window=value.get("issue_position_window"),
            special_parser=str(value.get("special_parser") or ""),
            article_identity=str(value.get("article_identity") or ""),
            api_url=str(value.get("api_url") or ""),
            encoding=str(value.get("encoding") or ""),
            position=str(value.get("position") or "first"),
            allow_ambiguous=bool(value.get("allow_ambiguous", False)),
            allow_duplicate_numbers=bool(value.get("allow_duplicate_numbers", False)),
            disabled=bool(value.get("disabled", False)),
            extra={key: item for key, item in value.items() if key not in known},
        )

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "url": self.url,
            "name": self.name,
            "keywords": list(self.keywords),
            "count": self.count,
            "region": self.region,
            "anchor": self.anchor,
            "stop_anchor": self.stop_anchor,
            "issue_position_window": self.issue_position_window,
            "special_parser": self.special_parser,
            "article_identity": self.article_identity,
            "api_url": self.api_url,
            "encoding": self.encoding,
            "position": self.position,
            "allow_ambiguous": self.allow_ambiguous,
            "allow_duplicate_numbers": self.allow_duplicate_numbers,
            "disabled": self.disabled,
        }
        value.update(self.extra)
        return {key: item for key, item in value.items() if item not in ("", None, (), [], False)}

