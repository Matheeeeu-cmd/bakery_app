# app.py
# Onde colar: salve como "app.py" na raiz do projeto.

import os
import json
import bcrypt
import datetime as dt
from typing import List, Dict, Optional, Tuple, Set

import pandas as pd
import streamlit as st

from db import (
    make_engine, make_sessionmaker, init_db,
    User, Role, Config,
    Ingredient, IngredientPrice, Supplier, StockLot, StockMove, LossEvent,
    Recipe, RecipeItem, Product, Client, Order, OrderItem,
    ManualPurchase, ManualPurchaseItem,
    get_or_create_default_config, get_user_permissions, ALL_PERMISSIONS,
    estimate_product_unit_cost, consume_fifo_for_order, ingredient_shortages, DEFAULT_KANBAN_STAGES,
    create_login_token, get_user_by_token, delete_token,
)

# -----------------------
# Cache de recursos: engine e sessionmaker
# -----------------------
@st.cache_resource(show_spinner=False)
def get_engine_and_sessionmaker():
    url = os.getenv("DATABASE_URL")
    engine = init_db(make_engine(url))
    SessionLocal = make_sessionmaker(engine)
    return engine, SessionLocal

engine, SessionLocal = get_engine_and_sessionmaker()

# Debug rápido da conexão com o banco
try:
    from sqlalchemy import text as _dbg_text
    with engine.connect() as conn:
        conn.execute(_dbg_text("SELECT 1"))
    st.sidebar.success("DB OK (conectado)")
except Exception as e:
    st.sidebar.error(f"DB erro de conexão: {e}")
# -----------------------
# Auto-login via token na URL
# -----------------------
def try_auto_login_from_token():
    params = st.query_params
    tok = params.get("token", [None])
    tok = tok[0] if isinstance(tok, list) else tok
    if tok and "user" not in st.session_state:
        with SessionLocal() as s:
            u = get_user_by_token(s, tok)
            if u and u.is_active:
                st.session_state["user"] = {"id": u.id, "username": u.username}
                st.session_state["perms"] = get_user_permissions(s, u)

try_auto_login_from_token()

# -----------------------
# Utils UI
# -----------------------
def copy_hint(msg: str = "Dica: use Ctrl+C/Cmd+C para copiar."):
    st.caption(msg)

def fmt_money(v: float) -> str:
    try:
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"R$ {v}"

def toast_ok(msg: str):
    st.success(msg)

def toast_err(msg: str):
    st.error(msg)

def can(code: str) -> bool:
    perms: Set[str] = st.session_state.get("perms") or set()
    return (code in perms) or (perms == ALL_PERMISSIONS)

PAGE_PERMISSION = {
    "Dashboard": "page.dashboard",
    "Ingredientes": "page.ingredients",
    "Receitas": "page.recipes",
    "Produtos": "page.products",
    "Clientes": "page.clients",
    "Pedidos – Novo": "page.orders.new",
    "Pedidos – Kanban": "page.orders.kanban",
    "Pós-venda": "page.postsale",
    "Calendário": "page.calendar",
    "Compras & Estoque": "page.stock",
    "Importação": "page.import",
    "Descarte & Vencidos": "page.discard",
    "Configurações": "page.settings",
    "Usuários & Acessos": "page.users",
}

# -----------------------
# Cache de listas estáveis
# -----------------------
@st.cache_data(show_spinner=False, ttl=60)
def cached_products():
    with SessionLocal() as s:
        q = s.query(Product).filter(Product.is_active == True).order_by(Product.name.asc()).all()
        return [(p.id, p.name) for p in q]

@st.cache_data(show_spinner=False, ttl=60)
def cached_ingredients():
    with SessionLocal() as s:
        q = s.query(Ingredient).filter(Ingredient.is_active == True).order_by(Ingredient.name.asc()).all()
        return [(i.id, i.name, i.unit) for i in q]

@st.cache_data(show_spinner=False, ttl=60)
def cached_clients():
    with SessionLocal() as s:
        q = s.query(Client).filter(Client.is_active == True).order_by(Client.name.asc()).all()
        return [(c.id, c.name) for c in q]

# -----------------------
# Autenticação
# -----------------------
def users_exist() -> bool:
    with SessionLocal() as s:
        return s.query(User).count() > 0

def create_first_admin():
    st.header("Configuração inicial — criar administrador")
    with st.form("create_admin"):
        username = st.text_input("Usuário", placeholder="admin")
        name = st.text_input("Nome")
        email = st.text_input("Email")
        pwd = st.text_input("Senha", type="password")
        pwd2 = st.text_input("Confirmar Senha", type="password")
        submitted = st.form_submit_button("Criar Admin")
        if submitted:
            if not username or not pwd or pwd != pwd2:
                toast_err("Dados inválidos ou senhas não conferem.")
                return
            phash = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            with SessionLocal() as s:
                if s.query(User).filter(User.username == username).first():
                    toast_err("Usuário já existe.")
                    return
                u = User(username=username, name=name, email=email, password_hash=phash, is_active=True)
                s.add(u); s.flush()
                admin_role = s.query(Role).filter(Role.name == "admin").first()
                if not admin_role:
                    toast_err("Papel 'admin' não encontrado. Reinicie o app.")
                    return
                u.roles.append(admin_role)
                s.commit()
                toast_ok("Administrador criado. Faça login na barra lateral.")

def login_sidebar():
    st.sidebar.title("Acesso")
    if "user" in st.session_state:
        u = st.session_state["user"]
        st.sidebar.markdown(f"**Logado como:** `{u['username']}`")
        if st.sidebar.button("Sair"):
            # apaga token da URL e do banco
            tok = st.query_params.get("token", [None])
            tok = tok[0] if isinstance(tok, list) else tok
            if tok:
                with SessionLocal() as s:
                    delete_token(s, tok)
            st.query_params.clear()
            for k in ["user", "perms"]:
                st.session_state.pop(k, None)
            st.rerun()
        return

    with st.sidebar.form("login"):
        username = st.text_input("Usuário")
        pwd = st.text_input("Senha", type="password")
        ok = st.form_submit_button("Entrar")
        if ok:
            with SessionLocal() as s:
                u = s.query(User).filter(User.username == username, User.is_active == True).first()
                if not u or not bcrypt.checkpw(pwd.encode("utf-8"), (u.password_hash or "").encode("utf-8")):
                    toast_err("Credenciais inválidas.")
                    return
                st.session_state["user"] = {"id": u.id, "username": u.username}
                st.session_state["perms"] = get_user_permissions(s, u)

                # Token na URL para manter login ao dar F5
                tok = create_login_token(s, u.id)
                st.query_params["token"] = tok

                toast_ok("Login efetuado.")
                st.rerun()

# -----------------------
# Placeholders e mensagens prontas
# -----------------------
def render_message(template: str, order: Order, items_text: str) -> str:
    entrega = str(order.delivery_date) if order.delivery_date else "data a combinar"
    cliente = (order.client.name if order.client else "Cliente")
    obs = order.obs or ""
    txt = template.format(
        order_id=order.id, cliente=cliente, entrega=entrega, itens=items_text, obs=obs
    )
    return txt

