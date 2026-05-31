# Phase 4: 向量召回与上下文增强 (Vector Recall & Context Enhancement) 设计文档

> 日期: 2026-05-30
> 状态: 设计中
> 前置: Phase 3 评测体系已完成，31/31 smoke cases 通过，约 68.4% avg context reduction
> 范围: 向量召回、混合检索、Value Recall、召回可解释展示

---

## 1. 关键决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 向量数据库 | LanceDB (本地嵌入, 无服务端) | 零运维: 纯 Python 库, 数据存本地文件, 不需要 Docker; 支持过滤搜索; 与项目轻量架构一致 |
| Embedding 模型 | 必须显式配置 EMBEDDING_MODEL; 本地开发建议 D:/Models/BAAI/bge-m3; Docker 建议 /models/BAAI/bge-m3 | 不做隐式 fallback, 避免生产环境用错模型或触发意外下载 |
| Embedding 维度 | 从模型实际输出推断并写入配置/索引元数据 | bge-m3 通常 1024 维; LanceDB schema 不能硬编码 |
| 混合打分策略 | 加权融合: rule*0.6 + vec*0.3 + priority*0.1 | ROADMAP 要求不完全依赖向量; 规则得分已证明有效; 权重可配置 |
| Value Recall | 精确/归一化值匹配优先, 向量值召回补充 | "华东"、"天猫" 这类短值用规则更稳; 向量用于模糊值、别称和错字 |
| 回退策略 | 混合召回无命中仍走现有 fallback | 保持 Phase 2/3 安全网 |
| 索引管理 | CLI/Admin 手动重建优先; Admin CRUD 后增量更新; 不阻塞后端启动 | 避免首次模型下载、加载和全量 embedding 拖慢 dev server 或测试 |

### 1.1 为什么不选 Qdrant / Chroma

- Qdrant: 需要 Docker 或 Cloud; 增加环境复杂度; 过度工程化
- Chroma: API 不稳定, 破坏性变更频繁; 文档质量一般
- LanceDB: 纯库 (pip install); 数据存本地 .lance 目录; PyArrow 底层; 与 DuckDB + SQLite 轻量架构一致

### 1.2 Embedding 模型说明

- 本地开发模型路径: D:/Models/BAAI/bge-m3, 通常 1024 维
- Docker 模型路径: /models/BAAI/bge-m3, 通过 volume 挂载宿主机模型目录
- 配置必须包含 `embedding_model`; `embedding_dimension` 可为空
- `embedding_dimension` 由首次加载模型后推断, 不在 schema 和测试中硬编码
- `vector_enabled=False` 时不需要模型, 也不会加载模型
- `vector_enabled=True` 但未配置 `embedding_model` 时明确报错
- 不做自动 fallback, 不自动下载公共模型

---

## 2. 架构设计

### 2.1 整体架构

检索链路变为:

1. retrieve_metadata_assets() -- Phase 2 规则检索
2. retrieve_vector_assets() -- Phase 4 新增向量检索, 仅在 vector_enabled=True 且索引 ready 时运行
3. hybrid_merge() -- Phase 4 新增混合融合
4. build_focused_context_from_retrieval() -- 输入结构保持兼容
5. generate_sql -> guard -> execute -- 不变

核心原则:

- 向量检索是规则检索的补充, 不是替代
- vector_enabled=False 时必须回到 Phase 3 行为
- vector_enabled=True 但索引未就绪时, 返回 warning 并走纯规则, 不阻塞问数
- 同一进程内要能分别跑 rule-only 与 rule+vector, 方便 eval 对比

### 2.2 模块结构

新建:
- backend/app/metadata/vector/__init__.py
- backend/app/metadata/vector/embedding.py -- 模型加载, 维度推断, embed(text)
- backend/app/metadata/vector/store.py -- LanceDB 连接, schema, upsert, search
- backend/app/metadata/vector/indexer.py -- 从 SQLite 构建向量索引
- backend/app/metadata/vector/searcher.py -- 向量搜索接口
- backend/app/metadata/hybrid.py -- 混合融合算法
- scripts/rebuild_vector_index.py -- 手动重建向量索引

修改:
- backend/app/metadata/retrieval.py -- 增加 use_vector 参数和 hybrid_merge 调用
- backend/app/config.py -- 新增 vector 配置项
- backend/app/api/chat.py -- SSE payload 增加 vector 字段
- backend/app/api/metadata.py -- Admin 索引管理 API

