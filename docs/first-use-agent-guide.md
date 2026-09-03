# 首次使用与对话流程

本指南用于第一次让对话式 Agent 操作 Radio Catch 时完成本地安装、MiMo 配置和真实素材验证。首次没有已保存的活动 MiMo 配置时，Agent 会先给出本说明并暂停，不会导入素材、生成文案或导出视频。

## 1. 安装与启动

以下以 Windows PowerShell 为主。先确认已安装 Node.js、Python 3 和 FFmpeg，再在项目根目录安装依赖：

```powershell
npm install

py -3 -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Copy-Item .env.example .env

ffmpeg -version
ffprobe -version
```

macOS/Linux 的对应命令为：

```bash
npm install

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..
cp .env.example .env

ffmpeg -version
ffprobe -version
```

将 `.env.example` 复制为 `.env`，并将 `RADIO_CATCH_SECRET_KEY` 替换为仅本机保存的长随机值。建议同时设置一个项目文件夹，例如 `RADIO_CATCH_PROJECT_DIR=D:\RadioCatch\lemon-september`；该文件夹会包含 `radio_catch.db` 标签库、`media` 素材/派生帧和 `exports` 成片/manifest，便于整体备份和迁移。模型 API Key 不写入 `.env`、终端命令、聊天、README 或其他文档；它只能通过 Web 的“模型与接口”页面保存到本地加密的 `ModelConfig` 中。

分别打开两个 PowerShell 窗口，并在项目根目录运行。`npm run dev:api` 会自动选用 Windows 的 `backend\.venv\Scripts\python.exe`；在 macOS/Linux 会选用 `backend/.venv/bin/python`：

```bash
npm run dev:api
```

```bash
npm run dev
```

打开 `http://localhost:5174`。启动后可检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/api/health
Invoke-RestMethod http://127.0.0.1:8001/api/media/health
```

两项检查必须成功；第二项还会确认 FFmpeg 与 FFprobe 是否在系统 PATH 中。

## 2. 配置 MiMo

在 Web 的“模型与接口”中选择“添加 MiMo 视频”，只在该表单中粘贴 Token Plan API Key。预设应使用协议 `mimo` 和模型 `mimo-v2.5`，并设为活动配置；如需作为默认素材理解模型，也设为默认。

MiMo 原生视频理解只接受 MP4、MOV、AVI 或 WMV。单条原文件应不超过 37 MB；系统会在发送前再次校验编码后的 Base64 data URL 不超过 50 MB。设置项目文件夹后，素材、数据库和导出均保留在其中，不应提交到版本控制。

保存后，告诉 Agent“配置完成”。Agent 会先测试连接。连接测试只验证模型列表可访问，不能替代真实视频验证。

## 3. 验证

准备一条小型、含音轨且格式受支持的视频。告诉 Agent其文件路径和菜品，例如：

> 验证素材在 `C:\Media\demo-lemon.mp4`，这是柠檬视频。请用 MiMo 分析并让我审核标签。

Agent 导入时只读取媒体头信息，再把原视频交给 MiMo 做原生理解；不会自动抽帧或生成缩略图。请审核返回的菜品、摘要、镜头角色与 `shot_capabilities`。审核通过后，这个项目才进入批量制作状态。

MiMo 分析失败时，先查看接口返回的本地解码诊断：本地也不能完整解码时，应重新导出或替换视频；本地能解码时，检查模型名称、Key、文件格式、37 MB 文件大小限制和供应商状态。验证失败时不要直接开始批量文案或渲染。

## 4. 之后怎么对话

### 第一句：给素材项目文件夹并让 MiMo 分析

拍摄完新素材后，给出一个项目文件夹的绝对路径，并说明这是柠檬还是葡萄素材。Agent 将该文件夹作为一个批次，导入其中受支持的视频、去重并用 MiMo 生成标签。卖点可以稍后补充，但必须说明来源，不能由商家背景或模型推断补充。

> 项目文件夹是 `C:\Media\2026-09-lemon`，这是柠檬素材。请用 MiMo 分析并打标签，先不要做视频。

Agent 会导入、去重并用 MiMo 生成结构化标签，同时返回本批标签摘要与可用性。文件夹和品类在当前对话中会作为本批次上下文保留，下一条指令无需重复；若你没有提出标签修订，下一条“生成混剪”指令会被视为对该批标签的确认。只有这样确认后的同菜品素材才能进入混剪规划。

### 第二句：指定数量和时长

标签结果返回后，直接给出数量与时长即可：

> 生成 5 个混剪视频，25 秒以内。

Agent 会根据当前已审核的柠檬素材先返回 5 条候选口播文案、每条预计时长和需由画面覆盖的事实，绝不直接渲染。你可以逐条或批量回复“通过”。只有明确通过的文案，才会进入选片、完整 EDL、基础混剪、MiMo TTS、授权配乐、无底板同步字幕与最终交付。若完整原片无法组合出 5 条不超过 25 秒且互不重复的变体，Agent 会明确说明还缺什么素材或需要调整的时长，不能裁剪或加速素材硬凑。

### 修改必须再次审核

任何已审核文案的增删改都会使该条重新变为“待审核”。例如：

> 第 2 条把价格改成 XX 元；改完先给我审核，不要重新渲染。

Agent 必须先给出修改稿并等待再次明确通过。未经再次审核，不得为该条选片、生成 TTS/字幕、混剪或交付。混剪中每条入选素材都必须完整播放，不能为迁就文案截取或加速；时长冲突先调整文案。

## 首次引导结束语

完成上面的 MiMo 配置后，请回复“配置完成”，并提供一条小型、含音轨的测试视频路径。我会先完成连接与真实素材分析验证，再开始处理你的批量素材。
