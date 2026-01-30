/**
 * =============================================================================
 * POLLING GLOBAL - SINCRONIZAÇÃO AUTOMÁTICA DE COTAÇÕES EXTERNAS
 * =============================================================================
 * 
 * Este script roda em TODAS as páginas do sistema e monitora automaticamente
 * as respostas de cotações externas, sincronizando-as com o banco local.
 * 
 * Funcionalidades:
 * - Polling a cada 20 segundos
 * - Notificação toast quando uma resposta chega
 * - Atualização automática se estiver na página de cotação
 * - Armazenamento de notificações pendentes
 * 
 * =============================================================================
 */

(function() {
    'use strict';
    
    // =========================================================================
    // CONFIGURAÇÕES
    // =========================================================================
    
    const CONFIG = {
        POLLING_INTERVAL: 20000,  // 20 segundos
        TOAST_DURATION: 5000,     // 5 segundos
        MAX_RETRIES: 3,
        RETRY_DELAY: 5000
    };
    
    // Estado do polling
    let pollingAtivo = true;
    let pollingEmAndamento = false;
    let retryCount = 0;
    let toastContainer = null;
    
    // =========================================================================
    // INICIALIZAÇÃO
    // =========================================================================
    
    function init() {
        console.log('[POLLING GLOBAL] Inicializando sistema de sincronização automática...');
        
        // Cria container para toasts
        criarToastContainer();
        
        // Adiciona estilos CSS
        injetarEstilos();
        
        // Inicia polling após pequeno delay
        setTimeout(iniciarPolling, 2000);
        
        console.log('[POLLING GLOBAL] Sistema inicializado com sucesso');
        console.log(`[POLLING GLOBAL] Intervalo: ${CONFIG.POLLING_INTERVAL/1000}s`);
    }
    
    // =========================================================================
    // CONTAINER E ESTILOS
    // =========================================================================
    
    function criarToastContainer() {
        toastContainer = document.getElementById('toast-container-global');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container-global';
            document.body.appendChild(toastContainer);
        }
    }
    
    function injetarEstilos() {
        if (document.getElementById('polling-styles')) return;
        
        const style = document.createElement('style');
        style.id = 'polling-styles';
        style.textContent = `
            /* Container de toasts - canto inferior direito */
            #toast-container-global {
                position: fixed;
                bottom: 20px;
                right: 20px;
                z-index: 99999;
                display: flex;
                flex-direction: column-reverse;
                gap: 10px;
                pointer-events: none;
            }
            
            /* Toast individual */
            .toast-cotacao {
                min-width: 320px;
                max-width: 400px;
                background: linear-gradient(135deg, #28a745 0%, #20c997 100%);
                color: white;
                padding: 16px 20px;
                border-radius: 12px;
                box-shadow: 0 8px 32px rgba(40, 167, 69, 0.35);
                display: flex;
                align-items: flex-start;
                gap: 14px;
                transform: translateX(400px);
                opacity: 0;
                transition: all 0.4s cubic-bezier(0.68, -0.55, 0.265, 1.55);
                pointer-events: auto;
            }
            
            .toast-cotacao.show {
                transform: translateX(0);
                opacity: 1;
            }
            
            .toast-cotacao.hide {
                transform: translateX(400px);
                opacity: 0;
            }
            
            .toast-cotacao-icon {
                font-size: 24px;
                flex-shrink: 0;
                animation: pulseIcon 1s ease-in-out infinite;
            }
            
            @keyframes pulseIcon {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.15); }
            }
            
            .toast-cotacao-content {
                flex: 1;
            }
            
            .toast-cotacao-title {
                font-weight: 700;
                font-size: 14px;
                margin-bottom: 4px;
                text-shadow: 0 1px 2px rgba(0,0,0,0.2);
            }
            
            .toast-cotacao-message {
                font-size: 13px;
                opacity: 0.95;
                line-height: 1.4;
            }
            
            .toast-cotacao-message strong {
                font-weight: 600;
            }
            
            .toast-cotacao-close {
                background: rgba(255,255,255,0.2);
                border: none;
                color: white;
                width: 24px;
                height: 24px;
                border-radius: 50%;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                flex-shrink: 0;
                transition: background 0.2s;
            }
            
            .toast-cotacao-close:hover {
                background: rgba(255,255,255,0.3);
            }
            
            /* Badge de notificação no menu (opcional) */
            .notification-badge {
                position: absolute;
                top: -5px;
                right: -5px;
                background: #dc3545;
                color: white;
                font-size: 10px;
                font-weight: 700;
                min-width: 18px;
                height: 18px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                animation: badgePulse 2s ease-in-out infinite;
            }
            
            @keyframes badgePulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.1); }
            }
            
            /* Highlight de linha atualizada na tabela */
            .row-sync-updated {
                animation: highlightRow 3s ease-out;
            }
            
            @keyframes highlightRow {
                0% { background-color: rgba(40, 167, 69, 0.3); }
                100% { background-color: transparent; }
            }
        `;
        document.head.appendChild(style);
    }
    
    // =========================================================================
    // TOAST NOTIFICATIONS
    // =========================================================================
    
    function mostrarToast(fornecedorNome, cotacaoId) {
        const toastId = 'toast-' + Date.now();
        
        const toastHtml = `
            <div class="toast-cotacao" id="${toastId}">
                <div class="toast-cotacao-icon">
                    <i class="fas fa-check-circle"></i>
                </div>
                <div class="toast-cotacao-content">
                    <div class="toast-cotacao-title">✨ Nova Resposta Recebida!</div>
                    <div class="toast-cotacao-message">
                        <strong>${fornecedorNome}</strong> respondeu à cotação
                    </div>
                </div>
                <button class="toast-cotacao-close" onclick="window.fecharToastCotacao('${toastId}')">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
        
        toastContainer.insertAdjacentHTML('beforeend', toastHtml);
        
        const toast = document.getElementById(toastId);
        
        // Anima entrada
        setTimeout(() => toast.classList.add('show'), 50);
        
        // Auto-fecha após duração
        setTimeout(() => fecharToast(toastId), CONFIG.TOAST_DURATION);
        
        // Toca som de notificação (se permitido)
        tocarSomNotificacao();
    }
    
    function fecharToast(toastId) {
        const toast = document.getElementById(toastId);
        if (toast) {
            toast.classList.remove('show');
            toast.classList.add('hide');
            setTimeout(() => toast.remove(), 400);
        }
    }
    
    // Expõe função globalmente para o onclick
    window.fecharToastCotacao = fecharToast;
    
    function tocarSomNotificacao() {
        try {
            // Som de notificação simples (base64 encoded)
            const audio = new Audio('data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdJivrJBhNjVgodDbq2EcBj+a2teleSkdfpO83NlsQBgqgsXa3oFYLRtcmcbTi14yFlWKvNHJgVowGEyCuM7EdGQ5IVyNvczDhWY3IViFuM/Edmo6IVqJuczDhWo4IFeFuNDDdWw8IVqJuc3EhWs4IFiFt9DDdWw8IVqIuM3FhWs4H1eFt9DDdWw8IVqIuM3FhWs4IFiFt9DEdWw8IVqIuM3FhWs4IFiFt9DDdWw8IVqIuM3FhWs4IFiFt9DEdWw8IVqIuM3Fh2s4IFiFt9DEd2w8IVqIuM3EhWs4IFmGt9DEd2w8IVqIuM3EhWs4IFmGt9DEd2w8IVqIuM3EhWs4');
            audio.volume = 0.3;
            audio.play().catch(() => {});
        } catch (e) {}
    }
    
    // =========================================================================
    // POLLING PRINCIPAL
    // =========================================================================
    
    function iniciarPolling() {
        console.log('[POLLING GLOBAL] Iniciando verificações periódicas...');
        
        // Executa imediatamente
        verificarRespostas();
        
        // Agenda verificações periódicas
        setInterval(verificarRespostas, CONFIG.POLLING_INTERVAL);
    }
    
    async function verificarRespostas() {
        if (!pollingAtivo || pollingEmAndamento) return;
        
        pollingEmAndamento = true;
        
        try {
            // Consulta endpoint de polling
            const response = await fetch('/api/cotacoes-externas/polling');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            
            if (!data.success) {
                console.warn('[POLLING GLOBAL] Erro na resposta:', data.error);
                return;
            }
            
            const respostas = data.respostas || [];
            
            if (respostas.length === 0) {
                retryCount = 0; // Reset retry counter on success
                return;
            }
            
            console.log(`[POLLING GLOBAL] ${respostas.length} resposta(s) pendente(s)`);
            
            // Processa cada resposta
            for (const resposta of respostas) {
                await sincronizarResposta(resposta);
            }
            
            retryCount = 0;
            
        } catch (error) {
            console.error('[POLLING GLOBAL] Erro:', error);
            retryCount++;
            
            if (retryCount >= CONFIG.MAX_RETRIES) {
                console.warn('[POLLING GLOBAL] Máximo de tentativas atingido, aguardando próximo ciclo');
            }
        } finally {
            pollingEmAndamento = false;
        }
    }
    
    async function sincronizarResposta(resposta) {
        try {
            console.log(`[POLLING GLOBAL] Sincronizando resposta de ${resposta.fornecedor_nome}...`);
            
            const syncResponse = await fetch('/api/cotacoes-externas/sincronizar-render', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(resposta)
            });
            
            const syncData = await syncResponse.json();
            
            if (syncData.success) {
                console.log(`[POLLING GLOBAL] ✓ Resposta sincronizada: ${resposta.fornecedor_nome}`);
                
                // Mostra notificação toast
                mostrarToast(resposta.fornecedor_nome, resposta.cotacao_id);
                
                // Verifica se estamos na página da cotação e atualiza
                atualizarPaginaSeNecessario(resposta);
                
            } else {
                console.error('[POLLING GLOBAL] Erro ao sincronizar:', syncData.error);
            }
            
        } catch (error) {
            console.error('[POLLING GLOBAL] Erro ao sincronizar resposta:', error);
        }
    }
    
    // =========================================================================
    // ATUALIZAÇÃO DE PÁGINA
    // =========================================================================
    
    function atualizarPaginaSeNecessario(resposta) {
        // Verifica se estamos na página de detalhes de cotação
        const urlAtual = window.location.pathname;
        
        // Se estiver na página da cotação específica
        if (urlAtual.includes('/cotacao/') && urlAtual.includes(resposta.cotacao_id)) {
            // Atualiza a linha do fornecedor se existir
            const row = document.getElementById(`row-forn-${resposta.fornecedor_id}`);
            if (row) {
                // Atualiza badge de status
                const statusCell = row.querySelector('td:nth-child(3)');
                if (statusCell) {
                    statusCell.innerHTML = '<span class="badge bg-success"><i class="fas fa-check me-1"></i>Respondido</span>';
                }
                
                // Adiciona highlight
                row.classList.add('row-sync-updated');
                
                // Agenda reload para atualizar comparativo
                setTimeout(() => {
                    if (confirm('Nova resposta recebida! Deseja recarregar a página para ver os dados atualizados?')) {
                        location.reload();
                    }
                }, 1500);
            } else {
                // Recarrega a página se não encontrar a linha
                setTimeout(() => location.reload(), 2000);
            }
        }
        
        // Se estiver na lista de cotações, pode atualizar contadores
        if (urlAtual.includes('/cotacoes')) {
            // Poderia atualizar um contador de "respostas pendentes" se existisse
        }
    }
    
    // =========================================================================
    // CONTROLES PÚBLICOS
    // =========================================================================
    
    window.PollingCotacoes = {
        pausar: function() {
            pollingAtivo = false;
            console.log('[POLLING GLOBAL] Polling pausado');
        },
        retomar: function() {
            pollingAtivo = true;
            console.log('[POLLING GLOBAL] Polling retomado');
        },
        verificarAgora: function() {
            verificarRespostas();
        },
        status: function() {
            return {
                ativo: pollingAtivo,
                emAndamento: pollingEmAndamento,
                intervalo: CONFIG.POLLING_INTERVAL
            };
        }
    };
    
    // =========================================================================
    // INICIALIZAÇÃO AUTOMÁTICA
    // =========================================================================
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    
})();
