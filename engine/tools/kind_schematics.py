# -*- coding: utf-8 -*-
"""资产种类的剖面示意图（本体「资产图鉴」用）：箱涵、临时转输（地面管 + 固定泵）、挡导（土堤 / Jersey 护栏 / 沙袋）、溢流堰、湿井。尺寸按本系统设计参数画，不是施工图。

地下调蓄箱涵（box_culvert）：dig_pond5 的设计口径——可用水深 2.0 m + 超高 0.3 m、沉沙 5%（手册 §4.2）、
进水闸孔满 95% 关闸、应急溢流堰漫回来水井（§4.14.1(f)）、排空泵雨后起判（2 h 雨取第 3 h）、下游井退水才开、约 12 h 排空。

用法：python tools/kind_schematics.py      产物：console/media/kinds/<种类>.<zh|en>.svg
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "console" / "media" / "kinds"

T = {
    "zh": {"title": "地下调蓄箱涵 · 剖面示意", "ground": "地面 / 道路", "cover": "覆土 ≥ 1.0 m", "net": "既有管网",
           "mh_up": "来水井", "mh_dn": "下游井", "gate": "进水闸孔", "gate2": "满 95% 关闸", "gate3": "水留在管网", "usable": "可用水深 2.0 m",
           "free": "超高 0.3 m", "silt": "沉沙 5%", "weir": "应急溢流堰", "weir2": "满到顶漫回来水井", "pump": "排空泵",
           "pump2": "雨后（第 3 h 起）且下游井水深 &lt; 0.5 m 才开，约 12 h 排空", "hatch": "检修口", "store": "雨峰时存水",
           "foot": "混凝土离线调蓄（手册 WA-726 第三版 §4.2 B）；建成区内不得设开敞塘（§4.2）。示意，不是施工图。",
           "l2": "客户分级：L2 · CAPEX"},
    "en": {"title": "Underground storage box culvert · section", "ground": "Ground / road", "cover": "Cover ≥ 1.0 m", "net": "Existing network",
           "mh_up": "Feed manhole", "mh_dn": "Downstream manhole", "gate": "Inlet orifice gate", "gate2": "closes at 95% full", "gate3": "water stays in the network",
           "usable": "Usable depth 2.0 m", "free": "Freeboard 0.3 m", "silt": "Silt 5%", "weir": "Emergency overflow weir",
           "weir2": "spills back to the feed manhole", "pump": "Emptying pump",
           "pump2": "after the storm (from 3 h) and only when downstream &lt; 0.5 m deep; empties in ~12 h", "hatch": "Access hatch",
           "store": "Stores the peak", "foot": "Offline concrete storage (Manual WA-726 3rd ed. §4.2 B); open ponds are not allowed in built-up areas (§4.2). Schematic, not a construction drawing.",
           "l2": "Client level: L2 · CAPEX"},
}


def box_culvert(lang: str) -> str:
    L = T[lang]
    # 竖向比例：87 px / m；箱内净高 2.3 m（2.0 可用 + 0.3 超高）
    g, top, bot = 92, 136, 336
    wl = bot - int(2.0 * 87)                  # 可用水位
    silt = bot - 10
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 780 430" role="img" aria-label="{L['title']}" font-family="IBM Plex Sans, 'Noto Sans SC', sans-serif">
  <defs>
    <pattern id="soil" width="10" height="10" patternUnits="userSpaceOnUse"><rect width="10" height="10" fill="#efe6d2"/><circle cx="3" cy="3" r="1" fill="#cdbb95"/><circle cx="8" cy="7" r="1" fill="#cdbb95"/></pattern>
    <pattern id="conc" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#c9ced6"/><path d="M0 8L8 0" stroke="#aeb5bf" stroke-width="1"/></pattern>
    <pattern id="silt" width="6" height="6" patternUnits="userSpaceOnUse"><rect width="6" height="6" fill="#a78b5c"/><path d="M0 6L6 0" stroke="#8a7246" stroke-width="1"/></pattern>
    <marker id="ar" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse" viewBox="0 0 8 8"><path d="M0,0 L8,4 L0,8 z" fill="#1d4ed8"/></marker>
    <marker id="dm" markerWidth="9" markerHeight="9" refX="4" refY="4" orient="auto" markerUnits="userSpaceOnUse" viewBox="0 0 8 8"><path d="M0,4 L8,4 M4,0 L4,8" stroke="#334155" stroke-width="1.2"/></marker>
  </defs>
  <rect width="780" height="430" fill="#f8fafc"/>
  <text x="20" y="28" font-size="17" font-weight="700" fill="#0f172a">{L['title']}</text>
  <text x="760" y="28" font-size="12" text-anchor="end" fill="#7c3aed" font-weight="600">{L['l2']}</text>
  <!-- 土与地面 -->
  <rect x="0" y="{g}" width="780" height="300" fill="url(#soil)"/>
  <rect x="0" y="{g - 6}" width="780" height="6" fill="#475569"/>
  <text x="20" y="{g - 12}" font-size="12" fill="#334155">{L['ground']}</text>
  <!-- 来水井、下游井 -->
  <rect x="62" y="{g}" width="44" height="232" fill="#e2e8f0" stroke="#64748b" stroke-width="2"/>
  <text x="84" y="{g + 250}" font-size="11.5" text-anchor="middle" fill="#334155">{L['mh_up']}</text>
  <rect x="672" y="{g}" width="44" height="200" fill="#e2e8f0" stroke="#64748b" stroke-width="2"/>
  <text x="694" y="{g + 218}" font-size="11.5" text-anchor="middle" fill="#334155">{L['mh_dn']}</text>
  <path d="M0 {g + 150} H62" stroke="#64748b" stroke-width="10"/><path d="M0 {g + 150} H60" stroke="#93c5fd" stroke-width="5" marker-end="url(#ar)"/>
  <text x="8" y="{g + 138}" font-size="11" fill="#334155">{L['net']}</text>
  <rect x="62" y="{g + 150}" width="44" height="82" fill="#93c5fd" opacity=".75"/>
  <!-- 箱体 -->
  <rect x="190" y="{top - 16}" width="400" height="{bot - top + 32}" fill="url(#conc)" stroke="#64748b" stroke-width="2"/>
  <rect x="206" y="{top}" width="368" height="{bot - top}" fill="#ffffff"/>
  <rect x="206" y="{wl}" width="368" height="{bot - wl}" fill="#60a5fa" opacity=".55"/>
  <path d="M206 {wl} H574" stroke="#1d4ed8" stroke-width="1.5" stroke-dasharray="6 4"/>
  <rect x="206" y="{silt}" width="368" height="{bot - silt}" fill="url(#silt)"/>
  <text x="420" y="{(wl + bot) // 2 + 34}" font-size="13" text-anchor="middle" fill="#1e3a8a" font-weight="600">{L['store']}</text>
  <text x="400" y="{silt - 5}" font-size="10.5" text-anchor="middle" fill="#5b4520">{L['silt']}</text>
  <!-- 检修口 -->
  <rect x="470" y="{g - 2}" width="34" height="{top - 16 - g + 2}" fill="#94a3b8" stroke="#64748b"/>
  <text x="487" y="{g - 12}" font-size="10.5" text-anchor="middle" fill="#334155">{L['hatch']}</text>
  <!-- 覆土 -->
  <path d="M618 {g} V{top - 16}" stroke="#334155" stroke-width="1.2" marker-start="url(#dm)" marker-end="url(#dm)"/>
  <text x="624" y="{(g + top - 16) // 2 + 4}" font-size="11" fill="#334155">{L['cover']}</text>
  <!-- 水深标注 -->
  <path d="M222 {silt} V{wl}" stroke="#1d4ed8" stroke-width="1.4" marker-start="url(#dm)" marker-end="url(#dm)"/>
  <text x="230" y="{(wl + bot) // 2 + 4}" font-size="12" fill="#1e3a8a" font-weight="600">{L['usable']}</text>
  <path d="M600 {wl} V{top}" stroke="#b45309" stroke-width="1.2" marker-start="url(#dm)" marker-end="url(#dm)"/>
  <text x="606" y="{(top + wl) // 2 + 4}" font-size="11" fill="#b45309">{L['free']}</text>
  <!-- 进水管 + 闸孔 -->
  <path d="M106 {g + 170} H190" stroke="#64748b" stroke-width="10"/><path d="M106 {g + 170} H200" stroke="#93c5fd" stroke-width="5" marker-end="url(#ar)"/>
  <rect x="178" y="{g + 156}" width="10" height="28" fill="#b91c1c"/>
  <text x="112" y="{g + 196}" font-size="11.5" fill="#b91c1c" font-weight="600">{L['gate']}</text>
  <text x="112" y="{g + 211}" font-size="10.5" fill="#7f1d1d">{L['gate2']}</text>
  <text x="112" y="{g + 225}" font-size="10.5" fill="#7f1d1d">{L['gate3']}</text>
  <!-- 应急溢流堰 -->
  <path d="M206 {top + 8} H150 V{g + 120} H106" stroke="#64748b" stroke-width="7" fill="none"/>
  <path d="M206 {top + 8} H152 V{g + 120} H112" stroke="#f59e0b" stroke-width="2.5" fill="none" stroke-dasharray="5 3" marker-end="url(#ar)"/>
  <rect x="206" y="{top + 2}" width="6" height="14" fill="#f59e0b"/>
  <text x="222" y="{g + 12}" font-size="11.5" fill="#b45309" font-weight="600">{L['weir']}</text>
  <text x="222" y="{g + 25}" font-size="10.5" fill="#92400e">{L['weir2']}</text>
  <!-- 排空泵 -->
  <rect x="548" y="{bot - 26}" width="24" height="24" rx="12" fill="#0e7490" stroke="#083344"/>
  <path d="M560 {bot - 26} V{top + 44} H650 V{g + 150} H672" stroke="#64748b" stroke-width="6" fill="none"/>
  <path d="M560 {bot - 28} V{top + 44} H650 V{g + 150} H670" stroke="#22d3ee" stroke-width="2" fill="none" marker-end="url(#ar)"/>
  <text x="520" y="{bot + 36}" font-size="11.5" fill="#0e7490" font-weight="600" text-anchor="end">{L['pump']}</text>
  <text x="20" y="{bot + 58}" font-size="10.5" fill="#155e75">{L['pump']}：{L['pump2']}</text>
  <text x="20" y="418" font-size="10.5" fill="#64748b">{L['foot']}</text>
</svg>
'''


HEAD = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 780 430" role="img" aria-label="{title}" font-family="IBM Plex Sans, 'Noto Sans SC', sans-serif">
  <defs>
    <pattern id="soil" width="10" height="10" patternUnits="userSpaceOnUse"><rect width="10" height="10" fill="#efe6d2"/><circle cx="3" cy="3" r="1" fill="#cdbb95"/><circle cx="8" cy="7" r="1" fill="#cdbb95"/></pattern>
    <pattern id="conc" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#c9ced6"/><path d="M0 8L8 0" stroke="#aeb5bf" stroke-width="1"/></pattern>
    <marker id="ar" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse" viewBox="0 0 8 8"><path d="M0,0 L8,4 L0,8 z" fill="#1d4ed8"/></marker>
    <marker id="dm" markerWidth="9" markerHeight="9" refX="4" refY="4" orient="auto" markerUnits="userSpaceOnUse" viewBox="0 0 8 8"><path d="M0,4 L8,4 M4,0 L4,8" stroke="#334155" stroke-width="1.2"/></marker>
  </defs>
  <rect width="780" height="430" fill="#f8fafc"/>
  <text x="20" y="28" font-size="17" font-weight="700" fill="#0f172a">{title}</text>
  <text x="760" y="28" font-size="12" text-anchor="end" fill="#7c3aed" font-weight="600">{lvl}</text>
"""


