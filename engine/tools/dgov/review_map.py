"""问题登记簿的看图页：单个 HTML（Leaflet），本地打开即可，数据嵌在页面里，不外传。

  python -m tools.dgov.review_map --extent rda

图层：融合后管网（按来源着色）、雨水口连接管（可选层）、DMT 废弃管、各类问题（点开看证据、初判、建议、状态）。
底图：OpenStreetMap / Esri 影像（在线瓦片，只取底图，数据不上传）。
产物：out/dgov/issues/<extent>_review_map.html
"""
from __future__ import annotations

import argparse
import json
import sys

from .common import OUT

COLORS = {"DMT+AECOM": "#2b6cb0", "DMT": "#2f855a", "AECOM": "#c05621"}
CAT_COLORS = {"A": "#805ad5", "B": "#dd6b20", "C": "#e53e3e", "D": "#d69e2e", "E": "#319795", "F": "#718096"}

TEMPLATE = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>html,body{margin:0;height:100%;font:13px/1.45 system-ui,"Microsoft YaHei",sans-serif}#map{position:absolute;inset:0 380px 0 0}
#side{position:absolute;top:0;right:0;bottom:0;width:380px;overflow:auto;border-left:1px solid #ddd;background:#fff;padding:10px;box-sizing:border-box}
#side h3{margin:4px 0 8px}#list div{padding:5px 6px;border-bottom:1px solid #eee;cursor:pointer}#list div:hover{background:#f4f7fb}
.tag{display:inline-block;padding:0 5px;border-radius:3px;color:#fff;font-size:11px;margin-right:4px}select,input{font:inherit;margin:2px 0 8px;width:100%}
pre{white-space:pre-wrap;word-break:break-all;background:#f7f7f7;padding:6px;font-size:11px;max-height:220px;overflow:auto}</style></head><body>
<div id="map"></div><div id="side"><h3>__TITLE__</h3>
<select id="cat"><option value="">全部类别</option></select><select id="st"><option value="">全部状态</option></select>
<input id="q" placeholder="按编号或对象搜"><div id="n"></div><div id="list"></div><div id="detail"></div></div>
<script>
const D=__DATA__;
const map=L.map('map',{preferCanvas:true});
const osm=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:20,attribution:'© OpenStreetMap'}).addTo(map);
const img=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:20,attribution:'Esri'});
const C=__COLORS__, CC=__CATC__;
const net=L.geoJSON(D.links,{style:f=>({color:C[f.properties.src]||'#555',weight:f.properties.flags?3.5:2,dashArray:f.properties.flags?'6 4':null}),
  onEachFeature:(f,l)=>l.bindPopup('<b>'+f.properties.asset_id+'</b><br>来源 '+f.properties.src+'<br>'+(f.properties.flags||'')+'<br>管径 '+f.properties.diameter_mm+' mm')}).addTo(map);
const opt=L.geoJSON(D.optional,{style:{color:'#999',weight:1.2}});
const ab=L.geoJSON(D.abandoned,{style:{color:'#000',weight:1.5,dashArray:'2 4'}});
const iss=L.geoJSON(D.issues,{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:7,color:'#fff',weight:1.5,fillColor:CC[f.properties.category[0]],fillOpacity:.9}),
  style:f=>({color:CC[f.properties.category[0]],weight:5,opacity:.85}),onEachFeature:(f,l)=>{l.on('click',()=>show(f.properties));f.properties._l=l}}).addTo(map);
map.fitBounds(net.getBounds());
L.control.layers({'OSM':osm,'影像':img},{'融合后管网':net,'雨水口连接管（可选层）':opt,'DMT 废弃管':ab,'问题':iss}).addTo(map);
const lg=L.control({position:'bottomleft'});lg.onAdd=()=>{const d=L.DomUtil.create('div');d.style.cssText='background:#fff;padding:6px;font-size:12px';
 d.innerHTML=Object.entries(C).map(([k,v])=>'<span style="color:'+v+'">━</span> '+k).join('<br>')+'<br>虚线 = 带标记（AECOM 独有 / 补收 / 压力管）';return d};lg.addTo(map);
const cats=[...new Set(D.issues.features.map(f=>f.properties.category))].sort(), sts=[...new Set(D.issues.features.map(f=>f.properties.status))];
cats.forEach(c=>cat.add(new Option(c,c)));sts.forEach(s=>st.add(new Option(s,s)));
function show(p){detail.innerHTML='<h3>'+p.issue_id+'</h3><span class="tag" style="background:'+CC[p.category[0]]+'">'+p.category+'</span><span class="tag" style="background:#4a5568">'+p.status+'</span>'+
 '<p><b>'+p.title+'</b></p><p>初判：'+p.assessment+'</p><p>建议：'+p.action+'</p><p>依据：'+(p.basis||'')+'</p><p>证据：</p><pre>'+p.evidence+'</pre><p>对象：</p><pre>'+p.objects+'</pre>';
 const l=p._l;if(l){if(l.getBounds)map.fitBounds(l.getBounds(),{maxZoom:19});else map.setView(l.getLatLng(),19)}}
function render(){const c=cat.value,s=st.value,t=q.value.trim();const fs=D.issues.features.filter(f=>(!c||f.properties.category==c)&&(!s||f.properties.status==s)&&(!t||(f.properties.issue_id+f.properties.objects+f.properties.title).includes(t)));
 n.textContent=fs.length+' 条';list.innerHTML='';fs.forEach(f=>{const d=document.createElement('div');d.innerHTML='<span class="tag" style="background:'+CC[f.properties.category[0]]+'">'+f.properties.category[0]+'</span>'+f.properties.issue_id+' · '+f.properties.title+' <i>（'+f.properties.status+'）</i>';d.onclick=()=>show(f.properties);list.appendChild(d)})}
[cat,st,q].forEach(e=>e.oninput=render);render();
</script></body></html>"""


def main(argv=None):
    import geopandas as gpd
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.review_map")
    ap.add_argument("--extent", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    ext = a.extent
    ig = OUT / "int" / f"{ext}.gpkg"
    gj = lambda g, cols: json.loads(g[cols + ["geometry"]].to_crs(4326).to_json(drop_id=True))
    L = gpd.read_file(ig, layer="link")
    L["flags"] = L["flags"].fillna("")
    opt = gpd.read_file(ig, layer="link_optional")
    BL = gpd.read_file(OUT / "clean" / f"dmt_stormwater__{ext}.gpkg", layer="link")
    I = gpd.read_file(OUT / "issues" / f"{ext}_issues.gpkg", layer="issues")
    I["geometry"] = I.geometry.simplify(0.5)
    data = {"links": gj(L, ["asset_id", "src", "flags", "diameter_mm"]), "optional": gj(opt, ["asset_id"]),
            "abandoned": gj(BL[BL.active == 0], ["asset_id"]),
            "issues": gj(I, ["issue_id", "category", "title", "objects", "evidence", "assessment", "action", "status", "basis"])}
    title = f"{ext.upper()} 数据治理 · 问题看图"
    html = (TEMPLATE.replace("__TITLE__", title).replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__COLORS__", json.dumps(COLORS)).replace("__CATC__", json.dumps(CAT_COLORS)))
    out = OUT / "issues" / f"{ext}_review_map.html"
    out.write_text(html, encoding="utf-8")
    print(f"→ {out}（{out.stat().st_size / 1e6:.1f} MB）")


if __name__ == "__main__":
    main()
