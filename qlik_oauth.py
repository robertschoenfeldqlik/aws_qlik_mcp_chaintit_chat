"""OAuth PKCE flow for Qlik Cloud MCP (streamable-http transport).

Matches the working LibreChat configuration:
- Transport: streamable-http
- Header: X-Agent-Id
- OAuth: Authorization Code + PKCE (S256)
- Scopes: user_default mcp:execute
- No client secret (public/native client)
"""

import base64
import hashlib
import html as html_mod
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from loguru import logger


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def _verifier() -> str:
    return secrets.token_urlsafe(48)

def _challenge(v: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# State Stores
# ---------------------------------------------------------------------------

@dataclass
class PendingOAuth:
    tenant_url: str
    client_id: str
    code_verifier: str
    redirect_uri: str
    created_at: float = field(default_factory=time.time)

pending_flows: dict[str, PendingOAuth] = {}
completed_tokens: dict[str, dict] = {}

def _cleanup():
    now = time.time()
    for store in (pending_flows, completed_tokens):
        for k in [k for k, v in store.items()
                   if now - (v.created_at if hasattr(v, 'created_at') else v.get('t', 0)) > 600]:
            del store[k]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_oauth_routes(app):
    router = APIRouter()

    @router.get("/auth/qlik/defaults")
    async def defaults(request: Request):
        return JSONResponse({
            "tenant_url": os.getenv("QLIK_TENANT_URL", ""),
            "client_id": os.getenv("QLIK_OAUTH_CLIENT_ID", ""),
        })

    @router.get("/auth/qlik/status")
    async def status(request: Request):
        state = request.query_params.get("state", "")
        if state in completed_tokens:
            token_data = completed_tokens.pop(state)
            return JSONResponse({
                "complete": True,
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token", ""),
                "tenant_url": token_data["tenant_url"],
                "client_id": token_data["client_id"],
            })
        return JSONResponse({"complete": False})

    @router.post("/auth/qlik/connect")
    async def connect(request: Request):
        """Called by JS after OAuth — stores token for app.py to pick up."""
        try:
            body = await request.json()
            key = body.get("session_id", "default")
            pending_connections[key] = {
                "access_token": body["access_token"],
                "tenant_url": body["tenant_url"],
                "client_id": body["client_id"],
            }
            logger.info(f"Stored pending MCP connection")
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, 500)

    @router.get("/auth/qlik/start")
    async def start(request: Request):
        tenant_url = request.query_params.get("tenant_url", "")
        client_id = request.query_params.get("client_id", "")
        state = request.query_params.get("state", "")

        if not tenant_url or not client_id or not state:
            return HTMLResponse("<h2>Missing parameters</h2>", 400)

        _cleanup()
        verifier = _verifier()
        challenge = _challenge(verifier)
        base_url = os.getenv("APP_BASE_URL", "http://localhost:8000")
        redirect_uri = f"{base_url}/auth/qlik/callback"

        pending_flows[state] = PendingOAuth(
            tenant_url=tenant_url, client_id=client_id,
            code_verifier=verifier, redirect_uri=redirect_uri,
        )

        url = f"{tenant_url.rstrip('/')}/oauth/authorize?" + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "user_default mcp:execute",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        logger.info(f"OAuth redirect to {tenant_url}")
        return RedirectResponse(url)

    @router.get("/auth/qlik/callback")
    async def callback(request: Request):
        error = request.query_params.get("error")
        if error:
            return HTMLResponse(_page("Authentication Failed",
                html_mod.escape(request.query_params.get("error_description", error)), False))

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        pending = pending_flows.pop(state, None)

        if not pending:
            return HTMLResponse(_page("Session Expired",
                "Please try connecting again from the chat.", False), 400)

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{pending.tenant_url.rstrip('/')}/oauth/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": pending.redirect_uri,
                        "client_id": pending.client_id,
                        "code_verifier": pending.code_verifier,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"Token exchange failed: {e}")
                return HTMLResponse(_page("Token Exchange Failed",
                    html_mod.escape(str(e)), False))

        completed_tokens[state] = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "tenant_url": pending.tenant_url,
            "client_id": pending.client_id,
            "t": time.time(),
        }

        logger.info("OAuth completed, token stored for polling")
        return HTMLResponse(_page("Connected to Qlik Cloud",
            "You can close this tab and return to the chat.", True))

    # Insert routes before Chainlit's catch-all
    for route in reversed(router.routes):
        app.routes.insert(0, route)
    logger.info(f"Registered {len(router.routes)} OAuth routes")


# Pending MCP connections: written by /auth/qlik/connect, read by app.py
pending_connections: dict[str, dict] = {}


def _page(title, msg, ok):
    c = "#009845" if ok else "#d32f2f"
    i = "&#10003;" if ok else "&#10007;"
    return f"""<!DOCTYPE html><html><head><title>{title}</title>
<style>body{{font-family:'Source Sans 3',sans-serif;background:#0f1a24;color:#e0e0e0;display:flex;
justify-content:center;align-items:center;min-height:100vh;margin:0}}
.c{{text-align:center;padding:40px}}.i{{font-size:64px;color:{c}}}
h1{{color:{c}}}p{{color:#a0a0a0}}
.b{{margin-top:20px;padding:10px 24px;background:{c};color:white;border:none;border-radius:6px;cursor:pointer}}
</style></head>
<body><div class="c"><div class="i">{i}</div><h1>{title}</h1><p>{msg}</p>
<button class="b" onclick="window.close()">Close this tab</button></div>
<script>{"setTimeout(()=>window.close(),3000)" if ok else ""}</script></body></html>"""
