# 本地接口

服务只用于本地教研复核，不连接真实人员目录或外部数据源。数据以 UTF-8 JSON 传输；未找到对象返回 404，无效业务输入返回 400，正文超出 8MB 返回 413。

- `GET /health` 返回就绪状态。
- `GET /api/context` 返回产品配置、默认版本、已导入版本、保存记录、冻结记录、比较记录、可用指标与比较默认参数。
- `GET /api/data?revision=版本` 返回该版本完整原始数据；省略版本使用演示材料。
- `POST /api/datasets` 接收 `label` 和完整 `data`，返回新建或复用的 `revision`。
- `POST /api/explore` 接收可选 `revision` 和 `selection`，返回数据指纹、实际选择和领域描述。
- `POST /api/notebooks` 使用相同选择参数和 `title`，服务端重新计算后保存，不能由客户端伪造结果。
- `GET /api/notebooks` 返回材料目录，`GET /api/notebooks/编号` 返回冻结描述、选择条件和所用数据。
- `POST /api/freezes` 接收 `revision`、`selection`、可选 `title` 和 `spec`，保存冻结的候选、指标、成本参数与证据范围，返回冻结记录及其证据等级（`confirmatory` 或 `exploratory`）与原因。
- `GET /api/freezes`、`GET /api/freezes/编号` 读取冻结记录。
- `POST /api/compare` 接收与冻结相同的参数外加 `freeze_id`，服务端重新计算并返回配对差异、区间、覆盖、误判与证据等级；与冻结规格不一致时 `registration.status` 为 `deviated`。
- `POST /api/comparisons` 使用相同参数和 `title` 保存比较材料；`GET /api/comparisons`、`GET /api/comparisons/编号` 读取，记录保存 `deviated` 标记与当时的证据等级。

领域描述包含 `summary`、`rows`、`series` 和 `notes`，学习进展另含作答历史。这里记录的是已有浏览交互，不限制新分析功能的内部结构。增添业务能力时需保持现有原始记录和保存材料可读取。比较接口的统计口径见 `docs/comparison.md`。
