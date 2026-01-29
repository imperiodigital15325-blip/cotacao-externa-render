#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Script de teste para verificar o banco de dados de cotações"""

import database as db

print("=" * 60)
print("TESTE DO BANCO DE DADOS DE COTAÇÕES")
print("=" * 60)

# Testar cotação existente
cotacao = db.obter_cotacao(11)

if cotacao:
    print(f"\n[COTAÇÃO ID 11]")
    print(f"  Código: {cotacao['codigo']}")
    print(f"  Status: {cotacao['status']}")
    print(f"  Total Itens: {len(cotacao['itens'])}")
    print(f"  Total Fornecedores: {len(cotacao['fornecedores'])}")
    print(f"  Total Respostas: {len(cotacao['respostas'])}")
    
    print(f"\n[FORNECEDORES]")
    for f in cotacao['fornecedores']:
        print(f"  - {f['nome_fornecedor']} | Status: {f['status']} | Token: {f['token_acesso'][:20]}...")
    
    print(f"\n[RESPOSTAS]")
    for r in cotacao['respostas']:
        print(f"  - Item {r['item_id']} | {r['nome_fornecedor']} | R$ {r['preco_unitario']}")
else:
    print("Cotação 11 não encontrada!")

# Testar busca de fornecedor por token
print("\n" + "=" * 60)
print("TESTE DE BUSCA POR TOKEN")
print("=" * 60)

if cotacao and cotacao['fornecedores']:
    token = cotacao['fornecedores'][0]['token_acesso']
    print(f"Token testado: {token[:30]}...")
    
    dados_token = db.obter_cotacao_por_token(token)
    if dados_token:
        print(f"  Cotação encontrada: {dados_token['codigo']}")
        print(f"  Fornecedor: {dados_token['nome_fornecedor']}")
        print(f"  Itens: {len(dados_token['itens'])}")
        print(f"  Já respondeu: {dados_token['ja_respondeu']}")
    else:
        print("  ERRO: Token não encontrado!")

print("\n" + "=" * 60)
print("TESTE CONCLUÍDO")
print("=" * 60)
