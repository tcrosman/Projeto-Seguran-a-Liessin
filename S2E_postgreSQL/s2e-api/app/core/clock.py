"""Relógio local da escola para datas de saída."""

from datetime import datetime
from zoneinfo import ZoneInfo


def school_now():
    return datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
