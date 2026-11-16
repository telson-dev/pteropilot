"""``--status`` 表格里的磁盘列。

背景：``_status_data`` 一直在取 ``disk_bytes``，但 ``_format_status`` 从来没把它
显示出来——取到的数据被丢掉。磁盘写满会让存档写不进去，是游戏服务器最常见的
事故之一，所以这一列该有。
"""

from __future__ import annotations


from pteropilot import server

_MB = 1024 * 1024
_GB = 1024 * _MB


def _row(**kw) -> dict:
    base = {
        "id": "smp", "name": "SMP", "state": "running", "health": "healthy",
        "cpu": 1.0, "memory_bytes": 100 * _MB, "memory_limit": _GB,
        "disk_bytes": 0, "disk_limit": None, "players": 1,
    }
    base.update(kw)
    return base


def _table(**kw) -> str:
    return server._format_status({"servers": [_row(**kw)], "errors": []})


# ------------------------------------------------------------------ 表头

def test_header_has_disk_column() -> None:
    out = _table()
    assert "磁盘" in out


def test_disk_before_players() -> None:
    """列序：CPU / 内存 / 磁盘 / 玩家 —— 容量类放一起。"""
    header = _table().splitlines()[0]
    assert header.index("内存") < header.index("磁盘") < header.index("玩家")


# ------------------------------------------------------------------ 单元格

def test_disk_with_limit_shows_ratio() -> None:
    out = _table(disk_bytes=2 * _GB, disk_limit=10 * _GB)
    assert "2G/10G" in out


def test_disk_without_limit_shows_used_only() -> None:
    out = _table(disk_bytes=3 * _GB, disk_limit=None)
    line = out.splitlines()[-1]
    assert "3G" in line
    assert "3G/" not in line, "没有上限时不该出现斜杠"


def test_disk_limit_zero_is_no_limit() -> None:
    """limit=0 表示不限制：不能显示成 0 分母。"""
    out = _table(disk_bytes=3 * _GB, disk_limit=0)
    line = out.splitlines()[-1]
    assert "3G" in line
    assert "3G/" not in line


def test_disk_missing_shows_dash() -> None:
    out = _table(disk_bytes=None)
    assert "-" in out


def test_disk_zero_bytes() -> None:
    out = _table(disk_bytes=0, disk_limit=_GB)
    assert "0M/1G" in out


# ------------------------------------------------------------------ 与其它列共存

def test_disk_column_coexists_with_backup_column() -> None:
    out = _table(disk_bytes=2 * _GB, disk_limit=10 * _GB,
                 backup_age_days=3.0, backup_ok_count=1)
    header = out.splitlines()[0]
    assert "磁盘" in header and "最新备份" in header
    line = out.splitlines()[-1]
    assert "2G/10G" in line
    assert "3 天前" in line


def test_disk_present_in_json_data() -> None:
    """JSON 里本来就该有 disk_limit，便于脚本算占用率。"""
    import asyncio

    class Client:
        async def client_servers(self):
            return [{"attributes": {"identifier": "smp", "name": "SMP"}}]

        async def resources(self, sid):
            return {"current_state": "running",
                    "resources": {"cpu_absolute": 1.0, "memory_bytes": 1,
                                  "memory_limit": 2, "disk_bytes": 3, "disk_limit": 4}}

    original = server.panel
    server.panel = Client()
    try:
        data = asyncio.run(server._status_data())
    finally:
        server.panel = original
    row = data["servers"][0]
    assert row["disk_bytes"] == 3
    assert row["disk_limit"] == 4, "磁盘上限也要进 JSON，否则脚本没法算占用率"
