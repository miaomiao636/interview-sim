# 模型与本地配置

## 网页配置优先

在“系统设置”填写称呼（可选）、目标岗位与训练目标，再配置 AI。保存后新请求使用新配置，无需改源码；录音、作答或报告运行中先等待完成。

默认连接包括 Base URL 和 API Key，四个模型名分别填写。展开“分别配置对话、分析和语音服务”，关闭某个能力的“继承默认连接”后，可填写独立地址与 Key。

| 能力 | 用途 | 协议要求 |
|---|---|---|
| 对话 | 下一题与追问 | /chat/completions，流式文本 |
| 分析 | JD 提取、题纲与报告 | /chat/completions，支持 response_format 的 json_object |
| ASR | 录音转写 | MiMo 音频消息，或标准 /audio/transcriptions |
| TTS | 试听与朗读 | MiMo 语音，或标准 /audio/speech，PCM16 |

一些“兼容 OpenAI”的网关只支持文字，未必支持 JSON 模式或音频。

## 同一提供商

填写默认 API 根地址和 Key，让需要的能力继承，并为每个能力填写账号实际可用的模型名。

| 项目内置 MiMo 字段 | 默认值 |
|---|---|
| Base URL | https://api.xiaomimimo.com/v1 |
| 对话 | mimo-v2.5 |
| 分析 | mimo-v2.5-pro |
| ASR | mimo-v2.5-asr |
| TTS | mimo-v2.5-tts |
| 语音协议 | mimo |

这些只是项目默认值，不承诺权限、价格或可用性；请核对提供商当前控制台。

## 不同提供商组合

例如文字继承默认连接，ASR / TTS 各自关闭继承，分别设置地址与 Key，并选择“OpenAI 标准音频”协议；模型名称也要分别设置。

Base URL 填 API 根地址，例如 https://api.example.com/v1，不能填产品官网、浏览器聊天地址或完整的 /chat/completions 路径。example.com 仅作说明，不可调用。

更换连接地址必须重新填 Key，不会自动把旧提供商密钥发给新地址。Key 留空通常表示保留已有值，**不是删除密钥**。

## 音色与采样率

- MiMo 使用面试页提供的声音选项，具体支持范围以服务为准。
- 标准 TTS 音色 ID 留空时适配器默认 alloy；提供商不支持时需填其有效音色 ID。
- 独立 TTS 音色 ID 会覆盖面试页的 MiMo 音色，因此不同提供商下的性别选择不一定生效。
- PCM 采样率必须匹配实际输出，默认 24000 Hz。不匹配会导致变速、音调异常。
- 先“试听”再正式练习；仅修改模型名无法适配私有语音协议。

## 个人数据位置

| 本机文件 | 内容 |
|---|---|
| ~/.interview-sim/config.json | 个人设置、连接与密钥 |
| ~/.interview-sim/presets.json | 岗位 JD、简历和预设 |
| ~/.interview-sim/sessions/*.json | 回答、报告与阶段进度 |

设置接口只返回 has_api_key，不向前端回传 Key。文件权限不等于加密，仍需保护电脑与备份。

高级用户可用 INTERVIEW_SIM_HOME 改变数据根目录，SESSION_DIR 单独覆盖记录目录。网页保存配置优先于环境默认值。

config/.env.example 只提供示例，一般无需复制。若创建 config/.env，请勿提交 Git。启用其中 SESSION_DIR=./sessions 时，记录改存启动时的相对目录，而非用户目录。

本项目不提供公共模型额度。费用、地区可用性和数据政策由提供商决定。排错时只分享错误类别、模型名与脱敏地址，不分享密钥或完整配置。
