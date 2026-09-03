# API 集成指南

基础地址：`http://127.0.0.1:8001`。交互式接口定义位于 `/docs`；如设置了 `RADIO_CATCH_API_PORT`，请替换为对应端口。

## 项目文件夹

推荐在启动 API 前设置 `RADIO_CATCH_PROJECT_DIR`。系统会在该文件夹内统一保存 `radio_catch.db`（素材标签、审核、EDL）、`media/`（原视频和派生帧）及 `exports/`（基础成片、后期文件和 manifest）。这样整个项目可以作为一个文件夹备份或迁移；首次在另一台电脑启动时，系统会对已存在于该项目文件夹内的素材、标签证据、EDL 和交付路径做安全重定位。模型 Key 仍只存储在数据库的加密字段中，迁移到另一台电脑后应重新保存 Key。

旧工作区可先在项目根目录运行以下命令；默认复制而非删除旧文件，确认新项目正常后才可附加 `--move`：

```bash
backend/.venv/bin/python backend/scripts/migrate_to_project_folder.py \
  --project-dir /absolute/path/to/radio-catch-project
```

Windows PowerShell 示例：

```powershell
.\backend\.venv\Scripts\python.exe .\backend\scripts\migrate_to_project_folder.py `
  --project-dir D:\RadioCatch\lemon-september
$env:RADIO_CATCH_PROJECT_DIR = 'D:\RadioCatch\lemon-september'
```

## 常用流程

### 1. 导入素材（MiMo 优先）

```bash
curl -F 'file=@/absolute/path/clip.mp4' http://127.0.0.1:8001/api/media/imports
```

导入只保存原视频并读取必要的媒体头信息，不会自动抽帧或生成缩略图；返回中的 `job` 为 `null`。相同文件会返回 `duplicate: true`，不会创建重复素材。使用 MiMo 原生配置时可直接进入下一步。若需为 OpenAI 兼容的关键帧模型或人工排查生成证据帧，再显式调用：

```bash
curl -X POST http://127.0.0.1:8001/api/media/clips/<clip_id>/process
curl http://127.0.0.1:8001/api/media/jobs/<job_id>
```

### 1a. 查看素材原视频

素材库详情使用以下接口播放完整原视频：

```bash
curl -I http://127.0.0.1:8001/api/media/clips/<clip_id>/video
```

接口仅接受已登记的 `clip_id`，并在服务端确认原文件仍在项目 `media/`（或显式 `RADIO_CATCH_STORAGE_DIR`）内；不会暴露、返回或接受任意本地文件路径。素材不存在、文件已删除或记录指向存储目录外时返回 `404`。

### 2. 配置模型并理解素材

```bash
curl -X POST http://127.0.0.1:8001/api/model-configs \
  -H 'content-type: application/json' \
  -d '{"name":"vision","provider":"OpenAI-compatible","protocol":"openai","base_url":"https://provider.example/v1","api_key":"YOUR_KEY","model_name":"vision-model","supports_images":true,"supports_structured_json":true,"is_default":true}'

curl -X POST http://127.0.0.1:8001/api/clips/<clip_id>/analyze \
  -H 'content-type: application/json' -d '{"mode":"auto"}'
```

对 MiMo/Gemini 原生视频理解，接口会先直接发送原视频。若模型返回失败，响应会附带一次本地 FFmpeg 全量解码诊断：本地也无法完整解码时，说明源文件可能损坏；本地可完整解码时，应检查模型配置、媒体大小限制或供应商状态。该诊断不生成关键帧，也不保存原视频 Base64、请求体或密钥。

模型配置读取接口只返回 `api_key_masked`，不会返回原始密钥。

### 2a. 设置商家业务背景

新项目默认以“销售新鲜柠檬的商家”为素材标注背景。该背景会同时用于 Gemini、MiMo 和关键帧理解；它只帮助模型理解标签的后续用途，不能让模型编造产地、价格、甜度、农残等画面不可证实的卖点。

```bash
curl http://127.0.0.1:8001/api/project-settings
curl -X PATCH http://127.0.0.1:8001/api/project-settings \
  -H 'content-type: application/json' \
  -d '{"business_context":"我是柠檬商家；只标注视频或音频中可证实的信息，并为可见镜头标注带货角色。"}'
```

素材分析的 `tags.commerce_roles` 可能包含 `hook`（开场吸引）、`product_proof`（品质展示）、`usage`（使用场景）和 `cta`（明确行动引导）。`tags.shot_capabilities` 只会保留 `backend/prompts/shot_capabilities.json` 中、与已识别菜品匹配的可见能力；人工审核可删除不具备画面或音频证据的角色或能力。

### 兔子 API Gemini 3 原生视频配置

在“模型与接口”中选择“添加 Gemini 3”，填写兔子 API Key 即可创建预设。该预设固定使用根地址 `https://api.tu-zi.com`、协议 `gemini` 和模型 `gemini-3-flash-preview`；默认原生媒体大小上限为 100 MB，可按配置调整。

也可通过 API 创建：

