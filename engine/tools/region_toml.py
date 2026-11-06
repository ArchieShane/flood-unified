# -*- coding: utf-8 -*-
"""自动改 region.toml（只改指定的键，保留注释和其余内容）。new_region.sh 调它。

    python tools/region_toml.py <region.toml> region.id=ABC region.title="AB C" network.outfalls='["X","Y"]' inputs.catchments=/path/x.shp
值：字符串直接写；列表 / 数字 / 布尔写成 TOML 字面量（以 [ 开头、纯数字、true/false 视为字面量）。键不存在就在对应表里追加。
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


def lit(v: str) -> str:
    if v.startswith("[") or re.fullmatch(r"-?\d+(\.\d+)?", v) or v in ("true", "false"):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_keys(path: Path, kv: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for dotted, val in kv.items():
        table, key = dotted.rsplit(".", 1)
        hdr = re.compile(rf"^\s*\[{re.escape(table)}\]\s*(#.*)?$")
        i = next((n for n, l in enumerate(lines) if hdr.match(l)), None)
        if i is None:
            lines += ["", f"[{table}]"]
            i = len(lines) - 1
        j = i + 1
        while j < len(lines) and not lines[j].lstrip().startswith("["):
            j += 1
        pat = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*[^#]*?(\s*#.*)?$")
        for n in range(i + 1, j):
            m = pat.match(lines[n])
            if m:
                lines[n] = f"{m.group(1)}{key} = {lit(val)}{m.group(2) or ''}"
                break
        else:
            lines.insert(j, f"{key} = {lit(val)}")
    text = "\n".join(lines) + "\n"
    tomllib.loads(text)           # 改完必须仍是合法 TOML
    path.write_text(text, encoding="utf-8")


def get(path: Path, dotted: str):
    v = tomllib.loads(path.read_text(encoding="utf-8"))
    for k in dotted.split("."):
        v = v[k]
    return ",".join(map(str, v)) if isinstance(v, list) else v


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "get":          # python tools/region_toml.py get <region.toml> run.default_scenarios
        print(get(Path(sys.argv[2]), sys.argv[3])); sys.exit(0)
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    set_keys(Path(sys.argv[1]), dict(a.split("=", 1) for a in sys.argv[2:]))
    print(f"已更新 {sys.argv[1]}：{', '.join(a.split('=')[0] for a in sys.argv[2:])}")
