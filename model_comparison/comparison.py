"""可复核的候选比较。

约定：
- 比较单元是“分折簇 × 观测”。测试集完全相同的重复分折合并为一个簇；簇内先对重复折
  取平均，再让簇等权。预测行数不是样本量。
- 候选只有在一个簇内完整覆盖该簇全部测试观测时，才让这个簇进入比较。缺折候选不会
  被静默排除：其覆盖缺口逐簇列出，配对只在双方共同完整覆盖的簇上做。
- 区间同时重采样分折簇与 subject（双重聚类 bootstrap）。
- 冻结记录保存候选、指标、成本、阈值和证据范围。比较时只要规格、范围或数据版本与
  冻结记录不一致，就降级为事后探索；只有真正独立于查看与选型历史的发布，才可能
  支撑确认性结论。
"""

from collections import defaultdict
from statistics import mean

import math
import random

from .storage import fingerprint


ENGINE = "paired-crossed-bootstrap/1"
METRICS = {
    "accuracy": {"label": "准确率", "higher_is_better": True},
    "business_cost": {"label": "业务成本（漏判＋误报＋推理，越低越好）", "higher_is_better": False},
}
DEFAULTS = {
    "metric": "accuracy",
    "threshold": 0.5,
    "fn_cost": 10.0,
    "fp_cost": 1.0,
    "latency_cost_per_ms": 0.5,
    "alpha": 0.05,
    "min_coverage": 0.9,
    "min_effect": 0.0,
    "draws": 1000,
    "max_fn_rate": None,
    "max_mean_latency_ms": None,
}


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + "应为有限数字")
    if minimum is not None and value < minimum:
        raise ValueError(name + "低于允许范围")
    if maximum is not None and value > maximum:
        raise ValueError(name + "超过允许范围")
    return float(value)


def _strings(value, name):
    if not isinstance(value, list) or not value:
        raise ValueError(name + "应为非空数组")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(name + "只能包含非空字符串")
    if len(set(value)) != len(value):
        raise ValueError(name + "不能重复")
    return sorted(value)


def normalize_spec(spec):
    """补全并校验为可冻结、可比较的规格。"""
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("比较参数必须是对象")
    unknown = set(spec) - set(DEFAULTS) - {"candidates"}
    if unknown:
        raise ValueError("比较参数含未知字段：" + "、".join(sorted(unknown)))
    result = dict(DEFAULTS)
    result.update(spec)
    if result["metric"] not in METRICS:
        raise ValueError("未知指标：" + str(result["metric"]))
    result["threshold"] = _number(result["threshold"], "判定阈值", 0.0, 1.0)
    result["fn_cost"] = _number(result["fn_cost"], "漏判成本", 0.0)
    result["fp_cost"] = _number(result["fp_cost"], "误报成本", 0.0)
    result["latency_cost_per_ms"] = _number(result["latency_cost_per_ms"], "推理成本单价", 0.0)
    result["alpha"] = _number(result["alpha"], "显著性水平", 0.001, 0.5)
    result["min_coverage"] = _number(result["min_coverage"], "最低覆盖率", 0.0, 1.0)
    result["min_effect"] = _number(result["min_effect"], "最小实际差异", 0.0)
    result["draws"] = int(_number(result["draws"], "重采样次数", 200, 5000))
    for key, name in [("max_fn_rate", "漏判率上限"), ("max_mean_latency_ms", "平均时延上限")]:
        if result[key] is not None:
            result[key] = _number(result[key], name, 0.0)
    if "candidates" in result:
        result["candidates"] = _strings(result["candidates"], "候选")
    else:
        result.pop("candidates", None)
    return result


def spec_differences(frozen, current):
    """列出冻结规格与当前请求不一致的字段（含候选集合）。"""
    fields = ["candidates"] if sorted(frozen.get("candidates") or []) != sorted(current.get("candidates") or []) else []
    for key in DEFAULTS:
        if frozen.get(key) != current.get(key):
            fields.append(key)
    return fields


def _split_number(split, key):
    value = split.get(key, 0)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _release(split):
    return split.get("release") or "未标注"


def _cluster_of(split):
    return (split.get("dataset") or "未标注", tuple(sorted(split.get("test_ids") or [])))


def _cluster_name(key):
    dataset, test_ids = key
    if not test_ids:
        return dataset + "/空测试集"
    return "%s/%s+%d" % (dataset, test_ids[0], len(test_ids))


