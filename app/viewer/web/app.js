// 洪水推演成果展示：三个下拉框（区域 / 格网精度 / 模拟雨量）→ 一个情景的四态对比。
// 展示内容和原控制台的 C1 页一致（状态页签、关键指标、逐帧播放 / 最大水深 / 差值图、积水面积曲线、雨型、热点过程线、各热点表、设施），
// 数据来自 server.py 的 /api/catalog、/api/scenario 和 /data/…（逐帧 PNG）。
(() => {
'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = (v, d = 0) => v == null || isNaN(v) ? '—' : Number(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
const api = async url => { const r = await fetch(url); if(!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status); return r.json(); };

// ---------------------------------------------------------------- 常量（态的名称、颜色、说明）
const ST_COL = {L1: '#334155', L2: '#0e7490', L2P: '#b45309', L2S: '#7c3aed', L2S3: '#15803d'};
const ST_PAL = ['#334155', '#0e7490', '#b45309', '#15803d', '#7c3aed', '#be185d'];
const ST_SUB = {L1: '现状 + 现状泵站', L2: '+ 移动泵', L2P: 'Parsons 方案', L2S: '+ 转输 / 地面取水泵', L2S3: 'v3 按塘余量分配'};
const ST_LAB = {L1: 'L1', L2: 'L2', L2P: 'L2+planned', L2S: 'L2+suggested', L2S3: 'L2+suggested v3'};
const ST_DESC = {
  L1: 'L1：现状管网 + 客户库的 6 座现状泵站',
  L2: 'L2：L1 + OP-02 的 23 台 12 英寸移动泵（雨前布设），排到下游不溢流的井',
  L2P: 'L2+planned：L1 + Parsons PDF 版 27 座固定泵站（只开主用）+ 12 座 1 m 围堤塘 + 压力管；保留 MP-16 移动泵',
  L2S3: 'L2+suggested v3：措施处数与位置同 v2，只改去向与控制——按规划塘剩余库容分配、塘满停泵、地面取水泵直接打进塘；为消掉 v2 在小塘周边的副作用'};
const PARENT = {L2: 'L1', L2P: 'L2', L2S: 'L2P', L2S3: 'L2P'};
const chain = st => st === 'L2S3' ? ['L1', 'L2P', 'L2S3'] : st === 'L2S' ? ['L1', 'L2P', 'L2S'] : st === 'L2P' ? ['L1', 'L2P'] : st === 'L2' ? ['L1', 'L2'] : ['L1'];
const ZONE_KIND = {catch: '汇水区', '200m': '200 m 内', served: '管网服务区', all: '全域', unit: '模型单元', rain_on_grid: '网格直接降雨'};

const FAC_NAME = {ps_l1: '现状泵站', mobile: '移动泵', ps_planned: 'Parsons 泵站', pond: '规划塘', conv: '转输泵（固定泵 + 地面管）', sump: '地面取水泵', bund: '挡水', box: '箱涵'};
const FAC_STYLE = {ps_l1: {sh: 'sq', c: '#334155'}, mobile: {sh: 'tri', c: '#0891b2'}, ps_planned: {sh: 'sq', c: '#c2410c'}, pond: {sh: 'pond', c: '#2563eb'},
                   conv: {sh: 'dia', c: '#7c3aed'}, sump: {sh: 'cir', c: '#db2777'}, box: {sh: 'rect', c: '#57534e'}, bund: {sh: 'rect', c: '#a16207'}};
const FAC_ORDER = ['ps_l1', 'mobile', 'ps_planned', 'pond', 'conv', 'sump', 'box', 'bund'];
const P_LAB = {q_lps: '流量', n: '台数', q_each: '单泵流量', src: '流量来源', on: '开泵水深', off: '停泵水深', unitid: '客户编号', to: '排到', from: '吸水', well: '吸水井深',
               suction: '离吸水井', path: '到接收井管长', hs: '服务热点', hsd: '离热点', wetwell: '湿井', vol: '容积', area: '占地', depth: '塘深', floor: '塘底高程',
               patch: '所治积水片面积', peak: '所治积水片最大水量', node: '接在井'};
const M_LAB = {vol_m3: '抽水量', util_pct: '运行时间占比', starts: '启停次数', fill_pct: '末满度', end_m3: '末蓄水', surface_m3: '地表进塘', pumped_in_m3: '泵打入',
               overflow_m3: '塘满溢出', max_m3: '最大蓄水', max_pct: '最大满度'};
const UNIT = {n: [0, ''], q_each: [0, 'L/s'], q_lps: [0, 'L/s'], on: [2, 'm'], off: [2, 'm'], well: [2, 'm'], suction: [0, 'm'], path: [0, 'm'], hsd: [0, 'm'], vol: [0, 'm³'],
              area: [2, 'ha'], patch: [2, 'ha'], depth: [2, 'm'], floor: [2, 'm'], peak: [0, 'm³'], vol_m3: [0, 'm³'], util_pct: [1, '%'], starts: [0, ''], fill_pct: [1, '%'],
              end_m3: [0, 'm³'], surface_m3: [0, 'm³'], pumped_in_m3: [0, 'm³'], overflow_m3: [0, 'm³'], max_m3: [0, 'm³'], max_pct: [1, '%']};
const pv = (k, v) => { const u = UNIT[k]; return u && typeof v === 'number' ? fmt(v, u[0]) + (u[1] ? ' ' + u[1] : '') : esc(v); };

// ---------------------------------------------------------------- 状态
let CAT = null;                       // /api/catalog
const SEL = {region: null, grid: null, rain: null};
let S = null;                         // 当前情景 /api/scenario
const V = {st: null, base: null, t: null, mode: 'frame', timer: null, speed: 320, lay: {}};
let token = 0;

const stLab = s => S?.cs?.state_names?.[s] || ST_LAB[s] || s;
const stCol = s => ST_COL[s] || ST_PAL[Math.max(0, (S?.manifest?.states || []).findIndex(x => x.state === s)) % ST_PAL.length];
const haveStates = () => (S?.manifest?.states || []).map(x => x.state);
const curSt = () => (S?.manifest?.states || []).find(x => x.state === V.st) || null;
const zones = () => S?.cs?.zones || {};
const zv = (zone, st, k) => zones()[zone]?.[st]?.[k];

// ---------------------------------------------------------------- 地图
const map = L.map('map', {zoomControl: true, preferCanvas: true}).setView([24.42, 54.55], 11);
// 底图：由浏览器直接取瓦片（服务器不需要外网）。缺省 OSM 在线；挂了离线瓦片目录（/tiles）则优先用它；TILE_URL 设空 = 纯色背景，见 /api/config
api('/api/viewer/config').then(c => { if(!c.tiles) return; L.tileLayer(c.tiles, {maxZoom: 19}).addTo(map); map.getPane('tilePane').style.filter = 'grayscale(.85) opacity(.7)'; }).catch(() => {});
function mapClear(){ Object.values(V.lay).forEach(l => l && map.removeLayer(l)); V.lay = {}; }

function symbol(kind, z){
  const k = FAC_STYLE[kind] || {sh: 'cir', c: '#64748b'}, c = k.c, h = z / 2, w = 'stroke="#fff" stroke-width="1.6"';
  let g;
  if(k.sh === 'sq') g = `<rect x="1.5" y="1.5" width="${z - 3}" height="${z - 3}" rx="2" fill="${c}" ${w}/><path d="M${h - z * .18} ${h + z * .14}L${h} ${h - z * .18}L${h + z * .18} ${h + z * .14}" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>`;
  else if(k.sh === 'tri') g = `<path d="M${h} 1.5L${z - 1.5} ${z - 2}L1.5 ${z - 2}Z" fill="${c}" ${w} stroke-linejoin="round"/>`;
  else if(k.sh === 'dia') g = `<path d="M${h} 1L${z - 1} ${h}L${h} ${z - 1}L1 ${h}Z" fill="${c}" ${w}/><path d="M${h - z * .2} ${h}H${h + z * .18}M${h + z * .05} ${h - z * .13}L${h + z * .2} ${h}L${h + z * .05} ${h + z * .13}" stroke="#fff" stroke-width="1.5" fill="none"/>`;
  else if(k.sh === 'rect') g = `<rect x="1" y="${z * .22}" width="${z - 2}" height="${z * .56}" rx="1.5" fill="${c}" ${w}/>`;
  else if(k.sh === 'pond') g = `<rect x="1.5" y="${z * .18}" width="${z - 3}" height="${z * .64}" rx="${z * .3}" fill="${c}" fill-opacity=".35" stroke="${c}" stroke-width="1.6"/><path d="M${z * .25} ${h}q${z * .125} -${z * .14} ${z * .25} 0t${z * .25} 0" fill="none" stroke="${c}" stroke-width="1.3"/>`;
  else g = `<circle cx="${h}" cy="${h}" r="${h - 1.5}" fill="${c}" ${w}/><circle cx="${h}" cy="${h}" r="${z * .14}" fill="#fff"/>`;
  return `<svg width="${z}" height="${z}" viewBox="0 0 ${z} ${z}" style="overflow:visible;vertical-align:middle;flex:none">${g}</svg>`;
}
const symSize = f => f.kind === 'pond' ? 16 : f.kind === 'box' ? Math.max(14, Math.min(24, 10 + Math.sqrt(f.vol_m3 || 0) / 3)) : Math.max(12, Math.min(28, 10 + 3.2 * Math.sqrt((f.q_lps || 0) / 50)));

function facPopup(f, s){
  const k = FAC_STYLE[f.kind] || {c: '#64748b'}, P = f.p || {}, Sm = (f.sim || {})[V.st];
  const row = (a, b) => `<tr><td style="color:#66717f;padding:1px 10px 1px 0;white-space:nowrap;vertical-align:top">${a}</td><td style="padding:1px 0">${b}</td></tr>`;
  let h = `<div style="min-width:240px;max-width:340px"><div style="display:flex;gap:6px;align-items:center;margin-bottom:4px">${symbol(f.kind, 16)}<b style="font-size:13px">${esc(f.id)}</b>`
    + `<span style="color:${k.c}">${esc(FAC_NAME[f.kind] || f.kind)}</span><span class="m" style="margin-left:auto">${esc(stLab(s))}</span></div><table>`;
  if(f.q_lps && f.kind !== 'ps_planned') h += row('流量', `<b>${pv('q_lps', f.q_lps)}</b>`);
  Object.entries(P).forEach(([key, v]) => { if(v == null || v === '' || key === 'why') return; h += row(P_LAB[key] || esc(key), pv(key, v)); });
  h += '</table>';
  if(P.why) h += `<div class="m" style="margin-top:4px">${esc(P.why)}</div>`;
  h += `<div style="margin-top:6px;border-top:1px solid #e3e7ec;padding-top:4px"><b>本场表现（${esc(stLab(V.st))}）</b>`;
  h += Sm ? `<table>${Object.entries(Sm).map(([key, v]) => row(M_LAB[key] || esc(key), pv(key, v))).join('')}</table>` : '<div class="m">这个态里没有它的运行记录</div>';
  return h + '</div></div>';
}

function drawFacilities(){
  if(V.lay.fac){ map.removeLayer(V.lay.fac); V.lay.fac = null; }
  const F = S.cs?.facilities; if(!F) return;
  const g = L.featureGroup(), pts = [];
  chain(V.st).forEach(s => (F[s] || []).forEach(f => { if(!f.ll) return; const c = (FAC_STYLE[f.kind] || {c: '#64748b'}).c;
    if(f.kind === 'pond' && f.poly) L.polygon(f.poly, {color: c, weight: 1.6, fillColor: c, fillOpacity: .22}).bindPopup(() => facPopup(f, s), {maxWidth: 360}).addTo(g);
    if(f.line) L.polyline(f.line, {color: c, weight: 3.5, opacity: .9}).addTo(g);
    if(f.to_ll){ L.polyline([f.ll, f.to_ll], {color: c, weight: 1.8, opacity: .85, dashArray: '5,4', interactive: false}).addTo(g);
      L.circleMarker(f.to_ll, {radius: 2.6, color: c, weight: 1, fillColor: '#fff', fillOpacity: 1, interactive: false}).addTo(g); }
    pts.push([f, s]); }));
  pts.sort((a, b) => symSize(b[0]) - symSize(a[0])).forEach(([f, s]) => { const z = Math.round(symSize(f));
    L.marker(f.ll, {icon: L.divIcon({className: 'csym', html: symbol(f.kind, z), iconSize: [z, z], iconAnchor: [z / 2, z / 2]}), keyboard: false, riseOnHover: true})
      .bindTooltip(`<b>${esc(f.id)}</b> · ${esc(FAC_NAME[f.kind] || f.kind)}${f.q_lps ? ` · ${fmt(f.q_lps)} L/s` : ''}${f.vol_m3 ? ` · ${fmt(f.vol_m3)} m³` : ''} · ${esc(stLab(s))}`, {direction: 'top', offset: [0, -z / 2]})
      .bindPopup(() => facPopup(f, s), {maxWidth: 360}).addTo(g); });
  V.lay.fac = g.addTo(map);
}

// ---------------------------------------------------------------- 指标 / 表格
function baseState(){ const have = new Set(haveStates()), b = V.base || PARENT[V.st]; return b && have.has(b) && b !== V.st ? b : null; }
function delta(v, a, b){
  if(a == null || v == null) return '';
  if(!a) return v ? `<span class="d bad">+${fmt(v, 1)} 对比 ${esc(stLab(b))}</span>` : `<span class="d">= ${esc(stLab(b))}</span>`;
  const p = (v - a) / Math.abs(a) * 100;
  return Math.abs(p) < 0.05 ? `<span class="d">= ${esc(stLab(b))}</span>` : `<span class="d ${p < 0 ? 'good' : 'bad'}">${p > 0 ? '+' : ''}${p.toFixed(1)}% 对比 ${esc(stLab(b))}</span>`;
}
function kpis(){
  const zk = Object.keys(zones());
  const hsU = zk.find(k => /个热点\|catch$/.test(k)) || zk.find(k => /个热点\|200m$/.test(k));
  const hs200 = zk.find(k => /个热点\|200m$/.test(k)) || hsU;
  const all = zk.find(k => k === '全域|served') || zk.find(k => k === '全域|all') || zk.find(k => k.startsWith('全域'));
  const nm = z => { const [a, b] = z.split('|'); return `${a} · ${ZONE_KIND[b] || b}`; };
  const K = [];
  if(hsU) K.push([hsU, 'area_ge_0p30_ha', 'ha', 1, `${nm(hsU)} · ≥0.30 m 积水面积`]);
  if(hs200) K.push([hs200, 'max_depth_m', 'm', 2, `${nm(hs200)} · 最大水深`]);
  if(hsU) K.push([hsU, 'peak_vol_m3', 'm³', 0, `${nm(hsU)} · 最大积水量`]);
  if(all) K.push([all, 'area_ge_0p30_ha', 'ha', 1, `${nm(all)} · ≥0.30 m 积水面积`]);
  // 没有热点 / 汇水区分区的区域（艾因 250 m 等）只有全域一档：把最大水深、最大积水量、≥0.10 m 面积也列出来
  if(all && !hsU){
    K.push([all, 'area_ge_0p10_ha', 'ha', 1, `${nm(all)} · ≥0.10 m 积水面积`]);
    K.push([all, 'max_depth_m', 'm', 2, `${nm(all)} · 最大水深`]);
    K.push([all, 'peak_vol_m3', 'm³', 0, `${nm(all)} · 最大积水量`]);
  }
  const b = baseState();
  return K.map(([z, k, u, d, lab]) => { const v = zv(z, V.st, k), a = b ? zv(z, b, k) : null;
    return `<div class="kpi"><span class="lab">${esc(lab)}</span><b>${fmt(v, d)}<small>${u}</small></b>${b ? delta(v, a, b) : ''}</div>`; }).join('')
    || '<div class="m">没有统计值</div>';
}
function hotspotTable(){
  const zk = Object.keys(zones()), ids = [];
  zk.forEach(k => { const [n, kind] = k.split('|'); if((kind === 'catch' || kind === '200m') && !/个热点|全域/.test(n) && !ids.includes(n)) ids.push(n); });
  if(!ids.length) return '';
  const useCatch = ids.some(i => zones()[i + '|catch']), st = V.st, b = baseState();
  const cell = (v, a, d) => { if(v == null) return '—'; if(a == null) return fmt(v, d); const dd = v - a;
    return `${fmt(v, d)}${Math.abs(dd) >= Math.pow(10, -d) ? ` <span class="${dd < 0 ? 'good' : 'bad'}">${dd > 0 ? '+' : ''}${fmt(dd, d)}</span>` : ''}`; };
  const zz = i => zones()[i + (useCatch ? '|catch' : '|200m')] ? i + (useCatch ? '|catch' : '|200m') : i + '|200m';
  return `<table><tr><th>热点</th><th class="num">${useCatch ? '汇水区' : '200 m 内'} ≥0.30 m（ha）</th><th class="num">200 m 内最深（m）</th><th class="num">${useCatch ? '汇水区' : '200 m 内'}最大积水量（m³）</th></tr>`
    + ids.map(h => { const z = zz(h), z2 = h + '|200m';
      return `<tr><td><i class="dot" style="background:#64748b"></i><b>${esc(h)}</b></td>`
        + `<td class="num">${cell(zv(z, st, 'area_ge_0p30_ha'), b ? zv(z, b, 'area_ge_0p30_ha') : null, 1)}</td>`
        + `<td class="num">${cell(zv(z2, st, 'max_depth_m'), b ? zv(z2, b, 'max_depth_m') : null, 2)}</td>`
        + `<td class="num">${cell(zv(z, st, 'peak_vol_m3'), b ? zv(z, b, 'peak_vol_m3') : null, 0)}</td></tr>`; }).join('')
    + '</table><div class="m" style="margin-top:4px">括号外是本态，彩色数是和对比基准之差（绿 = 改善）；各热点汇水区有重叠，「全部热点」是并集不是相加。</div>';
}
function facList(){
  const F = S.cs?.facilities; if(!F) return '<div class="m">这个情景没有设施清单。</div>';
  const rows = chain(V.st).map(s => { const fs = F[s] || []; if(!fs.length) return '';
    const by = {}; fs.forEach(f => { const o = by[f.kind] = by[f.kind] || {n: 0, q: 0, v: 0}; o.n++; o.q += f.q_lps || 0; o.v += f.vol_m3 || 0; });
    return `<div class="m" style="margin:6px 0 2px"><b style="color:${stCol(s)}">${esc(stLab(s))}</b></div>`
      + FAC_ORDER.filter(k => by[k]).map(k => { const o = by[k];
        return `<div class="facrow">${symbol(k, 15)}<span>${esc(FAC_NAME[k])}</span><span class="m">${o.n}${o.q ? ` · ${fmt(o.q)} L/s` : ''}${o.v ? ` · ${fmt(o.v)} m³` : ''}</span></div>`; }).join(''); }).join('');
  return rows + '<div class="m" style="margin-top:6px">形状、颜色分设施类型，泵的大小随流量；虚线 = 排水去向，白点 = 排到的位置；点设施看设计参数和当前态的实际表现。</div>';
}

// ---------------------------------------------------------------- 图
function nowFrame(){ const s = curSt(); if(!s) return null; const i = frameIdx(); return {s, i, f: s.frames[i]}; }
function frameIdx(){ const s = curSt(); if(!s) return 0; if(V.t == null) return s.peak_frame || 0;
  let b = 0; s.frames.forEach((f, i) => { if(Math.abs(f.t_h - V.t) < Math.abs(s.frames[b].t_h - V.t)) b = i; }); return b; }
const axStep = tm => tm > 30 ? 6 : tm > 12 ? 4 : tm > 6 ? 2 : 1;

function chartWet(){
  const el = $('chWet'); if(!el) return;
  const X0 = 38, X1 = 332, Y0 = 34, Y1 = 132, all = S.manifest.states;
  const TM = Math.max(...all.map(s => s.frames[s.frames.length - 1].t_h));
  const AM = Math.max(50, Math.ceil(Math.max(...all.flatMap(s => s.frames.map(f => f.wet10_ha || 0))) / 100) * 100);
  const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / AM;
  let h = '';
  for(let a = 0; a <= AM + 1e-9; a += AM / 4) h += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a)}</text>`;
  for(let v = 0; v <= TM + 1e-9; v += axStep(TM)) h += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
  h += `<text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">时间 h</text><text x="2" y="9" font-size="9" fill="#66717f">积水面积（≥0.10 m）ha</text>`;
  const b = baseState();
  all.forEach(s => { const on = s.state === V.st, bs = s.state === b;
    h += `<polyline points="${s.frames.map(f => `${sx(f.t_h).toFixed(1)},${sy(f.wet10_ha || 0).toFixed(1)}`).join(' ')}" fill="none" stroke="${stCol(s.state)}" stroke-width="${on ? 2.6 : bs ? 1.8 : 1}" opacity="${on || bs ? 1 : .35}"/>`; });
  const n = nowFrame();
  if(n && V.mode === 'frame') h += `<line x1="${sx(n.f.t_h)}" x2="${sx(n.f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/><circle cx="${sx(n.f.t_h)}" cy="${sy(n.f.wet10_ha || 0)}" r="4" fill="${stCol(V.st)}" stroke="#fff" stroke-width="1.5"/>`;
  h += `<g font-size="9.5">${all.map((s, i) => `<g transform="translate(${X0 + 6 + i * 74},${Y0 - 12})"><rect width="9" height="9" y="-8" fill="${stCol(s.state)}"/><text x="12" y="0" fill="#44505c">${esc(stLab(s.state))}</text></g>`).join('')}</g>`;
  el.innerHTML = h;
}
function chartRain(){
  const el = $('chRain'), cap = $('rainCap'), h = S.hyeto; if(!el) return;
  if(!h || !h.t_h){ el.innerHTML = ''; if(cap) cap.textContent = '这个情景没有雨型数据（雨量 ' + S.mm + ' mm / ' + S.h + ' h）。'; return; }
  const X0 = 38, X1 = 312, Y0 = 16, Y1 = 96, TM = h.sim_h, dt = h.dt_min / 60;
  const PM = Math.ceil(Math.max(...h.p_mmph) / 100) * 100 || 100, CM = Math.ceil(h.rain_mm / 20) * 20;
  const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / PM, sc = v => Y1 - (Y1 - Y0) * v / CM;
  let g = '';
  for(let a = 0; a <= PM + 1e-9; a += PM / 2) g += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a)}</text>`;
  for(let a = 0; a <= CM + 1e-9; a += CM / 2) g += `<text x="${X1 + 4}" y="${sc(a) + 3.5}" font-size="9" fill="#1f5f96">${fmt(a)}</text>`;
  for(let v = 0; v <= TM + 1e-9; v += axStep(TM)) g += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
  const bw = Math.max((X1 - X0) * dt / TM - .6, .8);
  h.t_h.forEach((a, i) => { const p = h.p_mmph[i]; if(p > 0) g += `<rect x="${sx(a).toFixed(1)}" y="${sy(p).toFixed(1)}" width="${bw.toFixed(1)}" height="${(Y1 - sy(p)).toFixed(1)}" fill="#b07a1c"><title>${a.toFixed(2)}–${(a + dt).toFixed(2)} h · ${fmt(p, 1)} mm/h</title></rect>`; });
  const pl = arr => `${sx(0)},${sc(0)} ` + h.t_h.map((a, i) => `${sx(a + dt).toFixed(1)},${sc(arr[i]).toFixed(1)}`).join(' ');
  g += `<polyline points="${pl(h.p_cum_mm)}" fill="none" stroke="#1f5f96" stroke-width="1.8"/><polyline points="${pl(h.exc_cum_mm)}" fill="none" stroke="#1f5f96" stroke-width="1.3" stroke-dasharray="4 3"/>`;
  g += `<text x="2" y="9" font-size="9" fill="#66717f">雨强 mm/h</text><text x="${X1 + 26}" y="9" text-anchor="end" font-size="9" fill="#1f5f96">累计 mm（虚线 = 净雨）</text><text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">时间 h</text>`;
  const n = nowFrame();
  if(n && V.mode === 'frame') g += `<line x1="${sx(n.f.t_h)}" x2="${sx(n.f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`;
  el.innerHTML = g;
  if(cap) cap.textContent = `${fmt(h.rain_mm)} mm / ${h.dur_h} h · 最大 ${h.dt_min} 分钟雨强 ${fmt(h.peak_mmph)} mm/h（${h.peak_t_h.toFixed(2)}–${(h.peak_t_h + dt).toFixed(2)} h）· 净雨 ${fmt(h.excess_mm, 1)} mm · ${h.source || ''}`;
}
function chartHs(){
  const box = $('chHs'), D = S.hs; if(!box) return;
  const hd = D?.hotspots, ids = (Array.isArray(hd) ? hd : Object.keys(hd || {})).filter(h => D[h]);   // hotspots 是 {热点: 属性} 或列表
  if(!D || !ids.length){ box.innerHTML = '<div class="m">这个情景没有热点过程线数据。</div>'; return; }
  const tt = D.t_h, ym = Math.max(0.6, Math.ceil(Math.max(...ids.flatMap(h => Object.values(D[h] || {}).flatMap(e => e.r50 || [0]))) * 11) / 10);
  const TM = Math.max(6, Math.ceil(tt[tt.length - 1] || 0)), TS = axStep(TM);
  const X0 = 30, X1 = 332, Y0 = 18, Y1 = 92, sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * Math.min(v, ym) / ym;
  const n = nowFrame(), b = baseState();
  box.innerHTML = ids.map(h => {
    let g = '';
    for(let a = 0; a <= ym + 1e-9; a += ym > 0.6 ? 0.2 : 0.1) g += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 4}" y="${sy(a) + 3}" text-anchor="end" font-size="9" fill="#66717f">${a.toFixed(1)}</text>`;
    for(let v = 0; v <= TM; v += TS) g += `<text x="${sx(v)}" y="${Y1 + 11}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    g += `<line x1="${X0}" x2="${X1}" y1="${sy(.3)}" y2="${sy(.3)}" stroke="#b91c1c" stroke-dasharray="4 3"/><text x="${X1}" y="${sy(.3) - 3}" text-anchor="end" font-size="9" fill="#b91c1c">0.30 m</text>`;
    haveStates().filter(x => D[h][x]).sort((a, c) => (a === V.st) - (c === V.st)).forEach(x => { const on = x === V.st;
      g += `<polyline points="${D[h][x].r50.map((v, i) => `${sx(tt[i]).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${stCol(x)}" stroke-width="${on ? 2.6 : 1.2}" opacity="${on ? 1 : .45}"/>`; });
    if(n && V.mode === 'frame'){ const i = tt.findIndex(v => Math.abs(v - n.f.t_h) < 1e-3), e = D[h][V.st];
      g += `<line x1="${sx(n.f.t_h)}" x2="${sx(n.f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`;
      if(e && i >= 0) g += `<circle cx="${sx(n.f.t_h)}" cy="${sy(e.r50[i])}" r="3.5" fill="${stCol(V.st)}" stroke="#fff" stroke-width="1.3"/>`; }
    const e = D[h][V.st] || {}, e0 = b ? D[h][b] || {} : {}, dd = e.peak_m != null && e0.peak_m != null ? e.peak_m - e0.peak_m : null;
    g += `<text x="${X0}" y="10" font-size="10" font-weight="600" fill="#1b2430">${esc(h)}</text><text x="${X1}" y="10" text-anchor="end" font-size="9.5" fill="#44505c">最深 ${fmt(e.peak_m, 2)} m · ≥0.30 m ${fmt(e.h_ge30, 1)} h · 末 ${fmt(e.end_m, 2)} m${dd != null && Math.abs(dd) >= .005 ? ` (${dd > 0 ? '+' : ''}${dd.toFixed(2)})` : ''}</text>`;
    return `<svg class="ch" viewBox="0 0 340 106" role="img" aria-label="${esc(h)}">${g}</svg>`; }).join('')
    + '<div class="m">曲线 = 热点 50 m 内最深的水深，每 5 分钟一个值；粗线是当前态，括号里是和对比基准的最深水深之差；红虚线 0.30 m。</div>';
}

