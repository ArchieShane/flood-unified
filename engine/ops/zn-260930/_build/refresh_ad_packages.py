# 10-16 塘清单更新：把改过的脚本 / 案例数据 / ad_reset_l2.sh 刷进 upload/ad_30m/server1|2|3 并重打 zip（h2d 等不动）
import pathlib, shutil, zipfile
R = pathlib.Path(r"C:\freedo\zn-260930"); T = R / "thinking-drainage-system" / "tools"; CASES = R / "thinking-drainage-system" / "cases" / "ad"; OUT = R / "upload" / "ad_30m"
for n in (1, 2, 3):
    d = OUT / f"server{n}"
    for f in ("ad_run_30m.py", "ad_regions.py", "ad_post.py", "c1_batch.py", "ad_frames.py"):
        shutil.copy2(T / f, d / f)
    shutil.copy2(R / "_build" / "ad_reset_l2.sh", d / "ad_reset_l2.sh")
    shutil.copy2(R / "_build" / "ad_copy_npz.sh", d / "ad_copy_npz.sh")
    shutil.copy2(R / "_build" / "ad_convert_full.sh", d / "ad_convert_full.sh")
    for f in CASES.iterdir():
        if f.suffix in (".json", ".geojson"):
            shutil.copy2(f, d / "cases" / "ad" / f.name)
    with zipfile.ZipFile(OUT / f"server{n}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(d.rglob("*")):
            if f.is_file():
                z.write(f, f"server{n}/" + f.relative_to(d).as_posix())
    print(n, "ok", sum(1 for _ in (d / "cases" / "ad").iterdir()))