def _metric_value(row, truth, spec):
    positive = row["score"] >= spec["threshold"]
    false_negative = bool(truth) and not positive
    false_positive = (not bool(truth)) and positive
    if spec["metric"] == "accuracy":
        value = 0.0 if (false_negative or false_positive) else 1.0
    else:
        value = (spec["fn_cost"] * (1.0 if false_negative else 0.0)
                 + spec["fp_cost"] * (1.0 if false_positive else 0.0)
                 + spec["latency_cost_per_ms"] * row["latency_ms"])
    return {"value": value, "false_negative": false_negative,
            "false_positive": false_positive, "latency": row["latency_ms"]}


def _scope(data, dataset, candidates):
    """证据范围：分折簇、发布、查看事件、候选选型历史与独立发布。

    发布是否独立按整个数据版本的选型历史判断，而不只看本对候选：只要某个候选
    在该发布之后加入，这个发布就已被用于选型，不能算未受影响的后续资料。
    """
    observations = {row["observation_id"]: row for row in data["observations"]}
    splits = {row["split_id"]: row for row in data["splits"]}
    selected = set(candidates)
    in_scope = {key for key, row in observations.items()
                if dataset == "all" or row.get("dataset") == dataset}
    active = {row["split_id"] for row in data["predictions"]
              if row["candidate"] in selected and row["observation_id"] in in_scope
              and row["split_id"] in splits}
    clusters = {}
    for split_id in active:
        key = _cluster_of(splits[split_id])
        cluster = clusters.setdefault(key, {"name": _cluster_name(key), "splits": set(),
                                            "releases": set()})
        cluster["splits"].add(split_id)
        cluster["releases"].add(_release(splits[split_id]))
    releases = sorted({release for cluster in clusters.values() for release in cluster["releases"]})
    views = [{"event_id": row.get("event_id"), "release": row.get("release"),
              "action": row.get("action"), "metric": row.get("metric")}
             for row in data.get("views", [])
             if (row.get("release") or "未标注") in releases]
    history = {row["id"]: row.get("created_after_release")
               for row in data["candidates"]}
    # “改指标”会改变分析口径；“发布后才加入候选”说明该发布已参与选型。
    taint_reasons = []
    tainted = set()
    for view in views:
        if view["action"] != "inspect":
            tainted.add(view["release"])
            taint_reasons.append("发布 %s 上发生过%s（%s）"
                                 % (view["release"], view["action"], view["event_id"]))
    for candidate, release in history.items():
        if release:
            tainted.add(release)
            taint_reasons.append("候选 %s 在发布 %s 之后才加入" % (candidate, release))
    independent_releases = sorted(set(releases) - tainted)
    independent_clusters = sorted(cluster["name"] for cluster in clusters.values()
                                  if cluster["releases"] <= set(independent_releases))
    independent_splits = sorted(split_id for split_id in active
                                if _release(splits[split_id]) in independent_releases)
    return {
        "dataset": dataset,
        "clusters": sorted(cluster["name"] for cluster in clusters.values()),
        "releases": releases,
        "views": views,
        "candidate_history": {name: release for name, release in history.items()
                              if name in selected},
        "all_candidate_history": history,
        "tainted_releases": sorted(tainted),
        "taint_reasons": sorted(set(taint_reasons)),
        "independent_releases": independent_releases,
        "independent_clusters": independent_clusters,
        "independent_splits": independent_splits,
        "cluster_releases": {cluster["name"]: sorted(cluster["releases"])
                             for cluster in clusters.values()},
        "sha256": fingerprint({"dataset": dataset,
                               "clusters": sorted(cluster["name"] for cluster in clusters.values()),
                               "candidates": sorted(selected)}),
    }


def _prepare(data, dataset, candidates):
    """整理作用域内的分折簇、单元覆盖和逐候选预测。"""
    observations = {row["observation_id"]: row for row in data["observations"]}
    splits = {row["split_id"]: row for row in data["splits"]}
    in_scope = {key for key, row in observations.items()
                if dataset == "all" or row.get("dataset") == dataset}
    selected = set(candidates)
    raw = defaultdict(dict)  # (split_id, observation_id) -> candidate -> row
    for row in data["predictions"]:
        if row["candidate"] in selected and row["observation_id"] in in_scope:
            raw[(row["split_id"], row["observation_id"])][row["candidate"]] = row
    clusters = {}
    for (split_id, observation_id) in sorted(raw):
        if split_id not in splits:
            continue
        key = _cluster_of(splits[split_id])
        cluster = clusters.setdefault(key, {"name": _cluster_name(key), "dataset": key[0],
                                            "splits": set(), "units": defaultdict(set)})
        cluster["splits"].add(split_id)
        cluster["units"][observation_id].add(split_id)
    for cluster in clusters.values():
        cluster["units"] = {key: sorted(value) for key, value in sorted(cluster["units"].items())}
    return observations, splits, in_scope, raw, clusters


