"""子图探查领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services import hop_policy, hop_store
from app.services.base import BaseModuleService
from app.store import store


class SubgraphExpService(BaseModuleService):
    MODULE = "subgraph_exp"
    ENTITY = "探查配置"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["配置编号", "探查种子", "扩展策略", "关系过滤", "深度限制", "结果数量", "缓存策略", "上次探查"]
    FILTER_FIELDS = ["跳数状态", "可计算跳数", "采用模板", "跳数可执行", "待办状态"]
    REQUIRED_FIELDS = ["配置编号", "探查种子", "扩展策略"]
    STATUS_ORDER = ["草稿", "探查中", "已缓存", "已丢弃"]
    ACTION_RULES = {"启动探查": "探查中", "缓存结果": "已缓存", "丢弃缓存": "已丢弃"}
    NEGATIVE_ACTIONS = []

    # ===== 查询与读取：存量、派生、新建统一补上二版跳数 =====
    def list_entries(self, **kwargs: Any):
        hop_store.ensure_migrated()
        return super().list_entries(**kwargs)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        hop_store.ensure_migrated()
        return super().get_entry(entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is not None:
            for key in ("跳数边界", "来源模板"):
                if values.get(key) is not None:
                    entry[key] = values.get(key)
            hop_store.resolve_and_apply(entry, self.MODULE)
        return entry, missing

    # ===== 跨模块数据流实现 =====
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        hop = hop_store.resolve_subgraph_hop(entry)
        hop_store.apply_hop_fields(entry, hop)

        if action == "启动探查" and not hop.get("executable"):
            # 不能用 0 / 空值 / 占位文本顶替跳数：拒绝执行但保留复查能力。
            return None, (
                f"{self.ENTITY} {entry.get('配置编号')} 无法执行：{hop.get('reason')}；"
                f"已保留原字面「{hop.get('literal')}」与原有边界"
            )

        entry, message = super().run_action(entry_id, action)
        if entry is None:
            return entry, message

        if action == "启动探查":
            entry["本次执行跳数"] = hop.get("value")
            entry["本次执行模板"] = hop.get("adopted_template")
            entry["执行口径版本"] = hop_policy.VERSION
            entry["待办状态"] = "已执行"
            adopted = hop.get("adopted_template") or "子图自有深度"
            message += f"（跳数 {hop.get('value')}，采用 {adopted}）"

        # 链路 L24: subgraph_exp → graph_search（探查结果更新搜索模板命中）
        if entry.get("status") in ("探查中", "已缓存"):
            search_msg = self._push_to_graph_search(entry)
            message += "；" + search_msg
        # 链路 L25: subgraph_exp → reco_engine（探查种子触发推荐）
        if entry.get("status") in ("探查中", "已缓存"):
            reco_msg = self._push_to_reco_engine(entry)
            message += "；" + reco_msg
        hop_store.refresh_conflicts()
        return entry, message

    def _push_to_graph_search(self, entry: dict[str, Any]) -> str:
        rows = store.rows("graph_search")
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        hop = hop_store.resolve_subgraph_hop(entry)
        literal = hop.get("literal")
        gs_entry: dict[str, Any] = {
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "模板编号": f"GRAP-SUB-{next_id:04d}",
            "搜索类型": "subgraph-traversal",
            "查询语法": str(entry.get("扩展策略", "bfs")),
            "起始实体": str(entry.get("探查种子", "")),
            "关系过滤": str(entry.get("关系过滤", "")),
            # 旧字段保留原字面；二版可计算跳数随行写入。
            "跳数上限": "" if literal is None else str(literal),
            "排序规则": "relevance-desc",
            "使用频次": "0",
        }
        rows.append(gs_entry)
        hop_store.resolve_and_apply(gs_entry, "graph_search")
        return f"已生成搜索模板 {gs_entry['模板编号']}（跳数 {hop.get('value')}）"

    def _push_to_reco_engine(self, entry: dict[str, Any]) -> str:
        rows = store.rows("reco_engine")
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        reco_entry: dict[str, Any] = {
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "规则编号": f"RECO-SUB-{next_id:04d}",
            "推荐场景": "子图探查关联推荐",
            "源实体": str(entry.get("探查种子", "")),
            "推荐算法": "subgraph-neighbor-rank",
            "候选数量": int(entry.get("结果数量", 10)),
            "过滤条件": str(entry.get("关系过滤", "")),
            "命中次数": "0",
            "采纳率": "0%",
        }
        rows.append(reco_entry)
        return f"探查种子推送推荐引擎 {reco_entry['规则编号']}"

    def domain_snapshot(self) -> dict[str, Any]:
        hop_store.ensure_migrated()
        base = super().domain_snapshot()
        base["cross_module_consumers"] = ["graph_search", "reco_engine"]
        base["hop_version"] = hop_policy.VERSION
        return base
