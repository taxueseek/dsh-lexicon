"""从词表或正文收行业 / 定制词。不联网。

搜索在 search.py（归档 industry-dict 第 2 步）。这里只合并、分类、清洗。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from clean_dict import clean_industry
from search import SCHEMAS, detect_schema, known_categories
from tsvutil import Row, merge_rows

# 常见会改整词读音的多音字。整词进扩展层，不跟「引擎自动注音」大库混。
_HETERONYM = set("行长重还得地了着数乐差传弹调降觉角卡系省率纤扎只中种转倒干间强少朝和会教空落没难散色塞削血咽应与载脏涨正")

LAYERS = ("核心", "扩展", "大词库")

THUOCL_FILES = {
    "IT": "THUOCL_IT.txt",
    "财经": "THUOCL_caijing.txt",
    "成语": "THUOCL_chengyu.txt",
    "地名": "THUOCL_diming.txt",
    "历史名人": "THUOCL_lishimingren.txt",
    "诗词": "THUOCL_poem.txt",
    "食物": "THUOCL_food.txt",
    "医学": "THUOCL_medical.txt",
    "动物": "THUOCL_animal.txt",
    "法律": "THUOCL_law.txt",
    "汽车": "THUOCL_car.txt",
}

_CJK = re.compile(r"[\u4e00-\u9fff]{2,8}")
_LATIN_ID = re.compile(r"\b[A-Za-z][A-Za-z0-9+\-/.]{1,15}\b")
_CATS = known_categories()
_STOP = {
    "可以", "这个", "一个", "没有", "不是", "我们", "他们", "因为", "所以",
    "如果", "还是", "什么", "怎么", "这样", "那样", "自己", "以及", "或者",
    "the", "and", "for", "with", "from", "this", "that",
}


def is_junk(word: str) -> bool:
    w = word.strip()
    if w.lower() in _STOP:
        return True
    return not clean_industry([w])


def _split_cat(s: str) -> tuple[str, str]:
    """词\\t材料 或 材料:磷酸铁锂。分类进 note。"""
    s = s.strip()
    if "\t" in s:
        a, b = s.split("\t", 1)
        a, b = a.strip(), b.strip()
        if b in _CATS:
            return a, b
        if a in _CATS:
            return b, a
        return a, ""
    if ":" in s:
        a, b = s.split(":", 1)
        a, b = a.strip(), b.strip()
        if a in _CATS:
            return b, a
        if b in _CATS:
            return a, b
    return s, ""


def from_lines(lines: list[str], *, source: str) -> list[Row]:
    rows: list[Row] = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if " " in s and "\t" not in s and len(s) > 12 and len(_CJK.findall(s)) >= 3:
            continue
        word, cat = _split_cat(s)
        if not is_junk(word):
            rows.append(Row(word=word, source=source, freq=8, note=cat))
    return rows


def from_prose(text: str, *, source: str) -> list[Row]:
    """正文兜底：只收短 CJK / 大写缩写。默认词表走 from_lines。"""
    rows: list[Row] = []
    for m in _CJK.findall(text):
        if not is_junk(m):
            rows.append(Row(word=m, source=source, freq=8))
    for m in _LATIN_ID.findall(text):
        if not is_junk(m):
            rows.append(Row(word=m, source=source, freq=8))
    return merge_rows(rows)


def category_counts(rows: list[Row], *, topic: str = "") -> dict[str, int]:
    c: Counter[str] = Counter()
    for r in rows:
        cat = r.note if r.note else "未分类"
        c[cat] += 1
    order = SCHEMAS.get(detect_schema(topic), ())
    keys = [k for k in order if c[k]]
    keys.extend(k for k in c if k not in keys and k != "未分类")
    if c["未分类"]:
        keys.append("未分类")
    return {k: c[k] for k in keys}


def thuocl_path(kind: str) -> Path:
    import paths as ime_paths

    name = THUOCL_FILES.get(kind) or THUOCL_FILES.get(kind.upper())
    if not name:
        known = "、".join(THUOCL_FILES)
        raise ValueError(f"未知 THUOCL 类别 {kind}，可选：{known}")
    return ime_paths.research_dict_dir() / "06-清华THUOCL" / "data" / name


def from_thuocl(path: Path, *, kind: str) -> list[Row]:
    """THUOCL：词\\tDF。当行业种子，不当时 apply 源。"""
    rows: list[Row] = []
    src = f"thuocl:{kind}"
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        word, *rest = s.split("\t")
        word = word.strip()
        if not word or is_junk(word):
            continue
        freq = 8
        if rest:
            try:
                freq = max(1, int(str(rest[0]).strip() or 8))
            except ValueError:
                freq = 8
        rows.append(Row(word=word, source=src, freq=freq, note=kind))
    return rows


def try_pinyin(word: str) -> str:
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return ""
    parts = [p for p in lazy_pinyin(word, style=Style.NORMAL) if p]
    return " ".join(parts)


def is_polyphone(word: str) -> bool:
    return any(ch in _HETERONYM for ch in word)


def classify_layer(row: Row) -> str:
    """核心必须有拼音；多音进扩展；无拼音进大词库（禁止 apply）。"""
    if not (row.pinyin or "").strip():
        return "大词库"
    if is_polyphone(row.word):
        return "扩展"
    return "核心"


def apply_ok(row: Row) -> bool:
    return classify_layer(row) in {"核心", "扩展"}


def layer_rows(rows: list[Row], *, fill_pinyin: bool = True) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {k: [] for k in LAYERS}
    for r in rows:
        item = Row(r.word, r.pinyin, r.freq, r.source, r.syll, r.note, dict(r.extra))
        if fill_pinyin and not item.pinyin.strip():
            item.pinyin = try_pinyin(item.word)
        layer = classify_layer(item)
        if layer != "大词库":
            item.extra["layer"] = layer
        else:
            item.extra["layer"] = layer
            item.extra["apply"] = "no"
        out[layer].append(item)
    return out


def build(
    paths: list[Path],
    terms: list[str],
    *,
    topic: str,
    prose: bool = False,
    seeds: list[str] | None = None,
) -> list[Row]:
    src = f"build:{topic}" if topic else "build"
    rows: list[Row] = []
    for t in terms:
        word, cat = _split_cat(t)
        if word and not is_junk(word):
            rows.append(Row(word=word, source=src, freq=8, note=cat))
    for spec in seeds or []:
        kind = spec.split(":", 1)[-1] if spec.lower().startswith("thuocl:") else spec
        p = thuocl_path(kind)
        if not p.exists():
            raise FileNotFoundError(f"THUOCL 种子不存在：{kind}（先下载到 研究/06-清华THUOCL）")
        rows.extend(from_thuocl(p, kind=kind))
    for p in paths:
        text = p.read_text(encoding="utf-8")
        tag = f"{src}:{p.name}"
        if prose:
            rows.extend(from_prose(text, source=tag))
            continue
        lines = text.splitlines()
        short = sum(1 for ln in lines if 0 < len(ln.strip()) <= 16)
        if lines and short / max(len(lines), 1) >= 0.6:
            rows.extend(from_lines(lines, source=tag))
        else:
            rows.extend(from_prose(text, source=tag))
    return merge_rows(rows)

