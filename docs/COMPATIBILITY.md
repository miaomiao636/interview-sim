# 平台、Agent 与验收边界

本应用不是 Codex 专用程序。Python 服务负责面试，桌面浏览器负责交互，Skill 只是安装/启动/配置导航。

## 支持目标与实际验证

| 层次 | 支持目标 | 不等于什么 |
|---|---|---|
| 桌面系统 | Windows、macOS、Linux；Python 3.11/3.12 自动测试矩阵 | 不等于所有发行版、CPU 和硬件均已验收 |
| 浏览器 | Playwright Chromium 跨平台模拟数据验收；推荐桌面 Chrome/Edge | 不等于真实麦克风、扬声器、权限及所有语音提供商均已验收 |
| Agent | 能读取本地文件、执行命令、安装依赖并维持进程的宿主 | 不等于所有客户端自动发现 ~/.agents/skills |
| 模型 | 已实现的 Chat Completions、MiMo/标准 Audio 协议 | 不等于所有声称兼容的网关均支持 JSON、流式和音频 |

查看 [Tests 实际运行结果](https://github.com/miaomiao636/interview-sim/actions/workflows/tests.yml)，仅绿色且对应测试版本的运行表示通过。手机、多人协作、跨设备同步和公网托管不在范围内。

## 不同 Agent 如何使用

1. 支持通用 Skill 目录：按快速开始注册；必要时重开会话。
2. 使用自己的 Skill 目录：向 `scripts/install_skill.py --dest` 传入该宿主文档确认的完整目录，Windows 默认复制，无需管理员权限。
3. 不支持自动发现，但能读文件和执行命令：让它直接读取完整仓库 `.agents/skills/interview-sim/SKILL.md`。
4. 只有聊天能力、没有本机执行权限：不能替用户启动本地服务；用户可自行启动网页。

复制版含 `interview-sim-location.json`，只保存完整项目所在位置，不保存密钥。它是本机生成文件，不应提交 Git。移动项目或更新 Skill 后重新注册；安装器拒绝覆盖已有内容。原来的符号链接安装继续可用。

## 图片识别

- macOS：使用 Apple Vision；需要系统 Swift 工具链。
- Windows：按 [Tesseract 官方安装指引](https://tesseract-ocr.github.io/tessdoc/Installation.html)选择 Windows 安装包，安装到用户认可的位置，将包含 `tesseract.exe` 的目录加入当前用户 PATH，重开终端和 Agent；不要求把整个面试服务以管理员身份运行。
- Linux（Debian/Ubuntu 示例）：按系统管理规范安装 `tesseract-ocr`、`tesseract-ocr-chi-sim`、`tesseract-ocr-eng`；其他发行版按官方包名安装。
- 两个平台均运行 `tesseract --version` 和 `tesseract --list-langs`，确认包含 `chi_sim`、`eng`。语言模型来自 [官方 tessdata](https://github.com/tesseract-ocr/tessdata)。缺少时只影响图片识别，文字、DOCX 和文本型 PDF 仍可使用。

OCR 临时图片在外部识别开始前关闭，成功或失败后清理；输出统一按 UTF-8 解码。不要通过关闭安全软件、提升整个 Agent 权限或上传简历到陌生 OCR 网站解决问题。

## Windows 独立验收清单

先使用 examples/ 的虚构材料；用户在本地设置页自行填写模型 Key，不在聊天或 Issue 中粘贴。

- 新目录（包含空格和中文）安装、启动、关闭后重启；端口冲突时正确使用新地址。
- 无管理员权限安装复制版 Skill；由目标 Agent 读取并启动。记录 Agent 名称和版本，不能用其他宿主测试代替。
- 创建/编辑两个岗位；TXT、DOCX、文本 PDF 和中文图片分别导入。
- 文字回答、跳题、仅结束保存；从训练记录恢复查看并补生成报告。
- 真实麦克风长停顿不自动结束，手动停止、修改文字、确认发送；试听、重听、停止朗读。
- 对话/分析/ASR/TTS 分别配置；报告失败后恢复、导出 Markdown/JSON、打印。
- 反馈记录系统、Python、浏览器/Agent 版本、提交或 Release 版本、复现步骤及脱敏错误；不上传 config.json、原始 session、真实简历或 Key。

## 依赖与本地文件权限

Python 3.9/3.10 不再属于新版本安装范围，以避免安全修复版依赖无法安装。运行依赖固定在 requirements.txt，开发依赖固定在 requirements-dev.txt，均有下载哈希；普通使用无需开发工具。安装命令见快速开始。

macOS/Linux 使用 0700 目录和 0600 文件。Windows 使用用户目录继承的 ACL；Python chmod 不能提供同等的 POSIX 隔离保证。使用自己 Windows 账户的非共享目录，不要把 INTERVIEW_SIM_HOME 指向所有人可写目录、公共盘或同步盘。自动测试在 Windows 检查可读写及密钥不回传，不将“通过测试”描述为完整 ACL 安全审计。
