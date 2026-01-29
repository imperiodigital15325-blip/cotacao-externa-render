"""
=============================================================================
COTAÇÃO EXTERNA - APLICAÇÃO ISOLADA PARA RENDER
=============================================================================
Aplicação externa para fornecedores preencherem cotações via link.
- Não acessa banco de dados interno
- Comunicação apenas via JSON controlado
- Tokens com validação e expiração
- Pronto para Render (gratuito) e migração futura
=============================================================================
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
import secrets
import time
from functools import wraps

# =============================================================================
# CONFIGURAÇÃO DA APLICAÇÃO
# =============================================================================

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# CORS para permitir comunicação com sistema interno (quando necessário)
CORS(app, resources={
    r"/api/*": {
        "origins": os.environ.get('ALLOWED_ORIGINS', '*').split(','),
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-API-Key"]
    }
})

# Chave secreta compartilhada com sistema interno (definida via variável de ambiente)
API_SECRET_KEY = os.environ.get('API_SECRET_KEY', 'chave-secreta-compartilhada-trocar-em-producao')

# Tempo de expiração do token em horas (padrão: 72 horas)
TOKEN_EXPIRATION_HOURS = int(os.environ.get('TOKEN_EXPIRATION_HOURS', 72))

# =============================================================================
# ARMAZENAMENTO EM MEMÓRIA (para POC - em produção usar Redis ou similar)
# =============================================================================

# Dicionário para armazenar cotações ativas
# Estrutura: { token: { dados_cotacao, created_at, expires_at, status } }
cotacoes_ativas = {}

# Dicionário para armazenar respostas
# Estrutura: { token: { resposta_json, submitted_at } }
respostas_enviadas = {}

# =============================================================================
# FUNÇÕES DE SEGURANÇA
# =============================================================================

def gerar_token_seguro():
    """Gera um token seguro único"""
    return secrets.token_urlsafe(32)


def calcular_hash_dados(dados: dict) -> str:
    """Calcula hash SHA256 dos dados para validação de integridade"""
    dados_str = json.dumps(dados, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(dados_str.encode()).hexdigest()


def validar_assinatura(dados: dict, assinatura_recebida: str) -> bool:
    """Valida assinatura HMAC dos dados recebidos do sistema interno"""
    dados_str = json.dumps(dados, sort_keys=True, ensure_ascii=False)
    assinatura_calculada = hmac.new(
        API_SECRET_KEY.encode(),
        dados_str.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(assinatura_calculada, assinatura_recebida)


def gerar_assinatura(dados: dict) -> str:
    """Gera assinatura HMAC para dados de resposta"""
    dados_str = json.dumps(dados, sort_keys=True, ensure_ascii=False)
    return hmac.new(
        API_SECRET_KEY.encode(),
        dados_str.encode(),
        hashlib.sha256
    ).hexdigest()


def api_key_required(f):
    """Decorator para validar API Key nas rotas protegidas"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != API_SECRET_KEY:
            return jsonify({'success': False, 'error': 'API Key inválida'}), 401
        return f(*args, **kwargs)
    return decorated_function


# =============================================================================
# ROTAS PÚBLICAS (ACESSO DO FORNECEDOR)
# =============================================================================

@app.route('/')
def index():
    """
    Rota raiz - bloqueada para evitar navegação pública.
    Retorna HTTP 403 com mensagem de acesso não autorizado.
    """
    return render_template('erro.html',
                         titulo='Acesso Não Autorizado',
                         mensagem='Este sistema não possui acesso público.',
                         detalhes='Utilize apenas o link de cotação enviado pelo comprador.'), 403


@app.route('/cotar')
def cotar():
    """
    Página principal de cotação para o fornecedor.
    Recebe token via query string: /cotar?token=ABC123
    """
    token = request.args.get('token')
    
    if not token:
        return render_template('erro.html', 
                             titulo='Link Inválido',
                             mensagem='O link de cotação está incompleto.',
                             detalhes='Verifique se copiou o link completo ou solicite um novo ao comprador.')
    
    # Verifica se o token existe
    if token not in cotacoes_ativas:
        return render_template('erro.html',
                             titulo='Cotação Não Encontrada',
                             mensagem='Esta cotação não existe ou o link é inválido.',
                             detalhes='Solicite um novo link ao comprador.')
    
    cotacao = cotacoes_ativas[token]
    
    # Verifica expiração
    if datetime.now() > cotacao['expires_at']:
        return render_template('erro.html',
                             titulo='Cotação Expirada',
                             mensagem='O prazo para responder esta cotação expirou.',
                             detalhes=f'A cotação expirou em {cotacao["expires_at"].strftime("%d/%m/%Y às %H:%M")}.')
    
    # Verifica se já foi respondida
    if token in respostas_enviadas:
        resposta = respostas_enviadas[token]
        return render_template('ja_respondida.html',
                             cotacao=cotacao['dados'],
                             resposta=resposta,
                             data_envio=resposta['submitted_at'].strftime('%d/%m/%Y às %H:%M'))
    
    # Renderiza página de cotação
    return render_template('cotacao.html',
                         token=token,
                         cotacao=cotacao['dados'],
                         expires_at=cotacao['expires_at'].strftime('%d/%m/%Y às %H:%M'))


