"""OAuth PKCE flow for Qlik Cloud MCP."""

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
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def _verifier() -> str:
    return secrets.token_urlsafe(48)

def _challenge(v: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# Shared State
# ---------------------------------------------------------------------------

@dataclass
class PendingOAuth:
    tenant_url: str
    client_id: str
    code_verifier: str
    redirect_uri: str
    created_at: float = field(default_factory=time.time)

# Pending flows keyed by state token
pending_flows: dict[str, PendingOAuth] = {}

# Completed flows: state -> access_token (polled by JS)
completed_tokens: dict[str, dict] = {}

# Pending MCP connections: session_id -> {access_token, tenant_url, client_id}
# Written by /auth/qlik/connect, read by app.py on_message
pending_connections: dict[str, dict] = {}


def cleanup():
    now = time.time()
    for store in (pending_flows, completed_tokens):
        for k in [k for k, v in store.items() if now - (v.created_at if hasattr(v, 'created_at') else v.get('t', 0)) > 600]:
            del store[k]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_oauth_routes(app):
    from fastapi import APIRouter
    oauth_router = APIRouter()

    @oauth_router.get("/auth/qlik/status")
    async def status(request: Request):
        """Polled by the JS to check if OAuth completed. Returns token when ready."""
        state = request.query_params.get("state", "")
        from fastapi.responses import JSONResponse
        if state in completed_tokens:
            token_data = completed_tokens.pop(state)
            return JSONResponse({
                "complete": True,
                "access_token": token_data["access_token"],
                "tenant_url": token_data["tenant_url"],
                "client_id": token_data["client_id"],
            })
        return JSONResponse({"complete": False})

    @oauth_router.get("/auth/qlik/defaults")
    async def defaults(request: Request):
        """Returns default Qlik credentials from env vars for the JS form."""
        from fastapi.responses import JSONResponse
        return JSONResponse({
            "tenant_url": os.getenv("QLIK_TENANT_URL", ""),
            "client_id": os.getenv("QLIK_OAUTH_CLIENT_ID", ""),
        })

    @oauth_router.post("/auth/qlik/connect")
    async def connect(request: Request):
        """Called by JS after OAuth completes. Stores token for the session to pick up."""
        from fastapi.responses import JSONResponse
        try:
            body = await request.json()
            access_token = body.get("access_token", "")
            tenant_url = body.get("tenant_url", "")
            client_id = body.get("client_id", "")
            session_id = body.get("session_id", "")

            if not access_token or not tenant_url:
                return JSONResponse({"error": "Missing token or tenant_url"}, 400)

            # Store in pending_connections for the app to pick up
            pending_connections[session_id or "default"] = {
                "access_token": access_token,
                "tenant_url": tenant_url,
                "client_id": client_id,
            }
            logger.info(f"Stored pending MCP connection for session {session_id[:8] if session_id else 'default'}...")
            return JSONResponse({"ok": True})
        except Exception as e:
            logger.error(f"Connect endpoint error: {e}")
            return JSONResponse({"error": str(e)}, 500)

    @oauth_router.get("/auth/qlik/start")
    async def start(request: Request):
        tenant_url = request.query_params.get("tenant_url", "")
        client_id = request.query_params.get("client_id", "")
        state = request.query_params.get("state", "")

        if not tenant_url or not client_id or not state:
            return HTMLResponse("<h2>Missing parameters</h2>", 400)

        cleanup()
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
        return RedirectResponse(url)

    @oauth_router.get("/auth/qlik/callback")
    async def callback(request: Request):
        error = request.query_params.get("error")
        if error:
            return HTMLResponse(_page("Authentication Failed", html_mod.escape(request.query_params.get("error_description", error)), False))

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        pending = pending_flows.pop(state, None)

        if not pending:
            return HTMLResponse(_page("Session Expired", "Please try connecting again from the chat.", False), 400)

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
                return HTMLResponse(_page("Token Exchange Failed", html_mod.escape(str(e)), False))

        # Store completed token for polling by app.py
        completed_tokens[state] = {
            "access_token": data["access_token"],
            "tenant_url": pending.tenant_url,
            "client_id": pending.client_id,
            "t": time.time(),
        }

        logger.info("OAuth completed, token stored for polling")
        return HTMLResponse(_page("Connected to Qlik Cloud", "You can close this tab and return to the chat.", True))

    # Insert our routes BEFORE Chainlit's catch-all "/{full_path:path}" route
    # by including the router at index 0 of app.routes
    from starlette.routing import Route
    oauth_routes = oauth_router.routes
    for route in reversed(oauth_routes):
        app.routes.insert(0, route)
    logger.info(f"Registered {len(oauth_routes)} OAuth routes")


def _page(title, msg, ok):
    c = "#009845" if ok else "#d32f2f"
    i = "&#10003;" if ok else "&#10007;"
    return f"""<!DOCTYPE html><html><head><title>{title}</title>
<style>body{{font-family:'Source Sans 3',sans-serif;background:#0f1a24;color:#e0e0e0;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}}
.c{{text-align:center;padding:40px}}.i{{font-size:64px;color:{c}}};h1{{color:{c}}}p{{color:#a0a0a0}}
.b{{margin-top:20px;padding:10px 24px;background:{c};color:white;border:none;border-radius:6px;cursor:pointer}}</style></head>
<body><div class="c"><div class="i">{i}</div><h1>{title}</h1><p>{msg}</p>
<button class="b" onclick="window.close()">Close this tab</button></div>
<script>{"setTimeout(()=>window.close(),3000)" if ok else ""}</script></body></html>"""
