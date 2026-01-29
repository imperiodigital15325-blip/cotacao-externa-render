"""
AUDITORIA COMPLETA - VARIAÇÃO DE PREÇO
Script para validar a lógica e os cálculos do dashboard
"""
import pyodbc
import pandas as pd
import numpy as np

def main():
    server = r'172.16.45.117\TOTVS'
    database = 'TOTVSDB'
    username = 'excel'
    password = 'Db_Polimaquinas'
    conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'

    try:
        conn = pyodbc.connect(conn_str, timeout=60)
        print('=' * 70)
        print('AUDITORIA COMPLETA - VARIAÇÃO DE PREÇO')
        print('=' * 70)
        print()
        
        # 1. Contagens básicas
        q1 = '''SELECT COUNT(*) AS Total FROM SD1010 NF WITH (NOLOCK)
        LEFT JOIN SB1010 PROD WITH (NOLOCK) ON NF.D1_COD = PROD.B1_COD AND PROD.D_E_L_E_T_ = ''
        WHERE NF.D_E_L_E_T_ <> '*' AND NF.D1_DTDIGIT >= '20240101'
        AND NF.D1_QUANT > 0 AND NF.D1_TOTAL > 0 AND NF.D1_VUNIT > 0 AND NF.D1_TIPO = 'N'
        AND LTRIM(RTRIM(ISNULL(NF.D1_PEDIDO, ''))) <> ''
        AND PROD.B1_TIPO NOT IN ('BN', 'SV', 'PR')'''
        r1 = pd.read_sql(q1, conn)
        total_nfs = r1.iloc[0,0]
        print(f'[SQL BRUTO] Total NFs válidas (2024+): {total_nfs:,}')
        
        # 2. Produtos únicos
        q2 = '''SELECT COUNT(DISTINCT NF.D1_COD) AS Total FROM SD1010 NF WITH (NOLOCK)
        LEFT JOIN SB1010 PROD WITH (NOLOCK) ON NF.D1_COD = PROD.B1_COD AND PROD.D_E_L_E_T_ = ''
        WHERE NF.D_E_L_E_T_ <> '*' AND NF.D1_DTDIGIT >= '20240101'
        AND NF.D1_QUANT > 0 AND NF.D1_TOTAL > 0 AND NF.D1_VUNIT > 0 AND NF.D1_TIPO = 'N'
        AND LTRIM(RTRIM(ISNULL(NF.D1_PEDIDO, ''))) <> ''
        AND PROD.B1_TIPO NOT IN ('BN', 'SV', 'PR')'''
        r2 = pd.read_sql(q2, conn)
        total_produtos = r2.iloc[0,0]
        print(f'[SQL BRUTO] Produtos únicos: {total_produtos:,}')
        
        # 3. Valor total
        q3 = '''SELECT SUM(NF.D1_TOTAL) AS Total FROM SD1010 NF WITH (NOLOCK)
        LEFT JOIN SB1010 PROD WITH (NOLOCK) ON NF.D1_COD = PROD.B1_COD AND PROD.D_E_L_E_T_ = ''
        WHERE NF.D_E_L_E_T_ <> '*' AND NF.D1_DTDIGIT >= '20240101'
        AND NF.D1_QUANT > 0 AND NF.D1_TOTAL > 0 AND NF.D1_VUNIT > 0 AND NF.D1_TIPO = 'N'
        AND LTRIM(RTRIM(ISNULL(NF.D1_PEDIDO, ''))) <> ''
        AND PROD.B1_TIPO NOT IN ('BN', 'SV', 'PR')'''
        r3 = pd.read_sql(q3, conn)
        valor_total = r3.iloc[0,0]
        print(f'[SQL BRUTO] Valor Total NFs: R$ {valor_total:,.2f}')
        
        conn.close()
        print()
        print('=' * 70)
        print('AUDITORIA CONCLUÍDA')
        print('=' * 70)
        
    except Exception as e:
        print(f'Erro: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
