#!/usr/bin/env python3
"""把 30 m 格网 / 艾因 250 m 的成果从 NAS 拷到另一个目录，拷贝时把路径里的中文改成英文（只改目录名、文件名，不改文件内容）。

成果位置（NAS，NAS_BASE 缺省 /media/pop-fs/flood_data）：
  <NAS_BASE>/result/<主机名>/<区域>_<雨量>mm<历时>h_30m/        30 m：积水点、统计、一维、二维(.sww、最大水深 tif)、计算设置
  <NAS_BASE>/result/<主机名>/艾因_模型版_<雨量>mm<历时>h_250m/    艾因 250 m，同上结构
  <NAS_BASE>/shp_dat/<主机名>/<同名目录>/<L1|L2>/{shp,dat}        .sww 转出的 shp + RESULTSxxx.dat
缺省拷 16 份：15 个 30 m 区域（61mm/1h）+ 艾因_模型版_61mm1h_250m。每份在 result/<主机>/<名> 或 result/<主机>/runtime/out/交付/<名> 里找，同名多份时选 .sww 最多、其次最新的；预览会打印所有候选。不碰 old_*、shp_dat、flood-aio-sim_deliveries 等。

安全：对 NAS 上的原始数据只读（只 open 读取，绝不写 / 改名 / 删除）。分两步：
  1) copy   原样（中文名不动）拷到目标目录 DEST，目标目录不能在 result / shp_dat 里面或包含它们（与 result 平级的 NAS/result-017 可以），且必须是空目录或本脚本建过的目录
  2) rename 只在 DEST（拷贝件）里把中文改英文；路径一旦不在 DEST 里就拒绝执行
用法（服务器上，python3，只用标准库）：
  python3 export_results_en.py list                          # 只列出会拷哪些目录
  python3 export_results_en.py copy /目标目录 --dry-run       # 只打印 源 -> 目标
  python3 export_results_en.py copy /目标目录                 # 第 1 步：原样拷贝并逐个校验大小（已存在且同大小的跳过，可重复执行）
  python3 export_results_en.py rename /目标目录 --dry-run     # 只打印会怎么改名
  python3 export_results_en.py rename /目标目录               # 第 2 步：在拷贝件里改英文名
选项：
  --include 正则   只要目录名匹配的（缺省 '_61mm1h_30m$|^艾因_模型版_.*_250m$'，即 30 m 的 61mm/1h 批 + 艾因全部）
                   例：--include '_96mm2h_30m$'；--include '.'（全选）
  --no-sww         不拷 .sww（体积最大）
  --nas-base 路径  NAS 根目录
映射表没覆盖的中文名不会停：用 cn<4位哈希> 兜底，原名对照写在目标目录的 rename_map.tsv。
"""
import argparse, hashlib, os, re, shutil, sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

# 长的放前面（先替换长词）。路径任意一段里出现的子串都会替换。
MAP = [
    ("艾因_模型版_", "AlAin_Model_"),
    ("L1_既有管网", "L1_existing_network"),
    ("L2_已建成调蓄", "L2_built_storage"),
    ("积水点结果表", "hotspot_results"),
    ("积水点图层", "hotspot_layer"),
    ("耦合运行记录", "coupled_run_log"),
    ("汇水区汇总", "catchment_summary"),
    ("计算设置", "calc_settings"),
    ("一维报告", "1d_report"),
    ("逐帧水深", "depth_frames"),
    ("最大水深", "max_depth"),
    ("积水点", "hotspots"),
    ("计算域", "domain"),
    ("现状泵站", "existing_pumps"),
    ("已建成塘", "built_ponds"),
    ("输入参数", "input_params"),
    ("统计值", "stats_values"),
    ("统计", "stats"),
    ("一维", "1d"),
    ("二维", "2d"),
    ("公共", "common"),
    ("对比", "comparison"),
    ("缺口分析", "gap_analysis"),
    ("差值图", "diff_map"),
    ("说明", "README"),
    ("热点", "hotspots"),
    ("交付", "delivery"),
]
CJK = re.compile(r"[　-〿㐀-鿿＀-￯]")
CJK_RUN = re.compile(r"[　-〿㐀-鿿＀-￯]+")
DEFAULT_INCLUDE = (r"^(MI-8|RD-2|YAS_OUTFALL|RB-1|Al_Riyadh_ponds|RBE1-OF1|MBZ|RD-14|RB-3|RD-A|RD-F|RD-C|Qasr_Al_Bahr-1|RD-I|RD-G)_61mm1h_30m$"
                   r"|^艾因_模型版_61mm1h_250m$")   # 15 个 30 m 区域 + 艾因 = 16 份


