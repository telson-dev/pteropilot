# 立项卡 v0.1 — PteroPilot（Pterodactyl / Pelican 的 AI 管理 MCP）

- 日期：2026-09-26
- 状态：🟢 启动
- 代号：**PteroPilot**（2026-09-27 改名，见 §7）
- 母品牌：telson（telson.dev）
- 项目根目录：D:\Projects\pteropilot

## 1. 一句话
给游戏服务器管理面板（Pterodactyl / Pelican）做一个开源 MCP server：让 AI（Claude 等）代替人管理游戏服务器。**开源免费获客 → 托管版收订阅**。

## 2. 目标用户
- 游戏服务器**托管商**（几千家，已有付费习惯：主题 / 模块 / WHMCS 生态）
- 社区服主 / 单人运维（省人力）
- 中文用户（差异化：双语文档）

## 3. 竞品快照（2026-09-26 GitHub 实测）
- 10+ 个散装仓库，多为 ★0–3；最高 ★11（BlockMind，多主机聚合型）
- 大多只支持 Pterodactyl；个别已兼容 Pelican（PteroMCP、mediavee 等）
- 多个仓库本月仍在更新 → 需求活跃，但没有王者
- 共性弱点（待深扒确认）：文档差、无托管版、维护断档、安全粗糙、无中文

## 4. 差异化（我们 4 张牌）
1. **双面板**：Pterodactyl + Pelican 都支持（行业正在换代迁移，卡位时机）
2. **质量**：测试 + 语义化版本 + CHANGELOG + 认真做安全（token 处理、危险操作确认）
3. **易用**：`uvx` 一键安装 + 中英双语文档 + 示例集
4. **托管版**（后置变现）：零部署云版，$10–30/月

## 5. v0 功能（MVP，只做这些）
- [ ] 服务器/节点列表（在线状态、资源占用）
- [ ] 电源控制：启动 / 停止 / 重启 / 强杀（危险操作需确认）
- [ ] 实时控制台：读日志、发指令
- [ ] 文件管理：列表 / 读取 / 写入 / 上传（插件、配置）
- [ ] 备份：创建 / 列表 / 恢复
- [ ] 安全：只读模式开关、确认机制、不泄漏 token

## 6. 技术方案
- Python 3.12+；`mcp` SDK；httpx
- 目录：`src/panelpilot/`（client / server / config）+ `tests/`
- 配置走环境变量：PANEL_URL / PANEL_API_KEY / PANEL_READONLY
- 分发：PyPI + uvx；Docker 镜像（备选）
- 不做：计费 / WHMCS 集成（v1 之后再谈）

## 7. 名字
**PteroPilot** — 翼龙领航员（2026-09-27 季总定名）。
改名原因：原名 PanelPilot 与硬件品牌 Lascar PanelPilot 及多个工控/PLC 仓撞名。
查重（2026-09-27）：GitHub 0 仓、npm/PyPI 均 free；`Ptero-` 直指 Pterodactyl，语义更强。
（曾考虑的备选：Pteria、Conure、Avion；更早的 PanelPilot、Wingman、Roost。）

## 8. 第一周节奏
- D1–2：竞品深扒 3 个主力 + 功能定稿（名字已定 ✅）
- D3–4：代码骨架 + 核心工具（列表 / 电源 / 控制台）
- D5：文件 / 备份 + 文档初稿（中英）
- D6：打包 + README + 发 GitHub + 提交 MCP 目录（mcp.so / Glama / awesome 列表）
- D7：中文 MC 社区首帖

## 9. 分工
- **我（小虾）**：竞品深扒、代码、文案、上架、进度跟踪
- **季总**：GitHub 账号、拍板；之后：用户访谈 / 发帖 / 选下一步

## 10. 成功指标 & 退路
- 第 1 周：repo 上线 + 目录收录
- 第 2–4 周：有真实 star / issue / 至少 1 人问「有没有托管」
- 4 周无信号 → 按框架换方向（不恋战）

---
_项目根目录：D:\Projects\pteropilot；GitHub 仓库建议 `telson-dev/pteropilot`。_
