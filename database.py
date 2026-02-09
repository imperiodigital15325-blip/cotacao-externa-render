# =============================================================================
# MÓDULO DE BANCO DE DADOS LOCAL - COTAÇÕES E ORÇAMENTOS
# =============================================================================
# Este módulo gerencia o banco de dados SQLite local para:
# - Cotações e orçamentos
# - Itens de cotação vinculados às solicitações
# - Fornecedores cotados
# - Respostas de fornecedores
# - Anotações e cores das solicitações
# =============================================================================

import sqlite3
import os
from datetime import datetime
import uuid
import json

# Caminho do banco de dados
DB_PATH = os.path.join(os.path.dirname(__file__), 'cotacoes.db')

def get_db_connection():
    """Retorna uma conexão com o banco de dados SQLite"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Permite acessar colunas por nome
    return conn

def init_database():
    """Inicializa o banco de dados com todas as tabelas necessárias"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # ==========================================================================
    # TABELA: COTAÇÕES (Cabeçalho)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE NOT NULL,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            comprador_responsavel TEXT,
            data_validade DATE,
            status TEXT DEFAULT 'Aberta',
            observacoes TEXT,
            criado_por TEXT,
            atualizado_em TIMESTAMP
        )
    ''')
    
    # ==========================================================================
    # TABELA: ITENS DA COTAÇÃO
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacao_itens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cotacao_id INTEGER NOT NULL,
            numero_sc TEXT NOT NULL,
            item_sc TEXT,
            cod_produto TEXT,
            descricao_produto TEXT,
            quantidade REAL,
            unidade TEXT,
            data_necessidade DATE,
            observacao TEXT,
            FOREIGN KEY (cotacao_id) REFERENCES cotacoes(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: FORNECEDORES COTADOS
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacao_fornecedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cotacao_id INTEGER NOT NULL,
            cod_fornecedor TEXT,
            nome_fornecedor TEXT NOT NULL,
            email_fornecedor TEXT,
            telefone_fornecedor TEXT,
            token_acesso TEXT UNIQUE,
            data_envio TIMESTAMP,
            data_resposta TIMESTAMP,
            status TEXT DEFAULT 'Pendente',
            FOREIGN KEY (cotacao_id) REFERENCES cotacoes(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: RESPOSTAS DOS FORNECEDORES
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacao_respostas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cotacao_id INTEGER NOT NULL,
            fornecedor_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            preco_unitario REAL,
            prazo_entrega INTEGER,
            condicao_pagamento TEXT,
            frete_total REAL DEFAULT 0,
            observacao TEXT,
            arquivo_anexo TEXT,
            data_resposta TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cotacao_id) REFERENCES cotacoes(id) ON DELETE CASCADE,
            FOREIGN KEY (fornecedor_id) REFERENCES cotacao_fornecedores(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES cotacao_itens(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: ANOTAÇÕES DAS SOLICITAÇÕES (Cores e Observações)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS solicitacao_anotacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_sc TEXT NOT NULL,
            item_sc TEXT,
            cor TEXT,
            observacao TEXT,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_por TEXT,
            UNIQUE(numero_sc, item_sc)
        )
    ''')
    
    # ==========================================================================
    # TABELA: HISTÓRICO DE COTAÇÕES (Auditoria)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacao_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cotacao_id INTEGER NOT NULL,
            acao TEXT NOT NULL,
            descricao TEXT,
            usuario TEXT,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dados_json TEXT,
            FOREIGN KEY (cotacao_id) REFERENCES cotacoes(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: ATRIBUIÇÕES MANUAIS DE COMPRADORES (Nova funcionalidade)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS solicitacao_atribuicoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_sc TEXT NOT NULL,
            item_sc TEXT NOT NULL,
            cod_comprador TEXT NOT NULL,
            nome_comprador TEXT NOT NULL,
            atribuido_por TEXT,
            data_atribuicao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            observacao TEXT,
            UNIQUE(numero_sc, item_sc)
        )
    ''')
    
    # Criar índices para melhor performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_cotacao_status ON cotacoes(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_cotacao_itens_sc ON cotacao_itens(numero_sc)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fornecedor_token ON cotacao_fornecedores(token_acesso)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_anotacao_sc ON solicitacao_anotacoes(numero_sc)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_atribuicao_sc ON solicitacao_atribuicoes(numero_sc, item_sc)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_atribuicao_comprador ON solicitacao_atribuicoes(cod_comprador)')
    
    # ==========================================================================
    # TABELA: RODADAS DE NEGOCIAÇÃO
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacao_rodadas_negociacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cotacao_id INTEGER NOT NULL,
            fornecedor_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            rodada INTEGER DEFAULT 2,
            preco_unitario_original REAL,
            preco_unitario_negociado REAL,
            prazo_entrega_original INTEGER,
            prazo_entrega_negociado INTEGER,
            observacao TEXT,
            data_negociacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT,
            FOREIGN KEY (cotacao_id) REFERENCES cotacoes(id) ON DELETE CASCADE,
            FOREIGN KEY (fornecedor_id) REFERENCES cotacao_fornecedores(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES cotacao_itens(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: ANEXOS DOS FORNECEDORES (para persistência confiável)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacao_anexos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cotacao_id INTEGER NOT NULL,
            fornecedor_id INTEGER NOT NULL,
            nome_original TEXT NOT NULL,
            nome_arquivo TEXT NOT NULL,
            caminho_arquivo TEXT NOT NULL,
            tipo_arquivo TEXT,
            tamanho_bytes INTEGER,
            mime_type TEXT,
            data_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            usuario_upload TEXT,
            ativo INTEGER DEFAULT 1,
            FOREIGN KEY (cotacao_id) REFERENCES cotacoes(id) ON DELETE CASCADE,
            FOREIGN KEY (fornecedor_id) REFERENCES cotacao_fornecedores(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: COTAÇÕES EXTERNAS VIA JSON (Envio/Importação)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacao_json_envios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cotacao_id INTEGER NOT NULL,
            fornecedor_id INTEGER NOT NULL,
            token_envio TEXT UNIQUE NOT NULL,
            hash_validacao TEXT NOT NULL,
            data_geracao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_importacao TIMESTAMP,
            status TEXT DEFAULT 'Gerado',
            arquivo_json_gerado TEXT,
            arquivo_json_resposta TEXT,
            usuario_geracao TEXT,
            usuario_importacao TEXT,
            observacao TEXT,
            FOREIGN KEY (cotacao_id) REFERENCES cotacoes(id) ON DELETE CASCADE,
            FOREIGN KEY (fornecedor_id) REFERENCES cotacao_fornecedores(id) ON DELETE CASCADE
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_json_envio_token ON cotacao_json_envios(token_envio)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_json_envio_fornecedor ON cotacao_json_envios(fornecedor_id)')
    
    # ==========================================================================
    # TABELA: AVALIAÇÃO DE FORNECEDORES - ISO (PQ021)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS avaliacao_fornecedores_iso (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cod_fornecedor TEXT NOT NULL,
            nome_fornecedor TEXT NOT NULL,
            email_fornecedor TEXT,
            data_ultima_avaliacao DATE,
            data_vencimento DATE,
            possui_iso TEXT DEFAULT 'Nao',
            nota REAL,
            observacao TEXT,
            criado_por TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_por TEXT,
            atualizado_em TIMESTAMP,
            UNIQUE(cod_fornecedor)
        )
    ''')
    
    # ==========================================================================
    # TABELA: DOCUMENTOS DA AVALIAÇÃO ISO (PDFs, Word, etc.)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS avaliacao_iso_documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            avaliacao_id INTEGER NOT NULL,
            tipo_documento TEXT NOT NULL,
            nome_documento TEXT,
            nome_original TEXT NOT NULL,
            nome_arquivo TEXT NOT NULL,
            caminho_arquivo TEXT NOT NULL,
            extensao TEXT,
            tamanho_bytes INTEGER,
            data_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            usuario_upload TEXT,
            ativo INTEGER DEFAULT 1,
            FOREIGN KEY (avaliacao_id) REFERENCES avaliacao_fornecedores_iso(id) ON DELETE CASCADE
        )
    ''')
    
    # Migração: adicionar novas colunas se não existirem
    try:
        cursor.execute("ALTER TABLE avaliacao_iso_documentos ADD COLUMN nome_documento TEXT")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE avaliacao_iso_documentos ADD COLUMN extensao TEXT")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE avaliacao_iso_documentos ADD COLUMN tamanho_bytes INTEGER")
    except:
        pass
    
    # ==========================================================================
    # TABELA: HISTÓRICO DE ENVIO DE E-MAILS ISO
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS avaliacao_iso_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            avaliacao_id INTEGER NOT NULL,
            email_destinatario TEXT NOT NULL,
            assunto TEXT,
            mensagem TEXT,
            data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            enviado_por TEXT,
            status TEXT DEFAULT 'Enviado',
            erro_msg TEXT,
            FOREIGN KEY (avaliacao_id) REFERENCES avaliacao_fornecedores_iso(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: COTAÇÕES EXTERNAS (RENDER) - PERSISTÊNCIA PARA LINKS PÚBLICOS
    # ==========================================================================
    # Esta tabela armazena as cotações externas criadas via API do Render.
    # Substitui o armazenamento em memória para garantir persistência após deploys.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotacoes_externas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            dados_json TEXT NOT NULL,
            status TEXT DEFAULT 'aberta',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expira_em TIMESTAMP,
            respondido_em TIMESTAMP,
            resposta_json TEXT,
            ip_criacao TEXT,
            ip_resposta TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_cotacoes_externas_token ON cotacoes_externas(token)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_cotacoes_externas_status ON cotacoes_externas(status)')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_avaliacao_iso_fornecedor ON avaliacao_fornecedores_iso(cod_fornecedor)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_avaliacao_iso_vencimento ON avaliacao_fornecedores_iso(data_vencimento)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_avaliacao_iso_docs ON avaliacao_iso_documentos(avaliacao_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_avaliacao_iso_emails ON avaliacao_iso_emails(avaliacao_id)')
    
    # Migration: Adicionar coluna desconto_percentual nas rodadas de negociação
    try:
        cursor.execute("ALTER TABLE cotacao_rodadas_negociacao ADD COLUMN desconto_percentual REAL DEFAULT 0")
    except:
        pass  # Coluna já existe
    
    # Migration: Adicionar coluna frete_negociado nas rodadas de negociação
    try:
        cursor.execute("ALTER TABLE cotacao_rodadas_negociacao ADD COLUMN frete_negociado REAL DEFAULT 0")
    except:
        pass  # Coluna já existe
    
    # Migration: Adicionar coluna arquivo_anexo_r2 nas rodadas de negociação (anexo da segunda rodada)
    try:
        cursor.execute("ALTER TABLE cotacao_rodadas_negociacao ADD COLUMN arquivo_anexo_r2 TEXT")
    except:
        pass  # Coluna já existe
    
    # Migration: Adicionar coluna frete_total se não existir
    try:
        cursor.execute("ALTER TABLE cotacao_respostas ADD COLUMN frete_total REAL DEFAULT 0")
    except:
        pass  # Coluna já existe
    
    # Migration: Adicionar coluna informacao_fornecedor na tabela cotacoes
    try:
        cursor.execute("ALTER TABLE cotacoes ADD COLUMN informacao_fornecedor TEXT")
    except:
        pass  # Coluna já existe
    
    # Migration: Adicionar campos a nível de fornecedor (não por item)
    try:
        cursor.execute("ALTER TABLE cotacao_fornecedores ADD COLUMN frete_total REAL DEFAULT 0")
    except:
        pass  # Coluna já existe
    
    try:
        cursor.execute("ALTER TABLE cotacao_fornecedores ADD COLUMN condicao_pagamento TEXT")
    except:
        pass  # Coluna já existe
    
    try:
        cursor.execute("ALTER TABLE cotacao_fornecedores ADD COLUMN observacao_geral TEXT")
    except:
        pass  # Coluna já existe
    
    # ==========================================================================
    # MIGRATION: Campos para Orçamento Manual (sem Solicitação de Compra)
    # ==========================================================================
    # Adiciona campo tipo_origem para distinguir Manual vs Solicitação
    try:
        cursor.execute("ALTER TABLE cotacoes ADD COLUMN tipo_origem TEXT DEFAULT 'Solicitacao'")
    except:
        pass  # Coluna já existe
    
    # Adiciona campo preco_referencia nos itens (último preço conhecido)
    try:
        cursor.execute("ALTER TABLE cotacao_itens ADD COLUMN preco_referencia REAL")
    except:
        pass  # Coluna já existe
    
    # Adiciona campo fornecedor_referencia nos itens (último fornecedor)
    try:
        cursor.execute("ALTER TABLE cotacao_itens ADD COLUMN fornecedor_referencia TEXT")
    except:
        pass  # Coluna já existe
    
    # ==========================================================================
    # MIGRATION: Campos para Cotação Externa Online (Render)
    # ==========================================================================
    try:
        cursor.execute("ALTER TABLE cotacao_fornecedores ADD COLUMN token_externo TEXT")
    except:
        pass  # Coluna já existe
    
    try:
        cursor.execute("ALTER TABLE cotacao_fornecedores ADD COLUMN link_externo TEXT")
    except:
        pass  # Coluna já existe
    
    try:
        cursor.execute("ALTER TABLE cotacao_fornecedores ADD COLUMN data_envio_externo TIMESTAMP")
    except:
        pass  # Coluna já existe
    
    try:
        cursor.execute("ALTER TABLE cotacao_fornecedores ADD COLUMN arquivo_anexo TEXT")
    except:
        pass  # Coluna já existe
    
    # ==========================================================================
    # MIGRATION: Campos para sincronização de cotações externas (Polling)
    # ==========================================================================
    try:
        cursor.execute("ALTER TABLE cotacoes_externas ADD COLUMN cotacao_id INTEGER")
    except:
        pass  # Coluna já existe
    
    try:
        cursor.execute("ALTER TABLE cotacoes_externas ADD COLUMN fornecedor_id INTEGER")
    except:
        pass  # Coluna já existe
    
    try:
        cursor.execute("ALTER TABLE cotacoes_externas ADD COLUMN fornecedor_nome TEXT")
    except:
        pass  # Coluna já existe
    
    try:
        cursor.execute("ALTER TABLE cotacoes_externas ADD COLUMN sincronizada INTEGER DEFAULT 0")
    except:
        pass  # Coluna já existe
    
    # Índice para busca rápida de cotações não sincronizadas
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_cotacoes_externas_sync ON cotacoes_externas(sincronizada, status)')

    # ==========================================================================
    # TABELA: PEDIDOS DE COMPRA
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pedidos_compra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_pedido TEXT UNIQUE NOT NULL,
            data_pedido DATE NOT NULL,
            cod_fornecedor TEXT,
            nome_fornecedor TEXT,
            condicao_pagamento TEXT,
            contato TEXT,
            observacoes TEXT,
            valor_total REAL DEFAULT 0,
            status TEXT DEFAULT 'Gerado',
            enviado_totvs INTEGER DEFAULT 0,
            data_envio_totvs TIMESTAMP,
            resposta_totvs TEXT,
            criado_por TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP
        )
    ''')
    
    # ==========================================================================
    # TABELA: ITENS DO PEDIDO DE COMPRA
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pedido_itens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            numero_sc TEXT NOT NULL,
            item_sc TEXT,
            cod_produto TEXT,
            descricao_produto TEXT,
            quantidade REAL,
            unidade TEXT,
            valor_unitario REAL DEFAULT 0,
            ipi REAL DEFAULT 0,
            valor_total REAL DEFAULT 0,
            data_necessidade DATE,
            observacao TEXT,
            FOREIGN KEY (pedido_id) REFERENCES pedidos_compra(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: HISTÓRICO/LOG DOS PEDIDOS (Auditoria)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pedido_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            acao TEXT NOT NULL,
            descricao TEXT,
            usuario TEXT,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dados_json TEXT,
            FOREIGN KEY (pedido_id) REFERENCES pedidos_compra(id) ON DELETE CASCADE
        )
    ''')
    
    # ==========================================================================
    # TABELA: HISTÓRICO DE ENVIOS DE E-MAIL (Controle 24h)
    # ==========================================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_envios_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            identificador TEXT NOT NULL,
            email_destinatario TEXT NOT NULL,
            assunto TEXT,
            corpo TEXT,
            data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            enviado_por TEXT,
            sucesso INTEGER DEFAULT 1
        )
    ''')
    
    # Índices para performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pedido_numero ON pedidos_compra(numero_pedido)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pedido_status ON pedidos_compra(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pedido_fornecedor ON pedidos_compra(cod_fornecedor)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pedido_itens_pedido ON pedido_itens(pedido_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pedido_itens_sc ON pedido_itens(numero_sc, item_sc)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_email_envio_tipo_id ON email_envios_historico(tipo, identificador)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_email_envio_data ON email_envios_historico(data_envio)')

    conn.commit()
    conn.close()
    print("[DB] Banco de dados inicializado com sucesso!")

