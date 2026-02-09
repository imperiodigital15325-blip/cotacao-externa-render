# =============================================================================
# MÓDULO DE INTEGRAÇÃO COM TOTVS - PEDIDOS DE COMPRA
# =============================================================================
# Este módulo gerencia a comunicação com a API REST do TOTVS para criação
# de pedidos de compra (SC7010) a partir das solicitações (SC1010).
# =============================================================================

import requests
from requests.auth import HTTPBasicAuth
import json
from datetime import datetime

# =============================================================================
# CONFIGURAÇÕES DA API DO TOTVS
# =============================================================================
# ⚠️ VOCÊ PRECISA PREENCHER ESSES DADOS COM AS INFORMAÇÕES REAIS DO SEU TOTVS

# URL base da API REST do TOTVS
TOTVS_API_URL = "http://172.16.45.117:8080/rest"  # ⚠️ AJUSTAR CONFORME SEU AMBIENTE

# Credenciais de autenticação
TOTVS_API_USER = "seu_usuario_api"  # ⚠️ PREENCHER
TOTVS_API_PASSWORD = "sua_senha_api"  # ⚠️ PREENCHER

# Endpoints específicos
TOTVS_ENDPOINT_PEDIDO = "/WSSC7"  # ⚠️ CONFIRMAR O ENDPOINT CORRETO

# Timeout das requisições (segundos)
REQUEST_TIMEOUT = 30

# Empresa e Filial padrão
EMPRESA = "01"
FILIAL = "01"


# =============================================================================
# FUNÇÕES DE INTEGRAÇÃO
# =============================================================================

