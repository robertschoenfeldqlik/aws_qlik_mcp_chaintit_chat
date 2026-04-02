"""OAuth PKCE flow for Qlik Cloud MCP."""

import base64
import hashlib
import html
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def generate_code_verifier() -> str:
    return secrets.token_urlsafe(48)

def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# State Store
# ---------------------------------------------------------------------------

@dataclass
class PendingOAuth:
    session_id: str
    tenant_url: str
    client_id: str
    code_verifier: str
    redirect_uri: str
    created_at: float = field(default_factory=time.time)

_pending: dict[str, PendingOAuth] = {}

def _cleanup():
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v.created_at > 600]:
        del _pending[k]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_oauth_routes(app):
    """Register /auth/qlik/start and /auth/qlik/callback on FastAPI app."""

    @app.get("/auth/qlik/start")
    async def start(request: Request):
        session_id = request.query_params.get("session_id", "")
        tenant_url = request.query_params.get("tenant_url", "")
        client_id = request.query_params.get("client_id", "")

        if not tenant_url or not client_id:
            return HTMLResponse("<h2>Missing tenant_url or client_id</h2>", 400)

        _cleanup()
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        state = secrets.token_urlsafe(32)
        base_url = os.getenv("APP_BASE_URL", "http://localhost:8000")
        redirect_uri = f"{base_url}/auth/qlik/callback"

        _pending[state] = PendingOAuth(
            session_id=session_id, tenant_url=tenant_url,
            client_id=client_id, code_verifier=verifier,
            redirect_uri=redirect_uri,
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
        return RedirectResponse(url)

    @app.get("/auth/qlik/callback")
    async def callback(request: Request):
        error = request.query_params.get("error")
        if error:
            desc = request.query_params.get("error_description", error)
            return HTMLResponse(_page("Authentication Failed", html.escape(desc), False))

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        pending = _pending.pop(state, None)

        if not pending:
            return HTMLResponse(_page("Session Expired", "Please try connecting again.", False), 400)

        # Exchange code for token
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
                return HTMLResponse(_page("Token Exchange Failed", html.escape(str(e)), False))

        # Store token in Chainlit session
        try:
            from chainlit.user_session import user_sessions
            if pending.session_id in user_sessions:
                user_sessions[pending.session_id]["qlik_access_token"] = data["access_token"]
                user_sessions[pending.session_id]["qlik_tenant_url"] = pending.tenant_url
                user_sessions[pending.session_id]["qlik_client_id"] = pending.client_id
                user_sessions[pending.session_id]["oauth_complete"] = True
                logger.info("OAuth tokens stored in session")
            else:
                return HTMLResponse(_page("Session Expired", "Chat session not found. Refresh and try again.", False))
        except Exception as e:
            logger.error(f"Failed to store token: {e}")
            return HTMLResponse(_page("Error", html.escape(str(e)), False))

        return HTMLResponse(_page("Connected to Qlik Cloud", "You can close this tab and return to the chat.", True))


def _page(title: str, message: str, success: bool) -> str:
    color = "#009845" if success else "#d32f2f"
    icon = "&#10003;" if success else "&#10007;"
    return f"""<!DOCTYPE html><html><head><title>{title}</title>
<style>body{{font-family:'Source Sans 3',sans-serif;background:#0f1a24;color:#e0e0e0;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}}
.c{{text-align:center;padding:40px}}.i{{font-size:64px;color:{color};margin-bottom:20px}}h1{{color:{color};font-size:24px}}p{{color:#a0a0a0}}
.b{{display:inline-block;margin-top:20px;padding:10px 24px;background:{color};color:white;border:none;border-radius:6px;cursor:pointer;text-decoration:none}}</style></head>
<body><div class="c"><div class="i">{icon}</div><h1>{title}</h1><p>{message}</p>
<button class="b" onclick="window.close()">Close this tab</button></div>
<script>{"setTimeout(()=>window.close(),3000)" if success else ""}</script></body></html>"""
