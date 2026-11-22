"""二版可计算跳数（hop）取数口径。

一版的「跳数上限」「深度限制」只是展示字段，值可能是占位文本、数字字符串、
空值，或者根本没有写入。二版要求把这些数据转成可计算、可解释、可追溯的跳数
数据，供图谱搜索的查询/发布与子图探查的执行共同复用。

核心约束：

* 同一个取数函数同时服务「查询」和「发布」，避免两套口径算出不同跳数；
* 合法旧值保留原始字面（``literal``）和原有边界（``boundary``）；
* 非法文本、空值、越界值一律 **不能** 解释成「不限跳」，而是给出
  ``executable=False`` 和可读原因；
* 每个结果都记录实际采用的模板（``adopted_template``），便于追证；
* 模板与已派生子图深度不一致时记录 ``conflicts``，但不覆盖子图证据。

本模块只做纯计算，不直接读写 store，方便查询、发布、迁移和测试共用。
"""
from __future__ import annotations

import re
from typing import Any

VERSION = "v2"

MIN_HOPS = 0
MAX_HOPS = 10
HOP_UNIT = "hop"

STATUS_COMPUTABLE = "可计算"
STATUS_MISSING = "缺失"
STATUS_INVALID = "非法"
STATUS_OUT_OF_RANGE = "越界"
STATUS_UNLIMITED = "不限跳"

KIND_INTEGER = "integer"
KIND_NUMERIC_STRING = "numeric_string"
KIND_UNLIMITED = "unlimited"
KIND_RANGE = "range"
KIND_NULL = "null"
KIND_BLANK = "blank"
KIND_PLACEHOLDER = "placeholder"

# 只有旧值明确写了这些词，才允许解释成不限跳。空值和非法文本不在此列。
UNLIMITED_TOKENS = {
    "不限",
    "不限跳",
    "无限",
    "无限跳",
    "unlimited",
    "inf",
    "infinite",
    "infinity",
    "*",
    "all",
}

_INT_RE = re.compile(r"^[+-]?\d+$")
_HOP_RE = re.compile(r"^([+-]?\d+)\s*(?:跳|hops?)?$", re.IGNORECASE)
_RANGE_RE = re.compile(r"^\s*(\d+)\s*[-~至]\s*(\d+)\s*$")


def default_boundary() -> dict[str, Any]:
    """默认跳数边界；二版按 0..10 跳解释存量数据。"""
    return {
        "min": MIN_HOPS,
        "max": MAX_HOPS,
        "unit": HOP_UNIT,
        "min_inclusive": True,
        "max_inclusive": True,
        "source": "default",
    }


def normalize_boundary(raw: Any) -> dict[str, Any]:
    """把记录上的「跳数边界」归一化，缺省时使用默认边界。

    支持 ``{"min": 0, "max": 8}`` 以及 ``"0-8"`` / ``"0~8"`` / ``"0至8"``
    等旧写法。边界属于「原有边界」，归一化后仍会随跳数数据一起返回。
    """
    boundary = default_boundary()
    if isinstance(raw, dict):
        changed = False
        for key in ("min", "max"):
            if key not in raw:
                continue
            value = raw[key]
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                boundary[key] = value
                changed = True
            elif isinstance(value, str) and _INT_RE.match(value.strip()):
                boundary[key] = int(value.strip())
                changed = True
        for key in ("min_inclusive", "max_inclusive"):
            if isinstance(raw.get(key), bool):
                boundary[key] = raw[key]
                changed = True
        if raw.get("unit"):
            boundary["unit"] = str(raw["unit"])
        if changed:
            boundary["source"] = "legacy"
    elif isinstance(raw, str):
        match = _RANGE_RE.match(raw)
        if match:
            boundary["min"] = int(match.group(1))
            boundary["max"] = int(match.group(2))
            boundary["source"] = "legacy"
    if boundary["min"] > boundary["max"]:
        # 边界本身写反时也不能悄悄放宽：交换只用于展示，并记录异常。
        boundary["min"], boundary["max"] = boundary["max"], boundary["min"]
        boundary["malformed"] = True
    return boundary


