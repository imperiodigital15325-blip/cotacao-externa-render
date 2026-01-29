# Atribuição Manual de Comprador - Documentação

## 📋 Visão Geral

Esta funcionalidade permite a atribuição manual de compradores para solicitações de compra (SCs) que estão no filtro "Outros", resolvendo o problema de itens sem fornecedor cadastrado ou com cadastro incorreto no TOTVS.

## ✨ Funcionalidades Implementadas

### 1. **Banco de Dados**
- Nova tabela `solicitacao_atribuicoes` no SQLite local
- Armazena atribuições manuais com rastreabilidade completa
- Campos: numero_sc, item_sc, cod_comprador, nome_comprador, atribuido_por, data_atribuicao, observacao

### 2. **Backend (app.py)**
Novas rotas API:
- `POST /api/solicitacao/atribuir_comprador` - Atribui comprador a uma ou mais SCs
- `POST /api/solicitacao/remover_atribuicao` - Remove atribuição manual
- `GET /api/solicitacoes/atribuicoes` - Lista todas as atribuições

**Lógica de Priorização:**
- Atribuição manual tem **prioridade máxima**
- Se existe atribuição manual, sobrescreve o comprador do TOTVS
- Mantém compatibilidade com lógica automática existente

### 3. **Frontend (solicitacoes.html)**

#### Interface:
- ✅ Checkbox em cada linha da tabela para seleção múltipla
- ✅ Botão "Atribuir Comprador" na barra de ações flutuante
- ✅ Modal com lista de compradores e campo de observação
- ✅ Ícone 📌 nas linhas com atribuição manual (tooltip informativo)

#### JavaScript:
- Função `abrirModalAtribuicao()` - Abre modal de atribuição
- Função `confirmarAtribuicao()` - Envia requisição e recarrega página
- Integração com sistema de seleção múltipla existente

## 🚀 Como Usar

### Passo a Passo:

1. **Acesse a aba "Solicitações em Aberto"**
   - Filtre por comprador "Outros" para ver itens sem atribuição

2. **Selecione as solicitações desejadas**
   - Marque os checkboxes das SCs que deseja atribuir
   - Pode selecionar uma ou múltiplas solicitações

3. **Clique em "Atribuir Comprador"**
   - Na barra de ações flutuante (aparece quando há itens selecionados)

4. **Escolha o comprador**
   - Selecione o comprador responsável no dropdown
   - Opções: Daniel Amaral, Aline Chen, Hélio Doce, Diego Moya

5. **Adicione observação (opcional)**
   - Ex: "Item sem fornecedor cadastrado", "Cliente específico"

6. **Confirme a atribuição**
   - A página será recarregada
   - As solicitações agora aparecem no filtro do comprador selecionado

## 📊 Regras de Negócio

### Priorização de Comprador:
```
1º. Atribuição Manual (banco local)
2º. Fornecedor da SC (SC1010.C1_FORNECE → SA2010.A2_X_COMPR)
3º. Fornecedor Padrão do Produto (SB1010.B1_PROC → SA2010.A2_X_COMPR)
4º. "Outros" (quando nenhum dos anteriores existe)
```

### Rastreabilidade:
- Cada atribuição registra usuário e data/hora
- Campo observação permite documentar o motivo
- Ícone visual indica atribuição manual na tabela

### Conflitos:
- Se o item for corretamente cadastrado no TOTVS posteriormente:
  - A atribuição manual continua tendo prioridade
  - É possível remover a atribuição manual se desejar usar o cadastro do TOTVS

## 🔧 Estrutura Técnica

### Arquivos Modificados:

1. **database.py**
   - Nova tabela `solicitacao_atribuicoes`
   - Funções: `salvar_atribuicao_comprador()`, `obter_atribuicoes_compradores()`, etc.

2. **app.py**
   - 3 novas rotas API
   - Modificação na função `solicitacoes()` para aplicar atribuições manuais

3. **templates/solicitacoes.html**
   - Checkbox na primeira coluna da tabela
   - Novo botão na barra de ações
   - Modal de atribuição
   - JavaScript para gerenciar atribuições

### Banco de Dados:

```sql
CREATE TABLE solicitacao_atribuicoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_sc TEXT NOT NULL,
    item_sc TEXT NOT NULL,
    cod_comprador TEXT NOT NULL,
    nome_comprador TEXT NOT NULL,
    atribuido_por TEXT,
    data_atribuicao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    observacao TEXT,
    UNIQUE(numero_sc, item_sc)
);
```

## 💡 Benefícios

1. **Reduz ruído no filtro "Outros"**
   - Solicitações importantes não ficam perdidas

2. **Mantém integridade do TOTVS**
   - Não altera cadastros do sistema oficial
   - Atribuição é local ao sistema de compras

3. **Rastreabilidade completa**
   - Quem atribuiu, quando e por quê

4. **Flexibilidade**
   - Fácil remover atribuição se necessário
   - Não interfere com lógica automática

5. **Melhor gestão**
   - Compradores veem apenas suas solicitações relevantes
   - Menos retrabalho operacional

## 🎯 Casos de Uso

### Cenário 1: Item sem Fornecedor
```
Problema: SC criada para produto novo, sem fornecedor cadastrado
Solução: Atribuir manualmente ao comprador responsável pela categoria
```

### Cenário 2: Fornecedor Incorreto
```
Problema: Produto com fornecedor cadastrado errado
Solução: Atribuir manualmente ao comprador correto enquanto aguarda correção
```

### Cenário 3: Cliente Específico
```
Problema: Item que deve ser tratado por comprador específico por questões comerciais
Solução: Atribuir manualmente ao comprador responsável pelo cliente
```

## 🔄 Fluxo Completo

```
1. SC criada no TOTVS
   ↓
2. Sistema busca comprador (fornecedor SC → fornecedor produto)
   ↓
3. Se não encontrar → vai para "Outros"
   ↓
4. Usuário visualiza "Outros" e identifica item importante
   ↓
5. Seleciona item e clica "Atribuir Comprador"
   ↓
6. Escolhe comprador e confirma
   ↓
7. Atribuição é salva no banco local
   ↓
8. Próxima carga: item aparece no filtro do comprador atribuído
   ↓
9. Comprador vê item em sua lista e pode processar
```

## 📝 Observações Importantes

1. **Não altera TOTVS**: Atribuição é apenas no sistema local
2. **Prioridade**: Manual > SC > Produto > Outros
3. **Reversível**: Pode remover atribuição a qualquer momento
4. **Multi-usuário**: Registra quem fez a atribuição
5. **Performance**: Usa índices no banco para consultas rápidas

## 🛠️ Manutenção Futura

### Para adicionar novo comprador:
1. Adicionar opção no select do modal (solicitacoes.html)
2. Usar formato: `value="XXX|Nome Completo"`
   - XXX = código do comprador no TOTVS (campo A2_X_COMPR)

### Para remover atribuição via interface:
- Implementar botão/ação na tabela
- Chamar endpoint DELETE `/api/solicitacao/remover_atribuicao`

### Para auditoria:
- Consultar tabela `solicitacao_atribuicoes`
- Campos: atribuido_por, data_atribuicao, observacao

## ✅ Status de Implementação

- ✅ Banco de dados (tabela e funções)
- ✅ Backend (rotas API)
- ✅ Frontend (interface e JavaScript)
- ✅ Lógica de priorização
- ✅ Rastreabilidade visual
- ✅ Documentação

**Implementação Completa e Funcional!**