```bash
curl -X POST http://127.0.0.1:8001/api/model-configs \
  -H 'content-type: application/json' \
  -d '{"name":"tuzi-gemini","provider":"兔子 API","protocol":"gemini","base_url":"https://api.tu-zi.com","api_key":"YOUR_KEY","model_name":"gemini-3-flash-preview","supports_images":true,"supports_native_video":true,"supports_structured_json":true,"max_native_media_bytes":104857600}'
```

Gemini 配置分析 `auto` 或 `native` 时会发送原始视频及其内嵌音轨，不会回退关键帧；`adaptive` 和 `dense` 返回 `422`。支持的本地原生视频 MIME 映射包括 MP4、MOV、M4V、WebM、AVI 与 MKV；超过上限或原文件不存在同样返回 `422`。`test-connection` 仅调用 `GET https://api.tu-zi.com/v1/models`，不消耗推理额度，也不验证视频推理。

### 小米 MiMo Token Plan 原生视频配置

在“模型与接口”中选择“添加 MiMo 视频”，填写 Token Plan API Key。预设使用 `https://token-plan-cn.xiaomimimo.com/v1`、协议 `mimo` 与模型 `mimo-v2.5`，通过 OpenAI 兼容的 `POST /chat/completions` 将完整视频和内嵌音轨作为 Base64 `video_url` 发送。模型名可按账户能力调整，但 `mimo-v2.5` 是当前预设和视频理解文档所列模型。

MiMo 原生适配器只接受 `auto` 或 `native`，不会回退关键帧；`adaptive` 和 `dense` 返回 `422`。本地仅接受 MP4、MOV、AVI 与 WMV，界面将原视频上限限制在 37 MB，并在发送前再次校验 Base64 数据 URL 不超过 MiMo 的 50 MB 限制。Base64、原视频内容与 API Key 不会进入数据库、响应或日志。`test-connection` 只调用 `GET https://token-plan-cn.xiaomimimo.com/v1/models`，不消耗推理额度，也不验证视频推理。

也可通过 API 创建：

```bash
curl -X POST http://127.0.0.1:8001/api/model-configs \
  -H 'content-type: application/json' \
  -d '{"name":"mimo-native","provider":"小米 MiMo Token Plan","protocol":"mimo","base_url":"https://token-plan-cn.xiaomimimo.com/v1","api_key":"YOUR_KEY","model_name":"mimo-v2.5","supports_images":true,"supports_native_video":true,"supports_structured_json":true,"max_native_media_bytes":38797312}'
```

### 3. 审核并创建实验

```bash
curl -X PATCH http://127.0.0.1:8001/api/clips/<clip_id>/review \
  -H 'content-type: application/json' \
  -d '{"status":"approved","updates":{"dish":"柠檬","segment_role":"middle","usable_range":{"start":0.2,"end":2.4},"tags":{"commerce_roles":["product_proof"]}}}'
```

素材库详情中的“编辑素材”使用独立的状态中性接口；所有字段均可选，未提交的字段保持原值。`tags` 必须是 JSON 对象，且采用完整替换语义，因此可删除或重构任意业务标签；服务端会保留内部 `thumbnail_path`。`dish` 作为独立字段优先写入标签中的 `dish`。

```bash
curl -X PATCH http://127.0.0.1:8001/api/clips/<clip_id>/metadata \
  -H 'content-type: application/json' \
  -d '{"summary":"人工修订的镜头摘要","dish":"柠檬","segment_role":"head","climax_time":0.8,"usable_range":{"start":0.2,"end":2.4},"tags":{"actions":["切片"],"commerce_roles":["product_proof"]}}'
```

重新调用 `POST /api/clips/<clip_id>/analyze` 会创建新的分析版本并让详情展示它，但会保留素材原有审核状态；它不会自动重新抽帧或重新处理原视频。

### 4. AI 规划混剪并确认导出

先请求规划。服务端会从所选菜品的全部已审核素材中挑选最多 24 条代表性候选，并只将元数据、封面和最多 3 张关键帧临时发送给支持图片输入的 `remix_planning` 模型。图片会优先在内存中缩放为最长边 512 像素、最大 256 KiB 的 JPEG；转换失败时仅接受本已不超过该上限的图片。压缩图和 Base64 不会进入响应、数据库或日志。

```bash
curl -X POST http://127.0.0.1:8001/api/remix-plans \
  -H 'content-type: application/json' \
  -d '{"name":"柠檬切片展示","dish":"柠檬","requested_count":5,"target_duration_seconds":22,"script_facts":["汁水多","无籽"]}'
```

响应中的 `strategies` 是少量叙事结构，`variants` 是实际可导出的 EDL；规划请求只选择和排列候选中的 `clip_id`，每个入选素材都会完整保留，系统不支持截取或变速。`script_facts` 只放需要画面能力佐证的断言；价格、产地等用户确认商品事实应在后期 `product_facts` 记录来源。响应的 `uncovered_facts` 非空时，必须改写对应断言或补充素材，不得送入 TTS。响应和最终 EDL 仍展示服务端写入的 `start: 0`、`end: 原片时长`、`speed: 1` 以便追溯。若素材不足，`planned_count` 可以小于 `requested_count`，并通过 `shortfall_reason` 说明原因；客户端确认后，将 `variants[].clips` 作为既有 `POST /api/experiments` 的 variants 提交。规划响应和 Experiment 快照不含图片 Base64 或原始视频内容。

