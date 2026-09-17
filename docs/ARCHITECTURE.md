# 项目结构与数据流

下方 Archify 结构图对应已发布版本的基础模块，不包含本地未发布的专家协作增量。未手改生成的 HTML；新增模块与接口以本文和 [岗位准备](PREPARATION.md)、[评分规则](SCORING.md)为准。图中可点选节点查看关联、搜索、缩放、切换主题或导出图片。

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
| 准备工作区 | frontend/preparation.js、backend/routers/preparation.py | 岗位双入口、版本、原文建议与素材确认 |
| 专家任务 | backend/preparation_tasks.py、preparation_models.py | 简历/招聘视角/经历整理/面后摘录，严格结构与来源校验 |
| 准备档案 | backend/preparation_store.py | 不可变正文、修订链、建议采纳及用户确认事实 |
| 音频 | frontend/voice.js、recorder-worklet.js、audio.js | 手动录音、分段转写、PCM 缓冲播放 |
| 文件提取 | backend/document_parser.py、ocr_image.swift | 文本 / DOCX / PDF、本地 OCR |
| 题纲与对话 | backend/routers/plan.py、chat.py、prompts.py | 基于材料建题纲，结合回答追问 |
| 报告 | backend/routers/review.py、report_pipeline.py、report_models.py、structured.py | 分阶段分析、逐题引用校验、四维服务端聚合、首答/重答缓存和历史报告 |
| 记录与配置 | backend/store.py、config.py、routers/presets.py | 本地 JSON、岗位预设、分能力连接 |
| 模型适配 | backend/xiaomi_client.py | 对话、分析、ASR、TTS 的远程请求 |

## 三条数据路径

1. **导入**：浏览器上传 → 本机解析 / OCR → 页面核对 → 岗位或会话保存。原始文件不长期存储。
2. **面试**：服务端保存当前回答 → 对话 API 出题 → 保存新问题 → 网页显示，可调用 TTS 朗读。语音作答先调用 ASR，再由用户确认发送。
3. **结束**：仅结束直接保存本地状态；选择报告才进入分析 API → 分阶段校验与缓存 → 汇总报告 → 本机保存和导出。

新增的显式闭环：岗位/简历版本 → 可选准备任务 → 实战会话快照 → 首次与重答分开复盘 → 用户选择提取原答 → 待确认素材 → 确认/修订/拒绝 → 下次准备。任何一步都不自动把示范提纲当成真实简历；跳过准备也能进入实战。

## 状态与一致性

- 会话是原题原答的事实源。提交/跳题使用 `question_id + attempt + operation_id`；相同操作同内容重放不重复写入，不同内容返回409，旧页面不能把答案挂到新题。
- 动态追问标为 adaptive 并记录父题；不把第几题当成蓝图 ID。跳题仅按真实已问蓝图项推进。
- 出题、报告和专家任务使用输入指纹、代次和运行标识隔离迟到结果；每次远程调用前及落盘时检查当前状态。服务关闭先使运行任务失效再取消，重启仅标中断，不自动计费重跑。
- 任务状态、版本和会话均以 JSON 原子替换保存，单进程线程锁保护写入。涉及面试素材的双存储操作固定会话锁→准备锁顺序；不支持多个进程同时写同一数据目录。
- 新报告按单次作答指纹复用已验证反馈，即使汇总失败再重答也不覆盖首次评分。整体汇总与局部缓存各自校验；旧五维报告不转成新版分数。

## 数据边界

用户自己的 ~/.interview-sim/ 保存 config.json、presets.json、sessions/*.json 和 preparations/*.json。准备档案含任务输入、版本、结果和来源，属于私人资料。Key 只保存在本机配置，设置接口仅返回配置状态，模型连接密钥不写入任务快照。

模型调用会发送必要的文字或音频；本地提取与本地页面不等于离线 AI。公司背景来自用户材料，不存在自动公司调查模块。

没有数据库服务、云同步、账号登录、招聘平台采集或自动投递。不要把本地服务部署到公网。

## 主要接口

| 接口 | 方法 | 用途 |
|---|---|---|
| /api/settings | GET / PUT | 读取非密配置 / 保存配置 |
| /api/presets | GET / POST | 读取 / 创建岗位 |
| /api/presets/{id} | PUT | 修改岗位 |
| /api/presets/parse | POST | AI 提取 JD |
| /api/preparation/{id} | GET | 档案、版本、建议、素材与任务历史 |
| /api/preparation/{id}/versions | POST | 显式保存新简历版本（revision 冲突检查） |
| /api/preparation/{id}/tasks | POST | 启动可选专家任务，202 返回 task 与 revision |
| /api/preparation/{id}/tasks/{task_id} | GET | 查询专家任务，不自动重新运行 |
| /api/preparation/{id}/tasks/{task_id}/retry 或 /cancel | POST | 明确重试或取消 |
| /api/preparation/{id}/suggestions/{suggestion_id}/accept | POST | 原文定位、真实性确认后采纳为新版本 |
| /api/preparation/{id}/session-materials | POST | 从结束的关联面试提取原文片段为 pending，202 |
| /api/preparation/{id}/facts/{fact_id}/decision | POST | 确认、编辑或拒绝事实（不自动改简历） |
| /api/documents/extract | POST | 本地文件文字提取 |
| /api/plan | POST | 建立会话、题纲和首题 |
| /api/chat | POST | 保存当前回答并生成下一题 |
| /api/transcribe/stream | POST | 分段音频转写 |
| /api/tts/stream | POST | PCM16 音频返回 |
| /api/sessions | GET | 训练记录摘要 |
| /api/sessions/{id} | GET | 记录详情 |
| /api/sessions/{id}/skip | POST | 当前题未回答并推进 |
| /api/sessions/{id}/next-question | POST | 使用新 operation_id 显式恢复失败/中断的下一题，不重复保存原答 |
| /api/sessions/{id}/end | POST | 仅结束保存，不调用模型，可重复请求 |
| /api/sessions/{id}/retry | POST | 准备原题下一次作答 |
| /api/review/jobs | POST | 创建 / 继续报告任务 |
| /api/sessions/{id}/review-job | GET | 进度与结果 |
| /api/review/estimate | GET | 按题量和本机样本估时 |
| /api/review | POST | 兼容同步报告入口 |
| /api/health | GET | 本地服务和配置状态 |

新接口示例与完整字段可在本地服务的 `/docs` 查看。公开字段不含密钥，但会话与准备档案包含个人资料，不应公开代理这些接口。兼容同步报告 `/api/review` 保留新 schema 字段；读取旧会话不会隐式重新评分。