# =============================================================================
# FUNÇÕES: COTAÇÕES
# =============================================================================

def gerar_numero_cotacao():
    """Gera o próximo número sequencial de cotação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Busca o maior número de cotação existente
    cursor.execute("SELECT MAX(CAST(SUBSTR(codigo, INSTR(codigo, ' - Nº') + 5) AS INTEGER)) FROM cotacoes WHERE codigo LIKE '%- Nº%'")
    result = cursor.fetchone()[0]
    
    if result is None:
        # Se não encontrou no novo formato, tenta contar cotações antigas
        cursor.execute("SELECT COUNT(*) FROM cotacoes")
        result = cursor.fetchone()[0]
    
    conn.close()
    return (result or 0) + 1

def criar_cotacao(comprador, observacoes='', usuario='Admin', nome_customizado=None, data_criacao=None, tipo_origem='Solicitacao'):
    """
    Cria uma nova cotação e retorna o ID.
    
    Args:
        comprador: Nome do comprador responsável
        observacoes: Observações da cotação
        usuario: Usuário que criou
        nome_customizado: Nome customizado (ex: "Cotação de Gás"). Se None, usa "Cotação" ou "Orçamento Manual"
        data_criacao: Data no formato DD/MM/YYYY. Se None, usa data atual
        tipo_origem: 'Solicitacao' (padrão) ou 'Manual'
    
    Formato do código: "Nome - DD/MM/YYYY - Nº123"
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Gerar número sequencial
    numero = gerar_numero_cotacao()
    
    # Data de criação (formato DD/MM/YYYY)
    if data_criacao:
        data_formatada = data_criacao  # Já vem no formato DD/MM/YYYY
    else:
        data_formatada = datetime.now().strftime('%d/%m/%Y')
    
    # Nome da cotação
    if nome_customizado:
        nome = nome_customizado
    elif tipo_origem == 'Manual':
        nome = 'Orçamento Manual'
    else:
        nome = 'Cotação'
    
    # Código final: "Cotação - 23/01/2026 - Nº127"
    codigo = f"{nome} - {data_formatada} - Nº{numero}"
    
    cursor.execute('''
        INSERT INTO cotacoes (codigo, comprador_responsavel, observacoes, criado_por, atualizado_em, tipo_origem)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (codigo, comprador, observacoes, usuario, datetime.now(), tipo_origem))
    
    cotacao_id = cursor.lastrowid
    
    # Registrar no histórico
    descricao_hist = 'Cotação criada' if tipo_origem == 'Solicitacao' else 'Orçamento Manual criado'
    cursor.execute('''
        INSERT INTO cotacao_historico (cotacao_id, acao, descricao, usuario)
        VALUES (?, 'CRIACAO', ?, ?)
    ''', (cotacao_id, descricao_hist, usuario))
    
    conn.commit()
    conn.close()
    
    return cotacao_id, codigo

def adicionar_itens_cotacao(cotacao_id, itens):
    """
    Adiciona itens à cotação.
    itens = lista de dicionários com: numero_sc, item_sc, cod_produto, descricao, quantidade, unidade, data_necessidade
    
    Para orçamentos manuais, também pode conter:
        - preco_referencia: último preço conhecido do produto
        - fornecedor_referencia: nome do último fornecedor
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for item in itens:
        cursor.execute('''
            INSERT INTO cotacao_itens 
            (cotacao_id, numero_sc, item_sc, cod_produto, descricao_produto, quantidade, unidade, data_necessidade, preco_referencia, fornecedor_referencia)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cotacao_id,
            item.get('numero_sc', 'MANUAL'),  # Para orçamentos manuais, usa 'MANUAL'
            item.get('item_sc', '001'),
            item.get('cod_produto', ''),
            item.get('descricao', ''),
            item.get('quantidade', 0),
            item.get('unidade', 'UN'),
            item.get('data_necessidade'),
            item.get('preco_referencia'),
            item.get('fornecedor_referencia')
        ))
    
    conn.commit()
    conn.close()

