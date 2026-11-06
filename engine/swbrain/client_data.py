"""客户原始数据的位置（放在代码文件夹外面，见 CLAUDE.md 第 2 条）。

根目录缺省 D:/阿布扎比/客户数据/20261019，可用环境变量 SWBRAIN_CLIENT_DATA 改。
2026-10-30 重新下载的 test/ 按顾问分组（AECOM/RDA、AECOM/RD-F/RD-F、Parsons/RB-01 …），
旧结构是 test/RD-A/RD-A、test/RB-01；model() 两种都认。
"""
import os
from pathlib import Path

DATA = Path(os.environ.get("SWBRAIN_CLIENT_DATA", "D:/阿布扎比/客户数据/20261019"))
TEST = DATA / "test"
STORMWATER = DATA / "Stormwater"


def wa726(n):
    """设计手册第 n 卷 PDF。"""
    return DATA / f"WA-726-{n}.pdf"


def model(name):
    """客户水力模型目录：SewerGEMS 模型返回放各场雨子目录（10+20%-1hr …）和模型子目录（Hydraulic Model / HYD. MODEL）的那一层，
    Parsons 模型返回放 shp 的那一层。"""
    flat = name.replace("-", "")
    cands = [TEST / "AECOM" / flat, TEST / "AECOM" / name / name, TEST / "AECOM" / name, TEST / "Parsons" / name,
             TEST / name / name, TEST / name]
    for d in cands:
        if (d / "10+20%-1hr").is_dir() or any(d.glob("*.shp")):
            return d
    raise FileNotFoundError(f"找不到客户模型 {name}，查过：" + "；".join(str(c) for c in cands))
