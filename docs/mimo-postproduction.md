# MiMo 音画后期流程

本流程把已完成的基础 Render 转成可交付成片。它适用于需要 MiMo 原生看片、MiMo TTS、配乐和同步字幕的本地后期；不会修改基础 Render、原片、模型配置或 EDL。成功后，脚本会将最终 MP4、无秘密的 manifest 摘要和交付时间回写到原 Render，供 Web 优先展示。

可复用后期实现必须位于受版本控制的 `backend/scripts/`；当前参考实现为 `backend/scripts/mimo_postprocess.py` 与 `backend/scripts/render_caption_layers.py`。设置 `RADIO_CATCH_PROJECT_DIR` 后，交付批次、音频、字幕图层和 manifest 仅写入 `<项目>/exports/<批次名>/`；未设置时沿用 `backend/data/exports/<批次名>/`。不将运行时副本作为唯一实现来源。

## 1. 输入与安全边界

每条任务都需要基础 MP4、`video_id`、完整 `edit_decision_list`、用户确认的交付要求，以及一首已获授权的本地音乐。任务运行前确认存在启用的 MiMo 原生 `ModelConfig`，并使用项目的加密字段在运行时取得凭据；不得复制、打印或另存 API Key。音乐必须通过每批 `--music-path` 与 `--music-license-reference` 显式传入，或由 `RADIO_CATCH_POSTPROCESS_MUSIC_PATH` 和 `RADIO_CATCH_POSTPROCESS_MUSIC_LICENSE_REFERENCE` 提供；禁止依赖个人电脑路径。

对每个 MP4，在本地先校验 MiMo 支持的格式、配置的原文件大小上限和 50 MB Base64 data URL 上限。完整视频仅用于一次原生 `video_url` 请求，Base64、请求体和原始视频绝不进入日志、JSON、数据库或交付目录。

## 2. 标准作业

1. **预检和追溯**：用 FFprobe 记录源片时长、尺寸、帧率和音轨；从 Render 保存 `video_id` 与完整 EDL。创建 `RADIO_CATCH_PROJECT_DIR/exports/<批次名>/`（或显式 `RADIO_CATCH_EXPORT_DIR`），绝不覆盖源片。
2. **文案先行与配片**：从已审核素材的 `shot_capabilities` 汇总和用户确认卖点池起草一段连续安利；不再按镜头逐句播报，也不解说画面动作。动作只作事实校验，口播要补足为什么值得、和什么比、什么场景会想吃/喝。将需画面证据的事实断言传入 planner，所选 EDL 必须覆盖这些能力；未覆盖的 `uncovered_facts` 必须改写或补素材。`--scripts-json` 在请求 TTS 前会拒绝“画面/镜头里有什么”“镜头一转”“某动作画面”等镜头解说；应改成商品事实、价值对比或消费场景。每条入选素材均须完整播放，不能为 TTS 时长截取、加速或缩短镜头，更不能以仓促的短镜头替代收尾；时长不匹配时先改文案，允许且仅允许在全片末尾额外保留 1–2 秒留白/静默，并在 EDL 与 manifest 标明。价格、产地等用户确认商品事实在 manifest 的 `product_facts` 记录来源。按基础 Render 的实测时长精修定稿。
3. **可选 MiMo 看片**：`mimo_postprocess.py --analyze-only` 可用 `mimo-v2.5`、2 fps、`media_resolution=default` 做成片复核，输出覆盖全片的粗粒度事实 JSON；不保存原请求。它不是文案和 TTS 的必经前置。
4. **MiMo TTS**：使用 `mimo-v2.5-tts` 和用户指定音色；默认茉莉、1.2×基准、自然口语语气。以实测音频为准扩写/压缩文案，默认开头与片尾各保留不超过 0.3 秒安全余量。若用户明确允许且文案自然时长不足，可用 `--max-tail-blank-seconds` 在**完整视频**的片尾保留至多 2 秒无旁白留白；不能借此截取、加速或缩短任何素材。茉莉实测约 4.3–4.8 有效字/秒（含标点约 4.9–5.6 字符/秒），20s 成片有效字数约 100–108；常规实测语速必须落在 1.08×–1.34×，超出即精简重写（后期脚本会在 TTS 前按字数预估并预警）。
5. **混音和字幕**：保留原始 TTS 与速度/响度处理后的音频。音乐必须淡入淡出、低于人声，默认在旁白期间 duck；用户明确要求关闭 duck 时可以固定音量混音，但 manifest 必须记录该例外，且音乐仍仅作氛围。背景音乐按项目默认目标提高 10% 线性增益（约 +0.83 dB）。字幕使用最终 cue，白字黑描边、不加底板，且不得遮挡主体。默认字幕字体为受版本控制的 `backend/assets/fonts/CheeseFoamOolongSong-Bold.ttf`，字号 60px；长句须拆为依次出现的单行 cue，不得在同一时刻显示多行。
6. **交付、回写与验证**：输出 H.264/AAC MP4，并完整播放。使用 FFprobe、全量解码和响度检测验证规格、时长、音轨与无削波；人工确认文案事实性、字幕可读性和音乐不盖人声。脚本写完批次 `delivery_manifest.json` 后，必须成功回写原 Render；Web 应显示“最终交付版”，且仍可切换基础 Render 核查 EDL。