创建实验的 `variants[].clips` 只需提供审核通过的 `clip_id`。响应中的每个成片都有唯一 `video_id`；随后调用 `POST /api/renders/<render_id>/run` 执行完整原片拼接导出。

导出完成后，可通过以下接口查看或下载成片：

```bash
curl -I http://127.0.0.1:8001/api/renders/<render_id>/video
curl -I http://127.0.0.1:8001/api/renders/<render_id>/thumbnail
curl -OJ http://127.0.0.1:8001/api/renders/<render_id>/download
```

`video` 供浏览器内联播放并支持范围请求，`thumbnail` 返回由最终 MP4 在约 15% 时刻生成的 JPEG 封面。封面缺失时会为历史已完成成片按需补生成；若 FFmpeg 不可用或视频无法解码，封面接口返回 `404`，但已完成的 MP4 仍可下载或播放。三个接口只会返回已完成且位于本地导出目录内的成片，不接受任意文件路径。

### 4a. MiMo 音画后期交付

基础 Render 不能直接作为默认交付。后期操作者先从 `GET /api/renders/<render_id>` 读取 `video_id` 和完整 `edit_decision_list`，再依照 [MiMo 音画后期流程](mimo-postproduction.md)创建独立输出目录。文案先由素材能力和商品卖点池起草、选片、渲染，再按实测时长定稿。`mimo_postprocess.py --analyze-only` 可使用已启用的 MiMo 原生配置进行 2 fps、`media_resolution=default` 的可选成片复核；审核连接与 TTS 均只在运行时从加密的 `ModelConfig` 读取凭据。

交付目录的 manifest 至少要包含以下字段，供后续复查或平台数据回溯：

| 字段 | 内容 |
| --- | --- |
| `video_id` / `source_edl` | 基础成片标识及完整镜头 EDL。 |
| `vision_review` | 不含视频 Base64 的 MiMo 分段画面事实文件。 |
| `script` / `cues` | 经事实审核的口播全文与逐句时间轴。 |
| `fact_evidence_mapping` / `product_facts` | 事实断言到基础 EDL 切片的证据，以及用户确认商品卖点的来源。 |
| `tts` | 模型、音色、风格、实际语速和对应音频路径。 |
| `music` | 本地授权素材路径、授权来源/凭证引用、音量和 ducking 参数。 |
| `output` / `media_probe` | 后期 MP4 路径及 FFprobe、响度和解码验证结果。 |

Agent 后期成功后会自动回写原 `Render`，基础 MP4 和 EDL 不会被覆盖。Web 通过下列只读接口优先展示最终版；最终文件缺失时会回退基础 Render：

```bash
curl -I http://127.0.0.1:8001/api/renders/<render_id>/delivery-video
curl -I http://127.0.0.1:8001/api/renders/<render_id>/delivery-thumbnail
curl -OJ http://127.0.0.1:8001/api/renders/<render_id>/delivery-download
curl http://127.0.0.1:8001/api/renders/<render_id>/delivery-manifest
```

后期命令必须为每批提供已获授权的音乐和授权引用（也可预先配置环境变量 `RADIO_CATCH_POSTPROCESS_MUSIC_PATH`、`RADIO_CATCH_POSTPROCESS_MUSIC_LICENSE_REFERENCE`）：

```bash
backend/.venv/bin/python backend/scripts/mimo_postprocess.py \
  --batch <batch_name> --scripts-json /absolute/path/confirmed_scripts.json \
  --music-path /absolute/path/licensed.mp3 \
  --music-license-reference '素材库条目或用户确认记录'
```

`RADIO_CATCH_PROJECT_DIR/exports`（或显式的 `RADIO_CATCH_EXPORT_DIR`）同时控制基础 Render、后期与最终交付媒体接口的根目录。Base64 请求体、原视频内容和 API Key 绝不能写入 manifest、日志或响应。

### 5. 导入平台数据

上传 UTF-8 或 GB18030 编码的 CSV 至 `POST /api/metrics/import`。必填列为 `video_id`，可用英文或中文别名：`views/播放量`、`retention_2s/2秒留存率`、`retention_5s/5秒留存率`、`completion_rate/完播率`、`observation_hours/观察小时数` 等。

```bash
curl -F 'file=@metrics.csv' http://127.0.0.1:8001/api/metrics/import
curl http://127.0.0.1:8001/api/analysis/patterns
curl http://127.0.0.1:8001/api/analysis/recommendations
```

## 错误约定

| 状态码 | 含义 |
| --- | --- |
| `202` | 上传或重处理任务已进入队列。 |
| `409` | 重复配置或不允许的配置状态。 |
| `422` | 素材、审核、时间线或 CSV 数据未通过业务校验。 |
| `503` | FFmpeg/FFprobe 不可用。 |

对于 `PATCH /api/clips/<clip_id>/metadata`，非法 `segment_role`、结束时间不大于开始时间的 `usable_range`、或非对象的 `tags` 均返回 `422`。
