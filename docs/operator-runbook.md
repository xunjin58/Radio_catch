# 本地运维手册

## 配置

复制 `.env.example` 为 `.env`，并在启动 API 的 shell 中加载所需值。

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `RADIO_CATCH_SECRET_KEY` | API Key 加密派生密钥；生产/长期使用时必须替换。 | 开发用固定值 |
| `RADIO_CATCH_ENCRYPTION_KEY` | 可选 Fernet 密钥，优先于 `RADIO_CATCH_SECRET_KEY`。 | 未设置 |
| `RADIO_CATCH_DATABASE_URL` | SQLAlchemy SQLite 或外部数据库 URL。 | `backend/data/radio_catch.db` |
| `RADIO_CATCH_STORAGE_DIR` | 上传素材和派生帧目录。 | `backend/storage` |
| `RADIO_CATCH_EXPORT_DIR` | 成片导出目录。 | `backend/data/exports` |
| `RADIO_CATCH_API_PORT` | 本地 API 监听端口。 | `8001` |

## 启动与验证

```bash
npm run dev:api
npm run dev
curl http://127.0.0.1:8001/api/health
curl http://127.0.0.1:8001/api/media/health
npm run build
```

`/api/media/health` 会返回 FFmpeg 和 FFprobe 的可用性。需要系统 PATH 中存在两个可执行文件。素材导入只读取必要的文件头信息；默认不再自动生成缩略图或关键帧，MiMo 原生理解会直接使用原视频。

素材库详情播放使用 `GET /api/media/clips/<clip_id>/video`。它只会提供仍位于 `RADIO_CATCH_STORAGE_DIR` 内、且已在数据库登记的原视频；若详情显示视频无法播放，先确认原文件未被手动移动或删除，并确认浏览器支持该视频编码。

素材库详情可人工修改摘要、菜品、片段角色、最佳出现时间、可用区间和结构化标签 JSON；保存不会改变素材的审核状态。最佳出现时间必须是非负秒数，且不能超过素材时长；留空可清除。点击“重新回炉识别”会创建新的分析版本并更新详情显示，同样保留当前审核状态；旧分析版本保留在本地数据库中供追溯。MiMo 原生理解失败时，接口会随后执行一次本地全量解码诊断；诊断不抽帧或保存派生媒体。需要关键帧时才显式调用 `POST /api/media/clips/<clip_id>/process`。

AI 混剪规划使用 `remix_planning` 任务模型；如未单独分配，则使用默认且已启用图片输入的模型。规划仅读取同菜品、已审核素材，并发送每条候选的摘要、标签和已存在的封面/最多 3 张关键帧；未显式处理的 MiMo 素材没有派生图时，规划会仅依据其结构化理解结果。文案先行时将需画面佐证的 `script_facts` 传入；返回 `uncovered_facts` 时必须改写事实或补素材，不能继续 TTS。规划只能安排完整原视频的顺序，导出不会裁剪或变速。规划图片会优先通过 FFmpeg 内存管道缩放为最长边 512 像素、最大 256 KiB 的 JPEG，避免多素材请求过大；转换失败时仅接受本已不超过该上限的图片。图片数据、原视频和 API Key 都不得写入日志、数据库、响应或故障单。素材库较大时服务端只发送 24 条代表性候选；规划数少于请求数量属于正常结果，表示当前素材不足以形成更多不重复变体。

成片管理使用 `GET /api/renders/<render_id>/video` 播放最终 MP4，并通过 `GET /api/renders/<render_id>/thumbnail` 读取宫格封面。封面由 FFmpeg 从最终成片约 15% 时刻提取，存放在 `RADIO_CATCH_EXPORT_DIR` 中，属于可再生派生文件；历史成片首次请求封面时会自动补生成。两个接口都只允许读取已完成且仍位于导出目录内的成片。

## 成片音画后期（交付必经）

除非用户明确要求静音或不做后期，基础混剪 MP4 不能直接作为最终交付；必须为其制作带旁白、低音量配乐和同步字幕的新版 MP4。原始混剪须保留，后期版本写入导出目录下的独立子目录。

