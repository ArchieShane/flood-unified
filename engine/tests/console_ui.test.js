// 控制台前端：本体关系图的交互（拖节点 / 平移 / 缩放 / 点击选中）与智能体抽屉的宽度变量。
// 关系图部分从 console/app.js 里取出 ontBindGraph 的真身，
// 套一个最小的 SVG DOM 桩跑一遍，验证坐标换算与元素更新。不开浏览器。
//   node tests/console_ui.test.js
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'console', 'app.js'), 'utf8');
const start = src.indexOf('function ontBindGraph(pos){');
if (start < 0) throw new Error('app.js 里找不到 ontBindGraph');
let depth = 0, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
  if (src[i] === '{') depth++;
  else if (src[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
}
const fnText = src.slice(start, end);

// ---- 最小 DOM 桩 ----
let ok = 0, fail = 0;
const check = (name, cond, extra) => { if (cond) { ok++; console.log('  ok   ' + name); } else { fail++; console.log('  FAIL ' + name + (extra ? '  ' + extra : '')); } };

class El {
  constructor(cls, attrs = {}) { this.cls = cls; this.attrs = { ...attrs }; this.children = []; this.listeners = {}; this.classList = { add: () => {}, remove: () => {} }; }
  get dataset() { const d = {}; for (const k of Object.keys(this.attrs)) if (k.startsWith('data-')) d[k.slice(5)] = this.attrs[k]; return d; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  dispatch(t, ev) { (this.listeners[t] || []).forEach(fn => fn(ev)); }
  querySelector(sel) { return this.find(sel)[0] || null; }
  querySelectorAll(sel) { return this.find(sel); }
  find(sel) { const want = sel.replace(/^(svg)?\./, ''); const out = []; const walk = n => { if (n.cls === want || n.tag === want) out.push(n); n.children.forEach(walk); }; this.children.forEach(walk); return out; }
  closest(sel) { const want = sel.replace(/^\./, ''); let n = this; while (n) { if (n.cls === want) return n; n = n.parent; } return null; }
  setPointerCapture() {} releasePointerCapture() {}
  getScreenCTM() { return { inverse: () => ({ _id: 1 }) }; }              // 屏幕坐标 = viewBox 坐标（1:1）
  getBoundingClientRect() { return { width: 460, height: 420 }; }
}
function mk(cls, attrs, children = []) { const e = new El(cls, attrs); children.forEach(c => { c.parent = e; e.children.push(c); }); return e; }

// 图：两个节点 A(100,100)、B(300,200)，一条边 A→B
const pos = { A: [100, 100], B: [300, 200] };
const line = new El(null); line.tag = 'line';
const label = new El(null); label.tag = 'text';
const edge = mk('ont-edge', { 'data-s': 'A', 'data-d': 'B' }, [line, label]);
// 非选中的边不画标签（只留 <line>），拖动刷新时不能因为找不到 <text> 就炸
const line2 = new El(null); line2.tag = 'line';
const edge2 = mk('ont-edge', { 'data-s': 'B', 'data-d': 'A' }, [line2]);
const nodeA = mk('ont-node', { 'data-ont': 'A', transform: 'translate(100,100)' });
const nodeB = mk('ont-node', { 'data-ont': 'B', transform: 'translate(300,200)' });
const vp = mk('ont-vp', {}, [edge, edge2, nodeA, nodeB]);
const svg = mk('ont-graph', {}, [vp]);
svg.viewBox = { baseVal: { width: 460, height: 420 } };

// 页面级依赖
const store = {};
const localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } };
const panel = { querySelector: () => svg };
const document = { getElementById: () => null };
class DOMPoint {
  constructor(x, y) { this.x = x; this.y = y; }
  matrixTransform() { return { x: this.x, y: this.y }; }               // 1:1 变换
}
let ONTSEL = 'A', rerendered = 0;
const renderOntology = () => { rerendered++; };
const ontPos = () => { try { return JSON.parse(localStorage.getItem('swb_ont_pos') || '{}') || {}; } catch (e) { return {}; } };
const ontPosSave = (o) => { try { localStorage.setItem('swb_ont_pos', JSON.stringify(o)); } catch (e) {} };
const t = () => '';

const bind = new Function('panel', 'document', 'DOMPoint', 'localStorage', 'ontPos', 'ontPosSave', 't',
  'ONTSEL_ref', 'renderOntology', `${fnText}; return ontBindGraph;`)(
  panel, document, DOMPoint, localStorage, ontPos, ontPosSave, t, null, renderOntology);

