# 生成 upload/ad_30m/server1|2|3/（每台一个自包含目录）和 zip
import pathlib, shutil, zipfile

R = pathlib.Path(r"C:\freedo\zn-260930")
T = R / "thinking-drainage-system" / "tools"
CASES = R / "thinking-drainage-system" / "cases" / "ad"
OUT = R / "upload" / "ad_30m"
TPL = (R / "_build" / "ad_server.sh.tpl").read_text(encoding="utf-8")
H2D_SCRIPT = pathlib.Path(r"C:\Users\admin\Documents\xwechat_files\wxid_g7zgcnxdygbf22_7399\temp\RWTemp\2026-10\e406f3b57ee5a23bdde23a0c84eb7683\hydro2d_convert(1).py")
WHEEL = R / "_build" / "wheels" / "pyshp-3.1.6-py3-none-any.whl"

S = {
    1: dict(spec="48 核 / 188 GB，AZPDW006aim01", what="MI-8（250.6 km²，约 128 万个三角单元，L1 + L2 两个态同时算，各吃一半核）",
            tags="MI8_M61H1",
            cfg='''TAGS=${TAGS:-"MI8_M61H1"}
SLOTS=${SLOTS:-2}                                    # L1 / L2 同时算
THREADS_MAP=${THREADS_MAP:-"MI8=$(( (CORES - 4) / 2 ))"}   # 留 4 核给调度 / 建模 / 后处理；48 核 → 每态 22 线程
MIN_FREE_GB=${MIN_FREE_GB:-20}
CONV_JOBS=${CONV_JOBS:-2}                            # 算完后 2 个态同时转 shp + dat'''),
    2: dict(spec="32 核 / 64 GB，AZPDW006uca03", what="RD-2（127 km²，约 65 万个三角单元）、YAS OUTFALL（25.9 km²）、RB-1（18.4 km²），共 6 个态同时算",
            tags="RD2_M61H1 YASO_M61H1 RB1_M61H1",
            cfg='''TAGS=${TAGS:-"RD2_M61H1 YASO_M61H1 RB1_M61H1"}
SLOTS=${SLOTS:-6}                                    # 6 个态一起算
THREADS_MAP=${THREADS_MAP:-"RD2=$(scale 11),YASO=$(scale 3),RB1=$(scale 2)"}   # 32 核：RD-2 每态 11（共 22）、YAS 每态 3、RB-1 每态 2，合计 32
MIN_FREE_GB=${MIN_FREE_GB:-8}
CONV_JOBS=${CONV_JOBS:-6}                            # 算完后 6 个态同时转 shp + dat'''),
    3: dict(spec="32 核 / 64 GB，AZPDW006uca06", what="RBE1-OF1、MBZ、RD-14、RB-3、RD-I、RD-F、RD-G、RD-A、RD-C、Qasr Al Bahr-1（共 104 km²，20 个态，大区域先算）",
            tags="RBE1_M61H1 MBZ_M61H1 RD14_M61H1 RB3_M61H1 RDI_M61H1 RDF_M61H1 RDG_M61H1 RDA_M61H1 RDC_M61H1 QAB1_M61H1",
            cfg='''TAGS=${TAGS:-"RBE1_M61H1 MBZ_M61H1 RD14_M61H1 RB3_M61H1 RDI_M61H1 RDF_M61H1 RDG_M61H1 RDA_M61H1 RDC_M61H1 QAB1_M61H1"}
SLOTS=${SLOTS:-12}                                   # 20 个态里同时算 12 个（大区域排前面），一个算完补一个
THREADS_MAP=${THREADS_MAP:-"RBE1=$(scale 3),MBZ=$(scale 3),RD14=$(scale 3),RB3=$(scale 2),RDI=$(scale 2),RDF=$(scale 2),RDG=$(scale 2),RDA=$(scale 1),RDC=$(scale 1),QAB1=$(scale 1)"}
MIN_FREE_GB=${MIN_FREE_GB:-6}
CONV_JOBS=${CONV_JOBS:-8}                            # 算完后 8 个态同时转 shp + dat'''),
}
DOCKERFILE = '''# 转换镜像 h2d-convert:1.0：anuga-swmm:1.0 + pyshp + hydro2d_convert.py（离线构建：docker build -t h2d-convert:1.0 h2d/）
FROM anuga-swmm:1.0
COPY pyshp-3.1.6-py3-none-any.whl /tmp/
RUN pip install --no-cache-dir --no-index /tmp/pyshp-3.1.6-py3-none-any.whl && rm /tmp/*.whl
COPY hydro2d_convert.py /opt/h2d/hydro2d_convert.py
WORKDIR /data
ENTRYPOINT ["python", "/opt/h2d/hydro2d_convert.py"]
'''
OUT.mkdir(parents=True, exist_ok=True)
for n, c in S.items():
    d = OUT / f"server{n}"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    (d / "h2d").mkdir(parents=True, exist_ok=True)
    sh = (TPL.replace("@@N@@", str(n)).replace("@@SPEC@@", c["spec"]).replace("@@WHAT@@", c["what"]).replace("@@TAGS@@", c["tags"]).replace("@@CONFIG@@", c["cfg"]))
    (d / f"ad_30m_server{n}.sh").write_text(sh, encoding="utf-8", newline="\n")
    for f in ("ad_run_30m.py", "ad_regions.py", "ad_post.py", "c1_batch.py"):
        shutil.copy2(T / f, d / f)
    (d / "cases" / "ad").mkdir(parents=True, exist_ok=True)
    for f in CASES.iterdir():
        if f.suffix in (".json", ".geojson"):
            shutil.copy2(f, d / "cases" / "ad" / f.name)
    shutil.copy2(H2D_SCRIPT, d / "h2d" / "hydro2d_convert.py")
    shutil.copy2(WHEEL, d / "h2d" / WHEEL.name)
    (d / "h2d" / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8", newline="\n")
    with zipfile.ZipFile(OUT / f"server{n}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(d.rglob("*")):
            if f.is_file():
                z.write(f, f"server{n}/" + f.relative_to(d).as_posix())
    print(f"server{n}：", sorted(p.name for p in d.iterdir()), sum(1 for _ in (d / 'cases' / 'ad').iterdir()), "个案例文件")