1. 从最终成片和完整 EDL 核对镜头顺序，只描述可见画面、可听见声音或画内文字支持的事实。
2. 先基于已审核素材的 `shot_capabilities` 和用户确认卖点池写连续安利，标出需画面证据的事实断言，再调用 planner 配片渲染；不要逐句映射镜头或解说画面动作。动作只用于校验，口播要说价值、对比和消费场景。按实测成片时长精修后，使最终 TTS 从视频开头连续覆盖至结尾。默认使用当前批准的 MIMO“茉莉”音色和 1.2 倍语速；规则与两段式字数预算见 `backend/prompts/copywriting_xiaohongshu.md`。
3. 从已获授权的候选池选择背景音乐并做淡入淡出；人声优先，默认将背景音乐相对默认目标提高 10% 线性增益（约 +0.83 dB），但音乐仍仅作氛围且显著低于人声。
4. 烧录与最终旁白对齐的分段字幕，不使用底板，并避免遮挡产品主体；保留旁白音频、字幕文件、音乐片段信息和源片映射清单。
5. 交付前完整播放每条视频，使用媒体探测确认视频时长和规格未改变、存在可解码 AAC 音轨、无爆音且音乐不盖住人声。

### MiMo 后期执行清单

按 [MiMo 音画后期流程](mimo-postproduction.md)执行。每个批次都在 `RADIO_CATCH_EXPORT_DIR` 下新建独立目录，绝不覆盖基础 Render 或旧版后期文件。

1. **预检**：读取 Render 的 `video_id` 和完整 EDL；用 FFprobe 确认源片可解码、1080×1920、30 fps、存在 AAC 音轨。核对待审媒体为 MiMo 支持格式，原文件不超过当前配置的上限，Base64 data URL 在发送前不超过 50 MB。
2. **文案与可选看片**：先由素材能力清单和用户确认卖点池起草连续安利，使用 `script_facts` 校验事实断言的选片证据；商业背景和模型推断不能充当卖点依据。成片级 MiMo `mimo-v2.5` 审核（2 fps、JSON）不再是必经步骤，可用 `--analyze-only` 复核粗粒度事实。manifest 必须记录事实→切片的 `fact_evidence_mapping`，以及实际使用的用户确认卖点和来源（`product_facts`）。
3. **TTS 与混音**：使用 `mimo-v2.5-tts`、茉莉音色；以 1.2×为基准，根据实测时长调整文案或语速，使人声自开头安全余量后连续覆盖至结尾安全余量前。配乐必须有授权来源记录，淡入淡出后以人声优先的 ducking 混音；默认背景音乐相对目标维持 +0.83 dB 调整，但仍显著低于人声。
4. **字幕与产物**：字幕按实测 TTS cues 烧录为白字黑描边、无背景板，并避开主体；保留原始与处理后旁白、ASS/等价字幕源、音乐片段信息、完整 EDL、画面审核和 manifest。
5. **验收**：完整播放成片；FFprobe 验证时长、1080×1920、30 fps、H.264/AAC；全量解码测试和音量检测必须通过，无削波，且人工确认音乐不压过人声。
6. **回写与展示**：`mimo_postprocess.py` 成功结束后，确认对应 Render 的 `final_delivery.status` 为 `available`；在“成片管理”播放和下载最终交付版，并切换一次“基础 Render”核对 EDL。最终交付丢失时 Web 会回退基础 Render，Agent 应重新执行该批后期，而不是覆盖基础文件。

## 商家业务背景

“模型与接口”页面的“商家业务背景 / AI 提示词补充说明”保存于本地数据库的 `ProjectSettings`，不属于 `ModelConfig`，也不保存 API Key。新项目默认使用柠檬商家背景；修改后只影响之后发起的素材分析，不会重写历史标签。

背景中的销售描述不是视频事实。审核时应移除没有画面或音频证据的 `commerce_roles`，并且不要把价格、产地、甜度、农残等不可验证声明作为素材标签或成片卖点。

## Gemini 原生视频

兔子 API Gemini 3 配置的 Base URL 必须是根地址 `https://api.tu-zi.com`。原生媒体默认上限为 100 MB，按单个模型配置存储；视频会连同内嵌音轨以一次请求发送给供应商，因此不要将原始请求体、Base64 数据或 API Key 写入终端、日志或故障单。

“测试连接”只读取模型列表，成功不代表模型的视频、音频或结构化输出调用已经验证。使用小型、含音轨的 MP4 完成一次素材分析和审核，才是实际链路验证。

## MiMo Token Plan 原生视频

