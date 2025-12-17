import json
import os
import pyodbc
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_caching import Cache
from datetime import datetime, timedelta
import re

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

# --- FUNÇÃO AUXILIAR: CÁLCULO DE VENCIMENTO ---
def calcular_vencimento_estimado(data_entrega_prevista, condicao_pagamento):
    """
    Calcula a(s) data(s) de vencimento estimada(s) com base na data de entrega prevista
    e na condição de pagamento.
    
    Retorna uma lista de tuplas: [(data_vencimento, fator_divisao), ...]
    - Para condição simples (ex: "30 DDL"): [(data + 30 dias, 1.0)]
    - Para condição parcelada (ex: "30/60/90"): [(data + 30, 0.33), (data + 60, 0.33), (data + 90, 0.34)]
    """
    if pd.isnull(data_entrega_prevista):
        return []
    
    # Garante que é datetime
    if isinstance(data_entrega_prevista, str):
        try:
            data_entrega_prevista = pd.to_datetime(data_entrega_prevista)
        except:
            return []
    
    # Valor padrão: 30 dias
    dias_padrao = 30
    
    if not condicao_pagamento or pd.isnull(condicao_pagamento):
        return [(data_entrega_prevista + timedelta(days=dias_padrao), 1.0)]
    
    condicao_str = str(condicao_pagamento).upper().strip()
    
    # Tenta extrair números da condição de pagamento
    # Padrões comuns: "30 DDL", "30/60", "30/60/90", "28 DDL", "A VISTA", etc.
    
    # Caso especial: À vista ou similar
    if 'VISTA' in condicao_str or 'ANTECIPADO' in condicao_str:
        return [(data_entrega_prevista, 1.0)]
    
    # Extrai todos os números da string
    numeros = re.findall(r'\d+', condicao_str)
    
    if not numeros:
        # Não encontrou números, usa padrão
        return [(data_entrega_prevista + timedelta(days=dias_padrao), 1.0)]
    
    # Converte para inteiros
    dias_lista = [int(n) for n in numeros if int(n) <= 365]  # Ignora números muito grandes
    
    if not dias_lista:
        return [(data_entrega_prevista + timedelta(days=dias_padrao), 1.0)]
    
    # Se tem múltiplos números, considera como parcelamento
    qtd_parcelas = len(dias_lista)
    fator = 1.0 / qtd_parcelas
    
    vencimentos = []
    for dias in dias_lista:
        data_venc = data_entrega_prevista + timedelta(days=dias)
        vencimentos.append((data_venc, fator))
    
    return vencimentos

# --- CONFIGURAÇÕES DE E-MAIL (GMAIL) ---
EMAIL_REMETENTE = 'polimaquinascompras@gmail.com'
# Senha de App que funciona (do arquivo TESTEEMAIL.PY)
SENHA_EMAIL = 'krikozmhqmzdraiu' 
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587

# --- BLOQUEIOS HARDCODED ---
BLOQUEIO_FINANCEIRO = [
    "M R FERNANDES PRADO",
    "SIRIUS ELETRONIC",
    "BAUMUELLER",
    "THE BOX"
]

BLOQUEIO_STATUS = [
    "SIRIUS ELETRONIC",
    "BAUMUELLER",
    "TELEFONICA BRASIL",
    "TELEFONICA DO BRASIL",
    "JULIANO RICARDO DOS SANTOS",
    "CIA PAULISTA FORCA E LUZ",
    "R S DE S ROCHA TECNOLOGIA LTDA",
    "M R FERNANDES PRADO - ME",
    "55.493.375 JOAO PAULO RODRIGUES DO NASCI",
    "THE BOX MOVEIS PLANEJADOS LTDA ME",
    "TEC REVEST"
]

BLOQUEIO_PERFORMANCE = [
    "THE BOX",
    "BOX MOVEIS",
    "TEC REVEST",
    "REVEST COM",
    "M R FERNANDES PRADO",
    "SIRIUS ELETRONIC"
]

# Bloqueio por codigo de fornecedor (mais confiavel)
BLOQUEIO_PERFORMANCE_CODIGOS = [
    "003905",  # THE BOX
    "002506",  # TEC REVEST
    "001206"   # SIRIUS ELETRONIC
]

# --- CONFIGURAÇÃO DO CACHE ---
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 3600
cache = Cache(app)

# --- FUNÇÃO DE CONEXÃO E DADOS ---
@cache.cached(timeout=3600, key_prefix='dados_totvs_v10_fluxocaixa')
def get_database_data():
    try:
        server = r'172.16.45.117\TOTVS' 
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str)
        
        # QUERY COMPLETA
        query = """
        SELECT 
            PC.C7_NUM      AS NumeroPedido,
            PC.C7_ITEM     AS ItemPedido,
            PC.C7_EMISSAO  AS DataEmissao,
            PC.C7_DATPRF   AS DataEntregaPrevista,
            PC.C7_TOTAL    AS ValorTotal,
            PC.C7_QUANT    AS QtdPedida,
            ISNULL(PC.C7_QUJE, 0) AS QtdEntregue,
            
            ISNULL(COND.E4_DESCRI, PC.C7_COND) AS CondicaoPagamento,
            ISNULL(NF.D1_DOC, 'Pendente')    AS NumeroNota,
            ISNULL(NF.D1_SERIE, '')          AS SerieNota, 
            NF.D1_DTDIGIT  AS DataRecebimento,
            
            ISNULL(SOL.C1_SOLICIT, 'Estoque/Direto') AS NomeSolicitante, 
            SOL.C1_EMISSAO AS DataSolicitacao,
            SOL.C1_DATPRF  AS DataNecessidade,
            PC.C7_NUMSC    AS NumeroSC,
            FORN.A2_COD    AS CodFornecedor,
            FORN.A2_NOME   AS NomeFornecedor,
            
            ISNULL(FORN.A2_TEL, '') AS TelefoneFornecedor,
            ISNULL(FORN.A2_EMAIL, '') AS EmailFornecedor,

            CASE LTRIM(RTRIM(FORN.A2_X_COMPR))
                WHEN '016' THEN 'Aline Chen'
                WHEN '007' THEN 'Hélio Doce'
                WHEN '008' THEN 'Diego Moya'
                WHEN '018' THEN 'Daniel Amaral'
                ELSE 'Outros'
            END AS NomeComprador,
            PROD.B1_COD    AS CodProduto,
            PROD.B1_DESC   AS DescricaoProduto,
            PROD.B1_GRUPO  AS GrupoProduto,
            PROD.B1_TIPO   AS TipoProduto,
            ISNULL(PROD.B1_PE, 0) AS LeadTimePadrao,
            ISNULL(PC.C7_FLUXO, '') AS FluxoCaixa

        FROM SC7010 AS PC
        LEFT JOIN SC1010 AS SOL ON PC.C7_NUMSC = SOL.C1_NUM AND PC.C7_ITEMSC = SOL.C1_ITEM AND SOL.D_E_L_E_T_ = ''
        LEFT JOIN SD1010 AS NF ON PC.C7_NUM = NF.D1_PEDIDO AND PC.C7_ITEM = NF.D1_ITEMPC AND NF.D_E_L_E_T_ = ''
        INNER JOIN SA2010 AS FORN ON PC.C7_FORNECE = FORN.A2_COD AND PC.D_E_L_E_T_ = '' AND FORN.D_E_L_E_T_ = ''
        LEFT JOIN SB1010 AS PROD ON PC.C7_PRODUTO = PROD.B1_COD AND PROD.D_E_L_E_T_ = ''
        LEFT JOIN SE4010 AS COND ON LTRIM(RTRIM(PC.C7_COND)) = LTRIM(RTRIM(COND.E4_CODIGO)) AND COND.D_E_L_E_T_ = ''
        
        WHERE PC.D_E_L_E_T_ <> '*' 
          AND PC.C7_EMISSAO >= '20240101'
          AND (PC.C7_RESIDUO = '' OR PC.C7_RESIDUO IS NULL)
          AND PC.C7_QUANT > 0
          AND PC.C7_TOTAL > 0
        """
        
        df = pd.read_sql(query, conn)
        conn.close()

        colunas_data = ['DataEmissao', 'DataEntregaPrevista', 'DataRecebimento', 'DataSolicitacao', 'DataNecessidade']
        for col in colunas_data:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], format='%Y%m%d', errors='coerce')
        
        # Limpeza robusta da coluna de nomes
        if not df.empty and 'NomeComprador' in df.columns:
            df['NomeComprador'] = df['NomeComprador'].astype(str).str.strip()

        return df
    except Exception as e:
        print(f"Erro SQL: {e}")
        return None

# =============================================================================
# FUNÇÃO OTIMIZADA: VARIAÇÃO DE PREÇO (SAVING & INFLATION)
# =============================================================================
# Arquitetura: 
#   1. SQL com OUTER APPLY busca o preço anterior PAR-A-PAR diretamente no banco
#   2. Cache de 2h para evitar queries repetidas
#   3. Filtros de data aplicados APENAS na DataEmissao (compra atual)
# =============================================================================

