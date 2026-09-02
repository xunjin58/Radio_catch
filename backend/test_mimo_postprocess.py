"""Tests for the Script-First manifest input parser."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import mimo_postprocess  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import Render  # noqa: E402
from mimo_postprocess import fact_evidence_mapping, parse_script_entry  # noqa: E402


class MimoPostprocessTests(unittest.TestCase):
    def test_legacy_script_entry_remains_supported(self) -> None:
        entry = parse_script_entry("RC-a1", "一杯柠檬水。夏天喝着舒服。")
        self.assertEqual(entry["fact_assertions"], [])
        self.assertEqual(entry["product_facts"], {})
        self.assertEqual(entry["sentences"], ["一杯柠檬水。", "夏天喝着舒服。"])

    def test_extended_entry_requires_evidence_from_the_base_edl(self) -> None:
        entry = parse_script_entry("RC-a1", {
            "script": "这颗汁水很足。",
            "fact_assertions": ["汁水多"],
            "evidence": {"汁水多": {"clip_ids": ["clip-1"], "shot_capabilities": ["squeezing"]}},
            "product_facts": {"九块九两斤": "用户确认卖点池"},
        })
        mapping = fact_evidence_mapping("RC-a1", entry["fact_assertions"], entry["evidence"], [{"clip_id": "clip-1"}])
        self.assertEqual(mapping["汁水多"]["clip_ids"], ["clip-1"])
        self.assertEqual(entry["product_facts"]["九块九两斤"], "用户确认卖点池")

    def test_extended_entry_rejects_evidence_outside_the_base_edl(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "不在基础 EDL"):
            fact_evidence_mapping("RC-a1", ["汁水多"], {"汁水多": ["other-clip"]}, [{"clip_id": "clip-1"}])

    def test_script_first_rejects_shot_narration(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "镜头/动作解说"):
            parse_script_entry("RC-grape", "园里成串的葡萄和采摘画面都在。")

    def test_script_first_allows_confirmed_harvest_facts(self) -> None:
        entry = parse_script_entry("RC-grape", "每天现采摘、现发货，想吃新鲜葡萄的时候很合适。")
        self.assertEqual(entry["script"], "每天现采摘、现发货，想吃新鲜葡萄的时候很合适。")

    def test_delivery_record_is_attached_to_the_existing_base_render(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            exports = Path(tempdir) / "exports"
            output = exports / "grape-batch" / "RC-grape.mimo-final.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"final")
            session = Session(create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
            ))
            Base.metadata.create_all(session.bind)
            session.add(Render(
                video_id="RC-grape", dish="葡萄", status="completed", output_path=str(exports / "RC-grape.mp4"),
                edit_decision_list=[],
            ))
            session.commit()
            delivery = {"video_id": "RC-grape", "output": str(output), "script": "葡萄想吃的时候很方便。"}
            with patch.object(mimo_postprocess, "EXPORTS", exports), patch.object(mimo_postprocess, "SessionLocal", return_value=session):
                mimo_postprocess.persist_delivery_records([delivery])
            saved = session.query(Render).filter_by(video_id="RC-grape").one()
            self.assertEqual(saved.delivery_output_path, str(output.resolve()))
            self.assertEqual(saved.delivery_manifest["script"], "葡萄想吃的时候很方便。")
            self.assertIsNotNone(saved.delivered_at)
            session.close()


if __name__ == "__main__":
    unittest.main()
