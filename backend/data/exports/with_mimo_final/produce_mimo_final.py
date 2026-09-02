#!/usr/bin/env python3
"""Create MiMo-reviewed, voiced, captioned deliverables without touching source renders.

Credentials are read from the project's encrypted ModelConfig only at request time.
Neither credentials nor Base64 video payloads are written to disk or stdout.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models import ModelConfig, Render  # noqa: E402
from app.security import decrypt_api_key  # noqa: E402

EXPORTS = ROOT / "backend/data/exports"
OUTPUT = EXPORTS / "with_mimo_final"
MUSIC = Path("backend/assets/music/echoes_of_lumen-upbeat-music-happy-commercial-586975.mp3")
VIDEOS = [
    {
        "id": "RC-948bd7cef384",
        "sentences": [
            "你看这杯广东香水柠檬水，刚把水倒进去，杯里一下就清清爽爽的。",
            "桌上黄的青的切片都备好了，想在家做点有味道的喝的，拿两片放进杯里就行。",
            "再加几块冰，慢慢泡出柠檬香，画面里这一步一步做下来真的不麻烦。",
            "下班回家或者周末休息，想换个喝法的朋友，带些广东香水柠檬回去自己慢慢搭。",
        ],
    },
    {
        "id": "RC-7709992cdad8",
        "sentences": [
            "你看，杯子里先放好了黄的青的柠檬片，水一倒，整杯看着就很有夏天的感觉。",
            "广东香水柠檬平时切两片泡着喝就可以，喜欢冰一点的就再加几块冰，做法特别简单。",
            "后面还能看到切开的果肉和一片片的纹路，想喝浓一点就多放一片，淡一点就少放一片。",
            "不想总喝没味道白水的朋友，家里备一些，想喝的时候随手就能泡上一杯。",
        ],
    },
    {
        "id": "RC-254024913df0",
        "sentences": [
            "哎呀，你看这个柠檬片切开以后，纹路清清楚楚的，摆在盘子里一层一层还挺好看。",
            "广东香水柠檬黄的青的可以按自己喜欢的方式来搭，先把切片放进杯里，再倒上冰水。",
            "镜头里最后这一杯颜色干干净净的，天气热的时候在家自己泡一杯，喝着也挺舒服。",
            "平常爱研究自制饮品的朋友，带一些回家，切两片、加点冰，就能慢慢换着喝。",
        ],
    },
    {
        "id": "RC-fdb6589020a6",
        "sentences": [
            "你看，黄的青的柠檬切成薄片摆在一起，层层叠叠的，光看着就觉得特别清爽。",
            "想做饮品不用准备一大堆东西，广东香水柠檬切两三片放进杯里，再把冰水慢慢倒进去。",
            "杯子端起来以后，里面的切片和颜色都看得见，喜欢淡一点浓一点，自己就能调。",
            "家里常泡水喝的朋友，选一些放着，想喝的时候随手切两片，简单又有点仪式感。",
        ],
    },
    {
        "id": "RC-6c28ea41c3b8",
        "sentences": [
            "这一刀切下去，你看柠檬片切得透透的，桌上黄的青的果子也都摆得满满当当。",
            "想在家做一杯简单的柠檬饮品，先放切片，再倒水加冰，杯子一下就有了清爽的样子。",
            "后面还可以看到把绿色柠檬挤进杯里，喜欢果香明显一点的，就按自己的口味慢慢搭。",
            "平时喜欢自己泡一杯的朋友，广东香水柠檬备一些在家里，想喝的时候拿出来切两片就行。",
        ],
    },
]

STYLE = (
    "使用茉莉女声，像亲切自然的水果摊主分享夏日自制柠檬饮品；"
    "语速中快，吐字清楚，有自然的你看、哎呀等语气起伏；"
    "不过度叫卖，不添加文本之外的商品事实。"
)


def run(args: list[str]) -> None:
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def probe_duration(path: Path) -> float:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(completed.stdout.strip())


def probe_streams(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def get_mimo_config() -> ModelConfig:
    with SessionLocal() as session:
        config = session.scalar(select(ModelConfig).where(
            ModelConfig.protocol == "mimo", ModelConfig.is_active.is_(True), ModelConfig.is_default.is_(True)
        ))
        if config is None:
            config = session.scalar(select(ModelConfig).where(
                ModelConfig.protocol == "mimo", ModelConfig.is_active.is_(True)
            ).order_by(ModelConfig.created_at.desc()))
        if config is None:
            raise RuntimeError("未找到启用的 MiMo 模型配置")
        session.expunge(config)
        return config


def source_edl(video_id: str) -> list[dict[str, Any]]:
    """Read the persisted base Render EDL for the delivery manifest."""
    with SessionLocal() as session:
        render = session.scalar(select(Render).where(Render.video_id == video_id))
        if render is None:
            raise RuntimeError(f"找不到基础 Render：{video_id}")
        return list(render.edit_decision_list or [])


def parse_json_content(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        candidate = re.search(r"\{[\s\S]*\}", content)
        if not candidate:
            raise RuntimeError("MiMo 未返回可解析的 JSON")
        return json.loads(candidate.group(0))


def review_video(config: ModelConfig, video_id: str, source: Path) -> dict[str, Any]:
    size = source.stat().st_size
    if size > config.max_native_media_bytes:
        raise RuntimeError(f"{video_id} 超过 MiMo 配置的原始媒体上限")
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    data_url = f"data:video/mp4;base64,{encoded}"
    if len(data_url.encode("ascii")) > 50 * 1024 * 1024:
        raise RuntimeError(f"{video_id} 的 MiMo Base64 请求超过 50 MiB")
    prompt = (
        "逐段查看这条约 21 秒的竖屏柠檬饮品视频。只报告画面可直接验证的事实，"
        "不要从业务背景推断价格、产地、无籽、营养或功效。返回 JSON："
        "{overall_summary:string,segments:[{start:number,end:number,visual_facts:[string],narrative_role:string}],"
        "safe_copy_facts:[string],avoid_claims:[string]}。时间段覆盖完整视频。"
    )
    request = {
        "model": config.model_name,
        "messages": [
            {"role": "system", "content": "你是严谨的短视频画面审核员。"},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "video_url", "video_url": {"url": data_url}, "fps": 2, "media_resolution": "default"},
            ]},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {decrypt_api_key(config.api_key_encrypted)}", "Content-Type": "application/json"}
    with httpx.Client(timeout=max(config.timeout_seconds, 180)) as client:
        response = client.post(f"{config.base_url.rstrip('/')}/chat/completions", headers=headers, json=request)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise RuntimeError("MiMo 看片结果不是文本 JSON")
    return parse_json_content(content)


def synthesize_tts(config: ModelConfig, text: str, output: Path) -> None:
    request = {
        "model": "mimo-v2.5-tts",
        "messages": [{"role": "user", "content": STYLE}, {"role": "assistant", "content": text}],
        "audio": {"format": "wav", "voice": "茉莉"},
    }
    headers = {"Authorization": f"Bearer {decrypt_api_key(config.api_key_encrypted)}", "Content-Type": "application/json"}
    with httpx.Client(timeout=max(config.timeout_seconds, 180)) as client:
        response = client.post(f"{config.base_url.rstrip('/')}/chat/completions", headers=headers, json=request)
    response.raise_for_status()
    data = response.json()["choices"][0]["message"]["audio"]["data"]
    output.write_bytes(base64.b64decode(data))


def ass_timestamp(seconds: float) -> str:
    centiseconds = round(max(seconds, 0) * 100)
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    whole, fraction = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{fraction:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def wrap_caption(text: str, width: int = 18) -> str:
    return r"\N".join(text[index:index + width] for index in range(0, len(text), width))


def write_ass(path: Path, sentences: list[str], start: float, end: float) -> list[dict[str, Any]]:
    weights = [max(1, len(re.sub(r"[，。、“”！、]", "", sentence))) for sentence in sentences]
    cursor = start
    total = sum(weights)
    cues: list[dict[str, Any]] = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        cue_end = end if index == len(sentences) - 1 else cursor + (end - start) * weight / total
        cues.append({"start": round(cursor, 3), "end": round(cue_end, 3), "text": sentence})
        cursor = cue_end
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        "Style: Caption,Source Han Sans CN,54,&H00FFFFFF,&H000000FF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,2,84,84,210,1",
        "", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for cue in cues:
        lines.append(f"Dialogue: 0,{ass_timestamp(cue['start'])},{ass_timestamp(cue['end'])},Caption,,0,0,0,,{wrap_caption(ass_escape(cue['text']))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cues


def write_caption_pngs(video_id: str, cues: list[dict[str, Any]]) -> list[Path]:
    """Render transparent, outlined caption layers for FFmpeg builds without libass."""
    caption_dir = OUTPUT / "caption_layers"
    caption_dir.mkdir(exist_ok=True)
    cue_path = caption_dir / f"{video_id}.cues.json"
    cue_path.write_text(json.dumps(cues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    renderer = OUTPUT / "render_caption_layers.py"
    run(["/usr/bin/python3", str(renderer), str(cue_path), str(caption_dir), video_id])
    files: list[Path] = []
    for index, _cue in enumerate(cues, start=1):
        path = caption_dir / f"{video_id}-{index}.png"
        if not path.is_file():
            raise RuntimeError(f"字幕图层未生成：{path.name}")
        files.append(path)
    return files


def overlay_filter(cues: list[dict[str, Any]], layers: list[Path]) -> str:
    current = "[0:v]"
    filters: list[str] = []
    for index, (cue, _layer) in enumerate(zip(cues, layers)):
        output = "[v]" if index == len(layers) - 1 else f"[v{index}]"
        filters.append(
            f"{current}[{index + 3}:v]overlay=0:0:enable='between(t,{float(cue['start']):.3f},{float(cue['end']):.3f})'{output}"
        )
        current = output
    return ";".join(filters)


def produce_video(video_id: str, sentences: list[str], source: Path, config: ModelConfig) -> dict[str, Any]:
    duration = probe_duration(source)
    raw_voice = OUTPUT / f"{video_id}.mimo.raw.wav"
    voice = OUTPUT / f"{video_id}.mimo.coverage.wav"
    captions = OUTPUT / f"{video_id}.mimo.ass"
    rendered = OUTPUT / f"{video_id}.mimo-final.mp4"
    script = "".join(sentences)
    if not raw_voice.is_file():
        synthesize_tts(config, script, raw_voice)
    raw_duration = probe_duration(raw_voice)
    target_voice_duration = duration - 0.5
    speed = raw_duration / target_voice_duration
    if not 1.08 <= speed <= 1.34:
        raise RuntimeError(f"{video_id} 的口播长度无法在 1.2×基准附近覆盖视频（实际 {speed:.3f}×）")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_voice), "-af", f"atempo={speed:.6f},loudnorm=I=-16:LRA=7:TP=-1.5", "-ar", "48000", "-c:a", "pcm_s16le", str(voice)])
    speech_duration = probe_duration(voice)
    speech_start = 0.25
    cues = write_ass(captions, sentences, speech_start, speech_start + speech_duration)
    caption_layers = write_caption_pngs(video_id, cues)
    fade_out_start = max(0, duration - 0.7)
    filter_complex = (
        overlay_filter(cues, caption_layers) + ";"
        f"[2:a]atrim=duration={duration:.3f},afade=t=in:st=0:d=0.5,afade=t=out:st={fade_out_start:.3f}:d=0.7,volume=-26.7dB,aresample=48000,asetpts=PTS-STARTPTS[bg];"
        f"[1:a]adelay=250:all=1,apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[voicebase];"
        "[voicebase]asplit=2[voice_sc][voice_mix];"
        "[bg][voice_sc]sidechaincompress=threshold=0.015:ratio=6:attack=80:release=450[ducked];"
        "[ducked][voice_mix]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[a]"
    )
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-i", str(voice),
        "-stream_loop", "-1", "-i", str(MUSIC),
    ]
    for layer in caption_layers:
        command.extend(["-loop", "1", "-framerate", "30", "-i", str(layer)])
    command.extend([
        "-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(rendered),
    ])
    run(command)
    return {
        "video_id": video_id, "source": str(source), "source_edl": source_edl(video_id), "output": str(rendered), "raw_voice": str(raw_voice),
        "voice_audio": str(voice), "captions": str(captions), "caption_layers": [str(path) for path in caption_layers], "script": script, "cues": cues,
        "video_duration_seconds": round(duration, 3), "speech_duration_seconds": round(speech_duration, 3),
        "speech_start_seconds": speech_start, "effective_speech_speed": round(speed, 5), "voice_name": "茉莉",
        "tts_model": "mimo-v2.5-tts", "music": str(MUSIC), "music_license_reference": "用户确认：现有商业轻快曲可用于本项目", "music_volume_db": -26.7,
        "ducking": "sidechaincompress ratio=6 while voice is present", "subtitle_style": "white text, black outline, no panel",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--use-existing-reviews", action="store_true")
    args = parser.parse_args()
    if not MUSIC.is_file():
        raise RuntimeError("已选背景音乐不存在")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    config = get_mimo_config()
    review_path = OUTPUT / "mimo_vision_analysis.json"
    if args.use_existing_reviews:
        if not review_path.is_file():
            raise RuntimeError("未找到已完成的 MiMo 看片结果")
        reviews = json.loads(review_path.read_text(encoding="utf-8"))
    else:
        reviews = {}
        for item in VIDEOS:
            video_id = str(item["id"])
            source = EXPORTS / f"{video_id}.mp4"
            if not source.is_file():
                raise RuntimeError(f"缺少源视频：{video_id}")
            reviews[video_id] = review_video(config, video_id, source)
            print(f"MiMo reviewed {video_id}")
        review_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.analyze_only:
        return
    deliveries = [produce_video(str(item["id"]), list(item["sentences"]), EXPORTS / f"{item['id']}.mp4", config) for item in VIDEOS]
    validation = {entry["video_id"]: probe_streams(Path(entry["output"])) for entry in deliveries}
    (OUTPUT / "delivery_manifest.json").write_text(json.dumps({
        "voice": "茉莉", "tts_model": "mimo-v2.5-tts", "reviews": str(review_path), "exports": deliveries,
        "media_probe": validation,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("completed five MiMo final deliveries")


if __name__ == "__main__":
    main()