@app.route('/api/responder', methods=['POST'])
def api_responder():
    """
    API para o fornecedor enviar a resposta da cotação.
    Recebe JSON com preços, prazos e observações.
    """
    try:
        dados = request.get_json()
        
        if not dados:
            return jsonify({'success': False, 'error': 'Dados não recebidos'}), 400
        
        token = dados.get('token')
        
        if not token or token not in cotacoes_ativas:
            return jsonify({'success': False, 'error': 'Token inválido'}), 400
        
        cotacao = cotacoes_ativas[token]
        
        # Verifica expiração
        if datetime.now() > cotacao['expires_at']:
            return jsonify({'success': False, 'error': 'Cotação expirada'}), 400
        
        # Verifica se já foi respondida
        if token in respostas_enviadas:
            return jsonify({'success': False, 'error': 'Esta cotação já foi respondida'}), 400
        
        # Valida estrutura da resposta
        respostas = dados.get('respostas', [])
        if not respostas:
            return jsonify({'success': False, 'error': 'Nenhuma resposta enviada'}), 400
        
        # Monta resposta estruturada
        resposta_final = {
            'token': token,
            'cotacao_id': cotacao['dados']['cotacao_id'],
            'fornecedor_id': cotacao['dados']['fornecedor']['id'],
            'fornecedor_nome': cotacao['dados']['fornecedor']['nome'],
            'submitted_at': datetime.now().isoformat(),
            'respostas': respostas,
            'info_geral': {
                'frete_total': dados.get('frete_total', 0),
                'condicao_pagamento': dados.get('condicao_pagamento', ''),
                'validade_proposta': dados.get('validade_proposta', ''),
                'observacao_geral': dados.get('observacao_geral', '')
            }
        }
        
        # Adiciona hash de integridade
        resposta_final['hash'] = calcular_hash_dados(resposta_final)
        
        # Adiciona assinatura para validação pelo sistema interno
        resposta_final['assinatura'] = gerar_assinatura(resposta_final)
        
        # Armazena resposta
        respostas_enviadas[token] = {
            'dados': resposta_final,
            'submitted_at': datetime.now()
        }
        
        # Atualiza status da cotação
        cotacoes_ativas[token]['status'] = 'respondida'
        
        return jsonify({
            'success': True,
            'message': 'Cotação enviada com sucesso!',
            'protocolo': f"RESP-{token[:8].upper()}"
        })
        
    except Exception as e:
        print(f"[ERRO] api_responder: {e}")
        return jsonify({'success': False, 'error': 'Erro interno ao processar resposta'}), 500


# =============================================================================
# ROTAS DA API INTERNA (COMUNICAÇÃO COM SISTEMA PRINCIPAL)
# =============================================================================

