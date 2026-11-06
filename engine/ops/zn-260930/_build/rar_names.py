import re, os
BASE = "C:/freedo/zn-260930/thinking-drainage-system/data/261015"
SEP = chr(92)


def tree(folder):
    s = set()
    for r, _, fs in os.walk(folder):
        for f in fs:
            if not f.endswith(".rar"):
                s.add(os.path.relpath(os.path.join(r, f), folder).replace(SEP, "/"))
    return s


for rar, folder in (("FW__Hot_Spots_506.rar", "FW__Hot_Spots_506"), ("汇水区报告-Parsons.rar", "汇水区报告-Parsons")):
    b = open(f"{BASE}/{rar}", "rb").read().decode("latin1")
    ext = tree(f"{BASE}/{folder}")
    found = {f for f in ext if f.encode("utf8").decode("latin1") in b}
    cand = set(re.findall(r"[A-Za-z0-9_\- ()/]{3,80}\.(?:shp|shx|dbf|prj|kmz|pdf|xlsx|docx|inp|tif|csv|json|kml|cpg)\b", b))
    base_names = {e.split("/")[-1] for e in ext}
    extra = {c for c in cand if c.split("/")[-1] not in base_names}
    print(rar, "解压后文件", len(ext), "| rar 头里找到名字的", len(found), "| rar 里有、解压目录没有的名字", sorted(extra)[:20])
