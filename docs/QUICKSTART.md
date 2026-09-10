# 快速开始

## 1. 准备环境

主要验证 macOS 桌面，推荐 Python 3.11 / 3.12 和 Chrome / Edge；Python 最低要求 3.9。正常使用不需要 Node.js 或前端构建。

```bash
python3 --version
git --version
```

自备至少可用的对话、分析 API。语音额外需要 ASR / TTS，Python 安装不包含模型额度。

macOS 图片 OCR 使用 Swift / Vision；提示缺少开发工具时，可先使用文字材料，或安装 Apple Command Line Tools。非 macOS 需要 Tesseract 和 chi_sim、eng 语言包；其他系统尚未全流程验收。

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
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

安装需要联网获取依赖与构建工具。网络失败先恢复包源连接，再重试。不要用 sudo pip 修改系统 Python。

## 4. 注册 Skill（可选）

```bash
python scripts/install_skill.py
```

安装器将 ~/.agents/skills/interview-sim 链接到当前仓库的 Skill，同名内容已存在时拒绝覆盖。移动项目后需要重新核对链接，不要删除旧项目后仍使用原链接。

支持该约定的 Agent 可能需要重新开会话或重启才能发现。其他 Agent 可在完整项目中读取 .agents/skills/interview-sim/SKILL.md；不保证所有客户端自动发现。

对 Agent 说：

> 使用 interview-sim Skill，帮我启动本地面试工作台。

Agent 应优先使用本项目 .venv/bin/python 运行 Skill 脚本，避免系统 Python 缺少依赖。

## 5. 启动网页

```bash
interview-sim web
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

升级前备份 ~/.interview-sim/ 到仅自己可访问的位置，不要把备份上传仓库。