所有 FFmpeg 调用使用参数数组，禁止拼接 shell 命令。

## 柠檬历史批次参考（2026-08-27）

- 旁白：MiMo `mimo-v2.5-tts`，茉莉音色；每条语音从 0.25 秒开始，按实测时长覆盖至片尾前约 0.25 秒。
- 配乐：历史批次曾使用固定 `-18 dB` 且关闭 duck。新批次以脚本默认的旁白 ducking 为准；如业务方要求复用旧例外，须显式传入 `--no-ducking` 并在 manifest 中记录原因。
- 字幕：`CheeseFoamOolongSong-Bold.ttf`、60px、白字黑描边、透明无底板；每次只能显示一行，最长 14 个可见字符，优先在标点处分段。

## 3. 交付 manifest

每个批次生成一个不含秘密和原始媒体的 JSON manifest。单条记录至少含：

```json
{
  "video_id": "RC-...",
  "source": "基础 MP4 路径",
  "source_edl": [{"clip_id": "...", "start": 0.0, "end": 2.4, "speed": 1.0}],
  "vision_review": "mimo_vision_analysis.json",
  "script": "最终口播",
  "cues": [{"start": 0.25, "end": 4.8, "text": "字幕句子"}],
  "tts": {"model": "mimo-v2.5-tts", "voice": "茉莉", "effective_speed": 1.2, "audio": "voice.wav"},
  "music": {"path": "licensed.mp3", "license_reference": "授权记录", "volume_adjustment_db": 0.83, "ducking": "人声优先"},
  "output": "final.mp4",
  "fact_evidence_mapping": {"汁水多": {"clip_ids": ["clip-id"], "shot_capabilities": ["squeezing"]}},
  "product_facts": {"lemon_facts": "本批实际使用的用户确认卖点（价格/无籽/产地等）与确认来源", "beverage_mapping": "深色=可乐；有气泡清澈=气泡水；无气泡清澈=矿泉水"},
  "media_probe": {"width": 1080, "height": 1920, "fps": 30, "video_codec": "h264", "audio_codec": "aac"}
}
```

`source_edl` 必须是基础 Render 的完整 EDL，不以文件路径替代。音乐的授权引用可以是采购记录、素材库条目或用户确认记录；不确定授权时不得使用该音乐。

脚本还会将单条 manifest 摘要保存至原 Render 的 `delivery_manifest`，并保存最终 MP4 路径和 `delivered_at`。API 只返回受控的最终版播放、下载、封面和 manifest 接口，不向 Web 暴露任意本地路径；基础 `output_path` 与 EDL 始终保留。

## 4. 失败恢复

- MiMo 视频审核失败：停止后续文案和 TTS，检查模型配置、格式、原文件与 50 MB 请求上限；连接测试成功不代表视频推理可用。
- TTS 失败：不改用含硬编码 Key 的外部脚本；检查同一加密 `ModelConfig` 的凭据和模型可用性后重试。
- 字幕滤镜不可用：可改为透明 PNG 字幕图层叠加，但仍必须无底板、保留 cue 源文件并逐帧检查位置。
- 校验失败：保留基础 Render 和中间资产，仅删除/重做对应后期版本；修复后重新跑完整验收。
