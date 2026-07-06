#!/usr/bin/env bash
#
# reorganize.sh — Reorganiza o projeto Arrisca para a estrutura apps/ + core/.
#
# - Cria backup do projeto antes de mover qualquer coisa.
# - Idempotente: pode ser rodado mais de uma vez sem quebrar.
# - Cria placeholders TODO para arquivos esperados pela arquitetura
#   mas que ainda não existem no projeto (celery_app.py, permissions.py,
#   retrieval.py).
#
# Uso:
#   chmod +x reorganize.sh
#   ./reorganize.sh
#

set -euo pipefail

# ============================================================
# 0. Sanity check — rodar a partir da raiz do projeto
# ============================================================
if [ ! -f "docker-compose.yml" ] || [ ! -f "pyproject.toml" ]; then
    echo "❌ Erro: rode este script da raiz do projeto"
    echo "   (onde estão docker-compose.yml e pyproject.toml)"
    exit 1
fi

# ============================================================
# 1. Backup
# ============================================================
BACKUP_DIR="../files10_backup_$(date +%Y%m%d_%H%M%S)"
echo "📦 Criando backup em $BACKUP_DIR..."
cp -r . "$BACKUP_DIR"
echo "✅ Backup criado em $BACKUP_DIR"
echo ""

# ============================================================
# 2. Corrigir ownership de pastas criadas pelo Docker como root
# ============================================================
echo "🔧 Corrigindo ownership de pastas..."
if [ -d "apps" ] && [ "$(stat -c '%U' apps)" = "root" ]; then
    sudo chown -R "$USER:$USER" apps/
fi
if [ -d "core" ] && [ "$(stat -c '%U' core)" = "root" ]; then
    sudo chown -R "$USER:$USER" core/
fi
echo "✅ Ownership ok"
echo ""

# ============================================================
# 3. Criar estrutura de diretórios
# ============================================================
echo "📁 Criando diretórios..."
mkdir -p apps/api/routes
mkdir -p apps/worker/ingestion
mkdir -p core/services
mkdir -p migrations

# ============================================================
# 4. Criar __init__.py em todos os pacotes Python
# ============================================================
echo "🐍 Criando arquivos __init__.py..."
touch apps/__init__.py
touch apps/api/__init__.py
touch apps/api/routes/__init__.py
touch apps/worker/__init__.py
touch apps/worker/ingestion/__init__.py
touch core/__init__.py
touch core/services/__init__.py

# ============================================================
# 5. Helper: move arquivo só se existir, sem sobrescrever destino
# ============================================================
move_if_exists() {
    local src="$1"
    local dst="$2"

    if [ -f "$src" ]; then
        if [ -f "$dst" ]; then
            echo "  ⚠  $dst já existe — pulando $src"
        else
            mv "$src" "$dst"
            echo "  ✓  $src → $dst"
        fi
    elif [ -f "$dst" ]; then
        echo "  ↻  $src já movido anteriormente (existe em $dst)"
    else
        echo "  ✗  $src não encontrado"
    fi
}

# ============================================================
# 6. Mover arquivos da API (raiz do FastAPI + deps)
# ============================================================
echo ""
echo "📦 Movendo arquivos para apps/api/..."
move_if_exists main.py apps/api/main.py
move_if_exists deps.py apps/api/deps.py

# ============================================================
# 7. Mover rotas da API
# ============================================================
echo ""
echo "📦 Movendo arquivos para apps/api/routes/..."
move_if_exists admin.py         apps/api/routes/admin.py
move_if_exists area_agent.py    apps/api/routes/area_agent.py
move_if_exists audit.py         apps/api/routes/audit.py
move_if_exists conversations.py apps/api/routes/conversations.py
move_if_exists documents.py     apps/api/routes/documents.py
move_if_exists me.py            apps/api/routes/me.py
move_if_exists orchestrator.py  apps/api/routes/orchestrator.py

# ============================================================
# 8. Mover módulos compartilhados para core/
# ============================================================
echo ""
echo "📦 Movendo arquivos para core/..."
move_if_exists pool.py          core/pool.py
move_if_exists supabase_auth.py core/supabase_auth.py
move_if_exists user_context.py  core/user_context.py

# ============================================================
# 9. Mover SQL para migrations/
# ============================================================
echo ""
echo "📦 Movendo migrations/..."
move_if_exists 001_initial_schema.sql migrations/001_initial_schema.sql

# ============================================================
# 10. Criar placeholders para arquivos esperados pela arquitetura
# ============================================================
create_placeholder() {
    local file="$1"
    local module="$2"
    local description="$3"

    if [ ! -f "$file" ]; then
        cat > "$file" <<EOF
"""$module — $description

TODO: implementar este módulo.

Este arquivo é referenciado pela arquitetura do projeto mas ainda
não foi criado. Substitua este placeholder pela implementação real.
"""
EOF
        echo "  ⚠  placeholder criado: $file"
    fi
}

echo ""
echo "📝 Criando placeholders para arquivos faltantes..."
create_placeholder \
    "apps/worker/ingestion/celery_app.py" \
    "celery_app" \
    "Celery app + tasks de ingestão (download, extração, chunking, embeddings)"

create_placeholder \
    "core/services/permissions.py" \
    "permissions" \
    "Single source of truth de RBAC filtering (filtros SQL por role + sensitivity)"

create_placeholder \
    "core/services/retrieval.py" \
    "retrieval" \
    "Retrieval: vector search HNSW + permission filtering combinados em um único query"

# ============================================================
# 11. Mostrar resultado
# ============================================================
echo ""
echo "🌳 Estrutura final:"
if command -v tree >/dev/null 2>&1; then
    tree -L 4 -I '__pycache__|*.pyc' apps core migrations
else
    find apps core migrations -type f 2>/dev/null | sort | sed 's|^|  |'
fi

# ============================================================
# 12. Próximos passos
# ============================================================
cat <<'EOF'

✅ Reorganização concluída!

⚠️  PRÓXIMOS PASSOS MANUAIS:

  1. Revisar imports dentro dos arquivos Python movidos.
     Antes:  from pool import get_pool
     Depois: from core.pool import get_pool

     Para encontrar imports que provavelmente quebraram:
        grep -rn "^from \(pool\|supabase_auth\|user_context\|deps\) " apps/ core/
        grep -rn "^import \(pool\|supabase_auth\|user_context\|deps\)$"  apps/ core/

  2. Implementar os placeholders criados (celery_app.py, permissions.py,
     retrieval.py).

  3. Resetar o volume do postgres para o initdb rodar do zero:
        docker compose down -v
        docker compose up

  4. Verificar que as tabelas foram criadas:
        docker compose exec postgres psql -U postgres -d arrisca -c "\dt"

EOF