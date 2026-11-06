# -*- coding: utf-8 -*-
"""防线 2+（第五版）：塘是地上一个有标高、有进出水构筑物的东西，不是挂在井上的一个节点。

第四版的三个错（用户指出后逐条查实）：

1. **塘底拴在管子上，不跟地形走。** 塘底 = 接入井管底 − 0.30 − 挖深。接入井就在塘边时没问题，
   可用地约束把塘挪到了 106–658 m 外，那边的地面模型从来没看过——26 个塘里 17 个塘顶落到
   塘位地面以下 3.0–7.5 m，总开挖 5.0–9.5 m。手册 §4.14.1(b) 推荐进出水高差 1.8–2.4 m，
   那不是塘，是另一类构筑物。

2. **连接是零长度孔口。** 塘在水力上就是那个检查井本身：没有长度、没有管径、没有水头损失。
   按塘位真实地面挖 2 m 重算，26 个里只有 6 个能重力进水，20 个必须泵提升（扬程中位 3.1 m、
   最大 5.5 m）——Parsons 用泵不是偏好，是标高逼的。

3. **构筑物形式没分清。** 手册 §4.2：「Open lagoons shall not be permitted within or close to
   developed areas」，建成区内只能用离线箱涵（concrete box）+ 高位溢流回主管。

于是两种形式分开建：

  form="box"     地下离线调蓄箱涵。就地（积水点附近），埋在管底以下，重力进水 + 小孔口控流
                 + 高位溢流回主管 + 排空泵。建成区内合规。造价按钢筋混凝土结构 + 深开挖。
  form="lagoon"  地面开敞塘。**只落在台账 Public Open Space / Recreational / Agricultural
                 的地块上**，不兜底——建筑密度判不出「建成区」（Shaliela 820 块标住宅/商业/
                 工业的地块建筑覆盖率中位 0.0%，已分配未建设）。塘顶齐地面、往下挖 2 m。
                 实测这类地块离积水点中位 2.4–2.9 km，所以它自带一条输水干管。

两种形式都补上第四版缺的：真管连接（有长度、管径、管底、糙率）、应急溢流堰 §4.14.1(f)、
库容加 5% 沉沙 §4.2。
"""
import json, math, pathlib, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import OUT, WORK
from dig_pond import read_sections, parse_nodes, parse_coords, full_flow
from dig_pond3 import cluster_sites, DN_TIERS
from swbrain import landuse as LU

DEPTH = 2.0              # 设计水深。手册 §4.14.1(b) 推荐进出水高差 1.8–2.4 m
FREEBOARD = 0.30         # 超高
SEDIMENT = 1.05          # §4.2：塘/离线池库容要比所需调蓄量大 5%，留沉沙
CLEAR = 0.30             # §4.14.1(p)：管底至少高出塘底 0.30 m
PUMP_M3H = 700.0         # 12 寸移动泵，Parsons 报告用的型号，按台数计好比较
MIN_SLOPE = 0.0005       # 重力管可接受的最小坡降；比这还平就当重力送不过去
N_MANN = 0.013


def site_ground(cfg, sites, log=print):
    """塘位的真实地面高程（5 m DTM 在塘轮廓内取中位）。没有轮廓就取中心点。"""
    from shapely.geometry import Point, shape
    from shapely.ops import transform as reproj
    from pyproj import Transformer
    from swbrain import terrain
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform
    with terrain.open_dtm(cfg) as ds:
        for s in sites:
            fp = s.get("footprint")
            if fp:
                g = reproj(to_utm, shape(fp))
                x0, y0, x1, y1 = g.bounds
                xs, ys = np.meshgrid(np.linspace(x0, x1, 12), np.linspace(y0, y1, 12))
                pts = [(a, b) for a, b in zip(xs.ravel(), ys.ravel())
                       if g.contains(Point(a, b))] or [(g.centroid.x, g.centroid.y)]
            else:
                pts = [(s["x"], s["y"])]
            z = terrain.sample_points(ds, np.array([p[0] for p in pts]),
                                      np.array([p[1] for p in pts]))
            z = z[np.isfinite(z)]
            s["ground_m"] = float(np.median(z)) if len(z) else None
    bad = [s["id"] for s in sites if s.get("ground_m") is None]
    if bad:
        log(f"  ! DTM 采不到高程：{bad}")
    return sites