def listar_cotacoes(status=None, comprador=None, busca=None, tipo_origem=None, limit=100):
    """
    Lista cotações com filtros opcionais.
    
    Args:
        status: Filtrar por status (Aberta, Respondida, Encerrada, Cancelada)
        comprador: Filtrar por comprador responsável
        busca: Busca textual no código ou comprador
        tipo_origem: Filtrar por tipo ('Solicitacao', 'Manual' ou None para todos)
        limit: Limite de registros
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = '''
        SELECT c.*, 
               COALESCE(c.tipo_origem, 'Solicitacao') as tipo_origem,
               (SELECT COUNT(*) FROM cotacao_itens WHERE cotacao_id = c.id) as total_itens,
               (SELECT COUNT(*) FROM cotacao_fornecedores WHERE cotacao_id = c.id) as total_fornecedores,
               (SELECT COUNT(*) FROM cotacao_fornecedores WHERE cotacao_id = c.id AND status = 'Respondido') as fornecedores_responderam
        FROM cotacoes c
        WHERE 1=1
    '''
    params = []
    
    if status:
        query += ' AND c.status = ?'
        params.append(status)
    
    if comprador:
        query += ' AND c.comprador_responsavel = ?'
        params.append(comprador)
    
    if tipo_origem:
        query += ' AND COALESCE(c.tipo_origem, \'Solicitacao\') = ?'
        params.append(tipo_origem)
    
    if busca:
        query += ' AND (c.codigo LIKE ? OR c.comprador_responsavel LIKE ?)'
        params.append(f'%{busca}%')
        params.append(f'%{busca}%')
    
    query += ' ORDER BY c.data_criacao DESC LIMIT ?'
    params.append(limit)
    
    cursor.execute(query, params)
    cotacoes = cursor.fetchall()
    conn.close()
    
    return [dict(c) for c in cotacoes]

def obter_cotacao(cotacao_id):
    """Obtém detalhes completos de uma cotação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Cabeçalho
    cursor.execute('SELECT * FROM cotacoes WHERE id = ?', (cotacao_id,))
    cotacao = cursor.fetchone()
    
    if not cotacao:
        conn.close()
        return None
    
    cotacao = dict(cotacao)
    
    # Itens
    cursor.execute('SELECT * FROM cotacao_itens WHERE cotacao_id = ?', (cotacao_id,))
    cotacao['itens'] = [dict(i) for i in cursor.fetchall()]
    cotacao['total_itens'] = len(cotacao['itens'])
    
    # Fornecedores (com info de anexo e novos campos)
    cursor.execute('''
        SELECT f.*, 
               (SELECT COUNT(*) FROM cotacao_respostas r 
                WHERE r.fornecedor_id = f.id AND r.arquivo_anexo IS NOT NULL AND r.arquivo_anexo != '') as tem_anexo
        FROM cotacao_fornecedores f 
        WHERE f.cotacao_id = ?
    ''', (cotacao_id,))
    cotacao['fornecedores'] = [dict(f) for f in cursor.fetchall()]
    cotacao['total_fornecedores'] = len(cotacao['fornecedores'])
    
    # Criar mapa de frete/condição por fornecedor_id
    forn_map = {f['id']: f for f in cotacao['fornecedores']}
    
    # Respostas (agora com frete/condição do fornecedor, não do item)
    cursor.execute('''
        SELECT r.*, f.nome_fornecedor, i.descricao_produto,
               f.frete_total as frete_fornecedor, 
               f.condicao_pagamento as condicao_fornecedor, 
               f.observacao_geral as obs_fornecedor
        FROM cotacao_respostas r
        JOIN cotacao_fornecedores f ON r.fornecedor_id = f.id
        JOIN cotacao_itens i ON r.item_id = i.id
        WHERE r.cotacao_id = ?
    ''', (cotacao_id,))
    cotacao['respostas'] = [dict(r) for r in cursor.fetchall()]
    
    # Rodadas de Negociação
    cursor.execute('''
        SELECT rn.*, f.nome_fornecedor, i.cod_produto, i.descricao_produto, i.quantidade, i.unidade
        FROM cotacao_rodadas_negociacao rn
        JOIN cotacao_fornecedores f ON rn.fornecedor_id = f.id
        JOIN cotacao_itens i ON rn.item_id = i.id
        WHERE rn.cotacao_id = ?
        ORDER BY rn.rodada, rn.fornecedor_id, rn.item_id
    ''', (cotacao_id,))
    cotacao['rodadas_negociacao'] = [dict(r) for r in cursor.fetchall()]
    
    conn.close()
    
    # Adicionar dados agrupados por fornecedor (para nova interface)
    cotacao['rodadas_agrupadas'] = obter_rodadas_agrupadas_por_fornecedor(cotacao_id)
    
    return cotacao

def atualizar_status_cotacao(cotacao_id, novo_status, usuario='Admin'):
    """Atualiza o status de uma cotação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE cotacoes SET status = ?, atualizado_em = ? WHERE id = ?
    ''', (novo_status, datetime.now(), cotacao_id))
    
    # Histórico
    cursor.execute('''
        INSERT INTO cotacao_historico (cotacao_id, acao, descricao, usuario)
        VALUES (?, 'STATUS', ?, ?)
    ''', (cotacao_id, f'Status alterado para: {novo_status}', usuario))
    
    conn.commit()
    conn.close()


def atualizar_cotacao(cotacao_id, codigo=None, comprador=None, observacoes=None, informacao_fornecedor=None, usuario='Admin'):
    """Atualiza dados de uma cotação (código/nome, comprador, observações, informação ao fornecedor)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Montar query dinâmica
    campos = []
    valores = []
    
    if codigo is not None:
        campos.append('codigo = ?')
        valores.append(codigo)
    
    if comprador is not None:
        campos.append('comprador_responsavel = ?')
        valores.append(comprador)
    
    if observacoes is not None:
        campos.append('observacoes = ?')
        valores.append(observacoes)
    
    if informacao_fornecedor is not None:
        campos.append('informacao_fornecedor = ?')
        valores.append(informacao_fornecedor)
    
    if campos:
        campos.append('atualizado_em = ?')
        valores.append(datetime.now())
        valores.append(cotacao_id)
        
        query = f"UPDATE cotacoes SET {', '.join(campos)} WHERE id = ?"
        cursor.execute(query, valores)
        
        # Histórico
        cursor.execute('''
            INSERT INTO cotacao_historico (cotacao_id, acao, descricao, usuario)
            VALUES (?, 'EDICAO', 'Cotação editada', ?)
        ''', (cotacao_id, usuario))
        
        conn.commit()
    
    conn.close()


def excluir_cotacao(cotacao_id, usuario='Admin'):
    """
    Exclui uma cotação e todos os seus dados relacionados.
    IMPORTANTE: Só chamar após verificar que a cotação pode ser excluída.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Excluir respostas
    cursor.execute('DELETE FROM cotacao_respostas WHERE cotacao_id = ?', (cotacao_id,))
    
    # Excluir fornecedores
    cursor.execute('DELETE FROM cotacao_fornecedores WHERE cotacao_id = ?', (cotacao_id,))
    
    # Excluir itens
    cursor.execute('DELETE FROM cotacao_itens WHERE cotacao_id = ?', (cotacao_id,))
    
    # Excluir histórico
    cursor.execute('DELETE FROM cotacao_historico WHERE cotacao_id = ?', (cotacao_id,))
    
    # Excluir cotação
    cursor.execute('DELETE FROM cotacoes WHERE id = ?', (cotacao_id,))
    
    conn.commit()
    conn.close()


# =============================================================================
# FUNÇÕES: FORNECEDORES DA COTAÇÃO
# =============================================================================

def adicionar_fornecedor_cotacao(cotacao_id, nome_fornecedor, email='', telefone='', cod_fornecedor=''):
    """Adiciona um fornecedor à cotação e gera token de acesso"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    token = str(uuid.uuid4())
    
    cursor.execute('''
        INSERT INTO cotacao_fornecedores 
        (cotacao_id, cod_fornecedor, nome_fornecedor, email_fornecedor, telefone_fornecedor, token_acesso)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (cotacao_id, cod_fornecedor, nome_fornecedor, email, telefone, token))
    
    fornecedor_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return fornecedor_id, token


def editar_fornecedor_cotacao(fornecedor_id, nome=None, email=None, telefone=None, frete_total=None, condicao_pagamento=None, observacao_geral=None):
    """Atualiza os dados de um fornecedor de cotação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    updates = []
    params = []
    
    if nome is not None:
        updates.append('nome_fornecedor = ?')
        params.append(nome)
    if email is not None:
        updates.append('email_fornecedor = ?')
        params.append(email)
    if telefone is not None:
        updates.append('telefone_fornecedor = ?')
        params.append(telefone)
    if frete_total is not None:
        updates.append('frete_total = ?')
        params.append(frete_total)
    if condicao_pagamento is not None:
        updates.append('condicao_pagamento = ?')
        params.append(condicao_pagamento)
    if observacao_geral is not None:
        updates.append('observacao_geral = ?')
        params.append(observacao_geral)
    
    if updates:
        params.append(fornecedor_id)
        query = f'UPDATE cotacao_fornecedores SET {", ".join(updates)} WHERE id = ?'
        cursor.execute(query, params)
    
    conn.commit()
    conn.close()


def atualizar_status_fornecedor(fornecedor_id, status):
    """Atualiza o status de um fornecedor de cotação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE cotacao_fornecedores 
        SET status = ?, data_resposta = ?
        WHERE id = ?
    ''', (status, datetime.now(), fornecedor_id))
    
    conn.commit()
    conn.close()


def excluir_fornecedor_cotacao(fornecedor_id):
    """
    Exclui um fornecedor da cotação.
    Remove o vínculo e invalida o token.
    Também remove as respostas desse fornecedor.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Excluir respostas deste fornecedor
    cursor.execute('DELETE FROM cotacao_respostas WHERE fornecedor_id = ?', (fornecedor_id,))
    
    # Excluir o fornecedor da cotação
    cursor.execute('DELETE FROM cotacao_fornecedores WHERE id = ?', (fornecedor_id,))
    
    conn.commit()
    conn.close()
    
    return True


def obter_cotacao_por_token(token):
    """Obtém cotação pelo token do fornecedor (para acesso externo)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT f.*, c.codigo, c.status as cotacao_status, c.observacoes as cotacao_obs,
               c.informacao_fornecedor
        FROM cotacao_fornecedores f
        JOIN cotacoes c ON f.cotacao_id = c.id
        WHERE f.token_acesso = ?
    ''', (token,))
    
    fornecedor = cursor.fetchone()
    
    if not fornecedor:
        conn.close()
        return None
    
    fornecedor = dict(fornecedor)
    
    # Itens da cotação
    cursor.execute('SELECT * FROM cotacao_itens WHERE cotacao_id = ?', (fornecedor['cotacao_id'],))
    itens = [dict(i) for i in cursor.fetchall()]
    
    # Buscar respostas já enviadas por este fornecedor
    cursor.execute('''
        SELECT * FROM cotacao_respostas 
        WHERE cotacao_id = ? AND fornecedor_id = ?
    ''', (fornecedor['cotacao_id'], fornecedor['id']))
    respostas = {r['item_id']: dict(r) for r in cursor.fetchall()}
    
    # Anexar respostas aos itens
    for item in itens:
        if item['id'] in respostas:
            resp = respostas[item['id']]
            item['resposta'] = resp
            item['preco_resposta'] = resp.get('preco_unitario', 0)
            item['prazo_resposta'] = resp.get('prazo_entrega', 0)
            item['condicao_resposta'] = resp.get('condicao_pagamento', '')
            item['frete_resposta'] = resp.get('frete_total', 0)
            item['obs_resposta'] = resp.get('observacao', '')
            item['anexo_resposta'] = resp.get('arquivo_anexo', '')
        else:
            item['resposta'] = None
            item['preco_resposta'] = None
            item['prazo_resposta'] = None
            item['condicao_resposta'] = ''
            item['frete_resposta'] = 0
            item['obs_resposta'] = ''
            item['anexo_resposta'] = ''
    
    fornecedor['itens'] = itens
    fornecedor['ja_respondeu'] = fornecedor.get('status') == 'Respondido'
    
    conn.close()
    return fornecedor

def registrar_resposta_fornecedor(cotacao_id, fornecedor_id, item_id, preco, prazo, condicao, observacao='', anexo=None, frete=0):
    """
    Registra a resposta de um fornecedor para um item.
    IMPORTANTE: O campo anexo só é atualizado se um valor não-None for passado.
    Isso preserva anexos existentes durante edição de outros campos.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verifica se já existe resposta
    cursor.execute('''
        SELECT id, arquivo_anexo FROM cotacao_respostas 
        WHERE cotacao_id = ? AND fornecedor_id = ? AND item_id = ?
    ''', (cotacao_id, fornecedor_id, item_id))
    
    existente = cursor.fetchone()
    
    if existente:
        # CORREÇÃO: Preservar anexo existente se não houver novo
        anexo_atual = existente['arquivo_anexo'] if existente['arquivo_anexo'] else ''
        anexo_final = anexo if anexo is not None else anexo_atual
        
        cursor.execute('''
            UPDATE cotacao_respostas 
            SET preco_unitario = ?, prazo_entrega = ?, condicao_pagamento = ?, 
                frete_total = ?, observacao = ?, arquivo_anexo = ?, data_resposta = ?
            WHERE id = ?
        ''', (preco, prazo, condicao, frete, observacao, anexo_final, datetime.now(), existente['id']))
    else:
        cursor.execute('''
            INSERT INTO cotacao_respostas 
            (cotacao_id, fornecedor_id, item_id, preco_unitario, prazo_entrega, condicao_pagamento, frete_total, observacao, arquivo_anexo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (cotacao_id, fornecedor_id, item_id, preco, prazo, condicao, frete, observacao, anexo or ''))
    
    # Atualiza status do fornecedor
    cursor.execute('''
        UPDATE cotacao_fornecedores 
        SET status = 'Respondido', data_resposta = ?
        WHERE id = ?
    ''', (datetime.now(), fornecedor_id))
    
    conn.commit()
    conn.close()


