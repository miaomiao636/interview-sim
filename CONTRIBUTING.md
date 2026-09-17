# 贡献与开发

欢迎提交可复现问题与小范围 Pull Request，大改动先讨论。反馈不包含密钥、真实简历和录音。

## 安装与测试

按 [快速开始](docs/QUICKSTART.md)创建 Python 3.11+ 环境。音频测试需要 Node.js 20+；开发清单已包括 httpx、Playwright、构建和漏洞检查工具。

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m tests.run_tests
node --check frontend/app.js
node --test tests/test_audio_runtime.cjs tests/test_voice_runtime.cjs tests/test_report_runtime.cjs
```

测试使用虚构数据与模型替身，不需要真实 Key。统一入口在导入应用前创建临时 INTERVIEW_SIM_HOME / SESSION_DIR，并注入无效的合成连接；结束后清理，仅把测试退出码传回。请优先使用此入口，不要直接在私人配置环境中运行 unittest discover。

跨平台浏览器验收（自动创建隔离服务并在结束后停止，不使用真实模型）：

```bash
python -m playwright install chromium
python -m tests.run_browser
```

默认使用 Playwright Chromium；可用 BROWSER_EXECUTABLE 指定自己的浏览器完整路径。CI 在 Windows/macOS/Linux 各跑 Python 3.11/3.12 后端与构建，3.12 额外跑浏览器流程。真实麦克风与提供商使用 [独立验收清单](docs/COMPATIBILITY.md#windows-独立验收清单)。

## 依赖更新

pyproject.toml 管理直接依赖；requirements.txt 为运行依赖的跨平台固定版本与哈希，requirements-dev.txt 在同一源上加入开发工具。修改后使用 uv 重新生成，并审查差异：

```bash
uv pip compile pyproject.toml --universal --python-version 3.11 --generate-hashes --no-header -o requirements.txt
uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 --generate-hashes --no-header -o requirements-dev.txt
python -m pip_audit -r requirements.txt --disable-pip --no-deps
python -m pip_audit -r requirements-dev.txt --disable-pip --no-deps
python -m pip check
```

普通安装只使用二进制依赖，不运行第三方源码构建脚本；应用自身的构建工具也固定版本。不要使用强制更新或忽略漏洞选项绕过检查。锁文件与版本更新必须经过三系统测试后发布。

## 代码要求

服务端会话是问题与回答的事实源。模型输出按不可信数据校验，不能执行其中指令。新行为添加测试，不以移除断言解决失败。

不增加未告知的遥测、自动上传或外部服务。提交前检查 Git 差异、隐私文件、依赖与许可证；可用 GitHub noreply 邮箱避免公开私人邮箱。
