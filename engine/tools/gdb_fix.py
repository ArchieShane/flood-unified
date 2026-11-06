# -*- coding: utf-8 -*-
"""修 ArcGIS Utility Network 的 file geodatabase，让 GDAL / pyogrio 能读（原件不动，输出一个工作副本）。

问题：这类 GDB 的字段描述里，部分字段带了默认值，但标志位没置 0x04（如 smasubnetworkname、ASSOCIATIONSTATUS、created_user…）。
GDAL 因此把默认值当成下一个字段，报 `Unhandled field type`，结果图层能打开但要素数和字段都读成 0。
做法：在工作副本上把这些字段的标志位补成 |= 4；其余文件符号链接到原件，只复制要修的 .gdbtable。

用法：python tools/gdb_fix.py <原 .gdb> <工作副本 .gdb> [图层名 ...]     # 缺省修所有业务图层（Stormwater* / Structure*）
别的脚本里：from gdb_fix import fix_gdb；fix_gdb(src, dst, ["StormwaterLine", ...])
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import sys
from pathlib import Path

DEFAULT_LAYERS = ["StormwaterLine", "StormwaterDevice", "StormwaterJunction", "StormwaterAssembly", "StructureJunction", "StructureLine", "StructureBoundary"]
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _scan(b: bytes) -> list[tuple[int, str, int]]:
    """在字段描述块里按「长度 + UTF-16 名字 + 别名 + 类型字节」的形状找各字段，返回 (名字起点, 名字, 类型)。对字段类型细节的版本差异不敏感。"""
    nf = struct.unpack("<H", b[4:6])[0]
    out, p = [], 6
    while p < len(b) - 3 and len(out) < nf:
        nl = b[p]
        if 1 <= nl <= 40 and p + 1 + 2 * nl < len(b):
            try:
                name = b[p + 1:p + 1 + 2 * nl].decode("utf-16le")
            except UnicodeDecodeError:
                name = None
            if name and _IDENT.match(name):
                q = p + 1 + 2 * nl
                al = b[q]
                if q + 1 + 2 * al < len(b):
                    try:
                        alias = b[q + 1:q + 1 + 2 * al].decode("utf-16le")
                    except UnicodeDecodeError:
                        alias = None
                    if alias is not None and alias.isprintable() and b[q + 1 + 2 * al] <= 30:
                        out.append((p, name, b[q + 1 + 2 * al]))
                        p = q + 1 + 2 * al + 1
                        continue
        p += 1
    if len(out) != nf:
        raise ValueError(f"字段描述解析出 {len(out)} 个，声明 {nf} 个")
    return out


def _patch_table(path: Path) -> list[str]:
    patched = []
    with open(path, "r+b") as f:
        h = f.read(40)
        foff = struct.unpack("<Q", h[32:40])[0]
        f.seek(foff)
        ln, _ = struct.unpack("<II", f.read(8))
        b = f.read(ln - 4)
        base = foff + 8
        fields = _scan(b)
        for i, (p, name, tp) in enumerate(fields):
            nl = b[p]; q = p + 1 + 2 * nl; al = b[q]; tpos = q + 1 + 2 * al
            end = fields[i + 1][0] if i + 1 < len(fields) else len(b)
            span = end - tpos - 1
            if tp == 4:
                fpos, basespan = tpos + 5, 5
            elif tp in (0, 1, 2, 3, 5, 13, 14, 15, 16):
                fpos, basespan = tpos + 2, 2
            else:
                continue
            if span > basespan and not (b[fpos] & 4):
                f.seek(base + fpos)
                f.write(bytes([b[fpos] | 4]))
                patched.append(name)
    return patched


def _table_files(src: Path, layers: list[str]) -> dict[str, str]:
    import pyogrio
    cat = pyogrio.read_dataframe(str(src), layer="GDB_SystemCatalog", LIST_ALL_TABLES="YES", fid_as_index=True, read_geometry=False)
    return {r.Name: f"a{int(i):08x}" for i, r in cat.iterrows() if r.Name in layers}


def fix_gdb(src, dst, layers: list[str] | None = None) -> dict[str, list[str]]:
    src, dst = Path(src), Path(dst)
    files = _table_files(src, layers or DEFAULT_LAYERS)
    dst.mkdir(parents=True, exist_ok=True)
    copy = {f + ".gdbtable" for f in files.values()}
    for fn in os.listdir(src):
        d = dst / fn
        if d.is_symlink() or d.exists():
            d.unlink()
        if fn in copy:
            shutil.copyfile(src / fn, d)
        else:
            os.symlink((src / fn).resolve(), d)
    return {name: _patch_table(dst / (stem + ".gdbtable")) for name, stem in files.items()}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    res = fix_gdb(sys.argv[1], sys.argv[2], sys.argv[3:] or None)
    for k, v in res.items():
        print(f"{k}: 补了 {len(v)} 个字段标志位")
