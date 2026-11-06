# -*- coding: utf-8 -*-
"""本机自签 HTTPS 证书：统一身份的回调平台登记的是 https://<地址>:8766/auth/callback（回执 2026-10-01），服务要真的提供 HTTPS。

  python -m swbrain.tlscert                         # 127.0.0.1、localhost + 本机各网卡地址；写 out/tls/swbrain.crt / .key
  python -m swbrain.tlscert --ip 10.0.0.47          # 再加地址（局域网地址会变，变了重跑一次、重启服务）

写好后在 config.toml 的 [serve] 里设 tls_cert = "out/tls/swbrain.crt"、tls_key = "out/tls/swbrain.key"。
自签证书浏览器第一次会提示「不安全」，点「继续」即可；正式环境请用机构 CA 签的证书，路径照样填这两项。
私钥只在 out/tls/（已在 .gitignore 的 out/ 下），不进仓库、不进镜像。要 cryptography（统一身份那组可选依赖里已有）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import pathlib
import socket
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def make(out_dir: pathlib.Path, ips: list[str], days: int = 825) -> tuple[pathlib.Path, pathlib.Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "swbrain (self-signed)")])
    san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    out_dir.mkdir(parents=True, exist_ok=True)
    crt, kf = out_dir / "swbrain.crt", out_dir / "swbrain.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kf.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    try:
        kf.chmod(0o600)
    except OSError:
        pass
    return crt, kf


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", action="append", default=[], help="再加的地址（可多次）")
    ap.add_argument("--out", default="out/tls")
    a = ap.parse_args(argv)
    for s in (sys.stdout,):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ips = sorted(set(local_ips()) | set(a.ip))
    crt, kf = make(ROOT / a.out, ips)
    print(f"证书 {crt.relative_to(ROOT)}，私钥 {kf.relative_to(ROOT)}；地址：localhost、{'、'.join(ips)}")
    print(f'在 config.toml 的 [serve] 里设：\n  tls_cert = "{crt.relative_to(ROOT).as_posix()}"\n  tls_key = "{kf.relative_to(ROOT).as_posix()}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
