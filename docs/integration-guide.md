# API 集成指南

基础地址：`http://127.0.0.1:8001`。交互式接口定义位于 `/docs`；如设置了 `RADIO_CATCH_API_PORT`，请替换为对应端口。

## 常用流程

### 1. 导入并等待媒体处理

```bash
curl -F 'file=@/absolute/path/clip.mp4' http://127.0.0.1:8001/api/media/imports
curl http://127.0.0.1:8001/api/media/jobs/<job_id>
```

当任务 `state` 为 `succeeded` 时，素材的缩略图与关键帧已就绪。相同文件会返回 `duplicate: true`，不会创建重复素材。

### 1a. 查看素材原视频

素材库详情使用以下接口播放完整原视频：

```bash
curl -I http://127.0.0.1:8001/api/media/clips/<clip_id>/video
```

接口仅接受已登记的 `clip_id`，并在服务端确认原文件仍在 `RADIO_CATCH_STORAGE_DIR` 内；不会暴露、返回或接受任意本地文件路径。素材不存在、文件已删除或记录指向存储目录外时返回 `404`。

### 2. 配置模型并理解素材

```bash
curl -X POST http://127.0.0.1:8001/api/model-configs \
  -H 'content-type: application/json' \
  -d '{"name":"vision","provider":"OpenAI-compatible","protocol":"openai","base_url":"https://provider.example/v1","api_key":"YOUR_KEY","model_name":"vision-model","supports_images":true,"supports_structured_json":true,"is_default":true}'

curl -X POST http://127.0.0.1:8001/api/clips/<clip_id>/analyze \
  -H 'content-type: application/json' -d '{"mode":"auto"}'
```

模型配置读取接口只返回 `api_key_masked`，不会返回原始密钥。

### 2a. 设置商家业务背景

新项目默认以“销售新鲜柠檬的商家”为素材标注背景。该背景会同时用于 Gemini、MiMo 和关键帧理解；它只帮助模型理解标签的后续用途，不能让模型编造产地、价格、甜度、农残等画面不可证实的卖点。

```bash
curl http://127.0.0.1:8001/api/project-settings
curl -X PATCH http://127.0.0.1:8001/api/project-settings \
  -H 'content-type: application/json' \
  -d '{"business_context":"我是柠檬商家；只标注视频或音频中可证实的信息，并为可见镜头标注带货角色。"}'
```

素材分析的 `tags.commerce_roles` 可能包含 `hook`（开场吸引）、`product_proof`（品质展示）、`usage`（使用场景）和 `cta`（明确行动引导）。人工审核可删除不具备画面或音频证据的角色。

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
  -d '{"name":"柠檬切片展示","dish":"柠檬","requested_count":5,"target_duration_seconds":22}'
```

响应中的 `strategies` 是少量叙事结构，`variants` 是实际可导出的 EDL；每个片段均包含候选中原样给出的 `clip_id`、数值 `start`、`end` 和 `speed`。若素材不足，`planned_count` 可以小于 `requested_count`，并通过 `shortfall_reason` 说明原因；客户端确认后，将 `variants[].clips` 作为既有 `POST /api/experiments` 的 variants 提交。规划响应和 Experiment 快照不含图片 Base64 或原始视频内容。

创建实验的 `variants[].clips` 必须提供审核通过的 `clip_id`、`start`、`end` 和可选 `speed`。响应中的每个成片都有唯一 `video_id`；随后调用 `POST /api/renders/<render_id>/run` 执行导出。

导出完成后，可通过以下接口查看或下载成片：

```bash
curl -I http://127.0.0.1:8001/api/renders/<render_id>/video
curl -I http://127.0.0.1:8001/api/renders/<render_id>/thumbnail
curl -OJ http://127.0.0.1:8001/api/renders/<render_id>/download
```

`video` 供浏览器内联播放并支持范围请求，`thumbnail` 返回由最终 MP4 在约 15% 时刻生成的 JPEG 封面。封面缺失时会为历史已完成成片按需补生成；若 FFmpeg 不可用或视频无法解码，封面接口返回 `404`，但已完成的 MP4 仍可下载或播放。三个接口只会返回已完成且位于本地导出目录内的成片，不接受任意文件路径。

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