@app.route('/api/cotacao/registrar', methods=['POST'])
@api_key_required
def api_registrar_cotacao():
    """
    API para o sistema interno registrar uma nova cotação.
    Recebe os dados da cotação e retorna o link para o fornecedor.
    """
    try:
        dados = request.get_json()
        
        if not dados:
            return jsonify({'success': False, 'error': 'Dados não recebidos'}), 400
        
        # Valida assinatura (se fornecida)
        assinatura = dados.pop('assinatura', None)
        if assinatura and not validar_assinatura(dados, assinatura):
            return jsonify({'success': False, 'error': 'Assinatura inválida'}), 401
        
        # Valida campos obrigatórios
        campos_obrigatorios = ['cotacao_id', 'fornecedor', 'itens']
        for campo in campos_obrigatorios:
            if campo not in dados:
                return jsonify({'success': False, 'error': f'Campo obrigatório ausente: {campo}'}), 400
        
        # Gera token único
        token = gerar_token_seguro()
        
        # Define expiração
        expiration_hours = dados.get('expiration_hours', TOKEN_EXPIRATION_HOURS)
        expires_at = datetime.now() + timedelta(hours=expiration_hours)
        
        # Armazena cotação
        cotacoes_ativas[token] = {
            'dados': dados,
            'created_at': datetime.now(),
            'expires_at': expires_at,
            'status': 'ativa'
        }
        
        # Monta URL do link
        base_url = os.environ.get('BASE_URL', request.host_url.rstrip('/'))
        link_cotacao = f"{base_url}/cotar?token={token}"
        
        return jsonify({
            'success': True,
            'token': token,
            'link': link_cotacao,
            'expires_at': expires_at.isoformat(),
            'message': 'Cotação registrada com sucesso'
        })
        
    except Exception as e:
        print(f"[ERRO] api_registrar_cotacao: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/<token>/status', methods=['GET'])
@api_key_required
def api_status_cotacao(token):
    """
    API para o sistema interno verificar status de uma cotação.
    Retorna se foi respondida, expirada, etc.
    """
    if token not in cotacoes_ativas:
        return jsonify({'success': False, 'error': 'Token não encontrado'}), 404
    
    cotacao = cotacoes_ativas[token]
    
    status = 'ativa'
    if datetime.now() > cotacao['expires_at']:
        status = 'expirada'
    elif token in respostas_enviadas:
        status = 'respondida'
    
    return jsonify({
        'success': True,
        'token': token,
        'status': status,
        'created_at': cotacao['created_at'].isoformat(),
        'expires_at': cotacao['expires_at'].isoformat(),
        'respondida': token in respostas_enviadas
    })


@app.route('/api/cotacao/<token>/resposta', methods=['GET'])
@api_key_required
def api_obter_resposta(token):
    """
    API para o sistema interno obter a resposta de uma cotação.
    Retorna o JSON completo da resposta do fornecedor.
    """
    if token not in cotacoes_ativas:
        return jsonify({'success': False, 'error': 'Token não encontrado'}), 404
    
    if token not in respostas_enviadas:
        return jsonify({'success': False, 'error': 'Cotação ainda não foi respondida'}), 404
    
    resposta = respostas_enviadas[token]
    
    return jsonify({
        'success': True,
        'resposta': resposta['dados']
    })


@app.route('/api/cotacao/<token>/invalidar', methods=['POST'])
@api_key_required
def api_invalidar_cotacao(token):
    """
    API para o sistema interno invalidar uma cotação (cancelar link).
    """
    if token not in cotacoes_ativas:
        return jsonify({'success': False, 'error': 'Token não encontrado'}), 404
    
    # Remove a cotação
    del cotacoes_ativas[token]
    
    # Remove resposta se existir
    if token in respostas_enviadas:
        del respostas_enviadas[token]
    
    return jsonify({
        'success': True,
        'message': 'Cotação invalidada com sucesso'
    })


@app.route('/api/respostas/pendentes', methods=['GET'])
@api_key_required
def api_respostas_pendentes():
    """
    API para o sistema interno listar todas as respostas pendentes de importação.
    """
    pendentes = []
    
    for token, resposta in respostas_enviadas.items():
        pendentes.append({
            'token': token,
            'cotacao_id': resposta['dados']['cotacao_id'],
            'fornecedor_id': resposta['dados']['fornecedor_id'],
            'fornecedor_nome': resposta['dados']['fornecedor_nome'],
            'submitted_at': resposta['submitted_at'].isoformat()
        })
    
    return jsonify({
        'success': True,
        'total': len(pendentes),
        'respostas': pendentes
    })


# =============================================================================
# ROTAS DE MONITORAMENTO
# =============================================================================

@app.route('/health')
def health_check():
    """Health check para o Render"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'cotacoes_ativas': len(cotacoes_ativas),
        'respostas_pendentes': len(respostas_enviadas)
    })


@app.route('/api/stats')
@api_key_required
def api_stats():
    """Estatísticas do sistema (protegido)"""
    ativas = sum(1 for c in cotacoes_ativas.values() 
                 if datetime.now() <= c['expires_at'] and c['status'] == 'ativa')
    expiradas = sum(1 for c in cotacoes_ativas.values() 
                    if datetime.now() > c['expires_at'])
    respondidas = len(respostas_enviadas)
    
    return jsonify({
        'success': True,
        'stats': {
            'total_cotacoes': len(cotacoes_ativas),
            'ativas': ativas,
            'expiradas': expiradas,
            'respondidas': respondidas
        }
    })


# =============================================================================
# INICIALIZAÇÃO
# =============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    
    print(f"""
    ╔══════════════════════════════════════════════════════════════╗
    ║         COTAÇÃO EXTERNA - APLICAÇÃO RENDER                  ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  Porta: {port}                                                 ║
    ║  Debug: {debug}                                                ║
    ║  Token Expiration: {TOKEN_EXPIRATION_HOURS}h                                    ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
    
    app.run(host='0.0.0.0', port=port, debug=debug)
