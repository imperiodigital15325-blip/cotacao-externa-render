# =============================================================================
# RODADAS DE NEGOCIAÇÃO - Adicionar ao app.py antes de if __name__ == '__main__':
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
                usuario='Admin'
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
        preco_negociado = dados.get('preco_negociado')
        prazo_negociado = dados.get('prazo_negociado')
        observacao = dados.get('observacao')
        
        db.atualizar_rodada_negociacao(
            rodada_id=rodada_id,
            preco_negociado=float(preco_negociado) if preco_negociado is not None else None,
            prazo_negociado=int(prazo_negociado) if prazo_negociado is not None else None,
            observacao=observacao
        )
        
        return jsonify({'success': True, 'message': 'Rodada atualizada com sucesso'})
        
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
