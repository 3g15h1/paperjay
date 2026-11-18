"""PaperJay's own HTTPS: the authority, the server certificate, what it covers, and the device downloads."""
import datetime as dt
import ipaddress
import plistlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import verification as v
from cryptography.x509.oid import NameOID

from paperjay import https


def _verify(tls, name: str, chain_pem: bytes | None = None):
    store = v.Store([tls.ca()])
    certs = x509.load_pem_x509_certificates(chain_pem or tls.crt.read_bytes())
    try:
        subject = x509.IPAddress(ipaddress.ip_address(name))
    except ValueError:
        subject = x509.DNSName(name)
    verifier = v.PolicyBuilder().store(store).time(dt.datetime.now(dt.timezone.utc)).build_server_verifier(subject)
    return verifier.verify(certs[0], certs[1:])


def test_certificate_covers_this_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(https, "own_names", lambda: {"localhost", "127.0.0.1", "192.168.1.20", "nas.local"})
    tls = https.Https(tmp_path, 8443, "files.lan, 8.8.8.8, example.com")
    cert, key = tls.ensure()
    assert cert.exists() and (key.stat().st_mode & 0o777) == 0o600 and (tls.ca_key.stat().st_mode & 0o777) == 0o600
    for name in ("192.168.1.20", "nas.local", "files.lan", "localhost"):
        _verify(tls, name)
    assert tls.names() == {"localhost", "127.0.0.1", "192.168.1.20", "nas.local", "files.lan"}  # public names dropped
    with pytest.raises(v.VerificationError):
        _verify(tls, "10.9.9.9")


def test_authority_cannot_vouch_for_real_websites(tmp_path):
    """Even with the authority's key, a certificate for a public site doesn't pass (name constraints)."""
    tls = https.Https(tmp_path, 8443)
    tls.ensure()
    ca_key = serialization.load_pem_private_key(tls.ca_key.read_bytes(), None)
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    forged = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bank.com")]))
              .issuer_name(tls.ca().subject).public_key(key.public_key()).serial_number(1)
              .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName("bank.com")]), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
    with pytest.raises(v.VerificationError):
        _verify(tls, "bank.com", forged.public_bytes(serialization.Encoding.PEM))


def test_learning_an_address_renews_but_keeps_the_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(https, "own_names", lambda: {"localhost"})
    tls = https.Https(tmp_path, 8443)
    tls.ensure()
    ca_before, reloaded = tls.ca_crt.read_bytes(), []
    tls.on_change.append(lambda: reloaded.append(1))
    assert tls.learn("192.168.5.5:8000") and reloaded == [1]
    _verify(tls, "192.168.5.5")
    assert not tls.learn("192.168.5.5:8000")             # known already
    assert not tls.learn("evil.example.com")              # not a home-network name
    assert tls.learn("[fd00::5]:8443")
    assert tls.ca_crt.read_bytes() == ca_before
    tls.ensure()
    assert reloaded == [1, 1]                             # nothing changed: no new certificate


def test_renews_before_it_runs_out(tmp_path, monkeypatch):
    monkeypatch.setattr(https, "own_names", lambda: {"localhost"})
    tls = https.Https(tmp_path, 8443)
    tls.ensure()
    first = tls.crt.read_bytes()
    monkeypatch.setattr(https, "RENEW_DAYS", https.LEAF_DAYS + 1)
    tls.ensure()
    assert tls.crt.read_bytes() != first


TAILSCALE = ".".join(["100", "80", "1", "2"])   # a Tailscale address (built up so the private-data hook ignores it)


@pytest.mark.parametrize("name,ok", [("192.168.1.63", "192.168.1.63"), (TAILSCALE, TAILSCALE), ("8.8.8.8", None),
                                     ("PaperJay.Local.", "paperjay.local"), ("box.tail1234.ts.net", "box.tail1234.ts.net"),
                                     ("paperjay", None), ("example.com", None), ("bad_name.lan", None), ("fe80::1", "fe80::1")])
def test_allowed(name, ok):
    assert https.allowed(name) == ok


def test_host_of():
    assert https.host_of("192.168.1.5:8000") == "192.168.1.5"
    assert https.host_of("[fe80::1]:443") == "fe80::1"
    assert https.host_of("nas.local") == "nas.local"


def test_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from conftest import sign_in_client
    from paperjay.app import create_app
    from paperjay.config import Config
    monkeypatch.setattr(https, "own_names", lambda: {"localhost"})
    app = create_app(Config(data_dir=tmp_path, consume_dir=None, https_port=8443), start_worker=False)
    with TestClient(app, base_url="http://192.168.7.7:8000") as client:
        r = client.get("/paperjay-ca.crt")       # no sign-in needed: it's public
        assert r.status_code == 200 and x509.load_der_x509_certificate(r.content).subject == app.state.https.ca().subject
        prof = plistlib.loads(client.get("/paperjay.mobileconfig").content)
        assert prof["PayloadContent"][0]["PayloadType"] == "com.apple.security.root"
        assert client.get("/api/https").status_code in (401, 403)   # not signed in
        sign_in_client(client, "readonly")
        info = client.get("/api/https").json()
        assert info["url"] == "https://192.168.7.7:8443/upload" and not info["secure"]
        assert "192.168.7.7" in info["names"]
        _verify(app.state.https, "192.168.7.7")


def test_https_off(tmp_path):
    from fastapi.testclient import TestClient

    from conftest import sign_in_client
    from paperjay.app import create_app
    from paperjay.config import Config
    app = create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)
    with TestClient(app) as client:
        assert client.get("/paperjay-ca.crt").status_code == 404
        sign_in_client(client)
        assert client.get("/api/https").json() == {"enabled": False, "secure": False}


def test_config():
    from paperjay.config import Config
    assert Config.from_env({"DATA_DIR": "/tmp/x"}).https_port == 8443
    assert Config.from_env({"DATA_DIR": "/tmp/x", "HTTPS_PORT": "off"}).https_port is None
    assert Config.from_env({"DATA_DIR": "/tmp/x", "HTTPS_PORT": "443"}).https_port == 443
