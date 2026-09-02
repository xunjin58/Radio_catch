#!/usr/bin/env python3
"""Create voiced, captioned runtime exports without modifying source renders."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[4]
EXPORTS = ROOT / "backend/data/exports"
OUTPUT = EXPORTS / "with_voice"
MUSIC = ROOT / "backend/assets/music/echoes_of_lumen-upbeat-music-happy-commercial-586975.mp3"
MIMO_ROOT = Path(os.environ.get("MIMO_ROOT", "/opt/mimo-voiceclone"))
FONT_FILE = Path(
    os.environ.get(
        "RADIO_CATCH_CAPTION_FONT",
        str(ROOT / "backend/assets/fonts/CheeseFoamOolongSong-Bold.ttf"),
    )
)

STYLE = (
    "使用茉莉女声。语气亲切自然，像认真分享柠檬泡水方法的水果摊主；"
    "语速中快、吐字清楚，带有自然的‘你看’、‘哎呀’、‘呀’等语气起伏；"
    "不要夸张叫卖，不要添加原文没有的内容。"
)

VIDEOS = [
    {
        "id": "RC-7709992cdad8",
        "music_start": 0.0,
        "sentences": [
            "你看这杯里，青柠黄柠都已经切好了，水一倒，颜色马上就出来了。",
            "平时在家想喝点带柠檬香的饮品，拿两片放进去，再加点冰，做法一点都不复杂。",
            "喜欢这种随手泡、随手喝的朋友呀，今天可以带几颗回家，慢慢换着搭配。",
        ],
    },
    {
        "id": "RC-254024913df0",
        "music_start": 5.0,
        "sentences": [
            "哎呀，这片切开以后，里面的纹路看得清清楚楚，摆在盘子里也很有层次。",
            "黄柠檬、青柠檬都可以按自己喜欢的方式来搭，放进冰水里，整杯看着就清清爽爽。",
            "平常爱泡柠檬水的朋友，家里备上一些，想喝的时候切两片就行。",
        ],
    },
    {
        "id": "RC-948bd7cef384",
        "music_start": 10.0,
        "sentences": [
            "先把青柠切开，再加一片黄柠檬，杯子里的颜色立刻丰富起来了。",
            "倒水、加冰、放切片，几步就能做一杯，看画面就知道不费事。",
            "下班回家或者周末在家休息，想喝这种简单的自制饮品呀，就带几颗柠檬回去试着泡。",
        ],
    },
    {
        "id": "RC-fdb6589020a6",
        "music_start": 15.0,
        "sentences": [
            "你看，柠檬切成薄片之后，黄的、青的放在一起，层层叠叠特别有画面感。",
            "想泡水的时候不用准备太多，选两三片放进杯里，倒上冰水，自己喜欢怎么搭都可以。",
            "家里常做饮品的朋友呀，今天选几颗回去，随手就能做一杯。",
        ],
    },
    {
        "id": "RC-6c28ea41c3b8",
        "music_start": 20.0,
        "sentences": [
            "这一刀下去，柠檬片切得透透的，桌上黄柠檬和青柠都摆得满满当当。",
            "想做饮品时，先放切片，再倒水加冰，杯子一下就有了清爽的样子。",
            "平时喜欢自己泡一杯的朋友，可以按口味多放一片、少放一片，带几颗回家慢慢搭。",
        ],
    },
]


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def probe_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(completed.stdout.strip())


def ass_timestamp(seconds: float) -> str:
    centiseconds = round(max(0.0, seconds) * 100)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds_part, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds_part:02d}.{fraction:02d}"


def escape_ass(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def wrap_caption(text: str, width: int = 18) -> str:
    chunks = [text[index:index + width] for index in range(0, len(text), width)]
    return r"\N".join(chunks)


def visible_characters(text: str) -> int:
    return len(re.sub(r"[，。、“”！、]", "", text))


def write_ass(path: Path, sentences: list[str], start: float, spoken_duration: float) -> list[dict[str, float | str]]:
    weights = [visible_characters(sentence) for sentence in sentences]
    total_weight = sum(weights)
    cursor = start
    cues: list[dict[str, float | str]] = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        end = start + spoken_duration if index == len(sentences) - 1 else cursor + spoken_duration * weight / total_weight
        cues.append({"start": round(cursor, 3), "end": round(end, 3), "text": sentence})
        cursor = end

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        "Style: Caption,Source Han Sans CN,54,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,3,8,0,2,96,96,250,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for cue in cues:
        lines.append(
            "Dialogue: 0,{start},{end},Caption,,0,0,0,,{text}".format(
                start=ass_timestamp(float(cue["start"])),
                end=ass_timestamp(float(cue["end"])),
                text=wrap_caption(escape_ass(str(cue["text"]))),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cues


def write_caption_png_files(video_id: str, cues: list[dict[str, float | str]]) -> list[Path]:
    caption_dir = OUTPUT / "caption_png"
    caption_dir.mkdir(exist_ok=True)
    font = ImageFont.truetype(str(FONT_FILE), 54)
    files: list[Path] = []
    for index, cue in enumerate(cues, start=1):
        path = caption_dir / f"{video_id}-{index}.png"
        wrapped = "\n".join(
            str(cue["text"])[offset:offset + 18]
            for offset in range(0, len(str(cue["text"])), 18)
        )
        image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((70, 1440, 1010, 1685), radius=24, fill=(0, 0, 0, 143))
        draw.multiline_text(
            (540, 1562),
            wrapped,
            font=font,
            fill=(255, 255, 255, 255),
            anchor="mm",
            align="center",
            spacing=12,
        )
        image.save(path)
        files.append(path)
    return files


def overlay_filter(cues: list[dict[str, float | str]], png_files: list[Path]) -> str:
    filters: list[str] = []
    current = "[0:v]"
    for index, cue in enumerate(cues):
        start = float(cue["start"])
        end = float(cue["end"])
        output = "[captioned]" if index == len(cues) - 1 else f"[captioned{index}]"
        filters.append(
            f"{current}[{index + 3}:v]overlay=x=0:y=0:"
            f"enable='between(t,{start:.3f},{end:.3f})'{output}"
        )
        current = output
    return ";".join(filters)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(MIMO_ROOT))
    import tts_builtin

    manifest: dict[str, object] = {
        "voice": "茉莉",
        "tts_model": "mimo-v2.5-tts",
        "speech_speed": 1.2,
        "background_music": str(MUSIC),
        "voice_target_lufs": -16,
        "background_target_lufs": -36,
        "background_ducking_db": 6,
        "exports": [],
    }

    for item in VIDEOS:
        video_id = str(item["id"])
        source = EXPORTS / f"{video_id}.mp4"
        raw_voice = OUTPUT / f"{video_id}.moli.raw.wav"
        fast_voice = OUTPUT / f"{video_id}.moli.1p2.wav"
        normalized_voice = OUTPUT / f"{video_id}.moli.1p2.normalized.wav"
        captions = OUTPUT / f"{video_id}.moli.ass"
        rendered = OUTPUT / f"{video_id}.moli-captioned.mp4"
        sentences = list(item["sentences"])
        script = "".join(sentences)
        video_duration = probe_duration(source)

        if not raw_voice.exists():
            if not tts_builtin.speak_with_builtin_voice(
                script,
                voice="茉莉",
                output_path=str(raw_voice),
                style_instruction=STYLE,
            ):
                raise RuntimeError(f"TTS synthesis failed: {video_id}")

        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_voice),
            "-af", "atempo=1.2", "-ar", "48000", "-c:a", "pcm_s16le", str(fast_voice),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(fast_voice),
            "-af", "loudnorm=I=-16:LRA=7:TP=-1.5", "-ar", "48000", "-c:a", "pcm_s16le",
            str(normalized_voice),
        ])
        voice_duration = probe_duration(normalized_voice)
        maximum_voice_duration = video_duration - 0.95
        if voice_duration > maximum_voice_duration:
            raise RuntimeError(
                f"{video_id} speech is {voice_duration:.2f}s; exceeds {maximum_voice_duration:.2f}s after 1.2x speed"
            )

        speech_start = 0.35
        cues = write_ass(captions, sentences, speech_start, voice_duration)
        caption_png_files = write_caption_png_files(video_id, cues)
        music_start = float(item["music_start"])
        fade_out_start = max(0.0, video_duration - 0.4)
        filter_complex = (
            f"[1:a]atrim=start={music_start}:duration={video_duration},"
            f"afade=t=in:st=0:d=0.4,afade=t=out:st={fade_out_start}:d=0.4,"
            "volume=-26.7dB,aresample=48000,asetpts=PTS-STARTPTS[bg];"
            f"[2:a]adelay=350:all=1,apad=whole_dur={video_duration},"
            f"atrim=duration={video_duration},asetpts=PTS-STARTPTS[voicebase];"
            "[voicebase]asplit=2[voice_sc][voice_mix];"
            "[bg][voice_sc]sidechaincompress=threshold=0.01:ratio=4:attack=100:release=500[ducked];"
            "[ducked][voice_mix]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[mix];"
            + overlay_filter(cues, caption_png_files)
        )
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-ss", "0", "-i", str(MUSIC), "-i", str(normalized_voice),
        ]
        for caption_png in caption_png_files:
            command.extend(["-loop", "1", "-framerate", "30", "-i", str(caption_png)])
        command.extend([
            "-filter_complex", filter_complex,
            "-map", "[captioned]", "-map", "[mix]", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest", str(rendered),
        ])
        run(command)

        manifest["exports"].append({
            "video_id": video_id,
            "source": str(source),
            "output": str(rendered),
            "raw_voice": str(raw_voice),
            "voice_1p2": str(fast_voice),
            "voice_1p2_normalized": str(normalized_voice),
            "captions": str(captions),
            "caption_png_files": [str(path) for path in caption_png_files],
            "video_duration_seconds": round(video_duration, 3),
            "voice_duration_seconds": round(voice_duration, 3),
            "speech_start_seconds": speech_start,
            "music_start_seconds": music_start,
            "script": script,
            "cues": cues,
        })
        print(f"completed {video_id}: voice={voice_duration:.2f}s video={video_duration:.2f}s")

    (OUTPUT / "moli_voiceover_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("completed all exports")


if __name__ == "__main__":
    main()
