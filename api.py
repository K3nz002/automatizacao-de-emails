"""
api.py
------
Backend FastAPI de alta performance que integra a automação de e-mails
com a interface web (estilo Desktop Web App).

Recursos:
  - GET  /api/health           → Diagnóstico de saúde e status dos serviços
  - GET  /api/recipients       → Lista destinatários do Google Sheets (ou dados de demonstração)
  - POST /api/recipients       → Adiciona 1 novo destinatário à lista de disparos
  - POST /api/send             → Disparo individual ou simulação (com validação anti-tampering)
  - GET  /api/preview          → Prévia do e-mail com MJML real compilado
  - GET  /api/template         → Obtém a mensagem atual e configurações do display do e-mail
  - POST /api/template         → Atualiza e salva a mensagem e o display do e-mail
  - POST /api/template/reset   → Restaura o modelo original de e-mail
  - GET  /api/logs             → Últimos eventos do arquivo de log
  - GET  /                     → Aplicação web completa
"""

import html as html_lib
import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

# ---------------------------------------------------------------------------
# Windows — garante UTF-8 na saída
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("envios.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("politize_api")

# ---------------------------------------------------------------------------
# Carregamento de variáveis de ambiente
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    logger.info(f"Variáveis de ambiente carregadas de: {env_path}")
else:
    load_dotenv()
    logger.warning(".env não encontrado. Usando variáveis de ambiente do sistema.")

def get_config():
    sheet_id_raw = os.getenv("SPREADSHEET_ID", "")
    sheet_match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_id_raw)
    clean_sheet_id = sheet_match.group(1) if sheet_match else sheet_id_raw.strip()
    return {
        "resend_api_key":       os.getenv("RESEND_API_KEY", ""),
        "from_email":           os.getenv("FROM_EMAIL", "Politize <noreply@politize.com.br>"),
        "spreadsheet_id":       clean_sheet_id,
        "sheet_name":           os.getenv("SHEET_NAME", "Destinatários"),
        "credentials_path":     os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
        "email_subject":        os.getenv("EMAIL_SUBJECT", "Conheça o Politize — Educação Política para sua Escola"),
        "send_interval":        float(os.getenv("SEND_INTERVAL_SECONDS", "1") or "1"),
        "dry_run":              os.getenv("DRY_RUN", "False").lower() in ("true", "1", "yes"),
        "template_path":        str(BASE_DIR / "templates" / "email_template.mjml"),
        "allowed_origins":      os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"),
        "app_base_url":         os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/"),
    }

CONFIG = get_config()

# ---------------------------------------------------------------------------
# Importação dos módulos do domínio
# ---------------------------------------------------------------------------
from email_sender import (  # noqa: E402
    build_unsubscribe_url,
    compile_mjml,
    render_template,
    send_email,
)
from sheets_client import (  # noqa: E402
    DEFAULT_DESTINATARIOS_HEADERS,
    DEFAULT_ENVIADOS_HEADERS,
    MSG_ESTRUTURA_INVALIDA,
    TAB_DESTINATARIOS,
    TAB_ENVIADOS,
    add_recipient,
    get_recipients,
    get_sent_history,
    mark_as_sent,
    record_sent_email,
    resubscribe_recipient,
    unsubscribe_recipient,
    validate_spreadsheet_structure,
)

# ---------------------------------------------------------------------------
# Mecanismo de Descadastro Seguro (HMAC Token)
# ---------------------------------------------------------------------------
import hashlib  # noqa: E402
import hmac  # noqa: E402

UNSUB_SECRET = os.getenv("UNSUBSCRIBE_SECRET") or os.getenv("RESEND_API_KEY") or "politize_unsub_secret_2026"

def generate_unsubscribe_token(email: str) -> str:
    """Gera token HMAC para descadastro seguro sem risco de adulteração."""
    raw = email.strip().lower().encode("utf-8")
    return hmac.new(UNSUB_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:24]

def verify_unsubscribe_token(email: str, token: str) -> bool:
    """Valida o token de descadastro usando comparação em tempo constante."""
    if not token:
        return False
    expected = generate_unsubscribe_token(email)
    return hmac.compare_digest(expected, token.strip())


def compute_message_hash(subject: str, cfg: Optional[dict] = None) -> str:
    """
    Gera um hash representativo do conteúdo da mensagem (assunto, textos, blocos).
    Permite identificar com precisão quando o conteúdo do e-mail mudou para viabilizar reenvios.
    """
    if cfg is None:
        cfg = get_message_config()
    raw_parts = [
        subject.strip(),
        str(cfg.get("main_message", "")),
        str(cfg.get("hero_subtitle", "")),
        str(cfg.get("hero_title", "")),
        str(cfg.get("cta_title", "")),
    ]
    if cfg.get("blocks"):
        raw_parts.append(json.dumps(cfg["blocks"], sort_keys=True))
    elif cfg.get("custom_html"):
        raw_parts.append(str(cfg["custom_html"]))
    combined = "||".join(raw_parts).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Configuração Personalizada do Modelo de E-mail (Display & Conteúdo)
# ---------------------------------------------------------------------------
TEMPLATE_CONFIG_FILE = BASE_DIR / "templates" / "custom_message.json"

def _extract_name_and_email(from_str: str) -> tuple[str, str]:
    """Separa 'Nome <email@dominio.com>' em ('Nome', 'email@dominio.com')."""
    m = re.match(r"^(.*?)\s*<([^>]+)>$", from_str.strip())
    if m:
        return m.group(1).strip() or "Politize", m.group(2).strip()
    return "Politize", from_str.strip()

_init_name, _init_addr = _extract_name_and_email(CONFIG["from_email"])

