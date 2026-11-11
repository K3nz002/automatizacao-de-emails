# 📧 Politize Mailer — Automação de E-mails & Desktop Web App

> Sistema completo de automação e disparo de e-mails transacionais e educacionais utilizando **FastAPI**, **Resend API**, **Google Sheets**, templates responsivos em **MJML** e interface estilo **Desktop Web App**.

---

## ✨ Funcionalidades

- 💻 **Desktop Web App Integrado** — interface gráfica moderna, responsiva e pronta para uso via navegador ou PWA.
- 🗺️ **Mapa Visual de Disparos** — mosaico interativo de envelopes com acompanhamento em tempo real (enviados, pendentes, erros, simulações).
- 📋 **Integração com Google Sheets** — leitura e sincronização automática de destinatários com tratamento inteligente de cabeçalhos.
- 👁️ **Prévia Oficial em Tempo Real** — renderização do template MJML oficial com interpolação de dados reais, alternância entre visualizações Desktop (640px) e Celular (375px).
- 🔒 **Prevenção de Duplicatas e Adulteração** — verificação anti-tampering (Row ↔ Email) e verificação O(1) de destinatários já contatados.
- 🛡️ **Segurança em Camadas** — proteção contra CRLF Injection no cabeçalho do e-mail, sanitização contra XSS, cabeçalhos de defesa HTTP (`nosniff`, `SAMEORIGIN`, `xss-protection`) e controle rigoroso de CORS.
- 🚀 **Envio via Resend API** — cliente oficial Python com tratamento resiliente de cotas e erros.
- 🧪 **Modo Simulação (Dry Run)** — teste campanhas inteiras sem consumir créditos e sem alterar a planilha.
- 📊 **Diagnóstico do Sistema** — verificação ao vivo da integridade das credenciais, conexão com Sheets e disponibilidade do Resend.
- 🪵 **Logs e Auditoria** — registro em tempo real na interface com opção de copiar e exportar logs, além de gravação em `envios.log`.

---

## 🗂️ Estrutura do Projeto

```
automatizacao-de-emails/
├── app.py                      # Launcher do aplicativo (abre navegador automaticamente)
├── api.py                      # Backend FastAPI (serviços REST, segurança e estáticos)
├── index.html                  # Interface gráfica Desktop Web App (PWA)
├── iniciar_app.bat             # Atalho de inicialização rápida para Windows
├── main.py                     # Script alternativo para execução via linha de comando (CLI)
├── email_sender.py             # Compilação MJML + renderização + envio via Resend
├── sheets_client.py            # Integração e sincronização com Google Sheets
├── test_security.py            # Suíte completa de testes de segurança automatizados
├── test.py                     # Script rápido para envio de validação única
├── templates/
│   ├── email_template.mjml     # Template MJML fonte responsivo
│   └── email_template.html     # Cache compilado para alta velocidade e fallback
├── .env                        # Variáveis de ambiente locais (NÃO versionar!)
├── .env.example                # Modelo de configuração de ambiente
├── requirements.txt            # Dependências Python atualizadas
└── README.md                   # Documentação do projeto
```

---

## 🚀 Como Executar

### 1. Instale as Dependências

```bash
pip install -r requirements.txt
```

---

### 2. Inicie o Aplicativo

Você pode iniciar o aplicativo de duas formas:

#### Opção A: Executar via Python (Recomendado)
```bash
python app.py
```
> O terminal exibirá o status do servidor e abrirá automaticamente seu navegador padrão em `http://127.0.0.1:8000`.

#### Opção B: Dois cliques no Windows
Dê um duplo clique no arquivo:
```
iniciar_app.bat
```

#### Opção C: Linha de comando direta (Modo CLI clássico)
```bash
# Execução direta com a planilha
python main.py

# Ou em modo de simulação
$env:DRY_RUN="True"; python main.py
```

---

## 🛡️ Testes de Segurança

O projeto inclui uma suíte completa de testes de segurança automatizados que validam:
- Prevenção contra CRLF Header Injection no assunto do e-mail.
- Validação estrita de tipos com Pydantic (`EmailStr`, limites de tamanho).
- Proteção anti-adulteração de requisições (Row ↔ Email).
- Sanitização contra injeção de scripts (XSS).
- Validação de isolamento do modo simulação (`dry_run`).
- Cabeçalhos de defesa HTTP e políticas de CORS.
- Ocultação de stack traces e dados internos em falhas de servidor.

Para executar os testes:
```bash
pytest test_security.py -v
```

---

## ⚙️ Configuração das Credenciais (`.env`)

Copie o `.env.example` para `.env` e preencha as variáveis:

```env
# API do Resend (https://resend.com/api-keys)
RESEND_API_KEY=re_sua_chave_aqui
FROM_EMAIL=Politize <onboarding@resend.dev>

# Google Sheets
SPREADSHEET_ID=seu_id_da_planilha
SHEET_NAME=Destinatários
GOOGLE_CREDENTIALS_PATH=credentials.json

# Assunto e Intervalo
EMAIL_SUBJECT=Conheça o Politize — Educação Política para sua Escola
SEND_INTERVAL_SECONDS=1
DRY_RUN=False
```

> 💡 **Modo Demonstração Automático**: Se você ainda não configurou o arquivo `credentials.json` do Google Cloud, a aplicação detecta automaticamente e inicia no **Modo Demonstração**, permitindo explorar todas as funcionalidades, testar a prévia do e-mail e simular disparos sem quebrar o sistema.

---

## 📡 Endpoints da API

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Interface gráfica do aplicativo (Desktop Web App / PWA) |
| `GET` | `/api/health` | Diagnóstico completo de saúde e status dos serviços |
| `GET` | `/api/recipients` | Lista destinatários da planilha ou dados de demonstração |
| `POST` | `/api/recipients` | Adiciona 1 novo e-mail/destinatário à lista de disparos (Sheets ou demo) |
| `POST` | `/api/send` | Dispara ou simula o envio para um destinatário com validação anti-tampering |
| `GET` | `/api/template` | Consulta a configuração da mensagem, textos e display do e-mail |
| `POST` | `/api/template` | Atualiza e salva a mensagem e o display do e-mail em tempo real |
| `POST` | `/api/template/reset` | Restaura as mensagens e o display para os valores originais de fábrica |
| `GET` | `/api/preview` | Retorna o HTML real compilado do template MJML para o destinatário |
| `GET` | `/api/logs` | Retorna os eventos recentes registrados em `envios.log` |
| `GET` | `/docs` | Documentação interativa Swagger/OpenAPI |
