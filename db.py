# db.py
# ORM e utilitários de negócio/estoque/RBAC

from __future__ import annotations
import json
import os
import datetime as dt
from typing import Dict, List, Optional, Tuple, Union, Set

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey,
    Text, Date, Table, text, inspect
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# ---------------------------------------------------------------------
# Base / Constantes
# ---------------------------------------------------------------------
Base = declarative_base()

DEFAULT_KANBAN_STAGES = [
    "NOVO","PRA_PRODUCAO","EM_PRODUCAO","EMBALAGEM","PRONTO_RETIRADA","ENTREGUE","CANCELADO"
]

# ---------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------
def make_engine(database_url: Optional[str] = None):
    url = database_url or os.getenv("DATABASE_URL") or "sqlite:///bakery.db"
    kw = dict(echo=False, future=True, pool_pre_ping=True)
    # Força SSL no Postgres (Neon) se não houver parâmetro
    if url.startswith("postgres") and "sslmode=" not in url:
        if "?" in url:
            url = url + "&sslmode=require"
        else:
            url = url + "?sslmode=require"
    engine = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def make_sessionmaker(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

# ---------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------
ALL_PERMISSIONS: Set[str] = set([
    # páginas
    "page.dashboard","page.ingredients","page.recipes","page.products","page.clients",
    "page.orders.new","page.orders.kanban","page.postsale","page.calendar",
    "page.stock","page.import","page.discard","page.settings","page.users",
    # ações ingredientes
    "ingredient.create","ingredient.update","ingredient.delete","ingredient.buy_lot",
    # ações receitas
    "recipe.create","recipe.update","recipe.delete",
    # ações produtos
    "product.create","product.update","product.delete",
    # ações clientes
    "client.create","client.update","client.delete",
    # ações pedidos
    "order.create","order.update","order.delete","order.mark_paid","order.unmark_paid",
    "order.move_stage","order.consume_fifo","order.cancel",
    # ações estoque
    "stock.adjust","stock.discard","stock.discard_expired","stock.create_purchase",
    # importação
    "import.run",
    # configurações
    "settings.update",
    # usuários & acessos
    "rbac.manage_users","rbac.manage_roles","rbac.assign_roles",
])

# ---------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------
class Config(Base):
    __tablename__ = "config"
    id = Column(Integer, primary_key=True)
    margin_default = Column(Float, default=0.60, nullable=False)
    msg_producao = Column(Text, default="Pedido {order_id} do cliente {cliente} entrou em produção. Itens: {itens}. Obs: {obs}")
    msg_pronto = Column(Text, default="Pedido {order_id} do cliente {cliente} está pronto para {entrega}. Itens: {itens}. Obs: {obs}")
    kanban_stages_json = Column(Text, default=lambda: json.dumps(DEFAULT_KANBAN_STAGES))
    fifo_stage = Column(String(64), default="EM_PRODUCAO")  # estágio que dispara o consumo FIFO
    created_at = Column(DateTime, default=dt.datetime.utcnow)

# associação usuário <-> papel
user_role_table = Table(
    "user_role",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
)

class Role(Base):
    __tablename__ = "role"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    permissions_json = Column(Text, default=lambda: json.dumps(sorted(list(ALL_PERMISSIONS))))
    users = relationship("User", secondary=user_role_table, back_populates="roles")

class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(120))
    email = Column(String(200))
    password_hash = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    roles = relationship("Role", secondary=user_role_table, back_populates="users")

class LoginToken(Base):
    __tablename__ = "login_token"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

def create_login_token(session: Session, user_id: int) -> str:
    import secrets
    tok = secrets.token_hex(24)
    session.add(LoginToken(user_id=user_id, token=tok))
    session.commit()
    return tok

def get_user_by_token(session: Session, token: str) -> Optional[User]:
    lt = session.query(LoginToken).filter(LoginToken.token == token).first()
    return session.get(User, lt.user_id) if lt else None

def delete_token(session: Session, token: str):
    session.query(LoginToken).filter(LoginToken.token == token).delete()
    session.commit()

