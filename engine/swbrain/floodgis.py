# -*- coding: utf-8 -*-
"""客户 flood 库的资产图层：汇水区边界、L2 新建塘、L1 既有蓄水构筑物。

flood 库（10.255.254.109:5443）schema `stormwater_drainage` 共 35 张表、497,452 行。
**它不是纯 L1**——里面同时有 `spillway`（客户框架里 L2 独有）和 `movable_pump`
（L3 独有）两张表，所以表属于哪一层必须**逐表判**，不能按 schema 一刀切。

判 L1 还是 L2 的分辨器是客户自己的 `informigration` 字段，跨 6 张表验过：

    pipeline   EXISTING 209,336 / 260,725      inlet     EXISTING 132,689 / 167,459
    outfall    EXISTING     229 /     254      soakaway  EXISTING     882 /     977
    ps_pump    EXISTING      97 /     103      sw_reservoirstructure  EXISTING 15 / 20
    pond       EXISTING       0 /      23   ← 一个都没有

无争议的 L1 资产八九成标 `EXISTING`；23 个 pond **没有一个**标 EXISTING，
全是 `INSERT_BACKLOG` / `NewProject` / `INSERT`，且挂在道路工程下
（编号 `SW_MAR_RD113_PND_39`、`projectid` `DP2304-RN-02`，建成 2021–2023）——
这正是客户框架里 **L2 CAPEX「Ponds + Pump Stations」** 的形态。

**两处先前判错，原因留在这里免得再犯**：
  · 客户框架图的 L1 和 L2 **都列了 Ponds**，只看 L1 那一行会判反。
  · 拿 `status=Existing/Active`、`data_source=AS BUILT` 当「是 L1」的证据是无效的：
    那只能证明**建成了**，证不了**是永久设施**。**已建成 ≠ 永久。**

三层图层：

    catchment   `catchment.catchment`              39 个汇水区边界
    pond        `stormwater_drainage.pond`         23 个 **L2 新建塘**（2021–2023，合计 194 万 m³）
    reservoir   `stormwater_drainage.sw_reservoirstructure`  20 个 **L1 既有蓄水构筑物**

**落在三个汇水区里的：L2 塘 0 座、L1 蓄水构筑物 0 座**（判据见 SCOPE_LABELS）。
最近的一座塘在 RB3 界外 547 m（2,497 m³）——它离 RB3 的管网节点只有 299 m，
**邻近管网不等于落在区内**，这两件事我混过一次，别再混。
全市口径下 L2 已建成 23 座、194 万 m³，此前系统里「L2 已建成 0 件」是全称判断，那是错的。

源库只读，本模块只做 SELECT。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "console" / "data"

# 图层定义：表、几何列、要带出来的属性、以及给页面的说明
# 我们实际推演的三个汇水区在 catchment.catchment 里的 label。
# 库里同名行有重复（RB3 两行、OLD 七行），所以按 label 取并集而不是按 id。
SCOPE_LABELS = ("Mussafah_00", "Shaliela", "RB3")

LAYERS = {
    "catchments_sw": {
        "schema": "catchment", "table": "catchment", "geom": "wkb_geometry",
        "zh": "汇水区边界", "en": "Catchments",
        "props": {"id": "id", "label": "label", "area_ha": "tbl_ha", "parts": "parts"},
        "note": "客户 flood 库 catchment.catchment",
    },
    "ponds_l2": {
        "schema": "stormwater_drainage", "table": "pond", "geom": "geom",
        "zh": "塘（L2 新建）", "en": "Ponds (L2, new-build)",
        "props": {
            "unitid": "unitid", "name": "pond_name", "desc": "unitdesc",
            "status": "status", "condition": "condition", "unittype": "unittype",
            "volume_m3": "volume", "area_m2": "shape_area",
            "depth_m": "depth", "cover_level": "cover_level",
            "bottom_level": "bottom_floor_level", "pipes": "no_of_connected_pipes",
            "district": "zone_or_district_name", "sector": "sector_or_community_name",
            "city": "cityname", "ownership": "ownership", "data_source": "data_source",
            "installed": "asset_installation_date", "handover": "handover_date",
            "comments": "comments", "project": "projectid", "migration": "informigration",
        },
        "note": "客户 flood 库 stormwater_drainage.pond —— informigration 无一为 EXISTING，"
                "挂在道路工程下（2021–2023 建成），属 L2 CAPEX",
    },
    "reservoirs_l1": {
        "schema": "stormwater_drainage", "table": "sw_reservoirstructure", "geom": "geom",
        "zh": "蓄水构筑物（L1 既有）", "en": "Reservoir structures (L1, existing)",
        "props": {"unitid": "unitid", "status": "status", "migration": "informigration",
                  "district": "zone_or_district_name", "city": "cityname"},
        "note": "客户 flood 库 stormwater_drainage.sw_reservoirstructure —— "
                "informigration 15/20 为 EXISTING，属 L1",
    },
}


def params(cfg: dict) -> dict:
    return (cfg.get("source") or {}).get("flood") or {}


def available(cfg: dict) -> bool:
    return bool(params(cfg).get("host"))


def export(cfg: dict, key: str, log=print) -> Path:
    """一层 → console/data/<key>.geojson（EPSG:4326）。"""
    import psycopg
    L = LAYERS[key]
    c = params(cfg)
    sel = ", ".join(f'"{src}"::text as "{dst}"' for dst, src in L["props"].items())
    labels = ", ".join("'" + s.replace("'", "''") + "'" for s in SCOPE_LABELS)
    # in_scope：这个要素落不落在我们推演的三个汇水区里。库是全市范围，
    # 不打这个标记，区内区外在图上分不开。
    #
    # 汇水区那一层按 label 判，**不能**用 st_intersects：相邻汇水区共边，
    # 一相交就全成了「区内」（39 个会判出 8 个，实际只有 4 行）。
    # 资产层再带一个 dist_m（到最近的那个汇水区边界，米），
    # 免得把「邻近」说成「区内」——最近的那座塘在 RB3 界外 547 m。
    if key == "catchments_sw":
        scope_expr = f'(label in ({labels}))'
        dist_expr = "0"
    else:
        scope_expr = f'st_intersects(st_transform("{L["geom"]}", 4326), s.g)'
        dist_expr = (f'round(st_distance(st_transform("{L["geom"]}", 4326)::geography,'
                     f' s.g::geography)::numeric)')
    sql = f'''
        with scope as (
          select st_union(st_transform(wkb_geometry, 4326)) g
            from catchment.catchment
           where label in ({labels}) and wkb_geometry is not null)
        select json_build_object('type','FeatureCollection','features',
          coalesce(json_agg(json_build_object(
            'type','Feature',
            'geometry', st_asgeojson(p.g)::json,
            'properties', to_jsonb(p) - 'g')), '[]'::json))
        from (select {sel},
                     {scope_expr} as in_scope,
                     {dist_expr} as dist_m,
                     st_transform("{L['geom']}", 4326) as g
                from "{L['schema']}"."{L['table']}", scope s
               where "{L['geom']}" is not null) p
    '''
    with psycopg.connect(host=c["host"], port=c["port"], user=c["user"],
                         password=c["password"], dbname=c["dbname"],
                         connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            gj = cur.fetchone()[0]
    feats = gj.get("features") or []
    gj["source"] = L["note"]
    gj["layer"] = key
    gj["scope"] = list(SCOPE_LABELS)
    n_in = sum(1 for x in feats if (x.get("properties") or {}).get("in_scope"))
    out = [float((x.get("properties") or {}).get("dist_m") or 0)
           for x in feats if not (x.get("properties") or {}).get("in_scope")]
    near = f"，最近的一个在界外 {min(out):,.0f} m" if out and key != "catchments_sw" else ""

    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"{key}.geojson"
    f.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    log(f"  {L['zh']:<14}{len(feats):>6} 个要素（三区内 {n_in}{near}）"
        f"　{f.stat().st_size/1024:>7,.0f} KB → {f.name}")
    return f


def summary(cfg: dict, key: str) -> dict:
    """这一层的概况，给页面和自检用。"""
    f = OUT / f"{key}.geojson"
    if not f.exists():
        return {"layer": key, "available": False}
    gj = json.loads(f.read_text(encoding="utf-8"))
    ft = gj.get("features") or []
    o = {"layer": key, "available": True, "n": len(ft),
         "n_in_scope": sum(1 for x in ft if (x.get("properties") or {}).get("in_scope")),
         "scope": gj.get("scope"),
         "zh": LAYERS[key]["zh"], "en": LAYERS[key]["en"], "source": gj.get("source")}
    if key == "ponds_l2":
        vol = vol_in = 0.0
        st = {}
        for x in ft:
            p = x.get("properties") or {}
            try:
                v = float(p.get("volume_m3") or 0)
            except (TypeError, ValueError):
                v = 0.0
            vol += v
            if p.get("in_scope"):
                vol_in += v
            s = str(p.get("status") or "?")
            st[s] = st.get(s, 0) + 1
        o["total_volume_m3"] = round(vol)
        o["in_scope_volume_m3"] = round(vol_in)
        o["by_status"] = st
    return o


def main(argv=None):
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from . import db
    ap = argparse.ArgumentParser(description="把 flood 库的 L1 资产与汇水区导成控制台图层")
    ap.add_argument("--config", default=None)
    ap.add_argument("--only", default=None, choices=list(LAYERS))
    a = ap.parse_args(argv)
    cfg = db.load_config(a.config)
    if not available(cfg):
        print("config 里没有 [source.flood]，跳过"); return 1
    print("从 flood 库导出图层（源库只读）：")
    for k in ([a.only] if a.only else list(LAYERS)):
        export(cfg, k)
    for k in ([a.only] if a.only else list(LAYERS)):
        s = summary(cfg, k)
        if s.get("by_status"):
            print(f"\n{s['zh']}：全市 {s['n']} 个 / 容积 {s['total_volume_m3']:,} m³，"
                  f"状态 {s['by_status']}")
            print(f"  落在 {' / '.join(SCOPE_LABELS)} 里的："
                  f"{s['n_in_scope']} 个 / {s['in_scope_volume_m3']:,} m³")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
