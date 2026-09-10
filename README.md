# Interview Sim · 本地 AI 面试工作台

用岗位 JD 和真实经历练习面试，把每条反馈追溯到自己的回答。

**Skill / Agent 启动 · 本地网页操作 · 文字与语音作答 · 四类模型独立配置**

[快速开始](docs/QUICKSTART.md) · [模型配置](docs/CONFIGURATION.md) · [操作指南](docs/USER_GUIDE.md) · [常见问题](docs/FAQ.md) · [提交问题](https://github.com/miaomiao636/interview-sim/issues)

> **公开测试版 v1.4.0-beta.2**：包含完整应用与 Skill，不是独立桌面安装包或托管网站。主要在 macOS 桌面验证；需自备模型 API，费用由提供商收取。已知限制见下文，不承诺招聘结果。

## 演示视频

[▶ 查看 / 下载 89 秒操作演示（MP4）](https://github.com/miaomiao636/interview-sim/releases/download/v1.4.0-beta.2/interview-sim-demo.mp4)

视频展示岗位材料导入、AI 解析、面试配置与模拟作答。为避免公开本机信息，已裁去浏览器标签栏和 Dock，并对文件选择窗口做模糊处理。

## 项目结构图

[![Interview Sim 项目结构：Skill 与命令行启动本地服务，网页使用文档解析、题纲、对话和报告模块；本机保存配置与记录，模型请求发往各自的 API。](docs/architecture/interview-sim.svg)](docs/architecture/interview-sim.svg)

[模块与数据流说明](docs/ARCHITECTURE.md) · [可编辑 Mermaid 源文件](docs/architecture/interview-sim.mmd) · [SVG 原图](docs/architecture/interview-sim.svg)

这是静态结构图，可打开原图放大查看，不依赖第三方图表服务。

## 可以做什么

| 能力 | 实际行为 |
|---|---|
| 多岗位预设 | 独立保存公司、JD、简历与训练目标；导入后可人工修改 |
| 面试配置 | 选择角色、难度与声音，本次选择不覆盖岗位预设 |
| 自适应提问 | 结合公司材料与已保存回答追问；不自动联网核实公司信息 |
| 文件导入 | TXT、MD、DOCX、文本型 PDF、PNG/JPG/WEBP，本地提取文字 |
| 手动语音作答 | 约每 4 秒分段转写；停顿不结束，停止后编辑并确认发送 |
| 跳题与结束 | 跳题标记未回答；结束时可仅保存，或生成报告 |
| 证据报告 | 逐题引用、缺口、置信度、评分与训练建议；失败复用已完成阶段 |
| 专项重答 | 从报告练习原题，保留历次回答与得分 |
| 导出 | Markdown、JSON、浏览器打印 / PDF |

## 快速开始

推荐 macOS、Python 3.11 / 3.12 和桌面 Chrome / Edge。应用最低要求 Python 3.9；其他操作系统尚未全流程验收。正常使用不需要 Node.js，也无需构建前端。

```bash
git clone https://github.com/miaomiao636/interview-sim.git
cd interview-sim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python scripts/install_skill.py
interview-sim web
```

访问终端打印的 `INTERVIEW_SIM_URL`，通常为 `http://127.0.0.1:8800`；端口占用时自动选择后续端口。保持服务进程运行，不要双击 HTML 文件。

在“系统设置”填写目标岗位和模型连接，再创建岗位开始面试。**API Key 只在本地网页输入，不要发给 Agent 或提交 GitHub。**

安装 Skill 后，可以对支持本地 Skill 和命令执行的 Agent 说：

> 帮我启动 interview-sim 面试工作台；如果尚未配置，引导我在本地网页完成设置。

Skill 是完整应用的启动入口，不能只下载 `SKILL.md`。安装、ZIP 下载、虚拟环境和重新启动详见 [快速开始](docs/QUICKSTART.md)。

## 模型配置

网页支持对话、分析、ASR、TTS 分别设置地址和 Key，并分别填写模型名，也可继承同一连接。

- 对话 / 分析：OpenAI-compatible Chat Completions；分析接口还需支持 JSON 对象输出。
- 语音：MiMo 或标准 OpenAI Audio；厂商私有协议不能只改模型名兼容。
- 不使用语音时可文字作答并关闭自动朗读，对话与分析连接仍需配置。

字段、连接示例、音色与采样率要求见 [配置指南](docs/CONFIGURATION.md)。内置模型名只是默认值，不代表账号一定有调用权限。

## 运行与隐私边界

- 服务仅监听 `127.0.0.1`，不面向公网或局域网部署。
- 配置、岗位与面试记录保存在使用者自己的 `~/.interview-sim/`，与源码分离。
- 原始上传文件不长期保存；提取出的 JD、简历与回答会保存在本地记录。
- **本地网页不等于离线 AI**：使用 AI 识别、面试、语音或报告时，必要材料会发给对应模型服务。
- 设置接口不会返回 Key 明文；仍需保护本机配置、电脑与备份。
- 录音片段和未提交草稿仅在页面内存中，刷新或关闭前请先处理。

本仓库只提供源码与虚构示例，不附带真实用户资料。见 [隐私与安全](SECURITY.md)。

## 已知限制

1. 下一题生成连接中断后，回答可能已保存，但本版没有独立的“重试下一题”入口。可查看训练记录并选择仅结束或生成现有记录的报告；不要重复发送同一回答。
2. 语音为约 4 秒分段 ASR，不是逐字实时识别；网络和提供商影响延迟，音质需在自己的设备上试听。
3. 扫描 PDF 暂不支持逐页 OCR，可改传图片；非 macOS 图片识别需 Tesseract 与中文语言包。
4. 未验证所有提供商、浏览器、操作系统与 Agent，不宣称通用兼容。
5. 报告时间仅供参考。分数不代表真实招聘评价，简历改写需核实，不能添加不存在的经历。

## 文档导航

| 文档 | 内容 |
|---|---|
| [快速开始](docs/QUICKSTART.md) | 获取完整项目、安装 Skill、启动和停止 |
| [配置指南](docs/CONFIGURATION.md) | 四类模型、Key、协议与个人数据 |
| [操作指南](docs/USER_GUIDE.md) | 岗位、面试、语音、跳题、报告与导出 |
| [常见问题](docs/FAQ.md) | 启动、权限、连接、文件解析与报告失败 |
| [架构说明](docs/ARCHITECTURE.md) | 模块、接口和本地 / 远程数据流 |
| [虚构示例](examples/) | 试运行 JD / 简历，不代表真实招聘或经历 |
| [更新记录](CHANGELOG.md) | 版本内容与验证范围 |
| [贡献指南](CONTRIBUTING.md) | 开发、测试、脱敏反馈 |

## 许可证与致谢

采用 [MIT License](LICENSE)。

发布页面的组织方式参考 [BossHunter](https://github.com/shengjidaguai-china/BossHunter)。架构图按本项目代码独立绘制，未复制其代码、图片或许可证；两个项目无隶属或背书关系。