// ontBindGraph 里对 ONTSEL 的赋值在 new Function 作用域里够不到外层的 let，
// 这里只验证「没怎么动 = 触发重渲染（选中）」这一行为，选中值本身由页面代码保证。
globalThis.ONTSEL = ONTSEL;
bind(pos);

const ev = (x, y, extra = {}) => ({ clientX: x, clientY: y, pointerId: 1, target: extra.target || svg, preventDefault: () => {}, ...extra });

console.log('本体关系图交互：');
// 1 拖节点：位置与 transform 跟着走，连着的边端点更新
svg.dispatch('pointerdown', ev(100, 100, { target: nodeA }));
svg.dispatch('pointermove', ev(160, 130, { target: nodeA }));
check('拖节点后 pos 更新', pos.A[0] === 160 && pos.A[1] === 130, JSON.stringify(pos.A));
check('节点 transform 跟随', nodeA.getAttribute('transform') === 'translate(160,130)', nodeA.getAttribute('transform'));
check('边起点跟随', Number(line.getAttribute('x1')) === 160 && Number(line.getAttribute('y1')) === 130, `${line.getAttribute('x1')},${line.getAttribute('y1')}`);
const ex = Number(line.getAttribute('x2')), ey = Number(line.getAttribute('y2'));
check('边终点留出节点半宽（不插进方块里）', Math.hypot(300 - ex, 200 - ey) > 20, `(${ex},${ey})`);
// 标签落在起点到终点的 38% 处：A(160,130) -> B(300,200)
check('边标签跟着起点走（38% 处）',
  Math.abs(Number(label.getAttribute('x')) - (160 + 140 * 0.38)) < 0.01 && Math.abs(Number(label.getAttribute('y')) - (130 + 70 * 0.38 - 3)) < 0.01,
  `${label.getAttribute('x')},${label.getAttribute('y')}`);
check('无标签的边照样刷新（不抛异常）',
  Number(line2.getAttribute('x1')) === 300 && Number(line2.getAttribute('y1')) === 200 && isFinite(Number(line2.getAttribute('x2'))),
  `${line2.getAttribute('x1')},${line2.getAttribute('y1')} -> ${line2.getAttribute('x2')},${line2.getAttribute('y2')}`);
svg.dispatch('pointerup', ev(160, 130, { target: nodeA }));
check('松手后位置存进 localStorage', JSON.parse(store['swb_ont_pos']).A[0] === 160);
check('拖动不算点击（没重渲染）', rerendered === 0, 'rerendered=' + rerendered);

// 1b 只发一个 pointermove 的「慢拖」也要算拖动（判据是像素距离，不是事件个数）
const before = JSON.parse(store['swb_ont_pos']).A.slice();
svg.dispatch('pointerdown', ev(160, 130, { target: nodeA }));
svg.dispatch('pointermove', ev(260, 130, { target: nodeA }));
svg.dispatch('pointerup', ev(260, 130, { target: nodeA }));
check('单次大位移算拖动不算点击', JSON.parse(store['swb_ont_pos']).A[0] === 260 && rerendered === 0, `${before} -> ${JSON.parse(store['swb_ont_pos']).A}, rerendered=${rerendered}`);

// 2 轻点：算选中，触发重渲染
svg.dispatch('pointerdown', ev(300, 200, { target: nodeB }));
for (let i = 0; i < 6; i++) svg.dispatch('pointermove', ev(300 + (i % 2), 200, { target: nodeB }));   // 手抖出很多小事件
svg.dispatch('pointerup', ev(301, 200, { target: nodeB }));
check('轻点（含手抖多事件）算选中', rerendered === 1, 'rerendered=' + rerendered);
check('轻点不把节点挪走', Math.abs(pos.B[0] - 300) < 2 && Math.abs(pos.B[1] - 200) < 2, JSON.stringify(pos.B));

// 3 空白拖动 = 平移
svg.dispatch('pointerdown', ev(10, 10));
svg.dispatch('pointermove', ev(60, 40));
const tr = vp.getAttribute('transform') || '';
check('平移改的是视口 transform', /translate\(50,\s*30\)/.test(tr.replace(/\s+/g, ' ')), tr);
svg.dispatch('pointerup', ev(60, 40));

