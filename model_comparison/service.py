from pathlib import Path
import json

from .comparison import (compare, freeze_assessment, normalize_spec, spec_differences,
                         DEFAULTS, METRICS)
from .domain import explore, validate
from .storage import Store, fingerprint


class Workbench:
    def __init__(self, root, state):
        self.root = Path(root)
        self.product = json.loads((self.root / "product.json").read_text(encoding="utf-8"))
        self.store = Store(Path(state) / "research.sqlite3")
        initial = json.loads((self.root / "fixtures" / "data.json").read_text(encoding="utf-8"))
        validate(initial)
        self.default_revision = self.store.put_dataset("演示材料", initial)

    def context(self):
        return {"product": self.product, "default_revision": self.default_revision,
                "datasets": self.store.datasets(), "notebooks": self.store.notebooks(),
                "freezes": self.store.freezes(), "comparisons": self.store.comparisons(),
                "metrics": {key: dict(value, key=key) for key, value in METRICS.items()},
                "comparison_defaults": dict(DEFAULTS)}

    def upload(self, body):
        data = body["data"]
        try:
            validate(data)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("字段结构不完整或类型不符") from error
        return {"revision": self.store.put_dataset(body.get("label", ""), data)}

    def inspect(self, body):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", self.product["selection"])
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        result = explore(data, selection)
        return {"revision": revision, "selection": selection, "data_sha256": fingerprint(data), "view": result}

    def save(self, body):
        result = self.inspect(body)
        return self.store.save(body.get("title", ""), result["revision"], result["selection"], result)

    def _candidates(self, data, spec):
        """未显式给出候选时，冻结以当前数据中的全部候选为准。"""
        spec = normalize_spec(spec)
        if "candidates" not in spec:
            spec["candidates"] = sorted(row["id"] for row in data["candidates"])
        known = {row["id"] for row in data["candidates"]}
        unknown = [candidate for candidate in spec["candidates"] if candidate not in known]
        if unknown:
            raise ValueError("未知候选：" + "、".join(unknown))
        return spec

    def freeze(self, body):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", self.product["selection"])
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        spec = self._candidates(data, body.get("spec"))
        assessment = freeze_assessment(data, selection, spec)
        prior = self._prior_comparisons(fingerprint(data), selection)
        reasons = list(assessment["reasons"])
        if prior:
            reasons.append("冻结前已存在同范围保存的比较记录：" + "、".join(map(str, prior)))
        status = "confirmatory" if not reasons else "exploratory"
        return self.store.freeze(body.get("title", ""), revision, selection, spec,
                                 fingerprint(data), assessment["scope"]["sha256"],
                                 status, reasons)

    def _prior_comparisons(self, data_sha256, selection):
        """冻结前是否已有同数据、同范围的比较记录；只以保存过的材料为证。"""
        identifiers = []
        for comparison in self.store.comparisons():
            if comparison["revision"] != data_sha256:
                continue
            saved = self.store.comparison(comparison["id"])
            if saved["selection"] == selection:
                identifiers.append(comparison["id"])
        return identifiers

    def _registration(self, freeze_id, revision, selection, spec):
        if freeze_id in (None, ""):
            return {"status": "unregistered", "freeze_id": None,
                    "reasons": ["未预注册：当前规格是在查看结果后设定的"]}
        frozen = self.store.freeze_record(freeze_id)
        differences = []
        if frozen["revision"] != revision:
            differences.append("数据版本")
        if frozen["selection"] != selection:
            differences.append("证据范围")
        differences.extend(spec_differences(frozen["spec"], spec))
        if differences:
            return {"status": "deviated", "freeze_id": frozen["id"],
                    "reasons": ["与冻结规格不一致：" + "、".join(sorted(set(differences)))]}
        return {"status": frozen["status"], "freeze_id": frozen["id"],
                "reasons": list(frozen["reasons"]),
                "data_sha256": frozen["data_sha256"],
                "scope_sha256": frozen["scope_sha256"]}

    def compare(self, body, store_result=False):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", self.product["selection"])
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        spec = self._candidates(data, body.get("spec"))
        registration = self._registration(body.get("freeze_id"), revision, selection, spec)
        result = compare(data, selection, spec, registration)
        if not store_result:
            return {"revision": revision, "selection": selection,
                    "data_sha256": fingerprint(data), "result": result}
        title = body.get("title", "")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("保存比较需要 1 至 160 字的名称")
        deviated = registration["status"] == "deviated"
        stored = self.store.compare_record(title, registration["freeze_id"], revision,
                                           selection, spec, result["assessment"],
                                           result, deviated)
        return stored

    def comparison(self, identifier):
        return self.store.comparison(identifier)

    def freeze_record(self, identifier):
        return self.store.freeze_record(identifier)
