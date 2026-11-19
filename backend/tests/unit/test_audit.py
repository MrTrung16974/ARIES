from __future__ import annotations

from dataclasses import replace

from aiagent.modules.audit.application import AuditService, redact
from aiagent.modules.audit.domain import AuditEvent, AuditRecord, Outcome, find_broken_link
from tests.fakes import InMemoryAuditLog


def test_redact_masks_sensitive_keys_recursively() -> None:
    data = {
        "user": "alice",
        "password": "p",
        "nested": {"api_key": "k", "Authorization": "Bearer x", "ok": 1},
        "items": [{"refresh_token": "t"}, "plain"],
    }
    assert redact(data) == {
        "user": "alice",
        "password": "***",
        "nested": {"api_key": "***", "Authorization": "***", "ok": 1},
        "items": [{"refresh_token": "***"}, "plain"],
    }


async def test_service_records_redacted_details_and_chain_is_intact() -> None:
    log = InMemoryAuditLog()
    svc = AuditService(log)
    for i in range(3):
        await svc.record(
            tenant_id="default",
            actor_id="u1",
            action="test.action",
            outcome=Outcome.SUCCESS,
            details={"i": i, "token": "secret"},
        )
    assert log.records[0].event.details == {"i": 0, "token": "***"}
    status = await svc.verify_chain("default")
    assert status.intact
    assert status.records_checked == 3


async def test_tampering_is_detected() -> None:
    log = InMemoryAuditLog()
    svc = AuditService(log)
    for action in ("a", "b", "c"):
        await svc.record(tenant_id="default", actor_id="u", action=action, outcome=Outcome.SUCCESS)

    tampered = log.records[1]
    log.records[1] = replace(tampered, event=replace(tampered.event, action="hacked"))
    assert find_broken_link(log.records) == 2

    log.records[1] = tampered
    del log.records[1]  # xóa bản ghi giữa chuỗi
    assert find_broken_link(log.records) == 3


async def test_record_safely_swallows_errors() -> None:
    class Broken(InMemoryAuditLog):
        async def append(self, event: AuditEvent) -> AuditRecord:
            raise RuntimeError("db down")

    await AuditService(Broken()).record_safely(
        tenant_id="t", actor_id=None, action="x", outcome=Outcome.FAILURE
    )