// 4 滚轮缩放：光标下的点不动
svg.dispatch('wheel', ev(200, 150, { deltaY: -100 }));
const m = /translate\(([-\d.]+),([-\d.]+)\) scale\(([\d.]+)\)/.exec(vp.getAttribute('transform') || '');
check('缩放后 scale 变大', m && Number(m[3]) > 1, vp.getAttribute('transform'));
if (m) {
  const k = Number(m[3]), tx = Number(m[1]), ty = Number(m[2]);
  const lx = (200 - 50) / 1, ly = (150 - 30) / 1;                        // 缩放前该点的局部坐标（平移 50,30、scale 1）
  check('光标下的点保持不动', Math.abs(tx + k * lx - 200) < 0.01 && Math.abs(ty + k * ly - 150) < 0.01, `t=(${tx},${ty}) k=${k}`);
}

// ---- 转义：单引号也得转 ----
// 有两处把 esc() 的结果塞进单引号属性（data-reui='…'），而阿布扎比真实片区名带撇号的不少：
// Al Maqta'、Hadbat Al Za'faranah、Al 'Adlah。不转的话属性在撇号处就断了（2026-09-23 修）。
console.log('\n转义：');
{
  const m = /^\s*const esc\s*=\s*(.+);\s*$/m.exec(src);      // 整行取，不能按分号切：替换表里就有 '&amp;'
  check('app.js 里找得到 esc', !!m);
  const esc = m ? eval('(' + m[1] + ')') : (x) => x;
  const pairs = [['&', '&amp;'], ['<', '&lt;'], ['>', '&gt;'], ['"', '&quot;'], ["'", '&#39;']];
  for (const [c, want] of pairs) check(`esc(${JSON.stringify(c)}) = ${want}`, esc(c) === want, esc(c));
  const ui = { tab: 'overview', zone: "Al Maqta'", rp: '50' };
  const attr = `data-reui='${esc(JSON.stringify(ui))}'`;
  const inside = attr.slice(attr.indexOf("='") + 2, attr.length - 1);
  check('真实片区名不会截断单引号属性', !inside.includes("'"), attr);
  check('注入的事件处理器也被挡住', !esc("x' onmouseover='alert(1)").includes("'"));
  // 浏览器读 dataset 时会把 &#39; 解回 '，JSON.parse 仍然成立
  check('转义后仍是合法 JSON', JSON.parse(inside.replace(/&quot;/g, '"').replace(/&#39;/g, "'")).zone === "Al Maqta'");
}

// ---- 智能体抽屉宽度：拖出来的宽度只能写 --agent-w-open ----
// 写 --agent-w 会以内联样式盖过「关闭时归零」的规则，右侧留一条收不回去的空栏（2026-09-23 修）
const html = fs.readFileSync(path.join(__dirname, '..', 'console', 'index.html'), 'utf8');
console.log('\n智能体抽屉：');
check('app.js 不把宽度内联到 --agent-w', !/setProperty\(\s*['"]--agent-w['"]/.test(src));
check('app.js 写的是 --agent-w-open', (src.match(/setProperty\(\s*['"]--agent-w-open['"]/g) || []).length === 2);
check('只有 .agent-open 时才取这个宽度', /#app\.agent-open\{--agent-w:var\(--agent-w-open,400px\)\}/.test(html));
check('窄屏断点同样走 --agent-w-open', /#app\.agent-open\{--agent-w:var\(--agent-w-open,360px\)\}/.test(html));
check('关闭时右栏归零', /#app\{--side-w:500px;--agent-w:0px/.test(html));

// ---- 「防线2+」页把重现期一栏置灰（2026-10-02）----
// 那一页显示的是一二维热点群结果，用自己的设计雨型（M61 / R96 / D24…），重现期不牵动它；
// 原来按钮照样能点、页面重算却不变，看起来像按钮失灵。
console.log('\n防线2+ 页与重现期：');
{
  check('rpScope 按页签判断（line2p 时置灰）', /function rpScope\(\)\{[\s\S]*?tab===['"]line2p['"]/.test(src));
  check('这页点重现期不发请求', /async function setReturnPeriod\(key\)\{[\s\S]{0,120}if\(tab===['"]line2p['"]\) return;/.test(src));
  check('切页签时同步', /async function render\(\)\{[\s\S]{0,160}rpScope\(\);/.test(src));
  check('重画重现期按钮后也同步', /renderRpChips\(\)\{[\s\S]*?rpScope\(\);\s*\}/.test(src));
  check('置灰样式', /\.scen\.off \.chips/.test(html));
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'console', 'i18n.js'), 'utf8');
  check('中英两种说明文字都有', (i18n.match(/offLine2p:/g) || []).length === 2 && (i18n.match(/offLine2pTip:/g) || []).length === 2);
}

console.log(`\n${ok} 通过，${fail} 失败`);
process.exit(fail ? 1 : 0);
