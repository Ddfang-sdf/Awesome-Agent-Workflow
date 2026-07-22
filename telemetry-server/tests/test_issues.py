from __future__ import annotations


def issue_payload(**overrides):
    payload = {
        "title": "看板数据延迟",
        "description": "下午的采纳数据没有刷新，请协助排查。",
        "reporter": "李晓明",
        "assignee": "张轶勃",
        "priority": "high",
        "component": "team/example-service",
        "sr": "SR-1001",
    }
    payload.update(overrides)
    return payload


def test_issue_lifecycle_and_activity_log(client):
    created = client.post("/api/v1/issues", json=issue_payload())
    assert created.status_code == 201
    item = created.json()
    assert item["status"] == "todo"
    assert item["resolved_at"] is None

    updated = client.patch(
        f"/api/v1/issues/{item['id']}",
        json={"reporter": "王小红", "assignee": "徐哲威", "status": "resolved"},
    )
    assert updated.status_code == 200
    assert updated.json()["resolved_at"] is not None

    detail = client.get(f"/api/v1/issues/{item['id']}")
    assert detail.status_code == 200
    assert detail.json()["reporter"] == "王小红"
    assert [activity["action"] for activity in detail.json()["activities"]] == [
        "created",
        "updated",
    ]
    assert detail.json()["activities"][1]["details"]["assignee"]["to"] == "徐哲威"


def test_issue_list_filters_and_validates_assignee(client):
    client.post("/api/v1/issues", json=issue_payload(title="第一个", assignee="张轶勃"))
    client.post(
        "/api/v1/issues",
        json=issue_payload(title="第二个", assignee="宋东方", status="in_progress"),
    )
    result = client.get("/api/v1/issues", params={"status": "in_progress", "assignee": "宋东方"})
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["items"][0]["title"] == "第二个"

    invalid = client.get("/api/v1/issues", params={"assignee": "不在名单"})
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "INVALID_ASSIGNEE"


def test_issue_rejects_invalid_or_empty_updates(client):
    assert client.post("/api/v1/issues", json=issue_payload(assignee="其他人")).status_code == 400
    created = client.post("/api/v1/issues", json=issue_payload()).json()
    assert client.patch(f"/api/v1/issues/{created['id']}", json={}).status_code == 400
    assert client.get("/api/v1/issues/99999999-9999-4999-8999-999999999999").status_code == 404
