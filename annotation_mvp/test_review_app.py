import csv
import tempfile
import unittest
from pathlib import Path

from annotation_mvp.review_app import DeepSeekSceneClient, ReviewStore, STRATA, SceneSuggestionError


class ReviewStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.selection = self.root / "pool.csv"
        rows = [
            {"candidate_id": "C0002", "path": str(self.root / "2.jpg"), "theme": "portrait", "scene": "portrait", "candidate_stratum": "obvious_limpid"},
            {"candidate_id": "C0001", "path": str(self.root / "1.jpg"), "theme": "art", "scene": "still_life", "candidate_stratum": "obvious_dull"},
            {"candidate_id": "C0003", "path": str(self.root / "3.jpg"), "theme": "street", "scene": "city", "candidate_stratum": "obvious_dull"},
        ]
        with self.selection.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
        self.decisions = self.root / "decisions.csv"
        self.store = ReviewStore(self.root, self.selection, self.decisions, 2)

    def tearDown(self):
        self.temp.cleanup()

    def test_rows_are_sorted_by_original_theme_and_suggestion_is_expanded(self):
        self.assertEqual([row["theme_original"] for row in self.store.rows], ["art", "portrait", "street"])
        self.assertEqual(self.store.by_id["C0001"]["scene_suggested"], "art_graphic")
        self.assertEqual(self.store.by_id["C0003"]["scene_suggested"], "city_street")

    def test_keep_requires_reviewed_scene_and_persists_separate_fields(self):
        with self.assertRaisesRegex(ValueError, "请确认人工场景"):
            self.store.decide({"preview_id": "C0001", "decision": "keep", "scene_reviewed": ""})
        self.store.decide({"preview_id": "C0001", "decision": "keep", "scene_reviewed": "architecture", "note": "彩色玻璃"})
        loaded = ReviewStore(self.root, self.selection, self.decisions, 2)
        decision = loaded.decisions["C0001"]
        self.assertEqual(decision["theme_original"], "art")
        self.assertEqual(decision["scene_suggested"], "art_graphic")
        self.assertEqual(decision["scene_reviewed"], "architecture")
        self.assertEqual(decision["note"], "彩色玻璃")

    def test_drop_allows_empty_scene_and_unknown_scene_is_rejected(self):
        self.store.decide({"preview_id": "C0002", "decision": "drop", "scene_reviewed": ""})
        self.assertEqual(self.store.decisions["C0002"]["scene_reviewed"], "")
        with self.assertRaisesRegex(ValueError, "人工场景不合法"):
            self.store.decide({"preview_id": "C0003", "decision": "keep", "scene_reviewed": "unknown"})

    def test_deepseek_suggestion_persists_without_overwriting_manual_change(self):
        self.store.decide({"preview_id": "C0001", "decision": "keep", "scene_reviewed": "still_life", "note": "人工判断"})
        self.store.set_scene_suggestion("C0001", "architecture")
        loaded = ReviewStore(self.root, self.selection, self.decisions, 2)
        self.assertEqual(loaded.by_id["C0001"]["scene_suggested"], "architecture")
        self.assertEqual(loaded.by_id["C0001"]["scene_suggestion_source"], "deepseek")
        self.assertEqual(loaded.decisions["C0001"]["scene_reviewed"], "still_life")

    def test_deepseek_suggestion_replaces_previous_default_selection(self):
        self.store.decide({"preview_id": "C0001", "decision": "keep", "scene_reviewed": "art_graphic", "note": "默认值"})
        self.store.set_scene_suggestion("C0001", "architecture")
        loaded = ReviewStore(self.root, self.selection, self.decisions, 2)
        self.assertEqual(loaded.decisions["C0001"]["scene_suggested"], "architecture")
        self.assertEqual(loaded.decisions["C0001"]["scene_reviewed"], "architecture")

    def test_deepseek_output_is_normalized_to_existing_scene_values(self):
        self.assertEqual(DeepSeekSceneClient.normalize("architecture（建筑）"), "architecture")
        self.assertEqual(DeepSeekSceneClient.normalize("城市街景"), "city_street")
        with self.assertRaises(SceneSuggestionError):
            DeepSeekSceneClient.normalize("无法映射的长篇解释")

    def test_unassigned_batch_can_be_kept_without_proxy_stratum(self):
        self.assertEqual(STRATA["unassigned"], "未预分层（本轮未使用FADE）")
        self.store.by_id["C0001"]["candidate_stratum"] = "unassigned"
        self.store.decide({"preview_id": "C0001", "decision": "keep", "scene_reviewed": "art_graphic"})
        self.assertEqual(self.store.decisions["C0001"]["candidate_stratum"], "unassigned")


if __name__ == "__main__":
    unittest.main()
