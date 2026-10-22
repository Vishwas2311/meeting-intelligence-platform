"""External OIDC access-token validation or explicitly configured service API keys."""

import hashlib
import secrets
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings

bearer = HTTPBearer(
    auto_error=False, description="OIDC access token, or server-to-server API key in API-key mode"
)


@dataclass(frozen=True)
class Principal:
    actor: str
    admin: bool = False


@lru_cache(maxsize=4)
def signing_keys(url: str):
    # Only operator-configured HTTPS URLs are used; token jku/x5u headers are ignored.
    return jwt.PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)


def denied():
    return HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})


def require_principal(
    credential: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
    request: Request = None,
) -> Principal:
    if request is not None and getattr(request.state, "principal", None) is not None:
        return request.state.principal
    if (
        not credential
        or not credential.credentials.isascii()
        or len(credential.credentials) > 16384
    ):
        raise denied()
    token = credential.credentials
    if settings.auth_mode == "api_key":
        for client, key in settings.api_client_keys.items():
            if secrets.compare_digest(token, key):
                return Principal("client:" + client, client in settings.api_admin_clients)
        # Explicit local-only compatibility mode; never mixed into JWT mode.
        if not settings.api_client_keys and settings.app_api_token:
            if secrets.compare_digest(token, settings.app_api_token):
                return Principal("local-user", "local-user" in settings.api_admin_clients)
        if not settings.api_client_keys and not settings.app_api_token:
            raise HTTPException(503, "API authentication is not configured")
        raise denied()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise denied()
        key = signing_keys(settings.jwt_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
        roles = claims.get("roles", [])
        scope = claims.get("scp", "")
        if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
            raise denied()
        if not isinstance(scope, str) or not isinstance(claims["sub"], str) or not claims["sub"]:
            raise denied()
        if settings.jwt_required_permission not in set(roles) | set(scope.split()):
            raise HTTPException(403, "Required API permission is missing")
        actor = "jwt:" + hashlib.sha256((claims["iss"] + "\0" + claims["sub"]).encode()).hexdigest()
        return Principal(actor, settings.jwt_admin_role in roles)
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(503, "Identity provider signing keys unavailable") from None
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise denied() from None


def require_actor(principal: Principal = Depends(require_principal)) -> str:
    return principal.actor


def require_admin(principal: Principal = Depends(require_principal)) -> str:
    if not principal.admin:
        raise HTTPException(403, "Administrator access required")
    return principal.actor
