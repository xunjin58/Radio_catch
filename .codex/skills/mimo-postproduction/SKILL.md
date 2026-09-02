---
name: mimo-postproduction
description: 为 Radio Catch 已导出的短视频制作 Script-First 口播、MiMo TTS、配乐和无底板字幕；适用于用户要求视频后期、配音、配乐或字幕交付时。
---

# MiMo 音画后期

仅用于本项目基础 Render 的交付后期；不用于重新剪辑 EDL、抖音发布或普通素材分析。

## 先确认输入

- 读取项目 `AGENTS.md`、[后期流程](../../../docs/mimo-postproduction.md) 和目标 Render 的完整 EDL。
- 保留基础 MP4 与既有交付版；新批次只能写入 `backend/data/exports/<batch>/`。
- 使用启用的 MiMo `ModelConfig` 运行时解密凭据。禁止复制、打印、硬编码或持久化 API Key、视频 Base64 和原始请求体。
- 确认用户已提供或确认商品事实与背景音乐授权；画面审核不能自行证明产地、价格、无籽、营养或功效。

## 文案与可选看片

1. 先读取已审核素材的 `shot_capabilities` 和本批用户确认卖点池，按 `backend/prompts/copywriting_xiaohongshu.md` 为当前品类起草连续安利；柠檬可额外调用 [柠檬带货口播](../lemon-selling-copy/SKILL.md) 作为品类化起草方法。标出 `fact_assertions` 和证据；动作只用于校验，不能解说正在发生的镜头。
2. 用户要求模仿参考带货文案或需给多条视频写口播时，派一个子 Agent 基于每条素材能力与参考起草候选。主代理核对事实证据、用户确认商品信息与实测时长后才可定稿。
3. 将需要画面支撑的断言以 `script_facts` 交给 planner；`uncovered_facts` 非空时必须改写或补素材。用户确认的价格、产地等写入 `product_facts`，不能伪装成画面证据。
4. `mimo_postprocess.py --analyze-only` 可让 `mimo-v2.5` 以原生视频、2 fps 输出覆盖全片的可见事实 JSON，供交付前复核；只保存结果，不保存请求体。它不是文案或 TTS 的必经前置。

## 合成与交付

- 使用 `mimo-v2.5-tts` 与用户指定音色；默认茉莉、以 1.2×为基准。按实测音频调整文案或有效语速，使人声仅在开头/片尾各留不超过 0.3 秒安全余量。
- 配乐必须有授权来源，淡入淡出且在人声期间 duck；默认背景音乐相对目标增加约 +0.83 dB，但始终显著低于人声。
- 字幕采用最终 cue，白字黑描边、无底板，避开产品主体。FFmpeg 无 `subtitles`/libass 滤镜时，使用透明 PNG 图层叠加。
- 可复用实现位于 `backend/scripts/mimo_postprocess.py` 与 `backend/scripts/render_caption_layers.py`；执行前审核批次常量、音乐路径和文案，不能把运行时副本当作唯一实现。
- 每批次 manifest 必须关联 `video_id`、完整 EDL、可选画面审核、脚本、cues、`fact_evidence_mapping`、`product_facts`、TTS/音乐参数、授权引用和最终媒体校验。

## 验收

逐条播放并确认文案事实、字幕可读性及音乐不盖人声；再用 FFprobe、全量解码和响度检测验证 H.264/AAC、1080×1920、30 fps、时长和无削波。
