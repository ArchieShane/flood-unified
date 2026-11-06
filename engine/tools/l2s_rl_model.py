"""L2S 深度强化学习试点 · 第 2 步：代理模型（选了哪些措施 → 各热点 500 m 内溢流、塘溢流、别处变多），GPU 训练。

2026-10-26 用户：L2S 用深度强化学习试试，先用本机 RTX 5090。在 ArcGIS Pro 的 Python 环境（arcgispro-py3-clone1，PyTorch 2.9 + CUDA 12.9）里跑；
只读 tools/l2s_rl_data.py 造的 out/l2s_rl/<run>/base.json、samples.jsonl，不调 SWMM。
- 输入：每项措施用**可共享的特征**描述（不给每个候选单独学向量：1,490 个候选、几百个样本学不动，2026-10-26 实测验证 R² 为负）：
  一维筛选的「这口井理想截流后各热点溢流降幅」× 截流能力比例（物理先验），措施类型、造价、截流能力、泵流量。
  网络：每项措施的特征 → 共享编码器 → 按组合求和，连同各热点先验降幅之和 → MLP。没见过的候选也能估。
  措施之间的相互作用（同一条干管上截了上游、下游就少）靠 MLP 的非线性学；样本多了再换图神经网络（管网拓扑）。
- 输出：各热点 500 m 内溢流 ÷ 基线（基线 > 500 m³ 的热点），塘溢流、别处变多（÷ 基线全域溢流）。
- 报告验证集上每个输出的 R²，以及综合指标 J（= 各热点比值之和，同 l2opt_greedy 的 jscore 去掉洼坑项）的误差。
用法："C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" tools/l2s_rl_model.py --run RDA_A61 [--epochs 400]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import random
import sys

import numpy as np
import torch
import torch.nn as nn

ROOT = pathlib.Path(__file__).resolve().parents[1]


KINDS = ["tank_gravity", "tank_pumped", "ps_cluster"]


def features(run, B, storms, hs):
    """(雨数, 候选数, 维数) 的特征：各热点先验降幅（÷ 基线）、类型 one-hot、log 造价 / 截流能力 / 泵流量、截几口井。"""
    S1 = json.loads((ROOT / "out" / "l2opt" / run / "screen_stage1.json").read_text(encoding="utf-8"))["items"]
    C = {c["id"]: c for c in json.loads((ROOT / "out" / "l2opt" / run / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
    F = np.zeros((len(storms), len(B["candidates"]), len(hs) + len(KINDS) + 4), dtype=np.float32)
    for si, s in enumerate(storms):
        bz = B["base"][s]["zones"]
        for ci, cc in enumerate(B["candidates"]):
            c = C[cc["id"]]; ws = c.get("wells") or [c["well"]]
            frac = min(1.0, (c.get("capture_cap_m3") or c.get("pond_vol_m3") or 0) / max(c.get("overflow_m3") or 1.0, 1.0))
            for hi, h in enumerate(hs):
                b = max(bz[f"{h}|500"], 1.0)
                F[si, ci, hi] = frac * sum(max(0.0, b - S1[w][s]["zones"][f"{h}|500"]) for w in ws if w in S1 and s in S1[w]) / b
            k0 = len(hs)
            if c["kind"] in KINDS:
                F[si, ci, k0 + KINDS.index(c["kind"])] = 1.0
            F[si, ci, k0 + len(KINDS):] = [np.log1p((c.get("cost_usd") or 0) / 1e6), np.log1p((c.get("capture_cap_m3") or 0) / 1e3), np.log1p(c.get("q_m3s") or 0), len(ws)]
    return F


def load(run):
    d = ROOT / "out" / "l2s_rl" / run
    B = json.loads((d / "base.json").read_text(encoding="utf-8"))
    ids = [c["id"] for c in B["candidates"]]; ix = {c: i for i, c in enumerate(ids)}
    storms = B["storms"]; hs = [h for h in B["hotspots"] if any(B["base"][s]["zones"][f"{h}|500"] > 500 for s in storms)]
    X, S, Y = [], [], []
    for ln in (d / "samples.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln); s = r["storm"]; b = B["base"][s]
        if s not in storms or any(c not in ix for c in r["ids"]):
            continue
        y = [r["zones"][f"{h}|500"] / max(b["zones"][f"{h}|500"], 1.0) for h in hs]
        y += [(r.get("pond_spill") or 0) / max(b["total"], 1.0), (r.get("worse_m3") or 0) / max(b["total"], 1.0)]
        X.append([ix[c] for c in r["ids"]]); S.append(storms.index(s)); Y.append(y)
    return B, ids, storms, hs, X, S, np.array(Y, dtype=np.float32)


class Surrogate(nn.Module):
    """F：(雨数, 候选数, 维数) 特征表（buffer）；n_hs 个热点先验列在最前。"""
    def __init__(self, F, n_hs, n_out, dim=128):
        super().__init__()
        self.register_buffer("F", torch.as_tensor(F)); self.n_hs = n_hs
        self.enc = nn.Sequential(nn.Linear(F.shape[2], dim), nn.GELU(), nn.Linear(dim, dim))
        self.mlp = nn.Sequential(nn.Linear(dim + n_hs, 256), nn.GELU(), nn.Linear(256, 256), nn.GELU(), nn.Linear(256, n_out))

    def forward(self, idx, seg, storm_of_item, n):
        f = self.F[storm_of_item, idx]                                         # (项数, 维数)
        h = torch.zeros(n, self.enc[-1].out_features, device=f.device).index_add_(0, seg, self.enc(f))
        pr = torch.zeros(n, self.n_hs, device=f.device).index_add_(0, seg, f[:, :self.n_hs])
        return self.mlp(torch.cat([h, pr], 1))


def batch(Xs, Ss, dev):
    idx = torch.tensor([i for x in Xs for i in x], dtype=torch.long, device=dev)
    seg = torch.tensor([k for k, x in enumerate(Xs) for _ in x], dtype=torch.long, device=dev)
    st = torch.tensor([Ss[k] for k, x in enumerate(Xs) for _ in x], dtype=torch.long, device=dev)
    return idx, seg, st, len(Xs)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--run", default="RDA_A61"); ap.add_argument("--epochs", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0); ap.add_argument("--val", type=float, default=0.2)
    a = ap.parse_args(argv)
    torch.manual_seed(a.seed); random.seed(a.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    B, ids, storms, hs, X, S, Y = load(a.run)
    n = len(X); order = list(range(n)); random.shuffle(order); nv = max(1, int(n * a.val))
    va, tr = order[:nv], order[nv:]
    print(f"{a.run}：样本 {n}（训练 {len(tr)}、验证 {len(va)}），候选 {len(ids)}，热点输出 {len(hs)} + 2，设备 {dev}"
          + (f"（{torch.cuda.get_device_name(0)}）" if dev == "cuda" else ""))
    F = features(a.run, B, storms, hs)
    m = Surrogate(F, len(hs), Y.shape[1]).to(dev)
    opt = torch.optim.AdamW(m.parameters(), lr=2e-3, weight_decay=1e-4)
    Yt = torch.tensor(Y, device=dev)
    for ep in range(1, a.epochs + 1):
        m.train(); random.shuffle(tr)
        for k in range(0, len(tr), 64):
            bi = tr[k:k + 64]
            p = m(*batch([X[i] for i in bi], [S[i] for i in bi], dev))
            loss = nn.functional.mse_loss(p, Yt[bi])
            opt.zero_grad(); loss.backward(); opt.step()
        if ep % max(1, a.epochs // 8) == 0 or ep == a.epochs:
            m.eval()
            with torch.no_grad():
                pv = m(*batch([X[i] for i in va], [S[i] for i in va], dev)).cpu().numpy()
            yv = Y[va]
            r2 = 1 - ((pv - yv) ** 2).sum(0) / np.maximum(((yv - yv.mean(0)) ** 2).sum(0), 1e-9)
            jt, jp = yv[:, :len(hs)].sum(1), pv[:, :len(hs)].sum(1)
            r2j = 1 - ((jp - jt) ** 2).sum() / max(((jt - jt.mean()) ** 2).sum(), 1e-9)
            print(f"  第 {ep} 轮：训练损失 {loss.item():.4f}；验证 J 的 R² {r2j:.3f}、平均绝对误差 {np.abs(jp - jt).mean():.3f}（J 均值 {jt.mean():.2f}）；"
                  f"各热点 R² 中位 {np.median(r2[:len(hs)]):.2f}")
    out = ROOT / "out" / "l2s_rl" / a.run / "surrogate.pt"
    torch.save({"state": m.state_dict(), "ids": ids, "storms": storms, "hs": hs, "n_out": Y.shape[1]}, out)
    print("→", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
