"""PaperJay's own HTTPS, so phones can use the live camera scanner (browsers only allow the camera on https).

At first start PaperJay makes a small certificate authority of its own (DATA_DIR/tls/ca.*) and, signed by
it, a certificate for this server. Each phone or computer trusts the authority once (Settings → Secure
address: a profile for iPhone, a certificate file for the rest); after that https://<server>:<HTTPS_PORT>
opens without warnings.

The authority can only vouch for home-network names: private IP addresses (10.x, 172.16-31.x, 192.168.x,
Tailscale's 100.64-127.x, 127.x, IPv6 fc00::/7 and fe80::/10) and names ending in .local, .lan, .home,
.home.arpa, .internal, .localdomain or .ts.net. So even if its key were stolen, it couldn't be used to fake
a real website to a device that trusts it.

The server certificate names this machine's host name and addresses, the names in HTTPS_NAMES, and the
addresses people open PaperJay at (learned when a signed-in person opens the Secure address page, so the
address Docker users type works too). It's renewed on its own well before it runs out; devices don't need
to trust anything again, because the authority stays the same.
"""
import datetime as dt
import ipaddress
import json
import logging
import plistlib
import re
import socket
import threading
import uuid
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger("paperjay")

NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", (0xC0A80000, 16),  # 192.168/16
                                          (0x64400000, 10),  # 100.64/10: Tailscale and other carrier-grade NAT
                                          "127.0.0.0/8", "169.254.0.0/16", "fc00::/7", "fe80::/10", "::1/128")]
SUFFIXES = ("local", "lan", "home", "home.arpa", "internal", "localdomain", "ts.net")
CA_YEARS = 20
LEAF_DAYS = 397          # what browsers accept for a server certificate
RENEW_DAYS = 60
MAX_NAMES = 40
_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


def allowed(name: str) -> str | None:
    """The name in canonical form if the authority may vouch for it (a home-network address), else None."""
    name = (name or "").strip().lower().rstrip(".")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    try:
        ip = ipaddress.ip_address(name)
        return str(ip) if any(ip in n for n in NETS if n.version == ip.version) else None
    except ValueError:
        pass
    if name == "localhost":
        return name
    if _NAME.match(name) and any(name.endswith("." + s) for s in SUFFIXES):
        return name
    return None


def host_of(host_header: str) -> str:
    """'192.168.1.5:8000' → '192.168.1.5', '[fe80::1]:443' → 'fe80::1', 'nas.local' → 'nas.local'."""
    h = (host_header or "").strip()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def own_names() -> set[str]:
    """This machine's host name and addresses, as far as it can tell."""
    names = {"localhost", "127.0.0.1"}
    host = socket.gethostname().lower()
    names |= {f"{host}.local", f"{host}.lan"} if "." not in host else {host}
    try:
        for info in socket.getaddrinfo(host, None):
            names.add(info[4][0].split("%")[0])
    except OSError:
        pass
    try:  # every address of this machine (Linux), e.g. its LAN and VPN (Tailscale) addresses
        lines = Path("/proc/net/fib_trie").read_text().splitlines()
        names |= {lines[i - 1].split()[-1] for i, line in enumerate(lines) if i and "/32 host LOCAL" in line}
    except OSError:
        pass
    try:  # the address used to reach the network (nothing is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            names.add(s.getsockname()[0])
    except OSError:
        pass
    return {n for n in (allowed(x) for x in names) if n}


def _san(names) -> x509.SubjectAlternativeName:
    out = []
    for n in sorted(names):
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            out.append(x509.DNSName(n))
    return x509.SubjectAlternativeName(out)


def _now():
    return dt.datetime.now(dt.timezone.utc)


