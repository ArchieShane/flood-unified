# -*- coding: utf-8 -*-
"""公共规则读取：所有推演脚本从 config/model_rules.toml 取规则，不在脚本里写死数字。

    from model_rules import load, get, swmm_options, manning_for
    r = load()                               # 缺省 <仓库>/config/model_rules.toml，环境变量 SWBRAIN_MODEL_RULES 可覆盖
    get("exchange.inlet.clog_factor")        # 0.5；路径不存在直接抛 KeyError（不给默认值，免得悄悄用错）
    swmm_options()                           # [OPTIONS] 里按文件顺序的 (名, 值) 列表，直接写进 inp
    manning_for("Reinforced Concrete - RCP") # 0.015；不认识的材料回落 pipes.default_manning_n

命令行自检：python tools/model_rules.py [--inp <某个 .inp>]   # 带 --inp 就逐项核对 [OPTIONS] 与规则文件一致
"""
from __future__ import annotations

import os
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "config" / "model_rules.toml"
_NOT_OPTION = {"source", "ref", "EVAPORATION_CONSTANT"}


@lru_cache(maxsize=None)
def load(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("SWBRAIN_MODEL_RULES") or DEFAULT)
    with open(p, "rb") as f:
        return tomllib.load(f)


def get(dotted: str, rules: dict | None = None):
    node = rules if rules is not None else load()
    for k in dotted.split("."):
        node = node[k]
    return node


def swmm_options(rules: dict | None = None) -> list[tuple[str, str]]:
    opts = (rules or load())["swmm"]["options"]
    return [(k, str(v)) for k, v in opts.items() if k not in _NOT_OPTION]


def manning_for(material: str | None, rules: dict | None = None) -> float:
    r = (rules or load())["pipes"]
    return float(r["manning_by_material"].get((material or "").strip(), r["default_manning_n"]))


def check_inp(inp: Path, rules: dict | None = None) -> list[str]:
    """核对 .inp 的 [OPTIONS] 与规则文件：返回不一致的项（空 = 一致）。"""
    want = dict(swmm_options(rules))
    got, on = {}, False
    for line in inp.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("["):
            on = s.upper() == "[OPTIONS]"
            continue
        if on and s and not s.startswith(";"):
            parts = s.split(None, 1)
            if len(parts) == 2:
                got[parts[0]] = parts[1].strip()
    return [f"{k}: 规则 {v!r}，inp {got.get(k)!r}" for k, v in want.items() if got.get(k) != v]


if __name__ == "__main__":
    r = load()
    print(f"规则 {r['meta']['series']} 系列 {r['meta']['version']}（{r['meta']['date']}），{len(swmm_options())} 个 SWMM 选项，"
          f"{len(r['pipes']['manning_by_material'])} 种管材糙率")
    if "--inp" in sys.argv:
        diff = check_inp(Path(sys.argv[sys.argv.index("--inp") + 1]), r)
        print("与 inp 一致" if not diff else "不一致：\n  " + "\n  ".join(diff))
        sys.exit(1 if diff else 0)