def atualizar_resposta_fornecedor(resposta_id, preco=None, prazo=None, condicao=None, frete=None, observacao=None, allow_null=False):
    """
    Atualiza uma resposta de fornecedor existente (para edição inline pelo comprador).
    
    CORREÇÃO V2: Suporte a valores NULL/vazios para remover cotação do comparativo.
    - Se allow_null=True, valores None são persistidos como NULL no banco.
    - Isso permite que o usuário "limpe" preço/prazo de um fornecedor.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Se allow_null está ativado, sempre atualizamos todos os campos
    # Isso permite setar valores como NULL explicitamente
    if allow_null:
        cursor.execute('''
            UPDATE cotacao_respostas 
            SET preco_unitario = ?, prazo_entrega = ?, condicao_pagamento = ?, 
                frete_total = ?, observacao = ?
            WHERE id = ?
        ''', (preco, prazo, condicao or '', frete or 0, observacao or '', resposta_id))
        conn.commit()
        conn.close()
        return True
    
    # Comportamento legado: só atualiza campos não-None
    campos = []
    valores = []
    
    if preco is not None:
        campos.append('preco_unitario = ?')
        valores.append(preco)
    
    if prazo is not None:
        campos.append('prazo_entrega = ?')
        valores.append(prazo)
    
    if condicao is not None:
        campos.append('condicao_pagamento = ?')
        valores.append(condicao)
    
    if frete is not None:
        campos.append('frete_total = ?')
        valores.append(frete)
    
    if observacao is not None:
        campos.append('observacao = ?')
        valores.append(observacao)
    
    if campos:
        valores.append(resposta_id)
        query = f"UPDATE cotacao_respostas SET {', '.join(campos)} WHERE id = ?"
        cursor.execute(query, valores)
        conn.commit()
    
    conn.close()
    return True


def excluir_resposta_fornecedor(resposta_id):
    """
    Exclui uma resposta de fornecedor do banco de dados.
    Use para remover completamente uma cotação de um fornecedor para um item específico.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM cotacao_respostas WHERE id = ?', (resposta_id,))
    deleted = cursor.rowcount > 0
    
    conn.commit()
    conn.close()
    return deleted


def obter_resposta_por_id(resposta_id):
    """Obtém uma resposta específica pelo ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT r.*, f.nome_fornecedor, i.descricao_produto, i.quantidade, i.unidade
        FROM cotacao_respostas r
        JOIN cotacao_fornecedores f ON r.fornecedor_id = f.id
        JOIN cotacao_itens i ON r.item_id = i.id
        WHERE r.id = ?
    ''', (resposta_id,))
    
    resposta = cursor.fetchone()
    conn.close()
    
    return dict(resposta) if resposta else None


# =============================================================================
# FUNÇÕES: ANOTAÇÕES DAS SOLICITAÇÕES (Cores e Observações)
# =============================================================================

def salvar_anotacao_sc(numero_sc, item_sc='', cor=None, observacao=None, usuario='Admin'):
    """Salva ou atualiza anotação de uma solicitação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Se não tem cor E não tem observação, remove completamente
    if not cor and not observacao:
        remover_anotacao_sc(numero_sc, item_sc)
        return
    
    # Tenta atualizar primeiro
    cursor.execute('''
        INSERT INTO solicitacao_anotacoes (numero_sc, item_sc, cor, observacao, atualizado_em, atualizado_por)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(numero_sc, item_sc) DO UPDATE SET
            cor = excluded.cor,
            observacao = excluded.observacao,
            atualizado_em = excluded.atualizado_em,
            atualizado_por = excluded.atualizado_por
    ''', (numero_sc, item_sc or '', cor, observacao, datetime.now(), usuario))
    
    conn.commit()
    conn.close()

def obter_anotacoes_sc(numeros_sc=None):
    """Obtém anotações de solicitações. Se numeros_sc for None, retorna todas."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if numeros_sc:
        placeholders = ','.join(['?' for _ in numeros_sc])
        cursor.execute(f'SELECT * FROM solicitacao_anotacoes WHERE numero_sc IN ({placeholders})', numeros_sc)
    else:
        cursor.execute('SELECT * FROM solicitacao_anotacoes')
    
    anotacoes = cursor.fetchall()
    conn.close()
    
    # Retorna como dicionário indexado por numero_sc-item_sc
    return {f"{a['numero_sc']}-{a['item_sc']}": dict(a) for a in anotacoes}

def remover_anotacao_sc(numero_sc, item_sc=''):
    """Remove anotação de uma solicitação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM solicitacao_anotacoes WHERE numero_sc = ? AND item_sc = ?', (numero_sc, item_sc or ''))
    conn.commit()
    conn.close()

# =============================================================================
# FUNÇÕES: ATRIBUIÇÕES MANUAIS DE COMPRADORES
# =============================================================================

def salvar_atribuicao_comprador(numero_sc, item_sc, cod_comprador, nome_comprador, usuario='Admin', observacao=None):
    """
    Salva ou atualiza atribuição manual de comprador para uma solicitação.
    
    Args:
        numero_sc: Número da SC
        item_sc: Item da SC
        cod_comprador: Código do comprador (ex: '018', '016', '007', '008')
        nome_comprador: Nome do comprador (ex: 'Daniel Amaral', 'Aline Chen', etc)
        usuario: Usuário que fez a atribuição
        observacao: Observação opcional sobre a atribuição
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO solicitacao_atribuicoes (numero_sc, item_sc, cod_comprador, nome_comprador, atribuido_por, data_atribuicao, observacao)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(numero_sc, item_sc) DO UPDATE SET
            cod_comprador = excluded.cod_comprador,
            nome_comprador = excluded.nome_comprador,
            atribuido_por = excluded.atribuido_por,
            data_atribuicao = excluded.data_atribuicao,
            observacao = excluded.observacao
    ''', (numero_sc, item_sc, cod_comprador, nome_comprador, usuario, datetime.now(), observacao))
    
    conn.commit()
    conn.close()

def obter_atribuicoes_compradores(numeros_sc=None):
    """
    Obtém atribuições manuais de compradores.
    Se numeros_sc for None, retorna todas as atribuições.
    
    Returns:
        Dicionário indexado por 'numero_sc-item_sc' com dados da atribuição
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if numeros_sc:
        placeholders = ','.join(['?' for _ in numeros_sc])
        cursor.execute(f'SELECT * FROM solicitacao_atribuicoes WHERE numero_sc IN ({placeholders})', numeros_sc)
    else:
        cursor.execute('SELECT * FROM solicitacao_atribuicoes')
    
    atribuicoes = cursor.fetchall()
    conn.close()
    
    # Retorna como dicionário indexado por numero_sc-item_sc
    return {f"{a['numero_sc']}-{a['item_sc']}": dict(a) for a in atribuicoes}

def remover_atribuicao_comprador(numero_sc, item_sc):
    """Remove atribuição manual de comprador de uma solicitação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM solicitacao_atribuicoes WHERE numero_sc = ? AND item_sc = ?', (numero_sc, item_sc))
    conn.commit()
    conn.close()

def obter_atribuicoes_por_comprador(cod_comprador):
    """Obtém todas as atribuições para um comprador específico"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM solicitacao_atribuicoes WHERE cod_comprador = ?', (cod_comprador,))
    atribuicoes = [dict(a) for a in cursor.fetchall()]
    conn.close()
    return atribuicoes

# =============================================================================
# FUNÇÕES: AUDITORIA E RASTREABILIDADE
# =============================================================================

def obter_historico_cotacao(cotacao_id):
    """Obtém histórico completo de uma cotação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM cotacao_historico 
        WHERE cotacao_id = ? 
        ORDER BY data_hora DESC
    ''', (cotacao_id,))
    
    historico = [dict(h) for h in cursor.fetchall()]
    conn.close()
    return historico

# =============================================================================
# FUNÇÕES: RODADAS DE NEGOCIAÇÃO
# =============================================================================

def criar_rodada_negociacao(cotacao_id, fornecedor_id, item_id, preco_original, preco_negociado, 
                            prazo_original=None, prazo_negociado=None, observacao='', usuario='Admin',
                            desconto_percentual=0):
    """Cria uma nova rodada de negociação para um item específico"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO cotacao_rodadas_negociacao 
        (cotacao_id, fornecedor_id, item_id, rodada, preco_unitario_original, preco_unitario_negociado,
         prazo_entrega_original, prazo_entrega_negociado, observacao, usuario, desconto_percentual)
        VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?)
    ''', (cotacao_id, fornecedor_id, item_id, preco_original, preco_negociado, 
          prazo_original, prazo_negociado, observacao, usuario, desconto_percentual))
    
    conn.commit()
    rodada_id = cursor.lastrowid
    conn.close()
    
    print(f"[NEGOCIAÇÃO] Rodada criada: ID={rodada_id}, Fornecedor={fornecedor_id}, Item={item_id}, Desconto={desconto_percentual}%")
    return rodada_id


def obter_rodadas_agrupadas_por_fornecedor(cotacao_id):
    """
    Obtém rodadas de negociação agrupadas por fornecedor com cálculos de totais e economia.
    Retorna estrutura otimizada para exibição de múltiplos fornecedores na segunda rodada.
    Inclui frete negociado no cálculo.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Buscar todas as rodadas com dados completos
    cursor.execute('''
        SELECT rn.*, f.nome_fornecedor, f.id as forn_id, f.frete_total as frete_original,
               i.cod_produto, i.descricao_produto, i.quantidade, i.unidade
        FROM cotacao_rodadas_negociacao rn
        JOIN cotacao_fornecedores f ON rn.fornecedor_id = f.id
        JOIN cotacao_itens i ON rn.item_id = i.id
        WHERE rn.cotacao_id = ?
        ORDER BY rn.fornecedor_id, rn.item_id
    ''', (cotacao_id,))
    
    rodadas_raw = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    if not rodadas_raw:
        return {'fornecedores': [], 'melhor_proposta': None, 'total_economia': 0, 'total_fornecedores': 0}
    
    # Agrupar por fornecedor
    fornecedores_dict = {}
    for rodada in rodadas_raw:
        forn_id = rodada['fornecedor_id']
        if forn_id not in fornecedores_dict:
            # Pegar frete negociado do primeiro registro (é o mesmo para todos os itens do fornecedor)
            frete_negociado = rodada.get('frete_negociado') or 0
            frete_original = rodada.get('frete_original') or 0
            
            fornecedores_dict[forn_id] = {
                'fornecedor_id': forn_id,
                'nome_fornecedor': rodada['nome_fornecedor'],
                'itens': [],
                'total_original': 0,
                'total_negociado': 0,
                'frete_original': frete_original,
                'frete_negociado': frete_negociado,
                'economia_absoluta': 0,
                'economia_percentual': 0,
                'desconto_medio': 0,
                'data_negociacao': rodada['data_negociacao']
            }
        
        # Calcular valores do item
        preco_original = rodada['preco_unitario_original'] or 0
        preco_negociado = rodada['preco_unitario_negociado'] or 0
        quantidade = rodada['quantidade'] or 0
        
        total_item_original = preco_original * quantidade
        total_item_negociado = preco_negociado * quantidade
        economia_item = total_item_original - total_item_negociado
        
        rodada['total_original'] = total_item_original
        rodada['total_negociado'] = total_item_negociado
        rodada['economia'] = economia_item
        
        fornecedores_dict[forn_id]['itens'].append(rodada)
        fornecedores_dict[forn_id]['total_original'] += total_item_original
        fornecedores_dict[forn_id]['total_negociado'] += total_item_negociado
    
    # Calcular economia e desconto médio por fornecedor (incluindo frete)
    fornecedores_lista = []
    for forn_id, forn_data in fornecedores_dict.items():
        # Adicionar frete aos totais para cálculo de economia
        total_original_com_frete = forn_data['total_original'] + forn_data['frete_original']
        total_negociado_com_frete = forn_data['total_negociado'] + forn_data['frete_negociado']
        
        forn_data['total_original_com_frete'] = total_original_com_frete
        forn_data['total_negociado_com_frete'] = total_negociado_com_frete
        forn_data['economia_absoluta'] = total_original_com_frete - total_negociado_com_frete
        
        if total_original_com_frete > 0:
            forn_data['economia_percentual'] = (forn_data['economia_absoluta'] / total_original_com_frete) * 100
        
        # Desconto médio
        descontos = [item.get('desconto_percentual', 0) or 0 for item in forn_data['itens']]
        forn_data['desconto_medio'] = sum(descontos) / len(descontos) if descontos else 0
        
        fornecedores_lista.append(forn_data)
    
    # Ordenar por total negociado com frete (menor = melhor)
    fornecedores_lista.sort(key=lambda x: x['total_negociado_com_frete'])
    
    # Identificar melhor proposta
    melhor_proposta = fornecedores_lista[0] if fornecedores_lista else None
    
    # Marcar melhor proposta
    for forn in fornecedores_lista:
        forn['is_melhor'] = (forn['fornecedor_id'] == melhor_proposta['fornecedor_id']) if melhor_proposta else False
    
    # Total de economia considerando a melhor proposta
    total_economia = melhor_proposta['economia_absoluta'] if melhor_proposta else 0
    
    return {
        'fornecedores': fornecedores_lista,
        'melhor_proposta': melhor_proposta,
        'total_economia': total_economia,
        'total_fornecedores': len(fornecedores_lista)
    }


def obter_rodadas_negociacao(cotacao_id):
    """Obtém todas as rodadas de negociação de uma cotação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT rn.*, f.nome_fornecedor, i.cod_produto, i.descricao_produto, i.quantidade, i.unidade
        FROM cotacao_rodadas_negociacao rn
        JOIN cotacao_fornecedores f ON rn.fornecedor_id = f.id
        JOIN cotacao_itens i ON rn.item_id = i.id
        WHERE rn.cotacao_id = ?
        ORDER BY rn.rodada, rn.data_negociacao DESC
    ''', (cotacao_id,))
    
    rodadas = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rodadas


