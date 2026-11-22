"""跳数数据在仓库里的落地、迁移、冲突记录和子图证据保护。

``hop_policy`` 只负责纯计算，本模块负责把计算结果写回示例数据仓库，并保证：

* 存量模板和存量子图在读取时就已经是二版跳数数据（幂等迁移）；
* 查询、预览和发布都调用同一个 ``resolve_*`` 给出同一份口径；
* 派生出来的子图保留首次登记的证据，重试发布只做同步、不做覆盖；
* 冲突只记录，不自动修改任何一侧的旧值。
"""
from __future__ import annotations

from typing import Any

from app.services import hop_policy
from app.store import store

VERSION = hop_policy.VERSION
TEMPLATE_MODULE = "graph_search"
SUBGRAPH_MODULE = "subgraph_exp"

V2_FIELDS = (
    "跳数版本",
    "可计算跳数",
    "跳数单位",
    "跳数状态",
    "跳数可执行",
    "跳数不限",
    "跳数原字面",
    "跳数边界",
    "原有边界",
    "跳数说明",
    "跳数来源",
    "采用模板",
    "跳数冲突",
    "跳数数据",
)

_migrated = False


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _row_id(row: dict[str, Any]) -> int:
    try:
        return int(row.get("id", 0))
    except (TypeError, ValueError):
        return 0


def template_code(row: dict[str, Any]) -> str:
    return str(row.get("模板编号") or f"graph_search:{_row_id(row)}")


def subgraph_code(row: dict[str, Any]) -> str:
    return str(row.get("配置编号") or f"subgraph_exp:{_row_id(row)}")


def _find_template(code_or_id: Any) -> dict[str, Any] | None:
    if code_or_id in (None, ""):
        return None
    for row in store.rows(TEMPLATE_MODULE):
        if template_code(row) == str(code_or_id):
            return row
    try:
        entry_id = int(code_or_id)
    except (TypeError, ValueError):
        entry_id = None
    if entry_id is not None:
        return store.find(TEMPLATE_MODULE, entry_id)
    return None


def _known_template_codes() -> dict[str, dict[str, Any]]:
    return {template_code(row): row for row in store.rows(TEMPLATE_MODULE)}


# ---------------------------------------------------------------------------
# 口径：同一份取数函数服务查询、迁移和发布
# ---------------------------------------------------------------------------
def _boundary_of(row: dict[str, Any]) -> Any:
    """读取原有边界：迁移写回过的 ``原有边界`` 优先，其次是旧记录自带的值。"""
    stored = row.get("原有边界")
    if isinstance(stored, dict):
        return stored
    return row.get("跳数边界")


def resolve_template_hop(row: dict[str, Any]) -> dict[str, Any]:
    """解析搜索模板的二版跳数。旧值字面保留在 ``跳数上限`` 里不动。"""
    stored = row.get("跳数数据")
    return hop_policy.resolve_hop(
        row.get("跳数上限"),
        boundary=_boundary_of(row),
        adopted_template=template_code(row),
        stored=stored if isinstance(stored, dict) else None,
    )


def adopted_template_for_subgraph(row: dict[str, Any]) -> dict[str, Any] | None:
    """找出子图实际采用的搜索模板。

    优先使用显式登记的「来源模板」/「采用模板」；存量数据没有显式链接时，
    用「探查种子 + 关系过滤」做唯一匹配；仍不唯一就返回 ``None``，不臆测。
    """
    for key in ("来源模板", "采用模板"):
        candidate = _find_template(row.get(key))
        if candidate is not None:
            return candidate
    evidence = row.get("子图证据")
    if isinstance(evidence, dict):
        candidate = _find_template(evidence.get("来源模板"))
        if candidate is not None:
            return candidate
    seed = str(row.get("探查种子") or "")
    relation = str(row.get("关系过滤") or "")
    matches = [
        template for template in store.rows(TEMPLATE_MODULE)
        if seed and str(template.get("起始实体") or "") == seed
        and str(template.get("关系过滤") or "") == relation
    ]
    if len(matches) == 1:
        return matches[0]
    if seed:
        for code, template in _known_template_codes().items():
            if code and code in seed:
                return template
    return None


def derived_subgraphs(template: dict[str, Any]) -> list[dict[str, Any]]:
    """找出已经从指定模板派生出来的子图（含存量迁移登记的来源模板）。"""
    code = template_code(template)
    result: list[dict[str, Any]] = []
    for subgraph in store.rows(SUBGRAPH_MODULE):
        linked = str(subgraph.get("来源模板") or "")
        evidence = _evidence_of(subgraph) or {}
        if not linked and isinstance(evidence, dict):
            linked = str(evidence.get("来源模板") or "")
        if linked == code:
            result.append(subgraph)
    return result


