import pathlib
R = pathlib.Path(r"C:\freedo\zn-260930")
a = "find \"$RESULT_ROOT/$HOST_NAME\" -path '*/二维/*.sww' -type f"
b = "find \"$RESULT_ROOT/$HOST_NAME\" -path \"${PATHPAT:-*/二维/*.sww}\" -type f"
for f in [R/"_build"/"ad_server.sh.tpl"] + [R/"upload"/"ad_30m"/f"server{n}"/f"ad_30m_server{n}.sh" for n in (1,2,3)]:
    s = f.read_text(encoding="utf-8"); print(f.name, a in s)
    f.write_text(s.replace(a, b), encoding="utf-8", newline="\n")
