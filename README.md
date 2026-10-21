# 📧 Automação de E-mails — Politize

> Automação de envio de e-mails transacionais/marketing utilizando **Resend API**, **Google Sheets** e templates responsivos em **MJML**.

---

## ✨ Funcionalidades

- 📋 **Leitura do Google Sheets** — busca destinatários (Nome, E-mail, Status, Data de Envio)
- 🔒 **Prevenção de duplicatas** — verifica envios anteriores antes de cada disparo
- 📝 **Templates MJML responsivos** — compilados em tempo de execução para HTML otimizado
- 🚀 **Envio via Resend API** — biblioteca oficial Python do Resend
- ✅ **Atualização automática da planilha** — registra "Enviado" + timestamp após cada disparo
- 🪵 **Log detalhado** — console + arquivo `envios.log`
- 🧪 **Modo Dry Run** — simula o envio sem consumir créditos da API

---

## 🗂️ Estrutura do Projeto

```
automatizacao-de-emails/
├── main.py                     # Script principal (orquestrador)
├── email_sender.py             # Compilação MJML + envio via Resend
├── sheets_client.py            # Integração com Google Sheets
├── templates/
│   └── email_template.mjml     # Template MJML responsivo (com {{nome}})
├── .env                        # Variáveis de ambiente (NÃO versionar!)
├── .env.example                # Modelo de variáveis de ambiente
├── credentials.json            # Credenciais da Service Account (NÃO versionar!)
├── requirements.txt            # Dependências Python
├── envios.log                  # Log gerado automaticamente na execução
└── README.md
```

---

## 🚀 Configuração e Uso

### 1. Instale as dependências

```bash
pip install -r requirements.txt
```

> **Requisito opcional**: Para compilar MJML, você precisa de **Node.js** instalado.
> Instale o CLI do MJML globalmente:
> ```bash
> npm install -g mjml
> ```
> Ou instale o pacote Python (que usa Node internamente):
> ```bash
> pip install mjml
> ```

---

### 2. Configure o Google Sheets

#### a) Crie uma Service Account no Google Cloud

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/)
2. Crie ou selecione um projeto
3. Ative as APIs: **Google Sheets API** e **Google Drive API**
4. Vá em **IAM & Admin → Service Accounts → Create Service Account**
5. Baixe o arquivo JSON de credenciais e salve como `credentials.json` na raiz do projeto

#### b) Compartilhe a planilha com a Service Account

- Abra sua planilha no Google Sheets
- Clique em **Compartilhar** e adicione o e-mail da Service Account (ex.: `bot@projeto.iam.gserviceaccount.com`) com permissão de **Editor**

#### c) Formato da planilha

A planilha deve ter exatamente este cabeçalho na primeira linha:

| Nome     | Email                  | Status | Data de Envio |
|----------|------------------------|--------|---------------|
| João Silva | joao@escola.com.br  |        |               |
| Maria Souza | maria@escola.com.br |       |               |

---

### 3. Configure o arquivo `.env`

Copie o arquivo de exemplo e preencha os valores:

```bash
cp .env.example .env
```

Edite o `.env`:

```env
RESEND_API_KEY=re_sua_chave_aqui
FROM_EMAIL=Politize <noreply@seudominio.com>
SPREADSHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
SHEET_NAME=Destinatários
GOOGLE_CREDENTIALS_PATH=credentials.json
EMAIL_SUBJECT=Conheça o Politize — Educação Política para sua Escola
SEND_INTERVAL_SECONDS=1
DRY_RUN=False
```

> ⚠️ **Importante**: Adicione `.env` e `credentials.json` ao `.gitignore`!

---

### 4. Execute o script

```bash
# Envio real
python main.py

# Modo de teste (sem enviar e-mails de verdade)
DRY_RUN=True python main.py
```

---

## 🔧 Personalização do Template

O template MJML está em [`templates/email_template.mjml`](templates/email_template.mjml).

### Variáveis disponíveis

| Variável   | Descrição                         |
|------------|-----------------------------------|
| `{{nome}}` | Nome do destinatário da planilha  |

Para adicionar mais variáveis, edite o template MJML e adicione a coluna correspondente na planilha.

**Exemplo de nova variável:**

1. Adicione `{{escola}}` no template MJML
2. Adicione a coluna `Escola` na planilha
3. Atualize o `main.py` para passar a nova variável:
   ```python
   html_body = render_template(html_template, {
       "nome": nome,
       "escola": recipient.get("escola", "sua escola"),
   })
   ```

---

## 🛡️ Segurança e Boas Práticas

- ✅ Nunca versione `.env` ou `credentials.json`
- ✅ Use `DRY_RUN=True` para testar antes de disparar e-mails em produção
- ✅ Configure `SEND_INTERVAL_SECONDS` para respeitar os limites de rate da API Resend
- ✅ Verifique seu domínio no Resend antes de usar `FROM_EMAIL` com domínio customizado

---

## 📊 Exemplo de Saída

```
2026-10-17 17:30:00 [INFO] __main__ — ============================================================
2026-10-17 17:30:00 [INFO] __main__ —   Iniciando automação de e-mails — Politize
2026-10-17 17:30:01 [INFO] sheets_client — 3 destinatário(s) lido(s) da planilha.
2026-10-17 17:30:01 [INFO] sheets_client — 1 e-mail(s) já marcado(s) como enviado na planilha.
2026-10-17 17:30:02 [INFO] email_sender — E-mail enviado com sucesso para joao@escola.com.br | ID: abc123
2026-10-17 17:30:03 [INFO] __main__ — [IGNORADO] maria@escola.com.br — e-mail já enviado anteriormente.
2026-10-17 17:30:04 [INFO] __main__ —   ✅ Enviados com sucesso : 1
2026-10-17 17:30:04 [INFO] __main__ —   ⏭️  Ignorados (duplicata): 1
2026-10-17 17:30:04 [INFO] __main__ —   ❌ Erros                : 0
```

---

## 📦 Dependências

| Pacote           | Uso                                      |
|------------------|------------------------------------------|
| `resend`         | Cliente oficial da API Resend            |
| `gspread`        | Integração com Google Sheets             |
| `google-auth`    | Autenticação via Service Account         |
| `python-dotenv`  | Carregamento do arquivo `.env`           |
| `mjml`           | Compilador MJML para Python              |
