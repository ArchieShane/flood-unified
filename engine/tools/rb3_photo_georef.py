"""Georeference the phone photo of the QGIS screen against catchment.catchment boundaries (chamfer matching)."""
import sys, json, pathlib
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from scipy.optimize import minimize
import geopandas as gpd

S = pathlib.Path(sys.argv[1])
im = np.asarray(Image.open(r"D:\世界模型\微信图片_20260929161125_70_2.jpg").convert("RGB")).astype(int)
Hh, Ww = im.shape[:2]
gray = im.mean(2)
r, g, b = im[..., 0], im[..., 1], im[..., 2]
red = (r > 150) & (r - g > 70) & (r - b > 60)
dark = (gray < 95) & ~red
# map area only (exclude toolbar, left panel, status bar)
mask_map = np.zeros_like(dark); mask_map[230:3600, 230:3072] = True
dark &= mask_map
lab, n = ndi.label(dark, structure=np.ones((3, 3)))
sz = ndi.sum(dark, lab, range(1, n + 1))
keep = np.zeros(n + 1, bool); keep[1:] = sz > 400          # lines are big connected components; hatch dots are small
edges = keep[lab]
print("edge px", int(edges.sum()), "components kept", int(keep.sum()))
Image.fromarray((~edges * 255).astype(np.uint8)).resize((768, 1024)).save(S / "edges_view.png")
DT = ndi.distance_transform_edt(~edges)
TR = 40.0

G = gpd.read_file(S / "catch39.geojson")
pts = []
for geom in G.geometry:
    for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
        for ring in [poly.exterior, *poly.interiors]:
            L = ring.length; k = max(int(L / 15), 4)
            pts += [ring.interpolate(i / k, normalized=True).coords[0] for i in range(k)]
P = np.array(pts)
# window roughly around the area
sel = (P[:, 0] > 244000) & (P[:, 0] < 260000) & (P[:, 1] > 2692000) & (P[:, 1] < 2709000)
P = P[sel]
print("db pts", len(P))
M = np.load(S / "redmask.npy")
red_px = M.sum()
m_per_px = np.sqrt(2800069.0 / red_px)
print("scale from area: %.3f m/px" % m_per_px)


def project(h, XY):
    Hm = np.append(h, 1.0).reshape(3, 3)
    q = np.c_[XY, np.ones(len(XY))] @ Hm.T
    return q[:, :2] / q[:, 2:3]


def cost(h, XY=P, trunc=TR):
    uv = project(h, XY)
    u, v = uv[:, 0], uv[:, 1]
    ok = (u > 240) & (u < Ww - 5) & (v > 240) & (v < 3590)
    if ok.sum() < 300:
        return 1e6
    d = DT[v[ok].astype(int), u[ok].astype(int)]
    return float(np.minimum(d, trunc).mean()) + 20.0 * (1 - ok.mean()) * 0


def sim_h(s, th, tx, ty):
    # UTM -> pixel: u = s*(cos*x - sin*y) + tx ; v = -s*(sin*x + cos*y) + ty (y axis flipped)
    c, sn = np.cos(th), np.sin(th)
    return np.array([s * c, -s * sn, tx, -s * sn, -s * c, ty, 0.0, 0.0])


# coarse search: scale fixed from area (±15 %), rotation ±8 deg, translation grid
x0u, y0u = 251000.0, 2700500.0          # rough centre of the red strip area
best = (1e9, None)
cu, cv = 1500.0, 1800.0
for sf in np.arange(1.0, 1.36, 0.035):
    s = 1.0 / (m_per_px * sf)
    for thd in np.arange(-6, 6.1, 1.5):
        th = np.deg2rad(thd)
        for dx in np.arange(-3000, 3001, 150):
            for dy in np.arange(-3000, 3001, 150):
                X, Y = x0u + dx, y0u + dy
                c, sn = np.cos(th), np.sin(th)
                tx = cu - s * (c * X - sn * Y); ty = cv + s * (sn * X + c * Y)
                h = sim_h(s, th, tx, ty)
                cst = cost(h, P[::8], 25.0)
                if cst < best[0]:
                    best = (cst, h, sf, thd, dx, dy)
print("coarse best", best[0], "sf", best[2], "rot", best[3], "dx", best[4], "dy", best[5])
h = best[1]
for tr in (40.0, 25.0, 15.0):
    res = minimize(lambda z: cost(np.r_[z[:6] * h[:6] / np.where(h[:6] == 0, 1, h[:6]) if False else z[:6], z[6:8]], P[::2], tr),
                   np.r_[h[:6], 0.0, 0.0], method="Powell", options={"maxiter": 20000, "xtol": 1e-9, "ftol": 1e-6})
    h = res.x
    print("refine trunc", tr, "cost", res.fun)
# the perspective terms are tiny in magnitude; refine them with scaled parameters
scale = np.array([1e-3, 1e-3, 1.0, 1e-3, 1e-3, 1.0, 1e-9, 1e-9])
res = minimize(lambda z: cost(z * scale, P[::2], 15.0), h / scale, method="Powell", options={"maxiter": 40000, "xtol": 1e-6, "ftol": 1e-7})
h = res.x * scale
print("perspective refine cost", res.fun)
np.save(S / "H_utm2px.npy", h)
# residual stats at full res
uv = project(h, P); ok = (uv[:, 0] > 240) & (uv[:, 0] < Ww - 5) & (uv[:, 1] > 240) & (uv[:, 1] < 3590)
d = DT[uv[ok, 1].astype(int), uv[ok, 0].astype(int)]
print("in-view db pts", int(ok.sum()), "median dist px %.1f, share <=6px %.2f, <=15px %.2f" % (np.median(d), (d <= 6).mean(), (d <= 15).mean()))
# overlay
ov = im.copy().astype(np.uint8)
for u, v in uv[ok].astype(int):
    ov[max(v - 3, 0):v + 4, max(u - 3, 0):u + 4] = (0, 160, 255)
Image.fromarray(ov).resize((1152, 1536)).save(S / "overlay.jpg")
