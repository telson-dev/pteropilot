"""``--status`` 表格的列按需显示。

列全部服务器时 ``players`` 恒为 ``None``，整列都是 ``-``——看起来像坏了。
有任意一台拿得到人数才显示这一列（单台查询会填 players）。
"""

from __future__ import annotations

from pteropilot import server

_MB = 1024 * 1024
_GB = 1024 ** 3


def _row(players=None, **kw) -> dict:
    base = {
        "id": "smp", "name": "SMP", "state": "running", "health": "healthy",
        "cpu": 1.0, "memory_bytes": 100 * _MB, "memory_limit": _GB,
        "disk_bytes": _GB, "disk_limit": 10 * _GB, "players": players,
    }
    base.update(kw)
    return base


def _table(*rows) -> str:
    return server._format_status({"servers": list(rows), "errors": []})


# ------------------------------------------------------------------ 玩家列

def test_players_column_hidden_when_all_none() -> None:
    out = _table(_row(players=None), _row(id="b", players=None))
    assert "玩家" not in out


def test_players_column_shown_when_some_have_it() -> None:
    out = _table(_row(players=3), _row(id="b", players=None))
    assert "玩家" in out
    # 拿不到的那台显示 -
    assert "3" in out


def test_players_column_shown_for_single_server() -> None:
    out = _table(_row(players=0))
    assert "玩家" in out
    assert "0" in out, "0 个玩家也要如实显示 0，不是 -"


# ------------------------------------------------------------------ 与其它列共存

def test_header_always_has_disk() -> None:
    assert "磁盘" in _table(_row())


def test_header_column_order() -> None:
    header = _table(_row(players=1), _row(id="b", backup_age_days=1.0, backup_ok_count=1)).splitlines()[0]
    assert header.index("内存") < header.index("磁盘")
    if "玩家" in header:
        assert header.index("磁盘") < header.index("玩家")
    if "最新备份" in header:
        assert header.index("玩家") < header.index("最新备份")


def test_separator_width_tracks_columns() -> None:
    """分隔线宽度要跟着列数变，否则表头下面长短不齐。"""
    plain = _table(_row())
    with_players = _table(_row(players=2))
    with_both = _table(_row(players=2, backup_age_days=1.0, backup_ok_count=1))
    lines = [x.splitlines() for x in (plain, with_players, with_both)]
    widths = [len(x[1]) for x in lines]
    assert widths[0] < widths[1] < widths[2], f"分隔线宽度没随列增加: {widths}"
    for x in lines:
        assert server._display_width(x[0]) == len(x[1]), "分隔线长度要等于表头的显示宽度"


def test_empty_server_list() -> None:
    out = server._format_status({"servers": [], "errors": []})
    assert "没有可访问的服务器" in out
