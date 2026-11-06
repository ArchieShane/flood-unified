"""ICP-style homography refinement (DB boundary points -> nearest photo edge pixel), then vectorise the red mask."""
import sys, json, pathlib
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
import geopandas as gpd

S = pathlib.Path(sys.argv[1])
im = np.asarray(Image.open(r"D:\世界模型\微信图片_20260929161125_70_2.jpg").convert("RGB")).astype(int)
Hh, Ww = im.shape[:2]
gray = im.mean(2); r, g, b = im[..., 0], im[..., 1], im[..., 2]
red = (r > 150) & (r - g > 70) & (r - b > 60)
dark = (gray < 95) & ~red
mm = np.zeros_like(dark); mm[230:3600, 230:3072] = True; dark &= mm
lab, n = ndi.label(dark, structure=np.ones((3, 3))); sz = ndi.sum(dark, lab, range(1, n + 1))
keep = np.zeros(n + 1, bool); keep[1:] = sz > 400; edges = keep[lab]
DT, IDX = ndi.distance_transform_edt(~edges, return_indices=True)

G = gpd.read_file(S / "catch39.geojson")
pts = []
for geom in G.geometry:
    for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
        for ring in [poly.exterior, *poly.interiors]:
            k = max(int(ring.length / 10), 4)
            pts += [ring.interpolate(i / k, normalized=True).coords[0] for i in range(k)]
P = np.array(pts)
h = np.load(S / "H_utm2px.npy")
Hm = np.append(h, 1.0).reshape(3, 3)
c0 = P.mean(0)                                     # normalise UTM for numerical stability


def proj(Hm, XY):
    q = np.c_[XY, np.ones(len(XY))] @ Hm.T
    return q[:, :2] / q[:, 2:3]


def fit_h(src, dst):
    # DLT with normalisation
    def norm(a):
        m = a.mean(0); s = np.sqrt(2) / np.mean(np.linalg.norm(a - m, axis=1))
        T = np.array([[s, 0, -s * m[0]], [0, s, -s * m[1]], [0, 0, 1]]); return T, (np.c_[a, np.ones(len(a))] @ T.T)[:, :2]
    Ts, s2 = norm(src); Td, d2 = norm(dst)
    A = []
    for (x, y), (u, v) in zip(s2, d2):
        A.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u]); A.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, Vt = np.linalg.svd(np.asarray(A)); Hn = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ Hn @ Ts
    return H / H[2, 2]


for thr in (40, 30, 20, 14, 10, 8, 8, 8):
    uv = proj(Hm, P)
    ok = (uv[:, 0] > 240) & (uv[:, 0] < Ww - 5) & (uv[:, 1] > 240) & (uv[:, 1] < 3590)
    ui, vi = uv[ok, 0].astype(int), uv[ok, 1].astype(int)
    d = DT[vi, ui]
    inl = d < thr
    tgt = np.c_[IDX[1][vi[inl], ui[inl]], IDX[0][vi[inl], ui[inl]]].astype(float)
    Hm = fit_h(P[ok][inl], tgt)
    print(f"thr {thr:>3}: inliers {inl.sum():>5} / {ok.sum()} ({inl.mean():.2f}), median d {np.median(d[inl]):.1f} px")
uv = proj(Hm, P); ok = (uv[:, 0] > 240) & (uv[:, 0] < Ww - 5) & (uv[:, 1] > 240) & (uv[:, 1] < 3590)
d = DT[uv[ok, 1].astype(int), uv[ok, 0].astype(int)]
print("final: share <=5px %.2f, <=10px %.2f, <=20px %.2f" % ((d <= 5).mean(), (d <= 10).mean(), (d <= 20).mean()))
np.save(S / "Hm_utm2px.npy", Hm)
# local scale (m per px) at the red strip
Hi = np.linalg.inv(Hm)
M = np.load(S / "redmask.npy")
cy, cx = np.array(np.nonzero(M)).mean(1)
a = proj(Hi, np.array([[cx, cy], [cx + 100, cy], [cx, cy + 100]]))
print("m/px at red: x %.3f, y %.3f" % (np.linalg.norm(a[1] - a[0]) / 100, np.linalg.norm(a[2] - a[0]) / 100))
# hotspots into photo
Hs = gpd.read_file(r"D:\阿布扎比\排水智能体\console\data\hotspots.geojson").to_crs(32640).set_index("hotspot_id")
out = {}
for hsid in ["ADM-94", "ADM-192", "ADM-487", "ADM-90", "ADM-202", "ADM-486"]:
    p = Hs.loc[hsid].geometry; u, v = proj(Hm, np.array([[p.x, p.y]]))[0]
    inside = bool(M[int(round(v)), int(round(u))]) if 0 <= v < Hh and 0 <= u < Ww else None
    dd = ndi.distance_transform_edt(~M)[int(round(v)), int(round(u))] if inside is False else 0
    out[hsid] = (round(u), round(v), inside, round(float(dd), 1))
    print(hsid, "-> px", round(u), round(v), "inside red:", inside, "dist px", round(float(dd), 1))
ov = im.copy().astype(np.uint8)
for u, v in uv[ok].astype(int):
    ov[max(v - 3, 0):v + 4, max(u - 3, 0):u + 4] = (0, 160, 255)
for hsid, (u, v, _, _) in out.items():
    ov[max(v - 14, 0):v + 15, max(u - 14, 0):u + 15] = (255, 200, 0)
Image.fromarray(ov).resize((1152, 1536)).save(S / "overlay2.jpg")
json.dump(out, open(S / "hs_px.json", "w"))
