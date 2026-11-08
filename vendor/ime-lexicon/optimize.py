"""个人词库修正：明显英文笔误、词对拼音错、噪声。

只改表，不碰正在用的输入法文件。
明显的才自动改；吃不准的进待确认。
"""
from __future__ import annotations

import re
from collections import Counter

from tsvutil import Row, merge_rows

# 一眼能断、已在本机库里验证过的笔误。缩写（kigi）不进这里。
TYPO_MAP = {
    "agnet": "agent",
    "flsah": "flash",
    "clade": "claude",
    "gork": "grok",
    "grokgrok": "grok",
    "shft": "shift",
    "skilsub": "skill",
    "prma": "proma",
    "boos": "boss",
}

_V2U = "v2u"
_LATIN = re.compile(r"[A-Za-z]")


def _letters(s: str) -> str:
    return "".join(c.lower() for c in s if c.isascii() and c.isalpha())


def _edit1(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    i = j = skip = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            skip += 1
            if skip > 1:
                return False
            j += 1
    return True


def is_pinyin_dump(word: str) -> bool:
    if not word.isascii() or not word.isalpha() or not word.islower():
        return False
    if len(word) < 8:
        return False
    return sum(c in "aeiou" for c in word) >= 3


# 白霜点名过：不是词的词。个人库也删。
_PARTICLE_WORDS = {"的了", "的吧", "的吗", "的呢", "的啊", "了吧", "了啊", "的的"}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def cjk_len(word: str) -> int:
    return len(_CJK_RE.findall(word))


def is_particle_junk(word: str) -> bool:
    if word in _PARTICLE_WORDS:
        return True
    return len(word) <= 4 and word.endswith(("的了", "的吧"))


def is_overlong(word: str, limit: int) -> bool:
    return cjk_len(word) >= limit


def is_freq1_residual(word: str, freq: int) -> bool:
    """行业表才用：频次=1 的分词残片。"""
    if freq > 1:
        return False
    if word[: len(word) // 2] == word[len(word) // 2 :] and len(word) >= 4:
        return True
    return 2 <= len(word) <= 3 and word.endswith(("的", "了", "是", "在", "吧", "吗", "呢", "啊"))


def is_fragment(word: str) -> bool:
    if len(word) <= 1:
        return True
    if word[: len(word) // 2] == word[len(word) // 2 :] and len(word) >= 4:
        return True
    if cjk_len(word) >= 10:
        return True
    if word.endswith(("的", "了", "吧", "吗", "呢", "啊")) and len(word) <= 4:
        return True
    return False


def subtract_reason(word: str, freq: int, *, profile: str, drop_fragments: bool) -> str:
    """返回剔除原因；空字符串表示留下。"""
    if is_particle_junk(word):
        return "碎片剔除:的了"
    if profile == "industry":
        if is_overlong(word, 10):
            return "碎片剔除:过长"
        if is_freq1_residual(word, freq):
            return "碎片剔除:频次1残片"
        if is_fragment(word):
            return "碎片剔除"
        return ""
    # personal：默认只砍「的了」类。超长句子（≥16 字）才砍。
    if is_overlong(word, 16):
        return "碎片剔除:过长"
    if drop_fragments and is_fragment(word):
        return "碎片剔除"
    return ""


def _best_casing(rows: list[Row], dest: str) -> str:
    hits = [r for r in rows if r.word.lower() == dest]
    if not hits:
        return dest
    return max(hits, key=lambda r: r.freq).word


def _pinyin_should_fix(word: str, pinyin: str, freq: int) -> bool:
    w, p = _letters(word), _letters(pinyin)
    if not w or not p or w == p:
        return False
    if p == w[: len(p)] and len(p) >= 3 and freq >= 10:
        return False  # 高频缩写拼音（skil / claud）
    return _edit1(p, w)


def optimize(
    rows: list[Row],
    *,
    drop: set[str] | None = None,
    auto: bool = True,
    drop_fragments: bool = False,
    profile: str = "personal",
) -> tuple[list[Row], list[Row], list[Row]]:
    """返回 (词库, 已改记录, 待确认)。

    profile=personal：只砍「的了/的吧」、超长句、v2u。习惯词默认留。
    profile=industry：再砍过长、频次=1 残片、分词碎片。
    """
    if profile not in {"personal", "industry"}:
        raise ValueError(f"未知 profile: {profile}")
    drop = {x.strip() for x in (drop or set()) if x.strip()}
    work = [Row(r.word, r.pinyin, r.freq, r.source, r.syll, r.note, dict(r.extra)) for r in rows]
    changed: list[Row] = []
    review: list[Row] = []

    kept: list[Row] = []
    for r in work:
        if r.word in drop:
            changed.append(Row(r.word, r.pinyin, r.freq, r.source, r.syll, "删除", dict(r.extra)))
            continue
        kind = (r.extra.get("kind") or r.source or "").lower()
        if kind == _V2U or is_pinyin_dump(r.word):
            changed.append(Row(r.word, r.pinyin, r.freq, r.source, r.syll, "噪声剔除", dict(r.extra)))
            continue
        why = subtract_reason(r.word, r.freq, profile=profile, drop_fragments=drop_fragments)
        if why:
            changed.append(Row(r.word, r.pinyin, r.freq, r.source, r.syll, why, dict(r.extra)))
            continue
        kept.append(r)

    freq_by = Counter()
    for r in kept:
        freq_by[r.word.lower()] += max(r.freq, 1)
    # 近形词扫描是 O(英文词 × 全表)，_letters 每对重算是实测热点（50k 26.7s→2.7s），预计算一次
    letters_by = {w: _letters(w) for w in freq_by}

    if auto:
        by_word: dict[str, list[Row]] = {}
        for r in kept:
            by_word.setdefault(r.word.lower(), []).append(r)
        rewritten: list[Row] = []
        for r in kept:
            low = r.word.lower()
            dest = TYPO_MAP.get(low)
            if dest:
                new_word = _best_casing(kept, dest)
                note = f"{r.word}→{new_word}"
                targets = by_word.get(dest, [])
                if targets:
                    tgt = max(targets, key=lambda x: (x.pinyin.lower() == dest, x.freq))
                    tgt.freq += r.freq
                    tgt.note = f"{tgt.note}；{note}" if tgt.note else note
                else:
                    rewritten.append(
                        Row(new_word, dest if not r.pinyin or r.pinyin == r.word else r.pinyin,
                            r.freq, r.source, r.syll, note, dict(r.extra))
                    )
                changed.append(Row(r.word, r.pinyin, r.freq, r.source, r.syll, note, dict(r.extra)))
                continue
            if r.pinyin and _pinyin_should_fix(r.word, r.pinyin, r.freq):
                new_py = _letters(r.word)
                note = f"拼音 {r.pinyin}→{new_py}"
                hit = next(
                    (x for x in kept if x is not r and x.word == r.word and _letters(x.pinyin) == new_py),
                    None,
                )
                if hit:
                    hit.freq += r.freq
                    hit.note = f"{hit.note}；{note}" if hit.note else note
                    if hit.pinyin != new_py:
                        hit.pinyin = new_py
                else:
                    rewritten.append(Row(r.word, new_py, r.freq, r.source, r.syll, note, dict(r.extra)))
                changed.append(Row(r.word, r.pinyin, r.freq, r.source, r.syll, note, dict(r.extra)))
                continue
            if _LATIN.search(r.word) and low not in TYPO_MAP:
                wl = _letters(r.word)
                # 只对「小写完整英文词」猜笔误：全大写缩写（AGI/LLM/UI）、单双字母（b/C/ox）、
                # 带数字（V4/P0）都是合法输入，不是笔误，不提示。
                if len(wl) >= 4 and r.word.islower() and not any(ch.isdigit() for ch in r.word):
                    near = [
                        w for w, c in freq_by.items()
                        if w != low and c >= 8 and abs(len(w) - len(wl)) <= 1 and _edit1(wl, letters_by[w])
                    ]
                    if near and freq_by[low] <= 3:
                        r = Row(r.word, r.pinyin, r.freq, r.source, r.syll,
                                f"疑似→{near[0]}", dict(r.extra))
                        review.append(r)
            rewritten.append(r)
        kept = rewritten

    kept = merge_rows(kept)
    return kept, changed, review