class Supplier(Base):
    __tablename__ = "supplier"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False)
    contact = Column(String(200))
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    manual_purchases = relationship("ManualPurchase", back_populates="supplier")

class Ingredient(Base):
    __tablename__ = "ingredient"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False)
    unit = Column(String(10), default="g")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    prices = relationship("IngredientPrice", back_populates="ingredient", cascade="all, delete-orphan")
    stock_lots = relationship("StockLot", back_populates="ingredient", cascade="all, delete-orphan")

class IngredientPrice(Base):
    __tablename__ = "ingredient_price"
    id = Column(Integer, primary_key=True)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id", ondelete="CASCADE"), nullable=False)
    price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    ingredient = relationship("Ingredient", back_populates="prices")

class StockLot(Base):
    __tablename__ = "stock_lot"
    id = Column(Integer, primary_key=True)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id", ondelete="CASCADE"), nullable=False)
    qty_total = Column(Float, nullable=False)        # quantidade comprada
    qty_remaining = Column(Float, nullable=False)    # quanto ainda resta
    unit = Column(String(20), default="g")
    buy_price = Column(Float, nullable=False)        # preço por unidade
    best_before = Column(Date)                       # validade (opcional)
    note = Column(String(200))
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    ingredient = relationship("Ingredient", back_populates="stock_lots")
    moves = relationship("StockMove", back_populates="lot", cascade="all, delete-orphan")

class Recipe(Base):
    __tablename__ = "recipe"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False)
    yield_qty = Column(Float, default=1.0)           # rendimento total
    unit = Column(String(20), default="un")          # unidade do rendimento
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    items = relationship(
        "RecipeItem",
        back_populates="recipe",
        foreign_keys="RecipeItem.recipe_id",
        cascade="all, delete-orphan",
    )

class RecipeItem(Base):
    __tablename__ = "recipe_item"
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipe.id", ondelete="CASCADE"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id", ondelete="SET NULL"))
    sub_recipe_id = Column(Integer, ForeignKey("recipe.id", ondelete="SET NULL"))
    qty = Column(Float, nullable=False)
    item_type = Column(String(20), default="peso")  # "peso" ou "unidade"

    recipe = relationship("Recipe", back_populates="items", foreign_keys=[recipe_id])
    ingredient = relationship("Ingredient", foreign_keys=[ingredient_id])
    sub_recipe = relationship("Recipe", foreign_keys=[sub_recipe_id])

class Product(Base):
    __tablename__ = "product"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False)
    recipe_id = Column(Integer, ForeignKey("recipe.id", ondelete="SET NULL"))
    is_active = Column(Boolean, default=True)
    price_manual = Column(Float)                     # se preenchido, usa manual
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    recipe = relationship("Recipe")
    order_items = relationship("OrderItem", back_populates="product")

class Client(Base):
    __tablename__ = "client"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, index=True)
    phone = Column(String(50))
    address = Column(String(200))
    notes = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    orders = relationship("Order", back_populates="client")

class Order(Base):
    __tablename__ = "order"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("client.id", ondelete="SET NULL"))
    status = Column(String(64), default="NOVO", index=True)
    paid = Column(Boolean, default=False)
    delivery_date = Column(Date)
    total = Column(Float, default=0.0)
    obs = Column(Text)
    pos_stage = Column(String(32), default="ENTREGUE")
    # >>> ADICIONE ESTAS 2 LINHAS <<<
    pos1_date = Column(Date)
    pos2_date = Column(Date)
    # <<<
    canceled_reason = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    client = relationship("Client")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete")
    moves = relationship("StockMove", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_item"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("order.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("product.id", ondelete="SET NULL"))
    qty = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    unit_cost_snapshot = Column(Float, default=0.0)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")

class StockMove(Base):
    __tablename__ = "stock_move"
    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("stock_lot.id", ondelete="SET NULL"))
    ingredient_id = Column(Integer, ForeignKey("ingredient.id", ondelete="SET NULL"))
    move_type = Column(String(12), default="OUT")    # IN, OUT, ADJUST, LOSS
    qty = Column(Float, nullable=False)
    unit = Column(String(20), default="g")
    cost = Column(Float, default=0.0)                # custo (para OUT/LOSS)
    order_id = Column(Integer, ForeignKey("order.id", ondelete="SET NULL"))
    notes = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    lot = relationship("StockLot", back_populates="moves")
    order = relationship("Order", back_populates="moves")
    ingredient = relationship("Ingredient")