DEFAULT_BLOCKS = [
    {
        "id": "block_header",
        "type": "header",
        "name": "Cabeçalho com Logo",
        "title": "⚖️ POLITIZE",
        "subtitle": "Educação Política para Todos",
        "bg_color": "#1A1A2E",
        "text_color": "#FFFFFF",
    },
    {
        "id": "block_hero",
        "type": "hero",
        "name": "Banner Hero Principal",
        "tag": "Parceria Educacional",
        "title": "Olá, {{nome}}! 👋",
        "subtitle": "Transforme a forma como sua escola enxerga a política",
        "button_text": "Saiba Mais →",
        "button_url": "https://www.politize.com.br",
        "bg_color": "#16213E",
    },
    {
        "id": "block_text",
        "type": "text",
        "name": "Mensagem Principal",
        "section_title": "O que oferecemos à sua escola:",
        "content": "Sabemos que falar sobre política nas escolas pode ser um desafio — por isso o Politize existe: para tornar esse assunto acessível, imparcial e engajante para jovens de todo o Brasil.",
        "align": "left",
    },
    {
        "id": "block_cards",
        "type": "cards",
        "name": "Cards de Benefícios",
        "items": [
            {"icon": "📚", "title": "Conteúdo Gratuito", "desc": "Mais de 500 artigos e vídeos sobre política, direito e cidadania — sem custo para a escola."},
            {"icon": "🎯", "title": "Conteúdo Imparcial", "desc": "Abordagem apartidária, baseada em fatos e pensamento crítico para os alunos."},
            {"icon": "🤝", "title": "Parceria Educacional", "desc": "Suporte pedagógico e materiais adaptados para o currículo escolar."},
            {"icon": "🚀", "title": "Impacto Real", "desc": "Mais de 2 milhões de jovens impactados em todo o Brasil."}
        ],
    },
    {
        "id": "block_cta",
        "type": "cta",
        "name": "Chamada para Ação (CTA)",
        "title": "Leve o Politize para sua escola agora",
        "subtitle": "Entre em contato e descubra como podemos firmar uma parceria.",
        "button_text": "Entrar em Contato",
        "button_url": "mailto:parceria@politize.com.br",
        "bg_color": "#FF6B35",
    },
    {
        "id": "block_footer",
        "type": "footer",
        "name": "Rodapé com Descadastro",
        "text": "© 2026 Politize — Educação Política para Todos<br>Você está recebendo este e-mail porque sua instituição pode se beneficiar de nossa parceria.",
        "unsubscribe_text": "Descadastrar",
        "privacy_url": "https://www.politize.com.br/privacidade",
    },
]

