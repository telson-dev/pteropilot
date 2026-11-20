"""playerlog 读取要接受带 UTF-8 BOM 的 jsonl。

Windows 记事本 / PowerShell 5.1 保存的文件默认带 BOM。上一轮给 alerts.json
修了同样的问题（read_text("utf-8") + json.loads 直接报错，异常又被吞成
「没有配置」→ 静默失效）；playerlog 这边实测也有：明明有采样，--doctor 却
报「本机还没有记录」，因为带 BOM 的首行解析失败被当作坏行跳过。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pteropilot import playerlog

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _write(dirpath: Path, server_id: str, ages_days: list[float], *, bom: bool = False) -> None:
    p = playerlog.log_path(server_id, dirpath)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        json.dumps({"t": (NOW - timedelta(days=a)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "p": ["p1"]})
        for a in ages_days
    ) + "\n"
    p.write_text(("\ufeff" + body) if bom else body, encoding="utf-8")


def test_latest_sample_tolerates_bom(tmp_path: Path) -> None:
    _write(tmp_path, "smp", [1], bom=True)
    got = playerlog.latest_sample(tmp_path)
    assert got is not None, "带 BOM 的采样不能被当成坏文件"
    assert got[0] == "smp"


def test_stats_tolerates_bom(tmp_path: Path) -> None:
    _write(tmp_path, "smp", [1, 0.5], bom=True)
    d = playerlog.stats("smp", days=7, directory=tmp_path, now=NOW)
    assert d["total"] == 2, "带 BOM 时也要能数出全时段条数"
    assert d["samples"] == 2


def test_record_still_writes_without_bom(tmp_path: Path) -> None:
    """读侧宽容，写侧不该跟着写 BOM——别把它往下传。"""
    playerlog.record("smp", ["alice"], now=NOW, directory=tmp_path)
    raw = playerlog.log_path("smp", tmp_path).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
