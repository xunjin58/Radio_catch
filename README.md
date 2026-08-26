# Cutline / Radio Catch

单人本地部署的短视频理解、实验混剪与数据归因系统。前端提供工作流界面，后端通过 FastAPI 提供可用的本地媒体与分析能力。

## 启动

首次安装依赖：

```bash
cd /Users/xunjin/Desktop/vibe/Radio_catch
npm install

cd /Users/xunjin/Desktop/vibe/Radio_catch/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

在项目根目录分别启动 API 和前端：

```bash
npm run dev:api
npm run dev
```

访问 `http://localhost:5174`；完整 API 文档位于 `http://127.0.0.1:8001/docs`。前端固定使用 5174、后端默认使用 8001，以避免与常见本地开发服务冲突；如有需要，可用 `RADIO_CATCH_API_PORT` 调整后端端口。

复制 `.env.example` 为 `.env` 后，可设置加密密钥、SQLite 路径、媒体目录和导出目录。模型密钥在界面/API 内配置，不应写入 `.env` 或提交到版本控制。

## 已实现

- 本地视频上传、SHA-256 去重、FFprobe 元数据、自适应关键帧和缩略图。
- SQLite 持久化、可观察的媒体任务、失败隔离。
- OpenAI 兼容模型配置（密钥加密存储和掩码响应）、关键帧理解、结构化标签和审核。
- 项目级商家业务背景：默认按柠檬卖家场景理解素材，并以可见证据标注 `hook`、`product_proof`、`usage`、`cta` 带货镜头角色；背景不作为营销事实。
- 同菜品校验、多模态 AI 混剪规划：最多 24 条代表性素材的摘要、封面和关键帧会在内存中压缩后发送，返回可确认的策略与完整 EDL。
- 完整 EDL 追溯、FFmpeg 1080×1920 H.264/AAC 硬切混剪。
- 抖音 CSV 数据按 `video_id` 导入、重复更新、72 小时/低样本保护下的候选规律与已验证规律。

视频和数据库默认保存在 `backend/storage/` 与 `backend/data/`，均为本地文件并已忽略版本控制。

## 文档

- [架构](docs/architecture.md)：组件、数据模型和处理状态。
- [集成指南](docs/integration-guide.md)：API 使用、数据格式和错误处理。
- [运维手册](docs/operator-runbook.md)：启动、配置、验证和排障。
- [交接说明](docs/handoff.md)：已完成范围与下一步工作。
