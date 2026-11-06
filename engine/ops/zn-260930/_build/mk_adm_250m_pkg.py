# -*- coding: utf-8 -*-
"""打包 upload/adm_250m/（三台服务器共用一份）+ zip。先跑 thinking-drainage-system/tools/adm250_regions.py 生成 cases/ad250/。"""
import pathlib, shutil, zipfile

R = pathlib.Path("C:/freedo/zn-260930")
T = R / "thinking-drainage-system"
SRC = R / "upload" / "adm_250m_src"
OUT = R / "upload" / "adm_250m"
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)


def put(src: pathlib.Path, dst: pathlib.Path, lf: bool = False):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if lf:       # .sh / .py 一律 LF（服务器是 Linux，CRLF 会让 bash 报 $'\r': command not found）
        dst.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))
    else:
        shutil.copy2(src, dst)


for f in ("adm_250m_server.sh", "adm_250m_cleanup.sh", "README.txt"):
    put(SRC / f, OUT / f, lf=True)
for f in ("adm250_run.py", "adm250_post.py", "c1_batch.py"):
    put(T / "tools" / f, OUT / f, lf=True)
for p in (T / "cases" / "ad250").glob("*"):
    put(p, OUT / "cases" / "ad250" / p.name)
bad = [str(p) for p in OUT.rglob("*") if p.is_file() and p.suffix in (".sh", ".py", ".txt") and b"\r" in p.read_bytes()]
assert not bad, bad
z = R / "upload" / "adm_250m.zip"
if z.exists():
    z.unlink()
with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            zf.write(p, p.relative_to(OUT.parent).as_posix())
print(f"{OUT}：{sum(1 for _ in OUT.rglob('*') if _.is_file())} 个文件；{z}（{z.stat().st_size / 1048576:.1f} MB）")