def _literal_of(raw: Any) -> str | None:
    """保留旧值原字面：字符串原样保留，数字转成文本，空值保持 None。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return str(raw)


def _value_kind(raw: Any) -> str:
    if raw is None:
        return KIND_NULL
    if isinstance(raw, str):
        if not raw.strip():
            return KIND_BLANK
        text = raw.strip()
        if text.casefold() in UNLIMITED_TOKENS:
            return KIND_UNLIMITED
        if _INT_RE.match(text) or _HOP_RE.match(text):
            return KIND_NUMERIC_STRING
        if _RANGE_RE.match(text):
            return KIND_RANGE
        return KIND_PLACEHOLDER
    if isinstance(raw, bool):
        return KIND_PLACEHOLDER
    if isinstance(raw, int):
        return KIND_INTEGER
    if isinstance(raw, float) and raw.is_integer():
        return KIND_INTEGER
    return KIND_PLACEHOLDER


def _make(
    *,
    raw: Any,
    value: int | None,
    status: str,
    executable: bool,
    reason: str,
    literal: str | None,
    kind: str,
    boundary: dict[str, Any],
    source: str,
    adopted_template: str | None,
    unlimited: bool = False,
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "version": VERSION,
        "value": value,
        "unit": boundary.get("unit", HOP_UNIT),
        "status": status,
        "executable": executable,
        "unlimited": unlimited,
        "reason": reason,
        "literal": literal,
        "raw": raw,
        "kind": kind,
        "boundary": boundary,
        "source": source,
        "adopted_template": adopted_template,
        "conflicts": list(conflicts or []),
    }


def resolve_hop(
    raw: Any = None,
    *,
    boundary: Any = None,
    adopted_template: str | None = None,
    stored: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把旧字段解析成二版可计算跳数数据。

    参数：
        raw: 旧字段的字面值（「跳数上限」或「深度限制」）。
        boundary: 旧记录上可能存在的「跳数边界」。
        adopted_template: 实际采用的模板编号，用于追证。
        stored: 已经存在的二版跳数数据；存在时优先复用它，避免查询和发布
            对同一条数据反复解释出不同的结果。

    返回结构同时包含 ``value``（可执行跳数）、``status``、``executable``、
    ``literal``（旧值字面）、``boundary``（原有边界）、``adopted_template``
    （实际采用的模板）和 ``conflicts``（与已派生子图深度的冲突）。
    """
    normalized = normalize_boundary(boundary)
    current_literal = _literal_of(raw)
    if isinstance(stored, dict) and stored.get("version") == VERSION \
            and stored.get("literal") == current_literal:
        # 旧字面没变时才复用；旧字段被人工改正后必须重新计算，不能让
        # 过期的可计算跳数继续生效。
        merged = dict(stored)
        merged.setdefault("boundary", normalized)
        if adopted_template and not merged.get("adopted_template"):
            merged["adopted_template"] = adopted_template
        merged.setdefault("conflicts", [])
        return merged

    kind = _value_kind(raw)
    literal = _literal_of(raw)

    def build(**kwargs: Any) -> dict[str, Any]:
        return _make(
            raw=raw,
            literal=literal,
            kind=kind,
            boundary=normalized,
            adopted_template=adopted_template,
            **kwargs,
        )

    if kind == KIND_NULL:
        return build(
            value=None,
            status=STATUS_MISSING,
            executable=False,
            reason="旧值为空，未登记跳数；空值不解释为不限跳",
            source="legacy:null",
        )
    if kind == KIND_BLANK:
        return build(
            value=None,
            status=STATUS_MISSING,
            executable=False,
            reason="旧值为空白文本，未登记跳数；空白不解释为不限跳",
            source="legacy:blank",
        )
    if kind == KIND_UNLIMITED:
        return build(
            value=None,
            status=STATUS_UNLIMITED,
            executable=False,
            unlimited=True,
            reason="旧值显式声明不限跳，需要人工给出可执行跳数后再发布",
            source="legacy:explicit-unlimited",
        )
    if kind == KIND_PLACEHOLDER:
        return build(
            value=None,
            status=STATUS_INVALID,
            executable=False,
            reason=f"旧值「{literal}」不是跳数，按占位文本保留，不解释为不限跳",
            source="legacy:placeholder",
        )
    if kind == KIND_RANGE:
        match = _RANGE_RE.match(str(raw))
        assert match is not None
        low, high = int(match.group(1)), int(match.group(2))
        if low == high and _within(low, normalized):
            return build(
                value=low,
                status=STATUS_COMPUTABLE,
                executable=True,
                reason="旧值为单值区间，按该区间上下界解释为固定跳数",
                source="legacy:range",
            )
        return build(
            value=None,
            status=STATUS_INVALID,
            executable=False,
            reason=f"旧值「{literal}」是区间而不是单一跳数，不能直接执行",
            source="legacy:range",
        )

    # 数字 / 数字字符串。
    if isinstance(raw, float) and raw.is_integer():
        value = int(raw)
    else:
        text = str(raw).strip()
        hop_match = _HOP_RE.match(text)
        if hop_match is None:
            return build(
                value=None,
                status=STATUS_INVALID,
                executable=False,
                reason=f"旧值「{literal}」无法解析为整数跳数",
                source="legacy:invalid",
            )
        value = int(hop_match.group(1))
    if not _within(value, normalized):
        return build(
            value=value,
            status=STATUS_OUT_OF_RANGE,
            executable=False,
            reason=(
                f"跳数 {value} 超出边界 [{normalized['min']}, {normalized['max']}]，"
                "保留原值但不解释为不限跳"
            ),
            source="legacy:out-of-range",
        )
    reason = (
        "旧值为整数，保留原字面并采用原有边界"
        if kind == KIND_INTEGER
        else f"旧值「{literal}」是数字字符串，已解析为整数并保留原字面"
    )
    return build(
        value=value,
        status=STATUS_COMPUTABLE,
        executable=True,
        reason=reason,
        source="legacy:integer" if kind == KIND_INTEGER else "legacy:numeric-string",
    )


