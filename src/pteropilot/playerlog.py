"""玩家活跃历史：本地采样记录与统计。

为什么需要：``list_players`` 只能看到「此刻在线的人」，基于面板控制台最近千行日志推断。
想知道「谁最常来玩」「什么时段人最多」，需要把每次查询的结果累积起来看趋势。

设计取舍：
  * **纯本地文件**（``~/.pteropilot/playerlog/<server_id>.jsonl``）——不需要服务器，
    也不上传任何数据；
  * **去重写入**——玩家集合没变化就不追加，避免高频轮询把文件撑大；
  * **有上限**——单台超过 ``MAX_LINES`` 行时保留较新的一半，防止无限增长；
  * **失败不影响主流程**——记录失败只是少一条样本，不能影响 list_players 的正常返回。

口径与 ``list_players`` 一致：数据来自控制台日志推断，**不是权威在线数据**。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DIR = Path.home() / ".pteropilot" / "playerlog"
MAX_LINES = 20000
#：单次采样最多折算多少分钟在线时长（防止长时间不查询时把间隔算成超长在线）
MAX_SLICE_MINUTES = 30


def _safe_name(server_id: str) -> str:
    """server_id 一般是 UUID，但仍要防路径穿越与非法字符。"""
    keep = [c for c in server_id if c.isalnum() or c in "-_"]
    name = "".join(keep) or "unknown"
    return name[:64]


def log_path(server_id: str, directory: Path | None = None) -> Path:
    d = directory or DEFAULT_DIR
    return Path(d) / f"{_safe_name(server_id)}.jsonl"


def _last_record(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            last = None
            for line in f:
                line = line.strip()
                if line:
                    last = line
            return json.loads(last) if last else None
    except (OSError, json.JSONDecodeError):
        return None


def record(
    server_id: str,
    players: list[str],
    *,
    now: datetime | None = None,
    directory: Path | None = None,
) -> bool:
    """记录一次采样。玩家集合与上一条相同时跳过。

    返回是否真的写入（去重或出错时为 False）。
    """
    path = log_path(server_id, directory)
    current = sorted({p.strip() for p in players if p and p.strip()})
    try:
        prev = _last_record(path)
        if prev and sorted(prev.get("p") or []) == current:
            return False

        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        row = {"t": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), "p": current}
        path.parent.mkdir(parents=True, exist_ok=True)
        _trim_if_needed(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _trim_if_needed(path: Path) -> None:
    """文件过大时保留较新的部分（内存里处理，行数有上限所以可控）。"""
    try:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= MAX_LINES:
            return
        keep = lines[-(MAX_LINES // 2):]
        with path.open("w", encoding="utf-8") as f:
            f.writelines(keep)
    except OSError:
        return


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def stats(
    server_id: str,
    *,
    days: int = 7,
    directory: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """聚合最近 ``days`` 天的采样。

    返回：
        samples      采样条数
        unique       出现过的不同玩家数
        players      [{"name", "samples", "minutes"}] 按在线时长倒序
        hours        24 长度数组，各小时「有人在线」的采样次数（本地时区）
        first / last 采样时间范围（ISO 字符串，可能为 None）
        truncated    是否因为文件上限丢过历史
    """
    path = log_path(server_id, directory)
    empty = {
        "samples": 0, "unique": 0, "players": [], "hours": [0] * 24,
        "first": None, "last": None, "truncated": False,
    }
    if not path.exists():
        return empty

    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now_utc - timedelta(days=max(1, int(days)))

    rows: list[tuple[datetime, list[str]]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse(row.get("t", ""))
                if ts is None or ts < cutoff:
                    continue
                rows.append((ts, list(row.get("p") or [])))
    except OSError:
        return empty

    truncated = False
    try:
        truncated = sum(1 for _ in path.open("r", encoding="utf-8")) >= MAX_LINES
    except OSError:
        pass

    if not rows:
        return {**empty, "truncated": truncated}

    rows.sort(key=lambda r: r[0])
    local_tz = datetime.now().astimezone().tzinfo

    samples_per_player: dict[str, int] = {}
    minutes_per_player: dict[str, float] = {}
    hours = [0] * 24

    for idx, (ts, players) in enumerate(rows):
        # 这一条采样代表的时间片：到下一次采样的间隔，上限 MAX_SLICE_MINUTES
        if idx + 1 < len(rows):
            gap = (rows[idx + 1][0] - ts).total_seconds() / 60.0
        else:
            gap = 0.0
        slice_min = max(0.0, min(gap, MAX_SLICE_MINUTES))

        hour = ts.astimezone(local_tz).hour
        if players:
            hours[hour] += 1
        for name in players:
            samples_per_player[name] = samples_per_player.get(name, 0) + 1
            minutes_per_player[name] = minutes_per_player.get(name, 0.0) + slice_min

    ordered = sorted(
        samples_per_player.items(),
        key=lambda kv: (-minutes_per_player.get(kv[0], 0.0), -kv[1], kv[0]),
    )

    return {
        "samples": len(rows),
        "unique": len(samples_per_player),
        "players": [
            {
                "name": name,
                "samples": count,
                "minutes": round(minutes_per_player.get(name, 0.0), 1),
            }
            for name, count in ordered
        ],
        "hours": hours,
        "first": rows[0][0].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last": rows[-1][0].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "truncated": truncated,
    }


def format_stats(server_id: str, data: dict, *, days: int = 7, top: int = 10) -> str:
    """把 stats() 的结果排成给人看的文本。"""
    if not data.get("samples"):
        return (
            f"服务器 {server_id} 还没有玩家活跃记录。\n"
            "先调用几次 list_players（每次会记一条采样），积累后再来看趋势。"
        )

    out = [f"### 服务器 {server_id} 的玩家活跃（最近 {days} 天）"]
    out.append(f"- 采样次数：{data['samples']}")
    out.append(f"- 出现过的玩家：{data['unique']} 人")
    if data.get("first"):
        out.append(f"- 时间范围：{data['first']} ~ {data['last']}（UTC）")

    players = data.get("players") or []
    if players:
        out.append("")
        out.append(f"#### 在线时长排行（前 {min(top, len(players))} 名）")
        for i, p in enumerate(players[:top], 1):
            mins = p.get("minutes", 0)
            if mins >= 60:
                human = f"{mins / 60:.1f} 小时"
            else:
                human = f"{mins:.0f} 分钟"
            out.append(f"{i}. {p['name']} — 约 {human}（{p['samples']} 次采样）")

    hist = data.get("hours") or []
    if any(hist):
        peak = max(hist)
        out.append("")
        out.append("#### 各时段活跃度（本地时间，按有人在线的采样次数）")
        for h in range(0, 24, 6):
            chunk = hist[h:h + 6]
            cells = "".join(_bar(v, peak) for v in chunk)
            out.append(f"{h:02d}-{h + 5:02d}  {cells}")

    out.append("")
    out.append(
        "⚠️ 数据来自本地采样（基于控制台日志推断的在线名单），不是权威在线时长；"
        "采样越频繁，时长估算越接近真实。"
    )
    if data.get("truncated"):
        out.append("ℹ️ 记录文件已达上限，更早的历史已被裁剪。")
    return "\n".join(out)


def _bar(value: int, peak: int) -> str:
    if not value:
        return "·"
    levels = " ▁▂▃▄▅▆▇█"
    idx = max(1, round(value / peak * (len(levels) - 1)))
    return levels[idx]
