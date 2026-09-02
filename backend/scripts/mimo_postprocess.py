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
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.models import ModelConfig, Render, utcnow  # noqa: E402
from app.security import decrypt_api_key  # noqa: E402

EXPORTS = Path(os.getenv("RADIO_CATCH_EXPORT_DIR", ROOT / "backend/data/exports")).expanduser()
OUTPUT = EXPORTS / "with_mimo_final_v2"
DEFAULT_MUSIC_PATH = os.getenv("RADIO_CATCH_POSTPROCESS_MUSIC_PATH")
DEFAULT_MUSIC_LICENSE_REFERENCE = os.getenv("RADIO_CATCH_POSTPROCESS_MUSIC_LICENSE_REFERENCE")
CAPTION_FONT_NAME = "Cheese Foam Oolong Song"
CAPTION_FONT_SIZE_PX = 60
CAPTION_MAX_CHARS_PER_LINE = 14
VIDEOS = [
    {
        "id": "RC-948bd7cef384",
        "sentences": [
            "九块九两斤的广东香水柠檬，先往杯里放上柠檬片和冰块，再倒进透明饮品。",
            "你看这黄的青的都切好了，个头大，切开汁水也多，平时想泡着喝很方便。",
            "青柠从中间切开，切面也给你看，夏天不爱喝白水的，就能这样换着喝。",
            "黄柠檬冰杯在这儿，青柠那杯也端上来了。维C、多汁又不苦，想做柠檬饮的带两斤回去。",
        ],
    },
    {
        "id": "RC-7709992cdad8",
        "sentences": [
            "我现在买柠檬都是直接选这种，广东香水柠檬九块九两斤，个头真的不小。",
            "黄柠檬、青柠片放进杯里，倒入透明饮品，颜色慢慢就出来了。",
            "再挤点柠檬汁，看这个汁水就知道很足；平时切两片泡一泡，有维C，喝着也不苦。",
            "最后黄的青的都切开给你看，想在家做杯柠檬饮，带两斤回去慢慢泡就行。",
        ],
    },
    {
        "id": "RC-254024913df0",
        "sentences": [
            "喜欢喝柠檬水的，真的可以看看这个，广东香水柠檬九块九两斤，黄的青的搭着泡。",
            "你看这切片，个头大，纹理看得清楚，汁水足，维C也有。",
            "整个青柠、切开的黄柠檬都给你看，平时不爱喝白水，切两片随手放杯里就行。",
            "最后加冰、放进柠檬片，简简单单一杯。多汁不苦，夏天家里备两斤，想喝就自己泡。",
        ],
    },
    {
        "id": "RC-fdb6589020a6",
        "sentences": [
            "九块九两斤的广东香水柠檬，像这样一层层切好，光看着就很适合夏天泡饮。",
            "你看这整果个头也大，黄的青的家里换着泡，喝法一点都不单调。",
            "杯里已经放了切片，带冰一端，整杯看着特别清爽。",
            "黄柠檬的切面、整果都在这儿，多汁、有维C，泡着喝不苦。",
            "最后再倒进透明饮品，想做自己的柠檬饮，趁九块九两斤带回去慢慢喝。",
        ],
    },
    {
        "id": "RC-6c28ea41c3b8",
        "sentences": [
            "这个广东香水柠檬我真想让你们看看，九块九两斤，黄的青的搭着泡都很好看。",
            "先把青柠片放杯里，倒进透明饮品，马上就有一杯柠檬饮的样子。",
            "再加一片黄柠檬，镜头一转整果都在这儿，个头大，拿来切片很方便。",
            "后面直接挤进杯里，汁水很足，有维C，平时喝着也不苦。",
            "黄的青的切片一摆，想自己泡的朋友，九块九两斤带回家慢慢做。",
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


def _script_sentences(script: str) -> list[str]:
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])", script) if part.strip()]
    return sentences or [script.strip()]


SHOT_NARRATION_PATTERNS = (
    r"(?:画面|镜头|视频|片段)(?:里|中|上|下)?(?:有|是|都|正在|出现|展示|带到|拍到|包括|给你看)",
    r"(?:采摘|切开|倒进|放进|挤进|拿起|掰开|摆上|端上)\s*(?:画面|镜头|视频|片段)",
    r"(?:镜头|画面)一转",
    r"(?:我现在|先|再|然后|后面|最后)\s*(?:把|将)?.{0,8}(?:拿起|切开|倒进|放进|挤进|掰开|摆上|端上)",
)


def validate_script_first_voiceover(video_id: str, script: str) -> None:
    """Reject shot-by-shot narration before the text can be synthesized."""
    for pattern in SHOT_NARRATION_PATTERNS:
        match = re.search(pattern, script)
        if match:
            raise RuntimeError(
                f"{video_id} 的口播含镜头/动作解说“{match.group(0)}”；"
                "请改成商品事实、价值对比或消费场景，并将可见事实留在 evidence 中校验"
            )


def parse_script_entry(video_id: str, value: Any) -> dict[str, Any]:
    """Accept legacy text entries and the Script-First evidence-aware format."""
    if isinstance(value, str):
        script = value.strip()
        entry: dict[str, Any] = {
            "id": video_id, "script": script, "sentences": _script_sentences(script),
            "fact_assertions": [], "evidence": {}, "product_facts": {},
        }
    elif isinstance(value, dict):
        script = value.get("script")
        facts = value.get("fact_assertions", [])
        evidence = value.get("evidence", {})
        product_facts = value.get("product_facts", {})
        if not isinstance(script, str) or not script.strip():
            raise RuntimeError(f"{video_id} 缺少口播全文")
        if not isinstance(facts, list) or not all(isinstance(fact, str) and fact.strip() for fact in facts):
            raise RuntimeError(f"{video_id} 的 fact_assertions 必须是非空字符串数组")
        if not isinstance(evidence, dict):
            raise RuntimeError(f"{video_id} 的 evidence 必须是对象")
        if not isinstance(product_facts, dict):
            raise RuntimeError(f"{video_id} 的 product_facts 必须是对象")
        normalized_facts = list(dict.fromkeys(fact.strip() for fact in facts))
        entry = {
            "id": video_id, "script": script.strip(), "sentences": _script_sentences(script),
            "fact_assertions": normalized_facts, "evidence": evidence, "product_facts": product_facts,
        }
    else:
        raise RuntimeError(f"{video_id} 缺少口播全文")
    if not entry["script"]:
        raise RuntimeError(f"{video_id} 缺少口播全文")
    validate_script_first_voiceover(video_id, str(entry["script"]))
    return entry


def fact_evidence_mapping(
    video_id: str, fact_assertions: list[str], evidence: dict[str, Any], edl: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    """Validate that every visual assertion traces to a clip in the base EDL."""
    available_clip_ids = {str(item.get("clip_id")) for item in edl if isinstance(item, dict) and item.get("clip_id")}
    mapping: dict[str, dict[str, list[str]]] = {}
    for fact in fact_assertions:
        raw = evidence.get(fact)
        if isinstance(raw, list):
            clip_ids, capabilities = raw, []
        elif isinstance(raw, dict):
            clip_ids = raw.get("clip_ids", raw.get("supporting_clip_ids", []))
            capabilities = raw.get("shot_capabilities", [])
        else:
            raise RuntimeError(f"{video_id} 的事实断言“{fact}”缺少切片证据")
        if not isinstance(clip_ids, list) or not all(isinstance(item, str) and item for item in clip_ids):
            raise RuntimeError(f"{video_id} 的事实断言“{fact}”缺少有效 clip_ids")
        if not set(clip_ids).issubset(available_clip_ids):
            raise RuntimeError(f"{video_id} 的事实断言“{fact}”引用了不在基础 EDL 中的切片")
        if not isinstance(capabilities, list) or not all(isinstance(item, str) and item for item in capabilities):
            raise RuntimeError(f"{video_id} 的事实断言“{fact}”shot_capabilities 无效")
        mapping[fact] = {"clip_ids": list(dict.fromkeys(clip_ids)), "shot_capabilities": list(dict.fromkeys(capabilities))}
    return mapping


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
        "逐段查看这条竖屏商品视频。只报告口播可用的粗粒度事实："
        "可见商品和包装、可辨认场景、稳定可见的外观状态，以及明确动作；"
        "不要报告需要特写放大的微观细节，也不要从业务背景推断价格、产地、品种、口感、营养或功效。"
        "动作只用于校验事实，不能要求口播逐镜头解说。返回 JSON："
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
    for attempt in range(1, 4):
        try:
            with httpx.Client(timeout=max(config.timeout_seconds, 300)) as client:
                response = client.post(f"{config.base_url.rstrip('/')}/chat/completions", headers=headers, json=request)
            response.raise_for_status()
            data = response.json()["choices"][0]["message"]["audio"]["data"]
            output.write_bytes(base64.b64decode(data))
            return
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(attempt * 2)


def ass_timestamp(seconds: float) -> str:
    centiseconds = round(max(seconds, 0) * 100)
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    whole, fraction = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{fraction:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def caption_weight(text: str) -> int:
    return max(1, len(re.sub(r"[，。、“”！？；：、,.!?;:]", "", text)))


def split_caption_line(text: str, max_chars: int = CAPTION_MAX_CHARS_PER_LINE) -> list[str]:
    """Split a cue into sequential, single-line captions without dropping text."""
    value = text.replace("\n", "").strip()
    if not value:
        return []
    lines: list[str] = []
    while len(value) > max_chars:
        candidate = value[:max_chars]
        punctuation = max(candidate.rfind(char) for char in "，。！？；：、,.!?;:")
        cut = punctuation + 1 if punctuation >= 0 else max_chars
        lines.append(value[:cut])
        value = value[cut:]
    lines.append(value)
    return lines


def write_ass(path: Path, sentences: list[str], start: float, end: float) -> list[dict[str, Any]]:
    weights = [caption_weight(sentence) for sentence in sentences]
    cursor = start
    total = sum(weights)
    sentence_cues: list[dict[str, Any]] = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        cue_end = end if index == len(sentences) - 1 else cursor + (end - start) * weight / total
        sentence_cues.append({"start": cursor, "end": cue_end, "text": sentence})
        cursor = cue_end
    cues: list[dict[str, Any]] = []
    for sentence_cue in sentence_cues:
        lines = split_caption_line(str(sentence_cue["text"]))
        line_weights = [caption_weight(line) for line in lines]
        line_cursor = float(sentence_cue["start"])
        total_line_weight = sum(line_weights)
        for index, (line, weight) in enumerate(zip(lines, line_weights)):
            line_end = float(sentence_cue["end"]) if index == len(lines) - 1 else line_cursor + (
                (float(sentence_cue["end"]) - float(sentence_cue["start"])) * weight / total_line_weight
            )
            cues.append({"start": round(line_cursor, 3), "end": round(line_end, 3), "text": line})
            line_cursor = line_end
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Caption,{CAPTION_FONT_NAME},{CAPTION_FONT_SIZE_PX},&H00FFFFFF,&H000000FF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,2,84,84,210,1",
        "", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for cue in cues:
        lines.append(f"Dialogue: 0,{ass_timestamp(cue['start'])},{ass_timestamp(cue['end'])},Caption,,0,0,0,,{ass_escape(cue['text'])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cues


def write_caption_pngs(video_id: str, cues: list[dict[str, Any]]) -> list[Path]:
    """Render transparent, outlined caption layers for FFmpeg builds without libass."""
    caption_dir = OUTPUT / "caption_layers"
    caption_dir.mkdir(exist_ok=True)
    cue_path = caption_dir / f"{video_id}.cues.json"
    cue_path.write_text(json.dumps(cues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    renderer = Path(__file__).with_name("render_caption_layers.py")
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


def produce_video(
    video_id: str,
    sentences: list[str],
    source: Path,
    config: ModelConfig,
    *,
    music_path: Path,
    music_license_reference: str,
    duck_music: bool,
    music_volume_db: float,
    raw_voice_source: Path | None = None,
    min_effective_speech_speed: float = 1.08,
    max_effective_speech_speed: float = 1.34,
    max_tail_blank_seconds: float = 0.0,
    fact_assertions: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    product_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    duration = probe_duration(source)
    raw_voice = raw_voice_source if raw_voice_source and raw_voice_source.is_file() else OUTPUT / f"{video_id}.mimo.raw.wav"
    voice = OUTPUT / f"{video_id}.mimo.coverage.wav"
    captions = OUTPUT / f"{video_id}.mimo.ass"
    rendered = OUTPUT / f"{video_id}.mimo-final.mp4"
    script = "".join(sentences)
    target_voice_duration = duration - 0.5
    # 早期字数预算：按茉莉 TTS 实测约 4.6 字符/秒（含标点）预估，避免 TTS 后才因超速失败。
    # 规则见 backend/prompts/copywriting_xiaohongshu.md（20s 片有效字数约 100–108）。
    estimated_speed = len(script) / 4.6 / target_voice_duration
    if not min_effective_speech_speed <= estimated_speed <= max_effective_speech_speed:
        print(
            f"[warn] {video_id} 预估语速 {estimated_speed:.2f}×（全文 {len(script)} 字符），"
            f"超出 {min_effective_speech_speed:.2f}–{max_effective_speech_speed:.2f}×；"
            f"若 TTS 实测仍超限将失败，建议精简到约 "
            f"{int(max_effective_speech_speed * target_voice_duration * 4.6)} 字符（含标点）以内",
            file=sys.stderr,
        )
    if not raw_voice.is_file():
        synthesize_tts(config, script, raw_voice)
    raw_duration = probe_duration(raw_voice)
    speed = raw_duration / target_voice_duration
    applied_speed = speed
    tail_blank_seconds = 0.0
    if not min_effective_speech_speed <= speed <= max_effective_speech_speed:
        # A short, explicit tail hold is preferable to speeding up or trimming
        # a selected source clip.  It is opt-in per batch and bounded by the
        # delivery rule, so it cannot silently hide a materially short script.
        tail_blank_seconds = duration - (0.25 + raw_duration)
        # Container and FFprobe timestamps differ by a few milliseconds; this
        # tolerance does not turn the user-facing 2-second cap into a longer hold.
        if 0 < tail_blank_seconds <= max_tail_blank_seconds + 0.05:
            applied_speed = 1.0
        else:
            raise RuntimeError(
                f"{video_id} 的口播长度无法在 {min_effective_speech_speed:.2f}×–"
                f"{max_effective_speech_speed:.2f}×范围内覆盖视频（实际 {speed:.3f}×）"
            )
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_voice), "-af", f"atempo={applied_speed:.6f},loudnorm=I=-16:LRA=7:TP=-1.5", "-ar", "48000", "-c:a", "pcm_s16le", str(voice)])
    speech_duration = probe_duration(voice)
    speech_start = 0.25
    cues = write_ass(captions, sentences, speech_start, speech_start + speech_duration)
    caption_layers = write_caption_pngs(video_id, cues)
    fade_out_start = max(0, duration - 0.7)
    if duck_music:
        voice_mix = "[voicebase]asplit=2[voice_sc][voice_mix];"
        music_mix = "[bg][voice_sc]sidechaincompress=threshold=0.015:ratio=6:attack=80:release=450[music_mix];"
        ducking = "sidechaincompress ratio=6 while voice is present"
    else:
        voice_mix = "[voicebase]anull[voice_mix];"
        music_mix = "[bg]anull[music_mix];"
        ducking = "disabled by user request; fixed background level remains below voice target"
    filter_complex = (
        overlay_filter(cues, caption_layers) + ";"
        f"[2:a]atrim=duration={duration:.3f},afade=t=in:st=0:d=0.5,afade=t=out:st={fade_out_start:.3f}:d=0.7,volume={music_volume_db:.2f}dB,aresample=48000,asetpts=PTS-STARTPTS[bg];"
        f"[1:a]adelay=250:all=1,apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[voicebase];"
        + voice_mix
        + music_mix
        + "[music_mix][voice_mix]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[a]"
    )
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-i", str(voice),
        "-stream_loop", "-1", "-i", str(music_path),
    ]
    for layer in caption_layers:
        command.extend(["-loop", "1", "-framerate", "30", "-i", str(layer)])
    command.extend([
        "-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(rendered),
    ])
    run(command)
    edl = source_edl(video_id)
    fact_assertions = fact_assertions or []
    evidence_mapping = fact_evidence_mapping(video_id, fact_assertions, evidence or {}, edl)
    return {
        "video_id": video_id, "source": str(source), "source_edl": edl, "output": str(rendered), "raw_voice": str(raw_voice),
        "voice_audio": str(voice), "captions": str(captions), "caption_layers": [str(path) for path in caption_layers], "script": script, "cues": cues,
        "video_duration_seconds": round(duration, 3), "speech_duration_seconds": round(speech_duration, 3),
        "speech_start_seconds": speech_start, "speech_end_seconds": round(speech_start + speech_duration, 3),
        "effective_speech_speed": round(applied_speed, 5), "raw_to_coverage_speed": round(speed, 5),
        "tail_blank_seconds": round(max(0.0, tail_blank_seconds), 3), "voice_name": "茉莉",
        "tts_model": "mimo-v2.5-tts", "music": str(music_path), "music_license_reference": music_license_reference, "music_volume_db": music_volume_db,
        "ducking": ducking, "subtitle_style": "white text, black outline, no panel",
        "subtitle_font": CAPTION_FONT_NAME, "subtitle_font_size_px": CAPTION_FONT_SIZE_PX,
        "subtitle_max_chars_per_line": CAPTION_MAX_CHARS_PER_LINE,
        "fact_evidence_mapping": evidence_mapping, "product_facts": product_facts or {},
    }


def persist_delivery_records(deliveries: list[dict[str, Any]]) -> None:
    """Attach complete Agent deliveries to their immutable base Renders.

    Only final files under the configured export root are persisted.  The JSON
    records deliberately contain no model credentials or Base64 request data.
    """
    export_root = EXPORTS.resolve()
    with SessionLocal() as session:
        for delivery in deliveries:
            video_id = str(delivery["video_id"])
            output = Path(str(delivery["output"])).resolve()
            if export_root not in output.parents or not output.is_file():
                raise RuntimeError(f"{video_id} 的最终交付文件不在导出目录内")
            render = session.scalar(select(Render).where(Render.video_id == video_id))
            if render is None:
                raise RuntimeError(f"找不到基础 Render：{video_id}")
            if render.status != "completed":
                raise RuntimeError(f"基础 Render 尚未完成：{video_id}")
            render.delivery_output_path = str(output)
            render.delivery_manifest = delivery
            render.delivered_at = utcnow()
        session.commit()


def main() -> None:
    global OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--use-existing-reviews", action="store_true")
    parser.add_argument("--batch", default="with_mimo_final_v2")
    parser.add_argument("--video-id", help="基础 Render 的 video_id；内置批次以外的成片需同时传入 --sentence。")
    parser.add_argument("--sentence", action="append", help="一条经画面事实审核的口播句；可重复传入。")
    parser.add_argument("--scripts-json", help="已确认口播 JSON：兼容 {video_id: 口播全文}，也支持含 script、fact_assertions、evidence、product_facts 的对象；与 --video-id/--sentence 互斥。")
    parser.add_argument("--music-path", default=DEFAULT_MUSIC_PATH, help="本批已获授权背景音乐的本地路径；也可设置 RADIO_CATCH_POSTPROCESS_MUSIC_PATH。")
    parser.add_argument("--music-license-reference", default=DEFAULT_MUSIC_LICENSE_REFERENCE, help="音乐授权记录或用户确认引用；也可设置 RADIO_CATCH_POSTPROCESS_MUSIC_LICENSE_REFERENCE。")
    parser.add_argument("--no-ducking", action="store_true")
    parser.add_argument("--music-volume-db", type=float, default=-25.49)
    parser.add_argument("--min-effective-speech-speed", type=float, default=1.08)
    parser.add_argument("--max-effective-speech-speed", type=float, default=1.34)
    parser.add_argument("--max-tail-blank-seconds", type=float, default=0.0)
    parser.add_argument("--reuse-raw-voice-from-batch")
    parser.add_argument("--reuse-reviews-from-batch")
    args = parser.parse_args()
    if not 0.5 <= args.min_effective_speech_speed <= args.max_effective_speech_speed <= 2.0:
        raise RuntimeError("人声有效语速范围必须介于 0.5×–2.0×")
    if not 0.0 <= args.max_tail_blank_seconds <= 2.0:
        raise RuntimeError("片尾留白最多 2 秒")
    if args.video_id and not re.fullmatch(r"RC-[A-Za-z0-9]+", args.video_id):
        raise RuntimeError("video_id 格式无效")
    if args.sentence and not args.video_id:
        raise RuntimeError("--sentence 需要与 --video-id 一起使用")
    if args.scripts_json:
        if args.video_id or args.sentence:
            raise RuntimeError("--scripts-json 与 --video-id/--sentence 互斥")
        scripts_path = Path(args.scripts_json)
        if not scripts_path.is_file():
            raise RuntimeError("--scripts-json 文件不存在")
        scripts = json.loads(scripts_path.read_text(encoding="utf-8"))
        if not isinstance(scripts, dict) or not scripts:
            raise RuntimeError("--scripts-json 必须是 {video_id: 口播全文} JSON")
        selected_videos = []
        for video_id, text in scripts.items():
            if not re.fullmatch(r"RC-[A-Za-z0-9]+", str(video_id)):
                raise RuntimeError(f"video_id 格式无效：{video_id}")
            selected_videos.append(parse_script_entry(str(video_id), text))
    else:
        selected_videos = [item for item in VIDEOS if args.video_id is None or item["id"] == args.video_id]
        if args.video_id and not selected_videos:
            if not args.sentence:
                raise RuntimeError("非内置 video_id 必须提供至少一句经审核的 --sentence")
            selected_videos = [{"id": args.video_id, "sentences": args.sentence, "fact_assertions": [], "evidence": {}, "product_facts": {}}]
        elif args.sentence:
            selected_videos = [{**item, "sentences": args.sentence} for item in selected_videos]
    batch = Path(args.batch)
    if batch.name != args.batch or args.batch in {"", "."}:
        raise RuntimeError("批次名必须是 exports 下的单层目录名")
    def batch_path(value: str, label: str) -> Path:
        candidate = Path(value)
        if candidate.name != value or value in {"", "."}:
            raise RuntimeError(f"{label}必须是 exports 下的单层目录名")
        return EXPORTS / candidate

    if args.reuse_raw_voice_from_batch:
        source_batch = batch_path(args.reuse_raw_voice_from_batch, "复用人声批次名")
        if not source_batch.is_dir():
            raise RuntimeError("复用人声批次不存在")
    else:
        source_batch = None
    if args.reuse_reviews_from_batch:
        source_review_path = batch_path(args.reuse_reviews_from_batch, "复用画面审核批次名") / "mimo_vision_analysis.json"
        if not source_review_path.is_file():
            raise RuntimeError("复用画面审核不存在")
    else:
        source_review_path = None
    if args.use_existing_reviews and source_review_path:
        raise RuntimeError("只能选择当前批次审核或复用另一批次审核")
    OUTPUT = EXPORTS / batch
    OUTPUT.mkdir(parents=True, exist_ok=True)
    config = get_mimo_config()
    review_path = OUTPUT / "mimo_vision_analysis.json"
    if args.use_existing_reviews:
        if not review_path.is_file():
            raise RuntimeError("未找到已完成的 MiMo 看片结果")
        reviews = json.loads(review_path.read_text(encoding="utf-8"))
    elif source_review_path:
        reviews = json.loads(source_review_path.read_text(encoding="utf-8"))
        review_path = source_review_path
    elif args.analyze_only or not args.scripts_json:
        reviews = {}
        for item in selected_videos:
            video_id = str(item["id"])
            source = EXPORTS / f"{video_id}.mp4"
            if not source.is_file():
                raise RuntimeError(f"缺少源视频：{video_id}")
            reviews[video_id] = review_video(config, video_id, source)
            print(f"MiMo reviewed {video_id}")
        review_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        # Script-First batches can skip full-render MiMo review; it remains an
        # explicit --analyze-only or reuse option for final visual verification.
        reviews = {}
        review_path = None
    if args.analyze_only:
        return
    if not args.music_path:
        raise RuntimeError("请通过 --music-path 或 RADIO_CATCH_POSTPROCESS_MUSIC_PATH 指定已获授权背景音乐")
    if not args.music_license_reference or not args.music_license_reference.strip():
        raise RuntimeError("请通过 --music-license-reference 记录音乐授权来源")
    music_path = Path(args.music_path).expanduser().resolve()
    if not music_path.is_file():
        raise RuntimeError("已选背景音乐不存在")
    deliveries = [
        produce_video(
            str(item["id"]),
            list(item["sentences"]),
            EXPORTS / f"{item['id']}.mp4",
            config,
            music_path=music_path,
            music_license_reference=args.music_license_reference.strip(),
            duck_music=not args.no_ducking,
            music_volume_db=args.music_volume_db,
            raw_voice_source=(EXPORTS / source_batch / f"{item['id']}.mimo.raw.wav") if source_batch else None,
            min_effective_speech_speed=args.min_effective_speech_speed,
            max_effective_speech_speed=args.max_effective_speech_speed,
            max_tail_blank_seconds=args.max_tail_blank_seconds,
            fact_assertions=list(item.get("fact_assertions", [])),
            evidence=dict(item.get("evidence", {})),
            product_facts=dict(item.get("product_facts", {})),
        )
        for item in selected_videos
    ]
    validation = {entry["video_id"]: probe_streams(Path(entry["output"])) for entry in deliveries}
    for entry in deliveries:
        entry["media_probe"] = validation[entry["video_id"]]
    manifest_path = OUTPUT / "delivery_manifest.json"
    manifest_path.write_text(json.dumps({
        "voice": "茉莉", "tts_model": "mimo-v2.5-tts", "reviews": str(review_path) if review_path else None, "exports": deliveries,
        "media_probe": validation,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for entry in deliveries:
        entry["batch"] = batch.name
        entry["batch_manifest"] = str(manifest_path)
    persist_delivery_records(deliveries)
    print(f"completed {len(deliveries)} MiMo final deliveries")


if __name__ == "__main__":
    main()
