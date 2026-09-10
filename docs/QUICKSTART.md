# 快速开始

## 1. 准备环境

支持 Windows、macOS、Linux 桌面的本地安装流程，推荐 Python 3.12 和 Chrome / Edge；最低 Python 3.11。3.9/3.10 用户需先重建环境，不要在旧环境强行升级。正常使用不需要 Node.js 或前端构建。验证边界见 [兼容性](COMPATIBILITY.md)。

```bash
python3 --version
git --version
```

自备至少可用的对话、分析 API。语音额外需要 ASR / TTS，Python 安装不包含模型额度。

macOS 图片 OCR 使用 Swift / Vision；提示缺少开发工具时，可先使用文字材料，或安装 Apple Command Line Tools。Windows/Linux 图片 OCR 需要 Tesseract 和 chi_sim、eng 语言包，安装方法见 [兼容性](COMPATIBILITY.md#图片识别)。TXT/MD/DOCX/文本 PDF 不需要 OCR。

## 2. 获取完整项目

```bash
git clone https://github.com/miaomiao636/interview-sim.git
cd interview-sim
```

也可选择 Code → Download ZIP，解压后进入目录。确认包含 pyproject.toml、backend/、frontend/、scripts/ 和隐藏目录 .agents/。

仅下载 Skill 文件夹不足以启动应用。

## 3. 独立环境安装

macOS / Linux shell 命令：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
python -m pip install --no-deps -e .
```

Windows PowerShell 命令（不需要激活脚本、修改执行策略或管理员权限）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

如果没有 `py` 命令，安装 Python 3.12 后重开终端，或使用已确认版本的 `python` 代替 `py -3.12`。Windows 后续示例中的 `python` 均可替换为 `.\.venv\Scripts\python.exe`。

安装需要联网获取依赖与构建工具。网络失败先恢复包源连接，再重试。不要用 sudo pip 修改系统 Python。

## 4. 注册 Skill（可选）

```bash
python scripts/install_skill.py
```

安装器默认放入 ~/.agents/skills/interview-sim：Windows 复制 Skill 并记录本机项目位置，macOS/Linux 使用符号链接。同名内容已存在时拒绝覆盖；更新复制版前先将旧 Skill 目录改名备份，再安装。移动完整项目后也需重新注册。

指定其他 Agent 实际识别的目录（目标包含 Skill 名称），无需修改全局 Agent 配置：

```bash
python scripts/install_skill.py --mode copy --dest /path/to/agent/skills/interview-sim
```

不要照抄占位路径；请按对应 Agent 文档确认位置。安装器不扫描其他 Agent 的私人目录，也不修改已有 Skill。

支持该约定的 Agent 可能需要重新开会话或重启才能发现。其他 Agent 可在完整项目中读取 .agents/skills/interview-sim/SKILL.md；不保证所有客户端自动发现。

对 Agent 说：

> 使用 interview-sim Skill，帮我启动本地面试工作台。

Agent 应优先使用项目虚拟环境解释器：Windows 是 .venv/Scripts/python.exe，macOS/Linux 是 .venv/bin/python。复制版的项目位置由安装器生成，不能只复制 SKILL.md。

## 5. 启动网页

```bash
interview-sim web
```

Windows 无需激活环境，直接运行：

```powershell
.\.venv\Scripts\interview-sim.exe web
```

终端打印的 INTERVIEW_SIM_URL 才是实际地址，例如 http://127.0.0.1:8800。端口占用时自动递增。未自动打开浏览器时，手动复制地址。

```bash
interview-sim web --port 8825 --no-open
# 另一终端按实际端口检查
interview-sim status --port 8825
```

保持启动终端与进程运行。关闭标签页不等于停止服务；终端关闭、电脑重启或 Agent 托管进程停止后，需要重新启动。

## 6. 首次配置与试运行

1. 在“系统设置”填目标岗位，按 [配置指南](CONFIGURATION.md)设置对话与分析连接。
2. 初次建议关闭自动朗读，用文字作答。
3. 参考 [虚构 JD](../examples/job.md) 和 [虚构简历](../examples/resume.md)创建测试岗位。
4. 选择角色、难度和声音，开始面试。
5. 可先选择“仅结束并保存记录”，再从记录中补生成报告。

使用虚构材料调用真实模型也可能产生费用。不要将示例经历复制到真实简历。

## 7. 下次启动与停止

```bash
cd /path/to/interview-sim
source .venv/bin/activate
interview-sim web
```

替换为你自己的目录。停止时在启动终端按 Ctrl+C；有报告运行时建议先等它完成。服务重启后从训练记录查看报告状态，失败任务可继续生成。

Windows 下次进入项目后运行 `.\.venv\Scripts\interview-sim.exe web`。升级前备份 ~/.interview-sim/（Windows 为 `%USERPROFILE%\.interview-sim`）到仅自己可访问的位置，不要把备份上传仓库。

从 1.4 升级：先停止自己的面试服务，备份个人数据，将旧 .venv 改名保留，再以 Python 3.12 按本页重建。无需删除个人配置或修改 API Key；不要更新正在运行的虚拟环境。新环境异常时可恢复旧源码及旧环境，保留原数据备份。