### 2.3 向量索引 Schema

五个 LanceDB table。所有 table 的 vector 维度使用 `embedding_dimension`, 不硬编码:

- table_vectors: id=table_name, text=名称+描述+域, vector=embedding_dimension
- column_vectors: id=table.column, text=名称+描述+别名+sample_values, vector=embedding_dimension
- metric_vectors: id=name, text=名称+标签+描述+表达式, vector=embedding_dimension
- verified_query_vectors: id=query_id, text=问题+标签, vector=embedding_dimension
- value_vectors: id=table.column:value, text=单个 sample_value, vector=embedding_dimension

索引目录需要保存 metadata:

- embedding_model
- embedding_dimension
- built_at
- asset_counts
- schema_version

如果当前配置的模型或维度与索引 metadata 不一致, status 返回 stale, 搜索链路降级到 rule-only。

### 2.4 Retrieval Result 与来源追踪

`build_focused_context_from_retrieval()` 继续消费现有结构, 但 retrieval result 额外携带解释字段:

```python
{
    "tables": [...],
    "columns": [...],
    "metrics": [...],
    "verified_queries": [...],
    "fallback_used": false,
    "retrieval_meta": {
        "vector_used": true,
        "index_status": "ready",
        "sources": {
            "table:dim_regions": ["rule:alias", "value:华东"],
            "metric:sales_amount": ["rule:metric", "vector:0.82"]
        },
        "value_hits": [
            {
                "table_name": "dim_regions",
                "column_name": "region_group",
                "matched_value": "华东",
                "source": "exact",
                "score": 1.0
            }
        ]
    }
}
```

规则:

- 下游 context builder 忽略 `retrieval_meta` 也能正常工作
- SSE 和前端解释信息只读 `retrieval_meta`
- 不把来源信息只塞进 `reason` 字符串, 避免前端和 eval 再解析文本

### 2.5 混合融合算法

对每个资产类型:

1. 收集规则和向量的所有唯一资产 key
2. 计算 final = w_rule * normalize(rule_score) + w_vec * vec_similarity + w_priority * business_priority
3. 按 final 排序取 top-k
4. 记录每个资产的 sources

business_priority:

- verified_query +1.0
- metric +0.8
- 分析空间内表 +0.3

默认权重:

- rule: 0.6
- vector: 0.3
- priority: 0.1

### 2.6 Value Recall 策略

用户问题 "华东地区天猫渠道的美妆个护品类销售额" 中:

- "华东" -> dim_regions.region_group
- "天猫" -> dim_channels.channel_name
- "美妆个护" -> dim_products.category

实现顺序:

1. exact match: 归一化问题文本和 sample value, 做包含匹配
2. alias/value normalization: 去空格、大小写、常见全半角差异
3. vector value search: 对整个问题 embedding, 搜索 value_vectors top_k=20
4. threshold: value_vector_similarity_threshold 默认 0.75
5. 去重: 同 (table_name, column_name, matched_value) 只保留最高分
6. 注入 result["tables"] 和 result["columns"], 并写入 retrieval_meta.value_hits

不用 jieba 分词:

- sample_values 是短文本, exact/contains 已能覆盖核心业务值
- 全句 embedding 用于补充模糊匹配
- 避免额外依赖

---

## 3. Iteration 拆分

### I4.1 Embedding 配置与 wrapper

目标: 先验证本地 embedding 能稳定加载, 且向量链路可关闭。

新建文件:
- backend/app/metadata/vector/__init__.py
- backend/app/metadata/vector/embedding.py

修改文件:
- backend/app/config.py -- 新增 vector_db_path, embedding_model, embedding_dimension, vector_enabled, vector_similarity_threshold, value_vector_similarity_threshold
- backend/pyproject.toml -- 新增 sentence-transformers

实现要点:

- embedding.py 用 lru_cache 缓存模型实例
- 首次 embed 后推断维度
- 空文本返回 None, 调用方跳过, 不生成零向量
- 模型加载失败时给清晰错误, 不影响 vector_enabled=False 的主流程
- 单元测试使用 mock model, 不依赖真实模型下载

验收:

- mock embed 能返回配置维度
- vector_enabled=False 时不加载模型
- 真实模型可选手动 smoke, 不作为 CI 必须条件
- vector_enabled=True 且 embedding_model 为空时明确报错

