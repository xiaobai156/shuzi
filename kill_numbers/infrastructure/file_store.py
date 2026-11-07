import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(
    path: str | Path, content: str, *, newline: str | None = None
) -> None:
    """原子写文本。

    newline 沿用 open() 的语义：默认按平台翻译（Windows 下写 CRLF，正式 TXT
    保持既有换行）；JSON 等入库文件传 "" 强制 LF，避免每次提交出现整文件差异。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline=newline,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def atomic_write_json(
    path: str | Path,
    value: Any,
    *,
    ensure_ascii: bool = False,
    indent: int = 2,
    trailing_newline: bool = True,
) -> None:
    content = json.dumps(value, ensure_ascii=ensure_ascii, indent=indent)
    atomic_write_text(path, content + ("\n" if trailing_newline else ""), newline="")