def _foot(txt):
    return f'  <text x="20" y="418" font-size="10.5" fill="#64748b">{txt}</text>\n</svg>\n'


T2 = {
 "zh": {"conv_t": "临时转输 · 地面管 + 固定泵", "conv_l": "客户分级：L2 · IDD / Municipality", "low": "积水低点", "sump": "临时集水坑 / 检查井",
        "pump": "固定泵（雨前就位）", "pump2": "带浮球启停，柴油或电", "pipe": "地面管（沿路肩铺设，雨后撤）", "out": "排放点", "out2": "有余量的出水口 / 明渠 / 下游管网",
        "q": "能力 = 泵流量 × 台数；管长决定扬程", "conv_f": "把积水点的水抽走转输到能排的地方；不增加库容，靠「搬走」。示意，不是施工图。",
        "div_t": "挡导 · 土堤 / Jersey 护栏 / 沙袋", "div_l": "客户分级：L2 · IDD / Municipality", "tunnel": "要保护的入口（隧道口 / 地下通道）",
        "bund": "土堤", "jersey": "Jersey 护栏", "sand": "沙袋", "h": "挡水高度 = 预计水深 + 0.3 m 超高", "flow": "地表来水", "route": "导向可以淹的地方",
        "div_f": "不减少水量，只改变水去哪里：护住重要地点，把水导向可以接受积水的地方。二维里表达为地形上的障碍。示意，不是施工图。",
        "sp_t": "溢流堰", "sp_l": "客户分级：L2 · CAPEX（图 A）/ IDD（图 B）", "up": "塘 / 明渠 / 箱涵", "crest": "堰顶", "over": "超过堰顶的水溢出",
        "path": "指定的溢流通道", "sp_f": "水位到堰顶就溢出，把超量的水安全地放到指定去处，保护构筑物和下游。SWMM 里是 WEIR。示意，不是施工图。",
        "ww_t": "湿井 / 集水井", "ww_l": "客户分级：随所在泵站（L1 既有 / L2 新建）", "in": "来水管", "on": "启泵水位", "off": "停泵水位", "rm": "压力出水管",
        "vol": "调节容积：避免泵频繁启停、抽空", "ww_f": "泵取水的井。没有它，泵直接从检查井抽会抽空、失稳（29.RB2 的 PO 泵已补湿井）。SWMM 里是常面积蓄水节点。示意，不是施工图。"},
 "en": {"conv_t": "Temporary conveyance · surface pipeline + stationary pump", "conv_l": "Client level: L2 · IDD / Municipality", "low": "Flood low point", "sump": "Temporary sump / manhole",
        "pump": "Stationary pump (placed before the storm)", "pump2": "float switch, diesel or electric", "pipe": "Surface pipeline (along the verge, removed after)", "out": "Discharge point",
        "out2": "outfall / channel / downstream network with spare capacity", "q": "capacity = pump rate × units; pipe length sets the head",
        "conv_f": "Pumps water away from the flood point to where it can go; adds no storage, it moves the water. Schematic, not a construction drawing.",
        "div_t": "Divert / protect · bund / Jersey barrier / sandbags", "div_l": "Client level: L2 · IDD / Municipality", "tunnel": "Entrance to protect (tunnel / underpass)",
        "bund": "Bund", "jersey": "Jersey barrier", "sand": "Sandbags", "h": "height = expected depth + 0.3 m freeboard", "flow": "Surface flow", "route": "led to where flooding is acceptable",
        "div_f": "Removes no water, changes where it goes: protects key sites and steers water to acceptable areas. In 2D it is an obstacle in the terrain. Schematic, not a construction drawing.",
        "sp_t": "Spillway", "sp_l": "Client level: L2 · CAPEX (chart A) / IDD (chart B)", "up": "Pond / channel / box", "crest": "Crest", "over": "water above the crest spills",
        "path": "designated overflow path", "sp_f": "Lets water over at a set level, releasing excess safely to a chosen place. A WEIR in SWMM. Schematic, not a construction drawing.",
        "ww_t": "Wet well / sump", "ww_l": "Client level: as its pump station (L1 existing / L2 new)", "in": "Inlet pipe", "on": "Pump-on level", "off": "Pump-off level", "rm": "Rising main",
        "vol": "working volume: stops short-cycling and running dry", "ww_f": "The sump a pump draws from; without it a pump on a plain manhole runs dry and goes unstable (as with 29.RB2's PO pumps). A constant-area storage node in SWMM. Schematic, not a construction drawing."},
}


