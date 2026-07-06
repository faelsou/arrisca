"""Validação de JWT emitido pelo Supabase Auth.

Supabase usa HS256 com o JWT secret do projeto. Os claims relevantes:
    sub:    UUID do usuário em auth.users
    email:  email do usuário
    aud:    'authenticated'
    role:   'authenticated' | 'anon'
    exp:    expiração (Unix epoch)

Aqui validamos só a assinatura, expiração e audience. A resolução para
o `UserContext` (com tenant, papel, áreas) acontece em user_context.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import jwt


SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")


class InvalidToken(Exception):
    """Token ausente, expirado, mal assinado ou com audience incorreta."""


@dataclass(frozen=True)
class SupabaseClaims:
    auth_id: str    # sub
    email: str
    role: str       # 'authenticated' geralmente


def verify_supabase_jwt(token: str) -> SupabaseClaims:
    if not SUPABASE_JWT_SECRET:
        raise RuntimeError("SUPABASE_JWT_SECRET não configurado")
    if not token:
        raise InvalidToken("Token ausente")

    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError as e:
        raise InvalidToken("Token expirado") from e
    except jwt.InvalidAudienceError as e:
        raise InvalidToken("Audience inválida") from e
    except jwt.InvalidTokenError as e:
        raise InvalidToken(f"Token inválido: {e}") from e

    sub = payload.get("sub")
    email = payload.get("email")
    role = payload.get("role", "authenticated")

    if not sub:
        raise InvalidToken("Token sem 'sub'")

    return SupabaseClaims(auth_id=sub, email=email or "", role=role)
