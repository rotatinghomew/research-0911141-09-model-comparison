# 教学模型比较记录簿

用于教研人员查看本地记录、选择业务范围、核对原始数据、做可复核的候选比较并保存复核材料。页面展示各候选的原始预测与成本；“候选比较”在相同评测对象上估计差异与不确定性，区分数据集之间与重复分折带来的相关性，并把推理成本与漏判代价放进业务取舍。所有示例都是本地生成的虚构记录，不含个人信息、真实研究数据或受限材料。

## 运行

需要 Python 3.11，运行不依赖第三方服务或第三方包。推荐使用锁定基础镜像的容器；也可以在项目根目录直接启动。

```bash
python3 -m model_comparison --host 127.0.0.1 --port 8080
```

浏览器打开 `http://127.0.0.1:8080`。状态默认保存在 `.state/research.sqlite3`，可用 `--state` 指定独立目录。页面提供范围选择、表格和图、原始记录查看、完整数据导入、保存和导出。保存的是数据版本与当时的描述，不会因后续导入变化。

候选比较需先“冻结当次候选、指标与证据范围”，再运行比较并保存材料。冻结记录与比较记录都保存当时的数据版本、规格和证据等级；冻结后修改指标或增加候选会标为事后探索。比较口径（分折簇、共同支持、区间、业务约束与证据等级）见 `docs/comparison.md`。

```bash
docker build -t model_comparison:local .
docker run --rm -p 8080:8080 -v model_comparison-state:/app/.state model_comparison:local
```

## 检查

```bash
python3 -m unittest discover -s tests -v
```

回归覆盖数据口径、HTTP 交互、非法导入回滚、不可变版本与重启后的历史读取，以及配对比较的分折簇、缺折排除、冻结偏离与证据等级。测试使用临时状态目录，不改变演示数据。详见 `docs/data-contract.md`、`docs/http.md`、`docs/comparison.md` 和 `docs/walkthrough.md`。如果扩展运行依赖，请同时在依赖清单和镜像中固定版本。

## 构建与浏览器回归

服务只使用 Python 标准库。下列固定版本工具用于构建和自动浏览器检查，不是运行服务的前置依赖。

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-build.txt -r requirements-test.txt
python3 -m playwright install --with-deps chromium
python3 -m build --wheel --no-isolation
python3 tests/browser_smoke.py
```

自动浏览器检查在随机本地端口启动真实应用，使用临时数据库验证范围切换、原始记录、保存复开、导出和幂等导入，并验证冻结→比较→修改指标后显示“已偏离冻结规格”的完整链路；结束后关闭浏览器和服务。它不要求预先启动服务器，也不依赖外部数据。`npm start` 是现有 Python 服务的启动别名，支持附加 `--host`、`--port` 参数；`npm run test:browser` 调用同一浏览器回归。npm 锁文件不引入 Node 运行包。

支持 CPython 3.11 系列，产品镜像仍由不可变 digest 固定至同一 Python 3.11.14 镜像；运行兼容性不依赖补丁版本专有行为。上述构建工具和测试工具均通过固定版本清单预装，正式启动和浏览器操作无须下载。
