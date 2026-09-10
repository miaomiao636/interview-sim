---
name: interview-sim
description: 启动、配置和使用本地 AI 求职面试模拟工作台。当用户说“帮我启动面试系统”“打开面试工作台”“开始模拟面试”“配置面试 Skill”或需要导入 JD/简历进行证据驱动训练时使用。
---

# Interview Sim

将这个 Skill 当作本地应用启动器与使用导航，不在对话中收集 API Key。

## 启动

1. 解析本 Skill 目录的真实路径，上三级为完整仓库根目录。确认包含 backend/、frontend/ 和 pyproject.toml；只复制 Skill 文件夹不足以启动。
2. 优先使用项目 .venv/bin/python。环境未建立时，按完整仓库 docs/QUICKSTART.md 在用户授权的目录安装，不修改系统 Python。
3. 使用该解释器运行 <skill-directory>/scripts/launch.py。启动器输出 INTERVIEW_SIM_URL=http://127.0.0.1:<port> 并自动打开浏览器。
4. 保持服务进程运行。不要让用户直接打开 HTML 文件。

也可在安装命令行入口后直接运行 `interview-sim web`。如果首选端口被占用，启动器会自动选择后续可用端口；始终以命令输出的 URL 为准。

## 首次使用

页面会自动进入“系统设置”。请用户在本地页面中完成：

- 称呼、目标岗位与本轮训练目标。
- 默认 API 地址、Key 和模型名。对话、分析、ASR、TTS 可分别配置；文字需要对话 / 分析，语音适配 MiMo 或标准 OpenAI Audio，不默认认为所有网关兼容。
- 默认面试官、难度与音色。

API Key 仅写入本机 `~/.interview-sim/config.json`，页面和 API 都不会读回密钥明文。不要要求用户在对话中粘贴密钥。

## 常规使用

- 在“准备面试”导入 JD 和简历，支持 TXT、MD、DOCX、PDF、PNG、JPG 和 WEBP。
- DOCX/PDF/图片仅在本地提取文字，原文件不留存；开始面试后，提取出的 JD、简历和回答会发送给用户配置的模型 API。
- 若需检查服务，运行 `interview-sim status --port <port>` 或访问 `http://127.0.0.1:<port>/api/health`。
- 用户要停止时，向运行服务的终端发送 Ctrl-C。
- 网页结束面试可仅保存或生成报告。录音 / 草稿不自动提交，刷新前提醒用户处理。
- 下一题连接失败暂时没有独立重试入口，不重复发送已保存回答；可查看记录并选择结束。本 Skill 不授权代答或上传个人资料。
- 配置与排错分别参阅完整仓库 docs/CONFIGURATION.md、docs/FAQ.md。主要验证 macOS 桌面，不承诺其他宿主兼容。
