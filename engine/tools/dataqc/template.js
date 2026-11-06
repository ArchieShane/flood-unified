const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const fmt = n => n == null ? '—' : Number(n).toLocaleString('zh-CN');
const pct = (a, b) => (!b ? '—' : (100 * a / b < 0.1 && a > 0 ? '<0.1' : (100 * a / b).toFixed(1)) + '%');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const SEV = {crit:'影响建模', warn:'需处理', info:'提示', ok:'通过'};
const ROUTE = {desk:'桌面修正', supply:'补交资料', survey:'外业复测', none:'不需整改', std:'对照设计标准'};
const GROUP = {A:'A　建模必需的属性', B:'B　几何', C:'C　拓扑与连通', D:'D　水力合理性（重力管）', F:'F　设施（泵站与排口）'};
const has = k => Array.isArray(D[k]) ? D[k].length > 0 : !!D[k];

// 页头；没有的部分整节不出，其余章节按顺序重新编号
$('title').textContent = D.title;
$('sub').textContent = D.subtitle;
$('meta').innerHTML = D.meta.map(([k, v]) => `<span>${esc(k)}：<b class="mono">${esc(v)}</b></span>`).join('');
$('footer').innerHTML = D.footer;
document.querySelectorAll('section[data-sec]').forEach(s => { if (!has(s.dataset.sec)) s.remove(); });
const CN = '一二三四五六七八九十';
let hn = 0;
document.querySelectorAll('.wrap > h2, .wrap > section > h2').forEach(h => {
  if (!/^附录/.test(h.textContent)) h.textContent = CN[hn++] + '、' + h.textContent.replace(/^[一二三四五六七八九十]+、/, '');
});

// 结论
$('verdicts').innerHTML = D.verdicts.map(v => `<div class="card"><h3>${esc(v.title)}</h3>
  <p class="verdict ${v.level}">${esc(v.verdict)}</p><ul>${v.points.map(p => `<li>${p}</li>`).join('')}</ul></div>`).join('');

// 概况
$('overview').innerHTML = `<div class="tbl"><table><tr><th>数据</th><th>图层</th><th class="r">要素数</th><th>说明</th></tr>${
  D.overview.map(r => `<tr><td>${esc(r.data)}</td><td class="mono">${esc(r.layer)}</td><td class="r num">${fmt(r.count)}</td><td>${r.note || ''}</td></tr>`).join('')}</table></div>`;

// 管线
if (has('pipeChecks')) {
  let fSev = 'all', fRoute = 'all';
  const chips = [['all', '全部'], ['crit', '影响建模'], ['warn', '需处理'], ['info', '提示']];
  const rchips = [['all', '全部途径'], ['desk', '桌面修正'], ['supply', '补交资料'], ['survey', '外业复测'], ['std', '对照设计标准']];
  const drawPipes = () => {
    let h = '<tr><th>编号</th><th>检查项</th><th>程度</th><th class="r">数量</th><th class="r">占比</th><th>对建模的影响</th><th>整改途径</th><th>整改建议</th><th>样例编号</th></tr>';
    let g = '';
    for (const c of D.pipeChecks) {
      const sev = c.notDone ? 'info' : c.count ? c.severity : 'ok';
      if (fSev !== 'all' && sev !== fSev) continue;
      if (fRoute !== 'all' && c.route !== fRoute) continue;
      if (c.group !== g) { g = c.group; h += `<tr class="grp"><td colspan="9">${GROUP[g] || g}</td></tr>`; }
      const note = c.notDone ? `没做：${esc(c.notDone)}` : (c.note || '');
      h += `<tr><td class="mono">${c.code}</td><td>${esc(c.name)}${note ? `<div class="muted" style="font-size:.8rem">${note}</div>` : ''}</td>
        <td><span class="sev ${sev}">${c.notDone ? '未做' : SEV[sev]}</span></td><td class="r num">${c.notDone ? '—' : fmt(c.count)}</td>
        <td class="r num">${c.notDone ? '—' : pct(c.count, c.total)}</td><td>${esc(c.impact)}</td>
        <td><span class="route ${c.count ? c.route : 'none'}">${c.count ? ROUTE[c.route] : '—'}</span></td>
        <td>${c.count ? esc(c.fix) : ''}</td><td class="samples mono">${c.samples.map(esc).join('<br>')}</td></tr>`;
    }
    $('pipeTable').innerHTML = h;
  };
  const drawFilters = () => {
    $('filters').innerHTML = chips.map(([k, t]) => `<button class="chip" data-s="${k}" aria-pressed="${fSev === k}">${t}</button>`).join('')
      + '<span style="width:12px"></span>' + rchips.map(([k, t]) => `<button class="chip" data-r="${k}" aria-pressed="${fRoute === k}">${t}</button>`).join('');
    $('filters').querySelectorAll('button').forEach(b => b.onclick = () => {
      if (b.dataset.s) fSev = b.dataset.s;
      if (b.dataset.r) fRoute = b.dataset.r;
      drawFilters(); drawPipes();
    });
  };
  drawFilters(); drawPipes();
  $('thTable').innerHTML = '<tr><th>阈值</th><th class="r">取值</th><th>回答的问题</th><th>出处</th></tr>' +
    D.thresholds.map(t => `<tr><td>${esc(t.key)}</td><td class="r num">${t.value} ${esc(t.unit)}</td><td><span class="sev ${t.meaning === '标准符合' ? 'ok' : 'info'}">${esc(t.meaning)}</span></td><td>${esc(t.source)}${t.overridden ? '（已按客户要求调整）' : ''}</td></tr>`).join('');
}

