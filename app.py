import json
import os
import pyodbc
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
from flask_caching import Cache
from datetime import datetime, timedelta
from io import BytesIO
import re

# Importar módulo de banco de dados local (cotações)
import database as db

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

# --- FUNÇÕES AUXILIARES: DATAS PADRÃO ---
def get_data_inicio_padrao():
    """Retorna a data de início padrão (01/01 do ano atual)"""
    return f"{datetime.now().year}-01-01"

def get_data_fim_padrao():
    """Retorna a data de fim padrão (31/12 do ano atual)"""
    return f"{datetime.now().year}-12-31"

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
    "M R FERNANDES PRADO"
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
app.config['PREFERRED_URL_SCHEME'] = 'http'  # Garantir HTTP para links externos
cache = Cache(app)


# --- FUNÇÃO AUXILIAR: OBTER IP DO SERVIDOR ---
def obter_ip_servidor():
    """Retorna o IP local do servidor para gerar links acessíveis externamente"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


# --- FUNÇÃO AUXILIAR: PREPARAR DADOS PARA JSON ---
def preparar_tabela_json(df):
    """
    Converte um DataFrame para lista de dicionários, 
    tratando valores NaT (Not a Time) que não podem ser serializados para JSON.
    
    CORREÇÃO V2: 
    - Tratamento robusto de todos os tipos de valores problemáticos
    - Normalização de valores numéricos (NaN -> 0)
    - Tratamento de strings vazias e None
    - Proteção contra erros de conversão
    """
    if df is None or df.empty:
        return []
    
    # Colunas de data que podem conter NaT (inclui todas as variações de nome)
    colunas_data = ['DataEmissao', 'DataEntregaPrevista', 'DataRecebimento', 
                    'DataSolicitacao', 'DataNecessidade',
                    'Data de Emissão', 'Data Entrega Prevista', 'Data de Emissao']
    
    # Colunas numéricas que devem ter valor padrão 0
    colunas_numericas = ['ValorTotal', 'QtdPedida', 'QtdEntregue', 'PrecoUnitario', 'ValorUnitario']
    
    # Colunas de texto que devem ter valor padrão
    colunas_texto = ['CodProduto', 'DescricaoProduto', 'NomeFornecedor', 'CodFornecedor',
                     'NumeroPedido', 'NumeroNota', 'CondicaoPagamento', 'NomeComprador']
    
    # Cria cópia para não alterar o original
    df_copy = df.copy()
    
    # Converte para dicionário
    registros = df_copy.to_dict(orient='records')
    
    # Trata cada registro
    for registro in registros:
        # Tratamento de datas
        for col in colunas_data:
            if col in registro:
                valor = registro[col]
                try:
                    # Se for NaT, NaN ou None, converte para None
                    if valor is None or pd.isna(valor):
                        registro[col] = None
                    elif hasattr(valor, 'strftime'):
                        # Converte datetime para string formatada (DD/MM/AAAA)
                        registro[col] = valor.strftime('%d/%m/%Y')
                    elif hasattr(valor, 'isoformat'):
                        # Converte datetime para string ISO e formata para DD/MM/AAAA
                        data_obj = pd.to_datetime(valor, errors='coerce')
                        if pd.notnull(data_obj):
                            registro[col] = data_obj.strftime('%d/%m/%Y')
                        else:
                            registro[col] = None
                    elif isinstance(valor, str) and valor.strip():
                        # Tenta converter string para data
                        try:
                            data_obj = pd.to_datetime(valor, errors='coerce')
                            if pd.notnull(data_obj):
                                registro[col] = data_obj.strftime('%d/%m/%Y')
                            else:
                                registro[col] = None
                        except:
                            registro[col] = None
                    else:
                        registro[col] = None
                except Exception:
                    registro[col] = None
        
        # Tratamento de valores numéricos
        for col in colunas_numericas:
            if col in registro:
                valor = registro[col]
                try:
                    if valor is None or pd.isna(valor):
                        registro[col] = 0
                    else:
                        registro[col] = float(valor)
                except (ValueError, TypeError):
                    registro[col] = 0
        
        # Tratamento de valores de texto
        for col in colunas_texto:
            if col in registro:
                valor = registro[col]
                try:
                    if valor is None or pd.isna(valor):
                        registro[col] = ''
                    else:
                        registro[col] = str(valor).strip()
                except Exception:
                    registro[col] = ''
        
        # Tratamento especial para NumeroNota (deve mostrar 'Pendente' quando vazio)
        if 'NumeroNota' in registro:
            if not registro['NumeroNota'] or registro['NumeroNota'].strip() == '':
                registro['NumeroNota'] = 'Pendente'
    
    return registros


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
# FUNÇÃO OTIMIZADA: VARIAÇÃO DE PREÇO (SAVING, INFLATION, COST AVOIDANCE)
# =============================================================================
# Arquitetura V10 - Análise Par-a-Par baseada em NOTAS FISCAIS:
#   1. Fonte de dados: SD1010 (Notas Fiscais de Entrada) - não mais SC7010 (Pedidos)
#   2. Preço unitário: D1_VUNIT (valor faturado) - não mais C7_PRECO (valor do pedido)
#   3. Quantidade: D1_QUANT (quantidade recebida) - não mais C7_QUANT (quantidade pedida)
#   4. Cada NF é um evento de compra independente (pedidos programados contabilizados corretamente)
#   5. Cache de 2h para performance
# =============================================================================

@cache.cached(timeout=7200, key_prefix='variacao_preco_v17_auditado')
def get_variacao_preco_data():
    """
    Busca NOTAS FISCAIS com análise par-a-par (current vs previous) por produto.
    
    VERSÃO V11 - GOVERNANÇA REFORÇADA
    ==================================
    
    REGRA DE GOVERNANÇA:
    - Apenas NFs com pedido de compra associado são consideradas
    - NFs sem D1_PEDIDO são excluídas (movimentações diversas, devoluções, ajustes)
    - Isso garante que apenas compras reais do time de compras entrem na análise
    
    FONTE DE DADOS: SD1010 (Notas Fiscais de Entrada)
    - Preço unitário real (D1_VUNIT) - o que foi pago de fato
    - Quantidade real recebida (D1_QUANT) - não a quantidade pedida
    - Cada NF é um evento de compra independente
    - Pedidos programados geram múltiplas NFs = múltiplos eventos contabilizados
    - Data real de faturamento (D1_DTDIGIT) para comparação temporal correta
    
    ESTRATÉGIA PAR-A-PAR:
    1. Query principal busca todas NFs de 2024+ COM PEDIDO ASSOCIADO
    2. Para cada produto: identifica a NF atual
    3. Busca a NF imediatamente anterior do mesmo produto
    4. Compara preços faturados (não valores de pedido)
    """
    import time
    
    print("=" * 70)
    print("[VARIAÇÃO V11] INICIANDO CARREGAMENTO - NOTAS FISCAIS COM GOVERNANÇA")
    print("[VARIAÇÃO V11] → Apenas NFs com pedido de compra associado")
    print("=" * 70)
    
    try:
        # =====================================================
        # ETAPA 1: CONEXÃO COM O BANCO
        # =====================================================
        print("[VARIAÇÃO] Etapa 1/6: Conectando ao banco de dados...")
        t1 = time.time()
        
        server = r'172.16.45.117\TOTVS' 
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str, timeout=60)
        
        print(f"[VARIAÇÃO] ✓ Conexão estabelecida em {time.time() - t1:.2f}s")
        
        # =====================================================
        # ETAPA 2: BUSCAR NOTAS FISCAIS DE 2024+ (período analisável)
        # FONTE: SD1010 (Itens de NF de Entrada)
        # =====================================================
        print("[VARIAÇÃO] Etapa 2/6: Buscando NOTAS FISCAIS de 2024+...")
        t2 = time.time()
        
        # QUERY PRINCIPAL: Notas Fiscais de Entrada
        # Chave única: D1_DOC + D1_SERIE + D1_FORNECE + D1_COD (número NF + série + fornecedor + produto)
        query_notas = """
        SELECT 
            NF.D1_DOC           AS NumeroNota,
            NF.D1_SERIE         AS SerieNota,
            NF.D1_DTDIGIT       AS DataNota,
            NF.D1_COD           AS CodProduto,
            PROD.B1_DESC        AS DescricaoProduto,
            PROD.B1_TIPO        AS TipoProduto,
            NF.D1_QUANT         AS QtdRecebida,
            NF.D1_TOTAL         AS ValorTotal,
            NF.D1_VUNIT         AS PrecoUnitario,
            NF.D1_FORNECE       AS CodFornecedor,
            FORN.A2_NOME        AS NomeFornecedor,
            NF.D1_PEDIDO        AS NumeroPedido,
            NF.D1_ITEMPC        AS ItemPedido,
            
            CASE LTRIM(RTRIM(FORN.A2_X_COMPR))
                WHEN '016' THEN 'Aline Chen'
                WHEN '007' THEN 'Hélio Doce'
                WHEN '008' THEN 'Diego Moya'
                WHEN '018' THEN 'Daniel Amaral'
                ELSE 'Outros'
            END AS NomeComprador
            
        FROM SD1010 AS NF WITH (NOLOCK)
        
        INNER JOIN SA2010 AS FORN WITH (NOLOCK)
            ON NF.D1_FORNECE = FORN.A2_COD 
            AND FORN.D_E_L_E_T_ = ''
            
        LEFT JOIN SB1010 AS PROD WITH (NOLOCK)
            ON NF.D1_COD = PROD.B1_COD 
            AND PROD.D_E_L_E_T_ = ''
        
        WHERE NF.D_E_L_E_T_ <> '*' 
          AND NF.D1_DTDIGIT >= '20240101'
          AND NF.D1_QUANT > 0
          AND NF.D1_TOTAL > 0
          AND NF.D1_VUNIT > 0
          AND NF.D1_TIPO = 'N'  -- Apenas NFs normais (não devoluções)
          -- GOVERNANÇA: Excluir NFs sem pedido associado (movimentações diversas)
          AND LTRIM(RTRIM(ISNULL(NF.D1_PEDIDO, ''))) <> ''
          -- FILTRO: Excluir tipos BN, SV e PR
          AND PROD.B1_TIPO NOT IN ('BN', 'SV', 'PR')
        """
        
        df_notas = pd.read_sql(query_notas, conn)
        print(f"[VARIAÇÃO] ✓ {len(df_notas)} notas fiscais carregadas em {time.time() - t2:.2f}s")
        
        if df_notas.empty:
            conn.close()
            print("[VARIAÇÃO] ✗ Nenhuma nota fiscal encontrada!")
            return pd.DataFrame()
        
        # =====================================================
        # ETAPA 3: BUSCAR HISTÓRICO COMPLETO DE NFs (OTIMIZADO)
        # =====================================================
        print("[VARIAÇÃO] Etapa 3/6: Buscando histórico completo de preços (NFs)...")
        t3 = time.time()
        
        # Pega lista de produtos únicos para filtrar
        produtos_unicos = df_notas['CodProduto'].unique().tolist()
        print(f"[VARIAÇÃO]   → {len(produtos_unicos)} produtos únicos para buscar histórico")
        
        # OTIMIZAÇÃO: Buscar histórico em chunks de 500 produtos
        # Evita timeout e uso excessivo de memória
        chunk_size_produtos = 500
        df_historico_parts = []
        
        for i in range(0, len(produtos_unicos), chunk_size_produtos):
            chunk_produtos = produtos_unicos[i:i+chunk_size_produtos]
            # Cria lista de produtos para IN clause
            produtos_str = "'" + "','".join([str(p).replace("'", "''") for p in chunk_produtos]) + "'"
            
            query_historico_chunk = f"""
            SELECT 
                NF.D1_COD AS CodProduto,
                NF.D1_DOC AS NumeroNota,
                NF.D1_SERIE AS SerieNota,
                NF.D1_DTDIGIT AS DataNota,
                NF.D1_VUNIT AS PrecoUnitario,
                NF.D1_FORNECE AS CodFornecedor,
                A2.A2_NOME AS NomeFornecedorHist,
                NF.D1_PEDIDO AS NumeroPedido
            FROM SD1010 NF WITH (NOLOCK)
            LEFT JOIN SA2010 A2 WITH (NOLOCK) 
                ON NF.D1_FORNECE = A2.A2_COD AND A2.D_E_L_E_T_ = ''
            WHERE NF.D_E_L_E_T_ <> '*'
              AND NF.D1_VUNIT > 0
              AND NF.D1_QUANT > 0
              AND NF.D1_TIPO = 'N'
              AND LTRIM(RTRIM(ISNULL(NF.D1_PEDIDO, ''))) <> ''
              AND NF.D1_COD IN ({produtos_str})
            """
            
            df_chunk = pd.read_sql(query_historico_chunk, conn)
            df_historico_parts.append(df_chunk)
            
            if (i + chunk_size_produtos) % 2000 == 0:
                print(f"[VARIAÇÃO]   → Carregados {min(i + chunk_size_produtos, len(produtos_unicos))}/{len(produtos_unicos)} produtos...")
        
        conn.close()
        
        # Concatena todos os chunks
        if df_historico_parts:
            df_historico = pd.concat(df_historico_parts, ignore_index=True)
        else:
            df_historico = pd.DataFrame()
        
        print(f"[VARIAÇÃO]   → {len(df_historico)} registros históricos carregados em {time.time() - t3:.2f}s")
        
        if df_historico.empty:
            print("[VARIAÇÃO] ⚠ Nenhum histórico encontrado para os produtos")
        
        # =====================================================
        # ETAPA 4: CONVERTER DATAS
        # =====================================================
        print("[VARIAÇÃO] Etapa 4/6: Convertendo datas...")
        t4 = time.time()
        
        df_notas['DataNota'] = pd.to_datetime(df_notas['DataNota'], format='%Y%m%d', errors='coerce')
        if not df_historico.empty:
            df_historico['DataNota'] = pd.to_datetime(df_historico['DataNota'], format='%Y%m%d', errors='coerce')
        
        df_notas = df_notas.dropna(subset=['DataNota', 'CodProduto'])
        if not df_historico.empty:
            df_historico = df_historico.dropna(subset=['DataNota', 'CodProduto'])
        
        print(f"[VARIAÇÃO] ✓ Datas convertidas em {time.time() - t4:.2f}s")
        
        # =====================================================
        # ETAPA 5: CRIAR DICIONÁRIO DE HISTÓRICO OTIMIZADO
        # =====================================================
        print("[VARIAÇÃO] Etapa 5/6: Criando índice de histórico por produto...")
        t5 = time.time()
        
        # Remover duplicatas por produto/data
        if not df_historico.empty:
            df_historico = df_historico.sort_values(['CodProduto', 'DataNota', 'NumeroNota', 'SerieNota'])
            df_historico = df_historico.drop_duplicates(subset=['CodProduto', 'DataNota'], keep='first')
            print(f"[VARIAÇÃO]   → {len(df_historico)} registros após deduplicação por produto/data")
        else:
            print(f"[VARIAÇÃO]   → 0 registros de histórico")
        
        # =====================================================
        # ETAPA 5.1: OTIMIZAÇÃO - CRIAR LOOKUP DE HISTÓRICO COM NUMPY
        # =====================================================
        import numpy as np
        
        # Cria dicionário de arrays numpy para busca rápida
        historico_dict = {}
        
        if not df_historico.empty:
            # Agrupa histórico por produto de forma eficiente
            df_historico_sorted = df_historico.sort_values(['CodProduto', 'DataNota'])
            
            for produto, grupo in df_historico_sorted.groupby('CodProduto', sort=False):
                historico_dict[produto] = {
                    'datas': grupo['DataNota'].values,
                    'precos': grupo['PrecoUnitario'].values,
                    'fornecedores': grupo['CodFornecedor'].values,
                    'nomes_forn': grupo['NomeFornecedorHist'].fillna('').values,
                    'notas': grupo['NumeroNota'].values,
                    'pedidos': grupo['NumeroPedido'].fillna('').values
                }
        
        print(f"[VARIAÇÃO]   → {len(historico_dict)} produtos indexados")
        print(f"[VARIAÇÃO] ✓ Índice criado em {time.time() - t5:.2f}s")
        
        # =====================================================
        # ETAPA 6: PROCESSAMENTO VETORIZADO PAR-A-PAR (OTIMIZADO)
        # Substituição do loop iterrows() por processamento em lote
        # =====================================================
        print("[VARIAÇÃO] Etapa 6/6: Processando análise par-a-par VETORIZADO...")
        t6 = time.time()
        
        # Função otimizada para buscar NF anterior usando numpy searchsorted
        def buscar_nf_anterior_batch(produtos, datas, historico_dict):
            """
            Busca vetorizada da NF anterior para cada registro.
            Retorna arrays numpy com os resultados.
            """
            n = len(produtos)
            
            # Pré-aloca arrays para resultados
            ultimos_precos = np.full(n, np.nan)
            ultimas_datas = np.empty(n, dtype='datetime64[ns]')
            ultimas_datas[:] = np.datetime64('NaT')
            ultimos_forn_cod = np.empty(n, dtype=object)
            ultimos_forn_nome = np.empty(n, dtype=object)
            ultimas_notas = np.empty(n, dtype=object)
            ultimos_pedidos = np.empty(n, dtype=object)
            
            # Agrupa por produto para processar em lote
            df_temp = pd.DataFrame({
                'idx': np.arange(n),
                'produto': produtos,
                'data': datas
            })
            
            for produto, grupo in df_temp.groupby('produto', sort=False):
                if produto not in historico_dict:
                    continue
                    
                hist = historico_dict[produto]
                hist_datas = hist['datas']
                indices = grupo['idx'].values
                datas_grupo = grupo['data'].values
                
                for i, (idx, data_atual) in enumerate(zip(indices, datas_grupo)):
                    # Usa searchsorted para busca binária eficiente
                    pos = np.searchsorted(hist_datas, data_atual, side='left')
                    
                    if pos > 0:
                        # Existe NF anterior
                        prev_idx = pos - 1
                        ultimos_precos[idx] = hist['precos'][prev_idx]
                        ultimas_datas[idx] = hist_datas[prev_idx]
                        ultimos_forn_cod[idx] = hist['fornecedores'][prev_idx]
                        ultimos_forn_nome[idx] = hist['nomes_forn'][prev_idx]
                        ultimas_notas[idx] = hist['notas'][prev_idx]
                        ultimos_pedidos[idx] = hist['pedidos'][prev_idx]
            
            return (ultimos_precos, ultimas_datas, ultimos_forn_cod, 
                    ultimos_forn_nome, ultimas_notas, ultimos_pedidos)
        
        # Extrai arrays para processamento
        produtos_array = df_notas['CodProduto'].values
        datas_array = df_notas['DataNota'].values
        
        print(f"[VARIAÇÃO]   → Processando {len(df_notas)} registros em lote...")
        
        # Executa busca vetorizada
        (ultimos_precos, ultimas_datas, ultimos_forn_cod, 
         ultimos_forn_nome, ultimas_notas, ultimos_pedidos) = buscar_nf_anterior_batch(
            produtos_array, datas_array, historico_dict
        )
        
        # Atribui resultados diretamente (evita append)
        df_notas['PrecoAnterior'] = ultimos_precos
        df_notas['DataNFAnterior'] = pd.Series(ultimas_datas, index=df_notas.index)
        df_notas['FornecedorAnteriorCod'] = ultimos_forn_cod
        df_notas['FornecedorAnteriorNome'] = ultimos_forn_nome
        df_notas['NotaAnterior'] = ultimas_notas
        df_notas['PedidoAnterior'] = ultimos_pedidos
        
        # Renomear colunas para compatibilidade com o template
        df_notas = df_notas.rename(columns={
            'DataNota': 'DataEmissao',
            'QtdRecebida': 'QtdPedida',
            'DataNFAnterior': 'DataCompraAnterior'
        })
        
        # Limpar colunas
        df_notas['NomeComprador'] = df_notas['NomeComprador'].astype(str).str.strip()
        df_notas['FornecedorAnteriorNome'] = df_notas['FornecedorAnteriorNome'].fillna('')
        
        # Estatísticas
        com_hist = df_notas['PrecoAnterior'].notna().sum()
        sem_hist = len(df_notas) - com_hist
        
        print(f"[VARIAÇÃO] ✓ Processamento concluído em {time.time() - t6:.2f}s")
        print("=" * 70)
        print(f"[VARIAÇÃO V11] RESUMO FINAL (COM GOVERNANÇA):")
        print(f"[VARIAÇÃO]   → Total de NFs válidas (com pedido): {len(df_notas)}")
        print(f"[VARIAÇÃO]   → Com baseline (histórico NF): {com_hist} ({100*com_hist/len(df_notas):.1f}%)")
        print(f"[VARIAÇÃO]   → Primeira Compra (sem histórico): {sem_hist} ({100*sem_hist/len(df_notas):.1f}%)")
        print(f"[VARIAÇÃO]   → Período: {df_notas['DataEmissao'].min().strftime('%d/%m/%Y')} até {df_notas['DataEmissao'].max().strftime('%d/%m/%Y')}")
        print(f"[VARIAÇÃO]   → TEMPO TOTAL: {time.time() - t1:.2f}s")
        print("=" * 70)
        
        return df_notas
        
    except Exception as e:
        print(f"[VARIAÇÃO] Erro SQL: {e}")
        import traceback
        traceback.print_exc()
        return None


# --- PROCESSAMENTO DOS FILTROS ---
# CORREÇÃO V2: Filtros mais robustos e consistentes
def aplicar_filtros_comuns(df, filtros):
    """
    Aplica filtros comuns ao DataFrame.
    
    CORREÇÕES APLICADAS (V2):
    - Normalização consistente de strings (strip + upper)
    - Tratamento de valores nulos antes de comparação
    - Filtros aplicados em ordem correta (datas primeiro para reduzir dataset)
    - Proteção contra DataFrames vazios em cada etapa
    """
    if df is None or df.empty: 
        return pd.DataFrame()
    
    # Cria cópia para evitar SettingWithCopyWarning
    df = df.copy()
    
    # 1. DATAS (aplicar primeiro para reduzir dataset)
    if filtros.get('data_inicio'):
        dt_inicio = pd.to_datetime(filtros['data_inicio'], errors='coerce')
        if pd.notnull(dt_inicio):
            df = df[df['DataEmissao'] >= dt_inicio]
            if df.empty:
                return pd.DataFrame()

    if filtros.get('data_fim'):
        dt_fim = pd.to_datetime(filtros['data_fim'], errors='coerce')
        if pd.notnull(dt_fim):
            df = df[df['DataEmissao'] <= dt_fim]
            if df.empty:
                return pd.DataFrame()
    
    # 2. FORNECEDORES
    fornecedores_sel = filtros.get('fornecedores')
    if fornecedores_sel and isinstance(fornecedores_sel, list) and len(fornecedores_sel) > 0:
        df = df[df['NomeFornecedor'].isin(fornecedores_sel)]
        if df.empty:
            return pd.DataFrame()

    # 3. BUSCA GERAL - CORREÇÃO PRINCIPAL
    if filtros.get('busca_geral'):
        texto_busca = filtros['busca_geral'].strip()
        # Remove espaços extras e separa por vírgula
        termos = list(dict.fromkeys([t.strip().upper() for t in texto_busca.split(',') if t.strip()]))
        
        if termos:
            # Pré-processa colunas de busca para normalização consistente
            # Converte para string e normaliza ANTES da busca
            col_descricao = df['DescricaoProduto'].fillna('').astype(str).str.upper().str.strip()
            col_pedido = df['NumeroPedido'].fillna('').astype(str).str.upper().str.strip()
            col_condicao = df['CondicaoPagamento'].fillna('').astype(str).str.upper().str.strip()
            
            # CodProduto - normalização especial (pode ter espaços)
            col_codproduto = pd.Series([''] * len(df), index=df.index)
            if 'CodProduto' in df.columns:
                col_codproduto = df['CodProduto'].fillna('').astype(str).str.upper().str.strip()
            
            # NumeroNota - trata 'Pendente' como valor especial
            col_nota = pd.Series([''] * len(df), index=df.index)
            if 'NumeroNota' in df.columns:
                col_nota = df['NumeroNota'].fillna('').astype(str).str.upper().str.strip()
            
            mascara_final = pd.Series([False] * len(df), index=df.index)
            
            for termo in termos:
                termo_limpo = termo.strip()
                if not termo_limpo:
                    continue
                    
                # Busca por correspondência exata ou parcial (contains)
                # IMPORTANTE: Verifica correspondência EXATA para códigos curtos
                if len(termo_limpo) <= 6:
                    # Para termos curtos, prioriza correspondência exata no código
                    condicao_codigo_exato = (col_codproduto == termo_limpo)
                    condicao_pedido_exato = (col_pedido == termo_limpo)
                    condicao_nota_exato = (col_nota == termo_limpo)
                    
                    # Se não encontrar exato, usa contains
                    condicoes = (
                        condicao_codigo_exato |
                        condicao_pedido_exato |
                        condicao_nota_exato |
                        col_descricao.str.contains(termo_limpo, na=False, regex=False) |
                        col_codproduto.str.contains(termo_limpo, na=False, regex=False) |
                        col_pedido.str.contains(termo_limpo, na=False, regex=False)
                    )
                else:
                    # Para termos longos, usa contains em todos os campos
                    condicoes = (
                        col_descricao.str.contains(termo_limpo, na=False, regex=False) |
                        col_pedido.str.contains(termo_limpo, na=False, regex=False) |
                        col_condicao.str.contains(termo_limpo, na=False, regex=False) |
                        col_codproduto.str.contains(termo_limpo, na=False, regex=False) |
                        col_nota.str.contains(termo_limpo, na=False, regex=False)
                    )
                
                mascara_final = mascara_final | condicoes
            
            df = df[mascara_final]
            if df.empty:
                return pd.DataFrame()

    # 4. COMPRADOR
    comprador_filtro = filtros.get('comprador')
    if comprador_filtro and comprador_filtro not in ['Todos', 'Todos os Compradores', 'None', '']:
        if isinstance(comprador_filtro, list):
            df = df[df['NomeComprador'].isin(comprador_filtro)]
        else:
            # Normaliza para comparação case-insensitive
            comprador_normalizado = comprador_filtro.strip().lower()
            df = df[df['NomeComprador'].fillna('').astype(str).str.strip().str.lower() == comprador_normalizado]
        
    return df

# --- ROTA PARA LIMPAR CACHE DE VARIAÇÃO DE PREÇO ---
@app.route('/limpar_cache_variacao')
def limpar_cache_variacao():
    """Limpa o cache da aba de variação de preço."""
    cache.delete('variacao_preco_v17_auditado')
    cache.delete('variacao_preco_v13_sem_bn_sv')  # cache antigo também
    cache.delete('variacao_preco_v12_padronizado')  # cache antigo também
    cache.delete('variacao_preco_v11_governanca')  # cache antigo também
    cache.delete('variacao_preco_v10_notas_fiscais')  # cache antigo também
    flash('Cache de Variação de Preço limpo com sucesso!', 'success')
    return redirect(url_for('variacao_preco'))


# --- ROTA PARA LIMPAR TODOS OS CACHES ---
@app.route('/limpar_todos_caches')
def limpar_todos_caches():
    """Limpa todos os caches do sistema."""
    cache.clear()
    flash('Todos os caches foram limpos com sucesso!', 'success')
    return redirect(url_for('dashboard'))


# --- ROTA DE DIAGNÓSTICO/AUDITORIA ---
@app.route('/diagnostico_variacao')
def diagnostico_variacao():
    """
    Rota de diagnóstico para auditoria completa dos dados de Variação de Preço.
    Compara valores do SQL bruto vs valores processados vs valores exibidos.
    """
    import time
    
    diagnostico = {
        'etapas': [],
        'alertas': [],
        'totais': {}
    }
    
    try:
        # =====================================================
        # ETAPA 1: QUERY SQL BRUTA (sem processamento)
        # =====================================================
        t1 = time.time()
        
        server = r'172.16.45.117\TOTVS' 
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str, timeout=60)
        
        # Query de contagem bruta
        query_contagem = """
        SELECT 
            COUNT(*) AS TotalNFs,
            COUNT(DISTINCT NF.D1_COD) AS TotalProdutos,
            COUNT(DISTINCT NF.D1_FORNECE) AS TotalFornecedores,
            SUM(NF.D1_TOTAL) AS ValorTotalNFs
        FROM SD1010 AS NF WITH (NOLOCK)
        LEFT JOIN SB1010 AS PROD WITH (NOLOCK) ON NF.D1_COD = PROD.B1_COD AND PROD.D_E_L_E_T_ = ''
        WHERE NF.D_E_L_E_T_ <> '*' 
          AND NF.D1_DTDIGIT >= '20240101'
          AND NF.D1_QUANT > 0
          AND NF.D1_TOTAL > 0
          AND NF.D1_VUNIT > 0
          AND NF.D1_TIPO = 'N'
          AND LTRIM(RTRIM(ISNULL(NF.D1_PEDIDO, ''))) <> ''
          AND PROD.B1_TIPO NOT IN ('BN', 'SV', 'PR')
        """
        
        df_contagem = pd.read_sql(query_contagem, conn)
        conn.close()
        
        sql_bruto = {
            'total_nfs': int(df_contagem['TotalNFs'].iloc[0]),
            'total_produtos': int(df_contagem['TotalProdutos'].iloc[0]),
            'total_fornecedores': int(df_contagem['TotalFornecedores'].iloc[0]),
            'valor_total': float(df_contagem['ValorTotalNFs'].iloc[0])
        }
        
        diagnostico['etapas'].append({
            'nome': 'SQL Bruto (sem processamento)',
            'tempo': f'{time.time() - t1:.2f}s',
            'dados': sql_bruto
        })
        
        # =====================================================
        # ETAPA 2: DADOS DO CACHE (processados)
        # =====================================================
        t2 = time.time()
        
        df_cache = get_variacao_preco_data()
        
        if df_cache is not None and not df_cache.empty:
            # NOTA: Filtro de tipos BN, SV, PR já aplicado no SQL
            # Não é necessário refiltrar aqui
            
            cache_dados = {
                'total_nfs': len(df_cache),
                'total_produtos': df_cache['CodProduto'].nunique(),
                'total_fornecedores': df_cache['NomeFornecedor'].nunique(),
                'valor_total': float(df_cache['ValorTotal'].sum()),
                'com_preco_anterior': int(df_cache['PrecoAnterior'].notna().sum()),
                'sem_preco_anterior': int(df_cache['PrecoAnterior'].isna().sum())
            }
            
            diagnostico['etapas'].append({
                'nome': 'Cache (após processamento Python)',
                'tempo': f'{time.time() - t2:.2f}s',
                'dados': cache_dados
            })
            
            # Verificar perda de dados
            perda_nfs = sql_bruto['total_nfs'] - cache_dados['total_nfs']
            if perda_nfs > 0:
                diagnostico['alertas'].append({
                    'tipo': 'PERDA',
                    'mensagem': f'Perda de {perda_nfs} NFs entre SQL bruto e cache ({100*perda_nfs/sql_bruto["total_nfs"]:.1f}%)',
                    'possivel_causa': 'Deduplicação por produto/data no histórico ou conversão de datas'
                })
            
            # =====================================================
            # ETAPA 3: SIMULAÇÃO DO MODO EXECUTIVO
            # =====================================================
            t3 = time.time()
            
            df_exec = df_cache.copy()
            df_exec = df_exec.sort_values('DataEmissao', ascending=False)
            df_exec = df_exec.drop_duplicates(subset=['NumeroNota', 'SerieNota', 'CodFornecedor', 'CodProduto'], keep='first')
            
            # Deduplicar por produto (modo executivo)
            df_exec_unico = df_exec.drop_duplicates(subset=['CodProduto'], keep='first').copy()
            
            # Classificar
            def classificar(row):
                preco_atual = row['PrecoUnitario']
                preco_anterior = row['PrecoAnterior']
                if pd.isna(preco_anterior) or preco_anterior <= 0:
                    return 'Primeira Compra'
                diferenca = preco_atual - preco_anterior
                # Se houve redução de preço = Saving
                if diferenca < -0.01:
                    return 'Saving'
                # Calcular variação percentual para aumento
                variacao_percentual = (diferenca / preco_anterior) * 100
                # Se variação > 1% = Inflation, senão = Cost Avoidance
                if variacao_percentual > 1.0:
                    return 'Inflation'
                else:
                    return 'Cost Avoidance'
            
            df_exec_unico['Classificacao'] = df_exec_unico.apply(classificar, axis=1)
            
            # Calcular impacto
            def calc_impacto(row):
                if row['Classificacao'] in ['Primeira Compra', 'Cost Avoidance']:
                    return 0
                return (row['PrecoUnitario'] - row['PrecoAnterior']) * row['QtdPedida']
            
            df_exec_unico['Impacto'] = df_exec_unico.apply(calc_impacto, axis=1)
            
            # KPIs
            impactos_pos = df_exec_unico[df_exec_unico['Impacto'] > 0]['Impacto'].sum()
            impactos_neg = abs(df_exec_unico[df_exec_unico['Impacto'] < 0]['Impacto'].sum())
            
            exec_dados = {
                'total_linhas': len(df_exec_unico),
                'saving_count': len(df_exec_unico[df_exec_unico['Classificacao'] == 'Saving']),
                'inflation_count': len(df_exec_unico[df_exec_unico['Classificacao'] == 'Inflation']),
                'cost_avoidance_count': len(df_exec_unico[df_exec_unico['Classificacao'] == 'Cost Avoidance']),
                'primeira_compra_count': len(df_exec_unico[df_exec_unico['Classificacao'] == 'Primeira Compra']),
                'saving_valor': float(impactos_neg),
                'inflation_valor': float(impactos_pos),
                'saldo_liquido': float(impactos_neg - impactos_pos)
            }
            
            diagnostico['etapas'].append({
                'nome': 'Modo Executivo (1 linha por produto)',
                'tempo': f'{time.time() - t3:.2f}s',
                'dados': exec_dados
            })
            
            # Alerta sobre deduplicação
            nfs_perdidas_exec = cache_dados['total_nfs'] - exec_dados['total_linhas']
            diagnostico['alertas'].append({
                'tipo': 'INFO',
                'mensagem': f'Modo Executivo: {nfs_perdidas_exec} NFs agrupadas (1 por produto)',
                'possivel_causa': 'Comportamento esperado: mantém apenas a NF mais recente de cada produto'
            })
            
            # =====================================================
            # ETAPA 4: SIMULAÇÃO DO MODO ANALÍTICO (TODAS as NFs)
            # =====================================================
            t4 = time.time()
            
            df_analitico = df_cache.copy()
            df_analitico = df_analitico.sort_values('DataEmissao', ascending=False)
            df_analitico = df_analitico.drop_duplicates(subset=['NumeroNota', 'SerieNota', 'CodFornecedor', 'CodProduto'], keep='first')
            
            df_analitico['Classificacao'] = df_analitico.apply(classificar, axis=1)
            df_analitico['Impacto'] = df_analitico.apply(calc_impacto, axis=1)
            
            # KPIs
            impactos_pos_a = df_analitico[df_analitico['Impacto'] > 0]['Impacto'].sum()
            impactos_neg_a = abs(df_analitico[df_analitico['Impacto'] < 0]['Impacto'].sum())
            
            analitico_dados = {
                'total_linhas': len(df_analitico),
                'saving_count': len(df_analitico[df_analitico['Classificacao'] == 'Saving']),
                'inflation_count': len(df_analitico[df_analitico['Classificacao'] == 'Inflation']),
                'cost_avoidance_count': len(df_analitico[df_analitico['Classificacao'] == 'Cost Avoidance']),
                'primeira_compra_count': len(df_analitico[df_analitico['Classificacao'] == 'Primeira Compra']),
                'saving_valor': float(impactos_neg_a),
                'inflation_valor': float(impactos_pos_a),
                'saldo_liquido': float(impactos_neg_a - impactos_pos_a)
            }
            
            diagnostico['etapas'].append({
                'nome': 'Modo Analítico (TODAS as NFs)',
                'tempo': f'{time.time() - t4:.2f}s',
                'dados': analitico_dados
            })
            
            # =====================================================
            # COMPARATIVO FINAL
            # =====================================================
            diagnostico['totais'] = {
                'sql_bruto': sql_bruto,
                'cache': cache_dados,
                'modo_executivo': exec_dados,
                'modo_analitico': analitico_dados
            }
            
            # Alertas de divergência
            if abs(exec_dados['saving_valor'] - analitico_dados['saving_valor']) > 100:
                diagnostico['alertas'].append({
                    'tipo': 'DIFERENCA',
                    'mensagem': f'Saving difere entre modos: Exec R${exec_dados["saving_valor"]:,.2f} vs Analitico R${analitico_dados["saving_valor"]:,.2f}',
                    'possivel_causa': 'Modo Executivo considera apenas 1 NF por produto'
                })
            
        return f"""
        <html>
        <head>
            <title>Diagnóstico Variação de Preço</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 20px; background: #f5f5f5; }}
                .card {{ background: white; padding: 20px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                .alerta {{ padding: 10px; margin: 5px 0; border-radius: 4px; }}
                .alerta.PERDA {{ background: #ffebee; border-left: 4px solid #f44336; }}
                .alerta.INFO {{ background: #e3f2fd; border-left: 4px solid #2196f3; }}
                .alerta.DIFERENCA {{ background: #fff3e0; border-left: 4px solid #ff9800; }}
                table {{ width: 100%; border-collapse: collapse; }}
                th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background: #f0f0f0; }}
                .numero {{ text-align: right; font-family: monospace; }}
            </style>
        </head>
        <body>
            <h1>🔍 Diagnóstico de Variação de Preço</h1>
            
            <div class="card">
                <h2>📊 Etapas de Processamento</h2>
                {''.join([f'''
                <div style="margin: 15px 0; padding: 15px; background: #f9f9f9; border-radius: 4px;">
                    <h3>{e['nome']} <small style="color: #666;">({e['tempo']})</small></h3>
                    <table>
                        {''.join([f'<tr><td>{k}</td><td class="numero">{v:,.2f if isinstance(v, float) else v:,}</td></tr>' for k, v in e['dados'].items()])}
                    </table>
                </div>
                ''' for e in diagnostico['etapas']])}
            </div>
            
            <div class="card">
                <h2>⚠️ Alertas</h2>
                {''.join([f'<div class="alerta {a["tipo"]}"><strong>{a["tipo"]}:</strong> {a["mensagem"]}<br><em>Causa: {a["possivel_causa"]}</em></div>' for a in diagnostico['alertas']])}
            </div>
            
            <div class="card">
                <h2>📈 Comparativo Final</h2>
                <table>
                    <tr>
                        <th>Métrica</th>
                        <th>SQL Bruto</th>
                        <th>Cache</th>
                        <th>Modo Executivo</th>
                        <th>Modo Analítico</th>
                    </tr>
                    <tr>
                        <td>Total NFs/Linhas</td>
                        <td class="numero">{diagnostico['totais']['sql_bruto']['total_nfs']:,}</td>
                        <td class="numero">{diagnostico['totais']['cache']['total_nfs']:,}</td>
                        <td class="numero">{diagnostico['totais']['modo_executivo']['total_linhas']:,}</td>
                        <td class="numero">{diagnostico['totais']['modo_analitico']['total_linhas']:,}</td>
                    </tr>
                    <tr>
                        <td>Saving (R$)</td>
                        <td class="numero">-</td>
                        <td class="numero">-</td>
                        <td class="numero" style="color: green;">R$ {diagnostico['totais']['modo_executivo']['saving_valor']:,.2f}</td>
                        <td class="numero" style="color: green;">R$ {diagnostico['totais']['modo_analitico']['saving_valor']:,.2f}</td>
                    </tr>
                    <tr>
                        <td>Inflation (R$)</td>
                        <td class="numero">-</td>
                        <td class="numero">-</td>
                        <td class="numero" style="color: red;">R$ {diagnostico['totais']['modo_executivo']['inflation_valor']:,.2f}</td>
                        <td class="numero" style="color: red;">R$ {diagnostico['totais']['modo_analitico']['inflation_valor']:,.2f}</td>
                    </tr>
                </table>
            </div>
            
            <p style="margin-top: 20px; color: #666;">
                <a href="/variacao">← Voltar para Variação de Preço</a> | 
                <a href="/limpar_cache_variacao">🗑️ Limpar Cache</a>
            </p>
        </body>
        </html>
        """
        
    except Exception as e:
        import traceback
        return f"<pre>Erro: {e}\n\n{traceback.format_exc()}</pre>"


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
        'data_inicio': filtros.get('data_inicio') or get_data_inicio_padrao(),
        'data_fim': filtros.get('data_fim') or get_data_fim_padrao(),
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
    
    # Aplica BLOQUEIO_FINANCEIRO para remover fornecedores bloqueados de todas as visualizações
    if not df.empty:
        for termo_excluir in BLOQUEIO_FINANCEIRO:
            df = df[~df['NomeFornecedor'].str.contains(termo_excluir, case=False, na=False, regex=False)]
    
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
    
    total_compras = df_unique['ValorTotal'].sum() if not df_unique.empty and 'ValorTotal' in df_unique.columns else 0
    qtd_pedidos = df_unique['NumeroPedido'].nunique() if not df_unique.empty and 'NumeroPedido' in df_unique.columns else 0
    ticket_medio = total_compras / qtd_pedidos if qtd_pedidos > 0 else 0
    
    # Cria df_pendente e AQUI aplica o BLOQUEIO_FINANCEIRO (só afeta Backlog e Previsão de Pagamentos)
    if not df_unique.empty and 'NumeroNota' in df_unique.columns and 'QtdEntregue' in df_unique.columns and 'QtdPedida' in df_unique.columns:
        df_pendente = df_unique[(df_unique['NumeroNota'] == 'Pendente') | (df_unique['QtdEntregue'] < df_unique['QtdPedida'])].copy()
    else:
        df_pendente = pd.DataFrame()
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

    top_fornecedores = df_unique.groupby('NomeFornecedor')['ValorTotal'].sum().nlargest(10).reset_index() if not df_unique.empty and 'NomeFornecedor' in df_unique.columns else pd.DataFrame()
    
    # === LÓGICA CONTEXTUAL DO TOP 10 ===
    # Detecta se há fornecedor(es) selecionado(s)
    fornecedores_selecionados = filtros.get('fornecedores') or []
    tem_fornecedor_selecionado = len(fornecedores_selecionados) > 0
    
    if tem_fornecedor_selecionado and not df_unique.empty and 'CodProduto' in df_unique.columns:
        # MODO: Top 10 Produtos do(s) Fornecedor(es) selecionado(s)
        # Agrupa por produto somando VALOR e QUANTIDADE
        top_produtos = df_unique.groupby(['CodProduto', 'DescricaoProduto']).agg({
            'ValorTotal': 'sum',
            'QtdPedida': 'sum'
        }).nlargest(10, 'ValorTotal').reset_index()
        
        # Prepara labels com código + descrição truncada
        def formatar_label_produto(row):
            cod = str(row['CodProduto']).strip()
            desc = str(row['DescricaoProduto']).strip()[:30]
            return f"{cod} - {desc}"
        
        graf_top10_labels = top_produtos.apply(formatar_label_produto, axis=1).tolist() if not top_produtos.empty else []
        graf_top10_values = top_produtos['ValorTotal'].tolist() if not top_produtos.empty else []
        graf_top10_qtds = top_produtos['QtdPedida'].tolist() if not top_produtos.empty else []
        
        # Define o título dinâmico
        if len(fornecedores_selecionados) == 1:
            nome_fornecedor_titulo = fornecedores_selecionados[0][:25]
            top10_titulo = f"Top 10 Produtos – {nome_fornecedor_titulo}"
        else:
            top10_titulo = f"Top 10 Produtos – {len(fornecedores_selecionados)} fornecedores"
        
        top10_modo = 'produtos'
    else:
        # MODO: Top 10 Fornecedores (padrão)
        graf_top10_labels = top_fornecedores['NomeFornecedor'].tolist() if not top_fornecedores.empty else []
        graf_top10_values = top_fornecedores['ValorTotal'].tolist() if not top_fornecedores.empty else []
        graf_top10_qtds = []  # Fornecedores não têm quantidade
        top10_titulo = "Top 10 Fornecedores (Valor)"
        top10_modo = 'fornecedores'
    
    # Verifica se df_unique tem dados e as colunas necessárias
    if not df_unique.empty and 'DataEmissao' in df_unique.columns:
        df_unique = df_unique.copy()
        df_unique['MesAno'] = df_unique['DataEmissao'].dt.strftime('%m/%Y')
        vendas_mes = df_unique.groupby(['MesAno', df_unique['DataEmissao'].dt.to_period('M')])['ValorTotal'].sum().reset_index().sort_values(by='DataEmissao')
        top_cond = df_unique.groupby('CondicaoPagamento')['ValorTotal'].sum().nlargest(5).reset_index() if 'CondicaoPagamento' in df_unique.columns else pd.DataFrame()
    else:
        vendas_mes = pd.DataFrame()
        top_cond = pd.DataFrame()

    dados = {
        'kpi_total': f"R$ {total_compras:,.2f}",
        'kpi_qtd': qtd_pedidos,
        'kpi_ticket': f"R$ {ticket_medio:,.2f}",
        'kpi_pendente': f"R$ {total_pendente:,.2f}",
        # Top 10 Contextual (Fornecedores OU Produtos)
        'graf_top10_labels': graf_top10_labels,
        'graf_top10_values': graf_top10_values,
        'graf_top10_qtds': graf_top10_qtds,  # Quantidades para tooltip
        'top10_titulo': top10_titulo,
        'top10_modo': top10_modo,
        # Mantém para compatibilidade
        'graf_forn_labels': top_fornecedores['NomeFornecedor'].tolist() if not top_fornecedores.empty else [],
        'graf_forn_values': top_fornecedores['ValorTotal'].tolist() if not top_fornecedores.empty else [],
        'graf_mes_labels': vendas_mes['MesAno'].tolist() if not vendas_mes.empty else [],
        'graf_mes_values': vendas_mes['ValorTotal'].tolist() if not vendas_mes.empty else [],
        'graf_cond_labels': top_cond['CondicaoPagamento'].tolist() if not top_cond.empty else [],
        'graf_cond_values': top_cond['ValorTotal'].tolist() if not top_cond.empty else [],
        'previsao_labels': previsao_labels,
        'previsao_values': previsao_values,
        'tabela': preparar_tabela_json(df_unique.head(200)) if not df_unique.empty else [],
        'opcoes_fornecedores': sorted(df_raw['NomeFornecedor'].dropna().unique().tolist()) if df_raw is not None else [],
        'opcoes_compradores': sorted(df_raw['NomeComprador'].dropna().unique().tolist()) if df_raw is not None else [],
        'opcoes_tipos_produto': sorted(df_raw['TipoProduto'].dropna().unique().tolist()) if df_raw is not None and 'TipoProduto' in df_raw.columns else []
    }

    return render_template('dashboard.html', user="Admin", dados=dados, filtros=filtros_template, sql_meta=sql_meta)


# =============================================================================
# API: EXPORTAR DASHBOARD AGRUPADO PARA EXCEL
# =============================================================================
@app.route('/api/exportar_dashboard', methods=['POST'])
def api_exportar_dashboard():
    """
    API para exportar todos os dados do dashboard agrupados por produto/fornecedor.
    Soma quantidade e valor de itens duplicados.
    """
    try:
        # Recebe os filtros do frontend
        filtros = request.get_json() or {}
        
        data_inicio = filtros.get('data_inicio', get_data_inicio_padrao())
        data_fim = filtros.get('data_fim', get_data_fim_padrao())
        fornecedores = filtros.get('fornecedores', [])
        comprador = filtros.get('comprador', 'Todos')
        tipos_produto = filtros.get('tipos_produto', [])
        busca_geral = filtros.get('busca_geral', '')
        
        # Carrega dados
        df = get_database_data()
        
        if df is None or df.empty:
            return jsonify({'success': False, 'message': 'Erro ao carregar dados'})
        
        # Converte datas de filtro (trata diferentes formatos)
        try:
            # Se já estiver no formato YYYY-MM-DD, usa direto
            if len(data_inicio) == 10:  # YYYY-MM-DD
                dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d')
            else:  # YYYY-MM
                dt_inicio = datetime.strptime(f"{data_inicio}-01", '%Y-%m-%d')
            
            if len(data_fim) == 10:  # YYYY-MM-DD
                dt_fim = datetime.strptime(data_fim, '%Y-%m-%d')
            else:  # YYYY-MM
                dt_fim = datetime.strptime(f"{data_fim}-01", '%Y-%m-%d') + pd.offsets.MonthEnd(0)
        except Exception as e:
            # Fallback: usa o ano atual
            ano_atual = datetime.now().year
            dt_inicio = datetime(ano_atual, 1, 1)
            dt_fim = datetime(ano_atual, 12, 31)
        
        # Aplica filtros
        df_filtrado = df[(df['DataEmissao'] >= dt_inicio) & (df['DataEmissao'] <= dt_fim)].copy()
        
        if fornecedores:
            df_filtrado = df_filtrado[df_filtrado['NomeFornecedor'].isin(fornecedores)]
        
        if comprador and comprador != 'Todos':
            df_filtrado = df_filtrado[df_filtrado['NomeComprador'] == comprador]
        
        if tipos_produto:
            df_filtrado = df_filtrado[df_filtrado['TipoProduto'].isin(tipos_produto)]
        
        if busca_geral:
            busca = busca_geral.upper()
            df_filtrado = df_filtrado[
                df_filtrado['CodProduto'].astype(str).str.upper().str.contains(busca, na=False) |
                df_filtrado['DescricaoProduto'].astype(str).str.upper().str.contains(busca, na=False) |
                df_filtrado['NumeroPedido'].astype(str).str.upper().str.contains(busca, na=False) |
                df_filtrado['NumeroNota'].astype(str).str.upper().str.contains(busca, na=False)
            ]
        
        if df_filtrado.empty:
            return jsonify({'success': False, 'message': 'Nenhum dado encontrado com os filtros aplicados'})
        
        # Agrupa por Produto + Fornecedor, somando quantidade e valor
        df_agrupado = df_filtrado.groupby(['CodProduto', 'DescricaoProduto', 'CodFornecedor', 'NomeFornecedor']).agg({
            'QtdPedida': 'sum',
            'ValorTotal': 'sum',
            'NumeroPedido': lambda x: ', '.join(x.astype(str).unique()[:5]) + ('...' if len(x.unique()) > 5 else ''),  # Lista até 5 pedidos
            'CondicaoPagamento': lambda x: x.mode().iloc[0] if not x.mode().empty else '',  # Condição mais frequente
            'DataEmissao': ['min', 'max'],  # Primeira e última compra
        }).reset_index()
        
        # Flatten colunas multi-level
        df_agrupado.columns = ['CodProduto', 'DescricaoProduto', 'CodFornecedor', 'NomeFornecedor', 
                               'QtdTotal', 'ValorTotal', 'Pedidos', 'CondicaoPagamento', 
                               'PrimeiraCompra', 'UltimaCompra']
        
        # Ordena por valor total decrescente
        df_agrupado = df_agrupado.sort_values('ValorTotal', ascending=False)
        
        # Formata datas
        df_agrupado['PrimeiraCompra'] = df_agrupado['PrimeiraCompra'].dt.strftime('%d/%m/%Y')
        df_agrupado['UltimaCompra'] = df_agrupado['UltimaCompra'].dt.strftime('%d/%m/%Y')
        
        # Converte para lista de dicionários
        dados = df_agrupado.to_dict('records')
        
        return jsonify({
            'success': True,
            'dados': dados,
            'total_itens': len(dados),
            'total_valor': float(df_agrupado['ValorTotal'].sum()),
            'total_qtd': float(df_agrupado['QtdTotal'].sum())
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Erro ao exportar: {str(e)}'})


# =============================================================================
# API: ANÁLISE DE FREQUÊNCIA DE COMPRAS POR ITEM
# =============================================================================
@app.route('/api/frequencia_compras', methods=['GET'])
def api_frequencia_compras():
    """
    API para análise de frequência de compras por item.
    
    CORREÇÕES V2 APLICADAS:
    - Tratamento robusto de valores nulos e NaN
    - Proteção contra divisão por zero
    - Tratamento de listas vazias
    - Resposta consistente mesmo sem dados suficientes
    - Tratamento de erros de conversão de data
    
    Parâmetros:
        - item: código ou descrição do item (obrigatório)
        - data_inicio: data inicial no formato YYYY-MM-DD (opcional)
        - data_fim: data final no formato YYYY-MM-DD (opcional)
    
    Retorna:
        - Quantidade total comprada no período
        - Frequência de pedidos (quantas vezes foi comprado)
        - Média de quantidade por mês
        - Valor total médio mensal
        - Detalhamento por pedido
    """
    try:
        item_busca = request.args.get('item', '').strip()
        data_inicio = request.args.get('data_inicio', get_data_inicio_padrao())
        data_fim = request.args.get('data_fim', get_data_fim_padrao())
        
        if not item_busca:
            return jsonify({
                'success': False,
                'message': 'Parâmetro "item" é obrigatório'
            })
        
        # Carrega dados do banco
        df = get_database_data()
        
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'message': 'Erro ao carregar dados do banco ou sem dados disponíveis'
            })
        
        # Converte datas de filtro com tratamento de erro
        try:
            dt_inicio = datetime.strptime(data_inicio, '%Y-%m-%d')
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d')
        except ValueError as e:
            return jsonify({
                'success': False,
                'message': f'Formato de data inválido: {str(e)}. Use YYYY-MM-DD.'
            })
        
        # Valida ordem das datas
        if dt_inicio > dt_fim:
            return jsonify({
                'success': False,
                'message': 'Data inicial não pode ser maior que data final'
            })
        
        # Filtra por período - trata valores nulos na coluna de data
        df_com_data = df[df['DataEmissao'].notna()].copy()
        if df_com_data.empty:
            return jsonify({
                'success': False,
                'message': 'Sem dados com datas válidas no período especificado'
            })
        
        df_periodo = df_com_data[
            (df_com_data['DataEmissao'] >= dt_inicio) & 
            (df_com_data['DataEmissao'] <= dt_fim)
        ].copy()
        
        if df_periodo.empty:
            return jsonify({
                'success': False,
                'message': f'Nenhum dado encontrado no período de {data_inicio} a {data_fim}'
            })
        
        # Separa múltiplos itens por vírgula e normaliza
        termos_busca = [t.strip().upper() for t in item_busca.split(',') if t.strip()]
        
        if not termos_busca:
            return jsonify({
                'success': False,
                'message': 'Nenhum termo de busca válido informado'
            })
        
        # Pré-processa colunas para busca eficiente (evita apply row-by-row)
        df_periodo['CodProduto_Upper'] = df_periodo['CodProduto'].fillna('').astype(str).str.upper().str.strip()
        df_periodo['DescricaoProduto_Upper'] = df_periodo['DescricaoProduto'].fillna('').astype(str).str.upper().str.strip()
        
        # Filtra pelos itens usando vetorização (mais eficiente)
        mascara = pd.Series([False] * len(df_periodo), index=df_periodo.index)
        for termo in termos_busca:
            mascara = mascara | (
                df_periodo['CodProduto_Upper'].str.contains(termo, na=False, regex=False) |
                df_periodo['DescricaoProduto_Upper'].str.contains(termo, na=False, regex=False)
            )
        
        df_filtrado = df_periodo[mascara].copy()
        
        if df_filtrado.empty:
            return jsonify({
                'success': False,
                'message': f'Nenhum item encontrado para: "{item_busca}" no período selecionado',
                'sugestao': 'Verifique a ortografia ou tente um termo mais genérico'
            })
        
        # Agrupa por CodProduto para análise
        resultados = []
        
        for cod_produto in df_filtrado['CodProduto'].unique():
            try:
                df_item = df_filtrado[df_filtrado['CodProduto'] == cod_produto]
                
                if df_item.empty:
                    continue
                
                # Calcula métricas com proteção contra valores nulos
                qtd_total = df_item['QtdPedida'].fillna(0).sum()
                valor_total = df_item['ValorTotal'].fillna(0).sum()
                freq_pedidos = df_item['NumeroPedido'].nunique()
                
                # Calcula número de meses distintos
                df_item_copy = df_item.copy()
                df_item_copy['MesAno'] = df_item_copy['DataEmissao'].dt.to_period('M')
                num_meses = df_item_copy['MesAno'].nunique()
                
                # Médias mensais com proteção contra divisão por zero
                media_qtd_mes = qtd_total / max(num_meses, 1)
                media_valor_mes = valor_total / max(num_meses, 1)
                
                # Datas primeiro e último pedido com tratamento de nulos
                datas_validas = df_item['DataEmissao'].dropna()
                primeiro_pedido = datas_validas.min() if len(datas_validas) > 0 else None
                ultimo_pedido = datas_validas.max() if len(datas_validas) > 0 else None
                
                # Detalhamento por pedido
                detalhes_pedidos = df_item.groupby('NumeroPedido').agg({
                    'DataEmissao': 'first',
                    'QtdPedida': 'sum',
                    'ValorTotal': 'sum',
                    'NomeFornecedor': 'first'
                }).reset_index().to_dict(orient='records')
                
                # Formata datas nos detalhes
                for det in detalhes_pedidos:
                    if pd.notnull(det.get('DataEmissao')):
                        det['DataEmissao'] = det['DataEmissao'].strftime('%d/%m/%Y')
                    else:
                        det['DataEmissao'] = '-'
                    # Trata valores None nos outros campos
                    det['QtdPedida'] = det.get('QtdPedida') or 0
                    det['ValorTotal'] = det.get('ValorTotal') or 0
                    det['NomeFornecedor'] = det.get('NomeFornecedor') or 'N/A'
                
                # Obtém descrição do produto (primeiro registro não-nulo)
                descricao = df_item['DescricaoProduto'].dropna().iloc[0] if len(df_item['DescricaoProduto'].dropna()) > 0 else 'Sem descrição'
                
                resultados.append({
                    'CodProduto': str(cod_produto) if cod_produto else 'N/A',
                    'DescricaoProduto': str(descricao),
                    'QtdTotalComprada': float(qtd_total),
                    'ValorTotalComprado': float(valor_total),
                    'FrequenciaPedidos': int(freq_pedidos),
                    'NumMesesComCompra': int(num_meses),
                    'MediaQtdPorMes': round(float(media_qtd_mes), 2),
                    'MediaValorPorMes': round(float(media_valor_mes), 2),
                    'PrimeiroPedido': primeiro_pedido.strftime('%d/%m/%Y') if pd.notnull(primeiro_pedido) else '-',
                    'UltimoPedido': ultimo_pedido.strftime('%d/%m/%Y') if pd.notnull(ultimo_pedido) else '-',
                    'DetalhesPedidos': detalhes_pedidos
                })
            except Exception as item_error:
                print(f"[FREQUENCIA] Erro ao processar item {cod_produto}: {item_error}")
                continue
        
        if not resultados:
            return jsonify({
                'success': False,
                'message': f'Dados encontrados, mas não foi possível processar análise para: "{item_busca}"'
            })
        
        # Estatísticas globais com proteção
        total_geral_qtd = sum(r['QtdTotalComprada'] for r in resultados)
        total_geral_valor = sum(r['ValorTotalComprado'] for r in resultados)
        total_geral_pedidos = sum(r['FrequenciaPedidos'] for r in resultados)
        
        # Calcula média mensal global
        df_filtrado_copy = df_filtrado.copy()
        df_filtrado_copy['MesAno'] = df_filtrado_copy['DataEmissao'].dt.to_period('M')
        num_meses_global = df_filtrado_copy['MesAno'].nunique()
        media_mensal_global = total_geral_qtd / max(num_meses_global, 1)
        
        return jsonify({
            'success': True,
            'periodo': {
                'inicio': data_inicio,
                'fim': data_fim
            },
            'itens': resultados,
            'resumo': {
                'TotalGeralQtd': float(total_geral_qtd),
                'TotalGeralValor': float(total_geral_valor),
                'TotalGeralPedidos': int(total_geral_pedidos),
                'MediaMensalGlobal': round(float(media_mensal_global), 2),
                'NumMesesGlobal': int(num_meses_global)
            }
        })
        
    except Exception as e:
        import traceback
        print(f"[FREQUENCIA] Erro na API frequencia_compras: {e}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'Erro interno ao processar análise: {str(e)}'
        })


# =============================================================================
# API: ANÁLISE COMPARATIVA TEMPORAL (Ano vs Ano)
# =============================================================================
@app.route('/api/analise_comparativa', methods=['GET'])
def api_analise_comparativa():
    """
    API para análise comparativa temporal entre dois períodos.
    
    Parâmetros:
        - periodo1_inicio: data inicial do período base (YYYY-MM-DD)
        - periodo1_fim: data final do período base (YYYY-MM-DD)
        - periodo2_inicio: data inicial do período de comparação (YYYY-MM-DD)
        - periodo2_fim: data final do período de comparação (YYYY-MM-DD)
        - comprador: filtro por comprador (opcional)
        - fornecedores: lista de fornecedores para filtrar (multi-select, igual ao dashboard)
        - busca_produto: busca por código ou descrição de produto (igual ao dashboard)
        - tipo: filtro por tipo de produto (opcional)
    
    Retorna:
        - Métricas comparativas (valor total, quantidade, variação %)
        - Dados agrupados por mês para ambos os períodos
        - Top variações positivas e negativas
    
    IMPORTANTE: Usa a mesma lógica de cálculo do dashboard principal para garantir
    valores consistentes (remove duplicatas de NumeroPedido+ItemPedido).
    """
    try:
        # Parâmetros de período
        periodo1_inicio = request.args.get('periodo1_inicio', '')
        periodo1_fim = request.args.get('periodo1_fim', '')
        periodo2_inicio = request.args.get('periodo2_inicio', '')
        periodo2_fim = request.args.get('periodo2_fim', '')
        
        # Filtros PADRONIZADOS (idênticos ao dashboard)
        comprador = request.args.get('comprador', 'Todos')
        fornecedores = request.args.getlist('fornecedores')  # Multi-select igual ao dashboard
        busca_produto = request.args.get('busca_produto', '')  # Busca igual ao dashboard
        tipo = request.args.get('tipo', '')
        
        # Validação básica
        if not all([periodo1_inicio, periodo1_fim, periodo2_inicio, periodo2_fim]):
            return jsonify({
                'success': False,
                'message': 'Todos os períodos devem ser informados'
            }), 400
        
        # Converte datas
        try:
            dt_p1_inicio = datetime.strptime(periodo1_inicio, '%Y-%m-%d')
            dt_p1_fim = datetime.strptime(periodo1_fim, '%Y-%m-%d')
            dt_p2_inicio = datetime.strptime(periodo2_inicio, '%Y-%m-%d')
            dt_p2_fim = datetime.strptime(periodo2_fim, '%Y-%m-%d')
        except ValueError as e:
            return jsonify({
                'success': False,
                'message': f'Formato de data inválido. Use YYYY-MM-DD. Erro: {str(e)}'
            }), 400
        
        # Carrega dados
        df = get_database_data()
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'message': 'Erro ao carregar dados do banco'
            })
        
        # =====================================================================
        # APLICAÇÃO DE FILTROS - IDÊNTICO AO DASHBOARD PRINCIPAL
        # =====================================================================
        
        # 1. Filtro de comprador
        if comprador and comprador != 'Todos':
            df = df[df['NomeComprador'] == comprador]
        
        # 2. Filtro de fornecedores (MULTI-SELECT - igual ao dashboard)
        if fornecedores and isinstance(fornecedores, list) and len(fornecedores) > 0:
            df = df[df['NomeFornecedor'].isin(fornecedores)]
        
        # 3. Filtro de produto/busca (IGUAL ao campo "Busca" do dashboard)
        if busca_produto and busca_produto.strip():
            texto_busca = busca_produto.strip()
            termos = list(dict.fromkeys([t.strip().upper() for t in texto_busca.split(',') if t.strip()]))
            
            if termos:
                col_descricao = df['DescricaoProduto'].fillna('').astype(str).str.upper().str.strip()
                col_codproduto = df['CodProduto'].fillna('').astype(str).str.upper().str.strip() if 'CodProduto' in df.columns else pd.Series([''] * len(df), index=df.index)
                
                mascara_final = pd.Series([False] * len(df), index=df.index)
                
                for termo in termos:
                    termo_limpo = termo.strip()
                    if not termo_limpo:
                        continue
                    condicoes = (
                        col_descricao.str.contains(termo_limpo, na=False, regex=False) |
                        col_codproduto.str.contains(termo_limpo, na=False, regex=False)
                    )
                    mascara_final = mascara_final | condicoes
                
                df = df[mascara_final]
        
        # 4. Filtro de tipo de produto
        if tipo and tipo.strip() and 'TipoProduto' in df.columns:
            df = df[df['TipoProduto'] == tipo.strip()]
        
        # 5. Remove fornecedores bloqueados (IGUAL ao dashboard)
        for termo_excluir in BLOQUEIO_FINANCEIRO:
            df = df[~df['NomeFornecedor'].str.contains(termo_excluir, case=False, na=False, regex=False)]
        
        # =====================================================================
        # REMOÇÃO DE DUPLICATAS - CRÍTICO PARA VALORES CONSISTENTES
        # Mesma lógica do dashboard: df.drop_duplicates(subset=['NumeroPedido', 'ItemPedido'])
        # =====================================================================
        if not df.empty:
            df = df.drop_duplicates(subset=['NumeroPedido', 'ItemPedido'], keep='first')
        
        # Filtra por período 1
        df_p1 = df[(df['DataEmissao'] >= dt_p1_inicio) & (df['DataEmissao'] <= dt_p1_fim)].copy()
        
        # Filtra por período 2
        df_p2 = df[(df['DataEmissao'] >= dt_p2_inicio) & (df['DataEmissao'] <= dt_p2_fim)].copy()
        
        # Métricas totais
        total_p1 = df_p1['ValorTotal'].sum() if not df_p1.empty else 0
        total_p2 = df_p2['ValorTotal'].sum() if not df_p2.empty else 0
        qtd_pedidos_p1 = df_p1['NumeroPedido'].nunique() if not df_p1.empty else 0
        qtd_pedidos_p2 = df_p2['NumeroPedido'].nunique() if not df_p2.empty else 0
        qtd_itens_p1 = len(df_p1) if not df_p1.empty else 0
        qtd_itens_p2 = len(df_p2) if not df_p2.empty else 0
        
        # Cálculo de variação
        variacao_valor = ((total_p1 - total_p2) / total_p2 * 100) if total_p2 > 0 else (100 if total_p1 > 0 else 0)
        variacao_pedidos = ((qtd_pedidos_p1 - qtd_pedidos_p2) / qtd_pedidos_p2 * 100) if qtd_pedidos_p2 > 0 else (100 if qtd_pedidos_p1 > 0 else 0)
        
        # Dados agrupados sempre por mês (único agrupamento)
        dados_comparativos = []
        
        # Agrupa por mês para gráfico de linhas
        if not df_p1.empty:
            df_p1['MesNum'] = df_p1['DataEmissao'].dt.month
        if not df_p2.empty:
            df_p2['MesNum'] = df_p2['DataEmissao'].dt.month
        
        meses_nomes = {1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
                     7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'}
        
        agg_p1 = df_p1.groupby('MesNum').agg({'ValorTotal': 'sum', 'NumeroPedido': 'nunique'}).reset_index() if not df_p1.empty else pd.DataFrame()
        agg_p2 = df_p2.groupby('MesNum').agg({'ValorTotal': 'sum', 'NumeroPedido': 'nunique'}).reset_index() if not df_p2.empty else pd.DataFrame()
        
        # Cria série de meses do período
        meses_p1 = set(df_p1['MesNum'].unique()) if not df_p1.empty else set()
        meses_p2 = set(df_p2['MesNum'].unique()) if not df_p2.empty else set()
        todos_meses = sorted(meses_p1.union(meses_p2))
        
        for mes in todos_meses:
            val_p1 = agg_p1[agg_p1['MesNum'] == mes]['ValorTotal'].sum() if not agg_p1.empty else 0
            val_p2 = agg_p2[agg_p2['MesNum'] == mes]['ValorTotal'].sum() if not agg_p2.empty else 0
            ped_p1 = agg_p1[agg_p1['MesNum'] == mes]['NumeroPedido'].sum() if not agg_p1.empty else 0
            ped_p2 = agg_p2[agg_p2['MesNum'] == mes]['NumeroPedido'].sum() if not agg_p2.empty else 0
            
            var_pct = ((val_p1 - val_p2) / val_p2 * 100) if val_p2 > 0 else (100 if val_p1 > 0 else 0)
            
            dados_comparativos.append({
                'label': meses_nomes.get(mes, str(mes)),
                'mes_num': mes,
                'valor_periodo1': round(val_p1, 2),
                'valor_periodo2': round(val_p2, 2),
                'pedidos_periodo1': int(ped_p1),
                'pedidos_periodo2': int(ped_p2),
                'variacao_pct': round(var_pct, 1),
                'diferenca': round(val_p1 - val_p2, 2)
            })
        
        # Prepara labels de período para exibição
        label_p1 = f"{dt_p1_inicio.strftime('%d/%m/%Y')} a {dt_p1_fim.strftime('%d/%m/%Y')}"
        label_p2 = f"{dt_p2_inicio.strftime('%d/%m/%Y')} a {dt_p2_fim.strftime('%d/%m/%Y')}"
        
        # Descrição dos filtros aplicados
        filtros_aplicados = []
        if comprador and comprador != 'Todos':
            filtros_aplicados.append(f"Comprador: {comprador}")
        if fornecedores and len(fornecedores) > 0:
            filtros_aplicados.append(f"Fornecedores: {', '.join(fornecedores[:3])}{'...' if len(fornecedores) > 3 else ''}")
        if busca_produto:
            filtros_aplicados.append(f"Produto: {busca_produto}")
        if tipo:
            filtros_aplicados.append(f"Tipo: {tipo}")
        
        # Top variações (positivas e negativas)
        items_com_ambos = [d for d in dados_comparativos if d['valor_periodo1'] > 0 and d['valor_periodo2'] > 0]
        top_aumentos = sorted(items_com_ambos, key=lambda x: x['variacao_pct'], reverse=True)[:5]
        top_reducoes = sorted(items_com_ambos, key=lambda x: x['variacao_pct'])[:5]
        
        # Converte valores numpy para tipos nativos Python (evita erro JSON serialization)
        def converter_para_python_nativo(obj):
            if isinstance(obj, dict):
                return {k: converter_para_python_nativo(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [converter_para_python_nativo(i) for i in obj]
            elif hasattr(obj, 'item'):  # numpy types (int32, int64, float64, etc)
                return obj.item()
            else:
                return obj
        
        dados_comparativos = converter_para_python_nativo(dados_comparativos)
        top_aumentos = converter_para_python_nativo(top_aumentos)
        top_reducoes = converter_para_python_nativo(top_reducoes)
        
        return jsonify({
            'success': True,
            'periodos': {
                'periodo1': {
                    'inicio': periodo1_inicio,
                    'fim': periodo1_fim,
                    'label': label_p1
                },
                'periodo2': {
                    'inicio': periodo2_inicio,
                    'fim': periodo2_fim,
                    'label': label_p2
                }
            },
            'resumo': {
                'total_periodo1': float(total_p1),
                'total_periodo2': float(total_p2),
                'diferenca': float(total_p1 - total_p2),
                'variacao_pct': float(round(variacao_valor, 1)),
                'pedidos_periodo1': int(qtd_pedidos_p1),
                'pedidos_periodo2': int(qtd_pedidos_p2),
                'variacao_pedidos_pct': float(round(variacao_pedidos, 1)),
                'itens_periodo1': int(qtd_itens_p1),
                'itens_periodo2': int(qtd_itens_p2)
            },
            'filtros_aplicados': filtros_aplicados,
            'dados': dados_comparativos,
            'top_aumentos': top_aumentos,
            'top_reducoes': top_reducoes
        })
        
    except Exception as e:
        import traceback
        print(f"[COMPARATIVA] Erro na API analise_comparativa: {e}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'Erro interno ao processar análise: {str(e)}'
        })


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
        'data_inicio': filtros.get('data_inicio') or get_data_inicio_padrao(),
        'data_fim': filtros.get('data_fim') or get_data_fim_padrao(),
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
            """Normaliza telefone para formato WhatsApp Brasil: apenas DDD + 9 dígitos (sem 55)"""
            if not tel: return ''
            # Remove tudo que não é dígito
            numero = ''.join(filter(str.isdigit, str(tel)))
            original = numero
            if not numero: return ''
            
            # Remove zeros à esquerda (formato antigo de discagem interurbana)
            numero = numero.lstrip('0')
            
            # Remove código do país se existir (55 no início)
            if numero.startswith('55') and len(numero) >= 12:
                numero = numero[2:]
            
            # Se tiver 10 dígitos (DDD + 8 dígitos antigo celular), adiciona o 9
            if len(numero) == 10:
                terceiro_digito = numero[2] if len(numero) > 2 else ''
                if terceiro_digito in ['9', '8', '7', '6']:
                    numero = numero[:2] + '9' + numero[2:]
                    return numero
                else:
                    return ''  # telefone fixo
            
            # Se tiver 11 dígitos, verifica se é celular válido
            if len(numero) == 11:
                if numero[2] == '9':
                    return numero
                else:
                    return ''  # telefone fixo
            
            return ''

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
            'otd': 'Cálculo: SD1010.D1_DTDIGIT - (SC1010.C1_DATPRF + 7 dias tolerância)',
            'total_pedidos': 'Contagem única: SC7010.C7_NUM',
            
            # Tabela Fornecedores
            'cod_fornecedor': 'SA2010.A2_COD',
            'nome_fornecedor': 'SA2010.A2_NOME',
            'qtd_atrasos': 'Contagem: SC7010.C7_NUM onde OTD > 7 dias (tolerância aplicada)',
            'dias_atraso': 'Média: SD1010.D1_DTDIGIT - SC1010.C1_DATPRF (já considera tolerância)',
            'lead_time_forn': 'Média: SD1010.D1_DTDIGIT - SC7010.C7_EMISSAO',
            
            # Tabela Produtos
            'cod_produto': 'SB1010.B1_COD',
            'desc_produto': 'SB1010.B1_DESC',
            
            # Tabela Pedidos Atrasados
            'num_pedido': 'SC7010.C7_NUM',
            'data_emissao': 'SC1010.C1_EMISSAO',
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
        
        # ========================================================================
        # MARGEM DE TOLERÂNCIA PARA OTD (On-Time Delivery)
        # ========================================================================
        # Por que existe esta margem?
        # Na prática operacional, existem situações onde o material já foi enviado
        # ou recebido, mas ainda não está registrado no sistema:
        #   - Material em transporte pelo fornecedor
        #   - Item já chegou fisicamente, mas está em pré-nota
        #   - Nota fiscal ainda não integrada ao sistema
        #   - Material já sendo utilizado na fábrica, mas sem atualização final
        #
        # A tolerância evita que essas situações sejam classificadas como atraso,
        # tornando o indicador mais realista para a operação.
        #
        # PARA ALTERAR A TOLERÂNCIA: Modifique o valor abaixo (em dias)
        # ========================================================================
        OTD_TOLERANCIA_DIAS = 7  # Margem de tolerância em dias para classificar atraso
        # ========================================================================
        
        # Calcula OTD (On-Time Delivery): Data Necessidade SC x Data Recebimento NF
        # O valor calculado representa quantos dias após (positivo) ou antes (negativo)
        # da data de necessidade o material foi recebido
        df['OTD'] = (df['DataRecebimento'] - df['DataNecessidade']).dt.days
        
        # Remove valores negativos (dados inconsistentes)
        df.loc[df['LeadTime_Interno'] < 0, 'LeadTime_Interno'] = None
        df.loc[df['LeadTime_Fornecedor'] < 0, 'LeadTime_Fornecedor'] = None
        
        # Conta entregas no prazo - COM MARGEM DE TOLERÂNCIA
        # Considera apenas itens com DataRecebimento E DataNecessidade válidos
        # REGRA: Entrega é considerada "No Prazo" se OTD <= TOLERÂNCIA
        # Ou seja, até X dias após a data de necessidade ainda é considerado no prazo
        df_otd_valido = df[(df['DataRecebimento'].notna()) & (df['DataNecessidade'].notna())]
        entregas_no_prazo = (df_otd_valido['OTD'] <= OTD_TOLERANCIA_DIAS).sum()
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
            # REGRA COM TOLERÂNCIA: Até X dias após a data de necessidade = No Prazo
            df_mensal['StatusAtendimento'] = df_mensal['OTD'].apply(
                lambda x: 'No Prazo' if x <= OTD_TOLERANCIA_DIAS else 'Atrasado'
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
        # REGRA COM TOLERÂNCIA: Só considera atraso se OTD > TOLERÂNCIA
        df_atrasados = df_otd_valido[df_otd_valido['OTD'] > OTD_TOLERANCIA_DIAS].copy() if not df_otd_valido.empty else pd.DataFrame()
        
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
        # Agora inclui StatusSolicitacao para identificar se o atraso foi causado por:
        # - Falha de compras (solicitação Normal)
        # - Prazo inviável do solicitante (Prazo Incompatível)
        if not df_atrasados.empty:
            # Inclui colunas adicionais necessárias para o cálculo do status
            colunas_necessarias = ['NumeroPedido', 'CodFornecedor', 'NomeFornecedor', 
                                   'CodProduto', 'DescricaoProduto', 
                                   'DataNecessidade', 'DataRecebimento', 'OTD',
                                   'DataSolicitacao']
            
            # Adiciona LeadTimePadrao se existir
            if 'LeadTimePadrao' in df_atrasados.columns:
                colunas_necessarias.append('LeadTimePadrao')
            
            pedidos_atrasados = df_atrasados[colunas_necessarias].copy()
            
            # Trata valores NaN antes de converter para int
            pedidos_atrasados['DiasAtraso'] = pedidos_atrasados['OTD'].fillna(0).abs().round(0).astype(int)
            
            # ========================================
            # CÁLCULO DO STATUS DA SOLICITAÇÃO
            # ========================================
            # Reutiliza a mesma lógica de solicitações:
            # - NORMAL: Solicitação feita com prazo compatível com lead time
            # - PRAZO INCOMPATÍVEL: Solicitação com prazo menor que o lead time
            # OBS: Não inclui "ATRASADO" pois o card já trata de atrasos
            def calcular_status_solicitacao(row):
                """
                Calcula se a solicitação original era viável ou não.
                ORIGEM DOS DADOS:
                - DataSolicitacao = SC1010.C1_EMISSAO (data de emissão da SC)
                - DataNecessidade = SC1010.C1_DATPRF (data de necessidade)
                - LeadTimePadrao = SB1010.B1_PE (lead time do produto em dias)
                
                REGRA:
                PrazoSolicitado = DataNecessidade - DataSolicitacao
                Se PrazoSolicitado < LeadTimePadrao → "PRAZO INCOMPATÍVEL"
                Caso contrário → "NORMAL"
                """
                data_necessidade = row.get('DataNecessidade')
                data_emissao = row.get('DataSolicitacao')
                lead_time = row.get('LeadTimePadrao', 0) or 0
                
                # Se não tem dados para calcular, considera Normal
                if pd.isnull(data_necessidade) or pd.isnull(data_emissao):
                    return 'NORMAL'
                
                # Se tem lead time, verifica compatibilidade do prazo
                if lead_time > 0:
                    prazo_solicitado = (data_necessidade - data_emissao).days
                    if prazo_solicitado < lead_time:
                        return 'PRAZO INCOMPATÍVEL'
                
                return 'NORMAL'
            
            pedidos_atrasados['StatusSolicitacao'] = pedidos_atrasados.apply(calcular_status_solicitacao, axis=1)
            
            # Ordenar por dias de atraso (mais atrasados primeiro) - sem limitação de quantidade
            pedidos_atrasados = pedidos_atrasados.sort_values('DiasAtraso', ascending=False)
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
        
        # Detecta se o filtro aplicado é de um mês específico (para indicar no template)
        filtro_mes_ativo = False
        filtro_mes_label = ''
        if filtros.get('data_inicio') and filtros.get('data_fim'):
            from datetime import datetime
            try:
                data_ini = datetime.strptime(filtros['data_inicio'], '%Y-%m-%d')
                data_fim = datetime.strptime(filtros['data_fim'], '%Y-%m-%d')
                # Verifica se é um mês completo (dia 1 até último dia do mesmo mês)
                if data_ini.day == 1 and data_ini.year == data_fim.year and data_ini.month == data_fim.month:
                    # É um filtro de mês específico
                    from calendar import monthrange
                    ultimo_dia_mes = monthrange(data_ini.year, data_ini.month)[1]
                    if data_fim.day == ultimo_dia_mes:
                        filtro_mes_ativo = True
                        meses = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
                        filtro_mes_label = f"{meses[data_ini.month - 1]}/{data_ini.year}"
            except:
                pass
        
        # Prepara filtros para o template com valores padrão
        filtros_template = {
            'data_inicio': filtros.get('data_inicio') or get_data_inicio_padrao(),
            'data_fim': filtros.get('data_fim') or get_data_fim_padrao(),
            'busca_geral': filtros.get('busca_geral') or '',
            'comprador': filtros.get('comprador') or '',
            'fornecedores': filtros.get('fornecedores') or [],
            'filtro_mes_ativo': filtro_mes_ativo,
            'filtro_mes_label': filtro_mes_label
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
        # REGRA COM TOLERÂNCIA: Só considera atraso se OTD > 7 dias
        OTD_TOLERANCIA_DIAS = 7  # Manter sincronizado com a constante principal
        df_atrasados = df_mes[df_mes['OTD'] > OTD_TOLERANCIA_DIAS].copy()
        
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
    Análise de Variação de Preço - Saving, Inflation, Cost Avoidance, Primeira Compra.
    
    DOIS MODOS DE OPERAÇÃO:
    
    1. MODO EXECUTIVO (sem busca): Visão consolidada
       - 1 linha por produto (compra mais recente)
       - Comparação par-a-par
       - Gráficos por comprador e mensal
       
    2. MODO ANALÍTICO (com busca): Visão histórica profunda
       - TODAS as compras do item no período
       - Gráfico de evolução de preço
       - Histórico completo de transações
    """
    df_raw = get_variacao_preco_data()
    
    # Template de erro
    template_erro = {
        'erro': "Não foi possível carregar os dados.",
        'total_pago_mais': 0, 'total_pago_menos': 0, 'valor_cost_avoidance': 0,
        'saldo_liquido': 0, 'total_gasto': 0,
        'volume_primeira_compra': 0,
        'count_saving': 0, 'count_inflacao': 0, 'count_cost_avoidance': 0, 'count_primeira_compra': 0,
        'count_compras': 0,
        'items': [], 'compradores': [], 'tipos_produto': [], 'fornecedores': [],
        'chart_compradores': {'labels': [], 'saving': [], 'inflation': [], 'cost_avoidance': []},
        'chart_meses': {'labels': [], 'saving': [], 'inflation': [], 'cost_avoidance': []},
        'chart_evolucao': {'labels': [], 'precos': [], 'pedidos': []},
        'total_items': 0, 'comprador_selecionado': 'Todos',
        'tipo_selecionado': 'Todos', 'data_inicio': '', 'data_fim': '',
        'classificacao_selecionada': 'Todos',
        'fornecedores_selecionados': [], 'busca_geral': '',
        'modo_analitico': False, 'produto_analisado': None
    }
    
    if df_raw is None or df_raw.empty:
        return render_template('variacao.html', **template_erro)
    
    # =====================================================
    # PASSO 1: Capturar filtros do formulário
    # =====================================================
    comprador_filtro = request.form.get('comprador', 'Todos')
    tipo_filtro = request.form.get('tipo_produto', 'Todos')
    data_inicio = request.form.get('data_inicio', '') or get_data_inicio_padrao()
    data_fim = request.form.get('data_fim', '') or get_data_fim_padrao()
    classificacao_filtro = request.form.get('classificacao', 'Todos')
    fornecedores_filtro = request.form.getlist('fornecedores')  # Lista de fornecedores selecionados
    busca_geral = request.form.get('busca_geral', '').strip().upper()  # Busca geral
    
    # Opções para dropdowns (do dataset completo)
    # NOTA: Filtro de tipos BN, SV, PR já aplicado no SQL (get_variacao_preco_data)
    # Não é necessário refiltrar aqui
    
    compradores = sorted(df_raw['NomeComprador'].dropna().unique().tolist())
    tipos_produto = sorted(df_raw['TipoProduto'].dropna().astype(str).unique().tolist())
    fornecedores = sorted(df_raw['NomeFornecedor'].dropna().unique().tolist())
    
    # =====================================================
    # PASSO 2: Filtrar APENAS por DataEmissao (compra atual)
    # A coluna DataCompraAnterior NÃO é filtrada!
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
    
    # Filtro: Fornecedores (múltipla seleção)
    if fornecedores_filtro and len(fornecedores_filtro) > 0:
        df = df[df['NomeFornecedor'].isin(fornecedores_filtro)]
    
    # =====================================================
    # FILTRO: BUSCA GERAL / MODO ANALÍTICO
    # =====================================================
    # CORREÇÃO: O modo analítico (busca) deve ser ESTRITO por código de produto
    # 
    # Lógica:
    # 1. Se o termo de busca corresponde EXATAMENTE a um código de produto existente,
    #    filtrar APENAS por esse código (isolamento total)
    # 2. Caso contrário, usar busca genérica em múltiplos campos
    #
    # Isso evita que NFs de outros produtos "vazem" para o resultado quando
    # o número da NF/pedido coincide parcialmente com o código do produto pesquisado
    # =====================================================
    modo_analitico = bool(busca_geral)
    produto_analisado = None
    busca_termo = busca_geral  # Guardar para uso posterior
    
    if busca_geral:
        # Normalizar: remover espaços e converter para maiúsculas
        busca_normalizada = busca_geral.strip().upper()
        
        # Verificar se existe um produto com código EXATO (match completo)
        # Isso garante isolamento total quando o usuário busca por código de produto
        codigos_existentes = df['CodProduto'].str.strip().str.upper().unique()
        
        if busca_normalizada in codigos_existentes:
            # MODO ANALÍTICO ESTRITO: Busca EXATA por código de produto
            # Todos os dados (KPIs, gráfico, tabela) serão EXCLUSIVAMENTE deste produto
            df = df[df['CodProduto'].str.strip().str.upper() == busca_normalizada]
        else:
            # MODO BUSCA GENÉRICA: O termo não é um código de produto exato
            # Buscar em múltiplos campos (descrição, nota, pedido)
            # NOTA: Usar correspondência parcial apenas para descrição
            filtro_busca = (
                df['CodProduto'].str.upper().str.contains(busca_geral, na=False, regex=False) |
                df['DescricaoProduto'].str.upper().str.contains(busca_geral, na=False, regex=False)
            )
            df = df[filtro_busca]
        
        # CORREÇÃO: NÃO calcular total_compras aqui - será feito APÓS drop_duplicates
    
    if df.empty:
        template_erro['compradores'] = compradores
        template_erro['tipos_produto'] = tipos_produto
        template_erro['fornecedores'] = fornecedores
        template_erro['comprador_selecionado'] = comprador_filtro
        template_erro['tipo_selecionado'] = tipo_filtro
        template_erro['fornecedores_selecionados'] = fornecedores_filtro
        template_erro['busca_geral'] = busca_geral
        template_erro['data_inicio'] = data_inicio
        template_erro['data_fim'] = data_fim
        template_erro['classificacao_selecionada'] = classificacao_filtro
        template_erro['modo_analitico'] = modo_analitico
        template_erro['erro'] = "Nenhum registro encontrado para o período selecionado."
        return render_template('variacao.html', **template_erro)
    
    # =====================================================
    # PASSO 3: PROCESSAMENTO CONFORME MODO
    # MODO EXECUTIVO: 1 linha por produto (deduplicar)
    # MODO ANALÍTICO: todas as NFs (cada NF é um evento de compra único)
    # =====================================================
    df = df.sort_values('DataEmissao', ascending=False)
    
    # IMPORTANTE: Garantir unicidade por NOTA FISCAL (NumeroNota + SerieNota + CodFornecedor + CodProduto)
    # Cada NF é um evento de compra independente - não deduplicar por pedido!
    df = df.drop_duplicates(subset=['NumeroNota', 'SerieNota', 'CodFornecedor', 'CodProduto'], keep='first')
    
    # CORREÇÃO: Calcular produto_analisado APÓS drop_duplicates para contagem correta
    if modo_analitico and not df.empty:
        produtos_encontrados = df['CodProduto'].nunique()
        total_nfs = len(df)  # Total de NOTAS FISCAIS únicas
        if produtos_encontrados == 1:
            produto_analisado = {
                'codigo': df.iloc[0]['CodProduto'],
                'descricao': df.iloc[0]['DescricaoProduto'][:60],
                'total_compras': total_nfs  # Contagem de NFs (eventos reais de compra)
            }
        else:
            produto_analisado = {
                'codigo': f'{produtos_encontrados} produtos',
                'descricao': f'Busca: "{busca_termo}"',
                'total_compras': total_nfs
            }
    
    if modo_analitico:
        # MODO ANALÍTICO: manter TODAS as NFs únicas para histórico completo
        df_unico = df.copy()
        df_para_kpis = df.copy()  # KPIs também usam todas as NFs
    else:
        # MODO EXECUTIVO: TABELA mostra apenas a NF mais recente por produto
        # Mas os KPIs devem SOMAR TODAS as NFs para representar o impacto total
        df_unico = df.drop_duplicates(subset=['CodProduto'], keep='first').copy()  # Para tabela
        df_para_kpis = df.copy()  # Para KPIs: TODAS as NFs
    
    # =====================================================
    # PASSO 4: CLASSIFICAÇÃO MUTUAMENTE EXCLUSIVA
    # =====================================================
    def classificar_variacao(row):
        """
        Classifica cada item conforme regras de negócio:
        - Primeira Compra: sem baseline (PrecoAnterior é NULL)
        - Saving: preço atual < preço anterior (redução > 0.01)
        - Inflation: variação percentual > 1% (aumento significativo)
        - Cost Avoidance: variação percentual <= 1% (preço mantido/estável)
        """
        preco_atual = row['PrecoUnitario']
        preco_anterior = row['PrecoAnterior']
        
        # Verifica se existe baseline válido
        if pd.isna(preco_anterior) or preco_anterior <= 0:
            return 'Primeira Compra'
        
        # Diferença unitária
        diferenca = preco_atual - preco_anterior
        
        # Tolerância para evitar erros de arredondamento
        tolerancia = 0.01
        
        # Se houve redução de preço = Saving
        if diferenca < -tolerancia:
            return 'Saving'
        
        # Calcular variação percentual para aumento
        variacao_percentual = (diferenca / preco_anterior) * 100
        
        # Se variação > 1% = Inflation, senão = Cost Avoidance
        if variacao_percentual > 1.0:
            return 'Inflation'
        else:
            return 'Cost Avoidance'
    
    df_unico['Classificacao'] = df_unico.apply(classificar_variacao, axis=1)
    
    # Aplicar classificação também no df_para_kpis
    df_para_kpis['Classificacao'] = df_para_kpis.apply(classificar_variacao, axis=1)
    
    # =====================================================
    # PASSO 5: CÁLCULO DO IMPACTO FINANCEIRO
    # =====================================================
    def calcular_impacto(row):
        """
        Calcula impacto financeiro conforme regras:
        - Saving: impacto negativo (economia)
        - Inflation: impacto positivo (gasto adicional)
        - Cost Avoidance: impacto = 0
        - Primeira Compra: impacto = NULL/N/A
        """
        classificacao = row['Classificacao']
        
        if classificacao == 'Primeira Compra':
            return None  # Sem impacto calculável
        
        preco_atual = row['PrecoUnitario']
        preco_anterior = row['PrecoAnterior']
        qtd = row['QtdPedida']
        
        if classificacao == 'Cost Avoidance':
            return 0.0
        
        # Diferença Unitária × Quantidade
        diferenca_unitaria = preco_atual - preco_anterior
        impacto = diferenca_unitaria * qtd
        
        return impacto
    
    df_unico['ImpactoFinanceiro'] = df_unico.apply(calcular_impacto, axis=1)
    df_para_kpis['ImpactoFinanceiro'] = df_para_kpis.apply(calcular_impacto, axis=1)
    
    # =====================================================
    # PASSO 6: FILTRO DE CLASSIFICAÇÃO (após calcular)
    # =====================================================
    if classificacao_filtro and classificacao_filtro != 'Todos':
        df_unico = df_unico[df_unico['Classificacao'] == classificacao_filtro]
        df_para_kpis = df_para_kpis[df_para_kpis['Classificacao'] == classificacao_filtro]
    
    # =====================================================
    # PASSO 7: CALCULAR KPIs EXECUTIVOS
    # IMPORTANTE: Usar df_unico para consistência com gráficos (1 linha por produto)
    # =====================================================
    df_saving = df_para_kpis[df_para_kpis['Classificacao'] == 'Saving']
    df_inflation = df_para_kpis[df_para_kpis['Classificacao'] == 'Inflation']
    df_cost_avoidance = df_para_kpis[df_para_kpis['Classificacao'] == 'Cost Avoidance']
    df_primeira_compra = df_para_kpis[df_para_kpis['Classificacao'] == 'Primeira Compra']
    
    # Contagens baseadas em df_unico para consistência com gráficos (1 linha por produto)
    count_saving = len(df_unico[df_unico['Classificacao'] == 'Saving'])
    count_inflacao = len(df_unico[df_unico['Classificacao'] == 'Inflation'])
    count_cost_avoidance = len(df_unico[df_unico['Classificacao'] == 'Cost Avoidance'])
    count_primeira_compra = len(df_unico[df_unico['Classificacao'] == 'Primeira Compra'])
    
    # Total Pago a Mais (soma dos impactos positivos - Inflation)
    # CORREÇÃO: Usar df_unico para consistência com gráficos (1 linha por produto)
    impactos_positivos = df_unico[df_unico['ImpactoFinanceiro'].notna() & (df_unico['ImpactoFinanceiro'] > 0)]['ImpactoFinanceiro']
    total_pago_mais = impactos_positivos.sum() if len(impactos_positivos) > 0 else 0
    
    # Total Pago a Menos (soma absoluta dos impactos negativos - Saving)
    impactos_negativos = df_unico[df_unico['ImpactoFinanceiro'].notna() & (df_unico['ImpactoFinanceiro'] < 0)]['ImpactoFinanceiro']
    total_pago_menos = abs(impactos_negativos.sum()) if len(impactos_negativos) > 0 else 0
    
    # NOTA: Valor de Cost Avoidance = soma do ValorTotal das compras onde preço foi mantido
    
    # Valor de Cost Avoidance = soma dos valores totais das transações com preço mantido
    # CORREÇÃO: Usar df_unico para consistência com os gráficos (1 linha por produto)
    df_cost_avoidance_unico = df_unico[df_unico['Classificacao'] == 'Cost Avoidance']
    valor_cost_avoidance = df_cost_avoidance_unico['ValorTotal'].sum() if len(df_cost_avoidance_unico) > 0 else 0
    
    # =====================================================
    # COST AVOIDANCE ACUMULADO (APENAS modo analítico)
    # Calcula a economia acumulada quando um preço reduzido é mantido ao longo do tempo
    # NOTA: Só faz sentido no modo analítico onde temos o histórico completo de um produto
    # No modo executivo, cada produto tem apenas 1 linha (a mais recente), impossibilitando
    # a análise sequencial necessária para calcular a economia continuada.
    # =====================================================
    cost_avoidance_acumulado = 0
    
    if modo_analitico and not df_unico.empty:
        # MODO ANALÍTICO: um único produto, análise sequencial simples
        df_ordenado = df_unico.sort_values('DataEmissao', ascending=True).copy()
        
        # Identificar o menor preço já praticado e quando foi atingido
        preco_referencia = None
        preco_anterior_ref = None
        
        for idx, row in df_ordenado.iterrows():
            preco_atual = row['PrecoUnitario']
            preco_anterior = row.get('PrecoAnterior')
            classificacao = row['Classificacao']
            qtd = row['QtdPedida']
            
            # Se houve Saving, atualiza a referência de preço reduzido
            if classificacao == 'Saving' and pd.notna(preco_anterior) and preco_anterior > 0:
                economia_unitaria = preco_anterior - preco_atual
                preco_referencia = preco_atual
                preco_anterior_ref = preco_anterior
            
            # Se é Cost Avoidance (preço mantido) E temos uma referência de preço reduzido
            elif classificacao == 'Cost Avoidance' and preco_referencia is not None and preco_anterior_ref is not None:
                # O preço foi mantido no nível reduzido - isso é economia continuada
                # Economia = (Preço que pagávamos antes da redução - Preço atual) × Quantidade
                economia_acumulada = (preco_anterior_ref - preco_atual) * qtd
                if economia_acumulada > 0:
                    cost_avoidance_acumulado += economia_acumulada
    
    # Saldo Líquido (economia - inflação)
    saldo_liquido = total_pago_menos - total_pago_mais
    
    # Total Gasto no Período
    total_gasto = df_unico['ValorTotal'].sum()
    
    # Quantidade de compras (para modo analítico)
    count_compras = len(df_unico)
    
    # Volume de Primeiras Compras
    volume_primeira_compra = count_primeira_compra
    
    # =====================================================
    # PASSO 8: GRÁFICO DE EVOLUÇÃO (APENAS MODO ANALÍTICO)
    # =====================================================
    chart_evolucao = {'labels': [], 'precos': [], 'pedidos': [], 'notas': []}
    
    if modo_analitico and not df_unico.empty:
        # Ordenar por data de emissão (mais antiga primeiro)
        df_evolucao = df_unico.sort_values('DataEmissao', ascending=True)
        
        chart_evolucao = {
            'labels': [d.strftime('%d/%m/%Y') for d in df_evolucao['DataEmissao']],
            'precos': [float(p) for p in df_evolucao['PrecoUnitario']],
            'pedidos': [str(p) for p in df_evolucao['NumeroPedido']],
            'notas': [str(n) for n in df_evolucao['NumeroNota']]  # Número da NF
        }
    
    # =====================================================
    # PASSO 9: PREPARAR DADOS PARA TABELA
    # =====================================================
    if modo_analitico:
        # Modo Analítico: ordenar por data (mais recente primeiro)
        df_unico = df_unico.sort_values('DataEmissao', ascending=False)
    else:
        # Modo Executivo: ordenar por impacto
        df_unico = df_unico.sort_values('ImpactoFinanceiro', ascending=True, na_position='last')
    
    # Carregar TODOS os registros sem limite
    items = []
    for _, row in df_unico.iterrows():
        # Determinar se a compra anterior é muito antiga (> 2 anos)
        data_anterior = row.get('DataCompraAnterior')
        compra_antiga = False
        if pd.notna(data_anterior):
            anos_atras = (row['DataEmissao'] - data_anterior).days / 365
            compra_antiga = anos_atras > 2
        
        items.append({
            'NumeroNota': str(row.get('NumeroNota', '')),  # Número da NF (identificador principal)
            'SerieNota': str(row.get('SerieNota', '')),
            'NumeroPedido': str(row.get('NumeroPedido', '')),  # Referência ao pedido original
            'ItemPedido': str(row.get('ItemPedido', '')),
            'DataEmissao': row['DataEmissao'].strftime('%d/%m/%Y') if pd.notna(row.get('DataEmissao')) else '',
            'CodProduto': str(row.get('CodProduto', '')),
            'DescricaoProduto': str(row.get('DescricaoProduto', ''))[:60],
            'TipoProduto': str(row.get('TipoProduto', '')),
            'QtdPedida': float(row.get('QtdPedida', 0)),  # QtdRecebida na NF (renomeada para compatibilidade)
            'PrecoUnitarioAtual': float(row.get('PrecoUnitario', 0)),
            'PrecoUnitarioAnterior': float(row.get('PrecoAnterior', 0)) if pd.notna(row.get('PrecoAnterior')) else None,
            'DataCompraAnterior': data_anterior.strftime('%d/%m/%Y') if pd.notna(data_anterior) else None,
            'NotaAnterior': str(row.get('NotaAnterior', '')) if pd.notna(row.get('NotaAnterior')) else None,
            'CompraAntiga': compra_antiga,
            'CodFornecedor': str(row.get('CodFornecedor', '')),
            'FornecedorAtual': str(row.get('NomeFornecedor', ''))[:35],
            'FornecedorAnterior': str(row.get('FornecedorAnteriorNome', ''))[:35] if row.get('FornecedorAnteriorNome') else None,
            'NomeComprador': str(row.get('NomeComprador', '')),
            'Classificacao': row.get('Classificacao', 'Primeira Compra'),
            'ImpactoFinanceiro': float(row.get('ImpactoFinanceiro', 0)) if pd.notna(row.get('ImpactoFinanceiro')) else None,
            'ValorTotal': float(row.get('ValorTotal', 0))
        })
    
    # =====================================================
    # PASSO 10: DADOS PARA GRÁFICOS (apenas modo executivo)
    # =====================================================
    chart_compradores = {'labels': [], 'saving': [], 'inflation': [], 'cost_avoidance': []}
    chart_meses = {'labels': [], 'saving': [], 'inflation': [], 'cost_avoidance': []}
    
    if not modo_analitico:
        # Gráficos só no modo executivo
        df_com_impacto = df_unico[df_unico['Classificacao'] != 'Primeira Compra'].copy()
        
        if not df_com_impacto.empty:
            # === GRÁFICO POR COMPRADOR (3 barras por comprador) ===
            compradores_list = df_com_impacto['NomeComprador'].unique().tolist()
            
            # Calcular valores por classificação e comprador
            saving_por_comprador = df_com_impacto[df_com_impacto['Classificacao'] == 'Saving'].groupby('NomeComprador')['ImpactoFinanceiro'].sum().abs()
            inflation_por_comprador = df_com_impacto[df_com_impacto['Classificacao'] == 'Inflation'].groupby('NomeComprador')['ImpactoFinanceiro'].sum()
            cost_av_por_comprador = df_com_impacto[df_com_impacto['Classificacao'] == 'Cost Avoidance'].groupby('NomeComprador')['ValorTotal'].sum()
            
            chart_compradores = {
                'labels': compradores_list,
                'saving': [float(saving_por_comprador.get(c, 0)) for c in compradores_list],
                'inflation': [float(inflation_por_comprador.get(c, 0)) for c in compradores_list],
                'cost_avoidance': [float(cost_av_por_comprador.get(c, 0)) for c in compradores_list]
            }
            
            # === GRÁFICO POR MÊS (3 barras por mês) ===
            df_com_impacto['MesAno'] = df_com_impacto['DataEmissao'].dt.to_period('M').astype(str)
            meses_list = sorted(df_com_impacto['MesAno'].unique().tolist())
            
            # Calcular valores por classificação e mês
            saving_por_mes = df_com_impacto[df_com_impacto['Classificacao'] == 'Saving'].groupby('MesAno')['ImpactoFinanceiro'].sum().abs()
            inflation_por_mes = df_com_impacto[df_com_impacto['Classificacao'] == 'Inflation'].groupby('MesAno')['ImpactoFinanceiro'].sum()
            cost_av_por_mes = df_com_impacto[df_com_impacto['Classificacao'] == 'Cost Avoidance'].groupby('MesAno')['ValorTotal'].sum()
            
            chart_meses = {
                'labels': meses_list,
                'saving': [float(saving_por_mes.get(m, 0)) for m in meses_list],
                'inflation': [float(inflation_por_mes.get(m, 0)) for m in meses_list],
                'cost_avoidance': [float(cost_av_por_mes.get(m, 0)) for m in meses_list]
            }
    
    # =====================================================
    # PASSO 11: RENDERIZAR TEMPLATE
    # =====================================================
    return render_template('variacao.html',
        # Modo de operação
        modo_analitico=modo_analitico,
        produto_analisado=produto_analisado,
        # KPIs
        total_pago_mais=total_pago_mais,
        total_pago_menos=total_pago_menos,
        valor_cost_avoidance=valor_cost_avoidance,
        cost_avoidance_acumulado=cost_avoidance_acumulado,
        saldo_liquido=saldo_liquido,
        total_gasto=total_gasto,
        volume_primeira_compra=volume_primeira_compra,
        # Contadores
        count_saving=count_saving,
        count_inflacao=count_inflacao,
        count_cost_avoidance=count_cost_avoidance,
        count_primeira_compra=count_primeira_compra,
        count_compras=count_compras,
        # Dados
        items=items,
        compradores=compradores,
        tipos_produto=tipos_produto,
        fornecedores=fornecedores,
        comprador_selecionado=comprador_filtro,
        tipo_selecionado=tipo_filtro,
        fornecedores_selecionados=fornecedores_filtro,
        busca_geral=busca_geral,
        data_inicio=data_inicio,
        data_fim=data_fim,
        classificacao_selecionada=classificacao_filtro,
        # Gráficos
        chart_compradores=chart_compradores,
        chart_meses=chart_meses,
        chart_evolucao=chart_evolucao,
        total_items=len(df_unico),
        erro=None
    )


# =============================================================================
# FUNCIONALIDADE: EXTRAÇÃO DE COMPRAS POR FORNECEDOR
# Para gestão de lead time e negociação de preços
# =============================================================================

# Dicionário global para armazenar progresso das extrações
extraction_progress = {}

@app.route('/api/progresso_extracao/<extraction_id>')
def api_progresso_extracao(extraction_id):
    """
    Retorna o progresso atual de uma extração.
    """
    if extraction_id in extraction_progress:
        prog = extraction_progress[extraction_id]
        return jsonify({
            'success': True,
            'progresso': prog['progresso'],
            'etapa': prog['etapa']
        })
    # Se não existe ainda, retorna valores iniciais
    return jsonify({'success': True, 'progresso': 0, 'etapa': 'Aguardando início...'})


def atualizar_progresso(extraction_id, progresso, etapa):
    """Atualiza o progresso de uma extração."""
    extraction_progress[extraction_id] = {
        'progresso': progresso,
        'etapa': etapa
    }
    print(f"[PROGRESSO] {extraction_id}: {progresso}% - {etapa}")


@app.route('/api/fornecedores_por_ano/<int:ano>')
def api_fornecedores_por_ano(ano):
    """
    Retorna lista de fornecedores que tiveram compras no ano especificado.
    """
    try:
        df = get_database_data()
        if df is None or df.empty:
            return jsonify({'success': False, 'message': 'Sem dados disponíveis'})
        
        # Filtra pelo ano
        df = df[df['DataEmissao'].dt.year == ano]
        
        if df.empty:
            return jsonify({'success': True, 'fornecedores': []})
        
        # Lista de fornecedores únicos (código e nome)
        fornecedores = df[['CodFornecedor', 'NomeFornecedor']].drop_duplicates()
        fornecedores = fornecedores.sort_values('NomeFornecedor')
        
        lista_fornecedores = [
            {'codigo': str(row['CodFornecedor']).strip(), 'nome': str(row['NomeFornecedor']).strip()}
            for _, row in fornecedores.iterrows()
        ]
        
        return jsonify({'success': True, 'fornecedores': lista_fornecedores})
    
    except Exception as e:
        print(f"[ERRO] api_fornecedores_por_ano: {e}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/extrair_compras_fornecedor', methods=['POST'])
def api_extrair_compras_fornecedor():
    """
    Extrai compras por fornecedor(es) para gestão de lead time e negociação de preços.
    Gera planilha Excel (.xlsx) com:
    - Aba principal: dados consolidados por fornecedor + item
    - Aba Resumo: KPIs por fornecedor
    """
    extraction_id = 'default'
    try:
        data = request.get_json()
        fornecedores_selecionados = data.get('fornecedores', [])  # Lista de códigos
        ano = data.get('ano')
        extraction_id = data.get('extraction_id', 'default')
        
        print(f"[EXTRAÇÃO] Iniciando - ID: {extraction_id}")
        print(f"[EXTRAÇÃO] Fornecedores: {fornecedores_selecionados}")
        print(f"[EXTRAÇÃO] Ano: {ano}")
        
        if not fornecedores_selecionados:
            return jsonify({'success': False, 'message': 'Selecione pelo menos um fornecedor'})
        
        if not ano:
            return jsonify({'success': False, 'message': 'Selecione o ano'})
        
        ano = int(ano)
        
        # =====================================================
        # ETAPA 1: Carregando dados (0-20%)
        # =====================================================
        atualizar_progresso(extraction_id, 5, 'Conectando ao banco de dados...')
        
        df = get_database_data()
        if df is None or df.empty:
            print(f"[EXTRAÇÃO] ERRO: Sem dados do banco")
            return jsonify({'success': False, 'message': 'Sem dados disponíveis'})
        
        print(f"[EXTRAÇÃO] Dados carregados: {len(df)} registros")
        atualizar_progresso(extraction_id, 15, 'Filtrando dados por ano...')
        
        # Filtra pelo ano
        df = df[df['DataEmissao'].dt.year == ano]
        
        if df.empty:
            print(f"[EXTRAÇÃO] ERRO: Sem compras no ano {ano}")
            return jsonify({'success': False, 'message': f'Sem compras no ano {ano}'})
        
        print(f"[EXTRAÇÃO] Após filtro de ano: {len(df)} registros")
        atualizar_progresso(extraction_id, 25, 'Filtrando fornecedores selecionados...')
        
        # Filtra pelos fornecedores selecionados
        df = df[df['CodFornecedor'].astype(str).str.strip().isin(fornecedores_selecionados)]
        
        if df.empty:
            return jsonify({'success': False, 'message': 'Nenhuma compra encontrada para os fornecedores selecionados'})
        
        # =====================================================
        # ETAPA 2: Consolidação (20-50%)
        # =====================================================
        atualizar_progresso(extraction_id, 35, 'Consolidando itens por fornecedor...')
        
        df_consolidado = df.groupby([
            'CodFornecedor', 
            'NomeFornecedor', 
            'CodProduto', 
            'DescricaoProduto'
        ]).agg({
            'QtdPedida': 'sum',
            'NumeroPedido': 'count',
            'ValorTotal': 'sum'
        }).reset_index()
        
        atualizar_progresso(extraction_id, 45, 'Formatando colunas...')
        
        # Renomeia colunas para clareza
        df_consolidado = df_consolidado.rename(columns={
            'CodFornecedor': 'Código Fornecedor',
            'NomeFornecedor': 'Nome Fornecedor',
            'CodProduto': 'Código Item',
            'DescricaoProduto': 'Descrição Item',
            'QtdPedida': 'Quantidade Total Comprada',
            'NumeroPedido': 'Vezes Comprado no Ano',
            'ValorTotal': 'Valor Total (R$)'
        })
        
        # Adiciona coluna Lead Time (vazia para preenchimento pelo fornecedor)
        df_consolidado['Lead Time (dias)'] = ''
        
        # Ordena por fornecedor e depois por valor (maior primeiro)
        df_consolidado = df_consolidado.sort_values(
            ['Nome Fornecedor', 'Valor Total (R$)'], 
            ascending=[True, False]
        )
        
        # =====================================================
        # ETAPA 3: Geração do Resumo (50-70%)
        # =====================================================
        atualizar_progresso(extraction_id, 55, 'Gerando resumo por fornecedor...')
        
        resumo_data = []
        total_fornecedores = len(fornecedores_selecionados)
        
        for idx, cod_forn in enumerate(fornecedores_selecionados):
            progresso_parcial = 55 + (idx / total_fornecedores) * 15
            atualizar_progresso(extraction_id, progresso_parcial, f'Processando fornecedor {idx + 1}/{total_fornecedores}...')
            
            df_forn = df[df['CodFornecedor'].astype(str).str.strip() == cod_forn]
            
            if df_forn.empty:
                continue
            
            nome_forn = df_forn['NomeFornecedor'].iloc[0]
            total_gasto = df_forn['ValorTotal'].sum()
            skus_unicos = df_forn['CodProduto'].nunique()
            
            top10_itens = df_forn.groupby(['CodProduto', 'DescricaoProduto']).agg({
                'NumeroPedido': 'count',
                'QtdPedida': 'sum',
                'ValorTotal': 'sum'
            }).reset_index()
            
            top10_itens = top10_itens.nlargest(10, 'NumeroPedido')
            
            top10_str = "; ".join([
                f"{row['CodProduto']} ({int(row['NumeroPedido'])}x)"
                for _, row in top10_itens.iterrows()
            ])
            
            resumo_data.append({
                'Código Fornecedor': cod_forn,
                'Nome Fornecedor': nome_forn,
                'Total Gasto no Ano (R$)': total_gasto,
                'SKUs Únicos': skus_unicos,
                'Total de Compras': len(df_forn),
                'Top 10 Itens (Código e Vezes)': top10_str
            })
        
        df_resumo = pd.DataFrame(resumo_data)
        
        if not df_resumo.empty:
            df_resumo = df_resumo.sort_values('Total Gasto no Ano (R$)', ascending=False)
        
        # =====================================================
        # ETAPA 4: Geração do Excel (70-95%)
        # =====================================================
        atualizar_progresso(extraction_id, 75, 'Criando arquivo Excel...')
        
        output = BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            atualizar_progresso(extraction_id, 80, 'Escrevendo dados consolidados...')
            
            df_consolidado.to_excel(
                writer, 
                sheet_name='Compras por Fornecedor', 
                index=False,
                float_format='%.2f'
            )
            
            atualizar_progresso(extraction_id, 85, 'Escrevendo resumo...')
            
            df_resumo.to_excel(
                writer, 
                sheet_name='Resumo', 
                index=False,
                float_format='%.2f'
            )
            
            atualizar_progresso(extraction_id, 90, 'Formatando planilha...')
            
            # Formata largura das colunas - Aba Principal
            worksheet_principal = writer.sheets['Compras por Fornecedor']
            col_widths_principal = {
                'A': 18, 'B': 40, 'C': 15, 'D': 50,
                'E': 25, 'F': 20, 'G': 18, 'H': 18,
            }
            for col, width in col_widths_principal.items():
                worksheet_principal.column_dimensions[col].width = width
            
            # Formata largura das colunas - Aba Resumo
            worksheet_resumo = writer.sheets['Resumo']
            col_widths_resumo = {
                'A': 18, 'B': 40, 'C': 25, 'D': 15, 'E': 18, 'F': 80,
            }
            for col, width in col_widths_resumo.items():
                worksheet_resumo.column_dimensions[col].width = width
        
        output.seek(0)
        
        # =====================================================
        # ETAPA 5: Finalizando (95-100%)
        # =====================================================
        atualizar_progresso(extraction_id, 95, 'Preparando download...')
        
        # Nome do arquivo seguindo o padrão solicitado
        if len(fornecedores_selecionados) == 1:
            # Pega nome do fornecedor do resumo
            if not df_resumo.empty:
                nome_forn = df_resumo['Nome Fornecedor'].iloc[0]
                # Remove caracteres especiais e limita tamanho
                nome_formatado = re.sub(r'[^a-zA-Z0-9]', '_', nome_forn)[:30]
                nome_arquivo = f"Compras_{nome_formatado}_{ano}.xlsx"
            else:
                nome_arquivo = f"Compras_{fornecedores_selecionados[0]}_{ano}.xlsx"
        else:
            nome_arquivo = f"Compras_Multiplos_{ano}.xlsx"
        
        atualizar_progresso(extraction_id, 100, 'Concluído!')
        
        # Limpa progresso após um tempo
        def limpar_progresso():
            import time
            time.sleep(5)
            if extraction_id in extraction_progress:
                del extraction_progress[extraction_id]
        
        import threading
        threading.Thread(target=limpar_progresso, daemon=True).start()
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nome_arquivo
        )
    
    except Exception as e:
        import traceback
        print(f"[ERRO] api_extrair_compras_fornecedor: {e}")
        traceback.print_exc()
        if extraction_id in extraction_progress:
            del extraction_progress[extraction_id]
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/anos_disponiveis')
def api_anos_disponiveis():
    """
    Retorna lista de anos disponíveis nos dados de compras.
    """
    try:
        df = get_database_data()
        if df is None or df.empty:
            return jsonify({'success': False, 'message': 'Sem dados disponíveis'})
        
        # Extrai anos únicos
        anos = sorted(df['DataEmissao'].dt.year.unique().tolist(), reverse=True)
        
        return jsonify({'success': True, 'anos': anos})
    
    except Exception as e:
        print(f"[ERRO] api_anos_disponiveis: {e}")
        return jsonify({'success': False, 'message': str(e)})


# =============================================================================
# FUNÇÃO: BUSCAR DADOS DE TERCEIROS (INDUSTRIALIZAÇÃO EXTERNA)
# =============================================================================
# Filtros:
# - Apenas produtos cujo código inicia com 'BN'
# - Apenas pedidos em aberto (quantidade entregue < quantidade pedida)
# - Campos retornados conforme especificação
# =============================================================================
@cache.cached(timeout=3600, key_prefix='dados_terceiros_v2_bn')
def get_dados_terceiros():
    """
    Busca dados de terceiros (industrialização externa) do TOTVS Protheus.
    
    Filtros aplicados:
    - Apenas produtos cujo código (C7_PRODUTO) inicia com 'BN'
    - Apenas pedidos em aberto (C7_QUJE < C7_QUANT)
    
    Campos retornados:
    1. Produto OP (C7_PRODUTO)
    2. Descrição do Produto (C7_DESCRI)
    3. Programa OP (C7_OBS - quando aplicável)
    4. Quantidade do Item (C7_QUANT)
    5. Data de Emissão do Pedido (C7_EMISSAO)
    6. Data de Entrega Prevista (C7_DATPRF)
    7. Fornecedor (A2_NOME)
    8. Número do Pedido de Compra (C7_NUM)
    9. Item do Pedido de Compra (C7_ITEM)
    10. Dias em Aberto (calculado: GETDATE - C7_EMISSAO)
    """
    try:
        server = r'172.16.45.117\TOTVS' 
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str)
        
        # Query para buscar dados de terceiros - Produtos BN em aberto
        query_terceiros = """
        SELECT 
            ISNULL(OP.C2_PRODUTO, PC.C7_PRODUTO) AS [Produto OP],
            ISNULL(PROD.B1_DESC, PC.C7_DESCRI) + ' – ' + RTRIM(PC.C7_PRODUTO) AS [Descrição do Produto],
            ISNULL(OP.C2_POLPROG, '') AS [Programa OP],
            (PC.C7_QUANT - ISNULL(PC.C7_QUJE, 0)) AS [Quantidade do Item],
            PC.C7_EMISSAO       AS [Data de Emissão],
            PC.C7_DATPRF        AS [Data Entrega Prevista],
            FORN.A2_NOME        AS Fornecedor,
            PC.C7_NUM           AS [Número Pedido],
            PC.C7_ITEM          AS [Item Pedido],
            DATEDIFF(DAY, 
                CONVERT(DATE, PC.C7_EMISSAO, 112), 
                GETDATE()
            ) AS [Dias em Aberto],
            RTRIM(PC.C7_PRODUTO) AS [Codigo BN],
            RTRIM(ISNULL(FORN.A2_EMAIL, '')) AS [EmailFornecedor],
            REPLACE(REPLACE(REPLACE(REPLACE(NULLIF(RTRIM(FORN.A2_FAX), ''), ' ', ''), '-', ''), '(', ''), ')', '') AS [FaxWhatsAppLimpo],
            NULLIF(RTRIM(FORN.A2_FAX), '') AS [FaxWhatsAppOriginal]
            
        FROM SC7010 AS PC
        INNER JOIN SA2010 AS FORN 
            ON PC.C7_FORNECE = FORN.A2_COD 
            AND FORN.D_E_L_E_T_ = ''
        LEFT JOIN SC2010 AS OP
            ON PC.C7_OP = OP.C2_NUM + OP.C2_ITEM + OP.C2_SEQUEN
            AND OP.D_E_L_E_T_ = ''
        LEFT JOIN SB1010 AS PROD
            ON ISNULL(OP.C2_PRODUTO, PC.C7_PRODUTO) = PROD.B1_COD
            AND PROD.D_E_L_E_T_ = ''
        
        WHERE PC.D_E_L_E_T_ <> '*' 
          AND PC.C7_PRODUTO LIKE 'BN%'
          AND PC.C7_QUANT > ISNULL(PC.C7_QUJE, 0)
          AND (PC.C7_RESIDUO = '' OR PC.C7_RESIDUO IS NULL)
        
        ORDER BY DATEDIFF(DAY, CONVERT(DATE, PC.C7_EMISSAO, 112), GETDATE()) DESC, PC.C7_NUM, PC.C7_ITEM
        """
        
        df = pd.read_sql(query_terceiros, conn)
        conn.close()
        
        # Função para normalizar telefone para WhatsApp Brasil
        def normalizar_telefone_whatsapp(tel):
            """Normaliza telefone para formato WhatsApp Brasil: apenas DDD + 9 dígitos (sem 55)"""
            if not tel or pd.isna(tel): return ''
            # Remove tudo que não é dígito
            numero = ''.join(filter(str.isdigit, str(tel)))
            original = numero  # guardar para debug
            if not numero: return ''
            
            # Remove zeros à esquerda (formato antigo de discagem interurbana)
            numero = numero.lstrip('0')
            
            # Remove código do país se existir (55 no início)
            if numero.startswith('55') and len(numero) >= 12:
                numero = numero[2:]
            
            # Se tiver 10 dígitos (DDD + 8 dígitos antigo celular), adiciona o 9
            if len(numero) == 10:
                terceiro_digito = numero[2] if len(numero) > 2 else ''
                if terceiro_digito in ['9', '8', '7', '6']:
                    numero = numero[:2] + '9' + numero[2:]
                    print(f"[WHATSAPP DEBUG] OK (add 9): '{tel}' -> '{numero}'")
                    return numero
                else:
                    print(f"[WHATSAPP DEBUG] FIXO: '{tel}' -> telefone fixo, sem WhatsApp")
                    return ''
            
            # Se tiver 11 dígitos (DDD + 9 dígitos), verifica se é celular válido
            if len(numero) == 11:
                if numero[2] == '9':
                    print(f"[WHATSAPP DEBUG] OK: '{tel}' -> '{numero}'")
                    return numero
                else:
                    print(f"[WHATSAPP DEBUG] FIXO 11dig: '{tel}' -> telefone fixo")
                    return ''
            
            print(f"[WHATSAPP DEBUG] INVALIDO: '{tel}' -> {len(original)} digitos")
            return ''
        
        # Aplicar normalização no telefone
        df['FaxWhatsAppLimpo'] = df['FaxWhatsAppLimpo'].apply(normalizar_telefone_whatsapp)
        
        # Converter colunas de data
        colunas_data = ['Data de Emissão', 'Data Entrega Prevista']
        for col in colunas_data:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], format='%Y%m%d', errors='coerce')
        
        # Limpar colunas de texto (remover espaços)
        colunas_texto = ['Produto OP', 'Descrição do Produto', 'Programa OP', 'Fornecedor', 'Número Pedido', 'Item Pedido']
        for col in colunas_texto:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
        
        return df
        
    except Exception as e:
        print(f"[TERCEIROS] Erro SQL: {e}")
        return pd.DataFrame()
        
        # Limpar colunas de texto (remover espaços)
        colunas_texto = ['Produto OP', 'Descrição do Produto', 'Programa OP', 'Fornecedor', 'Número Pedido', 'Item Pedido']
        for col in colunas_texto:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
        
        return df
        
    except Exception as e:
        print(f"[TERCEIROS] Erro SQL: {e}")
        return pd.DataFrame()


# =============================================================================
# ROTA: CONTROLE DE TERCEIROS (INDUSTRIALIZAÇÃO EXTERNA)
# =============================================================================
# Filtros aplicados:
# - Apenas produtos cujo código inicia com 'BN'
# - Apenas pedidos em aberto (quantidade entregue < quantidade pedida)
# - Dias em Aberto calculado no SQL (GETDATE - C7_EMISSAO)
# =============================================================================
@app.route('/terceiros', methods=['GET', 'POST'])
def terceiros():
    """
    Aba de Controle de Terceiros - Industrialização Externa.
    Exibe exclusivamente pedidos de compra em aberto de produtos cujo código inicia com 'BN'.
    
    Campos exibidos:
    - Produto OP, Descrição do Produto, Programa OP, Quantidade do Item
    - Data de Emissão, Data Entrega Prevista, Fornecedor
    - Número Pedido, Item Pedido, Dias em Aberto
    """
    user = session.get('user')
    
    # Buscar dados de terceiros
    df_terceiros = get_dados_terceiros()
    
    # Extrair lista de fornecedores únicos
    opcoes_fornecedores = []
    if df_terceiros is not None and not df_terceiros.empty and 'Fornecedor' in df_terceiros.columns:
        opcoes_fornecedores = sorted(df_terceiros['Fornecedor'].dropna().unique().tolist())
    
    # Calcular KPIs
    if df_terceiros is not None and not df_terceiros.empty:
        total_pedidos = len(df_terceiros)
        
        # KPIs baseados em Dias em Aberto
        if 'Dias em Aberto' in df_terceiros.columns:
            criticos = len(df_terceiros[df_terceiros['Dias em Aberto'] > 30])  # Mais de 30 dias
            atencao = len(df_terceiros[(df_terceiros['Dias em Aberto'] > 15) & (df_terceiros['Dias em Aberto'] <= 30)])  # 15-30 dias
            normal = len(df_terceiros[df_terceiros['Dias em Aberto'] <= 15])  # Até 15 dias
            media_dias = int(df_terceiros['Dias em Aberto'].mean()) if not df_terceiros['Dias em Aberto'].empty else 0
        else:
            criticos = 0
            atencao = 0
            normal = 0
            media_dias = 0
        
        # Preparar tabela para JSON
        tabela_json = preparar_tabela_json(df_terceiros)
    else:
        total_pedidos = 0
        criticos = 0
        atencao = 0
        normal = 0
        media_dias = 0
        tabela_json = []
    
    dados = {
        'tabela': tabela_json,
        'opcoes_fornecedores': opcoes_fornecedores,
        'kpis': {
            'total_pedidos': total_pedidos,
            'criticos': criticos,
            'atencao': atencao,
            'normal': normal,
            'media_dias': media_dias
        },
        'mensagem': 'Dados carregados com sucesso' if tabela_json else 'Nenhum pedido BN em aberto encontrado'
    }
    
    return render_template('terceiros.html', user=user, dados=dados)


# ==================== SISTEMA DE ANOTAÇÕES UNIVERSAL ====================
# Arquivo para salvar anotações no servidor (compartilhado entre todos os usuários)
ANOTACOES_FILE = os.path.join(os.path.dirname(__file__), 'anotacoes_variacao.json')
ANOTACOES_OTD_FILE = os.path.join(os.path.dirname(__file__), 'anotacoes_otd.json')

def carregar_anotacoes():
    """Carrega anotações do arquivo JSON"""
    try:
        if os.path.exists(ANOTACOES_FILE):
            with open(ANOTACOES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Erro ao carregar anotações: {e}")
    return {}

def salvar_anotacoes(anotacoes):
    """Salva anotações no arquivo JSON"""
    try:
        with open(ANOTACOES_FILE, 'w', encoding='utf-8') as f:
            json.dump(anotacoes, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Erro ao salvar anotações: {e}")
        return False

def carregar_anotacoes_otd():
    """Carrega anotações OTD do arquivo JSON"""
    try:
        if os.path.exists(ANOTACOES_OTD_FILE):
            with open(ANOTACOES_OTD_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Erro ao carregar anotações OTD: {e}")
    return {}

def salvar_anotacoes_otd(anotacoes):
    """Salva anotações OTD no arquivo JSON"""
    try:
        with open(ANOTACOES_OTD_FILE, 'w', encoding='utf-8') as f:
            json.dump(anotacoes, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Erro ao salvar anotações OTD: {e}")
        return False

@app.route('/api/anotacoes', methods=['GET'])
def get_anotacoes():
    """Retorna todas as anotações salvas no servidor"""
    try:
        anotacoes = carregar_anotacoes()
        print(f"[ANOTAÇÕES] GET - Total: {len(anotacoes)} anotações carregadas")
        return jsonify({'success': True, 'anotacoes': anotacoes, 'total': len(anotacoes)})
    except Exception as e:
        print(f"[ANOTAÇÕES] ERRO GET: {e}")
        return jsonify({'success': False, 'anotacoes': {}, 'total': 0, 'error': str(e)})

@app.route('/api/anotacoes', methods=['POST'])
def salvar_anotacao():
    """Salva ou atualiza uma anotação"""
    try:
        data = request.get_json()
        chave = data.get('chave')
        texto = data.get('texto', '').strip()
        usuario = session.get('user', 'Anônimo')
        
        print(f"[ANOTAÇÕES] POST - Chave: {chave}, Usuário: {usuario}, Texto: {texto[:50] if texto else 'VAZIO'}...")
        
        anotacoes = carregar_anotacoes()
        
        if texto:
            anotacoes[chave] = {
                'texto': texto,
                'usuario': usuario,
                'data': datetime.now().strftime('%d/%m/%Y %H:%M')
            }
            print(f"[ANOTAÇÕES] Salvando anotação para chave: {chave}")
        else:
            # Remove anotação se texto vazio
            if chave in anotacoes:
                del anotacoes[chave]
                print(f"[ANOTAÇÕES] Removendo anotação: {chave}")
        
        if salvar_anotacoes(anotacoes):
            print(f"[ANOTAÇÕES] Sucesso! Total: {len(anotacoes)} anotações")
            return jsonify({'success': True, 'message': 'Anotação salva com sucesso!'})
        else:
            print(f"[ANOTAÇÕES] ERRO ao salvar arquivo")
            return jsonify({'success': False, 'message': 'Erro ao salvar anotação no arquivo'})
    except Exception as e:
        print(f"[ANOTAÇÕES] ERRO POST: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/anotacoes/<chave>', methods=['DELETE'])
def deletar_anotacao(chave):
    """Remove uma anotação específica"""
    try:
        anotacoes = carregar_anotacoes()
        
        if chave in anotacoes:
            del anotacoes[chave]
            if salvar_anotacoes(anotacoes):
                return jsonify({'success': True, 'message': 'Anotação removida!'})
        
        return jsonify({'success': False, 'message': 'Anotação não encontrada'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/anotacoes/exportar', methods=['GET'])
def exportar_anotacoes():
    """Exporta todas as anotações em JSON"""
    anotacoes = carregar_anotacoes()
    return jsonify({'success': True, 'anotacoes': anotacoes})

@app.route('/api/anotacoes/importar', methods=['POST'])
def importar_anotacoes():
    """Importa anotações de um JSON (mescla com existentes)"""
    try:
        data = request.get_json()
        anotacoes_importadas = data.get('anotacoes', {})
        
        anotacoes_atuais = carregar_anotacoes()
        
        # Mescla as anotações (importadas sobrescrevem existentes com mesma chave)
        for chave, valor in anotacoes_importadas.items():
            # Se for formato antigo (só texto), converte para novo formato
            if isinstance(valor, str):
                anotacoes_atuais[chave] = {
                    'texto': valor,
                    'usuario': 'Importado',
                    'data': datetime.now().strftime('%d/%m/%Y %H:%M')
                }
            else:
                anotacoes_atuais[chave] = valor
        
        if salvar_anotacoes(anotacoes_atuais):
            return jsonify({'success': True, 'message': f'{len(anotacoes_importadas)} anotações importadas!', 'total': len(anotacoes_atuais), 'importadas': len(anotacoes_importadas)})
        else:
            return jsonify({'success': False, 'message': 'Erro ao importar anotações'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# =============================================================================
# ANOTAÇÕES - OTD & PERFORMANCE
# =============================================================================
@app.route('/api/anotacoes_otd', methods=['GET'])
def get_anotacoes_otd():
    """Retorna todas as anotações OTD salvas no servidor"""
    try:
        anotacoes = carregar_anotacoes_otd()
        print(f"[ANOTAÇÕES OTD] GET - Total: {len(anotacoes)} anotações carregadas")
        return jsonify({'success': True, 'anotacoes': anotacoes, 'total': len(anotacoes)})
    except Exception as e:
        print(f"[ANOTAÇÕES OTD] ERRO GET: {e}")
        return jsonify({'success': False, 'anotacoes': {}, 'total': 0, 'error': str(e)})

@app.route('/api/anotacoes_otd', methods=['POST'])
def salvar_anotacao_otd():
    """Salva ou atualiza uma anotação OTD"""
    try:
        data = request.get_json()
        chave = data.get('chave')
        texto = data.get('texto', '').strip()
        usuario = session.get('user', 'Anônimo')
        
        print(f"[ANOTAÇÕES OTD] POST - Chave: {chave}, Usuário: {usuario}, Texto: {texto[:50] if texto else 'VAZIO'}...")
        
        anotacoes = carregar_anotacoes_otd()
        
        if texto:
            anotacoes[chave] = {
                'texto': texto,
                'usuario': usuario,
                'data': datetime.now().strftime('%d/%m/%Y %H:%M')
            }
            print(f"[ANOTAÇÕES OTD] Salvando anotação para chave: {chave}")
        else:
            # Remove anotação se texto vazio
            if chave in anotacoes:
                del anotacoes[chave]
                print(f"[ANOTAÇÕES OTD] Removendo anotação: {chave}")
        
        if salvar_anotacoes_otd(anotacoes):
            print(f"[ANOTAÇÕES OTD] Sucesso! Total: {len(anotacoes)} anotações")
            return jsonify({'success': True, 'message': 'Anotação salva com sucesso!'})
        else:
            print(f"[ANOTAÇÕES OTD] ERRO ao salvar arquivo")
            return jsonify({'success': False, 'message': 'Erro ao salvar anotação no arquivo'})
    except Exception as e:
        print(f"[ANOTAÇÕES OTD] ERRO POST: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/anotacoes_otd/<chave>', methods=['DELETE'])
def deletar_anotacao_otd(chave):
    """Remove uma anotação OTD específica"""
    try:
        anotacoes = carregar_anotacoes_otd()
        
        if chave in anotacoes:
            del anotacoes[chave]
            if salvar_anotacoes_otd(anotacoes):
                return jsonify({'success': True, 'message': 'Anotação removida!'})
        
        return jsonify({'success': False, 'message': 'Anotação não encontrada'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# =============================================================================
# SOLICITAÇÕES EM ABERTO (SC1010)
# =============================================================================
@cache.cached(timeout=600, key_prefix='solicitacoes_aberto_v3')  # 10 minutos de cache
def get_solicitacoes_aberto_data():
    """
    Busca solicitações de compra em aberto da tabela SC1010.
    Solicitações em aberto são aquelas que ainda não foram totalmente atendidas.
    """
    try:
        server = r'172.16.45.117\TOTVS' 
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str)
        
        query = """
        SELECT 
            SC.C1_NUM       AS NumeroSC,
            SC.C1_ITEM      AS ItemSC,
            SC.C1_PRODUTO   AS CodProduto,
            SC.C1_QUANT     AS Quantidade,
            SC.C1_DATPRF    AS DataNecessidade,
            SC.C1_EMISSAO   AS DataEmissao,
            SC.C1_DESCRI    AS Descricao,
            SC.C1_FORNECE   AS CodFornecedorSC,
            SC.C1_SOLICIT   AS Solicitante,
            SC.C1_TPOP      AS TipoOperacao,
            SC.C1_POLPROG   AS PolProg,
            SC.C1_ESTOQUE   AS Estoque,
            SC.C1_X_DTF     AS DataFechamento,
            SC.C1_X_USRF    AS UsuarioFechamento,
            SC.C1_X_URGEN   AS Urgencia,
            ISNULL(SC.C1_QUJE, 0) AS QtdEntregue,
            SC.C1_UM        AS UnidadeMedida,
            LTRIM(RTRIM(ISNULL(SC.C1_OBS, ''))) AS Observacao,
            LTRIM(RTRIM(ISNULL(SC.C1_POLPROG, ''))) AS Programa,
            ISNULL(PROD.B1_DESC, SC.C1_DESCRI) AS DescricaoProduto,
            ISNULL(PROD.B1_GRUPO, '') AS GrupoProduto,
            ISNULL(PROD.B1_PROC, '') AS FornecedorPadraoProduto,
            ISNULL(PROD.B1_PE, 0) AS LeadTimeProduto,
            
            -- Fornecedor Final: Prioridade 1 = SC, Prioridade 2 = Produto
            CASE 
                WHEN LTRIM(RTRIM(ISNULL(SC.C1_FORNECE, ''))) <> '' THEN LTRIM(RTRIM(SC.C1_FORNECE))
                WHEN LTRIM(RTRIM(ISNULL(PROD.B1_PROC, ''))) <> '' THEN LTRIM(RTRIM(PROD.B1_PROC))
                ELSE ''
            END AS CodFornecedorFinal,
            
            -- Código A2_COD do Fornecedor (prioridade: SC > Produto)
            CASE 
                WHEN LTRIM(RTRIM(ISNULL(SC.C1_FORNECE, ''))) <> '' THEN LTRIM(RTRIM(ISNULL(FORN_SC.A2_COD, '')))
                WHEN LTRIM(RTRIM(ISNULL(PROD.B1_PROC, ''))) <> '' THEN LTRIM(RTRIM(ISNULL(FORN_PROD.A2_COD, '')))
                ELSE ''
            END AS A2CodeFornecedor,
            
            -- Nome do Fornecedor (prioridade: SC > Produto)
            CASE 
                WHEN LTRIM(RTRIM(ISNULL(SC.C1_FORNECE, ''))) <> '' THEN ISNULL(FORN_SC.A2_NOME, '')
                WHEN LTRIM(RTRIM(ISNULL(PROD.B1_PROC, ''))) <> '' THEN ISNULL(FORN_PROD.A2_NOME, '')
                ELSE ''
            END AS NomeFornecedor,
            
            -- Código do Comprador (prioridade: fornecedor SC > fornecedor Produto)
            CASE 
                WHEN LTRIM(RTRIM(ISNULL(SC.C1_FORNECE, ''))) <> '' THEN LTRIM(RTRIM(ISNULL(FORN_SC.A2_X_COMPR, '')))
                WHEN LTRIM(RTRIM(ISNULL(PROD.B1_PROC, ''))) <> '' THEN LTRIM(RTRIM(ISNULL(FORN_PROD.A2_X_COMPR, '')))
                ELSE ''
            END AS CodComprador,
            
            -- Nome do Comprador (prioridade: fornecedor SC > fornecedor Produto)
            CASE 
                WHEN LTRIM(RTRIM(ISNULL(SC.C1_FORNECE, ''))) <> '' THEN
                    CASE LTRIM(RTRIM(ISNULL(FORN_SC.A2_X_COMPR, '')))
                        WHEN '016' THEN 'Aline Chen'
                        WHEN '007' THEN 'Hélio Doce'
                        WHEN '008' THEN 'Diego Moya'
                        WHEN '018' THEN 'Daniel Amaral'
                        ELSE 'Outros'
                    END
                WHEN LTRIM(RTRIM(ISNULL(PROD.B1_PROC, ''))) <> '' THEN
                    CASE LTRIM(RTRIM(ISNULL(FORN_PROD.A2_X_COMPR, '')))
                        WHEN '016' THEN 'Aline Chen'
                        WHEN '007' THEN 'Hélio Doce'
                        WHEN '008' THEN 'Diego Moya'
                        WHEN '018' THEN 'Daniel Amaral'
                        ELSE 'Outros'
                    END
                ELSE 'Outros'
            END AS NomeComprador

        FROM SC1010 AS SC
        LEFT JOIN SB1010 AS PROD ON SC.C1_PRODUTO = PROD.B1_COD AND PROD.D_E_L_E_T_ = ''
        -- JOIN com fornecedor da SOLICITAÇÃO
        LEFT JOIN SA2010 AS FORN_SC ON LTRIM(RTRIM(SC.C1_FORNECE)) = FORN_SC.A2_COD AND FORN_SC.D_E_L_E_T_ = ''
        -- JOIN com fornecedor padrão do PRODUTO
        LEFT JOIN SA2010 AS FORN_PROD ON LTRIM(RTRIM(PROD.B1_PROC)) = FORN_PROD.A2_COD AND FORN_PROD.D_E_L_E_T_ = ''
        
        WHERE SC.D_E_L_E_T_ <> '*' 
          AND SC.C1_QUANT > ISNULL(SC.C1_QUJE, 0)
          AND SC.C1_EMISSAO >= '20240101'
          -- Apenas SCs que NÃO viraram pedido de compra ainda
          AND NOT EXISTS (
              SELECT 1 
              FROM SC7010 AS PC 
              WHERE PC.C7_NUMSC = SC.C1_NUM 
                AND PC.C7_ITEMSC = SC.C1_ITEM 
                AND PC.D_E_L_E_T_ = ''
                AND (PC.C7_RESIDUO = '' OR PC.C7_RESIDUO IS NULL)
          )
        
        ORDER BY SC.C1_DATPRF ASC, SC.C1_EMISSAO DESC
        """
        
        df = pd.read_sql(query, conn)
        conn.close()

        # Converte colunas de data
        colunas_data = ['DataNecessidade', 'DataEmissao', 'DataFechamento']
        for col in colunas_data:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], format='%Y%m%d', errors='coerce')
        
        # Limpeza de strings
        if not df.empty:
            for col in ['Descricao', 'DescricaoProduto', 'Solicitante', 'NomeFornecedor', 'NomeComprador', 'Observacao', 'Programa']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.strip()

        return df
    except Exception as e:
        print(f"Erro SQL Solicitações: {e}")
        return None


@app.route('/solicitacoes', methods=['GET', 'POST'])
def solicitacoes():
    """Página de Solicitações em Aberto"""
    
    # METADADOS SQL - Origem dos dados para tooltips (Raio-X)
    sql_meta = {
        'numero_sc': 'SC1010.C1_NUM',
        'item_sc': 'SC1010.C1_ITEM',
        'cod_produto': 'SC1010.C1_PRODUTO',
        'quantidade': 'SC1010.C1_QUANT',
        'data_necessidade': 'SC1010.C1_DATPRF',
        'data_emissao': 'SC1010.C1_EMISSAO',
        'descricao': 'SC1010.C1_DESCRI',
        'cod_fornecedor': 'SC1010.C1_FORNECE',
        'solicitante': 'SC1010.C1_SOLICIT',
        'tipo_operacao': 'SC1010.C1_TPOP',
        'pol_prog': 'SC1010.C1_POLPROG',
        'estoque': 'SC1010.C1_ESTOQUE',
        'data_fechamento': 'SC1010.C1_X_DTF',
        'usuario_fechamento': 'SC1010.C1_X_USRF',
        'urgencia': 'SC1010.C1_X_URGEN',
        'cod_comprador': 'SC1010.C1_X_COMPR',
        'a2_code': 'SA2010.A2_CODE',
        'observacao': 'SC1010.C1_OBS',
        'programa': 'SC1010.C1_POLPROG'
    }
    
    if request.method == 'POST':
        filtros_novos = request.form.to_dict()
        if 'solicitantes' in request.form:
            filtros_novos['solicitantes'] = request.form.getlist('solicitantes')
        if 'compradores' in request.form:
            filtros_novos['compradores'] = request.form.getlist('compradores')
        
        session['filtros_solicitacoes'] = filtros_novos
        session.modified = True
        return redirect(url_for('solicitacoes'))

    if request.args:
        filtros_novos = request.args.to_dict()
        if 'solicitantes' in request.args:
            filtros_novos['solicitantes'] = request.args.getlist('solicitantes')
        if 'compradores' in request.args:
            filtros_novos['compradores'] = request.args.getlist('compradores')
        session['filtros_solicitacoes'] = filtros_novos
        session.modified = True
    
    filtros = session.get('filtros_solicitacoes', {})
    
    # Para solicitações, usamos período amplo desde 2024 para pegar todas as SCs em aberto
    filtros_template = {
        'data_inicio': filtros.get('data_inicio') or '2024-01-01',
        'data_fim': filtros.get('data_fim') or '2026-12-31',
        'busca_geral': filtros.get('busca_geral') or '',
        'compradores': filtros.get('compradores') or [],
        'status': filtros.get('status') or 'Todos',
        'tipo_operacao': filtros.get('tipo_operacao') or 'F',  # Padrão: Firmado
        'solicitantes': filtros.get('solicitantes') or []
    }

    df_raw = get_solicitacoes_aberto_data()
    
    if df_raw is None or df_raw.empty:
        dados = {
            'kpi_total': 0,
            'kpi_urgentes': 0,
            'kpi_atrasadas': 0,
            'tabela': [],
            'opcoes_compradores': [],
            'opcoes_solicitantes': [],
            'graf_comp_labels': [],
            'graf_comp_values': [],
            'graf_status_labels': [],
            'graf_status_values': []
        }
        return render_template('solicitacoes.html', user="Admin", dados=dados, filtros=filtros_template, sql_meta=sql_meta)
    
    df = df_raw.copy()
    
    # ========================================
    # BUSCAR ATRIBUIÇÕES MANUAIS
    # ========================================
    # Busca atribuições manuais do banco local
    atribuicoes_manuais = db.obter_atribuicoes_compradores()
    
    # Cria coluna para indicar atribuição manual
    df['AtribuicaoManual'] = False
    df['CompradorManual'] = None
    df['CodCompradorManual'] = None
    
    # Aplica atribuições manuais ao DataFrame
    if not df.empty:
        for idx, row in df.iterrows():
            chave = f"{row['NumeroSC']}-{row['ItemSC']}"
            if chave in atribuicoes_manuais:
                atrib = atribuicoes_manuais[chave]
                df.at[idx, 'AtribuicaoManual'] = True
                df.at[idx, 'CompradorManual'] = atrib['nome_comprador']
                df.at[idx, 'CodCompradorManual'] = atrib['cod_comprador']
                # PRIORIZA atribuição manual sobre o comprador do Totvs
                df.at[idx, 'NomeComprador'] = atrib['nome_comprador']
                df.at[idx, 'CodComprador'] = atrib['cod_comprador']
    
    # Garantir que NomeComprador está limpo (igual ao Dashboard)
    if not df.empty and 'NomeComprador' in df.columns:
        df['NomeComprador'] = df['NomeComprador'].astype(str).str.strip()
    
    # Garantir que TipoOperacao está limpo
    if not df.empty and 'TipoOperacao' in df.columns:
        df['TipoOperacao'] = df['TipoOperacao'].astype(str).str.strip().str.upper()
    
    # DEBUG: Imprimir compradores únicos para diagnóstico
    if not df.empty:
        compradores_unicos = df['NomeComprador'].unique()
        print(f"[SOLICITAÇÕES] Compradores (via Fornecedor) encontrados: {compradores_unicos}")
        print(f"[SOLICITAÇÕES] Total de SCs: {len(df)}")
    
    # Aplicar filtros (SEM FILTRO DE DATA - mostra todas as SCs em aberto)
    if filtros_template['busca_geral']:
        busca = filtros_template['busca_geral'].lower()
        df = df[
            df['NumeroSC'].astype(str).str.lower().str.contains(busca, na=False) |
            df['CodProduto'].astype(str).str.lower().str.contains(busca, na=False) |
            df['Descricao'].astype(str).str.lower().str.contains(busca, na=False) |
            df['DescricaoProduto'].astype(str).str.lower().str.contains(busca, na=False) |
            df['Solicitante'].astype(str).str.lower().str.contains(busca, na=False)
        ]
    
    # FILTRO DE COMPRADOR - Multi-seleção via Fornecedor (SA2010.A2_X_COMPR)
    if filtros_template['compradores']:
        compradores_selecionados = [c.strip().lower() for c in filtros_template['compradores']]
        print(f"[SOLICITAÇÕES] Filtrando por compradores: {compradores_selecionados}")
        df_antes = len(df)
        df = df[df['NomeComprador'].str.lower().isin(compradores_selecionados)]
        print(f"[SOLICITAÇÕES] Registros antes: {df_antes}, depois: {len(df)}")
    
    # FILTRO DE TIPO DE OPERAÇÃO (Firmado / Previsto)
    if filtros_template['tipo_operacao'] != 'Todos':
        tipo_filtro = filtros_template['tipo_operacao'].strip().upper()
        print(f"[SOLICITAÇÕES] Filtrando por tipo operação: '{tipo_filtro}'")
        df = df[df['TipoOperacao'] == tipo_filtro]
    
    if filtros_template['solicitantes']:
        df = df[df['Solicitante'].isin(filtros_template['solicitantes'])]
    
    hoje = datetime.now()
    
    # ========================================
    # CÁLCULO DE STATUS AUTOMÁTICO
    # ========================================
    def calcular_status(row):
        """
        Calcula o status da solicitação baseado em regras de negócio:
        1. CRÍTICO: Data atual > Data necessidade (prioridade máxima)
        2. PRAZO INCOMPATÍVEL: (Data necessidade - Data emissão) < Lead Time
        3. NORMAL: demais casos
        """
        data_nec = row['DataNecessidade']
        data_emis = row['DataEmissao']
        lead_time = row.get('LeadTimeProduto', 0)
        
        # Se não tem data de necessidade, considera NORMAL
        if pd.isnull(data_nec):
            return 'NORMAL'
        
        # REGRA 1: CRÍTICO (prioridade máxima)
        if hoje > data_nec:
            return 'CRÍTICO'
        
        # REGRA 2: PRAZO INCOMPATÍVEL
        if not pd.isnull(data_emis) and lead_time > 0:
            prazo_solicitado = (data_nec - data_emis).days
            if prazo_solicitado < lead_time:
                return 'PRAZO INCOMPATÍVEL'
        
        # REGRA 3: NORMAL (padrão)
        return 'NORMAL'
    
    # Aplica cálculo de status para todas as linhas
    df['Status'] = df.apply(calcular_status, axis=1)
    
    # FILTRO DE STATUS
    if filtros_template['status'] != 'Todos':
        df = df[df['Status'] == filtros_template['status']]
    
    # Calcular dias de atraso (criando lista de valores inteiros)
    dias_atraso_lista = []
    for _, row in df.iterrows():
        data_nec = row['DataNecessidade']
        if pd.isnull(data_nec):
            dias_atraso_lista.append(0)
        else:
            try:
                diff = (hoje - data_nec).days
                dias_atraso_lista.append(int(diff) if diff > 0 else 0)
            except:
                dias_atraso_lista.append(0)
    
    df['DiasAtraso'] = dias_atraso_lista
    
    # KPIs
    total_solicitacoes = len(df)
    total_atrasadas = len(df[df['Status'] == 'CRÍTICO'])
    total_prazo_incompativel = len(df[df['Status'] == 'PRAZO INCOMPATÍVEL'])
    
    # ========================================
    # GRÁFICO POR COMPRADOR - SEGMENTADO POR STATUS
    # ========================================
    # Agrupa por Comprador e Status, conta as solicitações
    comp_status_df = df.groupby(['NomeComprador', 'Status']).size().unstack(fill_value=0)
    
    # Garante que todas as colunas de status existem (mesmo com valor 0)
    for status in ['NORMAL', 'CRÍTICO', 'PRAZO INCOMPATÍVEL']:
        if status not in comp_status_df.columns:
            comp_status_df[status] = 0
    
    # Calcula total por comprador para ordenação
    comp_status_df['Total'] = comp_status_df.sum(axis=1)
    comp_status_df = comp_status_df.sort_values('Total', ascending=False)
    
    # Extrai arrays para o gráfico (mantém ordem)
    graf_comp_labels = comp_status_df.index.tolist()
    graf_comp_normal = comp_status_df['NORMAL'].tolist()
    graf_comp_atrasado = comp_status_df['CRÍTICO'].tolist()
    graf_comp_incompativel = comp_status_df['PRAZO INCOMPATÍVEL'].tolist()
    
    # Gráfico por Status
    status_counts = df['Status'].value_counts()
    
    # Listas de opções para filtros - usar NomeComprador (via SA2010.A2_X_COMPR)
    lista_compradores = sorted(df_raw['NomeComprador'].dropna().astype(str).str.strip().unique().tolist())
    print(f"[SOLICITAÇÕES] Lista de compradores para filtro: {lista_compradores}")
    
    lista_solicitantes = sorted(df_raw['Solicitante'].dropna().astype(str).str.strip().unique().tolist())
    
    dados = {
        'kpi_total': total_solicitacoes,
        'kpi_atrasadas': total_atrasadas,
        'kpi_prazo_incompativel': total_prazo_incompativel,
        'tabela': df.sort_values(by=['DiasAtraso', 'DataNecessidade'], ascending=[False, True]).head(500).to_dict(orient='records'),
        'opcoes_compradores': lista_compradores,
        'opcoes_solicitantes': lista_solicitantes,
        'graf_comp_labels': graf_comp_labels,
        'graf_comp_normal': graf_comp_normal,
        'graf_comp_atrasado': graf_comp_atrasado,
        'graf_comp_incompativel': graf_comp_incompativel,
        'graf_status_labels': status_counts.index.tolist(),
        'graf_status_values': status_counts.values.tolist(),
        'atribuicoes': atribuicoes_manuais  # Passa atribuições para o template
    }

    return render_template('solicitacoes.html', user="Admin", dados=dados, filtros=filtros_template, sql_meta=sql_meta)


@app.route('/limpar_filtros_solicitacoes')
def limpar_filtros_solicitacoes():
    session.pop('filtros_solicitacoes', None)
    return redirect(url_for('solicitacoes'))


@app.route('/limpar_cache_solicitacoes')
def limpar_cache_solicitacoes():
    """Limpa o cache das solicitações para forçar atualização dos dados"""
    cache.delete('solicitacoes_aberto_v3')
    session.pop('filtros_solicitacoes', None)
    flash('Cache de solicitações atualizado!', 'success')
    return redirect(url_for('solicitacoes'))


# =============================================================================
# COTAÇÕES E ORÇAMENTOS
# =============================================================================

@app.route('/cotacoes')
def cotacoes():
    """Página principal de Cotações e Orçamentos"""
    
    # Filtros
    status_filtro = request.args.get('status', '')
    comprador_filtro = request.args.get('comprador', '')
    busca_filtro = request.args.get('busca', '')
    tipo_filtro = request.args.get('tipo', '')  # 'Solicitacao', 'Manual' ou '' (todos)
    
    # Buscar cotações
    cotacoes_lista = db.listar_cotacoes(
        status=status_filtro if status_filtro else None,
        comprador=comprador_filtro if comprador_filtro else None,
        busca=busca_filtro if busca_filtro else None,
        tipo_origem=tipo_filtro if tipo_filtro else None
    )
    
    # Estatísticas (inclui contagem por tipo)
    todas = db.listar_cotacoes(limit=1000)
    stats = {
        'total': len(todas),
        'abertas': len([c for c in todas if c['status'] == 'Aberta']),
        'respondidas': len([c for c in todas if c['status'] == 'Respondida']),
        'encerradas': len([c for c in todas if c['status'] == 'Encerrada']),
        'manuais': len([c for c in todas if c.get('tipo_origem') == 'Manual']),
        'solicitacoes': len([c for c in todas if c.get('tipo_origem', 'Solicitacao') == 'Solicitacao'])
    }
    
    return render_template('cotacoes.html', 
                          user="Admin", 
                          cotacoes=cotacoes_lista,
                          stats=stats,
                          filtros={'status': status_filtro, 'comprador': comprador_filtro, 'busca': busca_filtro, 'tipo': tipo_filtro})


def buscar_ultimo_preco_pago(codigos_produtos):
    """
    Busca o último preço pago para uma lista de códigos de produtos.
    Retorna um dicionário {codigo_produto: {'preco': valor, 'data': data, 'fornecedor': nome}}
    
    Fonte: SD1010 (Notas Fiscais de Entrada) - considera apenas NFs válidas com pedido associado
    """
    if not codigos_produtos:
        return {}
    
    resultado = {}
    
    try:
        server = '172.16.45.117\\TOTVS'
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str, timeout=30)
        
        # Limpa e formata os códigos para a query
        codigos_limpos = []
        for cod in codigos_produtos:
            if cod:
                cod_limpo = str(cod).strip()
                if cod_limpo:
                    codigos_limpos.append(cod_limpo.replace("'", "''"))
        
        if not codigos_limpos:
            conn.close()
            return {}
        
        # Cria lista para IN clause
        codigos_str = "'" + "','".join(codigos_limpos) + "'"
        
        # Query para buscar o último preço pago por produto
        # Usa ROW_NUMBER para pegar apenas o registro mais recente por produto
        query = f"""
        WITH UltimoPreco AS (
            SELECT 
                LTRIM(RTRIM(NF.D1_COD)) AS CodProduto,
                NF.D1_VUNIT AS PrecoUnitario,
                NF.D1_DTDIGIT AS DataNota,
                NF.D1_DOC AS NumeroNota,
                LTRIM(RTRIM(A2.A2_NOME)) AS NomeFornecedor,
                ROW_NUMBER() OVER (
                    PARTITION BY LTRIM(RTRIM(NF.D1_COD)) 
                    ORDER BY NF.D1_DTDIGIT DESC, NF.D1_DOC DESC
                ) AS rn
            FROM SD1010 NF WITH (NOLOCK)
            LEFT JOIN SA2010 A2 WITH (NOLOCK) 
                ON NF.D1_FORNECE = A2.A2_COD AND A2.D_E_L_E_T_ = ''
            WHERE NF.D_E_L_E_T_ <> '*'
              AND NF.D1_VUNIT > 0
              AND NF.D1_QUANT > 0
              AND NF.D1_TIPO = 'N'
              AND LTRIM(RTRIM(ISNULL(NF.D1_PEDIDO, ''))) <> ''
              AND LTRIM(RTRIM(NF.D1_COD)) IN ({codigos_str})
        )
        SELECT CodProduto, PrecoUnitario, DataNota, NumeroNota, NomeFornecedor
        FROM UltimoPreco
        WHERE rn = 1
        """
        
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        
        for row in rows:
            cod_produto = row.CodProduto.strip() if row.CodProduto else ''
            if cod_produto:
                resultado[cod_produto] = {
                    'preco': float(row.PrecoUnitario) if row.PrecoUnitario else 0,
                    'data': row.DataNota if row.DataNota else '',
                    'nota': row.NumeroNota.strip() if row.NumeroNota else '',
                    'fornecedor': row.NomeFornecedor.strip() if row.NomeFornecedor else ''
                }
        
        conn.close()
        print(f"[ULTIMO_PRECO] Encontrado histórico para {len(resultado)} de {len(codigos_limpos)} produtos")
        
    except Exception as e:
        print(f"[ULTIMO_PRECO] Erro ao buscar último preço: {e}")
    
    return resultado


@app.route('/cotacao/<int:cotacao_id>')
def cotacao_detalhe(cotacao_id):
    """Página de detalhes de uma cotação"""
    cotacao = db.obter_cotacao(cotacao_id)
    
    if not cotacao:
        flash('Cotação não encontrada!', 'danger')
        return redirect(url_for('cotacoes'))
    
    # Busca último preço pago para os itens da cotação
    codigos_produtos = [item.get('cod_produto', '').strip() for item in cotacao.get('itens', []) if item.get('cod_produto')]
    ultimos_precos = buscar_ultimo_preco_pago(codigos_produtos)
    
    # Adiciona o último preço a cada item
    for item in cotacao.get('itens', []):
        cod_produto = item.get('cod_produto', '').strip()
        if cod_produto and cod_produto in ultimos_precos:
            item['ultimo_preco_pago'] = ultimos_precos[cod_produto]['preco']
            item['ultimo_preco_data'] = ultimos_precos[cod_produto]['data']
            item['ultimo_preco_fornecedor'] = ultimos_precos[cod_produto]['fornecedor']
        else:
            item['ultimo_preco_pago'] = None
            item['ultimo_preco_data'] = None
            item['ultimo_preco_fornecedor'] = None
    
    # Obter IP do servidor para gerar links funcionais em qualquer dispositivo
    server_ip = obter_ip_servidor()
    server_port = 5001
    base_url = f"http://{server_ip}:{server_port}"
    
    # Usa o novo template otimizado
    return render_template('cotacao_detalhe_new.html', 
                          user="Admin", 
                          cotacao=cotacao,
                          base_url=base_url)


@app.route('/api/fornecedores/buscar')
def api_buscar_fornecedores():
    """
    Busca fornecedores cadastrados no SA2010 (TOTVS).
    Permite filtrar por termo de busca (nome ou código).
    
    CORREÇÃO: SQL parametrizado para evitar SQL Injection
    """
    try:
        termo = request.args.get('termo', '').strip()
        
        # Log para debug
        print(f"[BUSCA FORNECEDOR] Termo recebido: '{termo}'")
        
        server = '172.16.45.117\\TOTVS'
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str, timeout=30)
        cursor = conn.cursor()
        
        # Query parametrizada para evitar SQL Injection
        if termo:
            query = """
            SELECT TOP 100
                RTRIM(A2_COD) AS codigo,
                RTRIM(A2_NOME) AS nome,
                RTRIM(ISNULL(A2_EMAIL, '')) AS email,
                RTRIM(ISNULL(A2_TEL, '')) AS telefone
            FROM SA2010
            WHERE D_E_L_E_T_ = ''
              AND A2_MSBLQL <> '1'
              AND (
                  UPPER(A2_COD) LIKE UPPER(?) 
                  OR UPPER(A2_NOME) LIKE UPPER(?)
              )
            ORDER BY A2_NOME
            """
            # Adiciona wildcards para busca parcial
            termo_busca = f'%{termo}%'
            cursor.execute(query, (termo_busca, termo_busca))
        else:
            query = """
            SELECT TOP 100
                RTRIM(A2_COD) AS codigo,
                RTRIM(A2_NOME) AS nome,
                RTRIM(ISNULL(A2_EMAIL, '')) AS email,
                RTRIM(ISNULL(A2_TEL, '')) AS telefone
            FROM SA2010
            WHERE D_E_L_E_T_ = ''
              AND A2_MSBLQL <> '1'
            ORDER BY A2_NOME
            """
            cursor.execute(query)
        
        # Converte resultado para lista de dicionários
        columns = [column[0] for column in cursor.description]
        fornecedores = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        
        print(f"[BUSCA FORNECEDOR] Encontrados: {len(fornecedores)} fornecedores")
        
        return jsonify({
            'success': True,
            'fornecedores': fornecedores,
            'total': len(fornecedores)
        })
        
    except Exception as e:
        print(f"[ERRO] api_buscar_fornecedores: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 'message': 'Erro ao buscar fornecedores no banco de dados'})


@app.route('/api/fornecedores/testar-conexao')
def api_testar_conexao_fornecedores():
    """
    Endpoint de diagnóstico para testar conexão com banco TOTVS (SA2010).
    Útil para identificar problemas de rede ou credenciais.
    """
    try:
        import time
        inicio = time.time()
        
        server = '172.16.45.117\\TOTVS'
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        print(f"[TESTE CONEXÃO] Tentando conectar a {server}...")
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str, timeout=15)
        cursor = conn.cursor()
        
        # Teste simples: contar fornecedores ativos
        cursor.execute("""
            SELECT COUNT(*) as total 
            FROM SA2010 
            WHERE D_E_L_E_T_ = '' AND A2_MSBLQL <> '1'
        """)
        total = cursor.fetchone()[0]
        
        conn.close()
        
        tempo_conexao = round(time.time() - inicio, 2)
        print(f"[TESTE CONEXÃO] Sucesso! {total} fornecedores ativos. Tempo: {tempo_conexao}s")
        
        return jsonify({
            'success': True,
            'message': 'Conexão com banco TOTVS estabelecida com sucesso',
            'total_fornecedores_ativos': total,
            'tempo_conexao_segundos': tempo_conexao,
            'servidor': server,
            'banco': database
        })
        
    except pyodbc.Error as e:
        print(f"[ERRO] Conexão TOTVS falhou: {e}")
        erro_msg = str(e)
        if 'Login failed' in erro_msg:
            detalhe = 'Credenciais inválidas (usuário/senha)'
        elif 'network' in erro_msg.lower() or 'timeout' in erro_msg.lower():
            detalhe = 'Servidor inacessível ou timeout de rede'
        elif 'driver' in erro_msg.lower():
            detalhe = 'Driver ODBC não instalado'
        else:
            detalhe = erro_msg
        
        return jsonify({
            'success': False,
            'error': 'Falha na conexão com banco TOTVS',
            'detalhe': detalhe
        }), 500
        
    except Exception as e:
        print(f"[ERRO] api_testar_conexao_fornecedores: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# =============================================================================
# API: BUSCAR PRODUTOS PARA ORÇAMENTO MANUAL
# =============================================================================
@app.route('/api/produtos/buscar')
def api_buscar_produtos():
    """
    Busca produtos cadastrados no SB1010 (TOTVS) com último preço de compra.
    Permite filtrar por código ou descrição.
    
    Retorna:
        - codigo: Código do produto
        - descricao: Descrição do produto
        - unidade: Unidade de medida
        - ultimo_preco: Último preço unitário pago (da última NF)
        - ultimo_fornecedor: Nome do último fornecedor
        - data_ultima_compra: Data da última compra
    """
    try:
        termo = request.args.get('termo', '').strip()
        
        print(f"[BUSCA PRODUTO] Termo recebido: '{termo}'")
        
        if not termo or len(termo) < 2:
            return jsonify({
                'success': False,
                'message': 'Digite pelo menos 2 caracteres para buscar'
            })
        
        server = '172.16.45.117\\TOTVS'
        database = 'TOTVSDB'
        username = 'excel'
        password = 'Db_Polimaquinas'
        
        conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        conn = pyodbc.connect(conn_str, timeout=30)
        cursor = conn.cursor()
        
        # Query para buscar produtos com último preço de compra
        # Usa subquery para pegar a NF mais recente de cada produto
        query = """
        SELECT TOP 50
            RTRIM(P.B1_COD) AS codigo,
            RTRIM(P.B1_DESC) AS descricao,
            RTRIM(ISNULL(P.B1_UM, 'UN')) AS unidade,
            RTRIM(ISNULL(P.B1_TIPO, '')) AS tipo,
            RTRIM(ISNULL(P.B1_GRUPO, '')) AS grupo,
            ISNULL(ULT.ultimo_preco, 0) AS ultimo_preco,
            ISNULL(ULT.ultimo_fornecedor, '') AS ultimo_fornecedor,
            ULT.data_ultima_compra
        FROM SB1010 P
        LEFT JOIN (
            SELECT 
                D1.D1_COD,
                D1.D1_VUNIT AS ultimo_preco,
                A2.A2_NOME AS ultimo_fornecedor,
                D1.D1_DTDIGIT AS data_ultima_compra,
                ROW_NUMBER() OVER (PARTITION BY D1.D1_COD ORDER BY D1.D1_DTDIGIT DESC, D1.D1_DOC DESC) AS rn
            FROM SD1010 D1
            INNER JOIN SA2010 A2 ON D1.D1_FORNECE = A2.A2_COD AND A2.D_E_L_E_T_ = ''
            WHERE D1.D_E_L_E_T_ <> '*'
              AND D1.D1_TIPO = 'N'
              AND D1.D1_VUNIT > 0
              AND D1.D1_QUANT > 0
        ) ULT ON P.B1_COD = ULT.D1_COD AND ULT.rn = 1
        WHERE P.D_E_L_E_T_ = ''
          AND P.B1_MSBLQL <> '1'
          AND (
              UPPER(P.B1_COD) LIKE UPPER(?)
              OR UPPER(P.B1_DESC) LIKE UPPER(?)
          )
        ORDER BY 
            CASE WHEN UPPER(P.B1_COD) = UPPER(?) THEN 0 ELSE 1 END,
            P.B1_DESC
        """
        
        termo_busca = f'%{termo}%'
        termo_exato = termo.strip()
        cursor.execute(query, (termo_busca, termo_busca, termo_exato))
        
        columns = [column[0] for column in cursor.description]
        produtos = []
        
        for row in cursor.fetchall():
            produto = dict(zip(columns, row))
            # Formata data se existir
            if produto.get('data_ultima_compra'):
                try:
                    data_str = str(produto['data_ultima_compra'])
                    if len(data_str) == 8:  # Formato YYYYMMDD
                        produto['data_ultima_compra'] = f"{data_str[6:8]}/{data_str[4:6]}/{data_str[0:4]}"
                except:
                    pass
            produtos.append(produto)
        
        conn.close()
        
        print(f"[BUSCA PRODUTO] Encontrados: {len(produtos)} produtos")
        
        return jsonify({
            'success': True,
            'produtos': produtos,
            'total': len(produtos)
        })
        
    except Exception as e:
        print(f"[ERRO] api_buscar_produtos: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Erro ao buscar produtos no banco de dados'
        })


# =============================================================================
# API: CRIAR ORÇAMENTO MANUAL (Sem Solicitação de Compra)
# =============================================================================
@app.route('/api/cotacao/criar-manual', methods=['POST'])
def api_criar_cotacao_manual():
    """
    API para criar orçamento manual (sem vinculação com Solicitação de Compra).
    
    Payload esperado:
    {
        "itens": [
            {
                "cod_produto": "001234",
                "descricao": "PRODUTO EXEMPLO",
                "quantidade": 10,
                "unidade": "UN",
                "preco_referencia": 150.00,
                "fornecedor_referencia": "FORNECEDOR XYZ"
            }
        ],
        "comprador": "Daniel Amaral",
        "nome_customizado": "Orçamento Exploratório",  // opcional
        "observacoes": "Cotação para benchmark de preços"  // opcional
    }
    """
    try:
        dados = request.get_json()
        
        itens = dados.get('itens', [])
        comprador = dados.get('comprador', 'Admin')
        observacoes = dados.get('observacoes', '')
        nome_customizado = dados.get('nome_customizado')
        
        if not itens:
            return jsonify({
                'success': False,
                'error': 'Nenhum item selecionado para o orçamento'
            }), 400
        
        # Valida itens
        for i, item in enumerate(itens):
            if not item.get('cod_produto'):
                return jsonify({
                    'success': False,
                    'error': f'Item {i+1} não possui código de produto'
                }), 400
            if not item.get('quantidade') or float(item.get('quantidade', 0)) <= 0:
                return jsonify({
                    'success': False,
                    'error': f'Item {i+1} ({item.get("cod_produto")}) deve ter quantidade maior que zero'
                }), 400
        
        # Cria a cotação com tipo_origem = 'Manual'
        cotacao_id, codigo = db.criar_cotacao(
            comprador=comprador,
            observacoes=observacoes,
            nome_customizado=nome_customizado,
            tipo_origem='Manual'
        )
        
        # Prepara itens para inserção
        itens_formatados = []
        for idx, item in enumerate(itens):
            itens_formatados.append({
                'numero_sc': 'MANUAL',
                'item_sc': f'{idx + 1:03d}',
                'cod_produto': item.get('cod_produto', ''),
                'descricao': item.get('descricao', ''),
                'quantidade': float(item.get('quantidade', 0)),
                'unidade': item.get('unidade', 'UN'),
                'data_necessidade': item.get('data_necessidade'),
                'preco_referencia': item.get('preco_referencia'),
                'fornecedor_referencia': item.get('fornecedor_referencia')
            })
        
        # Adiciona os itens
        db.adicionar_itens_cotacao(cotacao_id, itens_formatados)
        
        print(f"[ORÇAMENTO MANUAL] Criado: {codigo} com {len(itens)} item(ns)")
        
        return jsonify({
            'success': True,
            'cotacao_id': cotacao_id,
            'codigo': codigo,
            'message': f'Orçamento Manual {codigo} criado com {len(itens)} item(ns)',
            'tipo': 'Manual'
        })
        
    except Exception as e:
        print(f"[ERRO] api_criar_cotacao_manual: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/cotacao/criar', methods=['POST'])
def api_criar_cotacao():
    """API para criar nova cotação a partir de solicitações selecionadas"""
    try:
        dados = request.get_json()
        
        itens = dados.get('itens', [])
        comprador = dados.get('comprador', 'Admin')
        observacoes = dados.get('observacoes', '')
        nome_customizado = dados.get('nome_customizado')  # Nome opcional
        data_criacao = dados.get('data_criacao')  # Data opcional DD/MM/YYYY
        
        if not itens:
            return jsonify({'success': False, 'error': 'Nenhum item selecionado'}), 400
        
        # Criar cotação com nome customizado
        cotacao_id, codigo = db.criar_cotacao(
            comprador=comprador, 
            observacoes=observacoes,
            nome_customizado=nome_customizado,
            data_criacao=data_criacao
        )
        
        # Adicionar itens
        db.adicionar_itens_cotacao(cotacao_id, itens)
        
        return jsonify({
            'success': True, 
            'cotacao_id': cotacao_id, 
            'codigo': codigo,
            'message': f'Cotação {codigo} criada com {len(itens)} item(ns)'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/<int:cotacao_id>/fornecedor', methods=['POST'])
def api_adicionar_fornecedor(cotacao_id):
    """API para adicionar fornecedor à cotação"""
    try:
        dados = request.get_json()
        
        nome = dados.get('nome', '')
        email = dados.get('email', '')
        telefone = dados.get('telefone', '')
        cod_fornecedor = dados.get('cod_fornecedor', '')
        
        if not nome:
            return jsonify({'success': False, 'error': 'Nome do fornecedor é obrigatório'}), 400
        
        fornecedor_id, token = db.adicionar_fornecedor_cotacao(
            cotacao_id, nome, email, telefone, cod_fornecedor
        )
        
        # Gera link de acesso
        link = url_for('cotacao_fornecedor', token=token, _external=True)
        
        return jsonify({
            'success': True,
            'fornecedor_id': fornecedor_id,
            'token': token,
            'link': link
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/<int:cotacao_id>/status', methods=['POST'])
def api_atualizar_status_cotacao(cotacao_id):
    """API para atualizar status da cotação"""
    try:
        dados = request.get_json()
        novo_status = dados.get('status', '')
        
        if novo_status not in ['Aberta', 'Respondida', 'Encerrada', 'Cancelada']:
            return jsonify({'success': False, 'error': 'Status inválido'}), 400
        
        db.atualizar_status_cotacao(cotacao_id, novo_status)
        
        return jsonify({'success': True, 'message': f'Status atualizado para {novo_status}'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/<int:cotacao_id>/editar-nome', methods=['POST'])
def api_editar_nome_cotacao(cotacao_id):
    """API para editar apenas o nome/código da cotação"""
    try:
        dados = request.get_json()
        novo_codigo = dados.get('codigo', '').strip()
        
        if not novo_codigo:
            return jsonify({'success': False, 'error': 'Nome da cotação não pode estar vazio'}), 400
        
        db.atualizar_cotacao(cotacao_id, codigo=novo_codigo)
        
        return jsonify({'success': True, 'message': 'Nome da cotação atualizado'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/<int:cotacao_id>/informacao-fornecedor', methods=['POST'])
def api_informacao_fornecedor(cotacao_id):
    """API para salvar informação importante ao fornecedor"""
    try:
        dados = request.get_json()
        informacao = dados.get('informacao', '').strip()
        
        # Permite salvar vazio (para remover a mensagem)
        db.atualizar_cotacao(cotacao_id, informacao_fornecedor=informacao)
        
        return jsonify({'success': True, 'message': 'Informação salva com sucesso'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/excluir', methods=['POST'])
def api_excluir_fornecedor(fornecedor_id):
    """
    API para excluir fornecedor da cotação.
    
    REGRAS IMPLEMENTADAS:
    - Permite excluir mesmo se status = respondido
    - Permite excluir mesmo se houver anexo
    - Remove vínculo da cotação
    - Invalida link individual (token é excluído)
    - Remove todas as respostas daquele fornecedor
    """
    try:
        print(f"[EXCLUIR FORNECEDOR] Solicitação para excluir fornecedor ID: {fornecedor_id}")
        
        # Executa exclusão (a função já remove respostas associadas)
        resultado = db.excluir_fornecedor_cotacao(fornecedor_id)
        
        if resultado:
            print(f"[EXCLUIR FORNECEDOR] Fornecedor {fornecedor_id} excluído com sucesso")
            return jsonify({'success': True, 'message': 'Fornecedor excluído com sucesso'})
        else:
            return jsonify({'success': False, 'error': 'Erro ao excluir fornecedor'}), 500
        
    except Exception as e:
        print(f"[ERRO] api_excluir_fornecedor: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/editar', methods=['POST'])
def api_editar_fornecedor(fornecedor_id):
    """
    API para editar informações do fornecedor (nome, email, telefone).
    """
    try:
        dados = request.get_json()
        
        nome = dados.get('nome', '').strip()
        email = dados.get('email', '').strip()
        telefone = dados.get('telefone', '').strip()
        
        if not nome:
            return jsonify({'success': False, 'error': 'Nome do fornecedor é obrigatório'}), 400
        
        print(f"[EDITAR FORNECEDOR] Atualizando fornecedor ID: {fornecedor_id}")
        print(f"[EDITAR FORNECEDOR] Novo nome: {nome}")
        print(f"[EDITAR FORNECEDOR] Novo email: {email}")
        print(f"[EDITAR FORNECEDOR] Novo telefone: {telefone}")
        
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        # Primeiro, verifica se o fornecedor existe
        cursor.execute('SELECT nome_fornecedor FROM cotacao_fornecedores WHERE id = ?', (fornecedor_id,))
        fornecedor_atual = cursor.fetchone()
        
        if not fornecedor_atual:
            conn.close()
            return jsonify({'success': False, 'error': 'Fornecedor não encontrado'}), 404
        
        print(f"[EDITAR FORNECEDOR] Nome atual: {fornecedor_atual['nome_fornecedor']}")
        
        # Atualiza os dados
        cursor.execute('''
            UPDATE cotacao_fornecedores 
            SET nome_fornecedor = ?, email_fornecedor = ?, telefone_fornecedor = ?
            WHERE id = ?
        ''', (nome, email, telefone, fornecedor_id))
        
        linhas_afetadas = cursor.rowcount
        conn.commit()
        
        # Verifica se a atualização foi bem-sucedida
        cursor.execute('SELECT nome_fornecedor, email_fornecedor, telefone_fornecedor FROM cotacao_fornecedores WHERE id = ?', (fornecedor_id,))
        fornecedor_atualizado = cursor.fetchone()
        
        conn.close()
        
        print(f"[EDITAR FORNECEDOR] Linhas afetadas: {linhas_afetadas}")
        print(f"[EDITAR FORNECEDOR] Nome após update: {fornecedor_atualizado['nome_fornecedor']}")
        print(f"[EDITAR FORNECEDOR] Fornecedor {fornecedor_id} atualizado com sucesso")
        
        return jsonify({
            'success': True, 
            'message': 'Informações do fornecedor atualizadas com sucesso',
            'fornecedor': {
                'nome': fornecedor_atualizado['nome_fornecedor'],
                'email': fornecedor_atualizado['email_fornecedor'],
                'telefone': fornecedor_atualizado['telefone_fornecedor']
            }
        })
        
    except Exception as e:
        print(f"[ERRO] api_editar_fornecedor: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/resposta/<int:resposta_id>/editar', methods=['POST'])
def api_editar_resposta_fornecedor(resposta_id):
    """
    API para editar resposta de fornecedor (pelo comprador).
    
    Campos editáveis:
    - preco_unitario
    - prazo_entrega
    - condicao_pagamento
    - frete_total
    - observacao
    
    REGRA: Edição sobrescreve resposta original, não depende do status da cotação.
    
    CORREÇÃO V2: Suporte a valores NULL/vazios para remover cotação do comparativo.
    Se allow_null=true, valores vazios são salvos como NULL no banco.
    """
    try:
        dados = request.get_json()
        
        print(f"[EDITAR RESPOSTA] ID: {resposta_id}, Dados: {dados}")
        
        # Extrai dados do request
        preco = dados.get('preco_unitario')
        prazo = dados.get('prazo_entrega')
        condicao = dados.get('condicao_pagamento')
        frete = dados.get('frete_total')
        observacao = dados.get('observacao')
        allow_null = dados.get('allow_null', False)  # Flag para permitir limpeza de valores
        
        # CORREÇÃO: Se allow_null, aceita campos vazios/null para limpeza
        # Valida que ao menos um campo foi enviado (exceto quando é limpeza intencional)
        if not allow_null and all(v is None for v in [preco, prazo, condicao, frete, observacao]):
            return jsonify({'success': False, 'error': 'Nenhum campo para atualizar'}), 400
        
        # Converte tipos se necessário
        # CORREÇÃO: Permite None para limpar campos
        if preco is not None:
            try:
                preco = float(preco)
            except (ValueError, TypeError):
                preco = None  # Se não converte, trata como null (limpeza)
        
        if prazo is not None:
            try:
                prazo = int(prazo)
            except (ValueError, TypeError):
                prazo = None  # Se não converte, trata como null (limpeza)
        
        if frete is not None:
            try:
                frete = float(frete)
            except (ValueError, TypeError):
                frete = 0
        
        # Atualiza no banco - CORREÇÃO: passa allow_null para função
        resultado = db.atualizar_resposta_fornecedor(
            resposta_id=resposta_id,
            preco=preco,
            prazo=prazo,
            condicao=condicao,
            frete=frete,
            observacao=observacao,
            allow_null=allow_null  # Nova flag
        )
        
        if resultado:
            # Busca dados atualizados para retornar
            resposta_atualizada = db.obter_resposta_por_id(resposta_id)
            
            return jsonify({
                'success': True, 
                'message': 'Resposta atualizada com sucesso',
                'resposta': resposta_atualizada
            })
        else:
            return jsonify({'success': False, 'error': 'Erro ao atualizar resposta'}), 500
        
    except Exception as e:
        print(f"[ERRO] api_editar_resposta_fornecedor: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/resposta/<int:resposta_id>/excluir', methods=['POST'])
def api_excluir_resposta_fornecedor(resposta_id):
    """
    API para excluir/remover completamente uma resposta de fornecedor.
    
    Use quando o usuário quer remover um fornecedor do comparativo para um item específico.
    Diferente de limpar campos (que mantém o registro com NULL), isso REMOVE o registro.
    """
    try:
        print(f"[EXCLUIR RESPOSTA] ID: {resposta_id}")
        
        # Verifica se existe
        resposta = db.obter_resposta_por_id(resposta_id)
        if not resposta:
            return jsonify({'success': False, 'error': 'Resposta não encontrada'}), 404
        
        # Exclui
        resultado = db.excluir_resposta_fornecedor(resposta_id)
        
        if resultado:
            return jsonify({
                'success': True, 
                'message': 'Resposta excluída com sucesso. O fornecedor não aparecerá mais no comparativo para este item.'
            })
        else:
            return jsonify({'success': False, 'error': 'Erro ao excluir resposta'}), 500
        
    except Exception as e:
        print(f"[ERRO] api_excluir_resposta_fornecedor: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/itens')
def api_obter_itens_fornecedor(fornecedor_id):
    """
    API para obter itens da cotação para um fornecedor específico (para popular modal).
    ATUALIZADO: Retorna frete/condição a nível de fornecedor (não por item).
    """
    try:
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        # Buscar dados do fornecedor (incluindo novos campos)
        cursor.execute('''
            SELECT f.id, f.cotacao_id, f.nome_fornecedor, f.status, f.token_acesso,
                   f.frete_total as frete_fornecedor, 
                   f.condicao_pagamento as condicao_fornecedor, 
                   f.observacao_geral,
                   c.codigo as cotacao_codigo, c.status as cotacao_status
            FROM cotacao_fornecedores f
            JOIN cotacoes c ON f.cotacao_id = c.id
            WHERE f.id = ?
        ''', (fornecedor_id,))
        
        fornecedor = cursor.fetchone()
        
        if not fornecedor:
            conn.close()
            return jsonify({'success': False, 'error': 'Fornecedor não encontrado'}), 404
        
        fornecedor = dict(fornecedor)
        
        # Buscar itens da cotação
        cursor.execute('SELECT * FROM cotacao_itens WHERE cotacao_id = ?', (fornecedor['cotacao_id'],))
        itens_raw = cursor.fetchall()
        
        # Buscar respostas existentes deste fornecedor
        cursor.execute('''
            SELECT * FROM cotacao_respostas 
            WHERE cotacao_id = ? AND fornecedor_id = ?
        ''', (fornecedor['cotacao_id'], fornecedor_id))
        respostas = {r['item_id']: dict(r) for r in cursor.fetchall()}
        
        conn.close()
        
        # Montar lista de itens com respostas
        itens = []
        for item_row in itens_raw:
            item = dict(item_row)
            item_id = item['id']
            
            if item_id in respostas:
                resp = respostas[item_id]
                item['resposta_id'] = resp['id']
                item['preco_unitario'] = resp.get('preco_unitario', 0)
                item['prazo_entrega'] = resp.get('prazo_entrega', 0)
                item['tem_resposta'] = True
            else:
                item['resposta_id'] = None
                item['preco_unitario'] = 0
                item['prazo_entrega'] = 0
                item['tem_resposta'] = False
            
            itens.append(item)
        
        return jsonify({
            'success': True,
            'fornecedor': {
                'id': fornecedor['id'],
                'nome': fornecedor['nome_fornecedor'],
                'status': fornecedor['status'],
                'token': fornecedor['token_acesso'],
                'frete': fornecedor.get('frete_fornecedor', 0) or 0,
                'condicao': fornecedor.get('condicao_fornecedor', '') or '',
                'observacao': fornecedor.get('observacao_geral', '') or ''
            },
            'cotacao': {
                'id': fornecedor['cotacao_id'],
                'codigo': fornecedor['cotacao_codigo'],
                'status': fornecedor['cotacao_status']
            },
            'itens': itens
        })
        
    except Exception as e:
        print(f"[ERRO] api_obter_itens_fornecedor: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/salvar-respostas', methods=['POST'])
def api_salvar_respostas_fornecedor(fornecedor_id):
    """
    API para salvar/atualizar múltiplas respostas de fornecedor via modal.
    ATUALIZADO: Frete e condição de pagamento são salvos a nível de fornecedor (não por item).
    
    CORREÇÃO V2: Agora persiste corretamente valores zerados/nulos para remover do comparativo.
    """
    try:
        dados = request.get_json()
        respostas = dados.get('respostas', [])
        frete_fornecedor = float(dados.get('frete', 0) or 0)
        condicao_fornecedor = dados.get('condicao', '') or ''
        observacao_fornecedor = dados.get('observacao', '') or ''
        
        if not respostas:
            return jsonify({'success': False, 'error': 'Nenhuma resposta enviada'}), 400
        
        # Buscar cotacao_id do fornecedor
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT cotacao_id FROM cotacao_fornecedores WHERE id = ?', (fornecedor_id,))
        forn_data = cursor.fetchone()
        
        if not forn_data:
            conn.close()
            return jsonify({'success': False, 'error': 'Fornecedor não encontrado'}), 404
        
        cotacao_id = forn_data['cotacao_id']
        
        # Salvar frete e condição a nível do fornecedor
        cursor.execute('''
            UPDATE cotacao_fornecedores 
            SET frete_total = ?, condicao_pagamento = ?, observacao_geral = ?
            WHERE id = ?
        ''', (frete_fornecedor, condicao_fornecedor, observacao_fornecedor, fornecedor_id))
        conn.commit()
        conn.close()
        
        # Processar cada resposta de item
        respostas_salvas = 0
        respostas_zeradas = 0
        
        for resp in respostas:
            item_id = resp.get('item_id')
            preco_raw = resp.get('preco')
            prazo_raw = resp.get('prazo')
            
            # Converte valores - trata string vazia e None como zero
            preco = float(preco_raw) if preco_raw not in [None, '', 0, '0'] else 0
            prazo = int(prazo_raw) if prazo_raw not in [None, '', 0, '0'] else 0
            
            # CORREÇÃO: Não pular mais itens com preço zero!
            # Se o preço é zero, ainda assim salvamos para atualizar o banco
            # Isso permite "limpar" uma cotação existente
            
            if preco > 0:
                # Item com cotação válida - salva normalmente
                db.registrar_resposta_fornecedor(
                    cotacao_id=cotacao_id,
                    fornecedor_id=fornecedor_id,
                    item_id=item_id,
                    preco=preco,
                    prazo=prazo,
                    condicao='',
                    observacao='',
                    frete=0
                )
                respostas_salvas += 1
            else:
                # CORREÇÃO: Item com preço zero - atualizar para zero se já existir resposta
                # Isso permite remover/zerar uma cotação que já foi preenchida
                conn2 = db.get_db_connection()
                cursor2 = conn2.cursor()
                
                # Verifica se já existe resposta para este item
                cursor2.execute('''
                    SELECT id FROM cotacao_respostas 
                    WHERE cotacao_id = ? AND fornecedor_id = ? AND item_id = ?
                ''', (cotacao_id, fornecedor_id, item_id))
                
                existente = cursor2.fetchone()
                
                if existente:
                    # Atualiza para zero (limpa a cotação)
                    cursor2.execute('''
                        UPDATE cotacao_respostas 
                        SET preco_unitario = 0, prazo_entrega = 0, data_resposta = ?
                        WHERE id = ?
                    ''', (datetime.now(), existente['id']))
                    conn2.commit()
                    respostas_zeradas += 1
                    print(f"[SALVAR RESPOSTAS] Item {item_id} zerado com sucesso (resposta ID: {existente['id']})")
                
                conn2.close()
        
        mensagem = f'{respostas_salvas} resposta(s) salva(s)'
        if respostas_zeradas > 0:
            mensagem += f', {respostas_zeradas} resposta(s) zerada(s)'
        mensagem += ' com sucesso!'
        
        return jsonify({
            'success': True, 
            'message': mensagem,
            'respostas_salvas': respostas_salvas,
            'respostas_zeradas': respostas_zeradas
        })
        
    except Exception as e:
        print(f"[ERRO] api_salvar_respostas_fornecedor: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/resposta/<int:resposta_id>')
def api_obter_resposta(resposta_id):
    """
    API para obter dados de uma resposta específica (para popular modal de edição).
    """
    try:
        resposta = db.obter_resposta_por_id(resposta_id)
        
        if not resposta:
            return jsonify({'success': False, 'error': 'Resposta não encontrada'}), 404
        
        return jsonify({
            'success': True,
            'resposta': resposta
        })
        
    except Exception as e:
        print(f"[ERRO] api_obter_resposta: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/<int:cotacao_id>/excluir', methods=['POST'])
def api_excluir_cotacao(cotacao_id):
    """
    API para excluir cotação.
    Só permite excluir cotações com status 'Aberta'
    """
    try:
        # Verificar se pode excluir
        cotacao = db.obter_cotacao(cotacao_id)
        
        if not cotacao:
            return jsonify({'success': False, 'error': 'Cotação não encontrada'}), 404
        
        if cotacao['status'] != 'Aberta':
            return jsonify({'success': False, 'error': 'Só é possível excluir cotações com status "Aberta"'}), 400
        
        # Excluir (sem validar fornecedores ou respostas)
        db.excluir_cotacao(cotacao_id)
        
        return jsonify({'success': True, 'message': 'Cotação excluída com sucesso'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# LINK INDIVIDUAL DO FORNECEDOR (ACESSO INTERNO)
# =============================================================================

@app.route('/cotacao/responder/<token>')
def cotacao_fornecedor(token):
    """
    Página para fornecedor responder cotação.
    Cada fornecedor possui um token exclusivo.
    Acesso restrito à rede interna da empresa.
    """
    dados = db.obter_cotacao_por_token(token)
    
    if not dados:
        return render_template('cotacao_erro.html', 
                             mensagem='Link inválido ou expirado',
                             detalhes='Verifique se o link está correto ou entre em contato com o comprador.')
    
    if dados['cotacao_status'] == 'Encerrada':
        return render_template('cotacao_erro.html', 
                             mensagem='Esta cotação já foi encerrada',
                             detalhes='O prazo para envio de propostas foi finalizado.')
    
    return render_template('cotacao_fornecedor.html', dados=dados, token=token)


@app.route('/api/cotacao/responder/<token>', methods=['POST'])
def api_responder_cotacao(token):
    """
    API para fornecedor enviar resposta da cotação.
    Valida o token e registra as respostas no banco.
    """
    try:
        dados_fornecedor = db.obter_cotacao_por_token(token)
        
        if not dados_fornecedor:
            return jsonify({'success': False, 'error': 'Token inválido ou expirado'}), 400
        
        if dados_fornecedor['cotacao_status'] == 'Encerrada':
            return jsonify({'success': False, 'error': 'Cotação já encerrada'}), 400
        
        dados = request.get_json()
        respostas = dados.get('respostas', [])
        
        if not respostas:
            return jsonify({'success': False, 'error': 'Nenhuma resposta enviada'}), 400
        
        for resp in respostas:
            db.registrar_resposta_fornecedor(
                cotacao_id=dados_fornecedor['cotacao_id'],
                fornecedor_id=dados_fornecedor['id'],
                item_id=resp.get('item_id'),
                preco=resp.get('preco', 0),
                prazo=resp.get('prazo', 0),
                condicao=resp.get('condicao', ''),
                observacao=resp.get('observacao', ''),
                frete=resp.get('frete', 0)
            )
        
        return jsonify({'success': True, 'message': 'Proposta enviada com sucesso!'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/anexo', methods=['POST'])
def api_upload_anexo_fornecedor(fornecedor_id):
    """
    API OTIMIZADA para upload de anexo/orçamento do fornecedor.
    Melhorias:
    - Upload em streaming (chunks de 8KB) para menor uso de memória
    - Validação de tipo e tamanho de arquivo
    - Armazenamento organizado por ano/mês
    - Metadados persistidos em tabela dedicada (cotacao_anexos)
    - Nomes únicos evitam sobrescrita
    """
    import uuid
    import mimetypes
    from datetime import datetime
    
    # Configurações de upload
    UPLOAD_MAX_SIZE = 10 * 1024 * 1024  # 10MB
    UPLOAD_CHUNK_SIZE = 8 * 1024  # 8KB para streaming
    ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls', 'xlsx'}
    
    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    
    def get_file_type(filename):
        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        if ext == 'pdf':
            return 'PDF'
        elif ext in {'jpg', 'jpeg', 'png'}:
            return 'Imagem'
        elif ext in {'doc', 'docx'}:
            return 'Word'
        elif ext in {'xls', 'xlsx'}:
            return 'Excel'
        return 'Outro'
    
    try:
        if 'arquivo' not in request.files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'}), 400
        
        arquivo = request.files['arquivo']
        
        if arquivo.filename == '':
            return jsonify({'success': False, 'error': 'Arquivo sem nome'}), 400
        
        # Validação de tipo de arquivo
        if not allowed_file(arquivo.filename):
            return jsonify({
                'success': False, 
                'error': f'Tipo de arquivo não permitido. Permitidos: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400
        
        # Busca cotacao_id do fornecedor
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT cotacao_id FROM cotacao_fornecedores WHERE id = ?', (fornecedor_id,))
        forn_data = cursor.fetchone()
        
        if not forn_data:
            conn.close()
            return jsonify({'success': False, 'error': 'Fornecedor não encontrado'}), 404
        
        cotacao_id = forn_data['cotacao_id']
        conn.close()
        
        # Cria diretório organizado por ano/mês
        now = datetime.now()
        upload_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 
            'uploads', 'cotacoes',
            str(now.year), f'{now.month:02d}'
        )
        os.makedirs(upload_dir, exist_ok=True)
        
        # Gera nome único para o arquivo
        nome_original = arquivo.filename
        ext = os.path.splitext(nome_original)[1].lower()
        nome_arquivo = f"anexo_cot{cotacao_id}_forn{fornecedor_id}_{uuid.uuid4().hex[:8]}{ext}"
        caminho_arquivo = os.path.join(upload_dir, nome_arquivo)
        
        # Upload em streaming (salva em chunks para economia de memória)
        tamanho_total = 0
        with open(caminho_arquivo, 'wb') as f:
            while True:
                chunk = arquivo.stream.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                tamanho_total += len(chunk)
                
                # Verifica limite de tamanho durante upload
                if tamanho_total > UPLOAD_MAX_SIZE:
                    f.close()
                    os.remove(caminho_arquivo)
                    return jsonify({
                        'success': False, 
                        'error': f'Arquivo muito grande. Máximo permitido: {UPLOAD_MAX_SIZE // (1024*1024)}MB'
                    }), 400
                
                f.write(chunk)
        
        # Determina mime type
        mime_type, _ = mimetypes.guess_type(nome_original)
        tipo_arquivo = get_file_type(nome_original)
        
        # Salva metadados na tabela dedicada
        anexo_id = db.salvar_metadados_anexo(
            cotacao_id=cotacao_id,
            fornecedor_id=fornecedor_id,
            nome_original=nome_original,
            nome_arquivo=nome_arquivo,
            caminho_arquivo=caminho_arquivo,
            tipo_arquivo=tipo_arquivo,
            tamanho_bytes=tamanho_total,
            mime_type=mime_type or 'application/octet-stream',
            usuario=session.get('usuario', 'Admin')
        )
        
        # TAMBÉM atualiza cotacao_respostas para compatibilidade com código legado
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id FROM cotacao_respostas 
            WHERE fornecedor_id = ? 
            LIMIT 1
        ''', (fornecedor_id,))
        
        resposta_existente = cursor.fetchone()
        
        if resposta_existente:
            cursor.execute('''
                UPDATE cotacao_respostas 
                SET arquivo_anexo = ?
                WHERE fornecedor_id = ?
            ''', (caminho_arquivo, fornecedor_id))
        else:
            cursor.execute('SELECT id FROM cotacao_itens WHERE cotacao_id = ? LIMIT 1', (cotacao_id,))
            item = cursor.fetchone()
            
            if item:
                cursor.execute('''
                    INSERT INTO cotacao_respostas 
                    (cotacao_id, fornecedor_id, item_id, arquivo_anexo)
                    VALUES (?, ?, ?, ?)
                ''', (cotacao_id, fornecedor_id, item['id'], caminho_arquivo))
        
        conn.commit()
        conn.close()
        
        print(f"[ANEXO] Upload realizado com sucesso: {caminho_arquivo} ({tamanho_total/1024:.1f}KB)")
        
        return jsonify({
            'success': True, 
            'message': 'Anexo enviado com sucesso',
            'arquivo': nome_arquivo,
            'anexo_id': anexo_id,
            'tamanho': tamanho_total,
            'tipo': tipo_arquivo
        })
        
    except Exception as e:
        print(f"[ERRO] api_upload_anexo_fornecedor: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/cotacao/anexo/<int:fornecedor_id>')
def visualizar_anexo_fornecedor(fornecedor_id):
    """
    Rota para VISUALIZAÇÃO de anexo enviado pelo fornecedor (sem download automático).
    O anexo é armazenado no campo arquivo_anexo da tabela cotacao_respostas.
    
    ALTERAÇÃO: Content-Disposition: inline para abrir no navegador
    """
    try:
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        # Busca o anexo do fornecedor
        cursor.execute('''
            SELECT arquivo_anexo 
            FROM cotacao_respostas 
            WHERE fornecedor_id = ? AND arquivo_anexo IS NOT NULL AND arquivo_anexo != ''
            LIMIT 1
        ''', (fornecedor_id,))
        
        resultado = cursor.fetchone()
        conn.close()
        
        if not resultado or not resultado['arquivo_anexo']:
            flash('Nenhum anexo encontrado para este fornecedor', 'warning')
            return redirect(request.referrer or url_for('cotacoes'))
        
        arquivo_path = resultado['arquivo_anexo']
        
        # Verifica se é um caminho de arquivo
        if os.path.exists(arquivo_path):
            # Determina o mimetype baseado na extensão
            import mimetypes
            mimetype, _ = mimetypes.guess_type(arquivo_path)
            if not mimetype:
                mimetype = 'application/octet-stream'
            
            # Envia arquivo para VISUALIZAÇÃO (não download)
            # as_attachment=False + inline = abre no navegador
            response = send_file(
                arquivo_path, 
                mimetype=mimetype,
                as_attachment=False  # Não forçar download
            )
            # Força header inline para abrir no navegador
            response.headers['Content-Disposition'] = f'inline; filename="{os.path.basename(arquivo_path)}"'
            return response
        else:
            # Se for URL externa, redireciona
            return redirect(arquivo_path)
            
    except Exception as e:
        flash(f'Erro ao acessar anexo: {str(e)}', 'danger')
        return redirect(request.referrer or url_for('cotacoes'))


@app.route('/cotacao/anexo/<int:fornecedor_id>/download')
def download_anexo_fornecedor(fornecedor_id):
    """
    Rota para DOWNLOAD de anexo (quando usuário escolhe baixar explicitamente).
    """
    try:
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT arquivo_anexo 
            FROM cotacao_respostas 
            WHERE fornecedor_id = ? AND arquivo_anexo IS NOT NULL AND arquivo_anexo != ''
            LIMIT 1
        ''', (fornecedor_id,))
        
        resultado = cursor.fetchone()
        conn.close()
        
        if not resultado or not resultado['arquivo_anexo']:
            flash('Nenhum anexo encontrado para este fornecedor', 'warning')
            return redirect(request.referrer or url_for('cotacoes'))
        
        arquivo_path = resultado['arquivo_anexo']
        
        if os.path.exists(arquivo_path):
            return send_file(arquivo_path, as_attachment=True)
        else:
            return redirect(arquivo_path)
            
    except Exception as e:
        flash(f'Erro ao baixar anexo: {str(e)}', 'danger')
        return redirect(request.referrer or url_for('cotacoes'))


# =============================================================================
# COTAÇÃO EXTERNA VIA JSON
# =============================================================================

import hashlib
import secrets

def gerar_token_json():
    """Gera um token único para identificar o envio de JSON"""
    return secrets.token_urlsafe(32)

def gerar_hash_validacao(dados_json):
    """Gera hash SHA256 para validação de integridade do JSON"""
    json_str = json.dumps(dados_json, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

def validar_hash_json(dados_json, hash_esperado):
    """Valida se o hash do JSON corresponde ao esperado"""
    hash_calculado = gerar_hash_validacao(dados_json)
    return hash_calculado == hash_esperado


def gerar_html_cotacao_externa(dados_cotacao):
    """
    Gera um arquivo HTML standalone que o fornecedor pode abrir no navegador,
    preencher os valores e baixar o JSON de resposta.
    Visual idêntico ao modal de cotação do sistema.
    """
    itens_html = ""
    for i, item in enumerate(dados_cotacao['itens']):
        itens_html += f'''
                <tr data-item-id="{item['id']}" data-index="{i}">
                    <td class="ps-3"><small class="text-muted">{item['codigo_produto'].strip()}</small></td>
                    <td><strong>{item['descricao']}</strong></td>
                    <td class="text-center">{item['quantidade']} {item['unidade']}</td>
                    <td><input type="number" class="form-control form-control-sm preco-input" step="0.01" min="0" placeholder="0,00" data-index="{i}"></td>
                    <td><input type="number" class="form-control form-control-sm prazo-input" min="0" placeholder="0" data-index="{i}"></td>
                    <td><input type="text" class="form-control form-control-sm obs-input" placeholder="Observação do item..." data-index="{i}"></td>
                </tr>'''
    
    html_content = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cotação - {dados_cotacao['fornecedor']['nome']}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; }}
        body {{ 
            background-color: #f0f0f0; 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            padding: 20px;
        }}
        .cotacao-container {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .cotacao-header {{
            background: linear-gradient(135deg, #1a5a3c 0%, #2d7a5e 100%);
            color: white;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .cotacao-header h5 {{
            margin: 0;
            font-weight: 600;
        }}
        .cotacao-header .btn-close {{
            filter: brightness(0) invert(1);
            opacity: 0.8;
        }}
        .cotacao-body {{
            padding: 20px;
        }}
        .alert-instrucao {{
            background-color: #e8f4fd;
            border: 1px solid #b8daff;
            border-radius: 6px;
            padding: 12px 15px;
            margin-bottom: 20px;
            color: #004085;
            font-size: 14px;
        }}
        .alert-instrucao i {{
            color: #0066cc;
        }}
        .section-title {{
            font-size: 14px;
            font-weight: 600;
            color: #333;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .section-title i {{
            color: #1a5a3c;
        }}
        .table {{
            font-size: 13px;
            margin-bottom: 0;
        }}
        .table thead {{
            background-color: #f8f9fa;
        }}
        .table thead th {{
            font-weight: 600;
            color: #495057;
            border-bottom: 2px solid #dee2e6;
            padding: 10px 8px;
            font-size: 12px;
            text-transform: uppercase;
        }}
        .table tbody td {{
            padding: 10px 8px;
            vertical-align: middle;
            border-bottom: 1px solid #eee;
        }}
        .table tbody tr:hover {{
            background-color: #f8f9fa;
        }}
        .preco-input, .prazo-input {{
            width: 90px;
            text-align: right;
            border: 1px solid #ced4da;
            border-radius: 4px;
            padding: 6px 10px;
        }}
        .obs-input {{
            width: 200px;
            border: 1px solid #ced4da;
            border-radius: 4px;
            padding: 6px 10px;
        }}
        .preco-input:focus, .prazo-input:focus, .obs-input:focus {{
            border-color: #1a5a3c;
            box-shadow: 0 0 0 2px rgba(26,90,60,0.15);
            outline: none;
        }}
        .info-gerais {{
            background-color: #f8f9fa;
            border-radius: 6px;
            padding: 15px;
            margin-top: 20px;
        }}
        .info-gerais .form-label {{
            font-weight: 500;
            font-size: 13px;
            color: #495057;
            margin-bottom: 5px;
        }}
        .info-gerais .form-control {{
            font-size: 14px;
        }}
        .info-gerais small {{
            color: #6c757d;
            font-size: 11px;
        }}
        .cotacao-footer {{
            padding: 15px 20px;
            background-color: #f8f9fa;
            border-top: 1px solid #dee2e6;
            display: flex;
            justify-content: flex-end;
            gap: 10px;
        }}
        .btn-cancelar {{
            background-color: #6c757d;
            border: none;
            color: white;
            padding: 10px 25px;
            border-radius: 5px;
            font-weight: 500;
        }}
        .btn-salvar {{
            background-color: #1a5a3c;
            border: none;
            color: white;
            padding: 10px 25px;
            border-radius: 5px;
            font-weight: 500;
        }}
        .btn-salvar:hover {{
            background-color: #2d7a5e;
        }}
        .btn-salvar i {{
            margin-right: 5px;
        }}
        @media print {{
            body {{ background: white; padding: 0; }}
            .cotacao-container {{ box-shadow: none; }}
            .cotacao-footer {{ display: none; }}
            .preco-input, .prazo-input {{ border: 1px solid #ccc; }}
            .instrucoes-box {{ display: none; }}
        }}
        .instrucoes-box {{
            background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
            border: 1px solid #81c784;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
        }}
        .instrucoes-box h6 {{
            color: #2e7d32;
            margin-bottom: 10px;
        }}
        .instrucoes-box ol {{
            margin-bottom: 0;
            padding-left: 20px;
        }}
        .instrucoes-box li {{
            margin-bottom: 5px;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <div class="cotacao-container">
        <!-- Header -->
        <div class="cotacao-header">
            <h5><i class="fas fa-file-invoice me-2"></i>Cotação - {dados_cotacao['fornecedor']['nome']}</h5>
        </div>
        
        <!-- Body -->
        <div class="cotacao-body">
            <!-- Instruções detalhadas -->
            <div class="instrucoes-box">
                <h6><i class="fas fa-clipboard-list me-2"></i>Instruções para Preenchimento</h6>
                <ol>
                    <li><strong>Preencha</strong> o preço unitário (R$) e prazo de entrega (dias) para cada item</li>
                    <li>Use o campo <strong>Observação</strong> para informações específicas de cada item</li>
                    <li>Preencha as <strong>Informações Gerais</strong> (frete, condição de pagamento)</li>
                    <li>Clique em <strong>"Salvar Respostas"</strong> - um arquivo JSON será baixado</li>
                    <li><strong>Envie o arquivo JSON</strong> de volta por e-mail ao comprador</li>
                </ol>
            </div>
            
            <!-- Instrução resumida -->
            <div class="alert-instrucao">
                <i class="fas fa-info-circle me-2"></i>
                Preencha os valores abaixo e clique em <strong>"Salvar Respostas"</strong> para gerar o arquivo de retorno.
            </div>
            
            <!-- Tabela de Itens -->
            <div class="section-title">
                <i class="fas fa-list"></i>
                Itens da Cotação
            </div>
            
            <div class="table-responsive">
                <table class="table">
                    <thead>
                        <tr>
                            <th style="width: 100px;">CÓDIGO</th>
                            <th>DESCRIÇÃO</th>
                            <th class="text-center" style="width: 80px;">QTD</th>
                            <th class="text-center" style="width: 120px;">PREÇO UNIT. (R$)</th>
                            <th class="text-center" style="width: 100px;">PRAZO (DIAS)</th>
                            <th style="width: 220px;">OBSERVAÇÃO</th>
                        </tr>
                    </thead>
                    <tbody>
                        {itens_html}
                    </tbody>
                </table>
            </div>
            
            <!-- Informações Gerais do Fornecedor -->
            <div class="info-gerais">
                <div class="section-title">
                    <i class="fas fa-truck"></i>
                    Informações Gerais do Fornecedor
                </div>
                <div class="row">
                    <div class="col-md-4">
                        <label class="form-label">Frete (R$)</label>
                        <input type="number" class="form-control" id="freteTotal" step="0.01" min="0" placeholder="0,00">
                        <small>Valor único para todos os itens</small>
                    </div>
                    <div class="col-md-4">
                        <label class="form-label">Condição de Pagamento</label>
                        <input type="text" class="form-control" id="condicaoPagamento" placeholder="Ex: 30 DDL, À vista, etc.">
                    </div>
                    <div class="col-md-4">
                        <label class="form-label">Observação</label>
                        <input type="text" class="form-control" id="observacaoGeral" placeholder="Observações gerais...">
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Footer -->
        <div class="cotacao-footer">
            <button class="btn-cancelar" onclick="window.print()">
                Imprimir
            </button>
            <button class="btn-salvar" onclick="salvarRespostas()">
                <i class="fas fa-save"></i> Salvar Respostas
            </button>
        </div>
    </div>
    
    <script>
        // Dados originais da cotação
        const dadosCotacao = {json.dumps(dados_cotacao, ensure_ascii=False)};
        
        function coletarDados() {{
            // Coleta respostas dos itens
            document.querySelectorAll('table tbody tr').forEach((row, index) => {{
                const precoInput = row.querySelector('.preco-input');
                const prazoInput = row.querySelector('.prazo-input');
                const obsInput = row.querySelector('.obs-input');
                
                dadosCotacao.itens[index].resposta.preco_unitario = precoInput.value ? parseFloat(precoInput.value) : null;
                dadosCotacao.itens[index].resposta.prazo_entrega_dias = prazoInput.value ? parseInt(prazoInput.value) : null;
                dadosCotacao.itens[index].resposta.observacao = obsInput.value || null;
            }});
            
            // Coleta informações gerais
            dadosCotacao.resposta_geral.frete_total = document.getElementById('freteTotal').value ? parseFloat(document.getElementById('freteTotal').value) : null;
            dadosCotacao.resposta_geral.condicao_pagamento = document.getElementById('condicaoPagamento').value || null;
            dadosCotacao.resposta_geral.observacao_geral = document.getElementById('observacaoGeral').value || null;
            dadosCotacao.resposta_geral.data_resposta = new Date().toISOString();
            
            // Muda o tipo para resposta
            dadosCotacao.tipo = 'RESPOSTA_COTACAO';
            
            return dadosCotacao;
        }}
        
        function salvarRespostas() {{
            const dados = coletarDados();
            
            // Verifica se pelo menos um preço foi preenchido
            const temPreco = dados.itens.some(item => item.resposta.preco_unitario !== null && item.resposta.preco_unitario > 0);
            if (!temPreco) {{
                if (!confirm('Nenhum preço foi preenchido. Deseja salvar mesmo assim?')) {{
                    return;
                }}
            }}
            
            // Cria o arquivo para download
            const jsonString = JSON.stringify(dados, null, 2);
            const blob = new Blob([jsonString], {{ type: 'application/json' }});
            const url = URL.createObjectURL(blob);
            
            // Nome do arquivo de resposta
            const nomeArquivo = 'RESPOSTA_' + dadosCotacao.token.substring(0, 8) + '.json';
            
            // Cria link de download
            const a = document.createElement('a');
            a.href = url;
            a.download = nomeArquivo;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            
            alert('✅ Respostas salvas com sucesso!\\n\\nArquivo: ' + nomeArquivo + '\\n\\nEnvie este arquivo de volta ao comprador.');
        }}
        
        // Formata inputs de preço ao sair do campo
        document.querySelectorAll('.preco-input').forEach(input => {{
            input.addEventListener('blur', function() {{
                if (this.value) {{
                    this.value = parseFloat(this.value).toFixed(2);
                }}
            }});
        }});
    </script>
</body>
</html>'''
    
    return html_content


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/gerar-json', methods=['POST'])
def api_gerar_json_cotacao(fornecedor_id):
    """
    API para gerar JSON da cotação para envio externo ao fornecedor.
    O JSON contém todos os dados necessários para o fornecedor responder sem acessar o sistema.
    """
    try:
        # Busca dados do fornecedor
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT f.*, c.codigo as cotacao_codigo, c.id as cotacao_id,
                   c.observacoes as cotacao_observacoes, c.informacao_fornecedor
            FROM cotacao_fornecedores f
            JOIN cotacoes c ON f.cotacao_id = c.id
            WHERE f.id = ?
        ''', (fornecedor_id,))
        fornecedor = cursor.fetchone()
        
        if not fornecedor:
            conn.close()
            return jsonify({'success': False, 'error': 'Fornecedor não encontrado'}), 404
        
        # Busca itens da cotação
        cursor.execute('''
            SELECT i.id, i.numero_sc, i.item_sc, i.cod_produto, i.descricao_produto, 
                   i.quantidade, i.unidade, i.observacao
            FROM cotacao_itens i
            WHERE i.cotacao_id = ?
            ORDER BY i.numero_sc, i.item_sc
        ''', (fornecedor['cotacao_id'],))
        itens = [dict(item) for item in cursor.fetchall()]
        conn.close()
        
        # Gera token único e hash
        token_envio = gerar_token_json()
        
        # Monta estrutura do JSON
        dados_cotacao = {
            'versao': '1.0',
            'tipo': 'SOLICITACAO_COTACAO',
            'token': token_envio,
            'data_geracao': datetime.now().isoformat(),
            
            'cotacao': {
                'codigo': fornecedor['cotacao_codigo'],
                'observacoes': fornecedor['cotacao_observacoes'] or '',
                'informacao_fornecedor': fornecedor['informacao_fornecedor'] or ''
            },
            
            'fornecedor': {
                'id_interno': fornecedor['id'],
                'nome': fornecedor['nome_fornecedor'],
                'email': fornecedor['email_fornecedor'] or '',
                'telefone': fornecedor['telefone_fornecedor'] or ''
            },
            
            'itens': [
                {
                    'id': item['id'],
                    'numero_sc': item['numero_sc'],
                    'item_sc': item['item_sc'],
                    'codigo_produto': item['cod_produto'] or '',
                    'descricao': item['descricao_produto'],
                    'quantidade': item['quantidade'],
                    'unidade': item['unidade'] or 'UN',
                    'observacao': item['observacao'] or '',
                    # Campos para preenchimento pelo fornecedor
                    'resposta': {
                        'preco_unitario': None,
                        'prazo_entrega_dias': None,
                        'observacao': None
                    }
                }
                for item in itens
            ],
            
            # Campos gerais da resposta
            'resposta_geral': {
                'frete_total': None,
                'condicao_pagamento': None,
                'observacao_geral': None,
                'data_resposta': None
            },
            
            'instrucoes': {
                'preenchimento': [
                    'Preencha o campo "preco_unitario" com o valor em reais (use ponto como separador decimal)',
                    'Preencha "prazo_entrega_dias" com o número de dias para entrega',
                    'Informe "frete_total" se houver custo de frete (valor total)',
                    'Preencha "condicao_pagamento" com as condições (ex: "30 DDL")',
                    'Use os campos de "observacao" para informações adicionais'
                ],
                'retorno': 'Após preencher, salve o arquivo e envie de volta ao comprador'
            }
        }
        
        # Gera hash de validação (excluindo campos de resposta)
        dados_para_hash = {
            'token': token_envio,
            'cotacao_codigo': fornecedor['cotacao_codigo'],
            'fornecedor_id': fornecedor['id'],
            'itens_ids': [item['id'] for item in itens]
        }
        hash_validacao = gerar_hash_validacao(dados_para_hash)
        dados_cotacao['hash_validacao'] = hash_validacao
        
        # Salva o diretório de JSONs
        json_dir = os.path.join('uploads', 'json_cotacoes')
        os.makedirs(json_dir, exist_ok=True)
        
        # Nome do arquivo - sanitiza caracteres inválidos para Windows
        codigo_sanitizado = fornecedor['cotacao_codigo'].replace('/', '-').replace('\\', '-').replace(':', '-')
        nome_sanitizado = fornecedor['nome_fornecedor'].replace(' ', '_').replace('/', '-').replace('\\', '-')
        nome_base = f"cotacao_{codigo_sanitizado}_{nome_sanitizado}_{token_envio[:8]}"
        
        nome_arquivo_json = f"{nome_base}.json"
        nome_arquivo_html = f"{nome_base}.html"
        
        caminho_arquivo_json = os.path.join(json_dir, nome_arquivo_json)
        caminho_arquivo_html = os.path.join(json_dir, nome_arquivo_html)
        
        # Salva arquivo JSON (backup)
        with open(caminho_arquivo_json, 'w', encoding='utf-8') as f:
            json.dump(dados_cotacao, f, ensure_ascii=False, indent=2)
        
        # Gera e salva arquivo HTML (para o fornecedor)
        html_content = gerar_html_cotacao_externa(dados_cotacao)
        with open(caminho_arquivo_html, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # Registra no banco
        usuario = session.get('username', 'Admin')
        db.criar_envio_json(
            cotacao_id=fornecedor['cotacao_id'],
            fornecedor_id=fornecedor_id,
            token_envio=token_envio,
            hash_validacao=hash_validacao,
            arquivo_json=caminho_arquivo_json,
            usuario=usuario
        )
        
        print(f"[JSON] Gerado arquivos {nome_arquivo_json} e {nome_arquivo_html} para fornecedor {fornecedor['nome_fornecedor']}")
        
        return jsonify({
            'success': True,
            'message': f'Arquivos gerados com sucesso',
            'arquivo': nome_arquivo_html,
            'arquivo_json': nome_arquivo_json,
            'token': token_envio,
            'download_url': f'/api/cotacao/html/download/{token_envio}'
        })
        
    except Exception as e:
        print(f"[ERRO] api_gerar_json_cotacao: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/json/download/<token>')
def api_download_json_cotacao(token):
    """Download do arquivo JSON gerado"""
    try:
        envio = db.obter_envio_json_por_token(token)
        
        if not envio:
            return jsonify({'success': False, 'error': 'Token inválido'}), 404
        
        arquivo_path = envio['arquivo_json_gerado']
        
        if not arquivo_path or not os.path.exists(arquivo_path):
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404
        
        return send_file(arquivo_path, as_attachment=True)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/html/download/<token>')
def api_download_html_cotacao(token):
    """Download do arquivo HTML para o fornecedor preencher"""
    try:
        envio = db.obter_envio_json_por_token(token)
        
        if not envio:
            return jsonify({'success': False, 'error': 'Token inválido'}), 404
        
        # O arquivo HTML tem o mesmo nome base do JSON, mas com extensão .html
        arquivo_json_path = envio['arquivo_json_gerado']
        arquivo_html_path = arquivo_json_path.replace('.json', '.html')
        
        if not arquivo_html_path or not os.path.exists(arquivo_html_path):
            return jsonify({'success': False, 'error': 'Arquivo HTML não encontrado'}), 404
        
        return send_file(arquivo_html_path, as_attachment=True)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/importar-json', methods=['POST'])
def api_importar_json_cotacao(fornecedor_id):
    """
    API para importar JSON de resposta do fornecedor.
    Valida a estrutura, token e hash antes de registrar as respostas.
    """
    try:
        if 'arquivo' not in request.files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'}), 400
        
        arquivo = request.files['arquivo']
        
        if arquivo.filename == '':
            return jsonify({'success': False, 'error': 'Arquivo não selecionado'}), 400
        
        if not arquivo.filename.endswith('.json'):
            return jsonify({'success': False, 'error': 'Arquivo deve ser JSON'}), 400
        
        # Lê e parseia o JSON
        try:
            conteudo = arquivo.read().decode('utf-8')
            dados_json = json.loads(conteudo)
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': f'JSON inválido: {str(e)}'}), 400
        
        # Validações básicas de estrutura
        campos_obrigatorios = ['versao', 'tipo', 'token', 'hash_validacao', 'fornecedor', 'itens']
        for campo in campos_obrigatorios:
            if campo not in dados_json:
                return jsonify({'success': False, 'error': f'Campo obrigatório ausente: {campo}'}), 400
        
        if dados_json['tipo'] != 'SOLICITACAO_COTACAO':
            return jsonify({'success': False, 'error': 'Tipo de documento inválido'}), 400
        
        # Valida token
        token = dados_json['token']
        envio = db.obter_envio_json_por_token(token)
        
        if not envio:
            return jsonify({'success': False, 'error': 'Token não reconhecido. Este JSON não foi gerado pelo sistema.'}), 400
        
        if envio['fornecedor_id'] != fornecedor_id:
            return jsonify({'success': False, 'error': 'Token não corresponde a este fornecedor'}), 400
        
        # Valida hash de integridade
        hash_original = envio['hash_validacao']
        dados_para_hash = {
            'token': token,
            'cotacao_codigo': dados_json['cotacao']['codigo'],
            'fornecedor_id': dados_json['fornecedor']['id_interno'],
            'itens_ids': [item['id'] for item in dados_json['itens']]
        }
        
        if not validar_hash_json(dados_para_hash, hash_original):
            return jsonify({'success': False, 'error': 'Integridade do JSON comprometida. Os dados foram alterados.'}), 400
        
        # Processa as respostas
        cotacao_id = envio['cotacao_id']
        itens_processados = 0
        
        for item in dados_json['itens']:
            resposta = item.get('resposta', {})
            preco = resposta.get('preco_unitario')
            prazo = resposta.get('prazo_entrega_dias')
            obs_item = resposta.get('observacao')
            
            # Só registra se tiver pelo menos preço
            if preco is not None:
                db.registrar_resposta_fornecedor(
                    cotacao_id=cotacao_id,
                    fornecedor_id=fornecedor_id,
                    item_id=item['id'],
                    preco=float(preco) if preco else None,
                    prazo=int(prazo) if prazo else None,
                    condicao='',  # Condição vem no nível do fornecedor, não do item
                    observacao=obs_item or ''
                )
                itens_processados += 1
        
        # Processa resposta geral (frete, condição pagamento)
        resposta_geral = dados_json.get('resposta_geral', {})
        frete = resposta_geral.get('frete_total')
        condicao = resposta_geral.get('condicao_pagamento')
        obs_geral = resposta_geral.get('observacao_geral')
        
        if frete is not None or condicao or obs_geral:
            db.editar_fornecedor_cotacao(
                fornecedor_id,
                frete_total=float(frete) if frete else None,
                condicao_pagamento=condicao,
                observacao_geral=obs_geral
            )
        
        # Atualiza status do fornecedor
        if itens_processados > 0:
            db.atualizar_status_fornecedor(fornecedor_id, 'Respondido')
        
        # Salva arquivo de resposta
        json_resp_dir = os.path.join('uploads', 'json_cotacoes', 'respostas')
        os.makedirs(json_resp_dir, exist_ok=True)
        nome_resp = f"resposta_{token[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        caminho_resp = os.path.join(json_resp_dir, nome_resp)
        
        with open(caminho_resp, 'w', encoding='utf-8') as f:
            json.dump(dados_json, f, ensure_ascii=False, indent=2)
        
        # Atualiza registro de envio
        usuario = session.get('username', 'Admin')
        db.atualizar_importacao_json(envio['id'], caminho_resp, usuario)
        
        print(f"[JSON] Importado resposta do fornecedor {fornecedor_id}, {itens_processados} itens processados")
        
        return jsonify({
            'success': True,
            'message': f'JSON importado com sucesso! {itens_processados} item(ns) processado(s).',
            'itens_processados': itens_processados
        })
        
    except Exception as e:
        print(f"[ERRO] api_importar_json_cotacao: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/historico-json')
def api_historico_json_fornecedor(fornecedor_id):
    """API para obter histórico de envios/importações de JSON de um fornecedor"""
    try:
        envios = db.obter_envios_json_fornecedor(fornecedor_id)
        return jsonify({'success': True, 'envios': envios})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# COTAÇÃO EXTERNA - PÁGINA PÚBLICA PARA FORNECEDORES
# =============================================================================

@app.route('/cotacao/externa/<token>')
def pagina_cotacao_externa(token):
    """
    Página pública de cotação externa para fornecedores.
    Não requer login, acesso via token único.
    
    FLUXO:
    1. Sistema interno gera JSON com token único
    2. Link é enviado ao fornecedor (por email ou outro meio)
    3. Fornecedor acessa esta página, preenche preços
    4. Fornecedor envia resposta (online ou baixa JSON)
    5. Sistema valida e importa a resposta
    """
    try:
        # Busca envio pelo token
        envio = db.obter_envio_json_por_token(token)
        
        if not envio:
            return render_template('cotacao_erro.html', 
                erro='Link de cotação inválido ou expirado.',
                mensagem='Por favor, solicite um novo link ao comprador.')
        
        # Verifica se já foi respondido
        if envio.get('status') == 'Importado':
            return render_template('cotacao_erro.html', 
                erro='Esta cotação já foi respondida.',
                mensagem='Se precisar alterar sua resposta, entre em contato com o comprador.')
        
        # Carrega os dados do JSON gerado
        arquivo_json = envio.get('arquivo_json_gerado')
        
        if not arquivo_json or not os.path.exists(arquivo_json):
            return render_template('cotacao_erro.html', 
                erro='Arquivo de cotação não encontrado.',
                mensagem='Por favor, solicite um novo link ao comprador.')
        
        with open(arquivo_json, 'r', encoding='utf-8') as f:
            dados_cotacao = json.load(f)
        
        return render_template('cotacao_externa.html', 
            dados=dados_cotacao,
            ano_atual=datetime.now().year)
        
    except Exception as e:
        print(f"[ERRO] pagina_cotacao_externa: {e}")
        import traceback
        traceback.print_exc()
        return render_template('cotacao_erro.html', 
            erro='Erro ao carregar cotação.',
            mensagem=str(e))


@app.route('/api/cotacao/externa/responder/<token>', methods=['POST'])
def api_responder_cotacao_externa(token):
    """
    API para receber resposta de cotação externa do fornecedor.
    Valida token e hash antes de registrar as respostas.
    
    Esta rota pode ser chamada:
    - Diretamente pela página de cotação externa (se fornecedor tiver acesso à rede)
    - Futuramente por um servidor proxy externo
    """
    try:
        # Valida token
        envio = db.obter_envio_json_por_token(token)
        
        if not envio:
            return jsonify({'success': False, 'error': 'Token inválido ou expirado'}), 400
        
        if envio.get('status') == 'Importado':
            return jsonify({'success': False, 'error': 'Esta cotação já foi respondida'}), 400
        
        # Obtém dados do request
        dados_json = request.get_json()
        
        if not dados_json:
            return jsonify({'success': False, 'error': 'Dados não recebidos'}), 400
        
        # Valida estrutura básica
        if dados_json.get('token') != token:
            return jsonify({'success': False, 'error': 'Token não corresponde'}), 400
        
        # Valida hash de integridade
        hash_original = envio['hash_validacao']
        dados_para_hash = {
            'token': token,
            'cotacao_codigo': dados_json['cotacao']['codigo'],
            'fornecedor_id': dados_json['fornecedor']['id_interno'],
            'itens_ids': [item['id'] for item in dados_json['itens']]
        }
        
        if not validar_hash_json(dados_para_hash, hash_original):
            return jsonify({'success': False, 'error': 'Integridade comprometida - dados foram alterados'}), 400
        
        # Processa respostas dos itens
        cotacao_id = envio['cotacao_id']
        fornecedor_id = envio['fornecedor_id']
        itens_processados = 0
        
        for item in dados_json.get('itens', []):
            resposta = item.get('resposta', {})
            preco = resposta.get('preco_unitario')
            prazo = resposta.get('prazo_entrega_dias')
            obs_item = resposta.get('observacao')
            
            # Só registra se tiver preço válido
            if preco is not None and preco > 0:
                db.registrar_resposta_fornecedor(
                    cotacao_id=cotacao_id,
                    fornecedor_id=fornecedor_id,
                    item_id=item['id'],
                    preco=float(preco),
                    prazo=int(prazo) if prazo else 0,
                    condicao='',
                    observacao=obs_item or ''
                )
                itens_processados += 1
        
        # Processa resposta geral (frete, condição pagamento)
        resposta_geral = dados_json.get('resposta_geral', {})
        frete = resposta_geral.get('frete_total')
        condicao = resposta_geral.get('condicao_pagamento')
        obs_geral = resposta_geral.get('observacao_geral')
        
        if frete is not None or condicao or obs_geral:
            conn = db.get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE cotacao_fornecedores 
                SET frete_total = ?, condicao_pagamento = ?, observacao_geral = ?
                WHERE id = ?
            ''', (float(frete) if frete else 0, condicao or '', obs_geral or '', fornecedor_id))
            conn.commit()
            conn.close()
        
        # Atualiza status do fornecedor
        if itens_processados > 0:
            db.atualizar_status_fornecedor(fornecedor_id, 'Respondido')
        
        # Salva arquivo de resposta
        json_resp_dir = os.path.join('uploads', 'json_cotacoes', 'respostas')
        os.makedirs(json_resp_dir, exist_ok=True)
        nome_resp = f"resposta_externa_{token[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        caminho_resp = os.path.join(json_resp_dir, nome_resp)
        
        with open(caminho_resp, 'w', encoding='utf-8') as f:
            json.dump(dados_json, f, ensure_ascii=False, indent=2)
        
        # Atualiza registro de envio
        db.atualizar_importacao_json(envio['id'], caminho_resp, 'Fornecedor (Externo)')
        
        print(f"[COTAÇÃO EXTERNA] Resposta recebida - Token: {token[:8]}..., {itens_processados} itens")
        
        return jsonify({
            'success': True,
            'message': f'Cotação enviada com sucesso! {itens_processados} item(ns) processado(s).',
            'itens_processados': itens_processados
        })
        
    except Exception as e:
        print(f"[ERRO] api_responder_cotacao_externa: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/gerar-link-externo', methods=['POST'])
def api_gerar_link_externo(fornecedor_id):
    """
    Gera JSON e link para cotação externa.
    Retorna o link que pode ser enviado ao fornecedor por email.
    """
    try:
        # Primeiro, gera o JSON
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT f.*, c.codigo as cotacao_codigo, c.id as cotacao_id,
                   c.observacoes as cotacao_observacoes, c.informacao_fornecedor
            FROM cotacao_fornecedores f
            JOIN cotacoes c ON f.cotacao_id = c.id
            WHERE f.id = ?
        ''', (fornecedor_id,))
        fornecedor = cursor.fetchone()
        
        if not fornecedor:
            conn.close()
            return jsonify({'success': False, 'error': 'Fornecedor não encontrado'}), 404
        
        # Busca itens da cotação
        cursor.execute('''
            SELECT i.id, i.numero_sc, i.item_sc, i.cod_produto, i.descricao_produto as descricao, 
                   i.quantidade, i.unidade, i.observacao
            FROM cotacao_itens i
            WHERE i.cotacao_id = ?
            ORDER BY i.numero_sc, i.item_sc
        ''', (fornecedor['cotacao_id'],))
        itens = [dict(item) for item in cursor.fetchall()]
        conn.close()
        
        # Gera token único
        token_envio = gerar_token_json()
        
        # Monta estrutura do JSON
        dados_cotacao = {
            'versao': '1.0',
            'tipo': 'SOLICITACAO_COTACAO',
            'token': token_envio,
            'data_geracao': datetime.now().isoformat(),
            
            'cotacao': {
                'codigo': fornecedor['cotacao_codigo'],
                'observacoes': fornecedor['cotacao_observacoes'] or '',
                'informacao_fornecedor': fornecedor['informacao_fornecedor'] or ''
            },
            
            'fornecedor': {
                'id_interno': fornecedor['id'],
                'nome': fornecedor['nome_fornecedor'],
                'email': fornecedor['email_fornecedor'] or '',
                'telefone': fornecedor['telefone_fornecedor'] or ''
            },
            
            'itens': [
                {
                    'id': item['id'],
                    'numero_sc': item['numero_sc'],
                    'item_sc': item['item_sc'],
                    'codigo_produto': item['cod_produto'] or '',
                    'descricao': item['descricao'],
                    'quantidade': item['quantidade'],
                    'unidade': item['unidade'] or 'UN',
                    'observacao': item['observacao'] or '',
                    'resposta': {
                        'preco_unitario': None,
                        'prazo_entrega_dias': None,
                        'observacao': None
                    }
                }
                for item in itens
            ],
            
            'resposta_geral': {
                'frete_total': None,
                'condicao_pagamento': None,
                'observacao_geral': None,
                'data_resposta': None
            },
            
            'instrucoes': {
                'preenchimento': [
                    'Preencha o campo "preco_unitario" com o valor em reais (use ponto como separador decimal)',
                    'Preencha "prazo_entrega_dias" com o número de dias para entrega',
                    'Informe "frete_total" se houver custo de frete',
                    'Use os campos de "observacao" para informações adicionais'
                ],
                'retorno': 'Após preencher, salve o arquivo e envie de volta ao comprador'
            }
        }
        
        # Gera hash de validação
        dados_para_hash = {
            'token': token_envio,
            'cotacao_codigo': fornecedor['cotacao_codigo'],
            'fornecedor_id': fornecedor['id'],
            'itens_ids': [item['id'] for item in itens]
        }
        hash_validacao = gerar_hash_validacao(dados_para_hash)
        dados_cotacao['hash_validacao'] = hash_validacao
        
        # Salva arquivo JSON
        json_dir = os.path.join('uploads', 'json_cotacoes')
        os.makedirs(json_dir, exist_ok=True)
        
        nome_arquivo = f"cotacao_{fornecedor['cotacao_codigo']}_{fornecedor['nome_fornecedor'].replace(' ', '_')}_{token_envio[:8]}.json"
        # Sanitiza nome do arquivo
        nome_arquivo = "".join(c for c in nome_arquivo if c.isalnum() or c in ['_', '-', '.'])
        caminho_arquivo = os.path.join(json_dir, nome_arquivo)
        
        with open(caminho_arquivo, 'w', encoding='utf-8') as f:
            json.dump(dados_cotacao, f, ensure_ascii=False, indent=2)
        
        # Registra no banco
        usuario = session.get('username', 'Admin')
        db.criar_envio_json(
            cotacao_id=fornecedor['cotacao_id'],
            fornecedor_id=fornecedor_id,
            token_envio=token_envio,
            hash_validacao=hash_validacao,
            arquivo_json=caminho_arquivo,
            usuario=usuario
        )
        
        # Gera link externo
        # Nota: Em produção, este link pode ser de um domínio externo
        link_externo = url_for('pagina_cotacao_externa', token=token_envio, _external=True)
        
        # Também gera link para download do JSON
        link_download_json = url_for('api_download_json_cotacao', token=token_envio, _external=True)
        
        print(f"[COTAÇÃO EXTERNA] Link gerado para {fornecedor['nome_fornecedor']}: {link_externo}")
        
        return jsonify({
            'success': True,
            'message': 'Link de cotação externa gerado com sucesso!',
            'link_externo': link_externo,
            'link_download_json': link_download_json,
            'token': token_envio,
            'arquivo_json': nome_arquivo,
            'fornecedor': fornecedor['nome_fornecedor'],
            'cotacao': fornecedor['cotacao_codigo']
        })
        
    except Exception as e:
        print(f"[ERRO] api_gerar_link_externo: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# ANOTAÇÕES DAS SOLICITAÇÕES (Cores e Observações)
# =============================================================================

@app.route('/api/solicitacao/anotacao', methods=['POST'])
def api_salvar_anotacao():
    """API para salvar anotação (cor/observação) em uma solicitação"""
    try:
        dados = request.get_json()
        
        numero_sc = dados.get('numero_sc', '')
        item_sc = dados.get('item_sc', '')
        cor = dados.get('cor')
        observacao = dados.get('observacao')
        
        if not numero_sc:
            return jsonify({'success': False, 'error': 'Número da SC é obrigatório'}), 400
        
        db.salvar_anotacao_sc(numero_sc, item_sc, cor, observacao)
        
        return jsonify({'success': True, 'message': 'Anotação salva'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/solicitacao/anotacao', methods=['DELETE'])
def api_remover_anotacao():
    """API para remover anotação de uma solicitação"""
    try:
        dados = request.get_json()
        numero_sc = dados.get('numero_sc', '')
        item_sc = dados.get('item_sc', '')
        
        db.remover_anotacao_sc(numero_sc, item_sc)
        
        return jsonify({'success': True, 'message': 'Anotação removida'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/solicitacoes/anotacoes')
def api_obter_anotacoes():
    """API para obter todas as anotações"""
    try:
        anotacoes = db.obter_anotacoes_sc()
        return jsonify({'success': True, 'anotacoes': anotacoes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# ATRIBUIÇÕES MANUAIS DE COMPRADORES
# =============================================================================

@app.route('/api/solicitacao/atribuir_comprador', methods=['POST'])
def api_atribuir_comprador():
    """
    API para atribuir manualmente um comprador a uma ou mais solicitações.
    
    Payload esperado:
    {
        "solicitacoes": [
            {"numero_sc": "123456", "item_sc": "0001"},
            {"numero_sc": "123456", "item_sc": "0002"}
        ],
        "cod_comprador": "018",
        "nome_comprador": "Daniel Amaral",
        "observacao": "Atribuição manual devido a..."  (opcional)
    }
    """
    try:
        dados = request.get_json()
        
        solicitacoes = dados.get('solicitacoes', [])
        cod_comprador = dados.get('cod_comprador', '')
        nome_comprador = dados.get('nome_comprador', '')
        observacao = dados.get('observacao')
        usuario = session.get('username', 'Admin')
        
        if not solicitacoes:
            return jsonify({'success': False, 'error': 'Nenhuma solicitação selecionada'}), 400
        
        if not cod_comprador or not nome_comprador:
            return jsonify({'success': False, 'error': 'Comprador não informado'}), 400
        
        # Salva atribuição para cada solicitação (inclusive para "Outros")
        # Antes removia quando era "Outros", mas isso fazia voltar para o comprador do TOTVS
        for sol in solicitacoes:
            numero_sc = sol.get('numero_sc', '')
            item_sc = sol.get('item_sc', '')
            
            if numero_sc and item_sc:
                db.salvar_atribuicao_comprador(
                    numero_sc=numero_sc,
                    item_sc=item_sc,
                    cod_comprador=cod_comprador,
                    nome_comprador=nome_comprador,
                    usuario=usuario,
                    observacao=observacao
                )
        
        # IMPORTANTE: Limpar cache para refletir mudanças imediatamente
        cache.delete('solicitacoes_aberto_v3')
        print(f"[ATRIBUIÇÃO] Cache solicitacoes_aberto_v3 limpo após atribuição de {len(solicitacoes)} item(ns) para {nome_comprador}")
        
        return jsonify({
            'success': True, 
            'message': f'{len(solicitacoes)} solicitação(ões) atribuída(s) para {nome_comprador}'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/solicitacao/remover_atribuicao', methods=['POST'])
def api_remover_atribuicao():
    """API para remover atribuição manual de comprador"""
    try:
        dados = request.get_json()
        numero_sc = dados.get('numero_sc', '')
        item_sc = dados.get('item_sc', '')
        
        if not numero_sc or not item_sc:
            return jsonify({'success': False, 'error': 'SC e Item são obrigatórios'}), 400
        
        db.remover_atribuicao_comprador(numero_sc, item_sc)
        
        # Limpar cache para refletir mudanças imediatamente
        cache.delete('solicitacoes_aberto_v3')
        
        return jsonify({'success': True, 'message': 'Atribuição removida'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/solicitacoes/atribuicoes')
def api_obter_atribuicoes():
    """API para obter todas as atribuições manuais"""
    try:
        atribuicoes = db.obter_atribuicoes_compradores()
        return jsonify({'success': True, 'atribuicoes': atribuicoes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# RODADAS DE NEGOCIAÇÃO
# =============================================================================

@app.route('/api/cotacao/<int:cotacao_id>/rodada-negociacao', methods=['POST'])
def api_criar_rodada_negociacao(cotacao_id):
    """
    API para criar uma rodada de negociação para um fornecedor específico.
    Recebe array de itens com preços originais e negociados.
    """
    try:
        dados = request.get_json()
        fornecedor_id = dados.get('fornecedor_id')
        itens = dados.get('itens', [])
        observacao_geral = dados.get('observacao', '')
        desconto_global = float(dados.get('desconto_percentual', 0))  # Desconto global opcional
        
        if not fornecedor_id:
            return jsonify({'success': False, 'error': 'Fornecedor é obrigatório'}), 400
        
        if not itens:
            return jsonify({'success': False, 'error': 'Nenhum item para negociar'}), 400
        
        # Criar rodada para cada item
        rodadas_criadas = 0
        for item in itens:
            item_id = item.get('item_id')
            preco_original = float(item.get('preco_original', 0))
            preco_negociado = float(item.get('preco_negociado', 0))
            prazo_original = item.get('prazo_original')
            prazo_negociado = item.get('prazo_negociado')
            # Desconto individual ou global
            desconto = float(item.get('desconto_percentual', desconto_global))
            
            if not item_id or preco_negociado <= 0:
                continue
            
            db.criar_rodada_negociacao(
                cotacao_id=cotacao_id,
                fornecedor_id=fornecedor_id,
                item_id=item_id,
                preco_original=preco_original,
                preco_negociado=preco_negociado,
                prazo_original=prazo_original,
                prazo_negociado=prazo_negociado,
                observacao=observacao_geral,
                usuario='Admin',
                desconto_percentual=desconto
            )
            rodadas_criadas += 1
        
        return jsonify({
            'success': True,
            'message': f'Rodada de negociação criada com {rodadas_criadas} item(ns)',
            'rodadas_criadas': rodadas_criadas
        })
        
    except Exception as e:
        print(f"[ERRO] api_criar_rodada_negociacao: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/rodada/<int:rodada_id>/editar', methods=['POST'])
def api_editar_rodada_negociacao(rodada_id):
    """API para editar uma rodada de negociação existente"""
    try:
        dados = request.get_json()
        prazo_negociado = dados.get('prazo_negociado')
        observacao = dados.get('observacao')
        desconto_percentual = dados.get('desconto_percentual')
        
        # SEMPRE buscar o preço original da rodada para recalcular
        conn = db.get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT preco_unitario_original FROM cotacao_rodadas_negociacao WHERE id = ?', (rodada_id,))
        rodada = cursor.fetchone()
        conn.close()
        
        if not rodada or not rodada['preco_unitario_original']:
            return jsonify({'success': False, 'error': 'Rodada não encontrada ou sem preço original'}), 404
        
        preco_original = float(rodada['preco_unitario_original'])
        desconto = float(desconto_percentual) if desconto_percentual is not None else 0.0
        
        # SEMPRE recalcular preço com desconto (se desconto = 0, preço = original)
        preco_negociado = preco_original * (1 - desconto / 100)
        print(f"[NEGOCIAÇÃO] Rodada {rodada_id}: Original={preco_original}, Desconto={desconto}%, Negociado={preco_negociado}")
        
        db.atualizar_rodada_negociacao(
            rodada_id=rodada_id,
            preco_negociado=preco_negociado,
            prazo_negociado=int(prazo_negociado) if prazo_negociado is not None else None,
            observacao=observacao,
            desconto_percentual=desconto
        )
        
        return jsonify({
            'success': True, 
            'message': 'Rodada atualizada com sucesso',
            'preco_negociado': preco_negociado,
            'economia': preco_original - preco_negociado
        })
        
    except Exception as e:
        print(f"[ERRO] api_editar_rodada_negociacao: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/rodada/<int:rodada_id>/excluir', methods=['POST'])
def api_excluir_rodada_negociacao(rodada_id):
    """API para excluir uma rodada de negociação"""
    try:
        db.excluir_rodada_negociacao(rodada_id)
        return jsonify({'success': True, 'message': 'Rodada excluída com sucesso'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# MÓDULO: AVALIAÇÃO DE FORNECEDORES - ISO (PQ021)
# =============================================================================
# Este módulo é TOTALMENTE ISOLADO das demais funcionalidades.
# Gerencia avaliações de fornecedores para auditoria ISO.
# =============================================================================

import uuid
import hashlib
from werkzeug.utils import secure_filename

# Pasta para armazenar documentos ISO (PDFs)
UPLOAD_FOLDER_ISO = os.path.join(os.path.dirname(__file__), 'uploads', 'avaliacao_iso')
os.makedirs(UPLOAD_FOLDER_ISO, exist_ok=True)

# Extensões permitidas para upload
ALLOWED_EXTENSIONS_ISO = {'pdf'}

def allowed_file_iso(filename):
    """Verifica se a extensão do arquivo é permitida"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_ISO


def calcular_status_avaliacao(data_vencimento):
    """
    Calcula o status da avaliação baseado na data de vencimento.
    
    Retorna:
        - 'valido': Data > 30 dias no futuro (verde)
        - 'proximo': Data entre hoje e 30 dias no futuro (amarelo)
        - 'vencido': Data no passado (vermelho)
        - 'indefinido': Sem data de vencimento
    """
    if not data_vencimento:
        return 'indefinido'
    
    try:
        if isinstance(data_vencimento, str):
            data_venc = datetime.strptime(data_vencimento, '%Y-%m-%d').date()
        else:
            data_venc = data_vencimento
        
        hoje = datetime.now().date()
        dias_restantes = (data_venc - hoje).days
        
        if dias_restantes < 0:
            return 'vencido'
        elif dias_restantes <= 30:
            return 'proximo'
        else:
            return 'valido'
    except:
        return 'indefinido'


@app.route('/avaliacao-iso')
def avaliacao_iso():
    """Página principal da Avaliação de Fornecedores - ISO (PQ021)"""
    user = session.get('user', 'Admin')
    
    try:
        # Buscar todas as avaliações
        avaliacoes = db.listar_avaliacoes_iso()
        
        # Calcular status e enriquecer dados
        for av in avaliacoes:
            av['status'] = calcular_status_avaliacao(av.get('data_vencimento'))
            av['documentos'] = db.listar_documentos_avaliacao_iso(av['id'])
            av['total_emails'] = db.contar_emails_enviados_avaliacao(av['id'])
            ultimo_email = db.obter_ultimo_email_avaliacao(av['id'])
            av['ultimo_email'] = ultimo_email['data_envio'] if ultimo_email else None
        
        # Contar KPIs
        total = len(avaliacoes)
        validos = sum(1 for a in avaliacoes if a['status'] == 'valido')
        proximos = sum(1 for a in avaliacoes if a['status'] == 'proximo')
        vencidos = sum(1 for a in avaliacoes if a['status'] == 'vencido')
        
        kpis = {
            'total': total,
            'validos': validos,
            'proximos': proximos,
            'vencidos': vencidos
        }
        
        return render_template('avaliacao_iso.html', 
                              user=user, 
                              avaliacoes=avaliacoes,
                              kpis=kpis)
    
    except Exception as e:
        print(f"[ERRO] avaliacao_iso: {e}")
        import traceback
        traceback.print_exc()
        return render_template('avaliacao_iso.html', 
                              user=user, 
                              avaliacoes=[],
                              kpis={'total': 0, 'validos': 0, 'proximos': 0, 'vencidos': 0},
                              erro=str(e))


@app.route('/api/avaliacao-iso/criar', methods=['POST'])
def api_criar_avaliacao_iso():
    """API para criar uma nova avaliação ISO"""
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Não autenticado'}), 401
    
    try:
        dados = request.get_json()
        
        cod_fornecedor = dados.get('cod_fornecedor', '').strip()
        nome_fornecedor = dados.get('nome_fornecedor', '').strip()
        
        if not cod_fornecedor or not nome_fornecedor:
            return jsonify({'success': False, 'error': 'Código e nome do fornecedor são obrigatórios'})
        
        # Verificar se já existe
        existente = db.obter_avaliacao_iso_por_fornecedor(cod_fornecedor)
        if existente:
            return jsonify({'success': False, 'error': 'Fornecedor já cadastrado na avaliação ISO'})
        
        avaliacao_id = db.criar_avaliacao_iso(
            cod_fornecedor=cod_fornecedor,
            nome_fornecedor=nome_fornecedor,
            email_fornecedor=dados.get('email_fornecedor', '').strip() or None,
            data_ultima_avaliacao=dados.get('data_ultima_avaliacao') or None,
            data_vencimento=dados.get('data_vencimento') or None,
            possui_iso=dados.get('possui_iso', 'Nao'),
            nota=float(dados.get('nota')) if dados.get('nota') else None,
            observacao=dados.get('observacao', '').strip() or None,
            usuario=session['user']
        )
        
        if avaliacao_id:
            return jsonify({'success': True, 'id': avaliacao_id, 'message': 'Avaliação criada com sucesso'})
        else:
            return jsonify({'success': False, 'error': 'Erro ao criar avaliação - fornecedor pode já existir'})
    
    except Exception as e:
        print(f"[ERRO] api_criar_avaliacao_iso: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/<int:avaliacao_id>/atualizar', methods=['POST'])
def api_atualizar_avaliacao_iso(avaliacao_id):
    """API para atualizar uma avaliação ISO"""
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Não autenticado'}), 401
    
    try:
        dados = request.get_json()
        
        # Campos que podem ser atualizados
        campos = {}
        
        if 'nome_fornecedor' in dados:
            campos['nome_fornecedor'] = dados['nome_fornecedor'].strip()
        if 'email_fornecedor' in dados:
            campos['email_fornecedor'] = dados['email_fornecedor'].strip() or None
        if 'data_ultima_avaliacao' in dados:
            campos['data_ultima_avaliacao'] = dados['data_ultima_avaliacao'] or None
        if 'data_vencimento' in dados:
            campos['data_vencimento'] = dados['data_vencimento'] or None
        if 'possui_iso' in dados:
            campos['possui_iso'] = dados['possui_iso']
        if 'nota' in dados:
            campos['nota'] = float(dados['nota']) if dados['nota'] else None
        if 'observacao' in dados:
            campos['observacao'] = dados['observacao'].strip() or None
        
        campos['atualizado_por'] = session['user']
        
        db.atualizar_avaliacao_iso(avaliacao_id, **campos)
        
        return jsonify({'success': True, 'message': 'Avaliação atualizada com sucesso'})
    
    except Exception as e:
        print(f"[ERRO] api_atualizar_avaliacao_iso: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/<int:avaliacao_id>/excluir', methods=['POST'])
def api_excluir_avaliacao_iso(avaliacao_id):
    """API para excluir uma avaliação ISO"""
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Não autenticado'}), 401
    
    try:
        # Excluir documentos físicos primeiro
        documentos = db.listar_documentos_avaliacao_iso(avaliacao_id)
        for doc in documentos:
            caminho = doc.get('caminho_arquivo')
            if caminho and os.path.exists(caminho):
                try:
                    os.remove(caminho)
                except:
                    pass
        
        # Excluir do banco (CASCADE cuida dos registros relacionados)
        db.excluir_avaliacao_iso(avaliacao_id)
        
        return jsonify({'success': True, 'message': 'Avaliação excluída com sucesso'})
    
    except Exception as e:
        print(f"[ERRO] api_excluir_avaliacao_iso: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/<int:avaliacao_id>/upload', methods=['POST'])
def api_upload_documento_iso(avaliacao_id):
    """API para upload de documento (PDF) de avaliação ISO"""
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Não autenticado'}), 401
    
    try:
        if 'arquivo' not in request.files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'})
        
        arquivo = request.files['arquivo']
        tipo_documento = request.form.get('tipo_documento', 'avaliacao')
        
        if arquivo.filename == '':
            return jsonify({'success': False, 'error': 'Nenhum arquivo selecionado'})
        
        if not allowed_file_iso(arquivo.filename):
            return jsonify({'success': False, 'error': 'Apenas arquivos PDF são permitidos'})
        
        # Verificar se avaliação existe
        avaliacao = db.obter_avaliacao_iso(avaliacao_id)
        if not avaliacao:
            return jsonify({'success': False, 'error': 'Avaliação não encontrada'})
        
        # Criar pasta do fornecedor se não existir
        pasta_fornecedor = os.path.join(UPLOAD_FOLDER_ISO, avaliacao['cod_fornecedor'])
        os.makedirs(pasta_fornecedor, exist_ok=True)
        
        # Gerar nome único para o arquivo
        nome_original = secure_filename(arquivo.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        nome_arquivo = f"{tipo_documento}_{timestamp}_{nome_original}"
        caminho_completo = os.path.join(pasta_fornecedor, nome_arquivo)
        
        # Salvar arquivo
        arquivo.save(caminho_completo)
        
        # Registrar no banco
        doc_id = db.criar_documento_avaliacao_iso(
            avaliacao_id=avaliacao_id,
            tipo_documento=tipo_documento,
            nome_original=nome_original,
            nome_arquivo=nome_arquivo,
            caminho_arquivo=caminho_completo,
            usuario=session['user']
        )
        
        return jsonify({
            'success': True, 
            'id': doc_id,
            'message': f'Documento "{nome_original}" enviado com sucesso'
        })
    
    except Exception as e:
        print(f"[ERRO] api_upload_documento_iso: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/documento/<int:documento_id>/download')
def api_download_documento_iso(documento_id):
    """API para download de documento ISO"""
    if 'user' not in session:
        return redirect(url_for('dashboard'))
    
    try:
        documento = db.obter_documento_avaliacao_iso(documento_id)
        if not documento:
            return jsonify({'success': False, 'error': 'Documento não encontrado'}), 404
        
        caminho = documento['caminho_arquivo']
        if not os.path.exists(caminho):
            return jsonify({'success': False, 'error': 'Arquivo não encontrado no servidor'}), 404
        
        return send_file(
            caminho,
            as_attachment=True,
            download_name=documento['nome_original']
        )
    
    except Exception as e:
        print(f"[ERRO] api_download_documento_iso: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/documento/<int:documento_id>/excluir', methods=['POST'])
def api_excluir_documento_iso(documento_id):
    """API para excluir documento ISO"""
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Não autenticado'}), 401
    
    try:
        documento = db.obter_documento_avaliacao_iso(documento_id)
        if not documento:
            return jsonify({'success': False, 'error': 'Documento não encontrado'})
        
        # Excluir arquivo físico
        caminho = documento['caminho_arquivo']
        if os.path.exists(caminho):
            os.remove(caminho)
        
        # Desativar no banco (soft delete)
        db.desativar_documento_avaliacao_iso(documento_id)
        
        return jsonify({'success': True, 'message': 'Documento excluído com sucesso'})
    
    except Exception as e:
        print(f"[ERRO] api_excluir_documento_iso: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/<int:avaliacao_id>/enviar-email', methods=['POST'])
def api_enviar_email_iso(avaliacao_id):
    """
    API para enviar e-mail de solicitação de atualização da avaliação ISO.
    Envia um e-mail padrão solicitando atualização da avaliação e certificado ISO.
    """
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Não autenticado'}), 401
    
    try:
        # Buscar dados da avaliação
        avaliacao = db.obter_avaliacao_iso(avaliacao_id)
        if not avaliacao:
            return jsonify({'success': False, 'error': 'Avaliação não encontrada'})
        
        email_destinatario = avaliacao.get('email_fornecedor', '').strip()
        if not email_destinatario:
            return jsonify({'success': False, 'error': 'Fornecedor não possui e-mail cadastrado'})
        
        nome_fornecedor = avaliacao.get('nome_fornecedor', 'Fornecedor')
        
        # Template do e-mail
        assunto = f"Solicitação de Atualização - Avaliação de Fornecedor (PQ021) - {nome_fornecedor}"
        
        mensagem_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
            <p>Prezados,</p>
            
            <p>Conforme nosso procedimento de qualidade <strong>PQ021 - Avaliação de Fornecedores</strong>, 
            solicitamos o envio dos seguintes documentos atualizados:</p>
            
            <ol>
                <li><strong>Formulário de Avaliação de Fornecedor</strong> - devidamente preenchido</li>
                <li><strong>Certificado ISO</strong> - caso sua empresa possua certificação vigente</li>
            </ol>
            
            <p>Pedimos a gentileza de enviar os documentos em formato <strong>PDF</strong> 
            para o e-mail do setor de compras.</p>
            
            <p>Esta solicitação faz parte do nosso processo de auditoria ISO e é fundamental 
            para a manutenção do cadastro de fornecedores qualificados.</p>
            
            <p>Caso tenham dúvidas, estamos à disposição.</p>
            
            <br>
            <p>Atenciosamente,</p>
            <p><strong>Departamento de Compras</strong><br>
            Polimáquinas</p>
        </body>
        </html>
        """
        
        mensagem_texto = f"""
Prezados,

Conforme nosso procedimento de qualidade PQ021 - Avaliação de Fornecedores, 
solicitamos o envio dos seguintes documentos atualizados:

1. Formulário de Avaliação de Fornecedor - devidamente preenchido
2. Certificado ISO - caso sua empresa possua certificação vigente

Pedimos a gentileza de enviar os documentos em formato PDF 
para o e-mail do setor de compras.

Esta solicitação faz parte do nosso processo de auditoria ISO e é fundamental 
para a manutenção do cadastro de fornecedores qualificados.

Caso tenham dúvidas, estamos à disposição.

Atenciosamente,
Departamento de Compras
Polimáquinas
        """
        
        # Enviar e-mail
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = assunto
            msg['From'] = EMAIL_REMETENTE
            msg['To'] = email_destinatario
            
            # Adicionar versões texto e HTML
            parte_texto = MIMEText(mensagem_texto, 'plain', 'utf-8')
            parte_html = MIMEText(mensagem_html, 'html', 'utf-8')
            
            msg.attach(parte_texto)
            msg.attach(parte_html)
            
            # Conectar e enviar
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(EMAIL_REMETENTE, SENHA_EMAIL)
            server.sendmail(EMAIL_REMETENTE, email_destinatario, msg.as_string())
            server.quit()
            
            # Registrar envio no banco
            db.registrar_email_avaliacao_iso(
                avaliacao_id=avaliacao_id,
                email_destinatario=email_destinatario,
                assunto=assunto,
                mensagem=mensagem_texto,
                usuario=session['user'],
                status='Enviado'
            )
            
            return jsonify({
                'success': True, 
                'message': f'E-mail enviado com sucesso para {email_destinatario}'
            })
            
        except Exception as e_email:
            # Registrar falha no banco
            db.registrar_email_avaliacao_iso(
                avaliacao_id=avaliacao_id,
                email_destinatario=email_destinatario,
                assunto=assunto,
                mensagem=mensagem_texto,
                usuario=session['user'],
                status='Erro',
                erro_msg=str(e_email)
            )
            
            print(f"[ERRO EMAIL] {e_email}")
            return jsonify({'success': False, 'error': f'Erro ao enviar e-mail: {str(e_email)}'})
    
    except Exception as e:
        print(f"[ERRO] api_enviar_email_iso: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/<int:avaliacao_id>/historico-emails')
def api_historico_emails_iso(avaliacao_id):
    """API para listar histórico de e-mails de uma avaliação"""
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Não autenticado'}), 401
    
    try:
        emails = db.listar_emails_avaliacao_iso(avaliacao_id)
        return jsonify({'success': True, 'emails': emails})
    
    except Exception as e:
        print(f"[ERRO] api_historico_emails_iso: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/<int:avaliacao_id>')
def api_obter_avaliacao_iso(avaliacao_id):
    """API para obter dados de uma avaliação ISO"""
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Não autenticado'}), 401
    
    try:
        avaliacao = db.obter_avaliacao_iso(avaliacao_id)
        if not avaliacao:
            return jsonify({'success': False, 'error': 'Avaliação não encontrada'}), 404
        
        avaliacao['status'] = calcular_status_avaliacao(avaliacao.get('data_vencimento'))
        avaliacao['documentos'] = db.listar_documentos_avaliacao_iso(avaliacao_id)
        
        return jsonify({'success': True, 'avaliacao': avaliacao})
    
    except Exception as e:
        print(f"[ERRO] api_obter_avaliacao_iso: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/importar-excel', methods=['POST'])
def api_importar_excel_iso():
    """
    API para importar fornecedores de um arquivo Excel.
    Recebe uma lista de fornecedores processados pelo JavaScript.
    """
    user = session.get('user', 'Admin')
    
    try:
        dados = request.get_json()
        fornecedores = dados.get('fornecedores', [])
        
        if not fornecedores:
            return jsonify({'success': False, 'error': 'Nenhum fornecedor para importar'})
        
        importados = 0
        ignorados = 0
        erros = 0
        
        for forn in fornecedores:
            nome = forn.get('nome_fornecedor', '').strip()
            if not nome:
                erros += 1
                continue
            
            # Gerar código único baseado no nome (simplificado)
            # Usar primeiros caracteres do nome como código
            cod_base = ''.join(c for c in nome.upper()[:6] if c.isalnum())
            if not cod_base:
                cod_base = 'FORN'
            
            # Verificar se já existe pelo nome
            existente = None
            avaliacoes_existentes = db.listar_avaliacoes_iso()
            for av in avaliacoes_existentes:
                if av['nome_fornecedor'].upper().strip() == nome.upper().strip():
                    existente = av
                    break
            
            if existente:
                ignorados += 1
                continue
            
            # Gerar código único
            cod_fornecedor = cod_base
            contador = 1
            while True:
                existe_cod = False
                for av in avaliacoes_existentes:
                    if av['cod_fornecedor'] == cod_fornecedor:
                        existe_cod = True
                        break
                if not existe_cod:
                    break
                cod_fornecedor = f"{cod_base}{contador:03d}"
                contador += 1
            
            try:
                # Criar avaliação
                db.criar_avaliacao_iso(
                    cod_fornecedor=cod_fornecedor,
                    nome_fornecedor=nome,
                    email_fornecedor=forn.get('email_fornecedor'),
                    data_ultima_avaliacao=forn.get('data_ultima_avaliacao') or None,
                    data_vencimento=forn.get('data_vencimento') or None,
                    possui_iso=forn.get('possui_iso', 'Nao'),
                    nota=forn.get('nota'),
                    observacao='Importado via Excel',
                    usuario=user
                )
                importados += 1
                
            except Exception as e_insert:
                print(f"[ERRO] Importar fornecedor {nome}: {e_insert}")
                erros += 1
        
        return jsonify({
            'success': True,
            'importados': importados,
            'ignorados': ignorados,
            'erros': erros,
            'message': f'Importação concluída: {importados} importados, {ignorados} ignorados, {erros} erros'
        })
    
    except Exception as e:
        print(f"[ERRO] api_importar_excel_iso: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/avaliacao-iso/limpar-todos', methods=['POST'])
def api_limpar_todos_iso():
    """
    API para excluir TODOS os registros de avaliação ISO.
    Usado para limpar importações incorretas.
    """
    user = session.get('user', 'Admin')
    print(f"[AVISO] Usuário {user} solicitou exclusão de TODOS os registros ISO")
    
    try:
        # Buscar todas as avaliações
        avaliacoes = db.listar_avaliacoes_iso()
        total = len(avaliacoes)
        excluidos = 0
        
        for av in avaliacoes:
            try:
                db.excluir_avaliacao_iso(av['id'])
                excluidos += 1
            except Exception as e:
                print(f"[ERRO] Excluir avaliação {av['id']}: {e}")
        
        print(f"[INFO] Exclusão em massa concluída: {excluidos}/{total} registros excluídos")
        
        return jsonify({
            'success': True,
            'excluidos': excluidos,
            'total': total
        })
    
    except Exception as e:
        print(f"[ERRO] api_limpar_todos_iso: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# COTAÇÃO EXTERNA ONLINE - INTEGRAÇÃO COM RENDER
# =============================================================================

# Configuração da aplicação externa (Render)
COTACAO_EXTERNA_URL = os.environ.get('COTACAO_EXTERNA_URL', 'https://cotacao-externa.onrender.com')
COTACAO_EXTERNA_API_KEY = os.environ.get('COTACAO_EXTERNA_API_KEY', 'chave-secreta-compartilhada-trocar-em-producao')

# Import do cliente de integração (se disponível)
try:
    from integracao_cotacao_externa import CotacaoExternaClient, formatar_itens_para_externa, importar_resposta_externa
    cotacao_externa_client = CotacaoExternaClient(COTACAO_EXTERNA_URL, COTACAO_EXTERNA_API_KEY)
    print("[INFO] Módulo de cotação externa carregado com sucesso!")
except ImportError:
    cotacao_externa_client = None
    print("[AVISO] Módulo de cotação externa não encontrado - funcionalidade desabilitada")


@app.route('/api/cotacao/fornecedor/<int:fornecedor_id>/gerar-link-render', methods=['POST'])
def api_gerar_link_render(fornecedor_id):
    """
    API para gerar link de cotação externa (Render/Internet).
    Envia os dados da cotação para a aplicação externa e retorna o link.
    """
    if not cotacao_externa_client:
        return jsonify({
            'success': False, 
            'error': 'Módulo de cotação externa não está configurado'
        }), 500
    
    try:
        # Buscar dados do fornecedor e cotação
        conn = db.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT f.*, c.id as cotacao_id, c.codigo, c.observacoes, c.informacao_fornecedor, c.data_validade
            FROM cotacao_fornecedores f
            JOIN cotacoes c ON f.cotacao_id = c.id
            WHERE f.id = ?
        ''', (fornecedor_id,))
        
        fornecedor = cursor.fetchone()
        
        if not fornecedor:
            conn.close()
            return jsonify({'success': False, 'error': 'Fornecedor não encontrado'}), 404
        
        fornecedor = dict(fornecedor)
        cotacao_id = fornecedor['cotacao_id']
        
        # Buscar itens da cotação
        cursor.execute('SELECT * FROM cotacao_itens WHERE cotacao_id = ?', (cotacao_id,))
        itens = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        if not itens:
            return jsonify({'success': False, 'error': 'Cotação sem itens'}), 400
        
        # Formatar itens para a API externa
        itens_formatados = []
        for item in itens:
            itens_formatados.append({
                'id': item['id'],
                'cod_produto': item.get('cod_produto', ''),
                'descricao': item.get('descricao_produto', ''),
                'quantidade': float(item.get('quantidade', 0)),
                'unidade': item.get('unidade', 'UN'),
                'observacao': item.get('observacao', '')
            })
        
        # Registrar na aplicação externa
        resultado = cotacao_externa_client.registrar_cotacao(
            cotacao_id=cotacao_id,
            codigo=fornecedor['codigo'],
            fornecedor_id=fornecedor_id,
            fornecedor_nome=fornecedor['nome_fornecedor'],
            itens=itens_formatados,
            fornecedor_codigo=fornecedor.get('cod_fornecedor'),
            fornecedor_email=fornecedor.get('email_fornecedor'),
            data_validade=fornecedor.get('data_validade'),
            informacao_fornecedor=fornecedor.get('informacao_fornecedor'),
            expiration_hours=72  # 3 dias
        )
        
        if resultado.get('success'):
            # Salvar o token externo no banco local (opcional, para rastreamento)
            conn = db.get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE cotacao_fornecedores 
                SET token_externo = ?, link_externo = ?, data_envio_externo = ?
                WHERE id = ?
            ''', (resultado['token'], resultado['link'], datetime.now(), fornecedor_id))
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': True,
                'link': resultado['link'],
                'token': resultado['token'],
                'expires_at': resultado['expires_at'],
                'message': 'Link externo gerado com sucesso!'
            })
        else:
            return jsonify({
                'success': False,
                'error': resultado.get('error', 'Erro ao gerar link externo')
            }), 500
            
    except Exception as e:
        print(f"[ERRO] api_gerar_link_externo: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/externa/verificar-respostas', methods=['GET'])
def api_verificar_respostas_externas():
    """
    API para verificar e importar respostas pendentes da aplicação externa.
    """
    if not cotacao_externa_client:
        return jsonify({
            'success': False, 
            'error': 'Módulo de cotação externa não está configurado'
        }), 500
    
    try:
        # Buscar respostas pendentes
        resultado = cotacao_externa_client.listar_respostas_pendentes()
        
        if not resultado.get('success'):
            return jsonify(resultado), 500
        
        return jsonify({
            'success': True,
            'total': resultado.get('total', 0),
            'respostas': resultado.get('respostas', [])
        })
        
    except Exception as e:
        print(f"[ERRO] api_verificar_respostas_externas: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/externa/importar/<token>', methods=['POST'])
def api_importar_resposta_externa(token):
    """
    API para importar uma resposta específica da aplicação externa.
    """
    if not cotacao_externa_client:
        return jsonify({
            'success': False, 
            'error': 'Módulo de cotação externa não está configurado'
        }), 500
    
    try:
        # Buscar resposta da aplicação externa
        resultado = cotacao_externa_client.obter_resposta(token)
        
        if not resultado.get('success'):
            return jsonify(resultado), 404
        
        resposta = resultado['resposta']
        
        # Importar para o banco local
        import_result = importar_resposta_externa(resposta, db)
        
        return jsonify(import_result)
        
    except Exception as e:
        print(f"[ERRO] api_importar_resposta_externa: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cotacao/externa/status', methods=['GET'])
def api_status_cotacao_externa():
    """
    API para verificar status da conexão com aplicação externa.
    """
    if not cotacao_externa_client:
        return jsonify({
            'success': False,
            'online': False,
            'message': 'Módulo de cotação externa não está configurado'
        })
    
    try:
        online = cotacao_externa_client.health_check()
        return jsonify({
            'success': True,
            'online': online,
            'url': COTACAO_EXTERNA_URL,
            'message': 'Aplicação externa online' if online else 'Aplicação externa offline'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'online': False,
            'error': str(e)
        })


if __name__ == '__main__':
    # Habilitado para acesso externo (0.0.0.0)
    app.run(host='0.0.0.0', port=5001, debug=True)