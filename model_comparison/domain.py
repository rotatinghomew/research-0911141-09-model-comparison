from collections import Counter, defaultdict
from statistics import mean, median
import math

from .validation import require_tables, unique, finite, references


def validate(data):
    require_tables(data, ["observations", "splits", "predictions", "candidates", "views"])
    unique(data["observations"], ["observation_id"])
    unique(data["splits"], ["split_id"])
    unique(data["predictions"], ["split_id", "candidate", "observation_id"])
    unique(data["candidates"], ["id"])
    references(data["predictions"], "observation_id", data["observations"], "observation_id")
    references(data["predictions"], "split_id", data["splits"], "split_id")
    references(data["predictions"], "candidate", data["candidates"], "id")
    identifiers = {row["observation_id"] for row in data["observations"]}
    splits = {row["split_id"]: row for row in data["splits"]}
    for split in splits.values():
        if set(split["train_ids"]) & set(split["test_ids"]):
            raise ValueError("训练和测试观测重叠")
        if not (set(split["train_ids"]) | set(split["test_ids"])) <= identifiers:
            raise ValueError("分折包含未知观测")
    for prediction in data["predictions"]:
        finite(prediction["score"], minimum=0, maximum=1)
        finite(prediction["latency_ms"], minimum=0)
        if prediction["observation_id"] not in splits[prediction["split_id"]]["test_ids"]:
            raise ValueError("预测不属于该折测试集")
    for observation in data["observations"]:
        if observation["truth"] not in {0, 1}:
            raise ValueError("分类真值应为零或一")


def explore(data, selection):
    dataset = selection.get("dataset", "all")
    observations = {row["observation_id"]: row for row in data["observations"] if dataset == "all" or row["dataset"] == dataset}
    if not observations:
        raise ValueError("未知数据集")
    grouped = defaultdict(list)
    for prediction in data["predictions"]:
        observation = observations.get(prediction["observation_id"])
        if observation:
            grouped[prediction["candidate"]].append((prediction, observation))
    rows = []
    for candidate, pairs in sorted(grouped.items()):
        rows.append({"candidate": candidate, "prediction_rows": len(pairs),
                     "unique_observations": len({observation["observation_id"] for _, observation in pairs}),
                     "splits": len({prediction["split_id"] for prediction, _ in pairs}),
                     "raw_accuracy": mean((prediction["score"] >= 0.5) == bool(observation["truth"]) for prediction, observation in pairs),
                     "mean_latency_ms": mean(prediction["latency_ms"] for prediction, _ in pairs)})
    return {"summary": {"候选": len(rows), "不同观测": len(observations), "预测行数": sum(row["prediction_rows"] for row in rows)},
            "rows": rows, "series": [{"label": row["candidate"], "y": row["raw_accuracy"]} for row in rows],
            "notes": ["描述指标未校正重复折相关性，各候选覆盖可能不同。", "这些均值不能作为独立确认或显著优胜的依据。"]}
