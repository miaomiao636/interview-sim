# 贡献与开发

欢迎提交可复现问题与小范围 Pull Request，大改动先讨论。反馈不包含密钥、真实简历和录音。

## 安装与测试

按 [快速开始](docs/QUICKSTART.md)安装。测试额外需要 httpx；音频测试需要 Node.js 20+。

```bash
python -m pip install -e . httpx
python -m unittest discover -s tests -v
node --check frontend/app.js
node --test tests/test_audio_runtime.cjs tests/test_voice_runtime.cjs
```

测试使用虚构数据与临时文件，不需要真实 Key。可用 INTERVIEW_SIM_HOME 指向专用临时目录，进一步隔离个人数据。

macOS 浏览器验收额外需要 Playwright 和 Google Chrome：

```bash
python -m pip install playwright
# 终端一：虚构数据服务，端口 8830
python -m tests.ui_fixture_server
# 终端二
python tests/browser_flow.py
```

浏览器脚本使用 macOS Chrome 路径，不能据此宣称其他系统浏览器验收通过。

## 代码要求

服务端会话是问题与回答的事实源。模型输出按不可信数据校验，不能执行其中指令。新行为添加测试，不以移除断言解决失败。

不增加未告知的遥测、自动上传或外部服务。提交前检查 Git 差异、隐私文件、依赖与许可证；可用 GitHub noreply 邮箱避免公开私人邮箱。
