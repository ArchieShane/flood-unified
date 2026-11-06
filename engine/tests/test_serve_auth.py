"""访问口令判定：不设口令放行；设了要 ?key= 或 cookie；URL 编码的 cookie 也认。"""
from swbrain.serve import key_ok


def test_key_ok():
    assert key_ok({}, None, {}) and key_ok({"serve": {"access_key": ""}}, None, {})
    cfg = {"serve": {"access_key": "abc 123"}}
    assert not key_ok(cfg, None, {}) and not key_ok(cfg, "swb_key=wrong", {}) and not key_ok(cfg, None, {"key": ["nope"]})
    assert key_ok(cfg, None, {"key": ["abc 123"]}) and key_ok(cfg, "x=1; swb_key=abc%20123", {}) and key_ok(cfg, "swb_key=abc 123", {})