def set_levels(sites, inv, form, log=print):
    """定塘底/塘顶，判进水方式。

    box    箱涵埋在管底以下：塘底 = min(接入管底) − CLEAR − 挖深。永远重力进水。
    lagoon 塘顶齐地面：塘底 = 塘位地面 − 挖深。管底 ≥ 塘底 + CLEAR 才能重力，否则泵提升。
    """
    for s in sites:
        if s.get("skip"):          # 上游已经判过挖不了，别用笼统原因覆盖掉具体原因
            continue
        cns = [c for c in s["connect"] if c in inv]
        s["connect"] = cns
        if not cns:
            s["skip"] = "接入点都不在模型里"; continue
        i_min = min(inv[c] for c in cns)
        s["src_invert_m"] = i_min
        if form == "box":
            s["bottom_m"] = i_min - CLEAR - DEPTH
            s["top_m"] = s["bottom_m"] + DEPTH
            s["inflow"] = "gravity"
            s["lift_m"] = 0.0
            g = s.get("ground_m")
            s["cover_m"] = (g - s["top_m"]) if g is not None else None   # 箱涵顶的覆土
        else:
            g = s.get("ground_m")
            if g is None:
                s["skip"] = "塘位没有地面高程"; continue
            s["bottom_m"] = g - DEPTH
            s["top_m"] = g                                   # 塘顶齐地面
            head = i_min - (s["bottom_m"] + CLEAR)
            s["inflow"] = "gravity" if head >= 0 else "lift"
            s["lift_m"] = max(0.0, -head)
        s["depth_m"] = DEPTH
    return sites


def size_links(sites, form, window_h=2.0, log=print):
    """定输水管与泵：库容要在雨峰窗口内灌满，管子按实际坡降选径，坡降不够就上泵。"""
    for s in sites:
        if s.get("skip"):
            continue
        s["vol_m3"] = s["vol_m3"] * SEDIMENT                  # §4.2 加 5% 沉沙
        s["area_m2"] = s["vol_m3"] / DEPTH
        n = max(len(s["connect"]), 1)
        q_need = s["vol_m3"] / window_h                        # m³/h
        L = max(float(s.get("moved_m") or 0.0), 20.0)          # 输水管长 = 实际挪的距离
        s["conveyance_m"] = L
        if s["inflow"] == "gravity":
            slope = (s["src_invert_m"] - (s["bottom_m"] + CLEAR)) / L
            s["slope"] = slope
            if slope < MIN_SLOPE:                              # 太平，重力送不过去
                s["inflow"] = "lift"; s["lift_m"] = max(s.get("lift_m", 0.0), 0.5)
            else:
                dn = next((d for d in DN_TIERS
                           if full_flow(d / 1000.0, slope, N_MANN) * n >= q_need), 1500)
                s["dn_mm"] = dn
                s["q_in_m3h"] = full_flow(dn / 1000.0, slope, N_MANN) * n
                s["n_pumps_in"] = 0
        if s["inflow"] == "lift":
            s["slope"] = None
            npump = max(1, int(math.ceil(q_need / PUMP_M3H)))
            s["n_pumps_in"] = npump
            s["q_in_m3h"] = npump * PUMP_M3H
            # 压力输水管：按 1.5 m/s 选径
            a = s["q_in_m3h"] / 3600.0 / 1.5
            dn = next((d for d in DN_TIERS if math.pi * (d / 2000.0) ** 2 >= a), 1500)
            s["dn_mm"] = dn
        # 进水孔口按**控流**定径：灌满 ≈ 雨峰窗口（v3 的规则，两个方向都实测撞过）。
        # 输水管的管径是「能不能送到」，孔口的管径是「该让多少水进来」，两回事。
        h = DEPTH / 2.0                                        # 灌注过程的平均水头
        a_need = (q_need / 3600.0) / n / (0.65 * math.sqrt(2 * 9.81 * h))
        s["orifice_mm"] = next((d for d in DN_TIERS
                                if math.pi * (d / 2000.0) ** 2 >= a_need), 1500)
        s["q_gate_m3h"] = (0.65 * math.pi * (s["orifice_mm"] / 2000.0) ** 2
                           * math.sqrt(2 * 9.81 * h) * 3600.0 * n)
        s["fill_min"] = s["vol_m3"] / s["q_gate_m3h"] * 60.0
        # 排空：雨峰过后抽回管网（手册 §4.2「The outflow may be by gravity return or pumping back」）
        s["empty_q_m3h"] = s["vol_m3"] / 12.0
        s["n_pumps_out"] = max(1, int(math.ceil(s["empty_q_m3h"] / PUMP_M3H)))
    return sites


