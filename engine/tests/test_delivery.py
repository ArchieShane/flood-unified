"""交付（2026-09-30）：安装自检、服务精简模式、部署包配置生成、Windows 脚本编码。

锁住：
  · doctor 只读、口令不外泄，给出四种用法的就绪判断；
  · 没有运行产物时服务能起（精简模式），需要运行产物的功能报清楚的错，而 getattr(state, x, 缺省) 照常退回缺省；
  · 部署包配置把数据路径全部指到容器内 /data/…、输出库指到宿主机，其余键与 config.example.toml 一致；
  · .ps1 必须是 UTF-8 带 BOM（Windows PowerShell 5.1 否则按本地编码读中文注释，脚本直接解析失败——2026-09-30 实测踩过）。
"""
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import pytest


def test_doctor_structure_and_no_secrets():
    from swbrain import doctor
    r = doctor.check(db=False)
    assert set(r["modes"]) == {"快速验证", "一二维计算", "完整系统（看控制台）", "完整系统（重跑 swbrain.run）", "大模型规划"}
    assert r["modes"]["完整系统（重跑 swbrain.run）"] is None                    # --no-db：没试就不下结论
    groups = {c["group"] for c in r["checks"]}
    assert {"环境", "配置", "数据", "数据库", "运行产物", "大模型"} <= groups
    cfg_p = ROOT / "config.toml"
    if cfg_p.exists():
        sv = tomllib.loads(cfg_p.read_text(encoding="utf-8")).get("serve") or {}
        blob = repr(r)
        for k in ("access_key", "admin_key"):
            if sv.get(k):
                assert str(sv[k]) not in blob                                      # 口令只报有没有


def test_lite_state():
    from swbrain import serve
    st = serve.LiteState({"serve": {}})
    assert getattr(st, "state_manifest", "缺省") == "缺省"                       # getattr 带缺省照常
    with pytest.raises(serve.ontrt.ActionError):
        st.es                                                                      # 要运行产物的接口：动作错误（回 400 + 说明）
    from swbrain import routes
    with pytest.raises(serve.ontrt.ActionError):
        routes._chat(st)


def test_package_config():
    import make_release as M
    pkg = tomllib.loads(M.package_config())
    ex = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    def get(d, sec):                                               # "source.pipe" 在 TOML 里是嵌套表 source → pipe
        for part in sec.split("."):
            d = d[part]
        return d
    for (sec, key), v in M.PKG_PATHS.items():
        assert get(pkg, sec)[key] == v
        if key not in ("host", "password"):                         # 数据库地址 / 口令占位不是路径
            assert v.startswith("/data/")
    for (sec, key), v in M.PKG_PORTS.items():
        assert get(pkg, sec)[key] == v
    assert set(pkg) == set(ex) and set(pkg["data"]) == set(ex["data"])


@pytest.mark.parametrize("rel", ["scripts/install.ps1", "docker/deploy/start.ps1"])
def test_ps1_has_bom(rel):
    assert (ROOT / rel).read_bytes().startswith(b"\xef\xbb\xbf"), f"{rel} 要存成 UTF-8 带 BOM"


def test_compose_only_starts_exported_images():
    """compose 里启动的服务，镜像必须在部署包里。

    2026-10-04 踩到：swbrain-dsh 服务是后加的（基于应用镜像再加 node + 全局 dsh，单独 3.8 GB），
    make_release 只导出 swbrain-app 与 anuga-swmm，于是拿到包的人 `docker compose up -d` 因为拉不到
    swbrain-dsh 直接失败 —— 部署包开箱即坏。修法是把可选服务放进 profile（默认不起）。
    """
    import yaml
    compose = yaml.safe_load((ROOT / "docker" / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    exported = {"swbrain-app", "anuga-swmm", "swbrain-db"}          # make_release 导出的三个（swbrain-db = 离线数据库镜像），镜像名去掉 :tag
    for name, svc in (compose.get("services") or {}).items():
        if svc.get("profiles"):                                     # 在 profile 里：默认不起，用户可以另去构建
            continue
        img = str(svc.get("image") or "").split(":")[0]
        assert img in exported, f"服务 {name} 默认会起，但镜像 {img} 不在部署包里（放进 profiles 或让 make_release 导出）"
