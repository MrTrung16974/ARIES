"""TLS dùng chung cho connector: CA riêng + mTLS (ADR-0023)."""

from __future__ import annotations

import datetime as dt
import ipaddress
import ssl
from collections.abc import Iterator
from concurrent import futures
from typing import Any

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from grpc_reflection.v1alpha import reflection

from aiagent.plugin_sdk import ConnectionContext
from aiagent.plugin_sdk.tls import TlsConfigError, material, ssl_context
from aiagent_plugins.connector_grpc import GrpcConnector


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _pem_key(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _cert(
    subject: str,
    key: ec.EllipticCurvePrivateKey,
    issuer: tuple[x509.Name, ec.EllipticCurvePrivateKey] | None = None,
    *,
    ca: bool = False,
    san: bool = False,
) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    issuer_name, signer = issuer or (name, key)
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
    return builder.sign(signer, hashes.SHA256())


def _pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="module")
def pki() -> dict[str, str]:
    ca_key = _key()
    ca = _cert("ARIES test CA", ca_key, ca=True)
    issuer = (ca.subject, ca_key)
    server_key, client_key, other_key = _key(), _key(), _key()
    return {
        "ca": _pem(ca),
        "server_cert": _pem(_cert("localhost", server_key, issuer, san=True)),
        "server_key": _pem_key(server_key),
        "client_cert": _pem(_cert("aries-agent", client_key, issuer)),
        "client_key": _pem_key(client_key),
        "other_key": _pem_key(other_key),
    }


def test_ssl_context_and_validation(pki: dict[str, str]) -> None:
    cfg = {"ca_cert": pki["ca"], "client_cert": pki["client_cert"]}
    ctx = ssl_context(cfg, {"client_key": pki["client_key"]})
    assert ctx.verify_mode is ssl.CERT_REQUIRED and ctx.check_hostname
    assert not ssl_context({}, {}, verify=False).check_hostname

    with pytest.raises(TlsConfigError, match="không khớp"):
        ssl_context(cfg, {"client_key": pki["other_key"]})
    with pytest.raises(TlsConfigError, match="cả chứng chỉ client"):
        material({"client_cert": pki["client_cert"]}, {})
    with pytest.raises(TlsConfigError, match="PEM"):
        material({"ca_cert": "not a cert"}, {})


@pytest.fixture
def mtls_server(pki: dict[str, str]) -> Iterator[str]:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    reflection.enable_server_reflection((reflection.SERVICE_NAME,), server)
    creds = grpc.ssl_server_credentials(
        [(pki["server_key"].encode(), pki["server_cert"].encode())],
        root_certificates=pki["ca"].encode(),
        require_client_auth=True,
    )
    port = server.add_secure_port("localhost:0", creds)
    server.start()
    yield f"localhost:{port}"
    server.stop(None)


def _conn(target: str, config: dict[str, Any], secrets: dict[str, str]) -> ConnectionContext:
    cfg = {"target": target, "tls": True, "timeout_s": 3, **config}
    return ConnectionContext("g", "mTLS", "grpc", cfg, secrets)


async def test_grpc_mtls(mtls_server: str, pki: dict[str, str]) -> None:
    plugin = GrpcConnector()
    ok = await plugin.test_connection(
        _conn(
            mtls_server,
            {"ca_cert": pki["ca"], "client_cert": pki["client_cert"]},
            {"client_key": pki["client_key"]},
        )
    )
    assert ok.healthy, ok.detail

    # Thiếu chứng chỉ client → máy chủ từ chối bắt tay TLS
    no_client = await GrpcConnector().test_connection(
        _conn(mtls_server, {"ca_cert": pki["ca"], "timeout_s": 1}, {})
    )
    assert not no_client.healthy
    # Không có CA riêng → không tin chứng chỉ máy chủ
    no_ca = await GrpcConnector().test_connection(
        _conn(
            mtls_server,
            {"client_cert": pki["client_cert"], "timeout_s": 1},
            {"client_key": pki["client_key"]},
        )
    )
    assert not no_ca.healthy