# -----------------------
# Navegação
# -----------------------
def allowed_pages() -> List[str]:
    if "perms" not in st.session_state:
        return []
    pages = []
    for label, perm in PAGE_PERMISSION.items():
        if can(perm) or can("page.settings"):
            pages.append(label)
    return pages

def app_header():
    st.title("🍰 Gestão de Confeitaria/Restaurante")
    # Removido subtítulo longo a pedido do usuário.

def load_config() -> Config:
    with SessionLocal() as s:
        return get_or_create_default_config(s)

def get_kanban_stages(cfg: Config) -> List[str]:
    try:
        stages = json.loads(cfg.kanban_stages_json or "[]")
        if isinstance(stages, list) and stages:
            return stages
    except Exception:
        pass
    return DEFAULT_KANBAN_STAGES

# -----------------------
# Páginas — Dashboard, Ingredientes, Receitas
# -----------------------
def page_dashboard():
    st.subheader("Dashboard")
    with SessionLocal() as s:
        total_orders = s.query(Order).count()
        open_orders = s.query(Order).filter(Order.status != "ENTREGUE").count()
        today = dt.date.today()
        today_orders = s.query(Order).filter(Order.delivery_date == today).count()

        revenue = s.query(Order).filter(Order.status == "ENTREGUE") \
            .with_entities((Order.total).label("rev")).all()
        revenue_sum = sum(x.rev or 0.0 for x in revenue)

        loss_moves = s.query(StockMove).filter(StockMove.move_type == "LOSS").all()
        loss_sum = sum((m.cost or 0.0) for m in loss_moves)

        out_moves = s.query(StockMove).filter(StockMove.move_type == "OUT").all()
        cost_out_sum = sum((m.cost or 0.0) for m in out_moves)

    c = st.columns(4)
    c[0].metric("Pedidos (total)", total_orders)
    c[1].metric("Aberto", open_orders)
    c[2].metric("Hoje", today_orders)
    c[3].metric("Faturado (entregue)", fmt_money(revenue_sum))
    st.metric("Perdas (descartes)", fmt_money(loss_sum))
    st.metric("Custo consumido (estimado)", fmt_money(cost_out_sum))

