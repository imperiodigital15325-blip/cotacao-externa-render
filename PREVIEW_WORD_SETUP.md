# Configuração de Visualização Fiel de Documentos Word

## Problema

A visualização de documentos Word (.docx) requer conversão para PDF para preservar:
- Formatação completa
- Tabelas
- Imagens e logos
- Cabeçalhos e rodapés
- Layout original do documento

## Solução Recomendada: Instalar LibreOffice

### Windows

1. Baixe o LibreOffice em: https://www.libreoffice.org/download/download/
2. Execute o instalador
3. Instale com as opções padrão
4. Reinicie o servidor Flask

O sistema detectará automaticamente o LibreOffice e converterá documentos Word para PDF.

### Linux (Ubuntu/Debian)

```bash
sudo apt-get update
sudo apt-get install libreoffice-core libreoffice-writer
```

### Linux (CentOS/RHEL)

```bash
sudo yum install libreoffice-core libreoffice-writer
```

### macOS

```bash
brew install --cask libreoffice
```

## Verificação

Após instalar, teste no terminal:

```bash
# Windows (PowerShell)
& "C:\Program Files\LibreOffice\program\soffice.exe" --version

# Linux/macOS
soffice --version
```

## Comportamento do Sistema

| Cenário | Comportamento |
|---------|---------------|
| LibreOffice instalado | Converte Word → PDF e exibe no navegador |
| LibreOffice não instalado | Oferece download do arquivo original |

## Cache de Conversão

Os PDFs convertidos são armazenados em cache para evitar reconversões:
- Local: `uploads/avaliacao_iso/{cod_fornecedor}/.pdf_cache/`
- Atualizado automaticamente quando o documento original é modificado

## Alternativas (se não puder instalar LibreOffice)

### Opção 1: docx2pdf (Windows com MS Office)

Se o Microsoft Office estiver instalado:

```bash
pip install docx2pdf
```

O sistema tentará usar o MS Word para conversão.

### Opção 2: Download Direto

Se nenhum conversor estiver disponível, o sistema oferecerá download do arquivo original para visualização no Word local.

## Segurança

- O documento original **nunca** é modificado
- A conversão é apenas para visualização
- O PDF convertido fica em cache local
- Auditorias devem usar o arquivo original (.docx)
