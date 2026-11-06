import { del, get, patch, post, put } from './client'

export const fetchCoverage = () => get('/api/coverage')
export const fetchDistricts = () => get('/api/districts')
/** 二维水动力模型识别出的易积水片区（107 个，只有 Al Bateen 一个 run，约 280 KB）。
 *  与 `/api/hotspots` 的台账点是并列的两套数据，不是同一批地物。 */
export const fetchModelPonding = () => get('/api/model-ponding')
/** 管网落图线。layer：'mains' 三级干管（1.6 MB）/ 'laterals' 支管（3.2 MB，放大才取）。 */
export const fetchNetwork = layer => get(`/api/network/${layer}`)
export const fetchIdfDatasets = () => get('/api/idf-datasets')
export const fetchIdfTable = key => get(`/api/idf-datasets/${key}/table`)
export const fetchHyetographPresets = () => get('/api/hyetograph/presets')
/** step / peakPct 可省：步长由后端按历时取默认，峰值位置取后端默认 40%。 */
export const fetchHyetograph = ({ P, D, step, peakPct }) =>
  get(`/api/hyetograph?depth_mm=${P}&duration_min=${D}` +
      (step != null ? `&step_min=${step}` : '') + (peakPct != null ? `&peak_pct=${peakPct}` : ''))

export const runPointCurve = body => post('/api/point-curve', body)
export const calcHotspot = body => post('/api/calc', body)
export const compareDatasets = body => post('/api/compare', body)
export const solveQcap = body => post('/api/qcap', body)

// ---------------------------------------------------------------------------
// 仿真任务：Run history 列表 + New simulation 向导（后端 api/simulations.py）
// ---------------------------------------------------------------------------

/** 向导各步的选项与默认值（雨型预设 3×3、三道防线及资源、下拉取值）。 */
export const fetchSimulationOptions = () => get('/api/simulations/options')
/** 列表。tab：all / running / done / queued / replays / mine / drafts；
 *  user 为操作人缩写，tab=mine 必填，也决定 counts.mine。lang 不给跟界面语言。 */
export const fetchSimulations = ({ tab = 'all', user, limit = 50, offset = 0, lang } = {}) =>
  get(`/api/simulations?tab=${tab}&limit=${limit}&offset=${offset}` +
      (user ? `&user=${encodeURIComponent(user)}` : '') + (lang ? `&lang=${lang}` : ''))
export const fetchSimulation = id => get(`/api/simulations/${id}`)
/** 确认页底部：任务数 / 预计耗时 / 就绪时间，不落库。 */
export const estimateSimulation = form => post('/api/simulations/estimate', form)
/** 存草稿；submit=true 即确认页的「Run」，返回体带 queued_ids。 */
export const createSimulation = (form, { submit = false } = {}) =>
  post('/api/simulations', { ...form, submit })
export const updateSimulation = (id, form) => put(`/api/simulations/${id}`, form)
export const deleteSimulation = id => del(`/api/simulations/${id}`)
export const submitSimulation = id => post(`/api/simulations/${id}/submit`)
export const cancelSimulation = id => post(`/api/simulations/${id}/cancel`)
export const patchSimulationStatus = (id, body) => patch(`/api/simulations/${id}/status`, body)
export const addSimulationAnalysis = (id, body) => post(`/api/simulations/${id}/analyses`, body)
/** 首页：KPI 四卡、同场雨对比、任务队列、New results。 */
export const fetchSimulationSummary = () => get('/api/simulations/summary')
/** 点「Open ›」：记首次打开时间，New results 据此消掉这一条。 */
export const openSimulation = id => post(`/api/simulations/${id}/open`)
/** 结果图层（后端 sim_results.py）。元数据：范围、结果摘要、时间轴；无图层的任务 404。 */
/** 多态任务（一个任务涵盖几种工况，run.states）的图层都带 ?state=；不给取默认态。`+` 要转义（L2+A）。 */
const stateQ = state => (state ? `?state=${encodeURIComponent(state)}` : '')
export const fetchSimulationLayers = (id, state) => get(`/api/simulations/${id}/layers${stateQ(state)}`)
/** 逐格最大水深 GeoJSON（~500 KB），各帧的格子号即其 cell_id。 */
export const fetchSimulationMaxDepth = (id, state) => get(`/api/simulations/${id}/layers/max-depth${stateQ(state)}`)
/** 第 index 帧：{ time_minutes, cells: [cell_id…], depth_m: […] } 两列等长。 */
export const fetchSimulationFrame = (id, index, state) =>
  get(`/api/simulations/${id}/layers/frames/${index}${stateQ(state)}`)
