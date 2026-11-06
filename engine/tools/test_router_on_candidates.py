import sys, json
sys.path.insert(0, 'tools')
from l2opt_least_cost_path import LeastCostRouter
import numpy as np
from pyproj import Transformer

router = LeastCostRouter('RDA_96B')
print(f'Cost raster: {router.cost.shape}, passable: {(router.cost < 1e8).sum()} cells\n')

with open('out/l2opt/RDA_96B/candidates.json', encoding='utf-8') as f:
    cands = json.load(f)['candidates']

tw = Transformer.from_crs(4326, 32640, always_xy=True)

tested = 0
for c in cands:
    if c['kind'] != 'pump_outfall': continue
    x0, y0 = tw.transform(c['lon'], c['lat'])
    x1, y1 = c['of_xy']
    r = router.route(x0, y0, x1, y1)
    straight = np.hypot(x1-x0, y1-y0)

    if r['feasible']:
        detour = r['length'] / straight
        print(f"{c['id']}: {straight:.0f}m → {r['length']:.0f}m (×{detour:.2f})")
    else:
        print(f"{c['id']}: {straight:.0f}m → BLOCKED ({r['reason']})")

    tested += 1
    if tested >= 20:
        break

print(f'\nTested {tested} pump_outfall candidates')