def excluir_rodadas_fornecedor(cotacao_id, fornecedor_id):
    """Exclui todas as rodadas de um fornecedor específico em uma cotação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        DELETE FROM cotacao_rodadas_negociacao 
        WHERE cotacao_id = ? AND fornecedor_id = ?
    ''', (cotacao_id, fornecedor_id))
    
    linhas_excluidas = cursor.rowcount
    conn.commit()
    conn.close()
    
    print(f"[NEGOCIAÇÃO] Excluídas {linhas_excluidas} rodadas do fornecedor {fornecedor_id} na cotação {cotacao_id}")
    return linhas_excluidas

def atualizar_rodada_negociacao(rodada_id, preco_negociado=None, prazo_negociado=None, observacao=None, desconto_percentual=None, frete_negociado=None):
    """Atualiza uma rodada de negociação existente"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    campos = []
    valores = []
    
    if preco_negociado is not None:
        campos.append('preco_unitario_negociado = ?')
        valores.append(preco_negociado)
    
    if prazo_negociado is not None:
        campos.append('prazo_entrega_negociado = ?')
        valores.append(prazo_negociado)
    
    if observacao is not None:
        campos.append('observacao = ?')
        valores.append(observacao)
    
    if desconto_percentual is not None:
        campos.append('desconto_percentual = ?')
        valores.append(desconto_percentual)
    
    if frete_negociado is not None:
        campos.append('frete_negociado = ?')
        valores.append(frete_negociado)
    
    if not campos:
        conn.close()
        return
    
    valores.append(rodada_id)
    query = f"UPDATE cotacao_rodadas_negociacao SET {', '.join(campos)} WHERE id = ?"
    
    cursor.execute(query, valores)
    conn.commit()
    conn.close()


def atualizar_frete_fornecedor_rodada2(cotacao_id, fornecedor_id, frete_negociado):
    """Atualiza o frete negociado para todos os itens de um fornecedor na rodada 2"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE cotacao_rodadas_negociacao 
        SET frete_negociado = ?
        WHERE cotacao_id = ? AND fornecedor_id = ?
    ''', (frete_negociado, cotacao_id, fornecedor_id))
    
    linhas_atualizadas = cursor.rowcount
    conn.commit()
    conn.close()
    
    print(f"[NEGOCIAÇÃO] Frete atualizado para R${frete_negociado} - Fornecedor {fornecedor_id}, Cotação {cotacao_id}")
    return linhas_atualizadas

def excluir_rodada_negociacao(rodada_id):
    """Exclui uma rodada de negociação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM cotacao_rodadas_negociacao WHERE id = ?', (rodada_id,))
    conn.commit()
    conn.close()

# =============================================================================
# FUNÇÕES: ANEXOS DOS FORNECEDORES
# =============================================================================

def salvar_metadados_anexo(cotacao_id, fornecedor_id, nome_original, nome_arquivo, 
                           caminho_arquivo, tipo_arquivo, tamanho_bytes, mime_type, usuario='Admin'):
    """Salva metadados do anexo no banco (arquivo já está em disco)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO cotacao_anexos 
        (cotacao_id, fornecedor_id, nome_original, nome_arquivo, caminho_arquivo, 
         tipo_arquivo, tamanho_bytes, mime_type, usuario_upload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (cotacao_id, fornecedor_id, nome_original, nome_arquivo, caminho_arquivo,
          tipo_arquivo, tamanho_bytes, mime_type, usuario))
    
    anexo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    print(f"[ANEXO] Metadados salvos: ID={anexo_id}, Fornecedor={fornecedor_id}")
    return anexo_id

def obter_anexos_fornecedor(fornecedor_id):
    """Obtém todos os anexos de um fornecedor"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM cotacao_anexos 
        WHERE fornecedor_id = ? AND ativo = 1
        ORDER BY data_upload DESC
    ''', (fornecedor_id,))
    
    anexos = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return anexos

def obter_anexo_por_id(anexo_id):
    """Obtém um anexo específico pelo ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM cotacao_anexos WHERE id = ? AND ativo = 1', (anexo_id,))
    anexo = cursor.fetchone()
    conn.close()
    
    return dict(anexo) if anexo else None

def excluir_anexo(anexo_id):
    """Marca um anexo como inativo (soft delete)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE cotacao_anexos SET ativo = 0 WHERE id = ?', (anexo_id,))
    conn.commit()
    conn.close()

def obter_ultimo_anexo_fornecedor(fornecedor_id):
    """Obtém o anexo mais recente de um fornecedor"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM cotacao_anexos 
        WHERE fornecedor_id = ? AND ativo = 1
        ORDER BY data_upload DESC
        LIMIT 1
    ''', (fornecedor_id,))
    
    anexo = cursor.fetchone()
    conn.close()
    return dict(anexo) if anexo else None

def buscar_cotacoes_por_sc(numero_sc):
    """Busca todas as cotações que contêm determinada SC"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT c.* 
        FROM cotacoes c
        JOIN cotacao_itens i ON c.id = i.cotacao_id
        WHERE i.numero_sc = ?
        ORDER BY c.data_criacao DESC
    ''', (numero_sc,))
    
    cotacoes = [dict(c) for c in cursor.fetchall()]
    conn.close()
    return cotacoes


# =============================================================================
# FUNÇÕES: COTAÇÃO EXTERNA VIA JSON
# =============================================================================

def criar_envio_json(cotacao_id, fornecedor_id, token_envio, hash_validacao, arquivo_json=None, usuario=None, observacao=None):
    """
    Registra um novo envio de JSON para cotação externa.
    
    Args:
        cotacao_id: ID da cotação
        fornecedor_id: ID do fornecedor
        token_envio: Token único para identificar o envio
        hash_validacao: Hash para validar integridade do JSON
        arquivo_json: Caminho do arquivo JSON gerado
        usuario: Usuário que gerou o envio
        observacao: Observação opcional
    
    Returns:
        ID do registro criado
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO cotacao_json_envios 
        (cotacao_id, fornecedor_id, token_envio, hash_validacao, arquivo_json_gerado, usuario_geracao, observacao)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (cotacao_id, fornecedor_id, token_envio, hash_validacao, arquivo_json, usuario, observacao))
    
    envio_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return envio_id


def obter_envio_json_por_token(token_envio):
    """Obtém envio de JSON pelo token"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM cotacao_json_envios WHERE token_envio = ?', (token_envio,))
    envio = cursor.fetchone()
    conn.close()
    
    return dict(envio) if envio else None


def obter_envios_json_fornecedor(fornecedor_id):
    """Obtém todos os envios de JSON de um fornecedor"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM cotacao_json_envios 
        WHERE fornecedor_id = ?
        ORDER BY data_geracao DESC
    ''', (fornecedor_id,))
    
    envios = [dict(e) for e in cursor.fetchall()]
    conn.close()
    return envios


def atualizar_importacao_json(envio_id, arquivo_resposta=None, usuario=None):
    """
    Atualiza o registro de envio com a importação da resposta.
    
    Args:
        envio_id: ID do envio
        arquivo_resposta: Caminho do arquivo JSON de resposta importado
        usuario: Usuário que importou
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE cotacao_json_envios 
        SET data_importacao = ?, status = 'Importado', arquivo_json_resposta = ?, usuario_importacao = ?
        WHERE id = ?
    ''', (datetime.now(), arquivo_resposta, usuario, envio_id))
    
    conn.commit()
    conn.close()


