// 管理端（多智能体）：智能体清单与配置、会话与调用轨迹、一二维计算任务队列（批准 / 驳回 / 暂停 / 恢复 / 取消）、操作记录。
// 接口：/api/admin/*（swbrain/agents/admin_routes.py），管理员口令与控制台访问口令分开。
(async function(){
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const main = $('#main');
  const api = async (path, body) => {
    const r = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    let j = {}; try { j = await r.json(); } catch(e) {}
    if (!r.ok) { const e = new Error(j.error || ('HTTP ' + r.status)); e.code = r.status; e.body = j; throw e; }
    return j;
  };
  const ST = {pending:['待批准','warn'], approved:['已批准（排队）','acc'], running:['运行中','acc'], paused:['已暂停','warn'], done:['完成','ok'], failed:['失败','bad'], cancelled:['已取消',''], rejected:['已驳回','']};
  const chip = (t, k) => `<span class="chip ${k||''}">${esc(t)}</span>`;
  let tab = 'agents', timer = null;

  // ---------------------------------------------------------------- 登录
  async function gate(){
    let w; try { w = await api('/api/admin/whoami'); } catch(e) { main.innerHTML = `<div class="card err">连不上服务：${esc(e.message)}</div>`; return false; }
    if (w.mode === 'sdip') {        // 统一身份：没有管理员口令，持有 SWBRAIN.ADMIN 才进得来
      if (w.admin) return true;
      main.innerHTML = `<div class="card login"><h2>没有管理权限</h2><div class="muted">当前账号${w.who ? '（' + esc(w.who.name || w.who.username || w.who.principal_id) + '）' : ''}没有职能 SWBRAIN.ADMIN（系统管理）。请平台管理员授权，或换一个账号：<a href="/" onclick="fetch('/auth/logout',{method:'POST'}).then(r=>r.json()).then(d=>location.href=d.redirect);return false">注销</a></div></div>`;
      return false;
    }
    if (!w.configured) { main.innerHTML = `<div class="card login"><h2>管理端没有开</h2><div class="muted">在服务器的 config.toml 里给 [serve] 设 admin_key（管理员口令，与控制台访问口令分开），重启服务后再来。</div></div>`; return false; }
    if (w.admin) return true;
    main.innerHTML = `<form class="card login" id="lf"><h2>管理员口令</h2><div class="muted small">与控制台的访问口令不同；在服务器 config.toml 的 [serve].admin_key。</div>
      <input type="password" id="lk" autocomplete="current-password" autofocus><button class="btn pri" type="submit">进入</button><div class="err" id="le"></div></form>`;
    $('#lf').onsubmit = async ev => { ev.preventDefault();
      try { await api('/api/admin/login', {key: $('#lk').value}); boot(); } catch(e) { $('#le').textContent = e.message; } };
    return false;
  }

  // ---------------------------------------------------------------- 智能体
  async function renderAgents(){
    const d = await api('/api/admin/agents');
    main.innerHTML = `<div class="card"><h2>多智能体</h2><div class="muted small">主智能体按「分派关键词」把问题交给专业智能体（至少命中一个才交；都没命中交给调度研判）。停用的智能体不再被分派；停用的动作任何智能体都调不了（含 DeepSeek Harness 经 MCP 调用）。改动即时生效，存 out/agents/overrides.json。</div>
      <div class="row" style="margin-top:8px"><button class="btn pri" id="newagent">＋ 新建智能体</button><span class="muted small">从现有动作里挑一组、起名字、写职责、给分派关键词。不能造出新的动作能力——那要写代码（docs/智能体架构.md 第 7 节）。</span></div>
      <div id="newform"></div></div>
      <div class="grid">${d.agents.map(agentCard).join('')}</div>`;
    main.querySelectorAll('[data-save]').forEach(b => b.onclick = () => saveAgent(b.dataset.save));
    main.querySelectorAll('[data-reset]').forEach(b => b.onclick = async () => { await api('/api/admin/agent_reset', {id: b.dataset.reset}); render(); });
    main.querySelectorAll('[data-del]').forEach(b => b.onclick = () => deleteAgent(b.dataset.del));
    $('#newagent').onclick = openNewForm;
  }

  // ---------------------------------------------------------------- 新建智能体（重新打包现有动作，2026-10-02）
  async function openNewForm(){
    const box = $('#newform');
    if (box.innerHTML) { box.innerHTML = ''; return; }                 // 再点一次收起
    const d = await api('/api/admin/actions');
    const OWN = {dispatch: '调度研判', hydro: '一二维计算', report: '报告交付', qa: '数据质检'};
    const groups = {};
    d.actions.forEach(a => (groups[a.owner] = groups[a.owner] || []).push(a));
    const pick = Object.keys(groups).map(o => `<fieldset class="acts"><legend class="small">${esc(OWN[o] || o)} 名下</legend>${groups[o].map(a =>
      `<label title="${esc(a.desc)}"><input type="checkbox" name="act" value="${esc(a.api)}">${esc(a.zh)} <span class="muted small">${esc(a.api)}${a.side_effect!=='none'?' · <b>会写数据</b>':''}</span></label>`).join('')}</fieldset>`).join('');
    box.innerHTML = `<form id="nf" class="card" style="margin-top:10px">
      <div class="kv">
        <label for="nf-id">id</label><input id="nf-id" required pattern="[a-z][a-z0-9_]{1,30}" placeholder="小写字母开头，如 point_watch">
        <label for="nf-zh">名称</label><input id="nf-zh" required maxlength="30" placeholder="积水点值守">
        <label for="nf-en">英文名（可空）</label><input id="nf-en" maxlength="60" placeholder="Point watch">
        <label for="nf-role">职责</label><textarea id="nf-role" required maxlength="400" placeholder="只看积水点现状与历史，不派泵、不签预警。"></textarea>
        <label for="nf-kw">分派关键词</label><textarea id="nf-kw" required placeholder="逗号或换行分隔：值守，积水点现状"></textarea>
      </div>
      <div><b class="small">挑动作（至少一个）</b>${pick}</div>
      <div class="row"><span style="flex:1"></span><button type="button" class="btn" id="nf-cancel">取消</button><button class="btn pri">创建</button></div>
      <div class="small" id="nf-msg" aria-live="polite"></div></form>`;
    $('#nf-cancel').onclick = () => { box.innerHTML = ''; };
    $('#nf').onsubmit = async ev => {
      ev.preventDefault();
      const body = {id: $('#nf-id').value.trim(), zh: $('#nf-zh').value.trim(), en: $('#nf-en').value.trim(), role: $('#nf-role').value.trim(),
                    keywords: $('#nf-kw').value.split(/[，,、\n]+/).map(s => s.trim()).filter(Boolean),
                    actions: [...box.querySelectorAll('input[name=act]:checked')].map(c => c.value)};
      const m = $('#nf-msg');
      try { await api('/api/admin/agent_create', body); m.textContent = '已创建。控制台里的主智能体马上会分派给它；Harness 页面要重新打开才看得到。'; m.className = 'small'; setTimeout(render, 1200); }
      catch(e) { m.textContent = e.message; m.className = 'small err'; }
    };
  }
  async function deleteAgent(id){
    if (!confirm(`删除自建智能体 ${id}？它的配置与 Harness 预设一起删掉，内置智能体不受影响。`)) return;
    try { await api('/api/admin/agent_delete', {id}); render(); }
    catch(e) { const m = $('#msg-' + id); if (m) { m.textContent = e.message; m.className = 'small err'; } }
  }
  function agentCard(a){
    const u = a.usage || {};
    const acts = a.actions.length ? `<div class="acts">${a.actions.map(x => `<label><input type="checkbox" data-act="${a.id}" value="${esc(x.api)}" ${x.enabled?'checked':''}>${esc(x.zh)} <span class="muted small">${esc(x.api)}${x.side_effect!=='none'?' · 写文件':''}</span></label>`).join('')}</div>` : '<div class="muted small">没有业务动作（只做分派与汇总）</div>';
    const kw = a.id === 'orchestrator' || a.id === 'dispatch' ? `<div class="muted small">${a.id==='dispatch'?'兜底：没有命中其他智能体的问题都交给它':'—'}</div>`
      : `<textarea data-kw="${a.id}" title="逗号或换行分隔">${esc((a.keywords||[]).join('，'))}</textarea>`;
    return `<div class="card agent">
      <div class="top"><h3>${esc(a.zh)}</h3><span class="muted small">${esc(a.en)} · ${esc(a.id)}</span>
        ${a.enabled ? chip('启用','ok') : chip('已停用','bad')} ${chip(a.planner_zh,'acc')} ${a.custom ? chip('自建','acc') : ''} ${a.overridden.length ? chip('有自定义配置','warn') : ''}</div>
      <div>${esc(a.role)}</div>
      <div class="kv"><span>调用</span><span>${u.calls ?? 0} 次${u.last ? ' · 最近 ' + esc(u.last) : ''}${u.avg_ms != null ? ' · 平均 ' + (u.avg_ms/1000).toFixed(1) + ' s' : ''}${u.errors ? ' · 失败步骤 ' + u.errors : ''}</span>
        <span>人设</span><span class="small">${esc(a.prompt)}</span></div>
      <div><b class="small">名下动作（${a.n_actions}）</b>${acts}</div>
      <div><b class="small">分派关键词</b>${kw}</div>
      <div class="row">${a.id !== 'orchestrator' ? `<label class="row small"><input type="checkbox" data-en="${a.id}" ${a.enabled?'checked':''}> 启用</label>` : '<span class="muted small">主智能体不能停用</span>'}
        <span style="flex:1"></span>${a.custom ? `<button class="btn" data-del="${a.id}">删除</button>` : ''}<button class="btn" data-reset="${a.id}" ${a.overridden.length?'':'disabled'}>恢复缺省</button><button class="btn pri" data-save="${a.id}">保存</button></div>
      <div class="small" id="msg-${a.id}"></div></div>`;
  }
  async function saveAgent(id){
    const patch = {};
    const en = main.querySelector(`[data-en="${id}"]`); if (en) patch.enabled = en.checked;
    const acts = [...main.querySelectorAll(`[data-act="${id}"]`)]; if (acts.length) patch.disabled_actions = acts.filter(c => !c.checked).map(c => c.value);
    const kw = main.querySelector(`[data-kw="${id}"]`); if (kw) patch.keywords = kw.value.split(/[，,、\n]+/).map(s => s.trim()).filter(Boolean);
    const m = $('#msg-' + id);
    try { await api('/api/admin/agent', {id, patch}); m.textContent = '已保存'; m.className = 'small'; setTimeout(render, 600); }
    catch(e) { m.textContent = e.message; m.className = 'small err'; }
  }

  // ---------------------------------------------------------------- 会话与轨迹
  let sessQ = '', sessAgent = '';
  async function renderSessions(){
    const qs = new URLSearchParams({limit: 60}); if (sessQ) qs.set('q', sessQ); if (sessAgent) qs.set('agent', sessAgent);
    const d = await api('/api/admin/sessions?' + qs);
    main.innerHTML = `<div class="card"><h2>试问一句</h2><div class="muted small">走和控制台对话抽屉同一个入口（/api/agent），看主智能体分派给谁、为什么。</div>
        <form class="row" id="tf" style="margin-top:8px"><input type="text" id="tq" placeholder="例：模型版四个态算完了吗？质检过了没有？" style="flex:1;min-width:240px"><button class="btn pri">发送</button></form><div id="tr"></div></div>
      <div class="card"><div class="row"><h2 style="margin:0">最近的对话</h2><span style="flex:1"></span>
        <select id="fa" style="width:auto"><option value="">全部智能体</option>${['dispatch','hydro','report','qa'].map(a => `<option value="${a}" ${sessAgent===a?'selected':''}>${{dispatch:'调度研判',hydro:'一二维计算',report:'报告交付',qa:'数据质检'}[a]}</option>`).join('')}</select>
        <input type="text" id="fq" placeholder="搜问题" value="${esc(sessQ)}" style="width:200px"></div>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:10px">${d.sessions.length ? d.sessions.map(x => sessCard(x)).join('') : '<div class="muted">还没有记录（多智能体上线后的对话会记在 out/agents/sessions.jsonl）</div>'}</div></div>`;
    $('#fa').onchange = e => { sessAgent = e.target.value; render(); };
    $('#fq').onchange = e => { sessQ = e.target.value.trim(); render(); };
    $('#tf').onsubmit = async ev => { ev.preventDefault(); const q = $('#tq').value.trim(); if (!q) return; $('#tr').innerHTML = '<div class="muted">思考中…</div>';
      try { const r = await api('/api/agent', {message: q}); $('#tr').innerHTML = sessCard({ts: '刚才', message: q, routes: r.route || [], results: (r.agents||[]).map(a => ({...a, steps: (r.steps||[]).filter(s => s.agent === a.agent), reply: (r.agents.length === 1 ? r.reply : '')})), reply: r.reply, ms: r.ms}, true); }
      catch(e) { $('#tr').innerHTML = `<div class="err">${esc(e.message)}</div>`; } };
  }
  const AZ = {dispatch:'调度研判', hydro:'一二维计算', report:'报告交付', qa:'数据质检'};
  function sessCard(s, open){
    return `<div class="sess"><div class="row"><span class="q">${esc(s.message)}</span><span style="flex:1"></span><span class="muted small">${esc(s.ts)} · ${((s.ms||0)/1000).toFixed(1)} s</span></div>
      <div class="route">${(s.routes||[]).map(r => chip('→ ' + (AZ[r.agent] || r.agent || '无'), r.agent === 'dispatch' ? '' : 'acc') + ` <span class="muted small">${esc(r.reason)}</span>`).join(' ')}</div>
      ${(s.results||[]).map(x => `<div class="steps">${esc(AZ[x.agent]||x.agent)}（${esc(x.planner||'—')}）：${(x.steps||[]).map(st => `${esc(st.action)}${st.ok===false?' ✗ '+esc(st.error||''):' ✓'} ${st.ms!=null?Math.round(st.ms)+' ms':''}`).join(' · ') || '无动作'}</div>`).join('')}
      <details ${open?'open':''}><summary class="small">回答</summary><div style="white-space:pre-wrap">${esc(s.reply)}</div></details></div>`;
  }

  // ---------------------------------------------------------------- 计算任务
  let pendingOp = null, openLog = null;
  async function renderJobs(){
    const d = await api('/api/admin/jobs');
    const runs = (d.runs||[]).filter(r => !r.error);
    main.innerHTML = `<div class="card"><h2>机器上正在算的一二维计算</h2><div class="muted small">含命令行后台链起的（只读）；经队列起的标「队列」。进度 = 已模拟时长 / 总时长；剩余时间按到目前的平均速度外推，雨峰后会更慢。</div>
        <div class="tw"><table><thead><tr><th>容器</th><th>情景 · 态</th><th>进度</th><th></th><th>剩余（粗估）</th><th>最深 · 湿区</th><th>日志更新</th></tr></thead><tbody>
        ${runs.length ? runs.map(r => `<tr><td>${esc(r.container)} ${r.via_queue?chip('队列','acc'):''}</td><td>${esc(r.name)} · ${esc(r.state)}</td>
          <td><div class="bar ${r.paused?'paused':''}"><i style="width:${Math.min(100, r.pct||0)}%"></i></div></td><td>${r.t_h ?? '—'} / ${r.end_h ?? '—'} h（${r.pct ?? '—'}%）${r.paused?' '+chip('暂停','warn'):''}${r.finished?' '+chip('已算完','ok'):''}</td>
          <td>${r.paused ? '—' : (r.eta_h != null ? r.eta_h + ' h' : '—')}</td><td>${r.hmax_m ?? '—'} m · ${r.wet_ha ?? '—'} ha</td><td class="muted small">${esc(r.updated||'')}</td></tr>`).join('') : '<tr><td colspan="7" class="muted">现在没有在算的一二维计算</td></tr>'}
        </tbody></table></div>${(d.runs||[]).filter(r => r.error).map(r => `<div class="err small">${esc(r.error)}</div>`).join('')}</div>
      <div class="card"><h2>计算申请</h2><div class="muted small">一二维计算智能体 / 报告交付智能体只能提交申请；批准后才起计算（同时只跑一条，后面的排队）。暂停 = docker pause，保留进度。</div>
        <div class="tw"><table><thead><tr><th>申请</th><th>步骤</th><th>预计</th><th>提交</th><th>状态</th><th>操作</th></tr></thead><tbody>
        ${(d.jobs||[]).length ? d.jobs.map(jobRow).join('') : '<tr><td colspan="6" class="muted">没有申请。在对话里说「申请重算模型版 L1 L2 L2+planned」会生成一条。</td></tr>'}
        </tbody></table></div><div id="logbox"></div></div>`;
    main.querySelectorAll('[data-op]').forEach(b => b.onclick = () => { pendingOp = {id: b.dataset.id, op: b.dataset.op}; render(); });
    main.querySelectorAll('[data-cancelop]').forEach(b => b.onclick = () => { pendingOp = null; render(); });
    main.querySelectorAll('[data-doop]').forEach(b => b.onclick = async () => { const n = main.querySelector('#opnote'); const p = pendingOp; pendingOp = null;
      try { await api('/api/admin/job', {id: p.id, op: p.op, note: n ? n.value : ''}); } catch(e) { alert_(e.message); } render(); });
    main.querySelectorAll('[data-log]').forEach(b => b.onclick = () => { openLog = openLog === b.dataset.log ? null : b.dataset.log; render(); });
    if (openLog) { try { const l = await api('/api/admin/job_log?id=' + openLog + '&n=150'); $('#logbox').innerHTML = `<h3 style="margin-top:12px">日志 ${esc(openLog)}</h3><pre>${esc(l.log || '（还没有日志）')}</pre>`; } catch(e) {} }
  }
  // ---------------------------------------------------------------- 新建计算（2026-10）：计算域任意范围面 + 网格 / 雨 / 运行名
  // 计算域四种来源：行政区、汇水区（可多选，合并成一个面）、地图上自己画、上传面图层。C1 是工程项目编码，不是汇水区。
  const NJ = {open:false, src:'preset', tag:'M61', picks:{admin:new Set(), catch:new Set()}, opts:{}, draw:[], drawn:null, upload:null, runEdited:false, plan:null, map:null, layers:{}};
  function njSpec(){
    const v = id => { const e = $('#' + id); return e ? e.value : ''; };
    const num = id => v(id) === '' ? null : Number(v(id));
    const P = {kind: v('nj-kind') || 'compute', res: num('nj-res'), tif_res: num('nj-tif'), sww_res: num('nj-sww'), rain: num('nj-rain'), dur: num('nj-dur'),
               force: !!($('#nj-force') || {}).checked, run: NJ.runEdited ? v('nj-run').trim() : ''};
    const states = [...main.querySelectorAll('[name=nj-st]:checked')].map(x => x.value);
    if (NJ.src === 'preset') { P.tag = NJ.tag; P.states = states; P.then_v3 = !!($('#nj-v3') || {}).checked; }
    else {
      const d = {source: NJ.src, buffer_m: num('nj-buf') || 0, multi: ($('#nj-multi') || {}).checked ? 'largest' : 'error'};
      if (NJ.src === 'admin' || NJ.src === 'catch') d.ids = [...NJ.picks[NJ.src]];
      if (NJ.src === 'draw') d.geojson = NJ.drawn;
      if (NJ.src === 'upload' && NJ.upload) { d.upload_id = NJ.upload.upload_id; d.layer = v('nj-layer') || null; d.field = v('nj-field') || null;
        d.values = [...main.querySelectorAll('[name=nj-val]:checked')].map(x => x.value); }
      P.domain = d; P.states = ['L1'];
    }
    return P;
  }
  async function openJobForm(){
    const box = $('#njbox');
    if (NJ.open) { NJ.open = false; box.innerHTML = ''; NJ.map = null; return; }
    NJ.open = true; NJ.form = NJ.form || await api('/api/admin/job_form');
    const F = NJ.form, D = F.defaults, L = F.limits;
    const seg = (name, items, cur) => `<div class="seg">${items.map(([k, t]) => `<label><input type="radio" name="${name}" value="${esc(k)}" ${k === cur ? 'checked' : ''}>${esc(t)}</label>`).join('')}</div>`;
    const nin = (id, val, [lo, hi], ph) => `<input type="number" id="${id}" min="${lo}" max="${hi}" step="any" value="${val ?? ''}" placeholder="${esc(ph || '')}">`;
    box.innerHTML = `<form id="njf" class="card" style="margin-top:10px" autocomplete="off"><h3>新建计算</h3>
     <div class="nj"><div>
      <fieldset><legend>计算范围</legend>
        ${seg('nj-src', [['preset','预置情景（C1 工程）'], ['admin','行政区'], ['catch','汇水区'], ['draw','自己画'], ['upload','上传面图层']], NJ.src)}
        <div id="nj-srcbox" style="margin-top:8px"></div></fieldset>
      <fieldset><legend>降雨与网格</legend><div class="f">
        <label for="nj-rain">雨量 mm</label>${nin('nj-rain', null, L.rain, '缺省按情景（61）')}
        <label for="nj-dur">历时 h</label>${nin('nj-dur', null, L.dur, '缺省按情景（2）；模拟到历时 + 4 h')}
        <label for="nj-res">计算网格 m</label>${nin('nj-res', D.res, L.res)}
        <label for="nj-tif">GeoTIFF m</label>${nin('nj-tif', null, L.tif_res, '缺省 = 计算网格')}
        <label for="nj-sww">.sww 网格 m</label>${nin('nj-sww', null, L.sww_res, '缺省 = 计算网格')}
      </div></fieldset>
      <fieldset><legend>任务</legend><div class="f">
        <label for="nj-kind">类型</label><select id="nj-kind">${Object.entries(F.kinds).filter(([k]) => k !== 'prep').map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join('')}</select>
        <label for="nj-run">运行名</label><input type="text" id="nj-run" maxlength="40" placeholder="自动生成">
        <span></span><span class="muted small" id="nj-runhint">不改就用自动生成的名字；改了参数会自动换名，不覆盖已有结果</span>
        <label for="nj-reason">为什么算</label><input type="text" id="nj-reason" maxlength="300" placeholder="给批准的人看">
        <span></span><label class="small"><input type="checkbox" id="nj-force"> 强制重做（产物已在也重算）</label>
      </div></fieldset>
      <div class="planbox" id="nj-plan">改参数后点「预检」，看范围、单元数和预计耗时。</div>
      <div class="row" style="margin-top:8px"><button type="button" class="btn" id="nj-check">预检</button><button type="submit" class="btn pri" id="nj-go" disabled>提交申请</button>
        <button type="button" class="btn" id="nj-close">关闭</button><span class="err small" id="nj-err"></span></div>
     </div><div><div id="njmap"></div><div class="muted small" id="nj-maphint" style="margin-top:4px"></div></div></div></form>`;
    NJ.map = L.map('njmap', {zoomControl:true}).setView([24.42, 54.52], 10);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© OpenStreetMap'}).addTo(NJ.map);
    NJ.layers = {opts: L.layerGroup().addTo(NJ.map), draw: L.layerGroup().addTo(NJ.map), plan: L.layerGroup().addTo(NJ.map)};
    main.querySelectorAll('[name=nj-src]').forEach(r => r.onchange = () => { NJ.src = r.value; njSource(); njDirty(); });
    $('#njf').addEventListener('input', e => { if (e.target.id === 'nj-run') NJ.runEdited = e.target.value.trim() !== ''; njDirty(); });
    $('#nj-check').onclick = njCheck;
    $('#nj-close').onclick = openJobForm;
    $('#njf').onsubmit = njSubmit;
    njSource();
  }
  function njDirty(){ NJ.plan = null; const g = $('#nj-go'); if (g) g.disabled = true; }
  async function njSource(){
    const b = $('#nj-srcbox'), hint = $('#nj-maphint');
    NJ.layers.opts.clearLayers(); NJ.layers.draw.clearLayers(); NJ.layers.plan.clearLayers(); NJ.map.off('click'); NJ.map.off('dblclick'); NJ.map.doubleClickZoom.enable();
    hint.textContent = '';
    if (NJ.src === 'preset') {
      const P = NJ.form.presets;
      b.innerHTML = `<div class="f"><label for="nj-tag">情景</label><select id="nj-tag">${P.map(p => `<option value="${p.tag}" ${p.tag === NJ.tag ? 'selected' : ''}>${p.tag}（${p.km2} km² · ${p.rain} mm / ${p.dur} h）</option>`).join('')}</select>
        <label>态</label><div class="seg">${NJ.form.states.map(s => `<label><input type="checkbox" name="nj-st" value="${s}" ${['L1','L2','L2P'].includes(s) ? 'checked' : ''}>${s}</label>`).join('')}</div>
        <span></span><label class="small"><input type="checkbox" id="nj-v3"> 算完接着设计并算 L2+suggested v3</label></div>
        <div class="muted small" style="margin-top:6px">预置情景是 C1 工程的几种计算域；L2 / L2+planned / v3 是 C1 工程方案的设施，只有这里能算。</div>`;
      $('#nj-tag').onchange = e => { NJ.tag = e.target.value; njDirty(); };
      hint.textContent = '预检后地图上显示计算域。'; return;
    }
    const tail = `<div class="f" style="margin-top:8px"><label for="nj-buf">外扩 m</label><input type="number" id="nj-buf" min="0" max="5000" step="any" placeholder="0">
      <span></span><label class="small"><input type="checkbox" id="nj-multi"> 几块不相连时只留最大块（缺省报错）</label></div>
      <div class="muted small" style="margin-top:6px">自定义范围只算 L1（现状管网 + 现状泵站）。几个面会合并成一个计算域，内部的洞填上。</div>`;
    if (NJ.src === 'admin' || NJ.src === 'catch') {
      b.innerHTML = `<input type="text" id="nj-q" placeholder="搜名称"><div class="picklist" id="nj-pick" style="margin-top:6px"><span class="muted">装载中…</span></div>${tail}`;
      try { NJ.opts[NJ.src] = NJ.opts[NJ.src] || await api('/api/admin/domain_options?kind=' + NJ.src + '&geom=1'); }
      catch(e) { $('#nj-pick').innerHTML = `<span class="err">${esc(e.message)}</span>`; return; }
      const O = NJ.opts[NJ.src], sel = NJ.picks[NJ.src];
      const style = id => sel.has(id) ? {color:'#0b5fd7', weight:2, fillOpacity:.35} : {color:'#64748b', weight:1, fillOpacity:.06};
      const lyr = L.geoJSON(O.geojson, {style: f => style(f.properties.id), onEachFeature: (f, l) => { l.bindTooltip(f.properties.name); l.on('click', () => toggle(f.properties.id)); }}).addTo(NJ.layers.opts);
      const list = () => { const q = ($('#nj-q').value || '').toLowerCase();
        $('#nj-pick').innerHTML = O.items.filter(i => !q || (i.name + i.group).toLowerCase().includes(q)).map(i =>
          `<label><input type="checkbox" value="${esc(i.id)}" ${sel.has(i.id) ? 'checked' : ''}>${esc(i.name)} <span class="muted small">${esc(i.group !== i.name ? i.group : '')} ${i.km2} km²</span></label>`).join('') || '<span class="muted">没有匹配的</span>';
        $('#nj-pick').querySelectorAll('input').forEach(c => c.onchange = () => toggle(c.value)); };
      const toggle = id => { sel.has(id) ? sel.delete(id) : sel.add(id); lyr.setStyle(f => style(f.properties.id)); list(); njDirty(); };
      $('#nj-q').oninput = list; list();
      try { NJ.map.fitBounds(lyr.getBounds(), {padding:[10,10]}); } catch(e) {}
      hint.textContent = '点地图上的面或勾列表都能选；可以多选。'; return;
    }
    if (NJ.src === 'draw') {
      b.innerHTML = `<div class="row"><button type="button" class="btn" id="nj-dstart">开始画</button><button type="button" class="btn" id="nj-dend" disabled>完成</button><button type="button" class="btn" id="nj-dclr">清除</button><span class="muted small" id="nj-dinfo"></span></div>${tail}`;
      const redraw = (closed) => { NJ.layers.draw.clearLayers(); if (!NJ.draw.length) return;
        (closed ? L.polygon(NJ.draw, {color:'#0b5fd7', weight:2}) : L.polyline(NJ.draw, {color:'#0b5fd7', dashArray:'4 4'})).addTo(NJ.layers.draw);
        NJ.draw.forEach(p => L.circleMarker(p, {radius:3, color:'#0b5fd7'}).addTo(NJ.layers.draw)); $('#nj-dinfo').textContent = `${NJ.draw.length} 个点`; };
      const finish = () => { if (NJ.draw.length < 3) { $('#nj-dinfo').textContent = '至少 3 个点'; return; }
        NJ.map.off('click'); NJ.map.off('dblclick'); NJ.map.doubleClickZoom.enable(); $('#nj-dend').disabled = true;
        const ring = NJ.draw.map(([la, lo]) => [lo, la]); ring.push(ring[0]);
        NJ.drawn = {type:'Polygon', coordinates:[ring]}; redraw(true); njDirty(); hint.textContent = '画好了。点「预检」。'; };
      $('#nj-dstart').onclick = () => { NJ.draw = []; NJ.drawn = null; redraw(); njDirty(); $('#nj-dend').disabled = false; NJ.map.doubleClickZoom.disable();
        NJ.map.on('click', e => { NJ.draw.push([e.latlng.lat, e.latlng.lng]); redraw(); }); NJ.map.on('dblclick', finish);
        hint.textContent = '在地图上逐点点击，双击或点「完成」闭合。'; };
      $('#nj-dend').onclick = finish;
      $('#nj-dclr').onclick = () => { NJ.draw = []; NJ.drawn = null; redraw(); njDirty(); };
      if (NJ.drawn) { NJ.draw = NJ.drawn.coordinates[0].slice(0, -1).map(([lo, la]) => [la, lo]); redraw(true); }
      hint.textContent = hint.textContent || '点「开始画」。'; return;
    }
    // upload
    b.innerHTML = `<input type="file" id="nj-file" accept=".zip,.geojson,.json,.gpkg,.kml"><div class="muted small">shapefile 请把 .shp / .shx / .dbf / .prj 打成一个 zip；要带坐标系；≤ ${NJ.form.max_upload_mb} MB</div><div id="nj-upinfo" style="margin-top:6px"></div>${tail}`;
    const show = () => { const U = NJ.upload; if (!U) return;
      $('#nj-upinfo').innerHTML = `<div class="small">${esc(U.file)} · ${U.n} 个要素 · ${esc(U.crs || '无坐标系')}</div><div class="f" style="margin-top:6px">
        ${U.layers.length > 1 ? `<label for="nj-layer">图层</label><select id="nj-layer">${U.layers.map(l => `<option>${esc(l)}</option>`).join('')}</select>` : ''}
        <label for="nj-field">按字段筛</label><select id="nj-field"><option value="">（全部要素）</option>${U.fields.map(f => `<option>${esc(f)}</option>`).join('')}</select></div><div class="picklist" id="nj-vals" style="margin-top:6px" hidden></div>`;
      $('#nj-field').onchange = e => { const f = e.target.value, box = $('#nj-vals'); box.hidden = !f;
        box.innerHTML = f ? (U.values[f] || []).map(x => `<label><input type="checkbox" name="nj-val" value="${esc(x)}">${esc(x)}</label>`).join('') : ''; njDirty(); }; };
    $('#nj-file').onchange = async e => { const f = e.target.files[0]; if (!f) return; $('#nj-upinfo').innerHTML = '<span class="muted">上传中…</span>';
      try { const buf = new Uint8Array(await f.arrayBuffer()); let s = ''; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
        NJ.upload = await api('/api/admin/domain_upload', {name: f.name, data: btoa(s)}); show(); njDirty(); }
      catch(err) { NJ.upload = null; $('#nj-upinfo').innerHTML = `<span class="err">${esc(err.message)}</span>`; } };
    show(); hint.textContent = '上传后点「预检」，地图上显示合并后的计算域。';
  }
  async function njCheck(){
    const err = $('#nj-err'), box = $('#nj-plan'); err.textContent = ''; box.innerHTML = '<span class="muted">预检中…（行政区第一次要从库里读，可能要十几秒）</span>';
    try {
      const p = (await api('/api/admin/job_plan', njSpec())).plan; NJ.plan = p;
      if (!NJ.runEdited) $('#nj-run').placeholder = p.run;
      NJ.layers.plan.clearLayers();
      if (p.domain && p.domain.geojson) { const g = L.geoJSON(p.domain.geojson, {style:{color:'#b45309', weight:2, fillOpacity:.12, dashArray:'6 3'}}).addTo(NJ.layers.plan); NJ.map.fitBounds(g.getBounds(), {padding:[10,10]}); }
      const P = p.params || {};
      box.innerHTML = `<div><b>${esc(p.run)}</b>${p.custom ? ' ' + chip('自定义', 'acc') : ''}${p.duplicate_of ? ' ' + chip('已有同样的申请 ' + p.duplicate_of, 'warn') : ''}</div>
        <div>${p.domain ? esc(p.domain.what) + ' · ' : ''}${p.km2} km² · 网格 ${P.res} m（约 ${(p.n_tri || 0).toLocaleString()} 个三角单元）· ${P.rain} mm / ${P.dur} h，模拟 ${P.sim_h} h</div>
        <div>态 ${esc((p.states || []).join(' / ') || '—')} · 预计墙钟约 <b>${p.est_h} h</b>（粗估）</div>
        <div class="small" style="font-family:var(--mono)">${(p.steps || []).map(esc).join(' → ')}</div>
        ${[...(p.warnings || [])].map(w => `<div class="warn small">⚠ ${esc(w)}</div>`).join('')}
        ${[...((p.domain || {}).notes || []), ...(p.notes || [])].map(n => `<div class="muted small">· ${esc(n)}</div>`).join('')}`;
      $('#nj-go').disabled = !!p.duplicate_of;
    } catch(e) { box.innerHTML = ''; err.textContent = e.message; NJ.plan = null; $('#nj-go').disabled = true; }
  }
  async function njSubmit(ev){
    ev.preventDefault(); if (!NJ.plan) return;
    const err = $('#nj-err'); err.textContent = '';
    try { const r = await api('/api/admin/job_create', {...njSpec(), reason: $('#nj-reason').value.trim()});
      NJ.open = false; NJ.plan = null; NJ.runEdited = false; alert_(`已提交 ${r.job.id}（${r.job.run || r.job.title}），状态：待批准。`); render(); }
    catch(e) { err.textContent = e.message; }
  }

  const OPS = {approve:['批准','ok',['pending']], reject:['驳回','bad',['pending']], pause:['暂停','warn',['running']], resume:['恢复','ok',['paused']], cancel:['取消','bad',['pending','approved','running','paused']]};
  const OPQ = {approve:'批准后立刻开始计算（或排在正在跑的那条后面），占满 CPU 数小时。确定？', reject:'驳回这条申请？', pause:'暂停这条计算（docker pause，保留进度）？', resume:'恢复计算？', cancel:'取消会停掉这条任务的容器，已算的进度丢失。确定？'};
  function jobRow(j){
    const [zt, zk] = ST[j.status] || [j.status, ''];
    const by = j.requested_by || {};
    let ops = Object.entries(OPS).filter(([k, v]) => v[2].includes(j.status)).map(([k, v]) => `<button class="btn ${v[1]}" data-op="${k}" data-id="${j.id}">${v[0]}</button>`).join(' ');
    if (pendingOp && pendingOp.id === j.id) ops = `<span class="confirm small">${esc(OPQ[pendingOp.op])} <input type="text" id="opnote" placeholder="备注（可选）" style="width:140px"> <button class="btn ${OPS[pendingOp.op][1]}" data-doop="1">${OPS[pendingOp.op][0]}</button> <button class="btn" data-cancelop="1">算了</button></span>`;
    const prog = (j.progress||[]).map(p => `${esc(p.state)} ${p.pct ?? '—'}%`).join(' · ');
    return `<tr><td><b>${esc(j.title)}</b><div class="muted small">${esc(j.id)}${j.reason ? ' · ' + esc(j.reason) : ''}</div>${j.note ? `<div class="small">${esc(j.note)}</div>` : ''}</td>
      <td class="small" style="font-family:var(--mono)">${(j.steps||[]).map(esc).join('<br>')}</td><td>${j.est_h ?? '—'} h</td>
      <td class="small">${esc(j.created)}<br><span class="muted">${esc(AZ[by.agent] || by.agent || '')}</span></td>
      <td>${chip(zt, zk)}${prog ? `<div class="small">${prog}</div>` : ''}${j.finished ? `<div class="muted small">结束 ${esc(j.finished)}</div>` : ''}</td>
      <td>${ops} ${j.log ? `<button class="btn" data-log="${j.id}">日志</button>` : ''}</td></tr>`;
  }
  function alert_(m){ const d = document.createElement('div'); d.className = 'card err'; d.textContent = m; main.prepend(d); }

  // ---------------------------------------------------------------- 操作记录
  async function renderAudit(){
    const d = await api('/api/admin/audit?limit=200');
    const OPZ = {login:'登录', agent_config:'改配置', agent_reset:'恢复缺省', job_approve:'批准计算', job_reject:'驳回计算', job_pause:'暂停计算', job_resume:'恢复计算', job_cancel:'取消计算'};
    main.innerHTML = `<div class="card"><h2>管理员操作记录</h2><div class="muted small">out/agents/admin_audit.jsonl</div><div class="tw"><table><thead><tr><th>时间</th><th>操作</th><th>对象</th><th>详情</th></tr></thead><tbody>
      ${d.audit.length ? d.audit.map(a => `<tr><td class="small">${esc(a.ts)}</td><td>${esc(OPZ[a.op] || a.op)}${a.ok === false ? ' ' + chip('失败','bad') : ''}</td><td>${esc(a.agent || a.job || '')}</td><td class="small muted">${esc(a.note || (a.patch ? JSON.stringify(a.patch) : ''))}</td></tr>`).join('') : '<tr><td colspan="4" class="muted">没有记录</td></tr>'}
      </tbody></table></div></div>`;
  }

  // ---------------------------------------------------------------- 框架
  async function render(){
    clearTimeout(timer);
    try { await ({agents: renderAgents, sessions: renderSessions, jobs: renderJobs, audit: renderAudit}[tab])(); }
    catch(e) { if (e.code === 401 || e.code === 403) { $('#nav').hidden = true; await gate(); return; } main.innerHTML = `<div class="card err">${esc(e.message)}</div>`; }
    if (tab === 'jobs' && !pendingOp) timer = setTimeout(render, 30000);          // 计算页 30 s 刷一次（确认框开着时不刷）
  }
  async function boot(){
    if (!(await gate())) return;
    $('#nav').hidden = false;
    document.querySelectorAll('#nav [data-t]').forEach(b => b.onclick = () => { tab = b.dataset.t; pendingOp = null; openLog = null; document.querySelectorAll('#nav [data-t]').forEach(x => x.classList.toggle('on', x === b)); render(); });
    $('#logout').onclick = async () => { await api('/api/admin/logout', {}); location.reload(); };
    const h = (location.hash || '').slice(1); if (['agents','sessions','jobs','audit'].includes(h)) { tab = h; document.querySelectorAll('#nav [data-t]').forEach(x => x.classList.toggle('on', x.dataset.t === h)); }
    render();
  }
  boot();
})();
