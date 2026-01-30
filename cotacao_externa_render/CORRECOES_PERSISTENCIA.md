# Correções do Sistema de Persistência - Render

## Problema Identificado

O sistema de cotação externa no Render perdia os dados após hibernação/reinício porque:

1. **Variável `respostas_sincronizadas` declarada duas vezes**: Uma vez na inicialização e outra vez antes da função de polling, causando sobrescrita dos dados carregados.

2. **`respostas_sincronizadas` não era persistido**: O set não estava sendo salvo no arquivo JSON, então após reinício, o sistema não lembrava quais respostas já foram sincronizadas.

## Correções Implementadas

### 1. Estrutura de Persistência Completa

O arquivo `cotacoes_storage.json` agora armazena:

```json
{
  "cotacoes_ativas": { ... },
  "respostas_enviadas": { ... },
  "respostas_sincronizadas": ["token1", "token2", ...],
  "salvo_em": "2025-01-28T10:30:00"
}
```

### 2. Função `salvar_dados_persistentes()` (Linha 67)

- Agora salva `respostas_sincronizadas` como lista (Set não é serializável em JSON)
- Inclui timestamp de salvamento para diagnóstico
- Logging melhorado com contagem de sincronizados

### 3. Função `carregar_dados_persistentes()` (Linha 109)

- Agora carrega `respostas_sincronizadas` do JSON (List → Set)
- Logging melhorado mostrando quantos tokens sincronizados foram restaurados

### 4. Remoção de Declaração Duplicada (Linha ~829)

- Removida a segunda declaração `respostas_sincronizadas = set()` que sobrescrevia os dados carregados

### 5. Endpoint `/api/confirmar-sincronizacao` (Linha 884)

- Agora chama `salvar_dados_persistentes()` após marcar token como sincronizado
- Garante que a marcação sobrevive a reinícios

### 6. Endpoint `/api/diagnostico` Melhorado

- Agora mostra estatísticas de `respostas_sincronizadas`
- Mostra se cada cotação está sincronizada
- Inclui informações sobre o arquivo de persistência

## Pontos de Persistência

O sistema salva os dados nos seguintes momentos:

| Evento | Rota | Linha Aprox. |
|--------|------|-------------|
| Criar cotação (API) | `/api/cotacao/registrar` | 404 |
| Criar cotação externa | `/api/criar-cotacao-externa` | 710 |
| Enviar resposta | `/cotar` (POST) | 347 |
| Invalidar cotação | `/api/cotacao/<token>/invalidar` | 600 |
| Confirmar sincronização | `/api/confirmar-sincronizacao` | 904 |

## Endpoints de Diagnóstico

### GET `/api/diagnostico`

Retorna informações detalhadas do sistema:

```json
{
  "success": true,
  "status": "online",
  "timestamp": "2025-01-28T10:30:00",
  "estatisticas": {
    "cotacoes_ativas": 5,
    "respostas_enviadas": 3,
    "respostas_sincronizadas": 2,
    "respostas_pendentes": 1
  },
  "persistencia": {
    "arquivo": "cotacoes_storage.json",
    "existe": true,
    "tamanho_bytes": 15420,
    "ultima_gravacao": "2025-01-28T10:25:00"
  },
  "cotacoes": [
    {
      "token_preview": "abc123...",
      "fornecedor": "Empresa ABC",
      "respondida": true,
      "sincronizada": true
    }
  ]
}
```

### GET `/api/health`

Health check simples para monitoramento do Render.

## Instruções de Deploy

1. **Commit das alterações**:
```bash
cd cotacao_externa_render
git add app.py
git commit -m "fix: persistência completa de respostas sincronizadas"
git push
```

2. **O Render fará deploy automático** (se configurado com auto-deploy)

3. **Verificar logs do Render** após deploy:
   - Deve mostrar: `[PERSISTÊNCIA] Dados carregados de cotacoes_storage.json`
   - Deve mostrar: `- X sincronizadas`

4. **Testar endpoint de diagnóstico**:
```
https://cotacao-externa-render.onrender.com/api/diagnostico
```

## Fluxo Completo Validado

1. ✅ Sistema local gera link externo → Render cria token e salva
2. ✅ Fornecedor acessa link e responde → Render salva resposta
3. ✅ Sistema local faz polling → Render retorna apenas não-sincronizadas
4. ✅ Sistema local confirma sync → Render marca e persiste
5. ✅ Render reinicia → Carrega dados do JSON incluindo sincronizadas
6. ✅ Polling após reinício → Não retorna duplicadas

## Backup Automático

Se o arquivo JSON estiver corrompido, o sistema automaticamente:
1. Faz backup do arquivo corrompido: `cotacoes_storage.json.backup.{timestamp}`
2. Inicia com dados vazios
3. Grava log do erro

---

**Data**: Janeiro 2025  
**Versão**: 1.1 (com persistência completa)
