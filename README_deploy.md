# README — Deploy & Uso

## Rodando localmente
1. Crie um ambiente virtual e instale dependências:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. (Opcional) Configure `DATABASE_URL` (Postgres). Se não setar, usa SQLite `bakery.db` na raiz.
   - Exemplo Postgres:
     ```
     export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname
     ```
3. Rode o app:
   ```bash
   streamlit run app.py
   ```

## Primeiro acesso (criar admin)
- Ao abrir, se **não houver usuários**, será exibido um **wizard** para criar o primeiro **admin** (username, nome, email e senha).
- Depois, faça login na **sidebar**.
- Acesse **Usuários & Acessos** para criar usuários, papéis e editar permissões.

## RBAC
- Permissões em `ALL_PERMISSIONS` (db.py).
- Papéis padrão criados: `admin`, `staff`, `seller`.
- Admin enxerga tudo e não fica bloqueado por falta de permissão.

## Kanban Configurável
- Ajuste os estágios em **Configurações** → `kanban_stages_json` (JSON).
- Fallback automático para estágios padrão se o JSON for inválido.
- Configure o estágio que dispara **consumo FIFO** (padrão: `EM_PRODUCAO`).

## Estoque por Lotes (FIFO)
- Compras criam **StockLot** e **StockMove (IN)**.
- Consumo usa **FIFO** (validade crescente, depois criação).
- Descarte manual e descarte de vencidos disponíveis.
- Custo médio: média ponderada dos lotes restantes (fallback para último preço).

## Desempenho
- `st.cache_resource`: engine/sessions.
- `st.cache_data`: listas estáveis (produtos, ingredientes, clientes).
- Kanban carrega apenas pedidos por coluna + filtros.

## Deploy no Streamlit Cloud
1. Suba o repositório no Git.
2. Crie um app no **Streamlit Community Cloud** apontando para `app.py`.
3. **Secrets/Vars**:
   - Se for usar Postgres, defina `DATABASE_URL` em **Secrets** ou **Variables**.
4. Requisitos ficam em `requirements.txt`.
5. Ao iniciar, crie o admin via wizard.

## Teste rápido
- (Opcional) Rode `python seed_basic.py` para dados de exemplo.

## Backup
- SQLite: arquivo `bakery.db`. Faça cópia periódica.
- Postgres: utilize snapshot/backup do provedor.