// ---------------------------------------------------------------- 水深图层与播放
const imgCache = new Map();
function preload(url){ if(imgCache.has(url)) return; const im = new Image(); im.src = url; imgCache.set(url, im); }
function diffFor(){ const b = baseState(), D = S.cs?.diffs || []; return D.find(d => d.a === b && d.b === V.st) || D.find(d => d.b === V.st) || null; }
function draw(){
  const s = curSt(); if(!s) return;
  const i = frameIdx(), f = s.frames[i]; if(V.t == null) V.t = f.t_h;
  const dj = V.mode === 'diff' ? diffFor() : null;
  const url = S.base + (dj ? dj.png : V.mode === 'max' ? s.max_png : f.png), bnd = dj ? dj.bounds : s.bounds;
  if(!V.lay.water) V.lay.water = L.imageOverlay(url, bnd, {opacity: .82, interactive: false, zIndex: 450}).addTo(map);
  else { V.lay.water.setUrl(url); V.lay.water.setBounds(bnd); }
  if(V.mode === 'frame') for(let k = 1; k <= 5; k++) if(s.frames[i + k]) preload(S.base + s.frames[i + k].png);
  $('now').innerHTML = dj ? `<b>${esc(stLab(V.st))} − ${esc(stLab(dj.a))}</b>　变浅 ${fmt(dj.shallower_ha, 1)} ha · 变深 ${fmt(dj.deeper_ha, 1)} ha（|Δh| ≥ 5 cm）`
    : V.mode === 'diff' ? '<span class="m">这个态没有和所选基准的差值图</span>'
    : V.mode === 'max' ? '<b>全程最大水深</b>'
    : `<b>t = ${(+f.t_h).toFixed(2)} h</b>　积水面积(≥0.10 m) <b>${fmt(+f.wet10_ha || 0)}</b> ha　最大水深 <b>${fmt(+f.dmax_m, 2)}</b> m`;
  const rg = $('rng'); rg.max = String(s.frames.length - 1); rg.value = String(i);
  ['frame', 'max', 'diff'].forEach(m => $('m_' + m).classList.toggle('on', V.mode === m));
  chartWet(); chartRain(); chartHs();
}
function stop(){ if(V.timer){ clearInterval(V.timer); V.timer = null; } const b = $('play'); if(b) b.textContent = '▶'; }
function play(){
  const s = curSt(); if(!s) return; if(V.timer){ stop(); return; } V.mode = 'frame';
  if(frameIdx() >= s.frames.length - 1){ V.t = s.frames[0].t_h; draw(); }
  $('play').textContent = '❚❚';
  V.timer = setInterval(() => { const c = curSt(), k = frameIdx(); if(!c || k >= c.frames.length - 1){ stop(); return; } V.t = c.frames[k + 1].t_h; draw(); }, V.speed);
}
function selectState(st, first){
  stop(); V.st = st;
  document.querySelectorAll('.tabs [data-st]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.st === st)));
  const sel = $('baseSel'); if(sel) sel.value = V.base || '';
  $('kpis').innerHTML = kpis(); $('hsTab').innerHTML = hotspotTable(); $('facList').innerHTML = facList();
  $('desc').textContent = S.region === 'c1' ? ST_DESC[st] || '' : '';
  drawFacilities(); draw();
  if(first){ V.needFit = true; fitMap(); }
}
// 地图容器还没有尺寸（页面刚打开 / 面板在折叠）时 fitBounds 会落到 0 级，所以等容器有了尺寸再定位
function fitMap(){
  map.invalidateSize();
  const sz = map.getSize(); if(!V.needFit || !S || sz.x < 50 || sz.y < 50) return;
  try{ map.fitBounds(V.lay.dom ? V.lay.dom.getBounds().pad(.02) : L.latLngBounds(curSt().bounds), {animate: false}); V.needFit = false; }catch(e){}
}
new ResizeObserver(fitMap).observe($('map'));

// ---------------------------------------------------------------- 面板
function renderPanel(){
  const have = haveStates(), R = CAT.regions.find(r => r.id === S.region), cs = S.cs, st0 = S.stats?.L1 || {};
  if(!V.st || !have.includes(V.st)) V.st = have[have.length - 1];
  const sub = [`${S.mm} mm / ${S.h} h`, `${S.grid} m 网格`, st0.n_tri ? `${fmt(st0.n_tri)} 个三角单元` : '', st0.n_nodes ? `一维 ${fmt(st0.n_nodes)} 口井参与交换` : '',
    cs.sim_h || st0.end_h ? `模拟 ${cs.sim_h || st0.end_h} h` : `模拟 ${S.h + 4} h`].filter(Boolean).join(' · ');
  const kj = cs.koujing ? Object.entries(cs.koujing).map(([k, v]) => `<div><b>${esc(k)}</b>：${esc(v)}</div>`).join('') : '';
  $('panel').innerHTML = `
    <h2>${esc(R?.label || S.region)} · 一二维水动力</h2>
    <div class="m">${esc(sub)}${S.manifest.region ? '<br>' + esc(S.manifest.region) : ''}${S.manifest.job_id ? '<br>job_id：<code>' + esc(S.manifest.job_id) + '</code>' : ''}</div>
    <div class="tabs" role="tablist" style="grid-template-columns:repeat(${Math.min(have.length, 4)},1fr)">${have.map(s =>
      `<button role="tab" data-st="${s}" aria-selected="false" style="--c:${stCol(s)}"><b>${esc(stLab(s))}</b><small>${esc(S.region === 'c1' ? ST_SUB[s] || '' : '')}</small></button>`).join('')}</div>
    <div class="basebar"><span class="m">对比基准</span><select id="baseSel"><option value="">上一个态</option>${have.map(s => `<option value="${s}">${esc(stLab(s))}</option>`).join('')}</select></div>
    <div class="desc" id="desc"></div>
    <div class="kpis" id="kpis"></div>
    <h3>逐时播放</h3>
    <div class="play"><button id="play" aria-label="播放">▶</button><input id="rng" type="range" min="0" max="1" value="0" step="1" aria-label="时间"></div>
    <div id="now"></div>
    <div class="chips"><button class="chip" id="m_frame">逐帧</button><button class="chip" id="m_max">最大水深</button><button class="chip" id="m_diff">和对比基准的差</button></div>
    <svg id="chWet" class="ch" viewBox="0 0 340 158" role="img" aria-label="积水面积"></svg>
    <h3>降雨（雨型）</h3>
    <svg id="chRain" class="ch" viewBox="0 0 340 124" role="img" aria-label="雨型"></svg><div id="rainCap" class="m"></div>
    <h3>客户最关注的热点：水深过程</h3><div id="chHs"></div>
    <h3>各热点</h3><div id="hsTab"></div>
    <h3>这个态包含的设施</h3><div id="facList"></div>
    ${kj ? `<details><summary>统计口径</summary>${kj}</details>` : ''}`;
  document.querySelectorAll('.tabs [data-st]').forEach(b => b.onclick = () => selectState(b.dataset.st));
  $('baseSel').onchange = e => { V.base = e.target.value || null; selectState(V.st); };
  $('play').onclick = play;
  $('rng').oninput = e => { stop(); V.mode = 'frame'; const s = curSt(); if(s){ V.t = s.frames[+e.target.value].t_h; draw(); } };
  ['frame', 'max', 'diff'].forEach(m => $('m_' + m).onclick = () => { stop(); V.mode = m; draw(); });
}

// ---------------------------------------------------------------- 选择与加载
const sc = () => CAT.regions.find(r => r.id === SEL.region);
function fillSelect(el, items, val){
  el.innerHTML = items.map(i => `<option value="${esc(i.id)}">${esc(i.label)}</option>`).join('');
  el.disabled = items.length < 1; if(items.some(i => String(i.id) === String(val))) el.value = val;
  return el.value;
}
function syncSelects(){
  const R = sc();
  SEL.region = fillSelect($('selRegion'), CAT.regions, SEL.region);
  const grids = sc().grids;
  SEL.grid = fillSelect($('selGrid'), grids, SEL.grid);
  const G = grids.find(g => String(g.id) === String(SEL.grid));
  SEL.rain = fillSelect($('selRain'), G.rains, SEL.rain && G.rains.some(r => r.id === SEL.rain) ? SEL.rain : (G.rains.find(r => r.id === '61-2') || G.rains[0]).id);
}
async function load(){
  const my = ++token; stop();
  try{ history.replaceState(null, '', `#${new URLSearchParams(SEL)}`); }catch(e){}
  $('status').textContent = '加载中…';
  try{
    const d = await api(`/api/viewer/scenario?region=${encodeURIComponent(SEL.region)}&grid=${SEL.grid}&rain=${SEL.rain}`);
    if(my !== token) return;
    S = d; V.t = null; V.mode = 'frame'; V.base = null; mapClear();
    const dom = S.stats?.domain_wgs;
    if(dom) V.lay.dom = L.polygon(dom, {color: '#0f172a', weight: 2, fill: false, dashArray: '8,5', interactive: false}).addTo(map);
    renderPanel(); selectState(V.st, true);
    $('status').textContent = `数据来源 ${d.host || '—'} · 情景 ${d.tag} · 目录更新于 ${CAT.scanned_at}`;
  }catch(e){
    if(my !== token) return;
    S = null; mapClear(); $('panel').innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`; $('status').textContent = '';
  }
}
async function init(){
  const h = new URLSearchParams(location.hash.slice(1));
  try{ CAT = await api('/api/viewer/catalog'); }catch(e){ $('panel').innerHTML = `<div class="empty">读取成果目录失败：${esc(e.message)}</div>`; return; }
  if(!CAT.regions.length){ $('panel').innerHTML = '<div class="empty">没有找到任何推演成果。<br>请确认容器里 /data 挂载的是 NAS 成果目录（里面应有 &lt;主机名&gt;/runtime/console_data/flood2d_*），打开 /api/viewer/health 可以看扫描详情。</div>'; return; }
  SEL.region = h.get('region') || CAT.regions[0].id; SEL.grid = h.get('grid'); SEL.rain = h.get('rain');
  syncSelects();
  $('selRegion').onchange = () => { SEL.region = $('selRegion').value; SEL.grid = null; syncSelects(); load(); };
  $('selGrid').onchange = () => { SEL.grid = $('selGrid').value; syncSelects(); load(); };
  $('selRain').onchange = () => { SEL.rain = $('selRain').value; load(); };
  load();
}
init();
})();
