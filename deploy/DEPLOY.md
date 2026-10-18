# PteroPilot 真机实测 —— 部署指南

目标：在阿里云 Linux 上搭一套 Pterodactyl 面板 + 一个 MC 服务器，用 PteroPilot 实测「说一句话 → 查状态/重启」。

## ⚠️ 前置硬要求（先自查）

- **内存 ≥ 2GB**（跑面板 + wings 至少 2G；再跑 MC 服建议 4G）
- **系统**：Ubuntu 22.04 / 24.04（其他发行版脚本不保证）
- **能在服务器上执行命令**（root 或 sudo）

## 阶段 1：装 Docker + 依赖

```bash
# 一键安装 Docker（官方脚本）
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# 装 docker-compose v2（Pterodactyl 安装器依赖）
apt-get update && apt-get install -y docker-compose-plugin
```

## 阶段 2：用官方安装器装 Pterodactyl 面板

```bash
# 下载官方一键安装脚本
curl -Ls -o /tmp/ptero-install.sh https://github.com/pterodactyl/installer/raw/main/installers/panel.sh
bash /tmp/ptero-install.sh
```

安装器会交互式问：
- 数据库密码、面板管理员账号邮箱/密码
- 域名（先用 IP 也行，`:8080` 端口）

> ⚠️ 若 GitHub raw 被墙拉不动，用代理/镜像或换 Pelican 官方脚本。

## 阶段 3：装 wings（节点守护进程）

```bash
curl -Ls -o /tmp/wings-install.sh https://github.com/pterodactyl/installer/raw/main/installers/wings.sh
bash /tmp/wings-install.sh
```

装好后在面板后台「节点」里创建节点，记下 `token`，编辑 `/etc/pterodactyl/config.yml` 填入，然后：

```bash
systemctl enable --now wings
```

## 阶段 4：面板里建一个 MC 服务器

1. 后台 → 服务器 → 新建
2. 选 egg（如 Paper/Purpur 的 Minecraft Java）
3. 分配内存/磁盘/端口
4. 启动，确认能进去

## 阶段 5：本地 PteroPilot 接面板实测

面板 → 账号 → API 凭据，生成一个 API key，然后本地：

```bash
uvx pteropilot
# 配 PANEL_URL / PANEL_API_KEY
```

对 AI 说：「列出我的服务器」「重启生存服」「看下最近备份」，看是否真机生效。

## 记录（实测后回填）

- 服务器 IP：
- 面板地址：
- 遇到的坑 / 解决：