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
    return {
        "resend_api_key":       os.getenv("RESEND_API_KEY", ""),
        "from_email":           os.getenv("FROM_EMAIL", "Politize <noreply@politize.com.br>"),
        "spreadsheet_id":       os.getenv("SPREADSHEET_ID", ""),
        "sheet_name":           os.getenv("SHEET_NAME", "Destinatários"),
        "credentials_path":     os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
        "email_subject":        os.getenv("EMAIL_SUBJECT", "Conheça o Politize — Educação Política para sua Escola"),
        "send_interval":        float(os.getenv("SEND_INTERVAL_SECONDS", "1") or "1"),
        "dry_run":              os.getenv("DRY_RUN", "False").lower() in ("true", "1", "yes"),
        "template_path":        str(BASE_DIR / "templates" / "email_template.mjml"),
        "allowed_origins":      os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"),
    }

CONFIG = get_config()

# ---------------------------------------------------------------------------
# Importação dos módulos do domínio
# ---------------------------------------------------------------------------
from email_sender import compile_mjml, render_template, send_email  # noqa: E402
from sheets_client import add_recipient, get_recipients, mark_as_sent  # noqa: E402

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
    """Aplica as mensagens personalizadas ao template base compilado."""
    # Se houver custom_html explícito definido pelo usuário, utiliza diretamente
    if cfg.get("custom_html") and cfg["custom_html"].strip():
        return cfg["custom_html"]

    # Carrega o HTML base compilado do MJML
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
        base_html = """<!doctype html><html><head><meta charset="utf-8"><title>Politize</title></head>
        <body style="font-family:sans-serif;background:#F4F4F5;padding:40px;color:#16213E;">
          <div style="max-width:600px;margin:auto;background:#fff;padding:32px;border-radius:12px;">
            <h1 style="color:#FF6B35;">⚖️ POLITIZE</h1>
            <p style="font-weight:600;color:#FF6B35;text-transform:uppercase;">{{hero_tag}}</p>
            <h2>{{hero_title}}</h2>
            <p style="font-size:18px;color:#555;">{{hero_subtitle}}</p>
            <p style="font-size:16px;line-height:1.6;color:#333;">{{main_message}}</p>
            <div style="text-align:center;margin:30px 0;">
              <a href="{{hero_button_url}}" style="background:#FF6B35;color:#fff;padding:14px 28px;text-decoration:none;border-radius:8px;font-weight:bold;">{{hero_button_text}}</a>
            </div>
            <div style="background:#FFF3EB;padding:24px;border-radius:10px;text-align:center;margin-top:30px;">
              <h3 style="margin:0 0 8px;color:#8A2E0B;">{{cta_title}}</h3>
              <p style="margin:0 0 16px;color:#666;">{{cta_subtitle}}</p>
              <a href="{{cta_button_url}}" style="background:#8A2E0B;color:#fff;padding:10px 20px;text-decoration:none;border-radius:6px;font-weight:bold;">{{cta_button_text}}</a>
            </div>
          </div>
        </body></html>"""

    # Injeta os textos customizados no HTML
    html = base_html
    # Substituições de campos-chave
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
    {"row": 2, "nome": "Ana Luiza Barbosa", "email": "ana.barbosa@escola-darcy.example.br", "status": "Enviado", "data_envio": "2026-11-10 14:15:22"},
    {"row": 3, "nome": "Bruno Ribeiro", "email": "bruno.ribeiro@lourenco-filho.example.br", "status": "Enviado", "data_envio": "2026-11-10 14:15:24"},
    {"row": 4, "nome": "Carla Nogueira", "email": "carla.nogueira@paulo-freire.example.br", "status": "", "data_envio": ""},
    {"row": 5, "nome": "Daniel Teixeira", "email": "daniel.teixeira@anisio-teixeira.example.br", "status": "", "data_envio": ""},
    {"row": 6, "nome": "Elisa Duarte", "email": "elisa.duarte@cecilia-meireles.example.br", "status": "", "data_envio": ""},
    {"row": 7, "nome": "Fábio Guimarães", "email": "fabio.guimaraes@machado-assis.example.br", "status": "", "data_envio": ""},
    {"row": 8, "nome": "Gabriela Ferraz", "email": "gabriela.ferraz@cora-coralina.example.br", "status": "", "data_envio": ""},
    {"row": 9, "nome": "Heitor Esteves", "email": "heitor.esteves@rui-barbosa.example.br", "status": "", "data_envio": ""},
    {"row": 10, "nome": "Isabela Pacheco", "email": "isabela.pacheco@monteiro-lobato.example.br", "status": "", "data_envio": ""},
    {"row": 11, "nome": "João Queiroz", "email": "joao.queiroz@jose-alencar.example.br", "status": "", "data_envio": ""},
    {"row": 12, "nome": "Karina Vasconcelos", "email": "karina.vasconcelos@drummond.example.br", "status": "", "data_envio": ""},
    {"row": 13, "nome": "Lucas Junqueira", "email": "lucas.junqueira@clarice-lispector.example.br", "status": "", "data_envio": ""},
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


