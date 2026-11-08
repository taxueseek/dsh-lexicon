"""微信输入法 WeType 个人库：识别 / 导出 / WAL 注入。

真源键：`!u_d_v_p!{拼音逗号}\\x01{词}\\x01{seq}`
写入只追加 LevelDB WAL（CRC32C + WriteBatch Put），不改 SST。
"""
from __future__ import annotations

import json
import shutil
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from codec import split_greedy

# CRC-32C (Castagnoli), reflected poly
_POLY = 0x82F63B78
_MASK_DELTA = 0xA282EAD8
_KBLOCK = 32768  # LevelDB 日志块大小：记录不得以 full 类型跨块


def crc32c(data: bytes, crc: int = 0) -> int:
    crc ^= 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ _POLY if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def _mask_crc(crc: int) -> int:
    return (((crc >> 15) | (crc << 17)) & 0xFFFFFFFF) + _MASK_DELTA & 0xFFFFFFFF


def _put_varint(n: int) -> bytes:
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _get_varint(buf: bytes, i: int) -> tuple[int, int]:
    x = s = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        x |= (b & 0x7F) << s
        if b < 0x80:
            return x, i
        s += 7
    raise ValueError("truncated varint")


@dataclass
class WItem:
    word: str
    pinyin: str
    freq: int
    kind: str


def _walk_bang(data: bytes) -> list[tuple[bytes, bytes]]:
    entries: list[tuple[bytes, bytes]] = []
    i = 0
    key = b""
    while i < len(data) - 8:
        start = i
        try:
            shared, i = _get_varint(data, i)
            unshared, i = _get_varint(data, i)
            vlen, i = _get_varint(data, i)
            if shared > 10000 or unshared > 4000 or vlen > 8000:
                raise ValueError("insane")
            if i + unshared + vlen > len(data):
                raise ValueError("over")
            key = key[:shared] + data[i : i + unshared]
            i += unshared
            val = data[i : i + vlen]
            i += vlen
            if key.startswith(b"!"):
                entries.append((key, val))
        except Exception:
            i = start + 1
            key = b""
    return entries


def _split_udp_key(key: bytes) -> tuple[str, str] | None:
    # !u_d_v_p!py,yin\x01词\x01seq
    if not key.startswith(b"!u_d_v_p!"):
        return None
    rest = key[len(b"!u_d_v_p!") :]
    parts = rest.split(b"\x01")
    if len(parts) < 2:
        return None
    py = parts[0].decode("ascii", "ignore")
    try:
        word = parts[1].decode("utf-8")
    except Exception:
        return None
    if not word:
        return None
    return word, py.replace(",", "")


def decode_dir(db_dir: Path) -> list[WItem]:
    found: dict[tuple[str, str], WItem] = {}

    def add(word: str, pinyin: str, freq: int, kind: str) -> None:
        word = word.strip()
        if not word:
            return
        key = (word, pinyin)
        old = found.get(key)
        if old is None or freq > old.freq:
            found[key] = WItem(word=word, pinyin=pinyin, freq=freq, kind=kind)

    for p in sorted(db_dir.glob("*.ldb")):
        for k, _v in _walk_bang(p.read_bytes()):
            parsed = _split_udp_key(k)
            if parsed:
                add(parsed[0], parsed[1], 1, "udp")
            elif k.startswith(b"!v2u!"):
                py = k[5:].split(b"\x01", 1)[0].decode("ascii", "ignore").replace(",", "")
                if py:
                    add(py, py, 1, "v2u")
            elif k.startswith(b"!user_prefix2!"):
                # !user_prefix2!W!\x7fWORD\x01seq
                rest = k[len(b"!user_prefix2!") :]
                if b"\x7f" in rest:
                    tail = rest.split(b"\x7f", 1)[1]
                    word_b = tail.split(b"\x01", 1)[0]
                    try:
                        add(word_b.decode("utf-8"), "", 1, "prefix")
                    except Exception:
                        pass

    # WAL 里新写入的也能扫到
    for p in sorted(db_dir.glob("*.log")):
        raw = p.read_bytes()
        for k, _v in _parse_wal_puts(raw):
            parsed = _split_udp_key(k)
            if parsed:
                add(parsed[0], parsed[1], 1, "wal")

    items = list(found.values())
    items.sort(key=lambda x: (-x.freq, x.word))
    return items