class LossEvent(Base):
    __tablename__ = "loss_event"
    id = Column(Integer, primary_key=True)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id", ondelete="SET NULL"))
    lot_id = Column(Integer, ForeignKey("stock_lot.id", ondelete="SET NULL"))
    qty = Column(Float, nullable=False)
    reason = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    ingredient = relationship("Ingredient")
    lot = relationship("StockLot")

class ManualPurchase(Base):
    __tablename__ = "manual_purchase"
    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("supplier.id", ondelete="SET NULL"))
    total = Column(Float, default=0.0)

    # usados por "Sugestões de compra"
    is_suggestion = Column(Boolean, default=False, nullable=False)
    title = Column(String(200))
    completed_at = Column(DateTime)

    created_at = Column(DateTime, default=dt.datetime.utcnow)

    supplier = relationship("Supplier", back_populates="manual_purchases")
    items = relationship("ManualPurchaseItem", back_populates="purchase", cascade="all, delete-orphan")

class ManualPurchaseItem(Base):
    __tablename__ = "manual_purchase_item"
    id = Column(Integer, primary_key=True)
    purchase_id = Column(Integer, ForeignKey("manual_purchase.id", ondelete="CASCADE"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id", ondelete="SET NULL"))
    qty = Column(Float, default=0.0)
    unit = Column(String(10), default="g")
    price = Column(Float, default=0.0)

    purchase = relationship("ManualPurchase", back_populates="items")
    ingredient = relationship("Ingredient")

# ---------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------
def seed_default_roles(session: Session):
    """Cria papéis admin, staff e seller idempotentemente."""
    existing = {r.name: r for r in session.query(Role).all()}

    if "admin" not in existing:
        admin = Role(name="admin", permissions_json=json.dumps(sorted(list(ALL_PERMISSIONS))))
        session.add(admin)

    staff_perms = {
        "page.dashboard","page.ingredients","page.recipes","page.products",
        "page.clients","page.orders.new","page.orders.kanban","page.postsale","page.calendar",
        "page.stock","page.discard",
        "ingredient.create","ingredient.update","ingredient.buy_lot",
        "recipe.create","recipe.update",
        "product.create","product.update",
        "client.create","client.update",
        "order.create","order.update","order.mark_paid","order.unmark_paid",
        "order.move_stage","order.consume_fifo","order.cancel",
        "stock.discard","stock.discard_expired",
    }
    if "staff" not in existing:
        staff = Role(name="staff", permissions_json=json.dumps(sorted(list(staff_perms))))
        session.add(staff)

    seller_perms = {
        "page.dashboard","page.products","page.clients","page.orders.new","page.orders.kanban","page.calendar",
        "client.create","client.update",
        "order.create","order.update","order.mark_paid","order.unmark_paid","order.move_stage","order.cancel",
    }
    if "seller" not in existing:
        seller = Role(name="seller", permissions_json=json.dumps(sorted(list(seller_perms))))
        session.add(seller)

    session.commit()

def _normalize_user_ref(session: Session, user_ref: Union[int, str, User]) -> Optional[User]:
    if isinstance(user_ref, User):
        return user_ref
    if isinstance(user_ref, int):
        return session.get(User, user_ref)
    if isinstance(user_ref, str):
        return session.query(User).filter(User.username == user_ref).first()
    return None

def get_user_permissions(session: Session, user_ref: Union[int, str, User]) -> Set[str]:
    """Aceita id, username ou objeto User. Admin recebe TODAS as permissões."""
    user = _normalize_user_ref(session, user_ref)
    if not user or not user.is_active:
        return set()
    perms: Set[str] = set()
    for role in user.roles:
        try:
            role_perms = set(json.loads(role.permissions_json or "[]"))
        except Exception:
            role_perms = set()
        perms |= role_perms
    if any(r.name == "admin" for r in user.roles):
        perms = set(ALL_PERMISSIONS)
    return perms

# ---------------------------------------------------------------------
# Config default
# ---------------------------------------------------------------------
def get_or_create_default_config(session: Session) -> Config:
    cfg = session.query(Config).first()
    if not cfg:
        cfg = Config()
        session.add(cfg)
        session.commit()
    # fallback para JSON inválido ou vazio
    try:
        stages = json.loads(cfg.kanban_stages_json or "[]")
        assert isinstance(stages, list) and len(stages) > 0
    except Exception:
        cfg.kanban_stages_json = json.dumps(DEFAULT_KANBAN_STAGES)
        session.commit()
    if not cfg.fifo_stage:
        cfg.fifo_stage = "EM_PRODUCAO"
        session.commit()
    return cfg

# ---------------------------------------------------------------------
# Migrações idempotentes
# ---------------------------------------------------------------------
from sqlalchemy import text

def table_exists(engine, table_name: str) -> bool:
    insp = inspect(engine)
    try:
        return table_name in insp.get_table_names()
    except Exception:
        return False

def column_exists(engine, table_name: str, column_name: str) -> bool:
    insp = inspect(engine)
    try:
        cols = insp.get_columns(table_name)
    except Exception:
        return False
    return any(c.get("name") == column_name for c in cols)

def run_safe_migrations(engine):
    """Migrações idempotentes para Postgres/SQLite."""
    with engine.begin() as conn:
        # ---------- CONFIG ----------
        if table_exists(engine, "config"):
            if not column_exists(engine, "config", "kanban_stages_json"):
                conn.execute(text('ALTER TABLE "config" ADD COLUMN kanban_stages_json TEXT'))
            if not column_exists(engine, "config", "fifo_stage"):
                conn.execute(text('ALTER TABLE "config" ADD COLUMN fifo_stage VARCHAR(64)'))

        # ---------- MANUAL_PURCHASE ----------
        if table_exists(engine, "manual_purchase"):
            if not column_exists(engine, "manual_purchase", "is_suggestion"):
                # BOOLEAN DEFAULT FALSE (não 0) para Postgres
                conn.execute(text('ALTER TABLE "manual_purchase" ADD COLUMN is_suggestion BOOLEAN DEFAULT FALSE'))
            if not column_exists(engine, "manual_purchase", "title"):
                conn.execute(text('ALTER TABLE "manual_purchase" ADD COLUMN title VARCHAR(200)'))
            if not column_exists(engine, "manual_purchase", "completed_at"):
                conn.execute(text('ALTER TABLE "manual_purchase" ADD COLUMN completed_at TIMESTAMP'))

        # ---------- RECIPE / RECIPE_ITEM (garante que existam) ----------
        if not table_exists(engine, "recipe"):
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS recipe (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(120) UNIQUE NOT NULL,
                    yield_qty DOUBLE PRECISION DEFAULT 1.0 NOT NULL,
                    unit VARCHAR(20) DEFAULT 'un',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP
                )
            """))
        if not table_exists(engine, "recipe_item"):
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS recipe_item (
                    id SERIAL PRIMARY KEY,
                    recipe_id INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
                    ingredient_id INTEGER REFERENCES ingredient(id) ON DELETE SET NULL,
                    sub_recipe_id INTEGER REFERENCES recipe(id) ON DELETE SET NULL,
                    qty DOUBLE PRECISION NOT NULL,
                    item_type VARCHAR(20) DEFAULT 'peso'
                )
            """))

        # ---------- PRODUCT.recipe_id ----------
        if table_exists(engine, "product"):
            if not column_exists(engine, "product", "recipe_id"):
                conn.execute(text('ALTER TABLE "product" ADD COLUMN recipe_id INTEGER REFERENCES recipe(id) ON DELETE SET NULL'))

        # ---------- ORDER.pos_stage + POS1/POS2 ----------
        if table_exists(engine, "order"):
            if not column_exists(engine, "order", "pos_stage"):
                conn.execute(text("ALTER TABLE \"order\" ADD COLUMN pos_stage VARCHAR(32) DEFAULT 'ENTREGUE'"))
            if not column_exists(engine, "order", "pos1_date"):
                conn.execute(text('ALTER TABLE "order" ADD COLUMN pos1_date DATE'))
            if not column_exists(engine, "order", "pos2_date"):
                conn.execute(text('ALTER TABLE "order" ADD COLUMN pos2_date DATE'))


