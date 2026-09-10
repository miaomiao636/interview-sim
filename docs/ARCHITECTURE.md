# 项目结构与数据流

结构图按本版本实际源码绘制。静态图可放大查看，Mermaid 源文件可编辑；未实现在线图表编辑器。

![项目结构](architecture/interview-sim.svg)

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
