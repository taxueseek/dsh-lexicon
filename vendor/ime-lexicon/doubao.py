"""豆包 usr.dat 文件层导入导出。只读写传入路径，不碰正在运行的 IME 文件。"""
from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from codec import (
    decrypt_str,
    decrypt_u16,
    encrypt_str,
    encrypt_u16,
    latin_syllables,
    normalize_pinyin,
    split_greedy,
)

HDR_N = 16
# 豆包个人库槽数不硬编码：0.9.7 起从 5000 扩容到 7500，decode/rebuild 都以 header 实读为准。
ATTR_SIZE = 16


@dataclass
class Item:
    word: str
    pinyin: str
    freq: int
    ptime: int
    stamp: int
    flags: int
    syll: list[int]
    slot: int = -1
    off: int = -1

    def key_bytes(self) -> bytes:
        if not self.syll:
            return b""
        return struct.pack(f"<{len(self.syll)}H", *self.syll)


def _parse_header(data: bytes) -> tuple[int, ...]:
    if len(data) < 64:
        raise ValueError("usr.dat too small")
    return struct.unpack_from(f"<{HDR_N}I", data[:64])


def header_caps(path: Path) -> dict[str, int]:
    """槽数 / 已用 / 词容量。槽是条目，不是汉字。"""
    hdr = _parse_header(path.read_bytes())
    return {"slots": hdr[8], "used": hdr[9], "word_cap": hdr[7]}


def decode_file(path: Path) -> list[Item]:
    data = path.read_bytes()
    hdr = _parse_header(data)
    slot_off, nslot, used, val_off, val_size = hdr[6], hdr[8], hdr[9], hdr[10], hdr[11]
    # 槽表偏移与槽数都以 header 实读为准；豆包 0.9.7 起槽数从 5000 扩容到 7500，不硬编码
    slots = struct.unpack_from(f"<{nslot}I", data, slot_off)
    items: list[Item] = []
    for i, off in enumerate(slots):
        if off == 0 or off >= val_size:
            continue
        rec = _parse_value(data[val_off + off :])
        if rec is None:
            continue
        rec.slot = i
        rec.off = off
        items.append(rec)
    return items


def _parse_value(blob: bytes) -> Item | None:
    if len(blob) < 0x16:
        return None
    freq, ptime, stamp, flags = struct.unpack_from("<4I", blob, 0)
    sl = struct.unpack_from("<H", blob, 16)[0]
    if sl == 0 or sl % 2 or 0x14 + sl > len(blob):
        return None
    syll = list(struct.unpack(f"<{sl // 2}H", blob[0x12 : 0x12 + sl]))
    plen = struct.unpack_from("<H", blob, 0x12 + sl)[0]
    payload = blob[0x14 + sl : 0x14 + sl + plen]
    if len(payload) < 4:
        return None
    pyl = struct.unpack_from("<H", payload, 0)[0]
    if 2 + pyl + 2 > len(payload):
        return None
    try:
        pinyin = decrypt_str(payload[2 : 2 + pyl])
    except Exception:
        return None
    wn = struct.unpack_from("<H", payload, 2 + pyl)[0]
    word = decrypt_u16(payload[2 + pyl + 2 : 2 + pyl + 2 + wn * 2])
    if not word:
        return None
    return Item(
        word=word,
        pinyin=pinyin,
        freq=freq,
        ptime=ptime,
        stamp=stamp,
        flags=flags,
        syll=syll,
    )


def encode_value(it: Item) -> bytes:
    attr = struct.pack("<4I", it.freq & 0xFFFFFFFF, it.ptime, it.stamp, it.flags)
    syll = it.syll or latin_syllables(it.pinyin)
    if not syll:
        raise ValueError(f"no syllables for {it.word!r} {it.pinyin!r}")
    key = struct.pack(f"<{len(syll)}H", *syll)
    py_b = encrypt_str(it.pinyin)
    wd_b = encrypt_u16(it.word)
    payload = struct.pack("<H", len(py_b)) + py_b + struct.pack("<H", len(it.word)) + wd_b
    return attr + struct.pack("<H", len(key)) + key + struct.pack("<H", len(payload)) + payload


def build_syll_map(items: Iterable[Item]) -> dict[str, int]:
    """从已有词条用贪心切分拼音，多数表决得到音节 ID。切分表收口在 codec.split_greedy。"""
    votes: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for it in items:
        parts = split_greedy(normalize_pinyin(it.pinyin))
        if not parts or len(parts) != len(it.syll):
            continue
        for p, sid in zip(parts, it.syll):
            votes[p][sid] += 1
    return {p: max(ids, key=ids.get) for p, ids in votes.items()}


def encode_syllables(pinyin: str, word: str, syll_map: dict[str, int]) -> list[int]:
    if all("a" <= ch.lower() <= "z" or ch == "'" for ch in pinyin) and not any(
        "\u4e00" <= ch <= "\u9fff" for ch in word
    ):
        sy = latin_syllables(pinyin)
        if sy:
            return sy
    # 中文：按音节表切（表收口在 codec，豆包/微信共用）
    parts = split_greedy(normalize_pinyin(pinyin))
    if parts is None:
        raise ValueError(f"无法切分拼音 {pinyin!r}")
    ids: list[int] = []
    for p in parts:
        if p not in syll_map:
            raise ValueError(f"音节无 ID：{p}（来自 {pinyin} / {word}）")
        ids.append(syll_map[p])
    return ids


