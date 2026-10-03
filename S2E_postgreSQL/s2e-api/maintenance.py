"""Tarefa agendável de manutenção. Não remove histórico de alunos/saídas."""

from app.core.database import get_db, expirar_saidas_nao_liberadas


def main():
    with get_db() as conn:
        expirar_saidas_nao_liberadas(conn)
        conn.execute("""
            DELETE FROM auth_attempts
            WHERE window_start < CURRENT_TIMESTAMP - INTERVAL '30 days'
        """)


if __name__ == '__main__':
    main()