def _rims(lines, idx) -> dict:
    """节点 → 井盖高程（管底 + 井深）。SWMM [JUNCTIONS] 第一个数是管底，不是地面。"""
    out = {}
    for sec in ("JUNCTIONS", "STORAGE"):
        if sec not in idx:
            continue
        a, b = idx[sec]
        for ln in lines[a + 1:b + 1]:
            s = ln.strip()
            if not s or s.startswith(";") or s.startswith("["):
                continue
            c = s.split()
            try:
                out[c[0]] = float(c[1]) + float(c[2])
            except (IndexError, ValueError):
                continue
    return out


def _succ(lines, idx) -> dict:
    """节点 → 下游节点表，按 [CONDUITS] 的 from→to。"""
    out: dict = {}
    if "CONDUITS" not in idx:
        return out
    a, b = idx["CONDUITS"]
    for ln in lines[a + 1:b + 1]:
        s = ln.strip()
        if not s or s.startswith(";") or s.startswith("["):
            continue
        c = s.split()
        if len(c) >= 3:
            out.setdefault(c[1], []).append(c[2])
    return out


def _outlet_node(succ: dict, inv: dict, cns: list, hops: int = 4):
    """塘的出水 / 排空目标：**必须在进水井的下游**。

    接回进水井会形成 `管网 → 塘 → 泵 → 管网` 的闭环。这个坑踩过，而且
    「等接收节点退了水再抽」那条控制规则**防不住** —— 泵照样 95.83% 的时间在转：
      · 六台排空泵累计抽了约 10 万 m³，而箱涵总库容只有 32,034 m³；
      · PU_NP5 启停 20,562 次（典型泵振荡）；
      · 结果 72 小时后街面还滞着 8,543 m³，而同一场雨 L1 在 24 小时就退到 32 m³。
    只看「洪水量」看不出这个毛病（它确实降了 51.5%），要看退水才暴露。

    从各进水井沿下游走最多 hops 跳，取**不在进水井集合里、且井底最低**的那个。
    """
    avoid = set(cns)
    best = None
    for start in cns:
        frontier, seen = [start], {start}
        for _ in range(hops):
            nxt = []
            for n in frontier:
                for m in succ.get(n, []):
                    if m in seen:
                        continue
                    seen.add(m)
                    nxt.append(m)
                    if m in avoid:
                        continue
                    z = inv.get(m)
                    if z is None:
                        continue
                    if best is None or z < best[1]:
                        best = (m, z)
            if not nxt:
                break
            frontier = nxt
    return best[0] if best else None