# ---------------------------------------------------------------------
# Helpers de Estoque FIFO e custos
# ---------------------------------------------------------------------
def create_lot(session: Session, ingredient_id: int, qty: float, unit: str, unit_price: float,
               best_before: Optional[dt.date]=None, note: Optional[str]=None) -> StockLot:
    lot = StockLot(
        ingredient_id=ingredient_id,
        qty_total=qty,
        qty_remaining=qty,
        unit=unit,
        buy_price=unit_price,
        best_before=best_before,
        note=note
    )
    session.add(lot)
    session.flush()
    session.add(StockMove(
        lot_id=lot.id, ingredient_id=ingredient_id, move_type="IN",
        qty=qty, unit=unit, cost=qty*unit_price, notes="Compra/Lote"
    ))
    session.commit()
    return lot

def average_cost(session: Session, ingredient_id: int) -> float:
    """Custo médio ponderado pelos lotes restantes; se vazio, usa último preço cadastrado."""
    lots = session.query(StockLot).filter(
        StockLot.ingredient_id == ingredient_id,
        StockLot.qty_remaining > 0
    ).all()
    if lots:
        tot_qty = sum(l.qty_remaining for l in lots)
        tot_val = sum(l.qty_remaining * l.buy_price for l in lots)
        if tot_qty > 0:
            return tot_val / tot_qty
    p = session.query(IngredientPrice).filter(
        IngredientPrice.ingredient_id==ingredient_id
    ).order_by(IngredientPrice.created_at.desc()).first()
    return p.price if p else 0.0

