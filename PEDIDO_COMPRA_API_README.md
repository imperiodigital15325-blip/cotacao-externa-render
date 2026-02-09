# Módulo de Pedidos de Compra - Documentação da API

## Visão Geral

Este módulo permite gerar pedidos de compra a partir de solicitações selecionadas na aba "Solicitações em Aberto". O sistema suporta:

- Geração de pedidos com número sequencial automático
- Vinculação de múltiplas solicitações a um único pedido
- Preenchimento automático de fornecedor e preços
- Histórico de pedidos com auditoria
- Preparação para integração futura com TOTVS

---

## Endpoints da API

### 1. Obter Último Número de Pedido

**GET** `/api/ultimo-numero-pedido`

Retorna o último número de pedido usado e sugere o próximo.

**Resposta:**
```json
{
  "success": true,
  "ultimo_pedido": "PC2026001",
  "data_ultimo": "2026-02-01",
  "fornecedor_ultimo": "Fornecedor X",
  "proximo_sugerido": "PC2026002"
}
```

---

### 2. Gerar Pedido de Compra

**POST** `/api/gerar-pedido`

Cria um novo pedido de compra a partir de solicitações selecionadas.

**Payload:**
```json
{
  "numero_pedido": "PC2026001",
  "data_pedido": "2026-02-05",
  "fornecedor": {
    "codigo": "F001",
    "nome": "Fornecedor X"
  },
  "condicao_pagamento": "30 DDL",
  "contato": "Nome do contato",
  "observacoes": "Observações gerais",
  "itens": [
    {
      "numero_sc": "000123",
      "item_sc": "01",
      "cod_produto": "PROD001",
      "descricao_produto": "Descrição do produto",
      "quantidade": 10,
      "unidade": "UN",
      "valor_unitario": 15.5,
      "ipi": 0.0,
      "data_necessidade": "2026-02-10"
    }
  ]
}
```

**Campos opcionais:**
- `numero_pedido`: Se não informado, será gerado automaticamente (formato: PC + ANO + Sequencial)
- `condicao_pagamento`, `contato`, `observacoes`: Opcionais

**Resposta de Sucesso:**
```json
{
  "success": true,
  "pedido_id": 1,
  "numero_pedido": "PC2026001",
  "valor_total": 155.0,
  "total_itens": 1,
  "message": "Pedido PC2026001 gerado com sucesso!",
  "payload_totvs": { ... }
}
```

**Resposta de Erro (solicitação já tem pedido):**
```json
{
  "success": false,
  "error": "Algumas solicitações já possuem pedido",
  "itens_com_pedido": [
    {
      "sc": "000123",
      "item": "01",
      "pedido": "PC2026001"
    }
  ]
}
```

---

### 3. Listar Pedidos

**GET** `/api/pedidos`

Lista todos os pedidos de compra com filtros opcionais.

**Query Parameters:**
- `data_inicio`: Data inicial (YYYY-MM-DD)
- `data_fim`: Data final (YYYY-MM-DD)
- `fornecedor`: Filtro por código ou nome do fornecedor
- `status`: Filtro por status (Gerado, Enviado, etc.)

**Resposta:**
```json
{
  "success": true,
  "total": 10,
  "pedidos": [
    {
      "id": 1,
      "numero_pedido": "PC2026001",
      "data_pedido": "2026-02-05",
      "nome_fornecedor": "Fornecedor X",
      "valor_total": 1500.00,
      "status": "Gerado",
      "total_itens": 5
    }
  ]
}
```

---

### 4. Obter Detalhes do Pedido

**GET** `/api/pedido/<pedido_id>`

Retorna detalhes completos de um pedido específico.

**Resposta:**
```json
{
  "success": true,
  "pedido": {
    "id": 1,
    "numero_pedido": "PC2026001",
    "data_pedido": "2026-02-05",
    "cod_fornecedor": "F001",
    "nome_fornecedor": "Fornecedor X",
    "condicao_pagamento": "30 DDL",
    "contato": "João Silva",
    "observacoes": "Pedido urgente",
    "valor_total": 1500.00,
    "status": "Gerado",
    "itens": [
      {
        "numero_sc": "000123",
        "item_sc": "01",
        "cod_produto": "PROD001",
        "descricao_produto": "Produto X",
        "quantidade": 10,
        "valor_unitario": 150.0,
        "ipi": 0,
        "valor_total": 1500.0
      }
    ],
    "historico": [
      {
        "acao": "CRIACAO",
        "descricao": "Pedido criado com 1 item(ns)",
        "usuario": "Admin",
        "data_hora": "2026-02-05 10:30:00"
      }
    ]
  }
}
```

---

### 5. Atualizar Pedido

**PUT** `/api/pedido/<pedido_id>`

Atualiza dados de um pedido existente.

**Payload:**
```json
{
  "condicao_pagamento": "60 DDL",
  "observacoes": "Atualização de observações",
  "status": "Enviado"
}
```

---

### 6. Obter Payload TOTVS

**GET** `/api/pedido/<pedido_id>/payload-totvs`

Retorna o payload formatado para integração com o TOTVS.