def dig5(src: pathlib.Path, dst: pathlib.Path, sites: list[dict], form: str,
         empty_start_h: int = 14, close_at: float = 0.95, drain_depth: float = 0.50,
         log=print):
    """写出带真管、带闸、带提升泵、带应急溢流堰的 .inp。"""
    lines, idx = read_sections(src)
    inv, kind = parse_nodes(lines, idx)
    # 泵曲线要按**模型自己的流量单位**写。原先写死 m³/h ÷ 3.6 = L/s：Mussafah 客户模型是 LPS，对；
    # RB_02 重建模型是 CMS，排空泵被放大 1000 倍（NP1 0.139 → 139 m³/s，2026-09-28 查出），箱涵几秒排空倒进下游井
    units = next((ln.split()[1].upper() for ln in src.read_text(encoding="utf-8", errors="replace").splitlines()
                  if ln.strip().upper().startswith("FLOW_UNITS") and len(ln.split()) > 1), "CMS")
    per_m3h = {"LPS": 1 / 3.6, "CMS": 1 / 3600.0, "MLD": 24 / 1000.0, "CFS": 1 / 3600.0 / 0.0283168,
               "GPM": 1 / 3600.0 / 6.309e-5, "MGD": 1 / 3600.0 / 0.0438126}[units]
    fq = lambda q_m3h: f"{q_m3h * per_m3h:.4g}"
    rim = _rims(lines, idx)
    succ = _succ(lines, idx)
    add = {"STORAGE": [], "JUNCTIONS": [], "CURVES": [], "CONDUITS": [], "ORIFICES": [],
           "WEIRS": [], "XSECTIONS": [], "COORDINATES": [], "PUMPS": [], "CONTROLS": []}
    n_ok = 0
    for s in sites:
        if s.get("skip"):
            log(f"  ✗ {s['id']}: {s['skip']}"); continue
        pid, cns = s["id"], s["connect"]
        bot, top, dn = s["bottom_m"], s["top_m"], float(s["dn_mm"]) / 1000.0
        add["STORAGE"].append(f"{pid} {bot:.3f} {DEPTH + FREEBOARD:.2f} 0 TABULAR {pid}_Curve 0 0 0 0 0")
        add["CURVES"].append(f"{pid}_Curve Storage 0 {s['area_m2']:.1f} "
                             f"{DEPTH + FREEBOARD:.2f} {s['area_m2']:.1f}")
        add["COORDINATES"].append(f"{pid} {s['x']:.3f} {s['y']:.3f}")
        gates = []
        for j, cn in enumerate(cns, 1):
            # 塘边的进水井：井底 = 塘底 + CLEAR（§4.14.1(p) 管底至少高出塘底 0.30 m）
            jin = f"JI_{pid}_{j}"
            z_in = bot + CLEAR
            # 井盖做到地面，且不低于上游检查井的井盖：闸关之后水该顶回上游、从上游冒，
            # 而不是在地下好几米处「冒」出来（实测那样会凭空多出 4,193 m³）
            # 比上游井盖再高 1 m：闸关之后水顶回**街上**的井冒出来，算进街道积水。
            # 让它在塘边冒等于把账记到塘头上，③ 会显得比实际好——宁可算成下限。
            rim_ji = max(s.get("ground_m") or top,
                         max((rim.get(c, top) for c in cns), default=top)) + 1.0
            add["JUNCTIONS"].append(f"{jin} {z_in:.3f} {max(rim_ji - z_in, 1.0):.2f} 0 0 0")
            add["COORDINATES"].append(f"{jin} {s['x']:.3f} {s['y']:.3f}")
            if s["inflow"] == "gravity":
                # 重力输水管：接入井 → 塘边进水井。坡降由两端井底自然给出，偏移取 0
                add["CONDUITS"].append(
                    f"CV_{pid}_{j} {cn} {jin} {s['conveyance_m']:.2f} {N_MANN} 0 0 0 0")
                add["XSECTIONS"].append(f"CV_{pid}_{j} CIRCULAR {dn:.3f} 0 0 0")
            else:
                # 提升（Parsons 同一套拓扑）：泵 → 泵出水井 → 压力输水管 → 塘边进水井
                jp = f"JP_{pid}_{j}"
                z_p = top + FREEBOARD
                rim_jp = max(z_p + 1.0, (s.get("ground_m") or z_p) + 1.0)
                add["JUNCTIONS"].append(f"{jp} {z_p:.3f} {rim_jp - z_p:.2f} 0 0 0")
                add["COORDINATES"].append(f"{jp} {s['x']:.3f} {s['y']:.3f}")
                add["PUMPS"].append(f"PL_{pid}_{j} {cn} {jp} {pid}_Lift OFF 0 0")
                add["CONDUITS"].append(
                    f"CV_{pid}_{j} {jp} {jin} {s['conveyance_m']:.2f} {N_MANN} 0 0 0 0")
                add["XSECTIONS"].append(f"CV_{pid}_{j} CIRCULAR {dn:.3f} 0 0 0")
            # 塘的进水口是带闸的孔口（塘满关闸，水留在管网里）
            oid = f"IN_{pid}_{j}"
            add["ORIFICES"].append(f"{oid} {jin} {pid} SIDE 0 0.65 NO 0")
            add["XSECTIONS"].append(f"{oid} CIRCULAR {s['orifice_mm'] / 1000.0:.3f} 0 0 0")
            gates.append(oid)
        if s["inflow"] == "lift":
            q1 = s["q_in_m3h"] / len(cns)
            add["CURVES"].append(f"{pid}_Lift Pump4 0 {fq(q1)} "
                                 f"{DEPTH + FREEBOARD:.2f} {fq(q1)}")
        # 应急溢流堰 §4.14.1(f)：塘满到顶就漫回它来的那个节点，而不是让塘节点凭空冒水。
        # 堰顶是**相对塘底的高度**：模型没写 LINK_OFFSETS，SWMM 默认 DEPTH 口径。
        # 拍门必须是 YES（单向）。写 NO 时深埋箱涵的堰顶比上游井的管底还低，
        # 水会从管网**倒着**漫过堰进塘，把「塘满关闸」整条规则绕过去——
        # 实测 Mussafah NP4 因此持续冒了 672 m³。
        # 出水口必须在**下游**：接回进水井会形成 管网→塘→泵→管网 的闭环，
        # 靠控制规则防不住（见 _outlet_node 的实测数据）。找不到下游就退回进水井，
        # 但要在结果里标出来，别让它悄悄退化。
        tgt = _outlet_node(succ, inv, cns)
        s["outlet_node"] = tgt or cns[0]
        s["outlet_downstream"] = tgt is not None
        if tgt is None:
            log(f"  ⚠ {pid}: 进水井下游找不到可用节点，出水退回进水井 {cns[0]}，"
                f"可能形成回流环")
        add["WEIRS"].append(f"OF_{pid} {pid} {s['outlet_node']} TRANSVERSE "
                            f"{DEPTH:.3f} 1.4 YES 0")
        add["XSECTIONS"].append(f"OF_{pid} RECT_OPEN {FREEBOARD:.2f} "
                                f"{max(math.sqrt(s['area_m2']) / 4, 2.0):.2f} 0 0")
        # 排空泵：雨峰过后抽回管网，抽到**下游**井，且等接收节点退了水才启
        tgt = s["outlet_node"]
        add["PUMPS"].append(f"PU_{pid} {pid} {tgt} {pid}_Pump OFF 0 0")
        add["CURVES"].append(f"{pid}_Pump Pump4 0 {fq(s['empty_q_m3h'])} "
                             f"{DEPTH:.2f} {fq(s['empty_q_m3h'])}")
        add["CONTROLS"].append(f"RULE {pid}_GATE_SHUT")
        add["CONTROLS"].append(f"IF NODE {pid} DEPTH >= {close_at * DEPTH:.2f}")
        for k, oid in enumerate(gates):
            add["CONTROLS"].append(f"{'THEN' if k == 0 else 'AND'} ORIFICE {oid} SETTING = 0")
        add["CONTROLS"] += ["PRIORITY 3", ""]
        add["CONTROLS"].append(f"RULE {pid}_GATE_OPEN")
        add["CONTROLS"].append(f"IF NODE {pid} DEPTH < {close_at * DEPTH * 0.9:.2f}")
        for k, oid in enumerate(gates):
            add["CONTROLS"].append(f"{'THEN' if k == 0 else 'AND'} ORIFICE {oid} SETTING = 1")
        add["CONTROLS"] += ["PRIORITY 2", ""]
        if s["inflow"] == "lift":
            # 提升泵：管网里水位高就抽，塘满就停
            for j, cn in enumerate(cns, 1):
                add["CONTROLS"] += [
                    f"RULE {pid}_LIFT{j}_ON", f"IF NODE {cn} DEPTH > 0.50",
                    f"AND NODE {pid} DEPTH < {close_at * DEPTH:.2f}",
                    f"THEN PUMP PL_{pid}_{j} STATUS = ON", "PRIORITY 6", "",
                    f"RULE {pid}_LIFT{j}_OFF", f"IF NODE {cn} DEPTH <= 0.20",
                    f"THEN PUMP PL_{pid}_{j} STATUS = OFF", "PRIORITY 7", "",
                    f"RULE {pid}_LIFT{j}_FULL", f"IF NODE {pid} DEPTH >= {close_at * DEPTH:.2f}",
                    f"THEN PUMP PL_{pid}_{j} STATUS = OFF", "PRIORITY 8", ""]
        add["CONTROLS"] += [
            f"RULE {pid}_EMPTY_ON", f"IF NODE {pid} DEPTH > 0.05",
            f"AND NODE {tgt} DEPTH < {drain_depth:.2f}",
            f"AND SIMULATION TIME > {empty_start_h}:00:00",
            f"THEN PUMP PU_{pid} STATUS = ON", "PRIORITY 1", "",
            f"RULE {pid}_EMPTY_OFF", f"IF NODE {pid} DEPTH <= 0.05",
            f"THEN PUMP PU_{pid} STATUS = OFF", "PRIORITY 4", "",
            # 回差：停泵门槛比启泵高一倍，否则水深在阈值附近抖一下就启停一次
            # —— 实测 PU_NP1 启停过 12,802 次。
            f"RULE {pid}_EMPTY_HOLD", f"IF NODE {tgt} DEPTH >= {2 * drain_depth:.2f}",
            f"THEN PUMP PU_{pid} STATUS = OFF", "PRIORITY 5", ""]
        n_ok += 1
        log(f"  ✓ {pid} {s['vol_m3']:>9,.0f} m³｜塘底 {bot:>7.2f} 塘顶 {top:>7.2f}"
            f"｜{'重力 DN%d 坡 %.4f' % (s['dn_mm'], s['slope']) if s['inflow'] == 'gravity' else '提升 %d 台泵 扬程 %.1f m' % (s['n_pumps_in'], s['lift_m'])}"
            f"｜输水 {s['conveyance_m']:>5,.0f} m｜闸 DN{s['orifice_mm']} "
            f"{s['q_gate_m3h']:>7,.0f} m³/h（灌满 {s['fill_min']:>3.0f} min）")
    # 新小节必须插在 [CONTROLS] 之前：SWMM 按出现顺序解析控制规则里的对象（ERROR 209）
    new_secs = [sec for sec, rows in add.items() if sec not in idx and rows]
    anchor = idx.get("CONTROLS", [len(lines), len(lines)])[0]
    out = []
    for i, ln in enumerate(lines):
        if i == anchor:
            for sec in new_secs:
                out.append(f"[{sec}]"); out.extend(add[sec]); out.append("")
        out.append(ln)
        for sec, rows in add.items():
            if sec in idx and i == idx[sec][0] and rows:
                out.extend(rows)
    if anchor >= len(lines):
        for sec in new_secs:
            out.append(f"[{sec}]"); out.extend(add[sec]); out.append("")
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    log(f"  → {dst.name}（{n_ok} 个塘）")
    return dst