@cache.cached(timeout=7200, key_prefix='variacao_preco_v8_otimizado')
def get_variacao_preco_data():
    """
    Busca pedidos desde 01/01/2024 com o último preço histórico de cada produto.
    
    ESTRATÉGIA OTIMIZADA (2 queries separadas + join em Python):
    1. Query 1: Busca pedidos de 2024+ (rápido)
    2. Query 2: Busca histórico de preços usando LAG() ou agrupamento
    3. Join no pandas (muito rápido em memória)
    """
    import time
    
    print("=" * 70)
    print("[VARIAÇÃO] INICIANDO CARREGAMENTO DE DADOS")
    print("=" * 70)
    
    try:
        # =====================================================
        # ETAPA 1: CONEXÃO COM O BANCO
        # =====================================================
        print("[VARIAÇÃO] Etapa 1/5: Conectando ao banco de dados...")
        t1 = time.time()
        
        server = r'172.16.45.117\TOTVS' 
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str, timeout=60)
        
        print(f"[VARIAÇÃO] ✓ Conexão estabelecida em {time.time() - t1:.2f}s")
        
        # =====================================================
        # ETAPA 2: BUSCAR PEDIDOS DE 2024+
        # =====================================================
        print("[VARIAÇÃO] Etapa 2/5: Buscando pedidos de 2024+...")
        t2 = time.time()
        
        query_pedidos = """
        SELECT 
            PC.C7_NUM           AS NumeroPedido,
            PC.C7_ITEM          AS ItemPedido,
            PC.C7_EMISSAO       AS DataEmissao,
            PC.C7_PRODUTO       AS CodProduto,
            PROD.B1_DESC        AS DescricaoProduto,
            PROD.B1_TIPO        AS TipoProduto,
            PC.C7_QUANT         AS QtdPedida,
            PC.C7_TOTAL         AS ValorTotal,
            PC.C7_PRECO         AS PrecoUnitario,
            FORN.A2_COD         AS CodFornecedor,
            FORN.A2_NOME        AS NomeFornecedor,
            
            CASE LTRIM(RTRIM(FORN.A2_X_COMPR))
                WHEN '016' THEN 'Aline Chen'
                WHEN '007' THEN 'Hélio Doce'
                WHEN '008' THEN 'Diego Moya'
                WHEN '018' THEN 'Daniel Amaral'
                ELSE 'Outros'
            END AS NomeComprador
            
        FROM SC7010 AS PC WITH (NOLOCK)
        
        INNER JOIN SA2010 AS FORN WITH (NOLOCK)
            ON PC.C7_FORNECE = FORN.A2_COD 
            AND FORN.D_E_L_E_T_ = ''
            
        LEFT JOIN SB1010 AS PROD WITH (NOLOCK)
            ON PC.C7_PRODUTO = PROD.B1_COD 
            AND PROD.D_E_L_E_T_ = ''
        
        WHERE PC.D_E_L_E_T_ <> '*' 
          AND PC.C7_EMISSAO >= '20240101'
          AND (PC.C7_RESIDUO = '' OR PC.C7_RESIDUO IS NULL)
          AND PC.C7_QUANT > 0
          AND PC.C7_TOTAL > 0
          AND PC.C7_PRECO > 0
        """
        
        df_pedidos = pd.read_sql(query_pedidos, conn)
        print(f"[VARIAÇÃO] ✓ {len(df_pedidos)} pedidos carregados em {time.time() - t2:.2f}s")
        
        if df_pedidos.empty:
            conn.close()
            print("[VARIAÇÃO] ✗ Nenhum pedido encontrado!")
            return pd.DataFrame()
        
        # =====================================================
        # ETAPA 3: BUSCAR HISTÓRICO DE PREÇOS (query otimizada)
        # =====================================================
        print("[VARIAÇÃO] Etapa 3/5: Buscando histórico de preços...")
        t3 = time.time()
        
        # Pega lista de produtos únicos para filtrar
        produtos_unicos = df_pedidos['CodProduto'].unique().tolist()
        print(f"[VARIAÇÃO]   → {len(produtos_unicos)} produtos únicos para buscar histórico")
        
        # Data mínima dos pedidos (buscar histórico a partir de 2 anos antes)
        data_min_pedidos = df_pedidos['DataEmissao'].min() if 'DataEmissao' in df_pedidos.columns else '20240101'
        
        # OTIMIZAÇÃO: Buscar histórico filtrando por produtos que existem nos pedidos
        # E limitando a partir de 2020 (histórico mais antigo relevante)
        query_historico = """
        SELECT 
            C7_PRODUTO AS CodProduto,
            C7_EMISSAO AS DataCompra,
            C7_PRECO AS PrecoUnitario,
            C7_FORNECE AS CodFornecedor
        FROM SC7010 WITH (NOLOCK)
        WHERE D_E_L_E_T_ <> '*'
          AND C7_PRECO > 0
          AND C7_QUANT > 0
          AND (C7_RESIDUO = '' OR C7_RESIDUO IS NULL)
          AND C7_EMISSAO >= '20200101'
        """
        
        df_historico_raw = pd.read_sql(query_historico, conn)
        conn.close()
        
        print(f"[VARIAÇÃO]   → {len(df_historico_raw)} registros brutos do SQL em {time.time() - t3:.2f}s")
        
        # Filtrar apenas produtos que precisamos (muito mais rápido em pandas)
        produtos_set = set(produtos_unicos)
        df_historico = df_historico_raw[df_historico_raw['CodProduto'].isin(produtos_set)].copy()
        del df_historico_raw  # Liberar memória
        
        print(f"[VARIAÇÃO] ✓ {len(df_historico)} registros históricos filtrados (tempo total: {time.time() - t3:.2f}s)")
        
        # =====================================================
        # ETAPA 4: PROCESSAR DADOS EM PYTHON (otimizado)
        # =====================================================
        print("[VARIAÇÃO] Etapa 4/5: Processando dados em memória...")
        t4 = time.time()
        
        # Converter datas
        print("[VARIAÇÃO]   → Convertendo datas...")
        df_pedidos['DataEmissao'] = pd.to_datetime(df_pedidos['DataEmissao'], format='%Y%m%d', errors='coerce')
        df_historico['DataCompra'] = pd.to_datetime(df_historico['DataCompra'], format='%Y%m%d', errors='coerce')
        
        # Remover registros com datas nulas (necessário para o processamento)
        df_pedidos = df_pedidos.dropna(subset=['DataEmissao', 'CodProduto'])
        df_historico = df_historico.dropna(subset=['DataCompra', 'CodProduto'])
        
        print("[VARIAÇÃO]   → Criando dicionário de histórico por produto...")
        
        # Criar dicionário otimizado: para cada produto, lista ordenada de (data, preco, forn)
        historico_dict = {}
        for produto, grupo in df_historico.groupby('CodProduto'):
            # Ordenar por data e pegar arrays numpy (mais rápido)
            g = grupo.sort_values('DataCompra')
            historico_dict[produto] = {
                'datas': g['DataCompra'].values,
                'precos': g['PrecoUnitario'].values,
                'fornecedores': g['CodFornecedor'].values
            }
        
        print(f"[VARIAÇÃO]   → {len(historico_dict)} produtos no dicionário")
        print("[VARIAÇÃO]   → Buscando preço anterior para cada pedido (vetorizado)...")
        
        # Preparar colunas de resultado
        ultimos_precos = []
        ultimas_datas = []
        ultimos_forn = []
        
        # Processar em chunks para feedback de progresso
        total = len(df_pedidos)
        chunk_size = 10000
        
        for i, (_, row) in enumerate(df_pedidos.iterrows()):
            produto = row['CodProduto']
            data_atual = row['DataEmissao']
            
            if produto in historico_dict:
                hist = historico_dict[produto]
                # Buscar índices onde data < data_atual
                mask = hist['datas'] < data_atual
                if mask.any():
                    # Pegar o último (mais recente antes da data atual)
                    idx = mask.sum() - 1  # índice do último True
                    ultimos_precos.append(hist['precos'][idx])
                    ultimas_datas.append(pd.Timestamp(hist['datas'][idx]))
                    ultimos_forn.append(hist['fornecedores'][idx])
                else:
                    ultimos_precos.append(None)
                    ultimas_datas.append(None)
                    ultimos_forn.append(None)
            else:
                ultimos_precos.append(None)
                ultimas_datas.append(None)
                ultimos_forn.append(None)
            
            # Progresso a cada 10000 registros
            if (i + 1) % chunk_size == 0:
                print(f"[VARIAÇÃO]   → Processados {i+1}/{total} registros...")
        
        df_pedidos['UltimoPrecoUnitario'] = ultimos_precos
        df_pedidos['DataUltimaCompra'] = ultimas_datas
        df_pedidos['UltimoFornecedorCod'] = ultimos_forn
        
        print(f"[VARIAÇÃO] ✓ Processamento concluído em {time.time() - t4:.2f}s")
        
        # =====================================================
        # ETAPA 5: FINALIZAÇÃO
        # =====================================================
        print("[VARIAÇÃO] Etapa 5/5: Finalizando...")
        t5 = time.time()
        
        # Limpeza
        df_pedidos['NomeComprador'] = df_pedidos['NomeComprador'].astype(str).str.strip()
        
        # Adicionar nome do fornecedor anterior (simplificado)
        df_pedidos['UltimoFornecedor'] = df_pedidos['UltimoFornecedorCod'].fillna('')
        
        # Estatísticas
        com_hist = df_pedidos['UltimoPrecoUnitario'].notna().sum()
        sem_hist = len(df_pedidos) - com_hist
        
        print(f"[VARIAÇÃO] ✓ Finalizado em {time.time() - t5:.2f}s")
        print("=" * 70)
        print(f"[VARIAÇÃO] RESUMO FINAL:")
        print(f"[VARIAÇÃO]   → Total de pedidos: {len(df_pedidos)}")
        print(f"[VARIAÇÃO]   → Com histórico: {com_hist} ({100*com_hist/len(df_pedidos):.1f}%)")
        print(f"[VARIAÇÃO]   → Sem histórico: {sem_hist} ({100*sem_hist/len(df_pedidos):.1f}%)")
        print(f"[VARIAÇÃO]   → Período: {df_pedidos['DataEmissao'].min().strftime('%d/%m/%Y')} até {df_pedidos['DataEmissao'].max().strftime('%d/%m/%Y')}")
        print(f"[VARIAÇÃO]   → TEMPO TOTAL: {time.time() - t1:.2f}s")
        print("=" * 70)
        
        return df_pedidos
        
    except Exception as e:
        print(f"[VARIAÇÃO] Erro SQL: {e}")
        import traceback
        traceback.print_exc()
        return None


