# -*- coding: utf-8 -*-
"""区域推演主脚本（1104 批副本：加「L2 / L2planned 输入与 L1 相同则复制 L1 结果」；其余同 run_region.py）（L1 / L2 / L2planned）：对每个场景 建模 → 一二维耦合计算 → 后处理 → 成果导出，进度写进 status.json，日志写进 run.log / <场景>.log。
由 <区域>-server.sh start 在容器里调用（容器只是运行环境，本脚本在宿主机 engine/ 里，映射成 /app）。

场景记号 = S<雨量mm>D<历时h>[@态]（如 S33D1、S61D1@L2planned）；不带 @ 的按 --state（缺省 L1）。
job_id = <区域>_<雨量>mm<历时>h（L1，如 RDA_33mm1h）；L2 / L2planned 加后缀（RDA_61mm1h_L2、RDA_61mm1h_L2planned）。同时是 deliveries/、shp_dat/ 的目录名，也显示在两个页面上。
态按数据的 ASSET_STAGE 分（见 region_features.py）：L1 = Existing；L2 = L1 + Temporary；L2planned = L2 + Planned。管线不分状态。
含临时 / 计划态但其中的设施数据缺参数建不了时，默认拒绝跑（--allow-unmodeled 才跑，并在参数报告 / README 里写明未建模的设施）。
二维网格精度 --grid（本次 15 m）写进 outputs.json、params.json、交付 README、GRID_<精度>m.txt、逐帧图目录名（g15）。

用法（容器内）：python /app/tools/run_region.py --region RDA --scenarios S33D1,S61D1,S61D1@L2planned --host <主机名> --status /work/run/RDA/status.json
可选：--nas /nas --work /work --grid 15 --jobs N（并行场景数，缺省按 servers.toml 算）--max-sim-h H（只用于冒烟测试：只算前 H 小时）--allow-unmodeled
续跑：已有二维结果（npz + 运行记录）的场景跳过计算；已转过的 shp+dat 跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import model_rules as MR  # noqa: E402
import ponding_ge as PG  # noqa: E402
import region_model as RM  # noqa: E402
import region_deliver as RDL  # noqa: E402
import region_post as RP  # noqa: E402
import run_params as RPR  # noqa: E402
from run_status import Status  # noqa: E402

STAGE_PRIORITY = ["prepare", "build", "compute", "post", "export"]


STATES = ("L1", "L2", "L2planned")


def split_token(tok: str, default_state: str = "L1") -> tuple[str, str]:
    """S61D1@L2 → ("S61D1", "L2")；S61D1 → ("S61D1", default_state)。"""
    tag, _, st = tok.partition("@")
    st = st or default_state
    if st not in STATES:
        raise SystemExit(f"不认识的态 {st}（{tok}）：L1 / L2 / L2planned")
    return tag, st


def job_id(region: str, tag: str, state: str = "L1") -> str:
    m = re.fullmatch(r"S(\d+(?:\.\d+)?)D(\d+)", tag)
    return f"{region}_{m.group(1)}mm{m.group(2)}h" + ("" if state == "L1" else f"_{state}")


class Logger:
    def __init__(self, tag: str, path: Path):
        self.tag, self.f = tag, open(path, "a", encoding="utf-8")

    def __call__(self, *a):
        line = " ".join(str(x) for x in a)
        self.f.write(line + "\n"); self.f.flush()
        print(f"[{self.tag}] {line}", flush=True)


def _copy_from_l1(c2d: Path, name: str, state: str, fp: str, st, key: str, tag: str, end_h: float, log) -> bool:
    """用户 2026-11-05：L2 / L2planned 里新增设施全部没建模（输入和 L1 完全一样）时，把 L1 的计算结果复制成这个态的结果，不重算（设备清单照常展示并标「未建模」）。
    判据：L1 的输入指纹（<名>_L1.run.fp：一维 inp 去掉线程数那行 + 地形）与本态相同。L1 还没算完就等（队列里 L1 总排在它前面）；L1 没成果也没在跑 / 失败了 → 不复制，自己算。
    复制的文件：<名>_L1_2d.npz、twoway_<名>_L1.json、<名>_L1_2way.rpt → 改成本态的名字；twoway json 里的 state / 名字字段同步改，并加 copied_from_L1 标记。"""
    import shutil
    srcs = {"npz": c2d / f"{name}_L1_2d.npz", "tw": c2d / f"twoway_{name}_L1.json", "rpt": c2d / f"{name}_L1_2way.rpt", "fp": c2d / f"{name}_L1.run.fp"}
    waited = 0
    while not all(p.is_file() for p in srcs.values()):
        l1 = (st._read().get("scenarios") or {}).get(tag) or {}
        if not l1 or l1.get("state") == "failed":      # L1 不在本次队列里 / 失败了：不等了，自己算
            log("  L1 不在本次队列里或已失败，不复制，自己算")
            return False
        if waited == 0:
            log("  等 L1 算完再决定是否复制 L1 结果（输入相同就复制）…")
            st.update(key, stage="compute", stage_pct=0.0, message="等 L1 算完（输入相同就复制 L1 结果）")
        time.sleep(60); waited += 60
        if waited > 72 * 3600:
            log("  等 L1 超过 72 h，放弃复制，自己算")
            return False
    if srcs["fp"].read_text().strip() != fp:
        log(f"  本态输入指纹 {fp} 与 L1（{srcs['fp'].read_text().strip()}）不同，说明有新增设施建进了模型，自己算")
        return False
    tg = f"{name}_{state}"
    shutil.copy2(srcs["npz"], c2d / f"{tg}_2d.npz")
    shutil.copy2(srcs["rpt"], c2d / f"{tg}_2way.rpt")
    tw = json.loads(srcs["tw"].read_text(encoding="utf-8"))
    for k, v in list(tw.items()):
        if isinstance(v, str) and v.endswith("_L1"):
            tw[k] = v[:-3] + "_" + state
    if "state" in tw:
        tw["state"] = state
    tw["copied_from_L1"] = True
    (c2d / f"twoway_{tg}.json").write_text(json.dumps(tw, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  ✔ 新增设施全部未建模，输入指纹与 L1 相同（{fp}）→ 复制 L1 的计算结果作为 {state}，不重算")
    st.update(key, stage="compute", stage_pct=1.0, sim_h_done=end_h, sim_h_total=end_h, message=f"复制 L1 结果（{state} 新增设施未建模）")
    return True


def run_one(args_d: dict, token: str) -> dict:
    """一个场景（+ 态）的全流程。在子进程里跑；返回 {tag, ok, problems, outputs}。token = S<雨量>D<历时>[@态]，也是 status.json 里的键。"""
    a = argparse.Namespace(**args_d)
    tag, state = split_token(token, a.state)
    work, nas = Path(a.work), Path(a.nas)
    rundir = work / "run" / a.region
    st = Status(a.status)
    log = Logger(token, rundir / f"{token.replace('@', '_')}.log")
    rcfg = tomllib.loads((ROOT / "data" / "regions" / a.region / "region.toml").read_text(encoding="utf-8"))
    multi = (rcfg.get("delivery") or {}).get("layout") == "rda"       # 多态交付：同一场景的各态放同一个交付根，页面登记成同一个仿真（region_deliver.py）
    jid = job_id(a.region, tag, "L1" if multi else state)
    key = token               # status.json 的键
    problems = []
    try:
        R = RM.Region(a.region)
        lay = tomllib.loads((ROOT / "config" / "servers.toml").read_text(encoding="utf-8")).get("nas", {})
        # ---- 建模
        st.update(key, stage="prepare", stage_pct=0.0, message="读区域数据与规则")
        st.update(key, stage="build", stage_pct=0.1, message="建模：管网、产流、二维输入")
        S = RM.build_scenario(R, tag, work, a.threads, log, state=state, grid_m=float(a.grid))
        name, sim_h = S["name"], S["sim_h"]
        unm = [u for u in (S["reg"].get("facilities") or {}).get("unmodeled") or [] if u.get("stage") in ("Temporary", "Planned")]
        if unm:      # 用户 2026-10-31：缺设备参数的设施「不生效」但照常跑，页面上标明未建模（设施图层的「建模状态」、README、params、outputs）
            log(f"  ⚠ 这些设施数据缺参数，没有放进模型（页面上标「未建模」）：{[u['item'] for u in unm]}")
        st.update(key, stage="build", stage_pct=1.0, message=f"建模完成：井 {S['reg']['n_junctions']}、管 {S['reg']['n_conduits']}", sim_h_total=sim_h)
        # ---- 计算
        tg = f"{name}_{state}"
        c2d = work / "c2d"
        end_h = min(sim_h, a.max_sim_h) if a.max_sim_h else sim_h
        # 续跑只认「用同一份输入算出来的」二维结果：算完时把「一维 inp（去掉线程数那行）+ 地形」的指纹存在 <态>.run.fp；已有结果没有指纹（旧测试 / 旧数据算的）
        # 或指纹对不上（数据 / 规则 / 管网 / 地形变了），就拒绝续跑——否则旧结果会配着新数据的参数报告交付。
        # 地形必须进指纹：inp 里没有地形，5 m 和 50 cm 的 DTM 算出来的 inp 一模一样（2026-10-31 服务器 1 用 50 cm、服务器 2/3 用 5 m 的事故）。
        import hashlib
        import rasterio
        with rasterio.open(R.dtm_file()) as _ds:
            dtm_res = float(_ds.res[0])
        dtm_src = os.environ.get("FLOOD_DTM", "").strip() or "包里的 dtm_5m.tif"
        inp_txt = re.sub(r"(?m)^THREADS.*\n", "", Path(S["inp"]).read_text(encoding="utf-8"))
        inp_txt = re.sub(r"(?m)^;; \S+ · (?:L1|L2|L2planned) · .*\n", "", inp_txt)      # 1104 批：去掉标题注释行（只有态名不同），这样新增设施全部没建模的态与 L1 指纹相同，才能复制 L1 结果
        fp_inp = hashlib.sha256(inp_txt.encode()).hexdigest()[:16]
        grid_mean = R.dem_sampling == "grid_mean"      # M6：地形按网格尺寸取像元平均（RDA2）；缺省 = 形心单点（RDA，F 系列）
        fp = hashlib.sha256((inp_txt + f"\n#DTM {dtm_src} {dtm_res:g}" + ("\n#DEM grid_mean" if grid_mean else "")).encode()).hexdigest()[:16]
        fpf = c2d / f"{tg}.run.fp"
        legacy = c2d / f"{tg}.inp.sha"      # 旧版保护只记了 inp 的指纹（不含地形）：只有地形是包里的 5 m 时才认
        if (c2d / f"{tg}_2d.npz").is_file() and (c2d / f"twoway_{tg}.json").is_file():
            old = fpf.read_text().strip() if fpf.is_file() else None
            if old is None and legacy.is_file() and not os.environ.get("FLOOD_DTM", "").strip() and legacy.read_text().strip() == fp_inp:
                old = fp
            if old != fp:
                raise RuntimeError(f"已有的二维结果不是用当前输入 / 地形（{dtm_src}，{dtm_res:g} m）算的（{'没有输入指纹，是旧测试 / 旧数据留下的' if old is None else '输入或地形已变：' + old + ' → ' + fp}），不能当续跑用。"
                                   f"先 scripts/archive-stale.sh {a.region} {key}（挪开，不删）或 scripts/cleanup-test.sh {a.region} --yes 再跑（已有结果：{c2d / (tg + '_2d.npz')}）")
            log("  二维结果已存在且输入 / 地形指纹一致，跳过计算")
        elif state != "L1" and _copy_from_l1(c2d, name, state, fp, st, key, tag, end_h, log):
            fpf.write_text(fp + "\n")      # 已从 L1 复制（输入指纹与 L1 相同，新增设施全部没建模），不重算
        else:
            st.update(key, stage="compute", stage_pct=0.0, sim_h_done=0.0, sim_h_total=end_h, message="一二维耦合计算")
            env = {**os.environ, "OMP_NUM_THREADS": str(a.threads), "C2D_DEM_RAW": "0" if grid_mean else "1", "C2D_SAVE_UV": "1"}
            cmd = [sys.executable, "-u", str(HERE / "couple2way_v2.py"), name, state, str(a.grid), f"{end_h:g}", str(MR.get("exchange.couple_dt_s", R.rules))]
            log("  $ " + " ".join(cmd))
            p = subprocess.Popen(cmd, cwd=str(c2d), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            tail, last = [], 0.0
            for line in p.stdout:
                log(line.rstrip())
                tail = (tail + [line.rstrip()])[-15:]
                m = re.search(r"t=\s*([\d.]+)h", line)
                if m and time.time() - last > 5:
                    t = float(m.group(1)); last = time.time()
                    st.update(key, stage="compute", stage_pct=min(t / end_h, 1.0), sim_h_done=t, sim_h_total=end_h, message=line.strip()[:90])
            if p.wait() != 0 or not (c2d / f"{tg}_2d.npz").is_file():
                raise RuntimeError(f"耦合计算失败（退出码 {p.returncode}）：" + " | ".join(tail[-4:]))
            fpf.write_text(fp + "\n")
            log(f"  指纹 {fp}（地形 {dtm_src}，{dtm_res:g} m）")
        # ---- 后处理与导出
        import numpy as np
        from pyproj import Transformer
        z = np.load(c2d / f"{tg}_2d.npz")
        tw = json.loads((c2d / f"twoway_{tg}.json").read_text(encoding="utf-8"))
        host = a.host
        D = S["domain"]
        title = R.cfg["region"]["title"]
        meta = {"job_id": jid, "name": name, "tag": tag, "state": state, "grid_m": float(a.grid), "region": a.region.lower(), "title": title, "rain_mm": S["rain_mm"], "dur_h": S["dur_h"], "sim_h": end_h,
                "domain_km2": round(D.area / 1e6, 1), "res_m": float(a.grid)}
        st.update(key, stage="post", stage_pct=0.0, message="统计")
        stats = RP.stats(z, tw, c2d / f"{tg}_2way.rpt", end_h, tw.get("wall_s"))
        tr = Transformer.from_crs(32640, 4326, always_xy=True)
        D_wgs = [[round(la, 6), round(lo, 6)] for lo, la in (tr.transform(x, y) for x, y in D.exterior.coords)]
        ddir = nas / lay.get("deliveries", "deliveries") / jid
        fdir = nas / lay.get("result", "result") / host / "runtime" / "console_data" / (f"flood2d_ad_{a.region.lower()}_m{('%g' % S['rain_mm']).replace('.', 'p')}h{int(S['dur_h'])}g{int(a.grid)}"
                                                                                       + ("" if (state == "L1" or multi) else f"_{state}"))
        sdir = nas / lay.get("shp_dat", "shp_dat") / jid
        # params.json（页面「推演参数」）
        st.update(key, stage="post", stage_pct=0.3, message="参数记录 params.json")
        ddir.mkdir(parents=True, exist_ok=True)
        pdir = ddir
        if multi:      # 各态各写自己的 params（根上的 params.json 由 finish_root 在锁里放 L1 的）
            pdir = rundir / f"params_{tag}_{state}"
            pdir.mkdir(parents=True, exist_ok=True)
        pj = RPR.write_params(pdir, jid, state, S["reg"], f"inputs_{tag}_{state}.json")
        # 逐帧图
        st.update(key, stage="post", stage_pct=0.5, message="逐帧图（flood-viewer）")
        try:
            RP.frames(z, tw, stats, D_wgs, fdir, meta, float(a.grid), log, state=state)
        except Exception as e:  # noqa: BLE001
            problems.append(f"逐帧图：{type(e).__name__}: {e}"); log("  ✗ 逐帧图失败：", traceback.format_exc()[-500:])
        # 交付目录
        st.update(key, stage="export", stage_pct=0.0, message="交付目录")
        try:
            if multi:
                sww = RP.ensure_sww(name, work, state, int(a.grid))
                with RDL.locked(ddir):
                    RDL.deliver_state(ddir, R, name, tag, work, state, meta, S["reg"], sww, D, log)
                    RDL.write_common(ddir, R, D, title, tag, log)
            else:
                RP.delivery(ddir, name, tag, work, stats, meta, S["reg"], pj, log, state=state, domain=D, region=a.region)
                PG.write_ponding_ge(ddir, {state: c2d / f"{tg}_2d.npz"})
        except Exception as e:  # noqa: BLE001
            problems.append(f"交付目录：{type(e).__name__}: {e}"); log("  ✗ 交付目录失败：", traceback.format_exc()[-500:])
        # 成果位置记录（页面「成果数据位置」；入库时带上）
        try:
            rel = lambda p: str(Path(p).relative_to(nas))  # noqa: E731
            il = R.rules["exchange"]["inlet"]
            sdn = RP.STATE_DIRS.get(state, RP.STATE_DIR)
            n_tri = int(len(z["xy"]))
            def _outputs_call(root, m, paths, files, extra):
                if multi:
                    with RDL.locked(root):
                        RDL.finish_root(root, m, S["reg"], state, stats, pj, paths, {**extra, "files_state": files}, log, gpkg=R.p("network_v2.gpkg"))
                else:
                    RP.write_outputs(root, m, paths, files, extra)
            _outputs_call(ddir, {**meta, "host": host}, {
                "nas_root": "NAS 根目录（三台服务器共用同一个 NAS 目录）", "deliveries": rel(ddir), "flood2d": rel(fdir), "shp_dat": rel(sdir),
                "work_dir_on_host": f"{work}/run/{a.region}（工作目录，推演时的中间文件和日志，在跑这次推演的服务器 {host} 上）"},
                {"delivery_1d_inp": f"{sdn}/1D/{tg}.inp", "delivery_1d_rpt": f"{sdn}/1D/{tg}_1D_report.rpt",
                 "delivery_2d_npz": f"{sdn}/2D/{tg}_2d.npz", "delivery_2d_sww": f"{sdn}/2D/{tg}_2D.sww", "hotspots": "common/hotspots.json", "hotspots_all": "common/hotspots_all.json", "pilot": f"common/model_inputs/pilot_{tg}.json", "dem": f"common/model_inputs/dem_{name}.tif",
                 "surf": f"common/model_inputs/surf_{name}.tif", "params": "params.json", "ponding_ge": "ponding_ge.json", "inputs": f"common/inputs_{tag}_{state}.json",
                 "grid_marker": f"GRID_{float(a.grid):g}m.txt", "shp": f"{state}/shp", "dat": f"{state}/dat"},
                {"rules": {"series": R.rules["meta"]["series"], "version": R.rules["meta"]["version"]},
                 "grid": f"{float(a.grid):g} m（非结构三角网名义精度）", "facilities": (S["reg"].get("facilities") or {}).get("facilities"),
                 "facility_stages": (S["reg"].get("facilities") or {}).get("stages"), "unmodeled": (S["reg"].get("facilities") or {}).get("unmodeled"),
                 "terrain": f"DTM {dtm_res:g} m（" + (f"按网格尺寸 {float(a.grid):g} m 取像元平均后取形心所在格" if grid_mean else "原值，不重采样，取形心所在像元") + f"；{dtm_src}），非结构三角网 {n_tri:,} 个单元（名义 {a.grid} m），建筑挖洞 {S['reg']['building_holes']['n_holes']} 个，深坑填平 {len(S['reg'].get('dem_fills', []))} 个",
                 "rain_shape": S["reg"]["runoff"]["shape"],
                 "rain_shape_label": f"client Mussafah {int(S['dur_h'])} h design storm shape (10-yr + CC), scaled to {S['rain_mm']:g} mm",
                 "spatial_pattern": "Catchment runoff (SCS CN + unit hydrograph) injected at nodes; no direct rain on 2D", "outfall_level_m": float(MR.get("outfalls.sea_level_m", R.rules)),
                 "n_cells": n_tri, "n_junctions": S["reg"]["n_junctions"], "n_conduits": S["reg"]["n_conduits"]})
        except Exception as e:  # noqa: BLE001
            problems.append(f"成果位置记录：{type(e).__name__}: {e}"); log("  ✗ 成果位置记录失败：", traceback.format_exc()[-400:])
        # sww → shp + dat
        st.update(key, stage="export", stage_pct=0.4, message="npz → sww → shp + dat")
        try:
            RP.sww_shp_dat(name, work, sdir, jid, log=log, state=state, grid=int(a.grid))
        except Exception as e:  # noqa: BLE001
            problems.append(f"shp+dat：{type(e).__name__}: {e}"); log("  ✗ shp+dat 失败：", str(e)[-500:])
        msg = "完成" if not problems else "完成，但有步骤失败：" + "；".join(problems)[:200]
        st.finish(key, ok=True, message=msg)
        log("  " + msg)
        return {"tag": token, "state": state, "job_id": jid, "ok": True, "problems": problems}
    except Exception as e:  # noqa: BLE001
        log("  ✗ 失败：", traceback.format_exc()[-1500:])
        st.finish(key, ok=False, message=f"{type(e).__name__}: {e}"[:200])
        return {"tag": token, "state": state, "job_id": jid, "ok": False, "problems": [f"{type(e).__name__}: {e}"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--state", default="L1")
    ap.add_argument("--host", default=os.uname().nodename)
    ap.add_argument("--status", default=None)
    ap.add_argument("--work", default=os.environ.get("SWBRAIN_SWMM_WORK", "/work"))
    ap.add_argument("--nas", default="/nas")
    ap.add_argument("--grid", type=int, default=15)
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--max-sim-h", type=float, default=0.0, help="只用于冒烟测试：只算前 H 小时")
    ap.add_argument("--allow-unmodeled", action="store_true", help="L2 / L2planned 里有缺参数建不了的设施时也照跑（成果里写明未建模）")
    a = ap.parse_args()
    if a.state not in STATES:
        print(f"不认识的态 {a.state}", file=sys.stderr)
        return 2
    a.threads = int(os.environ.get("OMP_NUM_THREADS", "5"))
    work = Path(a.work)
    (work / "run" / a.region).mkdir(parents=True, exist_ok=True)
    a.status = a.status or str(work / "run" / a.region / "status.json")
    tags = [t.strip() for t in a.scenarios.split(",") if t.strip()]
    pairs = [split_token(t, a.state) for t in tags]
    multi = (tomllib.loads((ROOT / "data" / "regions" / a.region / "region.toml").read_text(encoding="utf-8")).get("delivery") or {}).get("layout") == "rda"
    jid_of = lambda g, s_: job_id(a.region, g, "L1" if multi else s_)  # noqa: E731
    if len(set(pairs)) != len(pairs):
        print("场景 + 态有重复", file=sys.stderr)
        return 2
    st = Status(a.status)
    st.init(a.region, tags, host=a.host, job_ids={t: jid_of(*split_token(t, a.state)) for t in tags})
    cfg = tomllib.loads((ROOT / "config" / "servers.toml").read_text(encoding="utf-8")).get("defaults", {})
    slots = a.jobs or max(1, ((os.cpu_count() or 8) - int(cfg.get("reserve_cores", 4))) // int(cfg.get("threads_per_run", 5)))
    print(f"区域 {a.region}，场景 {tags}，同时跑 {min(slots, len(tags))} 个（每个 {a.threads} 线程），主机 {a.host}", flush=True)
    st.set_state("running", f"{len(tags)} 个场景，并行 {min(slots, len(tags))}")
    results = []
    d = {k: v for k, v in vars(a).items()}
    with ProcessPoolExecutor(max_workers=min(slots, len(tags))) as ex:
        futs = {ex.submit(run_one, d, t): t for t in tags}
        for f in as_completed(futs):
            results.append(f.result())
    bad = [r for r in results if not r["ok"]]
    st.set_state("failed" if bad else "done", f"{len(results) - len(bad)} 个完成，{len(bad)} 个失败" if bad else f"{len(results)} 个场景全部完成")
    print(json.dumps(results, ensure_ascii=False), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