def conveyance(lang):
    L = T2[lang]; g = 250
    return HEAD.format(title=L["conv_t"], lvl=L["conv_l"]) + f"""  <rect x="0" y="{g}" width="780" height="150" fill="url(#soil)"/><rect x="0" y="{g - 6}" width="780" height="6" fill="#475569"/>
  <path d="M40 {g - 6} Q140 {g + 40} 240 {g - 6}" fill="#60a5fa" opacity=".6" stroke="#1d4ed8"/>
  <text x="140" y="{g - 16}" font-size="12" text-anchor="middle" fill="#1e3a8a" font-weight="600">{L['low']}</text>
  <rect x="160" y="{g}" width="36" height="70" fill="#e2e8f0" stroke="#64748b" stroke-width="2"/><rect x="160" y="{g + 30}" width="36" height="40" fill="#93c5fd" opacity=".8"/>
  <text x="178" y="{g + 88}" font-size="11" text-anchor="middle" fill="#334155">{L['sump']}</text>
  <rect x="230" y="{g - 60}" width="96" height="46" rx="6" fill="#0e7490"/><circle cx="248" cy="{g - 10}" r="9" fill="#1f2937"/><circle cx="308" cy="{g - 10}" r="9" fill="#1f2937"/>
  <path d="M190 {g + 40} V{g - 36} H230" stroke="#334155" stroke-width="6" fill="none"/>
  <text x="278" y="{g - 72}" font-size="12" text-anchor="middle" fill="#0e7490" font-weight="600">{L['pump']}</text>
  <text x="278" y="{g - 86}" font-size="10.5" text-anchor="middle" fill="#155e75">{L['pump2']}</text>
  <path d="M326 {g - 30} H600 V{g - 60} H690" stroke="#64748b" stroke-width="9" fill="none"/><path d="M326 {g - 30} H598 V{g - 60} H690" stroke="#22d3ee" stroke-width="3" fill="none"/>
  <text x="460" y="{g - 40}" font-size="11.5" text-anchor="middle" fill="#334155">{L['pipe']}</text>
  <path d="M676 {g - 6} L700 {g + 64} H752 L776 {g - 6} Z" fill="#e2e8f0" stroke="#64748b" stroke-width="2"/><path d="M688 {g + 28} L700 {g + 64} H752 L764 {g + 28} Z" fill="#93c5fd" opacity=".8"/>
  <path d="M690 {g - 60} V{g + 20}" stroke="#64748b" stroke-width="9"/><path d="M690 {g - 60} V{g + 18}" stroke="#22d3ee" stroke-width="3" marker-end="url(#ar)"/>
  <text x="730" y="{g - 100}" font-size="12" text-anchor="middle" fill="#1e3a8a" font-weight="600">{L['out']}</text>
  <text x="760" y="{g + 80}" font-size="10.5" text-anchor="end" fill="#334155">{L['out2']}</text>
  <text x="20" y="{g + 130}" font-size="11" fill="#155e75">{L['q']}</text>
""" + _foot(L["conv_f"])


