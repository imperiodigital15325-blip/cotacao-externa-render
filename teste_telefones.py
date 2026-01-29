import pyodbc
import pandas as pd

conn_str = 'DRIVER={SQL Server};SERVER=SRV-BNU-SQL;DATABASE=Protheus_Producao;UID=sa;PWD=Administrad0r;'
conn = pyodbc.connect(conn_str)

query = '''
SELECT TOP 30 
    RTRIM(A2_NOME) AS Fornecedor,
    RTRIM(A2_TEL) AS Telefone,
    RTRIM(A2_FAX) AS Fax
FROM SA2010 
WHERE D_E_L_E_T_ = '' 
AND (A2_TEL IS NOT NULL AND A2_TEL <> '' OR A2_FAX IS NOT NULL AND A2_FAX <> '')
ORDER BY A2_NOME
'''

df = pd.read_sql(query, conn)
conn.close()

def normalizar_telefone(tel):
    """Testa normalizacao"""
    if not tel: return ('', 'VAZIO')
    # Remove tudo que nao e digito
    numero = ''.join(filter(str.isdigit, str(tel)))
    if not numero: return ('', 'SEM DIGITOS')
    
    original_len = len(numero)
    
    # Remove codigo do pais se existir (55 no inicio)
    if numero.startswith('55') and len(numero) >= 12:
        numero = numero[2:]
    
    # Se tiver 10 digitos (DDD + 8 digitos antigo), adiciona o 9
    if len(numero) == 10:
        numero = numero[:2] + '9' + numero[2:]
    
    # Se tiver 11 digitos (DDD + 9 digitos), esta correto
    if len(numero) != 11:
        return ('', f'TAM INVALIDO: {original_len} dig -> {len(numero)} dig')
    
    return (numero, 'OK')

print('=== AMOSTRA DE TELEFONES DO BANCO ===\n')
for _, row in df.iterrows():
    tel = row['Telefone'] or ''
    fax = row['Fax'] or ''
    
    tel_limpo, tel_status = normalizar_telefone(tel)
    fax_limpo, fax_status = normalizar_telefone(fax)
    
    print(f"Fornecedor: {row['Fornecedor'][:35]}")
    print(f"  Tel: '{tel}' -> '{tel_limpo}' [{tel_status}]")
    print(f"  Fax: '{fax}' -> '{fax_limpo}' [{fax_status}]")
    print()