def tr(name):
    for a, b in MAP:
        name = name.replace(a, b)
    return name


def count_sww(p):
    n, newest = 0, 0.0
    for cur, _, files in os.walk(p):
        for f in files:
            if f.endswith(".sww"):
                n += 1
                newest = max(newest, os.path.getmtime(os.path.join(cur, f)))
    return n, newest


def dir_size(p):
    return sum(os.path.getsize(os.path.join(c, f)) for c, _, fs in os.walk(p) for f in fs)


def candidates(base, include):
    """成果目录可能在 result/<主机>/<名> 或 result/<主机>/runtime/out/交付/<名>；返回 {名: [(路径, sww数, 最新sww时间)]}。"""
    rx = re.compile(include)
    out = {}
    rd = os.path.join(base, "result")
    if not os.path.isdir(rd):
        return out
    for host in sorted(os.listdir(rd)):
        for parent in (os.path.join(rd, host), os.path.join(rd, host, "runtime", "out", "交付")):
            if not os.path.isdir(parent):
                continue
            for d in sorted(os.listdir(parent)):
                p = os.path.join(parent, d)
                if os.path.isdir(p) and rx.search(d):
                    n, t = count_sww(p)
                    out.setdefault(d, []).append((p, n, t))
    return out


def pick_dirs(base, include):
    """每个名字选一个目录：.sww 最多的，其次 .sww 最新的。[(名, 路径)]；同时打印候选供核对。"""
    out = []
    for d, cs in sorted(candidates(base, include).items()):
        cs.sort(key=lambda c: (c[1], c[2]), reverse=True)
        for i, (p, n, t) in enumerate(cs):
            print(f"  {'选用' if i == 0 else '    '} {p}   sww={n}")
        out.append((d, cs[0][0]))
    return out


MARK = ".export_results_en_staging"
MAPFILE = "rename_map.tsv"


def real(p):
    return os.path.realpath(os.path.abspath(p))


