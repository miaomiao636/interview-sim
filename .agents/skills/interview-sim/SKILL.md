---
name: interview-sim
description: 启动、配置和使用本地 AI 求职面试模拟工作台。当用户说“帮我启动面试系统”“打开面试工作台”“开始模拟面试”“配置面试 Skill”或需要导入 JD/简历进行证据驱动训练时使用。
---

# Interview Sim

将这个 Skill 当作本地应用启动器与使用导航，不在对话中收集 API Key。

## 启动

1. 解析本 Skill 目录的真实路径。仓库内 Skill 的上三级为完整仓库根目录；安装器生成的复制版使用同目录 interview-sim-location.json 定位。确认包含 backend/、frontend/ 和 pyproject.toml；只手动复制 Skill 文件夹不足以启动。位置失效时请用户确认完整项目位置并重新安装，不广泛搜索私人目录。
2. 需要 Python 3.11+，推荐 3.12。Windows 使用项目 .venv/Scripts/python.exe，macOS/Linux 使用 .venv/bin/python。环境未建立时，按完整仓库 docs/QUICKSTART.md 在用户授权的目录安装锁定依赖，不修改系统 Python。
3. 使用该解释器运行 <skill-directory>/scripts/launch.py。路径含空格时加引号。启动器也会尝试切换至项目 .venv，输出 INTERVIEW_SIM_URL=http://127.0.0.1:<port> 并自动打开浏览器。
4. 保持服务进程运行。不要让用户直接打开 HTML 文件。

也可在安装命令行入口后直接运行 `interview-sim web`。如果首选端口被占用，启动器会自动选择后续可用端口；始终以命令输出的 URL 为准。

## 首次使用

页面会自动进入“系统设置”。请用户在本地页面中完成：

- 称呼、目标岗位与本轮训练目标。
- 默认 API 地址、Key 和模型名。对话、分析、ASR、TTS 可分别配置；文字需要对话 / 分析，语音适配 MiMo 或标准 OpenAI Audio，不默认认为所有网关兼容。
- 默认面试官、难度与音色。

API Key 仅写入本机 `~/.interview-sim/config.json`，页面和 API 都不会读回密钥明文。不要要求用户在对话中粘贴密钥。

## 常规使用

- 在“岗位工作台”导入 JD 和简历，支持 TXT、MD、DOCX、文本 PDF、PNG、JPG 和 WEBP；扫描 PDF 请改传图片。
- 岗位卡的“准备这个岗位”提供可选的简历诊断、要求分析和经历提纲；“开始面试”直接选择已保存的简历版本与本次角色/声音/难度。两者进入同一套纯实战，现场不显示提示或评分。
- DOCX/PDF/图片仅在本地提取文字，原文件不留存；用户主动运行准备任务、面试、语音或报告时，必要材料会发送给所配置的模型 API。
- 若需检查服务，运行 `interview-sim status --port <port>` 或访问 `http://127.0.0.1:<port>/api/health`。
- 用户要停止时，向运行服务的终端发送 Ctrl-C。
- 网页结束面试可仅保存或生成报告。录音 / 草稿不自动提交，刷新前提醒用户处理。
- 下一题连接失败时，已保存回答不重发，使用页面明确的重新出题入口；后台仍运行时只刷新当前问题。服务重启后不自动重跑计费任务，需用户确认恢复；也可选择仅结束。
- 报告分开显示简历诊断、首次面试和面后重答，旧五维报告不改口径。单题重答提交或跳过后结束，不追加普通问题。
- 面后素材仅从关联会话原答提取为待确认；核对后确认/编辑/拒绝，只有确认素材用于后续准备，不自动改简历。详见完整仓库 docs/PREPARATION.md、docs/SCORING.md。本 Skill 不授权代答、自动发送答案、公开资料或替用户确认经历真实性。
- 配置与排错分别参阅完整仓库 docs/CONFIGURATION.md、docs/FAQ.md。跨平台安装与 Agent 能力边界见 docs/COMPATIBILITY.md；不假定每个宿主都会自动发现 Skill。无需 Codex 专有工具，能读取本地文件和执行命令的 Agent 可按上述流程使用。
