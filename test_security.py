"""
test_security.py
----------------
Suíte de testes de segurança automatizados para a aplicação Politize Mailer.

Cobertura de Segurança:
  1. Validação de Entrada e Tipos (Pydantic, EmailStr, limites de tamanho)
  2. Prevenção de CRLF Injection no Assunto do E-mail (Header Injection)
  3. Prevenção contra Adulteração de Dados (Row ↔ Email Anti-Tampering)
  4. Proteção contra Cross-Site Scripting (XSS) e Sanitização
  5. Isolamento e Garantia do Modo Simulação (Dry Run)
  6. Cabeçalhos de Segurança HTTP (X-Content-Type-Options, Frame-Options, etc.)
  7. Políticas de CORS (Origens Autorizadas vs Não Autorizadas)
  8. Proteção contra Vazamento de Stack Traces e Informações Internas
  9. Diagnóstico de Saúde e Status dos Serviços (/api/health)
  10. Renderização Segura de Prévia Oficial (/api/preview)
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# Garante path raiz
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Configura ambiente de teste
os.environ["RESEND_API_KEY"] = "re_test_mock_key_123456789"
os.environ["SPREADSHEET_ID"] = "mock_sheet_id_123"
os.environ["GOOGLE_CREDENTIALS_PATH"] = "credentials.json"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:8000,http://127.0.0.1:8000"

from api import app, load_template_html
from email_sender import render_template

@pytest.fixture(scope="module")
def client():
    """TestClient configurado para testes de integração."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ===========================================================================