/** 按 docs/analysis_ponding 算法从成果重算的易积水片区（首次现算后缓存）；`deviations` 列出没按文档口径算的地方。 */
/** 播放用的逐帧当前值：与 /layers 的 frames 一一对应（同刻积水面积、最深、累计溢水量、已出现溢水点），拿不出的项为 null。 */
export const fetchSimulationTimeline = (id, state) => get(`/api/simulations/${id}/layers/timeline${stateQ(state)}`)
export const fetchSimulationPonding = (id, state) => get(`/api/simulations/${id}/layers/ponding${stateQ(state)}`)
/** 全市 L1 交付任务的第二道防线（sim_l2.py）：一维报告的 L1 / L2 对照 + 已建成塘；只有 61 mm / 2 h 有报告数。 */
export const fetchSimulationL2 = id => get(`/api/simulations/${id}/l2`)
/** 多态任务某一态模型里的储水设施与接它们的管 / 泵（sim_l2.storage_layer）；≡ 别态或没有蓄水单元 404。 */
export const fetchSimulationStorage = (id, state) => get(`/api/simulations/${id}/layers/storage${stateQ(state)}`)
/** 交付自带的设施 GIS（塘 / 泵点 / 排水去向 / 地面取水片，sim_l2.facilities_layer）；没有 404。 */
/** 这次推演进模型的管线（pipes.geojson）；没有的任务 404。 */
export const fetchSimulationPipes = id => get(`/api/simulations/${id}/pipes`)
export const fetchSimulationFacilities = (id, state) => get(`/api/simulations/${id}/layers/facilities${stateQ(state)}`)
/** 交付自带的积水点（如 C1 的 8 个热点）：坐标 / 汇水区面 / 逐态统计，不带 ?state=；没有的任务 404。 */
export const fetchSimulationHotspots = id => get(`/api/simulations/${id}/hotspots`)
/** 各态「水深 ≥ 阈值」的积水量（ponding_ge.json）：{threshold_m, volume_m3: {态: m³}}；没有的任务 404。 */
export const fetchSimulationPondingGe = id => get(`/api/simulations/${id}/ponding-ge`)
/** 本次推演的全部参数（params.json：规则 + 输入登记 + 一致性核对）；没有的任务 404。 */
export const fetchSimulationParams = id => get(`/api/simulations/${id}/params`)
/** 成果数据位置（相对 NAS 根目录）+ 同一区域之前的推演（outputs.json）；没有的任务 404。 */
export const fetchSimulationOutputs = id => get(`/api/simulations/${id}/outputs`)
/** 全市应对设备配置（第一档固定设施 45% / 第二档移动泵与罐车 20%）：成果目录里没有这两份文件的任务 404。 */
export const fetchSimulationEquipment = id => get(`/api/simulations/${id}/equipment`)
/** 模拟积水点（口径同 RD-A，`city_ponding.py`）：元信息（态 / 热点 / 域外热点）与按阈值现算的清单；没有的任务 404。 */
export const fetchPondingPointsMeta = id => get(`/api/simulations/${id}/ponding-points/meta`)
export const fetchPondingPoints = (id, state, depth, minArea, minDur) =>
  get(`/api/simulations/${id}/ponding-points?${state ? `state=${encodeURIComponent(state)}&` : ''}depth=${depth}&min_area=${minArea}&min_dur=${minDur}`)
/** 单个模拟积水点逐帧的积水量 / 面积 / 最大水深；阈值要与清单一致（编号只在同一态、同一阈值内有效）。 */
export const fetchPondingPointSeries = (id, state, pointId, depth, minArea, minDur) =>
  get(`/api/simulations/${id}/ponding-points/${encodeURIComponent(pointId)}/series?${state ? `state=${encodeURIComponent(state)}&` : ''}depth=${depth}&min_area=${minArea}&min_dur=${minDur}`)
