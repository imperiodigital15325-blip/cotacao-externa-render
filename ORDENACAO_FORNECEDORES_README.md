# Ordenação Dinâmica de Fornecedores por Competitividade

## 📋 Resumo

Implementação de ordenação automática e dinâmica dos fornecedores na aba **Cotações e Orçamentos**, especificamente na seção de **Comparativo de Propostas**.

Os fornecedores são agora reordenados automaticamente com base na sua competitividade, mantendo os mais competitivos sempre no início da tabela.

---

## 🎯 Funcionalidades Implementadas

### 1️⃣ Ordenação Automática no Backend

**Arquivo:** `app.py`

**Função criada:** `ordenar_fornecedores_por_competitividade(cotacao)`

**Localização:** Antes da função `cotacao_detalhe()` (linha ~6308)

**Critérios de ordenação:**
1. **Primário:** Quantidade de melhores preços (maior → menor)
2. **Desempate 1:** Menor soma total dos valores cotados
3. **Desempate 2:** Menor valor médio por item

**Como funciona:**
- Analisa todas as respostas da cotação
- Para cada item, identifica o menor preço válido
- Conta quantos "melhores preços" cada fornecedor possui
- Calcula soma total e valor médio para desempate
- Retorna lista de fornecedores ordenados

**Integração:**
- A função é chamada em `cotacao_detalhe()` antes de renderizar o template
- A lista ordenada é passada para o template como `fornecedores_ordenados`

---

### 2️⃣ Ordenação Dinâmica no Frontend

**Arquivo:** `templates/cotacao_detalhe_new.html`

**Função criada:** `reordenarFornecedoresPorCompetitividade()`

**Localização:** Após a função `calcularVariacoesUltimoPreco()` (linha ~3230)

**Como funciona:**
1. **Análise:** Percorre a tabela e coleta dados de cada fornecedor
2. **Pontuação:** Calcula quantidade de melhores preços, soma total e valor médio
3. **Ordenação:** Aplica os mesmos critérios do backend
4. **Reordenação DOM:** Move as colunas da tabela dinamicamente

**Chamada automática:**
- Na inicialização da página (após `destacarMelhoresMatriz()`)
- Após salvar edições (via `location.reload()` que reaplica a ordenação)

---

### 3️⃣ Integração com Template

**Arquivo:** `templates/cotacao_detalhe_new.html`

**Modificação:** Seção de construção da lista de fornecedores (linha ~707)

```jinja2
{% set fornecedores_unicos = [] %}
{% set forn_data_map = {} %}
{% for resp in cotacao.respostas %}
    {% if resp.nome_fornecedor not in fornecedores_unicos %}
        {% set _ = fornecedores_unicos.append(resp.nome_fornecedor) %}
        {% set _ = forn_data_map.update({resp.nome_fornecedor: {...}}) %}
    {% endif %}
{% endfor %}

{# Usar lista ordenada do backend se disponível #}
{% if fornecedores_ordenados %}
    {% set fornecedores_unicos = fornecedores_ordenados %}
{% endif %}
```

**Resultado:** O template usa a lista ordenada vinda do backend sempre que disponível.

---

## ⚙️ Comportamento do Sistema

### Quando a ordenação é aplicada:

✅ **Ao carregar a página de detalhes da cotação**
- Backend ordena os fornecedores antes de renderizar
- Frontend aplica ordenação adicional após carregamento

✅ **Após salvar edições de respostas**
- Página é recarregada (`location.reload()`)
- Ordenação é reaplicada automaticamente

✅ **Após adicionar/remover fornecedores**
- Página é recarregada
- Nova ordenação é calculada

---

## 🔍 Regras de Ordenação Detalhadas

### Contagem de Melhores Preços
Para cada item da cotação:
- Identifica o menor preço válido (> 0)
- Fornecedor com esse preço ganha **+1 ponto**
- Tolerância de ±0.01 para arredondamentos

### Critérios de Desempate
Quando dois fornecedores têm a mesma quantidade de melhores preços:
1. **Menor soma total:** Soma de todos os preços cotados + frete
2. **Menor valor médio:** Soma total ÷ quantidade de itens cotados