def compile_blocks_to_html(blocks: list[dict]) -> str:
    """Compila a lista ordenada de blocos visuais arrastados pelo usuário em HTML de e-mail responsivo."""
    rendered_blocks = []
    for b in blocks:
        b_type = b.get("type", "text")
        if b_type == "header":
            title = b.get("title", "⚖️ POLITIZE")
            sub = b.get("subtitle", "Educação Política para Todos")
            bg = b.get("bg_color", "#1A1A2E")
            fg = b.get("text_color", "#FFFFFF")
            rendered_blocks.append(f"""
            <!-- HEADER BLOCK -->
            <tr>
              <td style="background-color:{bg};padding:30px 40px;text-align:center;border-radius:14px 14px 0 0;">
                <h1 style="margin:0;font-size:26px;font-weight:700;color:{fg};letter-spacing:2px;font-family:Inter,Arial,sans-serif;">{title}</h1>
                <p style="margin:6px 0 0;font-size:13px;color:#A8B2C8;font-family:Inter,Arial,sans-serif;">{sub}</p>
              </td>
            </tr>""")
        elif b_type == "hero":
            tag = b.get("tag", "Parceria Educacional")
            title = b.get("title", "Olá, {{nome}}! 👋")
            sub = b.get("subtitle", "Transforme a forma como sua escola enxerga a política")
            btn_text = b.get("button_text", "Saiba Mais →")
            btn_url = b.get("button_url", "https://www.politize.com.br")
            bg = b.get("bg_color", "#16213E")
            btn_html = f"""<div style="margin-top:24px;"><a href="{btn_url}" style="background-color:#FF6B35;color:#ffffff;text-decoration:none;padding:14px 32px;border-radius:8px;font-weight:bold;display:inline-block;font-size:15px;">{btn_text}</a></div>""" if btn_text else ""
            rendered_blocks.append(f"""
            <!-- HERO BANNER BLOCK -->
            <tr>
              <td style="background-color:{bg};padding:44px 36px;text-align:center;color:#ffffff;">
                <p style="margin:0 0 10px;font-size:12px;font-weight:700;color:#FF6B35;text-transform:uppercase;letter-spacing:3px;">{tag}</p>
                <h2 style="margin:0;font-size:30px;line-height:1.3;font-weight:700;color:#ffffff;">{title}</h2>
                <p style="margin:12px auto 0;font-size:17px;color:#C8D0E0;max-width:480px;line-height:1.5;">{sub}</p>
                {btn_html}
              </td>
            </tr>""")
        elif b_type == "text":
            sec_title = b.get("section_title", "")
            content = str(b.get("content", "")).replace("\n", "<br>")
            align = b.get("align", "left")
            title_html = f"""<h3 style="margin:0 0 12px;font-size:20px;font-weight:700;color:#1A1A2E;">{sec_title}</h3>""" if sec_title else ""
            rendered_blocks.append(f"""
            <!-- TEXT BLOCK -->
            <tr>
              <td style="background-color:#ffffff;padding:36px;text-align:{align};font-size:16px;line-height:1.7;color:#333333;">
                {title_html}
                <div style="font-size:16px;color:#444444;line-height:1.7;">{content}</div>
              </td>
            </tr>""")
        elif b_type == "cards":
            items = b.get("items", [])
            cards_html = ""
            for item in items:
                icon = item.get("icon", "📌")
                ititle = item.get("title", "")
                idesc = item.get("desc", "")
                cards_html += f"""
                <div style="background:#FAFAFA;border:1.5px solid #F0F0F0;border-radius:10px;padding:18px;margin-bottom:12px;text-align:left;">
                  <span style="font-size:26px;display:block;margin-bottom:4px;">{icon}</span>
                  <strong style="color:#1A1A2E;font-size:15.5px;display:block;margin-bottom:4px;">{ititle}</strong>
                  <span style="color:#666666;font-size:13.5px;line-height:1.5;display:block;">{idesc}</span>
                </div>"""
            rendered_blocks.append(f"""
            <!-- CARDS BLOCK -->
            <tr>
              <td style="background-color:#ffffff;padding:0 36px 28px;">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                  {cards_html}
                </div>
              </td>
            </tr>""")
        elif b_type == "highlight":
            title = b.get("title", "")
            content = str(b.get("content", "")).replace("\n", "<br>")
            rendered_blocks.append(f"""
            <!-- HIGHLIGHT BLOCK -->
            <tr>
              <td style="background-color:#ffffff;padding:12px 36px 24px;">
                <div style="background:#FFF3EB;border-left:4px solid #FF6B35;border-radius:8px;padding:20px 24px;">
                  <strong style="color:#8A2E0B;font-size:16px;display:block;margin-bottom:6px;">{title}</strong>
                  <div style="color:#666666;font-size:14.5px;line-height:1.6;">{content}</div>
                </div>
              </td>
            </tr>""")
        elif b_type == "cta":
            title = b.get("title", "Leve o Politize para sua escola agora")
            sub = b.get("subtitle", "Entre em contato e descubra como podemos firmar uma parceria.")
            btn_text = b.get("button_text", "Entrar em Contato")
            btn_url = b.get("button_url", "mailto:parceria@politize.com.br")
            bg = b.get("bg_color", "#FF6B35")
            rendered_blocks.append(f"""
            <!-- CTA BANNER BLOCK -->
            <tr>
              <td style="background-color:{bg};padding:40px 36px;text-align:center;color:#ffffff;">
                <h3 style="margin:0 0 10px;font-size:23px;font-weight:700;color:#ffffff;line-height:1.3;">{title}</h3>
                <p style="margin:0 0 24px;font-size:15px;color:#FFE5D9;">{sub}</p>
                <a href="{btn_url}" style="background-color:#ffffff;color:#FF6B35;text-decoration:none;padding:13px 32px;border-radius:8px;font-weight:700;font-size:15px;display:inline-block;">{btn_text}</a>
              </td>
            </tr>""")
        elif b_type == "button":
            text = b.get("button_text", "Clique Aqui")
            url = b.get("button_url", "https://www.politize.com.br")
            bg = b.get("bg_color", "#FF6B35")
            align = b.get("align", "center")
            rendered_blocks.append(f"""
            <!-- BUTTON BLOCK -->
            <tr>
              <td style="background-color:#ffffff;padding:20px 36px;text-align:{align};">
                <a href="{url}" style="background-color:{bg};color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:bold;font-size:15px;display:inline-block;">{text}</a>
              </td>
            </tr>""")
        elif b_type == "divider":
            rendered_blocks.append("""
            <!-- DIVIDER BLOCK -->
            <tr>
              <td style="background-color:#ffffff;padding:8px 36px;">
                <hr style="border:0;border-top:1px solid #ECECEC;margin:0;">
              </td>
            </tr>""")
        elif b_type == "image":
            img_url = b.get("image_url", "https://images.unsplash.com/photo-1524178232363-1fb2b075b655?w=800&q=80")
            alt = b.get("alt", "Banner Politize")
            link_url = b.get("link_url", "")
            img_tag = f"""<img src="{img_url}" alt="{alt}" style="max-width:100%;height:auto;border-radius:8px;display:block;margin:0 auto;">"""
            if link_url:
                img_tag = f"""<a href="{link_url}" style="text-decoration:none;">{img_tag}</a>"""
            rendered_blocks.append(f"""
            <!-- IMAGE BLOCK -->
            <tr>
              <td style="background-color:#ffffff;padding:18px 36px;text-align:center;">
                {img_tag}
              </td>
            </tr>""")
        elif b_type == "footer":
            text = b.get("text", "© 2026 Politize — Educação Política para Todos")
            unsub_text = b.get("unsubscribe_text", "Descadastrar")
            privacy_url = b.get("privacy_url", "https://www.politize.com.br/privacidade")
            rendered_blocks.append(f"""
            <!-- FOOTER BLOCK -->
            <tr>
              <td style="background-color:#1A1A2E;padding:32px 36px;text-align:center;color:#A8B2C8;font-size:12.5px;line-height:1.6;border-radius:0 0 14px 14px;">
                <div>{text}</div>
                <div style="margin-top:14px;color:#8592A6;">
                  <a href="{{{{unsubscribe_url}}}}" style="color:#A8B2C8;text-decoration:underline;">{unsub_text}</a> ·
                  <a href="{privacy_url}" style="color:#A8B2C8;text-decoration:underline;">Política de Privacidade</a>
                </div>
              </td>
            </tr>""")

    inner_html = "\n".join(rendered_blocks)
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Politize</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
    body {{ margin: 0; padding: 0; background-color: #F4F4F5; font-family: Inter, Arial, sans-serif; -webkit-font-smoothing: antialiased; }}
    table {{ border-collapse: collapse; }}
    @media only screen and (max-width: 620px) {{
      .email-container {{ width: 100% !important; border-radius: 0 !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:24px 12px;background-color:#F4F4F5;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
    <tr>
      <td align="center">
        <!-- Container Central -->
        <table class="email-container" role="presentation" width="100%" style="max-width:600px;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 4px 16px rgba(15,24,48,0.08);" border="0" cellspacing="0" cellpadding="0">
          {inner_html}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

DEFAULT_MESSAGE_CONFIG = {
    "from_name": _init_name,
    "from_email": _init_addr,
    "subject": CONFIG["email_subject"],
    "preheader": "Educação política acessível para todos — veja como o Politize pode transformar sua escola!",
    "hero_tag": "Parceria Educacional",
    "hero_title": "Olá, {{nome}}! 👋",
    "hero_subtitle": "Transforme a forma como sua escola enxerga a política",
    "hero_button_text": "Saiba Mais →",
    "hero_button_url": "https://www.politize.com.br",
    "main_message": "Sabemos que falar sobre política nas escolas pode ser um desafio — por isso o Politize existe: para tornar esse assunto acessível, imparcial e engajante para jovens de todo o Brasil.",
    "section_title": "O que oferecemos à sua escola:",
    "cta_title": "Leve o Politize para sua escola agora",
    "cta_subtitle": "Entre em contato e descubra como podemos firmar uma parceria.",
    "cta_button_text": "Entrar em Contato",
    "cta_button_url": "https://www.politize.com.br/contato",
    "footer_note": "© 2026 Politize — Educação Política para Todos",
    "custom_html": "",
    "blocks": DEFAULT_BLOCKS,
}

def get_message_config() -> dict:
    if TEMPLATE_CONFIG_FILE.exists():
        try:
            saved = json.loads(TEMPLATE_CONFIG_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_MESSAGE_CONFIG, **saved}
        except Exception as e:
            logger.warning(f"Erro ao ler custom_message.json: {e}")
    return dict(DEFAULT_MESSAGE_CONFIG)

def save_message_config(cfg: dict):
    TEMPLATE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE_CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

_COMPILED_HTML: str = ""

def build_template_from_config(cfg: dict) -> str:
    """Aplica a montagem por blocos visuais arrastáveis ou o template base compilado."""
    # 1. Se houver layout construído por blocos arrastáveis:
    if cfg.get("blocks") and isinstance(cfg["blocks"], list) and len(cfg["blocks"]) > 0:
        return compile_blocks_to_html(cfg["blocks"])

    # 2. Se houver custom_html explícito definido pelo usuário, utiliza diretamente:
    if cfg.get("custom_html") and cfg["custom_html"].strip():
        return cfg["custom_html"]

    # 3. Carrega o HTML base compilado do MJML:
    base_html = ""
    tpl_path = Path(CONFIG["template_path"])
    cached_html = tpl_path.with_suffix(".html")
    if cached_html.exists():
        base_html = cached_html.read_text(encoding="utf-8")
    elif tpl_path.exists():
        try:
            base_html = compile_mjml(str(tpl_path))
        except Exception:
            pass

    if not base_html:
        base_html = compile_blocks_to_html(DEFAULT_BLOCKS)

    # Injeta os textos customizados no HTML
    html = base_html
    if cfg.get("hero_subtitle"):
        html = re.sub(
            r"(<mj-text[^>]*>|\b)Transforme a forma como sua escola enxerga a política(<\/mj-text>|\b)",
            cfg["hero_subtitle"],
            html,
        )
    if cfg.get("main_message"):
        html = re.sub(
            r"Sabemos que falar sobre política nas escolas pode ser um desafio[\s\S]*?para jovens de todo o Brasil\.",
            cfg["main_message"],
            html,
        )
    if cfg.get("hero_button_text"):
        html = re.sub(
            r"Saiba Mais →",
            cfg["hero_button_text"],
            html,
        )
    if cfg.get("hero_button_url"):
        html = re.sub(
            r'href="https:\/\/www\.politize\.com\.br"',
            f'href="{cfg["hero_button_url"]}"',
            html,
        )
    if cfg.get("cta_title"):
        html = re.sub(
            r"Leve o Politize para sua escola agora",
            cfg["cta_title"],
            html,
        )
    if cfg.get("cta_subtitle"):
        html = re.sub(
            r"Entre em contato e descubra como podemos firmar uma parceria\.",
            cfg["cta_subtitle"],
            html,
        )
    if cfg.get("cta_button_text"):
        html = re.sub(
            r">Entrar em Contato<",
            f'>{cfg["cta_button_text"]}<',
            html,
        )
    return html

def load_template_html() -> str:
    """Tenta carregar ou compilar o template MJML com múltiplos fallbacks."""
    global _COMPILED_HTML
    cfg = get_message_config()
    _COMPILED_HTML = build_template_from_config(cfg)
    return _COMPILED_HTML


# ---------------------------------------------------------------------------
# Dados mock de demonstração para quando o Google Sheets não estiver configurado
# ---------------------------------------------------------------------------
MOCK_RECIPIENTS = [
    {"row": 2, "nome": "Ana Luiza Barbosa", "email": "ana.barbosa@escola-darcy.example.br", "empresa": "Escola Darcy Ribeiro", "email_empresa": "contato@escola-darcy.example.br", "status": "Enviado", "data_envio": "2026-11-10 14:15:22"},
    {"row": 3, "nome": "Bruno Ribeiro", "email": "bruno.ribeiro@lourenco-filho.example.br", "empresa": "Colégio Lourenço Filho", "email_empresa": "direcao@lourenco-filho.example.br", "status": "Enviado", "data_envio": "2026-11-10 14:15:24"},
    {"row": 4, "nome": "Carla Nogueira", "email": "carla.nogueira@paulo-freire.example.br", "empresa": "Instituto Paulo Freire", "email_empresa": "secretaria@paulo-freire.example.br", "status": "", "data_envio": ""},
    {"row": 5, "nome": "Daniel Teixeira", "email": "daniel.teixeira@anisio-teixeira.example.br", "empresa": "Escola Anísio Teixeira", "email_empresa": "pedagogico@anisio-teixeira.example.br", "status": "", "data_envio": ""},
    {"row": 6, "nome": "Elisa Duarte", "email": "elisa.duarte@cecilia-meireles.example.br", "empresa": "Colégio Cecília Meireles", "email_empresa": "adm@cecilia-meireles.example.br", "status": "", "data_envio": ""},
    {"row": 7, "nome": "Fábio Guimarães", "email": "fabio.guimaraes@machado-assis.example.br", "empresa": "Liceu Machado de Assis", "email_empresa": "faleconosco@machado-assis.example.br", "status": "", "data_envio": ""},
    {"row": 8, "nome": "Gabriela Ferraz", "email": "gabriela.ferraz@cora-coralina.example.br", "empresa": "Escola Cora Coralina", "email_empresa": "contato@cora-coralina.example.br", "status": "", "data_envio": ""},
    {"row": 9, "nome": "Heitor Esteves", "email": "heitor.esteves@rui-barbosa.example.br", "empresa": "Instituto Rui Barbosa", "email_empresa": "info@rui-barbosa.example.br", "status": "", "data_envio": ""},
    {"row": 10, "nome": "Isabela Pacheco", "email": "isabela.pacheco@monteiro-lobato.example.br", "empresa": "Colégio Monteiro Lobato", "email_empresa": "coord@monteiro-lobato.example.br", "status": "", "data_envio": ""},
    {"row": 11, "nome": "João Queiroz", "email": "joao.queiroz@jose-alencar.example.br", "empresa": "Escola José de Alencar", "email_empresa": "secretaria@jose-alencar.example.br", "status": "", "data_envio": ""},
    {"row": 12, "nome": "Karina Vasconcelos", "email": "karina.vasconcelos@drummond.example.br", "empresa": "Colégio Carlos Drummond", "email_empresa": "contato@drummond.example.br", "status": "", "data_envio": ""},
    {"row": 13, "nome": "Lucas Junqueira", "email": "lucas.junqueira@clarice-lispector.example.br", "empresa": "Liceu Clarice Lispector", "email_empresa": "diretoria@clarice-lispector.example.br", "status": "", "data_envio": ""},
]


# ---------------------------------------------------------------------------
# Ciclo de vida da aplicação (Lifespan)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_template_html()
    yield

# ---------------------------------------------------------------------------
# Aplicação FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Politize — Desktop Mailer App",
    description="Backend oficial da plataforma de automação de e-mails do Politize.",
    version="2.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# Configuração de CORS restritiva
allowed_origins = [o.strip() for o in CONFIG["allowed_origins"].split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# Middleware de Segurança de Cabeçalhos HTTP
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# ---------------------------------------------------------------------------
# Schemas Pydantic com validação rigorosa de segurança
# ---------------------------------------------------------------------------
class SendRequest(BaseModel):
    row: int = Field(..., ge=2, description="Linha correspondente na planilha (>= 2)")
    email: EmailStr = Field(..., description="E-mail válido do destinatário")
    dry_run: bool = Field(False, description="Simular envio sem disparar via Resend")
    subject: str = Field(
        ...,
        min_length=1,
        max_length=998,
        description="Assunto do e-mail (1 a 998 caracteres)",
    )

    @field_validator("subject")
    @classmethod
    def validate_no_crlf(cls, v: str) -> str:
        """Prevenção contra CRLF Header Injection."""
        if "\r" in v or "\n" in v:
            raise ValueError("O assunto não pode conter quebras de linha (CRLF).")
        v_stripped = v.strip()
        if not v_stripped:
            raise ValueError("O assunto não pode ser composto apenas por espaços.")
        return v_stripped


class AddRecipientRequest(BaseModel):
    nome: str = Field(..., min_length=2, max_length=150, description="Nome do destinatário")
    email: EmailStr = Field(..., description="E-mail válido do destinatário")
    empresa: Optional[str] = Field("", max_length=150, description="Nome da empresa ou escola")
    email_empresa: Optional[str] = Field("", max_length=254, description="E-mail corporativo ou institucional da empresa")


class RecipientItem(BaseModel):
    row: int
    nome: str
    email: str
    empresa: Optional[str] = ""
    email_empresa: Optional[str] = ""
    status: str
    data_envio: str


class RecipientsResponse(BaseModel):
    total: int
    source: str
    warning: Optional[str] = None
    recipients: list[RecipientItem]


class SendResponse(BaseModel):
    id: Optional[str]
    data_envio: str
    row: int
    email: str
    simulated: bool


class TemplateUpdateRequest(BaseModel):
    from_name: Optional[str] = Field(None, min_length=1, max_length=100)
    from_email: Optional[EmailStr] = None
    subject: Optional[str] = Field(None, min_length=1, max_length=998)
    hero_subtitle: Optional[str] = None
    hero_button_text: Optional[str] = None
    hero_button_url: Optional[str] = None
    main_message: Optional[str] = None
    cta_title: Optional[str] = None
    cta_subtitle: Optional[str] = None
    cta_button_text: Optional[str] = None
    cta_button_url: Optional[str] = None
    custom_html: Optional[str] = None
    blocks: Optional[list[dict]] = None

    @field_validator("subject")
    @classmethod
    def validate_no_crlf(cls, v: Optional[str]) -> Optional[str]:
        if v and ("\r" in v or "\n" in v):
            raise ValueError("O assunto não pode conter quebras de linha (CRLF).")
        return v.strip() if v else v


class UnsubscribePayload(BaseModel):
    email: EmailStr = Field(..., description="E-mail a ser descadastrado ou reativado")
    token: Optional[str] = Field(None, description="Token de segurança HMAC")


# ---------------------------------------------------------------------------
# Tratamento global de exceções
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Erro não tratado na requisição {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno do servidor. Consulte os logs para mais detalhes."},
    )


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["Diagnóstico"])
async def get_health():
    """Retorna diagnóstico completo do sistema e status dos serviços."""
    creds_file = BASE_DIR / CONFIG["credentials_path"]
    creds_exist = creds_file.exists()
    has_resend = bool(CONFIG["resend_api_key"] and not CONFIG["resend_api_key"].startswith("re_xxxx"))
    has_sheet = bool(CONFIG["spreadsheet_id"] and not CONFIG["spreadsheet_id"].startswith("1Bxi..."))
    msg_cfg = get_message_config()

    sheet_url = f"https://docs.google.com/spreadsheets/d/{CONFIG['spreadsheet_id']}/edit" if CONFIG["spreadsheet_id"] else ""

    return {
        "status": "ok",
        "app_name": "Politize Mailer",
        "version": "2.2.0",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "services": {
            "resend": {
                "configured": has_resend,
                "from_email": f"{msg_cfg['from_name']} <{msg_cfg['from_email']}>",
            },
            "google_sheets": {
                "configured": has_sheet and creds_exist,
                "credentials_file": str(creds_file.name),
                "credentials_found": creds_exist,
                "spreadsheet_id": CONFIG["spreadsheet_id"][:6] + "..." if CONFIG["spreadsheet_id"] else "",
                "spreadsheet_url": sheet_url,
                "sheet_name": CONFIG["sheet_name"],
            },
            "template": {
                "loaded": bool(_COMPILED_HTML),
                "type": "Blocos Visuais" if msg_cfg.get("blocks") else ("Personalizado" if msg_cfg.get("custom_html") else "MJML Compilado"),
            },
        },
        "default_mode": "dry_run" if CONFIG["dry_run"] else "live",
    }


@app.get("/api/recipients", response_model=RecipientsResponse, tags=["Destinatários"])
async def list_recipients():
    """
    Retorna a lista de destinatários lidos da planilha Google Sheets.
    Se o arquivo de credenciais não existir, fornece dados de demonstração
    para que o usuário possa usar a aplicação imediatamente.
    """
    creds_file = BASE_DIR / CONFIG["credentials_path"]
    
    if not creds_file.exists():
        logger.info("Arquivo credentials.json não encontrado. Operando com dados de demonstração.")
        return RecipientsResponse(
            total=len(MOCK_RECIPIENTS),
            source="demo",
            warning="Arquivo credentials.json não encontrado. Exibindo dados de exemplo para demonstração.",
            recipients=[RecipientItem(**r) for r in MOCK_RECIPIENTS],
        )

    try:
        raw = get_recipients(
            str(creds_file),
            CONFIG["spreadsheet_id"],
            CONFIG["sheet_name"],
        )
        items = [RecipientItem(**r) for r in raw]
        return RecipientsResponse(
            total=len(items),
            source="sheets",
            recipients=items,
        )
    except FileNotFoundError:
        return RecipientsResponse(
            total=len(MOCK_RECIPIENTS),
            source="demo",
            warning="Arquivo de credenciais não encontrado. Usando dados de exemplo.",
            recipients=[RecipientItem(**r) for r in MOCK_RECIPIENTS],
        )
    except Exception as e:
        logger.warning(f"Erro ao acessar Google Sheets: {e}. Usando dados de demonstração.")
        return RecipientsResponse(
            total=len(MOCK_RECIPIENTS),
            source="demo",
            warning="Falha ao conectar com o Google Sheets. Modo demonstração ativo.",
            recipients=[RecipientItem(**r) for r in MOCK_RECIPIENTS],
        )


@app.post("/api/recipients", tags=["Destinatários"])
async def create_recipient(payload: AddRecipientRequest):
    """
    Adiciona 1 novo e-mail/destinatário com nome da empresa e email da empresa:
    - Se conectado ao Google Sheets, grava uma nova linha na planilha oficial.
    - Se estiver em modo demonstração, adiciona à lista da sessão em memória.
    """
    creds_file = BASE_DIR / CONFIG["credentials_path"]
    is_live_sheet = creds_file.exists() and bool(CONFIG["spreadsheet_id"])
    
    clean_name = payload.nome.strip()
    clean_email = str(payload.email).strip().lower()
    clean_empresa = (payload.empresa or "").strip()
    clean_email_empresa = (payload.email_empresa or "").strip().lower()

    if is_live_sheet:
        try:
            current = get_recipients(str(creds_file), CONFIG["spreadsheet_id"], CONFIG["sheet_name"])
            if any(r["email"].lower() == clean_email for r in current):
                raise HTTPException(
                    status_code=409,
                    detail=f"O e-mail '{clean_email}' já está registrado na lista de disparos.",
                )
            new_item = add_recipient(
                str(creds_file),
                CONFIG["spreadsheet_id"],
                CONFIG["sheet_name"],
                nome=clean_name,
                email=clean_email,
                empresa=clean_empresa,
                email_empresa=clean_email_empresa,
            )
            return {"status": "ok", "recipient": new_item, "source": "sheets"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro ao gravar novo destinatário na planilha: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Falha ao adicionar na planilha: {e}")
    else:
        # Modo demonstração
        if any(r["email"].lower() == clean_email for r in MOCK_RECIPIENTS):
            raise HTTPException(
                status_code=409,
                detail=f"O e-mail '{clean_email}' já está registrado na lista de disparos.",
            )
        next_row = max((r["row"] for r in MOCK_RECIPIENTS), default=1) + 1
        new_item = {
            "row": next_row,
            "nome": clean_name,
            "email": clean_email,
            "empresa": clean_empresa,
            "email_empresa": clean_email_empresa,
            "status": "",
            "data_envio": "",
        }
        MOCK_RECIPIENTS.append(new_item)
        logger.info(f"Destinatário adicionado à lista demo (linha {next_row}): {clean_name} <{clean_email}>")
        return {"status": "ok", "recipient": new_item, "source": "demo"}


@app.post("/api/send", response_model=SendResponse, tags=["Envio"])
async def send_single(payload: SendRequest):
    """
    Processa o envio de um e-mail individual com validação anti-tampering e bloqueio de opt-out:
    - Destinatários descadastrados são estritamente rejeitados.
    - Injeta a URL de descadastro personalizada com token HMAC.
    - Em modo dry_run=True, simula o envio sem consumir créditos nem gravar na planilha.
    - Em modo real, dispara pela API do Resend e registra status na planilha.
    """
    creds_file = BASE_DIR / CONFIG["credentials_path"]
    is_live_sheet = creds_file.exists() and bool(CONFIG["spreadsheet_id"])

    # 1. Carrega lista para validar o destinatário da linha solicitada
    if is_live_sheet:
        try:
            recipients = get_recipients(str(creds_file), CONFIG["spreadsheet_id"], CONFIG["sheet_name"])
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Erro ao verificar planilha: {e}")
    else:
        recipients = MOCK_RECIPIENTS

    # Busca destinatário
    target = next((r for r in recipients if r["row"] == payload.row), None)
    if not target:
        raise HTTPException(
            status_code=404,
            detail=f"Destinatário na linha {payload.row} não encontrado.",
        )

    # Prevenção de adulteração: o e-mail deve bater com a linha informada
    if target["email"].lower() != payload.email.lower():
        raise HTTPException(
            status_code=400,
            detail="O e-mail informado não corresponde ao cadastrado nesta linha.",
        )

    # Bloqueio de e-mails descadastrados (Opt-out)
    target_status = str(target.get("status", "")).strip().lower()
    if target_status in ("descadastrado", "optout", "opt-out"):
        raise HTTPException(
            status_code=400,
            detail=f"O destinatário '{payload.email}' solicitou descadastro e não pode receber novos envios.",
        )

    # 2. Renderiza o template oficial com URL de descadastro e variáveis
    unsub_token = generate_unsubscribe_token(target["email"])
    unsub_url = f"{CONFIG['app_base_url']}/api/unsubscribe?email={target['email']}&token={unsub_token}"
    html_template = load_template_html()
    rendered_html = render_template(
        html_template,
        {
            "nome": target["nome"],
            "empresa": target.get("empresa", ""),
            "email_empresa": target.get("email_empresa", ""),
            "unsubscribe_url": unsub_url,
        },
    )

    # 3. Disparo ou simulação
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if payload.dry_run:
        logger.info(f"[SIMULAÇÃO] Envio para {payload.email} (Linha {payload.row})")
        return SendResponse(
            id=f"sim_{payload.row}_{int(datetime.now().timestamp())}",
            data_envio=timestamp,
            row=payload.row,
            email=payload.email,
            simulated=True,
        )

    # Disparo real
    if not CONFIG["resend_api_key"] or CONFIG["resend_api_key"].startswith("re_xxxx"):
        raise HTTPException(
            status_code=400,
            detail="Chave RESEND_API_KEY não configurada no arquivo .env para envios reais.",
        )

    msg_cfg = get_message_config()
    from_display = f"{msg_cfg['from_name']} <{msg_cfg['from_email']}>"

    try:
        email_id = send_email(
            api_key=CONFIG["resend_api_key"],
            from_email=from_display,
            to_email=payload.email,
            subject=payload.subject,
            html_body=rendered_html,
            dry_run=False,
        )
    except Exception as e:
        logger.error(f"Erro na API do Resend ao enviar para {payload.email}: {e}")
        raise HTTPException(status_code=502, detail=f"Erro no provedor de e-mail (Resend): {e}")

    # Atualiza a planilha no envio real bem-sucedido
    if is_live_sheet:
        try:
            mark_as_sent(str(creds_file), CONFIG["spreadsheet_id"], CONFIG["sheet_name"], row=payload.row, timestamp=timestamp)
        except Exception as e:
            logger.error(f"E-mail enviado mas falhou atualização no Google Sheets: {e}")

    return SendResponse(
        id=email_id or "sent",
        data_envio=timestamp,
        row=payload.row,
        email=payload.email,
        simulated=False,
    )


# ---------------------------------------------------------------------------
# ROTAS DE DESCADASTRO (UNSUBSCRIBE & RESUBSCRIBE)
# ---------------------------------------------------------------------------

@app.get("/unsubscribe", response_class=HTMLResponse, tags=["Descadastro"], include_in_schema=False)
@app.get("/api/unsubscribe", response_class=HTMLResponse, tags=["Descadastro"])
async def unsubscribe_page(
    email: str = Query(..., min_length=3, max_length=254, description="E-mail a ser descadastrado"),
    token: Optional[str] = Query(None, description="Token de segurança HMAC"),
):
    """
    Página oficial de descadastro acessada diretamente pelo botão no e-mail:
    - Atualiza imediatamente o status para 'Descadastrado' no Google Sheets (ou dados de sessão).
    - Exibe uma interface amigável com opção para reativar caso tenha clicado por engano.
    """
    clean_email = email.strip().lower()
    safe_email = html_lib.escape(clean_email)
    creds_file = BASE_DIR / CONFIG["credentials_path"]
    is_live_sheet = creds_file.exists() and bool(CONFIG["spreadsheet_id"])

    # Executa o descadastro
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated = False

    if is_live_sheet:
        try:
            updated = unsubscribe_recipient(str(creds_file), CONFIG["spreadsheet_id"], CONFIG["sheet_name"], clean_email, timestamp=timestamp)
        except Exception as e:
            logger.error(f"Erro ao processar descadastro na planilha para {clean_email}: {e}")
    else:
        # Modo demonstração
        for r in MOCK_RECIPIENTS:
            if r["email"].lower() == clean_email:
                r["status"] = "Descadastrado"
                r["data_envio"] = timestamp
                updated = True
                break

    logger.info(f"Descadastro processado para {clean_email} (Atualizado na planilha: {updated})")

    resub_token = token or generate_unsubscribe_token(clean_email)

    page_html = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Inscrição Cancelada — Politize</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    body {{
      margin: 0; background-color: #F4F4F5; font-family: 'Instrument Sans', system-ui, sans-serif;
      display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; box-sizing: border-box;
      color: #16213E;
    }}
    .card {{
      background: #ffffff; border-radius: 16px; padding: 40px 32px; max-width: 480px; width: 100%;
      text-align: center; box-shadow: 0 10px 30px rgba(15,24,48,0.08); border: 1px solid #E2E6EF;
    }}
    .badge {{
      display: inline-flex; align-items: center; justify-content: center; width: 56px; height: 56px;
      border-radius: 50%; background: #DDF1EA; color: #17795E; font-size: 26px; margin-bottom: 20px;
    }}
    h1 {{ font-size: 24px; font-weight: 700; margin: 0 0 12px; color: #16213E; }}
    p {{ font-size: 15px; color: #5A6682; line-height: 1.6; margin: 0 0 24px; }}
    .email-box {{
      background: #FAFBFD; border: 1px solid #D9DEE8; border-radius: 8px; padding: 10px 14px;
      font-weight: 600; font-size: 14.5px; color: #16213E; margin-bottom: 24px; word-break: break-all;
    }}
    .btn {{
      display: inline-block; background: #FF6B35; color: #ffffff; text-decoration: none; padding: 12px 24px;
      border-radius: 8px; font-weight: 600; font-size: 14.5px; cursor: pointer; border: 0; transition: background .2s;
    }}
    .btn:hover {{ background: #E8551F; }}
    .btn-secondary {{
      background: transparent; color: #5A6682; border: 1px solid #D9DEE8; margin-top: 10px;
    }}
    .btn-secondary:hover {{ background: #F2F4F8; color: #16213E; }}
    .status-msg {{ margin-top: 18px; font-size: 14px; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">✓</div>
    <h1>Inscrição Cancelada</h1>
    <p>Você foi descadastrado(a) com sucesso da nossa lista de contatos educacionais.</p>
    <div class="email-box">{safe_email}</div>
    <p style="font-size:13.5px;margin-bottom:20px;">Você não receberá mais e-mails desta automação. O status já foi registrado em nossa planilha oficial.</p>
    
    <div id="actionArea">
      <button class="btn btn-secondary" id="resubBtn" type="button">Foi um engano? Quero me reinscrever</button>
      <div id="statusResult" class="status-msg" style="display:none;"></div>
    </div>
  </div>

  <script>
    document.getElementById("resubBtn").addEventListener("click", async () => {{
      const btn = document.getElementById("resubBtn");
      const st = document.getElementById("statusResult");
      btn.disabled = true;
      btn.textContent = "Reativando...";
      try {{
        const res = await fetch("/api/resubscribe", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ email: "{clean_email}", token: "{resub_token}" }})
        }});
        if (!res.ok) throw new Error("Falha ao reativar");
        st.style.display = "block";
        st.style.color = "#17795E";
        st.textContent = "✓ Inscrição reativada com sucesso! Você voltará a receber nossos comunicados.";
        btn.style.display = "none";
      }} catch (e) {{
        st.style.display = "block";
        st.style.color = "#B42342";
        st.textContent = "Erro ao reativar: " + e.message;
        btn.disabled = false;
        btn.textContent = "Tentar novamente";
      }}
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(content=page_html, status_code=200)


@app.post("/api/unsubscribe", tags=["Descadastro"])
async def api_unsubscribe(payload: UnsubscribePayload):
    """Endpoint da API para efetuar o descadastro programático de um destinatário."""
    clean_email = payload.email.strip().lower()
    creds_file = BASE_DIR / CONFIG["credentials_path"]
    is_live_sheet = creds_file.exists() and bool(CONFIG["spreadsheet_id"])

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated = False

    if is_live_sheet:
        try:
            updated = unsubscribe_recipient(str(creds_file), CONFIG["spreadsheet_id"], CONFIG["sheet_name"], clean_email, timestamp=timestamp)
        except Exception as e:
            logger.error(f"Erro ao descadastrar na planilha: {e}")
            raise HTTPException(status_code=500, detail=f"Erro na planilha: {e}")
    else:
        for r in MOCK_RECIPIENTS:
            if r["email"].lower() == clean_email:
                r["status"] = "Descadastrado"
                r["data_envio"] = timestamp
                updated = True
                break

    return {"status": "ok", "message": f"Destinatário '{clean_email}' descadastrado com sucesso.", "updated": updated}


@app.post("/api/resubscribe", tags=["Descadastro"])
async def api_resubscribe(payload: UnsubscribePayload):
    """Reativa a inscrição de um destinatário que havia sido descadastrado."""
    clean_email = payload.email.strip().lower()
    creds_file = BASE_DIR / CONFIG["credentials_path"]
    is_live_sheet = creds_file.exists() and bool(CONFIG["spreadsheet_id"])
    updated = False

    if is_live_sheet:
        try:
            updated = resubscribe_recipient(str(creds_file), CONFIG["spreadsheet_id"], CONFIG["sheet_name"], clean_email)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro na planilha: {e}")
    else:
        for r in MOCK_RECIPIENTS:
            if r["email"].lower() == clean_email:
                r["status"] = ""
                r["data_envio"] = ""
                updated = True
                break

    return {"status": "ok", "message": f"Inscrição de '{clean_email}' reativada com sucesso.", "updated": updated}


# ---------------------------------------------------------------------------
# GESTÃO DO MODELO DE E-MAIL E DISPLAY
# ---------------------------------------------------------------------------

@app.get("/api/template", tags=["Modelo de E-mail"])
async def get_template_settings():
    """Retorna as configurações atuais da mensagem, textos e display do e-mail."""
    return get_message_config()


@app.post("/api/template", tags=["Modelo de E-mail"])
async def update_template_settings(payload: TemplateUpdateRequest):
    """
    Atualiza a mensagem que será enviada e o display do e-mail (Nome de exibição e From).
    Recompila o template em tempo real e persiste as alterações.
    """
    current = get_message_config()
    update_data = payload.model_dump(exclude_unset=True)

    for k, v in update_data.items():
        if v is not None:
            current[k] = v

    # Atualiza variáveis em memória
    if payload.subject:
        CONFIG["email_subject"] = payload.subject
    if payload.from_name or payload.from_email:
        name = current.get("from_name", "Politize")
        addr = current.get("from_email", "noreply@politize.com.br")
        CONFIG["from_email"] = f"{name} <{addr}>"

    save_message_config(current)
    load_template_html()
    logger.info("Modelo de e-mail e configurações de display atualizados com sucesso.")
    return {"status": "ok", "message": "Configurações de mensagem e display salvas com sucesso!", "config": current}


@app.post("/api/template/reset", tags=["Modelo de E-mail"])
async def reset_template_settings():
    """Restaura o modelo de e-mail e display para o padrão original."""
    if TEMPLATE_CONFIG_FILE.exists():
        TEMPLATE_CONFIG_FILE.unlink(missing_ok=True)
    load_template_html()
    return {"status": "ok", "message": "Modelo restaurado para o padrão original com sucesso!", "config": DEFAULT_MESSAGE_CONFIG}


@app.get("/api/preview", response_class=HTMLResponse, tags=["Prévia"])
async def preview_email(
    nome: str = Query("Educador(a)", max_length=100),
    empresa: Optional[str] = Query("", max_length=150),
):
    """
    Retorna o HTML real compilado do template com as variáveis do destinatário e link funcional de descadastro.
    Ideal para exibição em iframe no frontend.
    """
    safe_name = html_lib.escape(nome.strip() or "Educador(a)")
    safe_empresa = html_lib.escape(empresa.strip() or "Sua Escola")
    unsub_token = generate_unsubscribe_token("exemplo@escola.edu.br")
    unsub_url = f"{CONFIG['app_base_url']}/api/unsubscribe?email=exemplo@escola.edu.br&token={unsub_token}"
    template = load_template_html()
    rendered = render_template(
        template,
        {
            "nome": safe_name,
            "empresa": safe_empresa,
            "email_empresa": "contato@escola.edu.br",
            "unsubscribe_url": unsub_url,
        },
    )
    return HTMLResponse(content=rendered, status_code=200)


@app.get("/api/logs", tags=["Diagnóstico"])
async def get_logs(limit: int = Query(50, ge=1, le=200)):
    """Retorna os eventos mais recentes gravados em envios.log."""
    log_file = BASE_DIR / "envios.log"
    if not log_file.exists():
        return {"lines": []}
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        return {"lines": lines[-limit:]}
    except Exception as e:
        return {"lines": [f"Erro ao ler logs: {e}"]}


# ---------------------------------------------------------------------------
# ROTAS DO FRONTEND (App Web)
# ---------------------------------------------------------------------------

@app.get("/manifest.json", include_in_schema=False)
async def pwa_manifest():
    return JSONResponse({
        "name": "Politize — Automação de E-mails",
        "short_name": "Politize Mailer",
        "description": "Painel de automação e envio de campanhas de e-mails para escolas e educadores.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#F2F4F8",
        "theme_color": "#FF6B35",
        "icons": [
            {
                "src": "/favicon.svg",
                "sizes": "any",
                "type": "image/svg+xml"
            }
        ]
    })


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
      <rect width="32" height="32" rx="8" fill="#FF6B35"/>
      <path d="M4 8 L16 19 L28 8" fill="none" stroke="#16213E" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <rect x="4" y="8" width="24" height="16" rx="3" fill="none" stroke="#16213E" stroke-width="2.5"/>
    </svg>"""
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/", include_in_schema=False)
async def serve_index():
    index_file = BASE_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html não encontrado na pasta raiz.")
    return FileResponse(str(index_file), media_type="text/html")
