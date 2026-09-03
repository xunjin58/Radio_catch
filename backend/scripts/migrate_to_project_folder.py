#!/usr/bin/env python3
"""Copy a legacy Radio Catch local state into one portable project folder.

The command is deliberately copy-first.  It updates paths only in the copied
SQLite database, so the legacy workspace stays available until the new project
folder has been checked on the destination computer.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LEGACY_DATABASE = ROOT / "backend" / "data" / "radio_catch.db"
LEGACY_MEDIA = ROOT / "backend" / "storage"
LEGACY_EXPORTS = ROOT / "backend" / "data" / "exports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将现有 Radio Catch 素材、标签和成片迁移到单一项目文件夹。")
    parser.add_argument("--project-dir", required=True, type=Path, help="目标项目文件夹，例如 D:\\RadioCatch\\grape-september")
    parser.add_argument("--move", action="store_true", help="验证成功后删除旧目录中的已复制运行时数据")
    return parser.parse_args()


def copy_tree(source: Path, destination: Path) -> None:
    if source.exists():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def remap_path(value: str, old_root: Path, new_root: Path) -> str:
    """Replace an old absolute runtime path only when it is inside ``old_root``."""
    try:
        relative = Path(value).resolve().relative_to(old_root.resolve())
    except (OSError, ValueError):
        return value
    return str(new_root / relative)


def remap_json(value: Any, old_media: Path, new_media: Path, old_exports: Path, new_exports: Path) -> Any:
    if isinstance(value, str):
        media_path = remap_path(value, old_media, new_media)
        return remap_path(media_path, old_exports, new_exports)
    if isinstance(value, list):
        return [remap_json(item, old_media, new_media, old_exports, new_exports) for item in value]
    if isinstance(value, dict):
        return {key: remap_json(item, old_media, new_media, old_exports, new_exports) for key, item in value.items()}
    return value


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate_database(database: Path, old_media: Path, new_media: Path, old_exports: Path, new_exports: Path) -> None:
    with sqlite3.connect(database) as connection:
        clip_columns = table_columns(connection, "clips")
        if "file_path" in clip_columns:
            for row_id, value in connection.execute("SELECT id, file_path FROM clips"):
                updated = remap_path(value, old_media, new_media)
                if updated != value:
                    connection.execute("UPDATE clips SET file_path = ? WHERE id = ?", (updated, row_id))

        analysis_columns = table_columns(connection, "clip_analyses")
        for column in ("tags", "evidence_frames"):
            if column not in analysis_columns:
                continue
            for row_id, value in connection.execute(f"SELECT id, {column} FROM clip_analyses"):
                if not value:
                    continue
                payload = json.loads(value)
                updated = remap_json(payload, old_media, new_media, old_exports, new_exports)
                if updated != payload:
                    connection.execute(
                        f"UPDATE clip_analyses SET {column} = ? WHERE id = ?",
                        (json.dumps(updated, ensure_ascii=False), row_id),
                    )

        render_columns = table_columns(connection, "renders")
        for column in ("output_path", "delivery_output_path"):
            if column not in render_columns:
                continue
            for row_id, value in connection.execute(f"SELECT id, {column} FROM renders WHERE {column} IS NOT NULL"):
                updated = remap_path(value, old_exports, new_exports)
                if updated != value:
                    connection.execute(f"UPDATE renders SET {column} = ? WHERE id = ?", (updated, row_id))
        for column in ("edit_decision_list", "delivery_manifest"):
            if column not in render_columns:
                continue
            for row_id, value in connection.execute(f"SELECT id, {column} FROM renders WHERE {column} IS NOT NULL"):
                payload = json.loads(value)
                updated = remap_json(payload, old_media, new_media, old_exports, new_exports)
                if updated != payload:
                    connection.execute(
                        f"UPDATE renders SET {column} = ? WHERE id = ?",
                        (json.dumps(updated, ensure_ascii=False), row_id),
                    )


def count_files(directory: Path) -> int:
    return sum(1 for path in directory.rglob("*") if path.is_file()) if directory.exists() else 0


def main() -> None:
    args = parse_args()
    target = args.project_dir.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    target_database = target / "radio_catch.db"
    target_media = target / "media"
    target_exports = target / "exports"

    if not LEGACY_DATABASE.is_file():
        raise SystemExit(f"找不到本地标签库：{LEGACY_DATABASE}")
    if target_database.exists():
        raise SystemExit(f"目标文件夹已有标签库，已停止以避免覆盖：{target_database}")

    shutil.copy2(LEGACY_DATABASE, target_database)
    copy_tree(LEGACY_MEDIA, target_media)
    copy_tree(LEGACY_EXPORTS, target_exports)
    migrate_database(target_database, LEGACY_MEDIA, target_media, LEGACY_EXPORTS, target_exports)

    with sqlite3.connect(target_database) as connection:
        clip_count = connection.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        tag_count = connection.execute("SELECT COUNT(*) FROM clip_analyses").fetchone()[0]
    print(f"已创建项目文件夹：{target}")
    print(f"素材标签库：{clip_count} 条素材，{tag_count} 份分析")
    print(f"媒体文件：{count_files(target_media)} 个；导出与交付文件：{count_files(target_exports)} 个")
    print("启动前设置 RADIO_CATCH_PROJECT_DIR 为该文件夹。旧数据尚未删除。")

    if args.move:
        if LEGACY_MEDIA.exists():
            shutil.rmtree(LEGACY_MEDIA)
        if LEGACY_EXPORTS.exists():
            shutil.rmtree(LEGACY_EXPORTS)
        if LEGACY_DATABASE.exists():
            LEGACY_DATABASE.unlink()
        print("已按 --move 删除旧运行时目录和标签库；请确认新项目可用后再清空废纸篓或备份。")


if __name__ == "__main__":
    main()