# --- PROCESSAMENTO DOS FILTROS ---
def aplicar_filtros_comuns(df, filtros):
    if df is None or df.empty: return pd.DataFrame()
    
    # 1. Fornecedores
    fornecedores_sel = filtros.get('fornecedores')
    if fornecedores_sel and isinstance(fornecedores_sel, list) and len(fornecedores_sel) > 0:
        df = df[df['NomeFornecedor'].isin(fornecedores_sel)]

    # 2. Busca Geral
    if filtros.get('busca_geral'):
        texto_busca = filtros['busca_geral']
        termos = list(dict.fromkeys([t.strip() for t in texto_busca.split(',') if t.strip()]))
        
        if termos:
            mascara_final = pd.Series([False] * len(df), index=df.index)
            for termo in termos:
                condicoes = (
                    df['DescricaoProduto'].str.contains(termo, case=False, na=False, regex=False) |
                    df['NumeroPedido'].astype(str).str.contains(termo, case=False, na=False, regex=False) |
                    df['CondicaoPagamento'].str.contains(termo, case=False, na=False, regex=False)
                )
                if 'CodProduto' in df.columns:
                    condicoes = condicoes | df['CodProduto'].str.contains(termo, case=False, na=False, regex=False)
                
                mascara_final = mascara_final | condicoes
            df = df[mascara_final]

    # 3. Comprador
    comprador_filtro = filtros.get('comprador')
    if comprador_filtro and comprador_filtro not in ['Todos', 'Todos os Compradores', 'None']:
        if isinstance(comprador_filtro, list):
             df = df[df['NomeComprador'].isin(comprador_filtro)]
        else:
             df = df[df['NomeComprador'].str.lower() == comprador_filtro.strip().lower()]
    
    # 4. Datas
    if filtros.get('data_inicio'):
        dt_inicio = pd.to_datetime(filtros['data_inicio'], errors='coerce')
        if pd.notnull(dt_inicio):
            df = df[df['DataEmissao'] >= dt_inicio]

    if filtros.get('data_fim'):
        dt_fim = pd.to_datetime(filtros['data_fim'], errors='coerce')
        if pd.notnull(dt_fim):
            df = df[df['DataEmissao'] <= dt_fim]
        
    return df

# --- ROTAS ---
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('dashboard')) 

# --- DASHBOARD FINANCEIRO ---
@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    # METADADOS SQL - Origem dos dados para tooltips (Raio-X)
    sql_meta = {
        # KPIs
        'total_emitido': 'Soma: SC7010.C7_TOTAL',
        'backlog': 'Soma: SC7010.C7_TOTAL onde SD1010.D1_DOC = Pendente',
        'qtd_pedidos': 'Contagem única: SC7010.C7_NUM',
        'ticket_medio': 'Cálculo: Total Emitido / Qtd Pedidos',
        
        # Tabela
        'num_pedido': 'SC7010.C7_NUM',
        'item_pedido': 'SC7010.C7_ITEM',
        'data_emissao': 'SC7010.C7_EMISSAO',
        'data_entrega_prevista': 'SC7010.C7_DATPRF',
        'status_detalhado': 'Calculado: Entregue (se D1_DTDIGIT preenchido) | Atrasado (se C7_DATPRF < Hoje) | No Prazo',
        'valor_total': 'SC7010.C7_TOTAL',
        'qtd_pedida': 'SC7010.C7_QUANT',
        'qtd_entregue': 'SC7010.C7_QUJE',
        'condicao_pagamento': 'SE4010.E4_DESCRI',
        'numero_nota': 'SD1010.D1_DOC',
        'data_recebimento': 'SD1010.D1_DTDIGIT',
        
        # Fornecedor
        'cod_fornecedor': 'SA2010.A2_COD',
        'nome_fornecedor': 'SA2010.A2_NOME',
        
        # Produto
        'cod_produto': 'SB1010.B1_COD',
        'desc_produto': 'SB1010.B1_DESC',
        
        # Comprador
        'nome_comprador': 'SA2010.A2_X_COMPR (traduzido)'
    }
    
    if request.method == 'POST':
        filtros_novos = request.form.to_dict()
        if 'fornecedores' in request.form:
            filtros_novos['fornecedores'] = request.form.getlist('fornecedores')
        if 'tipos_produto' in request.form:
            filtros_novos['tipos_produto'] = request.form.getlist('tipos_produto')
        session['filtros_dashboard'] = filtros_novos
        session.modified = True
        return redirect(url_for('dashboard'))

    if request.args:
        filtros_novos = request.args.to_dict()
        if 'fornecedores' in request.args:
            filtros_novos['fornecedores'] = request.args.getlist('fornecedores')
        if 'tipos_produto' in request.args:
            filtros_novos['tipos_produto'] = request.args.getlist('tipos_produto')
        session['filtros_dashboard'] = filtros_novos
        session.modified = True
    
    filtros = session.get('filtros_dashboard', {})
    
    filtros_template = {
        'data_inicio': filtros.get('data_inicio') or '',
        'data_fim': filtros.get('data_fim') or '',
        'busca_geral': filtros.get('busca_geral') or '',
        'comprador': filtros.get('comprador') or 'Todos',
        'fornecedores': filtros.get('fornecedores') or [],
        'tipos_produto': filtros.get('tipos_produto') or []
    }
    
    df_raw = get_database_data()
    df = aplicar_filtros_comuns(df_raw, filtros)
    
    # Aplica filtro de tipos de produto se selecionado
    tipos_produto_sel = filtros.get('tipos_produto')
    if tipos_produto_sel and isinstance(tipos_produto_sel, list) and len(tipos_produto_sel) > 0:
        if 'TipoProduto' in df.columns:
            df = df[df['TipoProduto'].isin(tipos_produto_sel)]
    
    # Não aplica BLOQUEIO_FINANCEIRO aqui - deixa aparecer na tabela e no Total Emitido
    
    if not df.empty:
        df_unique = df.drop_duplicates(subset=['NumeroPedido', 'ItemPedido'], keep='first')
    else:
        df_unique = df
    
    hoje = datetime.now()
    if not df_unique.empty:
        df_unique = df_unique.copy()
        df_unique['StatusEntrega'] = df_unique.apply(lambda row: 
            'Entregue' if pd.notnull(row['DataRecebimento']) else 
            ('Atrasado' if pd.notnull(row['DataEntregaPrevista']) and hoje > row['DataEntregaPrevista'] else 'No Prazo'), axis=1)
    
    total_compras = df_unique['ValorTotal'].sum() if not df_unique.empty else 0
    qtd_pedidos = df_unique['NumeroPedido'].nunique() if not df_unique.empty else 0
    ticket_medio = total_compras / qtd_pedidos if qtd_pedidos > 0 else 0
    
    # Cria df_pendente e AQUI aplica o BLOQUEIO_FINANCEIRO (só afeta Backlog e Previsão de Pagamentos)
    df_pendente = df_unique[(df_unique['NumeroNota'] == 'Pendente') | (df_unique['QtdEntregue'] < df_unique['QtdPedida'])].copy()
    if not df_pendente.empty:
        # Remove fornecedores bloqueados
        for termo_excluir in BLOQUEIO_FINANCEIRO:
            df_pendente = df_pendente[~df_pendente['NomeFornecedor'].str.contains(termo_excluir, case=False, na=False, regex=False)]
        # Remove pedidos com FluxoCaixa = 'N' (não entra no fluxo de caixa)
        # Usa .str.strip().str.upper() para garantir comparação correta
        if 'FluxoCaixa' in df_pendente.columns:
            df_pendente['FluxoCaixa_Limpo'] = df_pendente['FluxoCaixa'].astype(str).str.strip().str.upper()
            pedidos_excluidos = df_pendente[df_pendente['FluxoCaixa_Limpo'] == 'N']['NumeroPedido'].unique().tolist()
            if pedidos_excluidos:
                print(f"[BACKLOG] Excluindo {len(pedidos_excluidos)} pedidos com FluxoCaixa='N': {pedidos_excluidos[:10]}...")
            df_pendente = df_pendente[df_pendente['FluxoCaixa_Limpo'] != 'N']
            df_pendente = df_pendente.drop(columns=['FluxoCaixa_Limpo'], errors='ignore')
    total_pendente = df_pendente['ValorTotal'].sum() if not df_pendente.empty else 0

    # --- CÁLCULO DE PREVISÃO DE PAGAMENTOS FUTUROS ---
    previsao_pagamentos = {}
    meses_portugues = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }
    
    # Período atual para filtrar apenas pagamentos futuros
    periodo_atual = hoje.year * 100 + hoje.month
    
    if not df_pendente.empty:
        for _, row in df_pendente.iterrows():
            data_entrega = row['DataEntregaPrevista']
            condicao = row['CondicaoPagamento']
            valor_total = row['ValorTotal']
            
            # Se a data de entrega está no passado, usa a data atual como base
            # (pedido atrasado - pagamento só acontecerá quando entregar)
            if pd.notnull(data_entrega) and data_entrega < hoje:
                data_entrega = hoje
            
            vencimentos = calcular_vencimento_estimado(data_entrega, condicao)
            
            for data_venc, fator in vencimentos:
                if pd.notnull(data_venc):
                    periodo_venc = data_venc.year * 100 + data_venc.month
                    
                    # Considera apenas pagamentos do mês atual em diante
                    if periodo_venc >= periodo_atual:
                        # Agrupa por Mês/Ano
                        mes_ano_key = f"{meses_portugues[data_venc.month]}/{data_venc.year}"
                        
                        if mes_ano_key not in previsao_pagamentos:
                            previsao_pagamentos[mes_ano_key] = {'valor': 0, 'sort_key': periodo_venc}
                        
                        previsao_pagamentos[mes_ano_key]['valor'] += valor_total * fator
    
    # Ordena por período (mês/ano) e prepara para o gráfico
    previsao_ordenada = sorted(previsao_pagamentos.items(), key=lambda x: x[1]['sort_key'])
    previsao_labels = [item[0] for item in previsao_ordenada]
    previsao_values = [round(item[1]['valor'], 2) for item in previsao_ordenada]

    top_fornecedores = df_unique.groupby('NomeFornecedor')['ValorTotal'].sum().nlargest(10).reset_index() if not df_unique.empty else pd.DataFrame()
    
    df_unique = df_unique.copy()
    df_unique['MesAno'] = df_unique['DataEmissao'].dt.strftime('%m/%Y')
    vendas_mes = df_unique.groupby(['MesAno', df_unique['DataEmissao'].dt.to_period('M')])['ValorTotal'].sum().reset_index().sort_values(by='DataEmissao') if not df_unique.empty else pd.DataFrame()
    top_cond = df_unique.groupby('CondicaoPagamento')['ValorTotal'].sum().nlargest(5).reset_index() if not df_unique.empty else pd.DataFrame()

    dados = {
        'kpi_total': f"R$ {total_compras:,.2f}",
        'kpi_qtd': qtd_pedidos,
        'kpi_ticket': f"R$ {ticket_medio:,.2f}",
        'kpi_pendente': f"R$ {total_pendente:,.2f}",
        'graf_forn_labels': top_fornecedores['NomeFornecedor'].tolist() if not top_fornecedores.empty else [],
        'graf_forn_values': top_fornecedores['ValorTotal'].tolist() if not top_fornecedores.empty else [],
        'graf_mes_labels': vendas_mes['MesAno'].tolist() if not vendas_mes.empty else [],
        'graf_mes_values': vendas_mes['ValorTotal'].tolist() if not vendas_mes.empty else [],
        'graf_cond_labels': top_cond['CondicaoPagamento'].tolist() if not top_cond.empty else [],
        'graf_cond_values': top_cond['ValorTotal'].tolist() if not top_cond.empty else [],
        'previsao_labels': previsao_labels,
        'previsao_values': previsao_values,
        'tabela': df_unique.head(200).to_dict(orient='records') if not df_unique.empty else [],
        'opcoes_fornecedores': sorted(df_raw['NomeFornecedor'].dropna().unique().tolist()) if df_raw is not None else [],
        'opcoes_compradores': sorted(df_raw['NomeComprador'].dropna().unique().tolist()) if df_raw is not None else [],
        'opcoes_tipos_produto': sorted(df_raw['TipoProduto'].dropna().unique().tolist()) if df_raw is not None and 'TipoProduto' in df_raw.columns else []
    }

    return render_template('dashboard.html', user="Admin", dados=dados, filtros=filtros_template, sql_meta=sql_meta)

