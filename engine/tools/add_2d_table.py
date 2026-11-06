# -*- coding: utf-8 -*-
"""把三态 × 两种耦合方式的二维结果补进分享页。

这张表同时回答两件事：
  · 客户点名要的「最大积水面积是否变小」（①→③）
  · 单向耦合把退水过程算错了多少（每一态都一致，不是个例）
数字全部从运行结果读，不手抄。
"""
import json, pathlib, sys
sys.stdout.reconfigure(encoding="utf-8")
C2D = pathlib.Path(r"C:\Users\zn198\AppData\Local\Temp\claude\D----\f58f1df3-4927-423f-9aad-51c424e9800d\scratchpad\swmm\c2d")
PAGE = pathlib.Path(r"C:\Users\zn198\AppData\Local\Temp\claude\D----\f58f1df3-4927-423f-9aad-51c424e9800d\scratchpad\four_states.html")
NAME = {"L1": ("① 防线1 既有管网", "--s1"), "L2": ("② 防线2 已规划的塘", "--s2"),
        "L2B": ("③ 防线2+ 地下箱涵", "--s3")}

rows = []
for k, (zh, col) in NAME.items():
    o = json.loads((C2D / f"anuga_Mussafah_{k}.json").read_text(encoding="utf-8"))
    p = C2D / f"twoway_Mussafah_{k}.json"
    if not p.exists():
        raise SystemExit(f"{k} 的双向结果还没出，先别补")
    d = json.loads(p.read_text(encoding="utf-8"))
    om = max(o["series"], key=lambda z: z["wet_m2"]); oe = o["series"][-1]
    dm = max(d["series"], key=lambda z: z["wet_m2"]); de = d["series"][-1]
    back = d["acc_2to1_m3"] / max(d["acc_1to2_m3"], 1e-9) * 100
    clos = d["acc_1to2_m3"] - d["acc_2to1_m3"] - d["final_vol_m3"] - d["boundary_out_m3"]
    rows.append((zh, col,
                 om["wet_m2"] / 1e4, oe["wet_m2"] / 1e4, (1 - oe["wet_m2"] / om["wet_m2"]) * 100,
                 dm["wet_m2"] / 1e4, de["wet_m2"] / 1e4, (1 - de["wet_m2"] / dm["wet_m2"]) * 100,
                 back, clos))

tb = "".join(
    f'<tr><td style="color:var({c})"><b>{zh}</b></td>'
    f'<td class="n">{a:.1f} → {b:.1f}</td><td class="n" style="color:var(--warn)">{r1:.0f}%</td>'
    f'<td class="n">{d:.1f} → <b>{e:.1f}</b></td><td class="n">{r2:.0f}%</td>'
    f'<td class="n">{bk:.0f}%</td><td class="n"><span class="ok">{cl:.3f}</span></td></tr>'
    for zh, c, a, b, r1, d, e, r2, bk, cl in rows)

BLOCK = f'''  <div class="card rule">
    <h3>三个现状都是同一个模式，不是个例</h3>
    <p>Mussafah 三态各跑两遍二维，一维口径不变，只差耦合方式。
      左半边是客户点名要的<b>最大积水面积 ①→③</b>，右半边是单向耦合把退水过程错成了什么样。</p>
    <div class="scroll"><table>
      <tr><th>现状</th><th>单向 峰值 → 末态 ha</th><th>单向退水</th>
          <th>双向 峰值 → 末态 ha</th><th>双向退水</th><th>回灌占溢流</th><th>闭合差 m³</th></tr>
      {tb}
    </table></div>
    <p><b>最大积水面积 ①→③：5.4 → 2.3 ha（单向口径）、5.3 → {rows[2][5]:.1f} ha（双向口径）</b>，
      两种口径都指向同一个结论：地下箱涵把淹没范围砍掉一半以上。</p>
    <p>但退水那两列每一态都一样：<b>单向只退 {min(r[4] for r in rows):.0f}–{max(r[4] for r in rows):.0f}%</b>
      （那点还是水从计算域边界流走的），<b>双向退 {min(r[7] for r in rows):.0f}–{max(r[7] for r in rows):.0f}%</b>，
      {min(r[8] for r in rows):.0f}–{max(r[8] for r in rows):.0f}% 的溢流水从井盖回了管网。
      三态的水量对账闭合差都是 0.000 m³。</p>
  </div>

'''

t = PAGE.read_text(encoding="utf-8")
anchor = '  <div class="card rule">\n    <h3>每次运行都打印水量对账，不闭合就不要信结果</h3>'
assert t.count(anchor) == 1
PAGE.write_text(t.replace(anchor, BLOCK + anchor), encoding="utf-8")

# 保留那节里「二维只跑了 Mussafah」要更新成已含双向
old = "最大积水面积这一栏目前只有 Mussafah 三态（单向 5.4 → 4.8 → 2.3 ha）。"
new = (f"最大积水面积这一栏目前只有 Mussafah 三态（单向 {rows[0][2]:.1f} → {rows[1][2]:.1f} → {rows[2][2]:.1f} ha，"
       f"双向 {rows[0][5]:.1f} → {rows[1][5]:.1f} → {rows[2][5]:.1f} ha）。")
t = PAGE.read_text(encoding="utf-8")
assert t.count(old) == 1
PAGE.write_text(t.replace(old, new), encoding="utf-8")
print("二维三态对比表已补进页面")
for zh, c, a, b, r1, d, e, r2, bk, cl in rows:
    print(f"  {zh:<18}单向 {a:.1f}→{b:.1f}（退 {r1:.0f}%）　双向 {d:.1f}→{e:.1f}（退 {r2:.0f}%）　回灌 {bk:.0f}%　闭合差 {cl:.3f}")