**Resposta:**
```json
{
  "success": true,
  "payload": {
    "numero_pedido": "PC2026001",
    "data_pedido": "2026-02-05",
    "fornecedor": {
      "codigo": "F001",
      "nome": "Fornecedor X"
    },
    "condicao_pagamento": "30 DDL",
    "contato": "João Silva",
    "observacoes": "Observações gerais",
    "itens": [
      {
        "solicitacao_id": "000123",
        "item_sc": "01",
        "produto_id": "PROD001",
        "descricao": "Produto X",
        "quantidade": 10,
        "unidade": "UN",
        "valor_unitario": 150.0,
        "ipi": 0,
        "data_necessidade": "2026-02-10"
      }
    ]
  }
}
```

---

### 7. Enviar para TOTVS (Preparado para Integração)

**POST** `/api/pedido/<pedido_id>/enviar-totvs`

Registra o envio do pedido para o TOTVS. Atualmente simula o envio para testes.

**Resposta:**
```json
{
  "success": true,
  "message": "Pedido registrado para envio ao TOTVS",
  "resposta": {
    "status": "simulado",
    "message": "Integração com TOTVS ainda não implementada",
    "timestamp": "2026-02-05T10:30:00"
  }
}
```

---

### 8. Verificar se Solicitação Tem Pedido

**POST** `/api/solicitacao/verificar-pedido`

Verifica se uma ou mais solicitações já foram vinculadas a pedidos.

**Payload:**
```json
{
  "solicitacoes": [
    { "numero_sc": "000123", "item_sc": "01" },
    { "numero_sc": "000124", "item_sc": "01" }
  ]
}
```

**Resposta:**
```json
{
  "success": true,
  "resultados": [
    {
      "numero_sc": "000123",
      "item_sc": "01",
      "tem_pedido": true,
      "numero_pedido": "PC2026001",
      "status": "Gerado"
    },
    {
      "numero_sc": "000124",
      "item_sc": "01",
      "tem_pedido": false
    }
  ]
}
```

---

## Estrutura do Banco de Dados

### Tabela: pedidos_compra

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER | Chave primária |
| numero_pedido | TEXT | Número único do pedido (PC2026001) |
| data_pedido | DATE | Data do pedido |
| cod_fornecedor | TEXT | Código do fornecedor |
| nome_fornecedor | TEXT | Nome do fornecedor |
| condicao_pagamento | TEXT | Condição de pagamento |
| contato | TEXT | Contato no fornecedor |
| observacoes | TEXT | Observações gerais |
| valor_total | REAL | Valor total calculado |
| status | TEXT | Status do pedido (Gerado, Enviado) |
| enviado_totvs | INTEGER | Flag se foi enviado ao TOTVS |
| data_envio_totvs | TIMESTAMP | Data/hora do envio |
| resposta_totvs | TEXT | Resposta JSON do TOTVS |
| criado_por | TEXT | Usuário que criou |
| criado_em | TIMESTAMP | Data/hora de criação |

### Tabela: pedido_itens

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER | Chave primária |
| pedido_id | INTEGER | FK para pedidos_compra |
| numero_sc | TEXT | Número da solicitação |
| item_sc | TEXT | Item da solicitação |
| cod_produto | TEXT | Código do produto |
| descricao_produto | TEXT | Descrição |
| quantidade | REAL | Quantidade |
| unidade | TEXT | Unidade de medida |
| valor_unitario | REAL | Preço unitário |
| ipi | REAL | Percentual de IPI |
| valor_total | REAL | Valor total do item |
| data_necessidade | DATE | Data de necessidade |

### Tabela: pedido_historico

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INTEGER | Chave primária |
| pedido_id | INTEGER | FK para pedidos_compra |
| acao | TEXT | Ação realizada |
| descricao | TEXT | Descrição da ação |
| usuario | TEXT | Usuário que realizou |
| data_hora | TIMESTAMP | Data/hora da ação |
| dados_json | TEXT | Dados adicionais em JSON |

---

## Exemplos de Uso

### Exemplo 1: Gerar pedido simples

```javascript
fetch('/api/gerar-pedido', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        data_pedido: '2026-02-05',
        fornecedor: { codigo: 'F001', nome: 'Fornecedor X' },
        condicao_pagamento: '30 DDL',
        itens: [
            {
                numero_sc: '000123',
                item_sc: '01',
                cod_produto: 'PROD001',
                descricao_produto: 'Produto X',
                quantidade: 10,
                valor_unitario: 15.50
            }
        ]
    })
})
.then(r => r.json())
.then(data => console.log(data));
```

### Exemplo 2: Verificar antes de gerar

```javascript
// Primeiro verifica se as solicitações já têm pedido
fetch('/api/solicitacao/verificar-pedido', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        solicitacoes: [
            { numero_sc: '000123', item_sc: '01' }
        ]
    })
})
.then(r => r.json())
.then(data => {
    const comPedido = data.resultados.filter(r => r.tem_pedido);
    if (comPedido.length > 0) {
        alert('Algumas solicitações já têm pedido!');
    } else {
        // Pode prosseguir com a geração
    }
});
```

---

## Integração Futura com TOTVS

O sistema está preparado para integração com a API do TOTVS. Quando disponível:

1. Implemente a chamada real em `api_enviar_pedido_totvs()`
2. Configure a URL do endpoint TOTVS
3. Adicione autenticação necessária
4. Mapeie os campos conforme especificação do TOTVS

O payload já está estruturado no formato esperado.
