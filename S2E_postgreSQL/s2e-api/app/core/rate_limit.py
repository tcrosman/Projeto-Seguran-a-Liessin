"""Contadores de autenticação compartilhados por todos os workers via PostgreSQL."""

import hashlib
import hmac
import os
from datetime import datetime, timedelta

from app.core.database import get_db


def login_identity(ip, account):
    """Evita bloquear toda a escola quando o proxy aparece como 127.0.0.1."""
    if ip in {'127.0.0.1', '::1'}:
        return f'account:{(account or "").strip().casefold()}'
    return f'ip:{ip or "unknown"}'


def _key(scope, identity):
    secret = os.environ['SECRET_KEY'].encode('utf-8')
    value = f'{scope}:{identity or "unknown"}'.encode('utf-8')
    return hmac.new(secret, value, hashlib.sha256).hexdigest()


def is_limited(scope, identity):
    with get_db() as conn:
        row = conn.execute(
            "SELECT blocked_until FROM auth_attempts WHERE key = %s",
            (_key(scope, identity),),
        ).fetchone()
    return bool(row and row['blocked_until'] and row['blocked_until'] > datetime.now())


def record_failure(scope, identity, maximum=5, window_minutes=15, block_minutes=5):
    key = _key(scope, identity)
    now = datetime.now()
    with get_db() as conn:
        # O UPSERT serializa a atualização da mesma chave entre workers.
        conn.execute("""
            INSERT INTO auth_attempts (key, failures, window_start, blocked_until)
            VALUES (%s, 1, %s, NULL)
            ON CONFLICT (key) DO UPDATE SET
                failures = CASE
                    WHEN auth_attempts.window_start < %s OR auth_attempts.blocked_until <= %s THEN 1
                    ELSE auth_attempts.failures + 1 END,
                window_start = CASE
                    WHEN auth_attempts.window_start < %s OR auth_attempts.blocked_until <= %s THEN %s
                    ELSE auth_attempts.window_start END,
                blocked_until = CASE
                    WHEN auth_attempts.window_start < %s OR auth_attempts.blocked_until <= %s THEN NULL
                    WHEN auth_attempts.failures + 1 >= %s THEN %s
                    ELSE auth_attempts.blocked_until END
        """, (key, now, now - timedelta(minutes=window_minutes),
              now, now - timedelta(minutes=window_minutes), now, now,
              now - timedelta(minutes=window_minutes), now, maximum,
              now + timedelta(minutes=block_minutes)))


def clear_failures(scope, identity):
    with get_db() as conn:
        conn.execute("DELETE FROM auth_attempts WHERE key = %s", (_key(scope, identity),))
