// 控制台逻辑：地图、三道防线页、对话抽屉与结果卡片、本体页、假设总账。
// 经典脚本（非 ES module），由 index.html 以 <script src> 引入；执行时机与作用域与原来的内联脚本一致。
// 词典在 i18n.js；接口清单见 GET /api。
(async function(){
  // ---------------- 语言 ----------------
  const U=new URLSearchParams(location.search);
  let lang = U.get('lang') || (function(){ try{ return localStorage.getItem('sw_lang'); }catch(e){ return null; } })() || 'zh';
  if(!window.SW_I18N[lang]) lang='zh';
  const t = (k, vars) => { let v = k.split('.').reduce((o,x)=>o&&o[x], window.SW_I18N[lang]); if (v==null) v = k.split('.').reduce((o,x)=>o&&o[x], window.SW_I18N.zh); if (typeof v==='string' && vars) v = v.replace(/\{(\w+)\}/g, (_,n)=>vars[n]??''); return v ?? k; };
  const F = (o, f) => (lang==='en' && o && o[f+'_en']!=null && o[f+'_en']!=='') ? o[f+'_en'] : (o ? o[f] : '');
  document.documentElement.lang = lang==='en' ? 'en' : 'zh';
  // 数据里只有中文、取值有限的枚举字段（坡度标记、分诊、审批标记、29.RB2 设施类型与来源…）：英文界面查这张表，查不到原样显示
  const ZH_EN = {'过平': 'too flat', '无标高': 'no invert levels', 'DTM地面坡度': 'DTM ground slope', '逆坡': 'adverse slope',
    '无需处置（已基本退水）': 'no action needed (largely drained)', '1.5 km 内无可接纳的排放去处：等待管网退水或长距离接力': 'no discharge point within 1.5 km: wait for the network to drain or relay over a long distance',
    '入流仍大于可派排量：先封路疏散，雨峰后再派': 'inflow still exceeds dispatchable capacity: close roads and evacuate first, dispatch after the rain peak',
    '可派泵（需接力排放）': 'pumps dispatchable (relay discharge needed)', '可派泵': 'pumps dispatchable', '需个案批准': 'case-by-case approval needed', '建议': 'proposed',
    '临时塘': 'temporary pond', '集水井': 'sump', '地下箱涵': 'underground box culvert', '已建成塘（BNS）': 'built pond (BNS)', 'PO 塘': 'PO pond', '已建成塘': 'built pond',
    'Parsons（交付模型原件）': 'Parsons (delivered model as supplied)', '本系统（从 L1 出发选址）': 'this system (sited from L1)', '本系统（在 Parsons 之上选址）': 'this system (sited on top of Parsons)',
    'RB_02 模型（按已建成处理，用户 2026-09-27 同意）': 'RB_02 model (treated as built, user agreed 2026-09-27)', 'RB_02 模型的 PO 塘（设计方未确认）': 'PO pond in the RB_02 model (designer unconfirmed)',
    '本系统（在 PO 塘方案之上、严格用地）': 'this system (on top of the PO pond scheme, strict land use)', '本系统（在 PO 塘方案之上、可借用未盖楼地块）': 'this system (on top of the PO pond scheme, may use unbuilt plots)',
    '本系统（从 L2 出发、严格用地）': 'this system (from L2, strict land use)', '本系统（从 L2 出发、可借用未盖楼地块）': 'this system (from L2, may use unbuilt plots)',
    '客户台账 stormwater_drainage.pond': 'client register stormwater_drainage.pond'};
  const zE = v => (lang === 'en' && v != null && ZH_EN[v] != null) ? ZH_EN[v] : v;

  // ---------------- 态的显示名（2026-09-28 用户定）----------------
  // L2+A（Mussafah 的 Parsons 方案 / 29.RB2 的 PO 塘方案）→ L2+planned；L2+A+B（在其上加本系统箱涵）→ L2+suggested；L2+B 不改。
  // 数据键（'L2+A'、'L2AB' …）不动：页面上所有**显示出来的文字**统一替换，按钮 / 地址参数里的键照旧，功能不受影响。
  const ST_RE = [[/L2\+A\+B/g, 'L2+suggested'], [/L2\+A\+C/g, 'L2+planned+C'], [/L2\+A(?![+A-Za-z0-9])/g, 'L2+planned']];
  const stDisp = v => ST_RE.reduce((a, [r, w]) => a.replace(r, w), v);
  const stKey = v => v == null ? v : String(v).replace(/L2\+suggested/g, 'L2+A+B').replace(/L2\+planned/g, 'L2+A');   // 显示名 → 键（地址 / 智能体传进来的）
  // 英文界面：模板里拼接用的中文全角标点（（）、，；：「」　等）统一换成英文标点，只在 lang==='en' 时生效，中文界面不动（2026-10-28 中英文全覆盖）
  const PUNC_RE = /[（）、，；：「」『』。！？　【】]|预案/;
  const PUNC_MAP = [[/\s*（/g, ' ('], [/）/g, ')'], [/、/g, ', '], [/，/g, ', '], [/；/g, '; '], [/：/g, ': '], [/[「『]/g, '"'], [/[」』]/g, '"'],
                    [/。/g, '. '], [/！/g, '! '], [/？/g, '? '], [/　/g, '  '], [/【/g, '['], [/】/g, ']'],
                    [/预案\s?A/g, 'Plan A']];   // 数据里反复出现的固定词（驻地名里的「预案A」）
  const enPunc = v => PUNC_MAP.reduce((a, [r, w]) => a.replace(r, w), v).replace(/^ \(/, '(').replace(/ +$/, m => v.endsWith(' ') ? m : '');
  const stFix = n => { const v = n.nodeValue; if(!v) return; const q = n.parentNode; if(q && /^(SCRIPT|STYLE|TEXTAREA)$/.test(q.nodeName)) return;
    let w = v.indexOf('L2+A') >= 0 ? stDisp(v) : v;
    if(lang === 'en' && PUNC_RE.test(w)) w = enPunc(w);
    if(w !== v) n.nodeValue = w; };
  const stWalk = el => { const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let n; while((n = w.nextNode())) stFix(n); };
  new MutationObserver(ms => ms.forEach(m => { if(m.type === 'characterData') stFix(m.target);
    else m.addedNodes.forEach(n => n.nodeType === 3 ? stFix(n) : n.nodeType === 1 && stWalk(n)); }))
    .observe(document.documentElement, {childList: true, subtree: true, characterData: true});
  document.addEventListener('DOMContentLoaded', () => stWalk(document.body));

  // ---------------- 数据装载（按页签懒加载） ----------------
  const cache = {};
  const load = async (k) => { if (cache[k]) return cache[k]; const r = await fetch('data/' + k, {cache:'no-store'}); if (!r.ok) throw new Error(k); cache[k] = await r.json(); return cache[k]; };
  const api = async (path) => { try { const r = await fetch(path, {cache:'no-store'}); if (!r.ok) return null; return await r.json(); } catch(e) { return null; } };
  // 长任务：带 async=1 请求；服务端有缓存直接给结果，否则给 job_id，这里每 1.5 s 轮询 /api/jobs/<id>；onTick(秒) 给页面报进度
  const apiJob = async (path, onTick) => { const sep = path.includes('?') ? '&' : '?'; let r = await api(path + sep + 'async=1'); const t0 = Date.now();
    while (r && r.job_id && r.status !== 'done' && r.status !== 'error') { await new Promise(res => setTimeout(res, 1500)); if (onTick) onTick(Math.round((Date.now() - t0) / 1000), r); r = await api('/api/jobs/' + r.job_id); }
    if (r && r.job_id) return r.status === 'done' ? r.result : { error: r.error || 'job failed' };
    return r; };
  const tickText = (el, base) => (sec) => { if (el) el.textContent = `${base} ${sec}s`; };

  // ---- 假设总账：顶栏徽章 + 面板（/api/assumptions）
  let ASM = null;
  async function loadAssumptions(){ let rp = ''; try{ rp = RP || ''; }catch(e){ return; }   // DOMContentLoaded 可能早于 RP 定义（let 暂时性死区），RP 定义后会再调一次
    const r = await api(`/api/assumptions?rp=${encodeURIComponent(rp)}`); ASM = (r && r.items) ? r : null; const b = document.getElementById('asbtn'); if (!b) return; if (!ASM) { b.hidden = true; return; } b.hidden = false; b.textContent = t('as.badge', {n: ASM.summary.n}); b.title = Object.entries(ASM.summary.by_level||{}).map(([k,v])=>`${t('as.'+k)} ${v}`).join(' · '); }
  function renderAssumptions(){ const p = document.getElementById('aspanel'); if (!p || !ASM) return; const EN = lang==='en'; const col = {simulated:'#b45309', assumed:'#7c3aed', placeholder:'#0891b2', unverified:'#64748b', missing:'#b91c1c'};
    p.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><b>${t('as.title')} · ${ASM.summary.n}</b><button class="btn" id="asclose" style="margin:0;padding:2px 8px">${t('as.close')}</button></div><div class="m" style="margin:4px 0 8px">${t('as.sub')}</div>`
      + `<table><tr><th>${t('as.level')}</th><th>${t('as.what')}</th><th>${t('as.fix')}</th></tr>${ASM.items.map(x=>`<tr><td><span class="chip" style="border-color:${col[x.level]||'#999'};color:${col[x.level]||'#333'}">${esc(t('as.'+x.level))}</span><div class="m">${esc(x.area||'')}</div></td><td>${esc(EN?(x.what_en||x.what):x.what)}${x.source?`<div class="m">${t('as.source')}: ${esc(String(x.source)).slice(0,120)}</div>`:''}${(x.affects||[]).length?`<div class="m">${t('as.affects')}: ${x.affects.map(esc).join(', ')}</div>`:''}</td><td class="m">${esc(x.fix||'—')}</td></tr>`).join('')}</table>`;
    p.hidden = false; const c = document.getElementById('asclose'); if (c) c.onclick = () => { p.hidden = true; }; }
  document.addEventListener('click', (e) => { const c = e.target.closest && e.target.closest('[data-asm]'); if (c) { renderAssumptions(); } });
  document.addEventListener('DOMContentLoaded', () => { const b = document.getElementById('asbtn'); if (b) b.onclick = () => { const p = document.getElementById('aspanel'); if (p && !p.hidden) { p.hidden = true; return; } renderAssumptions(); }; loadAssumptions(); });

  const fmt = (n, d=0) => (n==null||isNaN(n)) ? '—' : Number(n).toLocaleString(lang==='en'?'en-GB':'zh-CN', {maximumFractionDigits:d});
  // 单引号也得转：有地方把结果塞进单引号属性里，而阿布扎比真实片区名带撇号的不少
  // （Al Maqta'、Hadbat Al Za'faranah、Al 'Adlah），属性会在撇号处断掉，值可控时还能挂事件处理器。2026-09-23 修。
  const esc = s => String(s??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // 散文字段里的 **粗体** 要真渲染出来。写 ** 是为了标出要害（「未经标定」「不是整个穆萨法」），
  // 原样显示成星号反而成了噪声。raw=true 给 i18n 里已可信的串用，不再转义。
  const mdb = (v, raw) => (raw ? String(v ?? '') : esc(v)).replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  const hrs = m => m==null ? '—' : (Math.round(m/60*10)/10)+' h';
  const vol = v => v==null ? '—' : (lang==='en' ? (Math.abs(v)>=1e6 ? (v/1e6).toFixed(2)+' M m³' : Math.round(v/1e3)+' k m³') : (Math.abs(v)>=1e8 ? (v/1e8).toFixed(2)+' 亿 m³' : (v/1e4).toFixed(1)+' 万 m³'));
  const aed = v => v==null ? '—' : (Math.abs(v)>=1e6 ? (v/1e6).toFixed(2)+' M' : (Math.abs(v)>=1e4 ? Math.round(v/1e3)+' k' : fmt(Math.round(v))));
  let TLS=null;   // 第三道地图时间轴的状态（切页签时复位）
  const kindColor = {'核实终点（疑似漏登出水口/泵站）':'#111827','核实拓扑（疑似流向/连接错误）':'#374151','核实拓扑（疑似缺失连接）':'#4b5563','数据治理/补测':'#6b7280',
    '扩管改造':'#dc2626','新增调蓄塘/池':'#0891b2','新增渗井群':'#0e7490','出水口增泵/改闸':'#7c3aed','泵站增容校核':'#6d28d9','连通至有出路管网':'#16a34a','就地消纳（渗井/塘）':'#15803d','雨季前维护排序':'#d97706','事件前临时泵位':'#db2777'};
  const whenOrder = ['立即','雨季前','事件前','并入在批','资本计划'];
  const stColor = {'就绪':'var(--ok)','部分':'var(--warn)','缺':'var(--bad)'};
  // 没跑 swbrain.run（要连客户的 pipe / makani 库）时没有 summary.json：只看一二维热点群结果，直接进 C1 页签（README「一二维热点群计算」）
  let S0 = null; try { S0 = await load('summary.json'); } catch(e) { S0 = null; }
  const C1_ONLY = !S0;
  if(C1_ONLY){ S0 = {summary:{'生成时间':'—','管段数':0}, scenarios:{'—':0}, standard_plan:{}}; if(!U.get('tab')){ U.set('tab','line2p'); U.set('v','c1'); } }
  const S = S0.summary, SCEN = S0.scenarios, SP = S0.standard_plan || {}; const main = Object.keys(SCEN)[0];
  const scn = n => lang==='en' ? ((S.scenario_en||{})[n] || n) : n;
  const health = await api('/api/health');
  let WHO = null; try { const w = await api('/api/whoami'); WHO = (w && w.who) ? w : null; } catch(e) { WHO = null; }   // 当前登录人（统一身份；共享口令模式下没有）
  let scen = null;
  const liveEl=document.getElementById('live');
  // 「展开全部」：长表默认只给前 N 行
  window._more = window._more || {};
  const moreBtn = (key, n, total) => total>n ? `<button class="more" data-more="${key}">${window._more[key]?t('ui.less'):t('ui.more',{n:total-n})}</button>` : '';
  const slice = (key, arr, n) => window._more[key] ? arr : arr.slice(0,n);
  const bindMore = root => root.querySelectorAll('[data-more]').forEach(b=>b.onclick=()=>{ window._more[b.dataset.more]=!window._more[b.dataset.more]; render(); });
  function chrome(){
    document.title = t('title'); document.getElementById('h1').textContent = t('title');
    document.getElementById('runinfo').textContent = `${t('generated')} ${S['生成时间']} · ${fmt(S['管段数'])} ${t('reaches')} · ` + (health ? `${t('online')} ${health.loaded_at}` : t('offline'));
    document.getElementById('runinfo').title = `${t('designRain')} ${Object.entries(SCEN).map(([k,v])=>scn(k)+((S.scenario_rp||{})[k]?'('+S.scenario_rp[k]+t('yr')+')':'')+' '+v).join(' / ')} mm/h`;
    document.getElementById('rplabel').textContent = t('rp.label'); renderRpChips();
    if(!RPINFO){ liveEl.textContent = health ? t('rp.hint') : t('liveOff'); liveEl.className = 'live' + (health ? ' on' : ''); }
    document.getElementById('rpinfo').title = t('rp.explain');
    document.getElementById('langbtn').textContent = lang==='en' ? '中文' : 'EN';
    renderUser();
    const ab=document.getElementById('agentbtn'); if(ab){ ab.textContent=t('ag.btn'); ab.title=t('ag.btnTip'); ab.onclick=()=>{ const a=document.getElementById('agent'); a.hidden?openAgent():closeAgent(); }; }
    const ac=document.getElementById('agclose'); if(ac) ac.onclick=closeAgent; const asd=document.getElementById('agsend'); if(asd) asd.onclick=()=>sendAgent(); const ai=document.getElementById('agin'); if(ai) ai.onkeydown=e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); sendAgent(); } };
    if(!document.getElementById('agent').hidden){ document.getElementById('agtitle').textContent=t('ag.title'); document.getElementById('agsend').textContent=t('ag.send'); document.getElementById('agin').placeholder=t('ag.placeholder'); }
    const sc=document.getElementById('sidecollapse'); if(sc) sc.title=t('ui.collapse'); const se=document.getElementById('sideexpand'); if(se){ se.textContent='▸ '+t('ui.expand'); } const rz=document.getElementById('agrsz'); if(rz) rz.title=t('ui.dragResize');
    const adb=document.getElementById('adminbtn'); if(adb){ adb.textContent=t('bi.admin'); adb.title=t('bi.adminTip'); }
    const rb=document.getElementById('reportbtn'); if(rb){ rb.textContent=t('ui.report'); rb.title=t('ui.reportTip'); rb.onclick=()=>{ const k=RP||(health&&health.active_rp)||'10'; window.open('/api/report?rp='+encodeURIComponent(k)+'&lang='+lang,'_blank'); }; }
    document.querySelectorAll('nav button').forEach(b=>{ if(!b.dataset.t) return; const s=b.querySelector('span:last-child'); if(s&&b.querySelector('.dot')) s.textContent=t('tabs.'+b.dataset.t); else b.textContent=t('tabs.'+b.dataset.t); });
    document.querySelectorAll('#layers label').forEach(l=>l.querySelector('span').textContent=t('layers.'+l.querySelector('input').dataset.l));
    navInit();
    document.getElementById('layerslabel').textContent=t('ui.layers'); layersCount();
  }
  document.getElementById('langbtn').onclick=()=>{ lang = lang==='en'?'zh':'en'; try{ localStorage.setItem('sw_lang', lang); }catch(e){} document.documentElement.lang = lang==='en'?'en':'zh'; chrome(); renderLegend(); render(); if(layers.zones) layers.zones.eachLayer(l=>l.setTooltipContent(zoneTip(l.feature.properties))); };

  // 当前登录人：统一身份下显示姓名、主体号、职能与注销入口；共享口令模式没有主体，整块收起（2026-10-01）
  function renderUser(){
    const el = document.getElementById('userbtn'); if(!el) return;
    const who = WHO && WHO.who;
    if(!who || ((WHO.mode||'') !== 'sdip' && !(who.username || who.principal_id))){ el.hidden = true; return; }
    const FN = {'SWBRAIN.VIEW': t('who.view'), 'SWBRAIN.FIELD': t('who.field'), 'SWBRAIN.ALERT': t('who.alert'), 'SWBRAIN.PLAN': t('who.plan'), 'SWBRAIN.ADMIN': t('who.admin'), 'SWBRAIN.FEED': t('who.feed')};
    const fns = (who.functions||[]).map(c=>FN[c]||c).join('、') || t('who.none');
    const name = who.name || who.username || who.principal_id;
    el.hidden = false; el.style.cursor = 'pointer';
    el.innerHTML = `<b>${esc(name)}</b><span class="fns">${esc(fns)}</span><a href="#" id="userauth">${t('who.logout')}</a>`;
    el.title = `${t('who.tip')}\n${t('who.pid')} ${who.principal_id}${who.username?' · '+who.username:''}${who.tenant_id?' · '+t('who.tenant')+' '+who.tenant_id:''}\n${t('who.fns')} ${(who.functions||[]).join('、')||t('who.none')}`;
    el.querySelector('#userauth').onclick = async ev => { ev.preventDefault();
      if(!confirm(t('who.logoutAsk'))) return;
      try{
        const r = await api('/auth/logout', {});
        if(r && r.redirect) location.href = r.redirect; else location.reload();
      }catch(e){ location.href = '/auth/logout'; }
    };
  }


  // ---------------- 地图（Canvas 渲染） ----------------
  // 全部矢量层共用**一块**画布：Leaflet 的每个 canvas 渲染器都是一整张铺满地图的 <canvas>，上面那块会把点击整个吞掉，
  // 下面画布里的面（汇水区等）就点不到了（2026-09-28）。所以地图默认渲染器就用这一块，别的图层不指定时也落到这里。
  const canvas = L.canvas({padding:0.3});
  const map = L.map('map', {renderer: canvas, zoomControl:true}).setView([24.42, 54.55], 10);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© OpenStreetMap'}).addTo(map);
  window._swMap = map;      // 自动化点击测试用（scratchpad/clicktest.py）
  const riskColor = r => r==null?'#cbd5e1': r>=55?'#b91c1c':r>=40?'#ea580c':r>=25?'#f59e0b':r>=10?'#60a5fa':'#bfdbfe';
  const ratioColor = r => r>=4?'#7f1d1d':r>=2?'#dc2626':r>=1.3?'#f97316':'#fbbf24';
  const resStyle = {'仍超载管段':{color:'#0e7490',dash:'7 5'}, '仍高潮暴露出水口':{color:'#2563eb'}, '仍疑似漏登终点':{color:'#111827'}};
  const layers = {}; let zonesGJ; let zoneFillNormal = true;
  const zoneTip = p => `${p.zone_name||(lang==='en'?'Unzoned':'未分区')} · ${t('riskLabel')} ${p.risk_score??t('tooSmall')}`;
  const zoneStyle = f => ({color:'#475569',weight:1,fillColor:riskColor(f.properties.risk_score),fillOpacity:f.properties.risk_score==null?.06:(zoneFillNormal?.28:.08)});
  const cbOf = k => document.querySelector(`#layers input[data-l="${k}"]`);
  const on = k => { const c=cbOf(k); return !!(c&&c.checked); };
  function layersCount(){ const n=[...document.querySelectorAll('#layers input')].filter(c=>c.checked).length; document.getElementById('layerscount').textContent=`(${n})`; }
  document.getElementById('layershead').onclick=()=>document.getElementById('layers').classList.toggle('collapsed');
  async function buildLayers(){
    zonesGJ = await load('zones.geojson'); const propsGJ = await load('proposals.geojson');
    layers.zones = L.geoJSON(zonesGJ, {renderer:canvas, style:zoneStyle, onEachFeature:(f,l)=>{ l.on('click', ()=>showZone(f.properties.zone_name)); l.bindTooltip(zoneTip(f.properties)); }}).addTo(map);
    layers.props = L.geoJSON(propsGJ, {renderer:canvas, pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:6,color:'#fff',weight:1,fillColor:kindColor[f.properties.kind]||'#333',fillOpacity:.95}),
      onEachFeature:(f,l)=>{ const p=f.properties; l.bindTooltip(()=>`#${p.rank} ${F(p,'kind')}`); l.on('click',()=>selectProposal(p.pid,false)); }}).addTo(map);
    // 初次定位到全酋范围。但页签若已经把视野定到某个汇水区，就别抢——
    // buildLayers 要先拉两个 geojson，落在页签的 fitBounds 之后，会把视野拽回全酋。
    if(!window._viewOwned){ try { map.fitBounds(layers.zones.getBounds(), {padding:[10,10]}); } catch(e){} }
    const bott = await load('bottlenecks.geojson');
    layers.bott = L.geoJSON(bott, {renderer:canvas, style:f=>({color:ratioColor(f.properties['ratio_'+main]||0),weight:2.5,opacity:.9}),
      onEachFeature:(f,l)=>{const p=f.properties;l.bindPopup(()=>`<h4>${t('pipe')} ${esc(p.unitid||p.gid)}</h4><div class="m">${esc(p.zone_name||'')}</div><div class="kv"><span>DN</span><span>${p.pipe_diameter}</span><span>${t('slope')}</span><span>${(p.slope||0).toFixed(4)} (${esc(zE(p.slope_flag))})</span><span>${t('fullCap')}</span><span>${(p.q_full||0).toFixed(3)} m³/s</span><span>${t('catch')}</span><span>${((p.area_m2||0)/1e4).toFixed(1)} ha · ${t('upInlets')} ${Math.round(p.n_inlets_up||0)}</span><span><b>${t('icapShort')}</b></span><span><b>${(p.i_cap_mmh||0).toFixed(0)} mm/h</b></span>`+Object.keys(SCEN).map(k=>`<span>${scn(k)} ${t('ratio')}</span><span>${(p['ratio_'+k]||0).toFixed(2)}</span>`).join('')+`</div>`)}}).addTo(map);
    layers.res = null;
    document.querySelectorAll('#layers input').forEach(cb=>cb.onchange=()=>toggleLayer(cb.dataset.l, cb.checked));
    // ?lay=catch,pond 预勾图层，并定位到最后一个——验收和分享定点视图都用得上
    const want=(U.get('lay')||'').split(',').map(s=>s.trim()).filter(Boolean);
    for(const k of want){ if(cbOf(k)) await toggleLayer(k, true); }
    // ?at=lat,lon&z=17 指定视野优先于图层包络（和三态页一致，便于分享定点视图）
    const at=(U.get('at')||'').split(','), z=parseFloat(U.get('z'));
    if(at.length===2 && isFinite(+at[0]) && isFinite(+at[1]) && isFinite(z)){
      window._viewOwned=true;
      const go=()=>{ try{ map.invalidateSize(); map.setView([+at[0],+at[1]], z); }catch(e){} };
      setTimeout(go,60); setTimeout(go,700); renderLegend(); return;
    }
    const last=want.filter(k=>layers[k]).pop();
    if(last){ window._viewOwned=true;
      const fit=()=>{ try{ map.invalidateSize(); map.fitBounds(layers[last].getBounds().pad(.2), {maxZoom:15}); }catch(e){} };
      setTimeout(fit,60); setTimeout(fit,700); }
    renderLegend();
  }
  // ---- 客户 flood 库的三层。都是面，点开有完整属性。
  // 塘是 **L2**（新建）不是 L1：判据是客户自己的 informigration —— 管线/雨水口/出水口
  // 八九成标 EXISTING，而 23 个塘一个都没标，全挂在道路工程下（2021–2023 建成）。
  // L1 的既有水体在 sw_reservoirstructure 里，单独一层。
  // flood 库这三层都是全市范围，而推演只在 Mussafah_00 / Shaliela / RB3 三个区里做。
  // 区外的要素仍然画出来（客户要看得到全市有什么），但淡化 + 虚线，一眼分得开。
  const inScope = f => !!(f && f.properties && f.properties.in_scope);
  const dim = (st, f) => inScope(f) ? st
    : Object.assign({}, st, {opacity:(st.opacity||1)*0.38, fillOpacity:(st.fillOpacity||0)*0.3,
                             weight:Math.max(1,(st.weight||1)-0.6), dashArray:'3,4'});
  const scopeRow = ly => { if(!ly) return '';
    const all=ly.getLayers(), n=all.filter(l=>inScope(l.feature)).length;
    return `<div class="row"><span class="sw" style="background:transparent;border:0"></span>`
      +`${t('lay.inScope')} <b>${n}</b> / ${t('lay.city')} ${all.length}</div>`; };
  const scopeKv = p => `<span>${t('lay.inScope')}</span><span>`
    +(p.in_scope ? `<b>${t('lay.yes')}</b>`
                 : `${t('lay.no')}${p.dist_m!=null?` · ${t('lay.outBy')} ${fmt(+p.dist_m,0)} m`:''}`)
    +`</span>`;

  // 汇水区属性框：全部原始字段 + 这个汇水区的全市 L1 / L2（一维洪水量：city_gap.json；一二维：flood2d_city2d/zones_catch.json）
  let CATX = null;
  function catchPopup(p){
    const FN = {id: 'cp.fId', label: 'cp.fLabel', parts: 'lay.parts', dist_m: 'cp.fDist', area_ha: 'cp.fArea', in_scope: 'lay.inScope'};
    const raw = Object.entries(p).map(([k, v]) => `<span>${FN[k] ? t(FN[k]) : esc(k)} <span class="m">${esc(k)}</span></span><span>${v == null || v === '' ? '—' : (typeof v === 'boolean' ? (v ? t('lay.yes') : t('lay.no')) : esc(isFinite(+v) && String(v).length > 8 ? fmt(+v, 2) : v))}</span>`).join('');
    const one = CATX?.one?.[p.label], two = CATX?.two?.[p.label];
    let h = `<h4>${t('layers.catch')} ${esc(p.label || p.id || '')}</h4><div class="m" style="margin:2px 0 4px">${t('cp.raw')}</div><div class="kv">${raw}</div>`;
    if(one) h += `<div class="m" style="margin:8px 0 4px"><b>${t('cp.one')}</b></div><div class="kv">`
      + `<span>${t('cp.flood')} L1</span><span>${fmt(one.L1_m3, 0)} m³</span><span>${t('cp.flood')} L2</span><span>${fmt(one.L2_m3, 0)} m³</span>`
      + `<span>${t('cp.diff')}</span><span><b style="color:${one.diff_m3 >= 1 ? 'var(--ok)' : 'var(--muted)'}">${one.diff_m3 >= 1 ? '−' + fmt(one.diff_m3, 0) : '0'} m³</b></span></div>`;
    if(two) h += `<div class="m" style="margin:8px 0 4px"><b>${t('cp.two')}</b></div><div class="kv">`
      + `<span>${t('cp.a30')}</span><span>${fmt(two.L1_ge_0p30_ha, 1)} → <b>${fmt(two.L2_ge_0p30_ha, 1)}</b> ha</span>`
      + `<span>${t('cp.a10')}</span><span>${fmt(two.L1_ge_0p10_ha, 1)} → <b>${fmt(two.L2_ge_0p10_ha, 1)}</b> ha</span>`
      + `<span>${t('cp.a80')}</span><span>${fmt(two.L1_ge_0p80_ha, 1)} → <b>${fmt(two.L2_ge_0p80_ha, 1)}</b> ha</span>`
      + `<span>${t('cp.dmax')}</span><span>${fmt(two.L1_max_m, 2)} → <b>${fmt(two.L2_max_m, 2)}</b> m</span>`
      + `<span>${t('cp.sh')}</span><span>${fmt(two.shallower_ha, 1)} ha</span></div>`;
    if(!one && !two) h += `<div class="m" style="margin-top:6px">${t('cp.none')}</div>`;
    return h + `<div class="m" style="margin-top:6px">${t('cp.note')}</div>`;
  }
  async function ensureCatchLayer(){
    const gj = await load('catchments_sw.geojson'); if(!gj) return;
    if(!CATX){ const cg = await load('city_gap.json').catch(() => null), zc = await load('flood2d_city2d/zones_catch.json').catch(() => null);
      CATX = {one: Object.fromEntries((cg?.by_catchment || []).map(r => [r.label, r])), two: zc || {}}; }
    layers.catch = L.geoJSON(gj, {renderer:canvas,
      style:f=>inScope(f)
        ? {color:'#7c3aed', weight:2.6, opacity:1,  fillColor:'#7c3aed', fillOpacity:.10}
        : {color:'#7c3aed', weight:1.2, opacity:.35, fillColor:'#7c3aed', fillOpacity:.02, dashArray:'3,4'},
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        l.bindTooltip(()=>`${esc(p.label||p.id||'')} · ${fmt(+p.area_ha||0,0)} ha`
          +(p.in_scope?` · ${t('lay.inScope')}`:''));
        l.bindPopup(()=>catchPopup(p) + (gj.source ? `<div class="m">${esc(F(gj, 'source'))}</div>` : ''), {maxWidth: 360}); }});
  }
  // 汇水区一旦打开就保持在最上层：全市 1 km 方格、各视图的面和它画在同一块画布上，后画的会把点击接走
  let catchRaise = null;
  map.on('layeradd', e => {
    if(!layers.catch || !map.hasLayer(layers.catch) || e.layer === layers.catch || layers.catch.hasLayer(e.layer) || catchRaise) return;
    catchRaise = setTimeout(() => { catchRaise = null; try{ if(layers.catch && map.hasLayer(layers.catch)) layers.catch.bringToFront(); }catch(_){} }, 0); });
  // ---- 汇水区名称注记（2026-09-28 用户：找汇水区太不方便）：打开图层就常显名称，缩放到一定级别才显示，免得满屏字
  const LBL_Z = {catch: 10, kcatch: 12};
  const lblLayers = {};
  function catchLabels(k){
    const src = layers[k]; if(!src) return;
    if(!lblLayers[k]){
      const g = L.layerGroup(), seen = new Set(), placed = {};
      const items = []; src.eachLayer(l => items.push(l.feature?.properties || {}));
      items.sort((x, y) => (+y.area_ha || 0) - (+x.area_ha || 0));          // 大的先标：同名相邻的小块（同一分区拆成几块）就不再重复标
      items.forEach(p => { const lab = p.label;
        if(!lab || !p.label_ll || seen.has(lab + p.label_ll)) return; seen.add(lab + p.label_ll);
        const ll = L.latLng(p.label_ll);
        if((placed[lab] || []).some(q => q.distanceTo(ll) < 800)) return; (placed[lab] = placed[lab] || []).push(ll);
        L.marker(p.label_ll, {interactive: false, keyboard: false,
          icon: L.divIcon({className: 'catchlbl ' + k, html: `<span>${esc(lab)}</span>`, iconSize: null})}).addTo(g); });
      lblLayers[k] = g;
    }
    const want = map.hasLayer(src) && map.getZoom() >= LBL_Z[k];
    if(want && !map.hasLayer(lblLayers[k])) lblLayers[k].addTo(map);
    if(!want && map.hasLayer(lblLayers[k])) map.removeLayer(lblLayers[k]);
    lblDeclutter();
  }
  // 注记避让：互相压住的只留先画的那个（客户汇水区优先于 KMZ），放大后自然都露出来
  function lblDeclutter(){
    const boxes = [];
    ['catch', 'kcatch'].forEach(k => { const g = lblLayers[k]; if(!g || !map.hasLayer(g)) return;
      g.eachLayer(m => { const el = m._icon && m._icon.firstChild; if(!el) return;
        el.style.visibility = 'visible'; const r = el.getBoundingClientRect();
        const hit = boxes.some(b => r.left < b.right + 2 && r.right > b.left - 2 && r.top < b.bottom + 1 && r.bottom > b.top - 1);
        if(hit) el.style.visibility = 'hidden'; else boxes.push(r); }); });
  }
  map.on('zoomend', () => ['catch', 'kcatch'].forEach(catchLabels));
  map.on('moveend', lblDeclutter);

  // ---- 客户邮件附件里的 4 个汇水区 KMZ（tools/import_client_kmz.py）
  const KC_COL = {'Al Wathba': '#0e7490', 'Mussafah (Mu)': '#b45309', 'Shahamah / Zayed': '#15803d', 'Island': '#be185d'};
  const kvRows = o => Object.entries(o).filter(([k, v]) => k.startsWith('a_') && v != null && v !== '')
    .map(([k, v]) => `<span class="m">${esc(k.slice(2))}</span><span>${esc(v)}</span>`).join('');
  async function ensureKcatchLayer(){
    const gj = await load('catchments_kmz.geojson'); if(!gj) return;
    layers.kcatch = L.geoJSON(gj, {renderer: canvas,
      style: f => { const c = KC_COL[f.properties.group] || '#475569'; return {color: c, weight: 1.6, opacity: .9, fillColor: c, fillOpacity: .06}; },
      onEachFeature: (f, l) => { const p = f.properties || {};
        l.bindTooltip(() => `${esc(p.label)} · ${fmt(+p.area_ha || 0, 0)} ha`);
        l.bindPopup(() => `<h4>${t('kc.title')} ${esc(p.label)}</h4><div class="m">${esc(p.group)} · ${esc(p.file)}</div><div class="kv">`
          + `<span>${t('kc.name')}</span><span>${esc(p.name || '—')}</span>`
          + `<span>${t('kc.lsrc')}</span><span>${t('kc.src_' + p.label_src)}</span>`
          + `<span>${t('lay.area')}</span><span>${fmt(+p.area_ha || 0, 1)} ha</span>`
          + `<span>${t('kc.pts')}</span><span>${esc((p.points_inside || []).join('、') || '—')}</span>`
          + kvRows(p) + `</div><div class="m">${esc(F(gj, 'source') || '')}</div>`, {maxWidth: 380, maxHeight: 420}); }});
  }
  // ---- 汇水区更正 RB-3（RAHA BEACH OUTFALL-3）：客户原 catchment 库漏了 ADM-94 / 192 / 487，按客户 QGIS 截图描绘（tools/rb3_trace_export.py）
  const RB3_KEYS = ['OBJECTID', 'Title', 'Old_Number', 'New_Number', 'AssignedTo', 'CatchBasin', 'Inlet', 'Sub_Catchm', 'Status', 'Model_Name', 'Model_Tota', 'Model_Stat', 'HYDROID', 'Shape_Leng', 'Shape_Area'];
  async function ensureRb3Layer(){
    const gj = await load('rb3_catch.geojson'); if(!gj) return;
    const g = L.featureGroup();
    L.geoJSON(gj, {style: {color: '#dc2626', weight: 2.6, opacity: 1, fillColor: '#ef4444', fillOpacity: .18, dashArray: '7,4'},
      onEachFeature: (f, l) => { const p = f.properties || {};
        l.bindTooltip(() => `<b>RB-3</b> · RAHA BEACH OUTFALL-3 · ${fmt((+p.trace_area || 0) / 1e4, 0)} ha`, {sticky: true});
        l.bindPopup(() => `<h4>${t('rb3.title')}</h4><div class="m">${t('rb3.why')}</div><div class="kv">`
          + RB3_KEYS.filter(k => p[k] != null && p[k] !== '').map(k => `<span class="m">${esc(k)}</span><span>${esc(typeof p[k] === 'number' && !Number.isInteger(p[k]) ? fmt(p[k], 1) : p[k])}</span>`).join('')
          + `<span class="m">${t('rb3.traced')}</span><span>${fmt((+p.trace_area || 0) / 1e6, 3)} km² · ${fmt((+p.trace_leng || 0) / 1000, 1)} km</span>`
          + `<span class="m">${t('rb3.hs')}</span><span>${esc(String(p.hs_inside || '').replaceAll(';', '、'))}</span></div>`
          + `<div class="m" style="margin-top:4px">${esc(F(gj, 'source') || '')}</div><div class="m">${t('rb3.acc')}</div>`, {maxWidth: 380, maxHeight: 440}); }}).addTo(g);
    // 面里的 3 个热点，标出来
    const hsj = await load('hotspots.geojson').catch(() => null);
    const want = ['ADM-94', 'ADM-192', 'ADM-487'];
    (hsj?.features || []).filter(f => want.includes(f.properties.hotspot_id)).forEach(f => { const [lo, la] = f.geometry.coordinates;
      L.circleMarker([la, lo], {radius: 6, color: '#fff', weight: 1.6, fillColor: '#dc2626', fillOpacity: 1}).bindTooltip(`<b>${esc(f.properties.hotspot_id)}</b> · ${esc(f.properties.criticality || '')}`, {permanent: true, direction: 'right', offset: [6, 0], className: 'rb3lbl'}).addTo(g); });
    layers.rb3 = g;
  }
  // ---- C1 汇水区（客户单元）：客户 Catchment 图层的 RB-3 / MBZ / ADGC-10 三个面（按截图描，tools/c1_units_export.py）
  const C1U_COL = {'RB-3': '#dc2626', 'MBZ': '#2563eb', 'ADGC-10': '#16a34a'};
  async function ensureC1uLayer(){
    const gj = await load('c1_units.geojson'); if(!gj) return;
    const g = L.featureGroup();
    if(gj.merged) L.geoJSON(gj.merged, {interactive: false, style: {color: '#0f172a', weight: 3, fill: false}}).addTo(g);
    L.geoJSON(gj, {style: f => { const c = C1U_COL[f.properties.New_Number] || '#475569'; return {color: c, weight: 2, dashArray: '6,4', fillColor: c, fillOpacity: .14}; },
      onEachFeature: (f, l) => { const p = f.properties || {};
        l.bindTooltip(() => `<b>${esc(p.New_Number)}</b> · ${esc(p.Title)} · ${fmt((+p.trace_area || 0) / 1e4, 0)} ha`, {sticky: true});
        l.bindPopup(() => `<h4>${esc(p.New_Number)} · ${esc(p.Title)}</h4><div class="m">${t('c1u.pop')}</div><div class="kv">`
          + ['OBJECTID', 'Old_Number', 'New_Number', 'Model_Name', 'CatchBasin', 'Inlet', 'Sub_Catchm', 'HYDROID', 'Shape_Area', 'Shape_Leng'].filter(k => p[k] != null)
            .map(k => `<span class="m">${esc(k)}</span><span>${esc(typeof p[k] === 'number' && !Number.isInteger(p[k]) ? fmt(p[k], 1) : p[k])}</span>`).join('')
          + `<span class="m">${t('rb3.traced')}</span><span>${fmt((+p.trace_area || 0) / 1e6, 2)} km²</span><span class="m">${t('rb3.hs')}</span><span>${esc(String(p.hs_inside || '—').replaceAll(';', '、'))}</span>`
          + `<span class="m">${t('c1u.how')}</span><span>${esc(F(p, 'how') || '')}</span></div><div class="m">${esc(F(gj, 'source') || '')}</div>`, {maxWidth: 380, maxHeight: 440}); }}).addTo(g);
    const hsj = await load('hotspots.geojson').catch(() => null);
    const want = ['ADM-21', 'ADM-90', 'ADM-94', 'ADM-104', 'ADM-192', 'ADM-202', 'ADM-486', 'ADM-487'];
    (hsj?.features || []).filter(f => want.includes(f.properties.hotspot_id)).forEach(f => { const [lo, la] = f.geometry.coordinates;
      L.circleMarker([la, lo], {radius: 5, color: '#fff', weight: 1.4, fillColor: '#0f172a', fillOpacity: 1}).bindTooltip(esc(f.properties.hotspot_id), {permanent: true, direction: 'right', offset: [5, 0], className: 'rb3lbl'}).addTo(g); });
    layers.c1u = g;
  }
  // ---- 模型汇水区：客户 22 个水力模型的子汇水区拼成（2026-09-30 用户定以此为准；tools/model_units_build.py）
  const MU_COL = {RB3: '#dc2626', Musaffah_RD09: '#2563eb', B_C: '#16a34a'};
  async function ensureMuLayer(){
    const gj = await load('model_units.geojson'); if(!gj) return;
    const dm = await load('c1_model_domain.geojson').catch(() => null);
    const g = L.featureGroup();
    L.geoJSON(gj, {filter: f => f.properties.level === 'model', style: f => { const c = MU_COL[f.properties.model] || '#64748b'; return {color: c, weight: MU_COL[f.properties.model] ? 2 : 1.2, fillColor: c, fillOpacity: MU_COL[f.properties.model] ? .14 : .06}; },
      onEachFeature: (f, l) => { const p = f.properties || {};
        l.bindTooltip(() => `<b>${esc(p.name)}</b> · ${t('mu.model')} · ${fmt((+p.area_ha || 0), 0)} ha · ${fmt(+p.n_sub || 0, 0)} ${t('mu.subs')}`, {sticky: true});
        l.bindPopup(() => `<h4>${esc(p.name)}</h4><div class="m">${t('mu.pop')}</div><div class="kv"><span class="m">${t('mu.subs')}</span><span>${fmt(+p.n_sub || 0, 0)}</span>`
          + `<span class="m">${t('lay.area')}</span><span>${fmt(+p.area_ha || 0, 1)} ha</span><span class="m">${t('rb3.hs')}</span><span>${esc(String(p.hotspots || '—').replaceAll(';', '、'))}</span></div>`, {maxWidth: 380}); }}).addTo(g);
    L.geoJSON(gj, {filter: f => f.properties.level === 'outfall', style: {color: '#15803d', weight: 1.4, dashArray: '4,4', fill: false},
      onEachFeature: (f, l) => l.bindTooltip(`<b>${esc(f.properties.name)}</b> · ${t('mu.outfall')} · ${fmt(+f.properties.area_ha || 0, 0)} ha`, {sticky: true})}).addTo(g);
    if(dm) L.geoJSON(dm, {interactive: false, style: {color: '#0f172a', weight: 3, fill: false}}).addTo(g);
    layers.mu = g;
  }
  async function ensureKptsLayer(){
    const gj = await load('catchments_kmz_pts.geojson'); if(!gj) return;
    layers.kpts = L.geoJSON(gj, {
      pointToLayer: (f, ll) => { const c = KC_COL[f.properties.group] || '#475569';
        return L.circleMarker(ll, {renderer: canvas, radius: 4, color: '#fff', weight: 1, fillColor: c, fillOpacity: .95}); },
      onEachFeature: (f, l) => { const p = f.properties || {};
        l.bindTooltip(() => esc(p.name || '—'));
        l.bindPopup(() => `<h4>${esc(p.name || '—')}</h4><div class="m">${esc(p.group)} · ${esc(p.file)}</div><div class="kv">${kvRows(p)}</div>`, {maxWidth: 380, maxHeight: 420}); }});
  }

  // ---- Parsons 关键热点方案 C1–C29（tools/import_parsons_crit.py）：塘 / 管 / 泵与进水口 + 每个热点群的编号注记
  let PCG = null, pcritLbl = null, critFx = null;
  const PCR_COL = {pond: '#b45309', pipe: '#dc2626', point: '#7c2d12'};
  async function ensurePcritLayer(){
    const gj = await load('parsons_crit.geojson'); if(!gj) return;
    PCG = await load('parsons_crit_groups.json').catch(() => null);
    const grpTip = c => { const g = PCG?.groups?.[c]; if(!g) return '';
      return `<div class="kv"><span>${t('pcr.members')}</span><span>${(g.hotspots || []).map(h => `<b>${esc(h.id)}</b>`).join('、') || '—'}</span>`
        + `<span>${t('pcr.facil')}</span><span>${t('pcr.facilv', {p: g.n_ponds, ha: fmt(g.pond_ha, 1), q: g.n_points, l: g.n_pipes})}</span>`
        + `<span>${t('pcr.dwg')}</span><span>${esc((g.drawings || []).join('；'))}</span></div>`; };
    layers.pcrit = L.geoJSON(gj, {renderer: canvas,
      style: f => { const k = f.properties.kind, c = PCR_COL[k] || '#475569';
        return k === 'pond' ? {color: c, weight: 1.6, fillColor: '#f59e0b', fillOpacity: .35} : {color: c, weight: 2.4, opacity: .9, dashArray: '6,4'}; },
      pointToLayer: (f, ll) => L.circleMarker(ll, {renderer: canvas, radius: 4, color: '#fff', weight: 1, fillColor: PCR_COL.point, fillOpacity: .95}),
      onEachFeature: (f, l) => { const p = f.properties || {};
        l.bindTooltip(() => `${esc(p.code)} · ${esc(p.sub)}`);
        l.bindPopup(() => `<h4>${t('pcr.title')} ${esc(p.code)}</h4><div class="m">${esc(p.sub)}</div><div class="kv">`
          + (p.area_ha != null ? `<span>${t('lay.area')}</span><span>${fmt(p.area_ha, 2)} ha</span><span>${t('pcr.vol')}</span><span>${fmt(p.volume_m3, 0)} m³ <span class="m">${t('pcr.volNote')}</span></span>` : '')
          + (p.length_m != null ? `<span>${t('pcr.len')}</span><span>${fmt(p.length_m, 0)} m</span>` : '') + `</div>`
          + grpTip(p.code) + `<div class="m">${esc(F(gj, 'source') || '')}</div>`, {maxWidth: 380}); }});
    pcritLbl = L.layerGroup();
    Object.values(PCG?.groups || {}).forEach(g => { if(!g.label_ll) return;
      L.marker(g.label_ll, {interactive: false, keyboard: false, icon: L.divIcon({className: 'pcrlbl', html: `<span>${esc(g.code)}</span>`, iconSize: null})}).addTo(pcritLbl); });
    // C1 PDF 版 27 座泵站（tools/extract_c1_design.py：图上泵符号经塘轮廓配准落点，待用户核对）
    const pu = await load('c1_pumps.geojson').catch(() => null);
    (pu?.features || []).forEach(f => { const p = f.properties, ll = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
      L.circleMarker(ll, {renderer: canvas, radius: 6, color: '#fff', weight: 1.5, fillColor: '#16a34a', fillOpacity: .95}).addTo(layers.pcrit)
        .bindPopup(`<h4>${t('pcr.pump')} ${esc(p.id)}</h4><div class="kv">`
          + `<span>${t('pcr.pq')}</span><span>${p.q_each_lps} L/s × 2 @ ${p.head_m} m</span>`
          + `<span>${t('pcr.ww')}</span><span>${p.wetwell_dia_m} m × ${p.wetwell_depth_m} m</span>`
          + `<span>${t('pcr.ptype')}</span><span>${p.type}${p.pond_tag ? ' (pond)' : ''}</span>`
          + `<span>UTM 40N</span><span>${fmt(p.x_utm, 0)}, ${fmt(p.y_utm, 0)}</span></div><div class="m">${t('pcr.pumpNote')}</div>`, {maxWidth: 360});
      L.marker(ll, {interactive: false, keyboard: false, icon: L.divIcon({className: 'pumplbl', html: `<span>${esc(p.id)}</span>`, iconSize: null})}).addTo(pcritLbl); });
    // C1 OP-02 的 12 英寸移动泵（tools/extract_c1_op02.py，C1 的 L2 用）：和 PDF 版泵站同址，画成外圈
    const mp = await load('c1_op02_pumps.geojson').catch(() => null);
    (mp?.features || []).forEach(f => { const p = f.properties, ll = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
      L.circleMarker(ll, {renderer: canvas, radius: 10, color: '#1d4ed8', weight: 2, fill: false}).addTo(layers.pcrit)
        .bindPopup(`<h4>${t('pcr.mp')} ${esc(p.id)}</h4><div class="kv"><span>${t('pcr.mpsize')}</span><span>12"${p.standby_gap_m != null ? ' + ' + t('pcr.stby') : ''}</span>`
          + `<span>${t('pcr.pq')}</span><span>${t('pcr.mpq')}</span><span>UTM 40N</span><span>${fmt(p.x_utm, 0)}, ${fmt(p.y_utm, 0)}</span></div><div class="m">${t('pcr.mpNote')}</div>`, {maxWidth: 360}); });
  }
  // ---- C1 建模用的空间数据（tools/c1_map_layers.py）：计算域、8 个热点的地表 / 管网汇水区、L1 现状泵站与排放线
  let c1Lbl = null;
  const C1_COL = {'ADM-21': '#7c3aed', 'ADM-90': '#ca8a04', 'ADM-94': '#0891b2', 'ADM-104': '#2563eb', 'ADM-192': '#ea580c',
                  'ADM-202': '#16a34a', 'ADM-486': '#be123c', 'ADM-487': '#64748b'};
  async function ensureC1Layer(){
    const [dom, cat, ps] = await Promise.all(['c1_domain.geojson', 'c1_catch.geojson', 'c1_ps_l1.geojson'].map(f => load(f).catch(() => null)));
    const g = L.featureGroup(); c1Lbl = L.layerGroup();
    if(cat) L.geoJSON(cat, {renderer: canvas,
      style: f => { const p = f.properties, c = C1_COL[p.hotspot_id] || '#475569';
        return p.kind === '地表' ? {color: c, weight: 1.2, opacity: .8, fillColor: c, fillOpacity: .14}
                                  : {color: c, weight: 2.2, opacity: .95, dashArray: '5,4', fillColor: c, fillOpacity: .05}; },
      onEachFeature: (f, l) => { const p = f.properties;
        l.bindTooltip(() => `${esc(p.hotspot_id)} · ${t('c1.k_' + p.kind)} · ${fmt(p.area_ha, 1)} ha`);
        l.bindPopup(() => `<h4>${esc(p.hotspot_id)} · ${t('c1.k_' + p.kind)}</h4><div class="kv"><span>${t('lay.area')}</span><span>${fmt(p.area_ha, 1)} ha</span></div>`
          + `<div class="m">${t('c1.m_' + p.kind)}${p.unconverged ? '<br><b>' + t('c1.unconv') + '</b>' : ''}</div>`, {maxWidth: 360}); }}).addTo(g);
    (cat?.features || []).forEach(f => { const p = f.properties; if(p.kind !== '地表') return;
      L.marker([p.lab_lat, p.lab_lon], {interactive: false, keyboard: false, icon: L.divIcon({className: 'c1lbl', html: `<span style="color:${C1_COL[p.hotspot_id] || '#334155'}">${esc(p.hotspot_id)}</span>`, iconSize: null})}).addTo(c1Lbl); });
    if(dom) L.geoJSON(dom, {renderer: canvas, style: {color: '#0f172a', weight: 2.6, dashArray: '10,6', fill: false},
      onEachFeature: (f, l) => l.bindPopup(`<h4>${t('c1.dom')}</h4><div class="kv"><span>${t('lay.area')}</span><span>${fmt(f.properties.area_km2, 1)} km²</span></div><div class="m">${esc(F(f.properties, 'note') || '')}</div>`)}).addTo(g);
    if(ps) L.geoJSON(ps, {renderer: canvas,
      style: {color: '#7e22ce', weight: 2.4, dashArray: '2,5', opacity: .9},
      pointToLayer: (f, ll) => L.circleMarker(ll, {renderer: canvas, radius: 7, color: '#fff', weight: 1.5, fillColor: f.properties.built ? '#7e22ce' : '#94a3b8', fillOpacity: .95}),
      onEachFeature: (f, l) => { const p = f.properties;
        l.bindTooltip(() => `${esc(p.ps)} · ${p.built ? fmt(p.q_lps, 0) + ' L/s' : t('c1.notbuilt')}`);
        l.bindPopup(() => `<h4>${t('c1.ps')} ${esc(p.ps)}</h4><div class="m">${esc(p.unitid)}</div><div class="kv">`
          + (p.built ? `<span>${t('c1.q')}</span><span>${p.n} × ${fmt(p.q_each, 1)} = ${fmt(p.q_lps, 0)} L/s</span><span>${t('c1.qsrc')}</span><span>${esc(F(p, 'q_src'))}</span>`
            + `<span>${t('c1.wet')}</span><span>${esc(p.wet)}：${fmt(p.inv_orig, 2)} → ${fmt(p.z_bot, 2)} m</span><span>${t('c1.onoff')}</span><span>${fmt(p.on, 2)} / ${fmt(p.off, 2)} m</span>`
            + `<span>${t('c1.to')}</span><span>${esc(F(p, 'how'))}</span>`
            : `<span>${t('c1.qsrc')}</span><span>${esc(F(p, 'q_src'))}</span>`) + `</div>`, {maxWidth: 380}); }}).addTo(g);
    layers.c1 = g;
  }

  // ---- Parsons 模型子汇水区（C1 计算域内，tools/c1_parsons_catch.py）：按 SCS CN 着色
  const PCA_BIN = [[0, 70, '#16a34a'], [70, 80, '#a3e635'], [80, 90, '#f59e0b'], [90, 95, '#ea580c'], [95, 101, '#b91c1c']];
  const pcaCol = cn => (PCA_BIN.find(([a, b]) => cn >= a && cn < b) || [0, 0, '#94a3b8'])[2];
  let PCA_N = null;
  async function ensurePcaLayer(){
    const gj = await load('c1_parsons_ca.geojson'); if(!gj) return;
    PCA_N = {bins: PCA_BIN.map(() => 0), models: {}};
    gj.features.forEach(f => { const p = f.properties, i = PCA_BIN.findIndex(([a, b]) => p.cn >= a && p.cn < b);
      if(i >= 0) PCA_N.bins[i]++; PCA_N.models[p.model] = (PCA_N.models[p.model] || 0) + 1; });
    layers.c1pca = L.geoJSON(gj, {renderer: canvas,
      style: f => ({color: '#334155', weight: .4, opacity: .55, fillColor: pcaCol(f.properties.cn), fillOpacity: .38}),
      onEachFeature: (f, l) => { const p = f.properties;
        l.bindPopup(() => `<h4>${t('pca.title')} ${esc(p.label || '—')}</h4><div class="m">${esc(p.model)}</div><div class="kv">`
          + `<span>SCS CN</span><span>${fmt(p.cn, 0)}</span><span>Tc</span><span>${fmt(p.tc, 1)} min</span>`
          + `<span>${t('lay.area')}</span><span>${fmt(p.area_ha * 1e4, 0)} m²</span><span>${t('pca.out')}</span><span>${esc(p.out_node || '—')}</span>`
          + `<span>${t('pca.runoff')}</span><span>${p.runoff_m3 != null ? fmt(p.runoff_m3, 1) + ' m³' : '—'}</span></div><div class="m">${esc(F(gj, 'note') || '')}</div>`, {maxWidth: 360}); }});
  }

  // ?crit=C1&hl=ADM-94,ADM-487,ADM-192：飞到这个热点群，标出它包含的客户热点（绿）和另给的点（在群里绿、不在群里红）
  async function critFocus(code, extra){
    for(const k of ['zones', 'bott', 'props']) if(on(k)) await toggleLayer(k, false);      // 默认开着的三层太乱，这个视图里先关掉
    await toggleLayer('pcrit', true); if(!layers.pcrit) return;
    const g = PCG?.groups?.[code]; if(!g) return;
    if(critFx){ map.removeLayer(critFx); critFx = null; }
    critFx = L.featureGroup();
    const mem = new Set((g.hotspots || []).map(h => h.id));
    const hs = await load('hotspots.geojson').catch(() => null);
    const pts = [], askedPts = []; const want = new Set([...mem, ...(extra || [])]);
    // 另给的点按经度排序，标签分别朝左 / 上 / 右 …，挨得近也不互相压住
    const order = (hs?.features || []).filter(f => (extra || []).includes(f.properties.hotspot_id)).sort((x, y) => x.geometry.coordinates[0] - y.geometry.coordinates[0]).map(f => f.properties.hotspot_id);
    const DIRS = ['left', 'top', 'right', 'bottom'], OFF = {left: [-12, 0], top: [0, -12], right: [12, 0], bottom: [0, 12]};
    (hs?.features || []).forEach(f => { const id = f.properties.hotspot_id; if(!want.has(id)) return;
      const ll = [f.geometry.coordinates[1], f.geometry.coordinates[0]], inG = mem.has(id), asked = (extra || []).includes(id);
      const c = inG ? '#15803d' : '#b91c1c'; pts.push(ll); if(asked) askedPts.push(ll);
      const dir = asked ? DIRS[order.indexOf(id) % DIRS.length] : 'right';
      L.circleMarker(ll, {radius: asked ? 11 : 7, color: c, weight: asked ? 4 : 2, fillColor: '#fff', fillOpacity: .9}).addTo(critFx)
        .bindTooltip(`<b>${esc(id)}</b> · ${inG ? t('pcr.in', {c: code}) : t('pcr.out', {c: code})}`, {permanent: asked, direction: dir, offset: OFF[dir], className: 'crittip ' + (inG ? 'in' : 'out')})
        .bindPopup(`<h4>${esc(id)}</h4><div class="m">${esc(f.properties.area || '')} · ${esc(f.properties.location || '')}</div><div class="kv"><span>${t('pcr.rel')}</span><span><b style="color:${c}">${inG ? t('pcr.in', {c: code}) : t('pcr.out', {c: code})}</b></span><span>${t('hsp.crit') !== 'hsp.crit' ? t('hsp.crit') : 'criticality'}</span><span>${esc(f.properties.criticality || '')}</span></div>`); });
    critFx.addTo(map);
    const b = L.latLngBounds([[g.bounds[1], g.bounds[0]], [g.bounds[3], g.bounds[2]]]); pts.forEach(p => b.extend(p));
    // 只按这个群的客户热点与另给的点定位（群的管线有一段一直拉到出水口，按它定位会缩得太远）；右边留出图层面板的宽度
    const fb = askedPts.length ? L.latLngBounds(askedPts) : pts.length ? L.latLngBounds(pts) : b;
    const fit = () => { try{   // 每次现量图层面板：页面刚加载时它可能还没展开
      const lp = document.getElementById('layers'), lr = lp ? lp.getBoundingClientRect() : null, pr = lr && lr.width ? lr.width + 30 : 30;
      map.invalidateSize(); map.fitBounds(fb.pad(.6), {paddingTopLeft: [60, 40], paddingBottomRight: [pr + 40, 40], maxZoom: 16, animate: false}); }catch(e){} };
    window._critFit = () => { fit(); return [fb.toBBoxString(), map.getCenter().lng.toFixed(4), map.getZoom()]; };   // 自动化测试用
    window._viewOwned = true; fit(); [300, 1200, 2500].forEach(ms => setTimeout(fit, ms));     // 页签渲染完会再定一次视野，补几次
    layersCount(); renderLegend();
  }

  async function ensurePondLayer(){
    const gj = await load('ponds_l2.geojson'); if(!gj) return;
    const col = s => ({Existing:'#0d6b74', Active:'#15803d', FinalHover:'#b07a1c'})[s] || '#64748b';
    layers.pond = L.geoJSON(gj, {renderer:canvas,
      style:f=>{ const c=col(f.properties&&f.properties.status);
        return dim({color:c, weight:2.4, opacity:.95, fillColor:c, fillOpacity:.35}, f); },
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        const v=+p.volume_m3||0, ar=+p.area_m2||0;
        l.bindTooltip(()=>`${esc(p.unitid||p.name||p.desc||'')}<br>${fmt(v,0)} m³ · ${esc(p.status||'')}`
          +(p.in_scope?`<br><b>${t('lay.inScope')}</b>`:`<br>${t('lay.outBy')} ${fmt(+p.dist_m||0,0)} m`));
        l.bindPopup(()=>`<h4>${esc(p.unitid||p.name||t('layers.pond'))}</h4>`
          +`<div class="m">${esc(p.district||'')}${p.sector?' · '+esc(p.sector):''}</div><div class="kv">`
          +`<span>${t('lay.status')}</span><span><b style="color:${col(p.status)}">${esc(p.status||'')}</b>`
          +`${p.condition?' · '+esc(p.condition):''}</span>`
          +`<span>${t('lay.vol')}</span><span>${fmt(v,0)} m³</span>`
          +`<span>${t('lay.area')}</span><span>${fmt(ar,0)} m²</span>`
          +`<span>${t('ma.dist')}</span><span>${p.in_model_area
              ? `<b>${t('ma.inside')}</b>（${esc(p.nearest_model||'')}）`
              : `<b style="color:#b23a2e">${fmt(+p.dist_to_model_m||0,0)} m</b> ${t('ma.outside')}（${esc(p.nearest_model||'')}）`}</span>`
          +pondCatchKv(p)
          +scopeKv(p)
          +(p.unittype?`<span>${t('lay.type')}</span><span>${esc(p.unittype)}</span>`:'')
          +(p.pipes?`<span>${t('lay.pipes')}</span><span>${esc(p.pipes)}</span>`:'')
          +(p.installed&&!String(p.installed).startsWith('1899')?`<span>${t('lay.installed')}</span><span>${esc(String(p.installed).slice(0,10))}</span>`:'')
          +(p.data_source?`<span>${t('lay.src')}</span><span>${esc(p.data_source)}</span>`:'')
          +`</div>`+(p.comments?`<div class="m">${esc(p.comments)}</div>`:'')); }});
  }

  async function ensureResvLayer(){
    const gj = await load('reservoirs_l1.geojson'); if(!gj) return;
    layers.resv = L.geoJSON(gj, {renderer:canvas,
      style:f=>dim({color:'#1d4ed8', weight:2.2, opacity:.95, fillColor:'#1d4ed8', fillOpacity:.3}, f),
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        l.bindTooltip(()=>`${esc(p.unitid||'')} · ${esc(p.status||'')}`);
        l.bindPopup(()=>`<h4>${esc(p.unitid||t('layers.resv'))}</h4>`
          +`<div class="m">${esc(p.district||p.city||'')}</div><div class="kv">`
          +`<span>${t('lay.status')}</span><span>${esc(p.status||'')}</span>`
          +`<span>${t('lay.mig')}</span><span>${esc(p.migration||'')}</span>`
          +scopeKv(p)+`</div>`); }});
  }

  // ---- 塘·接管与开挖：tools/pond_link.py 的产物。
  // 补的是客户数据里缺的那条边——26 万行管线里没有一行提到任何一座塘，
  // 可 23 座塘全都有管线贴着或穿过。这一层把「接哪根管、要不要挖、挖多深」画出来。
  async function ensurePlinkLayer(){
    const gj = await load('ponds_linked.geojson'); if(!gj) return;
    const col = p => p.no_target ? '#64748b' : (p.need_dig ? '#b45309' : '#15803d');
    layers.plink = L.geoJSON(gj, {renderer:canvas,
      style:f=>{ const c=col(f.properties||{});
        return {color:c, weight:2.6, opacity:.95, fillColor:c, fillOpacity:.3}; },
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        const dig = p.no_target ? t('pl.noTarget')
                  : (p.need_dig ? `${t('pl.dig')} ${fmt(+p.depth_m,2)} m` : t('pl.noDig'));
        l.bindTooltip(()=>`${esc(p.unitid||'')}<br>${dig} · ${fmt(+p.vol_design_m3||0,0)} m³`);
        l.bindPopup(()=>`<h4>${esc(p.unitid||'')}</h4><div class="m">${esc(p.zone||'')}</div><div class="kv">`
          +`<span>${t('pl.digQ')}</span><span><b style="color:${col(p)}">${dig}</b></span>`
          +`<span>${t('pl.volLedger')}</span><span>${fmt(+p.vol_ledger_m3||0,0)} m³</span>`
          +`<span>${t('pl.volNow')}</span><span>${fmt(+p.vol_dtm_m3||0,0)} m³</span>`
          +`<span>${t('pl.volDesign')}</span><span><b>${fmt(+p.vol_design_m3||0,0)} m³</b></span>`
          +(p.dig_volume_m3?`<span>${t('pl.cut')}</span><span>${fmt(+p.dig_volume_m3,0)} m³</span>`:'')
          +(p.short_m3?`<span>${t('pl.short')}</span><span style="color:#b23a2e">${fmt(+p.short_m3,0)} m³</span>`:'')
          +`<span>${t('pl.floor')}</span><span>${fmt(+p.z_floor_m,2)} → ${fmt(+p.z_top_m,2)} m</span>`
          +`<span>${t('pl.inflow')}</span><span>${p.inflow_pipe
              ?`${esc(p.inflow_pipe)} · ${fmt(+p.inflow_dia_mm,0)} mm @ ${fmt(+p.inflow_invert_m,2)} m`
              :t('pl.surface')}</span>`
          +`<span>${t('pl.outflow')}</span><span>${p.outflow_pipe
              ?`${esc(p.outflow_pipe)} · ${fmt(+p.outflow_dia_mm,0)} mm @ ${fmt(+p.outflow_invert_m,2)} m`
              :`<b style="color:#b23a2e">${t('pl.pump')}</b>`}</span>`
          +`</div>`+(p.dig_reason?`<div class="m">${esc(p.dig_reason)}</div>`:'')
          +(p.ledger_suspect?`<div class="m" style="color:#b23a2e">${t('pl.suspect')}</div>`:'')); }});
  }

  // ---- 全市汇水域缺口：tools/citywide.py 的产物。
  // 空间单元是**从管网自己划的**（244 个出水口沿管网反向广搜），不是客户那 39 个多边形——
  // 那 39 个只盖住 47.4% 的管线，而 19/23 座塘所在的片区覆盖率是 0。
  // 虚线边框 = 这块网没有登记在案的出水口（2,462 个这样的子网之一）。
  async function ensureCwgapLayer(){
    const gj = await load('citywide_gap.geojson'); if(!gj) return;
    const brk = [5e3, 2e4, 8e4, 3e5];                 // 按 1 h、Gap1 低值分档
    const pal = ['#fee6ce','#fdae6b','#e6550d','#a63603','#7f2704'];
    const col = v => { const i = brk.findIndex(b => v < b); return pal[i < 0 ? pal.length - 1 : i]; };
    const noOut = p => String(p.outfall||'').indexOf('无登记出口') >= 0;
    layers.cwgap = L.geoJSON(gj, {renderer:canvas,
      style:f=>{ const p=f.properties||{}, c=col(+p.gap1_1h_lo_m3||0);
        return {color:noOut(p)?'#b23a2e':c, weight:noOut(p)?1.6:0.8,
                dashArray:noOut(p)?'4,3':null, opacity:.9, fillColor:c, fillOpacity:.45}; },
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        l.bindTooltip(()=>`${esc(p.outfall||'')}<br>${fmt(+p.area_ha,0)} ha · ${t('cw.runoff')} ${fmt(+p.runoff_m3,0)} m³`);
        l.bindPopup(()=>`<h4>${esc(p.outfall||'')}</h4>`
          +`<div class="m">${fmt(+p.area_ha,0)} ha · ${t('cw.nodes')} ${fmt(+p.nodes,0)} · R=${fmt(+p.radius_m,0)} m</div>`
          +`<div class="kv">`
          +`<span>${t('cw.runoff')}</span><span><b>${fmt(+p.runoff_m3,0)} m³</b></span>`
          +`<span>${t('cw.gap1')} 1h</span><span>${fmt(+p.gap1_1h_lo_m3,0)} – ${fmt(+p.gap1_1h_hi_m3,0)} m³</span>`
          +`<span>${t('cw.gap1')} 24h</span><span>${fmt(+p.gap1_24h_lo_m3,0)} – ${fmt(+p.gap1_24h_hi_m3,0)} m³</span>`
          +`<span>${t('cw.ponds')}</span><span>${+p.n_ponds ? `<b>${p.n_ponds}</b>` : t('cw.none')}</span>`
          +(+p.n_ponds?`<span>${t('cw.l2now')}</span><span>${fmt(+p.l2_now_m3,0)} m³</span>`
                      +`<span>${t('cw.l2des')}</span><span>${fmt(+p.l2_design_m3,0)} m³</span>`:'')
          +`<span>${t('cw.gap2')} 1h</span><span>${fmt(+p.gap2_1h_lo_m3,0)} – ${fmt(+p.gap2_1h_hi_m3,0)} m³</span>`
          +`</div>`
          +(noOut(p)?`<div class="m" style="color:#b23a2e">${t('cw.noOutfall')}</div>`:'')
          +`<div class="m">${esc(F(gj,'source')||'')}</div>`); }});
  }

  // ---- 客户 22 个水力模型的覆盖范围。
  // 加这一层是为了能**自己看**一件事：194 万 m³ 已建成的 L2 塘，一座都不在这些范围里，
  // 最近的只差 13 m。不叠出来，这句话只能靠转述。
  async function ensureMareaLayer(){
    const gj = await load('model_areas.geojson'); if(!gj) return;
    layers.marea = L.geoJSON(gj, {renderer:canvas,
      style:{color:'#0f766e', weight:2, opacity:.9, fillColor:'#0f766e', fillOpacity:.10},
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        l.bindTooltip(()=>`${esc(p.model||'')} · ${fmt(+p.area_ha||0,0)} ha`);
        l.bindPopup(()=>`<h4>${esc(p.model||'')}</h4><div class="kv">`
          +`<span>${t('ma.area')}</span><span>${fmt(+p.area_ha||0,0)} ha</span>`
          +`<span>${t('ma.nsub')}</span><span>${fmt(+p.n_sub||0,0)}</span>`
          +`<span>${t('ma.method')}</span><span>${esc(p.method||'')}</span>`
          +`</div><div class="m">${esc(F(gj,'source')||'')}</div>`); }});
  }

  // ---- 塘·按容积缩放的标记点。
  // 多边形在市域尺度下太小（最大那座 14 万 m² 也就几个像素），肉眼判不了相交。
  // 这一层把每座塘画成半径 ∝ sqrt(容积) 的圆，颜色分「在模型区内 / 区外」。
  // 塘与汇水区的关系（tools/pond_catchment.py 写入）：客户那 39 个多边形是建模研究范围，
  // 不是全市排水分区——塘不在其中不等于没有汇水区，所以图上要把「在哪 / 差多远」直接画出来。
  const PM_BY = U.get('view') === 'l2' ? 'model' : 'catch';
  const PM_REL_COL = {in:'#15803d', part:'#0e7490', edge:'#b45309', out:'#b23a2e'};
  function pondCatchKv(p){
    if(!p.catch_rel) return '';
    const inc = (p.catch_in||[]).map(x=>`<b>${esc(x.label)}</b> ${x.pct}%`).join('、');
    const near = p.catch_near ? `${esc(p.catch_near.label)} <b>${fmt(+p.catch_near.dist_m,0)} m</b>` : '—';
    return `<span>${t('pm.catch')}</span><span><b style="color:${PM_REL_COL[p.catch_rel]}">${t('pm.rel.'+p.catch_rel)}</b>`
      + (inc ? ` · ${inc}` : ` · ${t('pm.nearest')} ${near}`) + `</span>`;
  }
  async function ensurePondmkLayer(){
    const gj = await load('ponds_l2_pts.geojson'); if(!gj) return;
    const R = v => Math.max(5, Math.min(34, Math.sqrt(Math.max(+v||0,1))/18));
    const colOf = p => PM_BY === 'model' ? (p.in_model_area ? '#15803d' : '#b23a2e') : (PM_REL_COL[p.catch_rel] || '#64748b');
    layers.pondmk = L.geoJSON(gj, {renderer:canvas,
      pointToLayer:(f,ll)=>{ const p=f.properties||{}; const c=colOf(p);
        return L.circleMarker(ll,{radius:R(p.volume_m3), color:c, weight:2, fillColor:c, fillOpacity:.45}); },
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        l.bindTooltip(()=>`${esc(p.unitid||'')}<br>${fmt(+p.volume_m3||0,0)} m³<br>`
          +(PM_BY === 'model'
            ? (p.in_model_area?`<b>${t('ma.inside')}</b>`:`<b>${fmt(+p.dist_to_model_m||0,0)} m</b> ${t('ma.outside')}`)+` · ${esc(p.nearest_model||'')}`
            : `<b>${t('pm.rel.'+(p.catch_rel||'out'))}</b>`+((p.catch_in||[]).length
                ? ` · ${esc(p.catch_in[0].label)} ${p.catch_in[0].pct}%`
                : (p.catch_near?` · ${esc(p.catch_near.label)} ${fmt(+p.catch_near.dist_m,0)} m`:''))), {permanent:false});
        l.bindPopup(()=>`<h4>${esc(p.unitid||'')}</h4>`
          +`<div class="m">${esc(p.district||'')}</div><div class="kv">`
          +`<span>${t('lay.vol')}</span><span><b>${fmt(+p.volume_m3||0,0)} m³</b></span>`
          +`<span>${t('lay.area')}</span><span>${fmt(+p.area_m2||0,0)} m²</span>`
          +`<span>${t('lay.status')}</span><span>${esc(p.status||'')}</span>`
          +pondCatchKv(p)
          +`<span>${t('ma.dist')}</span><span>${p.in_model_area
              ? `<b>${t('ma.inside')}</b>（${esc(p.nearest_model||'')}）`
              : `<b style="color:#b23a2e">${fmt(+p.dist_to_model_m||0,0)} m</b> ${t('ma.outside')}（${esc(p.nearest_model||'')}）`}</span>`
          +`</div><div class="m">${esc(F(gj,'catch_note')||'')}</div>`); }});
  }

  // 塘自己的地表汇水区（tools/pond_surface_catchments.py，从客户 5 m DTM 按 D8 划）。
  // 颜色 = 这场雨（61 mm）的来水 ÷ 塘的库容：红 = 装不下，橙 = 用上大半，蓝 = 大多闲置，灰 = 台账没写库容。
  // 碰到计算窗口边的（被截断，只是下限）画虚线。只算地表：管网转输看不见，靠管道进水的塘真实来水更多。
  const PC_COL = f => f==null ? '#94a3b8' : f >= 100 ? '#b91c1c' : f >= 30 ? '#d97706' : '#2563eb';
  async function ensurePcatchLayer(){
    const gj = await load('l2_pond_catch.geojson'); if(!gj) return;
    layers.pcatch = L.geoJSON(gj, {renderer:canvas,
      style:f=>{ const p=f.properties||{}, c=PC_COL(p.fill_pct);
        return {color:c, weight:1.6, opacity:.9, fillColor:c, fillOpacity:.18, dashArray:p.touches_edge?'6,4':null}; },
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        const ge = p.touches_edge ? '≥ ' : '';
        l.bindTooltip(()=>`${esc(p.unitid||'')}<br>${t('pc.catch')} ${ge}${fmt(+p.catch_ha||0,0)} ha`
          + (p.fill_pct!=null?` · ${t('pc.fill')} ${ge}${fmt(+p.fill_pct,0)}%`:''));
        l.bindPopup(()=>`<h4>${t('layers.pcatch')} · ${esc(p.unitid||'')}</h4><div class="m">${esc(p.district||'')} · ${esc(F(gj,'rain')||'')}</div><div class="kv">`
          +`<span>${t('pc.catch')}</span><span><b>${ge}${fmt(+p.catch_ha||0,1)} ha</b>（${t('pc.pond')} ${fmt(+p.pond_ha||0,2)} ha）</span>`
          +`<span>${t('lay.vol')}</span><span>${p.volume_m3?fmt(+p.volume_m3,0)+' m³':`<b style="color:#b23a2e">${t('pc.noVol')}</b>`}</span>`
          +`<span>${t('pc.runoff')}</span><span><b>${ge}${fmt(+p.runoff_m3||0,0)} m³</b></span>`
          +(p.fill_pct!=null?`<span>${t('pc.fill')}</span><span><b style="color:${PC_COL(p.fill_pct)}">${ge}${fmt(+p.fill_pct,0)}%</b>`
            +(p.spill_m3?` · ${t('pc.spill')} ${fmt(+p.spill_m3,0)} m³`:'')+(p.idle_m3?` · ${t('pc.idle')} ${fmt(+p.idle_m3,0)} m³`:'')+`</span>`:'')
          +`<span>${t('pc.client')}</span><span>${(p.in_client_catch||[]).map(x=>`${esc(x.label)} ${x.pct}%`).join('、')||t('pc.none')}</span>`
          +`</div>`+(p.touches_edge?`<div class="m" style="color:#b45309">${t('pc.edge')}</div>`:'')
          +`<div class="m">${esc(F(gj,'note')||'')}</div>`); }});
  }

  // ?view=l2c：汇水区 × 已建成 L2 塘。给「有塘 / 塘贴边」的汇水区打上名字，其余 36 个不标，免得满图字。
  let l2cLbl = null;
  async function l2cLabels(){
    if(l2cLbl){ map.removeLayer(l2cLbl); l2cLbl = null; }
    if(U.get('view') !== 'l2c' || !layers.catch || !layers.pondmk) return;
    const want = new Set();
    layers.pondmk.getLayers().forEach(l => { const p = l.feature.properties || {};
      (p.catch_in || []).forEach(x => want.add(x.label));
      if(p.catch_rel === 'edge' && p.catch_near) want.add(p.catch_near.label); });
    l2cLbl = L.layerGroup(); const seen = new Set();
    layers.catch.getLayers().forEach(l => { const lab = (l.feature.properties || {}).label;
      if(!want.has(lab) || seen.has(lab)) return; seen.add(lab);
      l.setStyle({weight: 3, opacity: 1, fillOpacity: .12, dashArray: null});
      L.marker(l.getBounds().getCenter(), {interactive: false, keyboard: false,
        icon: L.divIcon({className: 'l2clbl', html: `<span>${esc(lab)}</span>`, iconSize: null})}).addTo(l2cLbl); });
    l2cLbl.addTo(map);
  }

  // ---- 作业范围：本次一二维耦合到底算的是哪一块。
  // 穆萨法有 8 个编号分区、合计约 5,260 ha，本次只做了 Mussafah_00 的 148.8 ha（2.8%）。
  // 原有两层都消不掉这个歧义：catchments_sw 里 Mussafah_00 和另外 38 个长一个样；
  // model_areas 里有 Mussafah-01/02/03/08/10/RD09，**偏偏没有 Mussafah_00**。
  // 所以这一层用常驻标注把三件事写在图上：算了哪块、没算哪些、二维域有多大。
  async function ensureScopeLayer(){
    const gj = await load("scope.geojson"); if(!gj) return;
    const ST = {
      modelled: {color:"#b91c1c", weight:3,   fillOpacity:.22, dash:null},
      sibling:  {color:"#9aa3ad", weight:1.5, fillOpacity:.05, dash:"5,5"},
      net1d:    {color:"#047857", weight:2,   fillOpacity:.08, dash:"2,4"},
      domain2d: {color:"#1d4ed8", weight:2,   fillOpacity:0,   dash:"10,6"}
    };
    layers.scope = L.geoJSON(gj, {renderer:canvas,
      style:f=>{ const r=(f.properties||{}).role||"sibling"; const x=ST[r]||ST.sibling;
        return {color:x.color, weight:x.weight, opacity:.95,
                fillColor:x.color, fillOpacity:x.fillOpacity, dashArray:x.dash}; },
      onEachFeature:(f,l)=>{ const p=f.properties||{}; const r=p.role||"sibling";
        const nm=esc(F(p,"name")||""), ha=fmt(+p.area_ha||0,1);
        // 常驻标注 —— 名字必须一直看得见，不能靠鼠标悬停才知道是哪块
        const lbl = r==="modelled"
          ? "<b>"+nm+"</b><br>"+ha+" ha · "+t("sc.modelled")
          : r==="domain2d"
          ? nm+"<br>"+fmt(+p.res_m||0,0)+" m "+t("sc.grid")+" · "+ha+" ha"
          : r==="net1d"
          ? nm+"<br>"+ha+" ha · "+fmt(+p.n_nodes||0,0)+" "+t("sc.nodes")
          : nm+" · "+t("sc.notModelled");
        // 二维域的标注挪到框顶，否则和框中心那块汇水区的标注压在一起
        l.bindTooltip(lbl, {permanent:true,
                            direction: r==="domain2d" ? "top"
                                     : r==="net1d" ? "bottom" : "center",
                            className:"scope-lbl scope-"+r});
        l.bindPopup(()=>"<h4>"+nm+"</h4><div class=\"kv\">"
          +"<span>"+t("sc.role")+"</span><span><b>"+t("sc.r_"+r)+"</b></span>"
          +"<span>"+t("ma.area")+"</span><span>"+ha+" ha</span>"
          +(p.res_m?"<span>"+t("sc.grid")+"</span><span>"+fmt(+p.res_m,0)+" m · "
              +fmt(+p.n_tri||0,0)+" "+t("sc.tri")+"</span>":"")
          +(p.tbl_ha?"<span>"+t("sc.tbl")+"</span><span>"+fmt(+p.tbl_ha,1)+" ha"
              +(p.tbl_ratio?" ("+(+p.tbl_ratio).toFixed(2)+"×)":"")+"</span>":"")
          +"</div>"
          +(p.area_note?"<div class=\"m\" style=\"color:#b23a2e\">"+mdb(F(p,"area_note"))+"</div>":"")
          +"<div class=\"m\">"+mdb(F(p,"note")||"")+"</div>"
          +"<div class=\"m\">"+esc(F(gj,"source")||"")+"</div>"); }});
  }

  // ---- L2+ 的每一座设施：为什么建、为什么在这、为什么这个大小。
  // 两类来源画法不同也写法不同：L2+A（顾问）只写从交付模型读出的事实，**不替顾问编理由**；
  // L2+B（本系统）逐条给依据。位置一律给到汇水区多边形的距离 ——
  // 设施是按一维管网里的积水节点布的，管网比汇水区多边形大，所以有几座在多边形外。
  // 设施弹窗：全局图层与四态工作台共用一份，免得两处说法不一样
  const L2P_COL = {"L2+A":"#b45309", "L2+B":"#0e7490"};
  function l2plusPopupHtml(p, src){
    const loc = p.in_catchment ? `<b>${t('l2f.inCatch')}</b>`
      : `${t('l2f.outBy')} <b style="color:#b23a2e">${fmt(+p.d_catchment_m||0,0)} m</b>`;
    let h = `<h4>${esc(p.id||'')} <span style="color:${L2P_COL[p.state]||'#333'}">${esc(p.state||'')}</span></h4>`
      + `<div class="m">${esc(F(p,'who')||'')} · ${esc(F(p,'evidence')||'')}</div><div class="kv">`;
    if(p.vol_usable_m3) h += `<span>${t('l2f.volUse')}</span><span><b>${fmt(+p.vol_usable_m3,0)} m³</b></span>`
      + `<span>${t('l2f.volGeo')}</span><span>${fmt(+p.vol_geometric_m3||0,0)} m³ ${t('l2f.inclFree')}</span>`;
    else h += `<span>${t('l2f.vol')}</span><span><b>${fmt(+p.volume_m3||0,0)} m³</b></span>`;
    if(p.depth_m) h += `<span>${t('l2f.depth')}</span><span>${fmt(+p.depth_m,2)} m · ${esc(F(p,'wall')||'')}</span>`;
    if(p.n_served) h += `<span>${t('l2f.served')}</span><span>${fmt(+p.n_served,0)} ${t('l2f.pts')}`
      + `（${fmt(+p.served_m3||0,0)} m³${p.share_pct?`，${t('l2f.share')} ${p.share_pct}%`:''}）</span>`;
    if(p.n_in!=null) h += `<span>${t('l2f.io')}</span><span>${t('l2f.in')} ${p.n_in}`
      + `（${p.pump_fed?t('l2f.pump'):t('l2f.grav')}）· ${t('l2f.out')} ${p.n_out}`
      + (p.n_out===0?` <b style="color:#b23a2e">${t('l2f.noOut')}</b>`:'')+`</span>`;
    h += `<span>${t('l2f.loc')}</span><span>${loc}`
      + (p.in_net1d?` · ${t('l2f.inNet')}`:'') + (p.in_domain2d?` · ${t('l2f.in2d')}`:'') + `</span>`;
    if(p.land) h += `<span>${t('l2f.land')}</span><span><b>${p.land==='road_reserve'?t('wb.reserve'):t('wb.open')}</b>`
      + (p.moved_m?` · ${t('wb.moved',{m:fmt(+p.moved_m,0)})}`:'') + `</span>`;
    if(p.clr_pave_m!=null) h += `<span>${t('l2f.clear')}</span><span>${t('l2f.clearTxt',{pave:fmt(+p.clr_pave_m,0),
      bld:fmt(+p.clr_bld_m,0), plot:fmt(+p.clr_plot_m,0), row:fmt(+p.clr_row_m,0)})}</span>`;
    h += `</div>`;
    if(p.why_type) h += `<div class="m"><b>${t('l2f.whyType')}</b><br>${mdb(F(p,'why_type'))}</div>`;
    if(p.why_here) h += `<div class="m"><b>${t('l2f.whyHere')}</b><br>${mdb(F(p,'why_here'))}</div>`;
    if(p.why_size) h += `<div class="m"><b>${t('l2f.whySize')}</b><br>${mdb(F(p,'why_size'))}</div>`;
    if((p.rules||[]).length) h += `<div class="m"><b>${t('l2f.rules')}</b><br>`
      + F(p,'rules').map(x=>esc(x)).join('<br>') + `</div>`;
    if(src) h += `<div class="m">${esc(src)}</div>`;
    return h;
  }

  async function ensureL2plusLayer(){
    const gj = await load("l2plus.geojson"); if(!gj) return;
    const R = v => Math.max(7, Math.min(26, Math.sqrt(Math.max(+v||0,1))/9));
    layers.l2plus = L.geoJSON(gj, {renderer:canvas,
      pointToLayer:(f,ll)=>{ const p=f.properties||{}; const c=L2P_COL[p.state]||"#333";
        return L.circleMarker(ll,{radius:R(p.volume_m3), color:c, weight:2,
          fillColor:c, fillOpacity:.40, dashArray:p.in_catchment?null:"3,3"}); },
      onEachFeature:(f,l)=>{ const p=f.properties||{};
        l.bindTooltip(()=>`<b>${esc(p.id||'')}</b> · ${esc(p.state||'')}<br>${fmt(+p.volume_m3||0,0)} m³`);
        l.bindPopup(()=>l2plusPopupHtml(p, F(gj, 'source')||'')); }});
  }

  async function toggleLayer(k, onFlag){
    const cb=cbOf(k); if(cb) cb.checked=onFlag;
    if(k==='res' && onFlag && !layers.res){
      const r=await load('residual.geojson');
      layers.res=L.geoJSON(r,{renderer:canvas, style:f=>{const s=resStyle[f.properties.kind]||{color:'#333'}; return {color:s.color,weight:3,opacity:.95,dashArray:s.dash||null};},
        pointToLayer:(f,ll)=>{const s=resStyle[f.properties.kind]||{color:'#333'}; return L.circleMarker(ll,{radius:8,color:s.color,weight:3,fillColor:'#fff',fillOpacity:.9});},
        onEachFeature:(f,l)=>{const p=f.properties;l.bindPopup(()=>`<h4>${esc(F(p,'kind'))}</h4><div class="m">${esc(p.unitid||p.gid||'')} · ${esc(p.zone_name||'')}</div><div class="kv">`+Object.entries(p).filter(([k])=>!['kind','kind_en','unitid','gid','zone_name'].includes(k)).map(([k,v])=>`<span>${esc(k)}</span><span>${typeof v==='number'?v.toFixed(2):esc(v)}</span>`).join('')+`</div>`)}});
    }
    if((k==='flood'||k==='fleet') && onFlag && !layers[k]){ await ensureDispatchLayers(); }
    if(k==='catch' && onFlag && !layers.catch){ await ensureCatchLayer(); }
    if(k==='kcatch' && onFlag && !layers.kcatch){ await ensureKcatchLayer(); }
    if(k==='kpts' && onFlag && !layers.kpts){ await ensureKptsLayer(); }
    if(k==='rb3' && onFlag && !layers.rb3){ await ensureRb3Layer(); }
    if(k==='c1u' && onFlag && !layers.c1u){ await ensureC1uLayer(); }
    if(k==='mu' && onFlag && !layers.mu){ await ensureMuLayer(); }
    if(k==='pcrit' && onFlag && !layers.pcrit){ await ensurePcritLayer(); }
    if(k==='c1' && onFlag && !layers.c1){ await ensureC1Layer(); }
    if(k==='c1pca' && onFlag && !layers.c1pca){ await ensurePcaLayer(); }
    if(k==='c1' && c1Lbl){ onFlag ? c1Lbl.addTo(map) : map.removeLayer(c1Lbl); }
    if(k==='pcrit' && pcritLbl){ onFlag ? pcritLbl.addTo(map) : map.removeLayer(pcritLbl); if(!onFlag && critFx){ map.removeLayer(critFx); critFx = null; } }
    if(k==='pond' && onFlag && !layers.pond){ await ensurePondLayer(); }
    if(k==='resv' && onFlag && !layers.resv){ await ensureResvLayer(); }
    if(k==='plink' && onFlag && !layers.plink){ await ensurePlinkLayer(); }
    if(k==='cwgap' && onFlag && !layers.cwgap){ await ensureCwgapLayer(); }
    if(k==='marea' && onFlag && !layers.marea){ await ensureMareaLayer(); }
    if(k==='pondmk' && onFlag && !layers.pondmk){ await ensurePondmkLayer(); }
    if(k==='pcatch' && onFlag && !layers.pcatch){ await ensurePcatchLayer(); }
    if(k==='scope' && onFlag && !layers.scope){ await ensureScopeLayer(); }
    if(k==='l2plus' && onFlag && !layers.l2plus){ await ensureL2plusLayer(); }
    if(k==='hs' && onFlag && !layers.hs){ await ensureHotspotLayer(); }
    if(k==='dmt' && onFlag && !layers.dmt){ await ensureDmtLayer(); }
    if(k==='gwm' && onFlag && !layers.gwm){ await ensureGwmLayer(); }
    if(k==='prepo'){ if(onFlag){ await getDispatch(); drawPrepo(); } else if(prepoLayer){ map.removeLayer(prepoLayer); } layersCount(); renderLegend(); return; }
    if(k==='res'){
      zoneFillNormal = !onFlag; if(layers.zones) layers.zones.setStyle(zoneStyle);
      if(onFlag && layers.bott && map.hasLayer(layers.bott)){ map.removeLayer(layers.bott); cbOf('bott').checked=false; }
    }
    const ly=layers[k]; if(ly){ onFlag?ly.addTo(map):map.removeLayer(ly); }
    if(k==='catch' || k==='kcatch') catchLabels(k);
    layersCount(); renderLegend();
  }
  // 每个页签有自己的图层预设：进页签时只留跟这页有关的
  // 第一道是「基础设施」，客户 L1 台账的两层（汇水区边界、既有塘）就该默认在这一页上
  // ?view=l2：只看「已建成的 L2」与「水力模型覆盖范围」，其余图层全关，
  // 专门用来肉眼核对两者有没有相交。
  const VIEWS = {l2: ['marea','pondmk','scope'], scope: ['scope'],
                 l2plus: ['scope','pondmk','l2plus'],
                 l2c: ['catch','pcatch','pond','pondmk']};   // 汇水区 × 已建成 L2 塘，按塘的包络定位
  const PRESET = {overview:['zones','bott','props'], line1:['zones','catch','pond','resv','plink','cwgap','marea'], props:['zones','props'], sched:['zones','props'], standard:['zones','bott'], residual:['res'], line2:['hs','prepo','flood'], line3:['flood','fleet','hs'], trust:['hs','gwm','dmt']};
  async function applyPreset(key){
    const v = U.get('view');
    const want = new Set(VIEWS[v] || PRESET[key] || []);   // line2p 不用全局图层，自己画自己的
    // URL 里 ?lay=catch,pond 指定的图层不被页签预设关掉——否则 buildLayers 刚点上就被抹了
    (U.get('lay')||'').split(',').map(s=>s.trim()).filter(Boolean).forEach(k=>want.add(k));
    for(const cb of document.querySelectorAll('#layers input')){ const k=cb.dataset.l; const w=want.has(k); if(cb.checked!==w) await toggleLayer(k,w); }

    // ?view= 进来的视图要**定位到它自己的范围**。原来只开图层不挪视野，
    // 结果 Mussafah_00 这种 1.5 km² 的范围停在全酋尺度下，标注全叠在一个点上，
    // 等于没画。按视图里最后一个图层的包络定位（VIEWS 的顺序就是为这个排的）。
    if(v && VIEWS[v]){
      // 定位用哪一层：默认最后一层；l2c 按塘的地表汇水区（两座塘的汇水区比塘的分布大得多）
      const FIT = {l2c:'pcatch'};
      const last = (FIT[v] && layers[FIT[v]]) ? FIT[v] : VIEWS[v].filter(k=>layers[k]).pop();
      if(last){ window._viewOwned = true;
        const fit=()=>{ try{ map.invalidateSize();
          map.fitBounds(layers[last].getBounds().pad(.15), {maxZoom:15}); }catch(e){} };
        setTimeout(fit,60); setTimeout(fit,700); }
    }
    await l2cLabels();
  }
  function renderLegend(){
    let h='';
    const dot = (c, ring) => `<span class="sw" style="border-radius:50%;${ring ? `background:transparent;border:2px solid ${c}` : `background:${c};border:1.5px solid #fff;box-shadow:0 0 0 1px ${c}`}"></span>`;
    const lnS = (c, dash, w) => `<span class="ln" style="border-top:${w || 3}px ${dash ? 'dashed' : 'solid'} ${c}"></span>`;
    if(on('c1') && layers.c1){ const n = {b: 0, u: 0};
      layers.c1.eachLayer(g => g.eachLayer && g.eachLayer(l => { const p = l.feature?.properties; if(p?.kind === 'ps'){ p.built ? n.b++ : n.u++; } }));
      h += `<div class="m"><b>${t('layers.c1')}</b></div>`
        + `<div class="row">${lnS('#0f172a', true, 3)}${t('c1.dom')} 42.8 km²</div>`
        + `<div class="row"><span class="sw" style="background:#64748b;opacity:.3;border:1px solid #64748b"></span>${t('c1.k_地表')}（${t('c1.lgFill')}）</div>`
        + `<div class="row">${lnS('#64748b', true, 2)}${t('c1.k_管网')}（${t('c1.lgDash')}）</div>`
        + `<div class="row" style="flex-wrap:wrap;gap:4px 10px">${Object.entries(C1_COL).map(([k, c]) => `<span style="display:inline-flex;align-items:center;gap:4px"><span class="sw" style="background:${c}"></span>${esc(k)}</span>`).join('')}</div>`
        + `<div class="row">${dot('#7e22ce')}${t('c1.ps')}（${t('c1.built')}）${n.b}</div>`
        + `<div class="row">${dot('#94a3b8')}${t('c1.ps')}（${t('c1.notbuilt')}）${n.u}</div>`
        + `<div class="row">${lnS('#7e22ce', true, 2)}${t('c1.lgDis')}</div>`; }
    if(on('c1pca') && layers.c1pca && PCA_N){
      h += `<div class="m"><b>${t('layers.c1pca')}</b><br><span style="opacity:.75">${Object.entries(PCA_N.models).map(([m, n]) => `${esc(m)} ${fmt(n, 0)}`).join(' · ')}</span></div>`
        + PCA_BIN.map(([a, b, c], i) => `<div class="row"><span class="sw" style="background:${c};opacity:.6;border:1px solid #334155"></span>CN ${i === 0 ? '< 70' : i === PCA_BIN.length - 1 ? '≥ 95' : a + '–' + b} · ${fmt(PCA_N.bins[i], 0)}</div>`).join(''); }
    if(on('pcrit') && layers.pcrit){
      h += `<div class="m"><b>${t('layers.pcrit')}</b></div>`
        + `<div class="row"><span class="sw" style="background:#f59e0b;opacity:.5;border:1.6px solid #b45309"></span>${t('pcr.lgPond')}</div>`
        + `<div class="row">${lnS('#dc2626', true, 2.4)}${t('pcr.lgPipe')}</div>`
        + `<div class="row">${dot('#7c2d12')}${t('pcr.lgPt')}</div>`
        + `<div class="row">${dot('#16a34a')}${t('pcr.pump')} 27</div>`
        + `<div class="row">${dot('#1d4ed8', true)}${t('pcr.mp')} 27</div>`; }
    if(on('mu') && layers.mu) h += `<div class="m"><b>${t('layers.mu')}</b></div>`
      + Object.entries(MU_COL).map(([k, c]) => `<div class="row"><span class="sw" style="background:${c};opacity:.35;border:2px solid ${c}"></span>${esc(k)}</div>`).join('')
      + `<div class="row"><span class="sw" style="background:#64748b;opacity:.2;border:1px solid #64748b"></span>${t('mu.others')}</div><div class="row">${lnS('#15803d', true, 2)}${t('mu.outfall')}</div>`
      + `<div class="row">${lnS('#0f172a', false, 3)}${t('mu.c1m')}</div>`;
    if(on('c1u') && layers.c1u) h += `<div class="m"><b>${t('layers.c1u')}</b></div>`
      + Object.entries(C1U_COL).map(([k, c]) => `<div class="row"><span class="sw" style="background:${c};opacity:.35;border:2px dashed ${c}"></span>${esc(k)}</div>`).join('')
      + `<div class="row">${lnS('#0f172a', false, 3)}${t('c1u.merged')}</div><div class="m" style="opacity:.75">${t('rb3.lg')}</div>`;
    if(on('rb3') && layers.rb3) h += `<div class="m"><b>${t('layers.rb3')}</b></div>`
      + `<div class="row"><span class="sw" style="background:#ef4444;opacity:.35;border:2px dashed #dc2626"></span>RB-3 RAHA BEACH OUTFALL-3 · ${fmt((+(layers.rb3.getLayers()[0]?.getLayers?.()[0]?.feature?.properties?.trace_area) || 0) / 1e4, 0)} ha</div>`
      + `<div class="row">${dot('#dc2626')}ADM-94 / ADM-192 / ADM-487</div><div class="m" style="opacity:.75">${t('rb3.lg')}</div>`;
    if(on('catch')) h+=`<div class="m"><b>${t('layers.catch')}</b></div>`
      +`<div class="row"><span class="sw" style="background:#7c3aed;opacity:.35;border:2px solid #7c3aed"></span>`
      +`${(layers.catch?layers.catch.getLayers().length:0)} ${t('lay.unit')}</div>`
      +scopeRow(layers.catch);
    if(on('resv')) h+=`<div class="m"><b>${t('layers.resv')}</b></div>`
      +`<div class="row"><span class="sw" style="background:#1d4ed8;opacity:.5;border:1.5px solid #1d4ed8"></span>`
      +`${(layers.resv?layers.resv.getLayers().length:0)} ${t('lay.unit')}</div>`
      +scopeRow(layers.resv);
    if(on('pondmk')){ const P=layers.pondmk? layers.pondmk.getLayers():[];
      h+=`<div class="m"><b>${t('layers.pondmk')}</b><br><span style="opacity:.7">${t('pm.note')}</span></div>`;
      if(PM_BY === 'model'){ const ins=P.filter(x=>(x.feature.properties||{}).in_model_area).length;
        h+=`<div class="row"><span class="sw" style="background:#15803d;opacity:.6;border:2px solid #15803d"></span>${t('ma.inside')} ${ins}</div>`
          +`<div class="row"><span class="sw" style="background:#b23a2e;opacity:.6;border:2px solid #b23a2e"></span>${t('ma.outside')} ${P.length-ins}</div>`; }
      else { h+=['in','part','edge','out'].map(k=>{ const g=P.filter(x=>(x.feature.properties||{}).catch_rel===k);
          const v=g.reduce((a,x)=>a+(+(x.feature.properties||{}).volume_m3||0),0);
          return `<div class="row"><span class="sw" style="background:${PM_REL_COL[k]};opacity:.6;border:2px solid ${PM_REL_COL[k]}"></span>${t('pm.rel.'+k)} ${g.length} · ${fmt(v/1e4,1)} ${t('pm.wan')}</div>`; }).join(''); } }
    if(on('pcatch')){ const P=layers.pcatch? layers.pcatch.getLayers():[];
      const n=fn=>P.filter(x=>fn((x.feature.properties||{}).fill_pct)).length;
      h+=`<div class="m"><b>${t('layers.pcatch')}</b><br><span style="opacity:.7">${t('pc.legend')}</span></div>`
        +[[f=>f!=null&&f>=100,'#b91c1c','pc.lFull'],[f=>f!=null&&f>=30&&f<100,'#d97706','pc.lMost'],[f=>f!=null&&f<30,'#2563eb','pc.lIdle'],[f=>f==null,'#94a3b8','pc.noVol']]
          .map(([fn,c,k])=>`<div class="row"><span class="sw" style="background:${c};opacity:.35;border:1.5px solid ${c}"></span>${t(k)} ${n(fn)}</div>`).join('')
        +`<div class="row"><span class="sw" style="background:transparent;border:1.5px dashed #64748b"></span>${t('pc.lEdge')} ${P.filter(x=>(x.feature.properties||{}).touches_edge).length}</div>`; }
    if(on('marea')) h+=`<div class="m"><b>${t('layers.marea')}</b></div>`
      +`<div class="row"><span class="sw" style="background:#0f766e;opacity:.3;border:2px solid #0f766e"></span>`
      +`${(layers.marea?layers.marea.getLayers().length:0)} ${t('lay.unit')}</div>`;
    if(on('cwgap')){ const L3=layers.cwgap? layers.cwgap.getLayers():[];
      const noOut=L3.filter(x=>String((x.feature.properties||{}).outfall||'').indexOf('无登记出口')>=0).length;
      h+=`<div class="m"><b>${t('layers.cwgap')}</b><br><span style="opacity:.7">${t('cw.legendNote')}</span></div>`
        +[[t('bi.cw1'),'#fee6ce'],[t('bi.cw2'),'#fdae6b'],[t('bi.cw3'),'#e6550d'],
          [t('bi.cw4'),'#a63603'],[t('bi.cw5'),'#7f2704']].map(([lab,c])=>
          `<div class="row"><span class="sw" style="background:${c};opacity:.8;border:1px solid ${c}"></span>${lab}</div>`).join('')
        +`<div class="row"><span class="sw" style="background:transparent;border:1.5px dashed #b23a2e"></span>${t('cw.noOutfallShort')} ${noOut}</div>`; }
    if(on('plink')){ const L2=layers.plink? layers.plink.getLayers():[];
      const cnt=k=>L2.filter(x=>k(x.feature.properties||{})).length;
      h+=`<div class="m"><b>${t('layers.plink')}</b></div>`
        +[[t('pl.dig'),'#b45309',p=>p.need_dig&&!p.no_target],
          [t('pl.noDig'),'#15803d',p=>!p.need_dig&&!p.no_target],
          [t('pl.noTarget'),'#64748b',p=>p.no_target]].map(([lab,c,f])=>
          `<div class="row"><span class="sw" style="background:${c};opacity:.6;border:1.5px solid ${c}"></span>${lab} ${cnt(f)}</div>`).join('')
        +`<div class="row"><span class="sw" style="background:transparent;border:0"></span>${t('pl.pump')} ${cnt(p=>p.need_pump)}</div>`; }
    if(on('pond')) h+=`<div class="m"><b>${t('layers.pond')}</b></div>`
      +[['Existing','#0d6b74'],['Active','#15803d'],['FinalHover','#b07a1c']].map(([s,c])=>
        `<div class="row"><span class="sw" style="background:${c};opacity:.6;border:1.5px solid ${c}"></span>${s}</div>`).join('')
      +scopeRow(layers.pond);
    if(on('zones')) h+=`<div class="m"><b>${t('legend.zones')}</b></div>${[['≥55','#b91c1c'],['40–55','#ea580c'],['25–40','#f59e0b'],['10–25','#60a5fa'],['<10','#bfdbfe'],[t('tooSmall'),'#cbd5e1']].map(([x,c])=>`<div class="row"><span class="sw" style="background:${c};opacity:${zoneFillNormal?1:.4}"></span>${x}</div>`).join('')}`;
    if(on('bott')) h+=`<div class="sep"></div><div class="m"><b>${t('legend.bott',{s:scn(main)})}</b></div>${[['≥4','#7f1d1d'],['2–4','#dc2626'],['1.3–2','#f97316'],['1–1.3','#fbbf24']].map(([x,c])=>`<div class="row"><span class="ln" style="border-color:${c}"></span>${x}</div>`).join('')}`;
    if(on('props')) h+=`<div class="sep"></div><div class="m"><b>${t('legend.props')}</b> ${t('legend.propsNote')}</div>`;
    if(on('res')) h+=`<div class="sep"></div><div class="m"><b>${t('legend.res')}</b></div><div class="row"><span class="ln dash" style="border-color:${resStyle['仍超载管段'].color}"></span>${lang==='en'?'Still overloaded pipe':'仍超载管段'}</div><div class="row"><span class="ring" style="border-color:${resStyle['仍高潮暴露出水口'].color}"></span>${lang==='en'?'Still tide-exposed outfall':'仍高潮暴露出水口'}</div><div class="row"><span class="ring" style="border-color:${resStyle['仍疑似漏登终点'].color}"></span>${lang==='en'?'Still suspected unregistered terminal':'仍疑似漏登终点'}</div>`;
    if(on('hs')) h+=`<div class="sep"></div><div class="m"><b>${t('layers.hs')}</b> <span class="m">${t('legend.hsNote')}</span></div><div class="row"><span class="sw" style="background:#7f1d1d"></span>${lang==='en'?'Very High':'极高'}</div><div class="row"><span class="sw" style="background:#dc2626"></span>${lang==='en'?'High':'高'}</div><div class="row"><span class="sw" style="background:#f59e0b"></span>${lang==='en'?'Medium':'中'}</div><div class="row"><span class="sw" style="background:#9ca3af"></span>${lang==='en'?'Low':'低'}</div>`;
    if(on('flood')) h+=`<div class="sep"></div><div class="m"><b>${t('layers.flood')}</b></div><div class="row"><span class="sw" style="background:#7c2d12"></span>${lang==='en'?'dispatchable (served)':'可派泵（已派）'}</div><div class="row"><span class="sw" style="background:#f59e0b"></span>${lang==='en'?'dispatchable (unserved)':'可派泵（未派）'}</div><div class="row"><span class="sw" style="background:#94a3b8"></span>${lang==='en'?'wait / no discharge':'等待雨峰 / 无排放去处'}</div>`;
    if(on('fleet')) h+=`<div class="sep"></div><div class="m"><b>${t('layers.fleet')}</b></div><div class="row"><span class="sw" style="background:#1d4ed8;border-radius:2px"></span>${lang==='en'?'depot (units)':'驻地（泵数）'}</div><div class="row"><span class="ln" style="border-color:#1d4ed8"></span>${lang==='en'?'dispatch route (straight line)':'派车（直线示意）'}</div>`;
    if(on('dmt')) h+=`<div class="sep"></div><div class="m"><b>${t('layers.dmt')}</b> <span class="m">${t('legend.dmtNote')}</span></div><div class="row"><span class="ring" style="border-color:#6d28d9"></span>${t('legend.dmtBoundary')}</div><div class="row"><span class="sw" style="background:#0e7490"></span>${t('legend.dmtPond')}</div><div class="row"><span class="sw" style="background:#ef4444"></span>${t('legend.dmtOverflow')}</div><div class="row"><span class="sw" style="background:#1d4ed8"></span>${t('legend.dmtPump')}</div>`;
    if(on('gwm')) h+=`<div class="sep"></div><div class="m"><b>${t('layers.gwm')}</b> <span class="m">${GW&&GW.scenario?esc(lang==='en'?(GW.scenario.name_en||GW.scenario.name||''):(GW.scenario.name||'')):''}</span></div><div class="row"><span class="sw" style="background:#8b5cf6;opacity:.6;border:2px solid #7f1d1d"></span>${t('legend.gwmBoth')}</div><div class="row"><span class="sw" style="background:#8b5cf6;opacity:.6;border:2px solid #dc2626"></span>${t('legend.gwmHs')}</div><div class="row"><span class="sw" style="background:#8b5cf6;opacity:.6;border:2px solid #0e7490"></span>${t('legend.gwmSys')}</div><div class="row"><span class="sw" style="background:#8b5cf6;opacity:.35;border:2px dashed #7c3aed"></span>${t('legend.gwmOnly')}</div><div class="m">${t('legend.gwmNote')}</div>`;
    if(on('prepo')) h+=`<div class="sep"></div><div class="m"><b>${t('layers.prepo')}</b></div><div class="row"><span class="ring" style="border-color:#0f766e"></span>${lang==='en'?'staging site (pumps · covered points)':'预置驻地（泵数 · 覆盖点）'}</div>`;
    document.getElementById('legend').innerHTML=h; document.getElementById('legend').style.display=h?'block':'none';
  }

  // ---------------- 第二、三道：数据与图层 ----------------
  let DP=null;
  async function getDispatch(){ if(!DP) DP=await load('dispatch.json'); return DP; }
  const floodColor = p => !p.dispatchable ? '#94a3b8' : (p.served ? '#7c2d12' : '#f59e0b');
  const critColor = c => ({'Very High':'#7f1d1d','High':'#dc2626','Medium':'#f59e0b','Low':'#9ca3af'})[c] || '#6b7280';
  const critZh = c => ({'Very High':'极高','High':'高','Medium':'中','Low':'低'})[c]||c;
  // ---------------- DMT 1D 模型导出（模型边界 / 临时塘 / 溢流人孔 / 泵） ----------------
  async function ensureDmtLayer(){
    if(layers.dmt) return;
    let gj; try{ gj=await load('dmt_model.geojson'); }catch(e){ return; }
    const st={boundary:{color:'#6d28d9',weight:2,dashArray:'6 4',fillColor:'#6d28d9',fillOpacity:.05}};
    layers.dmt=L.geoJSON(gj,{renderer:canvas, style:f=>st[f.properties.kind]||{},
      pointToLayer:(f,ll)=>{const k=f.properties.kind; if(k==='pond') return L.circleMarker(ll,{radius:7,color:'#0e7490',weight:2,fillColor:'#a5f3fc',fillOpacity:.9}); if(k==='pump') return L.circleMarker(ll,{radius:4,color:'#fff',weight:1,fillColor:'#1d4ed8',fillOpacity:.95}); return L.circleMarker(ll,{radius:2.5,color:'#ef4444',weight:0,fillColor:'#ef4444',fillOpacity:.55});},
      onEachFeature:(f,l)=>{const p=f.properties; if(p.kind==='boundary') l.bindTooltip(`${t('legend.dmtBoundary')} ${esc(p.model)} · ${fmt(p.area_ha,0)} ha`,{sticky:true}); else if(p.kind==='pond') l.bindTooltip(`${esc(p.label||'')} · ${esc(p.model)}${p.max_stor_m3!=null?` · ${fmt(p.max_stor_m3,0)} m³`:''}`); else if(p.kind==='pump') l.bindTooltip(`${esc(p.label||'')}${p.definition?` · ${esc(p.definition)}`:''}`); else l.bindTooltip(`${esc(p.label||'')}${p.flow_max_lps!=null?` · ${fmt(p.flow_max_lps,0)} L/s`:''}`);}});
    if(on('dmt')) layers.dmt.addTo(map);
  }
  // ---------------- 世界模型推演积水（GWM）× 客户热点 × 本系统：三方对照图层 ----------------
  let GW=null, GWKEY=null;
  async function getGwm(){ const k=String(RP||(health&&health.active_rp)||'10'); if(GW&&GWKEY===k) return GW; GW=await api('/api/gwm_points?rp='+encodeURIComponent(k)); GWKEY=k; return GW; }
  const gwmBorder = a => ({both:'#7f1d1d',hotspot:'#dc2626',system:'#0e7490',model_only:'#7c3aed'})[a]||'#7c3aed';
  const gwmFill = d => d>=1.0?'#4c1d95':(d>=0.6?'#6d28d9':(d>=0.4?'#8b5cf6':'#c4b5fd'));
  const gwmAgreeKey = {both:'gc.agreeBoth',hotspot:'gc.agreeHs',system:'gc.agreeSys',model_only:'gc.agreeOnly'};
  async function ensureGwmLayer(){
    if(layers.gwm) return;
    const g=await getGwm(); if(!g||g.error||!g.geojson) return;
    layers.gwm=L.geoJSON(g.geojson,{renderer:canvas, style:f=>{const p=f.properties; return {color:gwmBorder(p.agreement),weight:p.agreement==='model_only'?1.2:2.2,dashArray:p.agreement==='model_only'?'4 3':null,fillColor:gwmFill(p.max_depth_m||0),fillOpacity:.38};},
      pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:7,color:gwmBorder(f.properties.agreement),weight:2,fillColor:gwmFill(f.properties.max_depth_m||0),fillOpacity:.6}),
      onEachFeature:(f,l)=>{const p=f.properties; l.bindPopup(()=>`<h4>${esc(p.fp_id)} · ${t(gwmAgreeKey[p.agreement]||'gc.agreeOnly')}</h4><div class="m">${esc(lang==='en'?(p.note_en||p.note||''):(p.note||''))}</div>
        <div class="kv"><span>${t('gc.depth')}</span><span>${p.max_depth_m} m · ${fmt(p.volume_m3,0)} m³ · ${fmt((p.area_m2||0)/1e4,0)} ha</span>
        <span>${t('gc.timing')}</span><span>${p.first_wet_min!=null?t('gc.wetFrom',{a:p.first_wet_min,b:p.peak_min}):'—'}</span>
        <span>${t('layers.hs')}</span><span>${p.hs_n?`${p.hs_n}${p.hs_inside_n?` (${p.hs_inside_n} ${t('gc.inside')})`:''}: ${(p.hs_ids||[]).map(id=>`<a href="#" onclick="showHotspot('${esc(id)}');return false">${esc(id)}</a>`).join(', ')}${p.hs_n>(p.hs_ids||[]).length?' …':''}`:t('gc.none')}</span>
        <span>${t('gc.sys')}</span><span>${p.sys_n?`${p.sys_n}: ${(p.sys_ids||[]).map(esc).join(', ')}${p.sys_n>(p.sys_ids||[]).length?' …':''}`:t('gc.none')}</span></div>
        <div class="m">${t('gc.modelNote')}</div>`);}});
    if(on('gwm')) layers.gwm.addTo(map);
  }
  async function refreshGwmLayer(){ if(!layers.gwm) return; const was=map.hasLayer(layers.gwm); map.removeLayer(layers.gwm); layers.gwm=null; if(was){ await ensureGwmLayer(); renderLegend(); } }
  async function showGwmPair(){ await toggleLayer('hs', true); await toggleLayer('gwm', true); if(layers.gwm){ try{ map.fitBounds(layers.gwm.getBounds().pad(0.05)); }catch(e){} } }
  window.showGwmPair=showGwmPair;
  // ---------------- 智能体：本体驱动的交互入口（/api/agent） ----------------
  let AGH=[], AGONT=null, AGBUSY=false, AGSID='console-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,7);
  const agEl=id=>document.getElementById(id);
  function openAgent(){ const a=agEl('agent'); a.hidden=false; document.getElementById('app').classList.add('agent-open'); try{ localStorage.setItem('sw_agent_open','1'); }catch(e){} agEl('agtitle').textContent=t('ag.title'); agEl('agsend').textContent=t('ag.send'); agEl('agin').placeholder=t('ag.placeholder'); agEl('agclose').title=t('ag.close'); loadAgentOntology(); if(!agEl('agmsgs').children.length) agBubble('a', t('ag.hello'), null); agEl('agin').focus(); }
  function closeAgent(){ agEl('agent').hidden=true; document.getElementById('app').classList.remove('agent-open'); try{ localStorage.setItem('sw_agent_open','0'); }catch(e){} }
  // 智能体栏拖宽（320–640 px），记住；左栏收起 / 展开，记住；容器尺寸一变就让 Leaflet 重算
  (function(){ const app=document.getElementById('app'); try{ const w=+localStorage.getItem('sw_agent_w'); if(w>=320&&w<=640) app.style.setProperty('--agent-w-open', w+'px'); }catch(e){}   // 写 --agent-w-open，不能写 --agent-w：内联值会盖过「关闭时归零」的规则，右边就留一条永远收不回去的空栏
    const rz=document.getElementById('agrsz'); let sx=0, sw=0;
    if(rz){ rz.onmousedown=e=>{ sx=e.clientX; sw=document.getElementById('agent').getBoundingClientRect().width; rz.classList.add('on'); document.body.style.userSelect='none'; const mv=ev=>{ const w=Math.max(320, Math.min(640, sw+(sx-ev.clientX))); app.style.setProperty('--agent-w-open', w+'px'); }; const up=()=>{ document.removeEventListener('mousemove',mv); document.removeEventListener('mouseup',up); rz.classList.remove('on'); document.body.style.userSelect=''; try{ localStorage.setItem('sw_agent_w', String(Math.round(document.getElementById('agent').getBoundingClientRect().width))); }catch(e){} }; document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up); e.preventDefault(); }; }
    const sc=document.getElementById('sidecollapse'), se=document.getElementById('sideexpand');
    const setSide=c=>{ app.classList.toggle('side-collapsed', c); try{ localStorage.setItem('sw_side', c?'0':'1'); }catch(e){} };
    if(sc) sc.onclick=()=>setSide(true); if(se) se.onclick=()=>setSide(false);
    try{ if(localStorage.getItem('sw_side')==='0') setSide(true); }catch(e){}
    if(window.ResizeObserver){ let tm=null; new ResizeObserver(()=>{ clearTimeout(tm); tm=setTimeout(()=>{ try{ map.invalidateSize({animate:false}); }catch(e){} }, 60); }).observe(document.getElementById('map')); }
  })();
  async function loadAgentOntology(){ if(AGONT) return AGONT; AGONT=await api('/api/ontology'); if(!AGONT) return null; const D=AGONT.dsh||{}; agEl('agplanner').textContent=AGONT.planner==='dsh'?t('ag.plDsh',{m:D.model||''}):(AGONT.planner==='llm'?t('ag.plLlm'):t('ag.plRules')); agEl('agplanner').title=t('ag.plTip')+(D.error?'\n'+D.error:''); const ob=agEl('agopen'); if(ob){ ob.hidden=!(D.available); ob.textContent=t('ag.openHarness'); ob.title=t('ag.openHarnessTip'); ob.onclick=openHarness; } const ex=agEl('agex'); ex.innerHTML=''; (AGONT.examples||[]).filter(e=>e[lang]||e.zh).slice(0,8).forEach(e=>{ const c=document.createElement('span'); c.className='chip'; c.textContent=e[lang]||e.zh; c.onclick=()=>sendAgent(c.textContent); ex.appendChild(c); }); return AGONT; }

  // 打开 Harness 原生 Web UI（只读）：先问服务要状态，没起就让它起（要等十几秒），再开它带一次性 token 的地址。
  // 地址由服务端在登录会话里给，所以这个入口只有登录进来的人拿得到（2026-10-02）。
  async function openHarness(){
    const ob = agEl('agopen'); if(ob){ ob.disabled = true; ob.textContent = t('ag.harnessStarting'); }
    try {
      let r = await api('/api/harness');
      if(r && !r.running) r = await api('/api/harness?launch=1');
      if(!r || !r.url){ alert(t('ag.harnessFail') + ((r && r.error) ? '\n' + r.error : '')); return; }
      window.open(r.url, '_blank');
    } catch(e){ alert(t('ag.harnessFail') + '\n' + e.message); }
    finally { if(ob){ ob.disabled = false; ob.textContent = t('ag.openHarness'); } }
  }
  // ---- 回答渲染：Markdown（表格 / 标题 / 列表 / 粗体 / 链接，自写小渲染器，不依赖外部库）
  function mdToHtml(src){ if(!src) return ''; const lines=String(src).replace(/\r/g,'').split('\n'); const out=[]; let i=0;
    const inline=s=>{ s=esc(s); s=s.replace(/`([^`]+)`/g,'<code>$1</code>'); s=s.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>'); s=s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g,'$1<i>$2</i>');
      s=s.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^\s)]*)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>'); s=s.replace(/(^|[\s（(，,：:])(https?:\/\/[^\s<）)，,]+)/g,'$1<a href="$2" target="_blank" rel="noopener">$2</a>'); return s; };
    const isTable=x=>/^\s*\|.*\|\s*$/.test(x), isUl=x=>/^\s*[-*•]\s+/.test(x), isOl=x=>/^\s*\d+[.)]\s+/.test(x), isH=x=>/^#{1,6}\s+/.test(x), isHr=x=>/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(x), isQ=x=>/^\s*>\s?/.test(x);
    while(i<lines.length){ const L=lines[i];
      if(/^\s*$/.test(L)){ i++; continue; }
      if(isHr(L)){ out.push('<hr>'); i++; continue; }
      const m=L.match(/^(#{1,6})\s+(.*)$/); if(m){ const lv=Math.min(3,m[1].length); out.push(`<h${lv}>${inline(m[2])}</h${lv}>`); i++; continue; }
      if(isTable(L)){ const rows=[]; while(i<lines.length&&isTable(lines[i])){ rows.push(lines[i]); i++; } const cells=r=>r.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim()); const hdr=cells(rows[0]); const body=rows.slice(1).filter(r=>!/^\s*\|?\s*:?-{2,}/.test(r));
        out.push('<table><thead><tr>'+hdr.map(h=>`<th>${inline(h)}</th>`).join('')+'</tr></thead><tbody>'+body.map(r=>'<tr>'+cells(r).map(c=>`<td>${inline(c)}</td>`).join('')+'</tr>').join('')+'</tbody></table>'); continue; }
      if(isUl(L)){ const it=[]; while(i<lines.length&&isUl(lines[i])){ it.push(lines[i].replace(/^\s*[-*•]\s+/,'')); i++; } out.push('<ul>'+it.map(x=>`<li>${inline(x)}</li>`).join('')+'</ul>'); continue; }
      if(isOl(L)){ const it=[]; while(i<lines.length&&isOl(lines[i])){ it.push(lines[i].replace(/^\s*\d+[.)]\s+/,'')); i++; } out.push('<ol>'+it.map(x=>`<li>${inline(x)}</li>`).join('')+'</ol>'); continue; }
      if(isQ(L)){ const q=[]; while(i<lines.length&&isQ(lines[i])){ q.push(lines[i].replace(/^\s*>\s?/,'')); i++; } out.push(`<blockquote>${inline(q.join(' '))}</blockquote>`); continue; }
      const para=[]; while(i<lines.length&&!/^\s*$/.test(lines[i])&&!isTable(lines[i])&&!isH(lines[i])&&!isUl(lines[i])&&!isOl(lines[i])&&!isHr(lines[i])&&!isQ(lines[i])){ para.push(lines[i]); i++; }
      out.push('<p>'+inline(para.join('\n')).replace(/\n/g,'<br>')+'</p>'); }
    return out.join(''); }
  // ---- 结果卡片：按动作类型把结构化结果画成图 / 表；带编号的都能点，飞到地图并打开详情
  const AG_LVC={III:'#b91c1c',II:'#ea580c',I:'#ca8a04'}, AG_ALC={'蓝':'#2563eb','黄':'#ca8a04','橙':'#ea580c','红':'#b91c1c'};
  const agTmin=v=>(v==null?t('cd.never'):(v>=120?`${(v/60).toFixed(1)} h`:`${Math.round(v)} min`));
  const agAed=v=>(v==null?'—':`${fmt(v,0)} AED`);
  const agLv=l=>l?`<span class="lv-${esc(l)}">${esc(l)}</span>`:'';
  const agBadge=(txt,color)=>`<span class="badge" style="background:${color||'#64748b'}">${esc(txt)}</span>`;
  const agFpl=(id,kind)=>id?`<span class="fplink" data-fp="${esc(id)}" data-kind="${kind||'points'}">${esc(id)}</span>`:'';
  const agCut=(x,n)=>{ x=String(x??''); return x.length>n?x.slice(0,n-1)+'…':x; };
  function agTiles(items){ const it=items.filter(x=>x&&x[1]!=null&&x[1]!==''&&x[1]!=='undefined'); return it.length?`<div class="tiles">${it.map(([k,v,c])=>`<div class="tile"><div class="v"${c?` style="color:${c}"`:''}>${v}</div><div class="k" title="${esc(k)}">${esc(k)}</div></div>`).join('')}</div>`:''; }
  function agBars(items,max){ items=items.filter(Boolean); if(!items.length) return ''; const mx=max||Math.max(1,...items.map(x=>+x.v||0)); return `<div class="bars">${items.map(x=>`<span title="${esc(x.label)}">${esc(x.label)}</span><div class="bar"><i style="width:${Math.max(1,Math.min(100,100*(+x.v||0)/mx)).toFixed(1)}%;background:${x.color||'var(--acc)'}"></i></div><span class="v">${x.text!=null?x.text:fmt(x.v,0)}</span>`).join('')}</div>`; }
  function agPtTable(rows, opts){ opts=opts||{}; rows=(rows||[]).filter(r=>r&&(r.fp_id||r.id)); if(!rows.length) return ''; const kind=opts.kind||'points'; const vol=r=>+(r.volume_m3??r.volume_now_m3??0); const mx=Math.max(1,...rows.map(vol));
    const extraH=opts.extraH||'', extra=opts.extra||(()=>'');
    return `<table class="agtable"><thead><tr><th>${t('cd.id')}</th><th>${t('cd.location')}</th><th>${t('cd.volume')}</th>${extraH}</tr></thead><tbody>${rows.map(r=>{ const id=r.fp_id||r.id; return `<tr class="row" data-fp="${esc(id)}" data-kind="${kind}"><td>${esc(id)} ${agLv(r.level)}${r.criticality?` <span class="m">${esc(r.criticality)}</span>`:''}</td><td title="${esc(r.location||r.zone_name||'')}">${esc(agCut(r.location||r.zone_name||r.area||'',26))}</td><td><span class="minibar" style="width:${Math.max(2,Math.round(60*vol(r)/mx))}px"></span>${vol(r)?fmt(vol(r),0):'—'}${r.max_depth_m!=null?` <span class="m">· ${r.max_depth_m} m</span>`:''}</td>${extra(r)}</tr>`; }).join('')}</tbody></table>
      ${rows.length>1?`<div class="agtools"><span class="chip" data-ids="${esc(rows.map(r=>r.fp_id||r.id).join(','))}" data-kind="${kind}">${t('cd.markAll',{n:rows.length})}</span></div>`:''}`; }
  function agPairs(items, legA, legB){ items=items.filter(x=>x&&(x.a!=null||x.b!=null)); if(!items.length) return ''; const mx=Math.max(1,...items.flatMap(x=>[+x.a||0,+x.b||0]));
    const bar=(v,c,txt)=>`<div><i style="width:${Math.max(1,Math.min(100,100*(+v||0)/mx)).toFixed(1)}%;background:${c}"></i><b>${txt!=null?txt:fmt(v,0)}</b></div>`;
    return `<div class="legend2"><span><i style="background:var(--acc)"></i>${esc(legA)}</span><span><i style="background:#c7cdd6"></i>${esc(legB)}</span></div><div class="pairs">${items.map(x=>`<span title="${esc(x.label)}">${esc(x.label)}</span><div class="pr">${bar(x.a,'var(--acc)',x.aText)}${bar(x.b,'#c7cdd6',x.bText)}</div>`).join('')}</div>`; }
  // 长回答拆成「结论」与「其余」：结论 = 第一个非结论小节标题之前的部分；没有标题就取前两段（≤ 700 字）
  function splitLead(md){ md=String(md||'').replace(/\r/g,''); if(md.length<=700) return [md,'']; const lines=md.split('\n'); let cutAt=-1, seenBody=false;
    for(let i=0;i<lines.length;i++){ const L=lines[i]; const h=/^#{1,6}\s+(.*)$/.exec(L); if(h){ if(seenBody && !/结论|Bottom line|Short answer|Answer|Summary|Conclusion/i.test(h[1])){ cutAt=i; break; } } else if(L.trim()) seenBody=true; }
    if(cutAt<0){ let n=0, paras=0; for(let i=0;i<lines.length;i++){ n+=lines[i].length+1; if(!lines[i].trim()) paras++; if(paras>=2&&n>=220){ cutAt=i; break; } if(n>700){ cutAt=i; break; } } }
    if(cutAt<=0) return [md,''];
    const isTab=x=>/^\s*\|.*\|\s*$/.test(x);
    while(cutAt>0 && isTab(lines[cutAt])) cutAt--;                                    // 不在表格中间切：退到表格开始之前
    while(cutAt>0 && (/[:：]\s*$/.test(lines[cutAt-1]) || /^\*\*[^*]+\*\*[:：]?\s*$/.test(lines[cutAt-1].trim()))) cutAt--;   // 结尾是「依据：」这类引子的行也一起挪到下面
    while(cutAt>0 && !lines[cutAt-1].trim()) cutAt--;
    if(cutAt<=0) return [md,'']; return [lines.slice(0,cutAt).join('\n'), lines.slice(cutAt).join('\n')]; }
  const AG_NEXT={count_points:['q_worst10','q_wmNew'],list_points:['q_speedUp','q_cost'],world_model_new_points:['q_speedUpThese','q_cost'],recession_options:['q_upsize','q_dispatchPoint'],recession_batch:['q_cost','q_report'],cost_summary:['q_prepo','q_report'],rain_to_scenario:['q_prepoOrange','q_howMany','q_worst10'],set_scenario:['q_howMany','q_worst10'],dispatch_plan:['q_unpumpable','q_prepoGain'],preposition_plan:['q_cost','q_report'],zone_overview:['q_zoneWorst','q_zoneSpeed'],inspect_point:['q_recession','q_downstream'],upsize_whatif:['q_recession','q_cost'],compare_world_model:['q_wmNew'],explain_rule:['q_alertLevel'],issue_alert:['q_prepoOrange'],generate_report:[],trace_downstream:['q_recession'],replay_point:['q_recession']};
  function nextChips(r){ const acts=[...new Set((r.steps||[]).map(s=>s.action))]; const keys=[]; acts.slice().reverse().forEach(a=>(AG_NEXT[a]||[]).forEach(k=>{ if(!keys.includes(k)) keys.push(k); })); const fp=((r.ui||{}).hs)||(((r.steps||[]).map(s=>(s.params||{}).fp_id).filter(Boolean))[0]); const zone=((r.steps||[]).map(s=>(s.params||{}).zone).filter(Boolean))[0];
    const asked=new Set(AGH.filter(h=>h.role==='user').map(h=>h.content)); const out=[]; for(const k of keys){ let q=t('ag.'+k,{fp:fp||'ADM-4',zone:zone||''}); if(!q||q.startsWith('ag.')||asked.has(q)) continue; out.push(q); if(out.length>=3) break; } return out; }
  function v2Card(v){ if(!v) return ''; const EN=lang==='en'; const pa=v.pareto||[]; const c=v.cost||{}; const u=v.used||{}, tt=v.totals||{};
    let h=`<div class="m" style="margin-top:6px"><b>${t('v2.title')}</b> · ${esc(EN?(v.objective_en||v.objective):(v.objective_zh||v.objective))} · ${t('v2.win_n',{n:v.n_windows||0})}</div>`;
    if(pa.length) h+=`<table class="agtable"><thead><tr><th>${t('v2.option')}</th><th>${t('v2.cost')}</th><th>${t('dp.served')}</th><th>${t('dp.wfh')}</th></tr></thead><tbody>${pa.map(x=>`<tr${x.selected?' style="font-weight:600;background:var(--acc-soft)"':''}><td>${esc(EN?x.en:x.zh)}</td><td>${fmt(x.cost_aed,0)} AED</td><td>${x.points_served??'—'}</td><td>${fmt(x.weighted_flood_hours,0)}</td></tr>`).join('')}</tbody></table>`;
    h+=agBars([{label:t('v2.duty'),v:c.duty_aed,color:'var(--acc)'},{label:t('v2.crewCost'),v:c.crew_aed,color:'#7c3aed'},{label:t('v2.fuelCost'),v:c.fuel_aed,color:'#ca8a04'},{label:t('v2.vehCost'),v:c.vehicle_aed,color:'#0891b2'},{label:t('v2.tankerCost'),v:c.tanker_aed,color:'#64748b'},{label:t('v2.mobCost'),v:c.mobilisation_aed,color:'#94a3b8'}].filter(x=>x.v));
    h+=agTiles([[t('v2.pumps'),tt.units!=null?`${u.units} / ${tt.units}`:null],[t('v2.crews'),tt.persons!=null?`${u.crews} / ${tt.persons}`:null],[t('dp.tankers'),tt.tankers!=null?`${u.tankers} / ${tt.tankers}`:null],[t('v2.shortLabel'),v.persons_shortage?String(v.persons_shortage):null,'#b91c1c'],[t('v2.relief'),v.relief_persons?String(v.relief_persons):null]]);
    const sp=v.silo_vs_pooled; if(sp&&!sp.error) h+=`<div class="m" style="margin-top:4px"><b>${t('v2.siloTitle')}</b> ${t('v2.silo',{dp:sp.delta_points,cu:sp.cross_zone_units||0,dc:fmt(sp.delta_cost_aed,0)+' AED',sp:(sp.silo||{}).points_served,sc:fmt((sp.silo||{}).cost_aed,0)+' AED',pp:(sp.pooled||{}).points_served,pc:fmt((sp.pooled||{}).cost_aed,0)+' AED'})}</div>`;
    if(v.resources_simulated) h+=`<div class="m">${t('v2.simulated')}</div>`; return h; }
  function agKv(r){ const it=[]; for(const [k,v] of Object.entries(r||{})){ if(k.startsWith('_')||k==='scenario'||k==='fp_id') continue; if(['number','string','boolean'].includes(typeof v)&&String(v).length<40) it.push([k,typeof v==='number'?fmt(v,v%1?2:0):(v===true?'✓':v===false?'✗':esc(String(v)))]); if(it.length>=8) break; } return agTiles(it); }
  function cardFor(st){ const r=st.result; if(!r||typeof r!=='object') return ''; const a=st.action, EN=lang==='en', L=(zh,en)=>EN?(en||zh):(zh||en); let body='', tools='';
    try{ switch(a){
      case 'count_points': { const by=r.by_level||{}, wm=r.world_model||{}, ag=wm.agreement||{}; body=agTiles([[t('cd.clientTotal'),fmt(r.client_total,0)],[t('cd.withState'),fmt(r.with_state,0)],[t('cd.served'),fmt(r.served,0)],[t('cd.dispatchable'),fmt(r.dispatchable,0)],[t('cd.wmConfirmed'),fmt(r.world_model_confirmed,0)],[t('cd.noState'),fmt(r.no_state,0)]])
        +agBars(['III','II','I'].map(k=>({label:t('cd.level')+' '+k,v:by[k]||0,color:AG_LVC[k]})))+(r.client_by_region?agBars(Object.entries(r.client_by_region).map(([k,v])=>({label:k,v,color:'#64748b'}))):'')
        +(wm.cores?`<div class="m">${t('cd.wmCores',{n:fmt(wm.cores,0),m:fmt(ag.model_only||0,0),h:fmt(wm.hotspots_confirmed||0,0),g:fmt(wm.hotspots_in_grid||0,0)})}</div>`:''); break; }
      case 'list_points': { body=`<div class="m">${t('cd.total',{n:fmt(r.total,0)})}</div>`+agPtTable(r.rows,{extraH:`<th>${t('cd.served')}</th>`,extra:x=>`<td>${x.served?'✓':'—'}${x.units_assigned?` ${x.units_assigned}`:''}${x.within_target?' ✓':''}</td>`}); break; }
      case 'world_model_new_points': { body=agTiles([[t('cd.cores'),fmt(r.cores_total,0)],[t('cd.newCores'),fmt(r.new_total,0),'#7c3aed'],[t('cd.newVolume'),`${fmt((r.new_volume_m3||0)/1e4,0)} ${t('cd.wanm3')}`]])
        +agPtTable((r.rows||[]).map(x=>({...x,location:(x.nearest_hotspot||{}).location})),{kind:'gwm',extraH:`<th>${t('cd.nearest')}</th>`,extra:x=>`<td>${x.nearest_hotspot?`${agFpl(x.nearest_hotspot.fp_id)} <span class="m">${fmt(x.nearest_hotspot.dist_m,0)} m</span>`:'—'}${x.nearest_system_point&&x.nearest_system_point.fp_id!==(x.nearest_hotspot||{}).fp_id?` / ${agFpl(x.nearest_system_point.fp_id)}`:''}</td>`}); break; }
      case 'compare_world_model': { const g=(r.stats||{}).gwm||{}, ag=g.agreement||{}, hs=(r.stats||{}).hotspots||{}; body=agTiles([[t('cd.cores'),fmt(g.n,0)],[t('cd.gwmScen'),esc(L((r.gwm_scenario||{}).name,(r.gwm_scenario||{}).name_en))]].concat(Object.entries(hs).filter(([k,v])=>typeof v==='number').slice(0,4).map(([k,v])=>[k,fmt(v,0)])))
        +agBars([['both',t('cd.agBoth'),'#16a34a'],['hotspot',t('cd.agHs'),'#ea580c'],['system',t('cd.agSys'),'#2563eb'],['model_only',t('cd.agModel'),'#7c3aed']].map(([k,lab,c])=>({label:lab,v:ag[k]||0,color:c}))); break; }
      case 'dispatch_plan': { const k=r.kpis||{}, c=r.cost||{}, tk=r.tankers||{}; body=agTiles([[t('cd.pointsServed'),`${fmt(k.points_served,0)} / ${fmt(k.points_total,0)}`],[t('cd.pumps'),fmt(k.units_used,0)],[t('cd.tankers'),`${fmt(k.tankers_used??tk.used,0)}${tk.pool?' / '+fmt(tk.pool,0):''}`],[t('cd.firstArrival'),k.first_arrival_median_min!=null?`${fmt(k.first_arrival_median_min,0)} min`:null],[t('cd.critTarget'),k.critical_total!=null?`${k.critical_within_target} / ${k.critical_total}`:null],[t('cd.cost'),c.total_aed!=null?agAed(c.total_aed):null],[t('cd.decision'),r.decision_time_min!=null?`${fmt(r.decision_time_min,0)} min`:null]])
        +(r.triage&&typeof r.triage==='object'?agBars(Object.entries(r.triage).sort((x,y)=>y[1]-x[1]).map(([lab,v])=>({label:agCut(lab,22),v,color:'#64748b'}))):'')
        +(k.served_by_level?agBars(['III','II','I'].map(l=>({label:t('cd.servedLv')+' '+l,v:(k.served_by_level||{})[l]||0,color:AG_LVC[l],text:`${(k.served_by_level||{})[l]||0} / ${(k.points_by_level||r.levels||{})[l]||0}`}))):'')
        +v2Card(r.v2); break; }
      case 'replan': { const rp=r.replan||{}, k=r.kpis||{}, b=r.base_kpis||{}; body=agTiles([[t('rc.tnow'),`${fmt(r.t_now_min,0)} min`],[t('rc.locked'),rp.locked_units],[t('rc.dry'),(rp.dry_points||[]).length],[t('rc.failed'),(rp.failed_units||[]).length],[t('rc.incidents'),`${Object.keys(rp.incidents_matched||{}).length} / ${rp.incidents_unmatched||0}`],[t('rc.dropped'),rp.n_dropped],[t('rc.newAssign'),r.n_new_assignments]])
        +agBars([{label:t('dp.served'),v:k.points_served,color:'var(--acc)',text:`${k.points_served} (${t('rc.was')} ${b.points_served??'—'})`},{label:t('cd.critTarget'),v:k.critical_within_target,color:'#16a34a',text:`${k.critical_within_target} (${t('rc.was')} ${b.critical_within_target??'—'})`}].filter(x=>x.v!=null))
        +((r.new_assignments||[]).length?`<table class="agtable"><thead><tr><th>${t('dp.unit')}</th><th>${t('dp.to')}</th><th>${t('v2.gDep')}</th><th>${t('v2.gDone')}</th></tr></thead><tbody>${(r.new_assignments||[]).slice(0,12).map(x=>`<tr class="row" data-fp="${esc(x.fp_id)}" data-kind="points"><td>${esc(x.unit_id)} <span class="m">${esc(String(x.model||'').replace('diesel-',''))}</span></td><td>${esc(x.fp_id)}</td><td>${fmt(x.depart_min,0)}</td><td>${x.done_min==null?t('cd.never'):fmt(x.done_min,0)}</td></tr>`).join('')}</tbody></table>`:'')
        +((rp.points_new||[]).length?`<div class="m">${t('rc.newPts')} ${(rp.points_new||[]).map(f=>agFpl(f)).join(' ')}</div>`:'')+((rp.points_no_longer_served||[]).length?`<div class="m">${t('rc.lostPts')} ${(rp.points_no_longer_served||[]).slice(0,12).map(f=>agFpl(f)).join(' ')}</div>`:''); break; }
      case 'point_history': { const H=r.history||{}, Pt=r.point||{}; const rows=H.history||[]; const agz={hit:L('命中','hit'),miss:L('漏报','miss'),false_alarm:L('误报','false alarm'),correct_negative:L('都无','both dry'),unknown:'—'};
        const rec=x=>x.observed_flooded===true?`<b style="color:#b91c1c">${t('lc.flooded')}</b>${x.obs_depth_max_m!=null?` ${x.obs_depth_max_m} m`:''}${x.units_sent!=null?` · ${x.units_sent} ${t('dp.units')}`:''}`:(x.observed_flooded===false?t('lc.dry'):`<span class="m">${t('lc.none')}</span>`);
        const mod=x=>x.model_flooded===true?`${t('lc.modelFlooded')}${x.model_depth_m!=null?` ${x.model_depth_m} m`:''}`:(x.model_flooded===false?t('lc.modelDry'):`<span class="m">${t('lc.notRun')}</span>`);
        body=agTiles([[t('lc.recurrence'),H.recurrence||'—'],[t('lc.recurrenceModel'),H.recurrence_model||'—'],[t('lc.last'),H.last_flooded||'—'],[t('lc.status'),esc(L(H.lifecycle_status_zh,H.lifecycle_status_en)||'')],[t('lc.treatment'),esc(Pt.treatment_status||'')]])
          +(rows.length?`<table><tr><th>${t('lc.event')}</th><th>${t('lc.rain')}</th><th>${t('lc.record')}</th><th>${t('lc.model')}</th><th>${t('lc.agree')}</th><th>${t('lc.stage')}</th></tr>${rows.map(x=>`<tr><td>${esc(L(x.label,x.label_en)||x.event_id)}<div class="m">${esc(x.date||'')}</div></td><td>${fmt(x.p24_mm,0)} mm<div class="m">≈ ${fmt(x.rp_equiv_yr,0)} ${t('yr')}</div></td><td>${rec(x)}</td><td>${mod(x)}</td><td>${agz[x.agreement]||'—'}</td><td>${esc(L(x.status_zh,x.status_en)||'')}</td></tr>`).join('')}</table>`:'')
          +(!H.n_observed&&rows.length?`<div class="m">${t('lc.estimateNote')}</div>`:''); break; }
      case 'event_review': { const E=r.event||{}, V=r.verification||{}; const bs=r.by_status_zh||r.by_status||{};
        body=agTiles([[t('lc.episodes'),E.n_episodes],[t('lc.modelFloodedN'),E.n_model_flooded],[t('lc.obsFlooded'),E.n_observed_flooded],[t('lc.pod'),V.pod==null?'—':fmt(V.pod,2)],[t('lc.far'),V.far==null?'—':fmt(V.far,2)],[t('lc.verdict'),esc(V.verdict||''),V.verdict==='fail'?'#b91c1c':(V.verdict==='pass'?'#166534':null)]])
          +agBars(Object.entries(r.by_status||{}).sort((a,b)=>b[1]-a[1]).map(([k,n])=>({label:(r.by_status_zh&&!EN)?(Object.keys(r.by_status_zh).find(z=>r.by_status_zh[z]===n&&true)||k):k,v:n})))
          +((r.misses||[]).length?`<div class="m">${t('lc.misses')}: ${r.misses.map(m=>`<span class="chip click" data-fp="${esc(m.fp_id)}">${esc(m.fp_id)}</span>`).join(' ')}</div>`:'')+((r.false_alarms||[]).length?`<div class="m">${t('lc.fas')}: ${r.false_alarms.map(m=>`<span class="chip click" data-fp="${esc(m.fp_id)}">${esc(m.fp_id)}</span>`).join(' ')}</div>`:''); break; }
      case 'lifecycle_overview': { const E=r.event||{}; body=agTiles([[t('lc.episodes'),r.n_episodes],[t('lc.active'),r.n_active,r.n_active?'#b45309':null],[t('lc.closed'),r.n_closed]])+agBars((r.funnel||[]).map(x=>({label:L(x.zh,x.en),v:x.n,color:'#7c3aed'})))
          +((r.active||[]).length?`<div class="m">${t('lc.active')}: ${r.active.map(a=>`<span class="chip click" data-fp="${esc(a.fp_id)}">${esc(a.fp_id)} · ${esc(a.status_zh||a.status)}</span>`).join(' ')}</div>`:''); break; }
      case 'update_episode': { const ep=r.episode||{}, rc=r.record||{}; body=agTiles([[t('lc.logged'),esc(rc.stage||'')],[t('lc.stage'),esc(L(ep.status_zh,ep.status_en)||'')],[t('lc.depth'),rc.depth_m!=null?`${rc.depth_m} m`:null],[t('lc.units'),rc.units]]); break; }
      case 'close_point': { const ep=r.episode||{}; body=agTiles([[t('lc.was'),esc(r.was||'')],[t('lc.now'),esc(L(ep.status_zh,ep.status_en)||'')],[t('lc.event'),esc(r.event_id||'')]]); break; }
      case 'set_proposal_status': { const p=r.proposal||{}; body=agTiles([[t('lc.proposal'),`#${p.pid}`],[t('lc.was'),esc(r.was||'')],[t('lc.now'),esc(p.status||'')]])+((r.points||[]).length?`<div class="m">${r.points.map(x=>`<span class="chip click" data-fp="${esc(x.fp_id)}">${esc(x.fp_id)} · ${esc(x.treatment_status)}</span>`).join(' ')}</div>`:''); break; }
      case 'open_event': { const E=r.event||{}; body=agTiles([[t('lc.opened'),esc(E.event_id||'')],[t('lc.rain'),`${fmt(E.p24_mm,0)} mm`],[t('lc.rp'),fmt(E.rp_equiv_yr,0)],[t('lc.alerted'),r.n_alerted,'#b45309']]); break; }
      case 'assumptions': { const lv={simulated:'#b45309',assumed:'#7c3aed',placeholder:'#0891b2',unverified:'#64748b',missing:'#b91c1c'}; ASM=r; body=agTiles(Object.entries((r.summary||{}).by_level||{}).map(([k,v])=>[t('as.'+k),v,lv[k]]))+`<div class="m">${(r.items||[]).slice(0,8).map(x=>`<div>· <span style="color:${lv[x.level]||'#333'}">${esc(t('as.'+x.level))}</span> ${esc(L(x.what,x.what_en))}</div>`).join('')}</div>`; tools=`<span class="chip click" data-asm="1">${t('as.title')}</span>`; break; }
      case 'traffic_status': { const m=r.meta||{}; const prov=m.provider||((r.status||{}).effective); const src=prov==='google'?t('v2.trfGoogle'):(prov==='none'?t('v2.trfOff'):t('v2.trfSim'));
        body=agTiles([[t('tr.provider'),esc(src),prov==='google'?'#166534':null],[t('tr.range'),`${fmt(r.factor_min,2)}–${fmt(r.factor_max,2)}`],[t('tr.dep'),esc(m.departure_local||'')],[t('tr.elements'),prov==='google'?(m.elements??0):null]])
          +agBars((r.by_depot||[]).filter(x=>x.n).slice(0,8).map(x=>({label:String(x.name||x.depot_id).split('·')[0].slice(0,18),v:x.factor,text:'×'+fmt(x.factor,2),color:'#7c3aed'})),Math.max(1.5,+r.factor_max||1))
          +((r.refine&&r.refine.refined)?`<div class="m">${t('v2.trfRefine',{n:r.refine.n_pairs,m:fmt(r.refine.delta_mean_min,1),s:r.refine.n_slower_10min})}</div>`:''); break; }
      case 'feeds_status': { const F=r.feeds||{}; body=`<table><tr><th>${t('fd.name')}</th><th>${t('fd.mode')}</th><th>${t('fd.rows')}</th><th>${t('fd.asof')}</th></tr>${Object.entries(F).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v.url?('pull '+v.url):('push '+v.push_endpoint))}</td><td>${v.rows??0}</td><td>${esc(v.as_of||v.mtime||'—')}</td></tr>`).join('')}</table>`; break; }
      case 'stress_test': { body=agTiles([[t('st.worse'),esc(L(r.worse_label,r.worse_label_en)||'')],[t('st.onTarget'),`${r.within_target_now??'—'} → ${r.within_target_worse}`,'#b91c1c'],[t('st.never'),r.never_empties_worse],[t('st.gapPts'),r.points_with_gap],[t('st.extra'),r.extra_12in_equiv_total]])
        +agPtTable((r.worst||[]).filter(w=>w.rate_gap_m3h>0).map(w=>({...w,volume_m3:w.volume_peak_m3,location:`${t('st.gap')} ${fmt(w.rate_gap_m3h,0)} m³/h`})),{extraH:`<th>${t('st.extra')}</th>`,extra:w=>`<td>+${w.extra_12in_equiv} × 12in${w.never_empties?' · '+t('cd.never'):''}</td>`}); break; }
      case 'staging_status': { const tt=r.totals||{}; body=agTiles([[t('stg.need'),fmt(tt.need_units,0)],[t('stg.erp'),fmt(tt.erp_plan_units,0)],[t('stg.checkin'),tt.checked_in_units==null?t('stg.none'):fmt(tt.checked_in_units,0)],[t('stg.readiness'),tt.readiness_pct==null?'—':`${tt.readiness_pct}%`,tt.readiness_pct!=null&&tt.readiness_pct<80?'#b91c1c':null],[t('stg.shortDepots'),tt.depots_short_vs_plan]])
        +`<table class="agtable"><thead><tr><th>${t('v2.depot')}</th><th>${t('stg.need')}</th><th>${t('stg.erp')}</th><th>${t('stg.checkin')}</th></tr></thead><tbody>${(r.rows||[]).slice().sort((a,b)=>b.need_units-a.need_units).slice(0,10).map(x=>`<tr><td title="${esc(x.name||'')}">${esc(String(x.name||x.depot_id).split('·')[0].slice(0,20))}</td><td>${x.need_units}</td><td${x.gap_vs_plan<0?' style="color:var(--warn);font-weight:600"':''}>${x.erp_plan_units}</td><td>${x.checked_in_units==null?'—':x.checked_in_units}</td></tr>`).join('')}</tbody></table>`+(r.plan_only?`<div class="m">${t('stg.planOnly')}</div>`:''); break; }
      case 'resource_check': { const tt=r.totals||{}, u=r.used||{}, g=r.gaps||{}; body=agTiles([[t('v2.pumps'),`${u.units??'—'} / ${tt.units??'—'}`],[t('v2.crews'),`${u.crews??'—'} / ${tt.persons??'—'}`],[t('dp.tankers'),`${u.tankers??'—'} / ${tt.tankers??'—'}`],[t('v2.hose'),`${fmt(u.hose_m,0)} / ${fmt(tt.hose_m,0)} m`],[t('v2.fuel'),`${fmt(u.fuel_l,0)} / ${fmt(tt.fuel_l,0)} L`],[t('v2.vehLabel'),`${tt.vehicles_heavy??0} + ${tt.vehicles_light??0}`],[t('v2.shortLabel'),g.persons_shortage!=null?String(g.persons_shortage):null,g.persons_shortage?'#b91c1c':null],[t('v2.relief'),g.relief_persons_at_shift_change!=null?String(g.relief_persons_at_shift_change):null]])
        +agBars([{label:t('v2.pumps'),v:u.units,color:'var(--acc)',text:`${u.units}/${tt.units}`},{label:t('v2.crews'),v:u.crews,color:'#7c3aed',text:`${u.crews}/${tt.persons}`},{label:t('dp.tankers'),v:u.tankers,color:'#0891b2',text:`${u.tankers}/${tt.tankers}`}].map(x=>({...x,v:100*(x.v||0)/Math.max(1,(x.label===t('v2.pumps')?tt.units:(x.label===t('v2.crews')?tt.persons:tt.tankers))||1)})),100)
        +((r.by_depot||[]).length?`<table class="agtable"><thead><tr><th>${t('v2.depot')}</th><th>${t('v2.pumps')}</th><th>${t('v2.crews')}</th><th>${t('dp.tankers')}</th></tr></thead><tbody>${(r.by_depot||[]).slice(0,10).map(x=>`<tr><td title="${esc(x.name||'')}">${esc(String(x.name||x.depot_id).split('·')[0].slice(0,20))}</td><td>${x.units_used}/${x.units_total}</td><td>${x.crews_used}/${x.crews_total}</td><td>${x.tankers_used}/${x.tankers_total}</td></tr>`).join('')}</tbody></table>`:'')
        +(r.simulated?`<div class="m">${t('v2.simulated')}</div>`:''); break; }
      case 'preposition_plan': { const v=r.value||{}, wt=v.within_target||{}, l3=v.level_iii_served||{}, fh=v.served_flood_h||{}; body=agTiles([[t('cd.alert'),agBadge(r.alert_level||'',AG_ALC[r.alert_level])],[t('cd.sites'),fmt((r.sites||[]).length,0)],[t('cd.units'),fmt(v.units_placed,0)],[t('cd.covered'),`${fmt(r.covered,0)} / ${fmt(r.points,0)}`],[t('cd.arrive'),v.arrive_median_min!=null?`${fmt(v.arrive_median_min,0)} min`:null],[t('cd.saved'),v.saved_vs_after_pct!=null?`${v.saved_vs_after_pct}%`:null,'#16a34a'],[t('cd.cost'),v.cost?agAed(v.cost.total_aed):null]])
        +agPairs([{label:t('cd.servedPts'),a:wt.pre_n,b:wt.after_n},{label:t('cd.withinTarget'),a:wt.pre,b:wt.after},{label:t('cd.iiiServed'),a:l3.pre,b:l3.after},(fh.pre!=null&&fh.after!=null?{label:t('cd.floodHours'),a:fh.after,b:fh.pre,aText:`${fmt(fh.after,0)} h`,bText:`${fmt(fh.pre,0)} h`}:null)].filter(Boolean), t('cd.staged'), t('cd.afterEvent'))
        +((r.sites||[]).length?`<table class="agtable"><thead><tr><th>${t('cd.site')}</th><th>${t('cd.units')}</th><th>${t('cd.covers')}</th></tr></thead><tbody>${(r.sites||[]).map(x=>`<tr class="row" data-ids="${esc((x.covers||[]).join(','))}" data-kind="points" data-ll="${x.lon!=null&&x.lat!=null?`${x.lon},${x.lat}`:''}" title="${esc(x.name||'')}"><td>${esc(agCut(x.name||x.site_id,24))}</td><td>${fmt(x.units_suggested,0)}</td><td>${fmt(x.n_covers??(x.covers||[]).length,0)}</td></tr>`).join('')}</tbody></table><div class="m">${t('cd.siteHint')}</div>`:''); break; }
      case 'recession_options': { const P=r.point||{}, opts=r.options||[], costs=r.costs_aed||{}, sv=r.single_vs_global||{}; const mx=Math.max(1,...opts.map(o=>o.t_empty_min==null?2880:o.t_empty_min));
        body=agTiles([[t('cd.level'),agLv(P.level)],[t('cd.criticality'),esc(P.criticality||'')],[t('cd.target'),r.target_drain_min!=null?`${fmt(r.target_drain_min,0)} min`:null],[t('cd.fastest'),r.fastest?esc(L((opts.find(o=>o.key===r.fastest)||{}).label_zh,(opts.find(o=>o.key===r.fastest)||{}).label_en)||r.fastest):t('cd.none')],[t('cd.dispatchable'),P.dispatchable===true?'✓':(P.dispatchable===false?'✗':null)],[t('cd.singleDelta'),sv.delta_min!=null?`${fmt(sv.delta_min,0)} min`:null]])
          +agBars(opts.map(o=>({label:L(o.label_zh,o.label_en),v:o.t_empty_min==null?mx:o.t_empty_min,color:o.t_empty_min==null?'#d1d5db':(o.key===r.fastest?'var(--acc)':(o.within_target?'#16a34a':'#94a3b8')),text:`${agTmin(o.t_empty_min)}${o.n_units?` · ${o.n_units} ${t('cd.unitsShort')}`:''}${costs[o.key]?` · ${agAed(costs[o.key])}`:''}${o.within_target?' ✓':''}`})),mx).replace('</div>',`<span class="tgt">${t('cd.targetLine',{t:fmt(r.target_drain_min,0),d:r.deadline_min!=null?fmt(r.deadline_min,0):'—'})}</span></div>`)
          +(P.triage?`<div class="m">${esc(zE(P.triage))}</div>`:'')+((sv.displaced_points||[]).length?`<div class="m">${t('cd.displaced',{n:sv.displaced_points.length})} ${sv.displaced_points.map(x=>agFpl(x)).join(' ')}</div>`:'')
          +(r.single_vs_global_strict&&!r.single_vs_global_strict.error?(function(st){ return `<div class="m" style="margin-top:4px;padding:4px 8px;border-left:3px solid #7c3aed"><b>${t('cd.strictTitle')}</b> ${t('cd.strict',{g:st.global&&st.global.done_min!=null?agTmin(st.global.done_min):t('cd.none'),f:st.focus&&st.focus.done_min!=null?agTmin(st.focus.done_min):t('cd.none'),u:(st.units_taken_from_global||[]).length,d:st.n_displaced||0,c:fmt(st.delta_cost_aed,0)})} ${(st.units_taken_from_global||[]).slice(0,6).map(x=>`<span class="chip" data-fp="${esc(x.from_point)}">${esc(x.unit_id)}←${esc(x.from_point)}</span>`).join(' ')}</div>`; })(r.single_vs_global_strict):'');
        tools=`<span class="chip" data-fp="${esc(r.fp_id)}" data-kind="points">${t('cd.showPoint',{id:esc(r.fp_id)})}</span>`; break; }
      case 'recession_batch': case 'cost_summary': { const g=r.aggregate||{}; body=agTiles([[t('cd.points'),fmt(g.n,0)],[t('cd.meetTarget'),fmt(g.meet_target,0),'#16a34a'],[t('cd.neverWithout'),fmt(g.never_recede_without_action,0),'#b91c1c'],[t('cd.units'),fmt(g.units_total,0)],[t('cd.opsCost'),g.ops_cost_total_aed!=null?agAed(g.ops_cost_total_aed):null],[t('cd.prepoCost'),g.prepo_cost_total_aed?agAed(g.prepo_cost_total_aed):null],[t('cd.upsizeCost'),g.upsize_cost_total_aed?agAed(g.upsize_cost_total_aed):null]])
        +(g.fastest_kinds?agBars(Object.entries(g.fastest_kinds).map(([k,v])=>({label:k,v,color:k==='none'?'#d1d5db':'var(--acc)'}))):'')
        +agPtTable(r.items,{extraH:`<th>${t('cd.fastest')}</th>`,extra:x=>`<td>${x.fastest?`${esc(L(x.fastest_label_zh,x.fastest_label_en)||x.fastest)} · ${agTmin(x.fastest_t_empty_min)}${x.fastest_cost_aed?` · ${agAed(x.fastest_cost_aed)}`:''}${x.fastest_within_target?' ✓':''}`:t('cd.none')}</td>`}); break; }
      case 'upsize_whatif': { const b=r.base_kpi||{}, dn0=(r.reach||{}).dn_mm; const items=[{label:`DN${fmt(dn0,0)} ${t('cd.now')}`,v:b.peak_m3,color:'#94a3b8',text:`${fmt(b.peak_m3,0)} m³ · ${agTmin(b.t_empty_min)}`}].concat((r.upsized||[]).map(u=>({label:`DN${fmt(u.dn_mm,0)}`,v:(u.kpi||{}).peak_m3,color:'var(--acc)',text:`${fmt((u.kpi||{}).peak_m3,0)} m³ · ${agTmin((u.kpi||{}).t_empty_min)}${u.shortfall_m3?` · ${t('cd.shortfall')} ${fmt(u.shortfall_m3,0)}`:''}`})));
        body=(r.no_gap?`<div class="m">${t('cd.noGap')}</div>`:'')+agBars(items); tools=`<span class="chip" data-fp="${esc(r.fp_id)}" data-kind="points">${t('cd.showPoint',{id:esc(r.fp_id)})}</span>`; break; }
      case 'rain_to_scenario': { const rp=r.return_period||{}, n=r.nearest||{}, al=r.alert||{}, tab=rp.table||[]; const lv=al.with_scenario_rules; body=agTiles([[t('cd.forecast'),`${fmt(r.p24_mm,0)} mm`],[t('cd.rp'),rp.years_est!=null?`≈ ${fmt(rp.years_est,0)} ${t('cd.yr')}`:(rp.bracket?`${rp.bracket[0]} ${rp.bracket[1]} ${t('cd.yr')}`:null)],[t('cd.nearest'),esc(L(n.label,n.label_en)||'')],[t('cd.alert'),lv?agBadge(lv,AG_ALC[lv]):null],[t('cd.window'),r.preposition_window?t('cd.win_'+r.preposition_window):null]]);
        if(tab.length>=2){ const W=420,H=150,ml=34,mr=10,mt=10,mb=24; const xs=tab.map(x=>Math.log(x.rp)); const x0=Math.min(...xs),x1=Math.max(...xs); const ys=tab.map(x=>x.p24_mm).concat([r.p24_mm]); const y0=0,y1=Math.max(...ys)*1.1; const X=v=>ml+(W-ml-mr)*(Math.log(v)-x0)/(x1-x0||1), Y=v=>mt+(H-mt-mb)*(1-(v-y0)/(y1-y0||1));
          const pts=tab.map(x=>`${X(x.rp).toFixed(1)},${Y(x.p24_mm).toFixed(1)}`).join(' '); const est=rp.years_est; const cands=(r.design_candidates||[]).filter(c=>c.kind==='rp_cc');
          body+=`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"><line x1="${ml}" y1="${Y(0)}" x2="${W-mr}" y2="${Y(0)}" stroke="#cbd5e1"/><polyline points="${pts}" fill="none" stroke="#64748b" stroke-width="1.5"/>${tab.map(x=>`<circle cx="${X(x.rp)}" cy="${Y(x.p24_mm)}" r="3" fill="#64748b"/><text x="${X(x.rp)}" y="${H-8}" font-size="9" text-anchor="middle" fill="#64748b">${x.rp}${EN?'y':'年'}</text>`).join('')}
            <line x1="${ml}" y1="${Y(r.p24_mm)}" x2="${W-mr}" y2="${Y(r.p24_mm)}" stroke="#b91c1c" stroke-dasharray="4 3"/><text x="${W-mr}" y="${Y(r.p24_mm)-3}" font-size="9" text-anchor="end" fill="#b91c1c">${t('cd.forecast')} ${fmt(r.p24_mm,0)} mm</text>
            ${est&&est>=Math.exp(x0)&&est<=Math.exp(x1)?`<line x1="${X(est)}" y1="${Y(r.p24_mm)}" x2="${X(est)}" y2="${Y(0)}" stroke="#b91c1c" stroke-dasharray="2 2"/><text x="${X(est)}" y="${Y(r.p24_mm)-12}" font-size="9" text-anchor="middle" fill="#b91c1c">≈${fmt(est,0)}${EN?'y':'年'}</text>`:''}
            ${cands.map(c=>`<circle cx="${X(Math.max(Math.exp(x0),Math.min(Math.exp(x1),est||c.p24_mm)))}" cy="${Y(c.p24_mm)}" r="4" fill="var(--acc)"/>`).join('')}
            ${n.p24_mm!=null?`<text x="${ml+4}" y="${Y(n.p24_mm)-3}" font-size="9" fill="var(--acc)">${esc(L(n.label,n.label_en))} ${fmt(n.p24_mm,1)} mm</text>`:''}<text x="4" y="${Y(y1/1.1)+3}" font-size="9" fill="#64748b">mm</text></svg>`; }
        if((al.triggers||[]).length) body+=`<table class="agtable"><thead><tr><th>${t('cd.rule')}</th><th>${t('cd.value')}</th><th>${t('bi.yor')}</th><th>${t('cd.level')}</th></tr></thead><tbody>${al.triggers.map(g=>{ const th=g.thresholds||{}; const f=k=>(th[k]>=1e6?fmt(th[k]/1e4,0)+(EN?'e4':' 万'):fmt(th[k],0)); return `<tr><td>${esc(t('cd.rule_'+g.rule)||g.rule)}</td><td>${g.value!=null?(g.value>=1e6?fmt(g.value/1e4,1)+(EN?'e4 m³':' 万 m³'):fmt(g.value,1)):'—'}</td><td class="m">${f('黄')} / ${f('橙')} / ${f('红')}</td><td>${agBadge(g.level,AG_ALC[g.level])}</td></tr>`; }).join('')}</tbody></table>`;
        if(r.applied&&n.key) tools=`<span class="chip" data-reui='${esc(JSON.stringify({rp:n.key}))}'>${t('cd.switchTo',{s:esc(L(n.label,n.label_en))})}</span>`; break; }
      case 'set_scenario': { const sc=r.scenario||{}, by=sc.points_by_level||{}; body=agTiles([[t('cd.scenario'),esc(L(sc.label,sc.label_en))],['24 h',sc.p24_mm!=null?`${fmt(sc.p24_mm,1)} mm`:null],['15 min',sc.i15_mmh!=null?`${fmt(sc.i15_mmh,1)} mm/h`:null],[t('cd.points'),fmt(sc.n_points,0)],[t('cd.alert'),sc.alert_level?agBadge(sc.alert_level,AG_ALC[sc.alert_level]):null],[t('cd.spill'),sc.spill_total_m3!=null?`${fmt(sc.spill_total_m3/1e4,1)} ${t('cd.wanm3')}`:null]])+agBars(['III','II','I'].map(k=>({label:t('cd.level')+' '+k,v:by[k]||0,color:AG_LVC[k]}))); break; }
      case 'zone_overview': { const z=r.zone||{}, by=r.by_level||{}; body=agTiles([[t('cd.zoneKind'),z.kind==='area'?t('cd.zoneArea'):t('cd.zoneNetwork')],[t('cd.points'),fmt(z.n_flood_points,0)],[t('cd.withState'),fmt(r.n_states,0)],[t('cd.served'),fmt(r.n_served,0)],[t('cd.risk'),z.risk_score!=null?fmt(z.risk_score,1):null],[t('cd.lenKm'),z.len_km!=null?`${fmt(z.len_km,1)} km`:null],[t('cd.storageDef'),z.storage_deficit_m3!=null?`${fmt(z.storage_deficit_m3,0)} m³`:null]])
        +agBars(['III','II','I'].map(k=>({label:t('cd.level')+' '+k,v:by[k]||0,color:AG_LVC[k]})).filter(x=>x.v))+agPtTable(r.top_points,{extraH:`<th>${t('cd.served')}</th>`,extra:x=>`<td>${x.served?'✓':'—'}</td>`}); break; }
      case 'inspect_point': { const P=r.point||{}, S=r.state||{}; body=agTiles([[t('cd.level'),agLv(S.level)],[t('cd.criticality'),esc(P.criticality||'')],[t('cd.volume'),S.volume_m3!=null?`${fmt(S.volume_m3,0)} m³`:null],[t('cd.depth'),S.max_depth_m!=null?`${S.max_depth_m} m`:null],[t('cd.target'),S.target_drain_min!=null?`${fmt(S.target_drain_min,0)} min`:null],[t('cd.dispatchable'),S.dispatchable===true?'✓':(S.dispatchable===false?'✗':null)],[t('cd.priority'),S.priority!=null?fmt(S.priority,0):null],[t('cd.zone'),esc((r.zone||{}).zone_name||P.area||'')]])+(S.triage?`<div class="m">${esc(zE(S.triage))}</div>`:'')+((r.assignments||[]).length?`<div class="m">${t('cd.assigned',{n:r.assignments.length})}</div>`:'');
        tools=`<span class="chip" data-fp="${esc(P.fp_id||r.fp_id||(st.params||{}).fp_id)}" data-kind="points">${t('cd.showPoint',{id:esc(P.fp_id||(st.params||{}).fp_id||'')})}</span>`; break; }
      case 'asset_kind': { const EN=lang==='en'; const img=r.schematic||r.photo;
        body=(img?`<img src="${esc(img)}" alt="" style="width:100%;border:1px solid var(--line);border-radius:6px;background:#fff">`:'')
          +`<div class="m" style="margin-top:4px">${(r.functions||[]).map(f=>`<span class="chip">${esc(EN?f.en:f.zh)}</span>`).join(' ')} · ${esc(r.level_user||r.level_a||r.level_b||'')}</div>`
          +(r.photo&&r.schematic?`<img src="${esc(r.photo)}" alt="" style="max-width:48%;margin-top:4px;border-radius:4px">`:''); break; }
      case 'explain_rule': { const R=r.rule||{}; body=`<div class="ont-rule"><b>${esc(L(R.zh,R.en))}</b> <span class="m">· ${esc(R.kind||'')} · ${esc(R.applies_to||'')}</span><div>${esc(L(R.logic_zh,R.logic_en)||'')}</div>${R.source?`<div class="m">${t('ont.source')}: ${esc(R.source)}</div>`:''}${(r.derives||[]).length?`<div class="m">→ ${r.derives.map(esc).join(', ')}</div>`:''}</div>`; break; }
      case 'trace_downstream': { body=agTiles([[t('cd.downstream'),r.downstream_n!=null?`${fmt(r.downstream_n,0)} · ${fmt(r.downstream_km,1)} km`:null],[t('cd.upstream'),r.upstream_n!=null?`${fmt(r.upstream_n,0)} · ${fmt(r.upstream_km,1)} km`:null],[t('cd.terminal'),esc(r.terminal_kind||'')]]); tools=`<span class="chip" data-fp="${esc(r.fp_id)}" data-kind="points">${t('cd.showPoint',{id:esc(r.fp_id)})}</span>`; break; }
      case 'generate_report': { const u=r.url||(st.ui||{}).url; body=`<div class="m">${esc(r.path||r.file||'')}</div>`; if(u) tools=`<span class="chip" data-url="${esc(u)}">${t('cd.openReport')}</span>`; break; }
      case 'issue_alert': { body=agTiles([[t('cd.alert'),r.level?agBadge(r.level,AG_ALC[r.level]):null],['24 h',r.p24_mm!=null?`${fmt(r.p24_mm,0)} mm`:null],[t('cd.scenario'),esc(L((r.scenario||{}).label,(r.scenario||{}).label_en)||r.scenario_key||'')]])+agKv({file:r.file||r.path}); break; }
      default: { body=agKv(r); if(r.rows) body+=agPtTable(r.rows); if(r.fp_id&&!tools) tools=`<span class="chip" data-fp="${esc(r.fp_id)}" data-kind="points">${t('cd.showPoint',{id:esc(r.fp_id)})}</span>`; }
    } }catch(e){ console.warn('card', a, e); body=''; }
    if(!body&&!tools) return '';
    const A=(AGONT&&AGONT.actions||[]).find(x=>x.api===a); const title=A?(EN?A.en:A.zh):(EN?(st.en||a):(st.zh||a));
    const pr=Object.entries(st.params||{}).filter(([k,v])=>v!=null&&v!=='').slice(0,3).map(([k,v])=>`${k}=${agCut(String(v),18)}`).join(' · ');
    return `<div class="agcard"><div class="h"><span>${esc(title)}</span><span class="m" title="${esc(pr)}">${esc(agCut(pr,40))}</span></div><div class="b">${body}${tools?`<div class="agtools">${tools}</div>`:''}</div></div>`; }
  // ---- 地图交互（卡片 / 芯片点击）
  async function focusPoint(id, kind){ kind=kind||'points';
    if(kind==='fac'){ const h=(HL||[]).find(x=>x.id===id); if(h) map.setView([h.lat,h.lon], Math.max(map.getZoom(),16)); return; }   // 设施只在页内高亮过，按坐标飞过去
    const items=await resolveHighlight([id], kind); if(items.length){ highlightPoints(items, false); map.setView([items[0].lat,items[0].lon], Math.max(map.getZoom(),15)); } if(kind!=='gwm') await showHotspot(id); }
  async function highlightIds(ids, kind){ const items=await resolveHighlight(ids, kind||'points'); if(items.length) highlightPoints(items, true); }
  function agBubble(kind, text, r){ const box=agEl('agmsgs'); const d=document.createElement('div'); d.className='amsg '+kind;
    let rest='';
    if(kind==='a'&&r){ const [lead,more]=splitLead(text||''); rest=more; const md=document.createElement('div'); md.className='md lead'; md.innerHTML=mdToHtml(lead); d.appendChild(md); } else { d.textContent=text||''; }
    if(r&&r.steps&&r.steps.length){ const cards=r.steps.map(cardFor).filter(Boolean).join(''); if(cards){ const c=document.createElement('div'); c.className='cards'; c.innerHTML=cards; c.querySelectorAll('.agcard .h').forEach(h=>{ h.insertAdjacentHTML('beforeend','<span class="tg">▾</span>'); h.onclick=ev=>{ if(ev.target.closest('.chip,[data-fp],[data-ids]')) return; const card=h.parentElement; card.classList.toggle('closed'); h.querySelector('.tg').textContent=card.classList.contains('closed')?'▸':'▾'; }; }); d.appendChild(c); } }
    if(rest){ const dt=document.createElement('details'); dt.className='full'; dt.innerHTML=`<summary>${t('ag.showFull',{n:fmt(rest.length,0)})}</summary><div class="md">${mdToHtml(rest)}</div>`; d.appendChild(dt); }
    const hl=(r&&r.ui&&Array.isArray(r.ui.highlight))?r.ui.highlight:null; if(hl&&hl.length){ const w=document.createElement('div'); w.className='hlchips'; w.innerHTML=`<span class="m">${t('ag.onMap',{n:hl.length})}</span> `+hl.map((h,i)=>`<span class="chip" data-fp="${esc(h.id)}" data-kind="${esc(h.kind||'points')}" style="border-color:${hlColor(h.kind)}">${i+1}. ${esc(h.label||h.id)}</span>`).join(' '); d.appendChild(w); }
    if(r&&(r.ui&&Object.keys(r.ui).length)){ const tl=document.createElement('div'); tl.className='agtools'; const u=r.ui; tl.innerHTML=`<span class="chip" data-reui='${esc(JSON.stringify(u))}'>${t('cd.showOnMap')}</span>`+(hl&&hl.length?` <span class="chip" data-clear="1">${t('ag.clearMap')}</span>`:'')+(u.url?` <span class="chip" data-url="${esc(u.url)}">${t('cd.openReport')}</span>`:''); d.appendChild(tl); }
    if(r&&r.steps&&r.steps.length){ const s=document.createElement('details'); s.className='steps'; s.innerHTML=`<summary>${t('cd.steps',{n:r.steps.length})}${r.planner?` · ${esc(r.planner)}`:''}${r.ms?` · ${fmt(r.ms/1000,1)} s`:''}</summary>`+r.steps.map(st=>`<div class="step ${st.ok?'':'bad'}"><b>${esc(lang==='en'?(st.en||st.action):(st.zh||st.action))}</b> <span class="mono">${esc(JSON.stringify(st.params||{}))}</span> · ${st.ms} ms${st.ok?'':' · '+esc(st.error||st.summary||'')}${st.side_effect&&st.side_effect!=='none'?' · <span style="color:var(--warn)">'+esc(st.side_effect)+'</span>':''}</div>`).join(''); d.appendChild(s); }
    if(r&&r.note){ const n=document.createElement('div'); n.className='m'; n.style.marginTop='4px'; n.textContent=F(r,'note'); d.appendChild(n); }
    if(kind==='a'&&r&&r.steps&&r.steps.length){ const qs=nextChips(r); if(qs.length){ const nx=document.createElement('div'); nx.className='agnext'; nx.innerHTML=`<span class="m">${t('ag.next')}</span>`+qs.map(q=>`<span class="chip" data-ask="${esc(q)}">${esc(q)}</span>`).join(''); nx.querySelectorAll('[data-ask]').forEach(c=>c.onclick=()=>sendAgent(c.dataset.ask)); d.appendChild(nx); } }
    box.appendChild(d); box.scrollTop=box.scrollHeight; return d; }
  agEl('agmsgs').addEventListener('click', async ev=>{ const el=ev.target.closest('[data-fp],[data-ids],[data-ll],[data-url],[data-reui],[data-clear]'); if(!el) return; ev.stopPropagation();
    try{ if(el.dataset.clear){ clearHighlight(); return; }
      if(el.dataset.url){ window.open(el.dataset.url,'_blank'); return; }
      if(el.dataset.reui){ await applyAgentUi(JSON.parse(el.dataset.reui)); return; }
      if(el.dataset.fp){ await focusPoint(el.dataset.fp, el.dataset.kind||'points'); return; }
      if(el.dataset.ids){ const ids=el.dataset.ids.split(',').filter(Boolean); if(ids.length) await highlightIds(ids, el.dataset.kind||'points'); else if(el.dataset.ll){ const [lo,la]=el.dataset.ll.split(',').map(Number); map.setView([la,lo],14); } return; }
      if(el.dataset.ll){ const [lo,la]=el.dataset.ll.split(',').map(Number); map.setView([la,lo],14); }
    }catch(e){ console.warn('agent click', e); } });
  // 对话框驱动地图：高亮点位（带坐标）、开图层、缩放、清除
  let hlLayer=null, HL=[];
  const hlColor = k => ({points:'#b91c1c', hotspots:'#dc2626', gwm:'#7c3aed', fac:'#0e7490'})[k] || '#b91c1c';   // fac = 防线 2+ 的设施（塘 / 井 / 箱涵），不是积水点
  function clearHighlight(){ if(hlLayer){ map.removeLayer(hlLayer); hlLayer=null; } HL=[]; }
  function highlightPoints(items, fit){ clearHighlight(); if(!items||!items.length) return; hlLayer=L.featureGroup(); HL=items;
    items.forEach((h,i)=>{ const c=hlColor(h.kind); const m=L.circleMarker([h.lat,h.lon],{radius:15,color:c,weight:3,fillColor:c,fillOpacity:.18,className:'hl-pulse'}); m.bindTooltip(`${i+1}. ${esc(h.label||h.id)}`,{permanent:true,direction:'top',offset:[0,-14],className:'hl-tip'}); if(h.kind!=='gwm' && h.kind!=='fac') m.on('click',()=>showHotspot(h.id)); hlLayer.addLayer(m); });
    hlLayer.addTo(map); if(fit!==false){ try{ map.fitBounds(hlLayer.getBounds().pad(0.25),{maxZoom:15}); }catch(e){} } }
  async function resolveHighlight(ids, kind){ const out=[]; if(kind==='gwm'){ const g=await getGwm(); (g&&g.geojson?g.geojson.features:[]).forEach(f=>{ if(ids.includes(f.properties.fp_id)) out.push({id:f.properties.fp_id,lon:f.properties.lon,lat:f.properties.lat,kind:'gwm',label:f.properties.fp_id}); }); }
    else { let gj=null; try{ gj=await load('hotspots.geojson'); }catch(e){} (gj?gj.features:[]).forEach(f=>{ if(ids.includes(f.properties.hotspot_id)) out.push({id:f.properties.hotspot_id,lon:f.geometry.coordinates[0],lat:f.geometry.coordinates[1],kind:kind||'points',label:f.properties.hotspot_id}); }); }
    return ids.map(id=>out.find(o=>o.id===id)).filter(Boolean); }
  window.highlightPoints=highlightPoints; window.clearHighlight=clearHighlight;
  async function applyAgentUi(ui){ if(!ui) return; try{
      if(ui.rp && SCENS && String(ui.rp)!==RP){ await setReturnPeriod(String(ui.rp)); }
      if(ui.l2p){ l2pView=ui.l2p;                                  // 防线 2+ 的视图 / 态 / 对比基准 / 地图模式（本体动作 state_gap 等）
        if(ui.l2p==='rb2'){ if(rbKey(ui.st)) RB.st=rbKey(ui.st); RB.base=rbKey(ui.base)||null; }
        else { if(ui.st && WB_ST.includes(stKey(ui.st))) WB.st=stKey(ui.st); if(ui.base && WB_ST.includes(stKey(ui.base))) WB.base=stKey(ui.base); }
        if(ui.m && ['L1','L2','diff'].includes(ui.m)) CY.mode=ui.m; if(!ui.tab && tab==='line2p') render(); }
      if(ui.kind){ ONTKIND=ui.kind; }
      if(ui.tab){ go(ui.tab); }
      if(ui.alert){ window._alertSel=ui.alert; if(tab==='line2') render(); }
      if(Array.isArray(ui.layers)){ for(const k of ui.layers){ if(!on(k)) await toggleLayer(k, true); } }
      if(ui.gwm){ await showGwmPair(); }
      if(ui.clear){ clearHighlight(); }
      if(Array.isArray(ui.highlight)&&ui.highlight.length&&ui.l2p!=='wb'&&ui.l2p!=='rb2'){ highlightPoints(ui.highlight, ui.fit!=='none'); }   // 五态工作台自己画设施
      else if(ui.fit==='all'){ try{ const ly=layers.flood||layers.hs; if(ly) map.fitBounds(ly.getBounds().pad(0.05)); }catch(e){} }
      if(ui.hs){ await showHotspot(ui.hs); }
      if(ui.pid!=null){ await selectProposal(+ui.pid, true); if(ui.whatif){ setTimeout(()=>{ const b=document.querySelector(`#det-${ui.pid} [data-a="whatif"]`); b&&b.click(); }, 700); } }
      if(ui.url){ window.open(ui.url, '_blank'); }
    }catch(e){ console.warn('agent ui', e); } }
  async function sendAgent(q){ q=(q||agEl('agin').value||'').trim(); if(!q||AGBUSY) return; AGBUSY=true; agEl('agin').value=''; agBubble('u', q, null); const wait=agBubble('a', '', null); wait.classList.add('wait'); const t0=Date.now();
    const tick=()=>{ const s=Math.round((Date.now()-t0)/1000); wait.innerHTML=`${esc(t('ag.thinking'))} <span class="mono">${s} s</span>${s>=15?`<span class="hint">${esc(t(s>=45?'ag.slowHint2':'ag.slowHint'))}</span>`:''}`; }; tick(); const tm=setInterval(tick,1000);
    let r=null; try{ const res=await fetch('/api/agent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:q, history:AGH.slice(-8), lang, scenario:RP, session_id:AGSID})}); r=await res.json(); }catch(e){ r={reply:t('ag.offline'), steps:[]}; } clearInterval(tm); wait.remove(); agBubble('a', r.reply||r.error||'', r); AGH.push({role:'user',content:q}); AGH.push({role:'assistant',content:r.reply||''}); AGBUSY=false; await applyAgentUi(r.ui); }
  window.openAgent=openAgent; window.sendAgent=sendAgent;
  async function ensureHotspotLayer(){
    if(layers.hs) return;
    let gj; try{ gj=await load('hotspots.geojson'); }catch(e){ return; }
    layers.hs=L.geoJSON(gj,{renderer:canvas, pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:5,color:'#fff',weight:1,fillColor:critColor(f.properties.criticality),fillOpacity:.95}),
      onEachFeature:(f,l)=>{const p=f.properties; l.bindPopup(()=>`<h4>${esc(p.hotspot_id)} · ${esc(lang==='en'?p.criticality:p.criticality_zh)} <button class="btn" style="font-size:11px;padding:1px 8px;margin-left:6px" onclick="showHotspot('${esc(p.hotspot_id)}')">${t('hd.open')}</button></h4><div class="m">${esc(p.area||'')} · ${esc(p.location||'')}</div>
        <div class="kv"><span>${t('hs.rootCause')}</span><span>${esc(lang==='en'?(p.root_cause_raw||p.root_cause):p.root_cause_zh)} · ${t('hs.network')} ${p.network_available?t('hs.yes'):t('hs.no')}</span>
        <span>${t('hs.planA')}</span><span>${t('hs.pumps')} ${p.planA_pumps??0} (12" ${p.planA_pump12??0} / 8" ${p.planA_pump8??0} / 6" ${p.planA_pump6??0}) · ${t('hs.tankers')} ${p.planA_tankers??0} · ${t('hs.manpower')} ${p.planA_manpower??0}</span>
        ${p.depth_max_m!=null?`<span>${t('hs.terrain')}</span><span>${t('hs.lowDepth')} ${p.depth_max_m} m · ${t('hs.pondVol')} ${fmt(p.pond_volume_m3)} m³ · ${t('hs.spill')} ${p.spill_z} m${(lang==='en'?p.terrain_note_en:p.terrain_note_zh)?`<br>${esc(lang==='en'?p.terrain_note_en:p.terrain_note_zh)}`:''}</span>`:''}${p.edge_gid!=null?`<span>${t('hs.nf')}</span><span>${p.nf_spill_m3_10>0?t('hs.nfSpill',{v:fmt(p.nf_spill_m3_10,0)}):t('hs.nfNoSpill')}${p.nf_backwater_10?` · <b style="color:var(--warn)">${t('hs.nfBw')}</b>`:''}</span>`:''}</div>
        <details><summary class="m">${t('ui.details')}</summary><div class="kv"><span>${t('hs.idd')}</span><span>${p.idd_planned_flag==='yes'?`${t('hs.yes')} · ${esc(p.idd_category||'')} · ${esc(p.idd_stage||'')} · ${esc(p.idd_owner||'')} · ${esc(p.idd_completion||'')}`:(p.idd_planned_flag==='assessing'?t('hs.assessing'):t('hs.no'))}</span>${p.design_event_future?`<span>${t('hs.target')}</span><span>${esc(p.design_event_future)}</span>`:''}</div></details>`);}});
    if(on('hs')) layers.hs.addTo(map);
  }
  async function ensureDispatchLayers(){
    const d=await getDispatch();
    if(layers.flood){ map.removeLayer(layers.flood); } if(layers.fleet){ map.removeLayer(layers.fleet); }
    layers.flood=L.geoJSON(d.points,{renderer:canvas, style:f=>({color:floodColor(f.properties),weight:1.5,fillColor:floodColor(f.properties),fillOpacity:.45}),
      pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:6,color:'#fff',weight:1,fillColor:floodColor(f.properties),fillOpacity:.9}),
      onEachFeature:(f,l)=>{const p=f.properties;l.bindPopup(()=>`<h4>${esc(p.fp_id)}${p.level?` · ${t('dp.level')} ${p.level}`:''}${p.client_criticality?` · ${esc(lang==='en'?p.client_criticality:critZh(p.client_criticality))}`:''} <button class="btn" style="font-size:11px;padding:1px 8px;margin-left:6px" onclick="showHotspot('${esc(p.client_hotspot_id||p.fp_id)}')">${t('hd.open')}</button></h4>
        <div class="kv"><span>${t('dp.depth')}</span><span>${p.max_depth_m} m</span><span>${t('dp.vol')}</span><span>${fmt(p.volume_now_m3)} m³ · ${t('dp.inflow')} ${fmt(p.inflow_now_m3h)} m³/h</span><span>${t('dp.need')}</span><span>${fmt(p.required_rate_m3h)} m³/h</span><span>${t('dp.triage')}</span><span>${esc(lang==='en'&&(d.triage_en||{})[p.triage]?d.triage_en[p.triage]:p.triage)}${p.discharge_to?` → ${esc(p.discharge_to)} (${fmt(p.discharge_dist_m)} m)`:''}</span></div>
        <details><summary class="m">${t('ui.howComputed')}</summary><div class="m">${esc(F(p,'note')||'')} · ${t('dp.prio')} ${p.priority}</div></details>
        <div class="rp" data-fp="${esc(p.fp_id)}" style="margin-top:6px;border-top:1px solid #e5e7eb;padding-top:6px;min-width:300px"><span class="m">${t('rpl.loading')}</span></div>`); l.on('popupopen',()=>loadReplay(p.fp_id));}});
    const g=L.layerGroup();
    (d.depots||[]).forEach(dp=>{ const n=Object.values(dp.units||{}).reduce((a,b)=>a+b,0); const m=L.marker([dp.lat,dp.lon],{icon:L.divIcon({className:'',html:`<div style="background:#1d4ed8;color:#fff;font:600 11px/18px system-ui;border-radius:4px;padding:0 5px;border:1px solid #fff;white-space:nowrap">${n}</div>`,iconSize:[26,18],iconAnchor:[13,9]})}); m.bindTooltip(`${esc(dp.name)} · ${n} ${t('dp.unitsAt')}`); g.addLayer(m); });
    L.geoJSON(d.routes,{renderer:canvas, style:f=>({color:'#1d4ed8',weight:1.5,opacity:.7,dashArray:'4 3'}), onEachFeature:(f,l)=>{const p=f.properties;l.bindTooltip(`${p.unit_id} ${p.model} → ${p.fp_id} · ${p.travel_min} min`)}}).addTo(g);
    layers.fleet=g;
    if(on('flood')) layers.flood.addTo(map);
    const want=U.get('fp'); if(want){ layers.flood.eachLayer(l=>{ if(l.feature&&l.feature.properties.fp_id===want){ const c=l.getLatLng?l.getLatLng():null; if(c) map.setView(c,15); setTimeout(()=>l.openPopup(),300); } }); }
    if(on('fleet')) layers.fleet.addTo(map);
  }
  let prepoLayer=null;
  function drawPrepo(){ if(!DP) return; const a=window._alertSel||Object.keys(DP.preposition).slice(-1)[0]; const pre=DP.preposition[a]; if(prepoLayer) map.removeLayer(prepoLayer); prepoLayer=L.layerGroup(); (pre.sites||[]).forEach(s=>{ prepoLayer.addLayer(L.circleMarker([s.lat,s.lon],{radius:11,color:'#0f766e',weight:3,fillColor:'#fff',fillOpacity:.9}).bindTooltip(`${esc(s.name)} · ${s.units_suggested} ${t('dp.unitsAt')} · ${s.covers.length} ${t('dp.pts')}`)); }); if(on('prepo')) prepoLayer.addTo(map); }
  // ---------------- 退水重演（弹窗） ----------------
  function replaySvg(r){
    const W=300,H=90,px=6; const tt=r.t_min, b=r.base_m3, p=r.pump_m3; const tmax=tt[tt.length-1]||1, vmax=Math.max(1,...b);
    const X=v=>px+(W-2*px)*v/tmax, Y=v=>H-px-(H-2*px)*v/vmax;
    const line=(arr,col,dash)=>`<polyline fill="none" stroke="${col}" stroke-width="1.6" ${dash?'stroke-dasharray="4 3"':''} points="${arr.map((v,i)=>X(tt[i]).toFixed(1)+','+Y(v).toFixed(1)).join(' ')}"/>`;
    const arr=(r.pumps||[]).map(u=>`<line x1="${X(u.arrive_min).toFixed(1)}" y1="${H-px}" x2="${X(u.arrive_min).toFixed(1)}" y2="${px}" stroke="#b45309" stroke-width="1" stroke-dasharray="2 2"/>`).join('');
    const ticks=[0,Math.round(tmax/2),Math.round(tmax)].map(v=>`<text x="${X(v).toFixed(1)}" y="${H-1}" font-size="8" fill="#6b7280" text-anchor="${v===0?'start':(v>=tmax?'end':'middle')}">${Math.round(v/60*10)/10}h</text>`).join('');
    return `<svg width="${W}" height="${H}" style="display:block;background:#f8fafc;border-radius:4px">${line(b,'#64748b',true)}${line(p,'#1d4ed8',false)}${arr}${ticks}<text x="${px}" y="${px+8}" font-size="8" fill="#6b7280">${fmt(vmax,0)} m³</text></svg>`;
  }
  function replayHtml(r){
    const k=r.kpi, kb=k.base, kp=k.pump; const h=v=>v==null?t('rpl.never'):hrs(v);
    const tgt=r.point&&r.point.target_drain_min?` · ${t('rpl.target')} ${hrs(r.point.target_drain_min)}`:'';
    const pumpsTxt=(r.pumps||[]).length?(r.pumps.map(u=>`${esc(u.label||'')}@${Math.round(u.arrive_min)}′`).join(', ')):t('rpl.noPumps');
    const models=(r.models||[]).map(m=>`<option value="${esc(m.model)}">${esc(m.model.replace('diesel-',''))} ${m.rate_m3h} m³/h</option>`).join('');
    const id=esc(r.point.fp_id);
    return `<div class="m"><b>${t('rpl.title')}</b> · ${r.pumps_source==='manual'?t('rpl.manualSrc'):t('rpl.planSrc')}：${pumpsTxt}</div>
      ${replaySvg(r)}
      <table style="margin:4px 0;font-size:11px"><tr><th></th><th>${t('rpl.noPump')}</th><th>${t('rpl.withPump')}</th></tr>
      <tr><td>${t('rpl.empty')}${tgt}</td><td>${h(kb.t_empty_min)}</td><td><b>${h(kp.t_empty_min)}</b>${k.saved_min!=null&&k.saved_min>0?` <span style="color:#15803d">−${hrs(k.saved_min)}</span>`:''}</td></tr>
      <tr><td>${t('rpl.peak')}</td><td>${fmt(kb.peak_m3,0)} m³</td><td><b>${fmt(kp.peak_m3,0)} m³</b></td></tr>
      <tr><td>${t('rpl.floodH')}</td><td>${kb.flood_h} h</td><td><b>${kp.flood_h} h</b></td></tr>
      <tr><td>${t('rpl.pumped')}</td><td>—</td><td><b>${fmt(k.pumped_total_m3,0)} m³</b></td></tr></table>
      <div style="display:grid;grid-template-columns:auto 1fr auto auto;gap:4px 6px;align-items:center;margin-top:4px"><span class="m">${t('rpl.manual')}</span>
        <select id="rp-model-${id}" style="font-size:11px">${models}</select>
        <span class="m">× <input id="rp-n-${id}" type="number" min="1" max="6" value="2" style="width:36px;font-size:11px" title="${t('rpl.count')}"></span>
        <span class="m">${t('rpl.arriveAt')} <input id="rp-at-${id}" type="number" min="0" step="10" value="${Math.round((r.decision_time_min||210)+40)}" style="width:52px;font-size:11px"></span>
        <button class="btn primary" style="grid-column:1/5;margin:2px 0 0;font-size:11px;padding:3px 8px" onclick="manualReplay('${id}')">${t('rpl.run')}</button></div>`;
  }
  async function loadReplay(fp, pumps){
    const box=document.querySelector(`.rp[data-fp="${fp}"]`); if(!box) return;
    const q='/api/replay?fp='+encodeURIComponent(fp)+(pumps?'&pumps='+encodeURIComponent(pumps):'')+(DP&&DP.decision_time_min?'&t='+DP.decision_time_min:'');
    const r=await api(q); if(!r||r.error){ box.innerHTML=`<span class="m">${esc((r&&r.error)||t('rpl.offline'))}</span>`; return; }
    box.innerHTML=replayHtml(r);
  }
  window.manualReplay=function(fp){ const m=document.getElementById('rp-model-'+fp).value, n=document.getElementById('rp-n-'+fp).value, at=document.getElementById('rp-at-'+fp).value; loadReplay(fp, `${m}:${n}@${at}`); };

  // ---------------- 单热点详情：洼地面 / 汇流树 / 无泵·派泵·换管 三条曲线 ----------------
  let detailLayer=null, detailId=null;
  function clearDetail(){ if(detailLayer){ map.removeLayer(detailLayer); detailLayer=null; } detailId=null; }
  window.closeDetail=function(){ clearDetail(); render(); };
  window.showHotspot=async function(id){
    detailId=id; map.closePopup(); panel.innerHTML=`<div class="empty">${t('loading')}</div>`;
    const r=await api('/api/hotspot?id='+encodeURIComponent(id));
    if(!r||r.error){ panel.innerHTML=`<button class="btn" onclick="closeDetail()" style="margin:0 0 8px">← ${t('hd.back')}</button><div class="empty">${esc((r&&r.error)||t('liveOff'))}</div>`; return; }
    if(detailId!==id) return;
    if(detailLayer) map.removeLayer(detailLayer); detailLayer=L.layerGroup();
    const h=r.hotspot||{}, m=r.matched||{}, e=r.reach||{}, up=r.upstream, pond=r.pond, c=(r.curves&&!r.curves.error)?r.curves:null; const EN=lang==='en';
    if(pond&&pond.geometry) detailLayer.addLayer(L.geoJSON(pond.geometry,{style:{color:'#0e7490',weight:1.5,fillColor:'#38bdf8',fillOpacity:.45}}).bindTooltip(`${t('hd.depth')} ${pond.depth_max_m} m · ${fmt(pond.pond_volume_m3,0)} m³`));
    if(up&&up.geojson) detailLayer.addLayer(L.geoJSON(up.geojson,{style:f=>({color:'#ea580c',weight:(f.properties.dn||300)>=600?3:1.8,opacity:.85})}));
    if(h.lat!=null) detailLayer.addLayer(L.circleMarker([h.lat,h.lon],{radius:9,color:'#7f1d1d',weight:3,fillColor:'#fff',fillOpacity:.9}).bindTooltip(esc(h.hotspot_id)));
    detailLayer.addTo(map);
    try{ const b=detailLayer.getBounds(); if(b&&b.isValid()) map.fitBounds(b.pad(0.25),{maxZoom:16}); else if(h.lat!=null) map.setView([h.lat,h.lon],15); }catch(err){ if(h.lat!=null) map.setView([h.lat,h.lon],15); }
    const svg=(()=>{ if(!c) return `<div class="m">${esc((r.curves&&r.curves.error)||t('hd.noCurve'))}</div>`;
      const W=440,H=150,px=8,py=10, T=c.t_min; const series=[{v:c.base,col:'#64748b',dash:true,lab:t('rpl.noPump')}];
      if(c.pump) series.push({v:c.pump,col:'#1d4ed8',lab:t('rpl.withPump')});
      (c.upsized||[]).forEach((o,i)=>series.push({v:o.curve,col:['#15803d','#65a30d','#0f766e'][i]||'#0f766e',lab:t('hd.dn',{d:o.dn_mm})}));
      const vmax=Math.max(1,...series.flatMap(sr=>sr.v)), tmax=T[T.length-1]||1; const X=i=>px+(W-2*px)*T[i]/tmax, Y=v=>H-py-(H-2*py)*v/vmax;
      const line=sr=>`<polyline fill="none" stroke="${sr.col}" stroke-width="1.8" ${sr.dash?'stroke-dasharray="5 3"':''} points="${sr.v.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(' ')}"/>`;
      const ticks=[0,Math.floor((T.length-1)/2),T.length-1].map(i=>`<text x="${X(i).toFixed(1)}" y="${H-1}" font-size="9" fill="#6b7280" text-anchor="${i===0?'start':(i===T.length-1?'end':'middle')}">${hrs(T[i])}</text>`).join('');
      return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:150px;display:block;background:#f8fafc;border-radius:6px">${series.map(line).join('')}${ticks}<text x="${px}" y="${py}" font-size="9" fill="#6b7280">${fmt(vmax,0)} m³</text></svg>
        <div class="m" style="margin-top:4px">${series.map(sr=>`<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${sr.col};margin-right:4px;vertical-align:middle"></span>${esc(sr.lab)}`).join(' &nbsp; ')}</div>`; })();
    const kp=k=>k?`${k.never_empties?t('rpl.never'):hrs(k.t_empty_min)} · ${fmt(k.peak_m3,0)} m³ · ${k.flood_h} h`:'—';
    panel.innerHTML=`<button class="btn" onclick="closeDetail()" style="margin:0 0 8px">← ${t('hd.back')}</button>
      <h2 style="margin:0">${esc(h.hotspot_id)} <small>${esc(h.area||'')} · ${esc(h.location||'')} · ${esc(EN?h.criticality:critZh(h.criticality))}</small></h2>
      <div class="kpis" style="margin-top:8px">
        <div class="kpi"><b>${pond&&pond.depth_max_m!=null?pond.depth_max_m+' m':'—'}</b><span>${t('hd.depth')}</span><small>${pond&&pond.pond_volume_m3!=null?`${fmt(pond.pond_volume_m3,0)} m³ · ${fmt(pond.pond_area_m2,0)} m²${pond.terrain_note?' · '+esc(EN?pond.terrain_note_en:pond.terrain_note_zh):''}`:(pond&&pond.error?esc(pond.error):t('hd.noDtm'))}</small></div>
        <div class="kpi"><b>${e.pipe_diameter?'DN'+Math.round(e.pipe_diameter):'—'}</b><span>${t('hd.reach')}</span><small>${e.q_full!=null?`${Math.round(e.q_full*1000)} L/s · ${esc(e.unitid||'')}${e.compliant===false?' · '+t('hd.nonCompliant'):''}`:t('hd.noReach')}</small></div>
        <div class="kpi"><b>${m.area_ha!=null?fmt(m.area_ha,1)+' ha':'—'}</b><span>${t('hd.upstream')}</span><small>${up?t('hd.upTree',{n:up.n_edges,k:up.len_km})+(up.truncated?' …':''):''}${e.nf_backwater_10?` · <b style="color:var(--warn)">${t('hs.nfBw')}</b>`:''}${e.nf_spill_m3_10>0?` · ${t('hs.nfSpill',{v:fmt(e.nf_spill_m3_10,0)})}`:''}</small></div>
      </div>
      <h2>${t('hd.curves')} <small>${esc(EN?(r.scenario_label_en||''):(r.scenario_label||''))}${c&&c.no_gap?' · '+t('hd.noGap'):''}</small></h2>${c&&c.no_reach?`<div class="m">${t('hd.noReachCurve')}</div>`:''}${svg}
      ${c?`<table style="margin-top:6px"><tr><th>${t('hd.option')}</th><th>${t('hd.kpi')}</th><th>${t('hd.gap')}</th></tr>
        <tr><td>${t('rpl.noPump')}</td><td>${kp(c.base_kpi)}</td><td>${fmt(Math.max(...c.base),0)} m³</td></tr>
        ${c.pump?`<tr><td>${t('rpl.withPump')} <span class="m">${c.pumps.map(p=>esc(p.label||'')+'@'+Math.round(p.arrive_min)+'′').join(', ')}</span></td><td>${kp(c.pump_kpi)}</td><td>—</td></tr>`:''}
        ${(c.upsized||[]).map(o=>`<tr><td>${t('hd.dn',{d:o.dn_mm})} <span class="m">${Math.round(o.q_full_m3s*1000)} L/s</span></td><td>${kp(o.kpi)}</td><td>${fmt(o.shortfall_m3,0)} m³</td></tr>`).join('')}</table>`:''}
      <div class="m" style="margin-top:8px">${esc(m.match||'')}</div>
      <div class="m" style="margin-top:6px">${t('hd.note')}</div>
      <h2 id="rcHead">${t('hd.rcTitle')} <small>${t('hd.rcSub')}</small></h2><div id="rcBox" class="m">${t('loading')}</div>`;
    (async()=>{ const rc=await api('/api/recession?fp='+encodeURIComponent(id)+(RP?'&rp='+encodeURIComponent(RP):'')); const box=document.getElementById('rcBox'); if(!box||detailId!==id) return;
      if(!rc||rc.error){ box.textContent=(rc&&rc.error)||t('liveOff'); return; }
      const H=m=>m==null?t('hd.rcNever'):hrs(m); const pt=rc.point||{};
      const rows=(rc.options||[]).map(o=>`<tr class="${o.key===rc.fastest?'hl':''}"><td>${esc(EN?o.label_en:o.label_zh)}${o.site?`<div class="m">${esc(o.site.name)} · ${o.site.travel_min} min</div>`:''}${(o.note_zh||o.note_en)?`<div class="m">${esc(EN?(o.note_en||o.note_zh):o.note_zh)}</div>`:''}</td><td>${o.n_units?`${o.n_units}<div class="m">${(o.units||[]).map(u=>esc(u.unit_id)).join(', ')}</div>`:'—'}</td><td><b>${H(o.t_empty_min)}</b>${o.flood_h!=null?`<div class="m">${o.flood_h} h</div>`:''}</td><td>${o.within_target?'✓':'✗'}</td></tr>`).join('');
      const svg=rc.single_vs_global||{}; const need=rc.need_units_for_target;
      box.className=''; box.innerHTML=`<div class="promise"><b>${esc(EN?rc.recommendation.en:rc.recommendation.zh)}</b></div>
        <table><tr><th>${t('hd.rcOption')}</th><th>${t('hd.rcUnits')}</th><th>${t('hd.rcEmpty')}</th><th>${t('hd.rcTarget',{m:Math.round(rc.target_drain_min)})}</th></tr>${rows}</table>
        <div class="m" style="margin-top:6px">${t('hd.rcState',{v:fmt(pt.volume_now_m3,0),q:fmt(pt.inflow_now_m3h,0),t:Math.round(rc.decision_time_min),tri:esc(pt.triage||''),r:pt.priority_rank_dispatchable??'—',n:pt.n_dispatchable??'—'})}${svg.units_taken_from_global&&svg.units_taken_from_global.length?`<br>${t('hd.rcCost',{u:svg.units_taken_from_global.join(', '),p:(svg.displaced_points||[]).map(d=>d.fp_id).join(', '),h:hrs(svg.displaced_saved_min_total)})}`:''}${need&&need.n?`<br>${t('hd.rcNeed',{n:need.n,h:hrs(need.done_min)})}`:(need?`<br>${t('hd.rcNeedNo',{r:esc(EN?need.reason_en:need.reason_zh)})}`:'')}${rc.wait_until_min!=null?`<br>${t('hd.rcWait',{h:hrs(rc.wait_until_min)})}`:''}</div>
        <div class="m" style="margin-top:4px">${t('hd.rcNote')}</div>`; })();
  };
  // ---------------- 页签 ----------------
  const panel=document.getElementById('panel'); let tab='overview'; let sub1=window._sub1||'props'; let zoneFilter=null;
  const LEGACY={props:['line1','props'],sched:['line1','sched'],standard:['line1','standard'],residual:['line1','residual'],dispatch:['line3',null]};
  const go=(tt,sub)=>{ if(LEGACY[tt]){ const [a,b]=LEGACY[tt]; tt=a; if(b) sub=b; } tab=tt; if(sub) sub1=sub; window._sub1=sub1; document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.t===tt)); render(); };
  document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>go(b.dataset.t));
  // ---------------- 范围 × 阶段导航（2026-10-25 控制台重组，docs/控制台重组方案_三道防线_20261025.md） ----------------
  // 主线 = 三道防线：范围（全市 / 某个汇水区）× 阶段（总览 / 数据与模型 / L1 现状设施 / L2 雨前措施 / L3 雨中应急 / 结论与待办）。
  // 只是旧页签之上的一层映射：各页面函数、旧链接（tab= / v=）、智能体的 go() 跳转都照旧可用；navSync 从当前 tab / l2pView 反推范围与阶段。
  const SCOPES = [['city', 'main'], ['rda', 'main'], ['rb01', 'pilot'], ['c1', 'pilot'], ['rb2', 'pilot'], ['wb', 'pilot'], ['pilot', 'hist'], ['1d', 'hist']];
  const SCOPE_KIND = Object.fromEntries(SCOPES);
  const STAGES = ['overview', 'data', 'l1', 'l2', 'l3', 'concl', 'more'];
  const R1_SCOPES = ['rda', 'rb01'];                                  // 已按阶段拆分的汇水区页（renderRB01）
  const NAV = {scope: 'city', stage: 'overview', citySub: {l2: 'sim'}};
  {
    const sc = U.get('scope'), sg = U.get('stage');
    if(sc && SCOPE_KIND[sc]) NAV.scope = sc;
    if(sg && STAGES.includes(sg)) NAV.stage = sg;
    else if(U.get('tab') === 'line2p' && U.get('st') && !['city', 'city2d'].includes(U.get('v'))) NAV.stage = 'l2';   // 旧链接带了态 → 落在 L2 对比
    if(U.get('sub') === 'prepo') NAV.citySub.l2 = 'prepo';
  }
  const scopeName = k => k === 'city' ? t('nav.city') : k === 'wb' ? 'Mussafah_00' : k === 'rb2' ? '29.RB2' : k === 'pilot' ? t('nav.mar') : k === '1d' ? t('nav.old1d') : k === 'rb01' ? 'RB-01' : k.toUpperCase().replace('RDA', 'RD-A');
  function navTo(scope, stage, sub){
    NAV.scope = SCOPE_KIND[scope] ? scope : 'city'; NAV.stage = STAGES.includes(stage) ? stage : 'overview';
    if(sub) NAV.citySub[NAV.stage] = sub;
    if(NAV.scope === 'city'){
      if(NAV.stage === 'l2' && NAV.citySub.l2 !== 'prepo'){ if(!['city', 'city2d'].includes(l2pView)) l2pView = 'city'; return go('line2p'); }
      return go({overview: 'overview', data: 'trust', l1: 'line1', l2: 'line2', l3: 'line3', concl: 'concl', more: 'ontology'}[NAV.stage]);
    }
    if(NAV.stage === 'more') return go('ontology');
    // 还没拆分的试点页：点「L1」落在现状，点「L2」从现状切回各自的主方案（和 RD-A / RB-01 的阶段口径一致）
    const l1 = NAV.stage === 'l1';
    if(NAV.scope === 'c1') C1V.st = l1 ? 'L1' : (C1V.st === 'L1' ? null : C1V.st);
    if(NAV.scope === 'rb2') RB.st = l1 ? 'L1' : (RB.st === 'L1' ? 'L2AB2' : RB.st);
    if(NAV.scope === 'wb') WB.st = l1 ? 'L1' : (WB.st === 'L1' ? 'L2+B' : WB.st);
    if(NAV.scope === 'pilot') PL.st = l1 ? 'L1' : (PL.st === 'L1' ? 'L2' : PL.st);
    l2pView = NAV.scope; return go('line2p');
  }
  function navSync(){
    if(tab === 'line2p'){
      if(l2pView === 'city' || l2pView === 'city2d'){ NAV.scope = 'city'; NAV.stage = 'l2'; NAV.citySub.l2 = 'sim'; }
      else { NAV.scope = l2pView; if(NAV.stage === 'more') NAV.stage = 'overview'; }
    } else if(tab === 'ontology'){ NAV.stage = 'more'; }
    else {
      NAV.scope = 'city';
      NAV.stage = {overview: 'overview', trust: 'data', line1: 'l1', line2: 'l2', line3: 'l3', concl: 'concl'}[tab] || 'overview';
      if(tab === 'line2') NAV.citySub.l2 = 'prepo';
    }
    panel.dataset.stage = NAV.stage;
    const sel = document.getElementById('scopesel'); if(sel && sel.value !== NAV.scope) sel.value = NAV.scope;
    document.querySelectorAll('nav button[data-s]').forEach(b => b.classList.toggle('on', b.dataset.s === NAV.stage));
    const rp = document.getElementById('rpbox'); if(rp) rp.style.display = NAV.scope === 'city' ? '' : 'none';   // 重现期只对全市有意义；汇水区用设计暴雨
    navSub();
    try{ const u = new URL(location.href); u.searchParams.set('scope', NAV.scope); u.searchParams.set('stage', NAV.stage); u.searchParams.set('tab', tab);
      if(NAV.scope === 'city' && NAV.stage === 'l2') u.searchParams.set('sub', NAV.citySub.l2); else u.searchParams.delete('sub');
      if(tab !== 'line2p') ['v', 'st', 'storm', 'base', 'm', 'd', 'r', 'c', 't', 'cyt'].forEach(k => u.searchParams.delete(k));
      history.replaceState(null, '', u); }catch(e){}
  }
  function navSub(){
    const el = document.getElementById('subnav'); if(!el) return;
    const k = SCOPE_KIND[NAV.scope], tag = k === 'main' ? '' : `<span class="stag ${k}">${t(k === 'pilot' ? 'nav.tagPilot' : 'nav.tagHist')}</span>`;
    let h = `<div class="snote">${tag}${t('nav.d_' + NAV.stage + (NAV.scope === 'city' ? '_city' : ''))}</div>`;
    if(NAV.scope === 'city' && NAV.stage === 'l2')
      h += `<div class="seg mini">${[['sim', t('nav.l2sim')], ['prepo', t('nav.l2prepo')]].map(([v, l]) => `<button data-csub="${v}" class="${NAV.citySub.l2 === v ? 'on' : ''}">${l}</button>`).join('')}</div>`;
    if(NAV.scope !== 'city' && !R1_SCOPES.includes(NAV.scope) && NAV.stage !== 'more') h += `<div class="snote">${t('nav.pilotFull')}</div>`;
    if(NAV.scope === 'city' && NAV.stage === 'overview') h += `<div style="margin:0 0 6px"><button class="chip" data-gop="1">${t('nav.goProg')}</button> <button class="chip" data-gorda="1">${t('nav.goRda')}</button></div>`;
    el.innerHTML = h;
    el.querySelectorAll('[data-gop]').forEach(b => b.onclick = () => navTo('city', 'concl'));
    el.querySelectorAll('[data-gorda]').forEach(b => b.onclick = () => navTo('rda', 'overview'));
    el.querySelectorAll('[data-csub]').forEach(b => b.onclick = () => navTo('city', 'l2', b.dataset.csub));
  }
  function navInit(){
    const sel = document.getElementById('scopesel');
    if(sel){
      const grp = g => SCOPES.filter(([, x]) => x === g).map(([k]) => `<option value="${k}">${esc(scopeName(k))}</option>`).join('');
      sel.innerHTML = `<optgroup label="${t('nav.gMain')}">${grp('main')}</optgroup><optgroup label="${t('nav.gPilot')}">${grp('pilot')}</optgroup><optgroup label="${t('nav.gHist')}">${grp('hist')}</optgroup>`;
      sel.value = NAV.scope; sel.onchange = () => navTo(sel.value, NAV.stage === 'more' ? 'overview' : NAV.stage);
    }
    const lb = document.getElementById('scopelabel'); if(lb) lb.textContent = t('nav.scope');
    const ob = document.getElementById('ontobtn'); if(ob){ ob.textContent = t('nav.s_more'); ob.title = t('nav.d_more'); ob.onclick = () => navTo(NAV.scope, 'more'); }
    document.querySelectorAll('nav button[data-s]').forEach(b => {
      const s = b.querySelector('span:last-child'), txt = t('nav.s_' + b.dataset.s);
      if(s && b.querySelector('.dot')) s.textContent = txt; else b.textContent = txt;
      b.title = t('nav.d_' + b.dataset.s + '_city');
      b.onclick = () => navTo(NAV.scope, b.dataset.s);
    });
  }
  // 全市「结论与待办」：三道防线在全市和各汇水区走到了哪一步、要客户补 / 确认什么、报告入口
  const PROGRESS = [
    // [范围, 数据治理, L1 现状, L2 客户计划, L2 我们的方案, L3 雨中应急]（ok = 已有成果；mid = 中间版 / 部分；no = 未做）
    ['city', 'mid', 'ok', 'ok', 'mid', 'ok'], ['rda', 'mid', 'ok', 'ok', 'mid', 'ok'], ['rb01', 'no', 'ok', 'ok', 'ok', 'no'], ['c1', 'no', 'ok', 'ok', 'ok', 'no'],
    ['rb2', 'no', 'ok', 'ok', 'ok', 'no'], ['wb', 'no', 'ok', 'ok', 'ok', 'no']];
  async function renderConcl(){
    const mark = v => `<span style="color:${v === 'ok' ? '#15803d' : v === 'mid' ? '#b45309' : '#94a3b8'}">${t('nav.p_' + v)}</span>`;
    panel.innerHTML = `<h2>${t('nav.cTitle')}</h2><div class="m" style="margin-bottom:8px">${t('nav.cSub')}</div>
      <table><tr><th>${t('nav.scope')}</th><th>${t('nav.s_data')}</th><th>${t('nav.s_l1')}</th><th>${t('nav.cL2p')}</th><th>${t('nav.cL2s')}</th><th>${t('nav.s_l3')}</th></tr>
      ${PROGRESS.map(([k, ...v]) => `<tr><td><a href="#" data-gos="${k}">${esc(scopeName(k))}</a>${SCOPE_KIND[k] === 'pilot' ? ` <span class="m">${t('nav.tagPilot')}</span>` : ''}</td>${v.map(x => `<td>${mark(x)}</td>`).join('')}</tr>`).join('')}
      <tr><td class="m">${t('nav.cOthers')}</td><td colspan="5" class="m">${t('nav.cOthersNote')}</td></tr></table>
      <div class="m" style="margin-top:4px">${t('nav.cCityNote')}</div>
      <h3 style="margin-top:14px">${t('nav.cTodo')}</h3><ul class="m" style="padding-left:18px;line-height:1.7">${t('nav.cTodoList').split('|').map(x => `<li>${esc(x)}</li>`).join('')}</ul>
      <h3 style="margin-top:14px">${t('nav.cRep')}</h3><div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="chip" id="crepCity">${t('nav.cRepCity')}</button><a class="chip" href="data/flood2d_rda/report.html" target="_blank" rel="noopener">${t('nav.cRepRda')}</a></div>`;
    panel.querySelectorAll('[data-gos]').forEach(a => a.onclick = e => { e.preventDefault(); navTo(a.dataset.gos, 'overview'); });
    const rb = document.getElementById('crepCity'); if(rb) rb.onclick = () => { const k = RP || (health && health.active_rp) || '10'; window.open('/api/report?rp=' + encodeURIComponent(k) + '&lang=' + lang, '_blank'); };
  }
  let rendering=0;
  async function render(){
    const my=++rendering; if(TLS) tlReset(); clearDetail(); navSync();
    rpScope();                                              // 切页签时同步重现期一栏是否可用
    if(tab!=='line2p'){ f2Clear(); wbClear(); plClear(); cyClear(); try{ r1Clear(); }catch(e){} }
    if(tab!=='line2p' && tl3Layer){ map.removeLayer(tl3Layer); tl3Layer=null; window._viewOwned=false; }
    if(tab!=='line2p' && tl3NetLayer){ map.removeLayer(tl3NetLayer); tl3NetLayer=null; }
    panel.innerHTML=`<div class="empty">${t('loading')}</div>`;
    try{
      const key = tab==='line1' ? sub1 : tab;
      if(tab!=='ontology') await applyPreset(key);
      if(my!==rendering) return;
      await ({overview:renderOverview,line1:renderLine1,line2:renderLine2,line3:renderLine3,line2p:renderLine2p,trust:renderTrust,ontology:renderOntology,concl:renderConcl})[tab]();
      bindMore(panel);
    } catch(e){ panel.innerHTML=`<div class="empty">${t('mapLoadFail')}${esc(e.message)}</div>`; }
  }
  const segHtml=(items,cur,attr)=>`<div class="seg">${items.map(([k,l])=>`<button data-${attr}="${k}" class="${k===cur?'on':''}">${l}</button>`).join('')}</div>`;

  // ---------------- 总览 ----------------
  const capKpi = () => { const share = scen ? scen.surcharge_share : (S['超能力管长份额']||{})[main];
    return `<b>${share==null?'—':Math.round(share*100)+'%'}</b><span>${scen?scen.i_mmh:(scn(main)+' '+SCEN[main])} ${t('kpi.under')}</span><small>${scen?`${t('kpi.underLive')}, ${scen.computed_ms} ms`:t('kpi.underPre')}</small>`; };
  async function renderOverview(){
    const P=S['承诺']||{}, R=S['就绪度']||[]; const CC=S['法定标准']||{};
    const promise = lang==='en' ? [P.commitment_en, P.readiness_en, P.note_en] : [P['承诺'], P['就绪条件'], P['说明']];
    const L3=S['三道防线']||{}; const l3=L3['第三道 应急调度']||{}; const l2=L3['第二道 预报后预置']||{}; const red=l2['红']||{};
    const d=DP||null; const rs=d&&d.replay_summary; const pv=d&&d.preposition&&d.preposition['红']&&d.preposition['红'].value;
    panel.innerHTML=`
      <h2 style="margin-top:4px">${t('ov.state')} <small>${t('ov.stateSub')}</small></h2>
      <div class="kpis">
        <div class="kpi"><b>${fmt(S['在用管长km'])} km</b><span>${t('kpi.len')}</span><small>${Math.round(S['能到出水口或泵站的管长份额']*100)}% ${t('kpi.drained')}</small></div>
        ${CC['达标管长份额']!=null?`<div class="kpi"><b>${Math.round(CC['达标管长份额']*100)}%</b><span>${t('kpi.code')}</span><small>${t('kpi.codeNote',{km:fmt(CC['不达标km'])})}</small></div>`:''}
        <div class="kpi" id="kpi-cap">${capKpi()}</div>
        <div class="kpi" id="kpi-nf">${nfKpi()}</div>
        <div class="kpi"><b>${S['高潮易涝出水口数(净空<阈值)']} / ${S['出水口总数(图上)']}</b><span>${t('kpi.tide')}</span><small>${t('kpi.tideNote')}</small></div>
        <div class="kpi"><b>${S['疑似漏登出水口/泵站的断头数']}</b><span>${t('kpi.suspect')}</span><small>${t('kpi.suspectNote',{km:fmt(S['疑似漏登断头承接的管长km'])})}</small></div>
        ${CC['校核不达标km']!=null?`<div class="kpi"><b>${fmt(CC['校核不达标km'])} km</b><span>${t('kpi.check')}</span><small>${t('kpi.checkNote')}</small></div>`:''}
      </div>
      <h2>${t('ov.lines')} <small>${t('ov.linesSub')}</small></h2>
      <div class="kpis">
        <div class="kpi l1" data-go="line1"><b>${(L3['第一道 基础设施']||{})['建议']??'—'}</b><span>${t('lines.l1')}</span><small>${t('ov.l1d',{n:SP.n_projects??'—',y:SP.years??'—'})}</small></div>
        <div class="kpi l2" data-go="line2"><b>${pv?('−'+(pv.saved_vs_after_pct??0)+'%'):(red['站点']??'—')}</b><span>${t('lines.l2')}</span><small>${pv?t('ov.l2d',{s:pv.sites,u:pv.units_placed}):t('lines.l2d')}</small></div>
        <div class="kpi l3" data-go="line3"><b>${(l3['求解器']||{}).points_served??'—'} / ${l3['积水点']??'—'}</b><span>${t('lines.l3')}</span><small>${rs?t('ov.l3d',{p:rs.served_saved_pct??0}):t('lines.l3d')}${l3['模拟']?' · '+(lang==='en'?'simulated':'模拟'):''}</small></div>
      </div>
      <div class="promise"><b>${esc(promise[0]||'')}</b><div class="m">${esc(promise[1]||'')} · ${esc(promise[2]||'')}</div></div>
      <details class="sec"><summary>${t('readiness')} <small>${R.filter(r=>r['状态']==='就绪').length}/${R.length} ${t('ov.ready')}</small></summary><div class="body">
        <table>${R.map(r=>`<tr><td style="width:34%"><span class="status" style="background:${stColor[r['状态']]}">${esc(lang==='en'?r.status_en:r['状态'])}</span> ${esc(lang==='en'?r.condition_en:r['条件'])}</td><td class="m">${esc(lang==='en'?r.evidence_en:r['证据'])}<br><i>${t('fix')}: ${esc(lang==='en'?r.todo_en:r['补什么'])}</i></td></tr>`).join('')}</table></div></details>
      <details class="sec" ${window._more.zones?'open':''}><summary>${t('zoneRank')} <small id="zonerank-sub">${scen?t('zoneRankLive',{i:scen.i_mmh}):t('zoneRankPre')}</small></summary><div class="body"><div id="zonetable"></div></div></details>
      <details class="sec"><summary>${t('ov.expert')} <small>${t('rp.expert')}</small></summary><div class="body">
        <div class="ctrl"><label>${t('rainLabel')}</label><input type="range" id="rain" min="10" max="150" step="5" value="${rainI}"><b id="rainv" style="min-width:74px;text-align:right">${rainI} mm/h</b></div>
        <div class="m" id="rainlive">${rainLive||(health?t('liveOn'):t('liveOff'))}</div></div></details>`;
    renderZoneTable(); bindRain();
    panel.querySelectorAll('[data-go]').forEach(el=>el.onclick=()=>go(el.dataset.go));
  }
  async function renderZoneTable(){
    const el=document.getElementById('zonetable'); if(!el) return;
    if(scen && scen.zones && scen.zones.length){
      const rows=slice('zones',scen.zones,10);
      el.innerHTML=`<table><tr><th>${t('th.zone')}</th><th>${t('th.underShare')}</th><th>${t('th.underKm')}</th></tr>${rows.map(z=>`<tr class="click" data-z="${esc(z.zone_name)}"><td>${esc(z.zone_name||'—')}</td><td style="width:38%"><div class="bar"><i style="width:${Math.round(z.share*100)}%"></i></div>${Math.round(z.share*100)}%</td><td class="m">${z.over_km} / ${z.len_km}</td></tr>`).join('')}</table>${moreBtn('zones',10,scen.zones.length)}`;
    } else {
      const all=(await load('zones.geojson')).features.map(f=>f.properties).filter(p=>p.rankable&&p.risk_score!=null).sort((a,b)=>b.risk_score-a.risk_score);
      const zs=slice('zones',all,10);
      el.innerHTML=`<table><tr><th>${t('th.zone')}</th><th>${t('th.risk')}</th><th>${t('th.icap')}</th><th>${t('th.share')}</th><th>${t('th.suspect')}</th></tr>${zs.map(p=>`<tr class="click" data-z="${esc(p.zone_name)}"><td>${esc(p.zone_name||'—')}</td><td><div class="bar"><i style="width:${Math.round(p.risk_score)}%"></i></div>${p.risk_score}</td><td>${p.i_cap_p50==null?'—':Math.round(p.i_cap_p50)+' mm/h'}</td><td>${Math.round((p['over_share_'+main]||0)*100)}%</td><td>${p.n_suspect_outlets||0}</td></tr>`).join('')}</table>${moreBtn('zones',10,all.length)}`;
    }
    el.querySelectorAll('tr.click').forEach(tr=>tr.onclick=()=>showZone(tr.dataset.z));
    bindMore(el);
  }
  async function showZone(name){
    zoneFilter=name; go('line1','props');
    const f=zonesGJ&&zonesGJ.features.find(x=>x.properties.zone_name===name); if(f&&f.geometry){ try{ map.fitBounds(L.geoJSON(f).getBounds(),{padding:[20,20]}); }catch(e){} }
  }

  // ---------------- 第一道：基础设施（建议 / 排期 / 达标分期 / 剩余风险） ----------------
  async function renderLine1(){
    const items=[['props',t('tabs.props')],['sched',t('tabs.sched')],['standard',t('tabs.standard')],['residual',t('tabs.residual')]];
    panel.innerHTML=segHtml(items,sub1,'sub')+`<div id="sub"></div>`;
    panel.querySelectorAll('[data-sub]').forEach(b=>b.onclick=()=>go('line1',b.dataset.sub));
    const box=document.getElementById('sub');
    await ({props:renderProps,sched:renderSched,standard:renderStandard,residual:renderResidual})[sub1](box);
  }

  // ---------------- 建议 ----------------
  let kindOn=new Set(Object.keys(kindColor)), whenOn=new Set(whenOrder), q='', selPid=null;
  async function renderProps(box){
    const feats=(await load('proposals.geojson')).features; const all=feats.map(f=>({...f.properties,_g:f.geometry}));
    const label=(id,k)=>{ const s=all.find(p=>(id==='k'?p.kind===k:p.when_class===k)); return s ? (id==='k'?F(s,'kind'):F(s,'when_class')) : k; };
    const chips=(id,arr,onSet,colorOf)=>arr.map(k=>{const n=all.filter(p=>(id==='k'?p.kind===k:p.when_class===k)&&(!zoneFilter||p.zone_name===zoneFilter)).length; if(!n) return ''; const isOn=onSet.has(k); return `<span class="chip ${isOn?'on':''}" data-c="${id}" data-k="${esc(k)}" style="${isOn&&colorOf?`background:${colorOf[k]};border-color:${colorOf[k]}`:''}">${esc(label(id,k))} (${n})</span>`;}).join('');
    box.innerHTML=`
      <input class="search" id="q" placeholder="${t('search')}" value="${esc(q)}">
      ${zoneFilter?`<div class="m" style="margin:6px 0">${t('onlyZone')} <b>${esc(zoneFilter)}</b> <span class="chip" id="clearz">${t('clear')}</span></div>`:''}
      <details class="sec" ${window._more.filters?'open':''}><summary>${t('ui.filters')} <small>${kindOn.size}/${Object.keys(kindColor).length} · ${whenOn.size}/${whenOrder.length}</small></summary><div class="body">
      <div class="chips">${chips('w',whenOrder,whenOn,null)}</div>
      <div class="chips">${chips('k',Object.keys(kindColor),kindOn,kindColor)}</div></div></details>
      <div id="list"></div>`;
    box.querySelector('details.sec').ontoggle=e=>{ window._more.filters=e.target.open; };
    document.getElementById('q').oninput=e=>{q=e.target.value;renderList(all);};
    const cz=document.getElementById('clearz'); if(cz) cz.onclick=()=>{zoneFilter=null;render();};
    box.querySelectorAll('.chip[data-c]').forEach(c=>c.onclick=()=>{const set=c.dataset.c==='k'?kindOn:whenOn; set.has(c.dataset.k)?set.delete(c.dataset.k):set.add(c.dataset.k); render();});
    renderList(all);
  }
  function renderList(all){
    const ql=q.trim().toLowerCase();
    const rows=all.filter(p=>kindOn.has(p.kind)&&whenOn.has(p.when_class)&&(!zoneFilter||p.zone_name===zoneFilter)&&(!ql||(F(p,'title')+' '+(p.zone_name||'')).toLowerCase().includes(ql)));
    const list=document.getElementById('list'); if(!list) return;
    if(!rows.length){list.innerHTML=`<div class="empty">${t('none')}</div>`;return;}
    const shown=slice('props',rows,30);
    list.innerHTML=`<div class="m">${t('nItems',{n:rows.length})}</div>`+shown.map(p=>`<div class="item ${p.pid===selPid?'sel':''}" data-pid="${p.pid}"><div class="t"><span class="tag" style="background:${kindColor[p.kind]||'#333'}">${esc(F(p,'kind'))}</span><span class="rank">#${p.rank} · ${t('priority')} ${p.priority} · ${t('confidence')} ${p.confidence}</span></div><div class="body">${esc(F(p,'title'))}</div><div class="m">${esc(F(p,'when'))}</div><div class="det" id="det-${p.pid}" style="display:${p.pid===selPid?'block':'none'}"></div></div>`).join('')+moreBtn('props',30,rows.length);
    list.querySelectorAll('.item').forEach(el=>el.onclick=e=>{ if(e.target.closest('.det')) return; selectProposal(+el.dataset.pid,true); });
    bindMore(list);
    if(selPid){ const p=all.find(x=>x.pid===selPid); if(p) fillDetail(p); }
  }
  async function selectProposal(pid,fly){
    selPid=pid; const feats=(await load('proposals.geojson')).features; const f=feats.find(x=>x.properties.pid===pid); if(!f) return;
    const p={...f.properties,_g:f.geometry};
    kindOn.add(p.kind); whenOn.add(p.when_class);
    if(zoneFilter&&p.zone_name!==zoneFilter) zoneFilter=null;
    window._more.props=true;
    if(!(tab==='line1'&&sub1==='props')){ go('line1','props'); } else { render(); }
    if(fly&&p._g){ map.flyTo([p._g.coordinates[1],p._g.coordinates[0]],15); }
    setTimeout(()=>{const el=document.querySelector(`.item[data-pid="${pid}"]`); el&&el.scrollIntoView({block:'nearest'});},200);
  }
  function fillDetail(p){
    const el=document.getElementById('det-'+p.pid); if(!el) return;
    const rows=o=>Object.entries(o||{}).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${typeof v==='object'?esc(JSON.stringify(v)):esc(v??'—')}</td></tr>`).join('');
    el.innerHTML=`<table><tr><td>${t('whyWhen')}</td><td>${esc(F(p,'when_reason'))}</td></tr><tr><td>${t('benefit')}</td><td>${esc(F(p,'benefit'))}</td></tr>${rows(F(p,'action'))}<tr><td>${t('before')}</td><td>${esc(JSON.stringify(F(p,'before')))}</td></tr><tr><td>${t('after')}</td><td>${esc(JSON.stringify(F(p,'after')))}</td></tr>${rows(F(p,'evidence'))}${F(p,'client_hotspot')?`<tr><td>${t('hs.card')}</td><td>${esc(F(p,'client_hotspot'))}</td></tr>`:''}${F(p,'regulatory')?`<tr><td>${t('reg')}</td><td>${esc(F(p,'regulatory'))}${p.regulatory_flag?` · <b>${esc(zE(p.regulatory_flag))}</b>`:''}</td></tr>`:''}<tr><td>${t('limits')}</td><td>${esc(F(p,'caveats')||'—')}</td></tr><tr><td>${t('status')}</td><td>${esc(lang==='en'?'proposed':p.status)}${t('pending')}</td></tr></table>
      <button class="btn primary" data-a="whatif">${t('whatif')}</button><button class="btn" data-a="sql">${t('sql')}</button><div id="live-${p.pid}"></div>`;
    el.querySelector('[data-a="whatif"]').onclick=async e=>{e.stopPropagation();const out=document.getElementById('live-'+p.pid);out.innerHTML=`<div class="m">${t('computing')}</div>`;const r=await api('/api/whatif?pid='+p.pid);
      if(!r){out.innerHTML=`<div class="m">${t('offlineWhatif')}</div>`;return;}
      if(r.note){out.innerHTML=`<div class="m">${esc(F(r,'note'))}</div>`;return;}
      const d=r.delta||{}; const kn=k=>{const [a,b]=k.split('@'); return b?`${a}@${scn(b)}`:a;};
      out.innerHTML=`<div class="m" style="margin-top:6px"><b>${t('justComputed')}</b> ${r.computed_at} · ${r.computed_ms} ${t('ms')} · ${t('costAbout')} ${fmt(r.cost_aed)} AED</div><table>${Object.entries(r.kpis_before).map(([k,v])=>`<tr><td>${esc(kn(k))}</td><td>${fmt(v,2)} → ${fmt(r.kpis_after[k],2)} <b style="color:${(d[k]||0)<0?'var(--ok)':'var(--muted)'}">${d[k]>0?'+':''}${fmt(d[k],2)}</b></td></tr>`).join('')}</table><div class="m">${esc(F(r,'method'))}</div>`;};
    el.querySelector('[data-a="sql"]').onclick=async e=>{e.stopPropagation();const out=document.getElementById('live-'+p.pid);const r=await api('/api/proposal?pid='+p.pid);
      out.innerHTML=r?`<div class="m">${t('runSqlAt',{db:esc(r.source_db)})}</div><pre>${esc((r.verify_sql||[]).join('\n'))}</pre>`:`<div class="m">${t('offlineShort')}</div>`;};
  }

  // ---------------- 排期 ----------------
  async function renderSched(box){
    const D=await load('schedule.json'); const rows=D.schedule||[]; const W=S['what-if']||{};
    if(!rows.length){box.innerHTML=`<div class="empty">${t('noSched')}</div>`;return;}
    const groups=whenOrder.map(w=>[w,rows.filter(r=>r.when_class===w)]).filter(([,g])=>g.length);
    box.innerHTML=`<div class="kpis"><div class="kpi"><b>${fmt(W['预算AED']/1e6,0)}M</b><span>${t('budget')} AED</span></div><div class="kpi"><b>${W['入选']}</b><span>${t('selected')}</span><small>${fmt(W['入选成本AED']/1e6,1)}M AED</small></div><div class="kpi"><b>${W['外部评估覆盖']}</b><span>${t('extCover')}</span></div></div>`+
      groups.map(([w,g])=>{const key='sched_'+w; const shown=slice(key,g,8); return `<h2>${esc(F(g[0],'when_class'))} <small>${g.length} ${t('steps')} · ${fmt(g.reduce((s,r)=>s+(r.cost_aed||0),0))} AED</small></h2><table><tr><th>${t('th.step')}</th><th>${t('th.proposal')}</th><th>${t('th.cost')}</th><th>${t('th.bpm')}</th></tr>${shown.map(r=>`<tr class="click" data-pid="${r.pid}"><td>${r.seq}</td><td><span class="tag" style="background:${kindColor[r.kind]||'#333'}">${esc(F(r,'kind'))}</span> ${esc(F(r,'title'))}${F(r,'depends_on')?`<div class="m">${esc(F(r,'depends_on'))}</div>`:''}</td><td>${fmt(r.cost_aed)}</td><td>${r.benefit_per_maed==null?'—':fmt(r.benefit_per_maed,1)}<div class="m">${esc(r.eval_source)}</div></td></tr>`).join('')}</table>${moreBtn(key,8,g.length)}`;}).join('');
    box.querySelectorAll('tr.click').forEach(tr=>tr.onclick=()=>selectProposal(+tr.dataset.pid,true));
  }

  // ---------------- 达标分期 ----------------
  async function renderStandard(box){
    const SPC=S['法定标准达标的分期']; const mode=window._stdMode||(SPC&&!SPC.error?'code':'target'); const P=(mode==='code'&&SPC&&!SPC.error)?SPC:SP;
    const c=P.curve||[]; const W=440,H=170,pad=34; const maxCost=Math.max(...c.map(x=>x.cost_aed),1); const maxShare=Math.max(...c.map(x=>x.remaining_share),0.01);
    const bw=(W-pad*2)/Math.max(c.length,1);
    const bars=c.map((x,i)=>`<rect x="${pad+i*bw+2}" y="${H-pad-(x.cost_aed/maxCost)*(H-pad*2)}" width="${Math.max(bw-4,2)}" height="${(x.cost_aed/maxCost)*(H-pad*2)}" fill="#bfdbfe"></rect><text x="${pad+i*bw+bw/2}" y="${H-pad+14}" font-size="10" text-anchor="middle" fill="#66717f">${x.year}</text>`).join('');
    const pts=c.map((x,i)=>`${pad+i*bw+bw/2},${H-pad-(x.remaining_share/maxShare)*(H-pad*2)}`).join(' ');
    const ub=S['全网达标扩管成本上限(静态,占位单价)']||{};
    box.innerHTML=`<div class="chips">${SPC&&!SPC.error?`<span class="chip ${mode==='code'?'on':''}" data-std="code">${t('stdCode')}</span>`:''}<span class="chip ${mode==='target'?'on':''}" data-std="target">${t('stdTarget',{t:scn(SP.target)})}</span></div>
      <div class="kpis"><div class="kpi"><b>${P.n_projects}</b><span>${t('th.projects')}</span></div><div class="kpi"><b>${fmt(P.total_cost_aed/1e8,0)}</b><span>${t('th.costB')} AED</span><small>${fmt(P.annual_budget_aed/1e8,0)} ${lang==='en'?'/yr':'亿/年'}</small></div><div class="kpi"><b>${P.years}</b><span>${t('th.year')}</span></div></div>
      <div class="promise"><b>${P.target==='法定标准'?t('stdTitleCode',{n:P.n_projects,c:fmt(P.total_cost_aed/1e8,0),b:fmt(P.annual_budget_aed/1e8,0),y:P.years}):t('stdTitle',{t:scn(P.target),i:P.rain_mmh,n:P.n_projects,c:fmt(P.total_cost_aed/1e8,0),b:fmt(P.annual_budget_aed/1e8,0),y:P.years})}</b><div class="m">${t('stdNote',{s:P.n_suspect_first,km:P.suspect_km,u:P.unfixable_edges})}</div></div>
      <svg class="svgchart" viewBox="0 0 ${W} ${H}"><text x="${pad}" y="12" font-size="11" fill="#66717f">${t('chartNote')}</text>${bars}<polyline points="${pts}" fill="none" stroke="#0b5fd7" stroke-width="2"></polyline>${c.map((x,i)=>`<circle cx="${pad+i*bw+bw/2}" cy="${H-pad-(x.remaining_share/maxShare)*(H-pad*2)}" r="3" fill="#0b5fd7"></circle>`).join('')}</svg>
      <table><tr><th>${t('th.year')}</th><th>${t('th.projects')}</th><th>${t('th.costB')}</th><th>${t('th.fixedKm')}</th><th>${t('th.remKm')}</th><th>${t('th.remShare')}</th></tr>${c.map(x=>`<tr><td>${x.year}</td><td>${x.n_projects}</td><td>${(x.cost_aed/1e8).toFixed(1)}</td><td>${x.km_fixed}</td><td>${x.remaining_surcharge_km}</td><td>${Math.round(x.remaining_share*100)}%</td></tr>`).join('')}</table>
      <details class="sec"><summary>${t('upperBoundShort')}</summary><div class="body m">${t('upperBound')} ${Object.entries(ub).map(([k,v])=>t('upperItem',{k:scn(k),i:v['雨强mm/h'],c:v['扩管成本上限_亿AED'],km:v['需扩管km']})).join('; ')}</div></details>`;
    box.querySelectorAll('[data-std]').forEach(ch=>ch.onclick=()=>{window._stdMode=ch.dataset.std; render();});
  }

  // ---------------- 剩余风险 ----------------
  async function renderResidual(box){
    const r=await load('residual.geojson'); const R=S['预算内方案实施后剩余']||{};
    const cnt={}, nameOf={}; r.features.forEach(f=>{cnt[f.properties.kind]=(cnt[f.properties.kind]||0)+1; nameOf[f.properties.kind]=F(f.properties,'kind');});
    box.innerHTML=`<div class="promise"><b>${t('resTitle',{n:S['what-if']?S['what-if']['入选']:'',t:scn(R['目标情景'])})}</b><div class="m">${t('resNote')}</div></div>
      <div class="kpis"><div class="kpi"><b>${R['实施前超载km']} → ${R['实施后超载km']}</b><span>${t('resBefore')} km</span></div><div class="kpi"><b>${R['仍高潮暴露出水口']}</b><span>${t('resTide')}</span></div><div class="kpi"><b>${R['仍疑似漏登终点']}</b><span>${t('resSuspect')}</span></div></div>
      <h2>${t('mapFeatures')}</h2><table>${Object.entries(cnt).map(([k,v])=>{const s=resStyle[k]||{color:'#333'}; return `<tr><td>${s.dash?`<span class="ln dash" style="border-color:${s.color};vertical-align:middle;margin-right:8px"></span>`:`<span class="ring" style="border-color:${s.color};vertical-align:middle;margin-right:8px;width:12px;height:12px"></span>`}${esc(nameOf[k])}</td><td>${v}${k==='仍超载管段'?t('only1500'):''}</td></tr>`;}).join('')}</table>
      <div class="m" style="margin-top:8px">${t('resWhy',{c:fmt(SP.total_cost_aed/1e8,0)})}</div>`;
  }

  // ---------------- 第二道：预报后预置 ----------------
  const pctTxt=x=>x==null?'—':((x>=0?'−':'+')+Math.abs(x)+'%');
  const alertLabel=(d,a)=>{ const m=d.preposition[a].manual; const lab=lang==='en'?(d.preposition[a].alert_en||a):a; const lv=m?(m.level==='0'?t('dp.routine'):(lang==='en'?'Level '+m.level:m.level+' 级')):''; return lab+(lv?' · '+lv:''); };
  function sourceBadge(d){
    if(d.simulated) return `<div class="promise warn" style="margin:0 0 8px"><b>${t('dp.simBadge')}</b></div>`;
    const sys=(d.flood_source||{}).type==='system';
    const txt = sys ? t(d.fleet_source==='client_plan_a'?'dp.sysBadgeA':'dp.sysBadge') : (d.fleet_source==='client_plan_a'?t('dp.gwmBadgeA',{m:(d.flood_source||{}).name||'?'}):t('dp.gwmBadge',{m:(d.flood_source||{}).name||'?'}));
    const st=d.flood_source&&d.flood_source.stats;
    return `<details class="sec" style="margin:0 0 8px"><summary>${t('ui.source')} <small>${sys?t('ui.srcSystem'):t('ui.srcModel')}</small></summary><div class="body m">${esc(txt)}${st?`<br>${t('dp.srcSystem',{n:st.n_points,g:st.n_network_gap,p:st.n_dtm_proxy,h:st.n_hotspots,v:fmt(st.volume_total_m3),s:fmt(st.spill_total_m3),rp:(d.scenario||{}).return_period_yr})}`:''}${d.flood_source&&d.flood_source.aggregation?`<br>${t('dp.srcLine',{m:esc(d.flood_source.name),v:esc(d.flood_source.version||''),w:d.flood_source.aggregation.wet_cells,l:d.flood_source.aggregation.land_cells,th:d.flood_source.aggregation.depth_min_m,c:d.flood_source.aggregation.cores,p:d.flood_source.aggregation.points})}`:''}<br>${t('dp.title')}：${esc(F(d.scenario,'name'))} · ${t('dp.method')}：${esc(F(d,'travel_method'))} ${d.travel_ms} ${t('dp.ms')}${d.computed_ms?` · ${t('justComputed')} ${d.computed_at}`:''}</div></details>`;
  }
  // ---------------- 预警触发（模拟一条预报） ----------------
  const lvColor={'蓝':'#2563eb','黄':'#ca8a04','橙':'#ea580c','红':'#b91c1c'};
  function bindForecast(){
    const sl=document.getElementById('fc-p24'), sv=document.getElementById('fc-p24v'), go=document.getElementById('fc-go'), out=document.getElementById('fc-out'), sec=document.getElementById('fc-sec'); if(!sl) return;
    sl.oninput=()=>{ sv.textContent=sl.value+' mm'; window._fcP24=+sl.value; };
    const q=U.get('fc'); if(q&&!window._fcDone){ window._fcDone=true; sl.value=+q; sv.textContent=q+' mm'; window._fcP24=+q; if(sec) sec.open=true; setTimeout(()=>go.click(),50); }   // 深链接 ?fc=90 直接生成
    if(sec) sec.addEventListener('toggle',()=>{ window._fcOpen=sec.open; });
    go.onclick=async()=>{ go.disabled=true; go.textContent=t('computing'); const lead=document.getElementById('fc-lead').value;
      const a=await api('/api/alert?p24='+sl.value+'&lead_h='+lead); go.disabled=false; go.textContent=t('al.go');
      if(!a||a.error){ out.innerHTML=`<span class="m">${esc((a&&a.error)||t('liveOff'))}</span>`; return; }
      const L=lang==='en'; const lvName=L?a.level_en:a.level;
      out.innerHTML=`<div style="display:flex;align-items:center;gap:10px;margin:4px 0 8px"><span class="tag" style="background:${lvColor[a.level]||'#333'};font-size:13px;line-height:24px;padding:0 10px">${esc(lvName)} · ${esc(L?('Level '+(a.manual.level||'-')):((a.manual.level||'-')+' 级'))}</span><b>${esc(L?a.headline_en:a.headline)}</b></div>
        <div class="m" style="margin-bottom:6px">${esc(L?a.manual.en:a.manual.zh)||''}</div>
        <div class="m">${t('al.scen',{s:esc(L?a.scenario.label_en:a.scenario.label),p:a.scenario.p24_mm,d:(a.scenario.delta_mm>=0?'+':'')+a.scenario.delta_mm})}</div>
        <table style="margin:6px 0"><tr><th>${t('al.colRule')}</th><th>${t('al.colVal')}</th><th>${t('al.colThr')}</th><th>${t('al.colLv')}</th></tr>
          ${a.triggers.map(x=>`<tr><td>${esc(t('al.rule_'+x.rule))}</td><td>${x.value==null?'—':fmt(x.value)}</td><td class="m">${['黄','橙','红'].map(k=>`${L?({'黄':'Y','橙':'O','红':'R'})[k]:k} ≥ ${fmt(x.thresholds[k])}`).join(' · ')}</td><td><span class="tag" style="background:${lvColor[x.level]}">${esc(L?({'蓝':'Blue','黄':'Yellow','橙':'Orange','红':'Red'})[x.level]:x.level)}</span></td></tr>`).join('')}</table>
        <h2 style="margin:8px 0 4px">${t('al.actions')} <small>${t('al.actionsSub')}</small></h2>
        <table>${a.actions.map(x=>`<tr><td style="width:24%"><b>${esc(L?x.role_en:x.role)}</b><div class="m">${esc(x.contact||'')}</div></td><td>${esc(L?x.en:x.zh)}</td></tr>`).join('')}</table>
        <h2 style="margin:8px 0 4px">${t('al.timeline')}</h2>
        <div class="m">${a.timeline.map(x=>`<b>${esc(x.t)}</b> ${esc(L?x.en:x.zh)}`).join(' → ')}</div>
        <div class="m" style="margin-top:6px">${esc(L?a.note_en:a.note)} · ${t('al.saved')}</div>`; };
  }
  async function loadStaging(alertSel){ const box=document.getElementById('stgbox'); if(!box) return; const r=await api(`/api/staging_status?rp=${encodeURIComponent(RP)}&alert=${encodeURIComponent(alertSel)}`); if(!r||r.error){ box.textContent=(r&&r.error)||''; return; }
    const EN=lang==='en'; const tt=r.totals||{}; const rows=(r.rows||[]).slice().sort((a,b)=>b.need_units-a.need_units).slice(0,12);
    box.className='stg'; box.innerHTML=`<h2 style="margin:6px 0 2px">${t('stg.title')} <small class="m">${r.plan_only?t('stg.planOnly'):t('stg.ready',{p:fmt(tt.readiness_pct,0)})}</small></h2>
      <div class="m">${t('stg.totals',{n:fmt(tt.need_units,0),e:fmt(tt.erp_plan_units,0),c:tt.checked_in_units==null?'—':fmt(tt.checked_in_units,0),s:tt.depots_short_vs_plan})}</div>
      <table><tr><th>${t('v2.depot')}</th><th>${t('stg.need')}</th><th>${t('stg.erp')}</th><th>${t('stg.checkin')}</th><th>${t('stg.readiness')}</th></tr>${rows.map(x=>`<tr><td title="${esc(x.name||'')}">${esc(String(x.name||x.depot_id).split('·')[0].slice(0,22))}<div class="m">${esc(x.area||'')}</div></td><td>${x.need_units}</td><td class="${x.gap_vs_plan<0?'short':''}">${x.erp_plan_units}${x.gap_vs_plan<0?` (${x.gap_vs_plan})`:''}</td><td>${x.checked_in_units==null?'—':x.checked_in_units}</td><td>${x.readiness_pct==null?'—':`<span class="ro"><i style="width:${x.readiness_pct}%"></i></span> ${x.readiness_pct}%`}</td></tr>`).join('')}</table>
      <div class="m">${esc(EN?(r.basis_en||r.basis):r.basis)}</div>`; }
  async function renderLine2(){
    const d=await getDispatch(); DP=d;
    const alerts=Object.keys(d.preposition||{}); const alertSel=window._alertSel||alerts[alerts.length-1]; window._alertSel=alertSel; setTimeout(()=>loadStaging(alertSel),0);
    const pre=(d.preposition||{})[alertSel]||{}; const v=pre.value&&!pre.value.error?pre.value:null;
    const sites=(pre.sites||[]).slice().sort((a,b)=>b.covers.length-a.covers.length); const shown=slice('sites',sites,6);
    panel.innerHTML=`
      ${sourceBadge(d)}
      <details class="sec" ${window._fcOpen?'open':''} id="fc-sec"><summary>${t('al.title')} <small>${t('al.sub')}</small></summary><div class="body">
        <div class="ctrl"><label>${t('al.p24')}</label><input type="range" id="fc-p24" min="10" max="180" step="5" value="${window._fcP24||90}"><b id="fc-p24v" style="min-width:64px;text-align:right">${window._fcP24||90} mm</b>
          <label>${t('al.lead')}</label><select id="fc-lead" style="font-size:12px"><option value="48">48 h</option><option value="24" selected>24 h</option><option value="12">12 h</option><option value="6">6 h</option></select>
          <button class="btn primary" id="fc-go" style="margin:0">${t('al.go')}</button></div>
        <div id="fc-out" class="m">${t('al.hint')}</div></div></details>
      <div class="m" style="margin-bottom:4px">${t('l2.pick')}</div>
      ${segHtml(alerts.map(a=>[a,alertLabel(d,a)]),alertSel,'alert')}
      <div id="stgbox" class="m" style="margin:6px 0">${t('computing')}</div>
      ${pre.manual?`<div class="m" style="margin:-6px 0 10px">${esc(lang==='en'?pre.manual.en:pre.manual.zh)}</div>`:''}
      ${v?`<div class="kpis">
        <div class="kpi l2"><b>−${v.saved_vs_after_pct??0}%</b><span>${t('l2.kFlood')}</span><small>${fmt(v.flood_h.after,0)} → ${fmt(v.flood_h.pre,0)} h · ${t('l2.vsAfter')}</small></div>
        <div class="kpi l2"><b>${v.level_iii_served.after} → ${v.level_iii_served.pre}</b><span>${t('l2.kIII')}</span><small>${t('l2.onTarget')} ${v.within_target.after}/${v.within_target.after_n} → ${v.within_target.pre}/${v.within_target.pre_n}</small></div>
        <div class="kpi l2"><b>${v.arrive_median_min} ${t('dp.min')}</b><span>${t('l2.kArrive')}</span><small>${t('l2.afterArrive')} ${v.after_arrive_median_min??'—'} ${t('dp.min')}</small></div>
      </div>
      <div class="promise"><b>${t('pv.head',{s:v.sites,u:v.units_placed,d:v.depart_min,a:v.arrive_median_min,b:v.after_arrive_median_min??'—'})}</b><div class="m">${t('pv.flood',{b:fmt(v.flood_h.base,0),a:fmt(v.flood_h.after,0),p:fmt(v.flood_h.pre,0),x:v.after_saved_vs_base_pct??0,y:v.saved_vs_base_pct??0,z:v.saved_vs_after_pct??0})}</div>${v.cost?`<div class="m">${t('pv.cost',{c:aed(v.cost.total_aed),h:fmt(v.cost.pump_hours),a:aed((v.cost_after||{}).total_aed)})}</div>`:''}</div>`:''}
      <h2>${t('l2.compare')} <small>${t('l2.compareSub')}</small></h2>
      <table><tr><th>${t('pv.colAlert')}</th><th>${t('pv.colSites')}</th><th>${t('pv.colUnits')}</th><th>${t('pv.colArrive')}</th><th>${t('pv.colFlood')}</th><th>${t('pv.colVsAfter')}</th><th>${t('pv.colTarget')}</th></tr>
        ${alerts.map(a=>{const vv=(d.preposition[a]||{}).value; if(!vv||vv.error) return ''; return `<tr class="click ${a===alertSel?'hl':''}" data-alert="${a}"><td>${lang==='en'?(d.preposition[a].alert_en||a):a}</td><td>${vv.sites}</td><td>${vv.units_placed}</td><td>${vv.arrive_median_min}</td><td>${fmt(vv.flood_h.pre,0)} h</td><td>${vv.saved_vs_after_pct!=null?(vv.saved_vs_after_pct>=0?'−':'+')+Math.abs(vv.saved_vs_after_pct)+'%':'—'}</td><td>${vv.within_target.pre}/${vv.within_target.pre_n}</td></tr>`;}).join('')}</table>
      ${d.plans&&Object.keys(d.plans).length?`<h2>${t('l2.plans')} <small>${t('l2.plansSub')}</small></h2>
      <table class="plans"><tr><th>${t('l2.colPlan')}</th><th>${t('l2.colPumpArr')}</th><th>${t('l2.colFloodVs')}</th><th>${t('l2.colCovered')}</th><th>${t('l2.colTargetIII')}</th><th>${t('l2.colCost')}</th></tr>
        ${(()=>{const cell=(w,p)=>`<td>${w.pumps_placed??w.units_placed}${p&&p.pumps_total&&p.pumps_total!==(w.pumps_placed??w.units_placed)?`<span class="m">/${p.pumps_total}</span>`:''}${w.tankers_placed?`<div class="m">+${w.tankers_placed} ${t('dp.tankers')}</div>`:''}<div class="m">${w.arrive_median_min} min</div></td><td>${fmt(w.flood_h.pre,0)} h<div class="m">${pctTxt(w.saved_vs_after_pct)}</div></td><td>${w.served_flood_h?`${pctTxt(w.served_flood_h.saved_pct)}<div class="m">${w.served_flood_h.n} ${t('dp.pts')}</div>`:'—'}</td><td>${w.within_target.pre}/${w.within_target.pre_n}<div class="m">III ${w.level_iii_served.pre}</div></td><td>${w.cost?`${aed(w.cost.total_aed)}<div class="m">${fmt(w.cost.pump_hours)} h</div>`:'—'}</td>`;
          const ours=v?`<tr class="hl"><td>${esc(t('l2.ours',{a:alertLabel(d,alertSel)}))}</td>${cell(v,null)}</tr>`:'';
          return ours+Object.entries(d.plans).map(([k,p])=>{const w=p.value; const lab=lang==='en'?(p.label_en||p.label):p.label; const srcTxt=(lang==='en'?(p.source_en||p.source):p.source)||''; const note=`<div class="m">${esc(srcTxt)}${p.ponds?` · ${p.ponds} ${t('l2.ponds')} ${fmt(p.pond_m3,0)} m³`:''}</div>`; if(!w) return `<tr><td>${esc(lab)}${note}</td><td colspan="5" class="m">${esc(p.error||'—')}</td></tr>`; return `<tr><td>${esc(lab)}${note}</td>${cell(w,p)}</tr>`;}).join('');})()}</table>
      <div class="m">${t('l2.plansNote',{b:fmt((v||{flood_h:{}}).flood_h.base,0),a:fmt((v||{flood_h:{}}).flood_h.after,0)})}${d.cost&&d.cost.units?' '+t('l2.costNote',{m:fmt(d.cost.mobilisation_aed_per_unit),h:d.cost.min_duty_h,a:aed(d.cost.total_aed),ph:fmt(d.cost.pump_hours)}):''}</div>`:''}
      <h2>${t('dp.sites')} <small>${pre.sites?pre.sites.length:0} · ${t('dp.cover')} ${pre.covered??0} / ${pre.points??0} · ${t('dp.prioShare')} ${Math.round((pre.covered_priority_share||0)*100)}%${pre.covered_trt!=null?` · ${t('dp.coverTrt',{t:pre.trt_min})} ${pre.covered_trt}`:''}</small></h2>
      <table><tr><th>${t('l2.site')}</th><th>${t('dp.pts')}</th><th>${t('pv.colUnits')}</th><th>${t('l2.need')}</th></tr>${shown.map(s=>`<tr class="click" data-site="${esc(s.site_id)}"><td>${esc(s.name)}</td><td>${s.covers.length}</td><td>${s.units_suggested}</td><td class="m">${fmt(s.required_rate_m3h)} m³/h</td></tr>`).join('')}</table>${moreBtn('sites',6,sites.length)}
      <details class="sec"><summary>${t('l2.how')}</summary><div class="body m">${t('l2.howText',{d:v?v.depart_min:30})}</div></details>`;
    panel.querySelectorAll('[data-alert]').forEach(c=>c.onclick=()=>{ window._alertSel=c.dataset.alert; render(); });
    bindForecast();
    panel.querySelectorAll('tr.click[data-site]').forEach(tr=>tr.onclick=()=>{ const s=(pre.sites||[]).find(x=>x.site_id===tr.dataset.site); if(s) map.flyTo([s.lat,s.lon],13); });
    drawPrepo();
  }

  // ---------------- 第三道：应急调度 ----------------
  const triColor = k => /可派泵/.test(k)&&!/接力/.test(k)?'#15803d':/接力/.test(k)?'#0e7490':/无需/.test(k)?'#94a3b8':/封路/.test(k)?'#b91c1c':'#d97706';
  // ---------------- 第三道：地图时间轴（雨前 / 雨中 / 雨后；无泵 vs 有泵） ----------------
  function tlReset(){
    if(TLS&&TLS.timer) clearInterval(TLS.timer);
    if(layers.flood) layers.flood.eachLayer(l=>{ const p=l.feature&&l.feature.properties; if(!p) return; if(l.setRadius) l.setRadius(6); l.setStyle({color:'#fff',weight:1,fillColor:floodColor(p),fillOpacity:.9}); });
    TLS=null;
  }
  function setupTimeline(d){
    const TL=d.timeline, box=document.getElementById('tl'); if(!TL||!box||!TL.t_min||!TL.t_min.length) return;
    const T=TL.t_min, N=T.length, P=TL.points||{}, ids=Object.keys(P), WET=50;
    const sumB=new Array(N).fill(0), sumP=new Array(N).fill(0), nB=new Array(N).fill(0), nP=new Array(N).fill(0); let VREF=1;
    ids.forEach(k=>{ const c=P[k], pp=c.pump||c.base; for(let i=0;i<N;i++){ sumB[i]+=c.base[i]; sumP[i]+=pp[i]; if(c.base[i]>WET) nB[i]++; if(pp[i]>WET) nP[i]++; if(c.base[i]>VREF) VREF=c.base[i]; } });
    const sl=document.getElementById('tlr'), tt=document.getElementById('tlt'), play=document.getElementById('tlplay'), sum=document.getElementById('tlsum'), svgBox=document.getElementById('tlsvg');
    TLS={i:0,mode:'base',timer:null};
    const W=300,H=76,px=6,py=9, vmax=Math.max(1,...sumB), X=i=>px+(W-2*px)*i/Math.max(N-1,1), Y=v=>H-py-(H-2*py)*v/vmax;
    const line=(arr,col,dash)=>`<polyline fill="none" stroke="${col}" stroke-width="1.6" ${dash?'stroke-dasharray="4 3"':''} points="${arr.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(' ')}"/>`;
    const dec=d.decision_time_min!=null?Math.round(d.decision_time_min/TL.dt_min):null;
    const ticks=[0,Math.floor((N-1)/2),N-1].map(i=>`<text x="${X(i).toFixed(1)}" y="${H-1}" font-size="8" fill="#6b7280" text-anchor="${i===0?'start':(i===N-1?'end':'middle')}">${hrs(T[i])}</text>`).join('');
    const draw=()=>{ svgBox.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${line(sumB,'#64748b',true)}${line(sumP,'#1d4ed8',false)}${dec!=null&&dec<N?`<line x1="${X(dec).toFixed(1)}" y1="${py}" x2="${X(dec).toFixed(1)}" y2="${H-py}" stroke="#b45309" stroke-dasharray="2 2"/>`:''}<line x1="${X(TLS.i).toFixed(1)}" y1="${py-5}" x2="${X(TLS.i).toFixed(1)}" y2="${H-py}" stroke="#b91c1c" stroke-width="1.5"/>${ticks}<text x="${px}" y="${py}" font-size="8" fill="#6b7280">${fmt(vmax,0)} m³</text></svg>`; };
    const summary=()=>{ const i=TLS.i, m=TLS.mode; sum.textContent=t('l3.tlSum',{n:(m==='pump'?nP:nB)[i],v:fmt((m==='pump'?sumP:sumB)[i],0),nb:nB[i],vb:fmt(sumB[i],0)}); };
    const apply=async()=>{ if(!TLS) return; const i=TLS.i, m=TLS.mode; tt.textContent=hrs(T[i]); sl.value=i; draw(); summary();
      if(!layers.flood){ if(!on('flood')) return; await ensureDispatchLayers(); }
      layers.flood.eachLayer(l=>{ const p=l.feature&&l.feature.properties; if(!p) return; const c=P[p.fp_id];
        if(!c){ if(l.setRadius) l.setRadius(3); l.setStyle({color:'#fff',weight:1,fillColor:'#cbd5e1',fillOpacity:.35}); return; }
        const v=(m==='pump'&&c.pump?c.pump:c.base)[i], wet=v>WET;
        if(l.setRadius) l.setRadius(wet?4+12*Math.sqrt(v/VREF):3);
        l.setStyle({color:c.pump?'#1d4ed8':'#fff',weight:c.pump?2:1,fillColor:wet?'#b91c1c':'#94a3b8',fillOpacity:wet?.8:.3}); }); };
    const stop=()=>{ if(TLS&&TLS.timer){ clearInterval(TLS.timer); TLS.timer=null; } play.textContent='▶'; };
    sl.oninput=()=>{ if(!TLS) TLS={i:0,mode:'base',timer:null}; TLS.i=+sl.value; apply(); };
    box.querySelectorAll('.seg.mini button').forEach(b=>b.onclick=()=>{ box.querySelectorAll('.seg.mini button').forEach(x=>x.classList.toggle('on',x===b)); if(!TLS) TLS={i:0,mode:'base',timer:null}; TLS.mode=b.dataset.m; apply(); });
    play.onclick=()=>{ if(!TLS) TLS={i:0,mode:'base',timer:null}; if(TLS.timer){ stop(); return; } if(TLS.i>=N-1) TLS.i=0; play.textContent='⏸'; TLS.timer=setInterval(()=>{ if(!TLS){ return; } if(TLS.i>=N-1){ stop(); return; } TLS.i++; apply(); },120); };
    document.getElementById('tlreset').onclick=()=>{ stop(); tlReset(); TLS={i:0,mode:'base',timer:null}; tt.textContent=hrs(T[0]); sl.value=0; draw(); summary(); };
    draw(); summary();
    const q=U.get('tl'); if(q!=null&&q!==''){ TLS.i=Math.min(N-1,Math.max(0,Math.round(+q/TL.dt_min))); if(U.get('tlm')==='pump'){ TLS.mode='pump'; box.querySelectorAll('.seg.mini button').forEach(x=>x.classList.toggle('on',x.dataset.m==='pump')); } apply(); }   // 深链接 ?tl=分钟&tlm=pump
  }
  // ---- v2 求解器（处置包 + 资源 + 滚动 + 费用进目标）的页面块：三方案切换、费用拆解、资源利用、缺口
  let V2OBJ = null;
  function v2Block(d){ const v=d.v2; if(!v) return ''; const EN=lang==='en'; const pa=v.pareto||[]; const c=v.cost||{}; const util=v.utilisation||{}; const sch=v.schedule||{}; const res=v.resources||{}; const tot=res.totals||{};
    const used={units:0,tankers:0,crews:0,hose:0,fuel:0}; Object.values(util).forEach(u=>{ used.units+=u.units_used||0; used.tankers+=u.tankers_used||0; used.crews+=u.crews_used||0; used.hose+=u.hose_used_m||0; used.fuel+=u.fuel_used_l||0; });
    const bar=(a,b,color)=>`<div class="track" style="margin:2px 0"><i style="width:${Math.min(100,Math.round(100*(a||0)/Math.max(b||1,1)))}%;background:${color||'var(--l3)'}"></i></div>`;
    const objTabs=`<span class="seg mini" id="v2obj">${['fastest','balanced','cheapest'].map(o=>`<button data-o="${o}" class="${(v.objective||'balanced')===o?'on':''}">${t('v2.'+o)}</button>`).join('')}</span>`;
    const robTabs=`<span class="seg mini" id="v2rob" title="${esc(t('v2.robBtnHint'))}"><button data-r="0" class="${d.robust?'':'on'}">${t('v2.single')}</button><button data-r="1" class="${d.robust?'on':''}">${t('v2.robust')}</button></span>`;
    const rb=v.robust||null; const rbW=(rb&&rb.weights)||{}; const rbL=(rb&&rb.labels)||{}; const rbLE=(rb&&rb.labels_en)||{};
    const br=(a,b)=>(b!=null?` <span class="m">(${b})</span>`:'');
    const robRows=rb&&rb.by_scenario?Object.entries(rb.by_scenario).filter(([k,e])=>e&&!e.error).map(([k,e])=>{ const b=e.base||{}; const lab=EN?(rbLE[k]||rbL[k]||k):(rbL[k]||k); return `<tr class="${k===rb.frame?'sel':''}"><td>${esc(lab)}</td><td>${rbW[k]??'—'}</td><td>${e.within_target_worse}/${e.points_in_plan}${br(0,b.within_target_worse)}</td><td>${e.never_empties_worse}${br(0,b.never_empties_worse)}</td><td>${e.extra_12in_equiv_total}${br(0,b.extra_12in_equiv_total)}</td></tr>`; }).join(''):'';
    const ex=rb&&rb.expected, exb=(rb&&rb.expected_base)||null;
    const robBlock=rb&&rb.by_scenario?`<div style="margin:6px 0;padding:6px 8px;border-left:3px solid #7c3aed;background:#f5f3ff"><b>${t('v2.robTitle')}</b> <small class="m">${t('v2.robSub')}</small>
      <table><tr><th>${t('v2.robScen')}</th><th>${t('v2.robW')}</th><th>${t('v2.robOn')}</th><th>${t('v2.robNever')}</th><th>${t('v2.robExtra')}</th></tr>${robRows}${ex?`<tr><td><b>${t('v2.robExp')}</b></td><td>—</td><td>${fmt(ex.within_target,1)}${br(0,exb?fmt(exb.within_target,1):null)}</td><td>${fmt(ex.never_empties,1)}${br(0,exb?fmt(exb.never_empties,1):null)}</td><td>${fmt(ex.extra_12in_equiv_total,1)}${br(0,exb?fmt(exb.extra_12in_equiv_total,1):null)}</td></tr>`:''}</table><div class="m">${t('v2.robLegend')}</div></div>`:'';
    const tr=d.traffic||null; const trSrc=tr?(tr.provider==='google'?t('v2.trfGoogle'):(tr.provider==='none'?t('v2.trfOff'):t('v2.trfSim'))):'';
    const trLine=tr?`<div class="m" style="margin:4px 0;display:flex;flex-wrap:wrap;gap:6px;align-items:center"><span class="chip" style="${tr.provider==='google'?'border-color:#16a34a;color:#166534':''}">${t('v2.trf')}: ${esc(trSrc)}</span><span>${t('v2.trfFactor',{a:fmt(tr.factor_min,2),b:fmt(tr.factor_max,2)})}</span><span>${t('v2.trfDep',{d:esc(tr.departure_local||'')})}</span>${tr.series&&tr.series_min!=null?`<span title="${esc(Object.entries(tr.series).map(([k,v])=>k+' min: ×'+fmt(Object.values(v).reduce((a,b)=>a+b,0)/Math.max(1,Object.values(v).length),2)).join(' · '))}">${t('v2.trfSeries',{n:Object.keys(tr.series).length-1,a:fmt(tr.series_min,2),b:fmt(tr.series_max,2)})}</span>`:''}${tr.provider==='google'?`<span>${t('v2.trfElements',{n:tr.elements||0})}</span>`:`<span title="${esc(tr.note||'')}">${t('v2.trfSimNote')}</span>`}<button class="btn" id="trfbtn" style="margin:0;padding:2px 8px;font-size:11px">${t('v2.trfRefresh')}</button>${tr.provider==='google'?`<button class="btn" id="trfref" style="margin:0;padding:2px 8px;font-size:11px">${t('v2.trfRefineBtn')}</button>`:''}<span id="trfout"></span></div>`:'';
    const paretoRows=pa.map(x=>`<tr class="${x.selected?'sel':''}"><td>${esc(EN?x.en:x.zh)}</td><td>${aed(x.cost_aed)}</td><td>${x.points_served??'—'}</td><td>${fmt(x.weighted_flood_hours,0)}</td><td>${x.units??'—'}${x.tankers?` + ${x.tankers} ${t('dp.tankers')}`:''}</td><td>${x.critical_within_target??'—'}</td></tr>`).join('');
    const wins=(v.windows||[]).filter(w=>w.points_served!=null).map(w=>`<span class="chip" title="${esc(w.status||'')}">${t('v2.win',{n:w.window,t:hrs(w.t_min)})} · ${w.points_served} ${t('dp.pts')} · ${w.units} ${t('dp.units')}</span>`).join(' ');
    const depots=Object.values(util).sort((a,b)=>(b.units_used||0)-(a.units_used||0)).slice(0,8).map(u=>{ const sd=(sch.depots||{})[u.depot_id||'']||Object.values(sch.depots||{}).find(x=>x.name===u.name)||{};
      return `<tr><td title="${esc(u.name||'')}">${esc(String(u.name||'').split('·')[0].slice(0,22))}<div class="m">${esc(u.area||'')}</div></td><td>${u.units_used}/${u.units_total}${bar(u.units_used,u.units_total)}</td><td>${u.crews_used}/${u.crews_total}${bar(u.crews_used,u.crews_total,'#7c3aed')}${sd.persons_shortage?`<div class="m" style="color:var(--warn)">${t('v2.short',{n:sd.persons_shortage})}</div>`:''}</td><td>${sd.trips??'—'}/${sd.trip_capacity?Object.values(sd.trip_capacity).reduce((a,b)=>a+b,0):'—'}<div class="m">${t('v2.veh',{h:(u.vehicles||{}).heavy??0,l:(u.vehicles||{}).light??0})}</div></td><td>${fmt(u.hose_used_m,0)}/${fmt(u.hose_total_m,0)}${bar(u.hose_used_m,u.hose_total_m,'#0891b2')}</td><td>${fmt(u.fuel_used_l,0)}/${fmt(u.fuel_total_l,0)}${bar(u.fuel_used_l,u.fuel_total_l,'#ca8a04')}</td><td>${u.tankers_used}/${u.tankers_total}</td></tr>`; }).join('');
    const unserved=Object.entries(v.unserved_reasons||{}).sort((a,b)=>b[1]-a[1]).map(([k,n])=>`<span class="chip">${esc(EN&&d.triage_en&&d.triage_en[k]?d.triage_en[k]:k)} · ${n}</span>`).join(' ');
    return `<div class="card" style="margin:8px 0;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#fff">
      <h2 style="margin:0 0 6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">${t('v2.title')} ${objTabs} ${robTabs} <small class="m">${t('v2.sub',{v:fmt(v.value_aed_per_priority_hour,0)})}</small></h2>
      ${trLine}
      ${paretoRows?`<table><tr><th>${t('v2.option')}</th><th>${t('v2.cost')}</th><th>${t('dp.served')}</th><th>${t('dp.wfh')}</th><th>${t('dp.units')}</th><th>${t('dp.crit')}</th></tr>${paretoRows}</table>`:''}
      ${robBlock}
      <div class="m" style="margin:6px 0">${t('v2.costBreak',{d:aed(c.duty_aed),c:aed(c.crew_aed),f:aed(c.fuel_aed),v:aed(c.vehicle_aed),tk:aed(c.tanker_aed),m:aed(c.mobilisation_aed),tot:aed(c.total_aed)})}${v.budget_aed?` · ${t('v2.budget',{b:aed(v.budget_aed)})}`:''}</div>
      <div class="m" style="margin:4px 0;display:flex;flex-wrap:wrap;gap:4px;align-items:center"><span>${t('v2.windows')}</span> ${wins}</div>
      <h2 style="margin:10px 0 4px">${t('v2.resources')} <small class="m">${res.simulated?t('v2.simulated'):t('v2.real')} · ${t('v2.totals',{u:used.units,ut:tot.units,c:used.crews,ct:tot.persons,od:tot.on_duty,h:fmt(used.hose,0),ht:fmt(tot.hose_m,0),f:fmt(used.fuel,0),ft:fmt(tot.fuel_l,0),tk:used.tankers,tkt:tot.tankers})}</small></h2>
      <table><tr><th>${t('v2.depot')}</th><th>${t('v2.pumps')}</th><th>${t('v2.crews')}</th><th>${t('v2.trips')}</th><th>${t('v2.hose')}</th><th>${t('v2.fuel')}</th><th>${t('dp.tankers')}</th></tr>${depots}</table>
      <div class="m" style="margin:6px 0">${t('v2.gaps',{s:sch.persons_shortage||0,r:sch.relief_crews_needed||0,tr:(sch.trips||[]).length})}</div>
      ${v.silo_vs_pooled&&!v.silo_vs_pooled.error?`<div class="m" style="margin:6px 0;padding:6px 8px;border-left:3px solid var(--acc);background:var(--acc-soft)"><b>${t('v2.siloTitle')}</b> ${t('v2.silo',{dp:v.silo_vs_pooled.delta_points,cu:v.silo_vs_pooled.cross_zone_units||0,dc:aed(v.silo_vs_pooled.delta_cost_aed),sp:(v.silo_vs_pooled.silo||{}).points_served,sc:aed((v.silo_vs_pooled.silo||{}).cost_aed),pp:(v.silo_vs_pooled.pooled||{}).points_served,pc:aed((v.silo_vs_pooled.pooled||{}).cost_aed)})}</div>`:''}
      ${unserved?`<div class="m" style="display:flex;flex-wrap:wrap;gap:4px;align-items:center"><span>${t('v2.unserved')}</span> ${unserved}</div>`:''}
      <div class="m" style="margin-top:6px">${t('v2.note')}</div></div>`; }
  // ---- 甘特图：泵视角（行程 / 架设 / 抽排）与车次视角（去 / 回）；班组表
  let GANTT_VIEW = 'units';
  function ganttSvg(d){ const v=d.v2; if(!v) return ''; const EN=lang==='en'; const t0=+d.decision_time_min||0; const plan=(d.plan||[]).slice();
    let rows=[]; let tmax=t0+60;
    if(GANTT_VIEW==='units'){ const byU={}; plan.forEach(p=>{ (byU[p.unit_id]=byU[p.unit_id]||[]).push(p); }); rows=Object.entries(byU).map(([u,ps])=>({label:u,sub:String(ps[0].model||'').replace('diesel-',''),pri:Math.max(...ps.map(p=>+p.priority||0)),segs:ps.map(p=>{ const dep=+p.depart_min, arr=+p.arrive_min, pf=+p.pumping_from_min, done=p.done_min==null?null:+p.done_min; return {dep,arr,pf,done,fp:p.fp_id,rel:p.released_min}; })})).sort((a,b)=>b.pri-a.pri).slice(0,40); rows.forEach(r=>r.segs.forEach(sg=>{ tmax=Math.max(tmax, sg.done||sg.pf+120); })); }
    else { const trips=((v.schedule||{}).trips)||[]; const byV={}; trips.forEach(tp=>{ (byV[tp.vehicle_id]=byV[tp.vehicle_id]||[]).push(tp); }); rows=Object.entries(byV).map(([vid,tps])=>({label:vid,sub:(tps[0].vehicle_class==='heavy'?t('v2.heavy'):t('v2.light'))+(tps[0].solver==='cpsat'?' · CP-SAT':''),pri:tps.length,segs:tps.map(tp=>({dep:+tp.depart_min,arr:+tp.arrive_min,pf:+tp.arrive_min,done:+tp.back_min,fp:tp.fp_id+' ('+(tp.units||[]).length+')'}))})).sort((a,b)=>b.pri-a.pri).slice(0,40); rows.forEach(r=>r.segs.forEach(sg=>{ tmax=Math.max(tmax, sg.done); })); }
    if(!rows.length) return `<div class="m">${t('v2.ganttEmpty')}</div>`;
    tmax=Math.min(Math.max(tmax, t0+120), t0+2880); const W=460, L=78, R=8, rowH=13, top=16; const H=top+rows.length*rowH+6; const X=m=>L+(W-L-R)*Math.max(0,Math.min(1,(m-t0)/(tmax-t0)));
    const ticks=[]; const span=tmax-t0; const step=span<=360?60:(span<=1440?180:360); for(let m=t0;m<=tmax+1;m+=step) ticks.push(m);
    const grid=ticks.map(m=>`<line class="grid" x1="${X(m).toFixed(1)}" y1="${top-4}" x2="${X(m).toFixed(1)}" y2="${H-4}"/><text x="${X(m).toFixed(1)}" y="${top-6}" text-anchor="middle">${(m/60).toFixed(0)}h</text>`).join('');
    const body=rows.map((r,i)=>{ const y=top+i*rowH; const segs=r.segs.map(sg=>{ const x1=X(sg.dep), x2=X(sg.arr), x3=X(sg.pf), x4=X(sg.done==null?tmax:sg.done); const never=sg.done==null;
        return `<rect x="${x1.toFixed(1)}" y="${y+2}" width="${Math.max(1,x2-x1).toFixed(1)}" height="${rowH-5}" fill="#cbd5e1"/><rect x="${x2.toFixed(1)}" y="${y+2}" width="${Math.max(1,x3-x2).toFixed(1)}" height="${rowH-5}" fill="#94a3b8"/><rect x="${x3.toFixed(1)}" y="${y+2}" width="${Math.max(1,x4-x3).toFixed(1)}" height="${rowH-5}" fill="${never?'#d1d5db':'var(--acc)'}" opacity="${never?0.7:1}"><title>${esc(r.label)} → ${esc(String(sg.fp))}: ${t('v2.gDep')} ${hrs(sg.dep)} · ${t('v2.gArr')} ${hrs(sg.arr)} · ${t('v2.gPump')} ${hrs(sg.pf)} · ${never?t('cd.never'):(t('v2.gDone')+' '+hrs(sg.done))}</title></rect>${sg.rel!=null?`<line x1="${X(sg.rel).toFixed(1)}" y1="${y+1}" x2="${X(sg.rel).toFixed(1)}" y2="${y+rowH-2}" stroke="#7c3aed" stroke-width="1.5"/>`:''}`; }).join('');
        return `<text class="lbl" x="2" y="${y+rowH-4}">${esc(String(r.label).slice(0,11))}</text><text x="${L-4}" y="${y+rowH-4}" text-anchor="end" style="font-size:8px">${esc(String(r.sub||'').slice(0,8))}</text>${segs}`; }).join('');
    return `<svg class="gantt" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${grid}<line class="now" x1="${X(t0)}" y1="${top-4}" x2="${X(t0)}" y2="${H-4}"/>${body}</svg>
      <div class="legend2"><span><i style="background:#cbd5e1"></i>${t('v2.gTravel')}</span><span><i style="background:#94a3b8"></i>${t('v2.gSetup')}</span><span><i style="background:var(--acc)"></i>${t('v2.gPump')}</span><span><i style="background:#d1d5db"></i>${t('cd.never')}</span>${GANTT_VIEW==='units'?`<span><i style="background:#7c3aed"></i>${t('v2.gReleased')}</span>`:''}</div>`; }
  function ganttBlock(d){ const v=d.v2; if(!v) return ''; const sch=v.schedule||{}; const dep=Object.values(sch.depots||{});
    const crews=dep.length?`<details class="sec"><summary>${t('v2.crewsTitle')} <small>${t('v2.crewsSub')}</small></summary><div class="body"><table><tr><th>${t('v2.depot')}</th><th>${t('v2.needP')}</th><th>${t('v2.onDuty')}</th><th>${t('v2.callable')}</th><th>${t('v2.reliefP')}</th><th>${t('v2.trips')}</th></tr>${dep.sort((a,b)=>b.persons_needed-a.persons_needed).slice(0,10).map(x=>`<tr><td title="${esc(x.name||'')}">${esc(String(x.name||x.depot_id).split('·')[0].slice(0,22))}</td><td>${x.persons_needed}${x.persons_shortage?` <b style="color:var(--warn)">−${x.persons_shortage}</b>`:''}</td><td>${x.persons_on_duty}</td><td>${x.persons_from_callable}/${x.persons_callable}</td><td>${x.relief_persons_at_shift_change}</td><td>${x.trips}/${Object.values(x.trip_capacity||{}).reduce((a,b)=>a+b,0)}</td></tr>`).join('')}</table></div></details>`:'';
    return `<div class="card" style="margin:8px 0;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#fff"><h2 style="margin:0 0 4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">${t('v2.gantt')} <span class="seg mini" id="gview"><button data-g="units" class="${GANTT_VIEW==='units'?'on':''}">${t('v2.gUnits')}</button><button data-g="trips" class="${GANTT_VIEW==='trips'?'on':''}">${t('v2.gTrips')}</button></span><small class="m">${t('v2.ganttSub')}</small></h2><div id="ganttbox">${ganttSvg(d)}</div>${crews}
      <div class="ctrl" style="margin-top:6px"><button class="btn" id="stressbtn" style="margin:0">${t('v2.stressBtn')}</button><span class="m" id="stressout">${t('v2.stressHint')}</span></div></div>`; }
  function bindGantt(){ const seg=document.getElementById('gview'); if(seg){ seg.querySelectorAll('button').forEach(b=>b.onclick=()=>{ GANTT_VIEW=b.dataset.g; seg.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b)); const box=document.getElementById('ganttbox'); if(box&&DP) box.innerHTML=ganttSvg(DP); }); }
    const sb=document.getElementById('stressbtn'); if(sb){ sb.onclick=async()=>{ sb.disabled=true; const out=document.getElementById('stressout'); out.textContent=t('computing'); const r=await apiJob(`/api/stress?rp=${encodeURIComponent(RP)}&objective=${V2OBJ||'balanced'}`, tickText(out, t('computing'))); sb.disabled=false; if(!r||r.error){ out.textContent=(r&&r.error)||t('liveOff'); return; }
      const EN=lang==='en'; out.innerHTML=`<b>${esc(EN?(r.worse_label_en||r.worse_label):r.worse_label)}</b>（${fmt(r.worse_p24_mm,1)} mm）：${t('v2.stressRes',{n:r.points_in_plan,a:r.within_target_now??'—',b:r.within_target_worse,ne:r.never_empties_worse,g:r.points_with_gap,x:r.extra_12in_equiv_total})} ${(r.worst||[]).filter(w=>w.rate_gap_m3h>0).slice(0,5).map(w=>`<span class="chip click" data-fp="${esc(w.fp_id)}">${esc(w.fp_id)} +${w.extra_12in_equiv}</span>`).join(' ')}`;
      out.querAll=null; out.querySelectorAll('.chip[data-fp]').forEach(c=>c.onclick=()=>showHotspot(c.dataset.fp)); }; } }
  function bindV2(){ bindGantt(); const seg=document.getElementById('v2obj'); if(!seg) return; const tq0=()=>{ const tq=document.getElementById('dpt'); return tq?tq.value:((DP&&DP.decision_time_min)||210); }; const rob0=()=>(DP&&DP.robust?1:0);
    seg.querySelectorAll('button').forEach(b=>b.onclick=async()=>{ if(b.classList.contains('on')) return; seg.querySelectorAll('button').forEach(x=>x.disabled=true); const lbl=b.textContent; const r=await apiJob(`/api/dispatch?t=${tq0()}&objective=${b.dataset.o}&robust=${rob0()}`, tickText(b, t('computing'))); if(r&&!r.error){ DP=r; V2OBJ=b.dataset.o; await ensureDispatchLayers(); render(); } else { b.textContent=lbl; seg.querySelectorAll('button').forEach(x=>x.disabled=false); } });
    const rob=document.getElementById('v2rob'); if(rob){ rob.querySelectorAll('button').forEach(b=>b.onclick=async()=>{ if(b.classList.contains('on')) return; rob.querySelectorAll('button').forEach(x=>x.disabled=true); b.textContent=t('computing'); const r=await apiJob(`/api/dispatch?t=${tq0()}&objective=${V2OBJ||'balanced'}&robust=${b.dataset.r}`, tickText(b, t('computing'))); if(r&&!r.error){ DP=r; await ensureDispatchLayers(); render(); } else { rob.querySelectorAll('button').forEach(x=>x.disabled=false); b.textContent=t(b.dataset.r==='1'?'v2.robust':'v2.single'); } }); }
    const tb=document.getElementById('trfbtn'); if(tb){ tb.onclick=async()=>{ tb.disabled=true; const out=document.getElementById('trfout'); out.textContent=t('computing'); const r=await api(`/api/traffic?refresh=1&rp=${encodeURIComponent(RP)}&t=${tq0()}`); if(!r||r.error||(r.meta&&r.meta.error)){ out.textContent=(r&&(r.error||(r.meta||{}).error))||t('liveOff'); tb.disabled=false; return; } const d2=await apiJob(`/api/dispatch?t=${tq0()}&objective=${V2OBJ||'balanced'}&robust=${rob0()}`, tickText(out, t('computing'))); if(d2&&!d2.error){ DP=d2; await ensureDispatchLayers(); render(); } else { out.textContent=(d2&&d2.error)||t('liveOff'); tb.disabled=false; } }; }
    const rf=document.getElementById('trfref'); if(rf){ rf.onclick=async()=>{ rf.disabled=true; const out=document.getElementById('trfout'); out.textContent=t('computing'); const r=await api(`/api/traffic?rp=${encodeURIComponent(RP)}&t=${tq0()}`); rf.disabled=false; const x=r&&r.refine; if(!x||!x.refined){ out.textContent=(x&&F(x,'note'))||(r&&r.error)||t('liveOff'); return; }
      out.innerHTML=t('v2.trfRefine',{n:x.n_pairs,m:fmt(x.delta_mean_min,1),s:x.n_slower_10min})+' '+(x.worst||[]).filter(w=>w.delta_min>0).slice(0,5).map(w=>`<span class="chip click" data-fp="${esc(w.fp_id)}">${esc(w.from)}→${esc(w.fp_id)} +${fmt(w.delta_min,0)} min</span>`).join(' '); out.querySelectorAll('.chip[data-fp]').forEach(c=>c.onclick=()=>showHotspot(c.dataset.fp)); }; } }
  async function renderLine3(){
    const d=await getDispatch(); DP=d;
    const ks=d.kpis.solver, kg=d.kpis.greedy, rs=d.replay_summary||null;
    const triAll=Object.entries(d.triage||{}).sort((a,b)=>b[1]-a[1]); const triN=triAll.reduce((s,[,v])=>s+v,0)||1;
    const plan=(d.plan||[]).slice().sort((a,b)=>a.wave-b.wave||b.priority-a.priority); const shownPlan=slice('plan',plan,12);
    const HS=d.hotspot_stats||null;
    panel.innerHTML=`
      ${sourceBadge(d)}
      <div class="ctrl"><label>${t('dp.decide')}</label><input type="range" id="dpt" min="30" max="${Math.max(600, Math.ceil(((d.scenario&&d.scenario.duration_min)||600)/30)*30)}" step="30" value="${d.decision_time_min}"><b id="dptv" style="min-width:64px;text-align:right">${d.decision_time_min} ${t('dp.min')}</b><button class="btn primary" id="dpgo" style="margin:0">${t('dp.recompute')}</button></div>
      <div class="kpis four">
        <div class="kpi l3"><b>${ks.points_served} / ${ks.points_total}</b><span>${t('dp.served')}</span></div>
        <div class="kpi l3"><b>${ks.critical_within_target} / ${ks.critical_total}</b><span>${t('dp.crit')}</span></div>
        <div class="kpi l3"><b>${rs?('−'+(rs.served_saved_pct??0)+'%'):'—'}</b><span>${t('l3.kFlood')}</span><small>${rs?`${fmt(rs.served_flood_h_base,0)} → ${fmt(rs.served_flood_h_pump,0)} h`:''}</small></div>
        <div class="kpi l3"><b>${ks.first_arrival_median_min??'—'} ${t('dp.min')}</b><span>${t('dp.firstArr')}</span><small>${t('dp.units')} ${ks.units_used}${ks.tankers_used?` · ${t('dp.tankers')} ${ks.tankers_used}（${ks.points_by_tanker} ${t('dp.pts')}）`:''}</small></div>
      </div>
      ${v2Block(d)}
      ${ganttBlock(d)}
      ${rs?`<div class="m" style="margin:6px 0">${t('rpl.summaryAll',{m:rs.all_points,c:fmt(rs.all_flood_h_base,0),d:fmt(rs.all_flood_h_pump,0),q:rs.all_saved_pct??0,tb:rs.served_t_empty_median_base_min??'—',tp:rs.served_t_empty_median_pump_min??'—'})}${rs.system_empty_base_min!=null?` ${t('rpl.systemEmpty',{eb:hrs(rs.system_empty_base_min),ep:hrs(rs.system_empty_pump_min),mb:hrs(rs.all_t_empty_median_base_min),mp:hrs(rs.all_t_empty_median_pump_min),nb:rs.never_empties_base??0,np:rs.never_empties_pump??0})}`:''}${d.cost&&d.cost.units?` ${t('l3.cost',{c:aed(d.cost.total_aed),h:fmt(d.cost.pump_hours)})}`:''}</div>`:''}
      ${d.timeline&&d.timeline.t_min&&d.timeline.t_min.length?`<div class="tl" id="tl"><h2 style="margin-bottom:0">${t('l3.tl')} <small>${t('l3.tlSub')}</small></h2>
        <div class="ctrl"><button class="btn" id="tlplay" title="${t('l3.tlPlay')}" style="margin:0">▶</button><input type="range" id="tlr" min="0" max="${d.timeline.t_min.length-1}" step="1" value="0"><b id="tlt" style="min-width:52px;text-align:right">0 h</b>
          <span class="seg mini"><button data-m="base" class="on">${t('rpl.noPump')}</button><button data-m="pump">${t('rpl.withPump')}</button></span><button class="btn" id="tlreset" style="margin:0">${t('l3.tlReset')}</button></div>
        <div id="tlsvg"></div><div class="m" id="tlsum"></div><div class="m">${t('l3.tlNote')}</div></div>`:''}
      <h2>${t('dp.triage')} <small>${d.points.features.length} ${t('dp.pts')}</small></h2>
      ${triAll.map(([k,v])=>`<div class="hbar"><div><div style="display:flex;justify-content:space-between"><span>${esc(lang==='en'&&d.triage_en&&d.triage_en[k]?d.triage_en[k]:k)}</span></div><div class="track"><i style="width:${Math.round(v/triN*100)}%;background:${triColor(k)}"></i></div></div><div class="n">${v}</div></div>`).join('')}
      ${d.levels?`<div class="m" style="margin:8px 0">${t('dp.levels',{iii:d.levels.points.III||0,ii:d.levels.points.II||0,i:d.levels.points.I||0,ev:d.levels.event||'—'})} · ${t('dp.trtNote',{t:d.levels.trt_min,m:d.levels.mobilization_min})}</div>`:''}
      <h2>${t('dp.plan')} <small>${plan.length} · ${t('l3.planSub')}</small></h2>
      <table><tr><th>${t('dp.wave')}</th><th>${t('dp.unit')}</th><th>${t('dp.to')}</th><th>${t('dp.eta')}</th><th>${t('dp.done')}</th><th>${t('dp.target')}</th></tr>
        ${shownPlan.map(p=>`<tr class="click" data-fp="${esc(p.fp_id)}"><td>${p.wave}</td><td>${esc(p.unit_id)}<div class="m">${esc(String(p.model).replace('diesel-',''))} · ${esc(p.from)}</div></td><td>${esc(p.fp_id)}<div class="m">${esc(p.top_kind)}${p.level?' · '+p.level:''}</div></td><td>${p.arrive_min}<div class="m">${fmt(p.site_rate_m3h)} m³/h</div></td><td>${p.done_min??'—'}${p.replay_saved_min!=null&&p.replay_saved_min>0?`<div class="m" style="color:var(--ok)">−${hrs(p.replay_saved_min)}</div>`:''}</td><td>${p.within_target?'✓':'✗'}</td></tr>`).join('')}</table>${moreBtn('plan',12,plan.length)}
      <details class="sec"><summary>${t('l3.algo')} <small>${t('l3.algoSub')}</small></summary><div class="body">
        <table><tr><th></th><th>${t('dp.solverKpi')}</th><th>${t('dp.greedyKpi')}</th></tr>
        <tr><td>${t('dp.wfh')}</td><td><b>${fmt(ks.weighted_flood_hours,0)}</b></td><td class="m">${fmt(kg.weighted_flood_hours,0)}</td></tr>
        <tr><td>${t('dp.served')}</td><td><b>${ks.points_served}</b></td><td class="m">${kg.points_served}</td></tr>
        <tr><td>${t('dp.crit')}</td><td><b>${ks.critical_within_target}</b></td><td class="m">${kg.critical_within_target}</td></tr>
        <tr><td>${t('dp.trt',{t:ks.trt_min||120})}</td><td><b>${ks.within_trt??'—'}</b></td><td class="m">${kg.within_trt??'—'}</td></tr>
        <tr><td>${t('dp.units')}</td><td><b>${ks.units_used}</b></td><td class="m">${kg.units_used}</td></tr></table>
        <div class="m">${(d.waves||[]).map(w=>`${t('dp.wave')}${w.wave} ${w.status} ${w.ms??''}${t('dp.ms')}`).join(' · ')}</div></div></details>
      ${HS?`<details class="sec"><summary>${t('hs.title')} <small>${HS.n} · ${Object.entries(HS.by_region||{}).map(([k,v])=>k+' '+v).join(' / ')}</small></summary><div class="body">
      <table><tr><td>${t('hs.byCrit')}</td><td>${Object.entries(HS.by_criticality||{}).map(([k,v])=>`${lang==='en'?k:critZh(k)} ${v}`).join(' · ')}</td></tr>
      <tr><td>${t('hs.idd')}</td><td>${HS.idd_planned} ${t('hs.iddPlanned')}${HS.idd_assessing?` · ${HS.idd_assessing} ${t('hs.assessing')}`:''}</td></tr>
      <tr><td>${t('hs.planA')}</td><td>${t('hs.pumps')} ${HS.planA.pumps}（12" ${HS.planA.pump12} / 8" ${HS.planA.pump8} / 6" ${HS.planA.pump6}）· ${t('hs.tankers')} ${HS.planA.tankers} · ${t('hs.manpower')} ${HS.planA.manpower}</td></tr>
      ${HS.adm_vs_network?`<tr><td>${t('hs.vsNet')}</td><td>${t('hs.vsNetText',{a:HS.adm_vs_network.within_500m_of_terminal,n:HS.adm_vs_network.n,b:HS.adm_vs_network.client_no_network_but_terminal_within_500m,c:HS.adm_vs_network.client_no_network})}</td></tr>`:''}
      ${HS.gwm_vs_hotspots?`<tr><td>${t('hs.vsGwm')}</td><td><b>${Math.round(HS.gwm_vs_hotspots.pod_015*100)}%</b> ${t('hs.vsGwmText',{h:HS.gwm_vs_hotspots.hit_015,n:HS.gwm_vs_hotspots.inside_grid,m:HS.gwm_vs_hotspots.median_depth_m,h3:HS.gwm_vs_hotspots.hit_030})}</td></tr>`:''}
      ${HS.hotspots_covered_by_flood_points!=null?`<tr><td>${t('hs.covered')}</td><td>${HS.hotspots_covered_by_flood_points} / ${(HS.adm_vs_network||{}).n||HS.n}</td></tr>`:''}</table></div></details>`:''}
      <div class="m" style="margin-top:8px">${t('l3.hint')}</div>`;
    const sl=document.getElementById('dpt'), sv=document.getElementById('dptv'); sl.oninput=()=>sv.textContent=sl.value+' '+t('dp.min'); bindV2();
    const btn=document.getElementById('dpgo');
    btn.onclick=async()=>{ btn.disabled=true; btn.textContent=t('computing'); const r=await apiJob(`/api/dispatch?t=${sl.value}&objective=${V2OBJ||'balanced'}&robust=${DP&&DP.robust?1:0}`, tickText(btn, t('computing'))); if(r&&!r.error){ DP=r; await ensureDispatchLayers(); render(); } else { btn.disabled=false; btn.textContent=t('dp.recompute'); sv.textContent=(lang==='en'?'service offline':'服务离线'); } };
    panel.querySelectorAll('tr.click[data-fp]').forEach(tr=>tr.onclick=()=>{ const f=d.points.features.find(x=>x.properties.fp_id===tr.dataset.fp); if(f){ const c=f.geometry.type==='Point'?f.geometry.coordinates:f.properties&&[f.properties.lon,f.properties.lat]; map.flyTo([c[1],c[0]],15); if(layers.flood) layers.flood.eachLayer(l=>{ if(l.feature&&l.feature.properties.fp_id===tr.dataset.fp) setTimeout(()=>l.openPopup(),400); }); } });
    setupTimeline(d);
  }

  // ---------------- 数据与可信度 ----------------
  // 本体模型页：以积水点为核心的关系图（SVG，无依赖）+ 选中对象的属性 / 关系 / 规则 / 动作；数据来自 /api/ontology
  let ONTSEL='FloodPoint';
  // 资产图鉴（swbrain/asset_kb.py）：每种资产的定义 / 功能 / 分级 / 建模口径 / 图——多模态本体（2026-09-28）
  let ONTKIND=U.get('kind')||null;
  function ontGallery(O){
    const EN=lang==='en', K=O.asset_kinds||[]; if(!K.length) return '';
    const lvOf=k=>String(k.level_user||k.level_a||k.level_b||'').slice(0,2)||'—';
    const groups=[['L1',t('ont.gL1')],['L2',t('ont.gL2')],['L3',t('ont.gL3')],['—',t('ont.gOther')]];
    const tile=k=>{ const img=k.schematic||k.photo; const on=k.kind===ONTKIND;
      return `<button class="kgt${on?' on':''}" data-kind="${esc(k.kind)}" title="${esc(EN?k.def_en:k.def_zh)}">${img?`<img src="${esc(img)}" alt="" loading="lazy">`:`<span class="kgt-no">${esc((EN?k.en:k.zh).slice(0,2))}</span>`}`
        +`<b>${esc(EN?k.en:k.zh)}</b><span class="m">${k.functions.map(f=>esc(EN?f.en:f.zh)).join(' · ')}</span>${k.schematic?`<span class="kgt-tag">${t('ont.hasSchem')}</span>`:''}</button>`; };
    let h=`<div class="ont-sec"><h3>${t('ont.gallery')} <span class="m">${t('ont.gallerySub')}</span></h3>`;
    for(const [lv,lab] of groups){ const ks=K.filter(k=>lvOf(k)===lv); if(!ks.length) continue; h+=`<div class="m" style="margin:6px 0 3px"><b>${esc(lv)}</b> ${esc(lab)}</div><div class="kg">${ks.map(tile).join('')}</div>`; }
    const k=K.find(x=>x.kind===ONTKIND);
    if(k){ const lv=[k.level_user?`${t('ont.lvUser')}：${k.level_user}`:'', k.level_a?`${t('ont.lvA')}：${k.level_a}`:'', k.level_b?`${t('ont.lvB')}：${k.level_b}`:''].filter(Boolean);
      h+=`<div class="kgd" id="kgd"><div class="kgd-h"><b>${esc(EN?k.en:k.zh)}</b> <span class="m">${esc(k.kind)}${k.covered_by?` · ${t('ont.coveredBy')} ${esc(k.covered_by)}`:''}</span></div>`
        +(k.schematic?`<img class="kgd-svg" src="${esc(k.schematic)}" alt="${esc(EN?k.en:k.zh)}">`:'')+(k.photo&&!k.schematic?`<img class="kgd-ph" src="${esc(k.photo)}" alt="${esc(EN?k.en:k.zh)}"><div class="m">${t('ont.photoSrc')}：${esc(k.photo_src||'')}</div>`:'')
        +`<p>${esc(EN?k.def_en:k.def_zh)}</p>`
        +`<table class="ont-prop"><tbody><tr><td>${t('ont.kFn')}</td><td>${k.functions.map(f=>`<span class="chip">${esc(EN?f.en:f.zh)}</span>`).join(' ')}</td></tr>`
        +`<tr><td>${t('ont.kLevel')}</td><td>${lv.map(esc).join('<br>')||'—'}</td></tr><tr><td>${t('ont.kDelivery')}</td><td>${k.delivery.map(esc).join(' / ')}</td></tr>`
        +`<tr><td>${t('ont.kModel')}</td><td>${esc(k.model_zh)}</td></tr><tr><td>${t('ont.kParams')}</td><td>${k.params.map(esc).join('、')}</td></tr>`
        +(k.refs&&k.refs.length?`<tr><td>${t('ont.source')}</td><td>${k.refs.map(esc).join('；')}</td></tr>`:'')+`</tbody></table>`
        +(k.photo&&k.schematic?`<div class="m" style="margin-top:6px">${t('ont.photoSrc')}：${esc(k.photo_src||'')}</div><img class="kgd-ph" src="${esc(k.photo)}" alt="">`:'')
        +`<div style="margin-top:6px"><span class="chip ont-ask" data-ask="${esc(EN?`What is a ${k.en}?`:`${k.zh}是什么？`)}">${t('ont.askKind')}</span></div></div>`; }
    return h+'</div>';
  }
  // 本体关系图的节点位置：用户拖过就记住（换语言、点别的对象、刷新都不丢）
  const ONT_POS_KEY='swb_ont_pos';
  const ontPos=()=>{ try{ return JSON.parse(localStorage.getItem(ONT_POS_KEY)||'{}')||{}; }catch(e){ return {}; } };
  const ontPosSave=(o)=>{ try{ localStorage.setItem(ONT_POS_KEY, JSON.stringify(o)); }catch(e){} };
  const ONT_TYPE_OF_PARAM={fp_id:'FloodPoint',scenario:'Scenario',zone:'Zone',pid:'Proposal',rule:'Rule'};
  const ONT_EXTRA_ACTS={Alert:['issue_alert','explain_rule','rain_to_scenario'],Observation:['record_observation'],GwmCore:['world_model_new_points','compare_world_model'],Assignment:['dispatch_plan','recession_options'],PumpUnit:['dispatch_plan','preposition_plan','recession_options'],Depot:['preposition_plan','dispatch_plan'],Reach:['upsize_whatif','trace_downstream'],FloodState:['list_points','count_points','recession_batch','cost_summary'],Scenario:['set_scenario','rain_to_scenario','generate_report','count_points']};
  // 关系图交互：拖节点、拖空白平移、滚轮缩放、复位。位置存 localStorage，选别的对象重渲染后仍在。
  function ontBindGraph(pos){
    const svg=panel.querySelector('svg.ont-graph'); if(!svg) return;
    const vp=svg.querySelector('.ont-vp'); const view={x:0,y:0,k:1};
    const apply=()=>vp.setAttribute('transform',`translate(${view.x},${view.y}) scale(${view.k})`);
    const outer=(ev)=>{ const m=svg.getScreenCTM(); if(!m) return [0,0]; const q=new DOMPoint(ev.clientX,ev.clientY).matrixTransform(m.inverse()); return [q.x,q.y]; };
    const local=(ev)=>{ const [ox,oy]=outer(ev); return [(ox-view.x)/view.k,(oy-view.y)/view.k]; };
    const edgeEls=[...svg.querySelectorAll('.ont-edge')];
    const refresh=(api)=>{ edgeEls.forEach(g=>{ const a=g.dataset.s,b=g.dataset.d; if(api&&a!==api&&b!==api) return; const p=pos[a],q=pos[b]; if(!p||!q) return;
        const dx=q[0]-p[0],dy=q[1]-p[1],len=Math.hypot(dx,dy)||1, ex=q[0]-dx/len*44, ey=q[1]-dy/len*18;
        const ln=g.querySelector('line'); ln.setAttribute('x1',p[0]); ln.setAttribute('y1',p[1]); ln.setAttribute('x2',ex); ln.setAttribute('y2',ey);
        const tx=g.querySelector('text'); if(tx){ tx.setAttribute('x',p[0]+dx*0.38); tx.setAttribute('y',p[1]+dy*0.38-3); } }); };
    let drag=null;
    svg.addEventListener('pointerdown', ev=>{
      const g=ev.target.closest && ev.target.closest('.ont-node');
      if(g){ const [x,y]=local(ev); const p=pos[g.dataset.ont]||[0,0]; drag={api:g.dataset.ont,g,ox:x-p[0],oy:y-p[1],sx:ev.clientX,sy:ev.clientY}; }
      else { drag={pan:true,sx:ev.clientX,sy:ev.clientY,vx:view.x,vy:view.y}; svg.classList.add('grabbing'); }
      try{ svg.setPointerCapture(ev.pointerId); }catch(e){}
    });
    svg.addEventListener('pointermove', ev=>{ if(!drag) return;
      if(drag.pan){ const m=svg.getScreenCTM(); const s=m?(svg.viewBox.baseVal.width/svg.getBoundingClientRect().width):1;
        view.x=drag.vx+(ev.clientX-drag.sx)*s; view.y=drag.vy+(ev.clientY-drag.sy)*s; apply(); return; }
      const [x,y]=local(ev); pos[drag.api]=[x-drag.ox,y-drag.oy];
      drag.g.setAttribute('transform',`translate(${pos[drag.api][0]},${pos[drag.api][1]})`); refresh(drag.api); });
    const end=(ev)=>{ if(!drag) return; const d=drag; drag=null; svg.classList.remove('grabbing'); try{ svg.releasePointerCapture(ev.pointerId); }catch(e){}
      if(d.pan) return;
      if(Math.hypot(ev.clientX-d.sx,ev.clientY-d.sy)<4){ ONTSEL=d.api; renderOntology(); return; }   // 位移不到 4 px = 当成点击选中（按像素判，不按事件个数，慢拖和抖动都不会判错）
      const saved=ontPos(); saved[d.api]=pos[d.api]; ontPosSave(saved); };
    svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
    svg.addEventListener('wheel', ev=>{ ev.preventDefault(); const [ox,oy]=outer(ev); const lx=(ox-view.x)/view.k, ly=(oy-view.y)/view.k;
      view.k=Math.min(3,Math.max(0.4,view.k*(ev.deltaY<0?1.12:1/1.12))); view.x=ox-view.k*lx; view.y=oy-view.k*ly; apply(); }, {passive:false});
    const rb=document.getElementById('ontreset'); if(rb) rb.onclick=()=>{ ontPosSave({}); renderOntology(); };
  }
  // ---------------- 防线2+ 四态对比 ----------------
  // 客户领导要看的那张：同一个汇水区、同一场雨，三个现状并排。
  // 每个现状都是真跑一遍 SWMM 得到的，差别只在有没有塘、塘在哪。
  let TL3=null, tl3Catch=null, tl3State='L1', tl3Layer=null;
  // ③ 拆成两种构筑物：地下箱涵（青）与地面开敞塘（绿）。两者造价量级不同，不能共用一个颜色
  const TL3COL={L1:'#b91c1c', L2:'#b45309', L2B:'#0e7490', L2L:'#15803d', L2P:'#64748b'};

  // ---- 基础设施底图：既有管网本身。三个现状共用同一套，按汇水区加载一次。
  // 以前图上只有积水点，孤零零一片圆点看着就像管网节点；把管线铺在底下，才看得出哪淹在哪根管上。
  let TL3NET=null, tl3NetLayer=null, tl3NetCatch=null, tl3PondRings=null;
  // 井和雨水口有几千个，默认关掉：全点开会盖住积水点，正是「看着像管线节点」的来源
  const TL3NETON={conduit:true, manhole:false, inlet:false, outfall:true, pump:true, wetwell:false};
  const NETSTYLE={conduit:{color:'#475569', weight:1.6, opacity:.85},
                  manhole:{color:'#64748b', fill:'#e2e8f0', r:2.2},
                  inlet:  {color:'#0284c7', fill:'#bae6fd', r:2.2},
                  outfall:{color:'#065f46', fill:'#34d399', r:5},
                  pump:   {color:'#9a3412', fill:'#fb923c', r:5},
                  wetwell:{color:'#7c2d12', fill:'#fdba74', r:3.4}};

  async function tl3NetLoad(){
    if(tl3Catch==='all'){ TL3NET=null; tl3NetCatch=null; return; }
    if(tl3NetCatch===tl3Catch) return;                       // 底图不随现状变，换区才重取
    TL3NET=await api('/api/three_lines_net?catchment='+encodeURIComponent(tl3Catch));
    tl3NetCatch=(TL3NET&&TL3NET.features)?tl3Catch:null;
  }

  function tl3NetDraw(){
    if(tl3NetLayer){ map.removeLayer(tl3NetLayer); tl3NetLayer=null; }
    if(!TL3NET||!(TL3NET.features||[]).length) return;
    // 管网画在积水点**下面**：它是底图，盖住结果就本末倒置了
    if(!map.getPane('tl3Net')){ map.createPane('tl3Net'); map.getPane('tl3Net').style.zIndex=395; }
    const rend=L.canvas({pane:'tl3Net'});
    tl3NetLayer=L.layerGroup();
    const attrs=TL3NET.has_attributes||{};
    TL3NET.features.forEach(ft=>{
      const p=ft.properties||{}, k=p.layer;
      if(k==='pond') return;                                  // 塘由现状那边画（带库容、满档）
      if(!TL3NETON[k]) return;
      const s=NETSTYLE[k]; if(!s) return;
      const bits=[];
      if(p.label) bits.push('<b>'+esc(p.label)+'</b>');
      if(p.dia_mm) bits.push('DN'+fmt(p.dia_mm,0));
      if(p.length_m) bits.push(fmt(p.length_m,0)+' m');
      if(p.gnd_m!=null) bits.push(t('l2p.netGnd')+' '+fmt(p.gnd_m,2)+' m');
      if(p.inv_m!=null) bits.push(t('l2p.netInv')+' '+fmt(p.inv_m,2)+' m');
      if(p.pump_flow!=null) bits.push(fmt(p.pump_flow,0)+' L/s');
      if(p.ever_overflow) bits.push(t('l2p.netOF'));
      if(!bits.length) bits.push(esc(NETNAME(k))+(attrs[k]===false?' · '+t('l2p.netNoAttr'):''));
      let lay;
      if(ft.geometry.type==='LineString'){
        const w=p.dia_mm? Math.max(.8, Math.min(4, p.dia_mm/500)) : s.weight;
        lay=L.polyline(ft.geometry.coordinates.map(c=>[c[1],c[0]]),
                       {color:s.color, weight:w, opacity:s.opacity, renderer:rend});
      } else if(ft.geometry.type==='Point'){
        lay=L.circleMarker([ft.geometry.coordinates[1], ft.geometry.coordinates[0]],
                           {radius:s.r, color:s.color, weight:1, fillColor:s.fill,
                            fillOpacity:.9, renderer:rend});
      } else return;
      tl3NetLayer.addLayer(lay.bindTooltip(bits.join(' · ')));
    });
    tl3NetLayer.addTo(map);
  }

  function NETNAME(k){
    const l=((TL3NET&&TL3NET.layers)||[]).find(x=>x.key===k);
    return l? (lang==='en'?l.en:l.zh) : k;
  }

  function tl3NetCtrl(){
    const ls=((TL3NET&&TL3NET.layers)||[]).filter(l=>l.n>0 && l.key!=='pond');
    if(!ls.length) return '';
    const chips=ls.map(l=>`<span class="chip${TL3NETON[l.key]?' on':''}" data-net="${l.key}">`+
      `${esc(lang==='en'?l.en:l.zh)} <small>${fmt(l.n,0)}</small></span>`).join('');
    const warn=TL3NET.caveat?`<div class="m" style="color:#b45309;margin:-4px 0 6px">${esc(TL3NET.caveat)}</div>`:'';
    return `<div class="ctrl" style="flex-wrap:wrap"><label>${t('l2p.net')}</label>${chips}</div>${warn}`;
  }

  // 塘的圈只在远景用：放大到看得见塘的边界了，圈就缩成一个点，不挡着面
  function tl3PondZoom(){
    if(!tl3PondRings||!tl3PondRings.length) return;
    const wide=map.getZoom()<16;
    tl3PondRings.forEach(o=>{ try{ o.m.setRadius(wide?o.r:3); }catch(e){} });
  }
  map.on('zoomend', tl3PondZoom);

  // ---- 图例可收起（2026-09-28）：任何视图写了 #legend，都自动套一个标题栏 + 收起 / 展开按钮；状态记在本机
  (function legendCollapse(){
    const box = document.getElementById('legend'); if(!box) return;
    let collapsed = false; try{ collapsed = localStorage.getItem('sw_legend_collapsed') === '1'; }catch(e){}
    const wrap = () => {
      if(!box.firstElementChild || box.querySelector(':scope > .lghead')) return;
      obs.disconnect();
      const body = document.createElement('div'); body.className = 'lgbody';
      while(box.firstChild) body.appendChild(box.firstChild);
      const head = document.createElement('div'); head.className = 'lghead';
      head.innerHTML = `<span>${t('lg.title')}</span><button type="button" aria-expanded="${!collapsed}">${collapsed ? t('lg.expand') : t('lg.collapse')}</button>`;
      head.querySelector('button').onclick = ev => { ev.stopPropagation(); collapsed = !collapsed;
        try{ localStorage.setItem('sw_legend_collapsed', collapsed ? '1' : '0'); }catch(e){}
        box.classList.toggle('collapsed', collapsed); ev.target.textContent = collapsed ? t('lg.expand') : t('lg.collapse'); ev.target.setAttribute('aria-expanded', String(!collapsed)); };
      box.appendChild(head); box.appendChild(body); box.classList.toggle('collapsed', collapsed);
      obs.observe(box, {childList: true});
    };
    const obs = new MutationObserver(wrap);
    obs.observe(box, {childList: true}); wrap();
  })();

  function tl3Legend(st){
    const box=document.getElementById('legend'); if(!box) return;
    // renderLegend 在图层全关时会把图例设成 display:none，只写 innerHTML 是看不见的
    box.style.display='block';
    const np=(st&&st.ponds||[]).filter(x=>x.lon!=null).length;
    const pc=tl3State==='L2P'?'#db2777':'#7c3aed';
    const on=Object.keys(TL3NETON).filter(k=>TL3NETON[k] && ((TL3NET&&TL3NET.counts||{})[k]||0)>0);
    box.innerHTML=`<div class="m"><b>${esc((st&&st.zh)||'')}</b></div>
      <div class="row"><span class="sw" style="background:${TL3COL[tl3State]};opacity:.55"></span>${t('l2p.lgPts')}</div>
      ${np?`<div class="row"><span class="ring" style="border-color:${pc};border-width:3px"></span>${tl3State==='L2P'?t('l2p.lgPondS'):t('l2p.lgPondC')} ${np}</div>`
          :`<div class="row m">${t('l2p.lgNone')}</div>`}
      ${on.map(k=>k==='conduit'
          ?`<div class="row"><span class="ln" style="border-color:${NETSTYLE[k].color}"></span>${esc(NETNAME(k))}</div>`
          :`<div class="row"><span class="ring" style="border-color:${NETSTYLE[k].color};background:${NETSTYLE[k].fill};border-width:2px;width:11px;height:11px"></span>${esc(NETNAME(k))}</div>`).join('')}`;
  }

  function tl3Draw(){
    if(tl3Layer){ map.removeLayer(tl3Layer); tl3Layer=null; }
    if(!TL3 || !TL3.states) return;                    // 三区一起看时没有 states
    const st=TL3.states.find(x=>x.key===tl3State);
    if(!st||!st.available) return;
    // 塘要画在积水点**上面**：四百个积水点盖下来，白色空心圈会被整个埋住。
    // 单开一个 pane 把 z-index 拉高，比靠添加顺序稳。
    if(!map.getPane('tl3Pond')){ map.createPane('tl3Pond'); map.getPane('tl3Pond').style.zIndex=650; }
    tl3Layer=L.layerGroup();
    const pts=(st.points||[]).filter(p=>p.lon!=null);
    const vmax=Math.max(1, ...pts.map(p=>p.volume_m3||0));
    pts.forEach(p=>{
      const r=3+9*Math.sqrt((p.volume_m3||0)/vmax);
      tl3Layer.addLayer(L.circleMarker([p.lat,p.lon],{radius:r,color:TL3COL[tl3State],weight:1,
        fillColor:TL3COL[tl3State],fillOpacity:.45,renderer:canvas})
        .bindTooltip(`${esc(p.node)}${p.hotspot_id?' · '+esc(p.hotspot_id):''}<br>${fmt(p.volume_m3,0)} ${t('l2p.m3')} · ${fmt(p.hours,1)} ${t('l2p.h')}`));
    });
    const ponds=(st.ponds||[]).filter(x=>x.lon!=null||x.footprint);
    const pmax=Math.max(1, ...ponds.map(x=>x.max_m3||0));
    tl3PondRings=[];
    ponds.forEach(x=>{
      // 紫=客户方案的塘；本系统的塘按构筑物形式取色（与该态一致），一眼分得出是哪一种
      const col=x.origin==='system_plan'?(TL3COL[tl3State]||'#db2777'):'#7c3aed';
      const tip=[`<b>${esc(x.node)}</b>`,
        `${t('l2p.pondUse')} ${fmt(x.max_m3,0)} ${t('l2p.m3')} · ${t('l2p.lgFull')} ${fmt(x.max_pct_full,0)}%`]
        .concat(x.vol_m3?[`${t('l2p.pondCap')} ${fmt(x.vol_m3,0)} ${t('l2p.m3')}`]:[])
        .concat(x.land_use?[`${t('l2p.pondLand')} ${esc(x.land_use)}`]
               :(x.land==='open_land'?[`${t('l2p.pondLand')} ${t('l2p.pondOpen')}`]:[]))
        .concat(x.moved_m!=null?[`${t('l2p.pondMoved')} ${fmt(x.moved_m,0)} m`]:[])
        .concat(x.q_in_m3h?[`${t('l2p.pondIn')} ${fmt(x.q_in_m3h,0)} m³/h`]:[])
        // 标高与构筑物：塘埋在地下还是摆在地面、靠重力还是靠泵——这才看得出它是什么东西
        .concat(x.bottom_m!=null?[`${t('l2p.pondLvl')} ${fmt(x.bottom_m,2)} / ${fmt(x.top_m,2)}`
          +(x.ground_m?` m（${t('l2p.pondGnd')} ${fmt(x.ground_m,2)}）`:' m')]:[])
        .concat(x.inflow?[`${t('l2p.pondFeed')} ${x.inflow==='gravity'?t('l2p.feedG')
          :t('l2p.feedL')+(x.lift_m?` ${fmt(x.lift_m,1)} m`:'')}`
          +(x.n_pumps_in?` · ${x.n_pumps_in} ${t('l2p.pumps')}`:'')]:[])
        .concat(x.conveyance_m?[`${t('l2p.pondConv')} ${fmt(x.conveyance_m,0)} m · DN${x.dn_mm}`]:[])
        .join('<br>');
      // 塘是个面：放大之后要看得见它的边界，不能只给一个圆点
      if(x.footprint){
        tl3Layer.addLayer(L.geoJSON(x.footprint,{pane:'tl3Pond',
          style:{color:col,weight:2.5,fillColor:col,fillOpacity:.3}}).bindTooltip(tip));
      }
      if(x.lon==null) return;
      const r=x.footprint? 6+6*Math.sqrt((x.max_m3||0)/pmax)      // 有面时圈小一点
                         : 10+10*Math.sqrt((x.max_m3||0)/pmax);
      const m=L.circleMarker([x.lat,x.lon],{radius:r,color:col,weight:4,
        fillColor:'#fff',fillOpacity:x.footprint?.5:.9,pane:'tl3Pond'}).bindTooltip(tip);
      tl3Layer.addLayer(m);
      if(x.footprint) tl3PondRings.push({m:m, r:r});
    });
    tl3Layer.addTo(map);
    tl3NetDraw();
    tl3PondZoom();
    tl3Legend(st);
    // 定位要把塘也算进来：塘可能落在积水点包络之外
    const box=pts.map(p=>[p.lat,p.lon])
      .concat(ponds.filter(x=>x.lon!=null).map(x=>[x.lat,x.lon]));
    // 延后一帧再定位：面板刚写进 DOM 时地图容器尺寸还没稳，直接 fitBounds 会落在旧视野上
    // 链接里带了视野就照它来：塘只有几十米宽，得能把「放大到看得见塘的边界」这一眼分享出去
    const at=(U.get('at')||'').split(','), z=parseFloat(U.get('z'));
    const pinned=(at.length===2 && isFinite(+at[0]) && isFinite(+at[1]) && isFinite(z));
    if(pinned || box.length){ window._viewOwned=true;
      const fit=()=>{ try{ map.invalidateSize();
        if(pinned) map.setView([+at[0], +at[1]], z);
        else map.fitBounds(L.latLngBounds(box).pad(.15), {maxZoom:15}); }catch(e){} };
      setTimeout(fit,60); setTimeout(fit,700);   // 再补一次：底图图层是异步加载的，可能后落地
    }
  }

  function tl3Card(st){
    if(!st.available) return `<div class="kpi"><b>—</b><span>${esc(st.zh)}</span><small>${t('l2p.notRun')}</small></div>`;
    const k=st.kpi, d=st.delta_vs_L1;
    const pct=d?`<span style="color:${d.street_flood_pct<0?'#047857':'#b91c1c'}">${d.street_flood_pct>0?'+':''}${d.street_flood_pct}%</span>`:'';
    const bad=k.continuity_pct!=null&&Math.abs(k.continuity_pct)>10;
    return `<div class="card" style="border-left:4px solid ${TL3COL[st.key]};padding:8px 10px;margin:6px 0">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <b>${esc(st.zh)}</b>
        <button class="btn" data-tl3="${st.key}" style="margin:0;padding:2px 8px;font-size:11px">${t('l2p.show')}</button>
      </div>
      <div class="kpis three" style="margin:6px 0">
        <div class="kpi"><b>${fmt(k.street_flood_m3,0)} ${pct}</b><span>${t('l2p.kFlood')}</span><small>${t('l2p.m3')}</small></div>
        <div class="kpi"><b>${fmt(k.n_flooded_nodes,0)}</b><span>${t('l2p.kNodes')}</span><small>${d?(d.n_flooded_nodes>0?'+':'')+d.n_flooded_nodes:''}</small></div>
        <div class="kpi"><b>${k.n_hotspots_flooded}</b><span>${t('l2p.kHot')}</span><small>${d?(d.n_hotspots_flooded>0?'+':'')+d.n_hotspots_flooded:''}</small></div>
      </div>
      <div class="m">${t('l2p.kMed')} ${k.median_flood_h==null?t('l2p.none'):fmt(k.median_flood_h,2)+' '+t('l2p.h')}
        ${k.n_ponds?` · ${t('l2p.kPond')} ${k.n_ponds} · ${t('l2p.kUsed')} ${fmt(k.pond_used_m3,0)} ${t('l2p.m3')}`:` · ${t('l2p.noPond')}`}
        ${k.pond_spill_m3?` · ${t('l2p.kSpill')} ${fmt(k.pond_spill_m3,0)}`:''}
        · ${t('l2p.kErr')} <span style="color:${bad?'#b91c1c':'inherit'}">${k.continuity_pct==null?t('l2p.none'):k.continuity_pct.toFixed(2)+'%'}</span>
        ${bad?`<br><span style="color:#b91c1c">${t('l2p.errHigh')}</span>`:''}</div>
      ${(st.hotspots_cleared&&st.hotspots_cleared.length)?`<div class="m" style="color:#047857">${t('l2p.cleared')} ${st.hotspots_cleared.length}：${st.hotspots_cleared.slice(0,10).map(esc).join('、')}${st.hotspots_cleared.length>10?' …':''}</div>`:''}
      ${(st.hotspots_new&&st.hotspots_new.length)?`<div class="m" style="color:#b91c1c">${t('l2p.newly')} ${st.hotspots_new.length}：${st.hotspots_new.slice(0,6).map(esc).join('、')}</div>`:''}
      <div class="m">${esc(st.desc||'')}</div></div>`;
  }

  function tl3AllTable(A){
    // 列按接口返回的态来，不写死。态的数量会变（三态→四态），写死就会出现
    // 「还缺 L2P」和合计行的 undefined%——页面上已经踩过一次。
    const ks=((A.catchments||[])[0]?.states||[]).map(x=>x.key);
    const zh={}; ((A.catchments||[])[0]?.states||[]).forEach(x=>zh[x.key]=lang==='en'?(x.en||x.zh):x.zh);
    const num={L1:'①',L2:'②',L2B:'③',L2L:'④',L2P:'·'};
    const pc=v=>`<span style="color:${v<0?'#047857':'#b91c1c'}">${v>0?'+':''}${v}%</span>`;
    const rows=(A.catchments||[]).map(r=>{
      const st={}; (r.states||[]).forEach(x=>st[x.key]=x);
      const miss=ks.filter(k=>!st[k]||!st[k].available);
      if(miss.length) return `<tr><td>${esc(r.catchment)}</td><td colspan="${ks.length+2}" class="m">${t('l2p.missing')}：${miss.map(k=>esc(zh[k]||k)).join('、')}</td></tr>`;
      const cells=ks.map(k=>{
        const v=st[k].kpi.street_flood_m3, d=st[k].delta_vs_L1;
        const best=k!=='L1' && ks.filter(z=>z!=='L1').every(z=>st[k].kpi.street_flood_m3<=st[z].kpi.street_flood_m3);
        return `<td>${best?'<b>':''}${fmt(v,0)}${best?'</b>':''}${d?`<div class="m">${pc(d.street_flood_pct)}</div>`:''}</td>`;
      }).join('');
      const cl=ks.filter(k=>k!=='L1').flatMap(k=>st[k].hotspots_cleared||[]);
      const uniq=[...new Set(cl)];
      const last=ks[ks.length-1], mid=ks.includes('L2B')?'L2B':last;
      return `<tr><td><b>${esc(r.catchment)}</b>${uniq.length?`<div class="m" style="color:#047857">${t('l2p.cleared')} ${uniq.length}：${uniq.slice(0,6).map(esc).join('、')}</div>`:''}</td>
        ${cells}
        <td>${fmt(st.L1.kpi.n_flooded_nodes,0)} → ${fmt(st[mid].kpi.n_flooded_nodes,0)}</td>
        <td>${st.L1.kpi.n_hotspots_flooded} → ${st[mid].kpi.n_hotspots_flooded}</td></tr>`;
    }).join('');
    const w=A.total||{};
    const tot=w.L1?`<tr class="hl"><td><b>${t('l2p.total')}</b></td>
      ${ks.map(k=>`<td><b>${fmt(w[k],0)}</b>${k==='L1'?'':`<div class="m">${pc(w[k+'_pct'])}</div>`}</td>`).join('')}
      <td>${fmt(w.nodes_L1,0)} → ${fmt(w.nodes_L2P,0)}</td><td>${w.hot_L1} → ${w.hot_L2P}</td></tr>`:'';
    const note=w.L1?`<div class="m" style="margin-top:6px">${t('l2p.partialNote',{n:w.catchments})}${(w.partial&&w.partial.length)?t('l2p.partialSkip',{names:w.partial.join('、')}):'）'}<br>${t('l2p.pondUsed',{a:fmt(w.pond_L2,0),b:fmt(w.pond_L2P,0)})}</div>`:'';
    const head=`<th>${t('l2p.colCatch')}</th>`+ks.map(k=>`<th>${num[k]||''} ${esc(zh[k]||k)}</th>`).join('')
      +`<th>${t('l2p.colNodes')}</th><th>${t('l2p.colHot')}</th>`;
    const legend=ks.filter(k=>k!=='L1').map(k=>`<div class="m" style="margin-top:4px"><b>${num[k]||''} ${esc(zh[k]||k)}</b>　${esc((((A.catchments||[])[0]?.states||[]).find(x=>x.key===k)||{}).desc||'')}</div>`).join('');
    return `<div class="scroll"><table><tr>${head}</tr>${rows}${tot}</table></div>${note}${legend}`;
  }


  // ---------------- 一二维耦合的四态缺口（Mussafah_00 / R61D2） ----------------
  // **和上面那张表不是一套数**：上面读 out/three_lines/（纯一维，R61D1/R61D24），
  // 这里读 out/state_table_*（一二维耦合，R61D2，72 h + ANUGA 15 m 网格）。
  // 雨情、窗口、是否耦合都不同，数对不上是正常的，所以分块并各自标口径。
  let SG = null;
  function sgFmt(v, unit) {
    if (v === null || v === undefined) return '—';
    const d = (unit === 'h') ? 2 : (unit === 'm' ? 3 : (unit === 'ha' ? 2 : 0));
    return fmt(+v, d);
  }

  // ---------------- 二维水深播放 ----------------
  // 原件是 3 个 .sww 合计 4.8 GB、每个 129,791 顶点 × 270–570 个时刻，
  // 浏览器读不了，所以服务端先抽成逐时刻的小 PNG（三态合计 0.5 MB）。
  // 口径和耦合脚本对齐：形心水深、干湿阈值 0.02 m、面积按三角单元加权 ——
  // 图上的峰值和四态表里的数**必须一模一样**，差一点都会被当成两套数。
  let F2 = null, f2Layer = null, f2Timer = null;

  function f2Stop(){ if(f2Timer){ clearInterval(f2Timer); f2Timer = null; } }
  function f2Clear(){ f2Stop(); if(f2Layer){ map.removeLayer(f2Layer); f2Layer = null; } }

  // ================= Mussafah_00 四态工作台 =================
  // 用户反馈（2026-09-26）：「我都不能自由切换几个态」。原先四个态的数字是一排静态卡片，
  // 真能切态的只有下面二维播放那一小块，地图上的设施、缺口表都不跟着动，后面还接着一维三区的旧口径。
  // 这里改成**一个选中态驱动整页**：点哪个态，地图水深 + 该态的设施 + 数字卡 + 设施清单 + 表里的高亮一起换。
  // 换态时时间轴停在**同一时刻**，这样看的是「同一时刻、不同方案」的淹没范围，才有可比性。
  // L2+A+B（2026-09-27）：把 Parsons 当既成事实，在它之上再加本系统箱涵（tools/l2ab.py，按 L2+A 的残余积水选址）。
  // 和 L2+B 不是一条链：L2+B 是从 L1 出发的并列方案，L2+A+B 接在 L2+A 后面
  const WB_ST = ['L1','L2','L2+A','L2+A+B','L2+B'];   // 主线在前，L2+B 对照放最后
  const WB_COL = {'L1':'#334155','L2':'#64748b','L2+A':'#b45309','L2+B':'#0e7490','L2+A+B':'#7c3aed'};
  const WB_FRAME_OF = {'L1':'L1','L2':'L1','L2+A':'L2+A','L2+B':'L2+B','L2+A+B':'L2+A+B'};   // L2 区内 0 座已建成塘，≡ L1
  const WB_KPI = [['二维·峰值淹没面积','ha',2,'wb.kArea','2d_wet_t_h'], ['二维·峰值水深','m',2,'wb.kDepth','2d_hmax_t_h'],
                  ['洪水量(节点累计)','m³',0,'wb.kFlood',null], ['退水(主网)','h',1,'wb.kRecede',null]];
  const WB = {st: WB_ST.includes(stKey(U.get('st'))) ? stKey(U.get('st')) : WB_ST.includes(stKey(U.get('f2s'))) ? stKey(U.get('f2s')) : 'L2+B',
              base: WB_ST.includes(stKey(U.get('base'))) ? stKey(U.get('base')) : 'L1',
              t: isFinite(parseFloat(U.get('t'))) ? parseFloat(U.get('t')) : null,
              timer: null, speed: 320, net: null, scope: null, fac: null, fp: null, fpAB: null, lay: null, keys: false};
  let l2pView = ['1d', 'pilot', 'city', 'city2d', 'rb2', 'c1', 'rb01', 'rda'].includes(U.get('v')) ? U.get('v') : 'wb';

  function wbStop(){ if(WB.timer){ clearInterval(WB.timer); WB.timer = null; }
    const b = document.getElementById('wbplay'); if(b){ b.textContent = '▶'; b.setAttribute('aria-label', t('wb.play')); } }
  function wbDim(on){ const tp = map.getPane('tilePane'); if(tp) tp.style.filter = on ? 'grayscale(.85) opacity(.7)' : ''; }
  function wbClear(){ wbStop(); wbDim(false); if(WB.lay){ Object.values(WB.lay).forEach(l => { if(l) map.removeLayer(l); }); WB.lay = null; } }
  function wbFr(st){ return (F2?.states || []).find(x => x.state === WB_FRAME_OF[st || WB.st]) || null; }
  function wbIdx(){ const s = wbFr(); if(!s) return 0;
    if(WB.t == null) return s.peak_frame || 0;
    let best = 0; s.frames.forEach((f, i) => { if(Math.abs(f.t_h - WB.t) < Math.abs(s.frames[best].t_h - WB.t)) best = i; });
    return best; }
  function wbVal(key, st){ const r = (SG?.table || {})[key]; return r ? (r.values || {})[st] : null; }
  function wbDelta(key, st){
    const r = (SG?.table || {})[key]; if(!r || st === WB.base) return '';
    const a = (r.values || {})[WB.base], b = (r.values || {})[st];
    if(a == null || b == null || !a) return '';
    const p = (b - a) / Math.abs(a) * 100;
    if(Math.abs(p) < 0.05) return `<span class="wbd">= ${esc(WB.base)}</span>`;
    const good = r.better === 'low' ? p < 0 : p > 0;
    return `<span class="wbd ${good ? 'good' : 'bad'}">${p > 0 ? '+' : ''}${p.toFixed(0)}% ${t('wb.vs')} ${esc(WB.base)}</span>`;
  }

  function wbViews(){
    return `<div class="seg wbviews">`
      + `<button data-l2pv="wb" class="${l2pView === 'wb' ? 'on' : ''}">${t('wb.vWb')}</button>`
      + `<button data-l2pv="city" class="${l2pView === 'city' || l2pView === 'city2d' ? 'on' : ''}">${t('wb.vCity')}</button>`
      + `<button data-l2pv="rb2" class="${l2pView === 'rb2' ? 'on' : ''}">${t('wb.vRb2')}</button>`
      + `<button data-l2pv="c1" class="${l2pView === 'c1' ? 'on' : ''}">C1</button>`
      + `<button data-l2pv="rb01" class="${l2pView === 'rb01' ? 'on' : ''}">RB-01</button>`
      + `<button data-l2pv="rda" class="${l2pView === 'rda' ? 'on' : ''}">RD-A</button>`
      + `<button data-l2pv="pilot" class="${l2pView === 'pilot' ? 'on' : ''}">${t('wb.vPilot')}</button>`
      + `<button data-l2pv="1d" class="${l2pView === '1d' ? 'on' : ''}">${t('wb.v1d')}</button></div>`;
  }
  // 全市：一维（管网溢流）/ 一二维（地面积水）切换 + 计算设置（data/city_params.json，tools/city_params.py，报告同源）
  let CPAR = null;
  async function cityBasis(active){
    if(!CPAR) CPAR = await load('city_params.json').catch(() => null);
    const seg = `<div class="seg cybasis" role="tablist" style="margin:6px 0 4px">`
      + [['city', 'cyb1'], ['city2d', 'cyb2']].map(([v, k]) => `<button role="tab" data-cyb="${v}" class="${active === v ? 'on' : ''}" aria-selected="${active === v}">${t('cy.' + k)}</button>`).join('') + `</div>`;
    if(!CPAR) return seg;
    const rows = r => r.map(([a, b]) => `<tr><td style="white-space:nowrap;vertical-align:top"><b>${esc(a)}</b></td><td>${esc(b)}</td></tr>`).join('');
    return seg + `<details class="sec wbsec"><summary>${t('cy.params')}</summary><div class="body">
      <table class="wbft" style="font-size:12px">${rows(F(CPAR, 'common'))}${rows(F(CPAR, active === 'city' ? 'oneD' : 'twoD'))}</table>
      <div class="m" style="margin-top:6px">${esc(F(CPAR, 'basis_note'))}</div></div></details>`;
  }
  function cityBasisBind(){
    panel.querySelectorAll('[data-cyb]').forEach(b => b.onclick = () => {
      if(l2pView === b.dataset.cyb) return;
      l2pView = b.dataset.cyb; wbClear(); plClear(); cyClear(); rbClear(); c1Clear(); r1Clear(); render(); });
  }
  function wbBindViews(){
    panel.querySelectorAll('[data-l2pv]').forEach(b => b.onclick = () => {
      if(l2pView === b.dataset.l2pv) return;
      l2pView = b.dataset.l2pv; wbClear(); plClear(); cyClear(); rbClear(); c1Clear(); r1Clear(); render(); });
  }

  function wbKpis(){
    const st = WB.st, S = (SG?.states || {})[st] || (SG?.states || {}).L1 || {};
    return WB_KPI.map(([k, u, d, lab, tk]) => {
      const v = wbVal(k, st);
      return `<div class="wbkc"><span class="lab">${t(lab)}</span>`
        + `<b>${v == null ? '—' : fmt(v, d)}<small>${u}</small></b>${wbDelta(k, st)}`
        + (tk && S[tk] != null ? `<span class="m">${t('wb.at')} t = ${(+S[tk]).toFixed(2)} h</span>` : '')
        + `</div>`; }).join('');
  }

  function wbFacList(){
    const st = WB.st;
    if(st === 'L1') return `<div class="m">${t('wb.facL1')}</div>`;
    if(st === 'L2+A+B'){                                  // Parsons 的（既成事实）+ 本系统在其上补的
      const pa = (WB.fac?.features || []).filter(f => f.properties.state === 'L2+A');
      const ab = WB.fpAB?.features || [];
      const row = (id, col, vol, land, tag) => `<tr data-wbf="${esc(id)}" tabindex="0"><td><i class="wbdot" style="background:${col}"></i><b>${esc(id)}</b></td>`
        + `<td class="num">${fmt(vol, 0)}</td><td>${land}</td><td class="m">${tag}</td></tr>`;
      return `<table class="wbft"><tr><th>${t('wb.fId')}</th><th class="num">${t('wb.fVol')}</th><th>${t('wb.fLand')}</th><th>${t('wb.fSrc')}</th></tr>`
        + pa.map(f => row(f.properties.id, WB_COL['L2+A'], f.properties.vol_usable_m3 || f.properties.volume_m3, '—', t('wb.srcA'))).join('')
        + ab.map(f => { const q = f.properties;
            const land = q.land === 'open_land' ? t('wb.open') : q.land === 'road_reserve' ? `<span style="color:var(--warn)">${t('wb.reserve')}</span>` : '—';
            return row('B·' + q.id, WB_COL['L2+A+B'], q.vol_m3, land + (q.moved_m ? ` <span class="m">· ${t('wb.moved', {m: fmt(q.moved_m, 0)})}</span>` : ''), t('wb.srcB')); }).join('')
        + `</table><div class="m">${t('wb.abNote')}</div>`;
    }
    if(st === 'L2') return `<div class="m">${lang === 'en' ? t('wb.facL2') : mdb((SG?.empty || {}).L2 || t('wb.facL2'))}</div>`;
    const fs = (WB.fac?.features || []).filter(f => f.properties.state === st);
    const rows = fs.map(f => { const p = f.properties;
      const land = p.land === 'open_land' ? t('wb.open')
        : p.land === 'road_reserve' ? `<span style="color:var(--warn)">${t('wb.reserve')}</span>`
        : (p.n_out === 0 ? `<span style="color:var(--bad)">${t('wb.noOut')}</span>` : '—');
      return `<tr data-wbf="${esc(p.id)}" tabindex="0">`
        + `<td><i class="wbdot" style="background:${WB_COL[st]}"></i><b>${esc(p.id)}</b></td>`
        + `<td class="num">${fmt(p.vol_usable_m3 || p.volume_m3, 0)}</td>`
        + `<td>${land}${p.moved_m ? ` <span class="m">· ${t('wb.moved', {m: fmt(p.moved_m, 0)})}</span>` : ''}</td>`
        + `<td class="num">${p.clr_pave_m != null ? fmt(p.clr_pave_m, 0) + ' m' : '—'}</td></tr>`; }).join('');
    return `<table class="wbft"><tr><th>${t('wb.fId')}</th><th class="num">${t('wb.fVol')}</th>`
      + `<th>${t('wb.fLand')}</th><th class="num">${t('wb.fPave')}</th></tr>${rows}</table>`
      + `<div class="m">${t('wb.facHint')}</div>`;
  }

  function wbTable(){
    const T = SG?.table || {}; const is2d = k => k.indexOf('二维') === 0;
    const keys = Object.keys(T); const order = keys.filter(is2d).concat(keys.filter(k => !is2d(k)));
    const head = `<tr><th>${t('sg.param')}</th>` + WB_ST.map(s =>
      `<th class="num${s === WB.st ? ' on' : ''}" data-wbs="${s}" style="color:${WB_COL[s]};cursor:pointer">${s}</th>`).join('')
      + `<th class="num">${t('sg.unit')}</th></tr>`;
    const body = order.map(k => { const m = T[k];
      return `<tr${is2d(k) ? ' class="twod"' : ''}><td>${esc(k)}</td>` + WB_ST.map(s =>
        `<td class="num${s === WB.st ? ' on' : ''}">${sgFmt((m.values || {})[s], m.unit)}`
        + (s === WB.st && s !== WB.base ? `<br>${wbDelta(k, s)}` : '') + `</td>`).join('')
        + `<td class="num m">${esc(m.unit)}</td></tr>`; }).join('');
    return `<div class="tblwrap"><table class="sg wbtbl">${head}${body}</table></div>`;
  }

  function wbChart(){
    const el = document.getElementById('wbch'); if(!el || !F2) return;
    const X0 = 34, X1 = 332, Y0 = 20, Y1 = 124;
    const all = ['L1', 'L2+A', 'L2+B', 'L2+A+B'].map(k => ({k, s: (F2.states || []).find(x => x.state === k)})).filter(x => x.s);
    if(!all.length) return;
    const TM = Math.max(...all.map(x => x.s.frames[x.s.frames.length - 1].t_h));
    const AM = Math.max(5, Math.ceil(Math.max(...all.map(x => x.s.peak_wet_ha)) / 5) * 5);
    const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / AM;
    let h = '';
    for(let a = 0; a <= AM; a += 5) h += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/>`
      + `<text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${a}</text>`;
    for(let v = 0; v <= TM + 1e-9; v += 2) h += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    h += `<text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">${t('wb.axT')}</text>`
      + `<text x="2" y="7" font-size="9" fill="#66717f">${t('wb.axA')}</text>`;
    const sel = WB_FRAME_OF[WB.st];
    all.forEach(({k, s}) => {
      const pts = s.frames.map(f => `${sx(f.t_h).toFixed(1)},${sy(f.wet_ha).toFixed(1)}`).join(' ');
      const on = k === sel;
      h += `<polyline points="${pts}" fill="none" stroke="${WB_COL[k]}" stroke-width="${on ? 2.6 : 1.2}" opacity="${on ? 1 : .5}" stroke-linejoin="round"/>`;
      const last = s.frames[s.frames.length - 1], lx = sx(last.t_h), right = lx > X1 - 40;
      h += `<text x="${right ? lx - 2 : lx + 3}" y="${sy(last.wet_ha) + (k === 'L2+A+B' ? 12 : -4)}" font-size="9" font-weight="${on ? 700 : 400}" fill="${WB_COL[k]}" text-anchor="${right ? 'end' : 'start'}">${k === 'L1' ? 'L1 / L2' : k}</text>`;
    });
    const s = wbFr(), f = s ? s.frames[wbIdx()] : null;
    if(f) h += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`
      + `<circle cx="${sx(f.t_h)}" cy="${sy(f.wet_ha)}" r="4" fill="${WB_COL[WB.st]}" stroke="#fff" stroke-width="1.5"/>`;
    h += `<rect x="${X0}" y="${Y0}" width="${X1 - X0}" height="${Y1 - Y0}" fill="transparent" id="wbchhit" style="cursor:crosshair"/>`;
    el.innerHTML = h;
    const hit = document.getElementById('wbchhit');
    if(hit) hit.onclick = ev => { const r = el.getBoundingClientRect(); const x = (ev.clientX - r.left) / r.width * 340;
      wbStop(); WB.t = Math.max(0, Math.min(TM, (x - X0) / (X1 - X0) * TM)); wbDraw(); };
  }

  function wbDraw(){
    const s = wbFr(); if(!s) return;
    const i = wbIdx(), f = s.frames[i];
    if(WB.t == null) WB.t = f.t_h;
    const url = 'data/flood2d/' + f.png;
    if(!WB.lay) WB.lay = {};
    if(!WB.lay.water) WB.lay.water = L.imageOverlay(url, s.bounds, {opacity: .8, interactive: false, zIndex: 450}).addTo(map);
    else { WB.lay.water.setUrl(url); WB.lay.water.setBounds(s.bounds); }
    const end = s.frames[s.frames.length - 1].t_h, over = WB.t > end + 0.02;
    const now = document.getElementById('wbnow');
    if(now) now.innerHTML = `<b>t = ${(+f.t_h).toFixed(2)} h</b>　${t('f2.area')} <b>${fmt(+f.wet_ha, 2)}</b> ha`
      + `　${t('f2.depth')} <b>${fmt(+f.dmax_m, 2)}</b> m`
      + (i === s.peak_frame ? `　<span class="pkbadge">${t('f2.peakA')}</span>` : '')
      + (i === s.deep_frame ? `　<span class="pkbadge deep">${t('f2.peakD')}</span>` : '')
      + (over ? `<div class="m" style="color:var(--warn)">${t('wb.over', {h: end.toFixed(1)})}</div>` : '');
    const rg = document.getElementById('wbrng'); if(rg){ rg.max = String(s.frames.length - 1); rg.value = String(i); }
    wbChart(); wbUrl();
  }

  function wbUrl(){ try{ const u = new URL(location.href);
    u.searchParams.set('tab', 'line2p'); u.searchParams.delete('v'); u.searchParams.set('st', WB.st);
    if(WB.base !== 'L1') u.searchParams.set('base', WB.base); else u.searchParams.delete('base');
    if(WB.t != null) u.searchParams.set('t', WB.t.toFixed(2));
    ['c', 'f2s', 'f2i'].forEach(k => u.searchParams.delete(k));
    history.replaceState(null, '', u); }catch(e){} }

  function wbPlay(){
    const s = wbFr(); if(!s) return;
    if(WB.timer){ wbStop(); return; }
    if(wbIdx() >= s.frames.length - 1){ WB.t = s.frames[0].t_h; wbDraw(); }
    const b = document.getElementById('wbplay'); if(b){ b.textContent = '❚❚'; b.setAttribute('aria-label', t('wb.pause')); }
    WB.timer = setInterval(() => { const c = wbFr(); if(!c){ wbStop(); return; }
      const k = wbIdx(); if(k >= c.frames.length - 1){ wbStop(); return; }
      WB.t = c.frames[k + 1].t_h; wbDraw(); }, WB.speed);
  }

  function wbMapStatic(){
    if(!WB.lay) WB.lay = {};
    if(!map.getPane('wbNet')){ map.createPane('wbNet'); map.getPane('wbNet').style.zIndex = 395; }
    const rend = L.canvas({pane: 'wbNet'});
    if(!WB.lay.net && WB.net && WB.net.features){
      const g = L.layerGroup();
      WB.net.features.forEach(ft => { const p = ft.properties || {}, k = p.layer;
        if(ft.geometry.type === 'LineString' && k === 'conduit')
          L.polyline(ft.geometry.coordinates.map(c => [c[1], c[0]]), {color: '#94a3b8', renderer: rend, interactive: false,
            weight: p.dia_mm ? Math.max(.7, Math.min(3.5, p.dia_mm / 550)) : 1, opacity: .85}).addTo(g);
        else if(ft.geometry.type === 'Point' && (k === 'pump' || k === 'outfall'))
          L.circleMarker([ft.geometry.coordinates[1], ft.geometry.coordinates[0]], {radius: 4, renderer: rend,
            color: k === 'pump' ? '#be123c' : '#1d4ed8', weight: 1.5, fillColor: '#fff', fillOpacity: 1})
            .bindTooltip(`${esc(NETNAME(k))} ${esc(p.label || '')}`).addTo(g); });
      WB.lay.net = g.addTo(map);
    }
    if(!WB.lay.catch && WB.scope){
      const f = (WB.scope.features || []).filter(x => (x.properties || {}).role === 'modelled');
      if(f.length) WB.lay.catch = L.geoJSON({type: 'FeatureCollection', features: f},
        {style: {color: '#b91c1c', weight: 2.2, fill: false}, interactive: false}).addTo(map);
    }
  }

  function wbFacLayer(){
    if(WB.lay?.fac){ map.removeLayer(WB.lay.fac); WB.lay.fac = null; }
    const st = WB.st; if(st !== 'L2+A' && st !== 'L2+B' && st !== 'L2+A+B') return;
    const g = L.featureGroup(), c = WB_COL[st === 'L2+A+B' ? 'L2+A' : st], src = WB.fac?.source || '';
    const lbl = (ll, id, col) => L.marker(ll, {icon: L.divIcon({className: 'wblbl', html: `<span style="border-color:${col};color:${col}">${esc(id)}</span>`,
      iconSize: null, iconAnchor: [-10, 9]}), interactive: false, keyboard: false}).addTo(g);
    if(st === 'L2+A+B') (WB.fpAB?.features || []).forEach(f => {
      const q = f.properties, cb = WB_COL['L2+A+B'];
      const lay = L.geoJSON(f, {style: {color: cb, weight: 2, fillColor: cb, fillOpacity: .45}});
      const b = lay.getBounds(), ll = [b.getCenter().lat, b.getCenter().lng];
      lay.bindPopup(`<h4>${esc(q.id)} · ${t('wb.srcB')}</h4><div class="kv"><span>${t('wb.fVol')}</span><span><b>${fmt(q.vol_m3, 0)}</b></span>`
        + `<span>${t('wb.fBot')}</span><span>${fmt(q.bottom_m, 2)} / ${fmt(q.top_m, 2)} m</span>`
        + `<span>${t('wb.fLand')}</span><span>${q.land === 'open_land' ? t('wb.open') : t('wb.reserve')}${q.moved_m ? ' · ' + t('wb.moved', {m: fmt(q.moved_m, 0)}) : ''}</span>`
        + `<span>${t('wb.fConv')}</span><span>DN${q.dn_mm} · ${fmt(q.conveyance_m, 0)} m</span></div><div class="m">${t('wb.abNote')}</div>`, {maxWidth: 360});
      lay._wbid = 'B·' + q.id; lay._wbll = ll; lay.addTo(g); lbl(ll, q.id, cb); });
    const fpOf = {}; (WB.fp?.features || []).forEach(f => { fpOf[f.properties.id] = f; });
    (WB.fac?.features || []).filter(f => f.properties.state === (st === 'L2+A+B' ? 'L2+A' : st)).forEach(f => {
      const p = f.properties, ll = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
      let lay;
      if(st === 'L2+B' && fpOf[p.id]) lay = L.geoJSON(fpOf[p.id], {style: {color: c, weight: 2, fillColor: c, fillOpacity: .45}});
      else lay = L.circleMarker(ll, {radius: Math.max(7, Math.min(18, Math.sqrt(+p.volume_m3 || 1) / 2.5)),
        color: c, weight: 2, fillColor: c, fillOpacity: .4});
      lay.bindPopup(() => l2plusPopupHtml(p, src), {maxWidth: 360});
      lay._wbid = p.id; lay._wbll = ll;
      lay.addTo(g);
      L.marker(ll, {icon: L.divIcon({className: 'wblbl', html: `<span style="border-color:${c};color:${c}">${esc(p.id)}</span>`,
        iconSize: null, iconAnchor: [-10, 9]}), interactive: false, keyboard: false}).addTo(g);
    });
    WB.lay.fac = g.addTo(map);
  }

  function wbFocus(id){
    const g = WB.lay?.fac; if(!g) return;
    g.eachLayer(l => { if(l._wbid === id){ try{ map.flyTo(l._wbll, Math.max(map.getZoom(), 16), {duration: .6});
      setTimeout(() => { try{ l.openPopup(l._wbll); }catch(e){} }, 650); }catch(e){} } });
  }

  function wbSelect(st, first){
    wbStop(); WB.st = st;
    panel.querySelectorAll('.wbbar [data-wbs]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.wbs === st)));
    const k = document.getElementById('wbkpi'); if(k) k.innerHTML = wbKpis();
    const fl = document.getElementById('wbfac'); if(fl) fl.innerHTML = wbFacList();
    const tb = document.getElementById('wbtbl'); if(tb) tb.innerHTML = wbTable();
    const nt = document.getElementById('wbstnote'); if(nt) nt.innerHTML = st === 'L2' ? t('wb.l2same') : '';
    panel.querySelectorAll('[data-wbf]').forEach(r => { r.onclick = () => wbFocus(r.dataset.wbf);
      r.onkeydown = e => { if(e.key === 'Enter') wbFocus(r.dataset.wbf); }; });
    panel.querySelectorAll('.wbtbl [data-wbs]').forEach(b => b.onclick = () => wbSelect(b.dataset.wbs));
    wbFacLayer(); wbDraw();
    if(first){ const s = wbFr(); window._viewOwned = true;
      const fit = () => { try{ map.invalidateSize();
        // 汇水区是一条南北向的窄带，设施有几座在带外：按「汇水区 + 设施」一起取包络，留一点边
        let b = WB.lay?.catch ? WB.lay.catch.getBounds() : L.latLngBounds(s.bounds);
        (WB.fac?.features || []).forEach(f => b.extend([f.geometry.coordinates[1], f.geometry.coordinates[0]]));
        map.fitBounds(b.pad(.06), {maxZoom: 17}); }catch(e){} };
      fit(); setTimeout(fit, 120); setTimeout(fit, 780); }
  }

  function wbKeys(){
    if(WB.keys) return; WB.keys = true;
    document.addEventListener('keydown', e => {
      if(tab !== 'line2p' || l2pView !== 'wb' || !document.getElementById('wbrng')) return;
      const tg = (e.target && e.target.tagName) || '';
      if(/INPUT|SELECT|TEXTAREA/.test(tg)) return;             // 滑块、下拉、输入框自己处理按键
      if(e.target && e.target.closest && e.target.closest('#agent')) return;
      const s = wbFr(); if(!s) return;
      if(e.key === 'ArrowRight' || e.key === 'ArrowLeft'){
        e.preventDefault(); wbStop();
        const i = Math.max(0, Math.min(s.frames.length - 1, wbIdx() + (e.key === 'ArrowRight' ? 1 : -1)));
        WB.t = s.frames[i].t_h; wbDraw();
      } else if(e.key === ' ' && tg !== 'BUTTON'){ e.preventDefault(); wbPlay(); }
      else if(/^[1-5]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey){ wbSelect(WB_ST[+e.key - 1]); }
    });
  }

  async function renderWB(){
    f2Clear(); cyClear();
    if(tl3Layer){ map.removeLayer(tl3Layer); tl3Layer = null; }
    if(tl3NetLayer){ map.removeLayer(tl3NetLayer); tl3NetLayer = null; }
    if(layers.l2plus && map.hasLayer(layers.l2plus)) await toggleLayer('l2plus', false);
    const lg = document.getElementById('legend'); if(lg){ lg.innerHTML = ''; lg.style.display = 'none'; }
    const [sg, f2] = await Promise.all([api('/api/state_gap?region=Mussafah_00'),
      F2 ? Promise.resolve(F2) : load('flood2d/manifest.json').catch(() => null)]);
    SG = sg; F2 = f2;
    if(!SG || !SG.available){ panel.innerHTML = wbViews() + `<div class="empty">${t('wb.noData')}</div>`; wbBindViews(); return; }
    if(!WB.fac) WB.fac = await load('l2plus.geojson').catch(() => null);
    if(!WB.fp) WB.fp = await load('l2plus_fp.geojson').catch(() => null);
    if(!WB.fpAB) WB.fpAB = await load('l2ab_fp.geojson').catch(() => null);
    if(!WB.scope) WB.scope = await load('scope.geojson').catch(() => null);
    if(!WB.net) WB.net = await api('/api/three_lines_net?catchment=Mussafah');
    const dom = (SG.states || {}).L1 || {};
    const legend = F2 ? [0.02, 0.1, 0.3, 0.7, 1.4].map(v => { const q = Math.sqrt(Math.min(v / F2.vmax_m, 1));
      const cs = ['#7dd3fc', '#2582eb', '#1d4ed8', '#4c1d95'];
      return `<span><i style="background:${cs[Math.min(3, Math.floor(q * 3))]}"></i>${v.toFixed(2)} m</span>`; }).join('') : '';
    const player = wbFr() ? `<div class="wbpl"><button id="wbplay" class="f2btn" aria-label="${t('wb.play')}">▶</button>
        <input id="wbrng" type="range" min="0" max="1" value="0" step="1" aria-label="${t('wb.time')}">
        <select id="wbspd" aria-label="${t('wb.speed')}"><option value="640">0.5×</option><option value="320" selected>1×</option><option value="140">2×</option></select></div>
        <div id="wbnow" class="f2now"></div>
        <div class="wbjump"><button class="chip" data-wbj="peak">${t('wb.jPeak')}</button>
          <button class="chip" data-wbj="deep">${t('wb.jDeep')}</button>
          <button class="chip" data-wbj="end">${t('wb.jEnd')}</button></div>
        <svg id="wbch" class="wbch" viewBox="0 0 340 150" role="img" aria-label="${t('wb.chart')}"></svg>
        <div class="m">${t('wb.chartHint')}</div>
        <div class="f2bar">${legend}<span><i style="background:#b91c1c;height:3px"></i>${t('wb.lgCatch')}</span></div>`
      : `<div class="m">${t('wb.no2d')}</div>`;
    panel.innerHTML = wbViews() + `
      <div class="wbhead"><h2>${t('wb.title')}</h2>
        <div class="m">${t('wb.sub', {ha: fmt(SG.region_ha, 0), scen: esc(SG.scen || ''),
          res: fmt(dom['2d_res_m'] || 15, 0), tri: fmt(dom['2d_n_tri'] || 0, 0)})}</div></div>
      <div class="wbsticky">
        <div class="wbbar" role="tablist" aria-label="${t('wb.pick')}">${WB_ST.map((s, i) =>
          `<button role="tab" data-wbs="${s}" aria-selected="${s === WB.st}" style="--c:${WB_COL[s]}" title="${t('wb.key', {n: i + 1})}">`
          + `<b>${s}</b><small>${esc(t('wb.d.' + s.replace(/\+/g, 'p')))}</small></button>`).join('')}</div>
        <div class="wbbase"><label class="m">${t('wb.base')} <select id="wbbase">${WB_ST.map(s =>
          `<option value="${s}"${s === WB.base ? ' selected' : ''}>${s}</option>`).join('')}</select></label>
          <span class="m" id="wbstnote"></span></div>
      </div>
      <div class="wbk" id="wbkpi"></div>
      <h3 class="wbh">${t('wb.hPlay')} <span class="m">${t('wb.keys')}</span></h3>
      ${player}
      <h3 class="wbh">${t('wb.hFac')}</h3><div id="wbfac"></div>
      <details class="sec wbsec" open><summary>${t('wb.hTable')}</summary><div class="body" id="wbtbl"></div></details>
      <details class="sec wbsec"><summary>${t('wb.hNotes')}</summary><div class="body">
        <div class="m">${mdb(t('sg.areaWarn'), true)}</div>
        <div class="m">${mdb(t('f2.note').replace('{dry}', F2?.dry_m ?? 0.02).replace('{cell}', F2?.cell_m ?? 15), true)}</div>
        ${Object.entries(SG.notes || {}).map(([k, v]) => `<div class="m"><b>${esc(k)}</b>　${mdb(v)}</div>`).join('')}
        <div class="m">${mdb(t('sg.vsOther'), true)}</div></div></details>`;
    wbBindViews(); wbMapStatic(); wbKeys(); wbDim(true);
    panel.querySelectorAll('.wbbar [data-wbs]').forEach(b => b.onclick = () => wbSelect(b.dataset.wbs));
    const bs = document.getElementById('wbbase'); if(bs) bs.onchange = () => { WB.base = bs.value; wbSelect(WB.st); };
    const pb = document.getElementById('wbplay'); if(pb) pb.onclick = wbPlay;
    const sp = document.getElementById('wbspd'); if(sp) sp.onchange = () => { WB.speed = +sp.value; if(WB.timer){ wbStop(); wbPlay(); } };
    const rg = document.getElementById('wbrng'); if(rg) rg.oninput = () => { wbStop(); const s = wbFr(); if(s){ WB.t = s.frames[+rg.value].t_h; wbDraw(); } };
    panel.querySelectorAll('[data-wbj]').forEach(b => b.onclick = () => { wbStop(); const s = wbFr(); if(!s) return;
      const j = b.dataset.wbj, i = j === 'peak' ? s.peak_frame : j === 'deep' ? s.deep_frame : s.frames.length - 1;
      WB.t = s.frames[i ?? 0].t_h; wbDraw(); });
    // 旧深链：?f2i=12 按帧号定位；?fac=NP1 切到那座设施所在的态并打开说明
    const qi = parseInt(U.get('f2i'), 10), s0 = wbFr();
    if(WB.t == null && s0 && isFinite(qi) && qi >= 0 && qi < s0.frames.length) WB.t = s0.frames[qi].t_h;
    const fq = U.get('fac'), ff = fq && (WB.fac?.features || []).find(f => f.properties.id === fq);
    if(ff) WB.st = ff.properties.state;
    wbSelect(WB.st, true);
    if(ff) setTimeout(() => wbFocus(fq), 900);
  }

  // ================= Madinat Al Riyad 一二维试跑：L1 / L2 =================
  // 全市一二维不能按管网切片（地表把各片连成一块），先拿这一片量速度、看 L2 的塘到底起多大作用。
  // 数据：tools/build_pilot.py → couple2way_v2.py → pilot_frames.py（console/data/flood2d_pilot/）。
  // 积水面积用 ≥0.10 / ≥0.30 m：这一片 64% 的地面直接在二维上下雨，2–3 cm 的雨水膜按 0.02 m 会被算成「淹没」。
  const PL_ST = ['L1', 'L2'];
  const PL_COL = {L1: '#334155', L2: '#0e7490'};
  const PL = {st: PL_ST.includes(U.get('st')) ? U.get('st') : 'L2', t: isFinite(parseFloat(U.get('t'))) ? parseFloat(U.get('t')) : null,
              timer: null, speed: 320, max: false, man: null, stats: null, ponds: null, lay: null, reg: 'mar', cache: {}, diff: false, dj: null};
  const PL_DIR = {mar: 'flood2d_pilot', city: 'flood2d_city2d'};
  const tp = (k, o) => { if(PL.reg === 'city'){ const v = t('pcy.' + k, o); if(v !== 'pcy.' + k) return v; } return t('pt.' + k, o); };
  const PL_KPI = [['max_area_ge_0p30_ha', 'ha', 1, 'pt.k30'], ['max_area_ge_0p10_ha', 'ha', 0, 'pt.k10'],
                  ['max_depth_m', 'm', 2, 'pt.kDepth'], ['final_vol_m3', 'm³', 0, 'pt.kLeft'], ['flood_1d_to_2d_m3', 'm³', 0, 'pcy.kOver']];
  const plFillCol = f => f == null ? '#94a3b8' : f >= 99 ? '#b91c1c' : f >= 30 ? '#d97706' : '#2563eb';

  function plStop(){ if(PL.timer){ clearInterval(PL.timer); PL.timer = null; }
    const b = document.getElementById('plplay'); if(b){ b.textContent = '▶'; b.setAttribute('aria-label', t('wb.play')); } }
  function plClear(){ plStop(); if(PL.lay){ Object.values(PL.lay).forEach(l => { if(l) map.removeLayer(l); }); PL.lay = null; }
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = ''; }
  function plFr(){ return (PL.man?.states || []).find(x => x.state === PL.st) || null; }
  function plIdx(){ const s = plFr(); if(!s) return 0; if(PL.t == null) return s.peak_frame || 0;
    let b = 0; s.frames.forEach((f, i) => { if(Math.abs(f.t_h - PL.t) < Math.abs(s.frames[b].t_h - PL.t)) b = i; }); return b; }
  function plDelta(k){
    if(PL.st === 'L1') return '';
    const a = PL.stats?.L1?.[k], b = PL.stats?.L2?.[k]; if(a == null || b == null || !a) return '';
    const p = (b - a) / Math.abs(a) * 100;
    if(Math.abs(p) < 0.05) return `<span class="wbd">= L1</span>`;
    return `<span class="wbd ${p < 0 ? 'good' : 'bad'}">${p > 0 ? '+' : ''}${p.toFixed(1)}% ${t('wb.vs')} L1</span>`;
  }

  function plKpis(){
    const S = PL.stats?.[PL.st] || {};
    let h = PL_KPI.filter(([k]) => S[k] != null || k !== 'flood_1d_to_2d_m3').map(([k, u, d, lab]) => `<div class="wbkc"><span class="lab">${t(lab)}</span>`
      + `<b>${S[k] == null ? '—' : fmt(S[k], d)}<small>${u}</small></b>${plDelta(k)}`
      + (k === 'max_area_ge_0p10_ha' && S.peak_wet10_t_h != null ? `<span class="m">${t('pt.peakAt')} t = ${(+S.peak_wet10_t_h).toFixed(2)} h</span>` : '')
      + `</div>`).join('');
    if(PL.st === 'L2'){
      const tot = (S.ponds || []).reduce((a, p) => a + (p.vol_end_m3 || 0), 0), cap = (S.ponds || []).reduce((a, p) => a + (p.vol_design_m3 || 0), 0);
      h += `<div class="wbkc" style="grid-column:1/-1"><span class="lab">${t('pt.kPond')}</span>`
        + `<b>${fmt(tot, 0)}<small>m³</small></b><span class="m">${tp('kPondSub', {cap: fmt(cap, 0), pct: cap ? (tot / cap * 100).toFixed(0) : '—', n: (S.ponds || []).length})}</span></div>`;
    }
    return h;
  }

  function plPondTable(){
    if(PL.st === 'L1') return `<div class="m">${tp('noPond')}</div>`;
    const P = [...(PL.stats?.L2?.ponds || [])].sort((a, b) => (b.vol_design_m3 || 0) - (a.vol_design_m3 || 0));
    return `<table class="wbft"><tr><th>${t('wb.fId')}</th><th class="num">${t('pt.cap')}</th><th class="num">${t('pt.stored')}</th><th class="num">${t('pt.fill')}</th></tr>`
      + P.map(p => `<tr data-plp="${esc(p.unitid)}" tabindex="0"><td><i class="wbdot" style="background:${plFillCol(p.fill_pct)}"></i>${esc(p.unitid.replace(/^SW_/, ''))}</td>`
        + `<td class="num">${fmt(p.vol_design_m3, 0)}</td><td class="num">${fmt(p.vol_end_m3 || 0, 0)}</td>`
        + `<td class="num" style="color:${plFillCol(p.fill_pct)};font-weight:600">${fmt(p.fill_pct || 0, 0)}%</td></tr>`).join('')
      + `</table><div class="m">${t('pt.pondNote')}</div>`;
  }

  function plChart(){
    const el = document.getElementById('plch'); if(!el || !PL.man) return;
    const X0 = 38, X1 = 332, Y0 = 20, Y1 = 124;
    const all = PL.man.states; if(!all.length) return;
    const TM = Math.max(...all.map(s => s.frames[s.frames.length - 1].t_h));
    const AM = Math.max(100, Math.ceil(Math.max(...all.flatMap(s => s.frames.map(f => f.wet10_ha || 0))) / 500) * 500);
    const STEP = AM <= 1000 ? 250 : AM <= 4000 ? 500 : 2000;
    const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / AM;
    let h = '';
    for(let a = 0; a <= AM; a += STEP) h += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/>`
      + `<text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a, 0)}</text>`;
    for(let v = 0; v <= TM + 1e-9; v += 1) h += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    h += `<text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">${t('wb.axT')}</text>`
      + `<text x="2" y="9" font-size="9" fill="#66717f">${t('pt.axA')}</text>`;
    all.forEach(s => { const on = s.state === PL.st;
      h += `<polyline points="${s.frames.map(f => `${sx(f.t_h).toFixed(1)},${sy(f.wet10_ha || 0).toFixed(1)}`).join(' ')}" fill="none" stroke="${PL_COL[s.state]}" stroke-width="${on ? 2.6 : 1.2}" opacity="${on ? 1 : .5}"/>`;
      const l = s.frames[s.frames.length - 1];
      h += `<text x="${sx(l.t_h) - 2}" y="${sy(l.wet10_ha || 0) + (s.state === 'L1' ? -5 : 11)}" font-size="9" font-weight="${on ? 700 : 400}" fill="${PL_COL[s.state]}" text-anchor="end">${s.state}</text>`; });
    const s = plFr(), f = s ? s.frames[plIdx()] : null;
    if(f) h += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`
      + `<circle cx="${sx(f.t_h)}" cy="${sy(f.wet10_ha || 0)}" r="4" fill="${PL_COL[PL.st]}" stroke="#fff" stroke-width="1.5"/>`;
    h += `<rect x="${X0}" y="${Y0}" width="${X1 - X0}" height="${Y1 - Y0}" fill="transparent" id="plchhit" style="cursor:crosshair"/>`;
    el.innerHTML = h;
    const hit = document.getElementById('plchhit');
    if(hit) hit.onclick = ev => { const r = el.getBoundingClientRect(); const x = (ev.clientX - r.left) / r.width * 340;
      plStop(); PL.max = false; plMaxBtn(); PL.t = Math.max(0, Math.min(TM, (x - X0) / (X1 - X0) * TM)); plDraw(); };
  }

  function plMaxBtn(){ const b = document.getElementById('plmax'); if(b) b.classList.toggle('on', PL.max);
    const d = document.getElementById('pldiff'); if(d) d.classList.toggle('on', !!PL.diff); }

  function plDraw(){
    const s = plFr(); if(!s) return;
    const i = plIdx(), f = s.frames[i];
    if(PL.t == null) PL.t = f.t_h;
    const dv = PL.diff && PL.dj;      // 全市：L2 − L1 全程最大水深差（tools/city2d_post.py）
    const url = 'data/' + PL_DIR[PL.reg] + '/' + (dv ? PL.dj.png : PL.max ? s.max_png : f.png), bnd = dv ? PL.dj.bounds : s.bounds;
    if(!PL.lay) PL.lay = {};
    if(!PL.lay.water) PL.lay.water = L.imageOverlay(url, bnd, {opacity: .85, interactive: false, zIndex: 450}).addTo(map);
    else { PL.lay.water.setUrl(url); PL.lay.water.setBounds(bnd); }
    const now = document.getElementById('plnow');
    if(now) now.innerHTML = dv ? `<b>${t('pcy.diffView')}</b>　${t('pcy.diffSub', {sh: fmt(PL.dj.shallower_ha, 0), near: fmt(PL.dj.near_ha, 0), deep: fmt(PL.dj.deeper_ha, 1)})}`
      : PL.max
      ? `<b>${t('pt.maxView')}</b>　${t('pt.k30')} <b>${fmt(PL.stats?.[PL.st]?.max_area_ge_0p30_ha, 1)}</b> ha`
      : `<b>t = ${(+f.t_h).toFixed(2)} h</b>　${t('pt.now10')} <b>${fmt(+f.wet10_ha || 0, 0)}</b> ha　${t('f2.depth')} <b>${fmt(+f.dmax_m, 2)}</b> m`
        + (i === s.peak_frame ? `　<span class="pkbadge">${t('f2.peakA')}</span>` : '');
    const rg = document.getElementById('plrng'); if(rg){ rg.max = String(s.frames.length - 1); rg.value = String(i); }
    plChart();
    try{ const u = new URL(location.href); u.searchParams.set('tab', 'line2p'); u.searchParams.set('v', PL.reg === 'city' ? 'city2d' : 'pilot');
      u.searchParams.set('st', PL.st); u.searchParams.set('t', PL.t.toFixed(2)); PL.max ? u.searchParams.set('m', '1') : u.searchParams.delete('m'); PL.diff ? u.searchParams.set('d', '1') : u.searchParams.delete('d'); ['c', 'base', 'f2s', 'f2i'].forEach(k => u.searchParams.delete(k));
      history.replaceState(null, '', u); }catch(e){}
  }

  function plPlay(){
    const s = plFr(); if(!s) return;
    if(PL.timer){ plStop(); return; }
    PL.max = false; plMaxBtn();
    if(plIdx() >= s.frames.length - 1){ PL.t = s.frames[0].t_h; plDraw(); }
    const b = document.getElementById('plplay'); if(b){ b.textContent = '❚❚'; b.setAttribute('aria-label', t('wb.pause')); }
    PL.timer = setInterval(() => { const c = plFr(); const k = plIdx();
      if(!c || k >= c.frames.length - 1){ plStop(); return; } PL.t = c.frames[k + 1].t_h; plDraw(); }, PL.speed);
  }

  function plMap(){
    if(!PL.lay) PL.lay = {};
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = 'grayscale(.85) opacity(.7)';
    if(!PL.lay.box && PL.man){      // 计算域按水文边界划（塘的地表汇水区 ∪ 管网服务范围），老版本才是矩形框
      const sty = {color: '#7c3aed', weight: 2, fill: false, dashArray: '6,4', interactive: false};
      PL.lay.box = (PL.stats?.domain_wgs ? L.polygon(PL.stats.domain_wgs, sty) : L.rectangle(PL.man.states[0].bounds, sty)).addTo(map);
    }
    if(PL.lay.ponds){ map.removeLayer(PL.lay.ponds); PL.lay.ponds = null; }
    if(!PL.ponds) return;
    const byId = {}; (PL.stats?.L2?.ponds || []).forEach(p => { byId[p.unitid] = p; });
    const ids = new Set(PL.stats?.pond_ids || []);
    const g = L.featureGroup();
    PL.ponds.features.filter(f => ids.has(f.properties.unitid)).forEach(f => {
      const u = f.properties.unitid, p = byId[u] || {}, c = PL.st === 'L2' ? plFillCol(p.fill_pct) : '#94a3b8';
      const lay = L.geoJSON(f, {style: {color: c, weight: 2.2, fillColor: c, fillOpacity: PL.st === 'L2' ? .35 : .08, dashArray: PL.st === 'L2' ? null : '4,3'}});
      lay.bindTooltip(`${esc(u)}<br>${PL.st === 'L2' ? `${fmt(p.vol_end_m3 || 0, 0)} / ${fmt(p.vol_design_m3 || 0, 0)} m³（${fmt(p.fill_pct || 0, 0)}%）` : t('pt.l1pond')}`);
      lay._plid = u; lay.addTo(g);
    });
    PL.lay.ponds = g.addTo(map);
  }

  function plFocus(u){ const g = PL.lay?.ponds; if(!g) return;
    g.eachLayer(l => { if(l._plid === u){ try{ map.fitBounds(l.getBounds().pad(2), {maxZoom: 16}); l.openTooltip(); }catch(e){} } }); }

  function plSelect(st, first){
    plStop(); PL.st = st;
    panel.querySelectorAll('.wbbar [data-pls]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.pls === st)));
    const k = document.getElementById('plkpi'); if(k) k.innerHTML = plKpis();
    const pt = document.getElementById('plponds'); if(pt) pt.innerHTML = plPondTable();
    panel.querySelectorAll('[data-plp]').forEach(r => { r.onclick = () => plFocus(r.dataset.plp); r.onkeydown = e => { if(e.key === 'Enter') plFocus(r.dataset.plp); }; });
    plMap(); plDraw();
    if(first){ const s = plFr(); window._viewOwned = true;
      const at = (U.get('at') || '').split(',').map(Number), z0 = +U.get('z');     // 地址里给了 at=lat,lon&z= 就停在那里
      const fit = at.length === 2 && at.every(isFinite) && z0 ? () => { try{ map.invalidateSize(); map.setView(at, z0); }catch(e){} }
        : () => { try{ map.invalidateSize(); map.fitBounds(L.latLngBounds(s.bounds).pad(.02)); }catch(e){} };
      fit(); setTimeout(fit, 120); setTimeout(fit, 780); }
  }

  async function renderPilot(){
    wbClear(); f2Clear(); cyClear();
    if(tl3Layer){ map.removeLayer(tl3Layer); tl3Layer = null; }
    if(tl3NetLayer){ map.removeLayer(tl3NetLayer); tl3NetLayer = null; }
    const lg = document.getElementById('legend'); if(lg){ lg.innerHTML = ''; lg.style.display = 'none'; }
    const reg = l2pView === 'city2d' ? 'city' : 'mar';
    if(PL.reg !== reg){ plClear(); PL.reg = reg; PL.man = null; PL.stats = null; }
    const C = PL.cache[reg] = PL.cache[reg] || {};
    if(!C.man) C.man = await load(PL_DIR[reg] + '/manifest.json').catch(() => null);
    if(!C.stats) C.stats = await load(PL_DIR[reg] + '/stats.json').catch(() => null);
    if(reg === 'city' && !C.dj) C.dj = await load(PL_DIR[reg] + '/diff.json').catch(() => null);
    PL.man = C.man; PL.stats = C.stats; PL.dj = C.dj || null; if(!PL.dj) PL.diff = false;
    if(!PL._url){ PL._url = true; PL.max = U.get('m') === '1'; PL.diff = U.get('d') === '1' && !!PL.dj; }   // 地址里的 m=1 全程最大、d=1 差值
    if(!PL.ponds) PL.ponds = await load('ponds_l2.geojson').catch(() => null);
    if(!PL.man || !PL.stats){ panel.innerHTML = wbViews() + `<div class="empty">${t('pt.noData')}</div>`; wbBindViews(); return; }
    const S1 = PL.stats.L1 || {};
    const legend = [0.1, 0.3, 0.7, 1.4].map(v => { const q = Math.sqrt(Math.min(v / PL.man.vmax_m, 1));
      const cs = ['#7dd3fc', '#2582eb', '#1d4ed8', '#4c1d95'];
      return `<span><i style="background:${cs[Math.min(3, Math.floor(q * 3))]}"></i>${v.toFixed(2)} m</span>`; }).join('');
    const basis = reg === 'city' ? await cityBasis('city2d') : '';
    panel.innerHTML = wbViews() + `
      <div class="wbhead"><h2>${tp('title')}</h2>
        <div class="m">${tp('sub', {res: fmt(S1.res_m || 30, 0), tri: fmt(S1.n_tri || 0, 0), nodes: fmt(S1.n_nodes || 0, 0), h: fmt(S1.end_h || 6, 0)})}</div></div>${basis}
      <div class="wbsticky"><div class="wbbar" role="tablist" style="grid-template-columns:repeat(2,1fr)">${PL_ST.map(s =>
        `<button role="tab" data-pls="${s}" aria-selected="${s === PL.st}" style="--c:${PL_COL[s]}"><b>${s}</b><small>${tp('d' + s)}</small></button>`).join('')}</div></div>
      <div class="wbk" id="plkpi"></div>
      <h3 class="wbh">${t('wb.hPlay')}</h3>
      <div class="wbpl"><button id="plplay" class="f2btn" aria-label="${t('wb.play')}">▶</button>
        <input id="plrng" type="range" min="0" max="1" value="0" step="1" aria-label="${t('wb.time')}">
        <select id="plspd" aria-label="${t('wb.speed')}"><option value="640">0.5×</option><option value="320" selected>1×</option><option value="140">2×</option></select></div>
      <div id="plnow" class="f2now"></div>
      <div class="wbjump"><button class="chip" data-plj="peak">${t('wb.jPeak')}</button><button class="chip" id="plmax">${t('pt.showMax')}</button>${PL.dj ? `<button class="chip" id="pldiff">${t('pcy.showDiff')}</button>` : ''}</div>
      <svg id="plch" class="wbch" viewBox="0 0 340 150" role="img" aria-label="${t('pt.axA')}"></svg>
      <div class="f2bar">${legend}<span><i style="background:transparent;border:1.5px dashed #7c3aed"></i>${tp('box')}</span></div>
      <h3 class="wbh">${t('pt.hPonds')}</h3><div id="plponds"></div>
      <details class="sec wbsec" open><summary>${t('pt.hNotes')}</summary><div class="body">
        ${['n1', 'n2', 'n3', 'n4', 'n5'].map(k => `<div class="m">${mdb(tp(k), true)}</div>`).join('')}</div></details>`;
    wbBindViews();
    cityBasisBind();
    panel.querySelectorAll('.wbbar [data-pls]').forEach(b => b.onclick = () => plSelect(b.dataset.pls));
    const pb = document.getElementById('plplay'); if(pb) pb.onclick = plPlay;
    const sp = document.getElementById('plspd'); if(sp) sp.onchange = () => { PL.speed = +sp.value; if(PL.timer){ plStop(); plPlay(); } };
    const rg = document.getElementById('plrng'); if(rg) rg.oninput = () => { plStop(); PL.max = false; PL.diff = false; plMaxBtn(); const s = plFr(); if(s){ PL.t = s.frames[+rg.value].t_h; plDraw(); } };
    panel.querySelectorAll('[data-plj]').forEach(b => b.onclick = () => { plStop(); PL.max = false; plMaxBtn(); const s = plFr(); if(s){ PL.t = s.frames[s.peak_frame].t_h; plDraw(); } });
    const mb = document.getElementById('plmax'); if(mb) mb.onclick = () => { plStop(); PL.diff = false; PL.max = !PL.max; plMaxBtn(); plDraw(); };
    const db_ = document.getElementById('pldiff'); if(db_) db_.onclick = () => { plStop(); PL.max = false; PL.diff = !PL.diff; plMaxBtn(); plDraw(); };
    plSelect(PL.st, true);
  }


  // ================= 29.RB2 多态（2026-09-28） =================
  // 数据：tools/rb02_model.py → rb02_l2b.py / l2ab.py → couple2way_v2.py → pilot_frames.py（console/data/flood2d_rb2/），
  // 设施来自 l2plus_catalog.json，「只在 29.RB2 内」的面积 / 深水面积来自 flood2d_rb2/inregion.json。
  // 主线 L1 → L2 → L2+A → L2+A+B（严格 / 放宽），L2+B（严格 / 放宽）是从 L2 出发的对照。
  // L2+A 是 RB_02 模型里的 PO 塘方案，**设计方未确认**，不叫 Parsons。
  // 最大水深 7 态都是 1.08 m：29.RB2 内一处约 1 m 深的封闭洼地（24.3137°N 54.6535°E）会灌满，与方案无关——所以看 ≥0.8 m 深水面积。
  let RB_ST = ['L1', 'L2', 'L2A', 'L2AB', 'L2AB2', 'L2AC', 'L2B', 'L2B2'];     // 载入 manifest 后只留有帧的态（L2AC 等塘的问题定了再出）
  const RB_LAB = {L1: 'L1', L2: 'L2', L2A: 'L2+A', L2AB: 'L2+A+B', get L2AB2(){ return 'L2+A+B' + t('bi.relaxed'); }, L2AC: 'L2+A+C', L2B: 'L2+B', get L2B2(){ return 'L2+B' + t('bi.relaxed'); }};
  const RB_KEY = {L1: 'L1', L2: 'L2', L2A: 'L2+A', L2AB: 'L2+A+B', L2AB2: 'L2+A+B（放宽）', L2AC: 'L2+A+C', L2B: 'L2+B', L2B2: 'L2+B（放宽）'};   // 数据里的键（l2plus_catalog），与显示名分开
  const RB_COL = {L1: '#334155', L2: '#64748b', L2A: '#b45309', L2AB: '#7c3aed', L2AB2: '#a855f7', L2AC: '#be123c', L2B: '#0e7490', L2B2: '#0891b2'};
  const RB_PARENT = {L2: 'L1', L2A: 'L2', L2AB: 'L2A', L2AB2: 'L2A', L2AC: 'L2A', L2B: 'L2', L2B2: 'L2'};
  const rbKey = s0 => { const s = stKey(s0); return RB_ST.includes(s) ? s : (Object.keys(RB_KEY).find(k => RB_KEY[k] === s || RB_LAB[k] === s) || null); };
  const RB = {st: rbKey(U.get('st')) || 'L2AB2', base: rbKey(U.get('base')) || null, t: null, timer: null, speed: 320,
              man: null, stats: null, inr: null, cat: null, lay: null};
  const RB_KPI = [['area_ge_0p30_in_region_ha', 'ha', 1, 'rb.k30', 'inr'], ['area_ge_0p10_in_region_ha', 'ha', 1, 'rb.k10', 'inr'],
                  ['area_ge_0p80_in_region_ha', 'ha', 2, 'rb.k80', 'inr'], ['flood_1d_to_2d_m3', 'm³', 0, 'rb.k12', 'pf']];
  const rbVal = (k, src, st) => src === 'inr' ? RB.inr?.states?.[st]?.[k] : RB.stats?.[st]?.[k];

  function rbStop(){ if(RB.timer){ clearInterval(RB.timer); RB.timer = null; } const b = document.getElementById('rbplay'); if(b) b.textContent = '▶'; }
  function rbClear(){ rbStop(); if(RB.lay){ Object.values(RB.lay).forEach(l => { if(l) map.removeLayer(l); }); RB.lay = null; }
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = ''; }
  function rbFr(){ return (RB.man?.states || []).find(x => x.state === RB.st) || null; }
  function rbIdx(){ const s = rbFr(); if(!s) return 0; if(RB.t == null) return s.peak_frame || 0;
    let b = 0; s.frames.forEach((f, i) => { if(Math.abs(f.t_h - RB.t) < Math.abs(s.frames[b].t_h - RB.t)) b = i; }); return b; }
  function rbBase(){ return RB.base || RB_PARENT[RB.st] || null; }

  function rbKpis(){
    const b = rbBase();
    return RB_KPI.map(([k, u, d, lab, src]) => { const v = rbVal(k, src, RB.st), a = b ? rbVal(k, src, b) : null;
      let dl = '';
      if(a != null && v != null && a){ const p = (v - a) / a * 100;
        dl = Math.abs(p) < 0.05 ? `<span class="wbd">= ${esc(RB_LAB[b])}</span>` : `<span class="wbd ${p < 0 ? 'good' : 'bad'}">${p > 0 ? '+' : ''}${p.toFixed(1)}% ${t('wb.vs')} ${esc(RB_LAB[b])}</span>`; }
      return `<div class="wbkc"><span class="lab">${t(lab)}</span><b>${v == null ? '—' : fmt(v, d)}<small>${u}</small></b>${dl}</div>`; }).join('');
  }

  function rbFacs(){ const reg = RB.cat?.regions?.['29.RB2']; return (reg?.facilities || {})[RB_KEY[RB.st]] || []; }
  function rbFacCol(f){ return f.kind === 'stationary_pump' ? RB_COL.L2AC : f.kind === 'box' ? RB_COL[RB.st] : f.id.startsWith('PO') ? RB_COL.L2A : RB_COL.L2; }
  function rbFacList(){
    const fs = rbFacs(); if(!fs.length) return `<div class="m">${t('rb.noFac')}</div>`;
    const by = {}; fs.forEach(f => { const k = f.kind_zh || f.kind; (by[k] = by[k] || {n: 0, v: 0, src: f.source}); by[k].n++; by[k].v += f.volume_m3 || 0; by[k].q = (by[k].q || 0) + (f.q_m3s || 0); by[k].np = (by[k].np || 0) + (f.n_pumps || 0); });
    const head = Object.entries(by).map(([k, o]) => `<div class="m"><b>${esc(zE(k))}</b> ${o.n} · ${o.q ? `${o.np} × 12″ · ${fmt(o.q, 2)} m³/s` : `${fmt(o.v, 0)} m³`} <span class="m">（${esc(zE(o.src) || '')}）</span></div>`).join('');
    const land = {open_land: t('wb.open'), road_reserve: t('wb.reserve'), vacant_plot: t('rb.vacant')};
    const rows = fs.filter(f => f.kind === 'box').slice(0, 24).map(f => `<tr data-rbf="${esc(f.id)}" tabindex="0"><td><i class="wbdot" style="background:${rbFacCol(f)}"></i><b>${esc(f.id)}</b></td>`
      + `<td class="num">${fmt(f.volume_m3, 0)}</td><td>${esc(land[f.land] || '—')}${f.moved_m ? ` <span class="m">· ${t('wb.moved', {m: fmt(f.moved_m, 0)})}</span>` : ''}</td></tr>`).join('');
    return head + (rows ? `<table class="wbft"><tr><th>${t('wb.fId')}</th><th class="num">${t('wb.fVol')}</th><th>${t('wb.fLand')}</th></tr>${rows}</table>` : '');
  }

  function rbMap(){
    if(!RB.lay) RB.lay = {};
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = 'grayscale(.85) opacity(.7)';
    if(!RB.lay.dom && RB.stats?.domain_wgs) RB.lay.dom = L.polygon(RB.stats.domain_wgs, {color: '#7c3aed', weight: 2, fill: false, dashArray: '6,4', interactive: false}).addTo(map);
    if(RB.lay.fac){ map.removeLayer(RB.lay.fac); RB.lay.fac = null; }
    const g = L.featureGroup();
    rbFacs().filter(f => f.lon != null).forEach(f => {
      if(f.kind === 'stationary_pump'){
        const c = RB_COL.L2AC;
        if(f.to_lat != null) L.polyline([[f.lat, f.lon], [f.to_lat, f.to_lon]], {color: c, weight: 3, dashArray: '2,5', interactive: false}).addTo(g);
        const m = L.marker([f.lat, f.lon], {icon: L.divIcon({className: '', iconSize: [18, 18], iconAnchor: [9, 9],
          html: `<div style="width:18px;height:18px;border-radius:3px;background:${c};border:2px solid #fff;color:#fff;font:700 10px/14px system-ui;text-align:center">${f.n_pumps}</div>`})});
        m.bindTooltip(`${esc(f.id)} · ${esc(zE(f.kind_zh))} · ${f.n_pumps} × 12″ = ${fmt(f.q_m3s, 2)} m³/s<br>→ ${esc(f.to)} · ${fmt(f.pipe_m, 0)} m<br><span class="m">${esc(zE(f.source) || '')}</span>`);
        m._rbid = f.id; m.addTo(g); return;
      }
      const c = rbFacCol(f), m = L.circleMarker([f.lat, f.lon], {radius: Math.max(5, Math.min(14, Math.sqrt(f.volume_m3 || 1) / 12)), color: c, weight: 2, fillColor: c, fillOpacity: .45});
      m.bindTooltip(`${esc(f.id)} · ${esc(zE(f.kind_zh) || '')} · ${fmt(f.volume_m3, 0)} m³<br><span class="m">${esc(zE(f.source) || '')}</span>`); m._rbid = f.id; m.addTo(g); });
    RB.lay.fac = g.addTo(map);
  }

  function rbChart(){
    const el = document.getElementById('rbch'); if(!el || !RB.man) return;
    const X0 = 38, X1 = 332, Y0 = 20, Y1 = 124, all = RB.man.states;
    const TM = Math.max(...all.map(s => s.frames[s.frames.length - 1].t_h));
    const AM = Math.max(50, Math.ceil(Math.max(...all.flatMap(s => s.frames.map(f => f.wet10_ha || 0))) / 50) * 50);
    const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / AM;
    let h = '';
    for(let a = 0; a <= AM; a += AM / 4) h += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a, 0)}</text>`;
    for(let v = 0; v <= TM + 1e-9; v += 1) h += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    h += `<text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">${t('wb.axT')}</text><text x="2" y="9" font-size="9" fill="#66717f">${t('pt.axA')}</text>`;
    all.forEach(s => { const on = s.state === RB.st, bs = s.state === rbBase();
      h += `<polyline points="${s.frames.map(f => `${sx(f.t_h).toFixed(1)},${sy(f.wet10_ha || 0).toFixed(1)}`).join(' ')}" fill="none" stroke="${RB_COL[s.state]}" stroke-width="${on ? 2.6 : bs ? 1.8 : 1}" opacity="${on || bs ? 1 : .35}"/>`; });
    const s = rbFr(), f = s ? s.frames[rbIdx()] : null;
    if(f) h += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/><circle cx="${sx(f.t_h)}" cy="${sy(f.wet10_ha || 0)}" r="4" fill="${RB_COL[RB.st]}" stroke="#fff" stroke-width="1.5"/>`;
    el.innerHTML = h;
  }

  function rbDraw(){
    const s = rbFr(); if(!s) return;
    const i = rbIdx(), f = s.frames[i]; if(RB.t == null) RB.t = f.t_h;
    const url = 'data/flood2d_rb2/' + f.png;
    if(!RB.lay) RB.lay = {};
    if(!RB.lay.water) RB.lay.water = L.imageOverlay(url, s.bounds, {opacity: .8, interactive: false, zIndex: 450}).addTo(map);
    else { RB.lay.water.setUrl(url); RB.lay.water.setBounds(s.bounds); }
    const now = document.getElementById('rbnow');
    if(now) now.innerHTML = `<b>t = ${(+f.t_h).toFixed(2)} h</b>　${t('pt.now10')} <b>${fmt(+f.wet10_ha || 0, 0)}</b> ha`;
    const rg = document.getElementById('rbrng'); if(rg){ rg.max = String(s.frames.length - 1); rg.value = String(i); }
    rbChart();
    try{ const u = new URL(location.href); u.searchParams.set('tab', 'line2p'); u.searchParams.set('v', 'rb2'); u.searchParams.set('st', RB.st);
      if(RB.base) u.searchParams.set('base', RB.base); else u.searchParams.delete('base'); history.replaceState(null, '', u); }catch(e){}
  }

  function rbPlay(){ const s = rbFr(); if(!s) return; if(RB.timer){ rbStop(); return; }
    if(rbIdx() >= s.frames.length - 1){ RB.t = s.frames[0].t_h; rbDraw(); }
    const b = document.getElementById('rbplay'); if(b) b.textContent = '❚❚';
    RB.timer = setInterval(() => { const c = rbFr(), k = rbIdx(); if(!c || k >= c.frames.length - 1){ rbStop(); return; } RB.t = c.frames[k + 1].t_h; rbDraw(); }, RB.speed); }

  function rbSelect(st, first){
    rbStop(); RB.st = st;
    panel.querySelectorAll('.wbbar [data-rbs]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.rbs === st)));
    const k = document.getElementById('rbkpi'); if(k) k.innerHTML = rbKpis();
    const fl = document.getElementById('rbfac'); if(fl) fl.innerHTML = rbFacList();
    const ds = document.getElementById('rbdesc'); if(ds) ds.textContent = RB.cat?.regions?.['29.RB2']?.[lang === 'en' ? 'states_en' : 'states']?.[RB_KEY[st]] || RB.cat?.regions?.['29.RB2']?.states?.[RB_KEY[st]] || '';
    panel.querySelectorAll('[data-rbf]').forEach(r => r.onclick = () => { RB.lay?.fac?.eachLayer(l => { if(l._rbid === r.dataset.rbf){ map.flyTo(l.getLatLng(), 16); l.openTooltip(); } }); });
    rbMap(); rbDraw();
    if(first){ window._viewOwned = true; const fit = () => { try{ map.invalidateSize(); map.fitBounds(RB.lay.dom ? RB.lay.dom.getBounds().pad(.03) : L.latLngBounds(rbFr().bounds)); }catch(e){} };
      fit(); setTimeout(fit, 120); setTimeout(fit, 780); }
  }

  async function renderRB(){
    wbClear(); plClear(); cyClear(); f2Clear();
    const lg = document.getElementById('legend'); if(lg){ lg.innerHTML = ''; lg.style.display = 'none'; }
    if(!RB.man) RB.man = await load('flood2d_rb2/manifest.json').catch(() => null);
    if(RB.man){ const have = new Set(RB.man.states.map(x => x.state)); RB_ST = RB_ST.filter(k => have.has(k)); if(!RB_ST.includes(RB.st)) RB.st = RB_ST[RB_ST.length - 1]; }
    if(!RB.stats) RB.stats = await load('flood2d_rb2/stats.json').catch(() => null);
    if(!RB.inr) RB.inr = await load('flood2d_rb2/inregion.json').catch(() => null);
    if(!RB.cat) RB.cat = await load('l2plus_catalog.json').catch(() => null);
    if(!RB.man || !RB.stats){ panel.innerHTML = wbViews() + `<div class="empty">${t('rb.noData')}</div>`; wbBindViews(); return; }
    const S1 = RB.stats.L1 || {};
    panel.innerHTML = wbViews() + `
      <div class="wbhead"><h2>${t('rb.title')}</h2>
        <div class="m">${t('rb.sub', {res: fmt(S1.res_m || 30, 0), tri: fmt(S1.n_tri || 0, 0), nodes: fmt(S1.n_nodes || 0, 0)})}</div></div>
      <div class="promise" style="margin-top:0"><div class="m">${mdb(t('rb.poNote'), true)}</div></div>
      <div class="wbsticky"><div class="wbbar" role="tablist" style="grid-template-columns:repeat(4,1fr)">${RB_ST.map(s =>
        `<button role="tab" data-rbs="${s}" aria-selected="${s === RB.st}" style="--c:${RB_COL[s]}"><b>${esc(RB_LAB[s])}</b><small>${t('rb.d' + s)}</small></button>`).join('')}</div>
        <div class="wbbase"><label class="m">${t('wb.base')} <select id="rbbase"><option value="">${t('rb.parent')}</option>${RB_ST.map(s => `<option value="${s}"${s === RB.base ? ' selected' : ''}>${esc(RB_LAB[s])}</option>`).join('')}</select></label>
          <span class="m" id="rbdesc"></span></div></div>
      <div class="wbk" id="rbkpi"></div>
      <h3 class="wbh">${t('wb.hPlay')}</h3>
      <div class="wbpl"><button id="rbplay" class="f2btn" aria-label="${t('wb.play')}">▶</button><input id="rbrng" type="range" min="0" max="1" value="0" step="1" aria-label="${t('wb.time')}"></div>
      <div id="rbnow" class="f2now"></div>
      <svg id="rbch" class="wbch" viewBox="0 0 340 150" role="img" aria-label="${t('pt.axA')}"></svg>
      <h3 class="wbh">${t('rb.hFac')}</h3><div id="rbfac"></div>
      <details class="sec wbsec" open><summary>${t('pt.hNotes')}</summary><div class="body">
        ${(F(RB.cat?.regions?.['29.RB2'], 'notes') || []).map(n => `<div class="m">${esc(n)}</div>`).join('')}
        <div class="m">${esc(t('rb.depthNote'))}</div></div></details>`;
    wbBindViews();
    panel.querySelectorAll('.wbbar [data-rbs]').forEach(b => b.onclick = () => rbSelect(b.dataset.rbs));
    const bs = document.getElementById('rbbase'); if(bs) bs.onchange = () => { RB.base = bs.value || null; rbSelect(RB.st); };
    const pb = document.getElementById('rbplay'); if(pb) pb.onclick = rbPlay;
    const rg = document.getElementById('rbrng'); if(rg) rg.oninput = () => { rbStop(); const s = rbFr(); if(s){ RB.t = s.frames[+rg.value].t_h; rbDraw(); } };
    rbSelect(RB.st, true);
  }

  // ================= C1 热点群：L1 / L2 / L2+planned / L2+suggested × 61 / 96 mm（2026-09-29） =================
  // 数据：tools/build_c1*.py → couple2way_v2.py → c1_post.py（console/data/flood2d_c1_r{61,96}/manifest.json、stats.json、c1_stats.json、diff_*.png）
  // 口径：形心水深，积水面积按全程最大水深 ≥0.10 / ≥0.30 m；热点汇水区 = 地表（D8）+ 管网汇水区；「八个热点」是并集，不是相加。
  const C1_ST = ['L1', 'L2', 'L2P', 'L2S', 'L2S3'];
  const C1_LAB = {L1: 'L1', L2: 'L2', L2P: 'L2+planned', L2S: 'L2+suggested', L2S3: 'L2+suggested v3'};
  const C1_SCOL = {L1: '#334155', L2: '#0e7490', L2P: '#b45309', L2S: '#7c3aed', L2S3: '#15803d'};
  const C1_PARENT = {L2: 'L1', L2P: 'L2', L2S: 'L2P', L2S3: 'L2P'};
  // 15 m 各雨型（c1_run_15m.py，2026-10-08 起服务器 2 / 3 跑）：情景名 M<雨量 mm>H<历时 h>，数据目录 flood2d_c1_<小写情景名>，雨型 / 热点过程线的键 = 情景名
  const C1_RS = ['61', '96', 'M61', 'M61G5', 'M33H1', 'M96H1', 'M33H2', 'M33H24', 'M61H1', 'M61H24', 'M96H2', 'M96H24', 'U61', 'D24'];
  const c1MH = r => /^M(\d+)H(\d+)$/.exec(r);                 // M61H1 → ['M61H1', '61', '1']
  const C1V = {r: C1_RS.includes(U.get('r')) ? U.get('r') : '61', st: C1_ST.includes(U.get('st')) ? U.get('st') : null, base: null,
               t: null, timer: null, speed: 320, mode: 'frame', cache: {}, man: null, stats: null, cs: null, lay: null};
  // 无有效结果的情景（2026-10-02 用户定）：D24 的设计暴雨那次用的是**原计算域**（8 个热点汇水区外扩 300 m，42.8 km²），
  // 不是以此为准的模型版计算域（38.1 km²），范围不对；而模型版下没算过设计暴雨。情景按钮保留，但不显示那批作废的结果。
  const C1_VOID = {D24: 'c1v.voidD24'};
  const c1Dir = () => ['U61', 'D24'].includes(C1V.r) || C1V.r.startsWith('M') ? 'flood2d_c1_' + C1V.r.toLowerCase() : `flood2d_c1_r${C1V.r}`;
  const c1Dur = () => C1V.r === 'D24' ? 24 : c1MH(C1V.r) ? +c1MH(C1V.r)[2] : 2;       // 雨历时 h；模拟时长 = 历时 + 4 h（参数 E17）
  const c1Mm = () => ['U61', 'M61', 'M61G5'].includes(C1V.r) ? '61' : c1MH(C1V.r) ? c1MH(C1V.r)[1] : C1V.r === 'D24' ? '61.34' : C1V.r;          // U61 = 客户单元版计算域（RB-3 + MBZ + ADGC-10），雨同 61 mm
  function c1Stop(){ if(C1V.timer){ clearInterval(C1V.timer); C1V.timer = null; } const b = document.getElementById('c1play'); if(b) b.textContent = '▶'; }
  function c1Clear(){ c1Stop(); if(C1V.lay){ Object.values(C1V.lay).forEach(l => { if(l) map.removeLayer(l); }); C1V.lay = null; }
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = ''; }
  function c1Fr(){ return (C1V.man?.states || []).find(x => x.state === C1V.st) || null; }
  function c1Idx(){ const s = c1Fr(); if(!s) return 0; if(C1V.t == null) return s.peak_frame || 0;
    let b = 0; s.frames.forEach((f, i) => { if(Math.abs(f.t_h - C1V.t) < Math.abs(s.frames[b].t_h - C1V.t)) b = i; }); return b; }
  function c1Base(){ const have = new Set((C1V.man?.states || []).map(x => x.state)); const b = C1V.base || C1_PARENT[C1V.st]; return b && have.has(b) && b !== C1V.st ? b : null; }
  const c1Z = (zone, st) => C1V.cs?.zones?.[zone]?.[st] || null;
  function c1Delta(v, a){ if(a == null || v == null) return ''; const b = c1Base();
    if(!a){ return v ? `<span class="wbd bad">+${fmt(v, 1)} ${t('wb.vs')} ${esc(C1_LAB[b])}</span>` : `<span class="wbd">= ${esc(C1_LAB[b])}</span>`; }
    const p = (v - a) / Math.abs(a) * 100;
    return Math.abs(p) < 0.05 ? `<span class="wbd">= ${esc(C1_LAB[b])}</span>` : `<span class="wbd ${p < 0 ? 'good' : 'bad'}">${p > 0 ? '+' : ''}${p.toFixed(1)}% ${t('wb.vs')} ${esc(C1_LAB[b])}</span>`; }
  function c1Kpis(){
    const b = c1Base(), st = C1V.st;
    const K = [['C1 八个热点|catch', 'area_ge_0p30_ha', 'ha', 1, 'c1v.k30c'], ['C1 八个热点|200m', 'max_depth_m', 'm', 2, 'c1v.kDep200'],
               ['C1 八个热点|catch', 'peak_vol_m3', 'm³', 0, 'c1v.kVolc'], ['全域|served', 'area_ge_0p30_ha', 'ha', 1, 'c1v.k30s']];
    return K.map(([z, k, u, d, lab]) => { const v = c1Z(z, st)?.[k], a = b ? c1Z(z, b)?.[k] : null;
      return `<div class="wbkc"><span class="lab">${t(lab)}</span><b>${v == null ? '—' : fmt(v, d)}<small>${u}</small></b>${b ? c1Delta(v, a) : ''}</div>`; }).join('');
  }
  function c1HsTable(){
    const st = C1V.st, b = c1Base(), ids = Object.keys(C1V.cs?.zones || {}).filter(k => k.endsWith('|catch') && k.startsWith('ADM-')).map(k => k.split('|')[0]);
    const cell = (v, a, d) => { if(v == null) return '—'; if(a == null) return fmt(v, d); const dd = v - a;
      return `${fmt(v, d)}${Math.abs(dd) >= Math.pow(10, -d) ? ` <span style="color:${dd < 0 ? '#15803d' : '#b91c1c'}">${dd > 0 ? '+' : ''}${fmt(dd, d)}</span>` : ''}`; };
    return `<table class="wbft"><tr><th>${t('c1v.hs')}</th><th class="num">${t('c1v.c30')}</th><th class="num">${t('c1v.cDep')}</th><th class="num">${t('c1v.cVol')}</th></tr>`
      + ids.map(h => { const c = c1Z(h + '|catch', st) || {}, cb = b ? c1Z(h + '|catch', b) || {} : {}, n = c1Z(h + '|200m', st) || {}, nb = b ? c1Z(h + '|200m', b) || {} : {};
        return `<tr><td><i class="wbdot" style="background:${C1_COL[h] || '#64748b'}"></i><b>${esc(h)}</b></td>`
          + `<td class="num">${cell(c.area_ge_0p30_ha, b ? cb.area_ge_0p30_ha : null, 1)}</td><td class="num">${cell(n.max_depth_m, b ? nb.max_depth_m : null, 2)}</td>`
          + `<td class="num">${cell(c.peak_vol_m3, b ? cb.peak_vol_m3 : null, 0)}</td></tr>`; }).join('')
      + `</table><div class="m">${t('c1v.hsNote')}</div>`;
  }
  const c1Chain = st => st === 'L2S3' ? ['L1', 'L2P', 'L2S3'] : st === 'L2S' ? ['L1', 'L2P', 'L2S'] : st === 'L2P' ? ['L1', 'L2P'] : st === 'L2' ? ['L1', 'L2'] : ['L1'];
  const c1FacName = k => { const v = t('c1v.f_' + k); return v === 'c1v.f_' + k ? k : v; };
  // 设施符号（2026-09-29 用户：各类设施要能一眼分开）：形状、颜色分类型，泵的大小随流量；虚线 = 排水去向；点开看设计参数 + 当前态的实际表现
  const C1_FK = {ps_l1: {sh: 'sq', c: '#334155'}, mobile: {sh: 'tri', c: '#0891b2'}, ps_planned: {sh: 'sq', c: '#c2410c'}, pond: {sh: 'pond', c: '#2563eb'},
                 conv: {sh: 'dia', c: '#7c3aed'}, sump: {sh: 'cir', c: '#db2777'}, box: {sh: 'rect', c: '#57534e'}, bund: {sh: 'rect', c: '#a16207'},
                 tank: {sh: 'rect', c: '#0f766e'}};     // tank = 地下调蓄池（RD-A v3，2026-10-24）
  const C1_FORDER = ['ps_l1', 'mobile', 'ps_planned', 'pond', 'tank', 'conv', 'sump', 'box', 'bund'];
  function c1Sym(kind, z){
    const k = C1_FK[kind] || {sh: 'cir', c: '#64748b'}, c = k.c, h = z / 2, w = 'stroke="#fff" stroke-width="1.6"';
    let g;
    if(k.sh === 'sq') g = `<rect x="1.5" y="1.5" width="${z - 3}" height="${z - 3}" rx="2" fill="${c}" ${w}/><path d="M${h - z * .18} ${h + z * .14}L${h} ${h - z * .18}L${h + z * .18} ${h + z * .14}" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>`;
    else if(k.sh === 'tri') g = `<path d="M${h} 1.5L${z - 1.5} ${z - 2}L1.5 ${z - 2}Z" fill="${c}" ${w} stroke-linejoin="round"/>`;
    else if(k.sh === 'dia') g = `<path d="M${h} 1L${z - 1} ${h}L${h} ${z - 1}L1 ${h}Z" fill="${c}" ${w}/><path d="M${h - z * .2} ${h}H${h + z * .18}M${h + z * .05} ${h - z * .13}L${h + z * .2} ${h}L${h + z * .05} ${h + z * .13}" stroke="#fff" stroke-width="1.5" fill="none"/>`;
    else if(k.sh === 'rect') g = `<rect x="1" y="${z * .22}" width="${z - 2}" height="${z * .56}" rx="1.5" fill="${c}" ${w}/>`;
    else if(k.sh === 'pond') g = `<rect x="1.5" y="${z * .18}" width="${z - 3}" height="${z * .64}" rx="${z * .3}" fill="${c}" fill-opacity=".35" stroke="${c}" stroke-width="1.6"/><path d="M${z * .25} ${h}q${z * .125} -${z * .14} ${z * .25} 0t${z * .25} 0" fill="none" stroke="${c}" stroke-width="1.3"/>`;
    else g = `<circle cx="${h}" cy="${h}" r="${h - 1.5}" fill="${c}" ${w}/><circle cx="${h}" cy="${h}" r="${z * .14}" fill="#fff"/>`;
    return `<svg width="${z}" height="${z}" viewBox="0 0 ${z} ${z}" aria-hidden="true" style="overflow:visible;vertical-align:middle;flex:none">${g}</svg>`;
  }
  const c1SymSize = f => f.kind === 'pond' ? 16 : f.kind === 'box' ? Math.max(14, Math.min(24, 10 + Math.sqrt(f.vol_m3 || 0) / 3))
    : Math.max(12, Math.min(28, 10 + 3.2 * Math.sqrt((f.q_lps || 0) / 50)));
  const C1_PU = {n: [0, ''], q_each: [0, 'L/s'], q_lps: [0, 'L/s'], on: [2, 'm'], off: [2, 'm'], well: [2, 'm'], suction: [0, 'm'], path: [0, 'm'], hsd: [0, 'm'],
                 vol: [0, 'm³'], area: [2, 'ha'], patch: [2, 'ha'], depth: [2, 'm'], floor: [2, 'm'], peak: [0, 'm³'],
                 vol_m3: [0, 'm³'], util_pct: [1, '%'], starts: [0, ''], fill_pct: [1, '%'], end_m3: [0, 'm³'], surface_m3: [0, 'm³'], pumped_in_m3: [0, 'm³'],
                 overflow_m3: [0, 'm³'], max_m3: [0, 'm³'], max_pct: [1, '%']};
  const c1PV = (k, v) => { const u = C1_PU[k]; return u && typeof v === 'number' ? `${fmt(v, u[0])}${u[1] ? ' ' + u[1] : ''}` : esc(String(v)); };
  function c1FacPop(f, s){
    const k = C1_FK[f.kind] || {c: '#64748b'}, P = f.p || {}, Sm = (f.sim || {})[C1V.st];
    const row = (lab, val) => `<tr><td style="color:#66717f;padding:1px 10px 1px 0;white-space:nowrap;vertical-align:top">${lab}</td><td style="padding:1px 0">${val}</td></tr>`;
    let h = `<div style="min-width:240px;max-width:340px;font-size:12px"><div style="display:flex;gap:6px;align-items:center;margin-bottom:4px">${c1Sym(f.kind, 16)}<b style="font-size:13px">${esc(f.id)}</b>`
      + `<span style="color:${k.c}">${esc(c1FacName(f.kind))}</span><span class="m" style="margin-left:auto">${esc(C1_LAB[s])}</span></div><table style="border-collapse:collapse">`;
    if(f.q_lps && f.kind !== 'ps_planned') h += row(t('c1v.p_q_lps'), `<b>${c1PV('q_lps', f.q_lps)}</b>`);
    Object.entries(P).forEach(([key, v]) => { if(v == null || v === '' || key === 'why' || key.endsWith('_en')) return; h += row(t('c1v.p_' + key), c1PV(key, typeof v === 'string' ? F(P, key) : v)); });
    h += `</table>`;
    if(P.why) h += `<div class="m" style="margin-top:4px;line-height:1.45">${esc(F(P, 'why'))}</div>`;
    h += `<div style="margin-top:6px;border-top:1px solid #e3e7ec;padding-top:4px"><b>${t('c1v.simH', {st: C1_LAB[C1V.st]})}</b>`;
    h += Sm ? `<table style="border-collapse:collapse">${Object.entries(Sm).map(([key, v]) => row(t('c1v.m_' + key), c1PV(key, v))).join('')}</table>` : `<div class="m">${t('c1v.simNone')}</div>`;
    return h + `</div></div>`;
  }
  function c1FacList(){
    const F = C1V.cs?.facilities || {};
    const rows = c1Chain(C1V.st).map(s => { const fs = F[s] || []; if(!fs.length) return '';
      const by = {}; fs.forEach(f => { (by[f.kind] = by[f.kind] || {n: 0, q: 0, v: 0}); by[f.kind].n++; by[f.kind].q += f.q_lps || 0; by[f.kind].v += f.vol_m3 || 0; });
      return `<div class="m" style="margin:6px 0 2px"><b style="color:${C1_SCOL[s]}">${esc(C1_LAB[s])}</b></div>`
        + C1_FORDER.filter(k => by[k]).map(k => { const o = by[k];
          return `<div style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin:3px 0 3px 4px">${c1Sym(k, 15)}<span>${esc(c1FacName(k))}</span>`
            + `<span class="m" style="margin-left:auto;font-variant-numeric:tabular-nums">${o.n}${o.q ? ` · ${fmt(o.q, 0)} L/s` : ''}${o.v ? ` · ${fmt(o.v, 0)} m³` : ''}</span></div>`; }).join(''); }).join('');
    return rows + `<div class="m" style="margin-top:6px">${t('c1v.facNote')}</div>`;
  }
  function c1Map(){
    if(!C1V.lay) C1V.lay = {};
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = 'grayscale(.85) opacity(.7)';
    if(!C1V.lay.dom && C1V.stats?.domain_wgs) C1V.lay.dom = L.polygon(C1V.stats.domain_wgs, {color: '#0f172a', weight: 2, fill: false, dashArray: '8,5', interactive: false}).addTo(map);
    if(C1V.lay.fac){ map.removeLayer(C1V.lay.fac); C1V.lay.fac = null; }
    const F = C1V.cs?.facilities || {}, g = L.featureGroup(), pts = [];
    c1Chain(C1V.st).forEach(s => (F[s] || []).forEach(f => { if(!f.ll) return; const c = (C1_FK[f.kind] || {c: '#64748b'}).c;
      if(f.kind === 'pond' && f.poly) L.polygon(f.poly, {color: c, weight: 1.6, fillColor: c, fillOpacity: .22}).bindPopup(() => c1FacPop(f, s), {maxWidth: 360}).addTo(g);
      if(f.line) L.polyline(f.line, {color: c, weight: 3.5, opacity: .9}).addTo(g);
      if(f.to_ll){ L.polyline([f.ll, f.to_ll], {color: c, weight: 1.8, opacity: .85, dashArray: '5,4', interactive: false}).addTo(g);
        L.circleMarker(f.to_ll, {radius: 2.6, color: c, weight: 1, fillColor: '#fff', fillOpacity: 1, interactive: false}).addTo(g); }
      pts.push([f, s]); }));
    // 符号画在线和面上面；大的先画、小的后画，免得被盖住
    pts.sort((a, b) => c1SymSize(b[0]) - c1SymSize(a[0])).forEach(([f, s]) => { const z = Math.round(c1SymSize(f));
      L.marker(f.ll, {icon: L.divIcon({className: 'c1sym', html: c1Sym(f.kind, z), iconSize: [z, z], iconAnchor: [z / 2, z / 2]}), keyboard: false, riseOnHover: true})
        .bindTooltip(`<b>${esc(f.id)}</b> · ${esc(c1FacName(f.kind))}${f.q_lps ? ` · ${fmt(f.q_lps, 0)} L/s` : ''}${f.vol_m3 ? ` · ${fmt(f.vol_m3, 0)} m³` : ''} <span class="m">· ${esc(C1_LAB[s])}</span>`, {direction: 'top', offset: [0, -z / 2]})
        .bindPopup(() => c1FacPop(f, s), {maxWidth: 360}).addTo(g); });
    C1V.lay.fac = g.addTo(map);
  }
  function c1Chart(){
    const el = document.getElementById('c1ch'); if(!el || !C1V.man) return;
    const X0 = 38, X1 = 332, Y0 = 20, Y1 = 124, all = C1V.man.states;
    const TM = Math.max(...all.map(s => s.frames[s.frames.length - 1].t_h));
    const AM = Math.max(50, Math.ceil(Math.max(...all.flatMap(s => s.frames.map(f => f.wet10_ha || 0))) / 100) * 100);
    const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / AM;
    let h = '';
    for(let a = 0; a <= AM + 1e-9; a += AM / 4) h += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a, 0)}</text>`;
    for(let v = 0; v <= TM + 1e-9; v += 1) h += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    h += `<text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">${t('wb.axT')}</text><text x="2" y="9" font-size="9" fill="#66717f">${t('pt.axA')}</text>`;
    all.forEach(s => { const on = s.state === C1V.st, bs = s.state === c1Base();
      h += `<polyline points="${s.frames.map(f => `${sx(f.t_h).toFixed(1)},${sy(f.wet10_ha || 0).toFixed(1)}`).join(' ')}" fill="none" stroke="${C1_SCOL[s.state]}" stroke-width="${on ? 2.6 : bs ? 1.8 : 1}" opacity="${on || bs ? 1 : .35}"/>`; });
    const s = c1Fr(), f = s ? s.frames[c1Idx()] : null;
    if(f && C1V.mode === 'frame') h += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/><circle cx="${sx(f.t_h)}" cy="${sy(f.wet10_ha || 0)}" r="4" fill="${C1_SCOL[C1V.st]}" stroke="#fff" stroke-width="1.5"/>`;
    el.innerHTML = h;
  }
  // 雨型图（console/data/c1_hyeto.json，tools/c1_hyetograph.py）：柱 = 时段雨强，线 = 累计雨量，虚线 = 累计净雨；竖线跟着当前帧
  function c1Rain(){
    const el = document.getElementById('c1rain'), h = C1V.hy?.[C1V.r === 'D24' || c1MH(C1V.r) ? C1V.r : 'R' + c1Mm()]; if(!el) return;
    if(!h){ el.innerHTML = ''; return; }
    const X0 = 38, X1 = 312, Y0 = 16, Y1 = 96, TM = h.sim_h, dt = h.dt_min / 60;
    const PM = Math.ceil(Math.max(...h.p_mmph) / 100) * 100, CM = Math.ceil(h.rain_mm / 20) * 20;
    const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / PM, sc = v => Y1 - (Y1 - Y0) * v / CM;
    let g = '';
    for(let a = 0; a <= PM + 1e-9; a += PM / 2) g += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a, 0)}</text>`;
    for(let a = 0; a <= CM + 1e-9; a += CM / 2) g += `<text x="${X1 + 4}" y="${sc(a) + 3.5}" font-size="9" fill="#1f5f96">${fmt(a, 0)}</text>`;
    for(let v = 0; v <= TM + 1e-9; v += 1) g += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    const bw = Math.max((X1 - X0) * dt / TM - .6, .8);
    h.t_h.forEach((a, i) => { const p = h.p_mmph[i]; if(p > 0) g += `<rect x="${sx(a).toFixed(1)}" y="${sy(p).toFixed(1)}" width="${bw.toFixed(1)}" height="${(Y1 - sy(p)).toFixed(1)}" fill="#b07a1c"><title>${a.toFixed(2)}–${(a + dt).toFixed(2)} h · ${fmt(p, 1)} mm/h</title></rect>`; });
    const pl = arr => `${sx(0)},${sc(0)} ` + h.t_h.map((a, i) => `${sx(a + dt).toFixed(1)},${sc(arr[i]).toFixed(1)}`).join(' ');
    g += `<polyline points="${pl(h.p_cum_mm)}" fill="none" stroke="#1f5f96" stroke-width="1.8"/><polyline points="${pl(h.exc_cum_mm)}" fill="none" stroke="#1f5f96" stroke-width="1.3" stroke-dasharray="4 3"/>`;
    g += `<text x="2" y="9" font-size="9" fill="#66717f">${t('c1v.rainAx')}</text><text x="${X1 + 26}" y="9" text-anchor="end" font-size="9" fill="#1f5f96">${t('c1v.rainCum')}</text><text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">${t('wb.axT')}</text>`;
    const s = c1Fr(), f = s ? s.frames[c1Idx()] : null;
    if(f && C1V.mode === 'frame') g += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`;
    el.innerHTML = g;
    const cp = document.getElementById('c1rainc');
    if(cp) cp.textContent = t('c1v.rainCap', {r: fmt(h.rain_mm, 0), d: h.dur_h, k: h.dt_min, pk: fmt(h.peak_mmph, 0), t0: h.peak_t_h.toFixed(2), t1: (h.peak_t_h + dt).toFixed(2), ex: fmt(h.excess_mm, 1), src: F(h, 'source')});
  }
  // 客户最关注的 3 个热点（ADM-94 / 487 / 192）：50 m 内最深水深的过程线，各态一条、当前态加粗，竖线跟着当前帧（console/data/c1_hs_series.json，tools/c1_hs_series.py）
  const C1_HS3 = ['ADM-94', 'ADM-487', 'ADM-192'];
  function c1Hs3(){
    const el = document.getElementById('c1hs3'), D = C1V.hs3?.[['U61', 'M61', 'M61G5', 'D24'].includes(C1V.r) || c1MH(C1V.r) ? C1V.r : 'R' + C1V.r]; if(!el) return;
    if(!D){ el.innerHTML = `<div class="m">${t('c1v.noData')}</div>`; return; }
    const tt = D.t_h, ym = Math.max(0.6, Math.ceil(Math.max(...C1_HS3.flatMap(h => Object.values(D[h] || {}).flatMap(e => e.r50))) * 11) / 10);
    const TM = Math.max(6, Math.ceil(tt[tt.length - 1] || 0)), TS = TM > 12 ? 4 : 1;   // 横轴 h：24 h 雨模拟 28 h
    const X0 = 30, X1 = 332, Y0 = 18, Y1 = 92, sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * Math.min(v, ym) / ym;
    const s = c1Fr(), f = s ? s.frames[c1Idx()] : null;
    el.innerHTML = C1_HS3.filter(h => D[h]).map(h => {
      let g = '';
      for(let a = 0; a <= ym + 1e-9; a += ym > 0.6 ? 0.2 : 0.1) g += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 4}" y="${sy(a) + 3}" text-anchor="end" font-size="9" fill="#66717f">${a.toFixed(1)}</text>`;
      for(let v = 0; v <= TM; v += TS) g += `<text x="${sx(v)}" y="${Y1 + 11}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
      g += `<line x1="${X0}" x2="${X1}" y1="${sy(.3)}" y2="${sy(.3)}" stroke="#b91c1c" stroke-dasharray="4 3"/><text x="${X1}" y="${sy(.3) - 3}" text-anchor="end" font-size="9" fill="#b91c1c">0.30 m</text>`;
      const sts = C1_ST.filter(x => D[h][x]).sort((a, b) => (a === C1V.st) - (b === C1V.st));
      sts.forEach(x => { const on = x === C1V.st;
        g += `<polyline points="${D[h][x].r50.map((v, i) => `${sx(tt[i]).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${C1_SCOL[x]}" stroke-width="${on ? 2.6 : 1.2}" opacity="${on ? 1 : .45}"/>`; });
      if(f && C1V.mode === 'frame'){ const i = tt.findIndex(v => Math.abs(v - f.t_h) < 1e-3); const e = D[h][C1V.st];
        g += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`;
        if(e && i >= 0) g += `<circle cx="${sx(f.t_h)}" cy="${sy(e.r50[i])}" r="3.5" fill="${C1_SCOL[C1V.st]}" stroke="#fff" stroke-width="1.3"/>`; }
      const e = D[h][C1V.st] || {}, b = D[h][c1Base() || 'L1'] || {};
      const dd = e.peak_m != null && b.peak_m != null && c1Base() ? e.peak_m - b.peak_m : null;
      g += `<text x="${X0}" y="10" font-size="10" font-weight="600" fill="#1b2430">${esc(h)}</text><text x="${X1}" y="10" text-anchor="end" font-size="9.5" fill="#44505c">${t('c1v.hs3Sum', {pk: fmt(e.peak_m, 2), h30: fmt(e.h_ge30, 1), end: fmt(e.end_m, 2)})}${dd != null && Math.abs(dd) >= .005 ? ` (${dd > 0 ? '+' : ''}${dd.toFixed(2)})` : ''}</text>`;
      return `<svg class="wbch" viewBox="0 0 340 106" role="img" aria-label="${esc(h)}">${g}</svg>`; }).join('') + `<div class="m">${t('c1v.hs3Note')}</div>`;
  }
  function c1Draw(){
    const s = c1Fr(); if(!s) return;
    const i = c1Idx(), f = s.frames[i]; if(C1V.t == null) C1V.t = f.t_h;
    const b = c1Base(), dj = C1V.mode === 'diff' && b ? (C1V.cs?.diffs || []).find(d => d.a === b && d.b === C1V.st) : null;
    const url = 'data/' + c1Dir() + '/' + (dj ? dj.png : C1V.mode === 'max' ? s.max_png : f.png), bnd = dj ? dj.bounds : s.bounds;
    if(!C1V.lay) C1V.lay = {};
    if(!C1V.lay.water) C1V.lay.water = L.imageOverlay(url, bnd, {opacity: .82, interactive: false, zIndex: 450}).addTo(map);
    else { C1V.lay.water.setUrl(url); C1V.lay.water.setBounds(bnd); }
    const now = document.getElementById('c1now');
    if(now) now.innerHTML = dj ? `<b>${esc(C1_LAB[C1V.st])} − ${esc(C1_LAB[b])}</b>　${t('c1v.diffSub', {sh: fmt(dj.shallower_ha, 1), deep: fmt(dj.deeper_ha, 1)})}`
      : C1V.mode === 'diff' ? `<span class="m">${t('c1v.noDiff')}</span>`
      : C1V.mode === 'max' ? `<b>${t('pt.maxView')}</b>`
      : `<b>t = ${(+f.t_h).toFixed(2)} h</b>　${t('pt.now10')} <b>${fmt(+f.wet10_ha || 0, 0)}</b> ha　${t('f2.depth')} <b>${fmt(+f.dmax_m, 2)}</b> m`;
    const rg = document.getElementById('c1rng'); if(rg){ rg.max = String(s.frames.length - 1); rg.value = String(i); }
    ['frame', 'max', 'diff'].forEach(m => { const x = document.getElementById('c1m_' + m); if(x) x.classList.toggle('on', C1V.mode === m); });
    c1Chart(); c1Rain(); c1Hs3();
    try{ const u = new URL(location.href); u.searchParams.set('tab', 'line2p'); u.searchParams.set('v', 'c1'); u.searchParams.set('r', C1V.r); u.searchParams.set('st', C1V.st);
      history.replaceState(null, '', u); }catch(e){}
  }
  function c1Play(){ const s = c1Fr(); if(!s) return; if(C1V.timer){ c1Stop(); return; } C1V.mode = 'frame';
    if(c1Idx() >= s.frames.length - 1){ C1V.t = s.frames[0].t_h; c1Draw(); }
    const b = document.getElementById('c1play'); if(b) b.textContent = '❚❚';
    C1V.timer = setInterval(() => { const c = c1Fr(), k = c1Idx(); if(!c || k >= c.frames.length - 1){ c1Stop(); return; } C1V.t = c.frames[k + 1].t_h; c1Draw(); }, C1V.speed); }
  function c1Select(st, first){
    c1Stop(); C1V.st = st;
    panel.querySelectorAll('.wbbar [data-c1s]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.c1s === st)));
    const k = document.getElementById('c1kpi'); if(k) k.innerHTML = c1Kpis();
    const ht = document.getElementById('c1hs'); if(ht) ht.innerHTML = c1HsTable();
    const fl = document.getElementById('c1fac'); if(fl) fl.innerHTML = c1FacList();
    const ds = document.getElementById('c1desc'); if(ds) ds.textContent = t('c1v.d' + st);
    c1Map(); c1Draw();
    if(first){ window._viewOwned = true; const fit = () => { try{ map.invalidateSize(); map.fitBounds(C1V.lay.dom ? C1V.lay.dom.getBounds().pad(.02) : L.latLngBounds(c1Fr().bounds)); }catch(e){} };
      fit(); setTimeout(fit, 120); setTimeout(fit, 780); }
  }
  function c1BindR(){ panel.querySelectorAll('[data-c1r]').forEach(b => b.onclick = () => { if(C1V.r === b.dataset.c1r) return; C1V.r = b.dataset.c1r; c1Clear(); renderC1(); }); }
  async function renderC1(){
    wbClear(); plClear(); cyClear(); f2Clear(); rbClear(); r1Clear();
    const lg = document.getElementById('legend'); if(lg){ lg.innerHTML = ''; lg.style.display = 'none'; }
    const C = C1V.cache[C1V.r] = C1V.cache[C1V.r] || {};
    const segOnly = `<div class="seg" role="tablist" style="margin:6px 0 4px">${['61', '96', 'M61', 'U61', 'D24'].map(r => `<button role="tab" data-c1r="${r}" class="${C1V.r === r ? 'on' : ''}" aria-selected="${C1V.r === r}">${r === 'U61' ? t('c1v.u61') : r === 'M61' ? t('c1v.m61') : r === 'D24' ? t('c1v.d24') : r + ' mm / 2 h'}</button>`).join('')}</div>`;
    if(C1_VOID[C1V.r]){                                         // 无有效结果的情景：只留按钮与说明，不摆那批作废的图与数字
      panel.innerHTML = wbViews() + segOnly + `<div class="empty">${mdb(t(C1_VOID[C1V.r]), true)}</div>`;
      wbBindViews(); c1BindR(); return;
    }
    if(!C.man) C.man = await load(c1Dir() + '/manifest.json').catch(() => null);
    if(!C.stats) C.stats = await load(c1Dir() + '/stats.json').catch(() => null);
    if(!C.cs) C.cs = await load(c1Dir() + '/c1_stats.json').catch(() => null);
    C1V.man = C.man; C1V.stats = C.stats; C1V.cs = C.cs;
    if(C1V.hy === undefined) C1V.hy = await load('c1_hyeto.json').catch(() => null);
    if(C1V.hs3 === undefined) C1V.hs3 = await load('c1_hs_series.json').catch(() => null);
    const seg = `<div class="seg" role="tablist" style="margin:6px 0 4px;flex-wrap:wrap">${C1_RS.map(r => `<button role="tab" data-c1r="${r}" class="${C1V.r === r ? 'on' : ''}" aria-selected="${C1V.r === r}">${r === 'U61' ? t('c1v.u61') : r === 'M61' ? t('c1v.m61') : r === 'M61G5' ? t('c1v.m61g5') : r === 'M33H1' ? t('c1v.m33h1') : r === 'M96H1' ? t('c1v.m96h1') : r === 'D24' ? t('c1v.d24') : c1MH(r) ? t('c1v.mh', {r: c1MH(r)[1], d: c1MH(r)[2]}) : r + ' mm / 2 h'}</button>`).join('')}</div>`;
    if(!C1V.man || !C1V.stats || !C1V.cs){ panel.innerHTML = wbViews() + seg + `<div class="empty">${t('c1v.noData')}</div>`; wbBindViews(); c1BindR(); return; }
    const have = C1V.man.states.map(x => x.state);
    if(!C1V.st || !have.includes(C1V.st)) C1V.st = have[have.length - 1];
    const S1 = C1V.stats.L1 || {};
    panel.innerHTML = wbViews() + seg + `
      <div class="wbhead"><h2>${t('c1v.title')}</h2>
        <div class="m">${C1V.r === 'U61' ? t('c1v.u61sub') + ' · ' : C1V.r === 'M61' || C1V.r === 'M61G5' || c1MH(C1V.r) ? t('c1v.m61sub') + ' · ' : C1V.r === 'D24' ? t('c1v.d24sub') + ' · ' : ''}${t('c1v.sub', {r: c1Mm(), dur: c1Dur(), sim: c1Dur() + 4, tri: fmt(S1.n_tri || 0, 0), nodes: fmt(S1.n_nodes || 0, 0)})}</div></div>
      <div class="wbsticky"><div class="wbbar" role="tablist" style="grid-template-columns:repeat(${Math.max(have.length, 2)},1fr)">${have.map(s =>
        `<button role="tab" data-c1s="${s}" aria-selected="${s === C1V.st}" style="--c:${C1_SCOL[s]}"><b>${esc(C1_LAB[s])}</b><small>${t('c1v.s' + s)}</small></button>`).join('')}</div>
        <div class="wbbase"><label class="m">${t('wb.base')} <select id="c1base"><option value="">${t('c1v.parent')}</option>${have.map(s => `<option value="${s}"${s === C1V.base ? ' selected' : ''}>${esc(C1_LAB[s])}</option>`).join('')}</select></label>
          <span class="m" id="c1desc"></span></div></div>
      <div class="wbk" id="c1kpi"></div>
      <h3 class="wbh">${t('wb.hPlay')}</h3>
      <div class="wbpl"><button id="c1play" class="f2btn" aria-label="${t('wb.play')}">▶</button><input id="c1rng" type="range" min="0" max="1" value="0" step="1" aria-label="${t('wb.time')}"></div>
      <div id="c1now" class="f2now"></div>
      <div class="wbjump"><button class="chip" id="c1m_frame">${t('c1v.mFrame')}</button><button class="chip" id="c1m_max">${t('pt.showMax')}</button><button class="chip" id="c1m_diff">${t('c1v.mDiff')}</button></div>
      <svg id="c1ch" class="wbch" viewBox="0 0 340 150" role="img" aria-label="${t('pt.axA')}"></svg>
      <h3 class="wbh">${t('c1v.hRain')}</h3>
      <svg id="c1rain" class="wbch" viewBox="0 0 340 124" role="img" aria-label="${t('c1v.hRain')}"></svg><div id="c1rainc" class="m"></div>
      <h3 class="wbh">${t('c1v.hHs3')}</h3><div id="c1hs3"></div>
      <h3 class="wbh">${t('c1v.hHs')}</h3><div id="c1hs"></div>
      <h3 class="wbh">${t('c1v.hFac')}</h3><div id="c1fac"></div>
      <details class="sec wbsec"><summary>${t('pt.hNotes')}</summary><div class="body">${['n1', 'n2', 'n3', 'n4', 'n5', 'n6'].map(k => `<div class="m">${mdb(t('c1v.' + k), true)}</div>`).join('')}</div></details>`;
    wbBindViews(); c1BindR();
    panel.querySelectorAll('.wbbar [data-c1s]').forEach(b => b.onclick = () => c1Select(b.dataset.c1s));
    const bs = document.getElementById('c1base'); if(bs) bs.onchange = () => { C1V.base = bs.value || null; c1Select(C1V.st); };
    const pb = document.getElementById('c1play'); if(pb) pb.onclick = c1Play;
    const rg = document.getElementById('c1rng'); if(rg) rg.oninput = () => { c1Stop(); C1V.mode = 'frame'; const s = c1Fr(); if(s){ C1V.t = s.frames[+rg.value].t_h; c1Draw(); } };
    ['frame', 'max', 'diff'].forEach(m => { const x = document.getElementById('c1m_' + m); if(x) x.onclick = () => { c1Stop(); C1V.mode = m; c1Draw(); }; });
    c1Select(C1V.st, true);
  }

  // ================= RB-01 / RD-A 汇水区：L1 / L2 / L2+planned / L2+suggested =================
  // 数据：RB-01 = tools/rb01_build.py → couple2way_v2.py → pilot_frames.py（manifest / stats）+ rb01_console.py（rb01_stats.json）
  //       RD-A  = tools/rda_build.py → … → rda_console.py（每场雨一个目录 flood2d_rda_<雨>/，目录表 flood2d_rda/index.json），2026-10-21 起共用这套视图
  // 口径：形心水深；积水面积按全程最大水深 ≥0.10 / ≥0.30 m，不含塘占地；热点统计 = 热点 200 m 内。设施符号与参数框同 C1。
  const R1_REG = {
    rb01: {st: ['L1', 'L2', 'L2P', 'L2S4', 'L2S5'], lab: {L1: 'L1', L2: 'L2', L2P: 'L2+planned', L2S4: 'L2+suggested v4', L2S5: 'L2+suggested v5'},
           col: {L1: '#334155', L2: '#0e7490', L2P: '#b45309', L2S4: '#7c3aed', L2S5: '#15803d'}, parent: {L2: 'L1', L2P: 'L1', L2S4: 'L2P', L2S5: 'L2P'},
           k: 'r1', cs: 'rb01_stats.json', union: '六个热点|200m', def: 'L2S5',
           dir: () => 'flood2d_rb01_h61',                // 2026-10-19：60 mm 是笔误，改 61 mm 重算（H60 的数据留在 flood2d_rb01_h60/ 作历史）
           run: () => 'RB01_H61', report: () => 'data/flood2d_rb01_h61/report.html'},
    // 2026-10-24 中间版：v1（OPT20F）已作废只作历史；v3（OPTC）和 v3 + 地面泵站（OPTCS2）目前只有 96 mm 1 h / 24 h
    // 2026-10-28：最终方案 = 推荐方案 / 96 mm 方案 + 高后果积水点地面泵站（OPTA61PH2 / OPTCS3X），对比基准默认它的上一版
    rda:  {st: ['L1', 'L1F', 'L2', 'L2F', 'L2P', 'L2PF', 'OPT20F', 'OPTC', 'OPTCS2', 'OPTA61PS', 'OPTA61PH2', 'OPTA61PH2F', 'OPTCS3X'], lab: {L1: 'L1', L1F: 'L1F(新参数)', L2: 'L2', L2F: 'L2F(新参数)', L2P: 'L2+planned', L2PF: 'L2PF(新参数)', get OPT20F(){ return t('bi.v1void'); }, get OPTC(){ return t('bi.v3nopump'); }, OPTCS2: 'L2+suggested', get OPTA61PS(){ return t('bi.a61rec'); }, get OPTA61PH2(){ return t('bi.a61final'); }, OPTA61PH2F: 'OPTA61PH2F(新参数)', get OPTCS3X(){ return t('bi.c96final'); }},
           col: {L1: '#334155', L1F: '#1e293b', L2: '#0e7490', L2F: '#0c4a6e', L2P: '#b45309', L2PF: '#92400e', OPT20F: '#9ca3af', OPTC: '#7c3aed', OPTCS2: '#15803d', OPTA61PS: '#be185d', OPTA61PH2: '#831843', OPTA61PH2F: '#dc2626', OPTCS3X: '#14532d'},
           parent: {L2: 'L1', L1F: 'L1', L2F: 'L1F', L2P: 'L1', L2PF: 'L1F', OPT20F: 'L2P', OPTC: 'L2P', OPTCS2: 'L2P', OPTA61PS: 'L2P', OPTA61PH2: 'OPTA61PS', OPTA61PH2F: 'OPTA61PS', OPTCS3X: 'OPTCS2'},
           k: 'ra', cs: 'rda_stats.json', union: '热点合计|200m', def: 'OPTCS2',
           dir: () => 'flood2d_rda_' + String(R1.storm || 'S96D1').toLowerCase(),
           run: () => 'RDA_' + (R1.storm || 'S96D1'), report: () => 'data/flood2d_rda/report.html'}};
  const R1 = {reg: null, storm: /^[SA]\d+D\d+$/.test(U.get('storm') || '') ? U.get('storm') : 'S96D1', idx: null,
              st: null, base: null, t: null, timer: null, speed: 320, mode: 'frame', man: null, stats: null, cs: null, lay: null};
  let R1C = R1_REG.rb01, R1_ST = R1C.st, R1_LAB = R1C.lab, R1_COL = R1C.col, R1_PARENT = R1C.parent, R1K = R1C.k, R1_DIR = R1C.dir();
  function r1Use(reg){
    if(R1.reg !== reg){ r1Clear(); Object.assign(R1, {man: null, stats: null, cs: null, hsPts: null, base: null, t: null, l3: null, routes: undefined, tracks: undefined}); }
    R1.reg = reg; R1C = R1_REG[reg]; R1_ST = R1C.st; R1_LAB = R1C.lab; R1_COL = R1C.col; R1_PARENT = R1C.parent; R1K = R1C.k; R1_DIR = R1C.dir();
    if(!R1_ST.includes(R1.st)) R1.st = R1_ST.includes(U.get('st')) ? U.get('st') : R1C.def;
  }
  function r1Storm(st){ if(st === R1.storm) return; r1Clear(); Object.assign(R1, {storm: st, man: null, stats: null, cs: null, t: null, l3: null, routes: undefined, tracks: undefined}); R1TK.t = null; R1_DIR = R1C.dir(); renderRB01(R1.reg); }
  function r1Stop(){ if(R1.timer){ clearInterval(R1.timer); R1.timer = null; } const b = document.getElementById('r1play'); if(b) b.textContent = '▶'; }
  function r1Clear(){ r1Stop(); r1TkStop(); pdClear(); hcClear(); if(R1.lay){ Object.values(R1.lay).forEach(l => { if(l) map.removeLayer(l); }); R1.lay = null; }
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = ''; }
  const r1Fr = () => (R1.man?.states || []).find(x => x.state === R1.st) || null;
  function r1Idx(){ const s = r1Fr(); if(!s) return 0; if(R1.t == null) return s.peak_frame || 0;
    let b = 0; s.frames.forEach((f, i) => { if(Math.abs(f.t_h - R1.t) < Math.abs(s.frames[b].t_h - R1.t)) b = i; }); return b; }
  function r1Base(){ const have = new Set((R1.man?.states || []).map(x => x.state)); const b = R1.base || R1_PARENT[R1.st]; return b && have.has(b) && b !== R1.st ? b : null; }
  const r1Z = (z, st) => R1.cs?.zones?.[z]?.[st] || null;
  function r1Delta(v, a){ if(a == null || v == null) return ''; const b = r1Base();
    if(!a) return v ? `<span class="wbd bad">+${fmt(v, 1)} ${t('wb.vs')} ${esc(R1_LAB[b])}</span>` : `<span class="wbd">= ${esc(R1_LAB[b])}</span>`;
    const p = (v - a) / Math.abs(a) * 100;
    return Math.abs(p) < 0.05 ? `<span class="wbd">= ${esc(R1_LAB[b])}</span>` : `<span class="wbd ${p < 0 ? 'good' : 'bad'}">${p > 0 ? '+' : ''}${p.toFixed(1)}% ${t('wb.vs')} ${esc(R1_LAB[b])}</span>`; }
  function r1Kpis(){
    const b = r1Base(), st = R1.st;
    const K = [['全域|all', 'area_ge_0p30_ha', 'ha', 1, R1K + '.k30'], [R1C.union, 'area_ge_0p30_ha', 'ha', 2, R1K + '.k30h'],
               ['全域|all', 'peak_vol_m3', 'm³', 0, R1K + '.kVol'], [R1C.union, 'max_depth_m', 'm', 2, R1K + '.kDep']];
    return K.map(([z, k, u, d, lab]) => { const v = r1Z(z, st)?.[k], a = b ? r1Z(z, b)?.[k] : null;
      return `<div class="wbkc"><span class="lab">${t(lab)}</span><b>${v == null ? '—' : fmt(v, d)}<small>${u}</small></b>${b ? r1Delta(v, a) : ''}</div>`; }).join('');
  }
  function r1Cost(){              // RD-A：L2+suggested 各态的占位造价（rda_console.py 的 cost）
    const C = R1.cs?.cost?.[R1.st];
    if(!C) return `<div class="m">${t('ra.costNone')}</div>`;
    const M = v => fmt(v / 1e6, 2);
    return `<div class="wbk"><div class="wbkc"><span class="lab">${t('ra.costTot')}</span><b>${M(C.total_usd)}<small>M USD</small></b></div>`
      + `<div class="wbkc"><span class="lab">${t('ra.costN')}</span><b>${fmt(C.items.reduce((a, i) => a + i.n, 0), 0)}<small>${t('ra.costItems')}</small></b></div>`
      + (C.n_outfalls_new ? `<div class="wbkc"><span class="lab">${t('ra.costOf')}</span><b>${C.n_outfalls_new}<small>${t('ra.costItems')}</small></b></div>` : '') + `</div>`
      + `<table class="wbft"><tr><th>${t('ra.costKind')}</th><th class="num">${t('ra.costCnt')}</th><th class="num">M USD</th><th class="num">%</th></tr>`
      + C.items.map(i => `<tr title="${esc(F(i, 'basis'))}"><td>${esc(F(i, 'label'))}<div class="m">${esc(F(i, 'basis'))}</div></td><td class="num">${i.n}</td><td class="num">${M(i.usd)}</td>`
        + `<td class="num">${fmt(i.usd / C.total_usd * 100, 0)}</td></tr>`).join('') + `</table><div class="m">${esc(F(C, 'note'))}</div>`;
  }
  // RD-A 三道防线联动（tools/rda_threelines.py → flood2d_rda_<雨>/l3.json，2026-10-24）：
  // L1 → L2 → L2+planned → L2+suggested 各态的二维残余积水点，交给第三道防线的调度求解器（swbrain.dispatch2）派单；
  // 资源两种：仅 Island 片区驻地 A13（现状）/ ADM 全部驻地集中调度（上限）。L3 赶不上时限的点回流给 L2 下一轮优化。
  const R1TL = {pool: 'island', map: true};
  const TL_RES = {'自然退干': ['tlR_nat', '#16a34a'], 'L3 按时退干': ['tlR_ok', '#2563eb'], 'L3 已派、超时': ['tlR_late', '#f59e0b'], 'L3 未派': ['tlR_no', '#dc2626']};
  const TL_WHY = {'行程不可达（积水断路或路网缺段）': 'tlW_road', '1.5 km 内无排放去处，罐车不够': 'tlW_out', '班组 / 资源已占满或派了也赶不上，求解器未选': 'tlW_res'};
  const r1TLX = s => R1.l3?.states?.[s]?.dispatch?.[R1TL.pool] || null;
  function r1TL(){
    const D = R1.l3; if(!D) return `<div class="m">${t('ra.tlNoData')}</div>`;
    const sts = R1_ST.filter(s => D.states[s]), n = (R1.idx?.hotspots || []).length, cur = (R1.idx?.storms || []).find(x => x.storm === R1.storm) || {};
    let h = `<div data-stg="overview l3"><div class="m" style="margin-bottom:4px">${mdb(t('ra.tlIntro', {re: fmt(D.rain_end_h, 0)}), true)}</div>`
      + `<div class="wbjump">${['island', 'adm'].map(p => `<button class="chip${R1TL.pool === p ? ' on' : ''}" data-tlp="${p}">${t(p === 'island' ? 'ra.tlIsl' : 'ra.tlAdm')}</button>`).join('')}`
      + `<label class="m" style="margin-left:6px"><input type="checkbox" id="tlmap"${R1TL.map ? ' checked' : ''}> ${t('ra.tlMap')}</label></div>` + r1RtNote() + r1Tk();
    // ① 链条：每一级防线之后剩下什么、L3 接不接得住（阶段显隐：data-stg，见 index.html）
    h += `</div><div data-stg="overview l3 concl">`;
    h += `<div class="m" style="margin:6px 0 2px"><b>${t('ra.tlChain')}</b></div><div style="overflow-x:auto"><table class="wbft"><tr>${['tlSt', 'tl30', 'tlPass', 'tlCap', 'tlN', 'tlNatMon', 'tlL3', 'tlUnits', 'tlBeyond'].map((k, i) => `<th${i ? ' class="num"' : ''}>${t('ra.' + k)}</th>`).join('')}</tr>`
      + sts.map(s => { const S = D.states[s], X = r1TLX(s) || {}, c = X.counts || {}, z = r1Z('全域|all', s) || {}, cap = R1.cs?.cost?.[s]?.total_usd, nb = S.summary?.n_beyond || 0;
        return `<tr${s === R1.st ? ' style="background:#eef2f7;font-weight:600"' : ''}><td><b style="color:${R1_COL[s]}">${esc(R1_LAB[s])}</b></td><td class="num">${z.area_ge_0p30_ha == null ? '—' : fmt(z.area_ge_0p30_ha, 2)}</td>`
          + `<td class="num">${cur.eval?.[s] != null ? cur.eval[s] + '/' + n : '—'}</td><td class="num">${cap ? fmt(cap / 1e6, 1) : t(s === 'L1' ? 'ra.tlExist' : 'ra.tlPlan')}</td>`
          + `<td class="num">${S.summary?.n || 0}</td><td class="num"><span style="color:${TL_RES['自然退干'][1]}">${c['自然退干'] || 0}</span> / <span style="color:#94a3b8">${c['只监测'] || 0}</span></td>`
          + `<td class="num">${['L3 按时退干', 'L3 已派、超时', 'L3 未派'].map(k => `<span style="color:${TL_RES[k][1]}">${c[k] || 0}</span>`).join(' / ')}</td>`
          + `<td class="num">${X.pumps ?? 0} / ${X.tankers ?? 0}</td><td class="num"${nb ? ' style="color:#b91c1c"' : ''}>${nb}</td></tr>`; }).join('') + `</table></div>`;
    h += `</div>`;
    const S = D.states[R1.st], X = r1TLX(R1.st);
    if(!S || !X) return h + `<div class="m" style="margin-top:6px">${t('ra.tlNone')}</div>`;
    const P = Object.fromEntries(S.points.map(p => [p.id, p])), res = id => X.points[id] || {};
    const resCell = r => { const rr = TL_RES[r.result]; return r.result === '只监测' ? `<span class="m">${t('ra.tlR_mon')}</span>`
      : `<span style="color:${rr ? rr[1] : '#334155'}">${rr ? t('ra.' + rr[0]) : '—'}${r.units ? ` · ${t(r.result === '自然退干' ? 'ra.tlSpeed' : 'ra.tlUnitsN', {n: r.units})}` : ''}${r.done_h != null ? ` · ${fmt(r.done_h, 1)} h` : ''}</span>`; };
    const lvTag = lv => `<span style="display:inline-block;padding:0 5px;border-radius:3px;font-size:11px;color:#fff;background:${({'关键': '#b91c1c', '高': '#ea580c', '中': '#ca8a04'})[lv] || '#94a3b8'}">${t('ra.lv_' + ({'无暴露': 'none', '中': 'mid', '高': 'high', '关键': 'key'}[lv] || 'none'))}</span>`;
    h += `<div data-stg="overview l3 concl">`;
    // ② 主表：客户历史积水点——每个点在这一级防线下怎样、L3 怎么兜底
    const E = R1.cs?.eval?.[R1.st], Z = h_ => r1Z(h_ + '|200m', R1.st) || {}, Z1 = h_ => r1Z(h_ + '|200m', 'L1') || {};
    const order = {'L3 已派、超时': 0, 'L3 未派': 1, 'L3 按时退干': 2, '自然退干': 3, '只监测': 4};
    h += `<div class="m" style="margin:10px 0 2px"><b>${t('ra.tlHsHead', {n: Object.keys(D.hotspots || {}).length})}</b></div><div class="m">${t('ra.tlHsSub')}</div>`
      + `<div style="overflow-x:auto"><table class="wbft"><tr><th>${t('ra.tlHs')}</th><th class="num">${t('ra.tlHsDep')}</th><th>${t('ra.tlHsCrit')}</th><th>${t('ra.tlHsL1')}</th><th>${t('ra.tlHsL3')}</th><th>${t('ra.tlHsPlanA')}</th><th>${t('ra.tlHsVerdict')}</th></tr>`
      + Object.entries(D.hotspots || {}).map(([hid, hi]) => {
        const ids = S.hs_links?.[hid] || [], ev = E?.hs?.[hid], pass = ev ? ev.A && ev.B && ev.C && E.pass_D : null, z = Z(hid), z1 = Z1(hid);
        const worst = ids.map(i => res(i)).sort((a, b) => (order[a.result] ?? 9) - (order[b.result] ?? 9))[0];
        const sent = ids.reduce((a, i) => a + (res(i).units || 0), 0), pa = hi.planA || {};
        const repro = (z1.max_depth_m ?? 0) >= 0.3 ? t('ra.tlRepYes', {d: fmt(z1.max_depth_m, 2)}) : t('ra.tlRepNo', {d: fmt(z1.max_depth_m ?? 0, 2)});
        const verdict = pass ? t('ra.tlV_solved') : !ids.length ? t('ra.tlV_short') : worst?.result === '自然退干' ? t('ra.tlV_self')
          : worst?.result === 'L3 按时退干' ? t('ra.tlV_l3') : t('ra.tlV_back');
        const vc = pass || (worst && ['自然退干', 'L3 按时退干'].includes(worst.result)) || !ids.length ? '#15803d' : '#b91c1c';
        return `<tr><td>${pdCli(hid)}<div class="m">${esc(hi.criticality || '')} · ${t('ra.tlTgt', {h: fmt((hi.target_min || 360) / 60, 0)})}</div></td>`
          + `<td class="num">${fmt(z.max_depth_m, 2)}<div class="m">${t('ra.tlHs30', {a: fmt(z.area_ge_0p30_ha, 2)})}</div></td>`
          + `<td>${ev ? ['A', 'B', 'C'].map(k => `<span style="color:${ev[k] ? '#15803d' : '#b91c1c'}">${k}${ev[k] ? '✓' : '✗'}</span>`).join(' ') : '—'}</td>`
          + `<td class="m">${repro}</td>`
          + `<td>${ids.length ? `${worst ? resCell(worst) : '—'}<div class="m">${t('ra.tlLinked', {n: ids.length, s: sent})}</div>` : `<span class="m">${t('ra.tlNoPond')}</span>`}</td>`
          + `<td class="m">${pa.pumps != null ? t('ra.tlPlanA', {p: pa.pumps, k: pa.tankers ?? 0}) : '—'}</td>`
          + `<td style="color:${vc}">${verdict}</td></tr>`; }).join('') + `</table></div>`;
    h += `</div><div data-stg="l3">`;
    // ③ 副表：模型提示的其他积水点（不在客户清单上），按后果等级 × 严重程度排
    const linked = new Set(Object.values(S.hs_links || {}).flat());
    const other = S.points.filter(p => !linked.has(p.id)), act = other.filter(p => p.level !== '无暴露').sort((a, b) => (res(b.id).priority || 0) - (res(a.id).priority || 0));
    const mon = other.filter(p => p.level === '无暴露');
    h += `<div class="m" style="margin:10px 0 2px"><b>${t('ra.tlOthHead', {n: other.length})}</b></div><div class="m">${t('ra.tlOthSub')}</div>`;
    h += act.length ? `<div style="overflow-x:auto"><table class="wbft"><tr><th>${t('ra.tlPt')}</th><th>${t('ra.tlLv')}</th><th class="num">m³</th><th class="num">m</th><th>${t('ra.tlRes')}</th></tr>`
      + act.map(p => `<tr><td>${pdSim(p.id)}</td><td>${lvTag(p.level)}<div class="m">${esc(String(F(p, 'level_why') || '').replace(/(hospital|ambulance|tunnel|metro)/g, k => t('ra.k_' + k)))}</div></td><td class="num">${fmt(p.vol_peak_m3, 0)}</td><td class="num">${fmt(p.max_depth_m, 2)}</td><td>${resCell(res(p.id))}${String(p.verdict).startsWith('超出') ? `<div class="m" style="color:#b91c1c">${t('ra.tlBeyondShort')}</div>` : ''}</td></tr>`).join('')
      + `</table></div>` : `<div class="m">${t('ra.tlOthNone')}</div>`;
    if(mon.length) h += `<div class="m" style="margin-top:4px">${t('ra.tlMonLine', {n: mon.length, v: fmt(mon.reduce((a, p) => a + p.vol_peak_m3, 0), 0), d: fmt(Math.max(...mon.map(p => p.max_depth_m)), 2)})}</div>`;
    h += `</div><div data-stg="l3 concl">`;
    // ④ 资源与回流
    const why = {}; Object.values(X.points).forEach(p => { if(p.why) why[p.why] = (why[p.why] || 0) + 1; });
    const ut = Object.entries(X.util || {}).filter(([, u]) => u.units_used || u.tankers_used);
    h += `<div class="m" style="margin-top:8px">${X.t_decide_h != null ? t('ra.tlUtil', {d: fmt(X.t_decide_h, 2), c: fmt(X.cost_aed, 0)}) : ''} ${ut.map(([d, u]) => t('ra.tlDep', {d, u: u.units_used, ut: u.units_total, k: u.tankers_used, kt: u.tankers_total, c: u.crews_used, ct: u.crews_total})).join('；')}</div>`;
    if(Object.keys(why).length) h += `<div class="m">${t('ra.tlWhyHead')} ${Object.entries(why).map(([k, v]) => `${esc(TL_WHY[k] ? t('ra.' + TL_WHY[k]) : k)} ${v}`).join('；')}</div>`;
    const back = S.points.filter(p => p.level !== '无暴露' && (String(p.verdict).startsWith('超出') || (p.level === '关键' && res(p.id).result !== '自然退干' && res(p.id).result !== 'L3 按时退干')));
    h += `<div class="m" style="margin-top:6px"><b>${t('ra.tlBack')}</b> ${back.length ? back.map(p => `${pdSim(p.id)}${p.hotspots_200m ? `（${esc(p.hotspots_200m)}）` : ''}`).join(' ') : t('ra.tlBackNone')}</div>`;
    return h + `</div><div class="m" data-stg="l3" style="margin-top:6px">${t('ra.tlNote')}</div>`;
  }
  // 积水点详情框（L3 图层）：点开看后果、积水、期限、自然退干、L3 实际派单
  function r1RtNote(){
    const S = R1.routes?.states?.[R1.st];
    if(!S) return R1.routes === null ? `<div class="m" style="margin-top:4px">${t('ra.rtNone')}</div>` : '';
    return `<div class="m" style="margin-top:4px">${t('ra.rtSum', {n: S.n_ok, r: S.ratio_median == null ? '—' : fmt(S.ratio_median, 2), p: S.ratio_p90 == null ? '—' : fmt(S.ratio_p90, 2), g: esc(R1.routes.generated || '')})}`
      + (S.n_blocked ? ' ' + t('ra.rtSumBlk', {b: S.n_blocked}) : '')
      + (S.n_local != null ? ' ' + t('ra.rtSrc', {l: S.n_local, o: S.n_online || 0}) : '') + `</div>`;   // 2026-10-28 起本地 NAServer 优先、在线备选
  }
  // L3 车辆回放（tools/rda_vehicle_tracks.py → flood2d_rda_<雨>/tracks.json，2026-10-28 用户：每辆车什么时候去了哪个积水点、走哪条路）：
  // 每台泵组 / 罐车按时刻在在线卡车路线上的位置；出发 / 开抽 / 结束取调度单，路上按导航每步的分钟插值。
  const R1TK = {t: null, timer: null, step: 2, mk: null};
  const TK_S = {base: '#94a3b8', drive: '#f59e0b', setup: '#8b5cf6', pump: '#2563eb', tank: '#0891b2', idle: '#64748b'};
  const r1TkX = () => R1.tracks?.states?.[R1.st]?.[R1TL.pool] || null;
  const tkClk = m => { m = Math.round(m); return `T+${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`; };
  function r1TkPos(U, tm){
    const L = U.legs, p0 = L[0].path[0];
    if(tm < L[0].dep) return {ll: [p0[2], p0[1]], s: 'base', l: L[0]};
    let l = L[0]; L.forEach(x => { if(x.dep <= tm) l = x; });
    const d = tm - l.dep, P = l.path;
    if(d < l.drive){ let i = 0; while(i < P.length - 2 && P[i + 1][0] <= d) i++;
      const a = P[i], b = P[i + 1] || a, f = b[0] > a[0] ? Math.min(1, (d - a[0]) / (b[0] - a[0])) : 1;
      return {ll: [a[2] + f * (b[2] - a[2]), a[1] + f * (b[1] - a[1])], s: 'drive', l}; }
    const e = P[P.length - 1];
    return {ll: [e[2], e[1]], s: tm < l.pump ? 'setup' : tm <= l.end ? (U.model === 'tanker-17m3' ? 'tank' : 'pump') : 'idle', l};
  }
  function r1TkSpan(X){ const us = Object.values(X.units);
    return [Math.min(...us.map(u => u.legs[0].dep)), Math.max(...us.flatMap(u => u.legs.map(l => l.end)))]; }
  function r1Tk(){
    const X = r1TkX();
    if(!X) return R1.tracks === null ? `<div class="m" style="margin-top:4px">${t('ra.tkNone')}</div>` : '';
    const [t0, t1] = r1TkSpan(X);
    if(R1TK.t == null || R1TK.t < t0 || R1TK.t > t1) R1TK.t = Math.floor(t0);
    return `<div class="m" style="margin:8px 0 2px"><b>${t('ra.tkHead', {n: Object.keys(X.units).length})}</b></div><div class="m">${t('ra.tkSub', {re: tkClk(R1.tracks.rain_end_min)})}</div>`
      + `<div style="display:flex;align-items:center;gap:6px;margin:4px 0"><button class="chip" id="tkplay" aria-label="${t('ra.tkPlay')}">▶</button>`
      + `<input type="range" id="tkt" aria-label="${t('ra.tkTime')}" min="${Math.floor(t0)}" max="${Math.ceil(t1)}" step="1" value="${Math.round(R1TK.t)}" style="flex:1;min-width:0">`
      + `<b id="tkclk" style="min-width:62px;font-variant-numeric:tabular-nums"></b><select id="tkstep" aria-label="${t('ra.tkStep')}">${[1, 2, 5, 10].map(v => `<option value="${v}"${R1TK.step === v ? ' selected' : ''}>${v} min</option>`).join('')}</select></div>`
      + `<div class="m" id="tkcnt"></div><div id="tklist" style="max-height:280px;overflow:auto"></div>`;
  }
  function r1TkStop(){ if(R1TK.timer){ clearInterval(R1TK.timer); R1TK.timer = null; } const b = document.getElementById('tkplay'); if(b) b.textContent = '▶'; }
  function r1TkUpd(){
    const X = r1TkX(), sl = document.getElementById('tkt'); if(!X || !sl){ r1TkStop(); return; }
    const tm = R1TK.t; sl.value = Math.round(tm); document.getElementById('tkclk').textContent = tkClk(tm);
    const cnt = {}, rows = [];
    Object.entries(X.units).forEach(([uid, U]) => { const p = r1TkPos(U, tm); cnt[p.s] = (cnt[p.s] || 0) + 1;
      const m = R1TK.mk?.[uid]; if(m){ m.setLatLng(p.ll); m.setStyle({fillColor: TK_S[p.s]}); m.setTooltipContent(`${esc(uid)} · ${t('ra.tkS_' + p.s)}`); }
      const l = p.l, where = p.s === 'base' ? t('ra.tkW_base', {d: esc(l.from), t: tkClk(l.dep)}) : p.s === 'drive' ? t('ra.tkW_drive', {a: esc(l.from), b: esc(l.fp), m: fmt(l.dep + l.drive - tm, 0)})
        : p.s === 'setup' ? t('ra.tkW_setup', {p: esc(l.fp), t: tkClk(l.pump)}) : p.s === 'idle' ? esc(l.fp) : t('ra.tkW_pump', {p: esc(l.fp), t: tkClk(l.end)});
      rows.push(`<tr data-tku="${esc(uid)}" style="cursor:pointer"><td>${esc(uid)}<div class="m">${esc(U.model.replace('diesel-', '').replace('tanker-17m3', t('ra.popTanker')))}</div></td>`
        + `<td><span style="color:${TK_S[p.s]};font-weight:600">${t('ra.tkS_' + p.s)}</span></td><td class="m">${where}${l.blocked ? ' · ' + t('ra.tkBlk') : ''}</td><td class="num">${l.k}/${U.legs.length}</td></tr>`); });
    document.getElementById('tkcnt').innerHTML = Object.keys(TK_S).filter(k => cnt[k]).map(k => `<span style="color:${TK_S[k]}">${t('ra.tkS_' + k)} ${cnt[k]}</span>`).join(' · ');
    const box = document.getElementById('tklist');
    box.innerHTML = `<table class="wbft"><tr><th>${t('ra.tkUnit')}</th><th>${t('ra.tkStat')}</th><th>${t('ra.tkWhere')}</th><th class="num">${t('ra.tkLeg')}</th></tr>${rows.join('')}</table>`;
    box.querySelectorAll('[data-tku]').forEach(r => r.onclick = () => { const m = R1TK.mk?.[r.dataset.tku]; if(m){ map.panTo(m.getLatLng()); m.openTooltip(); } });
  }
  function r1TkBind(){
    const sl = document.getElementById('tkt'); if(!sl) return;
    sl.oninput = () => { R1TK.t = +sl.value; r1TkUpd(); };
    document.getElementById('tkstep').onchange = e => { R1TK.step = +e.target.value; };
    document.getElementById('tkplay').onclick = () => {
      if(R1TK.timer){ r1TkStop(); return; }
      document.getElementById('tkplay').textContent = '⏸';
      R1TK.timer = setInterval(() => { const X = r1TkX(); if(!X){ r1TkStop(); return; } const [, t1] = r1TkSpan(X);
        R1TK.t = Math.min(t1, R1TK.t + R1TK.step); r1TkUpd(); if(R1TK.t >= t1) r1TkStop(); }, 300); };
    if(R1TK.timer) document.getElementById('tkplay').textContent = '⏸';             // 面板重绘时回放还在走
    r1TkUpd();
  }
  function r1TkMap(){
    if(!R1.lay) R1.lay = {};
    if(R1.lay.trk){ map.removeLayer(R1.lay.trk); R1.lay.trk = null; } R1TK.mk = null;
    const X = r1TkX(); if(R1.reg !== 'rda' || !R1TL.map || !X || !['overview', 'l3'].includes(NAV.stage)) return;
    if(!map.getPane('r1trk')) map.createPane('r1trk').style.zIndex = 470;
    const g = L.featureGroup(); R1TK.mk = {};
    Object.entries(X.units).forEach(([uid, U]) => { const p = r1TkPos(U, R1TK.t ?? U.legs[0].dep);
      R1TK.mk[uid] = L.circleMarker(p.ll, {pane: 'r1trk', radius: 6, color: '#fff', weight: 1.5, fillColor: TK_S[p.s], fillOpacity: 1}).bindTooltip(esc(uid), {direction: 'top'}).addTo(g); });
    R1.lay.trk = g.addTo(map); r1TkUpd();
  }
  function r1RtPop(l, rt){
    return `<div class="pdpop"><b>${esc(l.from)} → ${esc(l.fp)}</b> <span class="m">${esc(l.unit)} · ${esc(l.model)}</span><table>`
      + `<tr><td>${t('ra.rtOn_' + (rt.source || 'online'))}</td><td class="num">${fmt(rt.minutes, 1)} min · ${fmt(rt.km, 2)} km</td></tr>`
      + `<tr><td>${t('ra.rtLoc')}</td><td class="num">${rt.local_min == null ? '—' : fmt(rt.local_min, 1) + ' min'}</td></tr></table>`
      + (rt.blocked ? `<div style="color:#c2410c;margin-top:4px">${t('ra.rtBlocked')}</div>` : '')
      + `<div class="m" style="margin-top:4px">${t('ra.rtSteps')}</div><ol style="margin:2px 0 0 18px;padding:0;max-height:220px;overflow:auto;font-size:12px">${(F(rt, 'steps') || []).map(s => `<li>${esc(s)}</li>`).join('')}</ol></div>`;
  }
  function r1TLPop(p, r){
    const row = (a, b) => `<tr><td>${a}</td><td class="num">${b}</td></tr>`, rr = r.result === '只监测' ? ['tlR_mon', '#94a3b8'] : (TL_RES[r.result] || ['tlR_no', '#64748b']);
    const kz = s => String(s || '').replace(/(hospital|ambulance|tunnel|metro)/g, k => t('ra.k_' + k));
    const lvc = ({'关键': '#b91c1c', '高': '#ea580c', '中': '#ca8a04'})[p.level] || '#94a3b8';
    const units = r.units ? Object.entries(r.models || {}).map(([m, n]) => `${esc(m.replace('diesel-', '').replace('tanker-17m3', t('ra.popTanker')))} × ${n}`).join('，') : '—';
    return `<div class="pdpop"><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${pdSim(p.id)}<span style="padding:0 5px;border-radius:3px;font-size:11px;color:#fff;background:${lvc}">${t('ra.lv_' + ({'无暴露': 'none', '中': 'mid', '高': 'high', '关键': 'key'}[p.level] || 'none'))}</span></div>`
      + `<div class="m" style="margin:3px 0">${esc(kz(F(p, 'level_why')))}</div><table>`
      + row(t('ra.popVol'), `${fmt(p.vol_peak_m3, 0)} m³`) + row(t('ra.popDep'), `${fmt(p.max_depth_m, 2)} m`) + row(t('ra.popTpk'), `${fmt(p.t_peak_h, 2)} h`)
      + row(t('ra.popHs'), p.hotspots_200m ? esc(p.hotspots_200m) : '—')
      + row(t('ra.popDl'), `${fmt(p.deadline_h, 1)} h`) + row(t('ra.popNat'), p.t_dry_nat_h == null ? t('ra.popNatNo') : `${fmt(p.t_dry_nat_h, 1)} h`)
      + row(t('ra.popRes'), `<span style="color:${rr[1]}">${t('ra.' + rr[0])}</span>`)
      + (r.units ? row(t('ra.popUnits'), units) + row(t('ra.popFrom'), esc((r.from || []).join('，'))) + row(t('ra.popArr'), `${fmt(r.arrive_h, 1)} h`) + row(t('ra.popDone'), r.done_h == null ? '—' : `${fmt(r.done_h, 1)} h`) : '')
      + (r.why ? row(t('ra.popWhy'), esc(TL_WHY[r.why] ? t('ra.' + TL_WHY[r.why]) : r.why)) : '')
      + row(t('ra.popOut'), `${fmt(p.discharge_m, 0)} m`)
      + `</table>${String(p.verdict).startsWith('超出') ? `<div class="m" style="color:#b91c1c;margin-top:4px">${t('ra.tlBeyondShort')} → ${t('ra.popBack')}</div>` : ''}`
      + `<div class="m" style="margin-top:4px">${t('ra.popNote')}</div></div>`;
  }
  // RD-A / RB-01 地图图例：按当前显示的内容组合（水深或差值色标、客户热点、L3 处置结果、设施、模拟积水点）
  function r1Legend(){
    const lg = document.getElementById('legend'); if(!lg || tab !== 'line2p' || !R1_SCOPES.includes(l2pView)) return;
    const row = (sw, txt) => `<div class="row">${sw}<span>${txt}</span></div>`, dot = c => `<span class="sw" style="border-radius:50%;background:${c}"></span>`;
    let h = '';
    if(R1.mode === 'diff') h += `<div class="m"><b>${t('ra.lgDiff')}</b></div>` + row(`<span class="sw" style="background:#2563eb"></span>`, t('ra.lgShal')) + row(`<span class="sw" style="background:#dc2626"></span>`, t('ra.lgDeep'));
    else h += `<div class="m"><b>${t(R1.mode === 'max' ? 'ra.lgMax' : 'ra.lgDepth')}</b></div><div style="height:10px;border-radius:3px;margin:3px 0 1px;background:linear-gradient(90deg,#7dd3fc,#2582eb 33%,#1d4ed8 67%,#4c1d95)"></div>`
      + `<div class="m" style="display:flex;justify-content:space-between;font-size:11px"><span>0.1</span><span>0.16</span><span>0.6</span><span>≥1.4 m</span></div>`;
    h += `<div class="sep"></div>` + row(dot('#dc2626'), t('ra.lgHs'));
    if(R1.lay?.l3){
      h += `<div class="sep"></div><div class="m"><b>${t('ra.lgL3')}</b></div>`
        + Object.entries(TL_RES).map(([, [k, c]]) => row(dot(c), t('ra.' + k))).join('') + row(dot('#94a3b8'), t('ra.tlR_mon'))
        + row(`<span class="sw" style="border-radius:50%;background:#fff;border:2.5px solid #7f1d1d"></span>`, t('ra.tlBeyondShort'))
        + row(`<span class="ln" style="border-top:2px dashed #334155"></span>`, t('ra.lgRoute')) + `<div class="m">${t('ra.lgClick')}</div>`;
      if(r1TkX()) h += `<div class="sep"></div><div class="m"><b>${t('ra.lgTk')}</b></div>` + Object.entries(TK_S).map(([k, c]) => row(dot(c), t('ra.tkS_' + k))).join('');
    }
    if(R1.lay?.fac){
      const kinds = [...new Set(r1Chain(R1.st).flatMap(s => (R1.cs?.facilities?.[s] || []).map(f => f.kind)))];
      if(kinds.length) h += `<div class="sep"></div><div class="m"><b>${t('ra.lgFac')}</b></div>` + kinds.map(k => row(`<span style="display:inline-flex;width:16px;justify-content:center">${c1Sym(k === 'gpipe' ? 'bund' : k, 14)}</span>`, esc(r1FacName(k)))).join('');
    }
    if(['l1', 'l2'].includes(NAV.stage)) h += `<div class="sep"></div>` + row(dot('#1f6fb2'), t('ra.lgSim'));
    lg.innerHTML = h; lg.style.display = 'block';
  }
  // RD-A 计算进度（tools/rda_progress.py → data/flood2d_rda/progress.json，后台每 15 分钟更新；2026-10-26 用户：想在系统里看最新进展）
  async function r1Prog(){
    const box = document.getElementById('r1prog'); if(!box) return;
    let P = null; try{ P = await (await fetch('data/flood2d_rda/progress.json?_=' + Date.now(), {cache: 'no-store'})).json(); }catch(e){}
    if(!P){ box.innerHTML = `<div class="m">${t('ra.pgNone')}</div>`; return; }
    const LB = (lang === 'en' && P.labels_en) || P.labels || {}, tag = st => st === 'done' ? `<span style="color:#15803d;font-weight:600">${t('ra.pgDone')}</span>` : `<span style="color:#b45309;font-weight:600">${t('ra.pgRun')}</span>`;
    const bar = r => `<div style="display:flex;align-items:center;gap:6px;font-size:12px;margin:2px 0"><span style="width:170px;flex:none">${esc(F(r, 'label') || r.storm)} · ${esc(LB[r.state] || r.state)}</span>`
      + `<span style="flex:1;height:8px;background:#e5e7eb;border-radius:4px;overflow:hidden"><span style="display:block;height:8px;width:${r.pct}%;background:${r.status === 'done' ? '#15803d' : r.status === 'running' ? '#2563eb' : '#cbd5e1'}"></span></span>`
      + `<span class="m" style="width:96px;flex:none;text-align:right">${r.status === 'done' ? t('ra.pgDone') : r.status === 'running' ? `${fmt(r.t_h, 1)} / ${r.total_h} h` : t('ra.pgQueue')}</span></div>`;
    box.innerHTML = `<div class="m" style="margin-bottom:6px">${t('ra.pgAt', {t: esc(P.generated)})}</div>` + (P.items || []).map(it => {
      const cols = [...new Set((it.results || []).flatMap(r => Object.keys(r).filter(k => k !== 'storm' && !k.endsWith('_en'))))];
      return `<div style="border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0">`
        + `<div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline"><b>${esc(F(it, 'title'))}</b>${tag(it.status)}</div>`
        + ((it.steps || []).length ? `<ul class="m" style="padding-left:18px;margin:6px 0">${it.steps.map(s => `<li>${s.done ? '✓' : '…'} ${esc(F(s, 'name'))}${s.note ? `：${esc(F(s, 'note'))}` : ''}</li>`).join('')}</ul>` : '')
        + ((it.runs || []).length && it.status !== 'done' ? `<div style="margin:6px 0">${it.runs.map(bar).join('')}</div>` : '')
        + (cols.length ? `<table class="wbft" style="margin-top:6px"><tr><th>${t('ra.pgStorm')}</th>${cols.map(c => `<th class="num">${esc(LB[c] || c)}</th>`).join('')}</tr>`
            + it.results.map(r => `<tr><td>${esc(F(r, 'storm'))}</td>${cols.map(c => `<td class="num">${r[c] == null ? '—' : r[c] + '/11'}</td>`).join('')}</tr>`).join('') + `</table><div class="m">${t('ra.pgHsNote')}</div>` : '')
        + `</div>`; }).join('');
  }
  function r1TLBind(){
    const box = document.getElementById('r1tl'); if(!box) return;
    box.querySelectorAll('[data-tlp]').forEach(b => b.onclick = () => { R1TL.pool = b.dataset.tlp; box.innerHTML = r1TL(); r1TLBind(); r1TLMap(); });
    const m = document.getElementById('tlmap'); if(m) m.onchange = () => { R1TL.map = m.checked; r1TLMap(); r1Legend(); };
    r1TkBind();
  }
  function r1TLMap(){
    if(!R1.lay) R1.lay = {};
    if(R1.lay.l3){ map.removeLayer(R1.lay.l3); R1.lay.l3 = null; }
    r1TkMap();
    const S = R1.l3?.states?.[R1.st], X = r1TLX(R1.st); if(R1.reg !== 'rda' || !R1TL.map || !S || !X || !['overview', 'l3'].includes(NAV.stage)) return;
    if(!map.getPane('r1l3')) map.createPane('r1l3').style.zIndex = 460;          // 压在水深图（imageOverlay zIndex 450）之上
    const o = {pane: 'r1l3', renderer: R1TL.rd || (R1TL.rd = L.svg({pane: 'r1l3'}))};
    const g = L.featureGroup(), seen = new Set(), dep = {};
    const RT = R1.routes?.states?.[R1.st]?.routes || {};                         // 在线卡车路线：有就画实际路线（可点开看导航），没有就画直线
    (X.lines || []).forEach(l => { const k = l.from + '|' + l.fp; dep[l.from] = l.a; if(seen.has(k)) return; seen.add(k);
      const rt = RT[`${l.a[0]},${l.a[1]}|${l.b[0]},${l.b[1]}`];
      if(rt?.line?.length) L.polyline(rt.line, {...o, color: rt.blocked ? '#ea580c' : l.cross ? '#7c3aed' : '#0f766e', weight: 3, opacity: .8, dashArray: rt.blocked ? '7,5' : null})
        .bindTooltip(t('ra.rtTip', {d: esc(l.from), p: esc(l.fp), m: fmt(rt.minutes, 1), km: fmt(rt.km, 1), lm: rt.local_min == null ? '—' : fmt(rt.local_min, 1)}), {sticky: true})
        .bindPopup(() => r1RtPop(l, rt), {maxWidth: 380}).addTo(g);
      else L.polyline([[l.a[1], l.a[0]], [l.b[1], l.b[0]]], {...o, color: l.cross ? '#7c3aed' : '#334155', weight: 1.3, opacity: .55, dashArray: '4,4', interactive: false}).addTo(g); });
    Object.entries(dep).forEach(([d, a]) => L.marker([a[1], a[0]], {icon: L.divIcon({className: 'c1sym', html: `<div style="background:#0f172a;color:#fff;font:600 10px/16px sans-serif;padding:0 4px;border-radius:3px">${esc(d)}</div>`, iconSize: [30, 16], iconAnchor: [15, 8]})})
      .bindTooltip(t('ra.tlDepot', {d}), {direction: 'top'}).addTo(g));
    const vmax = Math.max(1, ...S.points.map(p => p.vol_peak_m3));
    S.points.forEach(p => { const r = X.points[p.id] || {}, rr = r.result === '只监测' ? ['tlR_mon', '#94a3b8'] : (TL_RES[r.result] || ['tlR_no', '#64748b']), bey = String(p.verdict).startsWith('超出');
      L.circleMarker([p.lat, p.lon], {...o, radius: 3.5 + 10 * Math.sqrt(p.vol_peak_m3 / vmax), color: bey ? '#7f1d1d' : '#fff', weight: bey ? 3 : 1.2, fillColor: rr[1], fillOpacity: .9})
        .bindTooltip(`${esc(p.id)} · ${t('ra.' + rr[0])}${bey ? ' · ' + t('ra.tlBeyond') : ''}<br>${fmt(p.vol_peak_m3, 0)} m³ · ${fmt(p.max_depth_m, 2)} m · ${t('ra.tlDl', {d: fmt(p.deadline_h, 1)})}`
          + `${r.units ? ' · ' + t('ra.tlSent', {n: r.units, a: fmt(r.arrive_h, 1), e: r.done_h == null ? '—' : fmt(r.done_h, 1)}) : ''}${r.why ? '<br>' + esc(TL_WHY[r.why] ? t('ra.' + TL_WHY[r.why]) : r.why) : ''}`, {direction: 'top'})
        .bindPopup(() => r1TLPop(p, r), {maxWidth: 340}).addTo(g); });
    R1.lay.l3 = g.addTo(map); r1Legend();
  }
  function r1HsTable(){
    const st = R1.st, b = r1Base(), ids = Object.keys(R1.cs?.zones || {}).filter(k => k.startsWith('ADM-')).map(k => k.split('|')[0]);
    const cell = (v, a, d) => { if(v == null) return '—'; if(a == null) return fmt(v, d); const dd = v - a;
      return `${fmt(v, d)}${Math.abs(dd) >= Math.pow(10, -d) ? ` <span style="color:${dd < 0 ? '#15803d' : '#b91c1c'}">${dd > 0 ? '+' : ''}${fmt(dd, d)}</span>` : ''}`; };
    return `<table class="wbft"><tr><th>${t('c1v.hs')}</th><th class="num">${t(R1K + '.c30')}</th><th class="num">${t(R1K + '.cDep')}</th><th class="num">${t(R1K + '.cH30')}</th></tr>`
      + ids.map(h => { const n = r1Z(h + '|200m', st) || {}, nb = b ? r1Z(h + '|200m', b) || {} : {}, e = R1.cs?.hs?.[h]?.[st] || {}, eb = b ? R1.cs?.hs?.[h]?.[b] || {} : {};
        return `<tr><td><b>${esc(h)}</b></td><td class="num">${cell(n.area_ge_0p30_ha, b ? nb.area_ge_0p30_ha : null, 2)}</td>`
          + `<td class="num">${cell(n.max_depth_m, b ? nb.max_depth_m : null, 2)}</td><td class="num">${cell(e.h_ge30, b ? eb.h_ge30 : null, 1)}</td></tr>`; }).join('')
      + `</table><div class="m">${t(R1K + '.hsNote')}</div>`;
  }
  const r1Chain = st => R1.cs?.chain?.[st] || ['L1'];
  const r1FacName = k => k === 'conv' && R1.reg === 'rda' ? t('ra.f_conv') : k === 'sump' && R1.reg === 'rda' ? t('ra.f_sump') : k === 'gpipe' ? t(R1K + '.f_gpipe') : k === 'ps_planned' ? t(R1K + '.f_ps') : k === 'pond' ? t(R1K + '.f_pond') : k === 'tank' ? t(R1K + '.f_tank') : c1FacName(k);
  function r1FacPop(f, s){       // 复用 C1 的参数框：临时换成 RB-01 的态名、当前态
    const bak = {...C1_LAB}, st0 = C1V.st; Object.assign(C1_LAB, R1_LAB); C1V.st = R1.st;
    let h; try{ h = c1FacPop(f, s).replace(esc(c1FacName(f.kind)), esc(r1FacName(f.kind))); } finally { Object.keys(C1_LAB).forEach(k => delete C1_LAB[k]); Object.assign(C1_LAB, bak); C1V.st = st0; }
    return h;
  }
  function r1FacList(){
    const F = R1.cs?.facilities || {};
    return r1Chain(R1.st).map(s => { const fs = F[s] || []; if(!fs.length) return s === 'L1' ? `<div class="m" style="margin:6px 0 2px"><b style="color:${R1_COL.L1}">L1</b> · ${t(R1K + '.noFacL1')}</div>` : '';
      const by = {}; fs.forEach(f => { (by[f.kind] = by[f.kind] || {n: 0, q: 0, v: 0}); by[f.kind].n++; by[f.kind].q += f.q_lps || 0; by[f.kind].v += f.vol_m3 || 0; });
      return `<div class="m" style="margin:6px 0 2px"><b style="color:${R1_COL[s]}">${esc(R1_LAB[s])}</b></div>`
        + Object.keys(by).map(k => { const o = by[k];
          return `<div style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin:3px 0 3px 4px">${c1Sym(k === 'gpipe' ? 'bund' : k, 15)}<span>${esc(r1FacName(k))}</span>`
            + `<span class="m" style="margin-left:auto;font-variant-numeric:tabular-nums">${o.n}${o.q ? ` · ${fmt(o.q, 0)} L/s` : ''}${o.v ? ` · ${fmt(o.v, 0)} m³` : ''}</span></div>`; }).join(''); }).join('')
      + `<div class="m" style="margin-top:6px">${t('c1v.facNote')}</div>`;
  }
  function r1Map(withFac = true){
    if(!R1.lay) R1.lay = {};
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = 'grayscale(.85) opacity(.7)';
    if(!R1.lay.dom && R1.stats?.domain_wgs) R1.lay.dom = L.polygon(R1.stats.domain_wgs, {color: '#0f172a', weight: 2, fill: false, dashArray: '8,5', interactive: false}).addTo(map);
    if(!R1.lay.hs && R1.hsPts) R1.lay.hs = L.featureGroup(R1.hsPts.map(p => L.circleMarker(p.ll, {radius: 6, color: '#fff', weight: 2, fillColor: '#dc2626', fillOpacity: 1})
      .bindTooltip(`${t('pd.client')} <b>${esc(p.id)}</b> · ${esc(p.crit || '')}`, {permanent: true, direction: 'right', offset: [6, 0], className: 'r1lbl'}))).addTo(map);
    if(R1.lay.fac){ map.removeLayer(R1.lay.fac); R1.lay.fac = null; }
    if(!withFac) return;
    const F = R1.cs?.facilities || {}, g = L.featureGroup(), pts = [];
    r1Chain(R1.st).forEach(s => (F[s] || []).forEach(f => { if(!f.ll) return; const c = f.kind === 'gpipe' ? '#15803d' : (C1_FK[f.kind] || {c: '#64748b'}).c;
      if((f.kind === 'pond' || f.kind === 'tank' || f.kind === 'sump') && f.poly) L.polygon(f.poly, {color: c, weight: 1.4, fillColor: c, fillOpacity: f.kind === 'sump' ? .08 : .22, dashArray: f.kind === 'sump' ? '4,3' : null})
        .bindPopup(() => r1FacPop(f, s), {maxWidth: 360}).addTo(g);
      if(f.line){ L.polyline(f.line, {color: c, weight: 3.5, opacity: .9}).bindPopup(() => r1FacPop(f, s), {maxWidth: 360}).addTo(g); return; }
      if(f.to_ll){ L.polyline([f.ll, f.to_ll], {color: c, weight: 1.6, opacity: .8, dashArray: '5,4', interactive: false}).addTo(g);
        L.circleMarker(f.to_ll, {radius: 2.4, color: c, weight: 1, fillColor: '#fff', fillOpacity: 1, interactive: false}).addTo(g); }
      if(f.kind !== 'pond' && f.kind !== 'tank') pts.push([f, s]); }));
    pts.sort((a, b) => c1SymSize(b[0]) - c1SymSize(a[0])).forEach(([f, s]) => { const z = Math.round(c1SymSize(f));
      L.marker(f.ll, {icon: L.divIcon({className: 'c1sym', html: c1Sym(f.kind, z), iconSize: [z, z], iconAnchor: [z / 2, z / 2]}), keyboard: false, riseOnHover: true})
        .bindTooltip(`<b>${esc(f.id)}</b> · ${esc(r1FacName(f.kind))}${f.q_lps ? ` · ${fmt(f.q_lps, 0)} L/s` : ''} <span class="m">· ${esc(R1_LAB[s])}</span>`, {direction: 'top', offset: [0, -z / 2]})
        .bindPopup(() => r1FacPop(f, s), {maxWidth: 360}).addTo(g); });
    R1.lay.fac = g.addTo(map);
  }
  function r1Chart(){
    const el = document.getElementById('r1ch'); if(!el || !R1.man) return;
    const X0 = 38, X1 = 332, Y0 = 20, Y1 = 124, all = R1.man.states;
    const TM = Math.max(...all.map(s => s.frames[s.frames.length - 1].t_h));
    const AM = Math.max(50, Math.ceil(Math.max(...all.flatMap(s => s.frames.map(f => f.wet10_ha || 0))) / 50) * 50);
    const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / AM;
    let h = '';
    for(let a = 0; a <= AM + 1e-9; a += AM / 4) h += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a, 0)}</text>`;
    for(let v = 0; v <= TM + 1e-9; v += 1) h += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    h += `<text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">${t('wb.axT')}</text><text x="2" y="9" font-size="9" fill="#66717f">${t('pt.axA')}</text>`;
    all.forEach(s => { const on = s.state === R1.st, bs = s.state === r1Base();
      h += `<polyline points="${s.frames.map(f => `${sx(f.t_h).toFixed(1)},${sy(f.wet10_ha || 0).toFixed(1)}`).join(' ')}" fill="none" stroke="${R1_COL[s.state]}" stroke-width="${on ? 2.6 : bs ? 1.8 : 1}" opacity="${on || bs ? 1 : .35}"/>`; });
    const s = r1Fr(), f = s ? s.frames[r1Idx()] : null;
    if(f && R1.mode === 'frame') h += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/><circle cx="${sx(f.t_h)}" cy="${sy(f.wet10_ha || 0)}" r="4" fill="${R1_COL[R1.st]}" stroke="#fff" stroke-width="1.5"/>`;
    el.innerHTML = h;
  }
  function r1Rain(){
    const el = document.getElementById('r1rain'), h = R1.cs?.hyeto; if(!el || !h) return;
    const X0 = 38, X1 = 312, Y0 = 16, Y1 = 96, TM = h.sim_h, dt = h.dt_min / 60;
    const PM = Math.ceil(Math.max(...h.p_mmph) / 50) * 50, CM = Math.ceil(h.rain_mm / 20) * 20;
    const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / PM, sc = v => Y1 - (Y1 - Y0) * v / CM;
    let g = '';
    for(let a = 0; a <= PM + 1e-9; a += PM / 2) g += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 5}" y="${sy(a) + 3.5}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a, 0)}</text>`;
    for(let a = 0; a <= CM + 1e-9; a += CM / 2) g += `<text x="${X1 + 4}" y="${sc(a) + 3.5}" font-size="9" fill="#1f5f96">${fmt(a, 0)}</text>`;
    for(let v = 0; v <= TM + 1e-9; v += 1) g += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    const bw = Math.max((X1 - X0) * dt / TM - .6, .8);
    h.t_h.forEach((a, i) => { const p = h.p_mmph[i]; if(p > 0) g += `<rect x="${sx(a).toFixed(1)}" y="${sy(p).toFixed(1)}" width="${bw.toFixed(1)}" height="${(Y1 - sy(p)).toFixed(1)}" fill="#b07a1c"><title>${a.toFixed(2)}–${(a + dt).toFixed(2)} h · ${fmt(p, 1)} mm/h</title></rect>`; });
    const pl = arr => `${sx(0)},${sc(0)} ` + h.t_h.map((a, i) => `${sx(a + dt).toFixed(1)},${sc(arr[i]).toFixed(1)}`).join(' ');
    g += `<polyline points="${pl(h.p_cum_mm)}" fill="none" stroke="#1f5f96" stroke-width="1.8"/><polyline points="${pl(h.exc_cum_mm)}" fill="none" stroke="#1f5f96" stroke-width="1.3" stroke-dasharray="4 3"/>`;
    g += `<text x="2" y="9" font-size="9" fill="#66717f">${t('c1v.rainAx')}</text><text x="${X1 + 26}" y="9" text-anchor="end" font-size="9" fill="#1f5f96">${t('c1v.rainCum')}</text>`;
    const s = r1Fr(), f = s ? s.frames[r1Idx()] : null;
    if(f && R1.mode === 'frame') g += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`;
    el.innerHTML = g;
    const cp = document.getElementById('r1rainc');
    if(cp) cp.textContent = t('c1v.rainCap', {r: fmt(h.rain_mm, 0), d: h.dur_h, k: h.dt_min, pk: fmt(h.peak_mmph, 0), t0: h.peak_t_h.toFixed(2), t1: (h.peak_t_h + dt).toFixed(2), ex: fmt(h.excess_mm, 1), src: F(h, 'source')});
  }
  function r1Hs(){
    const el = document.getElementById('r1hs6'), D = R1.cs?.hs, tt = R1.cs?.hs_t_h; if(!el || !D) return;
    const ids = Object.keys(D), ym = Math.max(0.6, Math.ceil(Math.max(...ids.flatMap(h => Object.values(D[h]).flatMap(e => e.r50))) * 11) / 10), TM = tt[tt.length - 1];
    const X0 = 30, X1 = 332, Y0 = 18, Y1 = 92, sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * Math.min(v, ym) / ym;
    const s = r1Fr(), f = s ? s.frames[r1Idx()] : null;
    el.innerHTML = ids.map(h => {
      let g = '';
      for(let a = 0; a <= ym + 1e-9; a += ym > 0.6 ? 0.2 : 0.1) g += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 4}" y="${sy(a) + 3}" text-anchor="end" font-size="9" fill="#66717f">${a.toFixed(1)}</text>`;
      for(let v = 0; v <= TM + 1e-9; v++) g += `<text x="${sx(v)}" y="${Y1 + 11}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
      g += `<line x1="${X0}" x2="${X1}" y1="${sy(.3)}" y2="${sy(.3)}" stroke="#b91c1c" stroke-dasharray="4 3"/><text x="${X1}" y="${sy(.3) - 3}" text-anchor="end" font-size="9" fill="#b91c1c">0.30 m</text>`;
      R1_ST.filter(x => D[h][x]).sort((a, b) => (a === R1.st) - (b === R1.st)).forEach(x => { const on = x === R1.st;
        g += `<polyline points="${D[h][x].r50.map((v, i) => `${sx(tt[i]).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${R1_COL[x]}" stroke-width="${on ? 2.6 : 1.2}" opacity="${on ? 1 : .45}"/>`; });
      if(f && R1.mode === 'frame') g += `<line x1="${sx(f.t_h)}" x2="${sx(f.t_h)}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`;
      const e = D[h][R1.st] || {};
      g += `<text x="${X0}" y="10" font-size="10" font-weight="600" fill="#1b2430">${esc(h)}</text><text x="${X1}" y="10" text-anchor="end" font-size="9.5" fill="#44505c">${t(R1K + '.hsSum', {pk: fmt(e.peak_m, 2), h30: fmt(e.h_ge30, 1), end: fmt(e.end_m, 2)})}</text>`;
      return `<svg class="wbch" viewBox="0 0 340 106" role="img" aria-label="${esc(h)}">${g}</svg>`; }).join('') + `<div class="m">${t(R1K + '.hs6Note')}</div>`;
  }
  function r1Draw(){
    const s = r1Fr(); if(!s) return;
    const i = r1Idx(), f = s.frames[i]; if(R1.t == null) R1.t = f.t_h;
    const b = r1Base(), dj = R1.mode === 'diff' && b ? (R1.cs?.diffs || []).find(d => d.a === b && d.b === R1.st) : null;
    const url = 'data/' + R1_DIR + '/' + (dj ? dj.png : R1.mode === 'max' ? s.max_png : f.png), bnd = dj ? dj.bounds : s.bounds;
    if(!R1.lay) R1.lay = {};
    if(!R1.lay.water) R1.lay.water = L.imageOverlay(url, bnd, {opacity: .82, interactive: false, zIndex: 450}).addTo(map);
    else { R1.lay.water.setUrl(url); R1.lay.water.setBounds(bnd); }
    const now = document.getElementById('r1now');
    if(now) now.innerHTML = dj ? `<b>${esc(R1_LAB[R1.st])} − ${esc(R1_LAB[b])}</b>　${t('c1v.diffSub', {sh: fmt(dj.shallower_ha, 1), deep: fmt(dj.deeper_ha, 1)})}`
      : R1.mode === 'diff' ? `<span class="m">${t('c1v.noDiff')}</span>` : R1.mode === 'max' ? `<b>${t('pt.maxView')}</b>`
      : `<b>t = ${(+f.t_h).toFixed(2)} h</b>　${t('pt.now10')} <b>${fmt(+f.wet10_ha || 0, 0)}</b> ha　${t('f2.depth')} <b>${fmt(+f.dmax_m, 2)}</b> m`;
    const rg = document.getElementById('r1rng'); if(rg){ rg.max = String(s.frames.length - 1); rg.value = String(i); }
    ['frame', 'max', 'diff'].forEach(m => { const x = document.getElementById('r1m_' + m); if(x) x.classList.toggle('on', R1.mode === m); });
    r1Chart(); r1Rain(); r1Hs();
    try{ const u = new URL(location.href); u.searchParams.set('tab', 'line2p'); u.searchParams.set('v', R1.reg); u.searchParams.set('st', R1.st); if(R1.reg === 'rda') u.searchParams.set('storm', R1.storm); else u.searchParams.delete('storm'); history.replaceState(null, '', u); }catch(e){}
  }
  function r1Play(){ const s = r1Fr(); if(!s) return; if(R1.timer){ r1Stop(); return; } R1.mode = 'frame';
    if(r1Idx() >= s.frames.length - 1){ R1.t = s.frames[0].t_h; r1Draw(); }
    const b = document.getElementById('r1play'); if(b) b.textContent = '❚❚';
    R1.timer = setInterval(() => { const c = r1Fr(), k = r1Idx(); if(!c || k >= c.frames.length - 1){ r1Stop(); return; } R1.t = c.frames[k + 1].t_h; r1Draw(); }, R1.speed); }
  function r1Select(st, first){
    r1Stop(); R1.st = st;
    panel.querySelectorAll('.wbbar [data-r1s]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.r1s === st)));
    const k = document.getElementById('r1kpi'); if(k) k.innerHTML = r1Kpis();
    const ht = document.getElementById('r1hs'); if(ht) ht.innerHTML = r1HsTable();
    const co = document.getElementById('r1cost'); if(co) co.innerHTML = r1Cost();
    const hcx = document.getElementById('r1hc'); if(hcx){ hcx.innerHTML = r1Hc(); r1HcBind(); }
    const tl = document.getElementById('r1tl'); if(tl){ tl.innerHTML = r1TL(); r1TLBind(); }
    const fl = document.getElementById('r1fac'); if(fl) fl.innerHTML = r1FacList();
    const ds = document.getElementById('r1desc'); if(ds) ds.textContent = t(R1K + '.d' + st);
    const facOn = ['l1', 'l2'].includes(NAV.stage);                 // 地图按阶段：L1 / L2 看设施与模拟积水点；总览 / L3 只看水深、L3 处置结果与客户热点
    r1Map(facOn); if(R1.reg === 'rda') r1TLMap(); r1Draw(); if(facOn) pdShow(R1C.run(), st); else pdClear(); r1HcMap(); r1Legend();
    if(first){ window._viewOwned = true; const fit = () => { try{ map.invalidateSize(); map.fitBounds(R1.lay.dom ? R1.lay.dom.getBounds().pad(.02) : L.latLngBounds(r1Fr().bounds)); }catch(e){} };
      fit(); setTimeout(fit, 120); setTimeout(fit, 780); }
  }
  function r1StormBox(){
    if(R1.reg !== 'rda' || !R1.idx) return '';
    const S = R1.idx.storms || [], cur = S.find(x => x.storm === R1.storm) || {}, n = (R1.idx.hotspots || []).length;
    const pass = st => cur.eval && cur.eval[st] != null ? `${cur.eval[st]}/${n}` : '—';
    return `<div class="wbbase" style="margin:6px 0"><label class="m">${t('ra.storm')} <select id="r1storm">${S.map(x =>
      `<option value="${x.storm}"${x.storm === R1.storm ? ' selected' : ''}>${x.label ? esc(F(x, 'label')) : `${fmt(x.rain_mm, x.rain_mm % 1 ? 1 : 0)} mm / ${x.dur_h} h`}${x.design || x.storm === 'S61D24' ? t('ra.design') : ''}</option>`).join('')}</select></label>
      <span class="m">${t('ra.passHead')} ${R1_ST.filter(st => !R1_HIDE.includes(st)).map(st => `<b style="color:${R1_COL[st]}">${esc(R1_LAB[st])}</b> ${pass(st)}`).join(' · ')}</span></div>`;
  }
  // 按阶段（NAV.stage）显示哪些态：L1 阶段只看现状；L2 阶段比客户计划与我们的方案；L3 阶段只列算过联动的态。作废 v1、无地面泵的中间版不上主栏
  const R1_HIDE = ['OPT20F', 'OPTC'];
  function r1StageStates(have){
    if(NAV.stage === 'l1') return have.filter(s => s === 'L1');
    if(NAV.stage === 'l3' && R1.l3) return have.filter(s => R1.l3.states[s]);
    return have.filter(s => !R1_HIDE.includes(s) && (NAV.stage !== 'l2' || s !== 'L1'));
  }
  async function renderRB01(reg = 'rb01'){
    wbClear(); plClear(); cyClear(); f2Clear(); rbClear(); c1Clear(); r1Use(reg);
    if(reg === 'rda' && !R1.idx) R1.idx = await load('flood2d_rda/index.json').catch(() => null);
    const lg = document.getElementById('legend'); if(lg){ lg.innerHTML = ''; lg.style.display = 'none'; }
    if(!R1.man) R1.man = await load(R1_DIR + '/manifest.json').catch(() => null);
    if(!R1.stats) R1.stats = await load(R1_DIR + '/stats.json').catch(() => null);
    if(!R1.cs) R1.cs = await load(R1_DIR + '/' + R1C.cs).catch(() => null);
    if(reg === 'rda' && !R1.l3) R1.l3 = await load(R1_DIR + '/l3.json').catch(() => null);
    if(reg === 'rda' && HCX.data === undefined) HCX.data = await load('rda_hc.json').catch(() => null);   // 高后果积水点的选择过程（tools/rda_hc_select.py）
    if(reg === 'rda' && R1.routes === undefined) R1.routes = await load(R1_DIR + '/routes.json').catch(() => null);   // 在线卡车路线（tools/rda_routes.py），没有就画直线
    if(reg === 'rda' && R1.tracks === undefined) R1.tracks = await load(R1_DIR + '/tracks.json').catch(() => null);   // L3 车辆回放（tools/rda_vehicle_tracks.py），只有 61 mm / 1 h 两场雨有
    if(!R1.hsPts){ const hs = await load('hotspots.geojson').catch(() => null); const ids = Object.keys(R1.cs?.hs || {});
      R1.hsPts = (hs?.features || []).filter(f => ids.includes(f.properties.hotspot_id)).map(f => ({id: f.properties.hotspot_id, crit: f.properties.criticality, ll: [f.geometry.coordinates[1], f.geometry.coordinates[0]]})); }
    if(!R1.man || !R1.stats || !R1.cs){ panel.innerHTML = wbViews() + `<div class="empty">${t(R1K + '.noData')}</div>`; wbBindViews(); return; }
    const have = R1.man.states.map(x => x.state), bar = r1StageStates(have);
    if(NAV.stage === 'l1' && have.includes('L1')) R1.st = 'L1';
    else if(!bar.includes(R1.st)) R1.st = bar.includes(R1C.def) ? R1C.def : (bar[bar.length - 1] || have[have.length - 1]);
    const S1 = R1.stats.L1 || {}, stg = NAV.stage;
    panel.innerHTML = wbViews() + `
      <div class="wbhead"><h2>${t(R1K + '.title')}</h2><div class="m">${t(R1K + '.sub', {tri: fmt(S1.n_tri || 0, 0), nodes: fmt(S1.n_nodes || 0, 0)})}</div>
        <div class="m" style="margin-top:4px"><a href="${R1C.report()}" target="_blank" rel="noopener">${t(R1K + '.report')}</a></div></div>
      <div data-stg="overview l1 l2 l3 concl">${r1StormBox()}</div>
      <div class="m" data-stg="overview" style="margin:4px 0 6px">${t('nav.r1Ov', {s: esc(R1_LAB[R1.st] || R1.st)})}</div>
      <div class="m" data-stg="l1" style="margin:4px 0 6px">${t('nav.r1L1')}</div>
      <div class="wbsticky" data-stg="l2 l3"><div class="wbbar" role="tablist" style="grid-template-columns:repeat(${Math.max(bar.length, 1)},1fr)">${bar.map(s =>
        `<button role="tab" data-r1s="${s}" aria-selected="${s === R1.st}" style="--c:${R1_COL[s]}"><b>${esc(R1_LAB[s])}</b><small>${t(R1K + '.s' + s)}</small></button>`).join('')}</div>
        <div class="wbbase"><label class="m">${t('wb.base')} <select id="r1base"><option value="">${t('c1v.parent')}</option>${have.map(s => `<option value="${s}"${s === R1.base ? ' selected' : ''}>${esc(R1_LAB[s])}</option>`).join('')}</select></label>
          <span class="m" id="r1desc"></span></div></div>
      <div class="wbk" id="r1kpi" data-stg="overview l1 l2"></div>
      <div data-stg="overview l3 concl"><h3 class="wbh" id="r1tlh">${t('ra.hTL')}</h3>${R1.reg === 'rda' ? `<div id="r1tl"></div>` : `<div class="m">${t('nav.r1NoL3')}</div>`}</div>
      ${R1.reg === 'rda' ? `<div data-stg="l2"><h3 class="wbh">${t('ra.hCost')}</h3><div id="r1cost"></div></div>` : ''}
      ${R1.reg === 'rda' ? `<div data-stg="l2"><h3 class="wbh" id="r1hch">${t('hc.h')}</h3><div id="r1hc"></div></div>` : ''}
      ${R1.reg === 'rda' ? `<div data-stg="concl"><h3 class="wbh">${t('ra.pgHead')}</h3><div id="r1prog"></div></div>` : ''}
      <div data-stg="concl"><h3 class="wbh">${t('nav.r1ConclHead')}</h3><ul class="m" style="padding-left:18px;line-height:1.7">${t('nav.r1Todo_' + R1.reg).split('|').map(x => `<li>${esc(x)}</li>`).join('')}</ul>
        <div class="m"><a href="${R1C.report()}" target="_blank" rel="noopener">${t(R1K + '.report')}</a></div></div>
      <div data-stg="data"><div class="m" style="margin-bottom:6px">${t('nav.r1Data_' + R1.reg)}</div></div>
      <div data-stg="l1 l2">
      <h3 class="wbh">${t('wb.hPlay')}</h3>
      <div class="wbpl"><button id="r1play" class="f2btn" aria-label="${t('wb.play')}">▶</button><input id="r1rng" type="range" min="0" max="1" value="0" step="1" aria-label="${t('wb.time')}"></div>
      <div id="r1now" class="f2now"></div>
      <div class="wbjump"><button class="chip" id="r1m_frame">${t('c1v.mFrame')}</button><button class="chip" id="r1m_max">${t('pt.showMax')}</button><button class="chip" id="r1m_diff">${t('c1v.mDiff')}</button></div>
      <svg id="r1ch" class="wbch" viewBox="0 0 340 150" role="img" aria-label="${t('pt.axA')}"></svg>
      <h3 class="wbh">${t('c1v.hRain')}</h3>
      <svg id="r1rain" class="wbch" viewBox="0 0 340 124" role="img" aria-label="${t('c1v.hRain')}"></svg><div id="r1rainc" class="m"></div>
      <h3 class="wbh">${t(R1K + '.hHs')}</h3><div id="r1hs"></div>
      <h3 class="wbh">${t(R1K + '.hHs6')}</h3><div id="r1hs6"></div></div>
      <div data-stg="l1 l2 l3"><h3 class="wbh">${t('pd.h')}</h3><div id="pdbox">${pdBox()}</div></div>
      <div data-stg="l1 l2"><h3 class="wbh">${t('c1v.hFac')}</h3><div id="r1fac"></div></div>
      <details class="sec wbsec" data-stg="data"${stg === 'data' ? ' open' : ''}><summary>${t('pt.hNotes')}</summary><div class="body">${['n1', 'n2', 'n3', 'n4', 'n5', 'n6'].map(k => `<div class="m">${mdb(t(R1K + '.' + k), true)}</div>`).join('')}</div></details>`;
    wbBindViews(); pdBind();
    const sm = document.getElementById('r1storm'); if(sm) sm.onchange = () => r1Storm(sm.value);
    panel.querySelectorAll('.wbbar [data-r1s]').forEach(b => b.onclick = () => r1Select(b.dataset.r1s));
    const bs = document.getElementById('r1base'); if(bs) bs.onchange = () => { R1.base = bs.value || null; r1Select(R1.st); };
    const pb = document.getElementById('r1play'); if(pb) pb.onclick = r1Play;
    const rg = document.getElementById('r1rng'); if(rg) rg.oninput = () => { r1Stop(); R1.mode = 'frame'; const s = r1Fr(); if(s){ R1.t = s.frames[+rg.value].t_h; r1Draw(); } };
    ['frame', 'max', 'diff'].forEach(m => { const x = document.getElementById('r1m_' + m); if(x) x.onclick = () => { r1Stop(); R1.mode = m; r1Draw(); r1Legend(); }; });
    r1Select(R1.st, true);
    if(R1.reg === 'rda' && NAV.stage === 'concl') r1Prog();
  }

  // ================= RD-A 高后果积水点：为什么是这些点（tools/rda_hc_select.py → data/rda_hc.json） =================
  // 2026-10-28 用户：客户会问为什么是这些点、其余点为什么优先级低 → 规则、逐场雨漏斗、目标清单（补了什么泵站 / 为什么没补）、逐点理由、地图按后果着色
  const HCX = {data: undefined, tier: null, map: false, lay: null, all: false};
  const HC_LV = {'关键': ['key', '#7f1d1d'], '高': ['high', '#dc2626'], '中': ['mid', '#f59e0b'], '无暴露': ['none', '#94a3b8']};
  const hcLv = lv => `<span class="hclv" style="background:${(HC_LV[lv] || HC_LV['无暴露'])[1]}">${t('ra.lv_' + (HC_LV[lv] || HC_LV['无暴露'])[0])}</span>`;
  const hcWhy = (o, k) => esc(String(F(o, k) || '').replace(/(hospital|ambulance|tunnel|metro)/g, w => lang === 'en' ? w : t('ra.k_' + w)));
  const hcStorm = (T, s) => { const f = T.funnel.find(x => x.storm === s); return f ? esc(F(f, 'label')) : s; };
  function hcClear(){ if(HCX.lay){ map.removeLayer(HCX.lay); HCX.lay = null; } }
  function hcTier(){ const D = HCX.data; if(!D) return null;
    return D.tiers.find(x => x.key === HCX.tier) || D.tiers.find(x => x.storms.includes(R1.storm)) || D.tiers[0]; }
  function hcEval(st, id){                                           // 当前这场雨下，某态在该高后果点的判据 A / B / C
    const e = R1.cs?.eval_hc?.[st]?.[id]; if(!e) return null;
    return ['A', 'B', 'C'].map(k => `<span style="color:${e[k] ? '#15803d' : '#b91c1c'}">${k}${e[k] ? '✓' : '✗'}</span>`).join(' ');
  }
  function r1Hc(){
    const D = HCX.data; if(!D) return `<div class="m">${t('hc.noData')}</div>`;
    const T = hcTier(), inT = T.storms.includes(R1.storm), sel = T.points.filter(p => p.code.startsWith('in_')).length;
    const n = (f, k) => f[k] || 0, neg = (f, k) => n(f, k) ? '−' + n(f, k) : '0';
    let h = `<div class="pdctl">${D.tiers.map(x => `<button class="chip${x.key === T.key ? ' on' : ''}" data-hct="${x.key}">${esc(x.key === 'A61' ? t('hc.tA61') : t('hc.t96'))}</button>`).join('')}</div>`;
    h += `<div class="m" style="margin:2px 0 6px">${t('hc.intro', {b: esc(R1_LAB[T.base_state] || T.base_state), f: esc(R1_LAB[T.final_state] || T.final_state)})}</div>`;
    h += `<ol class="hcrule">${['r1', 'r2', 'r3', 'r4'].map(k => `<li>${mdb(t('hc.' + k, {m: D.merge_m}), true)}</li>`).join('')}</ol>`;
    h += `<div class="hcsub">${t('hc.fH')}</div><table class="wbft hcfun"><tr><th>${t('hc.fStorm')}</th><th class="num">${t('hc.fAll')}</th><th class="num">${t('hc.fNone')}</th><th class="num">${t('hc.fMid')}</th><th class="num">${t('hc.fNat')}</th><th class="num">${t('hc.fIn')}</th></tr>`
      + T.funnel.map(f => `<tr${f.storm === R1.storm ? ' class="on"' : ''}><td>${esc(F(f, 'label'))}</td><td class="num">${f.n}</td><td class="num">${neg(f, 'out_noexpo')}</td><td class="num">${neg(f, 'out_mid')}</td><td class="num">${neg(f, 'out_nat')}</td><td class="num"><b>${n(f, 'in_l3') + n(f, 'in_beyond')}</b></td></tr>`).join('')
      + `</table><div class="m">${t('hc.merge', {s: sel, g: T.targets.length, m: D.merge_m})}</div>`;
    const fs = T.final_state, bs = T.base_state;
    const ord = m => T.storms.indexOf(m.storm);
    h += `<div class="hcsub">${t('hc.tH', {n: T.targets.length})}</div>` + (inT ? `<div class="m">${t('hc.evalNote', {s: hcStorm(T, R1.storm), b: esc(R1_LAB[bs] || bs), f: esc(R1_LAB[fs] || fs)})}</div>` : '') + T.targets.map(x => { const eb = hcEval(bs, x.hc_id), ef = hcEval(fs, x.hc_id);
      return `<div class="hccard" data-hcid="${esc(x.hc_id)}" tabindex="0"><div class="hchd"><b>${esc(x.hc_id)}</b> ${hcLv(x.level)} <span>${hcWhy(x, 'why')}</span>`
        + `<span class="m"> · ${fmt(x.max_depth_m, 2)} m · ${fmt(x.vol_peak_m3, 0)} m³</span></div>`
        + `<div class="hcrow"><span class="m">${t('hc.cSrc')}</span><span>${[...x.members].sort((a, b) => ord(a) - ord(b)).map(m => `${hcStorm(T, m.storm)} ${pdSim(m.id)}`).join('　')}</span></div>`
        + `<div class="hcrow"><span class="m">${t('hc.cPump')}</span><span>${x.pumps.length ? x.pumps.map(q => `<b>${esc(q.id)}</b> ${t('hc.pumpTxt', {n: q.n_intakes, q: fmt(q.q_lps, 0), c: fmt(q.cost_usd / 1e6, 2)})}`).join('<br>') : `<span style="color:#b45309">${esc(F(x, 'no_pump_why'))}</span>`}</span></div>`
        + (inT ? `<div class="hcrow"><span class="m">${t('hc.cEval')}</span><span>${eb || ef ? `${eb || '—'} → ${ef || '—'}` : `<span class="m">${t('hc.noEval')}</span>`}</span></div>` : '')
        + `</div>`; }).join('');
    const S = inT ? R1.storm : T.storms[0], P = T.points.filter(p => p.storm === S).sort((a, b) => (b.code.startsWith('in_') - a.code.startsWith('in_')) || (b.vol_peak_m3 - a.vol_peak_m3));
    h += `<details class="hcpts"${HCX.all ? ' open' : ''}><summary>${t('hc.pH', {s: hcStorm(T, S), n: P.length})}</summary><table class="wbft"><tr><th>${t('hc.cPt')}</th><th>${t('ra.tlLv')}</th><th>${t('hc.cRule')}</th><th class="num">m³</th></tr>`
      + P.map(p => `<tr class="${p.code.startsWith('in_') ? 'hcin' : ''}" data-hcpt="${esc(p.id)}" tabindex="0"><td>${pdSim(p.id)}${p.hc_id ? `<div class="m"><b>${esc(p.hc_id)}</b></div>` : ''}</td><td>${hcLv(p.level)}<div class="m">${hcWhy(p, 'level_why')}</div></td>`
        + `<td>${esc(F(p, 'why'))}${p.hotspots_200m ? `<div class="m">${t('hc.nearHs', {h: esc(p.hotspots_200m)})}</div>` : ''}</td><td class="num">${fmt(p.vol_peak_m3, 0)}</td></tr>`).join('') + `</table></details>`;
    h += `<div class="pdctl"><label><input type="checkbox" id="hc_map"${HCX.map ? ' checked' : ''}> ${t('hc.map', {s: hcStorm(T, S)})}</label></div>`;
    h += `<div class="m">${t('hc.note')}</div>`;
    return h;
  }
  function r1HcBind(){
    const box = document.getElementById('r1hc'); if(!box) return;
    box.querySelectorAll('[data-hct]').forEach(b => b.onclick = () => { HCX.tier = b.dataset.hct; box.innerHTML = r1Hc(); r1HcBind(); r1HcMap(); });
    const m = document.getElementById('hc_map'); if(m) m.onchange = e => { HCX.map = e.target.checked; r1HcMap(); };
    const d = box.querySelector('details.hcpts'); if(d) d.ontoggle = () => { HCX.all = d.open; };
    const go = (lon, lat) => { try{ map.setView([lat, lon], Math.max(map.getZoom(), 17)); }catch(e){} };
    box.querySelectorAll('.hccard[data-hcid]').forEach(r => { const f = () => { const x = hcTier().targets.find(q => q.hc_id === r.dataset.hcid); if(x){ if(!HCX.map){ HCX.map = true; const c = document.getElementById('hc_map'); if(c) c.checked = true; r1HcMap(); } go(x.lon, x.lat); } };
      r.onclick = f; r.onkeydown = e => { if(e.key === 'Enter') f(); }; });
    box.querySelectorAll('[data-hcpt]').forEach(r => { const f = () => { const T = hcTier(), S = T.storms.includes(R1.storm) ? R1.storm : T.storms[0], p = T.points.find(q => q.storm === S && q.id === r.dataset.hcpt);
      if(p){ if(!HCX.map){ HCX.map = true; const c = document.getElementById('hc_map'); if(c) c.checked = true; r1HcMap(); } go(p.lon, p.lat); } }; r.onclick = f; r.onkeydown = e => { if(e.key === 'Enter') f(); }; });
  }
  function r1HcMap(){
    hcClear(); const D = HCX.data; if(!D || !HCX.map || R1.reg !== 'rda' || NAV.stage !== 'l2') return;
    const T = hcTier(), S = T.storms.includes(R1.storm) ? R1.storm : T.storms[0], P = T.points.filter(p => p.storm === S), vmax = Math.max(1, ...P.map(p => p.vol_peak_m3)), g = L.featureGroup();
    P.forEach(p => { const lv = HC_LV[p.level] || HC_LV['无暴露'], sel = p.code.startsWith('in_');
      L.circleMarker([p.lat, p.lon], {radius: 4 + 10 * Math.sqrt(p.vol_peak_m3 / vmax), color: sel ? '#111827' : '#fff', weight: sel ? 3 : 1.2, fillColor: lv[1], fillOpacity: .85})
        .bindTooltip(`${esc(p.id)} · ${t('ra.lv_' + lv[0])}${p.hc_id ? ' · ' + esc(p.hc_id) : ''}`, {direction: 'top', className: 'pdtip'})
        .bindPopup(() => `<div class="pdpop">${pdSim(p.id)} ${hcLv(p.level)}${p.hc_id ? ` <b>${esc(p.hc_id)}</b>` : ''}<div class="m" style="margin-top:4px">${hcWhy(p, 'level_why')}</div><div style="margin-top:4px">${esc(F(p, 'why'))}</div><div class="m">${fmt(p.max_depth_m, 2)} m · ${fmt(p.vol_peak_m3, 0)} m³</div></div>`, {maxWidth: 320}).addTo(g); });
    T.targets.forEach(x => L.marker([x.lat, x.lon], {icon: L.divIcon({className: 'hclab', html: `<span>${esc(x.hc_id)}</span>`, iconSize: null}), interactive: false}).addTo(g));
    HCX.lay = g.addTo(map);
  }

  // ================= 二维模拟积水点（SIM-态-序号）：/api/ponding（swbrain/ponding.py） =================
  // 模型算出来的积水片，编号 SIM-…；和客户热点（ADM-xxx，红点）、调度研判的系统积水点（fp_id = 客户热点编号）都不是一回事。
  // 编号按峰值积水量从大到小，只在同一运行、同一态、同一组阈值内有效。阈值缺省是 2026-10-18 的试算值，未与客户对齐。
  const PD = {run: null, st: null, p: {depth: 0.15, min_area: 100, min_dur: 0.5}, on: true, near: false, data: null, lay: null, byId: {}, sel: null, req: 0, cache: {},
              ref: 'L1', flt: 'all'};   // ref：跨态对照基准（2026-10-28 用户：切到 L2+suggested 时 SIM-L1-029「消失」看不懂 → 标出已消除 / 仍在 / 新出现）
  const PD_DEPTHS = [0.1, 0.15, 0.2, 0.3, 0.5];
  function pdClear(){ if(PD.lay){ map.removeLayer(PD.lay); PD.lay = null; } PD.byId = {}; PD.sel = null; }
  const pdSim = id => `<span class="pdsim">${esc(id)}</span>`, pdCli = id => `<span class="pdcli">${esc(id)}</span>`;
  function pdBox(){
    return `<div class="m" style="margin-bottom:6px">${mdb(t('pd.intro'), true)}</div>
      <div class="pdctl">
        <label>${t('pd.depth')} <select id="pd_depth">${PD_DEPTHS.map(d => `<option value="${d}"${d === PD.p.depth ? ' selected' : ''}>≥${d} m</option>`).join('')}</select></label>
        <label>${t('pd.area')} <input id="pd_area" type="number" min="0" max="100000" step="50" value="${PD.p.min_area}"> m²</label>
        <label>${t('pd.dur')} <input id="pd_dur" type="number" min="0" max="72" step="0.25" value="${PD.p.min_dur}"> h</label>
        <button class="chip" id="pd_go">${t('pd.apply')}</button>
      </div>
      <div class="pdctl"><label><input type="checkbox" id="pd_on"${PD.on ? ' checked' : ''}> ${t('pd.show')}</label>
        <label><input type="checkbox" id="pd_near"${PD.near ? ' checked' : ''}> ${t('pd.near')}</label>
        <label>${t('pd.ref')} <select id="pd_ref"><option value="">${t('pd.refNone')}</option>${(R1_ST || []).map(s => `<option value="${esc(s)}"${s === PD.ref ? ' selected' : ''}>${esc(R1_LAB[s] || s)}</option>`).join('')}</select></label></div>
      <div id="pdsum" class="f2now"></div><div id="pdhs"></div><div id="pdflt" class="pdctl"></div><div id="pdlist" class="pdlist"></div>
      <div class="m" style="margin-top:4px">${t('pd.legend')} ${t('pd.goneLegend')}</div>`;
  }
  function pdBind(){
    const g = id => document.getElementById(id);
    const go = () => { const d = +g('pd_depth').value, a = +g('pd_area').value, u = +g('pd_dur').value;
      if(!(a >= 0 && u >= 0)) return; PD.p = {depth: d, min_area: a, min_dur: u}; pdLoad(); };
    if(g('pd_go')) g('pd_go').onclick = go;
    if(g('pd_depth')) g('pd_depth').onchange = go;
    if(g('pd_on')) g('pd_on').onchange = e => { PD.on = e.target.checked; pdDraw(); };
    if(g('pd_near')) g('pd_near').onchange = e => { PD.near = e.target.checked; pdList(); };
    if(g('pd_ref')) g('pd_ref').onchange = e => { PD.ref = e.target.value; PD.flt = 'all'; pdLoad(); };
    if(g('pdflt')) g('pdflt').onclick = e => { const b = e.target.closest('[data-flt]'); if(b){ PD.flt = b.dataset.flt; pdList(); } };
    const L0 = g('pdlist'); if(L0){ L0.onclick = e => { const r = e.target.closest('[data-pd]'); if(r) pdSelect(r.dataset.pd, true); };
      L0.onkeydown = e => { if(e.key === 'Enter'){ const r = e.target.closest('[data-pd]'); if(r) pdSelect(r.dataset.pd, true); } }; }
  }
  async function pdShow(run, st){ PD.run = run; PD.st = st; await pdLoad(); }
  async function pdLoad(){
    const ref = pdRef(), k = `${PD.run}|${PD.st}|${PD.p.depth}|${PD.p.min_area}|${PD.p.min_dur}|${ref}`, my = ++PD.req, sum = document.getElementById('pdsum');
    if(sum) sum.innerHTML = `<span class="m">${t('pd.loading')}</span>`;
    let r = PD.cache[k];
    if(!r){ r = await api(`/api/ponding?run=${encodeURIComponent(PD.run)}&state=${encodeURIComponent(PD.st)}&depth=${PD.p.depth}&min_area=${PD.p.min_area}&min_dur=${PD.p.min_dur}${ref ? '&ref=' + encodeURIComponent(ref) : ''}`);
      if(r && !r.error) PD.cache[k] = r; }
    if(my !== PD.req) return;                                                  // 期间又切了态 / 阈值
    PD.data = r && !r.error ? r : null;
    if(!PD.data){ pdClear(); pdHs(); if(sum) sum.innerHTML = `<span class="m">${esc(r?.error || t('pd.noApi'))}</span>`; const l = document.getElementById('pdlist'); if(l) l.innerHTML = ''; return; }
    pdDraw(); pdList(); pdHs();
  }
  // 客户热点 × 当前态模拟积水点的对照（2026-10-26 用户：一个场景一个场景切，切到每个场景后要能直观看到重合情况）
  // 距离 = 热点到积水片内最近网格中心（swbrain/ponding.py 的 near，只记 200 m 内）；热点只是重点点位，不是完整的历史积水记录
  const PD_HSC = [[50, '#15803d', 'hsHit'], [100, '#ca8a04', 'hsNear'], [200, '#ea580c', 'hsFar'], [Infinity, '#94a3b8', 'hsNone']];
  function pdHsRows(){
    const D = PD.data; if(!D) return [];
    const pts = R1.hsPts?.length ? R1.hsPts : (D.hotspots_in_area || []).map(id => ({id}));
    return pts.map(p => { let best = null;
      D.feats.forEach(f => (f.near || []).forEach(([h, d]) => { if(h === p.id && (!best || d < best.d)) best = {d, f}; }));
      return {...p, best, c: PD_HSC.find(c => (best ? best.d : Infinity) <= c[0])}; });
  }
  function pdHs(){
    const el = document.getElementById('pdhs'), D = PD.data, R = pdHsRows(); if(!el) return;
    if(!D || !R.length){ el.innerHTML = ''; return; }
    const n = k => R.filter(r => r.best && r.best.d <= k).length, ids = new Set(R.map(r => r.id));
    const nf = D.feats.filter(f => (f.near || []).some(([h]) => ids.has(h))), vn = nf.reduce((a, f) => a + f.vol_peak_m3, 0), vt = D.vol_total_m3 || 0;
    el.innerHTML = `<div class="pdhsh">${t('pd.hsH', {s: esc(R1_LAB[PD.st] || PD.st)})}</div><div style="font-size:12.5px">${t(PD.st === 'L1' ? 'pd.hsL1' : 'pd.hsOpt')}</div>`
      + `<div class="m" style="margin:2px 0 4px">${t('pd.hsSum', {n: R.length, a: n(50), b: n(100), c: n(200), z: R.length - n(200), d: PD.p.depth})}<br>${t('pd.hsRev', {k: nf.length, n: D.feats.length, p: vt ? fmt(100 * vn / vt, 0) : '—'})}</div>`
      + `<table class="wbft pdhst"><tr><th>${t('pd.client')}</th><th>${t('pd.hsSim')}</th><th class="num">${t('pd.hsD')}</th><th class="num">${t('pd.cH')}</th><th>${t('pd.hsJ')}</th></tr>`
      + R.map(r => `<tr data-pdh="${esc(r.id)}" tabindex="0"><td>${pdCli(r.id)}</td><td>${r.best ? pdSim(r.best.f.id) : '—'}</td>`
        + `<td class="num">${r.best ? fmt(r.best.d, 0) + ' m' : '&gt; 200 m'}</td><td class="num">${r.best ? fmt(r.best.f.hmax_m, 2) + ' m' : '—'}</td>`
        + `<td><span class="pdhsc" style="background:${r.c[1]}"></span>${t('pd.' + r.c[2])}</td></tr>`).join('') + `</table>`
      + `<div class="m" style="margin-top:4px">${t('pd.hsNote')}</div>`;
    el.querySelectorAll('[data-pdh]').forEach(tr => { const go = () => pdHsGo(tr.dataset.pdh); tr.onclick = go; tr.onkeydown = e => { if(e.key === 'Enter') go(); }; });
  }
  function pdHsGo(id){
    const r = pdHsRows().find(x => x.id === id); if(!r) return;
    if(r.best) pdSelect(r.best.f.id, false);
    try{ const pts = [r.ll, r.best ? [r.best.f.lat, r.best.f.lon] : null].filter(Boolean); map.fitBounds(L.latLngBounds(pts).pad(.8), {maxZoom: 17}); }catch(e){}
  }
  // 跨态对照：基准态不在本区域的态表里、或与当前态相同时不对照
  function pdRef(){ return PD.ref && PD.ref !== PD.st && (R1_ST || []).includes(PD.ref) ? PD.ref : ''; }
  function pdKind(f){ return !PD.data?.compare ? 'all' : (f.ref || []).length ? 'kept' : 'new'; }
  function pdRefTxt(f){
    const C = PD.data?.compare; if(!C) return '';
    const rl = esc(R1_LAB[C.ref] || C.ref), m = f.ref || [];
    if(!m.length) return `<div class="pdcmp new">${t('pd.refNo', {r: rl})}</div>`;
    return `<div class="pdcmp">${t('pd.refRow', {r: rl})}：` + m.slice(0, 3).map(([id]) => { const b = C.ref_brief[id] || {};
      return `${pdSim(id)} ${fmt(b.hmax_m, 2)} → ${fmt(f.hmax_m, 2)} m · ${fmt(b.vol_peak_m3, 0)} → ${fmt(f.vol_peak_m3, 0)} m³`; }).join('<br>') + (m.length > 3 ? ' …' : '') + `</div>`;
  }
  function pdPopGone(g){
    const C = PD.data.compare, P0 = PD.data.params;
    return `<div class="pdpop"><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${pdSim(g.id)}<span class="pdgone">${t('pd.gone')}</span></div>`
      + `<div style="margin-top:4px">${t('pd.popGone', {d: P0.depth, a: P0.min_area, u: P0.min_dur, r: esc(R1_LAB[C.ref] || C.ref), h: fmt(g.hmax_m, 2), v: fmt(g.vol_peak_m3, 0), s: fmt(g.area_m2, 0)})}</div></div>`;
  }
  function pdCol(h){ return h >= 1 ? '#0b3f78' : h >= 0.5 ? '#1f6fb2' : h >= 0.3 ? '#3b8fd0' : '#6fb3e3'; }
  function pdPop(f){
    const near = (f.near || []).length ? f.near.map(([h, d]) => `${pdCli(h)} ${fmt(d, 0)} m`).join('<br>') : t('pd.none');
    const row = (a, b) => `<tr><td>${a}</td><td class="num">${b}</td></tr>`;
    return `<div class="pdpop"><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${pdSim(f.id)}<span class="m">${t('pd.kindSim')}</span></div><table>`
      + row(t('pd.cH'), `${fmt(f.hmax_m, 2)} m`) + row(t('pd.cA', {d: PD.data.params.depth}), `${fmt(f.area_m2, 0)} m²`) + row(t('pd.cA30'), `${fmt(f.area30_m2, 0)} m²`)
      + row(t('pd.cV'), `${fmt(f.vol_peak_m3, 0)} m³`) + row(t('pd.cT'), `${f.t_peak_h} h`) + row(t('pd.cDur'), `${f.dur_h} h`)
      + row(t('pd.cRec'), f.recede_h == null ? t('pd.notRec', {h: PD.data.t_end_h}) : `${f.recede_h} h`) + row(t('pd.cEnd'), `${fmt(f.vol_end_m3, 0)} m³`)
      + row(t('pd.cZ'), `${fmt(f.z_m, 2)} m`) + `</table>${pdRefTxt(f)}<div class="m" style="margin-top:4px">${t('pd.cNear')}</div><div>${near}</div></div>`;
  }
  function pdDraw(){
    pdClear(); if(!PD.on || !PD.data) return;
    const F = PD.data.feats, vmax = Math.max(1, ...F.map(f => f.vol_peak_m3)), g = L.featureGroup();
    pdHsRows().forEach(r => { if(!r.ll) return;                                   // 热点外两圈：实线 50 m、虚线 200 m，颜色 = 与模拟积水的对照
      L.circle(r.ll, {radius: 200, color: r.c[1], weight: 1.2, dashArray: '5,4', fill: false, interactive: false}).addTo(g);
      L.circle(r.ll, {radius: 50, color: r.c[1], weight: 2.4, fillColor: r.c[1], fillOpacity: .15, interactive: false}).addTo(g); });
    F.forEach(f => {
      const poly = f.geom ? L.geoJSON(f.geom, {style: {color: pdCol(f.hmax_m), weight: 1.4, fill: true, fillOpacity: 0.04}}).bindPopup(() => pdPop(f), {maxWidth: 340}).on('click', () => pdSelect(f.id)) : null;
      const pt = L.circleMarker([f.lat, f.lon], {radius: 3 + 9 * Math.sqrt(f.vol_peak_m3 / vmax), color: '#fff', weight: 1.2, fillColor: '#1f6fb2', fillOpacity: 0.95})
        .bindTooltip(f.id, {direction: 'top', offset: [0, -4], className: 'pdtip'}).bindPopup(() => pdPop(f), {maxWidth: 340}).on('click', () => pdSelect(f.id));
      if(poly) poly.addTo(g); pt.addTo(g); PD.byId[f.id] = {f, poly, pt};
    });
    const C = PD.data.compare, rl = C ? (R1_LAB[C.ref] || C.ref) : '';
    (C?.gone || []).forEach(x => {                                               // 基准态有、本态已消除：灰色虚线框 + 空心点
      const poly = x.geom ? L.geoJSON(x.geom, {style: {color: '#64748b', weight: 1.4, dashArray: '4,3', fill: true, fillColor: '#94a3b8', fillOpacity: 0.08}})
        .bindPopup(() => pdPopGone(x), {maxWidth: 340}).on('click', () => pdSelect(x.id)) : null;
      const pt = L.circleMarker([x.lat, x.lon], {radius: 4, color: '#64748b', weight: 1.5, fillColor: '#fff', fillOpacity: 0.9, dashArray: '2,2'})
        .bindTooltip(t('pd.goneTip', {id: x.id, r: rl}), {direction: 'top', offset: [0, -4], className: 'pdtip'}).bindPopup(() => pdPopGone(x), {maxWidth: 340}).on('click', () => pdSelect(x.id));
      if(poly) poly.addTo(g); pt.addTo(g); PD.byId[x.id] = {f: x, poly, pt, gone: true};
    });
    PD.lay = g.addTo(map);
    if(R1.lay?.hs) R1.lay.hs.bringToFront();
  }
  function pdList(){
    const sum = document.getElementById('pdsum'), el = document.getElementById('pdlist'), D = PD.data; if(!D || !el) return;
    const C = D.compare, nearOk = f => !PD.near || (f.near || []).length;
    const F = D.feats.filter(f => nearOk(f) && (PD.flt === 'all' || PD.flt === pdKind(f)));
    const G = C && (PD.flt === 'all' || PD.flt === 'gone') ? C.gone.filter(nearOk) : [];
    if(sum) sum.innerHTML = t('pd.sum', {n: fmt(D.n, 0), v: fmt(D.vol_total_m3, 0), r: fmt(D.n_not_receded, 0), h: D.t_end_h})
      + (D.adjacency === 'approx' ? ` <span class="m">${t('pd.approx')}</span>` : '')
      + (C ? `<div class="pdcmpsum">${t('pd.cmp', {r: esc(R1_LAB[C.ref] || C.ref), nr: C.summary.n_ref, nc: C.summary.n_cur, ng: C.summary.n_gone, vg: fmt(C.summary.vol_gone_m3, 0),
          nkr: C.summary.n_kept_ref, ns: C.summary.n_shallower, nd: C.summary.n_deeper, nn: C.summary.n_new})}<div class="m">${t('pd.cmpNote')}</div></div>` : '');
    const fl = document.getElementById('pdflt');
    if(fl) fl.innerHTML = C ? [['all', 'fAll', D.n + C.summary.n_gone], ['kept', 'fKept', C.summary.n_cur - C.summary.n_new], ['new', 'fNew', C.summary.n_new], ['gone', 'fGone', C.summary.n_gone]]
      .map(([k, l, n]) => `<button class="chip${PD.flt === k ? ' on' : ''}" data-flt="${k}">${t('pd.' + l)} ${n}</button>`).join('') : '';
    const rl = C ? esc(R1_LAB[C.ref] || C.ref) : '';
    el.innerHTML = F.slice(0, 300).map(f => `<div class="pdrow${f.id === PD.sel ? ' on' : ''}" data-pd="${esc(f.id)}" tabindex="0">${pdSim(f.id)}`
      + `<span>${fmt(f.hmax_m, 2)} m · ${fmt(f.area_m2, 0)} m²</span><span class="num">${fmt(f.vol_peak_m3, 0)} m³</span>`
      + `<span class="m pdmeta">${f.recede_h == null ? t('pd.notRecS') : t('pd.rec', {h: f.recede_h})} · ${t('pd.nearest')} ${f.nearest ? pdCli(f.nearest[0]) + ' ' + fmt(f.nearest[1], 0) + ' m' : '—'}`
      + (C ? ' · ' + ((f.ref || []).length ? `${rl} ${f.ref.slice(0, 2).map(([id]) => pdSim(id)).join(' ')}` : `<span class="pdnew">${t('pd.newTag', {r: rl})}</span>`) : '') + `</span></div>`).join('')
      + G.map(g => `<div class="pdrow gone${g.id === PD.sel ? ' on' : ''}" data-pd="${esc(g.id)}" tabindex="0">${pdSim(g.id)}<span class="pdgone">${t('pd.gone')} · ${fmt(g.hmax_m, 2)} m</span>`
        + `<span class="num">${fmt(g.vol_peak_m3, 0)} m³</span><span class="m pdmeta">${t('pd.goneTip', {id: g.id, r: rl})}</span></div>`).join('')
      || `<div class="m" style="padding:6px">${t('pd.empty')}</div>`;
  }
  function pdSelect(id, fly){
    const o = PD.byId[id]; if(!o) return;
    const prev = PD.byId[PD.sel]; if(prev?.poly) prev.poly.setStyle({color: prev.gone ? '#64748b' : pdCol(prev.f.hmax_m), weight: 1.4});
    PD.sel = id; if(o.poly) o.poly.setStyle({color: '#f59e0b', weight: 3});
    if(fly){ try{ map.fitBounds(o.poly ? o.poly.getBounds() : L.latLngBounds([o.pt.getLatLng()]), {maxZoom: 17, padding: [60, 60]}); }catch(e){} o.pt.openPopup(); }
    document.querySelectorAll('#pdlist .pdrow').forEach(x => x.classList.toggle('on', x.dataset.pd === id));
  }

  // ================= 全市 L1 / L2 =================
  // 数据：tools/city_gap.py（全市一维 8 片 × 2 态）。术语：「L1 剩余」「L2 剩余」= 各态自己接不住的水；
  // 「差值 / 改善」= L1 剩余 − L2 剩余（用户说的 gap）。差值只在接塘的管网上算，别处是分片带来的数值噪声（0.04%），记 0。
  // 这一版是**一维**口径：退水两项、最大积水面积 / 水深要等全市一二维；降雨服务水平另跑。
  const CY = {mode: ['L1', 'L2', 'diff'].includes(U.get('m')) ? U.get('m') : 'L1', data: null, grid: null, catch: null, lay: null,
              nodes: null, pipes: null, on: false};
  // 街区级明细（tools/city_nodes_export.py）：逐井溢流、接塘管网的满管度。只在 ≥ CY_DZ 级、只画视野里的
  const CY_DZ = 14, CY_NBINS = [[10, '#fdd49e'], [100, '#fc8d59'], [500, '#d7301f'], [Infinity, '#7f0000']];
  const CY_BINS = [[1000, '#fde0c5'], [5000, '#facba6'], [15000, '#f59e72'], [35000, '#e2583e'], [Infinity, '#9f1d20']];
  const CY_DBINS = [[1000, '#c7e9c0'], [5000, '#74c476'], [15000, '#31a354'], [Infinity, '#006d2c']];
  const cyCol = (v, bins) => (bins.find(b => v < b[0]) || bins[bins.length - 1])[1];

  function cyClear(){ try{ cypOff(); }catch(e){} if(CY.lay){ Object.values(CY.lay).forEach(l => { if(l) map.removeLayer(l); }); CY.lay = null; }
    if(CY.on){ map.off('moveend', cyDetail); CY.on = false; }
    const tp = map.getPane('tilePane'); if(tp) tp.style.filter = ''; }

  // 逐井过程线（tools/city_node_series.py → data/city_nodeseries/<tx>_<ty>.json，0.02° 分块，点井时才取）
  const NS = {idx: null, tiles: {}};
  async function nsNear(la, lo){
    if(!NS.idx) NS.idx = await load('city_nodeseries/index.json').catch(() => null);
    if(!NS.idx) return null;
    const T = NS.idx.tile_deg, k = `${Math.floor(lo / T)}_${Math.floor(la / T)}`;
    if(!NS.idx.tiles.includes(k)) return null;
    if(!NS.tiles[k]) NS.tiles[k] = await load(`city_nodeseries/${k}.json`).catch(() => ({}));
    let best = null, bd = 1e9;
    Object.entries(NS.tiles[k]).forEach(([id, r]) => { const d = Math.abs(r.ll[0] - la) + Math.abs(r.ll[1] - lo); if(d < bd){ bd = d; best = {id, ...r}; } });
    return bd < 3e-5 ? best : null;
  }
  function nsSvg(r){
    const tt = NS.idx.t_h, W = 300, H = 120, X0 = 34, X1 = W - 6, Y0 = 20, Y1 = H - 18, TM = tt[tt.length - 1];
    const one = (a, b, unit, lab) => {
      const M = Math.max(1, ...(a || []), ...(b || []));
      const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / M;
      const pl = (s, c, w, da) => s ? `<polyline points="${s.map((v, i) => `${sx(tt[i]).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${c}" stroke-width="${w}"${da ? ` stroke-dasharray="${da}"` : ''}/>` : '';
      let h = `<text x="${X0}" y="10" font-size="10" font-weight="600" fill="#44505c">${lab}</text>`;
      [0, M].forEach(v => { h += `<text x="${X0 - 4}" y="${sy(v) + 3}" font-size="9" text-anchor="end" fill="#66717f">${fmt(v, 0)}</text><line x1="${X0}" x2="${X1}" y1="${sy(v)}" y2="${sy(v)}" stroke="#e3e7ec"/>`; });
      for(let v = 0; v <= TM + 1e-9; v += 1) h += `<text x="${sx(v)}" y="${Y1 + 11}" font-size="9" text-anchor="middle" fill="#66717f">${v}</text>`;
      return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${lab}">${h}${pl(a, '#334155', 1.8)}${pl(b, '#0e7490', 1.8, '4 2')}</svg>`;
    };
    return `<div class="m" style="margin:4px 0 2px"><b>${esc(r.id)}</b>${r.c ? ` · ${esc(r.c)}` : ''}${r.p ? ` · ${t('cy.pondNet')}` : ''}</div>`
      + one(r.q1, r.q2, 'L/s', t('cy.sQ')) + one(r.d1, r.d2, 'cm', t('cy.sD'))
      + `<div class="m"><span style="color:#334155">━ L1 ${fmt(r.v1, 0)} m³</span>　<span style="color:#0e7490">╌ L2 ${fmt(r.v2, 0)} m³</span>　${t('cy.sT')}</div>`;
  }

  function cyDetail(){
    if(!CY.lay) return;
    ['nodes', 'pipes'].forEach(k => { if(CY.lay[k]){ map.removeLayer(CY.lay[k]); CY.lay[k] = null; } });
    const z = map.getZoom(), hint = document.getElementById('cyzoom'), m = CY.mode;
    if(CY.lay.grid) CY.lay.grid.eachLayer(l => { const o = l._cyFo; if(o == null) l._cyFo = l.options.fillOpacity;
      l.setStyle({fillOpacity: (l._cyFo || 0) * (z >= CY_DZ ? .3 : 1)}); });
    if(z < CY_DZ || !CY.nodes){ if(hint) hint.textContent = t('cy.zoomHint'); return; }
    const B = map.getBounds().pad(.15), inb = (a, b) => B.contains([a, b]);
    const gp = L.featureGroup(), gn = L.featureGroup();
    let np = 0, nn = 0;
    (CY.pipes?.pipes || []).forEach(p => {
      if(!inb(p[0], p[1]) && !inb(p[2], p[3])) return;
      const r = m === 'L2' ? p[5] : p[4], d = p[4] - p[5];
      let c = null, w = 2.4;
      if(m === 'diff'){ if(d >= .05){ c = '#1a9850'; w = 3.6; } else if(d <= -.05){ c = '#d73027'; w = 3.6; } }
      else if(r >= .99) c = '#b91c1c'; else if(r >= .8) c = '#f59e0b';
      if(!c) return; np++;
      L.polyline([[p[0], p[1]], [p[2], p[3]]], {renderer: canvas, color: c, weight: w, opacity: .9})
        .bindTooltip(`${t('cy.pipe')} · ${t('cy.ratio')} L1 ${p[4].toFixed(2)} → L2 ${p[5].toFixed(2)}`).addTo(gp); });
    CY.nodes.nodes.forEach(q => {
      if(!inb(q[0], q[1])) return;
      const d = q[2] - q[3];
      let c, rad;
      if(m === 'diff'){ if(Math.abs(d) < 1) return; c = d > 0 ? '#1a9850' : '#d73027'; rad = Math.min(14, 3 + Math.sqrt(Math.abs(d)) / 3); }
      else { const v = m === 'L2' ? q[3] : q[2]; if(v < 1) return; c = cyCol(v, CY_NBINS); rad = Math.min(14, 2 + Math.sqrt(v) / 4); }
      nn++;
      L.circleMarker([q[0], q[1]], {renderer: canvas, radius: rad, color: '#fff', weight: .6, fillColor: c, fillOpacity: .9})
        .bindPopup(`<h4>${t('cy.node')}</h4><div class="kv"><span>${t('cy.l1')}</span><span><b>${fmt(q[2], 0)} m³</b></span>`
          + `<span>${t('cy.l2')}</span><span><b>${fmt(q[3], 0)} m³</b></span><span>${t('cy.diff')}</span>`
          + `<span><b style="color:${d >= 1 ? 'var(--ok)' : d <= -1 ? 'var(--bad)' : 'var(--muted)'}">${d >= 1 ? '−' + fmt(d, 0) : d <= -1 ? '+' + fmt(-d, 0) : '0'} m³</b></span></div>`
          + `<div class="nsbox m">${t('cy.sLoad')}</div>`, {maxWidth: 330, minWidth: 310})
        .on('popupopen', async e => { const r = await nsNear(q[0], q[1]); const el = e.popup.getElement()?.querySelector('.nsbox');
          if(el){ el.innerHTML = r ? nsSvg(r) : t('cy.sNone'); e.popup.update(); } }).addTo(gn); });
    CY.lay.pipes = gp.addTo(map); CY.lay.nodes = gn.addTo(map);
    if(hint) hint.textContent = t('cy.zoomOn', {n: fmt(nn, 0), p: fmt(np, 0)});
  }

  function cyMap(){
    if(!CY.lay) CY.lay = {};
    if(CY.lay.grid){ map.removeLayer(CY.lay.grid); CY.lay.grid = null; }
    const m = CY.mode;
    CY.lay.grid = L.geoJSON(CY.grid, {renderer: canvas,
      style: f => { const p = f.properties || {};
        if(m === 'diff') return p.diff > 0.5 ? {color: '#1b4332', weight: 1, fillColor: cyCol(p.diff, CY_DBINS), fillOpacity: .85}
          : {color: '#94a3b8', weight: .3, fillOpacity: 0, opacity: .5};
        const v = m === 'L1' ? p.L1 : p.L2;
        return {color: '#ffffff', weight: .4, fillColor: cyCol(v, CY_BINS), fillOpacity: .78}; },
      onEachFeature: (f, l) => { const p = f.properties || {};
        l.bindPopup(() => `<h4>${t('cy.cell')} ${esc(p.cell || '')}</h4><div class="kv">`
          + `<span>${t('cy.l1')}</span><span><b>${fmt(p.L1, 0)} m³</b></span>`
          + `<span>${t('cy.l2')}</span><span><b>${fmt(p.L2, 0)} m³</b></span>`
          + `<span>${t('cy.diff')}</span><span><b style="color:${p.diff > 0.5 ? 'var(--ok)' : 'var(--muted)'}">${p.diff > 0.5 ? '−' + fmt(p.diff, 0) : '0'} m³</b></span></div>`
          + `<div class="m">${p.diff > 0.5 ? t('cy.cellPond') : t('cy.cellNo')}</div>`); }}).addTo(map);
    if(!CY.lay.catch && CY.catch){
      CY.lay.catch = L.geoJSON(CY.catch, {style: {color: '#7c3aed', weight: 1.4, fill: false, dashArray: '4,3'}, interactive: false}).addTo(map);
    }
    if(!CY.lay.ponds){ load('ponds_l2_pts.geojson').then(gj => { if(!CY.lay || CY.lay.ponds) return;
      CY.lay.ponds = L.geoJSON(gj, {pointToLayer: (f, ll) => L.circleMarker(ll, {radius: 3, color: '#0e7490', weight: 1.2, fillColor: '#fff', fillOpacity: 1}),
        onEachFeature: (f, l) => l.bindTooltip(`${esc(f.properties.unitid || '')} · ${fmt(+f.properties.volume_m3 || 0, 0)} m³`)}).addTo(map); }).catch(() => {}); }
    const lg = document.getElementById('cylg');
    if(lg) lg.innerHTML = (m === 'diff' ? CY_DBINS : CY_BINS).map((b, i, arr) => {
      const lo = i ? arr[i - 1][0] : (m === 'diff' ? 1 : 0);
      return `<span><i style="background:${b[1]}"></i>${fmt(lo, 0)}${b[0] === Infinity ? '+' : '–' + fmt(b[0], 0)}</span>`; }).join('')
      + `<span>${t('cy.unit')}</span><span><i style="background:transparent;border:1.5px dashed #7c3aed"></i>${t('cy.catchLg')}</span>`
      + `<span><i style="background:#fff;border:1.5px solid #0e7490;border-radius:50%"></i>${t('cy.pondLg')}</span>`;
    const dl = document.getElementById('cydlg');
    if(dl) dl.innerHTML = m === 'diff'
      ? `<span><i style="background:#1a9850;border-radius:50%"></i>${t('cy.better')}</span><span><i style="background:#d73027;border-radius:50%"></i>${t('cy.worse')}</span>`
        + `<span><i style="background:#1a9850;height:3px"></i>${t('cy.pRelief')}</span><span><i style="background:#d73027;height:3px"></i>${t('cy.pWorse')}</span>`
      : CY_NBINS.map((b, i, arr) => `<span><i style="background:${b[1]};border-radius:50%"></i>${fmt(i ? arr[i - 1][0] : 1, 0)}${b[0] === Infinity ? '+' : '–' + fmt(b[0], 0)} m³</span>`).join('')
        + `<span><i style="background:#b91c1c;height:3px"></i>${t('cy.pFull')}</span><span><i style="background:#f59e0b;height:3px"></i>${t('cy.pNear')}</span>`;
    cyDetail();
  }


  // 逐时播放（tools/city_series_export.py，一维 8 片 × 2 态、每 5 分钟一帧）：500 m 方格的溢流量 + 全市溢流速率 + 塘蓄水
  const CYP = {data: null, i: null, timer: null, on: false, lay: null, rects: []};
  const CYP_BINS = [[10, '#fde0c5'], [100, '#fb9a6b'], [500, '#e2583e'], [2000, '#b91c1c'], [Infinity, '#67000d']];
  const CYP_DBINS = [[10, '#c7e9c0'], [100, '#74c476'], [500, '#238b45'], [Infinity, '#00441b']];
  function cypStop(){ if(CYP.timer){ clearInterval(CYP.timer); CYP.timer = null; } const b = document.getElementById('cypplay'); if(b) b.textContent = '▶'; }
  function cypOff(){ cypStop(); if(CYP.lay){ map.removeLayer(CYP.lay); CYP.lay = null; CYP.rects = []; } CYP.on = false;
    if(CY.lay?.grid && !map.hasLayer(CY.lay.grid)) CY.lay.grid.addTo(map); }
  function cypDraw(){
    const D = CYP.data; if(!D) return; const i = CYP.i ?? 0, m = CY.mode;
    if(!CYP.lay){ CYP.lay = L.featureGroup(); CYP.rects = D.cells.map(c => L.rectangle([[c.b[0], c.b[1]], [c.b[2], c.b[3]]], {stroke: false, fillOpacity: 0, interactive: false}).addTo(CYP.lay)); CYP.lay.addTo(map); }
    if(CY.lay?.grid && map.hasLayer(CY.lay.grid)) map.removeLayer(CY.lay.grid);
    CYP.on = true;
    D.cells.forEach((c, k) => { const v = m === 'L2' ? c.L2[i] : m === 'diff' ? c.L1[i] - c.L2[i] : c.L1[i];
      const on = m === 'diff' ? v >= 1 : v >= 1;
      CYP.rects[k].setStyle(on ? {fillColor: cyCol(v, m === 'diff' ? CYP_DBINS : CYP_BINS), fillOpacity: .82} : {fillOpacity: 0}); });
    const t = D.t_h[i], a = D.sys.L1[i], b = D.sys.L2[i];
    const now = document.getElementById('cypnow'); if(now) now.innerHTML = `<b>t = ${t.toFixed(2)} h</b>　L1 <b>${fmt(a, 0)}</b> m³/s　L2 <b>${fmt(b, 0)}</b> m³/s`;
    const rg = document.getElementById('cyprng'); if(rg){ rg.max = String(D.t_h.length - 1); rg.value = String(i); }
    cypPonds(i);
    cypChart();
  }
  function cypPonds(i){
    const D = CYP.data; const pt = document.getElementById('cypponds');
    if(pt) pt.innerHTML = `<table class="wbft"><tr><th>${t2('cy.pond')}</th><th class="num">${t2('cy.cap')}</th><th class="num">${t2('cy.now')}</th></tr>`
      + D.ponds.slice(0, 8).map(p => { const f = p.cap_m3 ? p.vol_m3[i] / p.cap_m3 * 100 : 0;
        return `<tr><td>${esc(p.id.replace(/^SW_/, ''))}</td><td class="num">${fmt(p.cap_m3, 0)}</td><td class="num">${fmt(p.vol_m3[i], 0)} <span class="m">${f.toFixed(1)}%</span></td></tr>`; }).join('') + `</table>`;
  }
  const t2 = k => t(k);
  function cypChart(){
    const el = document.getElementById('cypch'), D = CYP.data; if(!el || !D) return;
    const X0 = 40, X1 = 332, Y0 = 16, Y1 = 118, TM = D.t_h[D.t_h.length - 1];
    const AM = Math.ceil(Math.max(...D.sys.L1, ...D.sys.L2) / 500) * 500 || 1;
    const sx = v => X0 + (X1 - X0) * v / TM, sy = v => Y1 - (Y1 - Y0) * v / AM;
    let h = '';
    for(let a = 0; a <= AM; a += AM / 4) h += `<line x1="${X0}" x2="${X1}" y1="${sy(a)}" y2="${sy(a)}" stroke="#e3e7ec"/><text x="${X0 - 4}" y="${sy(a) + 3}" text-anchor="end" font-size="9" fill="#66717f">${fmt(a, 0)}</text>`;
    for(let v = 0; v <= TM + 1e-9; v++) h += `<text x="${sx(v)}" y="${Y1 + 12}" text-anchor="middle" font-size="9" fill="#66717f">${v}</text>`;
    h += `<text x="2" y="9" font-size="9" fill="#66717f">${t('cy.axRate')}</text><text x="${X1}" y="${Y1 + 24}" text-anchor="end" font-size="9" fill="#66717f">${t('wb.axT')}</text>`;
    [['L1', '#b91c1c'], ['L2', '#0e7490']].forEach(([k, c]) => { h += `<polyline points="${D.sys[k].map((v, j) => `${sx(D.t_h[j]).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${c}" stroke-width="${k === 'L1' ? 1.4 : 2}"/>`; });
    const i = CYP.i ?? 0; h += `<line x1="${sx(D.t_h[i])}" x2="${sx(D.t_h[i])}" y1="${Y0}" y2="${Y1}" stroke="#1b2430" stroke-dasharray="3 3"/>`;
    h += `<text x="${X1 - 4}" y="${Y0 + 10}" font-size="9" text-anchor="end" fill="#b91c1c">L1</text><text x="${X1 - 4}" y="${Y0 + 21}" font-size="9" text-anchor="end" fill="#0e7490">L2</text>`;
    el.innerHTML = h;
  }
  function cypPlay(){ const D = CYP.data; if(!D) return; if(CYP.timer){ cypStop(); return; }
    if((CYP.i ?? 0) >= D.t_h.length - 1) CYP.i = 0; const b = document.getElementById('cypplay'); if(b) b.textContent = '❚❚';
    CYP.timer = setInterval(() => { if(CYP.i >= D.t_h.length - 1){ cypStop(); return; } CYP.i++; cypDraw(); }, 260); }
  async function cypInit(){
    if(!CYP.data) CYP.data = await load('city_series.json').catch(() => null);
    const box = document.getElementById('cyplay'); if(!box) return;
    if(!CYP.data){ box.innerHTML = `<div class="m">${t('cy.noPlay')}</div>`; return; }
    const D = CYP.data; if(CYP.i == null) CYP.i = D.sys.L1.indexOf(Math.max(...D.sys.L1));
    box.innerHTML = `<div class="wbpl"><button id="cypplay" class="f2btn" aria-label="${t('wb.play')}">▶</button><input id="cyprng" type="range" min="0" max="1" value="0" step="1" aria-label="${t('wb.time')}">
        <button class="chip" id="cypoff">${t('cy.playOff')}</button></div><div id="cypnow" class="f2now"></div>
      <div class="f2bar" id="cyplg">${CYP_BINS.map((b, k, arr) => `<span><i style="background:${b[1]}"></i>${fmt(k ? arr[k - 1][0] : 1, 0)}${b[0] === Infinity ? '+' : '–' + fmt(b[0], 0)}</span>`).join('')}<span>m³ / 5 min · 500 m</span></div>
      <svg id="cypch" class="wbch" viewBox="0 0 340 145" role="img" aria-label="${t('cy.axRate')}"></svg>
      <div class="m">${t('cy.playNote')}</div><h3 class="wbh" style="margin-top:6px">${t('cy.hPonds')}</h3><div id="cypponds"></div>`;
    document.getElementById('cypplay').onclick = cypPlay;
    document.getElementById('cyprng').oninput = e => { cypStop(); CYP.i = +e.target.value; cypDraw(); };
    document.getElementById('cypoff').onclick = () => { cypOff(); const n = document.getElementById('cypnow'); if(n) n.innerHTML = `<span class="m">${t('cy.playIdle')}</span>`; };
    const n = document.getElementById('cypnow'); if(n) n.innerHTML = `<span class="m">${t('cy.playIdle')}</span>`;
    cypChart(); cypPonds(CYP.i);
    const rg = document.getElementById('cyprng'); rg.max = String(D.t_h.length - 1); rg.value = String(CYP.i);
    const ct = parseFloat(U.get('cyt'));                        // ?cyt=1.5 直接打开到这一时刻的播放画面（分享 / 复验用）
    if(isFinite(ct)){ let b = 0; D.t_h.forEach((x, k) => { if(Math.abs(x - ct) < Math.abs(D.t_h[b] - ct)) b = k; }); CYP.i = b; cypDraw(); }
  }

  function cyTable(){
    const c = CY.data.city, L1 = c.L1, L2 = c.L2;
    const f0 = v => v == null ? '—' : fmt(v, 0), fh = v => v == null ? '—' : fmt(v, 2);
    const pend = k => `<span class="m">${t(k)}</span>`;
    const pc = (a, b) => (a && b != null) ? `${((b - a) / a * 100).toFixed(1)}%` : '—';
    const rows = [
      [t('cy.rFlood'), `${f0(L1.flood_m3)} m³`, `${f0(L1.flood_m3 - c.diff_m3)} m³`, `<b style="color:var(--ok)">−${f0(c.diff_m3)} m³（−${(c.diff_pct || 0).toFixed(1)}%）</b>`],
      [t('cy.rRecM'), pend('cy.need2d'), pend('cy.need2d'), '—'],
      [t('cy.rRecR'), pend('cy.need2d'), pend('cy.need2d'), '—'],
      [t('cy.rEmpty'), `${fh(L1.emptying_majority_h)} h`, `${fh(L2.emptying_majority_h)} h`, `+${fh((L2.emptying_majority_h || 0) - (L1.emptying_majority_h || 0))} h`],
      [t('cy.rStore'), `${f0(L1.storage_capacity_m3)} m³`, `${f0(L2.storage_capacity_m3)} m³`, `+${f0(L2.storage_capacity_m3 - L1.storage_capacity_m3)} m³`],
      [t('cy.rServ'), pend('cy.needRuns'), pend('cy.needRuns'), '—'],
      [t('cy.rPump'), `${L1.pumps}`, `${L2.pumps}`, t('cy.noPump')],
      [t('cy.rArea'), pend('cy.need2d'), pend('cy.need2d'), '—'],
      [t('cy.rDepth'), pend('cy.need2d'), pend('cy.need2d'), '—'],
    ];
    return `<div class="tblwrap"><table class="sg wbtbl"><tr><th>${t('sg.param')}</th><th class="num">${t('cy.hL1')}</th><th class="num">${t('cy.hL2')}</th><th class="num">${t('cy.hImp')}</th></tr>`
      + rows.map(r => `<tr><td>${r[0]}</td><td class="num">${r[1]}</td><td class="num">${r[2]}</td><td class="num">${r[3]}</td></tr>`).join('') + `</table></div>`;
  }

  // 差值只出在接塘的网上：切到差值时缩到这些方格，否则全市范围下绿格小得看不见
  function cyFit(){ try{ map.invalidateSize();
    if(CY.mode === 'diff'){ let b = null; CY.lay.grid.eachLayer(l => { if((l.feature?.properties?.diff || 0) > 0.5){ b = b ? b.extend(l.getBounds()) : L.latLngBounds(l.getBounds().getSouthWest(), l.getBounds().getNorthEast()); } });
      if(b){ map.fitBounds(b.pad(.08)); return; } }
    map.fitBounds(CY.lay.grid.getBounds().pad(.03)); }catch(e){} }

  async function renderCity(){
    wbClear(); plClear(); f2Clear();
    if(tl3Layer){ map.removeLayer(tl3Layer); tl3Layer = null; }
    if(tl3NetLayer){ map.removeLayer(tl3NetLayer); tl3NetLayer = null; }
    const lg0 = document.getElementById('legend'); if(lg0){ lg0.innerHTML = ''; lg0.style.display = 'none'; }
    if(!CY.data) CY.data = await load('city_gap.json').catch(() => null);
    if(!CY.grid) CY.grid = await load('city_gap_grid.geojson').catch(() => null);
    if(!CY.catch) CY.catch = await load('city_gap_catch.geojson').catch(() => null);
    if(!CY.nodes) CY.nodes = await load('city_nodes.json').catch(() => null);
    if(!CY.pipes) CY.pipes = await load('city_pipes.json').catch(() => null);
    if(!CY.data || !CY.grid){ panel.innerHTML = wbViews() + `<div class="empty">${t('cy.noData')}</div>`; wbBindViews(); return; }
    const c = CY.data.city, pn = c.pond_networks || {}, v = CY.data.validation;
    const top = (CY.data.by_catchment || []).filter(r => r.L1_m3 > 0).slice(0, 12);
    const basis = await cityBasis('city');
    panel.innerHTML = wbViews() + `
      <div class="wbhead"><h2>${t('cy.title')}</h2><div class="m">${t('cy.sub')}</div></div>${basis}
      <div class="wbk">
        <div class="wbkc"><span class="lab">${t('cy.kL1')}</span><b>${fmt(c.L1.flood_m3 / 1e4, 1)}<small>${t('cy.wan')}</small></b><span class="m">${t('cy.ofRunoff', {p: (c.L1.flood_m3 / c.L1.inflow_m3 * 100).toFixed(1)})}</span></div>
        <div class="wbkc"><span class="lab">${t('cy.kL2')}</span><b>${fmt((c.L1.flood_m3 - c.diff_m3) / 1e4, 1)}<small>${t('cy.wan')}</small></b><span class="wbd good">−${(c.diff_pct || 0).toFixed(1)}% ${t('wb.vs')} L1</span></div>
        <div class="wbkc"><span class="lab">${t('cy.kDiff')}</span><b style="color:var(--ok)">${fmt(c.diff_m3 / 1e4, 1)}<small>${t('cy.wan')}</small></b><span class="m">${t('cy.lower')}</span></div>
        <div class="wbkc"><span class="lab">${t('cy.kPond', {n: pn.n || 0})}</span><b>−${(pn.diff_pct || 0).toFixed(1)}<small>%</small></b><span class="m">${t('cy.kPondSub', {a: fmt((pn.L1_m3 || 0) / 1e4, 1), b: fmt((pn.L2_m3 || 0) / 1e4, 1)})}</span></div>
      </div>
      <h3 class="wbh">${t('cy.hMap')}</h3>
      <div class="seg mini" id="cymode">${['L1', 'L2', 'diff'].map(k => `<button data-cym="${k}" class="${CY.mode === k ? 'on' : ''}">${t('cy.m' + k)}</button>`).join('')}</div>
      <div class="f2bar" id="cylg"></div>
      <div class="m" id="cyzoom" style="margin-top:6px"></div>
      <div class="f2bar" id="cydlg"></div>
      <h3 class="wbh">${t('cy.hPlay')}</h3><div id="cyplay"></div>
      ${CY.nodes ? `<div class="m">${t('cy.detail', {n: fmt(CY.nodes.n_nodes, 0), b: fmt(CY.nodes.n_better, 0), w: fmt(CY.nodes.n_worse, 0), r: fmt(CY.nodes.n_relieved, 0)})}</div>` : ''}
      <h3 class="wbh">${t('cy.hTable')}</h3>${cyTable()}
      <h3 class="wbh">${t('cy.hCatch')}</h3>
      <div class="tblwrap"><table class="wbft"><tr><th>${t('cy.catch')}</th><th class="num">${t('cy.l1')}</th><th class="num">${t('cy.diff')}</th></tr>
        ${top.map(r => `<tr><td>${esc(r.label)}</td><td class="num">${fmt(r.L1_m3, 0)}</td><td class="num" style="color:${r.diff_m3 > 0 ? 'var(--ok)' : 'var(--muted)'}">${r.diff_m3 > 0 ? '−' + fmt(r.diff_m3, 0) : '0'}</td></tr>`).join('')}
      </table></div>
      <div class="m">${t('cy.outside', {v: fmt((CY.data.outside_client_catch_L1_m3 || 0) / 1e4, 1), p: ((CY.data.outside_client_catch_L1_m3 || 0) / c.L1.flood_m3 * 100).toFixed(0)})}</div>
      <details class="sec wbsec" open><summary>${t('cy.hNotes')}</summary><div class="body">
        ${v ? `<div class="m">${mdb(t('cy.nVal', {r: (v.flood_ratio_city_over_client || 0).toFixed(2), a: fmt(v.runs?.city?.flood_m3 || 0, 0), b: fmt(v.runs?.client?.flood_m3 || 0, 0)}), true)}</div>` : ''}
        ${['n1', 'n2', 'n3', 'n4'].map(k => `<div class="m">${mdb(t('cy.' + k, {noise: fmt(Math.abs(c.noise_elsewhere_m3 || 0), 0), np: Math.abs(c.noise_pct || 0).toFixed(2)}), true)}</div>`).join('')}
      </div></details>`;
    wbBindViews(); cityBasisBind();
    panel.querySelectorAll('[data-cym]').forEach(b => b.onclick = () => { CY.mode = b.dataset.cym;
      panel.querySelectorAll('[data-cym]').forEach(x => x.classList.toggle('on', x === b)); const wasOn = CYP.on; cyMap(); cyFit(); if(wasOn) cypDraw();
      try{ const u = new URL(location.href); u.searchParams.set('m', CY.mode); history.replaceState(null, '', u); }catch(e){} });
    cyMap(); cypInit();
    if(!CY.on){ map.on('moveend', cyDetail); CY.on = true; }
    window._viewOwned = true;
    // ?at=lat,lon&z=15 定点视野（分享「放大到井」那一眼用）
    const at = (U.get('at') || '').split(','), z0 = parseFloat(U.get('z'));
    const fit = (at.length === 2 && isFinite(+at[0]) && isFinite(+at[1]) && isFinite(z0))
      ? () => { try{ map.invalidateSize(); map.setView([+at[0], +at[1]], z0); }catch(e){} } : cyFit;
    fit(); setTimeout(fit, 120); setTimeout(fit, 780);
    try{ const u = new URL(location.href); u.searchParams.set('tab', 'line2p'); u.searchParams.set('v', 'city'); history.replaceState(null, '', u); }catch(e){}
  }

  async function renderLine2p(){
    // 还没按阶段拆分的试点 / 历史页：总览、L1、L2 显示原来的整页；数据与模型、L3、结论只给说明（这些试点还没做到这一步）
    if(!['city', 'city2d'].includes(l2pView) && !R1_SCOPES.includes(l2pView) && ['data', 'l3', 'concl'].includes(NAV.stage)){
      c1Clear(); r1Clear(); wbClear(); plClear(); cyClear(); rbClear();
      if(tl3Layer){ map.removeLayer(tl3Layer); tl3Layer = null; } if(tl3NetLayer){ map.removeLayer(tl3NetLayer); tl3NetLayer = null; }
      panel.innerHTML = `<h2>${esc(scopeName(l2pView))}</h2><div class="m" style="line-height:1.7">${t('nav.pilotNo_' + NAV.stage)}</div>`; return;
    }
    if(l2pView==='c1'){ r1Clear(); return renderC1(); }
    if(l2pView==='rb01') return renderRB01('rb01');
    if(l2pView==='rda') return renderRB01('rda');
    c1Clear(); r1Clear();
    if(l2pView==='wb'){ plClear(); cyClear(); rbClear(); return renderWB(); }
    if(l2pView==='pilot' || l2pView==='city2d'){ cyClear(); rbClear(); return renderPilot(); }
    if(l2pView==='city'){ rbClear(); return renderCity(); }
    if(l2pView==='rb2') return renderRB();
    wbClear(); plClear(); cyClear(); rbClear();
    const idx=await api('/api/three_lines');
    if(!idx||!(idx.catchments||[]).length){ panel.innerHTML=`<div class="empty">${t('liveOff')}</div>`; return; }
    if(!tl3Catch){ tl3Catch=U.get('c')||'all'; const s0=U.get('st'); if(s0) tl3State=s0; }
    const seg=[['all', t('l2p.all')]].concat(idx.catchments.map(c=>[c.catchment, c.catchment+(c.complete?'':' ·'+c.states.length+'/3')]));
    TL3=await api('/api/three_lines?catchment='+encodeURIComponent(tl3Catch));
    if(!TL3){ panel.innerHTML=`<div class="empty">${t('liveOff')}</div>`; return; }
    const isAll=tl3Catch==='all';
    if(!isAll) await tl3NetLoad();
    // 一二维耦合四态与二维播放在「Mussafah_00 四态」视图（renderWB），这里只留一维三区
    panel.innerHTML=wbViews() + `
      <h2>${t('l2p.head')} <small>${t('l2p.sub')}</small></h2>
      <div class="ctrl"><label>${t('l2p.pick')}</label>
        ${segHtml(seg, tl3Catch, 'tl3c')}
        <span class="m">${esc(TL3.scenario||'')}</span></div>
      ${isAll?'':tl3NetCtrl()}
      ${isAll?tl3AllTable(TL3):(TL3.states||[]).map(tl3Card).join('')}
      <div class="promise" style="margin-top:8px"><b>${t('l2p.why')}</b><div class="m">${t('l2p.whyText')}</div></div>
      <div class="m" style="margin-top:6px">${t('l2p.caveat')}</div>`;
    wbBindViews();
    const fac = U.get('fac');
    if(fac && layers.l2plus){
      const hit = layers.l2plus.getLayers().find(l => (l.feature?.properties?.id||'') === fac);
      if(hit){ setTimeout(()=>{ try{ hit.openPopup(); }catch(e){} }, 900); }
    }
    const tl3Url=()=>{ try{ const u=new URL(location.href);
      u.searchParams.set('tab','line2p'); u.searchParams.set('v','1d'); u.searchParams.set('c',tl3Catch);
      if(tl3Catch==='all') u.searchParams.delete('st'); else u.searchParams.set('st',tl3State);
      history.replaceState(null,'',u); }catch(e){} };
    panel.querySelectorAll('[data-tl3c]').forEach(b=>b.onclick=()=>{ tl3Catch=b.dataset.tl3c; tl3State='L2P'; render(); });
    panel.querySelectorAll('[data-net]').forEach(b=>b.onclick=()=>{
      const k=b.dataset.net; TL3NETON[k]=!TL3NETON[k];
      b.classList.toggle('on', TL3NETON[k]);
      tl3NetDraw(); const st=(TL3.states||[]).find(x=>x.key===tl3State); if(st) tl3Legend(st);
    });
    panel.querySelectorAll('[data-tl3]').forEach(b=>b.onclick=()=>{
      tl3State=b.dataset.tl3;
      panel.querySelectorAll('[data-tl3]').forEach(x=>x.classList.toggle('primary',x.dataset.tl3===tl3State));
      tl3Draw(); tl3Url();
    });
    tl3Url();
    // 默认落在**有塘的**那一态：现状① 本来就没有塘，默认停在它上面，
    // 点「在地图上看这一态」什么也看不到，会被当成功能坏了。
    if(!isAll && !(TL3.states||[]).some(x=>x.key===tl3State && x.available && (x.ponds||[]).length)){
      const withPond=(TL3.states||[]).find(x=>x.available && (x.ponds||[]).length);
      if(withPond) tl3State=withPond.key;
    }
    panel.querySelectorAll('[data-tl3]').forEach(x=>x.classList.toggle('primary',x.dataset.tl3===tl3State));
    // 三区一起看时不画地图：三个区隔着几十公里，叠一张图上什么也看不清，选单区再画
    if(isAll){ if(tl3Layer){ map.removeLayer(tl3Layer); tl3Layer=null; }
               if(tl3NetLayer){ map.removeLayer(tl3NetLayer); tl3NetLayer=null; }
               const lg=document.getElementById('legend'); if(lg){ lg.innerHTML=''; lg.style.display='none'; } }
    else tl3Draw();
  }

  async function renderOntology(){
    const O=await loadAgentOntology(); if(!O){ panel.innerHTML=`<div class="empty">${t('ag.offline')}</div>`; return; }
    const EN=lang==='en', L=x=>EN?(x.en||x.zh):(x.zh||x.en);
    const types=O.object_types||[], links=O.link_types||[], rules=O.rules||[], acts=O.actions||[];
    if(!types.some(x=>x.api===ONTSEL)) ONTSEL=O.core;
    const W=460,H=420,cx=W/2,cy=H/2,R=158; const others=types.filter(x=>x.api!==O.core); const pos={}; pos[O.core]=[cx,cy];
    others.forEach((x,i)=>{ const a=-Math.PI/2+i*2*Math.PI/others.length; pos[x.api]=[cx+R*Math.cos(a), cy+R*Math.sin(a)]; });
    { const saved=ontPos(); types.forEach(x=>{ const v=saved[x.api]; if(Array.isArray(v)&&v.length===2&&isFinite(v[0])&&isFinite(v[1])) pos[x.api]=[v[0],v[1]]; }); }
    const edges=links.map(l=>{ const p=pos[l.src], q=pos[l.dst]; if(!p||!q) return ''; const dx=q[0]-p[0], dy=q[1]-p[1], len=Math.hypot(dx,dy)||1; const ex=q[0]-dx/len*44, ey=q[1]-dy/len*18; const mx=p[0]+dx*0.38, my=p[1]+dy*0.38; const hot=(l.src===ONTSEL||l.dst===ONTSEL);
      return `<g class="ont-edge" data-s="${esc(l.src)}" data-d="${esc(l.dst)}"><line x1="${p[0]}" y1="${p[1]}" x2="${ex}" y2="${ey}" stroke="${hot?'var(--acc)':'#cbd5e1'}" stroke-width="${hot?1.8:1.1}" marker-end="url(#ontarr)"><title>${esc(L(l))}</title></line>${hot?`<text x="${mx}" y="${my-3}" font-size="8.5" fill="var(--acc)" text-anchor="middle" paint-order="stroke" stroke="#fbfcfe" stroke-width="3">${esc(L(l))}</text>`:''}</g>`; }).join('');
    const nodes=types.map(x=>{ const [px,py]=pos[x.api]; const sel=x.api===ONTSEL, core=x.api===O.core; const w=core?100:84,h=32;      // 形状画在原点，位置全靠 transform，拖动时只改一个属性
      return `<g class="ont-node" data-ont="${esc(x.api)}" transform="translate(${px},${py})"><rect x="${-w/2}" y="${-h/2}" width="${w}" height="${h}" rx="8" fill="${sel?'var(--acc)':(core?'#fef3c7':'#fff')}" stroke="${sel?'var(--acc)':(core?'#d97706':'#94a3b8')}" stroke-width="${core?2:1}"/><text x="0" y="-2" font-size="11" font-weight="600" text-anchor="middle" fill="${sel?'#fff':'#0f172a'}">${esc(L(x))}</text><text x="0" y="10" font-size="8" text-anchor="middle" fill="${sel?'#e0e7ff':'#64748b'}">${esc(x.api)}</text></g>`; }).join('');
    const svg=`<div class="ont-bar"><span class="m">${t('ont.dragHint')}</span><button class="btn" id="ontreset" style="margin:0;padding:2px 8px;font-size:11px">${t('ont.reset')}</button></div>
      <svg class="ont-graph" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"><defs><marker id="ontarr" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#94a3b8"/></marker></defs><g class="ont-vp">${edges}${nodes}</g></svg>`;
    const sel=types.find(x=>x.api===ONTSEL)||types[0];
    const props=(sel.properties||[]).map(pp=>`<tr><td><code>${esc(pp.name)}</code></td><td>${esc(L(pp))}</td><td class="m">${esc(pp.type||'')}</td><td class="m">${esc(pp.derived_by?(t('ont.derived')+' '+pp.derived_by):(pp.source||''))}</td></tr>`).join('');
    const lout=links.filter(l=>l.src===sel.api), lin=links.filter(l=>l.dst===sel.api);
    const chip=(l,other)=>`<span class="chip ont-go" data-ont="${esc(other)}" title="${esc(l.how||'')}">${esc(L(l))} → ${esc(L(types.find(x=>x.api===other)||{zh:other}))}</span>`;
    const rl=rules.filter(r=>r.applies_to===sel.api).map(r=>`<div class="ont-rule"><b>${esc(L(r))}</b> <span class="m">· ${esc(r.kind)}</span><div class="m">${esc(EN?r.logic_en:r.logic_zh)}</div>${r.source?`<div class="m">${t('ont.source')}: ${esc(r.source)}</div>`:''}</div>`).join('');
    const actIds=new Set(ONT_EXTRA_ACTS[sel.api]||[]); acts.forEach(a=>{ if((a.params||[]).some(pp=>ONT_TYPE_OF_PARAM[pp.type]===sel.api||ONT_TYPE_OF_PARAM[pp.name]===sel.api)) actIds.add(a.api); });
    const al=acts.filter(a=>actIds.has(a.api)).map(a=>`<tr><td><b>${esc(L(a))}</b><div class="m">${esc((EN?a.desc_en:a.desc_zh)||'')}</div></td><td class="m">${(a.params||[]).map(pp=>pp.name+(pp.required?'*':'')).join(', ')}${a.side_effect&&a.side_effect!=='none'?`<div style="color:var(--warn)">${esc(a.side_effect)}</div>`:''}</td><td>${(a.examples||[]).slice(0,1).map(ex=>`<span class="chip ont-ask" data-ask="${esc(ex)}">${esc(ex)}</span>`).join('')}</td></tr>`).join('');
    const allRules=rules.map(r=>`<tr><td><b>${esc(L(r))}</b></td><td class="m">${esc(r.kind)} · ${esc(r.applies_to)}</td><td class="m">${esc(EN?r.logic_en:r.logic_zh)}</td></tr>`).join('');
    const allActs=acts.map(a=>`<tr><td><b>${esc(L(a))}</b> <span class="m">${esc(a.api)}</span></td><td class="m">${(a.params||[]).map(pp=>pp.name+(pp.required?'*':'')).join(', ')}</td><td class="m">${esc(a.side_effect||'none')}</td></tr>`).join('');
    panel.innerHTML=`<h2>${t('ont.title')}</h2><div class="m">${t('ont.sub',{core:esc(L(types.find(x=>x.api===O.core)||{zh:O.core}))})}</div>
      <div class="tiles"><div class="tile"><div class="v">${types.length}</div><div class="k">${t('ont.types')}</div></div><div class="tile"><div class="v">${links.length}</div><div class="k">${t('ont.links')}</div></div><div class="tile"><div class="v">${rules.length}</div><div class="k">${t('ont.rules')}</div></div><div class="tile"><div class="v">${acts.length}</div><div class="k">${t('ont.actions')}</div></div></div>
      ${svg}
      ${ontGallery(O)}
      <div class="ont-sec"><h3>${esc(L(sel))} <span class="m">${esc(sel.api)} · ${t('ont.pk')} ${esc(sel.pk||'')}${sel.scoped?' · '+t('ont.scoped'):''}</span></h3><div class="m">${esc(sel.desc||'')}</div></div>
      <div class="ont-sec"><h3>${t('ont.props')} <span class="m">(${(sel.properties||[]).length})</span></h3><table class="ont-prop"><thead><tr><th>${t('ont.propName')}</th><th></th><th>${t('ont.propType')}</th><th>${t('ont.propSource')}</th></tr></thead><tbody>${props||`<tr><td colspan="4" class="m">—</td></tr>`}</tbody></table></div>
      <div class="ont-sec"><h3>${t('ont.linksOut')}</h3><div class="ont-links">${lout.map(l=>chip(l,l.dst)).join('')||'<span class="m">—</span>'}</div><h3 style="margin-top:6px">${t('ont.linksIn')}</h3><div class="ont-links">${lin.map(l=>chip(l,l.src)).join('')||'<span class="m">—</span>'}</div></div>
      <div class="ont-sec"><h3>${t('ont.rulesFor')} <span class="m">(${rules.filter(r=>r.applies_to===sel.api).length})</span></h3>${rl||'<div class="m">—</div>'}</div>
      <div class="ont-sec"><h3>${t('ont.actsFor')} <span class="m">(${actIds.size}) · ${t('ont.ask')}</span></h3><table class="ont-prop"><tbody>${al||`<tr><td class="m">—</td></tr>`}</tbody></table></div>
      <details class="ont-sec"><summary>${t('ont.allRules')} (${rules.length})</summary><table class="ont-prop"><tbody>${allRules}</tbody></table></details>
      <details class="ont-sec"><summary>${t('ont.allActions')} (${acts.length})</summary><table class="ont-prop"><tbody>${allActs}</tbody></table></details>`;
    panel.querySelectorAll('.ont-go').forEach(g=>g.onclick=()=>{ ONTSEL=g.dataset.ont; renderOntology(); });
    panel.querySelectorAll('.kgt[data-kind]').forEach(b=>b.onclick=()=>{ ONTKIND=(ONTKIND===b.dataset.kind?null:b.dataset.kind); renderOntology();
      setTimeout(()=>{ const d=document.getElementById('kgd'); if(d) d.scrollIntoView({block:'nearest',behavior:'smooth'}); }, 60); });
    ontBindGraph(pos);
    panel.querySelectorAll('.ont-ask').forEach(c=>c.onclick=()=>{ openAgent(); sendAgent(c.dataset.ask); });
  }
  async function renderTrust(){
    const A=S['假设']||{}; const h=health;
    let DC=null; try{ DC=await load('dmt_compare.json'); }catch(e){ DC=null; }
    const GWD=await getGwm();
    const gwmTable=()=>{ const head=`<details class="sec" open><summary>${t('gc.title')}`; if(!GWD) return `${head}</summary><div class="body m">${t('gc.offline')}</div></details>`; if(GWD.error) return `${head}</summary><div class="body m">${esc(GWD.error)}</div></details>`;
      const st=GWD.stats||{}, g=st.gwm||{}, hs=st.hotspots||{}, sy=st.system||{}, ag=g.agreement||{}, sc=GWD.scenario||{}, R=st.radius_m||300;
      const pct=(a,b)=>b?`${Math.round(a/b*100)}%`:'—'; const ORD=['Very High','High','Medium','Low'];
      const crit=Object.entries(hs.by_criticality||{}).filter(([k])=>k).sort((a,b)=>ORD.indexOf(a[0])-ORD.indexOf(b[0])).map(([k,v])=>`${lang==='en'?k:critZh(k)} ${v.covered}/${v.n-(v.outside_grid||0)}`).join(' · ');
      const both=on('gwm')&&on('hs');
      return `${head} <small>${esc(lang==='en'?(sc.name_en||sc.name||''):(sc.name||''))}${GWD.matched_by==='nearest_rain'?` · ${t('gc.nearest',{p:sc.ours_p24_mm})}`:''}</small></summary><div class="body">
        <div class="m" style="margin-bottom:6px">${t('gc.sub',{r:R})} <button class="btn" style="font-size:11px;padding:1px 8px;margin-left:6px" onclick="showGwmPair()">${both?t('gc.shown'):t('gc.show')}</button></div>
        <table><tr><th>${t('gc.colWho')}</th><th>${t('gc.colN')}</th><th>${t('gc.colHit')}</th><th>${t('gc.colRead')}</th></tr>
        <tr><td><b>${t('layers.gwm')}</b><div class="m mono">${esc(GWD.run_id||'')}</div></td><td>${g.n??'—'}<div class="m">${vol(g.volume_m3||0)}</div></td><td><b>${pct(g.with_hotspot,g.n)}</b> ${t('gc.hitHs',{a:g.with_hotspot??0,n:g.n??0})}<div class="m">${t('gc.agreeRow',{b:ag.both||0,h:ag.hotspot||0,s:ag.system||0,o:ag.model_only||0})}</div></td><td class="m">${t('gc.readGwm',{o:ag.model_only||0,v:vol(g.model_only_volume_m3||0)})}</td></tr>
        <tr><td><b>${t('layers.hs')}</b></td><td>${hs.n??'—'}${hs.outside_grid!=null?`<div class="m">${t('gc.outGrid',{n:hs.outside_grid})}</div>`:''}</td><td><b>${pct(hs.covered,hs.in_grid??hs.n)}</b> ${t('gc.hitGwm',{a:hs.covered??0,n:hs.in_grid??hs.n??0,r:R})}<div class="m">${crit}</div></td><td class="m">${t('gc.readHs')}</td></tr>
        <tr><td><b>${t('gc.sysName')}</b><div class="m">${esc(sy.scenario||'')}</div></td><td>${sy.n??'—'}</td><td><b>${pct(sy.covered,sy.n)}</b> ${t('gc.hitGwm',{a:sy.covered??0,n:sy.n??0,r:R})}</td><td class="m">${t('gc.readSys')}</td></tr></table>
        <div class="m">${t('gc.note')}</div></div></details>`; };
    const dmtTable=()=>{ if(!DC) return ''; const rows=Object.values(DC).map(C=>{const hv=C.hotspot_vs_overflow||{}, hi=C.hotspots_inside||{}, fi=C.flood_points_inside||{}, on=C.our_network_inside||{}, of=C.our_flood_inside_m3||{}, pf=C.parsons_flood_m3||{};
        const two=(o,k1,k2)=>`${o[k1]!=null?vol(o[k1]):'—'} / ${o[k2]!=null?vol(o[k2]):'—'}`;
        return `<tr><td><b>${esc(C.model)}</b><div class="m">${esc(C.area||'')} · ${fmt(C.boundary_ha,0)} ha</div></td><td>${hi.n??'—'}<div class="m">${hv.n?`${hv.within}/${hv.n} ≤${hv.radius_m} m`:t('dmtc.noAttr')}</div></td><td>${fi.n??'—'}<div class="m">${fi.volume_m3!=null?vol(fi.volume_m3):''}</div></td><td>${two(of,'10cc','2024-04')}</td><td>${two(pf,'10cc','2024-04')}</td><td>${on.length_km!=null?`${fmt(on.length_km,0)} / ${C.dmt_conduit_km??'—'}`:'—'}<div class="m">${on.over_capacity_10yr_share!=null?t('dmtc.over',{p:Math.round(on.over_capacity_10yr_share*100)}):''}${C.dmt_overflow_manholes!=null?` · ${t('dmtc.dmtOf',{n:C.dmt_overflow_manholes,m:C.dmt_manholes})}`:''}</div></td></tr>`;}).join('');
      return `<details class="sec" open><summary>${t('dmtc.title')} <small>${t('dmtc.sub')}</small></summary><div class="body"><table><tr><th>${t('dmtc.colModel')}</th><th>${t('dmtc.colHs')}</th><th>${t('dmtc.colFp')}</th><th>${t('dmtc.colOurs')}</th><th>${t('dmtc.colParsons')}</th><th>${t('dmtc.colNet')}</th></tr>${rows}</table><div class="m">${t('dmtc.note')}</div></div></details>`; };
    const srcPaths=['Flood Management Data - Origen - Batch1 › *Stormwater_Hotspot_Analysis_v4*.xlsx','DMT WA-726-1 (2022) §3.2 / §4.8 · '+t('bi.tables')+' 3-2, 3-3, 3-6, 4-5','pipe › dmt_irrigationstormwater_abudhabi_network › sw_edge','pipe › …abudhabi › inlet / catchbasin','pipe › …abudhabi › outfall','pipe › …abudhabi › pond / sw_reservoirstructure / soakaway / ps_pump','makani_sync_full › storm_water_structure','liveability_data_20260730 › fact_oi_indicators','DTM_z40_customer › AUH_DTM_5m_Z40.TIF（EPSG:32640，5 m）'];
    const src=t('src').map(([c,n],i)=>`<tr><td><div>${esc(c)}</div><div class="m mono">${srcPaths[i]}</div></td><td style="white-space:nowrap;width:22%">${esc(n.replace('{n}',fmt(S['管段数'])))}</td></tr>`).join('');
    const row2=(arr,vars)=>{const [a,b]=arr; return `<tr><td style="width:26%">${esc(a)}</td><td>${esc((b||'').replace(/\{(\w+)\}/g,(_,k)=>vars[k]??''))}</td></tr>`;};
    const cA=A['径流系数']; const cTxt=(cA&&typeof cA==='object')?(lang==='en'?`${cA['中位']} (area-weighted by land cover: building ${cA['按地表覆盖加权'].building} / road ${cA['按地表覆盖加权'].road} / parking ${cA['按地表覆盖加权'].parking} / green ${cA['按地表覆盖加权'].green} / plot ${cA['按地表覆盖加权'].plot} / open land ${cA['按地表覆盖加权'].open})`:`${cA['中位']}（按地表覆盖加权：建筑 ${cA['按地表覆盖加权'].building} / 路面 ${cA['按地表覆盖加权'].road} / 停车场 ${cA['按地表覆盖加权'].parking} / 绿地 ${cA['按地表覆盖加权'].green} / 地块内 ${cA['按地表覆盖加权'].plot} / 空地 ${cA['按地表覆盖加权'].open}）`):cA;
    const sc=A['子汇水区']; const areaTxt=sc?(lang==='en'?`Per-inlet sub-catchments from the 5 m DTM (10 m grid, D8 flow directions, ${sc.n_with_area.toLocaleString()} of ${sc.n_inlets.toLocaleString()} inlets, median ${sc.area_median_m2} m²); plot interiors self-drain and are excluded; inlets with no public area get the 100 m² floor`:`5 m DTM 子汇水区（10 m 网格、D8 流向，${sc.n_with_area.toLocaleString()}/${sc.n_inlets.toLocaleString()} 个进水口，面积中位 ${sc.area_median_m2} m²）；地块内自排不计入；公共面积为 0 的进水口按 100 m² 下限计`):(lang==='en'?`upstream inlets × ${A['每进水口汇水m2']} m² or upstream pipe length × ${A['道路汇水宽m']} m, whichever is larger (assumption)`:`上游进水口数 × ${A['每进水口汇水m2']} m²，与上游管长 × ${A['道路汇水宽m']} m 取大者（假设）`);
    const vars={n:S['打断环移除边数'],a:A['每进水口汇水m2'],w:A['道路汇水宽m'],c:cTxt,t:A['高潮位m'],d:(lang==='en'?String(A['标高基准']??'').replace('（已用 5 m DTM 实测校核）',' (checked against the 5 m DTM)'):A['标高基准']),area:areaTxt};
    panel.innerHTML=`
      <div class="promise"><b>${t('trustHead')}</b><div class="m">${t('trustSub')}</div></div>
      <h2>${t('bounds')}</h2>
      <table><tr><td><span class="status" style="background:var(--ok)">${esc(t('b1')[0])}</span></td><td>${esc(t('b1')[1])}</td></tr><tr><td><span class="status" style="background:var(--warn)">${esc(t('b2')[0])}</span></td><td>${esc(t('b2')[1])}</td></tr><tr><td><span class="status" style="background:var(--bad)">${esc(t('b3')[0])}</span></td><td>${esc(t('b3')[1])}</td></tr></table>
      ${gwmTable()}
      ${dmtTable()}
      <details class="sec" open><summary>${t('sources')} <small>${t('sourcesSub')}</small></summary><div class="body"><table>${src}</table></div></details>
      <details class="sec"><summary>${t('how')} <small>${t('howSub')}</small></summary><div class="body"><table>${row2(t('how1'),vars)}${row2(t('how2'),vars)}${row2(t('how3'),vars)}${row2(t('how4'),vars)}${row2(t('how5'),vars)}${row2(t('how6'),vars)}</table></div></details>
      <details class="sec"><summary>${t('liveWhat')}</summary><div class="body"><table>${row2([t('live1')[0], h?t('live1')[1]:t('live1off')],{n:fmt(h?h.n_edges:0)})}${row2(t('live2'),{})}${row2(t('live3'),{t:S['生成时间']})}</table></div></details>
      <details class="sec"><summary>${t('verify')}</summary><div class="body m">${t('verifyText')}</div></details>`;
  }

  // ---------------- 雨强滑杆：当场算（专家控件） ----------------
  let rainI=30, rainLive='', timer=null;
  function bindRain(){ const rain=document.getElementById('rain'), rainv=document.getElementById('rainv'); if(!rain) return;
    rain.oninput=()=>{ rainI=+rain.value; rainv.textContent=rainI+' mm/h'; clearTimeout(timer); timer=setTimeout(recompute,150); }; }
  async function recompute(){
    const i=rainI;
    const r=await api('/api/scenario?i_mmh='+i);
    if(r){ scen=r; rainLive=`${t('justComputed')} ${r.computed_at} · ${r.computed_ms} ${t('ms')} · ${fmt(r.n_edges_over)} ${t('reachesOver')}`; }
    else { const near=Object.entries(SCEN).sort((a,b)=>Math.abs(a[1]-i)-Math.abs(b[1]-i))[0]; scen={i_mmh:i,surcharge_share:(S['超能力管长份额']||{})[near[0]],computed_ms:'—',zones:[]}; rainLive=`${t('offlineNearest')} · ${scn(near[0])}`; }
    updateScenBits();
  }
  function nfKpi(){ const nf=scen&&scen.network_flood; if(!nf||nf.error) return `<b>—</b><span>${t('kpi.nf')}</span><small>${nf&&nf.error?esc(nf.error):t('kpi.nfOff')}</small>`; return `<b>${vol(nf.total_m3)}</b><span>${t('kpi.nf')}</span><small>${t('kpi.nfNote',{n:fmt(nf.edges_spilling),p:Math.round((nf.spilling_len_share||0)*1000)/10})}</small>`; }
  function updateScenBits(){
    const c=document.getElementById('kpi-cap'); if(c) c.innerHTML=capKpi();
    const nfk=document.getElementById('kpi-nf'); if(nfk) nfk.innerHTML=nfKpi();
    const z=document.getElementById('zonerank-sub'); if(z) z.textContent=scen?t('zoneRankLive',{i:scen.i_mmh}):t('zoneRankPre');
    const l=document.getElementById('rainlive'); if(l){ l.textContent=rainLive; }
    if(document.getElementById('zonetable')) renderZoneTable();
  }

  // ---------------- 重现期：一个开关同时驱动管网筛查（15 分钟雨强）与积水点 → 调度 ----------------
  let RP=(health&&health.active_rp!=null)?String(health.active_rp):null, RPINFO=null; const IDF=(health&&health.idf)||null;
  loadAssumptions();
  const SCENS=(health&&health.scenarios&&health.scenarios.length)?health.scenarios:(IDF?Object.keys(IDF.return_periods||{}).sort((a,b)=>+a-+b).map(y=>({key:String(y),kind:'rp',label:`${y} 年`,label_en:`${y}-yr`,i15_mmh:IDF.return_periods[y].i15_mmh,p24_mm:IDF.return_periods[y].p24_mm})):null);
  const scenLabel=s=>s?(lang==='en'?(s.label_en||s.label):s.label):'';
  function renderRpChips(){
    const box=document.getElementById('rpchips'); if(!box) return;
    if(!SCENS){ box.innerHTML=`<span class="m">${t('rpl.offline')}</span>`; return; }
    box.innerHTML=SCENS.map(sc=>`<span class="chip ${RP===String(sc.key)?'on':''} k-${sc.kind||'rp'}" data-rp="${esc(sc.key)}" title="${esc(t('rp.tip',{i:sc.i15_mmh,p:sc.p24_mm}))}${sc.source?'&#10;'+esc(lang==='en'?(sc.source_en||sc.source):sc.source):''}">${esc(scenLabel(sc))}</span>`).join('');
    box.querySelectorAll('[data-rp]').forEach(c=>c.onclick=()=>setReturnPeriod(c.dataset.rp));
    rpScope();
  }
  // 「防线2+」页用的是一二维设计雨型（M61 / R61 / R96 / D24 / U61），重现期选择不牵动它：
  // 切到这页时把重现期一栏置灰、按钮不响应，并写明原因；切回别的页签恢复，原来选的重现期不变（2026-10-02）
  function rpScope(){
    const box=document.getElementById('rpbox'); if(!box) return;
    const off = tab==='line2p';
    box.classList.toggle('off', off);
    box.querySelectorAll('[data-rp]').forEach(c=>{ c.setAttribute('aria-disabled', off?'true':'false'); c.tabIndex = off ? -1 : 0; });
    let n=document.getElementById('rpoffnote');
    if(off){
      if(!n){ n=document.createElement('span'); n.id='rpoffnote'; n.className='offnote'; box.appendChild(n); }
      n.textContent=t('rp.offLine2p'); n.title=t('rp.offLine2pTip');
      liveEl.hidden=true;
    } else { if(n) n.remove(); liveEl.hidden=false; }
  }

  async function setReturnPeriod(key){
    if(!SCENS) return;
    if(tab==='line2p') return;                              // 这页不受重现期牵动（见 rpScope）
    RP=String(key); renderRpChips(); liveEl.textContent=t('rp.computing'); liveEl.className='live';
    const r=await api('/api/return_period?yr='+encodeURIComponent(RP));
    loadAssumptions();
    if(!r||r.error){ liveEl.textContent=(r&&r.error)||t('liveOff'); liveEl.className='live'; return; }
    RPINFO=r; scen=r.scenario; rainI=r.i15_mmh;
    rainLive=`${t('justComputed')} ${r.scenario.computed_at} · ${r.scenario.computed_ms} ${t('ms')} · ${fmt(r.scenario.n_edges_over)} ${t('reachesOver')}`;
    if(r.dispatch&&!r.dispatch.error){ DP=r.dispatch; if(layers.flood||layers.fleet) await ensureDispatchLayers(); }
    await refreshGwmLayer();
    const src=lang==='en'?(r.idf_source_en||r.idf_source||''):(r.idf_source||'');
    const rng=r.scenario&&r.scenario.i_range_mmh?` (${r.scenario.i_range_mmh[0]}–${r.scenario.i_range_mmh[1]})`:'';
    liveEl.textContent=(r.kind&&r.kind!=='rp'?`${lang==='en'?(r.label_en||r.label):r.label} · `:'')+t('rp.status',{i:String(r.i15_mmh)+rng,p:r.p24_mm,s:Math.round(r.total_ms/1000),g:r.gwm_error?t('rp.gwmOff'):(r.gwm_cached?t('rp.gwmCached'):t('rp.gwmRun'))});
    document.getElementById('rpinfo').title=t('rp.explain')+(src?`\n${src}`:'')+(r.gwm_error?`\n${r.gwm_error}`:''); liveEl.className='live on';
    render();
  }

  chrome();
  if(!C1_ONLY) await buildLayers();               // 只看 C1 时没有片区 / 建议图层
  // 深链接：?lang=en&tab=line3&rp=10&fp=ADM-62 ；旧的 tab=props|sched|standard|residual|dispatch 仍可用
  if(U.get('rain')){ rainI=+U.get('rain'); await recompute(); }
  const t0=U.get('tab');
  if(U.get('scope')) navTo(NAV.scope, NAV.stage);                                // 新链接：?scope=rda&stage=l3（范围 + 阶段决定一切，tab= 只是兼容旧版）
  else if(t0 && (LEGACY[t0] || document.querySelector(`nav button[data-t="${t0}"]`))) go(t0); else render();
  if(U.get('rp') && SCENS){ await setReturnPeriod(U.get('rp')); }
  if(U.get('hs')){ await showHotspot(U.get('hs')); }
  if(U.get('gwm')==='1'){ await showGwmPair(); }
  if(U.get('crit') === 'C1' || U.get('c1') === '1'){ for(const k of ['zones', 'bott', 'props']){ const cb = cbOf(k); if(cb && cb.checked) await toggleLayer(k, false); }
    await ensureC1Layer(); const cb = cbOf('c1'); if(cb) cb.checked = true; if(layers.c1) layers.c1.addTo(map); if(c1Lbl) c1Lbl.addTo(map);
    layersCount(); renderLegend();
    if(!U.get('crit') && layers.c1){ window._viewOwned = true; const b = layers.c1.getBounds(); setTimeout(() => { try{ map.invalidateSize(); map.fitBounds(b, {paddingTopLeft: [20, 20], paddingBottomRight: [420, 20], animate: false}); }catch(e){} }, 400); } }
  if(U.get('mu') === '1'){ for(const k of ['zones', 'bott', 'props']){ const cb = cbOf(k); if(cb && cb.checked) await toggleLayer(k, false); }
    await toggleLayer('mu', true);
    if(layers.mu){ window._viewOwned = true; const dm = layers.mu.getLayers().slice(-1)[0]; const b = (dm && dm.getBounds ? dm.getBounds() : layers.mu.getBounds()); setTimeout(() => { try{ map.invalidateSize(); map.fitBounds(b.pad(.08), {paddingTopLeft: [300, 20], paddingBottomRight: [20, 20], animate: false}); }catch(e){} }, 500); } }
  if(U.get('c1u') === '1'){ for(const k of ['zones', 'bott', 'props']){ const cb = cbOf(k); if(cb && cb.checked) await toggleLayer(k, false); }
    await toggleLayer('c1u', true); if(U.get('c1') === '1') await toggleLayer('c1', true);
    if(layers.c1u){ window._viewOwned = true; const b = layers.c1u.getBounds(); setTimeout(() => { try{ map.invalidateSize(); map.fitBounds(b.pad(.08), {paddingTopLeft: [300, 20], paddingBottomRight: [20, 20], animate: false}); }catch(e){} }, 500); } }
  if(U.get('rb3') === '1'){ for(const k of ['zones', 'bott', 'props']){ const cb = cbOf(k); if(cb && cb.checked) await toggleLayer(k, false); }
    await toggleLayer('rb3', true); if(U.get('catch') === '1') await toggleLayer('catch', true);
    if(layers.rb3){ window._viewOwned = true; const b = layers.rb3.getBounds(); setTimeout(() => { try{ map.invalidateSize(); map.fitBounds(b.pad(.15), {paddingTopLeft: [300, 20], paddingBottomRight: [20, 20], animate: false}); }catch(e){} }, 500); } }
  if(U.get('crit')){ await critFocus(U.get('crit'), (U.get('hl') || '').split(',').map(x => x.trim()).filter(Boolean)); }
  else if(U.get('hl')){ const items=await resolveHighlight(U.get('hl').split(',').map(x=>x.trim()).filter(Boolean), U.get('hlk')||'points'); highlightPoints(items, true); }
  if(U.get('ask')){ openAgent(); await sendAgent(U.get('ask')); }
  else { try{ if(localStorage.getItem('sw_agent_open')==='1' && U.get('agent')!=='0') openAgent(); }catch(e){} }
  if(U.get('agent')==='1' && document.getElementById('agent').hidden) openAgent();
  if(U.get('pid')){ await selectProposal(+U.get('pid'), true); if(U.get('whatif')==='1'){ setTimeout(()=>{ const b=document.querySelector(`#det-${U.get('pid')} [data-a="whatif"]`); b&&b.click(); }, 700); } }
})();