# 1. TESTES DE SEGURANÇA: PREVENÇÃO DE CRLF INJECTION
# ===========================================================================
class TestCRLFInjection:
    """Garante que cabeçalhos de e-mail não podem ser injetados via quebras de linha."""

    def test_rejeita_crlf_no_assunto(self, client):
        payload = {
            "row": 2,
            "email": "ana.barbosa@escola-darcy.example.br",
            "dry_run": True,
            "subject": "Conheça o Politize\r\nBcc: hacker@ataque.com"
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 422
        assert "CRLF" in str(r.json()) or "quebras de linha" in str(r.json())

    def test_rejeita_lf_isolado_no_assunto(self, client):
        payload = {
            "row": 2,
            "email": "ana.barbosa@escola-darcy.example.br",
            "dry_run": True,
            "subject": "Assunto Legítimo\nBcc: hacker@ataque.com"
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 422

    def test_rejeita_assunto_vazio_ou_espacos(self, client):
        payload = {
            "row": 2,
            "email": "ana.barbosa@escola-darcy.example.br",
            "dry_run": True,
            "subject": "     "
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 422


# ===========================================================================
# 2. TESTES DE SEGURANÇA: VALIDAÇÃO DE TIPOS E LIMITES (PAYLOAD)
# ===========================================================================
class TestInputValidation:
    """Testa limites de payload e formatos aceitos."""

    def test_rejeita_email_invalido(self, client):
        payload = {
            "row": 2,
            "email": "email_invalido_sem_arroba",
            "dry_run": True,
            "subject": "Assunto Válido"
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 422

    def test_rejeita_row_menor_que_2(self, client):
        # Linha 1 é cabeçalho, deve rejeitar row=1 ou menores
        payload = {
            "row": 1,
            "email": "teste@escola.com.br",
            "dry_run": True,
            "subject": "Assunto Válido"
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 422

    def test_rejeita_row_negativo(self, client):
        payload = {
            "row": -5,
            "email": "teste@escola.com.br",
            "dry_run": True,
            "subject": "Assunto Válido"
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 422

    def test_rejeita_assunto_gigante_dos(self, client):
        # Assunto maior que 998 caracteres (limite RFC 5322)
        payload = {
            "row": 2,
            "email": "ana.barbosa@escola-darcy.example.br",
            "dry_run": True,
            "subject": "A" * 1000
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 422


# ===========================================================================
# 3. TESTES DE SEGURANÇA: ANTI-TAMPERING (ROW VS EMAIL)
# ===========================================================================
class TestAntiTampering:
    """Verifica se o usuário pode tentar falsificar a linha alterando o e-mail de destino."""

    def test_impede_envio_quando_email_nao_bate_com_linha(self, client):
        # Na linha 2 temos "Ana Luiza Barbosa", se enviarmos outro e-mail para a linha 2, deve rejeitar
        payload = {
            "row": 2,
            "email": "outro.email@hacker.com",
            "dry_run": True,
            "subject": "Tentativa de Adulteração"
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 400
        assert "não corresponde" in r.json()["detail"].lower()

    def test_rejeita_linha_inexistente(self, client):
        payload = {
            "row": 9999,
            "email": "teste@escola.com.br",
            "dry_run": True,
            "subject": "Linha Fantasma"
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 404


# ===========================================================================
# 4. TESTES DE SEGURANÇA: CABEÇALHOS HTTP E CORS
# ===========================================================================
class TestSecurityHeaders:
    """Valida se os cabeçalhos de defesa em profundidade estão ativos."""

    def test_cabecalhos_de_seguranca_presentes(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        headers = {k.lower(): v for k, v in r.headers.items()}
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("x-frame-options") == "SAMEORIGIN"
        assert "1; mode=block" in headers.get("x-xss-protection", "")
        assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_cors_origem_autorizada(self, client):
        r = client.get("/api/health", headers={"Origin": "http://localhost:8000"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "http://localhost:8000"

    def test_cors_origem_nao_autorizada(self, client):
        r = client.get("/api/health", headers={"Origin": "http://site-malicioso.com"})
        assert r.status_code == 200
        # Não deve emitir o cabeçalho de permissão para o site não autorizado
        assert r.headers.get("access-control-allow-origin") is None


# ===========================================================================
# 5. TESTES DE SEGURANÇA: MODO DRY RUN (SIMULAÇÃO)
# ===========================================================================
class TestDryRunSafety:
    """Garante que no modo de simulação nenhum disparo externo ao Resend é efetuado."""

    @patch("api.send_email")
    def test_dry_run_nao_chama_resend(self, mock_send, client):
        payload = {
            "row": 2,
            "email": "ana.barbosa@escola-darcy.example.br",
            "dry_run": True,
            "subject": "Teste de Simulação Seguro"
        }
        r = client.post("/api/send", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["simulated"] is True
        assert data["id"].startswith("sim_")
        # Garante que a função de envio real não foi invocada
        mock_send.assert_not_called()


# ===========================================================================
# 6. TESTES DE SEGURANÇA: PROTEÇÃO CONTRA VAZAMENTO DE DADOS
# ===========================================================================
class TestInformationDisclosure:
    """Verifica que respostas de erro não expõem tracebacks ou caminhos sensíveis."""

    @patch("api.get_recipients", side_effect=Exception("Database password error: secret_123"))
    def test_nao_expoe_stack_trace_em_500(self, mock_recip, client):
        r = client.get("/api/recipients")
        # A API deve retornar 200 com modo demonstração ou 500 sem vazar a stack
        assert "secret_123" not in r.text
        assert "Traceback" not in r.text


# ===========================================================================
# 7. TESTES DE SEGURANÇA: PRÉVIA OFICIAL E XSS
# ===========================================================================
class TestPreviewAndXSS:
    """Testa renderização de prévia e escape de injeções HTML."""

    def test_preview_escapa_tags_script_no_nome(self, client):
        malicious_name = '<script>alert("xss")</script>'
        r = client.get(f"/api/preview?nome={malicious_name}")
        assert r.status_code == 200
        # O script deve ser sanitizado como &lt;script&gt; ou não executável
        assert "<script>alert" not in r.text
        assert "&lt;script&gt;" in r.text

    def test_render_template_substituicao_correta(self):
        tpl = "<p>Olá, {{nome}}!</p>"
        res = render_template(tpl, {"nome": "Gabriel"})
        assert res == "<p>Olá, Gabriel!</p>"


# ===========================================================================
# 8. TESTES DE SEGURANÇA: ADIÇÃO DE NOVO DESTINATÁRIO
# ===========================================================================
class TestAddRecipient:
    """Valida a adição de 1 novo e-mail para a lista de disparos."""

    def test_rejeita_email_invalido_na_adicao(self, client):
        payload = {"nome": "Maria Santos", "email": "formato-invalido"}
        r = client.post("/api/recipients", json=payload)
        assert r.status_code == 422

    def test_rejeita_nome_curto_demais(self, client):
        payload = {"nome": "M", "email": "maria@escola.com.br"}
        r = client.post("/api/recipients", json=payload)
        assert r.status_code == 422

    def test_adiciona_destinatario_com_sucesso(self, client):
        unique_email = "maria.nova.teste@escola.example.br"
        payload = {"nome": "Maria Santos", "email": unique_email}
        r = client.post("/api/recipients", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["recipient"]["email"] == unique_email
        assert data["recipient"]["nome"] == "Maria Santos"
        assert data["recipient"]["row"] >= 2

    def test_rejeita_adicao_de_email_duplicado(self, client):
        dup_email = "maria.duplicada@escola.example.br"
        payload = {"nome": "Maria Santos", "email": dup_email}
        # Primeiro cadastro
        r1 = client.post("/api/recipients", json=payload)
        assert r1.status_code == 200

        # Tentativa de duplicata
        r2 = client.post("/api/recipients", json=payload)
        assert r2.status_code == 409
        assert "já está registrado" in r2.json()["detail"].lower()


# ===========================================================================
# 9. TESTES DE SEGURANÇA: GESTÃO DO MODELO E DISPLAY DO E-MAIL
# ===========================================================================
class TestTemplateManagement:
    """Testa leitura, atualização segura e reset do modelo e display."""

    def test_obter_configuracao_do_template(self, client):
        r = client.get("/api/template")
        assert r.status_code == 200
        data = r.json()
        assert "from_name" in data
        assert "from_email" in data
        assert "subject" in data
        assert "main_message" in data

    def test_atualizar_mensagem_e_display(self, client):
        payload = {
            "from_name": "Politize Teste",
            "from_email": "onboarding@resend.dev",
            "subject": "Assunto Atualizado com Segurança",
            "hero_subtitle": "Novo Subtítulo da Campanha",
            "main_message": "Nova mensagem principal de impacto educacional.",
        }
        r = client.post("/api/template", json=payload)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        # Confere se refletiu no GET
        get_r = client.get("/api/template")
        assert get_r.json()["from_name"] == "Politize Teste"
        assert get_r.json()["hero_subtitle"] == "Novo Subtítulo da Campanha"

    def test_rejeita_crlf_no_subject_do_template(self, client):
        payload = {
            "subject": "Assunto\r\nBcc: hacker@teste.com"
        }
        r = client.post("/api/template", json=payload)
        assert r.status_code == 422

    def test_reset_template_restaura_padrao(self, client):
        r = client.post("/api/template/reset")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

