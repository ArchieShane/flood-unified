# -*- coding: utf-8 -*-
"""把 Case 1 的四张图嵌进 out/case1_baseline.html（base64 内嵌，页面自带、可单独发）。

为什么单独一个脚本：图由 tools/case1_charts.py 生成，重算后 HTML 里的 base64 就过期了。
这个脚本可重复跑 —— 每次先把上一次注入的块整段删掉，再从 docs/figs/case1/ 重新读盘注入，
所以不会越嵌越多。锚点文字找不到就报错退出，不静默少嵌一张。

用法：python tools/case1_page.py            # 就地更新 out/case1_baseline.html
      python tools/case1_page.py --check    # 只查锚点与图片在不在，不改文件
"""
from __future__ import annotations

import argparse
import base64
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "out" / "case1_baseline.html"
FIGS = ROOT / "docs" / "figs" / "case1"

# (图名, 说明标题, 说明正文, 插在哪个锚点之后)
# 锚点都是页面里唯一的句子，改文案时这个脚本会直接报错，不会悄悄少一张图。
PLAN = [
    ("fig1_capacity", "Peak flow and network capacity",
     "Left: the peak sits on the same link in every state. Right: peak node flooding — "
     "the one capacity figure the measures do move.",
     "does not lift the network above capacity.</li>\n    </ul>"),
    ("fig2_water_account", "L1 water account",
     "Inflow is written down step by step; what is left over is the continuity error, "
     "which is the run's own check on itself.",
     "which is the check we use on every run.</p>"),
    ("fig3_timing", "Time to peak",
     "The rain peaks at 0 h 55, the network at 2 h 22, the surface at 2 h 20. "
     "The 1.4 h gap is the mobilisation window.",
     "spread of about 30 minutes.</li>\n    </ul>"),
    ("fig4_extent", "Flood extent",
     "Left: whole domain, by state and depth threshold. Right: the eight hotspots, "
     "existing network versus suggested measures. The small hotspots lose the largest share of their area.",
     "file for GIS use.</li>\n    </ul>"),
]

CSS = """<!-- css:figures -->
  /* 图：PNG 是白底，深色模式下当"印版"看待 —— 底板恒定白色，不跟着主题变暗，
     否则米白底图贴在深色页面上会炸眼。 */
  figure { margin: 22px 0 0; }
  figure .plate {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 10px;
    overflow-x: auto;
  }
  figure img { display: block; width: 100%; height: auto; }
  figure figcaption {
    margin-top: 9px; font-size: 12.5px; color: var(--muted); max-width: 82ch;
  }
  figure figcaption b { color: var(--fg); font-weight: 600; }
  @media (max-width: 640px) { figure .plate { padding: 6px; } }
  /* /css:figures */"""


def strip_previous(html: str) -> str:
    """删掉上一次注入的图块与 CSS 块（按标记整段删，保证可重复跑）。
    CSS 块的收尾是 CSS 注释 /* /css:figures *​/，不是 HTML 注释 —— 用错就把整块留下，
    重跑时会再插一份 CSS。"""
    html = re.sub(r"<!-- css:figures -->.*?/\* /css:figures \*/\n?", "", html, flags=re.S)
    html = re.sub(r"    <!-- fig:\w+ -->.*?    <!-- /fig:\w+ -->\n", "", html, flags=re.S)
    return html


def figure_html(name: str, title: str, body: str) -> str:
    png = FIGS / f"{name}.png"
    if not png.exists():
        raise SystemExit(f"缺图：{png}　先跑 python tools/case1_charts.py")
    b64 = base64.b64encode(png.read_bytes()).decode("ascii")
    return (
        f"    <!-- fig:{name} -->\n"
        f'    <figure>\n'
        f'      <div class="plate"><img src="data:image/png;base64,{b64}" '
        f'alt="{title}"></div>\n'
        f'      <figcaption><b>Figure {{n}} — {title}.</b> {body}</figcaption>\n'
        f"    </figure>\n"
        f"    <!-- /fig:{name} -->\n"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 Case 1 的图嵌进 HTML 单页")
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not PAGE.exists():
        raise SystemExit(f"找不到页面：{PAGE}")

    html = strip_previous(PAGE.read_text(encoding="utf-8"))

    # 先把所有锚点核一遍，缺一个就整体不干 —— 半嵌的页面比不嵌更糟
    missing = [anchor[:40] for _, _, _, anchor in PLAN if anchor not in html]
    if missing:
        raise SystemExit("锚点找不到（页面文案改过了？）：\n  " + "\n  ".join(missing))

    if "<!-- css:figures -->" not in html:
        if "</style>" not in html:
            raise SystemExit("页面里没有 </style>，不敢插 CSS")
        html = html.replace("</style>", CSS + "\n</style>", 1)

    total = 0
    for i, (name, title, body, anchor) in enumerate(PLAN, 1):
        blk = figure_html(name, title, body).replace("{n}", str(i))
        html = html.replace(anchor, anchor + "\n" + blk, 1)
        total += (FIGS / f"{name}.png").stat().st_size
        print(f"  图{i} {name}.png  {(FIGS / f'{name}.png').stat().st_size / 1024:.0f} KB  → 锚点 OK")

    if a.check:
        print(f"检查通过：4 张图齐全，合计 {total / 1024:.0f} KB（未写文件）")
        return 0

    PAGE.write_text(html, encoding="utf-8")
    print(f"  → {PAGE.relative_to(ROOT)}  {PAGE.stat().st_size / 1024:.0f} KB"
          f"（图片原始 {total / 1024:.0f} KB，base64 约 +33 %）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
