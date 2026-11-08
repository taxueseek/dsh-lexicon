"""Doubao usr.dat 加解密（与 OimeEngine Encrypt/Decrypt 对齐）。"""
from __future__ import annotations

import struct


def encrypt_byte(b: int) -> int:
    x = b ^ 0xA5
    return ((x << 5) | (x >> 3)) & 0xFF


def decrypt_byte(b: int) -> int:
    rot = ((b >> 5) | ((b << 3) & 0xFF)) & 0xFF
    return rot ^ 0xA5


def encrypt_str(s: str) -> bytes:
    return bytes(encrypt_byte(b) for b in s.encode("utf-8"))


def decrypt_str(buf: bytes) -> str:
    return bytes(decrypt_byte(b) for b in buf).decode("utf-8", "replace")


def encrypt_char16(cp: int) -> int:
    x = cp ^ 0x65C9
    return ((x >> 7) | (x << 9)) & 0xFFFF


def decrypt_char16(c: int) -> int:
    rot = ((c >> 9) | ((c << 7) & 0xFFFF)) & 0xFFFF
    return rot ^ 0x65C9


def encrypt_u16(s: str) -> bytes:
    return b"".join(struct.pack("<H", encrypt_char16(ord(ch))) for ch in s)


def decrypt_u16(buf: bytes) -> str | None:
    if len(buf) % 2:
        buf = buf[: len(buf) // 2 * 2]
    chars: list[str] = []
    for (c,) in struct.iter_unpack("<H", buf):
        cp = decrypt_char16(c)
        if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
            return None
        chars.append(chr(cp))
    return "".join(chars)


def latin_syllables(text: str) -> list[int]:
    """英文字母按引擎规则：'a' → 10。"""
    out: list[int] = []
    for ch in text.lower():
        if "a" <= ch <= "z":
            out.append(0x0A + (ord(ch) - ord("a")))
    return out


# —— 音节表与拼音切分：豆包 / 微信共用的唯一一份，改音节表只改这里 ——
INITIALS = ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k", "h", "j", "q", "x", "r", "z", "c", "s", "y", "w"]
FINALS = [
    "uang", "iang", "iong", "uai", "uan", "ian", "iao", "ing", "ong", "eng", "ang",
    "ai", "ei", "ao", "ou", "er", "an", "en", "in", "un", "vn", "ia", "ie", "iu",
    "ua", "uo", "ue", "ve", "a", "o", "e", "i", "u", "v",
]
SYLS = {i + f for i in [""] + INITIALS for f in FINALS}
SYLS.update(["yi", "ya", "yao", "ye", "you", "yan", "yin", "yang", "ying", "yong",
             "wu", "wa", "wo", "wai", "wei", "wan", "wen", "wang", "weng",
             "yu", "yue", "yuan", "yun"])
_SYL_ORDER = sorted(SYLS, key=len, reverse=True)


def normalize_pinyin(s: str) -> str:
    """中间表拼音统一清洗：小写、去逗号/引号/空格。音节切分前必经。

    逗号是微信键的分隔写法、空格是 pypinyin 的拼法、引号是隔音写法，
    三种来源进豆包引擎前都要并成连续全拼。
    """
    return s.strip().lower().replace(",", "").replace("'", "").replace(" ", "")


def split_greedy(s: str, *, tail_ok: bool = False) -> list[str] | None:
    """贪心最长匹配切拼音。切不动时：tail_ok=True 把余段整体收尾（宽容模式），否则返回 None。"""
    out: list[str] = []
    i = 0
    while i < len(s):
        hit = next((sy for sy in _SYL_ORDER if s.startswith(sy, i)), None)
        if not hit:
            if tail_ok and i < len(s):
                out.append(s[i:])
                return out
            return None
        out.append(hit)
        i += len(hit)
    return out