def _unit_covered(cluster, observation_id, candidate, raw):
    return all(candidate in raw.get((split_id, observation_id), {})
               for split_id in cluster["units"][observation_id])


def _cluster_complete(cluster, candidate, raw):
    return bool(cluster["units"]) and all(
        _unit_covered(cluster, observation_id, candidate, raw)
        for observation_id in cluster["units"])


def _unit_stats(cluster, candidate, observations, raw, spec):
    """簇内逐观测指标：先对重复折取平均，避免重复折按行膨胀。"""
    result = {}
    for observation_id, split_ids in cluster["units"].items():
        truth = observations[observation_id]["truth"]
        items = []
        for split_id in split_ids:
            row = raw.get((split_id, observation_id), {}).get(candidate)
            if row is not None:
                items.append(_metric_value(row, truth, spec))
        if items:
            result[observation_id] = {
                "value": mean(item["value"] for item in items),
                "false_negative": mean(1.0 if item["false_negative"] else 0.0 for item in items),
                "false_positive": mean(1.0 if item["false_positive"] else 0.0 for item in items),
                "latency": mean(item["latency"] for item in items),
            }
    return result


def _coverage(clusters, candidates, raw):
    coverage = {}
    for candidate in candidates:
        covered_units = total_units = 0
        full, partial, absent = [], [], []
        for key in sorted(clusters):
            cluster = clusters[key]
            cluster_total = len(cluster["units"])
            cluster_covered = sum(1 for observation_id in cluster["units"]
                                  if _unit_covered(cluster, observation_id, candidate, raw))
            covered_units += cluster_covered
            total_units += cluster_total
            if cluster_total and cluster_covered == cluster_total:
                full.append(cluster["name"])
            elif cluster_covered:
                partial.append(cluster["name"])
            else:
                absent.append(cluster["name"])
        coverage[candidate] = {
            "units_evaluated": covered_units, "units_expected": total_units,
            "rate": covered_units / total_units if total_units else 0.0,
            "clusters_full": sorted(full), "clusters_partial": sorted(partial),
            "clusters_absent": sorted(absent),
        }
    return coverage


def _crossed_bootstrap(points, cluster_keys, subjects, spec, seed):
    """双重聚类 bootstrap：同时重采样分折簇与 subject。"""
    diffs = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for key in cluster_keys:
        for observation_id, value in points[key].items():
            cell = diffs[key][subjects[observation_id]]
            cell[0] += value
            cell[1] += 1
    subject_names = sorted({subject for key in cluster_keys for subject in diffs[key]})
    if not cluster_keys or not subject_names:
        return 0.0, [0.0, 0.0]
    cluster_means = []
    for key in cluster_keys:
        total = sum(cell[0] for cell in diffs[key].values())
        count = sum(cell[1] for cell in diffs[key].values())
        if count:
            cluster_means.append(total / count)
    point = mean(cluster_means)
    rng = random.Random(seed)
    replicates = []
    for _ in range(spec["draws"]):
        sampled_clusters = [cluster_keys[rng.randrange(len(cluster_keys))]
                            for _ in range(len(cluster_keys))]
        sampled_subjects = [subject_names[rng.randrange(len(subject_names))]
                            for _ in range(len(subject_names))]
        per_cluster = []
        for key in sampled_clusters:
            total = 0.0
            count = 0
            for subject in sampled_subjects:
                cell = diffs[key].get(subject)
                if cell:
                    total += cell[0]
                    count += cell[1]
            if count:
                per_cluster.append(total / count)
        if per_cluster:
            replicates.append(mean(per_cluster))
    if not replicates:
        return point, [point, point]
    replicates.sort()
    low = replicates[int((spec["alpha"] / 2) * (len(replicates) - 1))]
    high = replicates[int((1 - spec["alpha"] / 2) * (len(replicates) - 1))]
    return point, [low, high]