def resolve_subgraph_hop(row: dict[str, Any]) -> dict[str, Any]:
    """解析子图的二版跳数；有采用模板时以模板口径为准。"""
    template = adopted_template_for_subgraph(row)
    own_raw = row.get("深度限制")
    own = hop_policy.resolve_hop(
        own_raw,
        boundary=_boundary_of(row),
        adopted_template=subgraph_code(row),
    )
    if template is None:
        hop = own
        # 没有采用模板：执行只能依据子图自己保存的深度，这里如实标注为
        # 「采用配置」，不把子图编号伪装成模板编号。
        hop["adopted_template"] = None
        hop["adopted_config"] = subgraph_code(row)
        hop["adopted_source"] = "subgraph"
        hop["template_hop"] = None
        hop["own_depth"] = {
            "value": own.get("value"),
            "literal": own.get("literal"),
            "status": own.get("status"),
        }
        return hop

    template_hop = resolve_template_hop(template)
    hop = dict(template_hop)
    hop["adopted_template"] = template_code(template)
    hop["adopted_config"] = subgraph_code(row)
    hop["adopted_source"] = "graph_search"
    # 执行采用模板口径，但「旧值原字面」必须还是这条子图自己的值；模板的
    # 原字面单独留存，说明里把两者都写清楚，避免把模板的 3 当成子图的旧值。
    hop["template_literal"] = template_hop.get("literal")
    hop["literal"] = own.get("literal")
    if not template_hop.get("executable"):
        hop["reason"] = (
            f"采用模板 {template_code(template)} 的跳数不可执行："
            f"{template_hop.get('reason')}；子图独立深度「{own.get('literal')}」"
            f"（{own.get('status')}）保留为证据"
        )
    elif own.get("value") is not None and hop.get("value") is not None \
            and int(own["value"]) == int(hop["value"]):
        hop["reason"] = (
            f"采用模板 {template_code(template)} 的可计算跳数 {hop.get('value')}"
            f"（模板原值「{template_hop.get('literal')}」）；"
            f"子图独立深度「{own.get('literal')}」与之一致"
        )
    else:
        hop["reason"] = (
            f"采用模板 {template_code(template)} 的可计算跳数 {hop.get('value')}"
            f"（模板原值「{template_hop.get('literal')}」）；"
            f"子图独立深度「{own.get('literal')}」（{own.get('status')}）"
            "与模板跳数不一致，深度保留为证据，执行采用模板跳数"
        )
    # 子图这里只记录「模板跳数 vs 子图独立深度」这一项冲突；模板上的汇总
    # 冲突由 refresh_conflicts 统一生成，避免重复累加。
    conflicts: list[dict[str, Any]] = []
    if own.get("value") is not None and hop.get("value") is not None \
            and int(own["value"]) != int(hop["value"]):
        conflicts.append({
            "source": "子图独立深度",
            "derived_value": int(own["value"]),
            "adopted_value": int(hop["value"]),
            "derived_literal": own.get("literal"),
            "resolution": "保留子图已登记深度作为证据，执行采用模板跳数",
        })
    hop["conflicts"] = conflicts
    hop["own_depth"] = {
        "value": own.get("value"),
        "literal": own.get("literal"),
        "status": own.get("status"),
        "boundary": own.get("boundary"),
    }
    return hop


def hop_for_query(row: dict[str, Any], module: str = TEMPLATE_MODULE) -> dict[str, Any]:
    """查询/预览/发布共用入口。"""
    if module == SUBGRAPH_MODULE:
        return resolve_subgraph_hop(row)
    return resolve_template_hop(row)


# ---------------------------------------------------------------------------
# 写回
# ---------------------------------------------------------------------------
def apply_hop_fields(
    row: dict[str, Any],
    hop: dict[str, Any],
    *,
    preserve_conflicts: bool = False,
) -> dict[str, Any]:
    """把跳数数据写回行；旧字段（跳数上限/深度限制）始终不覆盖。

    ``preserve_conflicts=True`` 用于模板行：模板上的冲突是跨子图汇总，不能
    被单条模板自己的解析结果冲掉，统一交给 ``refresh_conflicts`` 维护。
    """
    boundary = hop.get("boundary") or hop_policy.default_boundary()
    row["跳数版本"] = VERSION
    row["可计算跳数"] = hop.get("value")
    row["跳数单位"] = hop.get("unit", hop_policy.HOP_UNIT)
    row["跳数状态"] = hop.get("status")
    row["跳数可执行"] = bool(hop.get("executable"))
    row["跳数不限"] = bool(hop.get("unlimited"))
    row["跳数原字面"] = hop.get("literal")
    row["跳数边界"] = f"{boundary.get('min')}-{boundary.get('max')}"
    row["原有边界"] = dict(boundary)
    row["跳数说明"] = hop.get("reason")
    row["跳数来源"] = hop.get("source")
    row["采用模板"] = hop.get("adopted_template")
    row["采用配置"] = hop.get("adopted_config")
    row["采用来源"] = hop.get("adopted_source")
    if not preserve_conflicts:
        row["跳数冲突"] = list(hop.get("conflicts") or [])
        hop["conflicts"] = list(row["跳数冲突"])
    else:
        hop.setdefault("conflicts", list(row.get("跳数冲突") or []))
    row["跳数数据"] = hop
    # 复查和查询始终可用；是否可执行单独给出，不把非法值伪装成不限跳。
    row["可查询"] = True
    row["查询可用"] = bool(hop.get("executable"))
    row["可执行"] = bool(hop.get("executable"))
    return row


