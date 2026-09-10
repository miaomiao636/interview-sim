# 项目结构与数据流

结构图按本版本实际源码编写，由 Archify 生成交互页面。可点选节点查看关联、搜索、缩放、切换主题或导出图片。

[![项目结构](architecture/interview-sim-preview.png)](https://miaomiao636.github.io/interview-sim/architecture/interview-sim.html)

[打开交互版](https://miaomiao636.github.io/interview-sim/architecture/interview-sim.html) · [编辑 JSON 源文件](architecture/interview-sim.architecture.json) · [下载 HTML](architecture/interview-sim.html) · [SVG 导出图](architecture/interview-sim.svg) · [详细 Mermaid 数据流](architecture/interview-sim.mmd)

本图将文件提取、岗位预设和题纲归为“材料与面试准备”，将 ASR 与 TTS 两类独立连接归为一组。为便于阅读，省略模块重复的文件读写线；下方表格保留具体实现对应关系。Pages 展示的是图表文档，实际面试仍需在自己的电脑启动。

## 查看与维护

- 点击节点查看上游与下游，按 Esc 或关闭按钮退出聚焦。
- 右下角搜索按钮查找节点，使用加减按钮缩放；PATH 查看有向路径。
- 顶部 Light / Dark 切换主题，Classic 菜单选择视觉样式，Present 进入演示模式，Export 导出 PNG 或 SVG 等格式。
- 修改 JSON 后使用 Archify 重新验证和生成，避免手动修改生成的 HTML。图中不包含个人配置或模型密钥。

生成工具：Archify 2.15.0，来源 [yuppiez99999/archify-](https://github.com/yuppiez99999/archify-)，固定提交 `98648ce928d2bda5516ec85b699e0b1ae8530885`。Node.js 18 及以上即可运行生成器，无须安装生成器依赖。生成页面内置 SVG 和交互代码，字体可回退到系统字体；模板可能请求 Google Fonts。

在包含 `bin/archify.mjs` 的 Archify Skill 目录执行，下面的路径替换为本项目内对应文件路径：

```bash
node bin/archify.mjs validate architecture <JSON路径> --quality showcase --json
node bin/archify.mjs deliver architecture <JSON路径> <HTML路径> --quality showcase --json
node bin/archify.mjs visual-check <HTML路径> --json
```

生成与视觉检查记录见 [校验说明](architecture/VALIDATION.md)，第三方查看器许可见 [ARCHIFY-LICENSE.txt](architecture/ARCHIFY-LICENSE.txt)。

## 启动与模块

| 模块 | 文件 | 职责 |
|---|---|---|
| Skill | .agents/skills/interview-sim/ | 指导 Agent 找到完整仓库并启动，不收集密钥 |
| 命令行 | backend/cli.py | 选择本地端口、启动服务、打开网页、检查状态 |
| 本地服务 | backend/main.py | 同源提供网页和接口，仅监听回环地址 |
| 工作台 | frontend/app.js、jobs.js、workspace.js | 岗位、设置、作答、历史记录 |
| 音频 | frontend/voice.js、recorder-worklet.js、audio.js | 手动录音、分段转写、PCM 缓冲播放 |
| 文件提取 | backend/document_parser.py、ocr_image.swift | 文本 / DOCX / PDF、本地 OCR |
| 题纲与对话 | backend/routers/plan.py、chat.py、prompts.py | 基于材料建题纲，结合回答追问 |
| 报告 | backend/routers/review.py、report_pipeline.py、structured.py | 后台分阶段分析、输出校验、缓存恢复 |
| 记录与配置 | backend/store.py、config.py、routers/presets.py | 本地 JSON、岗位预设、分能力连接 |
| 模型适配 | backend/xiaomi_client.py | 对话、分析、ASR、TTS 的远程请求 |

## 三条数据路径

1. **导入**：浏览器上传 → 本机解析 / OCR → 页面核对 → 岗位或会话保存。原始文件不长期存储。
2. **面试**：服务端保存当前回答 → 对话 API 出题 → 保存新问题 → 网页显示，可调用 TTS 朗读。语音作答先调用 ASR，再由用户确认发送。
3. **结束**：仅结束直接保存本地状态；选择报告才进入分析 API → 分阶段校验与缓存 → 汇总报告 → 本机保存和导出。

## 数据边界

用户自己的 ~/.interview-sim/ 保存 config.json、presets.json 和 sessions/*.json。Key 只保存在本机配置，设置接口仅返回配置状态。

模型调用会发送必要的文字或音频；本地提取与本地页面不等于离线 AI。公司背景来自用户材料，不存在自动公司调查模块。

没有数据库服务、云同步、账号登录、招聘平台采集或自动投递。不要把本地服务部署到公网。

## 主要接口

| 接口 | 方法 | 用途 |
|---|---|---|
| /api/settings | GET / PUT | 读取非密配置 / 保存配置 |
| /api/presets | GET / POST | 读取 / 创建岗位 |
| /api/presets/{id} | PUT | 修改岗位 |
| /api/presets/parse | POST | AI 提取 JD |
| /api/documents/extract | POST | 本地文件文字提取 |
| /api/plan | POST | 建立会话、题纲和首题 |
| /api/chat | POST | 保存当前回答并生成下一题 |
| /api/transcribe/stream | POST | 分段音频转写 |
| /api/tts/stream | POST | PCM16 音频返回 |
| /api/sessions | GET | 训练记录摘要 |
| /api/sessions/{id} | GET | 记录详情 |
| /api/sessions/{id}/skip | POST | 当前题未回答并推进 |
| /api/sessions/{id}/end | POST | 仅结束保存，不调用模型，可重复请求 |
| /api/sessions/{id}/retry | POST | 准备原题下一次作答 |
| /api/review/jobs | POST | 创建 / 继续报告任务 |
| /api/sessions/{id}/review-job | GET | 进度与结果 |
| /api/review/estimate | GET | 按题量和本机样本估时 |
| /api/review | POST | 兼容同步报告入口 |
| /api/health | GET | 本地服务和配置状态 |

出题失败独立重试入口尚未实现，见 [已知限制](../README.md#已知限制)。
