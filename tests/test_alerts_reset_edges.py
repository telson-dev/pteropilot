"""``pteropilot-alerts --reset`` 的边界路径。

清空冷却状态是「调完灵敏度想立刻验证」时最常用的动作，出错时要说人话、
给出非零退出码，而不是静默成功——否则用户会以为清干净了，实际冷却还在。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pteropilot.alerts as alerts


def _args(**kw) -> argparse.Namespace:
    base = {"state_file": None, "dry_run": False, "json": False}
    base.update(kw)
    return argparse.Namespace(**base)


def test_reset_dry_run_json(tmp_path, capsys) -> None:
    f = tmp_path / "state.json"
    f.write_text("{}", encoding="utf-8")

    rc = alerts._run_reset(_args(state_file=str(f), dry_run=True, json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"reset": True, "dry_run": True, "path": str(f), "existed": True}
    assert f.exists(), "演练模式绝不能真的删"


def test_reset_dry_run_when_missing(tmp_path, capsys) -> None:
    f = tmp_path / "nope.json"
    rc = alerts._run_reset(_args(state_file=str(f), dry_run=True))
    assert rc == 0
    assert "无需删除" in capsys.readouterr().out


def test_reset_json_reports_removed(tmp_path, capsys) -> None:
    f = tmp_path / "state.json"
    f.write_text("{}", encoding="utf-8")
    rc = alerts._run_reset(_args(state_file=str(f), json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["removed"] is True
    assert not f.exists()


def test_reset_failure_returns_one(tmp_path, capsys, monkeypatch) -> None:
    """删除失败必须返回 1 且说清楚，不能假装成功。"""
    f = tmp_path / "state.json"
    f.write_text("{}", encoding="utf-8")

    def boom(self):
        raise OSError("拒绝访问")

    monkeypatch.setattr(Path, "unlink", boom)
    rc = alerts._run_reset(_args(state_file=str(f)))
    assert rc == 1
    assert "删除失败" in capsys.readouterr().err


def test_reset_failure_json(tmp_path, capsys, monkeypatch) -> None:
    f = tmp_path / "state.json"
    f.write_text("{}", encoding="utf-8")

    def boom(self):
        raise OSError("拒绝访问")

    monkeypatch.setattr(Path, "unlink", boom)
    rc = alerts._run_reset(_args(state_file=str(f), json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["reset"] is False
    assert "拒绝访问" in out["error"]