/** 单个热点 200 m 圆内逐帧的积水量 / 面积 / 最大水深（口径同下面的圆内峰值积水量）。 */
export const fetchHotspotCircleSeries = (id, state, hotspotId, depth) =>
  get(`/api/simulations/${id}/hotspot-buffer/${encodeURIComponent(hotspotId)}/series?${state ? `state=${encodeURIComponent(state)}&` : ''}depth=${depth}`)
/** 热点 200 m 圆内的地表峰值积水量 + 圆重叠（hotspot_buffer.py）。 */
export const fetchHotspotBuffer = (id, state, depth) =>
  get(`/api/simulations/${id}/hotspot-buffer?${state ? `state=${encodeURIComponent(state)}&` : ''}depth=${depth}`)
export const fetchSimulationResidual = (id, state) => get(`/api/simulations/${id}/layers/residual${stateQ(state)}`)
/** 多态任务逐时刻的方案效果（sim_effect.py）：各态比基线少上地表多少水、少了几个溢水点。不带 ?state=，一次给全部态。 */
export const fetchSimulationStateEffect = id => get(`/api/simulations/${id}/states/effect`)
/** 效果分析页（sim_perf.py）：既有（第一态）对方案态 `vs` 的对照数字；`h` 是时刻档，不给取默认 2 h。 */
export const fetchSimulationPerf = (id, vs, h) => {
  const q = new URLSearchParams()
  if (vs) q.set('vs', vs)
  if (h != null) q.set('h', h)
  const s = q.toString()
  return get(`/api/simulations/${id}/states/perf${s ? `?${s}` : ''}`)
}

// ---------------------------------------------------------------------------
// RDA 全套成果（只读，后端 api/rda.py 直接读交付目录）。态 key：L1 / L2 / L2P / OPTCS2
export const fetchRdaOverview = () => get('/api/rda/overview')
export const fetchRdaRain = () => get('/api/rda/rain')
export const fetchRdaCriteria = state => get(`/api/rda/states/${state}/criteria`)
export const fetchRdaPonding = (state, depth = '0.15') => get(`/api/rda/states/${state}/ponding?depth=${depth}`)
export const fetchRdaPondingAreas = (state, depth = '0.15') => get(`/api/rda/states/${state}/ponding/areas?depth=${depth}`)
export const fetchRdaL3 = state => get(`/api/rda/states/${state}/l3`)
export const fetchRdaFacilities = state => get(`/api/rda/states/${state}/facilities`)
export const fetchRdaMaxDepth = (state, minDepth = 0.15) => get(`/api/rda/states/${state}/max-depth?min_depth=${minDepth}`)
export const fetchRdaSuggested = () => get('/api/rda/suggested')
export const fetchRdaCompliance = id => get(`/api/rda/suggested/${encodeURIComponent(id)}/compliance`)
export const fetchRdaHotspots = () => get('/api/rda/layers/hotspots')
export const fetchRdaGrid = () => get('/api/rda/grid')
export const fetchRdaFrameIndex = state => get(`/api/rda/states/${state}/frames`)
export const fetchRdaFrame = (state, i, base) =>
  get(`/api/rda/states/${state}/frames/${i}` + (base ? `?mode=diff&base=${base}` : ''))
export const fetchRdaMaxDepthDiff = (state, base) => get(`/api/rda/states/${state}/max-depth-diff?base=${base}`)
export const fetchRdaHotspotSeries = () => get('/api/rda/hotspots/series')
export const fetchRdaHotspotBuffer = (state, depth) => get(`/api/rda/states/${state}/hotspot-buffer?depth=${depth}`)
export const fetchRdaHotspotCircleSeries = (state, hotspotId, depth) =>
  get(`/api/rda/states/${state}/hotspot-buffer/${encodeURIComponent(hotspotId)}/series?depth=${depth}`)
/** RD-A 单个积水点（按经纬度指定）逐帧的积水量 / 面积 / 最大水深。 */
export const fetchRdaPointSeries = (state, lon, lat, depth, minArea, minDur) =>
  get(`/api/rda/states/${state}/ponding/series?lon=${lon}&lat=${lat}&depth=${depth}&min_area=${minArea}&min_dur=${minDur}`)
export const fetchRdaPondingCustom = (state, depth, minArea, minDur) =>
  get(`/api/rda/states/${state}/ponding/custom?depth=${depth}&min_area=${minArea}&min_dur=${minDur}`)
