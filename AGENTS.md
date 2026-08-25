# 项目约定

- 前端位于 `src/`，使用 Vite + React + TypeScript；后端位于 `backend/app/`，使用 FastAPI + SQLAlchemy。
- 默认运行命令为 `npm run dev` 和 `npm run dev:api`；前端固定监听 `localhost:5174`，后端默认监听 8001，可用 `RADIO_CATCH_API_PORT` 调整，依赖安装在 `backend/.venv`。
- SQLite、上传素材、抽帧和导出文件均为本地运行时数据，位于 `backend/data/`、`backend/storage/`，不得提交。
- 模型 API Key 只能经 `ModelConfig` 的加密字段保存；不得写入前端、响应日志、README 或测试输出。
- 商家业务背景只能保存在 `ProjectSettings.business_context`，不得与模型密钥混存；背景仅说明标签用途，不能作为产地、价格、甜度、农残等不可见卖点的证据。
- 原生媒体适配器不得记录或持久化 Base64 请求体、原始视频内容或 API Key；媒体大小限制必须在发送前于本地校验。
- 新增或变更 API、环境变量、数据库实体时，同步更新 `docs/architecture.md`、`docs/integration-guide.md`、`docs/operator-runbook.md` 和本文件的必要规则。
- FFmpeg 命令必须使用参数数组调用，禁止拼接 shell 字符串；混剪必须保存完整 EDL，且不得绕过同菜品校验。
- V1 不实现抖音自动登录或自动发布；不要将该能力加入后台任务。
