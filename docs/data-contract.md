# 数据口径

predictions 一行对应某 candidate、split 下一个 observation 的预测；observation 可跨重复分折出现，subject_id 标识同一学生来源。splits 给出 train_ids、test_ids、repeat、fold 和 release。candidate 的 created_after_release 标记已知选型历史；views 记录查看或改指标，不伪造未看过的保留集。truth 为二分类，score 为正类概率，latency_ms 为实测成本。

## 数据版本

`fixtures/data.json` 是可直接导入的一份完整材料，`product.json` 保存页面默认范围。导入必须提交完整对象，不把省略字段解释为删除补丁；以规范化 JSON 的 SHA-256 标识内容，同内容多次导入复用版本。原始记录只追加版本，保存的分析材料始终指向原来版本。

页面中的范围影响当前描述，不回写源数据。`null`、缺记录与实际零不能合并；数据格式问题应在导入时明确报错，而不是生成看似成功的空结果。涉及单位、时间或关系的解释以本文件为准，统计方法和业务决策没有预设结果。

## 目录

`model_comparison/domain.py` 提供现行领域校验和描述，`validation.py` 处理基础引用与数值校验，`service.py` 连接领域操作和材料存档，`storage.py` 负责内容寻址数据与历史记录，`server.py` 提供本地接口。`web/app.js` 使用接口构建交互页面，不自行推导独立口径。