def diversion(lang):
    L = T2[lang]; g = 300
    return HEAD.format(title=L["div_t"], lvl=L["div_l"]) + f"""  <rect x="0" y="{g}" width="780" height="100" fill="url(#soil)"/><rect x="0" y="{g - 6}" width="780" height="6" fill="#475569"/>
  <rect x="0" y="{g - 50}" width="560" height="44" fill="#60a5fa" opacity=".45"/>
  <path d="M30 {g - 30} H200" stroke="#1d4ed8" stroke-width="2.5" marker-end="url(#ar)"/><text x="30" y="{g - 58}" font-size="11.5" fill="#1e3a8a" font-weight="600">{L['flow']}</text>
  <path d="M250 {g - 6} L278 {g - 70} L312 {g - 70} L340 {g - 6} Z" fill="#b08a5a" stroke="#7c5c34"/><text x="295" y="{g + 22}" font-size="12" text-anchor="middle" fill="#5b4520" font-weight="600">{L['bund']}</text>
  <path d="M400 {g - 6} L408 {g - 30} L414 {g - 74} L430 {g - 74} L436 {g - 30} L444 {g - 6} Z" fill="url(#conc)" stroke="#64748b"/><text x="422" y="{g + 22}" font-size="12" text-anchor="middle" fill="#334155" font-weight="600">{L['jersey']}</text>
  {''.join(f'<rect x="{500 + (k % 3) * 18 + (6 if (k // 3) % 2 else 0)}" y="{g - 18 - (k // 3) * 14}" width="18" height="13" rx="4" fill="#e7dcc3" stroke="#a8946a"/>' for k in range(12))}
  <text x="530" y="{g + 22}" font-size="12" text-anchor="middle" fill="#5b4520" font-weight="600">{L['sand']}</text>
  <path d="M570 {g - 6} V{g - 120} H700 V{g - 6}" fill="#e2e8f0" stroke="#64748b" stroke-width="2"/><path d="M600 {g - 6} V{g - 70} Q635 {g - 100} 670 {g - 70} V{g - 6} Z" fill="#1f2937"/>
  <text x="635" y="{g - 128}" font-size="11.5" text-anchor="middle" fill="#334155" font-weight="600">{L['tunnel']}</text>
  <path d="M232 {g - 6} V{g - 74}" stroke="#334155" stroke-width="1.2" marker-start="url(#dm)" marker-end="url(#dm)"/><text x="226" y="{g - 80}" font-size="10.5" text-anchor="end" fill="#334155">{L['h']}</text>
  <path d="M300 {g - 90} Q300 {g - 150} 180 {g - 170}" stroke="#1d4ed8" stroke-width="2" fill="none" stroke-dasharray="6 4" marker-end="url(#ar)"/>
  <text x="100" y="{g - 178}" font-size="11" fill="#1e3a8a">{L['route']}</text>
""" + _foot(L["div_f"])