def testar_conexao_totvs():
    """
    Testa a conexão com a API do TOTVS.
    Retorna True se conectou com sucesso, False caso contrário.
    """
    try:
        url = f"{TOTVS_API_URL}/api/oauth2/v1/token"
        
        response = requests.get(
            url,
            auth=HTTPBasicAuth(TOTVS_API_USER, TOTVS_API_PASSWORD),
            timeout=5
        )
        
        if response.status_code in [200, 401]:  # 401 também indica que o servidor respondeu
            print(f"[TOTVS] Servidor respondendo: {url}")
            return True
        else:
            print(f"[TOTVS] Erro na conexão: Status {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"[TOTVS] Erro ao conectar: {e}")
        return False


def converter_payload_para_totvs(pedido):
    """
    Converte o payload do nosso sistema para o formato esperado pelo TOTVS.
    
    Args:
        pedido: Dict com dados do pedido no nosso formato
        
    Returns:
        Dict no formato esperado pela API do TOTVS
    """
    # ⚠️ VOCÊ PRECISA AJUSTAR ESTE MAPEAMENTO CONFORME A DOCUMENTAÇÃO DO SEU TOTVS
    
    # Formatar data no formato do TOTVS (geralmente YYYYMMDD)
    data_pedido = pedido.get('data_pedido', '')
    if data_pedido:
        # Converte de YYYY-MM-DD para YYYYMMDD
        data_totvs = data_pedido.replace('-', '')
    else:
        data_totvs = datetime.now().strftime('%Y%m%d')
    
    # Monta cabeçalho do pedido (SC7)
    payload_totvs = {
        "empresa": EMPRESA,
        "filial": FILIAL,
        "pedido": {
            "C7_NUM": pedido.get('numero_pedido', ''),
            "C7_EMISSAO": data_totvs,
            "C7_FORNECE": pedido.get('fornecedor', {}).get('codigo', ''),
            "C7_LOJA": "01",  # Loja padrão
            "C7_COND": pedido.get('condicao_pagamento', ''),
            "C7_CONTATO": pedido.get('contato', ''),
            "C7_OBS": pedido.get('observacoes', ''),
            "C7_TIPO": "1",  # 1 = Normal, consultar tabela SX5
            "C7_MOEDA": "1",  # 1 = Real
            "itens": []
        }
    }
    
    # Adiciona itens do pedido
    for idx, item in enumerate(pedido.get('itens', []), start=1):
        item_totvs = {
            "C7_ITEM": str(idx).zfill(4),  # 0001, 0002, etc
            "C7_PRODUTO": item.get('produto_id', ''),
            "C7_DESCRI": item.get('descricao', ''),
            "C7_UM": item.get('unidade', 'UN'),
            "C7_QUANT": item.get('quantidade', 0),
            "C7_PRECO": item.get('valor_unitario', 0),
            "C7_TOTAL": item.get('quantidade', 0) * item.get('valor_unitario', 0),
            "C7_IPI": item.get('ipi', 0),
            "C7_DATPRF": item.get('data_necessidade', '').replace('-', ''),  # YYYYMMDD
            "C7_NUMSC": item.get('solicitacao_id', ''),
            "C7_ITEMSC": item.get('item_sc', ''),
            "C7_SEGUM": item.get('unidade', 'UN'),
            "C7_QTSEGUM": item.get('quantidade', 0),
            "C7_TES": "",  # ⚠️ PREENCHER COM TES PADRÃO DE COMPRA
            "C7_LOCAL": "01"  # Armazém padrão
        }
        
        payload_totvs["pedido"]["itens"].append(item_totvs)
    
    return payload_totvs


def enviar_pedido_para_totvs(pedido):
    """
    Envia o pedido para a API do TOTVS.
    
    Args:
        pedido: Dict com dados do pedido no formato do nosso sistema
        
    Returns:
        Dict com resultado da operação {
            'success': bool,
            'numero_pedido_totvs': str,
            'message': str,
            'response': dict
        }
    """
    try:
        # Converte payload
        payload_totvs = converter_payload_para_totvs(pedido)
        
        # URL do endpoint
        url = f"{TOTVS_API_URL}{TOTVS_ENDPOINT_PEDIDO}"
        
        print(f"[TOTVS] Enviando pedido para: {url}")
        print(f"[TOTVS] Payload: {json.dumps(payload_totvs, indent=2)}")
        
        # Faz a requisição POST
        response = requests.post(
            url,
            json=payload_totvs,
            auth=HTTPBasicAuth(TOTVS_API_USER, TOTVS_API_PASSWORD),
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            timeout=REQUEST_TIMEOUT
        )
        
        # Processa resposta
        if response.status_code in [200, 201]:
            response_data = response.json()
            
            return {
                'success': True,
                'numero_pedido_totvs': response_data.get('C7_NUM', pedido.get('numero_pedido')),
                'message': 'Pedido criado com sucesso no TOTVS',
                'response': response_data,
                'status_code': response.status_code
            }
        else:
            return {
                'success': False,
                'numero_pedido_totvs': None,
                'message': f'Erro ao criar pedido no TOTVS: Status {response.status_code}',
                'response': response.text,
                'status_code': response.status_code
            }
            
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'message': 'Timeout ao conectar com TOTVS',
            'error': 'timeout'
        }
    except requests.exceptions.ConnectionError:
        return {
            'success': False,
            'message': 'Erro de conexão com TOTVS',
            'error': 'connection_error'
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'Erro inesperado: {str(e)}',
            'error': str(e)
        }


def consultar_pedido_totvs(numero_pedido):
    """
    Consulta um pedido específico no TOTVS.
    
    Args:
        numero_pedido: Número do pedido a consultar
        
    Returns:
        Dict com dados do pedido ou None se não encontrado
    """
    try:
        url = f"{TOTVS_API_URL}{TOTVS_ENDPOINT_PEDIDO}/{numero_pedido}"
        
        response = requests.get(
            url,
            auth=HTTPBasicAuth(TOTVS_API_USER, TOTVS_API_PASSWORD),
            timeout=REQUEST_TIMEOUT
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"[TOTVS] Pedido não encontrado: {numero_pedido}")
            return None
            
    except Exception as e:
        print(f"[TOTVS] Erro ao consultar pedido: {e}")
        return None