class Https:
    def __init__(self, data_dir: Path, port: int, extra_names: str = ""):
        self.dir, self.port = Path(data_dir) / "tls", port
        self.extra = {n for n in (allowed(x) for x in re.split(r"[,\s]+", extra_names or "")) if n}
        self.ca_key, self.ca_crt = self.dir / "ca.key", self.dir / "ca.crt"
        self.key, self.crt = self.dir / "server.key", self.dir / "server.crt"
        self.names_file = self.dir / "names.json"
        self.on_change: list = []      # called after the server certificate is replaced (to reload it)
        self._lock = threading.Lock()

    # ---------- files ----------
    def _write(self, path: Path, data: bytes, secret: bool = False) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        if secret:
            tmp.chmod(0o600)
        tmp.replace(path)

    def learned(self) -> set[str]:
        try:
            return {n for n in (allowed(x) for x in json.loads(self.names_file.read_text())) if n}
        except (OSError, ValueError, TypeError):
            return set()

    def names(self) -> set[str]:
        return own_names() | self.extra | self.learned()

    def ca(self) -> x509.Certificate:
        return x509.load_pem_x509_certificate(self.ca_crt.read_bytes())

    def ca_der(self) -> bytes:
        return self.ca().public_bytes(serialization.Encoding.DER)

    def fingerprint(self) -> str:
        return self.ca().fingerprint(hashes.SHA256()).hex(":").upper()

    # ---------- making certificates ----------
    def _make_ca(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PaperJay"),
                          x509.NameAttribute(NameOID.COMMON_NAME, f"PaperJay {socket.gethostname()} {uuid.uuid4().hex[:6]}")])
        permitted = [x509.IPAddress(n) for n in NETS] + [x509.DNSName(s) for s in SUFFIXES] + [x509.DNSName("localhost")]
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=365 * CA_YEARS))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                             key_encipherment=False, data_encipherment=False, key_agreement=False,
                                             encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .sign(key, hashes.SHA256()))
        self._write(self.ca_key, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()), secret=True)
        self._write(self.ca_crt, cert.public_bytes(serialization.Encoding.PEM))
        log.info("made PaperJay's own certificate authority (%s)", self.ca_crt)

    def _make_leaf(self, names: set[str]) -> None:
        ca_key = serialization.load_pem_private_key(self.ca_key.read_bytes(), None)
        ca = self.ca()
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sorted(names, key=len)[-1][:64])]))
                .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=LEAF_DAYS))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                             data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                             crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(_san(names), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
        self._write(self.key, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()), secret=True)
        # the chain: server certificate, then the authority (so clients can match it to the one they trust)
        self._write(self.crt, cert.public_bytes(serialization.Encoding.PEM) + self.ca_crt.read_bytes())
        log.info("HTTPS certificate for %s", ", ".join(sorted(names)))

    def _leaf_ok(self, names: set[str]) -> bool:
        try:
            cert = x509.load_pem_x509_certificate(self.crt.read_bytes())
            have = {str(v) for v in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                    .get_values_for_type(x509.IPAddress)} | set(cert.extensions.get_extension_for_class(
                        x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
            return names <= have and cert.not_valid_after_utc - _now() > dt.timedelta(days=RENEW_DAYS) \
                and cert.issuer == self.ca().subject and self.key.exists()
        except (OSError, ValueError, x509.ExtensionNotFound):
            return False

    def ensure(self) -> tuple[Path, Path]:
        """Make or renew what's needed. Returns (certificate chain, key)."""
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            if not (self.ca_key.exists() and self.ca_crt.exists()):
                self._make_ca()
            names = self.names()
            if not self._leaf_ok(names):
                self._make_leaf(names)
                changed = True
            else:
                changed = False
        if changed:
            for f in self.on_change:
                try:
                    f()
                except Exception:
                    log.exception("couldn't reload the HTTPS certificate")
        return self.crt, self.key

    def learn(self, host_header: str) -> bool:
        """Remember an address PaperJay was opened at, so the certificate covers it. True if it was new."""
        name = allowed(host_of(host_header))
        if not name or name in self.names():
            return False
        learned = self.learned()
        if len(learned) >= MAX_NAMES:
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        self._write(self.names_file, json.dumps(sorted(learned | {name})).encode())
        self.ensure()
        return True

    # ---------- for devices ----------
    def mobileconfig(self) -> bytes:
        """An iPhone/iPad/Mac profile that installs the authority (then: Settings → General → About →
        Certificate Trust Settings → turn it on)."""
        ca = self.ca()
        cn = ca.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        ident = "app.paperjay.ca." + ca.fingerprint(hashes.SHA256()).hex()[:16]
        inner = {"PayloadType": "com.apple.security.root", "PayloadVersion": 1, "PayloadIdentifier": ident + ".cert",
                 "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, ident + ".cert")).upper(),
                 "PayloadDisplayName": cn, "PayloadCertificateFileName": "PaperJay.cer", "PayloadContent": self.ca_der()}
        outer = {"PayloadType": "Configuration", "PayloadVersion": 1, "PayloadIdentifier": ident,
                 "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, ident)).upper(), "PayloadDisplayName": "PaperJay",
                 "PayloadDescription": "Lets this device open your PaperJay over https without warnings, so the "
                                       "camera scanner works. It only covers home-network addresses.",
                 "PayloadOrganization": "PaperJay", "PayloadRemovalDisallowed": False, "PayloadContent": [inner]}
        return plistlib.dumps(outer)
