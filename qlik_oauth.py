"""OAuth Authorization Code + PKCE flow for Qlik Cloud MCP.

Implements the same OAuth flow that Claude.ai and ChatGPT use to connect
to Qlik MCP servers. The user authenticates in the browser, approves data
sharing, and the app receives a Bearer token for the MCP SSE connection.

Ref: https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm
"""

import asyncio
import base64
import hashlib
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
# PKCE Utilities
# ---------------------------------------------------------------------------


def generate_code_verifier() -> str:
    """Generate a cryptographically random PKCE code verifier (43-128 chars)."""
    return secrets.token_urlsafe(48)


def generate_code_challenge(verifier: str) -> str:
    """Compute S256 code challenge from verifier per RFC 7636."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class PendingOAuth:
    """Tracks an in-progress OAuth flow."""
    session_id: str
    tenant_url: str
    client_id: str
    code_verifier: str
    redirect_uri: str
    created_at: float = field(default_factory=time.time)


@dataclass
class QlikTokens:
    """Stores OAuth tokens for a Qlik Cloud session."""
    access_token: str
    refresh_token: Optional[str]
    expires_at: float  # time.time() + expires_in
    tenant_url: str
    client_id: str


# ---------------------------------------------------------------------------
# Pending OAuth State Store
# ---------------------------------------------------------------------------

_pending_oauth: dict[str, PendingOAuth] = {}
_PENDING_TTL = 600  # 10 minutes


def _cleanup_expired():
    """Remove expired pending OAuth entries."""
    now = time.time()
    expired = [k for k, v in _pending_oauth.items() if now - v.created_at > _PENDING_TTL]
    for k in expired:
        del _pending_oauth[k]


# ---------------------------------------------------------------------------
# Token Helpers
# ---------------------------------------------------------------------------


def is_token_valid(tokens: QlikTokens) -> bool:
    """Check if access token is still valid (with 60s buffer)."""
    return time.time() < tokens.expires_at - 60


async def refresh_access_token(tokens: QlikTokens) -> Optional[QlikTokens]:
    """Refresh the access token using the refresh token.

    Returns new QlikTokens on success, None on failure.
    """
    if not tokens.refresh_token:
        return None

    token_url = f"{tokens.tenant_url.rstrip('/')}/oauth/token"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                    "client_id": tokens.client_id,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            return QlikTokens(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", tokens.refresh_token),
                expires_at=time.time() + data.get("expires_in", 3600),
                tenant_url=tokens.tenant_url,
                client_id=tokens.client_id,
            )
        except Exception as e:
            logger.warning(f"Token refresh failed: {e}")
            return None


# ---------------------------------------------------------------------------
# FastAPI Routes — register on Chainlit's app
# ---------------------------------------------------------------------------


def register_oauth_routes(fastapi_app):
    """Register the OAuth start and callback routes on the FastAPI app."""

    @fastapi_app.get("/auth/qlik/start")
    async def oauth_start(request: Request):
        """Initiate OAuth PKCE flow — redirects user to Qlik Cloud login."""
        session_id = request.query_params.get("session_id", "")
        tenant_url = request.query_params.get("tenant_url", "")
        client_id = request.query_params.get("client_id", "")

        if not all([session_id, tenant_url, client_id]):
            return HTMLResponse(
                "<h2>Missing parameters</h2><p>session_id, tenant_url, and client_id are required.</p>",
                status_code=400,
            )

        _cleanup_expired()

        # Generate PKCE
        code_verifier = generate_code_verifier()
        code_challenge = generate_code_challenge(code_verifier)

        # Build redirect URI
        base_url = os.getenv("APP_BASE_URL", "http://localhost:8000")
        redirect_uri = f"{base_url}/auth/qlik/callback"

        # Generate state
        state = secrets.token_urlsafe(32)

        # Store pending state
        _pending_oauth[state] = PendingOAuth(
            session_id=session_id,
            tenant_url=tenant_url,
            client_id=client_id,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
        )

        # Build Qlik authorize URL
        authorize_url = (
            f"{tenant_url.rstrip('/')}/oauth/authorize?"
            + urllib.parse.urlencode({
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "user_default mcp:execute",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            })
        )

        logger.info(f"OAuth flow started for session {session_id[:8]}...")
        return RedirectResponse(url=authorize_url)

    @fastapi_app.get("/auth/qlik/callback")
    async def oauth_callback(request: Request):
        """Handle Qlik OAuth callback — exchange code for tokens."""
        error = request.query_params.get("error")
        if error:
            error_desc = request.query_params.get("error_description", "Unknown error")
            logger.warning(f"OAuth error: {error} — {error_desc}")
            return HTMLResponse(_error_page(f"{error}: {error_desc}"))

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")

        if not code or not state:
            return HTMLResponse(_error_page("Missing code or state parameter."), status_code=400)

        # Look up pending state
        pending = _pending_oauth.pop(state, None)
        if not pending:
            return HTMLResponse(
                _error_page("OAuth session expired or invalid. Please try connecting again."),
                status_code=400,
            )

        # Exchange code for tokens
        token_url = f"{pending.tenant_url.rstrip('/')}/oauth/token"

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    token_url,
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
            except httpx.HTTPStatusError as e:
                logger.error(f"Token exchange failed: {e.response.status_code} {e.response.text}")
                return HTMLResponse(_error_page(f"Token exchange failed: {e.response.text}"))
            except Exception as e:
                logger.error(f"Token exchange error: {e}")
                return HTMLResponse(_error_page(f"Token exchange error: {e}"))

        tokens = QlikTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=time.time() + data.get("expires_in", 3600),
            tenant_url=pending.tenant_url,
            client_id=pending.client_id,
        )

        # Write tokens to the Chainlit user session
        try:
            from chainlit.user_session import user_sessions

            if pending.session_id in user_sessions:
                user_sessions[pending.session_id]["qlik_tokens"] = tokens
                user_sessions[pending.session_id]["oauth_complete"] = True
                logger.info(f"OAuth tokens stored for session {pending.session_id[:8]}...")
            else:
                logger.warning(f"Session {pending.session_id[:8]}... not found in user_sessions")
                return HTMLResponse(_error_page("Chat session expired. Please refresh the app and try again."))
        except Exception as e:
            logger.error(f"Failed to store tokens in session: {e}")
            return HTMLResponse(_error_page(f"Failed to store tokens: {e}"))

        return HTMLResponse(_success_page())


# ---------------------------------------------------------------------------
# HTML Pages
# ---------------------------------------------------------------------------

def _success_page() -> str:
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Qlik Authentication Successful</title>
    <style>
        body {
            font-family: 'Source Sans 3', 'Segoe UI', sans-serif;
            background: #0f1a24;
            color: #e0e0e0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
        }
        .container {
            text-align: center;
            padding: 40px;
        }
        .check {
            font-size: 64px;
            color: #009845;
            margin-bottom: 20px;
        }
        h1 { color: #009845; font-size: 24px; }
        p { color: #a0a0a0; font-size: 16px; }
        .btn {
            display: inline-block;
            margin-top: 20px;
            padding: 10px 24px;
            background: #009845;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            cursor: pointer;
            text-decoration: none;
        }
        .btn:hover { background: #007a38; }
    </style>
</head>
<body>
    <div class="container">
        <div class="check">&#10003;</div>
        <h1>Authenticated with Qlik Cloud</h1>
        <p>You can close this tab and return to the chat.</p>
        <button class="btn" onclick="window.close()">Close this tab</button>
    </div>
    <script>
        // Notify opener if this was opened as a popup
        if (window.opener) {
            window.opener.postMessage({type: 'qlik_oauth_complete'}, '*');
        }
        // Auto-close after 3 seconds
        setTimeout(() => window.close(), 3000);
    </script>
</body>
</html>
"""


def _error_page(message: str) -> str:
    import html
    safe_message = html.escape(message)
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Qlik Authentication Failed</title>
    <style>
        body {{
            font-family: 'Source Sans 3', 'Segoe UI', sans-serif;
            background: #0f1a24;
            color: #e0e0e0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
        }}
        .container {{
            text-align: center;
            padding: 40px;
            max-width: 500px;
        }}
        .icon {{ font-size: 64px; color: #d32f2f; margin-bottom: 20px; }}
        h1 {{ color: #d32f2f; font-size: 24px; }}
        .error {{ color: #a0a0a0; font-size: 14px; background: #1a2632; padding: 16px; border-radius: 6px; word-break: break-word; }}
        .btn {{
            display: inline-block;
            margin-top: 20px;
            padding: 10px 24px;
            background: #54565A;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            cursor: pointer;
            text-decoration: none;
        }}
        .btn:hover {{ background: #6a6c70; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">&#10007;</div>
        <h1>Authentication Failed</h1>
        <div class="error">{safe_message}</div>
        <button class="btn" onclick="window.close()">Close this tab</button>
    </div>
</body>
</html>
"""
