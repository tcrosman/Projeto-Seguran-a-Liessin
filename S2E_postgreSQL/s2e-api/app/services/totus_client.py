import os
import requests


class TotusClient:
    """Valida responsável via TOTVS REST API. Retorna apenas true/false — nenhum dado sensível trafega."""

    def __init__(self):
        self._url = os.getenv('TOTUS_API_URL', '').rstrip('/')
        self._token = os.getenv('TOTUS_API_TOKEN', '')

    def validar_responsavel(self, email: str) -> bool:
        """Envia email para o TOTVS; retorna True se o responsável está autorizado."""
        if not self._url or not self._token:
            raise RuntimeError("TOTUS_API_URL e TOTUS_API_TOKEN devem estar configurados no .env")
        try:
            resp = requests.post(
                f"{self._url}/api/v1/validacao/responsavel",
                json={"email": email},
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            return bool(resp.json().get("autorizado", False))
        except Exception as e:
            print(f"[TOTUS] Erro ao validar responsável: {e}")
            return False


class TotusClientMock(TotusClient):
    """Mock para desenvolvimento/testes sem acesso ao TOTVS real (TOTUS_MOCK=true)."""

    # Adicione emails de teste aqui para o ambiente de desenvolvimento
    EMAILS_VALIDOS = {
        "pai@teste.com",
        "mae@teste.com",
        "responsavel@teste.com",
    }

    def validar_responsavel(self, email: str) -> bool:
        return email.strip().lower() in self.EMAILS_VALIDOS


def get_totus_client() -> TotusClient:
    """Retorna mock ou cliente real com base na variável TOTUS_MOCK (padrão: true em dev)."""
    if os.getenv('TOTUS_MOCK', 'true').lower() == 'true':
        return TotusClientMock()
    return TotusClient()