# --- ROTA STATUS DE PEDIDOS ---
@app.route('/status_pedidos', methods=['GET', 'POST'])
def status_pedidos():
    # METADADOS SQL - Origem dos dados para tooltips (Raio-X)
    sql_meta = {
        # KPIs
        'pedidos_aberto': 'Contagem: SC7010.C7_NUM onde C7_QUJE < C7_QUANT',
        'itens_atrasados': 'Contagem: itens com DataEntregaPrevista < Hoje',
        'eficiencia_prazo': 'Cálculo: (Abertos - Atrasados) / Abertos * 100',
        
        # Tabela
        'num_pedido': 'SC7010.C7_NUM',
        'data_emissao': 'SC7010.C7_EMISSAO',
        'data_entrega_prevista': 'SC7010.C7_DATPRF',
        'dias_atraso': 'Cálculo: Hoje - SC7010.C7_DATPRF',
        'status_detalhado': 'Calculado: Atrasado se dias > 0',
        
        # Fornecedor
        'cod_fornecedor': 'SA2010.A2_COD',
        'nome_fornecedor': 'SA2010.A2_NOME',
        'telefone_fornecedor': 'SA2010.A2_TEL',
        'email_fornecedor': 'SA2010.A2_EMAIL',
        
        # Comprador
        'nome_comprador': 'SA2010.A2_X_COMPR (traduzido)',
        
        # Produto
        'cod_produto': 'SB1010.B1_COD',
        'desc_produto': 'SB1010.B1_DESC'
    }
    
    if request.method == 'POST':
        filtros_novos = request.form.to_dict()
        if 'fornecedores' in request.form:
            filtros_novos['fornecedores'] = request.form.getlist('fornecedores')
        
        session['filtros_status'] = filtros_novos
        session.modified = True
        return redirect(url_for('status_pedidos'))

    if request.args:
        filtros_novos = request.args.to_dict()
        if 'fornecedores' in request.args:
            filtros_novos['fornecedores'] = request.args.getlist('fornecedores')
        session['filtros_status'] = filtros_novos
        session.modified = True
    
    filtros = session.get('filtros_status', {})
    
    filtros_template = {
        'data_inicio': filtros.get('data_inicio') or '',
        'data_fim': filtros.get('data_fim') or '',
        'busca_geral': filtros.get('busca_geral') or '',
        'comprador': filtros.get('comprador') or 'Todos',
        'status_grafico': filtros.get('status_grafico') or '',
        'fornecedores': filtros.get('fornecedores') or []
    }

    df_raw = get_database_data()
    df = aplicar_filtros_comuns(df_raw, filtros)
    
    if not df.empty:
        for termo_excluir in BLOQUEIO_STATUS:
            df = df[~df['NomeFornecedor'].str.contains(termo_excluir, case=False, na=False, regex=False)]
    
    hoje = datetime.now()
    
    if not df.empty:
        # Filtra apenas se ainda tiver saldo a entregar
        df_aberto = df[df['QtdEntregue'] < df['QtdPedida']].copy()
        
        df_aberto['DiasAtraso'] = df_aberto['DataEntregaPrevista'].apply(
            lambda x: (hoje - x).days if pd.notnull(x) and hoje > x else 0
        )
        
        df_aberto['StatusDetalhado'] = df_aberto['DiasAtraso'].apply(
            lambda x: 'Atrasado' if x > 0 else 'No Prazo'
        )

        status_grafico = filtros.get('status_grafico')
        if status_grafico:
             df_aberto = df_aberto[df_aberto['StatusDetalhado'] == status_grafico]

        def limpar_telefone(tel):
            if not tel: return ''
            return ''.join(filter(str.isdigit, str(tel)))

        df_aberto['TelefoneLimpo'] = df_aberto['TelefoneFornecedor'].apply(limpar_telefone)

        total_aberto = len(df_aberto)
        total_atrasado = len(df_aberto[df_aberto['StatusDetalhado'] == 'Atrasado'])
        qtd_atrasado = len(df_aberto[df_aberto['StatusDetalhado'] == 'Atrasado'])
        qtd_noprazo = len(df_aberto[df_aberto['StatusDetalhado'] == 'No Prazo'])
        
        comp_status = df_aberto.groupby(['NomeComprador', 'StatusDetalhado']).size().unstack(fill_value=0).reset_index()
        if 'Atrasado' not in comp_status.columns: comp_status['Atrasado'] = 0
        if 'No Prazo' not in comp_status.columns: comp_status['No Prazo'] = 0
        comp_status = comp_status.sort_values(by='Atrasado', ascending=False)

        lista_compradores = sorted(df_raw['NomeComprador'].dropna().astype(str).str.strip().unique().tolist())

        dados = {
            'kpi_aberto': total_aberto,
            'kpi_atrasado': total_atrasado,
            'graf_status_labels': ['Atrasado', 'No Prazo'],
            'graf_status_values': [qtd_atrasado, qtd_noprazo],
            'graf_comp_labels': comp_status['NomeComprador'].tolist(),
            'graf_comp_atrasado': comp_status['Atrasado'].tolist(),
            'graf_comp_prazo': comp_status['No Prazo'].tolist(),
            'tabela': df_aberto.sort_values(by='DiasAtraso', ascending=False).head(300).to_dict(orient='records'),
            'opcoes_compradores': lista_compradores,
            'opcoes_fornecedores': sorted(df_raw['NomeFornecedor'].dropna().unique().tolist())
        }
    else:
        lista_compradores = []
        lista_fornecedores = []
        if df_raw is not None and not df_raw.empty:
            lista_compradores = sorted(df_raw['NomeComprador'].dropna().astype(str).str.strip().unique().tolist())
            lista_fornecedores = sorted(df_raw['NomeFornecedor'].dropna().unique().tolist())
            
        dados = {
            'kpi_aberto': 0, 'kpi_atrasado': 0, 'tabela': [], 
            'opcoes_compradores': lista_compradores,
            'opcoes_fornecedores': lista_fornecedores
        }

    return render_template('status.html', user="Admin", dados=dados, filtros=filtros_template, sql_meta=sql_meta)

# --- NOVA ROTA DE ENVIO DE E-MAIL (CORRIGIDA PARA MULTIPLOS EMAILS) ---
@app.route('/enviar_cobranca', methods=['POST'])
def enviar_cobranca():
    try:
        dados = request.json
        # Recebe string bruta (ex: "a@a.com; b@b.com")
        destinatario_raw = dados.get('email', '')
        pedido = dados.get('pedido')
        fornecedor = dados.get('fornecedor')
        data_prevista = dados.get('data')

        if not destinatario_raw or not pedido:
            return jsonify({'success': False, 'message': 'Dados incompletos ou e-mail vazio.'})

        # --- CORREÇÃO DO ERRO 553 ---
        # 1. Troca ; por ,
        # 2. Separa em lista
        # 3. Remove espaços
        destinatarios_lista = [e.strip() for e in destinatario_raw.replace(';', ',').split(',') if e.strip()]

        if not destinatarios_lista:
             return jsonify({'success': False, 'message': 'Nenhum e-mail válido encontrado.'})

        # Configuração do E-mail
        msg = MIMEMultipart()
        msg['From'] = EMAIL_REMETENTE
        # O cabeçalho 'To' é apenas visual, pode ter vírgulas
        msg['To'] = ', '.join(destinatarios_lista)
        msg['Subject'] = f"Cobrança de Entrega - Pedido {pedido} - {fornecedor}"

        # Verifica se é cobrança de item específico ou pedido completo
        produto = dados.get('produto')
        
        if produto:
            # Cobrança de item específico
            corpo = f"""Prezados, {fornecedor}

Solicito atualização urgente sobre o ITEM específico abaixo:

Pedido: {pedido}
Produto: {produto}
Previsão de Entrega: {data_prevista}

Por favor, informar previsão de entrega e status atual deste item.

Fico no aguardo.
"""
        else:
            # Cobrança de pedido completo
            corpo = f"""Prezados, {fornecedor}

Solicito atualização sobre o pedido {pedido}, que tinha previsão para {data_prevista}.

Fico no aguardo.
"""
        msg.attach(MIMEText(corpo, 'plain'))

        # Conexão com Gmail (TLS porta 587)
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_REMETENTE, SENHA_EMAIL)
        
        # Envia para a LISTA de destinatários (não string)
        text = msg.as_string()
        server.sendmail(EMAIL_REMETENTE, destinatarios_lista, text)
        server.quit()

        return jsonify({'success': True, 'message': 'E-mail enviado com sucesso!'})

    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return jsonify({'success': False, 'message': f'Erro: {str(e)}'})