def _within(value: int, boundary: dict[str, Any]) -> bool:
    low, high = boundary["min"], boundary["max"]
    low_ok = value >= low if boundary.get("min_inclusive", True) else value > low
    high_ok = value <= high if boundary.get("max_inclusive", True) else value < high
    return low_ok and high_ok


def interpret_candidate(raw: Any) -> dict[str, Any]:
    """解释一个旧候选值，不附加模板信息；供迁移时挑选用哪个来源。"""
    return resolve_hop(raw)


def detect_conflicts(
    hop: dict[str, Any],
    *,
    derived_depths: Iterable[Any] = (),
    source_label: str = "子图深度",
) -> list[dict[str, Any]]:
    """比较第二版跳数与已派生子图独立保存的深度，输出冲突列表。

    冲突只记录，不覆盖。 ``derived_depths`` 里每个元素可以是原始值，也可以
    是 ``{"literal": ..., "value": ...}``。
    """
    conflicts: list[dict[str, Any]] = []
    hop_value = hop.get("value")
    for item in derived_depths:
        if isinstance(item, dict):
            raw = item.get("raw", item.get("literal"))
            other = item.get("value")
        else:
            raw = item
            other = interpret_candidate(item).get("value")
        if other is None or hop_value is None:
            continue
        if int(other) != int(hop_value):
            conflicts.append({
                "source": source_label,
                "derived_value": int(other),
                "adopted_value": int(hop_value),
                "derived_literal": _literal_of(raw),
                "resolution": "保留子图已登记深度，模板跳数只记录冲突",
            })
    return conflicts