def spillway(lang):
    L = T2[lang]; g = 330
    return HEAD.format(title=L["sp_t"], lvl=L["sp_l"]) + f"""  <rect x="0" y="{g}" width="780" height="70" fill="url(#soil)"/>
  <path d="M40 120 V{g} H360 V170 H400 V{g} H760" fill="none" stroke="#64748b" stroke-width="3"/>
  <rect x="43" y="150" width="315" height="{g - 150}" fill="#60a5fa" opacity=".55"/><path d="M43 150 H358" stroke="#1d4ed8" stroke-dasharray="6 4"/>
  <rect x="360" y="170" width="40" height="{g - 170}" fill="url(#conc)" stroke="#64748b"/>
  <path d="M358 152 Q400 150 420 200 T470 {g - 20}" stroke="#1d4ed8" stroke-width="10" fill="none" opacity=".5"/><path d="M430 250 L480 {g - 30}" stroke="#1d4ed8" stroke-width="2" marker-end="url(#ar)"/>
  <text x="200" y="{g - 60}" font-size="13" text-anchor="middle" fill="#1e3a8a" font-weight="600">{L['up']}</text>
  <text x="380" y="164" font-size="11.5" text-anchor="middle" fill="#334155" font-weight="600">{L['crest']}</text>
  <text x="470" y="170" font-size="11" fill="#1e3a8a">{L['over']}</text><text x="600" y="{g - 12}" font-size="11.5" text-anchor="middle" fill="#334155">{L['path']}</text>
""" + _foot(L["sp_f"])