@app.route('/limpar_filtros')
def limpar_filtros():
    referrer = request.referrer or ''
    if 'status_pedidos' in referrer:
        session.pop('filtros_status', None)
        return redirect(url_for('status_pedidos'))
    elif 'performance' in referrer:
        session.pop('filtros_performance', None)
        return redirect(url_for('performance'))
    else:
        session.pop('filtros_dashboard', None)
        return redirect(url_for('dashboard'))

# --- NOVA ROTA: BUSCAR ITENS DE UM PEDIDO ---
@app.route('/detalhes_pedido/<numero_pedido>')
def detalhes_pedido(numero_pedido):
    try:
        df_raw = get_database_data()
        
        if df_raw is None or df_raw.empty:
            return jsonify({'success': False, 'message': 'Sem dados disponíveis.'})
        
        # Filtra apenas os itens deste pedido
        df_pedido = df_raw[df_raw['NumeroPedido'] == numero_pedido].copy()
        
        if df_pedido.empty:
            return jsonify({'success': False, 'message': 'Pedido não encontrado.'})
        
        hoje = datetime.now()
        
        # Calcula status e saldo para cada item
        df_pedido['SaldoQtd'] = df_pedido['QtdPedida'] - df_pedido['QtdEntregue']
        df_pedido['SaldoValor'] = (df_pedido['SaldoQtd'] / df_pedido['QtdPedida']) * df_pedido['ValorTotal']
        
        df_pedido['DiasAtraso'] = df_pedido['DataEntregaPrevista'].apply(
            lambda x: (hoje - x).days if pd.notnull(x) and hoje > x else 0
        )
        
        df_pedido['StatusItem'] = df_pedido.apply(lambda row:
            'Entregue' if row['SaldoQtd'] <= 0 else
            ('Atrasado' if row['DiasAtraso'] > 0 else 'No Prazo'), axis=1
        )
        
        # Formata as datas para exibição
        df_pedido['DataEmissaoFormatada'] = df_pedido['DataEmissao'].dt.strftime('%d/%m/%Y')
        df_pedido['DataEntregaPrevistaFormatada'] = df_pedido['DataEntregaPrevista'].dt.strftime('%d/%m/%Y')
        
        # Converte para lista de dicionários
        itens = df_pedido.to_dict(orient='records')
        
        # Limpa valores NaN/NaT para JSON
        for item in itens:
            for key, value in item.items():
                if pd.isna(value):
                    item[key] = ''
                elif isinstance(value, pd.Timestamp):
                    item[key] = value.strftime('%d/%m/%Y') if pd.notna(value) else ''
        
        return jsonify({
            'success': True,
            'itens': itens,
            'fornecedor': itens[0]['NomeFornecedor'] if itens else '',
            'email': itens[0]['EmailFornecedor'] if itens else ''
        })
        
    except Exception as e:
        print(f"Erro ao buscar detalhes do pedido: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/performance', methods=['GET', 'POST'])
def performance():
    try:
        # METADADOS SQL - Origem dos dados para tooltips
        sql_meta = {
            # KPIs
            'lead_time_interno': 'Cálculo: SC7010.C7_EMISSAO - SC1010.C1_EMISSAO',
            'otd': 'Cálculo: SD1010.D1_DTDIGIT - SC1010.C1_DATPRF',
            'total_pedidos': 'Contagem única: SC7010.C7_NUM',
            
            # Tabela Fornecedores
            'cod_fornecedor': 'SA2010.A2_COD',
            'nome_fornecedor': 'SA2010.A2_NOME',
            'qtd_atrasos': 'Contagem: SC7010.C7_NUM onde OTD > 0',
            'dias_atraso': 'Média: SD1010.D1_DTDIGIT - SC1010.C1_DATPRF',
            'lead_time_forn': 'Média: SD1010.D1_DTDIGIT - SC7010.C7_EMISSAO',
            
            # Tabela Produtos
            'cod_produto': 'SB1010.B1_COD',
            'desc_produto': 'SB1010.B1_DESC',
            
            # Tabela Pedidos
            'num_pedido': 'SC7010.C7_NUM',
            'data_necessidade': 'SC1010.C1_DATPRF',
            'data_recebimento': 'SD1010.D1_DTDIGIT'
        }
        
        # Carrega os filtros da sessão específicos para performance
        filtros = session.get('filtros_performance', {})
        
        # Se foi POST, atualiza os filtros
        if request.method == 'POST':
            filtros = {
                'comprador': request.form.get('comprador', ''),
                'busca_geral': request.form.get('busca_geral', ''),
                'data_inicio': request.form.get('data_inicio', ''),
                'data_fim': request.form.get('data_fim', '')
            }
            # Captura fornecedores múltiplos
            if 'fornecedores' in request.form:
                filtros['fornecedores'] = request.form.getlist('fornecedores')
            session['filtros_performance'] = filtros
            session.modified = True
            return redirect(url_for('performance'))
        
        # Busca dados do banco
        df_raw = get_database_data()
        if df_raw is None or df_raw.empty:
            return render_template('performance.html', 
                                 compradores=[], fornecedores=[],
                                 filtros=filtros, error="Nenhum dado disponível")
        
        # Remove fornecedores bloqueados por CODIGO (mais confiavel que nome)
        df_raw_filtrado = df_raw.copy()
        if not df_raw_filtrado.empty:
            # Primeiro remove por codigo (prioritario)
            df_raw_filtrado = df_raw_filtrado[~df_raw_filtrado['CodFornecedor'].isin(BLOQUEIO_PERFORMANCE_CODIGOS)]
            # Depois por nome como backup
            for termo_excluir in BLOQUEIO_PERFORMANCE:
                df_raw_filtrado = df_raw_filtrado[~df_raw_filtrado['NomeFornecedor'].str.contains(termo_excluir, case=False, na=False, regex=False)]
        
        # Aplica filtros comuns no dataframe ja sem os fornecedores bloqueados
        df = aplicar_filtros_comuns(df_raw_filtrado, filtros)
        
        # Remove registros com DataNecessidade anterior à DataSolicitacao (dados inconsistentes)
        # Isso evita que SCs que já nasceram "atrasadas" prejudiquem os indicadores
        if not df.empty:
            # Conta antes
            antes = len(df)
            # Remove onde DataNecessidade é anterior a DataSolicitacao
            df = df[~((df['DataNecessidade'].notna()) & (df['DataSolicitacao'].notna()) & (df['DataNecessidade'] < df['DataSolicitacao']))]
            # Remove também onde DataNecessidade é anterior a DataEmissao do pedido (caso não tenha SC)
            df = df[~((df['DataNecessidade'].notna()) & (df['DataEmissao'].notna()) & (df['DataNecessidade'] < df['DataEmissao']))]
            depois = len(df)
            print(f"[PERFORMANCE] Filtro DataNecessidade: removidos {antes - depois} registros inconsistentes")
        
        # Filtra apenas produtos dos tipos permitidos (ME, AI, IN, MC)
        if not df.empty and 'TipoProduto' in df.columns:
            tipos_permitidos = ['ME', 'AI', 'IN', 'MC']
            # Limpa e padroniza a coluna TipoProduto
            df['TipoProduto_Limpo'] = df['TipoProduto'].astype(str).str.strip().str.upper()
            df = df[df['TipoProduto_Limpo'].isin(tipos_permitidos)]
            # Remove a coluna temporária
            df = df.drop(columns=['TipoProduto_Limpo'], errors='ignore')
        
        # Calcula Lead Time Interno (Requisição → Pedido) em dias
        df['LeadTime_Interno'] = (df['DataEmissao'] - df['DataSolicitacao']).dt.days
        
        # Calcula Lead Time Fornecedor (Pedido → Entrega) apenas para pedidos entregues
        df['LeadTime_Fornecedor'] = (df['DataRecebimento'] - df['DataEmissao']).dt.days
        
        # Calcula OTD (On-Time Delivery): Data Necessidade SC x Data Recebimento NF
        df['OTD'] = (df['DataRecebimento'] - df['DataNecessidade']).dt.days
        
        # Remove valores negativos (dados inconsistentes)
        df.loc[df['LeadTime_Interno'] < 0, 'LeadTime_Interno'] = None
        df.loc[df['LeadTime_Fornecedor'] < 0, 'LeadTime_Fornecedor'] = None
        
        # Conta entregas no prazo - MESMA REGRA do gráfico mensal
        # Considera apenas itens com DataRecebimento E DataNecessidade válidos
        df_otd_valido = df[(df['DataRecebimento'].notna()) & (df['DataNecessidade'].notna())]
        entregas_no_prazo = (df_otd_valido['OTD'] <= 0).sum()
        total_entregue = len(df_otd_valido)
        percentual_otd = round((entregas_no_prazo / total_entregue * 100), 1) if total_entregue > 0 else 0
        
        # KPIs gerais
        # Conta pedidos únicos (não itens)
        total_pedidos_unicos = df['NumeroPedido'].nunique()
        
        # Índice de atendimento anual (entregas no prazo / total de entregas)
        indice_atendimento_anual = percentual_otd
        
        kpis = {
            'media_interno': round(df['LeadTime_Interno'].mean(), 1) if df['LeadTime_Interno'].notna().any() else 0,
            'media_fornecedor': round(df['LeadTime_Fornecedor'].mean(), 1) if df['LeadTime_Fornecedor'].notna().any() else 0,
            'media_otd': round(df['OTD'].mean(), 1) if df['OTD'].notna().any() else 0,
            'percentual_otd': percentual_otd,
            'total_pedidos': total_pedidos_unicos,
            'indice_atendimento_anual': indice_atendimento_anual
        }
        
        # Agrupa por comprador para gráficos
        df_comprador = df.groupby('NomeComprador').agg({
            'LeadTime_Interno': 'mean',
            'LeadTime_Fornecedor': 'mean',
            'NumeroPedido': 'count'
        }).reset_index()
        
        df_comprador.columns = ['Comprador', 'LeadTime_Interno', 'LeadTime_Fornecedor', 'TotalPedidos']
        df_comprador = df_comprador.round(1)
        
        # Prepara dados para gráficos
        compradores_chart = df_comprador['Comprador'].tolist()
        leadtime_interno_chart = df_comprador['LeadTime_Interno'].fillna(0).tolist()
        leadtime_fornecedor_chart = df_comprador['LeadTime_Fornecedor'].fillna(0).tolist()
        
        # ANÁLISE MENSAL DE ATENDIMENTO (para o gráfico temporal)
        # IMPORTANTE: Usa a MESMA BASE do cálculo do OTD geral para garantir consistência
        # Se somar os percentuais mensais e dividir pela qtd de meses, deve dar próximo ao OTD geral
        
        # Usa df_otd_valido (mesma base do KPI) para garantir consistência
        if not df_otd_valido.empty:
            df_mensal = df_otd_valido.copy()
            
            # Agrupa por mês de EMISSÃO do pedido (mantém o critério do filtro de período)
            df_mensal['MesAno'] = df_mensal['DataEmissao'].dt.to_period('M')
            
            # Classifica cada entrega como No Prazo ou Atrasada
            df_mensal['StatusAtendimento'] = df_mensal['OTD'].apply(
                lambda x: 'No Prazo' if x <= 0 else 'Atrasado'
            )
            
            # Agrupa por mês e status
            atendimento_mensal = df_mensal.groupby(['MesAno', 'StatusAtendimento']).size().unstack(fill_value=0)
            
            # Garante que ambas as colunas existam
            if 'No Prazo' not in atendimento_mensal.columns:
                atendimento_mensal['No Prazo'] = 0
            if 'Atrasado' not in atendimento_mensal.columns:
                atendimento_mensal['Atrasado'] = 0
            
            # Calcula percentuais (sempre somará 100% por mês)
            atendimento_mensal['Total'] = atendimento_mensal['No Prazo'] + atendimento_mensal['Atrasado']
            atendimento_mensal['%_NoPrazo'] = (atendimento_mensal['No Prazo'] / atendimento_mensal['Total'] * 100).round(1)
            atendimento_mensal['%_Atrasado'] = (atendimento_mensal['Atrasado'] / atendimento_mensal['Total'] * 100).round(1)
            
            # Ordena por período
            atendimento_mensal = atendimento_mensal.sort_index()
            
            # Converte para listas para o gráfico
            mensal_labels = [str(periodo) for periodo in atendimento_mensal.index]
            mensal_noprazo = atendimento_mensal['%_NoPrazo'].tolist()
            mensal_atrasado = atendimento_mensal['%_Atrasado'].tolist()
            
            # DEBUG: Verifica se a média ponderada bate com o OTD geral
            total_entregas_mensal = atendimento_mensal['Total'].sum()
            total_noprazo_mensal = atendimento_mensal['No Prazo'].sum()
            otd_recalculado = round((total_noprazo_mensal / total_entregas_mensal * 100), 1) if total_entregas_mensal > 0 else 0
            print(f"[DEBUG OTD] OTD Geral: {percentual_otd}% | OTD Recalculado dos Meses: {otd_recalculado}% | Diferença: {abs(percentual_otd - otd_recalculado)}%")
        else:
            mensal_labels = []
            mensal_noprazo = []
            mensal_atrasado = []
        
        # TODOS OS FORNECEDORES COM ATRASOS (não limita a 10)
        # Usa df_otd_valido que já tem OTD calculado e StatusAtendimento do gráfico mensal
        df_atrasados = df_otd_valido[df_otd_valido['OTD'] > 0].copy() if not df_otd_valido.empty else pd.DataFrame()
        
        # Remove fornecedores bloqueados dos atrasados tambem
        if not df_atrasados.empty:
            df_atrasados = df_atrasados[~df_atrasados['CodFornecedor'].isin(BLOQUEIO_PERFORMANCE_CODIGOS)]
        
        if not df_atrasados.empty:
            top_fornecedores_atrasados = df_atrasados.groupby(['CodFornecedor', 'NomeFornecedor']).agg({
                'NumeroPedido': 'count',
                'OTD': 'mean',
                'LeadTime_Fornecedor': 'mean'
            }).reset_index()
            top_fornecedores_atrasados.columns = ['CodFornecedor', 'Fornecedor', 'TotalAtrasos', 'MediaDiasAtraso', 'LeadTimeMedio']
            top_fornecedores_atrasados['MediaDiasAtraso'] = top_fornecedores_atrasados['MediaDiasAtraso'].abs().round(1)
            top_fornecedores_atrasados['LeadTimeMedio'] = top_fornecedores_atrasados['LeadTimeMedio'].round(1)
            top_fornecedores_atrasados = top_fornecedores_atrasados.sort_values('TotalAtrasos', ascending=False)
        else:
            top_fornecedores_atrasados = pd.DataFrame()
        
        # TODOS OS PRODUTOS COM ATRASOS (com código e dias de atraso)
        if not df_atrasados.empty:
            top_produtos_atrasados = df_atrasados.groupby(['CodProduto', 'TipoProduto', 'DescricaoProduto']).agg({
                'NumeroPedido': 'count',
                'OTD': 'mean'
            }).reset_index()
            top_produtos_atrasados.columns = ['CodProduto', 'TipoProduto', 'Produto', 'TotalAtrasos', 'MediaDiasAtraso']
            top_produtos_atrasados['MediaDiasAtraso'] = top_produtos_atrasados['MediaDiasAtraso'].abs().round(1)
            top_produtos_atrasados = top_produtos_atrasados.sort_values('TotalAtrasos', ascending=False)
        else:
            top_produtos_atrasados = pd.DataFrame()
        
        # ÚLTIMOS 50 PEDIDOS ATRASADOS (os piores)
        if not df_atrasados.empty:
            pedidos_atrasados = df_atrasados[['NumeroPedido', 'CodFornecedor', 'NomeFornecedor', 
                                              'CodProduto', 'DescricaoProduto', 
                                              'DataNecessidade', 'DataRecebimento', 'OTD']].copy()
            # Trata valores NaN antes de converter para int
            pedidos_atrasados['DiasAtraso'] = pedidos_atrasados['OTD'].fillna(0).abs().round(0).astype(int)
            pedidos_atrasados = pedidos_atrasados.sort_values('DiasAtraso', ascending=False).head(50)
            pedidos_atrasados_lista = pedidos_atrasados.to_dict(orient='records')
        else:
            pedidos_atrasados_lista = []
        
        # Listas para filtros (sempre usa df_raw para ter todas as opções)
        compradores = sorted(df_raw['NomeComprador'].dropna().unique().tolist())
        # Fornecedores: remove os bloqueados da lista de opções do filtro
        df_fornecedores_opcoes = df_raw.copy()
        for termo_excluir in BLOQUEIO_PERFORMANCE:
            df_fornecedores_opcoes = df_fornecedores_opcoes[~df_fornecedores_opcoes['NomeFornecedor'].str.contains(termo_excluir, case=False, na=False, regex=False)]
        fornecedores = sorted(df_fornecedores_opcoes['NomeFornecedor'].dropna().unique().tolist())
        
        # Prepara filtros para o template com valores padrão
        filtros_template = {
            'data_inicio': filtros.get('data_inicio') or '',
            'data_fim': filtros.get('data_fim') or '',
            'busca_geral': filtros.get('busca_geral') or '',
            'comprador': filtros.get('comprador') or '',
            'fornecedores': filtros.get('fornecedores') or []
        }
        
        return render_template('performance.html',
                             kpis=kpis,
                             sql_meta=sql_meta,
                             compradores_chart=json.dumps(compradores_chart),
                             leadtime_interno_chart=json.dumps(leadtime_interno_chart),
                             leadtime_fornecedor_chart=json.dumps(leadtime_fornecedor_chart),
                             mensal_labels=json.dumps(mensal_labels),
                             mensal_noprazo=json.dumps(mensal_noprazo),
                             mensal_atrasado=json.dumps(mensal_atrasado),
                             top_fornecedores_atrasados=top_fornecedores_atrasados.to_dict(orient='records'),
                             top_produtos_atrasados=top_produtos_atrasados.to_dict(orient='records'),
                             pedidos_atrasados=pedidos_atrasados_lista,
                             compradores=compradores,
                             fornecedores=fornecedores,
                             filtros=filtros_template)
    
    except Exception as e:
        print(f"Erro na rota performance: {e}")
        return render_template('performance.html', 
                             compradores=[], fornecedores=[],
                             filtros={}, error=str(e))

@app.route('/api/detalhes_previsao/<path:mes_ano>')
def detalhes_previsao_pagamento(mes_ano):
    """Retorna detalhes dos pedidos pendentes que compõem a previsão de pagamento de um mês"""
    import traceback
    from urllib.parse import unquote
    
    try:
        # Decodifica a URL (pode conter caracteres especiais)
        mes_ano = unquote(mes_ano)
        print(f"[API] Buscando detalhes para: {mes_ano}")
        
        filtros = session.get('filtros_dashboard', {})
        df_raw = get_database_data()
        
        if df_raw is None or df_raw.empty:
            return jsonify({'success': False, 'message': 'Sem dados disponíveis'})
        
        # Aplica os mesmos filtros do dashboard
        df = aplicar_filtros_comuns(df_raw, filtros)
        
        # Aplica filtro de tipos de produto
        tipos_produto_sel = filtros.get('tipos_produto')
        if tipos_produto_sel and isinstance(tipos_produto_sel, list) and len(tipos_produto_sel) > 0:
            if 'TipoProduto' in df.columns:
                df = df[df['TipoProduto'].isin(tipos_produto_sel)]
        
        # Remove bloqueios financeiros
        if not df.empty:
            for termo_excluir in BLOQUEIO_FINANCEIRO:
                df = df[~df['NomeFornecedor'].str.contains(termo_excluir, case=False, na=False, regex=False)]
        
        if df.empty:
            return jsonify({'success': False, 'message': 'Nenhum dado encontrado'})
        
        df_unique = df.drop_duplicates(subset=['NumeroPedido', 'ItemPedido'], keep='first')
        
        # Filtra apenas pedidos pendentes
        df_pendente = df_unique[(df_unique['NumeroNota'] == 'Pendente') | (df_unique['QtdEntregue'] < df_unique['QtdPedida'])].copy()
        
        # Remove pedidos com FluxoCaixa = 'N'
        if not df_pendente.empty and 'FluxoCaixa' in df_pendente.columns:
            df_pendente['FluxoCaixa_Limpo'] = df_pendente['FluxoCaixa'].astype(str).str.strip().str.upper()
            df_pendente = df_pendente[df_pendente['FluxoCaixa_Limpo'] != 'N']
            df_pendente = df_pendente.drop(columns=['FluxoCaixa_Limpo'], errors='ignore')
        
        if df_pendente.empty:
            return jsonify({'success': True, 'pedidos': [], 'message': 'Nenhum pedido pendente para este período'})
        
        # Dicionário para armazenar pedidos do mês solicitado
        pedidos_mes = []
        hoje = pd.Timestamp.now()  # Usa pd.Timestamp para consistência
        periodo_atual = hoje.year * 100 + hoje.month
        meses_portugues = {
            1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
            7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
        }
        
        erros_processamento = 0
        for idx, row in df_pendente.iterrows():
            try:
                data_entrega = row.get('DataEntregaPrevista')
                condicao = row.get('CondicaoPagamento')
                
                # Converte para Timestamp se necessário
                if pd.notnull(data_entrega):
                    if not isinstance(data_entrega, pd.Timestamp):
                        try:
                            data_entrega = pd.Timestamp(data_entrega)
                        except:
                            data_entrega = hoje
                else:
                    data_entrega = hoje
                
                # Se a data de entrega está no passado, usa a data atual
                if data_entrega < hoje:
                    data_entrega_calc = hoje
                else:
                    data_entrega_calc = data_entrega
                
                vencimentos = calcular_vencimento_estimado(data_entrega_calc, condicao)
                
                # Se não conseguiu calcular vencimentos, pula este pedido
                if not vencimentos:
                    continue
                
                for data_venc, fator in vencimentos:
                    if data_venc is not None:
                        try:
                            periodo_venc = data_venc.year * 100 + data_venc.month
                        except:
                            continue
                        
                        # Só considera pagamentos futuros
                        if periodo_venc >= periodo_atual:
                            mes_ano_key = f"{meses_portugues.get(data_venc.month, 'jan')}/{data_venc.year}"
                            
                            # Se é o mês solicitado, adiciona à lista
                            if mes_ano_key == mes_ano:
                                # Garante que todos os valores são convertíveis
                                try:
                                    valor_total = float(row['ValorTotal']) if pd.notnull(row.get('ValorTotal')) else 0.0
                                except:
                                    valor_total = 0.0
                                
                                valor_parcela = valor_total * fator
                                
                                # Formata condição de pagamento (pode ser None)
                                cond_pag = str(condicao) if pd.notnull(condicao) else 'Não definida'
                                
                                # Formata data de entrega prevista original
                                data_entrega_orig = row.get('DataEntregaPrevista')
                                data_entrega_str = 'Não definida'
                                if pd.notnull(data_entrega_orig):
                                    try:
                                        if hasattr(data_entrega_orig, 'strftime'):
                                            data_entrega_str = data_entrega_orig.strftime('%d/%m/%Y')
                                        else:
                                            data_entrega_str = str(data_entrega_orig)[:10]
                                    except:
                                        data_entrega_str = 'Não definida'
                                
                                # Formata data de vencimento
                                try:
                                    data_venc_str = data_venc.strftime('%d/%m/%Y')
                                except:
                                    data_venc_str = 'N/A'
                                
                                pedidos_mes.append({
                                    'NumeroPedido': str(row.get('NumeroPedido', 'N/A')),
                                    'ItemPedido': str(row.get('ItemPedido', 'N/A')),
                                    'NomeFornecedor': str(row.get('NomeFornecedor', 'N/A')) if pd.notnull(row.get('NomeFornecedor')) else 'N/A',
                                    'CodFornecedor': str(row.get('CodFornecedor', 'N/A')) if pd.notnull(row.get('CodFornecedor')) else 'N/A',
                                    'DescricaoProduto': str(row.get('DescricaoProduto', 'N/A')) if pd.notnull(row.get('DescricaoProduto')) else 'N/A',
                                    'ValorTotal': valor_total,
                                    'ValorParcela': valor_parcela,
                                    'CondicaoPagamento': cond_pag,
                                    'DataEntregaPrevista': data_entrega_str,
                                    'DataVencimento': data_venc_str,
                                    'Fator': f"{fator*100:.0f}%"
                                })
            except Exception as row_error:
                erros_processamento += 1
                print(f"Erro ao processar linha {idx}: {row_error}")
                continue
        
        if erros_processamento > 0:
            print(f"[API] Total de erros de processamento: {erros_processamento}")
        
        if not pedidos_mes:
            return jsonify({'success': True, 'pedidos': [], 'message': f'Nenhum pagamento previsto para {mes_ano}'})
        
        # Calcula total do mês
        total_mes = sum(p['ValorParcela'] for p in pedidos_mes)
        
        # Ordena pedidos por fornecedor e valor (maiores primeiro)
        pedidos_mes_ordenados = sorted(pedidos_mes, key=lambda x: (-x['ValorParcela'], x['NomeFornecedor']))
        
        print(f"[API] Sucesso: {len(pedidos_mes)} pedidos, total R$ {total_mes:.2f}")
        
        return jsonify({
            'success': True,
            'mes': mes_ano,
            'pedidos': pedidos_mes_ordenados,
            'total': round(total_mes, 2)
        })
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"[API] Erro em detalhes_previsao_pagamento: {e}")
        print(f"[API] Traceback: {error_details}")
        return jsonify({'success': False, 'message': f'Erro ao processar: {str(e)}'})

