# -*- coding: utf-8 -*-
"""公共数据源登记的读取：config/data_sources.toml（全市 GDB、DTM、Makani、Mussafah inp 的位置，按环境分段）。

    from data_sources import sources
    s = sources()            # 环境取 SWBRAIN_ENV（缺省 dev_mac）；返回 {gdb, dtm, mussafah_inp, makani_host, makani_port, makani:{...}}
密码不在这里：Makani 密码只从环境变量（makani.password_env 指定，缺省 MAKANI_PASSWORD）读。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

REG = Path(__file__).resolve().parent.parent / "config" / "data_sources.toml"


def sources(env: str | None = None) -> dict:
    with open(REG, "rb") as f:
        t = tomllib.load(f)
    env = env or os.environ.get("SWBRAIN_ENV", "dev_mac")
    if env not in t.get("env", {}):
        raise SystemExit(f"{REG.name} 里没有环境 [{env}]（SWBRAIN_ENV={env}）")
    return {**t["env"][env], "env": env, "makani": t["makani"]}
