# CHECKLIST DE TESTES (Manual)

## Acesso e RBAC
- [ ] Abrir o app sem usuários → wizard cria admin.
- [ ] Login com admin.
- [ ] Criar papel e ajustar permissões.
- [ ] Criar usuário comum, atribuir/remover papéis.
- [ ] Verificar bloqueio de ações sem permissão (mensagem curta, app não quebra).

## Ingredientes & Estoque
- [ ] Criar 2–3 ingredientes (g/un).
- [ ] Registrar compra por lote (com validade).
- [ ] Ver estoque por lotes (restante/validade).
- [ ] Registrar preço em histórico.
- [ ] Descartar manual (com confirmação).
- [ ] Rodar “Descartar vencidos”.

## Receitas
- [ ] Criar receita (rendimento).
- [ ] Adicionar itens (ingrediente e sub-receita).
- [ ] Visualizar itens e rendimentos.

## Produtos
- [ ] Criar produto vinculado a receita.
- [ ] Conferir custo unitário e preço sugerido (margem).

## Clientes
- [ ] Criar cliente, buscar por nome.

## Pedidos – Novo
- [ ] Criar pedido com itens (preço manual e/ou sugerido).
- [ ] Ver total e snapshot de custo.
- [ ] Alertar faltas (se houver).

## Pedidos – Kanban
- [ ] Filtrar por data de entrega e cliente.
- [ ] Conferir cards compactos: nº, cliente, itens, total, faltas.
- [ ] **Marcar/Desmarcar Pago** no card.
- [ ] Botões de mover para próximas colunas.
- [ ] Ao entrar no estágio configurado (padrão: **EM_PRODUCAO**), consumir estoque **FIFO**.
- [ ] Gerar/copiar mensagens (Produção e Cliente) com placeholders.
- [ ] Cancelar pedido (solicita justificativa).

## Pós-venda
- [ ] Avançar pipeline ENTREGUE → POS1 → POS2 → DONE com notas.

## Calendário
- [ ] Visualizar pedidos por data (listagem do dia).

## Compras & Estoque
- [ ] Registrar compra manual com múltiplas linhas.
- [ ] Conferir criação de lotes e total da compra.
- [ ] Ajustar/descartar lotes com confirmação.

## Importação
- [ ] Importar CSV de ingredientes/clientes/produtos.

## Configurações
- [ ] Alterar `margin_default`.
- [ ] Alterar mensagens prontas (`msg_producao`, `msg_pronto`).
- [ ] Alterar `kanban_stages_json` (testar JSON inválido → fallback).
- [ ] Alterar `fifo_stage` e verificar efeito imediato no Kanban.

## Qualidade de vida
- [ ] Ver dicas de cópia próximas aos textos.
- [ ] Toasters de sucesso/erro nos fluxos principais.

## Segurança
- [ ] checar `can(permission)` em criar/editar/excluir; admin enxerga tudo.
