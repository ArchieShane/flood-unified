# -*- coding: utf-8 -*-
"""打包 upload/adm_rest/（三台服务器共用一份）+ zip。先跑 thinking-drainage-system/tools/adm_rest_regions.py 生成 cases/ad/。"""
import pathlib, shutil, zipfile

R = pathlib.Path("C:/freedo/zn-260930")
T = R / "thinking-drainage-system"
SRC = R / "upload" / "adm_rest_src"
OUT = R / "upload" / "adm_rest"
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)


def put(src: pathlib.Path, dst: pathlib.Path, lf: bool = False):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if lf:       # .sh / .py 一律 LF（服务器是 Linux，CRLF 会让 bash 报 $'\r': command not found）
        dst.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))
    else:
        shutil.copy2(src, dst)


for f in ("adm_rest_server.sh", "adm_rest_publish.sh", "adm_rest_cleanup.sh", "README.txt"):
    put(SRC / f, OUT / f, lf=True)
for f in ("ad_run_30m.py", "ad_regions.py", "ad_post.py", "ad_frames.py", "c1_batch.py"):
    put(T / "tools" / f, OUT / f, lf=True)
for p in (T / "cases" / "ad").glob("*"):
    put(p, OUT / "cases" / "ad" / p.name)
for p in (R / "upload" / "ad_30m" / "server1" / "h2d").glob("*"):
    put(p, OUT / "h2d" / p.name)
for f in ("sim_c1.py", "sim_probe.py", "sim_ponding.py"):
    put(pathlib.Path("C:/freedo/flood-management/backend/src/stormwater") / f, OUT / "aio_patch" / f, lf=True)
put(R / "flood-viewer" / "server" / "server.py", OUT / "viewer_update" / "server.py", lf=True)
bad = [str(p) for p in OUT.rglob("*") if p.is_file() and p.suffix in (".sh", ".py") and b"\r" in p.read_bytes()]
assert not bad, bad
z = R / "upload" / "adm_rest.zip"
if z.exists():
    z.unlink()
with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            zf.write(p, p.relative_to(OUT.parent).as_posix())
print(f"{OUT}：{sum(1 for _ in OUT.rglob('*') if _.is_file())} 个文件；{z}（{z.stat().st_size / 1048576:.1f} MB）")
