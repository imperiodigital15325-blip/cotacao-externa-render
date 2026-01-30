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
# ARMAZENAMENTO COM PERSISTÊNCIA EM ARQUIVO JSON
# =============================================================================
# Para sobreviver reinícios do Render (plano gratuito hiberna após inatividade)
# Em produção, considere Redis ou PostgreSQL para maior confiabilidade

STORAGE_FILE = os.environ.get('STORAGE_FILE', 'cotacoes_storage.json')

# Dicionário para armazenar cotações ativas
# Estrutura: { token: { dados_cotacao, created_at, expires_at, status } }
cotacoes_ativas = {}

# Dicionário para armazenar respostas
# Estrutura: { token: { resposta_json, submitted_at } }
respostas_enviadas = {}

# Set para controlar respostas já sincronizadas com o sistema local
# IMPORTANTE: Também precisa ser persistido para não duplicar sincronizações após reinício
respostas_sincronizadas = set()


def salvar_dados_persistentes():
    """
    Salva cotações, respostas E tokens sincronizados em arquivo JSON para persistência.
    Chamado após cada alteração nos dicionários.
    """
    try:
        dados = {
            'cotacoes_ativas': {},
            'respostas_enviadas': {},
            'respostas_sincronizadas': list(respostas_sincronizadas),  # Set → List para JSON
            'salvo_em': datetime.now().isoformat()
        }
        
        # Converte cotações (datetime para string)
        for token, cotacao in cotacoes_ativas.items():
            dados['cotacoes_ativas'][token] = {
                'dados': cotacao['dados'],
                'created_at': cotacao['created_at'].isoformat() if isinstance(cotacao['created_at'], datetime) else cotacao['created_at'],
                'expires_at': cotacao['expires_at'].isoformat() if isinstance(cotacao['expires_at'], datetime) else cotacao['expires_at'],
                'status': cotacao.get('status', 'ativa')
            }
        
        # Converte respostas (datetime para string)
        for token, resposta in respostas_enviadas.items():
            dados['respostas_enviadas'][token] = {
                'dados': resposta.get('dados', resposta),
                'submitted_at': resposta['submitted_at'].isoformat() if isinstance(resposta.get('submitted_at'), datetime) else resposta.get('submitted_at', datetime.now().isoformat())
            }
        
        with open(STORAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        
        print(f"[PERSISTÊNCIA] Dados salvos em {STORAGE_FILE}: {len(cotacoes_ativas)} cotações, {len(respostas_enviadas)} respostas, {len(respostas_sincronizadas)} sincronizadas")
        return True
        
    except Exception as e:
        print(f"[PERSISTÊNCIA] ERRO ao salvar dados: {e}")
        import traceback
        traceback.print_exc()
        return False


def carregar_dados_persistentes():
    """
    Carrega cotações, respostas e tokens sincronizados do arquivo JSON na inicialização.
    """
    global cotacoes_ativas, respostas_enviadas, respostas_sincronizadas
    
    try:
        if not os.path.exists(STORAGE_FILE):
            print(f"[PERSISTÊNCIA] Arquivo {STORAGE_FILE} não existe. Iniciando vazio.")
            return
        
        with open(STORAGE_FILE, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        
        # Carrega cotações (string para datetime)
        cotacoes_carregadas = dados.get('cotacoes_ativas', {})
        for token, cotacao in cotacoes_carregadas.items():
            try:
                cotacoes_ativas[token] = {
                    'dados': cotacao['dados'],
                    'created_at': datetime.fromisoformat(cotacao['created_at']) if isinstance(cotacao['created_at'], str) else cotacao['created_at'],
                    'expires_at': datetime.fromisoformat(cotacao['expires_at']) if isinstance(cotacao['expires_at'], str) else cotacao['expires_at'],
                    'status': cotacao.get('status', 'ativa')
                }
            except Exception as e:
                print(f"[PERSISTÊNCIA] Erro ao carregar cotação {token[:20]}...: {e}")
        
        # Carrega respostas (string para datetime)
        respostas_carregadas = dados.get('respostas_enviadas', {})
        for token, resposta in respostas_carregadas.items():
            try:
                respostas_enviadas[token] = {
                    'dados': resposta.get('dados', resposta),
                    'submitted_at': datetime.fromisoformat(resposta['submitted_at']) if isinstance(resposta.get('submitted_at'), str) else datetime.now()
                }
            except Exception as e:
                print(f"[PERSISTÊNCIA] Erro ao carregar resposta {token[:20]}...: {e}")
        
        # Carrega tokens já sincronizados (List → Set)
        sincronizadas_carregadas = dados.get('respostas_sincronizadas', [])
        respostas_sincronizadas.clear()
        respostas_sincronizadas.update(sincronizadas_carregadas)
        
        salvo_em = dados.get('salvo_em', 'desconhecido')
        print(f"[PERSISTÊNCIA] Dados carregados de {STORAGE_FILE}")
        print(f"[PERSISTÊNCIA] - {len(cotacoes_ativas)} cotações ativas")
        print(f"[PERSISTÊNCIA] - {len(respostas_enviadas)} respostas")
        print(f"[PERSISTÊNCIA] - {len(respostas_sincronizadas)} sincronizadas")
        print(f"[PERSISTÊNCIA] - Último salvamento: {salvo_em}")
        
    except json.JSONDecodeError as e:
        print(f"[PERSISTÊNCIA] ERRO: Arquivo JSON corrompido: {e}")
        # Faz backup do arquivo corrompido
        if os.path.exists(STORAGE_FILE):
            backup_name = f"{STORAGE_FILE}.backup.{int(time.time())}"
            os.rename(STORAGE_FILE, backup_name)
            print(f"[PERSISTÊNCIA] Backup criado: {backup_name}")
    except Exception as e:
        print(f"[PERSISTÊNCIA] ERRO ao carregar dados: {e}")
        import traceback
        traceback.print_exc()


# Carrega dados ao iniciar a aplicação
carregar_dados_persistentes()

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
        
        # *** PERSISTE DADOS APÓS RESPOSTA ***
        salvar_dados_persistentes()
        
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
        
        # *** PERSISTE DADOS APÓS CRIAR COTAÇÃO ***
        salvar_dados_persistentes()
        
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


# =============================================================================
# ENDPOINT OFICIAL DE STATUS (PARA SISTEMA LOCAL)
# =============================================================================

@app.route('/api/cotacao-externa/<token>/status', methods=['GET'])
def api_status_cotacao_externa(token):
    """
    ENDPOINT OFICIAL para o sistema local verificar o status de uma cotação.
    
    Retorna um dos seguintes status:
    - nao_existe: Token não encontrado no Render (expirado da memória ou inválido)
    - aguardando: Cotação existe e está aguardando resposta do fornecedor
    - respondido: Fornecedor já respondeu a cotação
    - expirado: Cotação existe mas o prazo expirou
    
    NÃO REQUER API KEY para facilitar integração.
    """
    print(f"[STATUS] Verificando status do token: {token[:20]}...")
    
    # Token não existe no Render
    if token not in cotacoes_ativas:
        print(f"[STATUS] Token NÃO ENCONTRADO: {token[:20]}...")
        return jsonify({
            'success': True,
            'token': token,
            'status': 'nao_existe',
            'mensagem': 'Token não encontrado. Pode ter expirado da memória do servidor ou ser inválido.',
            'pode_gerar_novo': True
        })
    
    cotacao = cotacoes_ativas[token]
    
    # Verifica se já foi respondida
    if token in respostas_enviadas:
        resposta = respostas_enviadas[token]
        print(f"[STATUS] Token RESPONDIDO: {token[:20]}...")
        return jsonify({
            'success': True,
            'token': token,
            'status': 'respondido',
            'mensagem': 'Fornecedor já respondeu esta cotação.',
            'data_resposta': resposta['submitted_at'].isoformat(),
            'pode_gerar_novo': False
        })
    
    # Verifica expiração
    if datetime.now() > cotacao['expires_at']:
        print(f"[STATUS] Token EXPIRADO: {token[:20]}...")
        return jsonify({
            'success': True,
            'token': token,
            'status': 'expirado',
            'mensagem': f'Cotação expirou em {cotacao["expires_at"].strftime("%d/%m/%Y às %H:%M")}.',
            'expires_at': cotacao['expires_at'].isoformat(),
            'pode_gerar_novo': True
        })
    
    # Cotação ativa aguardando resposta
    print(f"[STATUS] Token AGUARDANDO: {token[:20]}...")
    return jsonify({
        'success': True,
        'token': token,
        'status': 'aguardando',
        'mensagem': 'Aguardando resposta do fornecedor.',
        'created_at': cotacao['created_at'].isoformat(),
        'expires_at': cotacao['expires_at'].isoformat(),
        'pode_gerar_novo': False
    })


@app.route('/api/cotacao-externa/<token>/resposta', methods=['GET'])
def api_obter_resposta_externa(token):
    """
    ENDPOINT OFICIAL para o sistema local baixar a resposta de uma cotação.
    
    NÃO REQUER API KEY para facilitar integração.
    """
    print(f"[RESPOSTA] Buscando resposta do token: {token[:20]}...")
    
    if token not in cotacoes_ativas:
        return jsonify({
            'success': False, 
            'error': 'Token não encontrado',
            'status': 'nao_existe'
        }), 404
    
    if token not in respostas_enviadas:
        return jsonify({
            'success': False, 
            'error': 'Cotação ainda não foi respondida',
            'status': 'aguardando'
        }), 404
    
    resposta = respostas_enviadas[token]
    cotacao = cotacoes_ativas[token]
    
    print(f"[RESPOSTA] Resposta encontrada para token: {token[:20]}...")
    
    return jsonify({
        'success': True,
        'status': 'respondido',
        'resposta': resposta['dados'],
        'data_resposta': resposta['submitted_at'].isoformat(),
        'cotacao_info': {
            'cotacao_id': cotacao['dados'].get('cotacao_id'),
            'fornecedor_id': cotacao['dados'].get('fornecedor', {}).get('id'),
            'fornecedor_nome': cotacao['dados'].get('fornecedor', {}).get('nome')
        }
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
    
    # *** PERSISTE DADOS APÓS INVALIDAR ***
    salvar_dados_persistentes()
    
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


# =============================================================================
# ROTA PARA CRIAR COTAÇÃO EXTERNA (CHAMADA PELO SISTEMA LOCAL)
# =============================================================================

@app.route('/api/criar-cotacao-externa', methods=['POST'])
def api_criar_cotacao_externa():
    """
    API para o sistema interno (local) criar uma cotação externa.
    
    Esta rota é chamada pelo sistema local quando o usuário clica em 
    "Gerar Link Externo". O sistema local envia os dados da cotação
    e esta rota:
    1. Gera um token único
    2. Salva os dados em memória
    3. Retorna o link público para o fornecedor
    
    IMPORTANTE: Esta rota NÃO requer API Key para facilitar a integração.
    Em produção, considere adicionar autenticação por IP ou secret simples.
    """
    try:
        dados = request.get_json()
        
        if not dados:
            return jsonify({'success': False, 'error': 'Dados não recebidos'}), 400
        
        print(f"[CRIAR COTAÇÃO] Dados recebidos: {json.dumps(dados, indent=2, default=str)}")
        
        # Valida campos obrigatórios
        if 'cotacao' not in dados:
            return jsonify({'success': False, 'error': 'Campo "cotacao" é obrigatório'}), 400
        if 'fornecedor' not in dados:
            return jsonify({'success': False, 'error': 'Campo "fornecedor" é obrigatório'}), 400
        if 'itens' not in dados:
            return jsonify({'success': False, 'error': 'Campo "itens" é obrigatório'}), 400
        
        # Gera token único
        token = gerar_token_seguro()
        
        # Define expiração (72 horas por padrão)
        expires_at = datetime.now() + timedelta(hours=TOKEN_EXPIRATION_HOURS)
        
        # Estrutura os dados da cotação para armazenamento
        # IMPORTANTE: A estrutura deve corresponder ao que o template espera
        # O template usa: cotacao.codigo, cotacao.fornecedor.nome, cotacao.itens, etc.
        dados_cotacao = {
            'cotacao_id': dados['cotacao'].get('id'),
            'codigo': dados['cotacao'].get('codigo'),  # Template usa cotacao.codigo
            'observacoes': dados['cotacao'].get('observacoes', ''),
            'informacao_fornecedor': dados['cotacao'].get('informacao_fornecedor', ''),
            'fornecedor': dados['fornecedor'],  # Template usa cotacao.fornecedor.nome
            'itens': dados['itens'],  # Template usa cotacao.itens
            'usuario': dados.get('usuario', 'Sistema')
        }
        
        # Armazena cotação
        cotacoes_ativas[token] = {
            'dados': dados_cotacao,
            'created_at': datetime.now(),
            'expires_at': expires_at,
            'status': 'ativa'
        }
        
        # *** PERSISTE DADOS APÓS CRIAR COTAÇÃO EXTERNA ***
        salvar_dados_persistentes()
        
        # Monta URL do link usando o domínio correto
        # Usa BASE_URL do ambiente ou constrói a partir do host
        base_url = os.environ.get('BASE_URL', 'https://cotacao-externa-render.onrender.com')
        link_externo = f"{base_url}/externo/{token}"
        
        print(f"[CRIAR COTAÇÃO] Token gerado: {token}")
        print(f"[CRIAR COTAÇÃO] Link externo: {link_externo}")
        print(f"[CRIAR COTAÇÃO] Expira em: {expires_at}")
        
        return jsonify({
            'success': True,
            'token': token,
            'link_externo': link_externo,
            'expires_at': expires_at.isoformat(),
            'message': 'Cotação externa criada com sucesso'
        })
        
    except Exception as e:
        print(f"[ERRO] api_criar_cotacao_externa: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/externo/<token>')
def pagina_cotacao_externa(token):
    """
    Página pública para o fornecedor preencher a cotação.
    Acesso via: /externo/<token>
    
    Esta é a rota que os fornecedores acessam quando recebem o link.
    """
    print(f"[EXTERNO] Acesso à cotação externa - Token: {token}")
    
    # Verifica se o token existe
    if token not in cotacoes_ativas:
        print(f"[EXTERNO] Token não encontrado: {token}")
        return render_template('erro.html',
                             titulo='Cotação Não Encontrada',
                             mensagem='Esta cotação não existe ou o link é inválido.',
                             detalhes='Solicite um novo link ao comprador.'), 404
    
    cotacao = cotacoes_ativas[token]
    
    # Verifica expiração
    if datetime.now() > cotacao['expires_at']:
        print(f"[EXTERNO] Cotação expirada: {token}")
        return render_template('erro.html',
                             titulo='Cotação Expirada',
                             mensagem='O prazo para responder esta cotação expirou.',
                             detalhes=f'A cotação expirou em {cotacao["expires_at"].strftime("%d/%m/%Y às %H:%M")}.'), 400
    
    # Verifica se já foi respondida
    if token in respostas_enviadas:
        resposta = respostas_enviadas[token]
        return render_template('ja_respondida.html',
                             cotacao=cotacao['dados'],
                             resposta=resposta,
                             data_envio=resposta['submitted_at'].strftime('%d/%m/%Y às %H:%M'))
    
    # Renderiza página de cotação
    print(f"[EXTERNO] Renderizando cotação para: {cotacao['dados'].get('fornecedor', {}).get('nome', 'N/A')}")
    return render_template('cotacao.html',
                         token=token,
                         cotacao=cotacao['dados'],
                         expires_at=cotacao['expires_at'].strftime('%d/%m/%Y às %H:%M'))


@app.route('/debug-token/<token>')
def debug_token(token):
    """Rota de debug para verificar se um token existe"""
    existe = token in cotacoes_ativas
    dados = None
    if existe:
        cotacao = cotacoes_ativas[token]
        dados = {
            'status': cotacao.get('status'),
            'created_at': cotacao['created_at'].isoformat(),
            'expires_at': cotacao['expires_at'].isoformat(),
            'fornecedor': cotacao['dados'].get('fornecedor', {}).get('nome', 'N/A'),
            'respondida': token in respostas_enviadas
        }
    
    return jsonify({
        'token': token,
        'existe': existe,
        'dados': dados,
        'total_cotacoes_ativas': len(cotacoes_ativas)
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
# API PARA SINCRONIZAÇÃO COM SISTEMA LOCAL (POLLING)
# =============================================================================

# NOTA: respostas_sincronizadas foi movido para o topo do arquivo junto com as outras
# variáveis globais e é persistido/carregado pelo sistema de persistência JSON

@app.route('/api/respostas-pendentes', methods=['GET'])
def api_respostas_pendentes_v2():
    """
    Retorna lista de respostas que ainda não foram sincronizadas com o sistema local.
    O sistema local faz polling nesta rota a cada 20 segundos.
    
    IMPORTANTE: Rota pública para facilitar integração (sistema local pode não ter IP fixo)
    """
    try:
        respostas = []
        
        for token, resposta_data in respostas_enviadas.items():
            # Pula se já foi sincronizada
            if token in respostas_sincronizadas:
                continue
            
            resposta = resposta_data.get('dados', {})
            
            # Monta dados para o sistema local
            respostas.append({
                'token': token,
                'cotacao_id': resposta.get('cotacao_id'),
                'fornecedor_id': resposta.get('fornecedor_id'),
                'fornecedor_nome': resposta.get('fornecedor_nome', 'Fornecedor'),
                'respondido_em': resposta.get('submitted_at'),
                'frete_total': resposta.get('info_geral', {}).get('frete_total', 0),
                'condicao_pagamento': resposta.get('info_geral', {}).get('condicao_pagamento', ''),
                'observacao_geral': resposta.get('info_geral', {}).get('observacao_geral', ''),
                'itens': [
                    {
                        'item_id': r.get('item_id'),
                        'preco_unitario': r.get('preco_unitario', 0),
                        'prazo_entrega': r.get('prazo_entrega', 0),
                        'observacao': r.get('observacao', '')
                    }
                    for r in resposta.get('respostas', [])
                ]
            })
        
        if respostas:
            print(f"[POLLING] {len(respostas)} resposta(s) pendente(s) de sincronização")
        
        return jsonify({
            'success': True,
            'respostas': respostas,
            'count': len(respostas)
        })
        
    except Exception as e:
        print(f"[ERRO] api_respostas_pendentes: {e}")
        return jsonify({'success': False, 'error': str(e), 'respostas': []}), 500


@app.route('/api/confirmar-sincronizacao', methods=['POST'])
def api_confirmar_sincronizacao():
    """
    Sistema local chama esta rota para confirmar que uma resposta foi sincronizada.
    Isso evita que a mesma resposta seja retornada novamente no polling.
    
    IMPORTANTE: Persiste os dados para que a sincronização sobreviva a reinícios.
    """
    try:
        dados = request.get_json()
        token = dados.get('token')
        
        if not token:
            return jsonify({'success': False, 'error': 'Token é obrigatório'}), 400
        
        # Marca como sincronizada
        respostas_sincronizadas.add(token)
        print(f"[SINCRONIZAÇÃO] Resposta {token[:20]}... marcada como sincronizada")
        
        # *** PERSISTE DADOS APÓS CONFIRMAR SINCRONIZAÇÃO ***
        salvar_dados_persistentes()
        
        return jsonify({
            'success': True,
            'message': f'Resposta confirmada como sincronizada',
            'total_sincronizadas': len(respostas_sincronizadas)
        })
        
    except Exception as e:
        print(f"[ERRO] api_confirmar_sincronizacao: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/status-cotacao/<token>', methods=['GET'])
def api_status_cotacao(token):
    """
    Retorna status de uma cotação específica.
    Útil para verificar se uma cotação já foi respondida.
    """
    if token not in cotacoes_ativas:
        return jsonify({
            'success': False,
            'existe': False,
            'error': 'Token não encontrado'
        }), 404
    
    cotacao = cotacoes_ativas[token]
    respondida = token in respostas_enviadas
    sincronizada = token in respostas_sincronizadas
    
    return jsonify({
        'success': True,
        'existe': True,
        'status': cotacao.get('status', 'ativa'),
        'respondida': respondida,
        'sincronizada': sincronizada,
        'fornecedor_nome': cotacao['dados'].get('fornecedor', {}).get('nome', 'N/A'),
        'cotacao_id': cotacao['dados'].get('cotacao_id'),
        'fornecedor_id': cotacao['dados'].get('fornecedor', {}).get('id')
    })


# =============================================================================
# ENDPOINT DE DIAGNÓSTICO
# =============================================================================

@app.route('/api/diagnostico', methods=['GET'])
def api_diagnostico():
    """
    Endpoint de diagnóstico para verificar estado do servidor.
    Útil para debug e monitoramento.
    
    Retorna:
    - Número de cotações ativas
    - Número de respostas
    - Lista de tokens (truncados)
    - Status da persistência
    """
    try:
        # Lista cotações ativas (com tokens truncados por segurança)
        cotacoes_info = []
        for token, cotacao in cotacoes_ativas.items():
            cotacoes_info.append({
                'token_preview': token[:20] + '...',
                'fornecedor': cotacao['dados'].get('fornecedor', {}).get('nome', 'N/A'),
                'cotacao_id': cotacao['dados'].get('cotacao_id'),
                'status': cotacao.get('status', 'ativa'),
                'created_at': cotacao['created_at'].isoformat() if isinstance(cotacao['created_at'], datetime) else str(cotacao['created_at']),
                'expires_at': cotacao['expires_at'].isoformat() if isinstance(cotacao['expires_at'], datetime) else str(cotacao['expires_at']),
                'respondida': token in respostas_enviadas,
                'sincronizada': token in respostas_sincronizadas
            })
        
        # Verifica arquivo de persistência
        persistencia_ok = os.path.exists(STORAGE_FILE)
        persistencia_tamanho = os.path.getsize(STORAGE_FILE) if persistencia_ok else 0
        
        # Lê última data de salvamento do arquivo
        ultima_gravacao = None
        if persistencia_ok:
            try:
                with open(STORAGE_FILE, 'r', encoding='utf-8') as f:
                    dados_arquivo = json.load(f)
                    ultima_gravacao = dados_arquivo.get('salvo_em', 'desconhecido')
            except:
                pass
        
        return jsonify({
            'success': True,
            'status': 'online',
            'timestamp': datetime.now().isoformat(),
            'estatisticas': {
                'cotacoes_ativas': len(cotacoes_ativas),
                'respostas_enviadas': len(respostas_enviadas),
                'respostas_sincronizadas': len(respostas_sincronizadas),
                'respostas_pendentes': len(respostas_enviadas) - len(respostas_sincronizadas.intersection(respostas_enviadas.keys()))
            },
            'persistencia': {
                'arquivo': STORAGE_FILE,
                'existe': persistencia_ok,
                'tamanho_bytes': persistencia_tamanho,
                'ultima_gravacao': ultima_gravacao
            },
            'cotacoes': cotacoes_info,
            'ambiente': {
                'token_expiration_hours': TOKEN_EXPIRATION_HOURS,
                'base_url': os.environ.get('BASE_URL', 'não configurado')
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/health', methods=['GET'])
def api_health():
    """
    Endpoint simples de health check.
    Usado pelo Render para verificar se a aplicação está rodando.
    """
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'cotacoes_ativas': len(cotacoes_ativas),
        'respostas_pendentes': len(respostas_enviadas)
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
