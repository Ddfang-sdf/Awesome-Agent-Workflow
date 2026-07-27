from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from conftest import DIFF, message, sync, upload_diff

from aaw_telemetry.logging import TextFormatter, configure_logging, request_id_var

LOG_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \[[A-Z]+\] \[[^\]]+\] "
)


def _flush_logs() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()
    for handler in logging.getLogger("aaw_telemetry.http.access").handlers:
        handler.flush()


def _lines(path: Path) -> list[str]:
    _flush_logs()
    return path.read_text(encoding="utf-8").splitlines()


def _event_line(path: Path, event: str) -> str:
    return next(line for line in reversed(_lines(path)) if f"event={event}" in line)


def test_text_formatter_matches_the_human_readable_contract():
    formatter = TextFormatter()
    record = logging.LogRecord(
        "aaw_telemetry.test",
        logging.INFO,
        __file__,
        1,
        "a text message",
        (),
        None,
    )
    record.status = "finalized_match"
    record.description = "two words"
    record.enabled = True

    rendered = formatter.format(record)

    assert LOG_PREFIX.match(rendered)
    assert "[INFO] [test] a text message" in rendered
    assert " | " in rendered
    assert "status=finalized_match" in rendered
    assert 'description="two words"' in rendered
    assert "enabled=true" in rendered


def test_formatter_includes_identity_fields_and_filters_secrets():
    formatter = TextFormatter()
    record = logging.LogRecord(
        "aaw_telemetry.telemetry.sync",
        logging.ERROR,
        __file__,
        1,
        "上报处理失败",
        (),
        None,
    )
    record.error_code = "EXPECTED"
    record.password = "must-not-be-logged"
    record.object_key = "step-diffs/private.diff"
    record.user_email = "developer@example.com"
    token = request_id_var.set("req-test")
    try:
        rendered = formatter.format(record)
    finally:
        request_id_var.reset(token)

    assert "request_id=req-test" in rendered
    assert "[telemetry.sync] 上报处理失败" in rendered
    assert "error_code=EXPECTED" in rendered
    assert "password" not in rendered
    assert "must-not-be-logged" not in rendered
    assert "object_key" not in rendered
    assert "private.diff" not in rendered
    assert "user_email=developer@example.com" in rendered


def test_formatter_escapes_message_line_breaks():
    formatter = TextFormatter()
    record = logging.LogRecord(
        "aaw_telemetry.test",
        logging.INFO,
        __file__,
        1,
        "first line\nforged line",
        (),
        None,
    )

    rendered = formatter.format(record)

    assert rendered.count("\n") == 0
    assert "first line\\nforged line" in rendered


def test_business_message_is_readable_and_traceable(client):
    payload = message()
    response = sync(client, payload)

    log_directory = client.app.state.log_directory
    line = _event_line(log_directory / "server.log", "telemetry.message_processed")

    assert LOG_PREFIX.match(line)
    assert "[telemetry.sync] 新的步骤上报已保存" in line
    assert f"message_id={payload['message_id']}" in line
    assert f"workflow_id={payload['workflow_id']}" in line
    assert f"request_id={response.headers['X-Request-ID']}" in line
    rendered = (log_directory / "server.log").read_text(encoding="utf-8")
    assert f"user_email={payload['user_email'].lower()}" in rendered
    assert f"user_name={payload['user_name']}" in rendered
    assert payload["data"]["file"]["sha256"] not in rendered


def test_service_start_is_logged_without_an_in_process_scheduler(client):
    log_directory = client.app.state.log_directory

    started = _event_line(log_directory / "server.log", "service.started")
    assert "version=0.1.0" in started
    assert "[system] Telemetry Server 已启动" in started
    rendered = (log_directory / "server.log").read_text(encoding="utf-8")
    assert "attribution.retry_scheduler" not in rendered


def test_workflow_consistency_failure_has_a_readable_business_event(client):
    first = message()
    assert sync(client, first).status_code == 200
    conflicting = message(
        message_id="33333333-3333-4333-8333-333333333333",
        repository="another/repository",
    )

    response = sync(client, conflicting)

    assert response.status_code == 400
    line = _event_line(
        client.app.state.log_directory / "server.log",
        "telemetry.message_rejected",
    )
    assert "error_code=INVALID_REQUEST" in line
    assert f"message_id={conflicting['message_id']}" in line
    assert f"user_email={conflicting['user_email'].lower()}" in line
    assert "事务已回滚" in line


