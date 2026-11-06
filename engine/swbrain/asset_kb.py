# -*- coding: utf-8 -*-
"""资产种类知识库（本体「资产图鉴」）：每种地表水管理资产是什么、干什么用、归哪一级、在模型里怎么表达。

为什么要有：本体里的 Asset 只有 kind 一个字段，工程师看得懂，领导、客户和智能体都不知道「箱涵」是什么、
和塘有什么区别。这里每种资产给**多模态**描述——一句话定义、功能、客户分级、建模口径、关键参数、出处，
加上图（客户分级图里的实景小图 / 本系统画的剖面示意）。

分级的两个出处（客户 2026-09-25 发来的两张图，口径不完全一样，都记）：
  图 A「Surface Water Management Levels」：L2 分 CAPEX / IDD / Municipality 三条线
  图 B「Resource Levels Definition」：按**时间 / 状态**定义——L1 既有永久，L2 **雨前**部署或建成的临时措施（IDD / Municipalities），
        L3 **雨中**动态调配；溢流堰、泵站、生态格室在图 B 里归 IDD
用户裁定（2026-09-28）：雨前预置在固定位置的泵算 L2；本系统的地下箱涵算 L2（CAPEX）。

图：console/media/kinds/<kind>.jpg（从图 A 裁出，客户材料）、<kind>.<zh|en>.svg（tools/kind_schematics.py 画，示意不是施工图）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "console" / "media" / "kinds"

FUNCTIONS = {
    "collect": ("收水", "Collect"), "convey": ("转输", "Convey"), "store": ("蓄", "Store"), "pump": ("抽排", "Pump"),
    "discharge": ("排放", "Discharge"), "divert": ("挡导", "Divert / protect"), "spill": ("溢流", "Spill"),
    "infiltrate": ("下渗", "Infiltrate"), "respond": ("应急", "Respond"),
}
SRC_A = "客户图 A「Surface Water Management Levels」"
SRC_B = "客户图 B「Resource Levels Definition」"
SRC_USER = "用户裁定 2026-09-28"
SRC_OURS = "本系统补充（客户图里没有）"

# kind → 知识条目。level_a / level_b = 两张图各自的归属（None = 该图没列）；delivery = 可能的交付线
KB: dict[str, dict] = {
    "pipeline": dict(zh="管线", en="Pipeline", fn=["convey"], level_a="L1", level_b="L1", delivery=["existing"],
        def_zh="埋地重力雨水管，把雨水口收进来的水输送到出水口或泵站。管网的主体，能力按管径、坡度、糙率算满管流量。",
        def_en="Buried gravity storm pipes carrying water from inlets to outfalls or pump stations; capacity is the full-bore flow from diameter, slope and roughness.",
        model_zh="SWMM 管段（CONDUIT），动力波演算；本体里是 Reach。", params=["管径", "坡度", "满管流量", "设计重现期"], refs=["手册 WA-726 §3"], covered_by="Reach"),
    "pump_station": dict(zh="泵站", en="Pump station (PS)", fn=["pump", "discharge"], level_a="L1 / L2 CAPEX", level_b="L1 / L2 IDD", delivery=["existing", "capex", "idd"],
        def_zh="湿井 + 固定泵组，把低处管网的水提升排走。既有的算 L1；为缓解新建的临时泵站在图 A 归 CAPEX、图 B 归 IDD。",
        def_en="Wet well plus fixed pumps lifting water out of a low network. Existing ones are L1; temporary new ones are CAPEX (chart A) or IDD (chart B).",
        model_zh="SWMM 蓄水节点（湿井）+ 泵（PUMP，Pump3/Pump4 曲线）+ 启停规则。", params=["额定流量", "扬程", "湿井面积", "启停水位"], refs=[]),
    "outfall": dict(zh="出水口", en="Outfall", fn=["discharge"], level_a="L1", level_b="L1", delivery=["existing"],
        def_zh="管网的末端，把水排进海、河道或干河谷。潮位高时可能被顶托（拍门）。",
        def_en="The end of the network discharging to the sea, a channel or a wadi; can be backed up at high tide (flap gate).",
        model_zh="SWMM 出水口节点（FREE / FIXED 潮位），可带拍门。", params=["管底高程", "潮位", "拍门"], refs=[]),
    "inlet": dict(zh="雨水口", en="Inlet / catch basin", fn=["collect"], level_a="L1", level_b="L1", delivery=["existing"],
        def_zh="路面上收水进管网的口子（雨篦 + 连接管）。收水能力受篦子面积、堵塞和连接管限制——雨水口不够时，干管不满街面也会积水（29.RB2 一半溢流出在雨水口）。",
        def_en="Street openings (grates + leads) taking surface water into the network. Limited by grate area, clogging and the lead pipe — when inlets are short, streets flood even if trunks are not full (half of 29.RB2's overflow is at catch basins).",
        model_zh="SWMM 节点（CB-…）；二维里是井盖处的孔口交换（Cd 0.6、DN600）。", params=["篦子面积", "连接管径", "堵塞系数"], refs=[]),
    "dam": dict(zh="坝", en="Dam", fn=["store", "divert"], level_a="L1", level_b="L1", delivery=["existing"],
        def_zh="拦住干河谷来水的挡水构筑物，削峰、滞洪。", def_en="Structures holding back wadi flows to attenuate peaks.",
        model_zh="二维地形里的抬高；或一维蓄水节点 + 溢流堰。", params=["坝顶高程", "库容"], refs=[]),
    "wadi": dict(zh="干河谷", en="Wadi", fn=["convey", "infiltrate"], level_a="L1", level_b="L1", delivery=["existing"],
        def_zh="平时干涸、暴雨时过洪的天然沟谷，是大片沙漠来水的通道。", def_en="Natural dry valleys that carry storm flow from large desert areas.",
        model_zh="二维地形直接表达（本系统模型里还没有单列）。", params=["过流断面", "下渗"], refs=[]),
    "ditch": dict(zh="明渠", en="Ditch", fn=["convey"], level_a="L1 / L2 IDD", level_b="L1 / L2 IDD", delivery=["existing", "idd"],
        def_zh="开挖的排水沟，把地表水导到出口。既有的算 L1；雨前临时挖的归 IDD。", def_en="Excavated channels leading surface water to an outlet; temporary ones dug before the season are IDD.",
        model_zh="一维明渠断面（OPEN）或二维地形刻槽。", params=["断面", "坡度"], refs=[]),
    "geocell": dict(zh="生态格室", en="Geocell", fn=["infiltrate", "divert"], level_a="L1 / L2 CAPEX", level_b="L1 / L2 IDD", delivery=["existing", "capex", "idd"],
        def_zh="蜂窝状土工格室，稳住坡面、渗水，减少冲刷。", def_en="Honeycomb cells stabilising slopes, letting water soak in and reducing scour.",
        model_zh="二维糙率 / 下渗参数（本系统模型里还没有）。", params=["面积", "下渗率"], refs=[]),
    "soakaway": dict(zh="渗井", en="Soakaway", fn=["infiltrate"], level_a=None, level_b=None, delivery=["existing"],
        def_zh="让水就地下渗的井。客户图里没有单列，台账里有。", def_en="Wells letting water soak into the ground; not on the client charts but in the register.",
        model_zh="SWMM 出水口或蓄水节点 + 下渗。", params=["下渗率"], refs=[SRC_OURS]),
    "pond": dict(zh="蓄水塘", en="Pond", fn=["store"], level_a="L1 / L2 CAPEX / L2 Municipality", level_b="L1 / L2 Municipalities", delivery=["existing", "capex", "municipality"],
        def_zh="地面开敞的调蓄塘，雨峰时把水存起来。已建成永久的算 L1；2021–2023 道路工程下建的 23 座挂在 CAPEX（本系统按 L2 处理）；Parsons 的临时塘是雨前开挖的 L2。手册不许在建成区内或紧邻建成区设开敞塘。",
        def_en="Open storage ponds holding the peak. Permanent ones are L1; the 23 built under 2021–2023 road projects are CAPEX (treated as L2 here); Parsons' temporary ponds are pre-event L2. Open ponds are not allowed in or next to built-up areas.",
        model_zh="SWMM 蓄水节点（面积曲线）+ 进水管 / 泵；二维里塘周边地表水可直接进塘（地表进塘）。", params=["库容", "水深", "边坡", "进出水方式"], refs=["手册 §4.2"]),
    "box_culvert": dict(zh="地下调蓄箱涵", en="Underground storage box culvert", fn=["store"], level_a=None, level_b=None, delivery=["capex"],
        level_user="L2 · CAPEX",
        def_zh="埋在路下或空地下的混凝土水箱，从管网分走雨峰的一部分存起来，雨后再用泵慢慢抽回管网。建成区里不能挖开敞塘时用它。进水口带闸，满 95% 就关，水留在管网里；满到顶由应急溢流堰漫回来水井。",
        def_en="A buried concrete tank under a road or open land that takes part of the peak out of the network and pumps it back slowly after the storm; used where open ponds are not allowed. Gated inlet closes at 95% full; an emergency weir spills back to the feed manhole.",
        model_zh="SWMM 蓄水节点（TABULAR）+ 进水孔口（ORIFICE，带关闸规则）+ 应急溢流堰（WEIR，拍门）+ 排空泵（Pump4）+ 控制规则；二维里不挖地形（地下构筑物，不做地表进水）。",
        params=["可用库容（可用水深 2.0 m）", "超高 0.3 m", "沉沙 5%", "覆土 ≥ 1.0 m", "灌满时间", "排空约 12 h"],
        refs=["手册 §4.2 B", "手册 §4.14.1", SRC_USER], schematic=True),
    "wetwell": dict(zh="湿井 / 集水井", en="Wet well / sump", fn=["collect", "pump"], level_a=None, level_b=None, delivery=["existing", "capex", "idd"],
        def_zh="泵站或临时泵取水的井，给泵留出调节容积。没有它，泵直接从检查井抽会抽空、失稳（29.RB2 的 PO 泵就是这样，已补成湿井）。",
        def_en="The sump a pump draws from, giving it working volume; without it a pump on a plain manhole runs dry and goes unstable (as with 29.RB2's PO pumps, since given wet wells).",
        model_zh="SWMM 蓄水节点（常面积）。", params=["面积", "启停水位"], refs=[SRC_OURS]),
    "surface_pipeline": dict(zh="地面管线", en="Surface pipeline", fn=["convey"], level_a="L2 CAPEX / L2 Municipality", level_b="L2 IDD / L2 Municipalities", delivery=["capex", "idd", "municipality"],
        def_zh="铺在地面上的临时管（常配固定泵），把积水点的水临时转输到出水口或能排的地方。雨前铺好，雨后撤。",
        def_en="Temporary pipes laid on the ground (usually with a stationary pump) conveying water from a flood point to an outfall; laid before the season, removed after.",
        model_zh="一维临时管段 + 泵（本系统还没建模——L2 非蓄水措施推演的下一步）。", params=["管径", "长度", "配泵流量"], refs=[]),
    "spillway": dict(zh="溢流堰", en="Spillway", fn=["spill"], level_a="L2 CAPEX", level_b="L2 IDD", delivery=["capex", "idd"],
        def_zh="水位到一定高度就溢出的堰，把超量的水安全地放到指定去处，保护下游或构筑物。",
        def_en="A weir that lets water over at a set level, releasing excess safely to a chosen place.",
        model_zh="SWMM 堰（WEIR）。", params=["堰顶高程", "堰宽"], refs=[]),
    "bund": dict(zh="土堤", en="Bund", fn=["divert"], level_a="L2 IDD / L2 Municipality", level_b="L2 IDD / L2 Municipalities", delivery=["idd", "municipality"],
        def_zh="临时堆的土埂，挡住水不进重要地点，或把水导向可以淹的地方。", def_en="Temporary earth embankments keeping water out of key sites or steering it to where flooding is acceptable.",
        model_zh="二维地形里抬高一条线（本系统还没建模）。", params=["堤顶高程", "长度"], refs=[]),
    "jersey_barrier": dict(zh="Jersey 护栏", en="Jersey barrier", fn=["divert"], level_a="L2 Municipality", level_b="L2 Municipalities", delivery=["municipality"],
        def_zh="混凝土隔离墩，临时排成一道挡水墙，护住隧道口、地下通道、重要设施。", def_en="Concrete barriers lined up as a temporary wall protecting tunnel portals, underpasses and key facilities.",
        model_zh="二维地形里的实心障碍（本系统还没建模）。", params=["高度", "长度"], refs=[]),
    "sandbag": dict(zh="沙袋", en="Sand bag", fn=["divert"], level_a="L2 Municipality", level_b="L2 Municipalities", delivery=["municipality"],
        def_zh="临时堆码的挡水沙袋，护门口、低处出入口。", def_en="Stacked sandbags protecting doorways and low entrances.",
        model_zh="二维地形抬高（本系统还没建模）。", params=["高度", "长度"], refs=[]),
    "small_barrier": dict(zh="小型挡水", en="Small barrier", fn=["divert"], level_a="L2 Municipality", level_b="L2 Municipalities（sand barriers）", delivery=["municipality"],
        def_zh="可移动的塑料 / 充水挡板，快速围出一小段挡水线。", def_en="Movable plastic or water-filled barriers for quick short lines.",
        model_zh="二维地形抬高（本系统还没建模）。", params=["高度", "长度"], refs=[]),
    "stationary_pump": dict(zh="固定泵", en="Stationary pump", fn=["pump"], level_a="L2 Municipality", level_b="L2 Municipalities（stationed pumps）", delivery=["municipality", "idd"],
        def_zh="雨前就放在固定位置的泵（常配地面管和湿井）。Parsons 方案里预置的 12 寸移动泵也按这一类算 L2。",
        def_en="Pumps stationed at a fixed site before the storm (usually with a surface pipeline and a sump). Parsons' pre-positioned 12-inch pumps are counted here as L2.",
        model_zh="SWMM 泵 + 湿井 + 启停规则。", params=["额定流量", "扬程", "台数"], refs=[SRC_USER]),
    "pump": dict(zh="临时泵（IDD）", en="Temporary pump (IDD)", fn=["pump"], level_a="L2 IDD", level_b="L2 IDD（temporary pumps）", delivery=["idd"],
        def_zh="IDD 线上雨前布设的临时泵。", def_en="Temporary pumps deployed before the season under the IDD line.",
        model_zh="同固定泵。", params=["额定流量", "扬程"], refs=[]),
    "tanker": dict(zh="罐车", en="Tanker", fn=["respond"], level_a="L3", level_b="L3", delivery=["erp"],
        def_zh="雨中把积水吸走拉走的罐车，适合没有排放去处、量不大的积水点。", def_en="Vacuum tankers removing water during the event where there is nowhere to pump to and volumes are small.",
        model_zh="第三道防线调度里的 PumpUnit（kind=tanker）。", params=["罐容", "往返时间"], refs=[], covered_by="PumpUnit"),
    "mobile_pump": dict(zh="移动泵", en="Mobile pump", fn=["respond", "pump"], level_a="L3", level_b="L3", delivery=["erp"],
        def_zh="雨中按积水情况动态调派的拖挂式泵。雨前预置在固定位置的算 L2（用户 2026-09-28 定）。",
        def_en="Trailer pumps dispatched dynamically during the event; those pre-positioned at fixed sites before the storm are L2.",
        model_zh="第三道防线调度里的 PumpUnit（6 / 8 / 12 寸），按驻地、路况、接力排放派单。", params=["口径", "流量", "架设时间"], refs=[SRC_USER], covered_by="PumpUnit"),
    "crane": dict(zh="吊车", en="Crane", fn=["respond"], level_a="L3", level_b="L3", delivery=["erp"],
        def_zh="雨中吊装大泵、吊移受阻车辆和障碍物。本系统的调度里还没有这一类资源。", def_en="Lifts large pumps and removes stranded vehicles or obstructions during the event; not yet in our dispatch.",
        model_zh="（未建模）", params=["吨位"], refs=[]),
}


def media(kind: str, lang: str = "zh") -> dict:
    """这一种资产的图：客户图裁出的实景小图、本系统画的剖面示意（相对控制台根路径）。"""
    out = {}
    if (MEDIA / f"{kind}.jpg").exists():
        out["photo"] = f"media/kinds/{kind}.jpg"; out["photo_src"] = SRC_A
    for lg in (lang, "zh"):
        if (MEDIA / f"{kind}.{lg}.svg").exists():
            out["schematic"] = f"media/kinds/{kind}.{lg}.svg"; break
    return out


def catalog(lang: str = "zh") -> list[dict]:
    rows = []
    for k, e in KB.items():
        rows.append({"kind": k, "zh": e["zh"], "en": e["en"], "functions": [{"key": f, "zh": FUNCTIONS[f][0], "en": FUNCTIONS[f][1]} for f in e["fn"]],
                     "level_a": e.get("level_a"), "level_b": e.get("level_b"), "level_user": e.get("level_user"), "delivery": e["delivery"],
                     "def_zh": e["def_zh"], "def_en": e["def_en"], "model_zh": e["model_zh"], "params": e["params"], "refs": e["refs"],
                     "covered_by": e.get("covered_by"), **media(k, lang)})
    return rows
