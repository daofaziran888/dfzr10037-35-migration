# 知谱工坊 · 元数据知识图谱与智能探查平台

面向元数据实体抽取、关系建模、知识图谱构建、图谱搜索、实体链接、消岐融合、路径推理、子图探查与图谱演化追踪的元数据知识图谱与智能探查平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实体抽取 | `entity_extract` | 实体定义 | 实体编号、实体类型、数据来源 |
| 关系建模 | `relation_model` | 关系类型 | 关系编号、关系名、主体类型 |
| 图谱构建 | `graph_build` | 构建任务 | 任务编号、数据范围、图类型 |
| 图谱搜索 | `graph_search` | 搜索模板 | 模板编号、搜索类型、查询语法、可计算跳数 |
| 实体链接 | `entity_link` | 链接规则 | 规则编号、源实体、目标实体 |
| 消岐融合 | `disambig` | 消岐配置 | 配置编号、歧义类型、候选实体 |
| 路径推理 | `path_reason` | 推理任务 | 任务编号、起始实体、目标实体 |
| 子图探查 | `subgraph_exp` | 探查配置 | 配置编号、探查种子、扩展策略、可计算跳数 |
| 图谱演化 | `graph_evolve` | 演化记录 | 记录编号、变更类型、影响实体 |
| 质量审计 | `quality_audit` | 审计报告 | 报告编号、审计范围、实体准确率 |
| 元数据标注 | `metadata_tag` | 标注任务 | 任务编号、标注对象、标注类型 |
| 分类体系 | `taxonomy` | 分类树 | 分类编号、分类名称、父分类 |
| 查询缓存 | `query_cache` | 缓存条目 | 条目编号、查询模板、结果哈希 |
| 图谱索引 | `graph_index` | 索引配置 | 索引编号、目标属性、索引类型 |
| 数据血缘 | `data_lineage` | 血缘链路 | 链路编号、上游实体、下游实体 |
| 推荐引擎 | `reco_engine` | 推荐规则 | 规则编号、推荐场景、源实体 |
| 实体归并 | `entity_merge` | 归并任务 | 任务编号、源实体、目标实体 |
| 批量导入 | `batch_load` | 导入任务 | 任务编号、数据文件、目标图谱 |
| 图谱报告 | `report_gen` | 图谱报表 | 报告编号、报告类型、数据范围 |
| 图谱比对 | `graph_compare` | 比对任务 | 任务编号、基准图、对比图 |
| 定时任务 | `schedule_job` | 调度作业 | 作业编号、任务类型、关联图谱 |
| 治理看板 | `govern_dash` | 治理指标 | 指标编号、指标名称、评估对象 |

## 二版可计算跳数

一版的「跳数上限 / 深度限制」只是展示字段，示例数据里同时存在整数 `0`、
数字字符串、占位文本、空值和显式「不限」。二版把存量与新增模板统一转成
可计算跳数数据，新增字段随列表和详情一起返回：

| 字段 | 含义 |
| --- | --- |
| `可计算跳数` | 可执行的整数跳数；不可执行时为 `null` |
| `跳数状态` | 可计算 / 缺失 / 非法 / 越界 / 不限跳 |
| `跳数可执行` | 是否允许发布或启动探查 |
| `跳数原字面` | 旧值原样保留，标量也转成文本 |
| `跳数边界` / `原有边界` | 原有边界归一化后的结果（默认 0..10） |
| `采用模板` | 实际采用的模板编号；子图自有深度时为空 |
| `跳数冲突` | 模板跳数与已派生子图独立深度的差异，只记录不覆盖 |
| `可查询` | 复查与查询始终为 `true`，与能否执行分开 |

关键口径：

- 查询、预览、发布和迁移都调用 `hop_policy.resolve_hop` / `hop_store.hop_for_query`，
  同一条记录只有一份跳数结果；
- 空值和占位文本给出不可执行 + 原因，**不会**被解释成不限跳；只有旧值显式写了
  「不限 / unlimited / *」才标记为不限跳，且同样不可执行；
- 已派生子图保留首次登记的 `子图证据`，重试发布只追加 `发布重试记录` 并刷新待办，
  不覆盖证据、不重复派生子图；
- 子图执行采用来源模板的可计算跳数，子图独立保存的深度只作为证据和冲突记录；
- 迁移是幂等的，服务启动时自动执行；`GET /api/hop-migration` 查看报告，
  `GET /api/hop/preview/{module}/{id}` 复查单条记录，`POST /api/hop/interpret`
  可临时解释一个旧值。回归测试见 `backend/tests/test_hop_v2.py`。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
