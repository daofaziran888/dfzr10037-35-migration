"""二版可计算跳数：口径、迁移、发布同步和子图执行的回归测试。"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.seed import SEED_ROWS
from app.services import hop_store
from app.store import store


@pytest.fixture()
def client():
    """每个用例重置内存仓库并重新迁移，避免用例之间互相污染。"""
    store._tables = {
        name: [copy.deepcopy(row) for row in rows]
        for name, rows in SEED_ROWS.items()
    }
    hop_store.reset_migration_state()
    hop_store.migrate_all()
    with TestClient(app) as test_client:
        yield test_client
    hop_store.reset_migration_state()


def _template(client, code):
    items = client.get("/api/graph_search?size=200").json()["items"]
    return next(item for item in items if item["模板编号"] == code)


def _subgraph(client, code):
    items = client.get("/api/subgraph_exp?size=200").json()["items"]
    return next(item for item in items if item["配置编号"] == code)


def _create_template(client, code, hop):
    response = client.post("/api/graph_search", json={"values": {
        "模板编号": code,
        "搜索类型": "multi-hop-path",
        "查询语法": "MATCH p=(a)-[*]-(b) RETURN p",
        "起始实体": f"seed-{code}",
        "关系过滤": f"rel-{code}",
        "跳数上限": hop,
    }})
    payload = response.json()
    assert payload["ok"], payload
    return payload["entry"]


# ---------------------------------------------------------------------------
# 取数口径
# ---------------------------------------------------------------------------
def test_zero_is_executable_and_keeps_literal(client):
    template = _template(client, "GRAP-0001")
    assert template["跳数上限"] == 0
    assert template["可计算跳数"] == 0
    assert template["跳数可执行"] is True
    assert template["跳数原字面"] == "0"
    assert template["跳数边界"] == "0-10"


def test_numeric_string_keeps_literal_and_boundary(client):
    template = _template(client, "GRAP-0002")
    assert template["跳数上限"] == "3"
    assert template["可计算跳数"] == 3
    assert template["跳数可执行"] is True
    assert template["跳数原字面"] == "3"


def test_null_is_missing_never_unlimited(client):
    template = _template(client, "GRAP-0003")
    assert template["可计算跳数"] is None
    assert template["跳数状态"] == "缺失"
    assert template["跳数可执行"] is False
    assert template["跳数不限"] is False
    assert template["可查询"] is True


def test_placeholder_is_invalid_never_unlimited(client):
    template = _template(client, "GRAP-0004")
    assert template["跳数状态"] == "非法"
    assert template["跳数可执行"] is False
    assert template["跳数不限"] is False
    assert template["跳数原字面"] == "图谱搜索样例4"


def test_explicit_unlimited_is_not_executable(client):
    template = _template(client, "GRAP-0005")
    assert template["跳数状态"] == "不限跳"
    assert template["跳数不限"] is True
    assert template["跳数可执行"] is False


def test_stored_hop_data_is_reused_but_recomputed_after_edit(client):
    from app.services import hop_store
    from app.store import store

    row = next(item for item in store.rows("graph_search") if item["模板编号"] == "GRAP-0002")
    assert hop_store.resolve_template_hop(row)["value"] == 3
    row["跳数上限"] = "6"
    assert hop_store.resolve_template_hop(row)["value"] == 6
    row["跳数上限"] = "3"


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------
def test_migration_report_and_conflict(client):
    report = client.get("/api/hop-migration").json()
    counts = report["counts"]
    assert counts["templates"] == 5
    assert counts["subgraphs"] == 4
    assert counts["conflicts"] == 1
    assert report["version"] == "v2"


def test_derived_subgraph_keeps_evidence_and_reports_conflict(client):
    subgraph = _subgraph(client, "SUBG-0003")
    assert subgraph["深度限制"] == "5"
    assert subgraph["可计算跳数"] == 3
    assert subgraph["采用模板"] == "GRAP-0002"
    assert subgraph["子图证据"]["登记深度"] == "5"
    conflicts = subgraph["跳数冲突"]
    assert conflicts and conflicts[0]["derived_value"] == 5
    assert conflicts[0]["adopted_value"] == 3


def test_template_matching_does_not_guess(client):
    # 种子和关系过滤都匹配不到任何模板时，不臆测采用模板。
    assert _subgraph(client, "SUBG-0001")["采用模板"] is None
    # 通过种子 + 关系过滤唯一匹配到 GRAP-0003。
    assert _subgraph(client, "SUBG-0002")["采用模板"] == "GRAP-0003"


# ---------------------------------------------------------------------------
# 发布、重试和子图探查
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hop,expect_ok,expect_value,expect_status", [
    (0, True, 0, "可计算"),
    ("6", True, 6, "可计算"),
    (None, False, None, "缺失"),
])
def test_publish_with_four_value_shapes(client, hop, expect_ok, expect_value, expect_status):
    code = f"T-{expect_status}-{hop}"
    entry = _create_template(client, code, hop)
    response = client.post(
        f"/api/graph_search/{entry['id']}/actions", json={"action": "发布模板"})
    payload = response.json()
    assert payload["ok"] is expect_ok
    reread = _template(client, code)
    assert reread["可计算跳数"] == expect_value
    assert reread["跳数状态"] == expect_status
    assert reread["跳数可执行"] is expect_ok

    subgraphs = [
        item for item in client.get("/api/subgraph_exp?size=200").json()["items"]
        if item.get("来源模板") == code
    ]
    if expect_ok:
        assert len(subgraphs) == 1
        assert subgraphs[0]["可计算跳数"] == expect_value
        assert subgraphs[0]["采用模板"] == code
        assert subgraphs[0]["待办状态"] == "待探查"
    else:
        assert subgraphs == []


def test_republish_does_not_overwrite_existing_evidence(client):
    before = _subgraph(client, "SUBG-0003")
    count_before = client.get("/api/subgraph_exp?size=200").json()["total"]

    response = client.post("/api/graph_search/2/actions", json={"action": "发布模板"})
    assert response.json()["ok"] is True
    assert "保留原有子图证据" in response.json()["message"]

    after = _subgraph(client, "SUBG-0003")
    assert client.get("/api/subgraph_exp?size=200").json()["total"] == count_before
    assert after["子图证据"] == before["子图证据"]
    assert after["深度限制"] == "5"
    assert after["可计算跳数"] == 3
    assert after["待办状态"] == "待探查"
    assert after["发布重试记录"][0]["重试模板跳数"] == 3


def test_execution_uses_template_hop_and_adopts_it(client):
    subgraph = _subgraph(client, "SUBG-0003")
    response = client.post(
        f"/api/subgraph_exp/{subgraph['id']}/actions", json={"action": "启动探查"})
    payload = response.json()
    assert payload["ok"] is True
    assert payload["entry"]["本次执行跳数"] == 3
    assert payload["entry"]["本次执行模板"] == "GRAP-0002"
    assert payload["entry"]["深度限制"] == "5"


@pytest.mark.parametrize("code", ["SUBG-0001", "SUBG-0002"])
def test_non_executable_subgraph_cannot_run_but_still_readable(client, code):
    subgraph = _subgraph(client, code)
    assert subgraph["可查询"] is True
    assert subgraph["跳数可执行"] is False
    response = client.post(
        f"/api/subgraph_exp/{subgraph['id']}/actions", json={"action": "启动探查"})
    assert response.json()["ok"] is False
    assert "不解释为不限跳" in response.json()["message"]
    reread = _subgraph(client, code)
    assert reread["status"] == subgraph["status"]


def test_query_and_publish_share_same_hop_resolution(client):
    # 查询接口看到的跳数，与发布后写入子图待办的跳数一致。
    entry = _create_template(client, "T-SHARED", "4")
    queried = _template(client, "T-SHARED")
    client.post(f"/api/graph_search/{entry['id']}/actions", json={"action": "发布模板"})
    subgraph = next(
        item for item in client.get("/api/subgraph_exp?size=200").json()["items"]
        if item.get("来源模板") == "T-SHARED"
    )
    assert queried["可计算跳数"] == subgraph["可计算跳数"] == 4
    assert queried["采用模板"] == subgraph["采用模板"] == "T-SHARED"


def test_hop_preview_endpoint(client):
    preview = client.get("/api/hop/preview/subgraph_exp/3").json()
    assert preview["value"] == 3
    assert preview["literal"] == "5"
    assert preview["adopted_template"] == "GRAP-0002"
    assert preview["conflicts"][0]["derived_value"] == 5
    interpreted = client.post("/api/hop/interpret", json={"raw": None}).json()
    assert interpreted["executable"] is False
    assert interpreted["status"] == "缺失"
    assert client.get("/api/hop/preview/entity_extract/1").status_code == 404


def test_hop_status_filter(client):
    payload = client.get("/api/graph_search", params={"跳数状态": "可计算"}).json()
    assert payload["total"] == 2
    assert {item["模板编号"] for item in payload["items"]} == {"GRAP-0001", "GRAP-0002"}
