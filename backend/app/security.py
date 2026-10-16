import secrets
from fastapi import Header,HTTPException,Depends
from .config import Settings,get_settings

def require_actor(authorization:str|None=Header(default=None),settings:Settings=Depends(get_settings)):
    # Local development gate only. Use OIDC/OAuth/JWT plus per-meeting authorization in production.
    if not settings.app_api_token:
        raise HTTPException(503,'APP_API_TOKEN must be configured for API access')
    expected='Bearer '+settings.app_api_token
    if not authorization or not secrets.compare_digest(authorization,expected):
        raise HTTPException(401,'Authentication required')
    return 'local-user'