def page_ingredients():
    st.subheader("Ingredientes")
    if not can("page.ingredients"):
        st.info("Sem permissão para visualizar.")
        return

    tab1, tab2, tab3 = st.tabs(["Lista", "Compra por Lote", "Histórico de Preços"])

    with SessionLocal() as s:
        # ---------------- Tab 1: Lista / CRUD rápido ----------------
        with tab1:
            st.markdown("### Cadastro Rápido")
            with st.form("ing_form"):
                name = st.text_input("Nome")
                unit = st.selectbox("Unidade padrão", ["g", "un"])
                active = st.checkbox("Ativo", value=True)
                ok = st.form_submit_button("Salvar")
                if ok:
                    if not can("ingredient.create"):
                        toast_err("Sem permissão.")
                    elif not name:
                        toast_err("Informe o nome.")
                    else:
                        if s.query(Ingredient).filter(Ingredient.name == name).first():
                            toast_err("Ingrediente já existe.")
                        else:
                            s.add(Ingredient(name=name, unit=unit, is_active=active))
                            s.commit()
                            cached_ingredients.clear()
                            toast_ok("Ingrediente criado.")
                            st.rerun()

            st.markdown("### Editar Ingrediente")
            all_ings = s.query(Ingredient).order_by(Ingredient.name.asc()).all()
            ing_sel = st.selectbox("Selecione para editar", all_ings, format_func=lambda i: i.name if i else "-")
            if ing_sel:
                c1, c2, c3 = st.columns([3, 1, 1])
                new_name = c1.text_input("Nome", value=ing_sel.name, key=f"ing_edit_name_{ing_sel.id}")
                new_unit = c2.selectbox(
                    "Unidade padrão",
                    ["g", "un"],
                    index=(0 if ( ing_sel.unit or "g") == "g" else 1),
                    key=f"ing_edit_unit_{ing_sel.id}",
                )
                new_active = c3.checkbox("Ativo", value=bool(ing_sel.is_active), key=f"ing_edit_active_{ing_sel.id}")
                if st.button("Salvar alterações", key=f"ing_edit_save_{ing_sel.id}"):
                    if not can("ingredient.update"):
                        toast_err("Sem permissão.")
                    else:
                        ing_sel.name = (new_name or ing_sel.name).strip()
                        ing_sel.unit = new_unit
                        ing_sel.is_active = new_active
                        s.commit()
                        cached_ingredients.clear()
                        toast_ok("Ingrediente atualizado.")
                        st.rerun()

            st.markdown("### Lista")
            ings = (
                s.query(Ingredient)
                .order_by(Ingredient.is_active.desc(), Ingredient.name.asc())
                .with_entities(Ingredient.id, Ingredient.name, Ingredient.unit, Ingredient.is_active)
                .all()
            )
            df = pd.DataFrame(ings, columns=["ID", "Nome", "Unidade", "Ativo"])
            st.dataframe(df, hide_index=True, use_container_width=True)

        # ---------------- Tab 2: Compra por Lote ----------------
        with tab2:
            st.markdown("### Registrar Compra/Lote")
            ing_opts = cached_ingredients()
            if not ing_opts:
                st.info("Cadastre ingredientes antes.")
            else:
                ing_map = {n: i for i, n, _ in ing_opts}
                col1, col2 = st.columns(2)
                sel_name = col1.selectbox("Ingrediente", [n for _, n, _ in ing_opts])
                unit = col2.selectbox("Unidade do lote", ["g", "un"])
                qty = st.number_input("Quantidade", min_value=0.0, step=0.1)
                price = st.number_input("Preço por unidade", min_value=0.0, step=0.01)
                best_before = st.date_input("Validade (opcional)", value=None)
                note = st.text_input("Nota (opcional)")
                if st.button("Criar Lote"):
                    if not can("ingredient.buy_lot"):
                        toast_err("Sem permissão.")
                    else:
                        from db import create_lot
                        create_lot(s, ing_map[sel_name], qty, unit, price, best_before, note)
                        toast_ok("Lote criado.")
                        st.rerun()

            st.markdown("### Estoque por Lotes (resumo)")
            lots = (
                s.query(StockLot)
                .order_by(StockLot.best_before.is_(None), StockLot.best_before.asc(), StockLot.created_at.asc())
                .with_entities(StockLot.id, StockLot.ingredient_id, StockLot.qty_remaining, StockLot.unit, StockLot.best_before)
                .all()
            )
            id_to_ing = {i.id: i.name for i in s.query(Ingredient).all()}
            rows = [
                {"Lote": lid, "Ingrediente": id_to_ing.get(iid, "?"), "Qtd restante": qty, "Un": u, "Validade": bb}
                for lid, iid, qty, u, bb in lots
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        # ---------------- Tab 3: Histórico de Preços (somente consulta) ----------------
       # ---------------- Tab 3: Histórico de Preços (somente consulta) ----------------
        with tab3:
            st.markdown("### Histórico de Preços")
            ing_opts = cached_ingredients()
            if not ing_opts:
                st.info("Cadastre ingredientes antes.")
            else:
                ing_names = [n for _, n, _ in ing_opts]
                sel_name = st.selectbox("Ingrediente", ing_names, key="price_hist_ing_view")
                # consultar preços do ingrediente escolhido
                q = (
                    s.query(IngredientPrice)
                    .join(Ingredient, IngredientPrice.ingredient_id == Ingredient.id)
                    .filter(Ingredient.name == sel_name)
                    .order_by(IngredientPrice.created_at.desc())
                    .limit(400)
                )
                prices = q.all()
                rows = [{"Quando": p.created_at, "Preço/un": p.price} for p in prices]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                
def page_recipes():
    st.subheader("Receitas")
    if not can("page.recipes"):
        st.info("Sem permissão.")
        return
    with SessionLocal() as s:
        st.markdown("### Nova Receita")
        with st.form("rec_new"):
            name = st.text_input("Nome da receita")
            yield_qty = st.number_input("Rendimento (quantidade total)", min_value=0.0, value=1.0)
            unit = st.selectbox("Unidade do rendimento", ["un", "g"])
            ok = st.form_submit_button("Criar")
            if ok:
                if not can("recipe.create"):
                    toast_err("Sem permissão.")
                elif not name:
                    toast_err("Informe o nome.")
                else:
                    if s.query(Recipe).filter(Recipe.name == name).first():
                        toast_err("Já existe.")
                    else:
                        s.add(Recipe(name=name, yield_qty=yield_qty, unit=unit, is_active=True))
                        s.commit()
                        toast_ok("Receita criada.")
                        st.rerun()

        st.markdown("### Itens da Receita")
        recs = s.query(Recipe).order_by(Recipe.name.asc()).all()
        if not recs:
            st.info("Crie uma receita acima.")
            return

        rec_sel = st.selectbox("Selecione a receita", recs, format_func=lambda r: r.name if r else "-")
        if rec_sel:
            # editar cabeçalho da receita
            e1, e2, e3, e4 = st.columns([3,1,1,1])
            new_name = e1.text_input("Nome", value=rec_sel.name, key=f"rec_name_{rec_sel.id}")
            new_yield = e2.number_input("Rendimento", min_value=0.0, step=0.1, value=float(rec_sel.yield_qty or 1.0), key=f"rec_yield_{rec_sel.id}")
            new_unit = e3.selectbox("Unidade", ["un","g"], index=(0 if (rec_sel.unit or "un")=="un" else 1), key=f"rec_unit_{rec_sel.id}")
            new_active = e4.checkbox("Ativa", value=bool(rec_sel.is_active), key=f"rec_active_{rec_sel.id}")
            if st.button("Salvar receita", key=f"rec_head_save_{rec_sel.id}"):
                if not can("recipe.update"):
                    toast_err("Sem permissão.")
                else:
                    rec_sel.name = (new_name or rec_sel.name).strip()
                    rec_sel.yield_qty = new_yield
                    rec_sel.unit = new_unit
                    rec_sel.is_active = new_active
                    s.commit()
                    toast_ok("Receita atualizada.")
                    st.rerun()

            st.write(f"Rendimento atual: {rec_sel.yield_qty} {rec_sel.unit}")

            ing_opts = cached_ingredients()
            with st.form("rec_item_add"):
                col1, col2, col3 = st.columns(3)
                opt = col1.selectbox("Tipo de item", ["Ingrediente","Sub-receita"])
                qty = col2.number_input("Quantidade", min_value=0.0, step=0.1)
                item_type = col3.selectbox("Tipo de medida", ["peso","unidade"])
                ingr = None; subr = None
                if opt == "Ingrediente":
                    ingr = st.selectbox("Ingrediente", ing_opts, format_func=lambda t: t[1], key=f"rec_ing_sel_{rec_sel.id}")
                else:
                    subr = st.selectbox("Sub-receita", [r for r in recs if r.id != rec_sel.id], format_func=lambda r: r.name, key=f"rec_sub_sel_{rec_sel.id}")
                ok_add = st.form_submit_button("Adicionar item")
                if ok_add:
                    if opt == "Ingrediente" and ingr:
                        s.add(RecipeItem(recipe_id=rec_sel.id, ingredient_id=ingr[0], qty=qty, item_type=item_type))
                    elif opt == "Sub-receita" and subr:
                        s.add(RecipeItem(recipe_id=rec_sel.id, sub_recipe_id=subr.id, qty=qty, item_type=item_type))
                    s.commit()
                    toast_ok("Item adicionado.")
                    st.rerun()

            st.markdown("#### Itens")
            items = s.query(RecipeItem).filter(RecipeItem.recipe_id==rec_sel.id).order_by(RecipeItem.id.asc()).all()
            for it in items:
                with st.container(border=True):
                    if it.ingredient:
                        st.write(f"Ingrediente: **{it.ingredient.name}** • Qtd: {it.qty} • Medida: {it.item_type}")
                    elif it.sub_recipe:
                        st.write(f"Sub-receita: **{it.sub_recipe.name}** • Qtd: {it.qty} • Medida: {it.item_type}")
                    c1, c2 = st.columns(2)
                    new_qty = c1.number_input("Qtd", min_value=0.0, step=0.1, value=float(it.qty), key=f"ri_qty_{it.id}")
                    if c1.button("Salvar", key=f"ri_save_{it.id}"):
                        it.qty = new_qty; s.commit(); toast_ok("Item atualizado."); st.rerun()
                    if c2.button("Remover", key=f"ri_del_{it.id}"):
                        s.delete(it); s.commit(); toast_ok("Item removido."); st.rerun()


# -----------------------
# Páginas — Produtos, Clientes, Pedidos (Novo), Kanban
# -----------------------
def page_products():
    st.subheader("Produtos")
    if not can("page.products"):
        st.info("Sem permissão.")
        return
    with SessionLocal() as s:
        recs = s.query(Recipe).order_by(Recipe.name.asc()).all()
        st.markdown("### Novo Produto")
        with st.form("prod_new"):
            name = st.text_input("Nome do produto")
            recipe = st.selectbox("Receita base (opcional)", [None] + recs, format_func=lambda r: r.name if r else "-")
            price_manual = st.number_input("Preço manual (deixe 0 para sugerido)", min_value=0.0, value=0.0, step=0.01)
            ok = st.form_submit_button("Criar")
            if ok:
                if not can("product.create"):
                    toast_err("Sem permissão.")
                elif not name:
                    toast_err("Nome obrigatório.")
                else:
                    if s.query(Product).filter(Product.name == name).first():
                        toast_err("Já existe.")
                    else:
                        s.add(Product(
                            name=name,
                            recipe_id=(recipe.id if recipe else None),
                            is_active=True,
                            price_manual=(price_manual or None) if price_manual > 0 else None
                        ))
                        s.commit()
                        cached_products.clear()
                        toast_ok("Produto criado.")
                        st.rerun()

        st.markdown("### Editar produto")
        allp = s.query(Product).order_by(Product.name.asc()).all()
        psel = st.selectbox("Selecione", allp, format_func=lambda p: p.name if p else "-")
        if psel:
            c1, c2, c3, c4 = st.columns([3,2,1,1])
            new_name = c1.text_input("Nome", value=psel.name, key=f"pname_{psel.id}")

            # ↓↓↓ CORREÇÃO AQUI (índice seguro por ID)
            recs = s.query(Recipe).order_by(Recipe.name.asc()).all()
            rec_choices = [None] + recs
            if psel.recipe_id:
                try:
                    idx = 1 + next(i for i, r in enumerate(recs) if r.id == psel.recipe_id)
                except StopIteration:
                    idx = 0
            else:
                idx = 0
            new_rec = c2.selectbox(
                "Receita",
                rec_choices,
                index=idx,
                format_func=lambda r: (r.name if r else "-"),
                key=f"prec_{psel.id}",
            )
            # ↑↑↑ FIM DA CORREÇÃO

            new_price_manual = c3.number_input("Preço manual", min_value=0.0, value=float(psel.price_manual or 0.0), step=0.01, key=f"pprice_{psel.id}")
            new_active = c4.checkbox("Ativo", value=bool(psel.is_active), key=f"pactive_{psel.id}")
            if st.button("Salvar produto", key=f"psave_{psel.id}"):
                if not can("product.update"):
                    toast_err("Sem permissão.")
                else:
                    psel.name = (new_name or "").strip() or psel.name
                    psel.recipe_id = new_rec.id if new_rec else None
                    psel.price_manual = new_price_manual if new_price_manual > 0 else None
                    psel.is_active = new_active
                    s.commit()
                    cached_products.clear()
                    toast_ok("Produto atualizado.")
                    st.rerun()


def page_clients():
    st.subheader("Clientes")
    if not can("page.clients"):
        st.info("Sem permissão.")
        return
    with SessionLocal() as s:
        st.markdown("### Novo Cliente")
        with st.form("cli_new"):
            name = st.text_input("Nome")
            phone = st.text_input("Telefone")
            address = st.text_input("Endereço")
            notes = st.text_area("Notas")
            ok = st.form_submit_button("Salvar")
            if ok:
                if not can("client.create"):
                    toast_err("Sem permissão.")
                elif not name:
                    toast_err("Informe o nome.")
                else:
                    s.add(Client(name=name, phone=phone, address=address, notes=notes, is_active=True))
                    s.commit()
                    cached_clients.clear()
                    toast_ok("Cliente criado.")
                    st.rerun()

        st.markdown("### Editar Cliente")
        cs = s.query(Client).order_by(Client.name.asc()).all()
        sel = st.selectbox("Selecione", cs, format_func=lambda c: c.name if c else "-")
        if sel:
            c1, c2 = st.columns([2, 1])
            new_name = c1.text_input("Nome", value=sel.name, key=f"cli_name_{sel.id}")
            new_phone = c2.text_input("Telefone", value=sel.phone or "", key=f"cli_phone_{sel.id}")
            new_addr = st.text_input("Endereço", value=sel.address or "", key=f"cli_addr_{sel.id}")
            new_notes = st.text_area("Notas", value=sel.notes or "", key=f"cli_notes_{sel.id}")
            new_active = st.checkbox("Ativo", value=bool(sel.is_active), key=f"cli_active_{sel.id}")
            if st.button("Salvar alterações", key=f"cli_save_{sel.id}"):
                if not can("client.update"):
                    toast_err("Sem permissão.")
                else:
                    sel.name = new_name.strip() or sel.name
                    sel.phone = new_phone
                    sel.address = new_addr
                    sel.notes = new_notes
                    sel.is_active = new_active
                    s.commit()
                    cached_clients.clear()
                    toast_ok("Cliente atualizado.")
                    st.rerun()

        st.markdown("### Busca")
        q = st.text_input("Pesquisar por nome")
        query = s.query(Client)
        if q:
            query = query.filter(Client.name.ilike(f"%{q}%"))
        clients = query.order_by(Client.is_active.desc(), Client.name.asc()).limit(500).all()
        rows = [{"ID": c.id, "Nome": c.name, "Telefone": c.phone, "Ativo": c.is_active} for c in clients]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

def _order_total(items: List[OrderItem]) -> float:
    return sum((it.unit_price or 0.0) * (it.qty or 0.0) for it in items)

def page_order_new():
    st.subheader("Novo Pedido")
    if not can("page.orders.new"):
        st.info("Sem permissão.")
        return
    with SessionLocal() as s:
        cl_opts = cached_clients()
        pr_opts = cached_products()
        with st.form("order_new"):
            client = st.selectbox("Cliente", cl_opts, format_func=lambda t: t[1] if t else "-")
            delivery = st.date_input("Data de entrega", value=dt.date.today())
            obs = st.text_area("Observações")
            st.markdown("**Itens**")
            item_rows = st.number_input("Quantos itens adicionar nesta tela?", min_value=1, max_value=10, value=1)
            entries = []
            for i in range(int(item_rows)):
                cols = st.columns((3, 1, 1))
                prod = cols[0].selectbox(f"Produto #{i+1}", pr_opts, key=f"prod_{i}", format_func=lambda t: t[1])
                qty = cols[1].number_input(f"Qtd #{i+1}", min_value=0.0, step=1.0, value=1.0, key=f"qty_{i}")
                price = cols[2].number_input(f"Preço unit. #{i+1}", min_value=0.0, step=0.01, value=0.0, key=f"price_{i}")
                entries.append((prod, qty, price))
            paid = st.checkbox("Pago?")
            submit = st.form_submit_button("Criar Pedido")
            if submit:
                if not can("order.create"):
                    toast_err("Sem permissão.")
                    return
                if not entries:
                    toast_err("Adicione ao menos um item.")
                    return
                o = Order(
                    client_id=client[0] if client else None,
                    delivery_date=delivery,
                    status="NOVO",
                    paid=paid,
                    obs=obs,
                    total=0.0
                )
                s.add(o); s.flush()
                cfg = get_or_create_default_config(s)
                for (prod, qty, price) in entries:
                    p = s.get(Product, int(prod[0]))
                    cost = estimate_product_unit_cost(s, p)
                    it = OrderItem(
                        order_id=o.id,
                        product_id=p.id,
                        qty=qty,
                        unit_price=price if price > 0 else cost * (1.0 + (cfg.margin_default or 0.60)),
                        unit_cost_snapshot=cost
                    )
                    s.add(it)
                s.flush()
                o.total = _order_total(o.items)
                s.commit()
                toast_ok(f"Pedido #{o.id} criado com total {fmt_money(o.total)}.")
                st.rerun()

def page_kanban():
    st.subheader("Kanban de Pedidos")
    if not can("page.orders.kanban"):
        st.info("Sem permissão.")
        return
    cfg = load_config()
    stages = get_kanban_stages(cfg)
    st.caption(f"Estágios: {', '.join(stages)}")
    col_top = st.columns(3)
    date_filter = col_top[0].date_input("Entrega em (filtro opcional)", value=None)
    client_query = col_top[1].text_input("Cliente (contém)", value="")
    consume_stage = cfg.fifo_stage or "EM_PRODUCAO"
    st.caption(f"Consumo FIFO será aplicado ao entrar em: **{consume_stage}** (configurável).")

    with SessionLocal() as s:
        cols = st.columns(len(stages))
        for idx, stage in enumerate(stages):
            with cols[idx]:
                st.markdown(f"#### {stage}")
                q = s.query(Order).filter(Order.status == stage)
                if date_filter:
                    q = q.filter(Order.delivery_date == date_filter)
                if client_query:
                    q = q.join(Client, isouter=True).filter(Client.name.ilike(f"%{client_query}%"))
                orders = q.order_by(Order.delivery_date.asc().nulls_last(), Order.created_at.asc()).limit(50).all()
                for o in orders:
                    with st.container(border=True):
                        cli_name = o.client.name if o.client else "—"
                        items_txt = ", ".join([f"{it.qty}x {(it.product.name if it.product else '??')}" for it in o.items])
                        st.markdown(f"**#{o.id}** — {cli_name}")
                        st.caption(f"Itens: {items_txt}")
                        st.caption(f"Total: {fmt_money(o.total or 0.0)}  •  Entrega: {o.delivery_date or '-'}")

                        # Faltas
                        sh = ingredient_shortages(s, o)
                        if sh:
                            st.warning("Faltando: " + ", ".join([f"{ing.name} ({qtd:.2f})" for ing, qtd in sh if ing]))

                        # Pago toggle
                        colp1, colp2 = st.columns(2)
                        if colp1.button(("✓ Desmarcar Pago" if o.paid else "💰 Marcar Pago"), key=f"paid_{stage}_{o.id}"):
                            if (o.paid and can("order.unmark_paid")) or ((not o.paid) and can("order.mark_paid")):
                                o.paid = not o.paid
                                s.commit()
                                toast_ok("Status de pagamento atualizado.")
                                st.rerun()
                            else:
                                toast_err("Sem permissão.")

                        # Mensagens
                        msg_cols = st.columns(2)
                        prod_msg = render_message(cfg.msg_producao, o, items_txt)
                        pronto_msg = render_message(cfg.msg_pronto, o, items_txt)
                        with msg_cols[0]:
                            st.text_area("Mensagem: Produção", prod_msg, height=80, key=f"mprod_{o.id}")
                            copy_hint()
                        with msg_cols[1]:
                            st.text_area("Mensagem: Cliente (Pronto)", pronto_msg, height=80, key=f"mpronto_{o.id}")
                            copy_hint()

                        # Mover estágio
                        if stage != "CANCELADO":
                            i_stage = stages.index(stage)
                            nexts = []
                            if i_stage + 1 < len(stages): nexts.append(stages[i_stage + 1])
                            if i_stage + 2 < len(stages): nexts.append(stages[i_stage + 2])  # pular
                            if nexts:
                                mv = st.selectbox("Mover para:", ["-"] + nexts, key=f"mv_{o.id}")
                                if mv != "-" and st.button("Mover", key=f"btn_mv_{o.id}"):
                                    if can("order.move_stage"):
                                        entering_consume = (mv == consume_stage and o.status != consume_stage)
                                        o.status = mv
                                        s.commit()
                                        if entering_consume and can("order.consume_fifo"):
                                            res = consume_fifo_for_order(s, o)
                                            faltantes = {ing_id: miss for ing_id, (_, miss) in res.items() if miss > 1e-9}
                                            if faltantes:
                                                st.warning("Consumo aplicado com faltas em alguns ingredientes.")
                                            toast_ok("Estoque consumido (FIFO).")
                                        st.rerun()
                                    else:
                                        toast_err("Sem permissão.")

                        # Cancelar
                        if st.button("Cancelar", key=f"cancel_{o.id}"):
                            just = st.text_input("Justificativa do cancelamento", key=f"just_{o.id}")
                            if st.button("Confirmar cancelamento", key=f"cnf_cancel_{o.id}"):
                                if not can("order.cancel"):
                                    toast_err("Sem permissão.")
                                else:
                                    o.status = "CANCELADO"
                                    o.canceled_reason = just or "Sem justificativa."
                                    s.commit()
                                    toast_ok("Pedido cancelado.")
                                    st.rerun()

# -----------------------
# Páginas — Pós-venda, Calendário, Compras & Estoque, Importação, Descarte & Vencidos,
#           Configurações, Usuários & Acessos
# -----------------------
def page_postsale():
    st.subheader("Pós-venda")
    if not can("page.postsale"):
        st.info("Sem permissão.")
        return
    stages = ["ENTREGUE","POS1","POS2","DONE"]
    cols = st.columns(4)
    with SessionLocal() as s:
        for i, stage in enumerate(stages):
            with cols[i]:
                st.markdown(f"### {stage}")
                q = s.query(Order).filter(Order.status=="ENTREGUE")  # só após entregue
                # organiza pela data relevante da etapa
                if stage == "POS1":
                    q = q.filter(Order.pos_stage.in_(["ENTREGUE","POS1"])).order_by(Order.pos1_date.asc().nulls_last(), Order.delivery_date.desc())
                elif stage == "POS2":
                    q = q.filter(Order.pos_stage.in_(["POS1","POS2"])).order_by(Order.pos2_date.asc().nulls_last(), Order.delivery_date.desc())
                elif stage == "DONE":
                    q = q.filter(Order.pos_stage=="DONE").order_by(Order.delivery_date.desc())
                else:
                    q = q.filter(Order.pos_stage=="ENTREGUE").order_by(Order.delivery_date.desc())

                for o in q.limit(50).all():
                    with st.container(border=True):
                        st.markdown(f"**#{o.id}** — {o.client.name if o.client else '—'}")
                        st.caption(f"Entrega: {o.delivery_date or '-'}")
                        d1, d2 = st.columns(2)
                        pos1 = d1.date_input("POS1 em", value=o.pos1_date, key=f"pos1_{o.id}")
                        pos2 = d2.date_input("POS2 em", value=o.pos2_date, key=f"pos2_{o.id}")
                        if st.button("Salvar datas", key=f"pos_save_{o.id}"):
                            o.pos1_date = pos1; o.pos2_date = pos2; s.commit(); toast_ok("Datas salvas.")
                        nxt = None
                        if o.pos_stage == "ENTREGUE": nxt = "POS1"
                        elif o.pos_stage == "POS1": nxt = "POS2"
                        elif o.pos_stage == "POS2": nxt = "DONE"
                        if nxt and st.button(f"Avançar para {nxt}", key=f"pos_next_{o.id}"):
                            o.pos_stage = nxt; s.commit(); toast_ok("Etapa atualizada."); st.rerun()

def page_calendar():
    st.subheader("Calendário de Entregas (listagem)")
    if not can("page.calendar"):
        st.info("Sem permissão.")
        return
    with SessionLocal() as s:
        day = st.date_input("Dia", value=dt.date.today())
        orders = s.query(Order).filter(Order.delivery_date == day).order_by(Order.created_at.asc()).all()
        rows = [{"#": o.id, "Cliente": (o.client.name if o.client else "—"), "Status": o.status, "Pago": "Sim" if o.paid else "Não", "Total": fmt_money(o.total or 0.0)} for o in orders]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

def page_stock():
    st.subheader("Compras & Estoque")
    if not can("page.stock"):
        st.info("Sem permissão.")
        return

    with SessionLocal() as s:
        # ---------------- Fornecedores ----------------
        st.markdown("### Fornecedores")
        with st.form("sup_new"):
            sname = st.text_input("Nome do fornecedor")
            scontact = st.text_input("Contato")
            if st.form_submit_button("Criar fornecedor"):
                if sname:
                    s.add(Supplier(name=sname, contact=scontact)); s.commit()
                    toast_ok("Fornecedor criado.")
                    st.rerun()
        sup_all = s.query(Supplier).order_by(Supplier.name.asc()).all()
        if sup_all:
            sup_sel = st.selectbox("Editar fornecedor", [None] + sup_all, format_func=lambda x: x.name if x else "-")
            if sup_sel:
                n1, n2 = st.columns(2)
                nname = n1.text_input("Nome", value=sup_sel.name or "", key=f"supn_{sup_sel.id}")
                ncont = n2.text_input("Contato", value=sup_sel.contact or "", key=f"supc_{sup_sel.id}")
                if st.button("Salvar fornecedor", key=f"supsave_{sup_sel.id}"):
                    sup_sel.name = (nname or sup_sel.name).strip()
                    sup_sel.contact = ncont
                    s.commit()
                    toast_ok("Fornecedor atualizado.")
                    st.rerun()

        st.markdown("### Compra manual")
        ing_opts = cached_ingredients()
        if not ing_opts:
            st.info("Cadastre ingredientes primeiro.")
        else:
            with st.form("purchase_form"):
                # Fornecedor por dropdown
                sup_list = s.query(Supplier).order_by(Supplier.name.asc()).all()
                sup = st.selectbox("Fornecedor", [None] + sup_list, format_func=lambda x: x.name if x else "-")

                lines = st.number_input("Itens nesta compra", min_value=1, max_value=20, value=1)
                entries = []
                for i in range(int(lines)):
                    cols = st.columns((3, 1, 1, 2, 2))
                    sel = cols[0].selectbox(f"Ingrediente #{i+1}", ing_opts, key=f"p_ing_{i}", format_func=lambda t: t[1])
                    qty = cols[1].number_input("Qtd", min_value=0.0, step=0.1, key=f"p_qty_{i}")
                    unit = cols[2].selectbox("Un", ["g", "un"], key=f"p_unit_{i}")
                    total_price = cols[3].number_input("Valor total do item (R$)", min_value=0.0, step=0.01, key=f"p_total_{i}")
                    bb = cols[4].date_input("Validade (opcional)", key=f"p_bb_{i}")
                    entries.append((sel, qty, unit, total_price, bb))

                ok = st.form_submit_button("Registrar compra")
                if ok:
                    if not can("stock.create_purchase"):
                        toast_err("Sem permissão.")
                    else:
                        # validação rápida
                        if any((q <= 0 for _, q, _, _, _ in entries)):
                            toast_err("Quantidade deve ser maior que zero.")
                            st.stop()

                        total_compra = 0.0
                        p = ManualPurchase(supplier_id=(sup.id if sup else None), total=0.0)
                        s.add(p); s.flush()

                        from db import create_lot
                        # mapa id -> (nome, unidade)
                        ing_id_to_unit = {int(i): u for i, _, u in ing_opts}
                        for (sel, qty, unit, total_price, bb) in entries:
                            ing_id = int(sel[0])
                            # calcula preço por unidade automaticamente
                            unit_price = (total_price / qty) if qty > 0 else 0.0

                            # item da lista de compra
                            s.add(
                                ManualPurchaseItem(
                                    purchase_id=p.id,
                                    ingredient_id=ing_id,
                                    qty=qty,
                                    unit=unit,
                                    price=unit_price,   # armazenamos o valor unitário aqui
                                )
                            )

                            # cria o lote com custo unitário calculado
                            create_lot(s, ing_id, qty, unit, unit_price, bb, note=f"Compra #{p.id}")

                            # grava histórico de preço (preço por unidade)
                            s.add(IngredientPrice(ingredient_id=ing_id, price=unit_price))

                            total_compra += (total_price or 0.0)

                        p.total = total_compra
                        s.commit()
                        toast_ok(f"Compra registrada (total {fmt_money(total_compra)}).")
                        st.rerun()


        # ---------------- Ajustes / Vencidos ----------------
        st.markdown("### Ajustes / Vencidos")
        lots = s.query(StockLot).order_by(
            StockLot.best_before.is_(None), StockLot.best_before.asc(), StockLot.created_at.asc()
        ).limit(200).all()
        for lot in lots:
            with st.expander(f"Lote #{lot.id} — {lot.ingredient.name if lot.ingredient else '?'} • Restante {lot.qty_remaining} {lot.unit} • Validade {lot.best_before or '-'}"):
                qty_adj = st.number_input("Descartar quantidade", min_value=0.0, step=0.1, key=f"dsc_{lot.id}")
                reason = st.text_input("Motivo", key=f"rsn_{lot.id}", value="Ajuste manual")
                if st.button("Descartar", key=f"btn_dsc_{lot.id}"):
                    if not can("stock.discard"):
                        toast_err("Sem permissão.")
                    else:
                        if st.checkbox("Confirmo o descarte", key=f"conf_{lot.id}"):
                            from db import discard_from_lot
                            taken = discard_from_lot(s, lot.id, qty_adj, reason=reason)
                            toast_ok(f"Descartado {taken} {lot.unit}.")
                            st.rerun()

        # ---------------- Sugestões de compra ----------------
        st.markdown("### Sugestões de compra")
        with st.expander("Gerar por faltas em pedidos abertos"):
            d1, d2 = st.columns(2)
            day_from = d1.date_input("De", value=dt.date.today())
            day_to = d2.date_input("Até", value=dt.date.today()+dt.timedelta(days=7))
            if st.button("Gerar sugestão"):
                # calcula necessidade total no intervalo
                open_orders = s.query(Order).filter(
                    Order.status.notin_(["CANCELADO","ENTREGUE"]),
                    Order.delivery_date >= day_from, Order.delivery_date <= day_to
                ).all()
                need: Dict[int,float] = {}
                for o in open_orders:
                    from db import required_ingredients_for_order
                    req = required_ingredients_for_order(s, o)
                    for k,v in req.items():
                        need[k] = need.get(k,0.0) + v
                # subtrai disponível
                suggest = []
                # ingredientes já sugeridos (listas abertas)
                open_suggestions = s.query(ManualPurchaseItem.ingredient_id).join(ManualPurchase, ManualPurchase.id==ManualPurchaseItem.purchase_id)\
                    .filter(ManualPurchase.is_suggestion==True, ManualPurchase.completed_at.is_(None)).all()
                already = set([x[0] for x in open_suggestions])

                for ing_id, req_qty in need.items():
                    avail = s.query(StockLot).with_entities(StockLot.qty_remaining).filter(
                        StockLot.ingredient_id==ing_id, StockLot.qty_remaining>0
                    ).all()
                    have = sum(x[0] for x in avail)
                    miss = req_qty - have
                    if miss > 0 and ing_id not in already:
                        suggest.append(( ing_id, miss ))

                if not suggest:
                    toast_ok("Sem faltas novas. Nada sugerido.")
                else:
                    mp = ManualPurchase(is_suggestion=True, title=f"Sugestão {day_from}..{day_to}", total=0.0)
                    s.add(mp); s.flush()
                    for ing_id, miss in suggest:
                        ing = s.get(Ingredient, ing_id)
                        s.add(ManualPurchaseItem(purchase_id=mp.id, ingredient_id=ing_id, qty=miss, unit=ing.unit, price=0.0))
                    s.commit()
                    toast_ok(f"Sugestão criada #{mp.id}.")
                    st.rerun()

        with st.expander("Adicionar itens manualmente à lista de compra"):
            ing_opts = cached_ingredients()
            if ing_opts:
                ing = st.selectbox("Ingrediente", ing_opts, format_func=lambda t: t[1], key="man_sugg_ing")
                qty = st.number_input("Quantidade a comprar", min_value=0.0, step=0.1, key="man_sugg_qty")
                if st.button("Adicionar a uma lista aberta (ou criar nova)", key="man_sugg_add"):
                    # pega lista aberta mais recente ou cria
                    mp = s.query(ManualPurchase).filter(ManualPurchase.is_suggestion==True, ManualPurchase.completed_at.is_(None))\
                        .order_by(ManualPurchase.created_at.desc()).first()
                    if not mp:
                        mp = ManualPurchase(is_suggestion=True, title=f"Sugestão manual {dt.date.today()}", total=0.0)
                        s.add(mp); s.flush()
                    ing_id, ing_unit = int(ing[0]), ing[2]
                    s.add(ManualPurchaseItem(purchase_id=mp.id, ingredient_id=ing_id, qty=qty, unit=ing_unit, price=0.0))
                    s.commit()
                    toast_ok("Adicionado à lista de compras.")
                    st.rerun()

        # Listagem de sugestões (histórico)
        sugg = s.query(ManualPurchase).filter(ManualPurchase.is_suggestion==True).order_by(
            ManualPurchase.created_at.desc()
        ).limit(20).all()
        for mp in sugg:
            with st.expander(f"Sugestão #{mp.id} — {mp.title or mp.created_at.date()}{' (aberta)' if not mp.completed_at else ''}"):
                items = s.query(ManualPurchaseItem).filter(ManualPurchaseItem.purchase_id==mp.id).all()
                rows = []
                for it in items:
                    ing = s.get(Ingredient, it.ingredient_id)
                    rows.append({"Ingrediente": ing.name if ing else "?", "Qtd": it.qty, "Un": it.unit})
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                if st.button("Baixar CSV", key=f"csv_{mp.id}"):
                    csv = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
                    st.download_button("Download CSV", csv, file_name=f"sugestao_{mp.id}.csv", mime="text/csv", key=f"dl_{mp.id}")
                if not mp.completed_at and st.button("Marcar como concluída", key=f"done_{mp.id}"):
                    mp.completed_at = dt.datetime.utcnow(); s.commit(); toast_ok("Marcada como concluída."); st.rerun()

        if st.button("Descartar vencidos automaticamente"):
            if not can("stock.discard_expired"):
                toast_err("Sem permissão.")
            else:
                from db import discard_expired
                res = discard_expired(s)
                toast_ok(f"Lotes processados: {len(res)}")

def page_import():
    st.subheader("Importação (CSV simples)")
    if not can("page.import"):
        st.info("Sem permissão.")
        return
    st.markdown("Modelos de CSV:")
    st.download_button("Modelo Ingredientes", data="name,unit\nFarinha,g\nAçúcar,g\n", file_name="ingredientes_modelo.csv", mime="text/csv")
    st.download_button("Modelo Clientes", data="name,phone,address\nCliente Exemplo,11999990000,Rua A 123\n", file_name="clientes_modelo.csv", mime="text/csv")
    st.download_button("Modelo Produtos", data="name\nBolo de Chocolate\n", file_name="produtos_modelo.csv", mime="text/csv")

    up = st.file_uploader("CSV de Ingredientes/Clientes/Produtos (colunas mínimas variam)", type=["csv"])
    if not up:
        st.info("Envie um CSV para processar.")
        return
    df = pd.read_csv(up)
    st.write("Prévia:", df.head())
    mode = st.selectbox("Importar como", ["Ingredientes", "Clientes", "Produtos"])
    if st.button("Importar"):
        with SessionLocal() as s:
            if mode == "Ingredientes":
                for _, r in df.iterrows():
                    name = str(r.get("name") or r.get("Nome") or "").strip()
                    unit = str(r.get("unit") or r.get("Unidade") or "g").strip()
                    if name and not s.query(Ingredient).filter(Ingredient.name == name).first():
                        s.add(Ingredient(name=name, unit=unit, is_active=True))
                s.commit()
            elif mode == "Clientes":
                for _, r in df.iterrows():
                    name = str(r.get("name") or r.get("Nome") or "").strip()
                    phone = str(r.get("phone") or r.get("Telefone") or "")
                    address = str(r.get("address") or r.get("Endereço") or "")
                    if name and not s.query(Client).filter(Client.name == name).first():
                        s.add(Client(name=name, phone=phone, address=address, is_active=True))
                s.commit()
            else:  # Produtos
                for _, r in df.iterrows():
                    name = str(r.get("name") or r.get("Nome") or "").strip()
                    if name and not s.query(Product).filter(Product.name == name).first():
                        s.add(Product(name=name, is_active=True))
                s.commit()
        toast_ok("Importação concluída.")

def page_discard():
    st.subheader("Descarte & Vencidos")
    if not can("page.discard"):
        st.info("Sem permissão.")
        return
    with SessionLocal() as s:
        st.markdown("### Registrar descarte por ingrediente")
        ing_opts = cached_ingredients()
        if not ing_opts:
            st.info("Cadastre ingredientes.")
            return
        ing = st.selectbox("Ingrediente", ing_opts, format_func=lambda t: t[1])
        qty = st.number_input("Quantidade", min_value=0.0, step=0.1)
        reason = st.text_input("Motivo", value="Perda/Quebra")
        if st.button("Registrar"):
            if not can("stock.discard"):
                toast_err("Sem permissão.")
            else:
                from db import consume_fifo
                consumed, _ = consume_fifo(s, ing[0], qty, ing[2], order_id=None, note=f"DESCARTE: {reason}")
                s.add(LossEvent(ingredient_id=ing[0], lot_id=None, qty=consumed, reason=reason))
                s.commit()
                toast_ok(f"Descartado {consumed} {ing[2]}.")

        st.markdown("### Perdas registradas (recentes)")
        losses = s.query(LossEvent).order_by(LossEvent.created_at.desc()).limit(200).all()
        rows = [{"Ingrediente": (s.get(Ingredient, l.ingredient_id).name if l.ingredient_id else "?"),
                 "Qtd": l.qty, "Motivo": l.reason, "Quando": l.created_at} for l in losses]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

def page_settings():
    st.subheader("Configurações")
    if not can("page.settings"):
        st.info("Sem permissão.")
        return
    with SessionLocal() as s:
        cfg = get_or_create_default_config(s)
        with st.form("form_cfg"):
            margin = st.number_input("Margem padrão (0.60 = 60%)", min_value=0.0, max_value=10.0, step=0.05, value=float(cfg.margin_default or 0.60))
            msg_prod = st.text_area("Mensagem de Produção (placeholders: {order_id}, {cliente}, {entrega}, {itens}, {obs})", value=cfg.msg_producao or "")
            msg_ready = st.text_area("Mensagem de Pedido Pronto (placeholders: {order_id}, {cliente}, {entrega}, {itens}, {obs})", value=cfg.msg_pronto or "")
            stages_txt = st.text_area("Kanban (JSON array de estágios)", value=cfg.kanban_stages_json or json.dumps(DEFAULT_KANBAN_STAGES))
            fifo_stage = st.text_input("Estágio que consome FIFO", value=cfg.fifo_stage or "EM_PRODUCAO")
            ok = st.form_submit_button("Salvar")
            if ok:
                if not can("settings.update"):
                    toast_err("Sem permissão.")
                else:
                    try:
                        arr = json.loads(stages_txt)
                        assert isinstance(arr, list) and len(arr) > 0
                    except Exception:
                        toast_err("JSON inválido para estágios. Usando padrão.")
                        arr = DEFAULT_KANBAN_STAGES
                    cfg.margin_default = float(margin)
                    cfg.msg_producao = msg_prod
                    cfg.msg_pronto = msg_ready
                    cfg.kanban_stages_json = json.dumps(arr)
                    cfg.fifo_stage = fifo_stage or "EM_PRODUCAO"
                    s.commit()
                    toast_ok("Configurações salvas.")
                    st.rerun()

def page_users():
    st.subheader("Usuários & Acessos")
    if not can("page.users"):
        st.info("Sem permissão.")
        return
    with SessionLocal() as s:
        st.markdown("### Criar usuário")
        with st.form("user_new"):
            username = st.text_input("Usuário")
            name = st.text_input("Nome")
            email = st.text_input("Email")
            pwd = st.text_input("Senha", type="password")
            roles_all = s.query(Role).order_by(Role.name.asc()).all()
            roles_pick = st.multiselect("Papéis", roles_all, format_func=lambda r: r.name)
            ok = st.form_submit_button("Criar")
            if ok:
                if not can("rbac.manage_users"):
                    toast_err("Sem permissão.")
                elif not username or not pwd:
                    toast_err("Preencha usuário e senha.")
                elif s.query(User).filter(User.username==username).first():
                    toast_err("Usuário já existe.")
                else:
                    phash = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                    u = User(username=username, name=name, email=email, password_hash=phash, is_active=True)
                    for r in roles_pick:
                        u.roles.append(r)
                    s.add(u); s.commit()
                    toast_ok("Usuário criado com papéis.")
                    st.rerun()

        st.markdown("### Papéis e permissões")
        roles = s.query(Role).order_by(Role.name.asc()).all()
        colL, colR = st.columns(2)
        with colL:
            st.markdown("#### Criar papel")
            with st.form("role_new"):
                rname = st.text_input("Nome do papel")
                ok = st.form_submit_button("Criar papel")
                if ok:
                    if not can("rbac.manage_roles"):
                        toast_err("Sem permissão.")
                    elif not rname:
                        toast_err("Informe o nome.")
                    elif s.query(Role).filter(Role.name == rname).first():
                        toast_err("Já existe.")
                    else:
                        s.add(Role(name=rname, permissions_json=json.dumps([]))); s.commit()
                        toast_ok("Papel criado.")
                        st.rerun()
        with colR:
            st.markdown("#### Editar permissões do papel")
            role_sel = st.selectbox("Papel", roles, format_func=lambda r: r.name if r else "-", key="role_edit_select")
            if role_sel:
                try:
                    curr = set(json.loads(role_sel.permissions_json or "[]"))
                except Exception:
                    curr = set()
                groups: Dict[str, List[str]] = {}
                for p in sorted(ALL_PERMISSIONS):
                    g = p.split(".")[0]
                    groups.setdefault(g, []).append(p)
                changed = False
                for g, items in groups.items():
                    st.markdown(f"**{g.upper()}**")
                    cols = st.columns(3)
                    for i, perm in enumerate(items):
                        checked = perm in curr
                        newv = cols[i % 3].checkbox(perm, value=checked, key=f"chk_{role_sel.id}_{perm}")
                        if newv != checked:
                            changed = True
                            if newv:
                                curr.add(perm)
                            else:
                                curr.discard(perm)
                if st.button("Salvar permissões"):
                    if not can("rbac.manage_roles"):
                        toast_err("Sem permissão.")
                    else:
                        role_sel.permissions_json = json.dumps(sorted(list(curr)))
                        s.commit()
                        toast_ok("Permissões salvas.")

        st.markdown("### Atribuir/Remover papéis")

        # seleção por ID (evita objetos desanexados)
        user_opts = [(u.id, u.username) for u in s.query(User).order_by(User.username.asc()).all()]
        u_tuple = st.selectbox("Usuário", user_opts, format_func=lambda t: t[1] if t else "-")
        if u_tuple:
            u_sel = s.get(User, u_tuple[0])  # recarrega da sessão ativa, com attach garantido
            st.write("Papéis atuais:", ", ".join([r.name for r in u_sel.roles]) or "—")

            r_opts = [(r.id, r.name) for r in s.query(Role).order_by(Role.name.asc()).all()]
           r_tuple = st.selectbox("Papel", r_opts, format_func=lambda t: t[1] if t else "-", key="role_assign_select")

            cols = st.columns(2)
            if cols[0].button("Atribuir"):
                if not can("rbac.assign_roles"):
                    toast_err("Sem permissão.")
                else:
                    role = s.get(Role, r_tuple[0])
                    if role not in u_sel.roles:
                        u_sel.roles.append(role); s.commit(); toast_ok("Papel atribuído.")
                        if "user" in st.session_state and st.session_state["user"]["id"] == u_sel.id:
                            st.session_state["perms"] = get_user_permissions(s, u_sel)
            if cols[1].button("Remover"):
                if not can("rbac.assign_roles"):
                    toast_err("Sem permissão.")
                else:
                    role = s.get(Role, r_tuple[0])
                    if role in u_sel.roles:
                        u_sel.roles.remove(role); s.commit(); toast_ok("Papel removido.")
                        if "user" in st.session_state and st.session_state["user"]["id"] == u_sel.id:
                            st.session_state["perms"] = get_user_permissions(s, u_sel)

# -----------------------
# Router
# -----------------------
def run_router():
    app_header()
    if not users_exist():
        create_first_admin()
        login_sidebar()
        return
    login_sidebar()
    if "user" not in st.session_state:
        st.info("Faça login para continuar.")
        return
    pages = allowed_pages()
    choice = st.sidebar.selectbox("Páginas", options=pages or ["Dashboard"])
    if choice == "Dashboard":
        page_dashboard()
    elif choice == "Ingredientes":
        page_ingredients()
    elif choice == "Receitas":
        page_recipes()
    elif choice == "Produtos":
        page_products()
    elif choice == "Clientes":
        page_clients()
    elif choice == "Pedidos – Novo":
        page_order_new()
    elif choice == "Pedidos – Kanban":
        page_kanban()
    elif choice == "Pós-venda":
        page_postsale()
    elif choice == "Calendário":
        page_calendar()
    elif choice == "Compras & Estoque":
        page_stock()
    elif choice == "Importação":
        page_import()
    elif choice == "Descarte & Vencidos":
        page_discard()
    elif choice == "Configurações":
        page_settings()
    elif choice == "Usuários & Acessos":
        page_users()
    else:
        st.info("Página indisponível para seu perfil.")

if __name__ == "__main__":
    run_router()
