# How credible are the results? (for DMT / client)

## In one sentence

**The results are computed live from DMT/ADM's real asset register, not pre-written; the hydraulics are planning-level screening and some parameters are placeholders.** Ranking and location are trustworthy; kilometres, cost and years are orders of magnitude; flood depths are not produced by this system.

## 1. The data is real

Read-only from the Abu Dhabi databases (<db-host>:<port>):

| Content | Table | Size |
|---|---|---|
| Storm pipes: diameter, material, invert levels, length, flow direction | pipe.dmt_irrigationstormwater_abudhabi_network.sw_edge | 261,847 reaches |
| Inlet and catch-basin cover levels | pipe.…abudhabi.inlet / catchbasin | 196,583 |
| Outfall type and invert level | pipe.…abudhabi.outfall | 254 |
| Terrain: 5 m DTM (fills missing cover levels, ground slope for reaches without inverts, fill-to-spill volume at client hotspots) | Client-supplied AUH_DTM_5m_Z40.TIF (EPSG:32640, 2026-03, ADNOC source) | Emirate-wide, 92,972 × 67,615 cells |
| Ponds, tanks, soakaways, pumps | pond / sw_reservoirstructure / soakaway / ps_pump | 23 / 20 / 977 / 103 |
| Planned and approved stormwater structures | makani_sync_full.storm_water_structure | 298 |
| Zone blockage statistics | liveability_data_20260730.fact_oi_indicators | 45 zones, Jan–Apr 2024 |

Every proposal carries gid, vertex_id and unitid; the console's “SQL to verify” button returns statements to check each cited object against the source database.

## 2. Computed live, not pre-written

- Each run (about 2 minutes) re-reads the source, rebuilds the graph, recomputes states and proposals, and stores everything in the local `sw_brain` database under a new run_id. Ten-plus runs were made on 2026-09-15.
- Two parts of the console compute in real time: the rainfall slider (recomputes capacity ratios for 260k reaches, showing elapsed milliseconds) and “Run what-if now” on any proposal (applies the intervention to the whole network state and recomputes KPIs). Other tabs show the last batch run.
- Formulas and graph accumulation are covered by unit tests (`tests/`, 19 cases): Manning against hand-calculated values, accumulation on toy networks.

## 3. What is assumed

All in `config.toml`; change and re-run:

| Assumption | Current | Replace with |
|---|---|---|
| Design rainfall (15-min) | 30 / 60 / 120 / 100 mm/h | ADM drainage design IDF |
| Per-inlet catchment, road width | With the DTM: sub-catchments delineated per inlet on a 10 m D8 grid (median area in the summary); without it 600 m², 20 m | Check against DMT's CivilStorm model; runoff C for the “plot interior” (0.55) and “open land” (0.30) classes and the 1 km flow-length cut-off remain assumptions |
| Runoff coefficient | 0.85 | by land use |
| High tide, freeboard threshold | +1.0 m, 1.5 m | NCM / port tide tables |
| Unit costs, annual budget | see [cost] [standard] | ADM/ADPIC rates and real budget |
| Indicative cost of emergency pumps | hourly rates from the client's Plan A table (12-in 90 / 8-in 60 / 6-in 36 AED/h); 500 AED mobilisation per unit, 4 h minimum charge and a 48 h cap for points that never drain are assumptions ([dispatch.cost]) | client rate card and real work orders |
| Client hotspot coordinates | 506 ADM hotspots re-positioned from the client's KMZ of 2026-09-20 (the v4.4 workbook has every latitude/longitude shifted down one row); the 184 Al Ain and 155 Dhafra hotspots still use workbook coordinates | client to confirm whether the Al Ain / Dhafra sheets carry the same shift |
| Gap to the DMT 1D models | network-wide spill is now computed on the same basis (peak flows routed reach by reach, excess spilled where a pipe cannot pass it; no backwater or pond/pump/gate storage): inside Mussafah_00, where the register matches the model, Apr 2024 differs by 4%; inside RB3 / Shaliela, where the register holds only 39% / 62% of the modelled length, this system is 3–11× lower | DMT to explain how the model network differs from the register; recompute once the 2026 GIS arrives |
| Acceptable ponding depth and duration | road 0.15 m etc., 30 min | DMT confirmation |
| Flood-point hydrograph | return-period scenarios use the Manual 3.6 trapezoid (max over durations); the two observed events use a 5-min water balance on the DMT gauge hyetograph, and areas without a gauge borrow the shape scaled to the Parsons zonal depth | NCM hourly station records |
| Tankers | 17 m³ (4,500 gal), 10 min load and unload, 30 km/h, 5 km haul where no discharge point exists, ≤6 per point; only the 250 AED/h rate comes from the client table, the rest are assumptions ([dispatch.tankers]) | client tanker register and O&M contracts |
| Relay pumping | beyond 300 m to the discharge point, every 100 m of hose adds a relay stage: pump rate ×0.85 per stage (floor 0.4) and one more setup time | manufacturer head-flow curves and hose losses |
| Downstream backwater | proxy: a reach that does not spill but has a spilling reach within 300 m downstream and peak utilisation ≥80% is flagged as backwater risk; not a surface-profile computation | 1D model surface profiles |

## 4. Credibility bounds

| | |
|---|---|
| Trust | Ranking and location: which pipe, zone or outfall fails first. From real geometry and levels. |
| Order of magnitude | Kilometres, cost, years: sound method, placeholder parameters. Values change with real parameters; ranking mostly does not. |
| Not provided | Flood depth, duration, affected buildings. These need the 1D/2D hydrodynamic prediction model; this system supplies its inputs and candidate plans. |

## 5. Known data problems the system reports itself

- 61 large dead ends carry 3,323 km of network with no registered outlet; the largest is MBZ City's ADM_SW_NODE_4271 (812 ha upstream). Most likely unregistered outfalls or pump stations; ranked first among all proposals.
- 35 reaches need a threefold or larger diameter jump and 5 have gaps of a few metres: direction or connection errors in the register.
- 19% of pipe length lacks invert levels or has inferred direction (Zayed City 58%, Madinat Al Riyad 43%). Reaches without invert levels now take the 5 m DTM ground slope where it exceeds the 0.2% default (flagged). The level datum “register = elevation + 100” was verified against the DTM on 6,000 inlets (82% within ±0.5 m).
- Pump-station telemetry fields are empty and sensor registers hold zero rows: no real-time data exists.

Engineering proposals in these areas carry confidence 0.5 or lower until verified.

## Design-standard basis (updated 2026-09-19)

Rainfall intensity, return periods, duration multipliers and tide level now follow the DMT Stormwater & Subsoil Drainage Design Manual WA-726-1, 3rd edition (April 2022): Zone B IDF (Tables 3-6/3-7), per-facility design return periods (Table 3-2), duration multipliers (Table 3-3), design high tide +1.3 m NADD (Table 4-5). Still placeholders: land-use-weighted runoff coefficient, acceptable ponding depth, trapezoidal flood-volume method.