class RecipientItem(BaseModel):
    row: int
    nome: str
    email: str
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

    @field_validator("subject")
    @classmethod
    def validate_no_crlf(cls, v: Optional[str]) -> Optional[str]:
        if v and ("\r" in v or "\n" in v):
            raise ValueError("O assunto não pode conter quebras de linha (CRLF).")
        return v.strip() if v else v


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

    return {
        "status": "ok",
        "app_name": "Politize Mailer",
        "version": "2.1.0",
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
                "sheet_name": CONFIG["sheet_name"],
            },
            "template": {
                "loaded": bool(_COMPILED_HTML),
                "type": "Personalizado" if msg_cfg.get("custom_html") else "MJML Compilado",
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
    except FileNotFoundError as e:
        return RecipientsResponse(
            total=len(MOCK_RECIPIENTS),
            source="demo",
            warning=f"Credenciais não encontradas: {e}. Usando dados de exemplo.",
            recipients=[RecipientItem(**r) for r in MOCK_RECIPIENTS],
        )
    except Exception as e:
        logger.warning(f"Erro ao acessar Google Sheets: {e}. Usando dados de demonstração.")
        return RecipientsResponse(
            total=len(MOCK_RECIPIENTS),
            source="demo",
            warning=f"Falha ao conectar com o Google Sheets ({e}). Modo demonstração ativo.",
            recipients=[RecipientItem(**r) for r in MOCK_RECIPIENTS],
        )


@app.post("/api/recipients", tags=["Destinatários"])
async def create_recipient(payload: AddRecipientRequest):
    """
    Adiciona 1 novo e-mail/destinatário para entrar na lista de disparos:
    - Se conectado ao Google Sheets, grava uma nova linha na planilha oficial.
    - Se estiver em modo demonstração, adiciona à lista da sessão em memória.
    """
    creds_file = BASE_DIR / CONFIG["credentials_path"]
    is_live_sheet = creds_file.exists() and bool(CONFIG["spreadsheet_id"])
    
    clean_name = payload.nome.strip()
    clean_email = str(payload.email).strip().lower()

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
                clean_name,
                clean_email,
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
            "status": "",
            "data_envio": "",
        }
        MOCK_RECIPIENTS.append(new_item)
        logger.info(f"Destinatário adicionado à lista demo (linha {next_row}): {clean_name} <{clean_email}>")
        return {"status": "ok", "recipient": new_item, "source": "demo"}


@app.post("/api/send", response_model=SendResponse, tags=["Envio"])
async def send_single(payload: SendRequest):
    """
    Processa o envio de um e-mail individual com validação anti-tampering:
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

    # 2. Renderiza o template oficial
    html_template = load_template_html()
    rendered_html = render_template(html_template, {"nome": target["nome"]})

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
async def preview_email(nome: str = Query("Educador(a)", max_length=100)):
    """
    Retorna o HTML real compilado do template com o nome do destinatário interpolado.
    Ideal para exibição em iframe no frontend.
    """
    safe_name = html_lib.escape(nome.strip() or "Educador(a)")
    template = load_template_html()
    rendered = render_template(template, {"nome": safe_name})
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