def obter_ultimo_envio_json_fornecedor(fornecedor_id):
    """Obtém o último envio de JSON de um fornecedor"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM cotacao_json_envios 
        WHERE fornecedor_id = ?
        ORDER BY data_geracao DESC
        LIMIT 1
    ''', (fornecedor_id,))
    
    envio = cursor.fetchone()
    conn.close()
    return dict(envio) if envio else None


# =============================================================================
# FUNÇÕES: AVALIAÇÃO DE FORNECEDORES ISO (PQ021)
# =============================================================================

def listar_avaliacoes_iso():
    """Lista todas as avaliações de fornecedores ISO"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM avaliacao_fornecedores_iso 
        ORDER BY data_vencimento ASC, nome_fornecedor ASC
    ''')
    
    avaliacoes = [dict(a) for a in cursor.fetchall()]
    conn.close()
    return avaliacoes


def obter_avaliacao_iso(avaliacao_id):
    """Obtém uma avaliação ISO pelo ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM avaliacao_fornecedores_iso WHERE id = ?', (avaliacao_id,))
    avaliacao = cursor.fetchone()
    conn.close()
    
    return dict(avaliacao) if avaliacao else None


def obter_avaliacao_iso_por_fornecedor(cod_fornecedor):
    """Obtém avaliação ISO pelo código do fornecedor"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM avaliacao_fornecedores_iso WHERE cod_fornecedor = ?', (cod_fornecedor,))
    avaliacao = cursor.fetchone()
    conn.close()
    
    return dict(avaliacao) if avaliacao else None


def criar_avaliacao_iso(cod_fornecedor, nome_fornecedor, email_fornecedor=None, 
                        data_ultima_avaliacao=None, data_vencimento=None,
                        possui_iso='Nao', nota=None, observacao=None, usuario=None):
    """
    Cria uma nova avaliação ISO para um fornecedor.
    
    Args:
        cod_fornecedor: Código do fornecedor no TOTVS
        nome_fornecedor: Nome do fornecedor
        email_fornecedor: E-mail do fornecedor
        data_ultima_avaliacao: Data da última avaliação (formato YYYY-MM-DD)
        data_vencimento: Data de vencimento da avaliação
        possui_iso: 'Sim' ou 'Nao'
        nota: Nota da avaliação (0-10)
        observacao: Observação geral
        usuario: Usuário que criou o registro
    
    Returns:
        ID do registro criado ou None se já existe
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO avaliacao_fornecedores_iso 
            (cod_fornecedor, nome_fornecedor, email_fornecedor, data_ultima_avaliacao, 
             data_vencimento, possui_iso, nota, observacao, criado_por)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (cod_fornecedor, nome_fornecedor, email_fornecedor, data_ultima_avaliacao,
              data_vencimento, possui_iso, nota, observacao, usuario))
        
        avaliacao_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return avaliacao_id
    except sqlite3.IntegrityError:
        # Fornecedor já existe
        conn.close()
        return None


def atualizar_avaliacao_iso(avaliacao_id, **kwargs):
    """
    Atualiza uma avaliação ISO existente.
    
    Args:
        avaliacao_id: ID da avaliação
        **kwargs: Campos a serem atualizados (nome_fornecedor, email_fornecedor, 
                  data_ultima_avaliacao, data_vencimento, possui_iso, nota, observacao, atualizado_por)
    """
    campos_permitidos = ['nome_fornecedor', 'email_fornecedor', 'data_ultima_avaliacao',
                         'data_vencimento', 'possui_iso', 'nota', 'observacao', 'atualizado_por']
    
    campos_update = []
    valores = []
    
    for campo, valor in kwargs.items():
        if campo in campos_permitidos:
            campos_update.append(f"{campo} = ?")
            valores.append(valor)
    
    if not campos_update:
        return False
    
    # Sempre atualiza a data de atualização
    campos_update.append("atualizado_em = ?")
    valores.append(datetime.now())
    valores.append(avaliacao_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = f"UPDATE avaliacao_fornecedores_iso SET {', '.join(campos_update)} WHERE id = ?"
    cursor.execute(query, valores)
    
    conn.commit()
    conn.close()
    return True


def excluir_avaliacao_iso(avaliacao_id):
    """Exclui uma avaliação ISO e seus documentos associados"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Os documentos serão excluídos automaticamente pelo CASCADE
    cursor.execute('DELETE FROM avaliacao_fornecedores_iso WHERE id = ?', (avaliacao_id,))
    
    conn.commit()
    conn.close()
    return True


# =============================================================================
# FUNÇÕES: DOCUMENTOS DA AVALIAÇÃO ISO
# =============================================================================

def listar_documentos_avaliacao_iso(avaliacao_id):
    """Lista todos os documentos de uma avaliação ISO"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM avaliacao_iso_documentos 
        WHERE avaliacao_id = ? AND ativo = 1
        ORDER BY tipo_documento, data_upload DESC
    ''', (avaliacao_id,))
    
    documentos = [dict(d) for d in cursor.fetchall()]
    conn.close()
    return documentos


def criar_documento_avaliacao_iso(avaliacao_id, tipo_documento, nome_original, 
                                   nome_arquivo, caminho_arquivo, usuario=None,
                                   nome_documento=None, extensao=None, tamanho_bytes=None):
    """
    Registra um novo documento de avaliação ISO.
    
    Args:
        avaliacao_id: ID da avaliação
        tipo_documento: 'iso', 'relatorio_avaliacao', 'contrato', 'outro'
        nome_original: Nome original do arquivo
        nome_arquivo: Nome único gerado para o arquivo
        caminho_arquivo: Caminho completo do arquivo no sistema
        usuario: Usuário que fez o upload
        nome_documento: Nome personalizado definido pelo usuário
        extensao: Extensão do arquivo (pdf, doc, docx)
        tamanho_bytes: Tamanho do arquivo em bytes
    
    Returns:
        ID do documento criado
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO avaliacao_iso_documentos 
        (avaliacao_id, tipo_documento, nome_documento, nome_original, nome_arquivo, 
         caminho_arquivo, extensao, tamanho_bytes, usuario_upload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (avaliacao_id, tipo_documento, nome_documento, nome_original, nome_arquivo, 
          caminho_arquivo, extensao, tamanho_bytes, usuario))
    
    doc_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return doc_id


def desativar_documento_avaliacao_iso(documento_id):
    """Desativa (soft delete) um documento de avaliação ISO"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE avaliacao_iso_documentos SET ativo = 0 WHERE id = ?', (documento_id,))
    
    conn.commit()
    conn.close()
    return True


def obter_documento_avaliacao_iso(documento_id):
    """Obtém um documento pelo ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM avaliacao_iso_documentos WHERE id = ?', (documento_id,))
    documento = cursor.fetchone()
    conn.close()
    
    return dict(documento) if documento else None


# =============================================================================
# FUNÇÕES: HISTÓRICO DE E-MAILS ISO
# =============================================================================

def listar_emails_avaliacao_iso(avaliacao_id):
    """Lista todos os e-mails enviados para uma avaliação ISO"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM avaliacao_iso_emails 
        WHERE avaliacao_id = ?
        ORDER BY data_envio DESC
    ''', (avaliacao_id,))
    
    emails = [dict(e) for e in cursor.fetchall()]
    conn.close()
    return emails


def registrar_email_avaliacao_iso(avaliacao_id, email_destinatario, assunto, mensagem, 
                                   usuario=None, status='Enviado', erro_msg=None):
    """
    Registra um e-mail enviado para avaliação ISO.
    
    Args:
        avaliacao_id: ID da avaliação
        email_destinatario: E-mail de destino
        assunto: Assunto do e-mail
        mensagem: Corpo do e-mail
        usuario: Usuário que enviou
        status: 'Enviado' ou 'Erro'
        erro_msg: Mensagem de erro se houver
    
    Returns:
        ID do registro de e-mail
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO avaliacao_iso_emails 
        (avaliacao_id, email_destinatario, assunto, mensagem, enviado_por, status, erro_msg)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (avaliacao_id, email_destinatario, assunto, mensagem, usuario, status, erro_msg))
    
    email_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return email_id


def contar_emails_enviados_avaliacao(avaliacao_id):
    """Conta quantos e-mails foram enviados para uma avaliação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT COUNT(*) FROM avaliacao_iso_emails 
        WHERE avaliacao_id = ? AND status = 'Enviado'
    ''', (avaliacao_id,))
    
    count = cursor.fetchone()[0]
    conn.close()
    return count


def obter_ultimo_email_avaliacao(avaliacao_id):
    """Obtém o último e-mail enviado para uma avaliação"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM avaliacao_iso_emails 
        WHERE avaliacao_id = ? AND status = 'Enviado'
        ORDER BY data_envio DESC
        LIMIT 1
    ''', (avaliacao_id,))
    
    email = cursor.fetchone()
    conn.close()
    return dict(email) if email else None


# =============================================================================
# FUNÇÕES: COTAÇÕES EXTERNAS (RENDER) - PERSISTÊNCIA PARA LINKS PÚBLICOS
# =============================================================================

def criar_cotacao_externa(token, dados_json, expira_em, ip_criacao=None):
    """
    Cria uma nova cotação externa no banco de dados.
    Substitui o armazenamento em memória para garantir persistência.
    
    Args:
        token: Token único gerado para a cotação
        dados_json: Dados da cotação em formato JSON string
        expira_em: Data/hora de expiração
        ip_criacao: IP de onde foi criada a cotação
    
    Returns:
        ID da cotação criada
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO cotacoes_externas (token, dados_json, status, expira_em, ip_criacao)
        VALUES (?, ?, 'aberta', ?, ?)
    ''', (token, dados_json, expira_em, ip_criacao))
    
    cotacao_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    print(f"[DB] Cotação externa criada: ID={cotacao_id}, Token={token[:20]}...")
    return cotacao_id


def obter_cotacao_externa_por_token(token):
    """
    Busca uma cotação externa pelo token.
    
    Args:
        token: Token único da cotação
    
    Returns:
        Dicionário com dados da cotação ou None se não encontrada
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM cotacoes_externas WHERE token = ?
    ''', (token,))
    
    cotacao = cursor.fetchone()
    conn.close()
    
    if cotacao:
        result = dict(cotacao)
        print(f"[DB] Cotação externa encontrada: ID={result['id']}, Status={result['status']}")
        return result
    else:
        print(f"[DB] Cotação externa NÃO encontrada: Token={token[:20]}...")
        return None


def atualizar_resposta_cotacao_externa(token, resposta_json, ip_resposta=None):
    """
    Atualiza uma cotação externa com a resposta do fornecedor.
    
    Args:
        token: Token da cotação
        resposta_json: Resposta em formato JSON string
        ip_resposta: IP de onde veio a resposta
    
    Returns:
        True se atualizado com sucesso, False se não encontrado
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE cotacoes_externas 
        SET status = 'respondida', 
            respondido_em = CURRENT_TIMESTAMP,
            resposta_json = ?,
            ip_resposta = ?
        WHERE token = ? AND status = 'aberta'
    ''', (resposta_json, ip_resposta, token))
    
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    if rows_affected > 0:
        print(f"[DB] Cotação externa respondida: Token={token[:20]}...")
        return True
    else:
        print(f"[DB] Falha ao responder cotação externa: Token={token[:20]}...")
        return False


def listar_cotacoes_externas(status=None, limite=100):
    """
    Lista cotações externas, opcionalmente filtradas por status.
    
    Args:
        status: Filtrar por status ('aberta', 'respondida', 'expirada')
        limite: Número máximo de resultados
    
    Returns:
        Lista de cotações
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if status:
        cursor.execute('''
            SELECT id, token, status, criado_em, expira_em, respondido_em 
            FROM cotacoes_externas 
            WHERE status = ?
            ORDER BY criado_em DESC
            LIMIT ?
        ''', (status, limite))
    else:
        cursor.execute('''
            SELECT id, token, status, criado_em, expira_em, respondido_em 
            FROM cotacoes_externas 
            ORDER BY criado_em DESC
            LIMIT ?
        ''', (limite,))
    
    cotacoes = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return cotacoes