def _variance(values):
    if len(values) < 2:
        return None
    center = mean(values)
    return sum((value - center) ** 2 for value in values) / (len(values) - 1)


def _decompose(points, splits, observations, raw, spec, left, right):
    """拆分簇间差异：数据集之间、簇之间与重复分折之内。"""
    cluster_means = {key: mean(points[key].values()) for key in points}
    grand = mean(cluster_means.values())
    by_dataset = defaultdict(list)
    for key, value in cluster_means.items():
        by_dataset[key[0]].append(value)
    dataset_rows = [{"dataset": dataset, "clusters": len(values),
                     "mean_difference": mean(values)}
                    for dataset, values in sorted(by_dataset.items())]
    total_ss = sum((value - grand) ** 2 for value in cluster_means.values())
    between_ss = sum(len(values) * (mean(values) - grand) ** 2
                     for values in by_dataset.values())
    between_share = between_ss / total_ss if total_ss else None
    repeat_variances = []
    for key in points:
        split_means = []
        for split_id in sorted(split for split in splits if _cluster_of(splits[split]) == key):
            values = []
            for observation_id in points[key]:
                left_row = raw.get((split_id, observation_id), {}).get(left)
                right_row = raw.get((split_id, observation_id), {}).get(right)
                if left_row is None or right_row is None:
                    continue
                truth = observations[observation_id]["truth"]
                values.append(_metric_value(left_row, truth, spec)["value"]
                              - _metric_value(right_row, truth, spec)["value"])
            if values:
                split_means.append(mean(values))
        if len(split_means) >= 2:
            repeat_variances.append(_variance(split_means))
    repeat_variance = mean(repeat_variances) if repeat_variances else None
    between_variance = _variance(list(cluster_means.values()))
    repeat_share = None
    if repeat_variance is not None and between_variance is not None:
        denominator = repeat_variance + between_variance
        repeat_share = repeat_variance / denominator if denominator else None
    return {
        "cluster_means": {_cluster_name(key): value for key, value in cluster_means.items()},
        "dataset_breakdown": dataset_rows,
        "between_dataset_share": between_share,
        "within_dataset_share": (1 - between_share) if between_share is not None else None,
        "repeat_sd": math.sqrt(repeat_variance) if repeat_variance is not None else None,
        "between_cluster_sd": math.sqrt(between_variance) if between_variance is not None else None,
        "repeat_share": repeat_share,
    }


def _pair_statistics(points, cluster_keys, subjects, spec, left, right, seed):
    point, interval = _crossed_bootstrap(points, cluster_keys, subjects, spec, seed)
    higher = METRICS[spec["metric"]]["higher_is_better"]
    advantage = point if higher else -point
    advantage_interval = ([interval[0], interval[1]] if higher
                          else [-interval[1], -interval[0]])
    distinguishable = (advantage_interval[0] > spec["min_effect"]
                       or advantage_interval[1] < -spec["min_effect"])
    if distinguishable:
        conclusion = (left + " 更好") if advantage > 0 else (right + " 更好")
    else:
        conclusion = "不足以区分"
    return {"mean_difference": point, "difference_ci": interval,
            "advantage": advantage, "advantage_ci": advantage_interval,
            "distinguishable": distinguishable, "conclusion": conclusion}


