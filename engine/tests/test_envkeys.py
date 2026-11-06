"""密钥读取：进程环境优先；没有时（Windows）查注册表；都没有给缺省。不写任何真密钥。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swbrain import envkeys


def test_env_first_and_default(monkeypatch):
    monkeypatch.setenv("SWBRAIN_TEST_KEY_X", "  abc ")
    assert envkeys.get("SWBRAIN_TEST_KEY_X") == "abc" and envkeys.present("SWBRAIN_TEST_KEY_X") and envkeys.source("SWBRAIN_TEST_KEY_X") == "process"
    monkeypatch.delenv("SWBRAIN_TEST_KEY_X")
    assert envkeys.get("SWBRAIN_NO_SUCH_KEY_ZZZ", "dflt") == "dflt" and not envkeys.present("SWBRAIN_NO_SUCH_KEY_ZZZ") and envkeys.source("SWBRAIN_NO_SUCH_KEY_ZZZ") is None


def test_registry_fallback_writes_back(monkeypatch):
    if sys.platform != "win32":
        return
    import winreg
    class K:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def open_key(hive, path):
        if hive == winreg.HKEY_CURRENT_USER:
            return K()
        raise OSError("no key")
    def query(k, name):
        if name == "SWBRAIN_REG_KEY_Y":
            return ("from-registry", winreg.REG_SZ)
        raise OSError("no value")
    monkeypatch.setattr(winreg, "OpenKey", open_key); monkeypatch.setattr(winreg, "QueryValueEx", query)
    monkeypatch.delenv("SWBRAIN_REG_KEY_Y", raising=False)
    assert envkeys.source("SWBRAIN_REG_KEY_Y") == "user"
    assert envkeys.get("SWBRAIN_REG_KEY_Y") == "from-registry"
    import os
    assert os.environ.get("SWBRAIN_REG_KEY_Y") == "from-registry"          # 写回进程环境，子进程能继承
    monkeypatch.delenv("SWBRAIN_REG_KEY_Y")
