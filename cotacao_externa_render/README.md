# 🚀 COTAÇÃO EXTERNA - RENDER

Sistema de cotação externa para fornecedores, isolado e seguro.

---

## 📋 Índice

1. [Arquitetura](#arquitetura)
2. [Deploy no Render](#deploy-no-render)
3. [Configuração](#configuração)
4. [Integração com Sistema Interno](#integração)
5. [Segurança](#segurança)
6. [API Reference](#api-reference)

---

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         ARQUITETURA COTAÇÃO EXTERNA                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────┐         ┌─────────────────────────────────────────┐   │
│  │  SISTEMA INTERNO    │         │         RENDER (EXTERNO)               │   │
│  │  (Rede Corporativa) │         │    https://cotacao-externa.onrender.com │   │
│  ├─────────────────────┤         ├─────────────────────────────────────────┤   │
│  │                     │         │                                         │   │
│  │  ┌───────────────┐  │  HTTPS  │  ┌───────────────────────────────────┐ │   │
│  │  │ Gerar Cotação │──┼────────►│  │  POST /api/cotacao/registrar     │ │   │
│  │  └───────────────┘  │   JSON  │  │  - Recebe dados da cotação       │ │   │
│  │         │           │         │  │  - Gera token único              │ │   │
│  │         ▼           │         │  │  - Retorna link para fornecedor  │ │   │
│  │  ┌───────────────┐  │         │  └───────────────────────────────────┘ │   │
│  │  │ Gera Link     │  │         │                    │                   │   │
│  │  │ Externo       │  │         │                    ▼                   │   │
│  │  └───────────────┘  │         │  ┌───────────────────────────────────┐ │   │
│  │         │           │         │  │  /cotar?token=ABC123              │ │   │
│  │         ▼           │         │  │  - Página HTML para fornecedor   │ │   │
│  │  ┌───────────────┐  │         │  │  - Preenchimento de preços       │ │   │
│  │  │ Envia Link    │  │         │  │  - Validação em tempo real       │ │   │
│  │  │ (Email/Whats) │  │         │  └───────────────────────────────────┘ │   │
│  │  └───────────────┘  │         │                    │                   │   │
│  │                     │         │                    ▼                   │   │
│  │                     │         │  ┌───────────────────────────────────┐ │   │
│  │                     │         │  │  POST /api/responder              │ │   │
│  │                     │         │  │  - Fornecedor envia respostas    │ │   │
│  │                     │         │  │  - Armazena com hash/assinatura  │ │   │
│  │                     │         │  └───────────────────────────────────┘ │   │
│  │                     │         │                    │                   │   │
│  │  ┌───────────────┐  │  HTTPS  │                    │                   │   │
│  │  │ Importar      │◄─┼────────┼────────────────────┘                   │   │
│  │  │ Respostas     │  │   JSON  │                                        │   │
│  │  └───────────────┘  │         │  ┌───────────────────────────────────┐ │   │
│  │         │           │         │  │  GET /api/cotacao/{token}/resposta│ │   │
│  │         ▼           │         │  │  - Sistema interno busca resposta│ │   │
│  │  ┌───────────────┐  │         │  │  - Valida assinatura HMAC        │ │   │
│  │  │ Atualiza      │  │         │  └───────────────────────────────────┘ │   │
│  │  │ Comparativo   │  │         │                                        │   │
│  │  └───────────────┘  │         │                                        │   │
│  │                     │         │                                        │   │
│  └─────────────────────┘         └─────────────────────────────────────────┘   │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        FORNECEDOR (Internet)                            │   │
│  ├─────────────────────────────────────────────────────────────────────────┤   │
│  │  📱 Celular ou 💻 Computador                                            │   │
│  │                                                                         │   │
│  │  1. Recebe link via email/WhatsApp                                      │   │
│  │  2. Abre no navegador (Chrome, Safari, etc)                            │   │
│  │  3. Visualiza itens da cotação                                         │   │
│  │  4. Preenche preços, prazos e observações                              │   │
│  │  5. Clica em "Enviar Cotação"                                          │   │
│  │  6. Recebe confirmação com protocolo                                   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Deploy no Render

### Passo 1: Criar Repositório Git

```bash
cd cotacao_externa_render
git init
git add .
git commit -m "Initial commit - Cotação Externa"
```

### Passo 2: Push para GitHub

1. Crie um repositório no GitHub (privado recomendado)
2. Conecte o repositório local:

```bash
git remote add origin https://github.com/seu-usuario/cotacao-externa.git
git branch -M main
git push -u origin main
```

### Passo 3: Criar Web Service no Render

1. Acesse https://render.com e faça login
2. Clique em **"New +"** → **"Web Service"**
3. Conecte seu repositório GitHub
4. Configure:
   - **Name**: `cotacao-externa`
   - **Region**: `Oregon (US West)` (mais próximo)
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Plan**: `Free`

### Passo 4: Configurar Variáveis de Ambiente

No Dashboard do Render, vá em **Environment** e adicione:

| Variable | Value |
|----------|-------|
| `SECRET_KEY` | `gerar-chave-aleatoria-64-chars` |
| `API_SECRET_KEY` | `mesma-chave-do-sistema-interno` |
| `BASE_URL` | `https://cotacao-externa.onrender.com` |
| `TOKEN_EXPIRATION_HOURS` | `72` |

### Passo 5: Deploy

Clique em **"Create Web Service"**. O deploy será automático.

URL final: `https://cotacao-externa.onrender.com`

---

## ⚙️ Configuração

### Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `SECRET_KEY` | Chave secreta Flask | Auto-gerada |
| `API_SECRET_KEY` | Chave compartilhada com sistema interno | - |
| `BASE_URL` | URL base da aplicação | Auto-detectada |
| `TOKEN_EXPIRATION_HOURS` | Horas até expirar token | 72 |
| `ALLOWED_ORIGINS` | Origins CORS (separadas por vírgula) | * |
| `FLASK_DEBUG` | Modo debug | false |
| `PORT` | Porta do servidor | 5000 |

---

## 🔌 Integração com Sistema Interno

### Estrutura do JSON de Envio (Cotação)

```json
{
  "cotacao_id": 123,
  "codigo": "COT-2026-0001",
  "fornecedor": {
    "id": 456,
    "codigo": "FORN001",
    "nome": "Fornecedor ABC Ltda",
    "email": "contato@fornecedorabc.com"
  },
  "itens": [
    {
      "id": 1,
      "cod_produto": "PROD001",
      "descricao": "Parafuso Sextavado M10x50",
      "quantidade": 1000,
      "unidade": "UN",
      "observacao": "Aço galvanizado"
    },
    {
      "id": 2,
      "cod_produto": "PROD002",
      "descricao": "Arruela Lisa 10mm",
      "quantidade": 2000,
      "unidade": "UN",
      "observacao": null
    }
  ],
  "data_validade": "2026-02-15",
  "informacao_fornecedor": "Favor informar marca e prazo de validade do produto.",
  "expiration_hours": 72,
  "assinatura": "HMAC_SHA256_DO_JSON"
}
```

### Estrutura do JSON de Resposta (Orçamento)

```json
{
  "token": "ABC123...",
  "cotacao_id": 123,
  "fornecedor_id": 456,
  "fornecedor_nome": "Fornecedor ABC Ltda",
  "submitted_at": "2026-01-29T15:30:00",
  "respostas": [
    {
      "item_id": 1,
      "preco_unitario": 2.50,
      "prazo_entrega": 15,
      "observacao": "Marca Ciser"
    },
    {
      "item_id": 2,
      "preco_unitario": 0.35,
      "prazo_entrega": 15,
      "observacao": ""
    }
  ],
  "info_geral": {
    "frete_total": 150.00,
    "condicao_pagamento": "30/60 dias",
    "validade_proposta": "15 dias",
    "observacao_geral": "Entrega via transportadora própria"
  },
  "hash": "SHA256_DOS_DADOS",
  "assinatura": "HMAC_SHA256_DO_JSON"
}
```

---

## 🔐 Segurança

### Tokens

- **Geração**: `secrets.token_urlsafe(32)` - 256 bits de entropia
- **Expiração**: Configurável (padrão 72h)
- **Unicidade**: Um token por fornecedor/cotação
- **Invalidação**: Possível via API

### Assinatura HMAC

Todas as comunicações entre sistemas usam HMAC-SHA256:

```python
import hmac
import hashlib
import json

def gerar_assinatura(dados, chave_secreta):
    dados_str = json.dumps(dados, sort_keys=True)
    return hmac.new(
        chave_secreta.encode(),
        dados_str.encode(),
        hashlib.sha256
    ).hexdigest()
```

### Hash de Integridade

Cada resposta inclui hash SHA256 dos dados para detectar alterações.

### Proteção de Rotas

- Rotas públicas: `/cotar`, `/api/responder`
- Rotas protegidas (API Key): `/api/cotacao/*`

---

## 📡 API Reference

### Rotas Públicas (Fornecedor)

#### `GET /cotar?token={token}`
Página de cotação para o fornecedor.

#### `POST /api/responder`
Envia resposta da cotação.

```json
// Request
{
  "token": "ABC123...",
  "respostas": [...],
  "frete_total": 150.00,
  "condicao_pagamento": "30 dias"
}

// Response
{
  "success": true,
  "message": "Cotação enviada com sucesso!",
  "protocolo": "RESP-ABC12345"
}
```

---

### Rotas Protegidas (Sistema Interno)

**Header obrigatório**: `X-API-Key: {API_SECRET_KEY}`

#### `POST /api/cotacao/registrar`
Registra nova cotação e gera link.

```json
// Response
{
  "success": true,
  "token": "ABC123...",
  "link": "https://cotacao-externa.onrender.com/cotar?token=ABC123...",
  "expires_at": "2026-02-01T15:30:00"
}
```

#### `GET /api/cotacao/{token}/status`
Verifica status da cotação.

```json
{
  "success": true,
  "status": "respondida",
  "respondida": true,
  "expires_at": "2026-02-01T15:30:00"
}
```

#### `GET /api/cotacao/{token}/resposta`
Obtém resposta do fornecedor.

#### `POST /api/cotacao/{token}/invalidar`
Invalida/cancela uma cotação.

#### `GET /api/respostas/pendentes`
Lista todas as respostas pendentes de importação.

---

## 🧪 Testando Localmente

```bash
cd cotacao_externa_render
pip install -r requirements.txt
python app.py
```

Acesse: http://localhost:5000

### Teste de Registro de Cotação

```bash
curl -X POST http://localhost:5000/api/cotacao/registrar \
  -H "Content-Type: application/json" \
  -H "X-API-Key: chave-secreta-compartilhada-trocar-em-producao" \
  -d '{
    "cotacao_id": 1,
    "codigo": "COT-TESTE-001",
    "fornecedor": {
      "id": 1,
      "nome": "Fornecedor Teste"
    },
    "itens": [
      {
        "id": 1,
        "cod_produto": "PROD001",
        "descricao": "Produto de Teste",
        "quantidade": 100,
        "unidade": "UN"
      }
    ]
  }'
```

---

## 🔄 Migração para Infraestrutura Corporativa

A aplicação está preparada para migração sem refatoração:

1. **Docker**: Adicionar `Dockerfile`
2. **Banco de dados**: Substituir dicionários em memória por Redis/PostgreSQL
3. **Domínio**: Atualizar `BASE_URL`
4. **SSL**: Configurar certificado no servidor destino

---

## 📞 Suporte

Dúvidas ou problemas? Abra uma issue no repositório ou contate a equipe de TI.