@app.route('/performance/detalhes_mes/<mes_ano>')
def detalhes_mes_performance(mes_ano):
    """Retorna detalhes de fornecedores atrasados em um mês específico"""
    try:
        filtros = session.get('filtros_performance', {})
        df = get_database_data()
        
        if df.empty:
            return jsonify({'success': False, 'message': 'Sem dados disponíveis'})
        
        # Remove fornecedores bloqueados por codigo
        df = df[~df['CodFornecedor'].isin(BLOQUEIO_PERFORMANCE_CODIGOS)]
        
        # Remove por nome também
        for termo_excluir in BLOQUEIO_PERFORMANCE:
            df = df[~df['NomeFornecedor'].str.contains(termo_excluir, case=False, na=False, regex=False)]
        
        # Remove registros com DataNecessidade anterior à DataSolicitacao (dados inconsistentes)
        df = df[~((df['DataNecessidade'].notna()) & (df['DataSolicitacao'].notna()) & (df['DataNecessidade'] < df['DataSolicitacao']))]
        
        # Aplica os mesmos filtros da tela principal
        if filtros.get('comprador'):
            df = df[df['NomeComprador'] == filtros['comprador']]
        if filtros.get('fornecedor'):
            df = df[df['NomeFornecedor'] == filtros['fornecedor']]
        if filtros.get('data_inicial'):
            data_inicial = pd.to_datetime(filtros['data_inicial'])
            df = df[df['DataEmissao'] >= data_inicial]
        if filtros.get('data_final'):
            data_final = pd.to_datetime(filtros['data_final'])
            df = df[df['DataEmissao'] <= data_final]
        
        # Calcula OTD
        df['OTD'] = (df['DataRecebimento'] - df['DataNecessidade']).dt.days
        
        # Filtra apenas entregas do mês solicitado
        df_entregue = df[df['DataRecebimento'].notna()].copy()
        df_entregue['MesAno'] = df_entregue['DataRecebimento'].dt.to_period('M').astype(str)
        df_mes = df_entregue[df_entregue['MesAno'] == mes_ano]
        
        if df_mes.empty:
            return jsonify({'success': False, 'message': 'Sem entregas neste mês'})
        
        # Filtra apenas os atrasados
        df_atrasados = df_mes[df_mes['OTD'] > 0].copy()
        
        if df_atrasados.empty:
            return jsonify({'success': True, 'fornecedores': [], 'message': 'Nenhum atraso neste mês! 🎉'})
        
        # Agrupa por fornecedor
        fornecedores_atrasados = df_atrasados.groupby('NomeFornecedor').agg({
            'NumeroPedido': 'count',
            'OTD': 'mean'
        }).reset_index()
        
        fornecedores_atrasados.columns = ['Fornecedor', 'QtdAtrasos', 'MediaDias']
        fornecedores_atrasados['MediaDias'] = fornecedores_atrasados['MediaDias'].round(1)
        fornecedores_atrasados = fornecedores_atrasados.sort_values('QtdAtrasos', ascending=False)
        
        return jsonify({
            'success': True,
            'mes': mes_ano,
            'fornecedores': fornecedores_atrasados.to_dict(orient='records'),
            'total_atrasados': len(df_atrasados),
            'total_entregas': len(df_mes)
        })
        
    except Exception as e:
        print(f"Erro em detalhes_mes_performance: {e}")
        return jsonify({'success': False, 'message': str(e)})


