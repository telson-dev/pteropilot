"""``--doctor`` 的 alerts.json 自检。

动机：alerts.json 里拼错一个键，告警就永远不响，而且**没有任何提示**——程序只是
忽略它。这类「配了但没生效」是最难自查的故障，正好是 --doctor 该管的。

另外这批测试固化一个实测踩到的坑：Windows 记事本 / PowerShell 5.1 保存的 JSON
带 UTF-8 BOM，`read_text("utf-8")` + json.loads 会直接失败，而 alerts.py 里
那个异常被吞掉 → 配置静默失效。
"""

from __future__ import annotations

import json

import pytest

from pteropilot import alerts, server


def _write(tmp_path, data, *, bom: bool = False):
    p = tmp_path / "alerts.json"
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    p.write_text(("\ufeff" + text) if bom else text, encoding="utf-8")
    return p


# ------------------------------------------------------------------ 缺省

def test_missing_file_is_off_not_bad(tmp_path) -> None:
    r = server._check_alerts_config(tmp_path / "nope.json")
    assert r["status"] == "off"
    assert r["problems"] == []
    assert "未设置" in r["lines"][0]


# ------------------------------------------------------------------ 合法

def test_valid_config(tmp_path) -> None:
    p = _write(tmp_path, {
        "default": {"cpu_threshold": 85, "backup_days": 7},
        "servers": {"smp": {"cpu_threshold": 97}},
    })
    r = server._check_alerts_config(p)
    assert r["status"] == "ok"
    assert r["problems"] == []
    assert "1 台服务器单独配置" in r["lines"][0]


def test_valid_config_with_bom(tmp_path) -> None:
    """记事本 / PowerShell 存的 BOM 不能把整份配置弄失效。"""
    p = _write(tmp_path, {"default": {"cpu_threshold": 85}}, bom=True)
    r = server._check_alerts_config(p)
    assert r["status"] == "ok", r["problems"]


# ------------------------------------------------------------------ 拼写错误

def test_typo_gets_suggestion(tmp_path) -> None:
    p = _write(tmp_path, {"default": {"cpu_treshold": 85}})
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    msg = " ".join(r["problems"])
    assert "cpu_treshold" in msg
    assert "cpu_threshold" in msg, "要给出「你是不是想写 X」"


def test_typo_without_close_match_no_suggestion(tmp_path) -> None:
    p = _write(tmp_path, {"default": {"completely_unrelated": 1}})
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    assert "不认识的键" in " ".join(r["problems"])


def test_unknown_top_level_key(tmp_path) -> None:
    """写成 "server"（少个 s）是最常见的手误，应当直接给出正确写法。"""
    p = _write(tmp_path, {"default": {}, "server": {"smp": {}}})
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    msg = " ".join(r["problems"])
    assert "'server'" in msg
    assert "想写 servers" in msg


# ------------------------------------------------------------------ 类型

@pytest.mark.parametrize("key,value,expect", [
    ("confirm_count", "2", "整数"),
    ("confirm_count", True, "整数"),          # bool 是 int 子类，但这里不该接受
    ("cpu_threshold", "85", "数字"),
    ("webhook", "yes", "true/false"),
    ("min_level", 3, "字符串"),
])
def test_type_errors(tmp_path, key, value, expect) -> None:
    p = _write(tmp_path, {"default": {key: value}})
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    assert expect in " ".join(r["problems"])


def test_int_accepts_real_int(tmp_path) -> None:
    p = _write(tmp_path, {"default": {"confirm_count": 3}})
    assert server._check_alerts_config(p)["status"] == "ok"


# ------------------------------------------------------------------ 取值 / 结构

def test_min_level_invalid_in_default(tmp_path) -> None:
    p = _write(tmp_path, {"default": {"min_level": "warn"}})
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    assert "abnormal 或 error" in " ".join(r["problems"])


def test_min_level_invalid_in_server(tmp_path) -> None:
    p = _write(tmp_path, {"servers": {"smp": {"min_level": "warning"}}})
    r = server._check_alerts_config(p)
    assert "servers.smp.min_level" in " ".join(r["problems"])


def test_servers_not_an_object(tmp_path) -> None:
    p = _write(tmp_path, {"servers": ["smp"]})
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    assert "servers" in " ".join(r["problems"])


def test_top_level_not_object(tmp_path) -> None:
    p = _write(tmp_path, "[1, 2, 3]")
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    assert "顶层" in " ".join(r["problems"])


# ------------------------------------------------------------------ 语法

def test_broken_json_reports_line(tmp_path) -> None:
    p = _write(tmp_path, '{"default": {,}}')
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    msg = " ".join(r["problems"])
    assert "合法 JSON" in msg and "行" in msg
    assert any("json.tool" in h for h in r["hints"])


def test_broken_json_with_bom_still_reports_syntax(tmp_path) -> None:
    """有 BOM 时不能把错误误报成 BOM 问题——BOM 应当被接受。"""
    p = _write(tmp_path, '{"default": {,}}', bom=True)
    r = server._check_alerts_config(p)
    assert r["status"] == "bad"
    msg = " ".join(r["problems"])
    assert "BOM" not in msg
    assert "合法 JSON" in msg


# ------------------------------------------------------------------ alerts.load_config 也接受 BOM

def test_load_config_accepts_bom(tmp_path) -> None:
    p = _write(tmp_path, {"default": {"cpu_threshold": 92}}, bom=True)
    cfg = alerts.load_alerts_config(p)
    assert cfg.get("default", {}).get("cpu_threshold") == 92, \
        "BOM 会让 load_config 静默返回 {}，告警就永远不生效"
