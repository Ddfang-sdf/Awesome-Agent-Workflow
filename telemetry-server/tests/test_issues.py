from __future__ import annotations

import io

from PIL import Image, PngImagePlugin


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


def image_bytes(*, size=(80, 50), image_format="PNG"):
    stream = io.BytesIO()
    image = Image.new("RGB", size, "#4b3fe4")
    if image_format == "PNG":
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("private-note", "must be stripped")
        image.save(stream, format=image_format, pnginfo=metadata)
    else:
        image.save(stream, format=image_format)
    return stream.getvalue()


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


def test_issue_image_upload_bind_render_and_delayed_remove(client):
    uploaded = client.post(
        "/api/v1/issues/images",
        files={"image": ("screen.png", image_bytes(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    image = uploaded.json()
    assert image["status"] == "temporary"
    assert (image["width"], image["height"]) == (80, 50)

    original = client.get(f"/api/v1/issues/images/{image['id']}/original")
    assert original.status_code == 200
    with Image.open(io.BytesIO(original.content)) as normalized:
        assert normalized.format == "PNG"
        assert "private-note" not in normalized.info
    preview = client.get(f"/api/v1/issues/images/{image['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/webp"

    description = "请查看截图。"
    document = {
        "version": 1,
        "nodes": [
            {"type": "text", "text": description},
            {"type": "image", "image_id": image["id"], "alt": "问题截图 1"},
        ],
    }
    created = client.post(
        "/api/v1/issues",
        json=issue_payload(description=description, description_doc=document),
    )
    assert created.status_code == 201, created.text
    issue = created.json()
    assert issue["image_count"] == 1
    assert issue["version"] == 1

    removed = client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={
            "description": "截图已移除。",
            "description_doc": {
                "version": 1,
                "nodes": [{"type": "text", "text": "截图已移除。"}],
            },
            "version": issue["version"],
        },
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["image_count"] == 0
    assert removed.json()["version"] == 2
    # Delayed deletion keeps the file readable during the recovery window.
    assert client.get(f"/api/v1/issues/images/{image['id']}/preview").status_code == 200
    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert detail["activities"][-1]["details"]["images_removed"] == 1


def test_issue_image_validation_binding_and_optimistic_lock(client):
    invalid = client.post(
        "/api/v1/issues/images",
        files={"image": ("animation.gif", image_bytes(image_format="GIF"), "image/gif")},
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "UNSUPPORTED_IMAGE_TYPE"

    uploaded = client.post(
        "/api/v1/issues/images",
        files={"image": ("screen.png", image_bytes(), "image/png")},
    ).json()
    first = client.post(
        "/api/v1/issues",
        json=issue_payload(
            description="第一条",
            description_doc={
                "version": 1,
                "nodes": [
                    {"type": "text", "text": "第一条"},
                    {
                        "type": "image",
                        "image_id": uploaded["id"],
                        "alt": "问题截图 1",
                    },
                ],
            },
        ),
    )
    assert first.status_code == 201

    reused = client.post(
        "/api/v1/issues",
        json=issue_payload(
            title="第二条",
            description="不能复用",
            description_doc={
                "version": 1,
                "nodes": [
                    {"type": "text", "text": "不能复用"},
                    {
                        "type": "image",
                        "image_id": uploaded["id"],
                        "alt": "问题截图 1",
                    },
                ],
            },
        ),
    )
    assert reused.status_code == 409
    assert reused.json()["code"] == "ISSUE_IMAGE_ALREADY_BOUND"

    issue = first.json()
    changed = client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "已更新", "version": issue["version"]},
    )
    assert changed.status_code == 200
    conflict = client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"title": "过期覆盖", "version": issue["version"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "ISSUE_VERSION_CONFLICT"


def test_issue_document_requires_matching_plain_text(client):
    response = client.post(
        "/api/v1/issues",
        json=issue_payload(
            description="文字 A",
            description_doc={
                "version": 1,
                "nodes": [{"type": "text", "text": "文字 B"}],
            },
        ),
    )
    assert response.status_code == 400
