"""Real Linux NGINX/TLS/proxy smoke with isolated containers and an ephemeral test certificate."""

import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_QUERY_SENTINEL = "synthetic-private-search-text"


def verify_upload_admission(client, port, token, path, capacity):
    """Hold partial synthetic multipart bodies; overflow must fail before body parsing."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Disposable localhost certificate only.
    holders = []
    try:
        for _ in range(capacity):
            connection = context.wrap_socket(
                socket.create_connection(("127.0.0.1", port), timeout=10),
                server_hostname="localhost",
            )
            holders.append(connection)
            request = (
                f"POST {path} HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {token}\r\n"
                "Expect: 100-continue\r\n"
                "Content-Type: multipart/form-data; boundary=synthetic\r\nContent-Length: 1000000\r\n\r\n"
            )
            connection.sendall(request.encode("ascii"))
            # Synchronize with NGINX admission instead of racing unread TLS sockets.
            interim = b""
            while b"\r\n\r\n" not in interim and len(interim) < 8192:
                packet = connection.recv(4096)
                if not packet:
                    break
                interim += packet
            if interim.split(b"\r\n", 1)[0] != b"HTTP/1.1 100 Continue":
                raise AssertionError(
                    "Synthetic upload was rejected before occupying an ingress slot"
                )
            body = (
                '--synthetic\r\nContent-Disposition: form-data; name="audio"; filename="test.wav"\r\n'
                "Content-Type: audio/wav\r\n\r\nx"
            )
            connection.sendall(body.encode("ascii"))
        deadline = time.monotonic() + 5
        observed_statuses = []
        while time.monotonic() < deadline:
            response = client.post(
                path + "?q=" + PRIVATE_QUERY_SENTINEL,
                headers={"Authorization": "Bearer " + token},
                content=b"",
            )
            observed_statuses.append(response.status_code)
            if response.status_code == 429 and response.headers.get("content-type", "").startswith(
                "text/html"
            ):
                break
            time.sleep(0.1)
        else:
            raise AssertionError(
                f"NGINX did not bound active uploads before multipart parsing: {observed_statuses}"
            )
    finally:
        for connection in holders:
            connection.close()


def command(args, check=True, with_stderr=False):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError("Isolated Docker web check failed: " + result.stderr[-2000:])
    return (result.stdout + (result.stderr if with_stderr else "")).strip()


def run(api_only=False):
    project = "meeting-web-check-" + uuid4().hex[:10]
    api = project + "-api"
    web = project + "-web"
    backend = os.getenv("SMOKE_BACKEND_IMAGE", "meeting-api-backend:test")
    frontend = (
        os.getenv("SMOKE_GATEWAY_IMAGE", "meeting-api-gateway:test")
        if api_only
        else os.getenv("SMOKE_WEB_IMAGE", "meeting-web:test")
    )
    with tempfile.TemporaryDirectory(prefix="meeting-test-tls-") as temporary:
        root = Path(temporary)
        tls = root / "tls"
        # Bind only disposable certificates; non-root NGINX must traverse the mount.
        # Keep the parent and its environment file private to the test runner.
        tls.mkdir(mode=0o755)
        tls.chmod(0o755)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        (tls / "privkey.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (tls / "fullchain.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        # Test-only disposable cert; production uses the company's restricted certificate custody.
        for file in tls.iterdir():
            file.chmod(0o644)
        env = root / "test.env"
        token = "synthetic-web-smoke-" + "x" * 40
        env.write_text(
            "\n".join(
                [
                    "APP_ENV=test",
                    "AUTH_MODE=api_key",
                    "APP_API_TOKEN=",
                    "API_CLIENT_KEYS=" + json.dumps({"websmoke": token}),
                    "API_ADMIN_CLIENTS=[]",
                    "DATABASE_URL=sqlite:////srv/service/var/web-smoke.db",
                    "STORAGE_DIR=/srv/service/var/audio",
                    "AZURE_SPEECH_KEY=",
                    "LLM_API_KEY=",
                    "LLM_ENDPOINT=",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        try:
            command(["network", "create", project])
            command(
                [
                    "run",
                    "-d",
                    "--name",
                    api,
                    "--network",
                    project,
                    "--network-alias",
                    "api",
                    "--env-file",
                    str(env),
                    backend,
                ]
            )
            command(
                [
                    "run",
                    "-d",
                    "--name",
                    web,
                    "--network",
                    project,
                    "-p",
                    f"127.0.0.1:{port}:8443",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--tmpfs",
                    "/tmp:size=536870912,mode=1777",
                    "--mount",
                    f"type=bind,source={tls},target=/etc/meeting-tls,readonly",
                    frontend,
                ]
            )
            with httpx.Client(
                base_url=f"https://127.0.0.1:{port}", verify=False, timeout=15
            ) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("Isolated HTTPS proxy did not become ready")
                index = client.get("/")
                assert index.status_code == (404 if api_only else 200)
                for name in [
                    "content-security-policy",
                    "strict-transport-security",
                    "x-content-type-options",
                    "permissions-policy",
                ]:
                    assert name in index.headers
                if api_only:
                    assert "default-src 'none'" in index.headers["content-security-policy"]
                    assert client.get("/docs").status_code == 404
                    assert client.get("/api/openapi.json").status_code == 401
                    assert (
                        client.get(
                            "/api/openapi.json", headers={"Authorization": "Bearer " + token}
                        ).status_code
                        == 200
                    )
                else:
                    assert "script-src 'self'" in index.headers["content-security-policy"]
                    assert "max-age=31536000" not in index.headers.get("cache-control", "")
                    asset = re.search(r'src="(/assets/[^"]+\.js)"', index.text).group(1)
                    assert "max-age=31536000" in client.get(asset).headers["cache-control"]
                assert client.get("/api/meetings").status_code == 401
                assert client.get("/api/metrics").status_code == 404
                headers = {"Authorization": "Bearer " + token}
                assert client.get("/api/meetings", headers=headers).json() == []
                assert (
                    client.get(
                        "/api/storage",
                        headers={**headers, "X-Actor": "admin", "X-Forwarded-User": "admin"},
                    ).status_code
                    == 403
                )
                assert client.get("/api/storage/allowance", headers=headers).status_code == 200
                assert (
                    client.post(
                        "/api/meetings", headers=headers, content=b"x" * (8 * 1024**2 + 1)
                    ).status_code
                    == 413
                )
                assert (
                    client.post(
                        "/api/meetings/synthetic/audio-chunks",
                        headers=headers,
                        content=b"x" * (17 * 1024**2 + 1),
                    ).status_code
                    == 413
                )
                ready = client.get("/api/ready").json()
                assert ready["worker"] is False and ready["status"] == "degraded"
                verify_upload_admission(client, port, token, "/api/recaps/synthetic/audio", 4)
                verify_upload_admission(
                    client, port, token, "/api/meetings/synthetic/audio-chunks", 12
                )
            command(["exec", web, "nginx", "-t"])
            gateway_logs = command(["logs", web], with_stderr=True)
            assert PRIVATE_QUERY_SENTINEL not in gateway_logs, "Gateway logged a private query"
            assert token not in gateway_logs, "Gateway logged an authorization credential"
            assert '"event":"edge.completed"' in gateway_logs
            result = {
                "status": "PASS",
                "mode": "isolated Linux containers and self-signed local test TLS",
                "tls_headers": True,
                "hashed_asset_cache": not api_only,
                "api_only_no_spa": api_only,
                "same_origin_proxy_auth": True,
                "public_metrics_blocked": True,
                "untrusted_identity_headers_rejected": True,
                "json_and_audio_ingress_limits": True,
                "active_upload_admission_before_parsing": {"notes": 4, "chunks": 12},
                "nginx_config": "PASS",
                "redacted_gateway_logs": True,
                "backend_image_id": command(["image", "inspect", "--format", "{{.Id}}", backend]),
                "web_image_id": command(["image", "inspect", "--format", "{{.Id}}", frontend]),
                "company_tls_idp_oracle_qualification": False,
            }
            destination = ROOT / (
                "output/backend-readiness/gateway-smoke.json"
                if api_only
                else "output/implementation/web-image-smoke.json"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result))
        finally:
            command(["rm", "-f", web, api], False)
            command(["network", "rm", project], False)


if __name__ == "__main__":
    run()