def rebuild(base: Path, items: list[Item], dest: Path) -> dict:
    """以 base 的头/扩展区为模板，把 items 重打包进 dest。"""
    src = base.read_bytes()
    hdr = list(_parse_header(src))
    slot_count = hdr[8]  # 槽数以目标库 header 实读为准（豆包 0.9.7 起 7500）
    # 布局全部以 header 实读为准：头长 hdr[2]、槽表偏移 hdr[6]。
    # 槽数已历经 5000→7500→10000，扩展区大小同样可能随版本漂移，不能硬编码 64+140。
    slot_off = hdr[6]
    extend = src[hdr[2] : slot_off]
    items_sorted = sorted(items, key=lambda it: it.key_bytes())
    if len(items_sorted) > slot_count:
        raise ValueError(f"词条 {len(items_sorted)} 超过槽位 {slot_count}")

    # 偏移 0 在槽表里表示空槽，值区必须从 1 起写
    values = bytearray(b"\x00")
    slots = [0] * slot_count
    for i, it in enumerate(items_sorted):
        raw = encode_value(it)
        slots[i] = len(values)
        values.extend(raw)

    # 值区至少保持原大小，避免引擎对容量过敏
    encoded_end = len(values) - 1  # 游标 = 已编码字节末尾（值区以 \x00 起占 1 字节），须在填充前取
    val_size = max(hdr[11], len(values) + 64)
    if len(values) < val_size:
        values.extend(b"\x00" * (val_size - len(values)))

    hdr[9] = len(items_sorted)  # used
    hdr[10] = slot_off + slot_count * 4  # 值区偏移显式回写，防源文件漂移后新旧不一致
    hdr[11] = val_size
    hdr[12] = encoded_end  # cursor
    total = slot_off + slot_count * 4 + val_size
    hdr[3] = total

    out = bytearray()
    out.extend(struct.pack(f"<{HDR_N}I", *hdr))
    out.extend(extend)
    out.extend(struct.pack(f"<{slot_count}I", *slots))
    out.extend(values)
    if len(out) != total:
        # header total 以实际文件为准
        real = len(out)
        hdr[3] = real
        struct.pack_into(f"<{HDR_N}I", out, 0, *hdr)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(out)
    return {"count": len(items_sorted), "bytes": dest.stat().st_size, "path": str(dest)}


def prune_inplace(base: Path, dest: Path) -> dict:
    """原位去重：每键保留频次最高的副本（字节原样），其余槽清零。

    与 rebuild 的整表重打包不同：槽位顺序不动、保留词条字节不动、值区不动，
    只把重复槽置空——等价于引擎在词库 UI 里删词的语义。
    外部整表重写会触发引擎/同步层的重同步（实测 apply 后 13 秒追加 53 个副本），
    原位裁剪把文件保持成引擎认识的样子，把这类扰动压到最小。
    """
    src = bytearray(base.read_bytes())
    hdr = list(_parse_header(bytes(src)))
    slot_off = hdr[6]
    items = decode_file(base)
    groups: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for it in items:
        groups[(it.word, it.pinyin)].append(it)
    drops: set[int] = set()
    kept = 0
    for v in groups.values():
        kept += 1
        if len(v) < 2:
            continue
        winner = max(v, key=lambda it: (it.freq, -it.slot))  # 频次并列取最老槽
        for it in v:
            if it is not winner:
                drops.add(it.slot)
    for s in sorted(drops):
        struct.pack_into("<I", src, slot_off + s * 4, 0)
    used = sum(1 for i in range(hdr[8]) if struct.unpack_from("<I", bytes(src), slot_off + i * 4)[0] != 0)
    hdr[9] = used
    struct.pack_into(f"<{HDR_N}I", src, 0, *hdr)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes(src))
    return {"kept": kept, "dropped": len(drops), "used": used, "path": str(dest)}


def merge_items(base: list[Item], incoming: list[Item], *, bump: int = 8) -> list[Item]:
    """同词+同音节合并，频次取较大并加上 bump；新词追加。"""
    index: dict[tuple[str, str], Item] = {}
    for it in base:
        index[(it.word, it.pinyin)] = it
    for it in incoming:
        key = (it.word, it.pinyin)
        if key in index:
            old = index[key]
            old.freq = max(old.freq, it.freq) + bump
            if it.pinyin:
                old.pinyin = it.pinyin
        else:
            if it.freq <= 0:
                it.freq = bump
            index[key] = it
    return list(index.values())


def items_to_rows(items: list[Item]) -> list[dict]:
    rows = []
    for it in items:
        d = asdict(it)
        d["syll"] = ",".join(str(x) for x in it.syll)
        rows.append(d)
    return rows