def _parse_wal_puts(data: bytes) -> list[tuple[bytes, bytes]]:
    out: list[tuple[bytes, bytes]] = []
    off = 0
    while off + 7 <= len(data):
        _crc, length, typ = struct.unpack_from("<IHB", data, off)
        if length == 0:
            # 块尾零填充（写侧对齐块边界留下的）：跳到下一个 32KB 块，而不是当文件结束，
            # 否则真实日志里第一个块尾填充之后的所有记录都被漏掉（回读假阴性 / last_seq 偏小）
            off = (off // _KBLOCK + 1) * _KBLOCK
            continue
        if off + 7 + length > len(data):
            break
        payload = data[off + 7 : off + 7 + length]
        off += 7 + length
        if typ != 1 or len(payload) < 13:
            continue
        # seq u64 + count u32
        count = struct.unpack_from("<I", payload, 8)[0]
        i = 12
        for _ in range(min(count, 10000)):
            if i >= len(payload):
                break
            tag = payload[i]
            i += 1
            try:
                klen, i = _get_varint(payload, i)
                key = payload[i : i + klen]
                i += klen
                if tag == 1:
                    vlen, i = _get_varint(payload, i)
                    val = payload[i : i + vlen]
                    i += vlen
                    out.append((key, val))
            except Exception:
                break
    return out


def _last_wal_seq(data: bytes) -> int:
    off = 0
    last = 0
    while off + 7 <= len(data):
        _crc, length, typ = struct.unpack_from("<IHB", data, off)
        if length == 0:
            # 块尾零填充：跳到下一个 32KB 块（与 _parse_wal_puts 同一语义）
            off = (off // _KBLOCK + 1) * _KBLOCK
            continue
        if off + 7 + length > len(data):
            break
        payload = data[off + 7 : off + 7 + length]
        off += 7 + length
        if typ == 1 and len(payload) >= 8:
            last = struct.unpack_from("<Q", payload, 0)[0]
    return last


def _wal_record(payload: bytes) -> bytes:
    typ = 1  # full
    crc = _mask_crc(crc32c(bytes([typ]) + payload))
    return struct.pack("<IHB", crc, len(payload), typ) + payload


def _write_batch_put(seq: int, key: bytes, value: bytes) -> bytes:
    rec = bytes([1]) + _put_varint(len(key)) + key + _put_varint(len(value)) + value
    return struct.pack("<QI", seq, 1) + rec


def _pinyin_commas(pinyin: str) -> str:
    s = pinyin.strip().replace("'", ",").replace(" ", ",")
    if "," in s:
        return ",".join(p for p in s.split(",") if p)
    # 无分隔：整段当一个音节（英文词），或按音节表贪心切（表收口在 codec，豆包共用）
    parts = split_greedy(s.lower(), tail_ok=True) or []
    return ",".join(parts)


def _template_value(db_dir: Path) -> bytes:
    for p in sorted(db_dir.glob("*.ldb")):
        for k, v in _walk_bang(p.read_bytes()):
            if k.startswith(b"!u_d_v_p!") and len(v) >= 60:
                return v[:73] if len(v) >= 73 else v.ljust(73, b"\x06")
    return b"\x07" + b"\x06" * 72


def inject_words(db_dir: Path, incoming: list[WItem]) -> dict:
    """往副本 WAL 追加 Put。返回写入条数。"""
    logs = sorted(db_dir.glob("*.log"))
    if not logs:
        raise ValueError(
            "staging 里没有 .log：库已被完全 flush，注入会落进 MANIFEST 不认识的孤儿日志被无视；"
            "先用输入法打一个词让引擎生成日志，再重新 import"
        )
    log = logs[-1]
    raw = log.read_bytes()
    seq = _last_wal_seq(raw) + 1
    tmpl = _template_value(db_dir)
    written = 0
    blob = bytearray(raw)
    for it in incoming:
        if not it.word:
            continue
        py = _pinyin_commas(it.pinyin or it.word)
        if not py.isascii():
            raise ValueError(
                f"微信键需要 ASCII 拼音：{it.word!r}（pinyin={it.pinyin!r}）；"
                "无拼音中文词先补拼音，或由 import 前置闸跳过"
            )
        key = b"!u_d_v_p!" + py.encode("ascii") + b"\x01" + it.word.encode("utf-8") + b"\x01\xee\xee\x01\x00\x00\x00\x00"
        rec = _wal_record(_write_batch_put(seq, key, tmpl))
        rem = _KBLOCK - len(blob) % _KBLOCK
        if rem < len(rec):
            # 块尾放不下整条记录：LevelDB 读侧会跳到下一块边界，
            # 不补零对齐的话真实引擎把跨块记录当损坏丢弃（小批量看不出，大批量必坏）
            blob.extend(b"\x00" * rem)
        blob.extend(rec)
        seq += 1
        written += 1
    log.write_bytes(blob)
    return {"written": written, "log": str(log), "last_seq": seq - 1}


def export_dir(db_dir: Path, dest: Path) -> dict:
    items = decode_dir(db_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as f:
        f.write("word\tpinyin\tfreq\tkind\n")
        for it in items:
            f.write(f"{it.word}\t{it.pinyin}\t{it.freq}\t{it.kind}\n")
    return {"count": len(items), "path": str(dest)}


def stage_import(live_dir: Path, staging: Path, incoming: list[WItem]) -> dict:
    if staging.resolve() == live_dir.resolve():
        raise ValueError("staging 不能等于 live 目录：会先删掉正在用的词库副本")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(live_dir, staging)
    plan = staging / "inject.jsonl"
    with plan.open("w", encoding="utf-8") as f:
        for it in incoming:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
    inj = inject_words(staging, incoming)
    check = decode_dir(staging)
    hit = {(it.word, it.pinyin) for it in check}
    check_words = {it.word for it in check}
    missing = [it.word for it in incoming if (it.word, _pinyin_commas(it.pinyin or it.word).replace(",", "")) not in hit
               and it.word not in check_words]
    return {
        "staging": str(staging),
        "plan": str(plan),
        "incoming": len(incoming),
        "written": inj["written"],
        "reread": len(check),
        "missing": missing,
    }
