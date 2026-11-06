/* 效果分析页（/states/perf）落图用：基线溢水点按「既有 vs 方案」分类着色，点开弹出点卡。
   类别 key（both / reduced / cleared / added / dry）与残留原因 key 是后端协议值，这里只管颜色和文案。 */
import { t } from '../i18n'
import { esc } from './format'
import { fmtInt, fmtLatLon } from './sim'

export const PERF_COLOR = {
  both: '#E5484D',
  reduced: '#F5A524',
  cleared: '#30A46C',
  added: '#8E4EC6',
  dry: '#8B8D98',
}

/** 名次 → 该点的对照数字；ResultMap 按基线片区名次查 */
export const perfByRank = data => new Map((data?.points || []).map(p => [p.rank, p]))

export function perfClass(data) {
  const by = perfByRank(data)
  return c => {
    const p = by.get(c.rank)
    return p?.category ? { color: PERF_COLOR[p.category], key: p.category } : null
  }
}

export function perfPopup(data) {
  const by = perfByRank(data)
  const h = data.h
  const endH = data.residual ? data.residual.end_minutes / 60 : null
  return c => {
    const p = by.get(c.rank)
    if (!p) return ''
    const f2 = x => (x == null ? '—' : Number(x).toFixed(2))
    const v = x => (x == null ? '—' : fmtInt(Math.round(x)))
    const kv = (k, val) => `<span class="k">${k}</span><span class="v">${val}</span>`
    const cat = p.category ? `<i style="background:${PERF_COLOR[p.category]}"></i>${t(`sim.perf.cat.${p.category}`)}` : '—'
    let reason
    if (p.residual) {
      const k = p.residual.class
      reason = `<b>${t(`sim.residual.cls.${k}`)}</b>：${t(`sim.residual.clsHint.${k}`)}`
    } else if (endH != null) reason = t('sim.perf.pop.noResidual', { end: endH })
    else reason = '—'
    const loc = fmtLatLon(c)
    return `<div class="pf-pop"><h4>${esc(p.name || `#${p.rank}`)}</h4><div class="kv">` +
      (loc ? kv(t('sim.perf.pop.loc'), loc) : '') +
      kv(t('sim.perf.pop.area'), `${fmtInt(Math.round(p.area_m2))} m²`) +
      kv(t('sim.perf.pop.nature'), cat) +
      kv(t('sim.perf.pop.vol', { h }), `${v(p.volume_base_m3)} → ${v(p.volume_vs_m3)} m³`) +
      kv(t('sim.perf.pop.depth', { h }), `${f2(p.depth_base_m)} → ${f2(p.depth_vs_m)} m`) +
      kv(t('sim.perf.pop.reason'), reason) +
      `</div></div>`
  }
}
