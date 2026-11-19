"""Segurança, validação de JWT OIDC/Keycloak com cache JWKS e controle de acesso baseado em papéis (RBAC)."""

import time
from enum import StrEnum
from typing import Any, Callable

import httpx
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError

bearer_scheme = HTTPBearer(auto_error=False)


class UserRole(StrEnum):
    """Papéis institucionais RBAC suportados pelo sistema."""

    OPERATOR = "OPERATOR"  # Servidor técnico-administrativo que submete e revisa documentos
    REVIEWER = "REVIEWER"  # Revisor de conformidade da unidade/Pró-Reitoria
    DPO_AUDITOR = "DPO_AUDITOR"  # Encarregado de Dados / Auditoria institucional (acesso a métricas e trilhas)
    ADMIN = "ADMIN"  # Administrador da plataforma


class AuthenticatedPrincipal(BaseModel):
    """Identidade do usuário extraída e validada a partir do token OIDC/Keycloak."""

    sub: str = Field(..., description="Subject identificador único no provedor OIDC (Keycloak)")
    username: str = Field(..., description="Username institucional (preferred_username)")
    organizational_unit: str | None = Field(
        default=None,
        description="Unidade administrativa / Pró-Reitoria de origem (ex.: PROGESP, PROPLAN, PROEG)",
    )
    roles: list[UserRole] = Field(default_factory=lambda: [UserRole.OPERATOR])


class OIDCKeyManager:
    """Gerenciador assíncrono de chaves públicas JWKS do Keycloak com cache em memória."""

    def __init__(self) -> None:
        self._jwks_cache: dict[str, Any] = {}
        self._cache_expires_at: float = 0.0

    async def get_signing_key(self, kid: str) -> Any:
        now = time.monotonic()
        if not self._jwks_cache or now >= self._cache_expires_at or kid not in self._jwks_cache:
            await self._refresh_jwks()

        jwk_dict = self._jwks_cache.get(kid)
        if not jwk_dict:
            raise AuthenticationError("Chave pública de assinatura (kid) não encontrada no provedor OIDC.")

        return RSAAlgorithm.from_jwk(jwk_dict)

    async def _refresh_jwks(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(settings.OIDC_JWKS_URL)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise AuthenticationError(
                "Não foi possível consultar o endpoint JWKS do provedor OIDC institucional."
            ) from exc

        keys = payload.get("keys", [])
        mapped_keys: dict[str, Any] = {}
        for key_entry in keys:
            kid = key_entry.get("kid")
            if kid:
                mapped_keys[kid] = key_entry

        self._jwks_cache = mapped_keys
        self._cache_expires_at = time.monotonic() + settings.OIDC_JWKS_CACHE_TTL_SECONDS


oidc_key_manager = OIDCKeyManager()


def _extract_roles_from_claims(claims: dict[str, Any]) -> list[UserRole]:
    """Extrai papéis do padrão Keycloak (`realm_access.roles` e `resource_access.<client>.roles`)."""
    raw_roles: set[str] = set()

    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        raw_roles.update(str(r).upper() for r in realm_access.get("roles", []))

    resource_access = claims.get("resource_access")
    if isinstance(resource_access, dict):
        client_box = resource_access.get(settings.OIDC_CLIENT_ID, {})
        if isinstance(client_box, dict):
            raw_roles.update(str(r).upper() for r in client_box.get("roles", []))

    if isinstance(claims.get("roles"), list):
        raw_roles.update(str(r).upper() for r in claims["roles"])

    valid_roles: list[UserRole] = []
    for role in UserRole:
        if role.value in raw_roles:
            valid_roles.append(role)

    return valid_roles or [UserRole.OPERATOR]


async def verify_jwt_token(token: str) -> AuthenticatedPrincipal:
    """Valida assinatura, expiração, emissor e audiência de um token JWT (Keycloak RS256 ou Dev HS256)."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Cabeçalho do token JWT malformado.") from exc

    try:
        if settings.OIDC_ENABLED:
            kid = unverified_header.get("kid")
            if not kid:
                raise AuthenticationError("Cabeçalho 'kid' ausente no token OIDC.")
            public_key = await oidc_key_manager.get_signing_key(kid)
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=settings.OIDC_ALGORITHMS,
                audience=settings.OIDC_AUDIENCE,
                issuer=settings.OIDC_ISSUER_URL,
            )
        else:
            # Modo de desenvolvimento/teste controlado quando OIDC_ENABLED=false
            claims = jwt.decode(
                token,
                key=settings.JWT_DEV_SECRET_KEY,
                algorithms=["HS256"],
                options={"verify_aud": False, "verify_iss": False},
            )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("O token de acesso expirou.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Token de acesso inválido.") from exc

    sub = claims.get("sub")
    if not sub:
        raise AuthenticationError("Claim 'sub' obrigatória ausente no token.")

    username = claims.get("preferred_username") or claims.get("username") or str(sub)
    org_unit = claims.get("organizational_unit") or claims.get("department") or claims.get("unidade")
    roles = _extract_roles_from_claims(claims)

    return AuthenticatedPrincipal(
        sub=str(sub),
        username=str(username),
        organizational_unit=str(org_unit) if org_unit else None,
        roles=roles,
    )


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedPrincipal:
    """Dependência FastAPI para exigir usuário autenticado."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Cabeçalho Authorization Bearer ausente.")
    return await verify_jwt_token(credentials.credentials)


def require_roles(*allowed_roles: UserRole) -> Callable[..., Any]:
    """Factory de dependência FastAPI para controle de acesso baseado em papéis (RBAC)."""

    async def _role_checker(
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
    ) -> AuthenticatedPrincipal:
        if UserRole.ADMIN in principal.roles:
            return principal
        if not any(role in principal.roles for role in allowed_roles):
            raise AuthorizationError(
                f"Operação restrita aos perfis: {', '.join(r.value for r in allowed_roles)}."
            )
        return principal

    return _role_checker