def resolve_and_apply(row: dict[str, Any], module: str) -> dict[str, Any]:
    hop = hop_for_query(row, module)
    apply_hop_fields(row, hop, preserve_conflicts=(module == TEMPLATE_MODULE))
    return hop


# ---------------------------------------------------------------------------
# 子图证据
# ---------------------------------------------------------------------------
def _evidence_of(row: dict[str, Any]) -> dict[str, Any] | None:
    evidence = row.get("子图证据")
    return evidence if isinstance(evidence, dict) else None


def register_evidence(
    subgraph: dict[str, Any],
    template: dict[str, Any],
    *,
    reason: str = "发布模板同步",
) -> dict[str, Any]:
    """登记首次子图证据；如果已有证据则原样返回，绝不覆盖。"""
    existing = _evidence_of(subgraph)
    if existing is not None:
        return existing
    template_hop = resolve_template_hop(template)
    evidence = {
        "证据版本": VERSION,
        "来源": TEMPLATE_MODULE,
        "来源模板": template_code(template),
        "模板跳数": template_hop.get("value"),
        "模板跳数状态": template_hop.get("status"),
        "模板原字面": template_hop.get("literal"),
        "登记深度": subgraph.get("深度限制"),
        "登记原因": reason,
        "首次登记": str(subgraph.get("上次探查") or template.get("构建时间") or "2026-11-22"),
    }
    subgraph["子图证据"] = evidence
    return evidence


def record_retry(subgraph: dict[str, Any], template: dict[str, Any]) -> None:
    """记录一次重试发布，只追加记录，不修改已有证据。"""
    log = subgraph.setdefault("发布重试记录", [])
    if not isinstance(log, list):
        log = []
        subgraph["发布重试记录"] = log
    log.append({
        "来源模板": template_code(template),
        "重试模板跳数": resolve_template_hop(template).get("value"),
        "保留证据": bool(_evidence_of(subgraph)),
    })


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------
def _migrate_templates() -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for row in store.rows(TEMPLATE_MODULE):
        hop = resolve_and_apply(row, TEMPLATE_MODULE)
        report.append({
            "id": row.get("id"),
            "模板编号": template_code(row),
            "跳数上限": row.get("跳数上限"),
            "可计算跳数": hop.get("value"),
            "跳数状态": hop.get("status"),
            "跳数可执行": bool(hop.get("executable")),
        })
    return report


def _migrate_subgraphs() -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for row in store.rows(SUBGRAPH_MODULE):
        template = adopted_template_for_subgraph(row)
        hop = resolve_and_apply(row, SUBGRAPH_MODULE)
        if template is not None:
            row["来源模板"] = template_code(template)
            register_evidence(row, template, reason="存量迁移")
        report.append({
            "id": row.get("id"),
            "配置编号": subgraph_code(row),
            "深度限制": row.get("深度限制"),
            "采用模板": hop.get("adopted_template"),
            "可计算跳数": hop.get("value"),
            "跳数状态": hop.get("status"),
            "跳数可执行": bool(hop.get("executable")),
            "跳数冲突": hop.get("conflicts"),
        })
    return report


def refresh_conflicts() -> None:
    """重新生成模板上的跨子图冲突汇总。

    冲突只记录、不覆盖：子图独立深度和模板跳数都保持原样，汇总给出
    ``subgraph`` 指向具体子图，便于追证。
    """
    by_template: dict[str, list[dict[str, Any]]] = {}
    for subgraph in store.rows(SUBGRAPH_MODULE):
        template = adopted_template_for_subgraph(subgraph)
        if template is None:
            continue
        hop = resolve_subgraph_hop(subgraph)
        for conflict in hop.get("conflicts") or []:
            by_template.setdefault(template_code(template), []).append({
                **conflict,
                "subgraph": subgraph_code(subgraph),
            })
    for template in store.rows(TEMPLATE_MODULE):
        template["跳数冲突"] = by_template.get(template_code(template), [])
        if isinstance(template.get("跳数数据"), dict):
            template["跳数数据"]["conflicts"] = list(template["跳数冲突"])


def migrate_all(force: bool = False) -> dict[str, Any]:
    """迁移全部存量模板和子图，幂等。

    ``force`` 表示重新读一遍旧值（例如旧字段被人工改正后），但始终不覆盖
    旧字面、子图证据或已经登记的冲突记录。
    """
    global _migrated
    templates = _migrate_templates()
    subgraphs = _migrate_subgraphs()
    refresh_conflicts()
    _migrated = True
    return {
        "version": VERSION,
        "templates": templates,
        "subgraphs": subgraphs,
        "counts": {
            "templates": len(templates),
            "subgraphs": len(subgraphs),
            "executable_templates": sum(1 for item in templates if item["跳数可执行"]),
            "conflicts": sum(len(item.get("跳数冲突") or []) for item in subgraphs),
        },
    }


def ensure_migrated() -> None:
    if not _migrated:
        migrate_all()


def reset_migration_state() -> None:
    """测试辅助：清掉幂等标记，强制下一次重新迁移。"""
    global _migrated
    _migrated = False