def validar_pedido_antes_envio(pedido):
    """
    Valida os dados do pedido antes de enviar ao TOTVS.
    
    Args:
        pedido: Dict com dados do pedido
        
    Returns:
        Tuple (bool, str) - (válido?, mensagem de erro)
    """
    # Validações obrigatórias
    if not pedido.get('fornecedor', {}).get('codigo'):
        return False, "Código do fornecedor é obrigatório"
    
    if not pedido.get('data_pedido'):
        return False, "Data do pedido é obrigatória"
    
    itens = pedido.get('itens', [])
    if not itens:
        return False, "Pedido deve ter pelo menos um item"
    
    # Valida itens
    for idx, item in enumerate(itens, start=1):
        if not item.get('produto_id'):
            return False, f"Item {idx}: Código do produto é obrigatório"
        
        if not item.get('quantidade') or item.get('quantidade') <= 0:
            return False, f"Item {idx}: Quantidade inválida"
        
        if not item.get('valor_unitario') or item.get('valor_unitario') <= 0:
            return False, f"Item {idx}: Valor unitário inválido"
        
        if not item.get('solicitacao_id'):
            return False, f"Item {idx}: Número da solicitação é obrigatório"
    
    return True, "Validação OK"


# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================

def obter_configuracoes():
    """
    Retorna as configurações atuais da integração.
    Útil para debug e verificação.
    """
    return {
        'api_url': TOTVS_API_URL,
        'endpoint_pedido': TOTVS_ENDPOINT_PEDIDO,
        'usuario': TOTVS_API_USER,
        'senha_configurada': bool(TOTVS_API_PASSWORD),
        'timeout': REQUEST_TIMEOUT,
        'empresa': EMPRESA,
        'filial': FILIAL
    }


def log_integracao(pedido_id, acao, resultado, detalhes=None):
    """
    Registra log de integração para auditoria.
    Pode ser expandido para salvar em arquivo ou banco.
    """
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'pedido_id': pedido_id,
        'acao': acao,
        'resultado': resultado,
        'detalhes': detalhes
    }
    
    print(f"[LOG INTEGRAÇÃO] {json.dumps(log_entry, indent=2)}")
    
    # TODO: Salvar em arquivo de log ou tabela do banco
    # with open('logs/integracao_totvs.log', 'a') as f:
    #     f.write(json.dumps(log_entry) + '\n')


# =============================================================================
# FUNÇÃO PRINCIPAL DE TESTE
# =============================================================================

if __name__ == '__main__':
    print("=" * 70)
    print("TESTE DE INTEGRAÇÃO COM TOTVS - PEDIDOS DE COMPRA")
    print("=" * 70)
    
    # 1. Testar conexão
    print("\n1. Testando conexão com TOTVS...")
    if testar_conexao_totvs():
        print("   ✅ Conexão OK")
    else:
        print("   ❌ Falha na conexão - Verifique as configurações")
    
    # 2. Exibir configurações
    print("\n2. Configurações atuais:")
    config = obter_configuracoes()
    for key, value in config.items():
        print(f"   {key}: {value}")
    
    # 3. Exemplo de payload
    print("\n3. Exemplo de pedido de teste:")
    pedido_teste = {
        'numero_pedido': 'PC2026001',
        'data_pedido': '2026-02-05',
        'fornecedor': {'codigo': 'F001', 'nome': 'Fornecedor Teste'},
        'condicao_pagamento': '30 DDL',
        'contato': 'João Silva',
        'observacoes': 'Pedido de teste',
        'itens': [
            {
                'produto_id': 'PROD001',
                'descricao': 'Produto Teste',
                'quantidade': 10,
                'unidade': 'UN',
                'valor_unitario': 15.50,
                'ipi': 0,
                'data_necessidade': '2026-02-10',
                'solicitacao_id': '000123',
                'item_sc': '01'
            }
        ]
    }
    
    # 4. Validar pedido
    print("\n4. Validando pedido...")
    valido, msg = validar_pedido_antes_envio(pedido_teste)
    if valido:
        print(f"   ✅ {msg}")
    else:
        print(f"   ❌ {msg}")
    
    # 5. Converter payload
    print("\n5. Payload convertido para TOTVS:")
    payload_totvs = converter_payload_para_totvs(pedido_teste)
    print(json.dumps(payload_totvs, indent=2, ensure_ascii=False))
    
    print("\n" + "=" * 70)
    print("⚠️  PRÓXIMOS PASSOS:")
    print("=" * 70)
    print("1. Configure as credenciais no topo deste arquivo")
    print("2. Ajuste a URL da API do TOTVS")
    print("3. Confirme o endpoint correto para criação de pedidos")
    print("4. Ajuste o mapeamento de campos na função converter_payload_para_totvs()")
    print("5. Teste a integração em ambiente de homologação primeiro")
    print("=" * 70)
