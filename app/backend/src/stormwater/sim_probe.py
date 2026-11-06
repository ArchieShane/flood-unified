"""内网自检：只读扫描一个交付目录或 NAS 路径，打印一份纯文本摘要（不输出任何数据本体）。

用在拿不到数据出内网的场合：在能读 NAS 的机器上跑，把摘要抄出来，用来核对格式。

    python -m stormwater.sim_probe <交付目录 | NAS 主机目录> [--depth 4] [--out 摘要.txt]
    # NAS 主机目录例：/media/pop-fs/flood_data/result/<主机名>（下有 work/c2d、runtime/out、runtime/console_data）
    # 没装本包时也可直接：python sim_probe.py <路径>（「抽取代码识别」一行会写不可用，其余照常）

摘要的内容：
  1. 目录清单（层数受限，大目录只给「按扩展名的文件数与体积」）
  2. 每个 npz：各键的 shape / dtype、帧数、t_h 首末与步长、压缩与否（只读 zip 里 .npy 的头，不解大数组）
  3. 每份耦合运行记录：res_m、end_h、tag 等标量与键清单（不列 series 内容）
  4. 每个疑似交付目录：目录名解析、各态目录、设施GIS 位置、统计值文件、与抽取代码要求逐条对照、能否被识别
  5. 已知口径差异（系统储水能力 / 溢水点）

只读：不写、不改、不连数据库；只依赖 numpy。读不到 / 读坏的文件记一行原因，不中断。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

MAX_VISIT = 50_000              # 一次最多走过多少个文件（NAS 上别扫成灾）
MAX_JSON_BYTES = 20 * 2**20     # 运行记录超过这个大小不读，只报大小
DELIVERY_RX = re.compile(r"^(?P<region>.+?)_(?P<depth>\d+(?:\.\d+)?)mm(?P<dur>\d+(?:\.\d+)?)h"
                         r"(?:_G(?P<g>\d+(?:\.\d+)?)|_(?P<m>\d+(?:\.\d+)?)m)?$")
#: 抽取代码（sim_c1.extract_state）要的 npz 键
NPZ_REQUIRED = ("xy", "area", "elev", "hmax", "frames", "t_h")
NPZ_OPTIONAL = ("rain_mask", "uh", "vh")
STATE_PREFIX = {"L1": "L1_", "L2": "L2_", "L2+planned": "L2+planned", "L2+suggested": "L2+suggested"}
SIBLING_FACILITY = {"L2+planned": "planned", "L2+suggested": "sugg"}

KNOWN_DIFFS = (
    "系统储水能力：交付里没有 analysis/state_stats.csv 时，系统退回按 .inp 自算，数偏小（不含检查井），"
    "与客户口径不同。",
    "页面上的「溢水点」是系统按自己的口径（docs/analysis_ponding 七步）从成果重新识别的，不等于服务器统计里的热点。",
)


def _mb(n: float) -> str:
    return f"{n / 2**20:,.1f} MB" if n < 2**30 else f"{n / 2**30:,.2f} GB"


def _real(p: Path) -> bool:
    return not p.name.startswith("._")      # macOS 打包带的伴生文件，抽取代码也忽略


# ---------------------------------------------------------------------------
# 目录扫描
# ---------------------------------------------------------------------------


class Scan:
    def __init__(self, root: Path, depth: int):
        self.root, self.depth = root, depth
        self.files: list[tuple[Path, int]] = []       # (路径, 字节)
        self.dirs: list[Path] = []
        self.truncated = False
        self.errors: list[str] = []
        self._walk(root, 0)

    def _walk(self, d: Path, lvl: int) -> None:
        if len(self.files) >= MAX_VISIT:
            self.truncated = True
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError as e:
            self.errors.append(f"无法列目录 {d}: {e.__class__.__name__}")
            return
        for p in entries:
            try:
                if p.is_dir():
                    self.dirs.append(p)
                    if lvl < self.depth:
                        self._walk(p, lvl + 1)
                elif _real(p):
                    self.files.append((p, p.stat().st_size))
            except OSError as e:
                self.errors.append(f"无法读取 {p}: {e.__class__.__name__}")


def _tree(scan: Scan, out: list[str], show_depth: int = 3, max_children: int = 40) -> None:
    by_dir: dict[Path, list[tuple[Path, int]]] = defaultdict(list)
    for p, sz in scan.files:
        by_dir[p.parent].append((p, sz))
    kids: dict[Path, list[Path]] = defaultdict(list)
    for d in scan.dirs:
        kids[d.parent].append(d)

    def total(d: Path) -> tuple[int, int]:
        n = len(by_dir.get(d, ()))
        s = sum(sz for _, sz in by_dir.get(d, ()))
        for k in kids.get(d, ()):
            a, b = total(k)
            n, s = n + a, s + b
        return n, s

    def show(d: Path, lvl: int) -> None:
        pad = "  " * lvl
        fs = by_dir.get(d, [])
        if len(fs) <= 12:
            for p, sz in fs:
                out.append(f"{pad}  {p.name}  {_mb(sz)}")
        else:
            exts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for p, sz in fs:
                e = p.suffix.lower() or "(无扩展名)"
                exts[e][0] += 1
                exts[e][1] += sz
            out.append(f"{pad}  [{len(fs)} 个文件] " + "；".join(f"{e}×{c} ({_mb(s)})" for e, (c, s) in sorted(exts.items())))
        sub = kids.get(d, [])
        for k in sub[:max_children]:
            n, s = total(k)
            out.append(f"{pad}  {k.name}/  ({n} 个文件, {_mb(s)})")
            if lvl + 1 < show_depth:
                show(k, lvl + 1)
        if len(sub) > max_children:
            out.append(f"{pad}  … 还有 {len(sub) - max_children} 个子目录")

    n, s = total(scan.root)
    out.append(f"{scan.root}/  ({n} 个文件, {_mb(s)})")
    show(scan.root, 0)


# ---------------------------------------------------------------------------
# npz / 运行记录
# ---------------------------------------------------------------------------


def npz_summary(path: Path) -> dict[str, Any]:
    """只读 zip 目录和各 .npy 的头；t_h 很小，读它取首末与步长。不解任何大数组。"""
    info: dict[str, Any] = {"arrays": {}, "error": None}
    try:
        with zipfile.ZipFile(path) as zf:
            for zi in zf.infolist():
                if not zi.filename.endswith(".npy"):
                    continue
                key = zi.filename[:-4]
                with zf.open(zi) as fh:
                    ver = np.lib.format.read_magic(fh)
                    if ver == (1, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_1_0(fh)
                    elif ver == (2, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_2_0(fh)
                    else:
                        info["arrays"][key] = {"shape": None, "dtype": f"npy 版本 {ver} 不认", "fortran": None}
                        continue
                    a: dict[str, Any] = {"shape": tuple(int(x) for x in shape), "dtype": str(dtype),
                                         "fortran": bool(fortran), "stored": zi.compress_type == zipfile.ZIP_STORED,
                                         "bytes": zi.file_size}
                    cnt = int(np.prod(shape)) if shape else 1
                    if key == "t_h" and 0 < cnt <= 100_000 and not dtype.hasobject:
                        t = np.frombuffer(fh.read(cnt * dtype.itemsize), dtype).astype(float)
                        a["t_first_last_h"] = (float(t[0]), float(t[-1]))
                        if t.size > 1:
                            d = np.diff(t) * 60
                            a["t_step_min"] = (float(d.min()), float(d.max()))
                    info["arrays"][key] = a
    except (OSError, zipfile.BadZipFile, ValueError) as e:
        info["error"] = f"{e.__class__.__name__}: {e}"
    return info


def npz_lines(info: dict[str, Any], pad: str = "    ") -> list[str]:
    if info["error"]:
        return [f"{pad}读不了：{info['error']}"]
    out = []
    for k, a in info["arrays"].items():
        extra = ""
        if "t_first_last_h" in a:
            f, l = a["t_first_last_h"]
            extra = f"  t {f:g} → {l:g} h"
            if "t_step_min" in a:
                lo, hi = a["t_step_min"]
                extra += f"，步长 {lo:g}" + (f"–{hi:g}" if abs(hi - lo) > 1e-6 else "") + " min"
        zip_note = f" {'未压缩' if a['stored'] else '已压缩'} {_mb(a['bytes'])}" if "stored" in a else ""
        flag = "" if k in NPZ_REQUIRED + NPZ_OPTIONAL else "  （抽取代码不用）"
        out.append(f"{pad}{k}: shape={a['shape']} dtype={a['dtype']}{zip_note}{extra}{flag}")
    return out


def record_summary(path: Path) -> dict[str, Any]:
    r: dict[str, Any] = {"error": None}
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            r["error"] = f"文件 {_mb(path.stat().st_size)}，超过 {_mb(MAX_JSON_BYTES)} 不读"
            return r
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        r["error"] = f"{e.__class__.__name__}: {e}"
        return r
    if not isinstance(d, dict):
        r["error"] = f"顶层不是对象（{type(d).__name__}）"
        return r
    r["scalars"] = {k: v for k, v in d.items() if isinstance(v, (int, float, str, bool)) and len(str(v)) < 60}
    r["structs"] = {k: (f"list[{len(v)}]" if isinstance(v, list) else f"dict[{len(v)}]")
                    for k, v in d.items() if isinstance(v, (list, dict))}
    r["res_m"], r["end_h"] = d.get("res_m"), d.get("end_h")
    r["has_acc"] = isinstance(d.get("acc_m3"), dict) or "acc_1to2_m3" in d
    ser = d.get("series")
    r["series_len"] = len(ser) if isinstance(ser, list) else None
    r["series_keys"] = sorted(ser[0]) if isinstance(ser, list) and ser and isinstance(ser[0], dict) else None
    return r


def record_lines(r: dict[str, Any], pad: str = "    ") -> list[str]:
    if r["error"]:
        return [f"{pad}读不了：{r['error']}"]
    keep = ("tag", "dt_c", "n_tri", "n_nodes", "n_coupled", "start_h", "max_wet_t_h")
    out = [f"{pad}res_m={r['res_m']}  end_h={r['end_h']}",
           pad + "标量: " + "  ".join(f"{k}={v}" for k, v in r["scalars"].items() if k in keep),
           pad + "结构: " + "  ".join(f"{k}={v}" for k, v in r["structs"].items())]
    if r["series_keys"]:
        out.append(f"{pad}series[{r['series_len']}] 键: {r['series_keys']}")
    return out


# ---------------------------------------------------------------------------
# 交付目录识别
# ---------------------------------------------------------------------------


def _glob1(d: Path, pat: str) -> tuple[Path | None, str]:
    hits = sorted(p for p in d.glob(pat) if _real(p)) if d.is_dir() else []
    if len(hits) == 1:
        return hits[0], "ok"
    return None, ("缺" if not hits else f"有 {len(hits)} 个，需恰好一个")


def _state_dirs(root: Path) -> dict[str, tuple[Path | None, str]]:
    out = {}
    for st, pre in STATE_PREFIX.items():
        hits = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith(pre) and _real(p))
        out[st] = (hits[0], "ok") if len(hits) == 1 else (None, "无" if not hits else f"有 {len(hits)} 个：{[h.name for h in hits]}")
    return out


def looks_like_delivery(d: Path) -> bool:
    if not DELIVERY_RX.match(d.name):
        return False
    try:
        return any(p.is_dir() and re.match(r"L\d", p.name) for p in d.iterdir())
    except OSError:
        return False


C2D_KEYS = {"L1": "L1", "L2": "L2", "L2+planned": "L2P", "L2+suggested": "L2S3"}


def find_c2d(root: Path) -> Path | None:
    """交付往上找 `<主机>/work/c2d`（NAS：result/<主机>/runtime/out/交付/<交付> 与 result/<主机>/work/c2d）。"""
    for p in root.parents:
        if (p / "work" / "c2d").is_dir():
            return p / "work" / "c2d"
    return None


def _locate_npz(root: Path, s: str, d2: Path, rec_tag: str | None, inp: Path | None, out: list[str]) -> tuple[Path | None, str]:
    """交付里的 npz，没有就去 work/c2d 找（与 sim_c1.state_files 同一套候选：运行记录 tag → .inp 文件名 → *_<态记号>_2d.npz 唯一匹配）。"""
    here = sorted(p for p in d2.glob("*_二维水深帧与最大水深.npz") if _real(p))
    if not here and d2.name == "二维":
        here = sorted(p for p in d2.glob("*_2d.npz") if _real(p))
    if len(here) == 1:
        return here[0], "随交付"
    if len(here) > 1:
        return None, f"*_二维水深帧与最大水深.npz 有 {len(here)} 个，需恰好一个"
    c2d = find_c2d(root)
    if c2d is None:
        return None, "交付里没有 npz，且往上找不到 work/c2d（导入时用 --c2d 指到存放 *_2d.npz 的目录）"
    for stem in [t for t in (rec_tag, inp.stem if inp else None) if t]:
        if (c2d / f"{stem}_2d.npz").is_file():
            return c2d / f"{stem}_2d.npz", f"work/c2d（按 {'运行记录 tag' if stem == rec_tag else '.inp 文件名'} = {stem}）"
    hits = sorted(p for p in c2d.glob(f"*_{C2D_KEYS[s]}_2d.npz") if _real(p))
    if len(hits) == 1:
        return hits[0], f"work/c2d（按 *_{C2D_KEYS[s]}_2d.npz 唯一匹配）"
    names = sorted(p.name for p in c2d.glob("*_2d.npz") if _real(p))
    out.append(f"    work/c2d = {c2d}（共 {len(names)} 个 *_2d.npz，前 12 个：{names[:12]}）")
    return None, f"work/c2d 里找不到 {s} 的 npz（试过 tag={rec_tag}、inp={inp.stem if inp else None}；*_{C2D_KEYS[s]}_2d.npz 匹配到 {len(hits)} 个）"


def _probe_state(root: Path, s: str, sd: Path, out: list[str], res_seen: set[float]) -> bool:
    out.append(f"  ── 态 {s}  目录 {sd.name}")
    probs: list[str] = []
    npz_end_h: float | None = None
    rec_end_h: float | None = None
    if (sd / "一二维").is_dir():
        d2, d1, layout = sd / "一二维", sd / "一二维" / "耦合的一维", "一二维（C1 形态）"
    elif (sd / "二维").is_dir() and (sd / "一维").is_dir():
        d2, d1, layout = sd / "二维", sd / "一维", "一维 + 二维（模型版形态，npz 在 work/c2d）"
    else:
        out.append("    ✗ 不可抽取：既没有「一二维/」也没有「一维/ + 二维/」")
        return False
    out.append(f"    形态：{layout}")
    rec, why = _glob1(d2, "*_耦合运行记录.json")
    r: dict[str, Any] = {"error": why}
    if rec is None:
        probs.append(f"*_耦合运行记录.json {why}")
    else:
        r = record_summary(rec)
        out.append(f"    运行记录 {rec.name}")
        out.extend(record_lines(r, "      "))
        if r["error"]:
            probs.append(f"运行记录读不了：{r['error']}")
        else:
            rec_end_h = float(r["end_h"]) if r["end_h"] else None
            if r["res_m"]:
                res_seen.add(float(r["res_m"]))
            else:
                out.append("    ⚠ 运行记录没有 res_m（抽取代码退回目录名里标的网格，再退回 15 m）")
            if not r["has_acc"]:
                probs.append("运行记录没有 acc_m3 / acc_1to2_m3")
            if not r["series_keys"] or not {"t_h", "acc_1to2"} <= set(r["series_keys"]):
                probs.append("运行记录 series 缺 t_h / acc_1to2")
            rain = (json.loads(rec.read_text(encoding="utf-8")).get("acc_m3") or {}).get("rain")
            out.append(f"    地表直接受雨量 acc_m3.rain = {rain}（{'直接受雨' if rain and rain > 0 else '不直接受雨，来水全是井口溢流'}）")
    inp, why_i = _glob1(d1, "*.inp")
    rpt, why_r = _glob1(d1, "*.rpt")
    for f, w, label in ((rpt, why_r, ".rpt"), (inp, why_i, ".inp")):
        out.append(f"    {label}: {f.name + '  ' + _mb(f.stat().st_size) if f else w}")
        if f is None:
            probs.append(f"{d1.name}/ 下 {label} {w}")
    npz, how = _locate_npz(root, s, d2, (r.get("scalars") or {}).get("tag") if not r["error"] else None,
                           inp if layout.startswith("一维") else None, out)
    if npz is None:
        probs.append(how)
    else:
        info = npz_summary(npz)
        out.append(f"    npz {npz}（{how}）")
        out.extend(npz_lines(info, "      "))
        arrs = info["arrays"]
        probs += [f"npz 缺键 {k}" for k in NPZ_REQUIRED if k not in arrs] if not info["error"] else [f"npz 读不了：{info['error']}"]
        if all(k in arrs for k in ("xy", "frames", "t_h")) and arrs["frames"]["shape"] and arrs["xy"]["shape"]:
            fs, n_tri = arrs["frames"]["shape"], arrs["xy"]["shape"][0]
            if len(fs) != 2 or fs[1] != n_tri or fs[0] != arrs["t_h"]["shape"][0]:
                probs.append(f"frames 形状 {fs} 与 xy({n_tri}) / t_h({arrs['t_h']['shape']}) 对不上")
            if arrs["frames"].get("fortran"):
                probs.append("frames 是 Fortran 序（分块读退回整块读，吃内存）")
            has_speed = "uh" in arrs and "vh" in arrs
            out.append(f"    帧数 {fs[0]}｜三角单元 {n_tri:,}｜流速 uh/vh {'有' if has_speed else '无'}"
                       f"｜分块读峰值内存估算 ≈ {_mb(n_tri * 90 + 3 * 192 * 2**20)}"
                       f"（整块读要 {_mb(fs[0] * n_tri * 2 * (3 if has_speed else 1))}）")
            npz_end_h = (arrs["t_h"].get("t_first_last_h") or (None, None))[1]
    if npz_end_h is not None and rec_end_h is not None and abs(npz_end_h - rec_end_h) > 0.02:
        out.append(f"    ⚠ npz 的 t_h 末值 {npz_end_h:g} h ≠ 运行记录 end_h {rec_end_h:g} h")
    fac, sib = sd / "设施GIS", SIBLING_FACILITY.get(s)
    sibd = root.parent / sib / "设施GIS" if sib else None
    out.append("    设施GIS: " + (f"态目录内（{len(list(fac.glob('*.geojson')))} 个 geojson）" if fac.is_dir()
                                  else f"旁边的 {sib}/设施GIS（旧样例位置）" if sibd and sibd.is_dir() else "无"))
    out.append(f"    ✗ 不可抽取：{'；'.join(probs)}" if probs else "    ✓ 满足抽取代码的文件要求")
    return not probs


KIND_LABEL = {"sww": "旧 .sww 交付（sim_sww）", "city": "全市一二维（sim_city）", "c1": "一二维 npz 交付（sim_c1）"}


def _kind(root: Path) -> str:
    """与 sim_import.detect 同一判据（自检脚本要能脱离本包单独跑，所以这里留一份）。"""
    if DELIVERY_RX.match(root.name).group("region").startswith("全市"):
        return "city"
    for sd in sorted(p for p in root.iterdir() if p.is_dir() and re.match(r"L\d", p.name)):
        d2 = sd / "二维"
        if d2.is_dir() and (d2 / "耦合的一维").is_dir() and any(_real(p) for p in d2.glob("*_二维水深时序.sww")):
            return "sww"
    return "c1"


def _probe_sww(root: Path, out: list[str]) -> None:
    """旧 .sww 交付（如 Mussafah_00_61mm2h）：每个有文件的态要 二维/*_二维水深时序.sww、*_耦合运行记录.json、耦合的一维/*.rpt|.inp。
    L2 若是「≡ L1」只放说明、没有自己的文件，抽取时硬链 L1，这里不算缺。"""
    present = [p for p in sorted(root.iterdir()) if p.is_dir() and re.match(r"L\d", p.name)]
    out.append(f"  各态目录：{[p.name for p in present]}")
    ok, bad = 0, 0
    for sd in present:
        d2 = sd / "二维"
        if not d2.is_dir():
            out.append(f"  ── {sd.name}：无「二维/」（{'≡ L1，只放说明，抽取时硬链 L1' if sd.name.startswith('L2_') else '不可抽取'}）")
            ok += sd.name.startswith("L2_")
            bad += not sd.name.startswith("L2_")
            continue
        probs = []
        for pat, label in (("*_二维水深时序.sww", ".sww"), ("*_耦合运行记录.json", "耦合运行记录"),
                           ("耦合的一维/*.rpt", ".rpt"), ("耦合的一维/*.inp", ".inp")):
            f, why = _glob1(d2, pat)
            out.append(f"    {sd.name} {label}: {f.name + '  ' + _mb(f.stat().st_size) if f else why}")
            if f is None:
                probs.append(f"{label} {why}")
        rec, _ = _glob1(d2, "*_耦合运行记录.json")
        if rec is not None:
            r = record_summary(rec)
            out.extend(record_lines(r, "      "))
            if not r["error"] and r["res_m"] is None:
                out.append("      ⚠ 运行记录没有 res_m（sim_sww 取 15 m）")
        out.append(f"  ── {sd.name}：" + (f"✗ {'；'.join(probs)}" if probs else "✓ 满足 sim_sww 的文件要求"))
        ok += not probs
        bad += bool(probs)
    stats = [p.name for pat in ("对比_缺口分析/五态统计值.csv", "对比_缺口分析/四态统计值.csv") for p in root.glob(pat)]
    out.append(f"  交付统计值（系统储水能力按它取）：{stats or '无 → 退回按 .inp 自算'}")
    out += [f"  结论：{'可抽取' if bad == 0 and ok else '不可抽取（见上面 ✗）'}（sim_sww，登记用 sim_import 自动选）", ""]


def probe_delivery(root: Path, out: list[str]) -> None:
    m = DELIVERY_RX.match(root.name)
    region, depth, dur = m.group("region"), float(m.group("depth")), float(m.group("dur"))
    named = m.group("g") or m.group("m")
    out += [f"■ 交付目录 {root}",
            f"  名称解析：片区={region}  雨={depth:g} mm / {dur:g} h  目录名标的网格={named or '无'} m"]
    kind = _kind(root)
    out.append(f"  交付形状：{KIND_LABEL[kind]}（登记命令 sim_import 按它自动选，不用自己挑）")
    if kind == "sww":
        _probe_sww(root, out)
        return
    states = _state_dirs(root)
    present = [s for s, (p, _) in states.items() if p is not None]
    out.append(f"  实有态：{present or '无'}（只读这些；缺的态不报错）")
    out += [f"  ✗ 态 {s}：{why}" for s, (p, why) in states.items() if p is None and why != "无"]
    res_seen: set[float] = set()
    ok_states = [s for s in present if _probe_state(root, s, states[s][0], out, res_seen)]
    stats = [p.relative_to(root) for pat in ("analysis/state_stats.csv", "对比_缺口分析/*统计值*.csv", "统计值/*.xlsx")
             for p in root.glob(pat) if _real(p)]
    out.append(f"  统计值文件：{[str(s) for s in stats] or '无'}"
               + ("" if any(s.name == "state_stats.csv" for s in stats) else "  → 系统储水能力将退回按 .inp 自算"))
    has_tab = any(p.name == "统计值.csv" or p.suffix == ".xlsx" for p in stats)
    ad_tab = (root / "积水点" / "积水点结果表.csv").is_file()   # AD 区域交付：积水点结果表 + 统计/汇水区汇总
    out.append(f"  积水点（hotspots.json）：统计值表 {'有' if has_tab else ('有（AD 区域交付：积水点/积水点结果表.csv + 统计/汇水区汇总.csv）' if ad_tab else '无 → 不会写 hotspots.json')}"
               f"｜坐标取台账；交付自带公共/热点.geojson {'有' if (root / '公共' / '热点.geojson').is_file() else ('有（积水点/积水点图层.geojson）' if (root / '积水点' / '积水点图层.geojson').is_file() else '无')}"
               f"｜汇水区面 {'有' if (root / '公共' / '8个热点的地表与管网汇水区.geojson').is_file() else '无（只出 200 m / 全域口径）'}")
    out.append(f"  公共/计算域.geojson: {'有' if (root / '公共' / '计算域.geojson').is_file() else '无'}"
               f"｜readme.md: {'有' if (root / 'readme.md').is_file() else '无'}"
               f"｜计算设置.md: {'有' if (root / '计算设置.md').is_file() else '无'}")
    if len(res_seen) > 1:
        out.append(f"  ✗ 各态 res_m 不一致：{sorted(res_seen)}（抽取代码会报错）")
    res = next(iter(res_seen)) if len(res_seen) == 1 else None
    key = f"{region}_{depth:g}mm{dur:g}h" + ("" if res is None or abs(res - 15.0) < 1e-6 else f"_{res:g}m")
    out.append(f"  网格 res_m={res}｜预期输出键 sim_deliveries/{key}/｜落图格网 {max(res or 15.0, 15.0):g} m")
    try:
        from stormwater import sim_c1
    except ImportError as e:
        sim_c1 = None
        out.append(f"  抽取代码识别：不可用（{e.__class__.__name__}），以上逐条对照为准")
    if kind == "city":
        # 全市走 sim_city：输出目录就是交付目录名、范围 City-wide（不是 sim_c1 的按网格加后缀 / 取目录名首词）
        out.append(f"  登记口径（sim_city）：态 {present}｜输出键 {root.name}｜范围 City-wide｜落图格网 250 m")
    elif sim_c1 is not None:
        try:
            r2 = sim_c1.delivery_res(root)
            ps = sim_c1.present_states(root)
            out.append(f"  抽取代码识别：态 {ps}｜res_m={r2:g}｜输出键 {sim_c1.output_key(root, r2)}｜范围 {sim_c1.extent_of(root)}"
                       + ("" if ps == present else f"  ⚠ 与上面实有态 {present} 不一致"))
        except Exception as e:  # noqa: BLE001 —— 自检脚本，任何失败都只记一行
            out.append(f"  抽取代码识别：不通过 —— {e.__class__.__name__}: {e}")
    ok = bool(present) and len(ok_states) == len(present) and len(res_seen) <= 1
    out += [f"  结论：{'可抽取' if ok else '不可抽取（见上面 ✗）'}（{len(ok_states)}/{len(present)} 态满足）", ""]


def probe_loose(scan: Scan, out: list[str], deliveries: list[Path]) -> None:
    """不在交付目录里的零散成果（NAS 的 work/c2d、runtime/out）：npz、运行记录、.sww。"""
    def outside(p: Path) -> bool:
        return not any(d in p.parents for d in deliveries)

    npzs = [p for p, _ in scan.files if p.suffix == ".npz" and outside(p)]
    recs = [p for p, _ in scan.files if outside(p) and p.suffix == ".json"
            and (p.name.startswith("twoway_") or p.name.endswith("耦合运行记录.json"))]
    sww = [(p, sz) for p, sz in scan.files if p.suffix == ".sww" and outside(p)]
    if not (npzs or recs or sww):
        return
    out.append("■ 交付目录之外的零散成果")
    for p in npzs[:30]:
        out += [f"  npz {p}"] + npz_lines(npz_summary(p), "    ")
    out += [f"  … 还有 {len(npzs) - 30} 个 npz 未展开"] if len(npzs) > 30 else []
    for p in recs[:30]:
        out += [f"  运行记录 {p}"] + record_lines(record_summary(p), "    ")
    for p, sz in sww[:30]:
        out.append(f"  .sww {p}  {_mb(sz)}（sim_sww 只认 15 m 规则网格、每格 4 个三角形的 .sww；非结构网要有 npz 才能抽）")
    out.append("")


def probe(path: Path, depth: int = 4) -> str:
    import platform

    out = ["=== 交付自检摘要（只读；不含数据本体）===", f"路径：{path}",
           f"环境：Python {platform.python_version()}｜numpy {np.__version__}｜{platform.system()}"]
    if not path.exists():
        return "\n".join(out + [f"✗ 路径不存在或读不到：{path}"])
    scan = Scan(path, depth)
    out += ["", "■ 目录清单"]
    _tree(scan, out)
    if scan.truncated:
        out.append(f"⚠ 文件数超过 {MAX_VISIT}，只扫了一部分")
    out += [f"⚠ {e}" for e in scan.errors[:20]] + [""]
    cands = ([path] if DELIVERY_RX.match(path.name) else []) + scan.dirs
    deliveries = [d for d in dict.fromkeys(cands) if looks_like_delivery(d)]
    if not deliveries:
        out += ["■ 没找到形如「片区_雨深mm历时h」且下有 L1_/L2_… 态目录的交付目录", ""]
    for d in deliveries:
        try:
            probe_delivery(d, out)
        except Exception as e:  # noqa: BLE001
            out += [f"■ 交付目录 {d}", f"  ✗ 自检中途出错：{e.__class__.__name__}: {e}", ""]
    probe_loose(scan, out, deliveries)
    out += ["■ 已知口径差异（不是 bug）"] + [f"  - {t}" for t in KNOWN_DIFFS]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", type=Path)
    ap.add_argument("--depth", type=int, default=4, help="扫描的目录层数（默认 4）")
    ap.add_argument("--out", type=Path, help="同时写到这个文本文件（UTF-8）")
    a = ap.parse_args()
    text = probe(a.path, a.depth)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # Windows 控制台默认不是 UTF-8
    except (AttributeError, OSError):
        pass
    print(text)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
