"""get_server_startup 工具测试：启动命令 / egg / 变量展示 / 截断 / 空变量。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.get_server_startup(*args, **kwargs)

    return asyncio.run(run())


def _payload(cmd="java -Xms{MIN_RAM} -Xmx{MAX_RAM} -jar server.jar nogui",
             egg="minecraft", variables=None):
    return {
        "attributes": {
            "user_id": 1,
            "egg": egg,
            "startup_command": cmd,
            "variables": variables if variables is not None else [
                {
                    "name": "内存",
                    "description": "最大内存",
                    "env_variable": "MAX_RAM",
                    "server_value": "2G",
                    "default_value": "1G",
                    "user_viewable": True,
                    "user_editable": True,
                },
                {
                    "name": "JVM 参数",
                    "env_variable": "JAVA_OPTS",
                    "server_value": "",
                    "default_value": "-Xms1G",
                    "user_viewable": False,
                    "user_editable": False,
                },
            ],
        }
    }


def test_get_server_startup_full(make_client):
    make_client[2].route("GET", "/startup", httpx.Response(200, json=_payload()))
    out = _call(make_client, "srv1")
    assert "java -Xms{MIN_RAM}" in out  # 启动命令
    assert "Egg: minecraft" in out
    assert "启动变量（2 个）" in out
    assert "内存 = 2G [可见/可改]" in out        # server_value 优先
    assert "JVM 参数 = -Xms1G [隐藏/只读]" in out  # server_value 空 → default_value
    assert "env_variable" not in out  # 只展示 name，不泄露环境变量名


def test_get_server_startup_no_variables(make_client):
    make_client[2].route("GET", "/startup", httpx.Response(200, json=_payload(variables=[])))
    out = _call(make_client, "srv1")
    assert "(无启动变量)" in out


def test_get_server_startup_truncates(make_client):
    variables = [
        {"name": f"v{i}", "env_variable": f"V{i}", "server_value": f"{i}",
         "user_viewable": True, "user_editable": False}
        for i in range(40)
    ]
    make_client[2].route("GET", "/startup", httpx.Response(200, json=_payload(variables=variables)))
    out = _call(make_client, "srv1")
    assert "共 40 个变量，仅显示前 30 个" in out
    assert "v29" in out and "v30" not in out


def test_get_server_startup_empty_attributes(make_client):
    make_client[2].route("GET", "/startup", httpx.Response(200, json={"attributes": {}}))
    out = _call(make_client, "srv1")
    assert "启动命令: (无)" in out
    assert "(无启动变量)" in out
