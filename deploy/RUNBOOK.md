# PteroPilot 真机端到端实测 Runbook

> 目标：在阿里云 Linux（Ubuntu 22.04 / 24.04）上搭一套 Pterodactyl 面板 + 一个 MC 服务器（Paper/Purpur），用 PteroPilot 实测「说一句话 → 查状态 / 重启 / 备份」。
>
> 本文是**可直接照做的执行手册**，含环境变量示例、按步验证清单、回滚方案。**不真跑**——等季总提供服务器后再执行。

---

## 0. 前置自查（开跑前逐项打勾）

- [ ] 服务器已开通，能 SSH 登录，具备 root 或 sudo 权限
- [ ] **内存 ≥ 4GB**（2GB 只够面板+wings，跑 MC 服会 OOM）
- [ ] 磁盘 ≥ 20GB（面板 + wings + 一个 MC 服 + egg 镜像）
- [ ] 系统为 Ubuntu 22.04 / 24.04（其他发行版官方脚本不保证）
- [ ] 出站能访问 GitHub / Docker Hub（大陆网络若拉不动，先备好代理或镜像）
- [ ] 一个可用域名（或先用服务器公网 IP + 端口）

**实测范围（本次只验收这些，不扩量）：**

| 序号 | 场景 | 预期 |
|---|---|---|
| T1 | 「列出我的服务器」 | 返回服务器标识/名称/UUID |
| T2 | 「查看 smp 服状态」 | 返回状态 + CPU/内存/磁盘 |
| T3 | 「重启 smp 服」 | 二次确认拦截 → 带 confirm=True 后生效 |
| T4 | 「建一个备份」 | 返回备份 UUID，面板里可见 |
| T5 | 只读模式「重启」 | 被拒绝，返回只读提示 |

---

## 阶段 1 — 装 Docker + 依赖

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

apt-get update && apt-get install -y docker-compose-plugin

docker --version          # 验证：输出版本号
docker compose version    # 验证：输出 Compose v2 版本号
```

✅ **阶段 1 验证**：`docker ps` 不报 `Cannot connect to the Docker daemon`。

---

## 阶段 2 — 装 Pterodactyl 面板

```bash
curl -Ls -o /tmp/ptero-install.sh https://github.com/pterodactyl/installer/raw/main/installers/panel.sh
bash /tmp/ptero-install.sh
```

安装器**交互式提问**，记录以下信息（务必写下来，后面回填环境变量要用）：

| 提问 | 记录 |
|---|---|
| 数据库密码 | `____________` |
| 面板管理员邮箱 | `____________` |
| 面板管理员密码 | `____________` |
| 面板域名/地址 | `____________` |
| 面板端口 | `____________` |

> 大陆网络若 raw.githubusercontent.com 拉不动：设 `HTTPS_PROXY` 或用镜像重跑；仍不行改用 [Pelican 官方脚本](https://pelican.dev/docs/installing-panel/)。

✅ **阶段 2 验证**：浏览器打开 `http://<IP>:端口`，能用管理员账号登录面板后台。

---

## 阶段 3 — 装 wings（节点守护进程）

```bash
curl -Ls -o /tmp/wings-install.sh https://github.com/pterodactyl/installer/raw/main/installers/wings.sh
bash /tmp/wings-install.sh
```

装好后：

1. 面板后台 → **Administrative → Nodes → New Node**，填节点名称、FQDN（用服务器 IP 或域名）。
2. 创建后点进节点 → **Configuration** 页，复制 `token` 和 `API 配置` 里那段 YAML。
3. 编辑 `/etc/pterodactyl/config.yml`，填入 token 与相关配置。
4. 启动：`systemctl enable --now wings`。
5. 分配端口：节点 Allocation 里加一段端口范围（如 `25565`）。

✅ **阶段 3 验证**：面板后台 Nodes 列表里该节点显示**绿色**（Heartbeat 正常），而不是红/黄。

---

## 阶段 4 — 建一个 MC 服务器

1. 后台 → **Servers → New Server**。
2. Egg 选 `Minecraft Java (Paper)` 或 `Purpur`。
3. 分配内存（≥1GB）、磁盘（≥5GB）、端口（`25565`）。
4. 启动，确认能看到控制台输出（出现 `Done (X.Xs)!` 意味 MC 服就绪）。

✅ **阶段 4 验证**：面板里服务器状态为运行中；`get_console_log`（阶段 5 后）能看到启动引导日志。

---

## 阶段 5 — 本地 PteroPilot 接面板实测

### 5.1 生成 API Key

面板后台 → **Account → API Credentials → Create**，生成 Key 并复制。

> 建议专门建一个**权限最小化**的 Key：只授予待测那一台服务器、去掉删除类权限，双保险。

### 5.2 本地配置

以 Claude Desktop 为例，`claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "pteropilot": {
      "command": "uvx",
      "args": ["pteropilot"],
      "env": {
        "PANEL_URL": "http://<服务器IP>:<端口>",
        "PANEL_API_KEY": "ptlc_xxxxxxxxxxxxxxxxxxxx",
        "PANEL_READONLY": "0"
      }
    }
  }
}
```

**环境变量清单（本次实测回填）：**

| 变量 | 值 |
|---|---|
| `PANEL_URL` | `http://<IP>:<端口>`（不带尾随斜杠） |
| `PANEL_API_KEY` | `ptlc_...` |
| `PANEL_READONLY` | 首轮先设 `1`，确认只读行为后再改 `0` |

### 5.3 按 T1–T5 逐项实测

| 序号 | 对 AI 说 | 预期结果 | 通过 |
|---|---|---|---|
| T1 | 「列出我的服务器」 | 返回服务器 identifier/名称/UUID | ☐ |
| T2 | 「查看 smp 服状态」 | 状态 + CPU/内存/磁盘/网络 | ☐ |
| T3 | 「重启 smp 服」 | 首次被二次确认拦截；带 confirm=True 重发后服重启 | ☐ |
| T4 | 「建一个备份」 | 返回备份 UUID，面板可见 | ☐ |
| T5 | 设 `PANEL_READONLY=1` 后「重启 smp 服」 | 返回只读拒绝提示，无请求发出 | ☐ |

---

## 回滚方案（出问题就撤）

| 故障 | 回滚 |
|---|---|
| 面板装到一半失败 | 重跑 `panel.sh`（幂等）；再不行重装系统快照 |
| wings 起不来 | 看 `journalctl -u wings -f`，核对 `config.yml` 的 token；重装 `wings.sh` |
| 误删/误改 MC 服文件 | 用阶段 T4 的备份在面板里恢复 |
| API Key 泄露 | 面板 → API Credentials 删除该 Key，重新生成 |
| 环境被污染 | 阿里云控制台重置系统盘 / 用镜像重建（前提：事先打好快照） |

**实验后清理（回收资源）：**

```bash
# 停止并删除全部容器（面板 + wings）
docker compose down   # 在面板目录
service wings stop
# 若要彻底释放：阿里云控制台释放 ECS 实例
```

---

## 实测后回填

- 服务器 IP：
- 面板地址：
- MC 服 identifier：
- T1–T5 结果：
- 遇到的坑 / 解决：
- 结论：（验收通过 / 需修哪里，回写 CHARTER / SECURITY）