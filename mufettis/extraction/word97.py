# -*- coding: utf-8 -*-
"""Word 97-2003 (.doc) metni: OLE "WordDocument" akışındaki parça tablosundan (piece table) düz metin.

Bazı resmî kurumlar düzenlemelerini hâlâ .doc olarak yayımlıyor (TBB tebliğleri, TMSF yönetmelikleri; M6.15).
Biçimlendirme ve alan kodları atılır, metin ve tablo hücreleri korunur. [MS-DOC] 2.4.1'deki algoritma:
FIB → Clx (tablo akışında) → Pcdt → her parça için CP aralığı ve dosya konumu (sıkıştırılmış: 8 bit cp1252,
değilse UTF-16LE).
"""
from __future__ import annotations

import io
import re
import struct

_FIB_FLAGS = 0x000A
_FIB_FC_CLX = 0x01A2
_FIB_LCB_CLX = 0x01A6
_WHICH_TABLE = 0x0200
_COMPRESSED = 0x40000000


def is_word97(data: bytes) -> bool:
    return data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _pieces(word: bytes, table: bytes):
    fc_clx, lcb_clx = struct.unpack_from("<II", word, _FIB_FC_CLX)
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:  # Prc: biçim bilgisi, atlanır
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise ValueError("Pcdt bulunamadı")
    lcb = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + lcb]
    n = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    for i in range(n):
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * i + 2)[0]
        yield cps[i], cps[i + 1], fc


def _clean(text: str) -> str:
    # Alan kodları: \x13 kod \x14 sonuç \x15 → sonuç; kod bölümü atılır.
    text = re.sub(r"\x13[^\x13\x14\x15]*\x14", "", text)
    text = re.sub(r"\x13[^\x13\x14\x15]*\x15", "", text)
    text = text.replace("\x15", "").replace("\x14", "")
    text = text.replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n").replace("\x07", "\t")
    text = re.sub(r"[\x00-\x08\x0e-\x1f]", "", text)
    text = re.sub(r"\t\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def word97_text(data: bytes) -> str:
    """.doc dosyasının düz metni; biçim tanınmazsa ValueError."""
    import olefile

    if not is_word97(data):
        raise ValueError("OLE (Word 97) dosyası değil")
    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        if not ole.exists("WordDocument"):
            raise ValueError("WordDocument akışı yok (Word belgesi değil)")
        word = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", word, _FIB_FLAGS)[0]
        table_name = "1Table" if flags & _WHICH_TABLE else "0Table"
        table = ole.openstream(table_name).read()
    finally:
        ole.close()
    parts = []
    for cp_start, cp_end, fc in _pieces(word, table):
        count = cp_end - cp_start
        if fc & _COMPRESSED:
            offset = (fc & ~_COMPRESSED) // 2
            parts.append(word[offset:offset + count].decode("cp1252", errors="replace"))
        else:
            parts.append(word[fc:fc + 2 * count].decode("utf-16-le", errors="replace"))
    return _clean("".join(parts))


__all__ = ["word97_text", "is_word97"]
