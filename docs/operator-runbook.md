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

`/api/media/health` 会返回 FFmpeg 和 FFprobe 的可用性。需要系统 PATH 中存在两个可执行文件。

素材库详情播放使用 `GET /api/media/clips/<clip_id>/video`。它只会提供仍位于 `RADIO_CATCH_STORAGE_DIR` 内、且已在数据库登记的原视频；若详情显示视频无法播放，先确认原文件未被手动移动或删除，并确认浏览器支持该视频编码。

素材库详情可人工修改摘要、菜品、片段角色、可用区间和结构化标签 JSON；保存不会改变素材的审核状态。点击“重新回炉识别”会创建新的分析版本并更新详情显示，同样保留当前审核状态；旧分析版本保留在本地数据库中供追溯。该操作只重跑模型理解，不会重新处理原视频或关键帧。

成片管理使用 `GET /api/renders/<render_id>/video` 播放最终 MP4，并通过 `GET /api/renders/<render_id>/thumbnail` 读取宫格封面。封面由 FFmpeg 从最终成片约 15% 时刻提取，存放在 `RADIO_CATCH_EXPORT_DIR` 中，属于可再生派生文件；历史成片首次请求封面时会自动补生成。两个接口都只允许读取已完成且仍位于导出目录内的成片。

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
| 媒体任务失败 | 查询 `/api/media/jobs/{job_id}`；确认磁盘空间、视频是否可解码、存储目录是否可写。 |
| 素材详情视频返回 404 或无法播放 | 确认素材 ID 存在、原文件仍在 `RADIO_CATCH_STORAGE_DIR` 内；若文件存在但浏览器不支持编码，重新导入兼容编码的 MP4。 |
| 保存素材编辑返回 422 | 检查片段角色为 `head`、`middle` 或 `tail`，可用区间结束秒数大于开始秒数，且结构化标签为 JSON 对象。 |
| 回炉识别后显示旧标签或状态异常 | 刷新素材库后确认新 `ClipAnalysis` 已生成；详情只展示最新版本。重新识别应继承 `Clip.review_status`，不会自动改为待审核。 |
| Gemini 原生理解失败 | 确认配置的 Base URL 为 `https://api.tu-zi.com`、模型开启原生视频、文件仍在本地且不超过上限；连接测试仅验证模型列表。 |
| MiMo 原生理解失败 | 确认 Token Plan Base URL、模型名、API Key，以及视频为 MP4/MOV/AVI/WMV 且原文件不超过 37 MB；连接测试仅验证模型列表。 |
| OpenAI 兼容模型理解失败 | 在模型配置上调用 `test-connection`；确认 `base_url` 包含供应商需要的版本路径，且模型接受图像输入。 |
| 导出失败 | 检查每段素材是否已审核、同菜品、时间范围有效，及 `RADIO_CATCH_EXPORT_DIR` 是否可写。 |
| 下载成片返回 409/404 | 先确认成片状态为 `completed`，并确认导出文件仍在 `RADIO_CATCH_EXPORT_DIR` 中。 |
| 成片宫格没有封面 | 确认 FFmpeg 可用、成片文件仍在 `RADIO_CATCH_EXPORT_DIR` 且可解码；刷新宫格会再次请求并补生成封面。封面失败不影响 MP4 下载或播放。 |
| CSV 跳过数据 | 查看导入响应中的 `errors`；确认 `video_id` 是本系统 Render 返回的值。 |

本地数据目录未纳入版本控制。升级前应备份 `backend/data/` 和 `backend/storage/`。
