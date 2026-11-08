"""Win10 微软拼音用户词库（mschxudp）解析。

2017 导出头：magic + version + 偏移/条数；词条无 1703 那 8 字节垫。
不复制深蓝源码，按本机文件头和词条布局解。
"""
from __future__ import annotations

import struct
from pathlib import Path

MAGIC = b"mschxudp"


def parse_win10_ms_pinyin(data: bytes) -> list[tuple[str, str, int]]:
    """返回 (word, pinyin, rank)。pinyin 为原文（可能无隔音）。"""
    if len(data) < 0x40 or data[:8] != MAGIC:
        raise ValueError("不是 mschxudp 微软拼音词库")
    # 2017：0x08=ver, 0x0c=off_start, 0x10=ph_start, 0x14=ph_end, 0x18=count
    # 深蓝新导出：0x08=0x00600002, 0x10=off_start
    unk = struct.unpack_from("<I", data, 8)[0]
    if unk == 0x00600002:
        off_start, ph_start, ph_end, count = struct.unpack_from("<IIII", data, 0x10)
    else:
        off_start, ph_start, ph_end, count = struct.unpack_from("<IIII", data, 0x0C)
    if count <= 0 or count > 200_000:
        raise ValueError(f"条数异常 {count}")
    if off_start + 4 * count > len(data) or ph_start >= len(data):
        raise ValueError("偏移超出文件")
    offs = [struct.unpack_from("<I", data, off_start + 4 * i)[0] for i in range(count)]
    offs.append(ph_end - ph_start)
    out: list[tuple[str, str, int]] = []
    for i in range(count):
        a = ph_start + offs[i]
        z = ph_start + offs[i + 1]
        if a < 0 or z > len(data) or z - a < 12:
            continue
        hanzi_off = struct.unpack_from("<H", data, a + 4)[0]
        rank = data[a + 6]
        py_len = hanzi_off - 10
        if 0 < py_len <= 128 and a + 8 + py_len <= z:
            py = data[a + 8 : a + 8 + py_len].decode("utf-16le", errors="ignore").strip("\x00")
        else:
            py = ""
        wraw = data[a + hanzi_off : z]
        if len(wraw) >= 2 and wraw[-2:] == b"\x00\x00":
            wraw = wraw[:-2]
        word = wraw.decode("utf-16le", errors="ignore").strip("\x00")
        if word:
            out.append((word, py, int(rank)))
    return out


def parse_file(path: Path) -> list[tuple[str, str, int]]:
    return parse_win10_ms_pinyin(path.read_bytes())
