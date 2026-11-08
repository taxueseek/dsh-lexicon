"""中间表：word / pinyin / freq / source / syll / note。

所有子命令只认这张表。引擎特有列（ptime/kind）读进来放进 extra，写回时丢掉。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


FIELDS = ("word", "pinyin", "freq", "source", "syll", "note")


@dataclass
class Row:
    word: str
    pinyin: str = ""
    freq: int = 1
    source: str = ""
    syll: str = ""
    note: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def key(self) -> tuple[str, str]:
        return (self.word, self.pinyin)


def _as_int(v: object, default: int = 1) -> int:
    try:
        return int(str(v).strip() or default)
    except ValueError:
        return default


def load(path: Path) -> list[Row]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return []
    sample = "\n".join(lines[:5])
    if "\t" in sample or path.suffix.lower() in {".tsv", ".csv"}:
        delim = "\t" if "\t" in sample else ","
        reader = csv.DictReader(lines, delimiter=delim)
        if reader.fieldnames and any(h and h.strip() == "word" for h in reader.fieldnames):
            out: list[Row] = []
            for rec in reader:
                word = (rec.get("word") or "").strip()
                if not word:
                    continue
                extra = {k: v for k, v in rec.items() if k and k not in FIELDS and v}
                out.append(
                    Row(
                        word=word,
                        pinyin=(rec.get("pinyin") or "").strip(),
                        freq=_as_int(rec.get("freq"), 1),
                        source=(rec.get("source") or rec.get("kind") or "").strip(),
                        syll=(rec.get("syll") or "").strip(),
                        note=(rec.get("note") or "").strip(),
                        extra=extra,
                    )
                )
            return out
    rows: list[Row] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "\t" in s:
            word, *rest = s.split("\t")
            rows.append(Row(word=word.strip(), pinyin=(rest[0] if rest else "").strip()))
        else:
            rows.append(Row(word=s))
    return rows


def dump(path: Path, rows: list[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    extra_keys: list[str] = []
    for r in rows:
        for k in r.extra:
            if k not in extra_keys:
                extra_keys.append(k)
    fields = list(FIELDS) + sorted(extra_keys)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=fields, delimiter="\t", lineterminator="\n",
            restval="", extrasaction="ignore",
        )
        w.writeheader()
        for r in rows:
            row = {k: getattr(r, k, "") for k in FIELDS}
            row.update(r.extra)
            w.writerow(row)


def merge_rows(rows: list[Row]) -> list[Row]:
    """同词同拼音合并，频次取大，来源去重。"""
    idx: dict[tuple[str, str], Row] = {}
    for r in rows:
        k = r.key()
        old = idx.get(k)
        if old is None:
            idx[k] = Row(r.word, r.pinyin, r.freq, r.source, r.syll, r.note, dict(r.extra))
            continue
        if r.freq > old.freq:
            old.freq = r.freq
        if r.syll and not old.syll:
            old.syll = r.syll
        if r.source and r.source not in old.source.split("+"):
            old.source = f"{old.source}+{r.source}" if old.source else r.source
        if r.note and r.note not in old.note:
            old.note = f"{old.note}；{r.note}" if old.note else r.note
    out = list(idx.values())
    out.sort(key=lambda x: (-x.freq, x.word, x.pinyin))
    return out
