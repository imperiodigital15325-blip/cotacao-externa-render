"""
=============================================================================
MÓDULO DE INTEGRAÇÃO - COTAÇÃO EXTERNA (RENDER)
=============================================================================
Este módulo permite que o sistema interno se comunique com a aplicação
externa hospedada no Render para gerenciar cotações online.

Adicione este arquivo ao seu projeto principal e importe onde necessário:
    from integracao_cotacao_externa import CotacaoExternaClient

Uso:
    client = CotacaoExternaClient()
    resultado = client.registrar_cotacao(dados_cotacao)
    print(resultado['link'])  # Link para enviar ao fornecedor
=============================================================================
"""

import requests
import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any


class CotacaoExternaClient:
    """
    Cliente para comunicação com a aplicação de Cotação Externa no Render.
    """
    
    def __init__(self, base_url: str = None, api_key: str = None):
        """
        Inicializa o cliente.
        
        Args:
            base_url: URL base da aplicação externa (ex: https://cotacao-externa.onrender.com)
            api_key: Chave de API compartilhada
        """
        self.base_url = base_url or os.environ.get(
            'COTACAO_EXTERNA_URL', 
            'https://SEU-APP-AQUI.onrender.com'  # ⚠️ ALTERAR após deploy!
        )
        self.api_key = api_key or os.environ.get(
            'COTACAO_EXTERNA_API_KEY',
            'chave-compartilhada-sistema-interno-987654321'  # ⚠️ DEVE SER A MESMA do Render!
        )
        self.timeout = 30  # segundos
    
    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers padrão para requisições"""
        return {
            'Content-Type': 'application/json',
            'X-API-Key': self.api_key
        }
    
    def _gerar_assinatura(self, dados: dict) -> str:
        """Gera assinatura HMAC-SHA256 dos dados"""
        dados_str = json.dumps(dados, sort_keys=True, ensure_ascii=False)
        return hmac.new(
            self.api_key.encode(),
            dados_str.encode(),
            hashlib.sha256
        ).hexdigest()
    
    def _validar_assinatura(self, dados: dict, assinatura: str) -> bool:
        """Valida assinatura HMAC dos dados recebidos"""
        assinatura_calculada = self._gerar_assinatura(dados)
        return hmac.compare_digest(assinatura_calculada, assinatura)
    
    def registrar_cotacao(
        self,
        cotacao_id: int,
        codigo: str,
        fornecedor_id: int,
        fornecedor_nome: str,
        itens: List[Dict],
        fornecedor_codigo: str = None,
        fornecedor_email: str = None,
        data_validade: str = None,
        informacao_fornecedor: str = None,
        expiration_hours: int = 72
    ) -> Dict[str, Any]:
        """
        Registra uma cotação na aplicação externa e retorna o link.
        
        Args:
            cotacao_id: ID da cotação no sistema interno
            codigo: Código da cotação (ex: COT-2026-0001)
            fornecedor_id: ID do fornecedor
            fornecedor_nome: Nome/razão social do fornecedor
            itens: Lista de itens da cotação
            fornecedor_codigo: Código do fornecedor (opcional)
            fornecedor_email: Email do fornecedor (opcional)
            data_validade: Data limite para resposta (opcional)
            informacao_fornecedor: Mensagem para o fornecedor (opcional)
            expiration_hours: Horas até o link expirar (padrão: 72)
        
        Returns:
            Dict com token, link e data de expiração
        
        Raises:
            Exception: Se houver erro na comunicação
        """
        # Monta payload
        payload = {
            'cotacao_id': cotacao_id,
            'codigo': codigo,
            'fornecedor': {
                'id': fornecedor_id,
                'nome': fornecedor_nome,
                'codigo': fornecedor_codigo,
                'email': fornecedor_email
            },
            'itens': itens,
            'data_validade': data_validade,
            'informacao_fornecedor': informacao_fornecedor,
            'expiration_hours': expiration_hours
        }
        
        # Adiciona assinatura
        payload['assinatura'] = self._gerar_assinatura(payload)
        
        try:
            response = requests.post(
                f"{self.base_url}/api/cotacao/registrar",
                json=payload,
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            data = response.json()
            
            if response.status_code == 200 and data.get('success'):
                return {
                    'success': True,
                    'token': data['token'],
                    'link': data['link'],
                    'expires_at': data['expires_at']
                }
            else:
                return {
                    'success': False,
                    'error': data.get('error', 'Erro desconhecido')
                }
                
        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'Timeout na comunicação'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': 'Erro de conexão com servidor externo'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def verificar_status(self, token: str) -> Dict[str, Any]:
        """
        Verifica o status de uma cotação.
        
        Args:
            token: Token da cotação
        
        Returns:
            Dict com status, datas e se foi respondida
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/cotacao/{token}/status",
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            return response.json()
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def obter_resposta(self, token: str) -> Dict[str, Any]:
        """
        Obtém a resposta de uma cotação respondida.
        
        Args:
            token: Token da cotação
        
        Returns:
            Dict com a resposta completa do fornecedor
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/cotacao/{token}/resposta",
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            data = response.json()
            
            if data.get('success') and 'resposta' in data:
                resposta = data['resposta']
                
                # Valida assinatura da resposta
                assinatura = resposta.pop('assinatura', None)
                if assinatura:
                    dados_para_validar = resposta.copy()
                    dados_para_validar.pop('hash', None)
                    
                    # Nota: A validação de assinatura pode ser feita aqui
                    # Por enquanto, retornamos a resposta diretamente
                
                return {
                    'success': True,
                    'resposta': resposta
                }
            else:
                return data
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def invalidar_cotacao(self, token: str) -> Dict[str, Any]:
        """
        Invalida/cancela uma cotação (expira o link).
        
        Args:
            token: Token da cotação
        
        Returns:
            Dict com resultado da operação
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/cotacao/{token}/invalidar",
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            return response.json()
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def listar_respostas_pendentes(self) -> Dict[str, Any]:
        """
        Lista todas as respostas pendentes de importação.
        
        Returns:
            Dict com lista de respostas pendentes
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/respostas/pendentes",
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            return response.json()
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def health_check(self) -> bool:
        """
        Verifica se a aplicação externa está online.
        
        Returns:
            True se online, False caso contrário
        """
        try:
            response = requests.get(
                f"{self.base_url}/health",
                timeout=5
            )
            return response.status_code == 200
        except:
            return False


# =============================================================================
# FUNÇÕES AUXILIARES PARA INTEGRAÇÃO NO APP.PY PRINCIPAL
# =============================================================================

def formatar_itens_para_externa(itens_cotacao: list) -> list:
    """
    Formata itens da cotação do banco de dados para o formato da API externa.
    
    Args:
        itens_cotacao: Lista de itens do banco (dict ou Row objects)
    
    Returns:
        Lista formatada para a API
    """
    itens_formatados = []
    
    for item in itens_cotacao:
        item_dict = dict(item) if hasattr(item, 'keys') else item
        
        itens_formatados.append({
            'id': item_dict.get('id'),
            'cod_produto': item_dict.get('cod_produto', ''),
            'descricao': item_dict.get('descricao_produto', ''),
            'quantidade': float(item_dict.get('quantidade', 0)),
            'unidade': item_dict.get('unidade', 'UN'),
            'observacao': item_dict.get('observacao', '')
        })
    
    return itens_formatados


def importar_resposta_externa(resposta_json: dict, db_module) -> dict:
    """
    Importa resposta da aplicação externa para o banco de dados local.
    
    Args:
        resposta_json: JSON de resposta da API externa
        db_module: Módulo de banco de dados (database.py)
    
    Returns:
        Dict com resultado da importação
    """
    try:
        cotacao_id = resposta_json.get('cotacao_id')
        fornecedor_id = resposta_json.get('fornecedor_id')
        respostas = resposta_json.get('respostas', [])
        info_geral = resposta_json.get('info_geral', {})
        
        importados = 0
        erros = 0
        
        for resp in respostas:
            try:
                db_module.registrar_resposta_fornecedor(
                    cotacao_id=cotacao_id,
                    fornecedor_id=fornecedor_id,
                    item_id=resp.get('item_id'),
                    preco=resp.get('preco_unitario', 0),
                    prazo=resp.get('prazo_entrega', 0),
                    condicao=info_geral.get('condicao_pagamento', ''),
                    observacao=resp.get('observacao', ''),
                    frete=0  # Frete vai no fornecedor, não no item
                )
                importados += 1
            except Exception as e:
                print(f"[ERRO] Importar item {resp.get('item_id')}: {e}")
                erros += 1
        
        # Atualiza dados gerais do fornecedor
        if info_geral:
            try:
                db_module.editar_fornecedor_cotacao(
                    fornecedor_id=fornecedor_id,
                    frete_total=info_geral.get('frete_total', 0),
                    condicao_pagamento=info_geral.get('condicao_pagamento', ''),
                    observacao_geral=info_geral.get('observacao_geral', '')
                )
            except Exception as e:
                print(f"[ERRO] Atualizar fornecedor: {e}")
        
        return {
            'success': True,
            'importados': importados,
            'erros': erros,
            'message': f'Importados {importados} itens com {erros} erros'
        }
        
    except Exception as e:
        return {'success': False, 'error': str(e)}


# =============================================================================
# EXEMPLO DE USO
# =============================================================================

if __name__ == '__main__':
    # Teste básico
    client = CotacaoExternaClient(
        base_url='http://localhost:5000',  # Para teste local
        api_key='chave-secreta-compartilhada-trocar-em-producao'
    )
    
    # Verifica se está online
    if client.health_check():
        print("✅ Aplicação externa está online!")
        
        # Teste de registro
        resultado = client.registrar_cotacao(
            cotacao_id=1,
            codigo='COT-TESTE-001',
            fornecedor_id=1,
            fornecedor_nome='Fornecedor Teste',
            itens=[
                {
                    'id': 1,
                    'cod_produto': 'PROD001',
                    'descricao': 'Produto de Teste',
                    'quantidade': 100,
                    'unidade': 'UN'
                }
            ]
        )
        
        if resultado['success']:
            print(f"✅ Cotação registrada!")
            print(f"   Token: {resultado['token'][:20]}...")
            print(f"   Link: {resultado['link']}")
        else:
            print(f"❌ Erro: {resultado['error']}")
    else:
        print("❌ Aplicação externa está offline!")
