"""Chính sách đích mạng cho connector (ADR-0023)."""

from __future__ import annotations

import pytest

from aiagent.bootstrap.container import PluginConnectorLookup, build_network_policy
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.bootstrap.settings import Settings
from aiagent.modules.audit.application import AuditService
from aiagent.modules.connections.application import ConnectionService
from aiagent.modules.connections.domain import ConnectionScope
from aiagent.modules.identity.domain import Principal, Role
from aiagent.plugin_sdk import BlockedEndpointError, Endpoint, NetworkPolicy
from aiagent.shared_kernel.errors import ForbiddenError, ValidationError
from tests.fakes import (
    FakeApiConnector,
    InMemoryAuditLog,
    InMemoryConnectionRepository,
    InMemoryVault,
    source_with,
)

DNS = {
    "redis": ["172.18.0.5"],
    "sneaky.corp.vn": ["172.18.0.5"],  # tên khác nhưng trỏ vào Redis của ARIES
    "erp.corp.vn": ["10.20.1.15"],
    "meta.corp.vn": ["169.254.169.254"],
    "lo.corp.vn": ["127.0.0.1"],
    "localhost": ["127.0.0.1"],
    "public.example.com": ["93.184.216.34"],
}


async def fake_resolver(host: str, port: int) -> list[str]:
    if host not in DNS:
        raise OSError("NXDOMAIN")
    return DNS[host]


def policy(**kw) -> NetworkPolicy:  # type: ignore[no-untyped-def]
    return NetworkPolicy.build(blocked_hosts=["redis", "postgres"], resolver=fake_resolver, **kw)


async def test_private_company_network_allowed() -> None:
    assert await policy().check(Endpoint("erp.corp.vn", 5432)) == ["10.20.1.15"]
    assert await policy().check(Endpoint("10.1.2.3", 9092)) == ["10.1.2.3"]


@pytest.mark.parametrize(
    ("host", "port", "message"),
    [
        ("redis", 6379, "nội bộ của ARIES"),
        ("REDIS.", 6379, "nội bộ của ARIES"),
        ("sneaky.corp.vn", 6379, "trỏ vào dịch vụ nội bộ"),
        ("172.18.0.5", 6379, "trỏ vào dịch vụ nội bộ"),
        ("localhost", 80, "loopback"),
        ("lo.corp.vn", 80, "loopback"),
        ("::1", 80, "loopback"),
        ("meta.corp.vn", 80, "không được phép"),
        ("169.254.169.254", 80, "không được phép"),
        ("0.0.0.0", 80, "không được phép"),  # noqa: S104 - đích kết nối, không phải bind
        ("224.0.0.1", 80, "không được phép"),
        ("nowhere.corp.vn", 80, "Không phân giải"),
        ("erp.corp.vn", 0, "Cổng"),
        ("", 80, "không hợp lệ"),
    ],
)
async def test_blocked_targets(host: str, port: int, message: str) -> None:
    with pytest.raises(BlockedEndpointError, match=message):
        await policy().check(Endpoint(host, port))


async def test_allowed_networks_and_loopback_opt_in() -> None:
    limited = policy(allowed_networks=["10.20.0.0/16"])
    assert await limited.check(Endpoint("erp.corp.vn", 443))
    with pytest.raises(BlockedEndpointError, match="ngoài các dải"):
        await limited.check(Endpoint("public.example.com", 443))
    assert await policy(allow_loopback=True).check(Endpoint("localhost", 5432)) == ["127.0.0.1"]


def test_build_policy_from_settings() -> None:
    s = Settings(
        env="test",
        redis_url="redis://cache-01:6379/0",
        connector_blocked_hosts=["vault.corp.vn"],
        connector_allowed_internal_hosts=["postgres"],
    )
    p = build_network_policy(s)
    assert {"cache-01", "vault.corp.vn", "openbao", "keycloak"} <= p.blocked_hosts
    assert "postgres" not in p.blocked_hosts  # ngoại lệ cho demo


# ---------------------------------------------------------------- ConnectionService
def _service(net: NetworkPolicy) -> tuple[ConnectionService, InMemoryConnectionRepository]:
    plugins = PluginRegistry(source=source_with(FakeApiConnector()))
    plugins.load_all()
    repo = InMemoryConnectionRepository()
    svc = ConnectionService(
        repo,
        InMemoryVault(),
        PluginConnectorLookup(plugins),
        AuditService(InMemoryAuditLog()),
        network=net,
    )
    return svc, repo


BUILDER = Principal(
    subject="b1", tenant_id="t1", username=None, email=None, roles=frozenset({Role.BUILDER})
)


async def test_service_blocks_internal_targets() -> None:
    svc, repo = _service(policy())
    with pytest.raises(ValidationError, match="nội bộ của ARIES"):
        await svc.create(
            BUILDER,
            name="Redis nội bộ",
            connector_id="fakeapi",
            scope=ConnectionScope.PERSONAL,
            config={"host": "redis", "port": 6379},
            secrets={},
        )

    # Tên máy hợp lệ lúc lưu nhưng phân giải vào hạ tầng ARIES → test báo lỗi, không gọi plugin
    c = await svc.create(
        BUILDER,
        name="Lách DNS",
        connector_id="fakeapi",
        scope=ConnectionScope.PERSONAL,
        config={"host": "sneaky.corp.vn"},
        secrets={},
    )
    tested = await svc.test(BUILDER, c.id)
    assert tested.last_test_ok is False and "bị chặn" in (tested.last_test_note or "")
    assert tested.tools == ()
    with pytest.raises(ForbiddenError, match="bị chặn"):
        await svc.open(BUILDER, str(c.id))
    with pytest.raises(ValidationError, match="bị chặn"):
        await svc.sync_tools(BUILDER, c.id)

    # Sửa cấu hình sang host bị chặn cũng bị từ chối
    with pytest.raises(ValidationError):
        await svc.update(BUILDER, c.id, config={"host": "postgres"})

    ok = await svc.create(
        BUILDER,
        name="ERP",
        connector_id="fakeapi",
        scope=ConnectionScope.PERSONAL,
        config={"host": "erp.corp.vn"},
        secrets={},
    )
    ctx = await svc.open(BUILDER, str(ok.id))
    assert ctx.network is not None and "redis" in ctx.network.blocked_hosts
    assert len(repo.items) == 2