// DTM
if (has('dtmChecks')) {
  $('dtmIntro').innerHTML = D.dtmIntro;
  $('dtmTable').innerHTML = '<tr><th>编号</th><th>检查项</th><th>结果</th><th>程度</th><th>说明</th></tr>' +
    D.dtmChecks.map(r => `<tr><td class="mono">${r.code}</td><td>${esc(r.name)}</td><td class="num">${esc(r.value)}</td><td><span class="sev ${r.sev}">${SEV[r.sev]}</span></td><td>${r.note || ''}</td></tr>`).join('');
  $('dtmStats').innerHTML = '<tr><th>统计量</th><th class="r">值</th><th>说明</th></tr>' +
    D.dtmStats.map(r => `<tr><td>${esc(r[0])}</td><td class="r num">${esc(r[1])}</td><td>${r[2] || ''}</td></tr>`).join('');
  $('elevNote').textContent = D.elevNote;
  const em = Math.max(1, ...D.elevBins.map(b => b[2]));
  $('elevHist').innerHTML = D.elevBins.map(b => `<span class="mono">${b[0]}～${b[1]} m</span><div class="b" style="width:${(100 * b[2] / em).toFixed(1)}%"></div><span class="num r">${fmt(b[2])}</span>`).join('');
  $('lowTitle').textContent = D.lowTitle;
  $('lowIntro').innerHTML = D.lowIntro;
  if (D.low.length) {
    const near = D.low.some(a => a.nearestPipeM != null);
    $('lowTable').innerHTML = '<tr><th>#</th><th class="r">最低值</th><th class="r">面积</th><th>最低点位置（UTM 40N）</th>' + (near ? '<th class="r">离最近管线</th>' : '') + '</tr>' +
      D.low.map((a, i) => `<tr><td class="num">${i + 1}</td><td class="r num">${a.min.toFixed(2)} m</td><td class="r num">${a.areaHa} ha</td><td class="mono">${fmt(a.x)}, ${fmt(a.y)}</td>${near ? `<td class="r num">${fmt(a.nearestPipeM)} m</td>` : ''}</tr>`).join('');
  } else {
    $('lowTable').closest('.tbl').remove();
  }
}

// 管线与 DTM
if (has('eChecks')) {
  $('eTable').innerHTML = '<tr><th>编号</th><th>检查项</th><th>程度</th><th class="r">数量</th><th class="r">占比</th><th>对建模的影响</th><th>整改途径</th><th>整改建议</th><th>按图层</th></tr>' +
    D.eChecks.map(c => {
      const sev = c.count || c.code === 'E1' ? c.severity : 'ok';
      return `<tr><td class="mono">${c.code}</td><td>${esc(c.name)}</td>
      <td><span class="sev ${sev}">${SEV[sev]}</span></td><td class="r num">${c.display ?? fmt(c.count)}</td><td class="r num">${c.code === 'E1' ? '' : pct(c.count, c.total)}</td>
      <td>${esc(c.impact)}</td><td><span class="route ${c.route}">${ROUTE[c.route]}</span></td><td>${esc(c.fix)}</td>
      <td class="samples">${Object.entries(c.byLayer || {}).map(([k, v]) => `${esc(k)} ${fmt(v)}`).join('<br>')}</td></tr>`;
    }).join('');
  $('histNote').textContent = D.histNote;
  const hmax = Math.max(1, ...D.hist.counts);
  $('hist').innerHTML = D.hist.bins.map((b, i) => `<span class="mono">${b} m</span><div class="b ${i >= 3 && i <= 5 ? '' : 'off'}" style="width:${(100 * D.hist.counts[i] / hmax).toFixed(1)}%"></div><span class="num r">${fmt(D.hist.counts[i])}</span>`).join('');
}

// 建议
$('routes').innerHTML = ['supply', 'survey', 'desk', 'std'].filter(k => (D.advice[k] || []).length).map(k =>
  `<div class="card"><h3><span class="route ${k}">${ROUTE[k]}</span></h3><ol>${D.advice[k].map(a => `<li>${a}</li>`).join('')}</ol></div>`).join('');

// dq 对照
if (has('dq')) {
  $('dqTable').innerHTML = '<tr><th>项目</th><th>dq（asset_adm）</th><th>本报告</th><th>结论</th></tr>' +
    D.dq.map(r => `<tr><td>${r[0]}</td><td class="num">${r[1]}</td><td class="num">${r[2]}</td><td>${r[3]}</td></tr>`).join('');
}

// 附录
if (has('cross')) {
  $('crossIntro').innerHTML = D.crossIntro;
  $('xTable').innerHTML = '<tr><th>检查项</th><th>本程序</th><th>独立复核</th><th>结论</th></tr>' +
    D.cross.map(r => `<tr><td>${r.item}</td><td class="num">${r.ours}</td><td class="num">${r.ref}</td><td>${r.verdict}</td></tr>`).join('');
  if (has('bugs')) {
    $('bugTable').innerHTML = '<tr><th>#</th><th>问题</th><th>影响</th><th>处理</th></tr>' +
      D.bugs.map((b, i) => `<tr><td class="num">${i + 1}</td><td>${b[0]}</td><td>${b[1]}</td><td>${b[2]}</td></tr>`).join('');
  }
}
$('defTable').innerHTML = '<tr><th>口径</th><th>说明</th></tr>' + D.defs.map(d => `<tr><td style="white-space:nowrap">${d[0]}</td><td>${d[1]}</td></tr>`).join('');