def _consume_from_lot(session: Session, lot: StockLot, qty: float, order_id: Optional[int], note: str="Consumo FIFO") -> float:
    taken = min(qty, lot.qty_remaining)
    if taken <= 0:
        return 0.0
    lot.qty_remaining -= taken
    session.add(StockMove(
        lot_id=lot.id, ingredient_id=lot.ingredient_id, move_type="OUT",
        qty=taken, unit=lot.unit, cost=taken*lot.buy_price, order_id=order_id, notes=note
    ))
    return taken

def consume_fifo(session: Session, ingredient_id: int, qty_needed: float, unit: str,
                 order_id: Optional[int]=None, note:str="Consumo FIFO") -> Tuple[float, float]:
    """Consome por FIFO. Retorna (consumido, faltante)."""
    remaining = max(qty_needed, 0.0)
    lots = session.query(StockLot).filter(
        StockLot.ingredient_id==ingredient_id,
        StockLot.qty_remaining>0
    ).order_by(
        StockLot.best_before.is_(None),
        StockLot.best_before.asc(),
        StockLot.created_at.asc()
    ).all()
    for lot in lots:
        if remaining <= 1e-12:
            break
        taken = _consume_from_lot(session, lot, remaining, order_id, note)
        remaining -= taken
    consumed = qty_needed - max(remaining, 0.0)
    session.commit()
    return consumed, max(remaining, 0.0)

def discard_from_lot(session: Session, lot_id: int, qty: float, reason: str="Descartado") -> float:
    lot = session.get(StockLot, lot_id)
    if not lot or qty <= 0:
        return 0.0
    taken = min(qty, lot.qty_remaining)
    lot.qty_remaining -= taken
    session.add(StockMove(
        lot_id=lot.id, ingredient_id=lot.ingredient_id, move_type="LOSS",
        qty=taken, unit=lot.unit, cost=taken*lot.buy_price, notes=reason
    ))
    session.add(LossEvent(
        ingredient_id=lot.ingredient_id, lot_id=lot.id, qty=taken, reason=reason
    ))
    session.commit()
    return taken