# ==================== VARIAÇÃO DE PREÇO (SAVING & INFLATION) ====================
@app.route('/variacao_preco', methods=['GET', 'POST'])
def variacao_preco():
    """
    Análise de Variação de Preço - Saving vs Inflação.
    
    LÓGICA:
    1. Dados vêm do cache (pedidos 2024+ com histórico via OUTER APPLY)
    2. Filtros de DATA aplicados APENAS em DataEmissao (compra atual)
    3. DataUltimaCompra NÃO é filtrada (pode ser de qualquer ano)
    4. Cálculos de variação feitos após filtrar
    """
    df_raw = get_variacao_preco_data()
    
    # Template de erro
    template_erro = {
        'erro': "Não foi possível carregar os dados.",
        'total_saving': 0, 'total_inflacao': 0, 'saldo_final': 0,
        'count_saving': 0, 'count_inflacao': 0, 'count_estavel': 0,
        'items': [], 'compradores': [], 'tipos_produto': [],
        'chart_compradores': {'labels': [], 'valores': []},
        'chart_meses': {'labels': [], 'valores': []},
        'total_items': 0, 'comprador_selecionado': 'Todos',
        'tipo_selecionado': 'Todos', 'data_inicio': '', 'data_fim': '',
        'classificacao_selecionada': 'Todos'
    }
    
    if df_raw is None or df_raw.empty:
        return render_template('variacao.html', **template_erro)
    
    # =====================================================
    # PASSO 1: Capturar filtros do formulário
    # =====================================================
    comprador_filtro = request.form.get('comprador', 'Todos')
    tipo_filtro = request.form.get('tipo_produto', 'Todos')
    data_inicio = request.form.get('data_inicio', '')
    data_fim = request.form.get('data_fim', '')
    classificacao_filtro = request.form.get('classificacao', 'Todos')
    
    # Opções para dropdowns (do dataset completo)
    compradores = sorted(df_raw['NomeComprador'].dropna().unique().tolist())
    tipos_produto = sorted(df_raw['TipoProduto'].dropna().astype(str).unique().tolist())
    
    # =====================================================
    # PASSO 2: Filtrar APENAS por DataEmissao (compra atual)
    # A coluna DataUltimaCompra NÃO é filtrada!
    # =====================================================
    df = df_raw.copy()
    
    # Filtro: Data Início (apenas DataEmissao)
    if data_inicio and data_inicio.strip():
        try:
            dt_inicio = pd.to_datetime(data_inicio, format='%Y-%m-%d')
            df = df[df['DataEmissao'] >= dt_inicio]
        except:
            pass
    
    # Filtro: Data Fim (apenas DataEmissao)
    if data_fim and data_fim.strip():
        try:
            dt_fim = pd.to_datetime(data_fim, format='%Y-%m-%d') + pd.Timedelta(days=1)
            df = df[df['DataEmissao'] < dt_fim]
        except:
            pass
    
    # Filtro: Comprador
    if comprador_filtro and comprador_filtro != 'Todos':
        df = df[df['NomeComprador'] == comprador_filtro]
    
    # Filtro: Tipo Produto
    if tipo_filtro and tipo_filtro != 'Todos':
        df = df[df['TipoProduto'] == tipo_filtro]
    
    # =====================================================
    # PASSO 3: Separar registros COM e SEM histórico
    # =====================================================
    df_com_hist = df[df['UltimoPrecoUnitario'].notna() & (df['UltimoPrecoUnitario'] > 0)].copy()
    
    if df_com_hist.empty:
        template_erro['compradores'] = compradores
        template_erro['tipos_produto'] = tipos_produto
        template_erro['comprador_selecionado'] = comprador_filtro
        template_erro['tipo_selecionado'] = tipo_filtro
        template_erro['data_inicio'] = data_inicio
        template_erro['data_fim'] = data_fim
        template_erro['classificacao_selecionada'] = classificacao_filtro
        template_erro['erro'] = "Nenhum registro com histórico de preço encontrado para o período selecionado."
        return render_template('variacao.html', **template_erro)
    
    # =====================================================
    # PASSO 4: Calcular Variações (APÓS filtrar)
    # =====================================================
    df_com_hist['PrecoAtual'] = df_com_hist['PrecoUnitario']
    df_com_hist['VariacaoValor'] = (df_com_hist['PrecoAtual'] - df_com_hist['UltimoPrecoUnitario']) * df_com_hist['QtdPedida']
    df_com_hist['VariacaoPercentual'] = ((df_com_hist['PrecoAtual'] / df_com_hist['UltimoPrecoUnitario']) - 1) * 100
    
    # Classificação
    def classificar(valor):
        if valor < -0.01:
            return 'Saving'
        elif valor > 0.01:
            return 'Inflação'
        return 'Estável'
    
    df_com_hist['Classificacao'] = df_com_hist['VariacaoValor'].apply(classificar)
    
    # Filtro: Classificação (após calcular)
    if classificacao_filtro and classificacao_filtro != 'Todos':
        df_com_hist = df_com_hist[df_com_hist['Classificacao'] == classificacao_filtro]
    
    # =====================================================
    # PASSO 5: Calcular KPIs
    # =====================================================
    df_saving = df_com_hist[df_com_hist['Classificacao'] == 'Saving']
    df_inflacao = df_com_hist[df_com_hist['Classificacao'] == 'Inflação']
    df_estavel = df_com_hist[df_com_hist['Classificacao'] == 'Estável']
    
    total_saving = abs(df_saving['VariacaoValor'].sum())
    total_inflacao = df_inflacao['VariacaoValor'].sum()
    saldo_final = df_com_hist['VariacaoValor'].sum()
    
    count_saving = len(df_saving)
    count_inflacao = len(df_inflacao)
    count_estavel = len(df_estavel)
    
    # =====================================================
    # PASSO 6: Preparar dados para tabela
    # =====================================================
    df_com_hist = df_com_hist.sort_values('VariacaoValor', ascending=True)
    
    items = []
    for _, row in df_com_hist.head(500).iterrows():  # Limita a 500 para performance
        items.append({
            'NumeroPedido': str(row.get('NumeroPedido', '')),
            'ItemPedido': str(row.get('ItemPedido', '')),
            'DataEmissao': row['DataEmissao'].strftime('%d/%m/%Y') if pd.notna(row.get('DataEmissao')) else '',
            'CodProduto': str(row.get('CodProduto', '')),
            'DescricaoProduto': str(row.get('DescricaoProduto', ''))[:50],
            'TipoProduto': str(row.get('TipoProduto', '')),
            'QtdPedida': float(row.get('QtdPedida', 0)),
            'PrecoAtual': float(row.get('PrecoAtual', 0)),
            'UltimoPrecoUnitario': float(row.get('UltimoPrecoUnitario', 0)),
            'DataUltimaCompra': row['DataUltimaCompra'].strftime('%d/%m/%Y') if pd.notna(row.get('DataUltimaCompra')) else 'Sem histórico',
            'UltimoFornecedor': str(row.get('UltimoFornecedor', ''))[:30],
            'NomeFornecedor': str(row.get('NomeFornecedor', ''))[:30],
            'NomeComprador': str(row.get('NomeComprador', '')),
            'VariacaoValor': float(row.get('VariacaoValor', 0)),
            'VariacaoPercentual': float(row.get('VariacaoPercentual', 0)),
            'Classificacao': row.get('Classificacao', 'Estável')
        })
    
    # =====================================================
    # PASSO 7: Dados para gráficos
    # =====================================================
    # Por Comprador
    if not df_com_hist.empty:
        var_comprador = df_com_hist.groupby('NomeComprador')['VariacaoValor'].sum().reset_index()
        var_comprador = var_comprador.sort_values('VariacaoValor')
        chart_compradores = {
            'labels': var_comprador['NomeComprador'].tolist(),
            'valores': var_comprador['VariacaoValor'].tolist()
        }
        
        # Por Mês
        df_com_hist['MesAno'] = df_com_hist['DataEmissao'].dt.to_period('M').astype(str)
        var_mes = df_com_hist.groupby('MesAno')['VariacaoValor'].sum().reset_index()
        var_mes = var_mes.sort_values('MesAno')
        chart_meses = {
            'labels': var_mes['MesAno'].tolist(),
            'valores': var_mes['VariacaoValor'].tolist()
        }
    else:
        chart_compradores = {'labels': [], 'valores': []}
        chart_meses = {'labels': [], 'valores': []}
    
    # =====================================================
    # PASSO 8: Renderizar template
    # =====================================================
    return render_template('variacao.html',
        total_saving=total_saving,
        total_inflacao=total_inflacao,
        saldo_final=saldo_final,
        count_saving=count_saving,
        count_inflacao=count_inflacao,
        count_estavel=count_estavel,
        items=items,
        compradores=compradores,
        tipos_produto=tipos_produto,
        comprador_selecionado=comprador_filtro,
        tipo_selecionado=tipo_filtro,
        data_inicio=data_inicio,
        data_fim=data_fim,
        classificacao_selecionada=classificacao_filtro,
        chart_compradores=chart_compradores,
        chart_meses=chart_meses,
        total_items=len(df_com_hist),
        erro=None
    )


if __name__ == '__main__':
    # Habilitado para acesso externo (0.0.0.0)
    app.run(host='0.0.0.0', port=5001, debug=True)