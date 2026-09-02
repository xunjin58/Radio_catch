# 葡萄 Agent 交付模板

本模板用于 Agent 为**已完成的葡萄基础 Render**制作最终交付版。Web 只展示结果；Agent 负责事实核验、文案定稿、后期命令与最终验收。

## 1. 任务前核对

1. 将项目业务背景改为葡萄的素材标注说明；不得沿用柠檬背景。
2. 读取基础 Render 的 `video_id` 和完整 EDL，确认每条素材均完整保留。
3. 汇总已审核素材的 `shot_capabilities`；只把需要画面证明的内容写入 `fact_assertions`。
4. 将无籽、价格、产地、口感、发货等用户确认信息写入 `product_facts`，并附明确来源；它们不能冒充画面证据。
5. 使用下方对象格式创建 `confirmed_grape_scripts.json`。占位文字必须替换为本批实际内容，不能直接执行。

```json
{
  "RC-替换为实际视频ID": {
    "script": "替换为经审核的连续口播：说清消费场景、价值或对比，不解说画面和动作。",
    "fact_assertions": ["替换为可由本条 EDL 画面能力证明的事实"],
    "evidence": {
      "替换为同一事实": {
        "clip_ids": ["替换为本条基础 EDL 中的 clip_id"],
        "shot_capabilities": ["grape_cluster"]
      }
    },
    "product_facts": {
      "替换为本批实际使用的商品事实": "用户确认来源、日期或授权记录"
    }
  }
}
```

## 2. 执行

音乐路径与授权记录每批显式指定，避免依赖任何个人电脑目录：

```bash
backend/.venv/bin/python backend/scripts/mimo_postprocess.py \
  --batch grape-YYYYMMDD \
  --scripts-json /absolute/path/confirmed_grape_scripts.json \
  --music-path /absolute/path/to/licensed-music.mp3 \
  --music-license-reference '素材库条目或用户确认记录'
```

脚本成功后会把最终 MP4、无秘密的 manifest 摘要和交付时间回写到对应 `Render`。Web 成片管理会优先播放、下载“最终交付版”，仍可切换回基础 Render 核对 EDL。

## 3. 验收

- 完整观看最终 MP4：人声连续、字幕单行可读、音乐不盖过人声。
- 确认 Web 中显示“Agent 最终交付版”，可播放/下载，并能切回基础 Render。
- 打开 `delivery-manifest.json`，确认 `source_edl`、`script`、`cues`、`fact_evidence_mapping`、`product_facts`、音乐授权记录与 `media_probe` 齐全。