def inside(child, parent):
    c, p = real(child), real(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def src_roots(nas_base):
    return [os.path.join(nas_base, x) for x in ("result", "shp_dat")]


def bad_dest(dest, nas_base):
    """目标不能在 result / shp_dat 里面，也不能包含它们（含 NAS 根目录本身）；与它们平级（如 NAS/result-017）可以。"""
    return any(inside(dest, r) or inside(r, dest) for r in src_roots(nas_base))


def check_dest(dest, nas_base):
    """目标必须与 result / shp_dat 无包含关系；必须为空或是本脚本建过的目录。"""
    if bad_dest(dest, nas_base):
        sys.exit(f"拒绝：目标 {dest} 在 result / shp_dat 里面，或包含了它们；请选与它们平级或无关的目录（如 {nas_base}/result-017）")
    if os.path.exists(dest):
        if not os.path.isdir(dest):
            sys.exit(f"拒绝：{dest} 不是目录")
        if os.listdir(dest) and not os.path.exists(os.path.join(dest, MARK)):
            sys.exit(f"拒绝：{dest} 非空且不是本脚本建的目录，请换一个空目录")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["list", "copy", "rename"])
    ap.add_argument("dest", nargs="?")
    ap.add_argument("--include", default=DEFAULT_INCLUDE)
    ap.add_argument("--nas-base", default=os.environ.get("NAS_BASE", "/media/pop-fs/flood_data"))
    ap.add_argument("--no-sww", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-unmapped", action="store_true")
    a = ap.parse_args()

    if a.mode == "rename":
        return do_rename(a)

    print(f"NAS_BASE={a.nas_base}  include={a.include}")
    dirs = pick_dirs(a.nas_base, a.include)
    names = {d for d, _ in dirs}
    print(f"共选中 {len(dirs)} 份")
    miss = [n for n in "MI-8 RD-2 YAS_OUTFALL RB-1 Al_Riyadh_ponds RBE1-OF1 MBZ RD-14 RB-3 RD-A RD-F RD-C Qasr_Al_Bahr-1 RD-I RD-G".split() if f"{n}_61mm1h_30m" not in names]
    if a.include == DEFAULT_INCLUDE and (miss or "艾因_模型版_61mm1h_250m" not in names):
        print("⚠ 缺少：", miss, "" if "艾因_模型版_61mm1h_250m" in names else "艾因_模型版_61mm1h_250m")
    if a.mode == "list":
        return
    if not a.dest:
        sys.exit("copy 需要目标目录")
    check_dest(a.dest, a.nas_base)

    plan, collide = [], {}
    for d, p in dirs:
        for cur, _, files in os.walk(p):
            for f in files:
                if a.no_sww and f.endswith(".sww"):
                    continue
                src = os.path.join(cur, f)
                dst = os.path.join(a.dest, d, os.path.relpath(src, p))   # 目标 DEST/<成果目录>/...，原样不改名
                collide[dst] = src
                plan.append((src, dst))
    total = sum(os.path.getsize(s) for s, _ in plan)
    print(f"\n{len(plan)} 个文件，{total / 1e9:.2f} GB -> {a.dest}（原样，中文名不动）")
    if a.dry_run:
        for s, d in plan:
            print(f"{s}\n   -> {d}")
        return
    os.makedirs(a.dest, exist_ok=True)
    open(os.path.join(a.dest, MARK), "a").close()
    done = skipped = 0
    for i, (s, d) in enumerate(plan, 1):
        assert inside(d, a.dest) and not bad_dest(d, a.nas_base)   # 只写目标目录，且绝不在 result / shp_dat 里
        if os.path.exists(d) and os.path.getsize(d) == os.path.getsize(s):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(s, "rb") as fi, open(d + ".partial", "wb") as fo:   # 源只读打开
            shutil.copyfileobj(fi, fo, 8 << 20)
        shutil.copystat(s, d + ".partial")
        if os.path.getsize(d + ".partial") != os.path.getsize(s):
            sys.exit(f"校验失败（大小不一致）：{s}")
        os.replace(d + ".partial", d)
        done += 1
        if i % 200 == 0:
            print(f"  {i}/{len(plan)}", flush=True)
    print(f"完成：拷贝 {done}，跳过（已存在）{skipped}。下一步：python3 {sys.argv[0]} rename {a.dest} --dry-run")


def tr2(name):
    """映射表替换；映射表没覆盖到的中文用 cn<4位哈希> 兜底（稳定、不重名），保证一次改完。"""
    name = tr(name)
    return CJK_RUN.sub(lambda m: "cn" + hashlib.md5(m.group(0).encode("utf-8")).hexdigest()[:4], name)


def do_rename(a):
    """只在拷贝件 DEST 里改名（自底向上，先文件后目录），并写 DEST/rename_map.tsv（原路径 -> 新路径）。"""
    dest = a.dest
    if not dest or not os.path.isdir(dest) or not os.path.exists(os.path.join(dest, MARK)):
        sys.exit("rename 只能用于 copy 建出的目标目录（里面要有标记文件）")
    if bad_dest(dest, a.nas_base):
        sys.exit("拒绝：目标在 result / shp_dat 里面或包含它们")
    ops, fallback, seen, rows = [], set(), set(), []
    for cur, dirs, files in os.walk(dest, topdown=False):
        for n in files + dirs:
            if n in (MARK, MAPFILE):
                continue
            m = tr2(n)
            if m != tr(n):
                fallback.add(n)
            if m != n:
                ops.append((os.path.join(cur, n), os.path.join(cur, m)))
                old = os.path.relpath(os.path.join(cur, n), dest)
                rows.append((old, os.sep.join(tr2(x) for x in old.split(os.sep))))
    for s_, d_ in ops:
        if os.path.exists(d_) or d_ in seen:
            sys.exit(f"改名后重名，停止（什么都没改）：{s_} -> {d_}")
        seen.add(d_)
    print(f"{len(ops)} 处改名（只在 {dest} 内）")
    if fallback:
        print("下面这些中文名映射表里没有，已用 cnXXXX 兜底（对照见 rename_map.tsv）：")
        for x in sorted(fallback):
            print("  ", x, "->", tr2(x))
    if a.dry_run:
        for s_, d_ in ops:
            print(f"{os.path.relpath(s_, dest)}  ->  {os.path.basename(d_)}")
        return
    for s_, d_ in ops:
        assert inside(s_, dest) and inside(d_, dest) and not bad_dest(s_, a.nas_base)
        os.rename(s_, d_)
    with open(os.path.join(dest, MAPFILE), "w", encoding="utf-8", newline=chr(10)) as f:
        f.write("original" + chr(9) + "english" + chr(10))
        for o, n in sorted(rows):
            f.write(o + chr(9) + n + chr(10))
    print(f"完成；原名对照表 {os.path.join(dest, MAPFILE)}")


if __name__ == "__main__":
    main()
