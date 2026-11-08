"""往豆包写之前按槽位裁。个人库优先，剩下的按频次补。"""
from __future__ import annotations

from tsvutil import Row

PERSONAL_MARKS = ("doubao", "wetype", "both", "personal", "usr")


def is_personal(row: Row) -> bool:
    src = (row.source or "").lower()
    if not src:
        return False
    parts = [p.strip() for p in src.replace("+", " ").replace(":", " ").split() if p.strip()]
    if any(p in PERSONAL_MARKS for p in parts):
        return True
    return src.startswith("doubao") or src.startswith("wetype")


def clip(
    rows: list[Row],
    *,
    limit: int,
    prefer_personal: bool = True,
) -> tuple[list[Row], dict[str, int]]:
    """按 (word, pinyin) 去重后裁到 limit 条。

    重复键：prefer_personal 时留下个人库那条（频次取较大值）。
    槽位：先锁个人库；个人已超限则个人按频次留前 limit；否则用外来词按频次填剩余。
    """
    if limit <= 0:
        raise ValueError("budget limit 必须 > 0")

    merged: dict[tuple[str, str], Row] = {}
    for r in rows:
        k = r.key()
        old = merged.get(k)
        if old is None:
            merged[k] = Row(r.word, r.pinyin, r.freq, r.source, r.syll, r.note, dict(r.extra))
            continue
        take_new = False
        if prefer_personal:
            new_p, old_p = is_personal(r), is_personal(old)
            if new_p and not old_p:
                take_new = True
            elif old_p and not new_p:
                take_new = False
            else:
                take_new = r.freq > old.freq
        elif r.freq > old.freq:
            take_new = True
        if take_new:
            keep = Row(r.word, r.pinyin, max(r.freq, old.freq), r.source, r.syll or old.syll, r.note, dict(r.extra))
            merged[k] = keep
        else:
            old.freq = max(old.freq, r.freq)
            if r.syll and not old.syll:
                old.syll = r.syll

    items = list(merged.values())
    personal = [r for r in items if prefer_personal and is_personal(r)]
    if prefer_personal:
        # 按身份切分，不用 `r not in personal`：那是对整个列表做 O(n^2) 的逐字段相等比较
        personal_ids = {id(r) for r in personal}
        others = [r for r in items if id(r) not in personal_ids]
    else:
        others = items

    personal.sort(key=lambda r: (-r.freq, r.word, r.pinyin))
    others.sort(key=lambda r: (-r.freq, r.word, r.pinyin))

    if len(personal) >= limit:
        kept = personal[:limit]
        dropped = personal[limit:] + others
    else:
        room = limit - len(personal)
        kept = personal + others[:room]
        dropped = others[room:]

    stats = {
        "in": len(rows),
        "unique": len(items),
        "limit": limit,
        "kept": len(kept),
        "kept_personal": sum(1 for r in kept if is_personal(r)),
        "added": sum(1 for r in kept if not is_personal(r)),
        "dropped": len(dropped),
    }
    return kept, stats
