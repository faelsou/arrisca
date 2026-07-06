# 05 — Páginas e Fluxos

Especificação página a página. Cada seção define: rota, acesso, dados, estados e critérios de aceite.

---

## `/login`

**Acesso:** anônimo. **Dados:** Supabase Auth direto.

Tela centrada: logo do tenant, email, senha, "Entrar", link "Esqueci minha senha". Sem cadastro. Erro de credencial: mensagem única "Email ou senha incorretos" (não distinguir qual, por segurança). Loading no botão, nunca dupla submissão.

**Aceite:** login válido → `/chat` (ou `?next=`); sessão viva em `/login` → redirect `/chat`; funciona 100% por teclado.

---

## `/chat` e `/chat/[conversationId]`

Ver documento 04. **Aceite adicional:** primeira resposta começa a renderizar < 2s após envio em rede normal (percepção via evento `sources` mostrando "Consultando documentos…" antes do primeiro `delta`).

---

## `/historico`

**Acesso:** autenticado. **Dados:** `GET /conversations` (infinite).

Lista com busca client-side sobre títulos carregados, ordenada por atividade recente. Ações por item: abrir, renomear, excluir. Empty state: "Nenhuma conversa ainda" + botão "Nova conversa".

---

## `/transporte`

**Acesso:** `permissions.can_use_transport_agent`. **Dados:** mesmo endpoint de chat.

Duas entradas no topo em `Tabs`: "Pergunta livre" (chat) e "Cálculo estruturado" (formulário origem/destino/veículo/eixos com autocomplete de cidades — dataset estático IBGE no MVP). Ambas convergem no mesmo stream. Resultados recentes de cálculo listados abaixo (últimas conversas com `intent=transport`).

**Aceite:** manager de Transporte vê a página; employee de Design recebe 404; card de custo formata moeda pt-BR e soma bate com os componentes.

---

## `/conta`

**Acesso:** autenticado.

Nome, email (somente leitura), papel e área exibidos com `AreaBadge` (somente leitura — "Para alterações, fale com o administrador"), troca de senha (Supabase `updateUser`), toggle de tema, botão sair.

---

## `(admin)/admin/usuarios`

**Acesso:** `can_admin_users` para editar; `executive` somente leitura.

Tabela: nome, email, `AreaBadge`, papel (badge), status (ativo/desativado), último acesso. Ações (`super_admin`): convidar (dialog: email, nome, área, papel → `POST /admin/users/invite`), editar papel/área, desativar/reativar (confirmação). Filtros por área e papel; busca por nome/email.

**Regras:** `super_admin` não pode se auto-rebaixar nem desativar a si mesmo (UI bloqueia; API garante). Papel `super_admin` só atribuível por outro `super_admin`.

---

## `(admin)/admin/documentos`

**Acesso:** `can_admin_documents`.

**Dados:** `GET /admin/documents?area=&sensitivity=&status=&cursor=`.

Tabela: título, `AreaBadge`, `SensitivityBar` + label, origem (pasta do Drive), nº de chunks, atualizado em, status de ingestão. Painel lateral (`Sheet`) ao clicar: metadados completos, histórico de versões (quando existir), ações "Reprocessar" e "Remover do índice" (confirmação dupla para `confidential`).

Banner informativo permanente: "A sensibilidade é inferida da estrutura de pastas do Drive: `/Área/Sensibilidade/…`. Para corrigir, mova o arquivo no Drive e sincronize."

---

## `(admin)/admin/ingestao`

**Acesso:** `can_admin_documents`.

Painel do pipeline Celery: botão "Sincronizar com o Drive" (`POST /admin/ingestion/sync`, desabilitado enquanto job ativo), lista de jobs com estado (`pendente/processando/concluído/erro`), progresso (x/y documentos), erros expandíveis com mensagem técnica copiável. Polling TanStack Query `refetchInterval: 4000` enquanto houver job ativo, senão desligado.

**Aceite:** disparar sync mostra o job em < 4s; erro de um documento não mascara o sucesso dos demais.

---

## `(admin)/admin/areas`

**Acesso:** `super_admin`.

Lista das 8 áreas: nome, slug, cor, nº de usuários, nº de documentos. Edição de nome/cor. Criação de nova área (dialog) com aviso: "Crie a pasta correspondente no Google Drive para habilitar a ingestão."

---

## Estados globais obrigatórios (todas as páginas)

| Estado | Padrão |
|---|---|
| Carregando | `Skeleton` com a forma do conteúdo real (nunca spinner de página inteira) |
| Vazio | `EmptyState` com ação primária |
| Erro | `Alert destructive` + "Tentar novamente" que refaz a query |
| Offline | Toast persistente "Sem conexão" via listener `online/offline` |
| 403 vindo da API | Toast + redirect `/chat` |