---

### I4.2 LanceDB 存储层

目标: 建立可复用的本地向量 store, 支持动态维度。

新建文件:
- backend/app/metadata/vector/store.py

修改文件:
- backend/pyproject.toml -- 新增 lancedb>=0.16.0

实现要点:

- LanceDB table schema 使用 embedding_dimension
- 保存并读取 index metadata
- upsert/search/delete_by_ids
- 模型维度与索引维度不一致时返回 stale
- 单元测试用 fake DB 覆盖 store wrapper; 真实 LanceDB smoke 在依赖安装后单独跑

验收:

- 能 create/open 五张向量表
- 能 upsert/search 返回 score 和 asset_id
- 维度不一致时 status=stale
- 未安装 lancedb 时不影响 vector_enabled=False 和现有测试

---

### I4.3 索引构建器 + CLI

目标: 从 SQLite 元数据手动构建向量索引, 不阻塞后端启动。

新建文件:
- backend/app/metadata/vector/indexer.py
- scripts/rebuild_vector_index.py

实现要点:

- rebuild_vector_index(): 读 SQLite 全部元数据, 构建五种向量表
- full rebuild 在写入前清空五张 vector table, 避免 SQLite 删除资产后旧向量残留
- 批量 embed, 默认 batch_size=64
- 跳过空文本资产
- 写入 index metadata: model, dimension, built_at, asset_counts, schema_version
- 不在 app startup 自动执行

验收:

- mock 单测覆盖五类资产生成、批量 embedding、metadata 写入
- CLI 能手动触发 rebuild, 不接 app startup
- 真实 LanceDB + bge-m3 smoke 在依赖安装后执行
- 缺模型或模型路径错误时失败可诊断, 不污染旧索引

---

### I4.4 向量搜索器 + Hybrid Merge

目标: 在现有规则检索后追加向量召回, 并保持下游兼容。

新建文件:
- backend/app/metadata/vector/searcher.py
- backend/app/metadata/hybrid.py

修改文件:
- backend/app/metadata/retrieval.py -- retrieve_metadata_assets(question, use_vector: bool | None = None)

实现要点:

- searcher 返回标准 VectorHit: asset_type, asset_id, score, source
- hybrid_merge 返回现有 tables/columns/metrics/verified_queries 结构
- 额外写 retrieval_meta.sources
- use_vector=False 强制 rule-only, 用于 eval compare
- vector_enabled=True 但 index stale/missing 时降级 rule-only, retrieval_meta.index_status 标明原因

验收:

- retrieve_metadata_assets("营收总额", use_vector=True) 可命中 sales_amount metric
- retrieve_metadata_assets(..., use_vector=False) 与 Phase 3 输出一致
- build_focused_context_from_retrieval() 不需要改动即可消费

---

### I4.5 Value Recall

目标: 识别用户问题中的业务值, 提升维度表召回。

修改文件:
- backend/app/metadata/vector/searcher.py -- search_values()
- backend/app/metadata/hybrid.py -- value hits 注入 tables/columns

实现要点:

1. 先做 exact/contains sample value 匹配
2. 再做 vector value search
3. 同字段同值去重
4. value hit 注入 result["tables"] 和 result["columns"]
5. retrieval_meta.value_hits 标明 exact/vector 来源

验收:

- "华东地区销售额" 召回 dim_regions.region_group
- "天猫渠道订单数" 召回 dim_channels.channel_name
- "美妆个护品类销售额" 召回 dim_products.category
- "华东地区天猫渠道的美妆个护品类销售额" 能召回三个 dim 表和对应字段

---

### I4.6 召回可解释展示 (SSE + 前端)

目标: 前端展示召回来源, 方便判断是否真的走了向量和值召回。

修改文件:
- backend/app/api/chat.py -- _retrieval_step_payload() 增加 vector_used, index_status, value_hits, retrieval_sources
- frontend/src/App.vue -- 解释信息区域增加召回来源标签

SSE 新增字段:

- vector_used: bool
- index_status: ready / missing / stale / disabled
- value_hits: [{table_name, column_name, matched_value, source, score}]
- retrieval_sources: {asset_key: ["rule:xxx", "vector:0.82", "value:华东"]}

前端展示:

- 召回来源标签: 规则 / 向量 / 值召回
- Value Hit 高亮: "华东 -> dim_regions.region_group"
- index_status 非 ready 时显示轻量提示

验收:

- 前端能看到每个命中资产的召回来源
- DeepSeek/Mock 两种 provider 下展示一致

---

### I4.7 Eval 对比报告

目标: 支持"无向量" vs "有向量"对比。

修改文件:
- scripts/run_smoke_eval.py -- 新增 --vector-compare 模式
- evals/smoke_cases.yaml -- 新增 Phase 4 专用 case

新增 eval cases (6-8 条):

- phase4_value_region: "华东地区销售额" -- Value Recall 命中 dim_regions
- phase4_value_channel: "天猫渠道的订单数" -- Value Recall 命中 dim_channels
- phase4_value_category: "美妆个护品类的销售额" -- Value Recall 命中 dim_products
- phase4_value_multi: "华东地区天猫渠道的美妆个护品类销售额" -- 多值召回
- phase4_semantic_alias: "营收总额是多少" -- 语义匹配 "营收" 约等于 "销售额"
- phase4_semantic_typo: "按渠到统计销售额" -- 轻量错字, 作为 stretch case

对比命令:

```bash
python scripts/run_smoke_eval.py --vector-compare --report-path evals/reports/phase4_compare.md
```

对比报告:

- Rule Only vs Rule+Vector pass rate
- fallback count
- context reduction
- Value Recall hit rate
- vector_used/index_status 分布

验收:

- 现有 31 条 smoke case 在 vector_enabled=True 时仍全部通过
- vector_enabled=False 时 31 条 smoke case 与 Phase 3 行为一致
- Phase 4 case 在 vector 模式通过或标明 stretch 未达成

---

### I4.8 Admin 索引管理 + CRUD 一致性 + README

目标: Admin UI 管理向量索引, 并避免语义资产变更后索引过期。

修改文件:
- backend/app/api/metadata.py -- 新增 POST /api/metadata/vector/rebuild, GET /api/metadata/vector/status
- backend/app/metadata/admin_service.py 或现有 CRUD service -- 语义资产变更后标记 index stale 或触发单资产更新
- frontend/src/Admin.vue -- 向量索引管理 Tab
- README.md -- Phase 4 能力说明

实现要点:

- rebuild API 触发全量重建
- status API 返回 model, dimension, built_at, asset_counts, stale_reason
- metrics/aliases/verified queries/relationships CRUD 后至少标记 stale
- 单资产 update_vector_index_for_asset 可作为增强, 不阻塞首版

验收:

- 能通过 UI 触发索引重建
- CRUD 后 status 能显示 stale 或自动更新成功
- README 反映 Phase 4 能力

---

## 4. 文件变更总览

新建文件 (7 个):
- backend/app/metadata/vector/__init__.py
- backend/app/metadata/vector/embedding.py
- backend/app/metadata/vector/store.py
- backend/app/metadata/vector/indexer.py
- backend/app/metadata/vector/searcher.py
- backend/app/metadata/hybrid.py
- scripts/rebuild_vector_index.py

修改文件:
- backend/app/config.py
- backend/pyproject.toml
- backend/app/metadata/retrieval.py
- backend/app/metadata/service.py
- backend/app/api/chat.py
- backend/app/api/metadata.py
- scripts/run_smoke_eval.py
- evals/smoke_cases.yaml
- frontend/src/App.vue
- frontend/src/Admin.vue
- README.md

---

## 5. 验证计划

### 5.1 单元测试

- test_embedding.py: embed() 返回正确维度; 空文本跳过; 缓存生效; vector_enabled=False 不加载模型
- test_store.py: upsert + search; 重复更新; 过滤搜索; 维度不一致 stale
- test_indexer.py: 从 test SQLite 构建; 行数一致; metadata 写入正确
- test_searcher.py: 各类搜索格式正确; 阈值过滤; index missing/stale 降级
- test_hybrid.py: 格式兼容; rule+vec 同时命中排序正确; sources 记录正确
- test_value_recall.py: exact match 优先; vector value hit 注入; 去重正确

### 5.2 集成测试