def wetwell(lang):
    L = T2[lang]; g = 90
    return HEAD.format(title=L["ww_t"], lvl=L["ww_l"]) + f"""  <rect x="0" y="{g}" width="780" height="310" fill="url(#soil)"/><rect x="0" y="{g - 6}" width="780" height="6" fill="#475569"/>
  <rect x="300" y="{g}" width="180" height="290" fill="url(#conc)" stroke="#64748b" stroke-width="2"/><rect x="314" y="{g + 10}" width="152" height="266" fill="#fff"/>
  <rect x="314" y="{g + 150}" width="152" height="126" fill="#60a5fa" opacity=".55"/>
  <path d="M0 {g + 120} H314" stroke="#64748b" stroke-width="12"/><path d="M0 {g + 120} H312" stroke="#93c5fd" stroke-width="5" marker-end="url(#ar)"/><text x="20" y="{g + 108}" font-size="11.5" fill="#334155">{L['in']}</text>
  <path d="M300 {g + 150} H270" stroke="#16a34a" stroke-width="2"/><text x="266" y="{g + 154}" font-size="11" text-anchor="end" fill="#15803d" font-weight="600">{L['on']}</text>
  <path d="M300 {g + 240} H270" stroke="#b91c1c" stroke-width="2"/><text x="266" y="{g + 244}" font-size="11" text-anchor="end" fill="#b91c1c" font-weight="600">{L['off']}</text>
  <rect x="380" y="{g + 238}" width="34" height="30" rx="6" fill="#0e7490"/><path d="M397 {g + 238} V{g + 40} H600 V{g - 6}" stroke="#64748b" stroke-width="7" fill="none"/>
  <path d="M397 {g + 236} V{g + 40} H598 V{g}" stroke="#22d3ee" stroke-width="2.5" fill="none" marker-end="url(#ar)"/><text x="610" y="{g + 34}" font-size="11.5" fill="#0e7490" font-weight="600">{L['rm']}</text>
  <text x="490" y="{g + 200}" font-size="11" fill="#1e3a8a">{L['vol']}</text>
""" + _foot(L["ww_f"])


SHARED = {"surface_pipeline": conveyance, "stationary_pump": conveyance, "pump": conveyance,
          "bund": diversion, "jersey_barrier": diversion, "sandbag": diversion, "small_barrier": diversion,
          "spillway": spillway, "wetwell": wetwell}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for lang in ("zh", "en"):
        (OUT / f"box_culvert.{lang}.svg").write_text(box_culvert(lang), encoding="utf-8")
        for kind, fn in SHARED.items():                      # 同一张图给同一类措施的几种资产（挡导三件、转输两件）
            (OUT / f"{kind}.{lang}.svg").write_text(fn(lang), encoding="utf-8")
    print(f"→ {OUT}：box_culvert + {len(SHARED)} 种（中 / 英）")


if __name__ == "__main__":
    main()