MiMo 预设使用 `https://token-plan-cn.xiaomimimo.com/v1` 和 `mimo-v2.5`。完整视频及其内嵌音轨会以 OpenAI 兼容的 `video_url` Base64 数据发送；不得将原始请求体、Base64、视频或 API Key 写入终端、日志或故障单。MiMo 请求的 Base64 数据 URL 上限为 50 MB，因此界面将原文件大小限制在 37 MB，并在发送前再次校验。

原生输入仅接受 MP4、MOV、AVI、WMV；`auto` 和 `native` 可用，`adaptive`、`dense` 不会触发关键帧回退。连接测试只读取 `/models`，不代表视频、音轨或结构化输出已验证。应使用小型、含音轨的 MP4 执行一次真实素材分析；若失败，确认模型名为 `mimo-v2.5`、Token Plan Key 仍有效、文件格式与大小符合限制。

## 排障

| 现象 | 检查与处理 |
| --- | --- |
| 上传返回 `503` | 安装 FFmpeg，重开终端后调用 `/api/media/health`。 |
| MiMo 原生理解失败 | 先阅读接口返回的本地解码诊断：若提示源视频无法完整解码，重新导出或替换该素材；若提示本地可完整解码，再检查模型配置、原文件大小、50 MB Base64 上限和供应商状态。 |
| 显式素材处理任务失败 | 查询 `/api/media/jobs/{job_id}`；确认磁盘空间、视频是否可解码、存储目录是否可写。 |
| 素材详情视频返回 404 或无法播放 | 确认素材 ID 存在、原文件仍在 `RADIO_CATCH_STORAGE_DIR` 内；若文件存在但浏览器不支持编码，重新导入兼容编码的 MP4。 |
| 保存素材编辑返回 422 | 检查片段角色为 `head`、`middle` 或 `tail`，最佳出现时间为未超过素材时长的非负秒数，可用区间结束秒数大于开始秒数，且结构化标签为 JSON 对象。 |
| 回炉识别后显示旧标签或状态异常 | 刷新素材库后确认新 `ClipAnalysis` 已生成；详情只展示最新版本。重新识别应继承 `Clip.review_status`，不会自动改为待审核。 |
| AI 混剪规划返回图片能力错误 | 在“模型与接口”中为默认模型或 `remix_planning` 分配一个 `supports_images=true` 的活动模型。 |
| AI 规划数量少于请求数量 | 查看 `shortfall_reason`；补充同菜品、已审核且有可用区间的素材，尤其是不同角度、动作或镜头角色的素材后重新规划。 |
| AI 混剪规划提示图片无法压缩 | 调用 `/api/media/health` 确认 FFmpeg 可用；规划图片仅在内存中转换，不会写入素材目录。 |
| Gemini 原生理解失败 | 确认配置的 Base URL 为 `https://api.tu-zi.com`、模型开启原生视频、文件仍在本地且不超过上限；连接测试仅验证模型列表。 |
| MiMo 原生理解失败 | 确认 Token Plan Base URL、模型名、API Key，以及视频为 MP4/MOV/AVI/WMV 且原文件不超过 37 MB；连接测试仅验证模型列表。 |
| OpenAI 兼容模型理解失败 | 在模型配置上调用 `test-connection`；确认 `base_url` 包含供应商需要的版本路径，且模型接受图像输入。 |
| 导出失败 | 检查每段完整原视频是否已审核、同菜品、具有有效时长，及 `RADIO_CATCH_EXPORT_DIR` 是否可写。 |
| 下载成片返回 409/404 | 先确认成片状态为 `completed`，并确认导出文件仍在 `RADIO_CATCH_EXPORT_DIR` 中。 |
| 最终交付版未显示或返回 409/404 | 确认 Agent 后期命令完整成功、`--music-path` 和授权引用已提供，并检查最终 MP4 仍位于 `RADIO_CATCH_EXPORT_DIR`；重新运行同批后期会回写对应 Render，基础 Render 不受影响。 |
| 成片宫格没有封面 | 确认 FFmpeg 可用、成片文件仍在 `RADIO_CATCH_EXPORT_DIR` 且可解码；刷新宫格会再次请求并补生成封面。封面失败不影响 MP4 下载或播放。 |
| CSV 跳过数据 | 查看导入响应中的 `errors`；确认 `video_id` 是本系统 Render 返回的值。 |

本地数据目录未纳入版本控制。升级前应备份 `backend/data/` 和 `backend/storage/`。
