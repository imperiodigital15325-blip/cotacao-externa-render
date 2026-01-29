# 🚀 DEPLOY GUIDE - RENDER

## ✅ Validações Concluídas

- [x] `app.py` exporta corretamente `app` (linha 17)
- [x] `gunicorn` incluído no `requirements.txt`
- [x] `.gitignore` configurado corretamente
- [x] `Procfile` criado
- [x] `runtime.txt` especifica Python 3.11

---

## 📋 Configuração no Render

### **Build Settings**

| Campo | Valor |
|-------|-------|
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app` |
| **Python Version** | `3.11.0` (auto-detectado via `runtime.txt`) |

### **Environment Variables**

⚠️ **IMPORTANTE**: Configure estas variáveis no Render:

```bash
# Segurança
SECRET_KEY=sua-chave-super-secreta-minimo-32-caracteres

# Banco de dados (se usar externo no futuro)
# DATABASE_URL=postgresql://user:pass@host:5432/db

# Aplicação
FLASK_ENV=production
```

---

## ⚙️ Observações Importantes

### **SQLite no Render**
- ⚠️ O Render usa **sistema de arquivos efêmero**
- Bancos `.db` serão **perdidos a cada deploy**
- **Solução**: Migrar para **PostgreSQL** (Render oferece free tier)

### **Uploads**
- Uploads também são **efêmeros**
- **Solução**: Usar **AWS S3** ou **Cloudinary**

### **Porta**
- Render define automaticamente via `$PORT`
- Gunicorn detecta automaticamente
- Não precisa alterar `app.py`

---

## 📁 Arquivos Criados

```
✅ .gitignore          # Ignora arquivos desnecessários
✅ Procfile            # Comando de start
✅ runtime.txt         # Versão Python
✅ requirements.txt    # Dependências (com gunicorn)
✅ uploads/.gitkeep    # Mantém pasta no Git
✅ DEPLOY_GUIDE.md     # Este arquivo
```

---

## 🛠️ Comandos Git Necessários

```bash
# 1. Remover arquivos já rastreados mas que agora estão no .gitignore
git rm -r --cached __pycache__/
git rm -r --cached .venv/
git rm --cached *.db
git rm --cached users.json
git rm --cached anotacoes_*.json
git rm -r --cached uploads/*

# 2. Adicionar novos arquivos
git add .gitignore Procfile runtime.txt requirements.txt uploads/.gitkeep

# 3. Commit
git commit -m "chore: configurar projeto para deploy no Render"

# 4. Push
git push origin main
```

---

## 🎯 Próximos Passos

1. **Executar comandos Git** acima
2. **Criar Web Service no Render**:
   - Dashboard → New → Web Service
   - Conectar repositório GitHub
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app`
3. **Configurar Environment Variables** (SECRET_KEY)
4. **Deploy automático** será iniciado

---

## ⚠️ Limitações Conhecidas

- **SQLite será resetado** a cada deploy → migrar para PostgreSQL
- **Uploads serão perdidos** → integrar S3 ou Cloudinary
- **pyodbc pode falhar** no Render (dependência Windows) → remover se não usar SQL Server

---

## 🔧 Melhorias Futuras (Opcional)

- [ ] Migrar para PostgreSQL
- [ ] Implementar upload S3
- [ ] Remover dependências desnecessárias (PyInstaller, pyodbc)
- [ ] Adicionar health check endpoint
- [ ] Configurar logs estruturados

