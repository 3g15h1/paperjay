"""The secure address: certificate downloads for devices, and what the Secure address page shows.

    GET /paperjay-ca.crt          the authority, for Android, Windows, Linux and as a plain file (open: it's public)
    GET /paperjay.mobileconfig    the same as a profile for iPhone, iPad and Mac (open)
    GET /api/https                {enabled, port, url, secure, fingerprint, names}; also learns this address
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .https import allowed, host_of

router = APIRouter()


def _https(request: Request):
    tls = request.app.state.https
    if tls is None:
        raise HTTPException(404, "PaperJay's own HTTPS is off (HTTPS_PORT=off)")
    tls.ensure()
    return tls


@router.get("/paperjay-ca.crt", include_in_schema=False)
def ca_file(request: Request):
    return Response(_https(request).ca_der(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="PaperJay.crt"'})


@router.get("/paperjay.mobileconfig", include_in_schema=False)
def ca_profile(request: Request):
    return Response(_https(request).mobileconfig(), media_type="application/x-apple-aspen-config",
                    headers={"Content-Disposition": 'attachment; filename="PaperJay.mobileconfig"'})


@router.get("/api/https")
def info(request: Request):
    tls = request.app.state.https
    secure = request.url.scheme == "https"
    if tls is None:
        return {"enabled": False, "secure": secure}
    host = request.headers.get("host", "")
    name = host_of(host)
    tls.learn(host)   # the address this person types: make sure the certificate covers it
    usable = allowed(name) is not None
    shown = f"[{name}]" if ":" in name else name
    port = "" if tls.port == 443 else f":{tls.port}"
    return {"enabled": True, "secure": secure, "port": tls.port, "usable": usable,
            "url": f"https://{shown}{port}/upload" if usable else None,
            "fingerprint": tls.fingerprint(), "names": sorted(tls.names())}
