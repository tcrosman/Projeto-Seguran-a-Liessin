"""Hora local da escola e hora UTC, separadas de propósito.

O servidor roda em UTC (padrão da instalação Linux) e o Postgres também. Mas pais,
portaria e secretaria digitam e leem horários no relógio de Brasília. Usar `datetime.now()` para
tudo misturava as duas referências e produzia dois defeitos reais:

  * às 10:00 em Brasília o servidor via 13:00, então uma saída pedida para as 11:00 era recusada
    com "o horário informado já passou";
  * depois das 21:00 em Brasília a data do servidor já era a do dia seguinte, e a portaria abria
    a lista do dia errado.

A regra para escolher:

  `agora()` / `hoje()` / `hora()`  — tudo que o usuário digita, vê ou compara com o próprio
      relógio: data padrão dos formulários, a lista de saídas do dia, "esse horário já passou".

  `agora_utc()` — tudo que é comparado com o NOW() do banco: validade de token de 2FA e de
      redefinição de senha. Essas colunas são TIMESTAMP sem fuso; gravar hora de Brasília nelas e
      comparar com NOW() do Postgres (UTC) expiraria os tokens três horas fora do lugar.
"""
import os
from datetime import datetime, timedelta, timezone

FUSO_ESCOLA = os.getenv('TIMEZONE', 'America/Sao_Paulo')

# O Brasil aboliu o horário de verão em 2019, então o UTC-3 fixo é hoje equivalente ao fuso de
# São Paulo. Serve de reserva para imagens de container sem a base de fusos (tzdata) instalada.
_RESERVA = timezone(timedelta(hours=-3))


def _zona():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(FUSO_ESCOLA)
    except Exception:
        return _RESERVA


_ZONA = _zona()


def agora():
    """Agora no fuso da escola, com informação de fuso."""
    return datetime.now(_ZONA)


def hoje():
    """Data de hoje na escola, em 'YYYY-MM-DD' — o mesmo formato de saidas.data_saida."""
    return agora().strftime('%Y-%m-%d')


def hora():
    """Hora atual na escola, em 'HH:MM' — mesmo formato dos campos de horário."""
    return agora().strftime('%H:%M')


def carimbo():
    """Data e hora locais para registro legível ('YYYY-MM-DD HH:MM:SS')."""
    return agora().strftime('%Y-%m-%d %H:%M:%S')


def agora_utc():
    """Agora em UTC, sem fuso — para gravar em coluna TIMESTAMP comparada com o NOW() do banco."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
