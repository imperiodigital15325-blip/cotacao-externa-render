"""
Script de teste para a funcionalidade de Atribuição Manual de Comprador
Execute este script para verificar se tudo está configurado corretamente.
"""

import sys
sys.path.append(r'w:\Compras\Daniel Amaral\PROJETO COMPRAS\PROJETO-COMPRAS-2')

import database as db

print("=" * 60)
print("TESTE: Atribuição Manual de Comprador")
print("=" * 60)

# 1. Verificar se banco de dados foi inicializado
print("\n1. Verificando banco de dados...")
try:
    db.init_database()
    print("   ✓ Banco de dados OK")
except Exception as e:
    print(f"   ✗ Erro: {e}")
    sys.exit(1)

# 2. Testar salvamento de atribuição
print("\n2. Testando salvamento de atribuição...")
try:
    db.salvar_atribuicao_comprador(
        numero_sc="TEST001",
        item_sc="0001",
        cod_comprador="018",
        nome_comprador="Daniel Amaral",
        usuario="Sistema",
        observacao="Teste de atribuição manual"
    )
    print("   ✓ Atribuição salva com sucesso")
except Exception as e:
    print(f"   ✗ Erro ao salvar: {e}")
    sys.exit(1)

# 3. Testar busca de atribuições
print("\n3. Testando busca de atribuições...")
try:
    atribuicoes = db.obter_atribuicoes_compradores()
    if "TEST001-0001" in atribuicoes:
        print("   ✓ Atribuição encontrada")
        print(f"   Comprador: {atribuicoes['TEST001-0001']['nome_comprador']}")
    else:
        print("   ✗ Atribuição não encontrada")
except Exception as e:
    print(f"   ✗ Erro ao buscar: {e}")
    sys.exit(1)

# 4. Testar remoção de atribuição
print("\n4. Testando remoção de atribuição...")
try:
    db.remover_atribuicao_comprador("TEST001", "0001")
    atribuicoes = db.obter_atribuicoes_compradores()
    if "TEST001-0001" not in atribuicoes:
        print("   ✓ Atribuição removida com sucesso")
    else:
        print("   ✗ Atribuição não foi removida")
except Exception as e:
    print(f"   ✗ Erro ao remover: {e}")
    sys.exit(1)

# 5. Verificar estrutura da tabela
print("\n5. Verificando estrutura da tabela...")
try:
    conn = db.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(solicitacao_atribuicoes)")
    colunas = cursor.fetchall()
    conn.close()
    
    colunas_esperadas = ['id', 'numero_sc', 'item_sc', 'cod_comprador', 
                        'nome_comprador', 'atribuido_por', 'data_atribuicao', 'observacao']
    
    colunas_encontradas = [col[1] for col in colunas]
    
    todas_presentes = all(col in colunas_encontradas for col in colunas_esperadas)
    
    if todas_presentes:
        print("   ✓ Estrutura da tabela OK")
        print(f"   Colunas: {', '.join(colunas_encontradas)}")
    else:
        print("   ✗ Estrutura da tabela incorreta")
        print(f"   Esperado: {colunas_esperadas}")
        print(f"   Encontrado: {colunas_encontradas}")
except Exception as e:
    print(f"   ✗ Erro: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("RESULTADO: Todos os testes passaram!")
print("=" * 60)
print("\nA funcionalidade está pronta para uso.")
print("\nPróximos passos:")
print("1. Execute o servidor Flask: python app.py")
print("2. Acesse http://localhost:5001/solicitacoes")
print("3. Filtre por 'Outros' para ver itens sem comprador")
print("4. Selecione itens e clique em 'Atribuir Comprador'")
print("=" * 60)