def contar_cotacoes_externas():
    """Conta cotações externas por status"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT status, COUNT(*) as total 
        FROM cotacoes_externas 
        GROUP BY status
    ''')
    
    resultado = {row['status']: row['total'] for row in cursor.fetchall()}
    conn.close()
    return resultado


def criar_cotacao_externa_v2(token, dados_json, expira_em, cotacao_id, fornecedor_id, fornecedor_nome, ip_criacao=None):
    """
    Cria uma nova cotação externa com referência à cotação interna.
    Versão 2 - Inclui cotacao_id e fornecedor_id para sincronização automática.
    
    Args:
        token: Token único gerado para a cotação
        dados_json: Dados da cotação em formato JSON string
        expira_em: Data/hora de expiração
        cotacao_id: ID da cotação interna
        fornecedor_id: ID do fornecedor na tabela cotacao_fornecedores
        fornecedor_nome: Nome do fornecedor para exibição
        ip_criacao: IP de onde foi criada a cotação
    
    Returns:
        ID da cotação externa criada
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO cotacoes_externas 
        (token, dados_json, status, expira_em, ip_criacao, cotacao_id, fornecedor_id, fornecedor_nome, sincronizada)
        VALUES (?, ?, 'aberta', ?, ?, ?, ?, ?, 0)
    ''', (token, dados_json, expira_em, ip_criacao, cotacao_id, fornecedor_id, fornecedor_nome))
    
    cotacao_externa_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    print(f"[DB] Cotação externa V2 criada: ID={cotacao_externa_id}, CotacaoID={cotacao_id}, FornecedorID={fornecedor_id}")
    return cotacao_externa_id


def listar_cotacoes_externas_respondidas_nao_sincronizadas(limite=50):
    """
    Lista cotações externas que foram respondidas mas ainda não foram sincronizadas.
    Usado pelo sistema de polling para atualizar automaticamente as cotações internas.
    
    Returns:
        Lista de dicionários com dados das cotações pendentes de sincronização
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            id,
            token,
            cotacao_id,
            fornecedor_id,
            fornecedor_nome,
            resposta_json,
            respondido_em,
            dados_json
        FROM cotacoes_externas 
        WHERE status = 'respondida' 
          AND (sincronizada = 0 OR sincronizada IS NULL)
          AND cotacao_id IS NOT NULL
          AND fornecedor_id IS NOT NULL
        ORDER BY respondido_em ASC
        LIMIT ?
    ''', (limite,))
    
    cotacoes = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    print(f"[DB] Cotações externas pendentes de sincronização: {len(cotacoes)}")
    return cotacoes