def _evidence(scope, registration, coverage, spec, pairs):
    """判定整体与逐对比较的证据等级，并说明原因。"""
    confirmatory_registration = registration.get("status") == "confirmatory"
    registration_reasons = ([] if confirmatory_registration
                            else registration.get("reasons") or ["未预注册"])
    pair_notes = {}
    for pair in pairs:
        local = list(registration_reasons)
        left, right = pair["left"], pair["right"]
        for candidate in (left, right):
            if coverage[candidate]["rate"] < spec["min_coverage"]:
                local.append("候选 %s 覆盖率 %.0f%% 低于冻结下限 %.0f%%"
                             % (candidate, coverage[candidate]["rate"] * 100,
                                spec["min_coverage"] * 100))
        if pair["excluded_clusters"]:
            local.append("只有 %d 个分折簇被双方完整覆盖，另有 %d 个簇被排除"
                         % (len(pair["common_clusters"]), len(pair["excluded_clusters"])))
        for candidate in (left, right):
            release = scope["candidate_history"].get(candidate)
            if release:
                local.append("候选 %s 在发布 %s 之后才加入" % (candidate, release))
        touched = sorted({row["release"] for row in scope["views"]
                          if row["action"] == "change_metric"
                          and set(pair["common_clusters"]) & {
                              name for name, releases in scope["cluster_releases"].items()
                              if row["release"] in releases}})
        if touched:
            local.append("共同支持的簇触及改动过指标的发布：" + "、".join(touched))
        independent = pair.get("independent_comparison")
        if independent is None:
            local.append("共同支持的簇中没有独立于查看与选型历史的簇")
        elif len(independent["clusters"]) < 2:
            local.append("独立簇少于 2 个，无法估计不确定性")
        if len(pair["common_clusters"]) < 2:
            local.append("共同支持少于 2 个簇，区间无法反映分折间的不确定性")
        else:
            pair["interval_reliable"] = True
        status = "confirmatory" if confirmatory_registration and not local else "exploratory"
        pair_notes[left + "|" + right] = {
            "status": status,
            "reasons": sorted(set(local)),
            "basis": "independent_clusters" if status == "confirmatory" else "joint_support",
            "independent_clusters": independent["clusters"] if independent else [],
        }
    statuses = {note["status"] for note in pair_notes.values()}
    overall = ("confirmatory" if statuses == {"confirmatory"} else
               ("mixed" if "confirmatory" in statuses else "exploratory"))
    overall_reasons = sorted({reason for note in pair_notes.values()
                              for reason in note["reasons"]})
    return {"status": overall,
            "confirmatory_pairs": [key for key, note in pair_notes.items()
                                   if note["status"] == "confirmatory"],
            "reasons": overall_reasons,
            "independent_clusters": scope["independent_clusters"],
            "pairs": pair_notes}


def freeze_assessment(data, selection, spec):
    """在冻结时检查证据来源，决定这次冻结能否支撑确认性比较。"""
    spec = normalize_spec(spec)
    dataset = selection.get("dataset", "all")
    known = [row["id"] for row in data["candidates"]]
    if "candidates" not in spec:
        spec["candidates"] = sorted(known)
    unknown = [candidate for candidate in spec["candidates"] if candidate not in known]
    if unknown:
        raise ValueError("未知候选：" + "、".join(unknown))
    if len(spec["candidates"]) < 2:
        raise ValueError("至少需要两个候选进行比较")
    scope = _scope(data, dataset, spec["candidates"])
    reasons = []
    if not scope["independent_clusters"]:
        reasons.append("证据范围没有独立于查看与选型历史的分折簇")
    reasons.extend(scope["taint_reasons"])
    return {"status": "confirmatory" if not reasons else "exploratory",
            "reasons": sorted(set(reasons)), "scope": scope}


