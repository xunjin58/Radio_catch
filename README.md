# Cutline / Radio Catch

单人本地部署的短视频理解、实验混剪与数据归因系统。前端提供工作流界面，后端通过 FastAPI 提供可用的本地媒体与分析能力。

## 启动

首次安装依赖。Windows 使用 PowerShell：

```powershell
cd <project-root>
npm install

py -3 -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Copy-Item .env.example .env
```

macOS/Linux 使用：

```bash
cd <project-root>
npm install

cd <project-root>/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..
cp .env.example .env
```

在项目根目录分别启动 API 和前端（`npm run dev:api` 已兼容 Windows、macOS 和 Linux）：

```bash
npm run dev:api
npm run dev
```

访问 `http://localhost:5174`；完整 API 文档位于 `http://127.0.0.1:8001/docs`。前端固定使用 5174、后端默认使用 8001，以避免与常见本地开发服务冲突；如有需要，可用 `RADIO_CATCH_API_PORT` 调整后端端口。

复制 `.env.example` 为 `.env` 后，可设置加密密钥、SQLite 路径、媒体目录和导出目录。模型密钥在界面/API 内配置，不应写入 `.env` 或提交到版本控制。

推荐设置 `RADIO_CATCH_PROJECT_DIR`，例如 `D:\RadioCatch\lemon-september`。系统会将这个项目的标签数据库、原始素材/派生帧和所有交付物分别保存为 `radio_catch.db`、`media/` 和 `exports/`，可整体备份或迁移到另一台电脑。旧工作区可通过 `backend/scripts/migrate_to_project_folder.py --project-dir <目标目录>` 复制并收敛；确认新目录正常后才使用 `--move` 清理旧运行时数据。

## 已实现

- 本地视频上传、SHA-256 去重与 FFprobe 文件头元数据；MiMo/Gemini 原生模型优先直接理解原视频，不在导入时自动抽帧。
- SQLite 持久化、显式素材处理任务和失败隔离；原生模型失败后才以 FFmpeg 全量解码诊断本地源文件。
- OpenAI 兼容模型配置（密钥加密存储和掩码响应）、按需关键帧理解、MiMo/Gemini 原生视频理解、结构化标签和审核。
- 项目级商家业务背景：默认按柠檬卖家场景理解素材，并以可见证据标注 `hook`、`product_proof`、`usage`、`cta` 带货镜头角色；背景不作为营销事实。
- 同菜品校验、多模态 AI 混剪规划：最多 24 条代表性素材的摘要、标签和已存在的封面/关键帧会在内存中发送，返回可确认的策略与完整 EDL。
- 完整 EDL 追溯、FFmpeg 1080×1920 H.264/AAC 硬切混剪。
- 文案先行的后期链路：受控 `shot_capabilities` 词表、`script_facts` 选片校验、`uncovered_facts` 回退，以及可追溯事实→切片证据与商品事实的交付 manifest；成片级 MiMo 看片仅作可选复核。
- 抖音 CSV 数据按 `video_id` 导入、重复更新、72 小时/低样本保护下的候选规律与已验证规律。

未设置项目根目录时，视频和数据库默认保存在 `backend/storage/` 与 `backend/data/`，均为本地文件并已忽略版本控制。

## 文档

- [架构](docs/architecture.md)：组件、数据模型和处理状态。
- [集成指南](docs/integration-guide.md)：API 使用、数据格式和错误处理。
- [运维手册](docs/operator-runbook.md)：启动、配置、验证和排障。
- [首次使用与对话流程](docs/first-use-agent-guide.md)：首次安装、MiMo 配置、真实素材验证与对话式批量制作流程。
- [MiMo 音画后期流程](docs/mimo-postproduction.md)：原生看片、TTS、配乐、字幕与交付追溯。
- [交接说明](docs/handoff.md)：已完成范围与下一步工作。