def marcar_cotacao_externa_sincronizada(cotacao_externa_id):
    """
    Marca uma cotação externa como sincronizada após processar os dados.
    
    Args:
        cotacao_externa_id: ID da cotação externa
    
    Returns:
        True se atualizado com sucesso
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE cotacoes_externas 
        SET sincronizada = 1
        WHERE id = ?
    ''', (cotacao_externa_id,))
    
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    if rows_affected > 0:
        print(f"[DB] Cotação externa {cotacao_externa_id} marcada como sincronizada")
        return True
    return False


def sincronizar_resposta_cotacao_externa(cotacao_externa_id, cotacao_id, fornecedor_id, resposta_json):
    """
    Sincroniza a resposta de uma cotação externa com o sistema interno.
    Insere os preços, prazos e observações na tabela de respostas.
    
    Args:
        cotacao_externa_id: ID da cotação externa
        cotacao_id: ID da cotação interna
        fornecedor_id: ID do fornecedor
        resposta_json: Resposta em formato JSON (já parseado como dict)
    
    Returns:
        True se sincronizado com sucesso
    """
    import json
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Parse resposta se for string
        if isinstance(resposta_json, str):
            resposta = json.loads(resposta_json)
        else:
            resposta = resposta_json
        
        # Dados gerais do fornecedor
        frete_total = float(resposta.get('frete_total', 0) or 0)
        condicao_pagamento = resposta.get('condicao_pagamento', '')
        observacao_geral = resposta.get('observacao_geral', '')
        
        # Atualiza dados do fornecedor
        cursor.execute('''
            UPDATE cotacao_fornecedores 
            SET status = 'Respondido',
                data_resposta = CURRENT_TIMESTAMP,
                frete_total = ?,
                condicao_pagamento = ?,
                observacao_geral = ?
            WHERE id = ?
        ''', (frete_total, condicao_pagamento, observacao_geral, fornecedor_id))
        
        # Processa itens da resposta
        itens = resposta.get('itens', [])
        for item in itens:
            item_id = item.get('item_id')
            preco_unitario = float(item.get('preco_unitario', 0) or 0)
            prazo_entrega = int(item.get('prazo_entrega', 0) or 0)
            observacao_item = item.get('observacao', '')
            
            if not item_id:
                continue
            
            # Verifica se já existe resposta para este item
            cursor.execute('''
                SELECT id FROM cotacao_respostas 
                WHERE cotacao_id = ? AND fornecedor_id = ? AND item_id = ?
            ''', (cotacao_id, fornecedor_id, item_id))
            
            existe = cursor.fetchone()
            
            if existe:
                # Atualiza resposta existente
                cursor.execute('''
                    UPDATE cotacao_respostas 
                    SET preco_unitario = ?,
                        prazo_entrega = ?,
                        observacao = ?,
                        data_resposta = CURRENT_TIMESTAMP
                    WHERE cotacao_id = ? AND fornecedor_id = ? AND item_id = ?
                ''', (preco_unitario, prazo_entrega, observacao_item, cotacao_id, fornecedor_id, item_id))
            else:
                # Insere nova resposta
                cursor.execute('''
                    INSERT INTO cotacao_respostas 
                    (cotacao_id, fornecedor_id, item_id, preco_unitario, prazo_entrega, observacao)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (cotacao_id, fornecedor_id, item_id, preco_unitario, prazo_entrega, observacao_item))
        
        # Marca a cotação externa como sincronizada
        cursor.execute('''
            UPDATE cotacoes_externas 
            SET sincronizada = 1
            WHERE id = ?
        ''', (cotacao_externa_id,))
        
        conn.commit()
        conn.close()
        
        print(f"[DB] Resposta sincronizada: CotacaoExterna={cotacao_externa_id}, Cotacao={cotacao_id}, Fornecedor={fornecedor_id}, Itens={len(itens)}")
        return True
        
    except Exception as e:
        print(f"[DB] Erro ao sincronizar resposta: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False


def atualizar_cotacao_externa_com_ids(token, cotacao_id, fornecedor_id, fornecedor_nome):
    """
    Atualiza uma cotação externa existente com os IDs de referência.
    Usado quando o token já foi criado mas sem os IDs de sincronização.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE cotacoes_externas 
        SET cotacao_id = ?,
            fornecedor_id = ?,
            fornecedor_nome = ?
        WHERE token = ?
    ''', (cotacao_id, fornecedor_id, fornecedor_nome, token))
    
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    if rows_affected > 0:
        print(f"[DB] Cotação externa atualizada com IDs: Token={token[:20]}..., Cotacao={cotacao_id}, Fornecedor={fornecedor_id}")
        return True
    return False


# =============================================================================
# FUNÇÕES: PEDIDOS DE COMPRA
# =============================================================================

def gerar_numero_pedido():
    """
    Gera o próximo número sequencial de pedido de compra.
    Formato: PC + ANO + Sequencial (ex: PC2026001)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    ano_atual = datetime.now().year
    prefixo = f"PC{ano_atual}"
    
    # Busca o maior número do ano atual
    cursor.execute("""
        SELECT numero_pedido FROM pedidos_compra 
        WHERE numero_pedido LIKE ? 
        ORDER BY numero_pedido DESC LIMIT 1
    """, (f"{prefixo}%",))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        # Extrai o número sequencial do último pedido
        ultimo = result[0]
        try:
            seq_atual = int(ultimo.replace(prefixo, ''))
            proximo = seq_atual + 1
        except:
            proximo = 1
    else:
        proximo = 1
    
    return f"{prefixo}{str(proximo).zfill(4)}"


def obter_ultimo_numero_pedido():
    """
    Retorna o último número de pedido usado e o próximo sugerido.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT numero_pedido, data_pedido, nome_fornecedor 
        FROM pedidos_compra 
        ORDER BY id DESC LIMIT 1
    """)
    
    result = cursor.fetchone()
    conn.close()
    
    proximo = gerar_numero_pedido()
    
    if result:
        return {
            'ultimo_pedido': result['numero_pedido'],
            'data_ultimo': result['data_pedido'],
            'fornecedor_ultimo': result['nome_fornecedor'],
            'proximo_sugerido': proximo
        }
    else:
        return {
            'ultimo_pedido': None,
            'data_ultimo': None,
            'fornecedor_ultimo': None,
            'proximo_sugerido': proximo
        }


def criar_pedido_compra(dados, itens, usuario='Admin'):
    """
    Cria um novo pedido de compra com seus itens.
    
    Args:
        dados: Dict com campos do pedido {
            numero_pedido: opcional (auto-gera se não informado),
            data_pedido: YYYY-MM-DD,
            cod_fornecedor, nome_fornecedor,
            condicao_pagamento, contato, observacoes
        }
        itens: Lista de dicts [{
            numero_sc, item_sc, cod_produto, descricao_produto,
            quantidade, unidade, valor_unitario, ipi, data_necessidade
        }]
        usuario: Usuário que criou
    
    Returns:
        Dict com id, numero_pedido e success
    """
    import json
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Gera número se não informado
        numero = dados.get('numero_pedido') or gerar_numero_pedido()
        
        # Calcula valor total
        valor_total = 0
        for item in itens:
            qtd = float(item.get('quantidade', 0) or 0)
            preco = float(item.get('valor_unitario', 0) or 0)
            ipi = float(item.get('ipi', 0) or 0)
            valor_item = qtd * preco * (1 + ipi / 100)
            valor_total += valor_item
        
        # Insere o pedido
        cursor.execute('''
            INSERT INTO pedidos_compra (
                numero_pedido, data_pedido, cod_fornecedor, nome_fornecedor,
                condicao_pagamento, contato, observacoes, valor_total, criado_por, atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            numero,
            dados.get('data_pedido'),
            dados.get('cod_fornecedor'),
            dados.get('nome_fornecedor'),
            dados.get('condicao_pagamento'),
            dados.get('contato'),
            dados.get('observacoes'),
            valor_total,
            usuario,
            datetime.now()
        ))
        
        pedido_id = cursor.lastrowid
        
        # Insere os itens
        for item in itens:
            qtd = float(item.get('quantidade', 0) or 0)
            preco = float(item.get('valor_unitario', 0) or 0)
            ipi = float(item.get('ipi', 0) or 0)
            valor_item = qtd * preco * (1 + ipi / 100)
            
            cursor.execute('''
                INSERT INTO pedido_itens (
                    pedido_id, numero_sc, item_sc, cod_produto, descricao_produto,
                    quantidade, unidade, valor_unitario, ipi, valor_total,
                    data_necessidade, observacao
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pedido_id,
                item.get('numero_sc'),
                item.get('item_sc'),
                item.get('cod_produto'),
                item.get('descricao_produto'),
                qtd,
                item.get('unidade'),
                preco,
                ipi,
                valor_item,
                item.get('data_necessidade'),
                item.get('observacao')
            ))
        
        # Registrar histórico
        cursor.execute('''
            INSERT INTO pedido_historico (pedido_id, acao, descricao, usuario, dados_json)
            VALUES (?, 'CRIACAO', ?, ?, ?)
        ''', (
            pedido_id, 
            f'Pedido criado com {len(itens)} item(ns)', 
            usuario,
            json.dumps({'itens': len(itens), 'valor_total': valor_total})
        ))
        
        conn.commit()
        conn.close()
        
        print(f"[DB] Pedido criado: {numero} com {len(itens)} itens, Valor: R${valor_total:.2f}")
        
        return {
            'success': True,
            'pedido_id': pedido_id,
            'numero_pedido': numero,
            'valor_total': valor_total
        }
        
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"[DB] Erro ao criar pedido: {e}")
        return {'success': False, 'error': str(e)}


def listar_pedidos_compra(filtros=None):
    """
    Lista todos os pedidos de compra com opção de filtros.
    
    Args:
        filtros: Dict opcional com {
            data_inicio, data_fim, fornecedor, status
        }
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT 
            p.*,
            (SELECT COUNT(*) FROM pedido_itens WHERE pedido_id = p.id) as total_itens
        FROM pedidos_compra p
        WHERE 1=1
    """
    params = []
    
    if filtros:
        if filtros.get('data_inicio'):
            query += " AND p.data_pedido >= ?"
            params.append(filtros['data_inicio'])
        if filtros.get('data_fim'):
            query += " AND p.data_pedido <= ?"
            params.append(filtros['data_fim'])
        if filtros.get('fornecedor'):
            query += " AND (p.cod_fornecedor LIKE ? OR p.nome_fornecedor LIKE ?)"
            params.extend([f"%{filtros['fornecedor']}%", f"%{filtros['fornecedor']}%"])
        if filtros.get('status'):
            query += " AND p.status = ?"
            params.append(filtros['status'])
    
    query += " ORDER BY p.id DESC"
    
    cursor.execute(query, params)
    pedidos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return pedidos


def obter_pedido_detalhado(pedido_id):
    """
    Obtém um pedido completo com seus itens e histórico.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Pedido principal
    cursor.execute("SELECT * FROM pedidos_compra WHERE id = ?", (pedido_id,))
    pedido = cursor.fetchone()
    
    if not pedido:
        conn.close()
        return None
    
    pedido = dict(pedido)
    
    # Itens do pedido
    cursor.execute("SELECT * FROM pedido_itens WHERE pedido_id = ? ORDER BY id", (pedido_id,))
    pedido['itens'] = [dict(row) for row in cursor.fetchall()]
    
    # Histórico
    cursor.execute("SELECT * FROM pedido_historico WHERE pedido_id = ? ORDER BY data_hora DESC", (pedido_id,))
    pedido['historico'] = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return pedido


def atualizar_pedido_compra(pedido_id, dados, usuario='Admin'):
    """
    Atualiza campos do pedido de compra.
    """
    import json
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        campos_update = []
        params = []
        
        for campo in ['data_pedido', 'cod_fornecedor', 'nome_fornecedor', 
                      'condicao_pagamento', 'contato', 'observacoes', 'status']:
            if campo in dados:
                campos_update.append(f"{campo} = ?")
                params.append(dados[campo])
        
        if not campos_update:
            return {'success': False, 'error': 'Nenhum campo para atualizar'}
        
        campos_update.append("atualizado_em = ?")
        params.append(datetime.now())
        params.append(pedido_id)
        
        cursor.execute(f"""
            UPDATE pedidos_compra 
            SET {', '.join(campos_update)}
            WHERE id = ?
        """, params)
        
        # Registrar histórico
        cursor.execute('''
            INSERT INTO pedido_historico (pedido_id, acao, descricao, usuario, dados_json)
            VALUES (?, 'ATUALIZACAO', 'Pedido atualizado', ?, ?)
        ''', (pedido_id, usuario, json.dumps(dados)))
        
        conn.commit()
        conn.close()
        
        return {'success': True}
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return {'success': False, 'error': str(e)}


def verificar_solicitacao_tem_pedido(numero_sc, item_sc):
    """
    Verifica se uma solicitação já foi vinculada a um pedido.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT pi.id, p.numero_pedido, p.status
        FROM pedido_itens pi
        JOIN pedidos_compra p ON p.id = pi.pedido_id
        WHERE pi.numero_sc = ? AND pi.item_sc = ?
    """, (numero_sc, item_sc))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            'tem_pedido': True,
            'pedido_id': result['id'],
            'numero_pedido': result['numero_pedido'],
            'status': result['status']
        }
    
    return {'tem_pedido': False}


def gerar_payload_totvs(pedido_id):
    """
    Gera o payload no formato esperado pela API do TOTVS para envio futuro.
    """
    pedido = obter_pedido_detalhado(pedido_id)
    
    if not pedido:
        return None
    
    payload = {
        "numero_pedido": pedido['numero_pedido'],
        "data_pedido": pedido['data_pedido'],
        "fornecedor": {
            "codigo": pedido['cod_fornecedor'],
            "nome": pedido['nome_fornecedor']
        },
        "condicao_pagamento": pedido['condicao_pagamento'],
        "contato": pedido['contato'],
        "observacoes": pedido['observacoes'],
        "itens": []
    }
    
    for item in pedido['itens']:
        payload['itens'].append({
            "solicitacao_id": item['numero_sc'],
            "item_sc": item['item_sc'],
            "produto_id": item['cod_produto'],
            "descricao": item['descricao_produto'],
            "quantidade": item['quantidade'],
            "unidade": item['unidade'],
            "valor_unitario": item['valor_unitario'],
            "ipi": item['ipi'],
            "data_necessidade": item['data_necessidade']
        })
    
    return payload


def registrar_envio_totvs(pedido_id, resposta, usuario='Admin'):
    """
    Registra o envio do pedido para o TOTVS.
    """
    import json
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE pedidos_compra 
        SET enviado_totvs = 1, data_envio_totvs = ?, resposta_totvs = ?, atualizado_em = ?
        WHERE id = ?
    """, (datetime.now(), json.dumps(resposta), datetime.now(), pedido_id))
    
    cursor.execute('''
        INSERT INTO pedido_historico (pedido_id, acao, descricao, usuario, dados_json)
        VALUES (?, 'ENVIO_TOTVS', 'Pedido enviado ao TOTVS', ?, ?)
    ''', (pedido_id, usuario, json.dumps(resposta)))
    
    conn.commit()
    conn.close()
    
    return True


def excluir_pedido_compra(pedido_id, usuario='Admin'):
    """
    Exclui um pedido de compra e seus itens.
    Apenas pedidos que não foram enviados ao TOTVS podem ser excluídos.
    
    Args:
        pedido_id: ID do pedido a ser excluído
        usuario: Usuário que está excluindo
    
    Returns:
        Dict com success e mensagem
    """
    import json
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Verifica se o pedido existe e se foi enviado ao TOTVS
        cursor.execute("""
            SELECT numero_pedido, enviado_totvs, status 
            FROM pedidos_compra 
            WHERE id = ?
        """, (pedido_id,))
        
        pedido = cursor.fetchone()
        
        if not pedido:
            return {'success': False, 'error': 'Pedido não encontrado'}
        
        # Não permite excluir pedidos já enviados ao TOTVS
        if pedido['enviado_totvs'] == 1:
            return {
                'success': False, 
                'error': 'Não é possível excluir pedidos já enviados ao TOTVS'
            }
        
        numero_pedido = pedido['numero_pedido']
        
        # Registra exclusão no histórico antes de excluir
        cursor.execute('''
            INSERT INTO pedido_historico (pedido_id, acao, descricao, usuario, dados_json)
            VALUES (?, 'EXCLUSAO', 'Pedido excluído', ?, ?)
        ''', (pedido_id, usuario, json.dumps({'numero_pedido': numero_pedido})))
        
        # Exclui itens (CASCADE deve funcionar, mas fazemos explícito para garantir)
        cursor.execute("DELETE FROM pedido_itens WHERE pedido_id = ?", (pedido_id,))
        
        # Exclui histórico
        cursor.execute("DELETE FROM pedido_historico WHERE pedido_id = ?", (pedido_id,))
        
        # Exclui pedido
        cursor.execute("DELETE FROM pedidos_compra WHERE id = ?", (pedido_id,))
        
        conn.commit()
        conn.close()
        
        print(f"[DB] Pedido excluído: {numero_pedido} (ID: {pedido_id})")
        
        return {
            'success': True,
            'message': f'Pedido {numero_pedido} excluído com sucesso'
        }
        
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"[DB] Erro ao excluir pedido: {e}")
        return {'success': False, 'error': str(e)}


# =============================================================================
# FUNÇÕES: HISTÓRICO DE ENVIO DE E-MAILS (Controle 24h)
# =============================================================================

def registrar_envio_email(tipo, identificador, email_destinatario, assunto=None, corpo=None, enviado_por=None):
    """
    Registra um envio de e-mail no histórico.
    
    Args:
        tipo: 'pedido' ou 'item' ou 'terceiros'
        identificador: número do pedido ou pedido-item (ex: '123456' ou '123456-001')
        email_destinatario: e-mail do fornecedor
        assunto: assunto do e-mail
        corpo: corpo do e-mail
        enviado_por: usuário que enviou
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO email_envios_historico 
            (tipo, identificador, email_destinatario, assunto, corpo, enviado_por)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (tipo, identificador, email_destinatario, assunto, corpo, enviado_por))
        
        conn.commit()
        envio_id = cursor.lastrowid
        conn.close()
        
        print(f"[DB] E-mail registrado: {tipo}/{identificador} -> {email_destinatario}")
        return {'success': True, 'id': envio_id}
        
    except Exception as e:
        print(f"[DB] Erro ao registrar envio de e-mail: {e}")
        return {'success': False, 'error': str(e)}


def verificar_ultimo_envio_email(tipo, identificador):
    """
    Verifica o último envio de e-mail para um pedido/item.
    Retorna informações sobre se pode reenviar (regra 24h).
    
    Args:
        tipo: 'pedido' ou 'item' ou 'terceiros'
        identificador: número do pedido ou pedido-item
        
    Returns:
        dict com: pode_enviar, ultimo_envio, horas_passadas, minutos_restantes
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Busca o último envio bem-sucedido
        cursor.execute('''
            SELECT data_envio, email_destinatario, assunto
            FROM email_envios_historico
            WHERE tipo = ? AND identificador = ? AND sucesso = 1
            ORDER BY data_envio DESC
            LIMIT 1
        ''', (tipo, identificador))
        
        resultado = cursor.fetchone()
        conn.close()
        
        if not resultado:
            return {
                'pode_enviar': True,
                'ultimo_envio': None,
                'horas_passadas': None,
                'minutos_restantes': 0
            }
        
        # Calcula diferença de tempo
        data_envio_str = resultado['data_envio']
        
        # Parse da data (formato SQLite: YYYY-MM-DD HH:MM:SS)
        data_envio = datetime.strptime(data_envio_str, '%Y-%m-%d %H:%M:%S')
        agora = datetime.now()
        diferenca = agora - data_envio
        
        horas_passadas = diferenca.total_seconds() / 3600
        pode_enviar = horas_passadas >= 24
        
        # Calcula minutos restantes para poder enviar
        if pode_enviar:
            minutos_restantes = 0
        else:
            segundos_restantes = (24 * 3600) - diferenca.total_seconds()
            minutos_restantes = int(segundos_restantes / 60)
        
        return {
            'pode_enviar': pode_enviar,
            'ultimo_envio': data_envio_str,
            'ultimo_envio_formatado': data_envio.strftime('%d/%m/%Y às %H:%M'),
            'email_ultimo': resultado['email_destinatario'],
            'horas_passadas': round(horas_passadas, 1),
            'minutos_restantes': minutos_restantes
        }
        
    except Exception as e:
        print(f"[DB] Erro ao verificar último envio: {e}")
        # Em caso de erro, permite enviar
        return {
            'pode_enviar': True,
            'ultimo_envio': None,
            'horas_passadas': None,
            'minutos_restantes': 0,
            'error': str(e)
        }


def listar_historico_envios(tipo=None, identificador=None, limite=50):
    """
    Lista o histórico de envios de e-mail.
    
    Args:
        tipo: filtrar por tipo (opcional)
        identificador: filtrar por identificador (opcional)
        limite: número máximo de registros
        
    Returns:
        Lista de envios
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query = 'SELECT * FROM email_envios_historico WHERE 1=1'
        params = []
        
        if tipo:
            query += ' AND tipo = ?'
            params.append(tipo)
            
        if identificador:
            query += ' AND identificador = ?'
            params.append(identificador)
            
        query += ' ORDER BY data_envio DESC LIMIT ?'
        params.append(limite)
        
        cursor.execute(query, params)
        
        resultados = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return resultados
        
    except Exception as e:
        print(f"[DB] Erro ao listar histórico de envios: {e}")
        return []


# =============================================================================
# INICIALIZAÇÃO
# =============================================================================
# Inicializa o banco ao importar o módulo
init_database()
