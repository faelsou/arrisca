# 02 — Design System

## 1. Direção visual

O Grupo Arrisca é uma **gráfica**. O design system pega emprestado o vocabulário do mundo da impressão sem cair em clichê: precisão de registro, tinta sobre papel, marcas de corte. A interface é sóbria e densa em informação (é ferramenta de trabalho diário), com **um** elemento de assinatura: o indicador de sensibilidade inspirado em barras de calibração de cor de impressão (ver §5).

Tom geral: papel claro no modo light, "prova de prelo" escura no modo dark. Nada de gradientes decorativos; cor é usada para significar (área, sensibilidade, estado), não para enfeitar.

## 2. Tokens de cor

Definidos como CSS variables em `globals.css`, mapeados no `tailwind.config.ts` (convenção shadcn/ui).

### Paleta base (light)

| Token | Hex | Uso |
|---|---|---|
| `--background` | `#FAFAF8` | Fundo de página (papel offset) |
| `--foreground` | `#1A1B1E` | Texto principal (tinta preta) |
| `--card` | `#FFFFFF` | Superfícies elevadas |
| `--muted` | `#EFEFEA` | Fundos secundários, código |
| `--muted-foreground` | `#5C5F66` | Texto secundário |
| `--border` | `#E2E2DC` | Bordas hairline |
| `--primary` | `#0E4DA4` | Ação primária (azul de impressão/cyan escuro) |
| `--primary-foreground` | `#FFFFFF` | Texto sobre primary |
| `--destructive` | `#B3261E` | Ações destrutivas |
| `--ring` | `#0E4DA4` | Focus ring |

### Dark

Fundo `#141517`, cards `#1C1D21`, bordas `#2A2C31`, primary clareado para `#5B8DEF`. Contraste mínimo AA em todos os pares (verificar com tooling no CI, §07).

### Cores semânticas de sensibilidade

Usadas **somente** no componente `SensitivityBar` e badges — nunca como decoração genérica:

| Nível | Cor | Hex |
|---|---|---|
| `public` | Verde impressão | `#1E7F4F` |
| `internal` | Cyan | `#0E7490` |
| `restricted` | Âmbar | `#B45309` |
| `confidential` | Magenta escuro | `#9D174D` |

### Cores de área (8 áreas)

Cada área recebe um hue fixo para o `AreaBadge` (dot + label). Tons dessaturados, mesma luminosidade, para não competirem com a hierarquia:

Financeiro `#7C3AED` · RH `#DB2777` · Operações `#0891B2` · Design `#EA580C` · Marketing `#DC2626` · Vendas `#16A34A` · Produção `#4F46E5` · Transporte `#CA8A04`

## 3. Tipografia

| Papel | Fonte | Uso |
|---|---|---|
| Interface | **Inter** (variable) | Todo o corpo, labels, navegação |
| Dados/código | **JetBrains Mono** | Custos do Transport Agent, IDs, trechos de código, valores monetários em tabelas |

Sem display font: é ferramenta interna de uso diário, a personalidade vem da cor semântica e da assinatura, não de títulos ornamentais.

Escala (rem): `xs 0.75 · sm 0.875 · base 0.9375 · lg 1.125 · xl 1.25 · 2xl 1.5 · 3xl 1.875`. Base levemente menor que 16px por ser UI densa; corpo de mensagens do chat usa `base` com `line-height 1.65`.

## 4. Espaçamento, raio, elevação

Grid de 4px. Raio: `--radius: 0.5rem` (cards e inputs), `0.75rem` em bolhas de chat, `9999px` só em avatares e badges. Elevação por borda + sombra sutil (`shadow-sm`); nunca sombras dramáticas.

## 5. Assinatura: `SensitivityBar`

Todo documento citado e todo card administrativo exibe uma micro-barra vertical de 3px na borda esquerda, dividida em 4 segmentos empilhados — referência direta às barras de calibração CMYK que saem na margem de toda prova gráfica. Segmentos até o nível do documento ficam preenchidos com a cor do nível; os demais, vazios.

```
public        ▮▯▯▯   (1 segmento verde)
internal      ▮▮▯▯   (2 segmentos, topo cyan)
restricted    ▮▮▮▯   (3 segmentos, topo âmbar)
confidential  ▮▮▮▮   (4 segmentos, topo magenta)
```

Sempre acompanhada de `title`/`aria-label` textual ("Sensibilidade: restrito") — cor nunca é o único canal.

## 6. Componentes shadcn/ui a instalar

`button, input, textarea, dialog, dropdown-menu, avatar, badge, tooltip, toast (sonner), skeleton, table, tabs, select, command, sheet, scroll-area, separator, alert, form, popover, switch`

## 7. Componentes próprios (inventário)

| Componente | Descrição |
|---|---|
| `AppSidebar` | Navegação colapsável; itens filtrados por `/me`; lista de conversas recentes |
| `AreaBadge` | Dot colorido + nome da área |
| `SensitivityBar` / `SensitivityBadge` | §5 |
| `MessageBubble` | Mensagem user/assistant; assistant renderiza Markdown |
| `StreamingIndicator` | Cursor de "impressão" piscante durante SSE |
| `CitationCard` | Fonte citada: título do doc, área, sensibilidade, trecho |
| `Composer` | Textarea auto-expansível, envio Enter / Shift+Enter, botão parar geração |
| `ConversationListItem` | Título + data relativa + área dominante |
| `TransportCostCard` | Resultado do agente: rota, pedágio, combustível, mão de obra, total em mono |
| `IngestionStatusRow` | Documento + estado Celery (`pendente/processando/concluído/erro`) com polling |
| `RoleGate` | Wrapper client que renderiza children se `role >= mínimo` (conveniência de UX) |
| `EmptyState` | Ilustração leve + ação primária ("Nenhuma conversa ainda. Comece perguntando algo.") |

## 8. Voz e microcopy (pt-BR)

Sentence case, verbos ativos, sem jargão de sistema. O botão diz o que acontece: "Enviar", "Parar geração", "Reprocessar documento". Erros dizem causa e saída: "Não foi possível carregar as conversas. Verifique sua conexão e tente novamente." Estados vazios convidam à ação, nunca apenas constatam.

Nomes visíveis ao usuário: "Documentos", não "chunks"; "Fontes", não "retrieval"; "Sincronizar com o Drive", não "trigger ingestion pipeline".

## 9. Modo escuro

`next-themes`, toggle no menu do usuário, padrão `system`. Todos os tokens têm par dark; cores de área/sensibilidade clareiam ~15% em dark para manter contraste.