def compare(data, selection, spec, registration=None):
    """在相同评测对象上比较候选，返回差异、区间、覆盖与复核材料。"""
    if not isinstance(selection, dict):
        raise ValueError("选择条件必须是对象")
    spec = normalize_spec(spec)
    dataset = selection.get("dataset", "all")
    known = [row["id"] for row in data["candidates"]]
    if dataset != "all" and not any(row.get("dataset") == dataset for row in data["observations"]):
        raise ValueError("未知数据集")
    if "candidates" not in spec:
        spec["candidates"] = sorted(known)
    unknown = [candidate for candidate in spec["candidates"] if candidate not in known]
    if unknown:
        raise ValueError("未知候选：" + "、".join(unknown))
    candidates = spec["candidates"]
    if len(candidates) < 2:
        raise ValueError("至少需要两个候选进行比较")
    registration = registration or {"status": "unregistered", "freeze_id": None,
                                    "reasons": ["未预注册：当前规格是在查看结果后设定的"]}

    observations, splits, in_scope, raw, clusters = _prepare(data, dataset, candidates)
    if not clusters:
        raise ValueError("所选范围没有可比较的预测")
    subjects = {key: row.get("subject_id", key) for key, row in observations.items()}
    coverage = _coverage(clusters, candidates, raw)
    scope = _scope(data, dataset, candidates)

    complete = {candidate: [key for key in sorted(clusters)
                            if _cluster_complete(clusters[key], candidate, raw)]
                for candidate in candidates}
    joint = [key for key in sorted(clusters)
             if all(key in complete[candidate] for candidate in candidates)]
    joint_units = sum(len(clusters[key]["units"]) for key in joint)
    unit_stats = {candidate: {key: _unit_stats(clusters[key], candidate, observations, raw, spec)
                              for key in complete[candidate]}
                  for candidate in candidates}

    summaries = []
    for candidate in candidates:
        joint_values = [unit_stats[candidate][key][observation_id]
                        for key in joint for observation_id in unit_stats[candidate][key]]
        own_values = [unit_stats[candidate][key][observation_id]
                      for key in complete[candidate] for observation_id in unit_stats[candidate][key]]
        prediction_rows = sum(1 for rows in raw.values() if candidate in rows)
        summaries.append({
            "candidate": candidate,
            "joint_units": len(joint_values),
            "joint_clusters": len(joint),
            "own_units": len(own_values),
            "own_clusters": len(complete[candidate]),
            "prediction_rows": prediction_rows,
            "pseudo_replication_factor": (prediction_rows / len(own_values)) if own_values else None,
            "metric_mean": mean(item["value"] for item in joint_values) if joint_values else None,
            "false_negative_rate": mean(item["false_negative"] for item in joint_values) if joint_values else None,
            "false_positive_rate": mean(item["false_positive"] for item in joint_values) if joint_values else None,
            "mean_latency_ms": mean(item["latency"] for item in joint_values) if joint_values else None,
            "own_metric_mean": mean(item["value"] for item in own_values) if own_values else None,
            "own_false_negative_rate": mean(item["false_negative"] for item in own_values) if own_values else None,
            "own_mean_latency_ms": mean(item["latency"] for item in own_values) if own_values else None,
            "coverage_rate": coverage[candidate]["rate"],
            "coverage_ok": coverage[candidate]["rate"] >= spec["min_coverage"],
            "selection_history": scope["candidate_history"].get(candidate),
        })

    pairs = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            points = {}
            for key in sorted(clusters):
                if key not in complete[left] or key not in complete[right]:
                    continue
                shared = sorted(set(unit_stats[left][key]) & set(unit_stats[right][key]))
                if not shared:
                    continue
                points[key] = {observation_id: unit_stats[left][key][observation_id]["value"]
                               - unit_stats[right][key][observation_id]["value"]
                               for observation_id in shared}
            if not points:
                continue
            seed = int(fingerprint([scope["sha256"], left, right])[:16], 16) % (2 ** 32)
            pair = {"left": left, "right": right,
                    "common_units": sum(len(value) for value in points.values()),
                    "common_clusters": sorted(clusters[key]["name"] for key in points),
                    "excluded_clusters": sorted(clusters[key]["name"]
                                                for key in clusters if key not in points)}
            pair.update(_pair_statistics(points, list(points), subjects, spec, left, right, seed))
            pair["decomposition"] = _decompose(points, splits, observations, raw, spec, left, right)
            independent_keys = [key for key in points
                                if clusters[key]["name"] in scope["independent_clusters"]]
            if independent_keys:
                block = {"clusters": sorted(clusters[key]["name"] for key in independent_keys),
                         "units": sum(len(points[key]) for key in independent_keys)}
                block.update(_pair_statistics(
                    {key: points[key] for key in independent_keys}, independent_keys, subjects,
                    spec, left, right,
                    int(fingerprint(["independent", scope["sha256"], left, right])[:16], 16) % (2 ** 32)))
                pair["independent_comparison"] = block
            else:
                pair["independent_comparison"] = None
            pair["coverage_ok"] = (coverage[left]["rate"] >= spec["min_coverage"]
                                   and coverage[right]["rate"] >= spec["min_coverage"])
            pair["interval_reliable"] = len(pair["common_clusters"]) >= 2
            pairs.append(pair)

    constraints = []
    for summary in summaries:
        # 业务取舍按候选自身完整覆盖的簇评估，避免共同支持太小而虚报“可接受”。
        failures = []
        if not summary["coverage_ok"]:
            failures.append("覆盖不足")
        if spec["max_fn_rate"] is not None and summary["own_false_negative_rate"] is not None:
            if summary["own_false_negative_rate"] > spec["max_fn_rate"]:
                failures.append("漏判率超限")
        if spec["max_mean_latency_ms"] is not None and summary["own_mean_latency_ms"] is not None:
            if summary["own_mean_latency_ms"] > spec["max_mean_latency_ms"]:
                failures.append("平均时延超限")
        constraints.append({"candidate": summary["candidate"],
                            "metric_mean": summary["own_metric_mean"],
                            "false_negative_rate": summary["own_false_negative_rate"],
                            "mean_latency_ms": summary["own_mean_latency_ms"],
                            "units": summary["own_units"],
                            "coverage_ok": summary["coverage_ok"],
                            "failures": failures, "acceptable": not failures})

    misjudgments = []
    for (split_id, observation_id), rows in raw.items():
        split = splits[split_id]
        observation = observations[observation_id]
        for candidate in candidates:
            row = rows.get(candidate)
            if row is None:
                continue
            computed = _metric_value(row, observation["truth"], spec)
            if not (computed["false_negative"] or computed["false_positive"]):
                continue
            misjudgments.append({
                "candidate": candidate, "split_id": split_id,
                "cluster": _cluster_name(_cluster_of(split)),
                "dataset": split.get("dataset"), "repeat": _split_number(split, "repeat"),
                "fold": _split_number(split, "fold"), "release": _release(split),
                "observation_id": observation_id,
                "subject_id": observation.get("subject_id"),
                "truth": observation["truth"], "score": row["score"],
                "latency_ms": row["latency_ms"],
                "type": "漏判" if computed["false_negative"] else "误报",
            })
    misjudgments.sort(key=lambda row: (row["candidate"], row["split_id"], row["observation_id"]))
    truncated = len(misjudgments) > 2000
    misjudgments = misjudgments[:2000]

    folds = []
    for key in sorted(clusters):
        cluster = clusters[key]
        folds.append({
            "cluster": cluster["name"], "dataset": cluster["dataset"],
            "observations": sorted(cluster["units"]),
            "splits": [{"split_id": split_id, "repeat": _split_number(splits[split_id], "repeat"),
                        "fold": _split_number(splits[split_id], "fold"),
                        "release": _release(splits[split_id]),
                        "test_size": len(splits[split_id].get("test_ids", []))}
                       for split_id in sorted(cluster["splits"])],
            "coverage": {candidate: {
                "covered_units": sum(1 for observation_id in cluster["units"]
                                     if _unit_covered(cluster, observation_id, candidate, raw)),
                "expected_units": len(cluster["units"]),
                "complete": _cluster_complete(cluster, candidate, raw),
            } for candidate in candidates},
        })

    evidence = _evidence(scope, registration, coverage, spec, pairs)
    for pair in pairs:
        pair["evidence"] = evidence["pairs"][pair["left"] + "|" + pair["right"]]

    notes = [
        "样本量按“分折簇 × 观测”计；预测行数含重复折，不能当作独立样本。",
        "同一测试集合的重复分折合并为一个簇，簇内先平均；区间同时重采样分折簇与 subject。",
        "候选只在双方都完整覆盖的分折簇上配对，其余簇列入 excluded_clusters。",
        "指标：" + METRICS[spec["metric"]]["label"] + "；漏判成本 %g，误报成本 %g，每毫秒推理成本 %g。"
        % (spec["fn_cost"], spec["fp_cost"], spec["latency_cost_per_ms"]),
        "配对汇总使用共同支持的 %d 个簇、%d 个单元；业务取舍与覆盖率按各候选自身完整覆盖的簇评估。"
        % (len(joint), joint_units),
    ]
    if any(not item["coverage_ok"] for item in constraints):
        notes.append("存在覆盖不足的候选；缺失簇已列出，不参与有利方向的排序。")
    if not any(item["acceptable"] for item in constraints):
        notes.append("没有候选同时满足覆盖、漏判率与时延约束；应扩充证据或调整业务约束，而不是按总榜选优。")
    if evidence["status"] != "confirmatory":
        notes.append("本次比较含探索性结论：" + "；".join(evidence["reasons"]) + "。")

    return {
        "engine": ENGINE,
        "metric": {"key": spec["metric"], **METRICS[spec["metric"]]},
        "spec": spec,
        "registration": registration,
        "assessment": evidence,
        "scope": scope,
        "coverage": coverage,
        "summaries": summaries,
        "pairs": pairs,
        "undecided": [{"left": pair["left"], "right": pair["right"],
                       "common_units": pair["common_units"], "conclusion": "不足以区分"}
                      for pair in pairs if not pair["distinguishable"]],
        "constraints": constraints,
        "acceptable": [item["candidate"] for item in constraints if item["acceptable"]],
        "misjudgments": misjudgments,
        "misjudgments_truncated": truncated,
        "folds": folds,
        "joint_support": {"clusters": sorted(clusters[key]["name"] for key in joint),
                          "units": joint_units},
        "notes": notes,
    }
