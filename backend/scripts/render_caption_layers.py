#!/usr/bin/env python3
"""Render transparent, outlined Chinese caption layers using the macOS Pillow runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(__file__).resolve().parents[1] / "assets/fonts/CheeseFoamOolongSong-Bold.ttf"
FONT_SIZE_PX = 60
SAFE_TEXT_WIDTH = 906


def main() -> None:
    cue_path, output_dir, video_id = map(Path, sys.argv[1:4])
    cues = json.loads(cue_path.read_text(encoding="utf-8"))
    if not FONT_PATH.is_file():
        raise RuntimeError(f"缺少字幕字体：{FONT_PATH}")
    font = ImageFont.truetype(str(FONT_PATH), FONT_SIZE_PX)
    output_dir.mkdir(exist_ok=True)
    for index, cue in enumerate(cues, start=1):
        text_value = str(cue["text"])
        if "\n" in text_value:
            raise RuntimeError("字幕 cue 只能包含单行文本")
        image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        bbox = draw.textbbox((0, 0), text_value, font=font, stroke_width=3)
        if bbox[2] - bbox[0] > SAFE_TEXT_WIDTH:
            raise RuntimeError(f"字幕单行超出安全宽度：{text_value}")
        draw.text(
            (540, 1650), text_value, font=font, fill=(255, 255, 255, 255), anchor="mm", align="center",
            stroke_width=3, stroke_fill=(16, 16, 16, 255),
        )
        image.save(output_dir / f"{video_id}-{index}.png")


if __name__ == "__main__":
    main()
