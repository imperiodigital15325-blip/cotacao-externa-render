# 🚀 Guia Prático: Integração TOTVS - Pedidos de Compra

## ✅ O que já está pronto e funcionando:

1. ✅ Interface completa para gerar pedidos
2. ✅ Validação de solicitações duplicadas
3. ✅ Cálculo automático de valores e IPI
4. ✅ Histórico e auditoria
5. ✅ Estrutura de payload para TOTVS
6. ✅ Módulo de integração preparado (`totvs_integration.py`)

---

## 📋 Checklist: O que VOCÊ precisa fazer

### Passo 1: Obter Informações do TOTVS

**Entre em contato com sua equipe de TI/Infraestrutura e obtenha:**

- [ ] URL da API REST do TOTVS Protheus
  - Exemplo: `http://172.16.45.117:8080/rest`
  - Pode ser `http://IP:PORTA/rest` ou `http://IP:PORTA/api`

- [ ] Usuário e senha para API
  - Usuário com permissão para criar pedidos (SC7)
  - Pode ser usuário específico de integração

- [ ] Endpoint para criar pedidos
  - Exemplo: `/WSSC7`, `/api/pedidos`, `/SC7010`
  - Depende de como o TOTVS foi configurado

- [ ] Documentação da API
  - Peça o manual de integração REST do TOTVS
  - Ou pergunte ao consultor Protheus

### Passo 2: Descobrir o Mapeamento de Campos

**Você precisa saber como os campos se mapeiam:**

```
Pergunte ao seu consultor TOTVS:

"Quais campos são obrigatórios para criar um pedido de compra (SC7) via API REST?"

Exemplo de resposta esperada:
- C7_NUM = Número do pedido
- C7_EMISSAO = Data de emissão
- C7_FORNECE = Código do fornecedor
- C7_PRODUTO = Código do produto
- C7_QUANT = Quantidade
- C7_PRECO = Preço unitário
- C7_NUMSC = Número da SC vinculada
- C7_ITEMSC = Item da SC
- C7_TES = Tipo de Entrada/Saída (importante!)
```

### Passo 3: Testar Conexão

**Execute o teste de conexão:**

```powershell
cd "w:\Compras\Daniel Amaral\PROJETO COMPRAS\PROJETO-COMPRAS-2"
python totvs_integration.py
```

**O que você verá:**
- ✅ Se conectou: "Conexão OK"
- ❌ Se não conectou: "Falha na conexão"

### Passo 4: Configurar o Módulo

**Edite o arquivo `totvs_integration.py`:**

```python
# Linha 15-17: Configure a URL
TOTVS_API_URL = "http://SEU_IP:SUA_PORTA/rest"

# Linha 20-21: Configure usuário e senha
TOTVS_API_USER = "seu_usuario"
TOTVS_API_PASSWORD = "sua_senha"

# Linha 24: Configure o endpoint
TOTVS_ENDPOINT_PEDIDO = "/WSSC7"  # Confirme com TI
```

### Passo 5: Ajustar Mapeamento de Campos

**Na função `converter_payload_para_totvs()` (linha 70):**

Ajuste conforme a documentação do seu TOTVS:

```python
# Exemplo: Se seu TOTVS usa nomes diferentes
payload_totvs = {
    "empresa": "01",  # Código da sua empresa
    "filial": "01",   # Código da sua filial
    "pedido": {
        "C7_NUM": pedido.get('numero_pedido'),
        "C7_EMISSAO": data_totvs,
        "C7_FORNECE": pedido['fornecedor']['codigo'],
        "C7_TIPO": "1",  # Verificar na tabela SX5 do Protheus
        "C7_TES": "XXX",  # ⚠️ IMPORTANTE: TES de entrada de compra
        # ... outros campos
    }
}
```

### Passo 6: Testar em Homologação

**NUNCA teste direto em produção!**

1. Configure URL de homologação primeiro
2. Crie um pedido de teste
3. Verifique se apareceu no TOTVS
4. Valide todos os campos
5. Só depois libere para produção

---

## 🔧 Comandos Úteis

### Testar integração:
```powershell
python totvs_integration.py
```

