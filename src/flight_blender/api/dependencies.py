from fastapi import Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from flight_blender.infrastructure.auth.jwt_validator import validate_token

security = HTTPBearer()


def require_scopes(required: list[str], allow_any: bool = False):
    async def _dep(creds: HTTPAuthorizationCredentials = Security(security)) -> dict:
        return await validate_token(creds.credentials, required, allow_any)

    return _dep
