#!/usr/bin/env python3
"""行业词库清洗。真源是归档 industry-dict 的规则，不另起一套。

    python3 clean_dict.py input.txt output.txt
    cat words.txt | python3 clean_dict.py - output.txt
    python3 clean_dict.py --industry input.txt output.txt   # 再建库时多滤叠词/语气助词
"""
from __future__ import annotations

import re
import sys

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ACRONYM = re.compile(r"^[A-Z][A-Z0-9]{1,6}$")
_PARTICLES = ("的", "了", "吧", "吗", "呢", "啊")


def clean(words: list[str]) -> list[str]:
    """归档 industry-dict 原规则。standalone CLI 默认走这条。"""
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        w = w.strip().lstrip("\ufeff").strip()
        if not w:
            continue
        if any(c in w for c in "?<>&\ufffd"):
            continue
        if not _CJK.search(w) and not _ACRONYM.match(w):
            continue
        n = len(w)
        if n < 2 or n > 12:
            continue
        if len(set(w)) <= 2 and n >= 5:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def filter_industry(words: list[str]) -> list[str]:
    """建库额外一层：叠词、短语气助词。个人词库碎片过滤在 optimize，不放这里。"""
    out: list[str] = []
    for w in words:
        n = len(w)
        if n >= 4 and w[: n // 2] == w[n // 2 :]:
            continue
        if w.endswith(_PARTICLES) and n <= 4:
            continue
        out.append(w)
    return out


def clean_industry(words: list[str]) -> list[str]:
    return filter_industry(clean(words))


def main() -> int:
    argv = [a for a in sys.argv[1:] if a != "--industry"]
    industry = "--industry" in sys.argv[1:]
    if len(argv) < 2:
        print("用法: clean_dict.py [--industry] <input.txt|-> <output.txt>", file=sys.stderr)
        return 1
    src, dst = argv[0], argv[1]
    text = sys.stdin.read() if src == "-" else open(src, encoding="utf-8", errors="replace").read()
    lines = text.splitlines()
    cleaned = clean_industry(lines) if industry else clean(lines)
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(cleaned) + ("\n" if cleaned else ""))
    print(f"输入 {len(lines)} 词 → 输出 {len(cleaned)} 词")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
