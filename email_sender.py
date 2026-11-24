"""
email_sender.py
---------------
Módulo responsável por:
  1. Compilar o template MJML → HTML (via pacote 'mjml' para Python ou fallback com Node.js)
  2. Injetar variáveis personalizadas no HTML
  3. Disparar o e-mail via API do Resend
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import resend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. COMPILAÇÃO DO TEMPLATE MJML
# ---------------------------------------------------------------------------

def _compile_mjml_via_nodejs(mjml_source: str) -> str:
    """
    Compila MJML para HTML usando o CLI do Node.js (mjml instalado via npm).
    Utilizado como método principal caso o pacote 'mjml' Python não esteja disponível.

    Requisito: Node.js e 'mjml' instalados globalmente (npm install -g mjml)
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mjml", delete=False, encoding="utf-8"
    ) as tmp_in:
        tmp_in.write(mjml_source)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path.replace(".mjml", ".html")

    try:
        result = subprocess.run(
            ["mjml", tmp_in_path, "-o", tmp_out_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Erro ao compilar MJML via Node.js:\n{result.stderr}"
            )

        html = Path(tmp_out_path).read_text(encoding="utf-8")
        logger.debug("MJML compilado com sucesso via Node.js CLI.")
        return html

    finally:
        # Limpeza dos arquivos temporários
        Path(tmp_in_path).unlink(missing_ok=True)
        Path(tmp_out_path).unlink(missing_ok=True)


def _compile_mjml_via_python(mjml_source: str) -> str:
    """
    Compila MJML para HTML usando o pacote Python 'mjml'.
    Requer: pip install mjml  (internamente também usa Node.js)
    """
    import mjml  # importação tardia para não quebrar se não estiver instalado

    result = mjml.mjml_to_html(mjml_source)
    if result.errors:
        raise RuntimeError(f"Erros ao compilar MJML: {result.errors}")
    return result.html


def compile_mjml(template_path: str) -> str:
    """
    Lê o arquivo MJML e tenta compilá-lo para HTML.
    Tenta primeiro o pacote Python 'mjml', depois o CLI do Node.js.

    Args:
        template_path: Caminho absoluto ou relativo para o arquivo .mjml

    Returns:
        String com o HTML compilado.

    Raises:
        RuntimeError: Se nenhum método de compilação estiver disponível.
    """
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Template MJML não encontrado: '{template_path}'")

    mjml_source = path.read_text(encoding="utf-8")
    logger.info(f"Compilando template MJML: {template_path}")

    # Tenta o pacote Python primeiro
    try:
        html = _compile_mjml_via_python(mjml_source)
        logger.info("Compilação MJML concluída via pacote Python.")
        try:
            path.with_suffix(".html").write_text(html, encoding="utf-8")
        except Exception:
            pass
        return html
    except (ImportError, RuntimeError) as e:
        logger.warning(f"Pacote 'mjml' Python não disponível ou falhou ({e}). Tentando Node.js CLI...")

    # Fallback para o CLI do Node.js
    try:
        html = _compile_mjml_via_nodejs(mjml_source)
        logger.info("Compilação MJML concluída via Node.js CLI.")
        # Salva cópia compilada para cache
        try:
            path.with_suffix(".html").write_text(html, encoding="utf-8")
        except Exception:
            pass
        return html
    except Exception as e:
        logger.warning(f"Falha na compilação externa do MJML: {e}")
        cached_html = path.with_suffix(".html")
        if cached_html.exists():
            logger.info(f"Usando template HTML pré-compilado em cache: {cached_html}")
            return cached_html.read_text(encoding="utf-8")
        raise RuntimeError(
            "Não foi possível compilar o MJML e nenhum HTML em cache foi encontrado. "
            "Instale o pacote Python com 'pip install mjml' "
            "ou o CLI do Node.js com 'npm install -g mjml'."
        )


# ---------------------------------------------------------------------------
# 2. INJEÇÃO DE VARIÁVEIS NO TEMPLATE
# ---------------------------------------------------------------------------

def render_template(html_template: str, variables: dict) -> str:
    """
    Substitui os marcadores {{chave}} no HTML pelas variáveis fornecidas.

    Args:
        html_template: HTML compilado contendo marcadores como {{nome}}.
        variables: Dicionário com os valores a substituir, ex.: {"nome": "João"}.

    Returns:
        HTML com todas as variáveis substituídas.

    Exemplo:
        render_template("<p>Olá, {{nome}}!</p>", {"nome": "Maria"})
        # → "<p>Olá, Maria!</p>"
    """
    html = html_template
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        html = html.replace(placeholder, str(value))

    # Injeta a URL de descadastro se fornecida, substituindo marcadores ou links href="#"
    unsub_url = variables.get("unsubscribe_url")
    if unsub_url:
        html = html.replace("{{unsubscribe_url}}", str(unsub_url))
        # Substitui links de descadastrar que apontavam para "#"
        html = re.sub(
            r'(<a\s+[^>]*href=["\'])#(["\'][^>]*>.*?Descadastr.*?<\/a>)',
            rf'\g<1>{unsub_url}\g<2>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

    # Avisa sobre marcadores não preenchidos que restaram no HTML (ignorando opcionais)
    remaining = [m for m in re.findall(r"\{\{(\w+)\}\}", html) if m not in ("unsubscribe_url", "empresa", "email_empresa")]
    if remaining:
        logger.warning(f"Marcadores não substituídos no template: {remaining}")

    return html


def build_unsubscribe_url(to_email: str, token: str = "") -> str:
    """
    Gera a URL de descadastro apropriada para o destinatário:
    1. Se UNSUBSCRIBE_URL (ou GOOGLE_APPS_SCRIPT_URL) estiver configurado, usa a URL em nuvem
       (permite funcionamento 24/7 sem que a aplicação local esteja rodando).
    2. Se APP_BASE_URL for um servidor público, usa a rota /api/unsubscribe da aplicação.
    3. Caso contrário, utiliza o padrão universal mailto: que funciona autonomamente sem nenhum servidor.
    """
    clean_email = to_email.strip().lower()
    cloud_url = os.getenv("UNSUBSCRIBE_URL") or os.getenv("GOOGLE_APPS_SCRIPT_URL") or ""
    if cloud_url.strip().startswith("http"):
        sep = "&" if "?" in cloud_url else "?"
        token_part = f"&token={token}" if token else ""
        return f"{cloud_url.strip()}{sep}email={clean_email}{token_part}"

    base_url = os.getenv("APP_BASE_URL", "").strip()
    if base_url and not any(h in base_url.lower() for h in ("localhost", "127.0.0.1")):
        token_part = f"&token={token}" if token else ""
        return f"{base_url}/api/unsubscribe?email={clean_email}{token_part}"

    # Padrão universal autônomo (não requer servidor nem aplicação local rodando)
    sub = "Descadastrar da lista Politize"
    msg = f"Solicito o descadastro do e-mail ({clean_email}) da lista de envios da Politize."
    return f"mailto:contato@politize.com.br?subject={quote(sub)}&body={quote(msg)}"


# ---------------------------------------------------------------------------
# 3. ENVIO VIA API RESEND
# ---------------------------------------------------------------------------

def send_email(
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Envia um e-mail HTML via API do Resend com cabeçalhos oficiais de Opt-Out (RFC 8058).

    Args:
        api_key:    Chave de API do Resend (RESEND_API_KEY).
        from_email: Endereço remetente verificado no Resend.
        to_email:   Endereço do destinatário.
        subject:    Assunto do e-mail.
        html_body:  Conteúdo HTML do e-mail.
        dry_run:    Se True, simula o envio sem chamar a API (modo de teste).

    Returns:
        ID do e-mail retornado pelo Resend em caso de sucesso, ou None em dry_run.

    Raises:
        Exception: Propaga qualquer erro da API Resend para tratamento externo.
    """
    if dry_run:
        logger.info(f"[DRY RUN] Simulando envio para {to_email} | Assunto: {subject}")
        return None

    # Configura a chave da API
    resend.api_key = api_key

    # Cabeçalhos padrão RFC 8058 para descadastro direto nos clientes de e-mail (Gmail, Outlook)
    headers = {
        "List-Unsubscribe": f"<mailto:contato@politize.com.br?subject=Descadastrar%20{to_email}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }

    params = {
        "from":    from_email,
        "to":      [to_email],
        "subject": subject,
        "html":    html_body,
        "headers": headers,
    }

    logger.debug(f"Enviando e-mail para {to_email}...")
    email_response = resend.Emails.send(params)

    email_id = email_response.get("id") if isinstance(email_response, dict) else getattr(email_response, "id", None)
    logger.info(f"E-mail enviado com sucesso para {to_email} | ID: {email_id}")
    return email_id
