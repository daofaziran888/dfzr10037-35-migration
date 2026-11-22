"""图谱搜索领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services import hop_policy, hop_store
from app.services.base import BaseModuleService
from app.store import store


class GraphSearchService(BaseModuleService):
    MODULE = "graph_search"
    ENTITY = "搜索模板"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["模板编号", "搜索类型", "查询语法", "起始实体", "关系过滤", "跳数上限", "排序规则", "使用频次"]
    FILTER_FIELDS = ["跳数状态", "可计算跳数", "采用模板", "跳数可执行"]
    REQUIRED_FIELDS = ["模板编号", "搜索类型", "查询语法"]
    STATUS_ORDER = ["草稿", "已发布", "性能告警", "已停用"]
    ACTION_RULES = {"发布模板": "已发布", "标记告警": "性能告警", "停用模板": "已停用"}
    NEGATIVE_ACTIONS = ["停用模板"]

    # ===== 查询与读取：统一补上二版可计算跳数 =====
    def list_entries(self, **kwargs: Any):
        hop_store.ensure_migrated()
        return super().list_entries(**kwargs)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        hop_store.ensure_migrated()
        return super().get_entry(entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is not None:
            if values.get("跳数边界") is not None:
                entry["跳数边界"] = values.get("跳数边界")
            # 新建模板也走同一份取数口径，保证新旧模板查询结果一致。
            hop_store.resolve_and_apply(entry, self.MODULE)
        return entry, missing

    # ===== 跨模块数据流实现 =====
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        if action == "发布模板":
            hop = hop_store.resolve_template_hop(entry)
            hop_store.apply_hop_fields(entry, hop, preserve_conflicts=True)
            if not hop.get("executable"):
                return None, (
                    f"{self.ENTITY} {entry.get('模板编号')} 跳数不可执行：{hop.get('reason')}；"
                    "已保留旧字面与原有边界，未签发探查"
                )

        entry, message = super().run_action(entry_id, action)
        if entry is None:
            return entry, message
        # 链路 L5: graph_search → query_cache（发布搜索模板后写入查询缓存）
        if entry.get("status") == "已发布":
            cache_msg = self._push_to_query_cache(entry)
            message += "；" + cache_msg
        # 链路 L6: graph_search → subgraph_exp（发布结果同步到子图探查待办）
        if entry.get("status") == "已发布":
            subgraph_msg = self._push_to_subgraph_exp(entry)
            message += "；" + subgraph_msg
        return entry, message

    def _push_to_query_cache(self, entry: dict[str, Any]) -> str:
        cache_rows = store.rows("query_cache")
        next_id = max((int(r.get("id", 0)) for r in cache_rows), default=0) + 1
        hop = hop_store.resolve_template_hop(entry)
        qc_entry: dict[str, Any] = {
            "id": next_id,
            "status": "有效",
            "pending": True,
            "abnormal": False,
            "条目编号": f"QUER-CACHE-{next_id:04d}",
            "查询模板": str(entry.get("查询语法", "")),
            "结果哈希": f"hash_{entry.get('模板编号','')}",
            "命中次数": "0",
            "TTL秒数": "3600",
            "存储大小": f"{len(str(entry))}B",
            "最后命中": "",
            "缓存状态": "warm",
            "跳数版本": hop.get("version"),
            "可计算跳数": hop.get("value"),
            "采用模板": hop.get("adopted_template"),
        }
        cache_rows.append(qc_entry)
        return f"已写入查询缓存 {qc_entry['条目编号']}"

    def _push_to_subgraph_exp(self, entry: dict[str, Any]) -> str:
        """把发布结果同步到子图探查待办。

        已经派生过子图的旧模板在重试发布时只同步待办，不覆盖已登记的证据。
        """
        template_hop = hop_store.resolve_template_hop(entry)
        derived = hop_store.derived_subgraphs(entry)
        if derived:
            for subgraph in derived:
                hop_store.record_retry(subgraph, entry)
                hop_store.register_evidence(subgraph, entry, reason="重试发布同步")  # 已有证据不覆盖
                hop_store.apply_hop_fields(subgraph, hop_store.resolve_subgraph_hop(subgraph))
                subgraph["待办来源"] = "图谱搜索发布"
                subgraph["待办状态"] = "待探查"
                subgraph["待办模板跳数"] = template_hop.get("value")
                subgraph["pending"] = True
            codes = "、".join(hop_store.subgraph_code(item) for item in derived)
            hop_store.refresh_conflicts()
            return f"已同步子图探查待办 {codes}（保留原有子图证据，跳数 {template_hop.get('value')}）"

        sub_rows = store.rows("subgraph_exp")
        next_id = max((int(r.get("id", 0)) for r in sub_rows), default=0) + 1
        literal = template_hop.get("literal")
        sb_entry: dict[str, Any] = {
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "配置编号": f"SUBG-SRCH-{next_id:04d}",
            "探查种子": str(entry.get("起始实体", "")),
            "扩展策略": "bfs-traversal",
            "关系过滤": str(entry.get("关系过滤", "")),
            # 旧字面保留；二版可计算跳数由统一口径写入。
            "深度限制": "" if literal is None else str(literal),
            "结果数量": 15,
            "缓存策略": "auto-warm",
            "上次探查": "2026-11-18",
            "来源模板": hop_store.template_code(entry),
            "待办来源": "图谱搜索发布",
            "待办状态": "待探查",
            "待办模板跳数": template_hop.get("value"),
        }
        sub_rows.append(sb_entry)
        hop_store.register_evidence(sb_entry, entry, reason="发布派生")
        hop_store.apply_hop_fields(sb_entry, hop_store.resolve_subgraph_hop(sb_entry))
        hop_store.refresh_conflicts()
        return f"已生成子图探查配置 {sb_entry['配置编号']}（跳数 {template_hop.get('value')}）"

    def domain_snapshot(self) -> dict[str, Any]:
        hop_store.ensure_migrated()
        base = super().domain_snapshot()
        base["cross_module_consumers"] = ["query_cache", "subgraph_exp"]
        base["hop_version"] = hop_policy.VERSION
        return base
