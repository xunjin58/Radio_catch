# MiMo 音画后期流程

本流程把已完成的基础 Render 转成可交付成片。它适用于需要 MiMo 原生看片、MiMo TTS、配乐和同步字幕的本地后期；不会修改基础 Render、原片、模型配置或 EDL。

可复用后期实现必须位于受版本控制的 `backend/scripts/`；当前参考实现为 `backend/scripts/mimo_postprocess.py` 与 `backend/scripts/render_caption_layers.py`。交付批次、音频、字幕图层和 manifest 仅写入 `backend/data/exports/<批次名>/`，不将运行时副本作为唯一实现来源。

## 1. 输入与安全边界

每条任务都需要基础 MP4、`video_id`、完整 `edit_decision_list`、用户确认的交付要求，以及一首已获授权的本地音乐。任务运行前确认存在启用的 MiMo 原生 `ModelConfig`，并使用项目的加密字段在运行时取得凭据；不得复制、打印或另存 API Key。

对每个 MP4，在本地先校验 MiMo 支持的格式、配置的原文件大小上限和 50 MB Base64 data URL 上限。完整视频仅用于一次原生 `video_url` 请求，Base64、请求体和原始视频绝不进入日志、JSON、数据库或交付目录。

## 2. 标准作业

1. **预检和追溯**：用 FFprobe 记录源片时长、尺寸、帧率和音轨；从 Render 保存 `video_id` 与完整 EDL。创建 `RADIO_CATCH_EXPORT_DIR/<批次名>/`，绝不覆盖源片。
2. **MiMo 看片**：以 `mimo-v2.5`、2 fps、`media_resolution=default` 请求 JSON，要求返回覆盖全片的时间段、可见事实、可用叙事角色和应避免的卖点。保存处理后的事实 JSON，不保存原请求。
3. **人工文案**：按镜头编写“抓眼开头（仅当画面支持）—使用动作—成品展示—轻 CTA”。每句话需能落在画面事实、画内文字、可听见声音或有明确来源的商品信息上；把每句映射为 cue。不要将业务背景、模型猜测或“未见籽”转换为产地、价格、无籽、营养或功效宣称。
4. **MiMo TTS**：使用 `mimo-v2.5-tts` 和用户指定音色；默认茉莉、1.2×基准、自然口语语气。以实测音频为准扩写/压缩文案，允许开头与片尾各保留不超过 0.3 秒的安全余量，其余时间必须连续有人声。
5. **混音和字幕**：保留原始 TTS 与速度/响度处理后的音频。音乐必须淡入淡出、低于人声，默认在旁白期间 duck；用户明确要求关闭 duck 时可以固定音量混音，但 manifest 必须记录该例外，且音乐仍仅作氛围。背景音乐按项目默认目标提高 10% 线性增益（约 +0.83 dB）。字幕使用最终 cue，白字黑描边、不加底板，且不得遮挡主体。默认字幕字体为受版本控制的 `backend/assets/fonts/CheeseFoamOolongSong-Bold.ttf`，字号 60px；长句须拆为依次出现的单行 cue，不得在同一时刻显示多行。
6. **交付与验证**：输出 H.264/AAC MP4，并完整播放。使用 FFprobe、全量解码和响度检测验证规格、时长、音轨与无削波；人工确认文案事实性、字幕可读性和音乐不盖人声。

所有 FFmpeg 调用使用参数数组，禁止拼接 shell 命令。

## 当前柠檬五条交付配置（2026-08-27）

- 旁白：MiMo `mimo-v2.5-tts`，茉莉音色；每条语音从 0.25 秒开始，按实测时长覆盖至片尾前约 0.25 秒。
- 配乐：用户确认的现有轻快商业配乐，固定 `-18 dB`，淡入淡出，不使用 duck；每条 manifest 必须记录 `ducking: disabled by user request`。
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
  "media_probe": {"width": 1080, "height": 1920, "fps": 30, "video_codec": "h264", "audio_codec": "aac"}
}
```

`source_edl` 必须是基础 Render 的完整 EDL，不以文件路径替代。音乐的授权引用可以是采购记录、素材库条目或用户确认记录；不确定授权时不得使用该音乐。

## 4. 失败恢复

- MiMo 视频审核失败：停止后续文案和 TTS，检查模型配置、格式、原文件与 50 MB 请求上限；连接测试成功不代表视频推理可用。
- TTS 失败：不改用含硬编码 Key 的外部脚本；检查同一加密 `ModelConfig` 的凭据和模型可用性后重试。
- 字幕滤镜不可用：可改为透明 PNG 字幕图层叠加，但仍必须无底板、保留 cue 源文件并逐帧检查位置。
- 校验失败：保留基础 Render 和中间资产，仅删除/重做对应后期版本；修复后重新跑完整验收。
