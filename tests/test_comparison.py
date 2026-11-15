from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math
import unittest

from model_comparison.comparison import (compare, freeze_assessment, normalize_spec,
                                         spec_differences)
from model_comparison.service import Workbench


ROOT = Path(__file__).resolve().parents[1]


def fixture():
    return json.loads((ROOT / "fixtures" / "data.json").read_text())


def independent_fixture():
    """把 fixture 改成带真正独立后续发布的材料。"""
    data = deepcopy(fixture())
    for split in data["splits"]:
        if split["release"] == "reused":
            split["release"] = "followup"
    data["views"] = [{"event_id": "view_1", "release": "initial", "action": "inspect",
                      "metric": "accuracy"}]
    for candidate in data["candidates"]:
        if candidate["id"] == "tuned_tree":
            candidate["created_after_release"] = None
    return data


def pair(result, left, right):
    return next(item for item in result["pairs"]
                if item["left"] == left and item["right"] == right)


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def test_fixture_comparison_is_serializable_and_declares_exploration(self):
        result = compare(self.data, {"dataset": "all"}, {})
        json.dumps(result, allow_nan=False)
        self.assertEqual(result["assessment"]["status"], "exploratory")
        self.assertTrue(result["notes"])

    def test_repeated_folds_are_clusters_not_prediction_rows(self):
        result = compare(self.data, {"dataset": "all"}, {})
        # 3 数据集 × 3 折，重复分折共享测试集，因此只有 9 个簇，不是 18 折或 520 行。
        self.assertEqual(len(result["scope"]["clusters"]), 9)
        linear = next(row for row in result["summaries"] if row["candidate"] == "linear")
        self.assertEqual(linear["prediction_rows"], 180)
        self.assertAlmostEqual(linear["pseudo_replication_factor"], 2.0)
        self.assertEqual(result["joint_support"]["units"], 70)

    def test_identical_predictions_give_zero_difference(self):
        data = deepcopy(self.data)
        data["candidates"][1]["id"] = "tree"
        twin = deepcopy(data["candidates"][0])
        twin["id"] = "tree_twin"
        data["candidates"].append(twin)
        for prediction in data["predictions"]:
            if prediction["candidate"] == "linear":
                copy = deepcopy(prediction)
                copy["candidate"] = "tree_twin"
                data["predictions"].append(copy)
        result = compare(data, {"dataset": "all"}, {"candidates": ["linear", "tree_twin"]})
        chosen = pair(result, "linear", "tree_twin")
        self.assertAlmostEqual(chosen["mean_difference"], 0.0, places=12)
        self.assertEqual(chosen["difference_ci"], [0.0, 0.0])
        self.assertEqual(chosen["conclusion"], "不足以区分")

    def test_near_identical_candidates_are_not_separated(self):
        result = compare(self.data, {"dataset": "all"}, {"candidates": ["linear", "tree"]})
        chosen = pair(result, "linear", "tree")
        low, high = chosen["advantage_ci"]
        self.assertLess(low, 0)
        self.assertGreater(high, 0)
        self.assertFalse(chosen["distinguishable"])
        self.assertIn(chosen, [row for row in result["pairs"] if not row["distinguishable"]])

    def test_missing_candidate_folds_are_excluded_from_pairs(self):
        result = compare(self.data, {"dataset": "all"}, {})
        tuned = pair(result, "linear", "tuned_tree")
        # tuned_tree 缺 mixed_tasks 两个折；缺折单元不能悄悄参与配对。
        self.assertEqual(tuned["common_clusters"].__len__(), 7)
        self.assertEqual(len(tuned["excluded_clusters"]), 2)
        self.assertTrue(all("mixed_tasks" in name for name in tuned["excluded_clusters"]))
        self.assertEqual(tuned["common_units"], 70)
        coverage = result["coverage"]["tuned_tree"]
        self.assertLess(coverage["rate"], 0.9)
        self.assertEqual(coverage["clusters_absent"],
                         ["mixed_tasks/mixed_tasks_row_02+10",
                          "mixed_tasks/mixed_tasks_row_04+10"])

    def test_single_cluster_pair_is_marked_unreliable(self):
        result = compare(self.data, {"dataset": "mixed_tasks"}, {})
        tuned = pair(result, "linear", "tuned_tree")
        self.assertEqual(len(tuned["common_clusters"]), 1)
        self.assertFalse(tuned["interval_reliable"])
        self.assertEqual(tuned["conclusion"], "不足以区分")
        self.assertTrue(any("少于 2 个簇" in reason for reason in tuned["evidence"]["reasons"]))

    def test_incomplete_pair_is_never_labelled_confirmatory(self):
        data = independent_fixture()
        registration = {"status": "confirmatory", "freeze_id": 1, "reasons": [],
                        "frozen_events": []}
        result = compare(data, {"dataset": "all"},
                         {"candidates": ["linear", "tuned_tree"]}, registration)
        pair_result = pair(result, "linear", "tuned_tree")
        self.assertEqual(pair_result["evidence"]["status"], "exploratory")
        self.assertTrue(any("覆盖" in reason or "排除" in reason
                            for reason in pair_result["evidence"]["reasons"]))

    def test_misjudgments_preserve_split_and_subject(self):
        result = compare(self.data, {"dataset": "all"}, {})
        self.assertTrue(result["misjudgments"])
        for row in result["misjudgments"]:
            self.assertIn(row["type"], {"漏判", "误报"})
            self.assertIn(row["split_id"], {split["split_id"] for split in self.data["splits"]})
            self.assertIn(row["subject_id"], {observation["subject_id"]
                                              for observation in self.data["observations"]})
        self.assertFalse(result["misjudgments_truncated"])

    def test_fold_manifest_maps_repeats_to_clusters(self):
        result = compare(self.data, {"dataset": "all"}, {})
        self.assertEqual(len(result["folds"]), 9)
        for fold in result["folds"]:
            self.assertEqual(len(fold["splits"]), 2)
            self.assertEqual({split["repeat"] for split in fold["splits"]}, {0, 1})
            for candidate, coverage in fold["coverage"].items():
                if candidate == "tuned_tree" and fold["dataset"] == "mixed_tasks" \
                        and fold["cluster"].endswith(("row_02+10", "row_04+10")):
                    self.assertEqual(coverage["covered_units"], 0)
                    self.assertFalse(coverage["complete"])


class DecompositionTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def test_dataset_and_repeat_contributions_are_reported(self):
        result = compare(self.data, {"dataset": "all"}, {"candidates": ["linear", "tree"]})
        decomposition = pair(result, "linear", "tree")["decomposition"]
        self.assertEqual({row["dataset"] for row in decomposition["dataset_breakdown"]},
                         {"short_tasks", "case_tasks", "mixed_tasks"})
        self.assertAlmostEqual(decomposition["between_dataset_share"]
                               + decomposition["within_dataset_share"], 1.0)
        # 重复折的方差被单独估计，说明为何不能拿行数当独立样本。
        self.assertIsNotNone(decomposition["repeat_share"])

    def test_scope_filter_uses_that_dataset_only(self):
        result = compare(self.data, {"dataset": "case_tasks"}, {"candidates": ["linear", "tree"]})
        self.assertEqual(set(result["coverage"]), {"linear", "tree"})
        self.assertEqual(len(result["scope"]["clusters"]), 3)
        self.assertEqual(result["joint_support"]["units"], 30)

    def test_business_cost_prefers_lower_values(self):
        spec = {"metric": "business_cost", "fn_cost": 10.0, "fp_cost": 1.0,
                "latency_cost_per_ms": 0.5, "candidates": ["linear", "tree"]}
        result = compare(self.data, {"dataset": "all"}, spec)
        linear = next(row for row in result["summaries"] if row["candidate"] == "linear")
        tree = next(row for row in result["summaries"] if row["candidate"] == "tree")
        self.assertLess(linear["metric_mean"], tree["metric_mean"])
        chosen = pair(result, "linear", "tree")
        self.assertGreater(chosen["advantage"], 0)
        self.assertGreater(chosen["advantage_ci"][0], chosen["difference_ci"][0])


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def test_spec_validation_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            normalize_spec({"metric": "accuracy", "unknown": 1})
        with self.assertRaises(ValueError):
            normalize_spec({"metric": "f1"})

    def test_spec_differences_detect_metric_and_candidate_changes(self):
        frozen = normalize_spec({"metric": "accuracy", "candidates": ["linear", "tree"]})
        changed = normalize_spec({"metric": "business_cost", "candidates": ["linear", "tree"]})
        self.assertIn("metric", spec_differences(frozen, changed))
        added = normalize_spec({"metric": "accuracy",
                                "candidates": ["linear", "tree", "tuned_tree"]})
        self.assertIn("candidates", spec_differences(frozen, added))
        self.assertEqual(spec_differences(frozen, frozen), [])

    def test_fixture_evidence_is_not_independent(self):
        assessment = freeze_assessment(self.data, {"dataset": "all"},
                                       {"candidates": ["linear", "tree"]})
        self.assertEqual(assessment["status"], "exploratory")
        self.assertEqual(assessment["scope"]["independent_releases"], [])
        # initial 因 tuned_tree 在发布后加入而参与选型，reused 因改指标事件而受污染。
        self.assertEqual(assessment["scope"]["tainted_releases"], ["initial", "reused"])
        self.assertTrue(any("没有独立" in reason for reason in assessment["reasons"]))

    def test_independent_followup_supports_confirmatory_only_when_frozen(self):
        data = independent_fixture()
        assessment = freeze_assessment(data, {"dataset": "all"},
                                       {"candidates": ["linear", "tree"]})
        self.assertEqual(assessment["status"], "confirmatory")
        registration = {"status": "confirmatory", "freeze_id": 1, "reasons": [],
                        "frozen_events": []}
        result = compare(data, {"dataset": "all"},
                         {"candidates": ["linear", "tree"]}, registration)
        self.assertEqual(result["assessment"]["status"], "confirmatory")
        self.assertEqual(pair(result, "linear", "tree")["evidence"]["status"],
                         "confirmatory")

    def test_independent_data_without_freeze_stays_exploratory(self):
        data = independent_fixture()
        result = compare(data, {"dataset": "all"}, {"candidates": ["linear", "tree"]})
        self.assertEqual(result["assessment"]["status"], "exploratory")
        self.assertIn("未预注册", " ".join(result["assessment"]["reasons"]))

    def test_frozen_candidate_set_controls_taint_even_when_unselected(self):
        data = independent_fixture()
        # tuned_tree 未入选，但在后续发布之后加入，仍说明该发布已参与选型。
        data["candidates"][2]["created_after_release"] = "followup"
        assessment = freeze_assessment(data, {"dataset": "all"},
                                       {"candidates": ["linear", "tree"]})
        self.assertEqual(assessment["scope"]["independent_releases"], ["initial"])
        self.assertNotIn("followup", assessment["scope"]["independent_releases"])
        self.assertTrue(any("tuned_tree" in reason for reason in assessment["reasons"]))


class ServiceFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workbench = Workbench(ROOT, self.temporary.name)

    def test_freeze_then_exact_rerun_and_deviations(self):
        spec = {"candidates": ["linear", "tree"], "metric": "accuracy"}
        frozen = self.workbench.freeze({"title": "上线前冻结", "spec": spec})
        self.assertEqual(frozen["status"], "exploratory")
        exact = self.workbench.compare({"freeze_id": frozen["id"], "spec": spec})
        self.assertEqual(exact["result"]["registration"]["status"], "exploratory")
        changed = self.workbench.compare(
            {"freeze_id": frozen["id"],
             "spec": {"candidates": ["linear", "tree"], "metric": "business_cost"}})
        self.assertEqual(changed["result"]["registration"]["status"], "deviated")
        added = self.workbench.compare(
            {"freeze_id": frozen["id"],
             "spec": {"candidates": ["linear", "tree", "tuned_tree"]}})
        self.assertEqual(added["result"]["registration"]["status"], "deviated")
        self.assertIn("candidates", " ".join(added["result"]["registration"]["reasons"]))

    def test_freeze_after_saved_comparison_is_flagged(self):
        spec = {"candidates": ["linear", "tree"], "metric": "accuracy"}
        self.workbench.compare({"title": "先看结果", "spec": spec}, store_result=True)
        frozen = self.workbench.freeze({"title": "后来冻结", "spec": spec})
        self.assertEqual(frozen["status"], "exploratory")
        self.assertTrue(any("冻结前已存在" in reason for reason in frozen["reasons"]))

    def test_saved_comparison_records_deviation_and_survives_restart(self):
        spec = {"candidates": ["linear", "tree"], "metric": "accuracy"}
        frozen = self.workbench.freeze({"title": "冻结", "spec": spec})
        saved = self.workbench.compare(
            {"title": "事后改指标", "freeze_id": frozen["id"],
             "spec": {"candidates": ["linear", "tree"], "metric": "business_cost"}},
            store_result=True)
        self.assertTrue(saved["deviated"])
        restarted = Workbench(ROOT, self.temporary.name)
        self.assertEqual(restarted.store.comparison(saved["id"]), saved)
        self.assertEqual(saved["result"]["registration"]["status"], "deviated")

    def test_saved_comparison_keeps_original_dataset(self):
        saved = self.workbench.compare({"title": "留存"}, store_result=True)
        data = self.workbench.store.dataset(self.workbench.default_revision)
        data["context_note"] = "新资料"
        self.workbench.upload({"label": "新数据", "data": data})
        reopened = self.workbench.store.comparison(saved["id"])
        self.assertEqual(reopened, saved)

    def test_unknown_candidate_and_single_candidate_rejected(self):
        with self.assertRaises(ValueError):
            self.workbench.compare({"spec": {"candidates": ["linear", "missing"]}})
        with self.assertRaises(ValueError):
            self.workbench.compare({"spec": {"candidates": ["linear"]}})

    def test_bad_numeric_spec_rejected(self):
        for spec in [{"alpha": float("nan")}, {"threshold": 2},
                     {"fn_cost": -1}, {"draws": 5}]:
            with self.assertRaises(ValueError):
                self.workbench.compare({"spec": spec})


if __name__ == "__main__":
    unittest.main()