def test_upload_success_and_failure_have_safe_audit_events(client):
    payload = message()
    sync(client, payload)
    upload_diff(client, payload)

    log_directory = client.app.state.log_directory
    confirmed = _event_line(log_directory / "server.log", "objects.upload_confirmed")
    assert "bytes_received=" in confirmed
    assert f"owner_id={payload['message_id']}" in confirmed
    assert "[objects.diff] Dev Patch 上传并校验成功" in confirmed

    failed_payload = message(
        message_id="33333333-3333-4333-8333-333333333333",
        workflow_id="44444444-4444-4444-8444-444444444444",
    )
    sync(client, failed_payload)
    response = client.put(
        f"/api/v1/objects/step-diffs/{failed_payload['message_id']}",
        content=b"wrong diff",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 422

    failed = _event_line(log_directory / "server.log", "objects.upload_failed")
    assert "error_code=FILE_HASH_MISMATCH" in failed
    assert "status_code=422" in failed
    rendered = (log_directory / "server.log").read_text(encoding="utf-8")
    assert payload["data"]["file"]["sha256"] not in rendered
    assert failed_payload["data"]["file"]["sha256"] not in rendered
    assert DIFF.decode() not in rendered
    assert "wrong diff" not in rendered


def test_dashboard_queries_only_write_access_log(client):
    payload = message()
    sync(client, payload)

    response = client.get(
        "/api/v1/dashboard/workflows",
        params={"user_email": payload["user_email"], "page": 1, "page_size": 10},
    )
    assert response.status_code == 200

    log_directory = client.app.state.log_directory
    access_line = _event_line(log_directory / "access.log", "http.request_completed")
    assert "[http.access] GET /api/v1/dashboard/workflows 返回 200" in access_line
    assert "status_code=200" in access_line
    assert "dashboard.query_completed" not in (
        log_directory / "server.log"
    ).read_text(encoding="utf-8")


def test_request_and_error_logs_are_written_to_separate_files(client):
    assert (client.app.state.log_directory / "access.log").is_file()
    assert (client.app.state.log_directory / "error.log").is_file()
    response = client.get("/health/live")
    logging.getLogger("aaw_telemetry.test").error(
        "测试预期错误",
        extra={
            "event": "test.expected_error",
            "error_code": "EXPECTED",
            "password": "must-not-be-logged",
        },
    )

    log_directory = client.app.state.log_directory
    request = _event_line(log_directory / "access.log", "http.request_completed")
    error = _event_line(log_directory / "error.log", "test.expected_error")
    assert f"request_id={response.headers['X-Request-ID']}" in request
    assert "status_code=200" in request
    assert "http.request_completed" not in (
        log_directory / "server.log"
    ).read_text(encoding="utf-8")
    assert "error_code=EXPECTED" in error
    assert "password" not in error
    assert "must-not-be-logged" not in (
        log_directory / "error.log"
    ).read_text(encoding="utf-8")


def test_exception_traceback_is_rendered_on_following_lines():
    formatter = TextFormatter()
    try:
        raise RuntimeError("expected failure")
    except RuntimeError:
        record = logging.LogRecord(
            "aaw_telemetry.test",
            logging.ERROR,
            __file__,
            1,
            "test.exception",
            (),
            exc_info=sys.exc_info(),
        )

    rendered = formatter.format(record)

    first_line, traceback = rendered.split("\n", 1)
    assert LOG_PREFIX.match(first_line)
    assert "[ERROR] [test] test.exception" in first_line
    assert "Traceback (most recent call last):" in traceback
    assert "RuntimeError: expected failure" in traceback


def test_server_log_rotates_to_gzip(tmp_path):
    log_directory = configure_logging(
        Path("config/logging.yaml"),
        level="INFO",
        directory_override=tmp_path / "rotating-logs",
    )
    handler = next(
        item for item in logging.getLogger().handlers if item.name == "server_file"
    )
    handler.clh.maxBytes = 512
    handler.backupCount = 3

    logger = logging.getLogger("aaw_telemetry.rotation_test")
    for index in range(40):
        logger.info("rotation.test", extra={"index": index, "padding": "x" * 80})
    handler.flush()

    assert (log_directory / "server.log").is_file()
    assert list(log_directory.glob("server.log*.gz"))
