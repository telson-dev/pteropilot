"""``--doctor`` 的版本新鲜度检查。

背景：PyPI 上的 pteropilot 长期停在 0.2.0，用户 `pip install` 装到旧版后
看到的是「工具少了 / 功能不对」，很容易以为项目坏了。doctor 现在会对比
GitHub 最新 release 并给出确切升级命令。
"""

from __future__ import annotations

import pytest

from pteropilot import server


# ------------------------------------------------------------------ 版本号比较

@pytest.mark.parametrize("a,b,expect", [
    ("0.2.9", "0.2.33", False),     # 字符串比较会得到 True，这里必须按数字段比
    ("0.2.33", "0.2.9", True),
    ("v0.2.33", "0.2.33", False),   # 前缀 v 要能剥掉
    ("0.2.0", "0.2.33", False),
    ("1.0.0", "0.99.99", True),
    ("0.3", "0.2.99", True),        # 段数不同也能比
    ("0.2.33", "0.2.33", False),
])
def test_version_tuple(a: str, b: str, expect: bool) -> None:
    assert (server._version_tuple(a) > server._version_tuple(b)) is expect


def test_version_tuple_handles_garbage() -> None:
    # 非数字段截断，不抛异常
    assert server._version_tuple("") == ()
    assert server._version_tuple("abc") == ()
    assert server._version_tuple("0.2.rc1") == (0, 2)


# ------------------------------------------------------------------ doctor 输出

@pytest.fixture
def clean_env(monkeypatch):
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY", "PANEL_READONLY",
              "WEBHOOK_URL", "SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_doctor_warns_when_behind(clean_env, capsys) -> None:
    clean_env.setattr(server, "_latest_release_version", lambda *a, **k: "9.9.9")
    rc = server._run_doctor(skip_network=False)
    out = capsys.readouterr().out
    assert "落后" in out
    assert "9.9.9" in out
    assert "uv tool install --force" in out, "要给出可直接复制的升级命令"
    assert rc == 1, "缺面板配置时仍然返回 1"


def test_doctor_quiet_when_current(clean_env, capsys) -> None:
    clean_env.setattr(server, "_latest_release_version", lambda *a, **k: server.__version__)
    server._run_doctor(skip_network=False)
    out = capsys.readouterr().out
    assert "已是最新" in out
    assert "落后" not in out


def test_doctor_survives_lookup_failure(clean_env, capsys) -> None:
    """查不到最新版时只提示一句，不能崩、也不能当成问题。"""
    clean_env.setattr(server, "_latest_release_version", lambda *a, **k: None)
    rc = server._run_doctor(skip_network=False)
    out = capsys.readouterr().out
    assert "查不到最新版" in out
    assert rc == 1


def test_doctor_skip_network_skips_version_line(clean_env, capsys) -> None:
    """--no-network 时不该打印版本对比行（避免让人以为查过了）。"""
    clean_env.setattr(server, "_latest_release_version",
                      lambda *a, **k: pytest.fail("--no-network 不应发起网络查询"))
    server._run_doctor(skip_network=True)
    out = capsys.readouterr().out
    assert "版本：" not in out


def test_release_lookup_returns_none_when_unreachable(monkeypatch) -> None:
    """网络异常必须被吞掉，返回 None 而不是抛。"""
    import urllib.request

    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert server._latest_release_version(timeout=0.1) is None