### Exemplo Prático

**Cotação com 5 itens:**

| Fornecedor | Melhores Preços | Soma Total | Valor Médio | Posição |
|------------|----------------|------------|-------------|---------|
| A          | 3              | R$ 5.000   | R$ 1.000    | 🥇 1º   |
| B          | 2              | R$ 4.800   | R$ 960      | 🥈 2º   |
| C          | 2              | R$ 5.200   | R$ 1.040    | 🥉 3º   |
| D          | 1              | R$ 5.500   | R$ 1.100    | 4º      |

- **Fornecedor A:** Melhor colocado (3 melhores preços)
- **Fornecedores B e C:** Empate em melhores preços, mas B tem menor soma total
- **Fornecedor D:** Pior colocado (apenas 1 melhor preço)

---

## ⚠️ Considerações Importantes

### Valores Válidos
- Considera apenas: `preço > 0`
- Ignora: valores zerados, nulos ou vazios

### Performance
- Função backend: O(n × m) onde n = itens, m = fornecedores
- Função frontend: Mesma complexidade
- Impacto mínimo mesmo com muitos fornecedores/itens

### Compatibilidade
- Mantém compatibilidade total com código existente
- Não quebra funcionalidades anteriores
- Funciona com ou sem a ordenação (fallback automático)

---

## 🎨 Experiência do Usuário

### Antes
- Fornecedores em ordem aleatória ou de cadastro
- Melhor fornecedor poderia estar no final da tabela
- Análise visual demorada e confusa

### Depois
- ✅ Fornecedores ordenados por competitividade
- ✅ Melhor fornecedor sempre no início
- ✅ Leitura rápida e intuitiva
- ✅ Identificação imediata do mais competitivo
- ✅ Melhor apoio à tomada de decisão

---

## 🐛 Resolução de Problemas

### Fornecedores não reordenam
- Verifique se `fornecedores_ordenados` está sendo passado do backend
- Abra o console do navegador e procure por `[ORDENACAO]`
- Verifique se há respostas válidas (preços > 0)

### Ordem incorreta
- Verifique se há empates nos critérios
- Confira logs do console: `[ORDENACAO] FornecedorX: N melhores preços`
- Valide se os valores de frete estão sendo considerados

### Performance lenta
- Normal em cotações com 20+ fornecedores e 50+ itens
- Considere otimizar a consulta SQL se necessário

---

## 📝 Logs e Debug

### Backend
```python
print(f"[ORDENACAO] {fornecedor}: {melhores_precos} melhores preços, total={total}")
```

### Frontend
```javascript
console.log('[ORDENACAO] Iniciando reordenação de fornecedores...');
console.log('[ORDENACAO] FornecedorX: 3 melhores preços, total=5000.00, média=1000.00');
console.log('[ORDENACAO] Reordenação concluída!');
```

Para ver os logs: Abra **DevTools (F12) → Console**

---

## ✅ Checklist de Implementação

- [x] Função de ordenação no backend (`app.py`)
- [x] Integração com rota `cotacao_detalhe()`
- [x] Passagem da lista ordenada para o template
- [x] Modificação do template para usar lista ordenada
- [x] Função JavaScript de reordenação dinâmica
- [x] Chamada automática na inicialização
- [x] Logs de debug implementados
- [x] Documentação completa

---

## 🚀 Próximos Passos Sugeridos

1. **Indicador visual:** Adicionar badge/indicador mostrando posição do fornecedor
2. **Ordenação manual:** Permitir que usuário altere ordem temporariamente
3. **Salvar preferências:** Lembrar ordem customizada por usuário
4. **Filtros adicionais:** Ordenar por prazo, condição de pagamento, etc.
5. **Exportação:** Incluir ordem de competitividade em relatórios PDF/Excel

---

## 📅 Histórico de Alterações

### v1.0 - 06/02/2026
- ✅ Implementação inicial da ordenação por competitividade
- ✅ Backend e frontend integrados
- ✅ Documentação completa

---

**Desenvolvido por:** Daniel Amaral - Projeto Compras  
**Data:** Fevereiro de 2026  
**Status:** ✅ Funcional e Operacional