### Verificar payload que seria enviado:
```python
from totvs_integration import converter_payload_para_totvs
import json

pedido_teste = {
    'numero_pedido': 'PC2026001',
    'data_pedido': '2026-02-05',
    'fornecedor': {'codigo': 'F001', 'nome': 'Teste'},
    'itens': [...]
}

payload = converter_payload_para_totvs(pedido_teste)
print(json.dumps(payload, indent=2))
```

---

## 🎯 Exemplo Real de Uso

### 1. No Sistema (Interface)

```
1. Selecione solicitações
2. Clique em "Gerar Pedido"
3. Preencha dados
4. Clique "Gerar Pedido"
5. Pedido é salvo no banco local
6. Clique "Enviar para TOTVS"
```

### 2. O que acontece no Backend

```python
# app.py linha ~8730
@app.route('/api/pedido/<id>/enviar-totvs', methods=['POST'])
def api_enviar_pedido_totvs(pedido_id):
    # 1. Busca pedido no banco local
    payload = db.gerar_payload_totvs(pedido_id)
    
    # 2. Valida dados
    valido, msg = totvs.validar_pedido_antes_envio(payload)
    
    # 3. Envia para TOTVS
    resultado = totvs.enviar_pedido_para_totvs(payload)
    
    # 4. Registra resultado
    db.registrar_envio_totvs(pedido_id, resultado)
    
    # 5. Retorna sucesso ou erro
    return jsonify(resultado)
```

---

## 🆘 Troubleshooting

### Erro: "Timeout ao conectar"
**Causa:** Firewall bloqueando ou URL incorreta
**Solução:** 
1. Teste ping: `ping 172.16.45.117`
2. Verifique firewall
3. Confirme porta com TI

### Erro: "401 Unauthorized"
**Causa:** Credenciais incorretas
**Solução:** Verifique usuário e senha

### Erro: "404 Not Found"
**Causa:** Endpoint incorreto
**Solução:** Confirme endpoint com TI

### Erro: "Campo obrigatório não informado"
**Causa:** Mapeamento incompleto
**Solução:** Adicione campo no `converter_payload_para_totvs()`

---

## 📞 Quem Procurar

**Para informações técnicas do TOTVS:**
- 👨‍💼 Consultor Protheus da empresa
- 👨‍💻 Equipe de TI/Infraestrutura
- 📞 Suporte TOTVS: 0800-770-9130

**Perguntas importantes:**
1. "Qual a URL da API REST do nosso Protheus?"
2. "Qual usuário posso usar para integração?"
3. "Quais campos são obrigatórios no SC7 (pedido)?"
4. "Qual o código TES padrão para compras?"
5. "Temos ambiente de homologação?"

---

## ✨ Recursos Extras Implementados

### Adicionar botão "Enviar ao TOTVS" no modal de histórico

No arquivo `templates/solicitacoes.html`, você pode adicionar:

```javascript
// Na função verDetalhesPedido(), adicione botão:
function verDetalhesPedido(pedidoId) {
    // ... código existente ...
    
    // Adiciona botão para enviar ao TOTVS
    if (p.status === 'Gerado') {
        const confirma = confirm('Deseja enviar este pedido ao TOTVS?');
        if (confirma) {
            enviarPedidoTotvs(pedidoId);
        }
    }
}

function enviarPedidoTotvs(pedidoId) {
    fetch(`/api/pedido/${pedidoId}/enviar-totvs`, {
        method: 'POST'
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            alert('✅ Pedido enviado ao TOTVS com sucesso!');
            location.reload();
        } else {
            alert('❌ Erro: ' + data.error);
        }
    });
}
```

---

## 📝 Checklist Final

Antes de colocar em produção:

- [ ] URL da API configurada
- [ ] Credenciais testadas
- [ ] Endpoint confirmado
- [ ] Mapeamento de campos validado
- [ ] TES correto configurado
- [ ] Testado em homologação
- [ ] Validado criação no TOTVS
- [ ] Verificado vinculação SC → PC
- [ ] Logs de erro funcionando
- [ ] Equipe treinada

---

## 🎓 Dica Final

**Comece simples!**

1. Configure apenas conexão primeiro
2. Teste com 1 pedido simples (1 item)
3. Valide no TOTVS
4. Ajuste campos conforme necessário
5. Só depois libere para uso geral

Boa sorte! 🚀
