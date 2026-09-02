#!/usr/bin/env python3
"""Render transparent, outlined Chinese caption layers using the macOS Pillow runtime."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    cue_path, output_dir, video_id = map(Path, sys.argv[1:4])
    cues = json.loads(cue_path.read_text(encoding="utf-8"))
    default_font = Path(__file__).resolve().parents[4] / "backend/assets/fonts/CheeseFoamOolongSong-Bold.ttf"
    font_path = Path(os.environ.get("RADIO_CATCH_CAPTION_FONT", str(default_font)))
    if not font_path.is_file():
        raise RuntimeError(f"缺少字幕字体: {font_path}")
    font = ImageFont.truetype(str(font_path), 54)
    output_dir.mkdir(exist_ok=True)
    for index, cue in enumerate(cues, start=1):
        text_value = str(cue["text"])
        text = "\n".join(text_value[offset:offset + 18] for offset in range(0, len(text_value), 18))
        image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.multiline_text(
            (540, 1650), text, font=font, fill=(255, 255, 255, 255), anchor="mm", align="center",
            spacing=12, stroke_width=3, stroke_fill=(16, 16, 16, 255),
        )
        image.save(output_dir / f"{video_id}-{index}.png")


if __name__ == "__main__":
    main()