def discard_expired(session: Session, ref_date: Optional[dt.date]=None) -> List[Tuple[int, float]]:
    """Descarta lotes vencidos. Retorna [(lot_id, descartado_qty), ...]."""
    today = ref_date or dt.date.today()
    out: List[Tuple[int,float]] = []
    lots = session.query(StockLot).filter(
        StockLot.best_before.isnot(None),
        StockLot.qty_remaining>0,
        StockLot.best_before < today
    ).all()
    for lot in lots:
        q = lot.qty_remaining
        if q > 0:
            taken = discard_from_lot(session, lot.id, q, reason=f"Vencido em {lot.best_before}")
            out.append((lot.id, taken))
    return out

# ---------------------------------------------------------------------
# Explosão de receita / custos
# ---------------------------------------------------------------------
def explode_recipe(session: Session, recipe_id: int, factor: float = 1.0) -> Dict[int, float]:
    """
    Calcula insumos base (ingredient_id -> quantidade) para produzir 'factor' * rendimento da receita.
    Considera sub-receitas recursivamente.
    """
    req: Dict[int, float] = {}
    recipe = session.get(Recipe, recipe_id)
    if not recipe or recipe.yield_qty == 0:
        return req
    scale = factor / recipe.yield_qty
    for it in recipe.items:
        if it.ingredient_id:
            req[it.ingredient_id] = req.get(it.ingredient_id, 0.0) + (it.qty * scale)
        elif it.sub_recipe_id:
            sub_req = explode_recipe(session, it.sub_recipe_id, factor=it.qty * scale)
            for k, v in sub_req.items():
                req[k] = req.get(k, 0.0) + v
    return req

def required_ingredients_for_order(session: Session, order: Order) -> Dict[int, float]:
    """Soma insumos por todos os itens do pedido."""
    totals: Dict[int,float] = {}
    for oi in order.items:
        if not oi.product or not oi.product.recipe_id:
            continue
        per_unit = explode_recipe(session, oi.product.recipe_id, factor=1.0)
        for ing_id, qty in per_unit.items():
            totals[ing_id] = totals.get(ing_id, 0.0) + qty * oi.qty
    return totals

def ingredient_shortages(session: Session, order: Order) -> List[Tuple[Ingredient, float]]:
    """Retorna [(ingrediente, faltante_qty>0)]."""
    req = required_ingredients_for_order(session, order)
    shortages: List[Tuple[Ingredient,float]] = []
    for ing_id, need in req.items():
        available = session.query(StockLot).with_entities(StockLot.qty_remaining)\
            .filter(StockLot.ingredient_id==ing_id, StockLot.qty_remaining>0).all()
        available_qty = sum(q[0] for q in available)
        if need > available_qty + 1e-9:
            shortages.append((session.get(Ingredient, ing_id), need - available_qty))
    return shortages

def consume_fifo_for_order(session: Session, order: Order) -> Dict[int, Tuple[float,float]]:
    """Consome estoque para todos os ingredientes do pedido. Retorna {ingredient_id: (consumido, faltante)}"""
    res: Dict[int, Tuple[float,float]] = {}
    req = required_ingredients_for_order(session, order)
    for ing_id, qty in req.items():
        ing = session.get(Ingredient, ing_id)
        consumed, missing = consume_fifo(session, ing_id, qty, ing.unit if ing else "g",
                                         order_id=order.id, note=f"Consumo pedido #{order.id}")
        res[ing_id] = (consumed, missing)
    return res

def estimate_product_unit_cost(session: Session, product: Product) -> float:
    """Custo unitário estimado baseado no custo médio dos ingredientes."""
    if not product or not product.recipe_id:
        return 0.0
    req = explode_recipe(session, product.recipe_id, factor=1.0)
    total = 0.0
    for ing_id, qty in req.items():
        unit_cost = average_cost(session, ing_id)
        total += qty * unit_cost
    return total

# ---------------------------------------------------------------------
# Inicialização do DB
# ---------------------------------------------------------------------
def init_db(engine):
    # cria tabelas
    Base.metadata.create_all(engine)
    # migra colunas que possam faltar
    run_safe_migrations(engine)
    # seeds
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as session:
        seed_default_roles(session)
        get_or_create_default_config(session)
        session.commit()
    return engine