| 场景 | 验证 |
|------|------|
| "华东地区销售额" | 返回 dim_regions; value_hits 包含华东 |
| "天猫渠道订单数" | 返回 dim_channels; value_hits 包含天猫 |
| "营收总额" | 向量匹配到 sales_amount metric |
| "随便看一下数据" | 仍走 fallback; vector 未改变 fallback |
| vector_enabled=False | 完全回退纯规则; 行为与 Phase 3 一致 |
| index missing/stale | 不报错; retrieval_meta.index_status 标明原因 |

### 5.3 验收标准

1. 不回归: 现有 31 条 smoke case 在 vector_enabled=True 时仍全部通过
2. 可关闭: vector_enabled=False 时 31 条 smoke case 与 Phase 3 行为一致
3. Value Recall: "华东"、"天猫"、"美妆个护" 能正确映射到对应字段
4. 语义召回: 同义词问题向量模式命中率高于纯规则
5. 前端展示: 召回来源可解释, Value Recall 有标识
6. 对比报告: 能量化有向量 vs 无向量的差异
7. 零运维: 不需要额外启动数据库或向量服务; Docker 部署只需挂载模型目录和索引目录
8. 不阻塞启动: 后端启动不自动下载模型或全量建索引

---

## 6. 不做清单

1. 实时 embedding 服务 -- 不部署独立 API server
2. 用户反馈学习 -- 不从查询历史调整权重
3. 多语言支持 -- 只优化中文
4. 向量维度调优 -- 不做 fine-tuning
5. ANN 参数调优 -- 用 LanceDB 默认参数
6. 增量监听 -- 不做 SQLite WAL 监听
7. Elasticsearch -- ROADMAP 排除
8. 查询历史向量化 -- 不用历史查询做召回
9. 跨数据源向量 -- Phase 6 ClickHouse 后再考虑
10. 复杂 RAG -- 不做 chunking, re-ranking, cross-encoder
11. 启动自动全量建索引 -- Phase 4 首版不做

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Embedding 模型路径错误 | vector_enabled=True 时显式报错; Docker 用 volume 挂载 /models/BAAI/bge-m3; vector_enabled=False 绕过 |
| LanceDB Windows 兼容 | I4.2 单独验证; 如有问题再降级 NumPy+FAISS 或 SQLite vec |
| 向量搜索增加延迟 | 约100-500ms, 占 DeepSeek 5s 调用的不到2%; 可通过 vector_enabled 关闭 |
| 混合权重不合理 | 权重可配置; 默认 rule=0.6 确保不比纯规则差; eval 对比验证 |
| sample_values 覆盖不足 | Admin UI 可补充; rebuild 后生效 |
| CRUD 后索引过期 | I4.8 至少标记 stale; 增量更新作为增强 |
| 模型维度切换导致索引不可用 | index metadata 校验 model/dimension; stale 时降级 rule-only |

---

## 8. 实现顺序与时间估计

I4.1 Embedding 配置与 wrapper              0.5 天
I4.2 LanceDB 存储层                       0.5-1 天
I4.3 索引构建器 + CLI                     1 天
I4.4 向量搜索器 + Hybrid Merge            1-1.5 天
I4.5 Value Recall                         1 天
I4.6 SSE + 前端召回可解释展示              0.5-1 天
I4.7 Eval 对比报告 + cases                1 天
I4.8 Admin 索引管理 + CRUD 一致性 + README 1 天

总计: 6-8 天

关键路径: I4.1 -> I4.2 -> I4.3 -> I4.4。I4.5 依赖 I4.3/I4.4。I4.6/I4.7 依赖 I4.4/I4.5。I4.8 最后做, 避免先把 UI 绑定到不稳定 API。

每个 iteration 完成后:

1. 跑现有 31 条 smoke case 确认不回归
2. 对新增能力跑对应单测或 eval case
3. 如果失败, 先修再进下一个 iteration

---

## 9. Critical Files for Implementation

以下文件是实现 Phase 4 最关键的 5 个文件:

- backend/app/metadata/retrieval.py -- 现有规则检索, 需要接入 use_vector 和 hybrid_merge
- backend/app/metadata/vector/store.py (新建) -- LanceDB 存储层, 所有向量操作的基础
- backend/app/metadata/vector/indexer.py (新建) -- 索引生命周期, 控制是否能稳定上线
- backend/app/metadata/hybrid.py (新建) -- 混合融合算法, 向量与规则的核心集成点
- scripts/run_smoke_eval.py -- eval runner, 需要扩展对比模式和 Value Recall 验证
