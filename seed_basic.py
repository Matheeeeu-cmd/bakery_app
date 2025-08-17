# seed_basic.py
# Semente simples para demonstração (idempotente).
from db import (
    init_db, make_engine, make_sessionmaker,
    Ingredient, Recipe, RecipeItem, Product, Client, Order, OrderItem,
    create_lot, estimate_product_unit_cost, get_or_create_default_config
)

def run():
    engine = init_db(make_engine())
    SessionLocal = make_sessionmaker(engine)
    with SessionLocal() as s:
        # Ingredientes
        ing_names = [("Farinha de Trigo","g"), ("Açúcar","g"), ("Chocolate","g")]
        idmap = {}
        for name, unit in ing_names:
            obj = s.query(Ingredient).filter(Ingredient.name==name).first()
            if not obj:
                obj = Ingredient(name=name, unit=unit, is_active=True)
                s.add(obj); s.flush()
            idmap[name] = obj.id
        # Lotes
        create_lot(s, idmap["Farinha de Trigo"], 10000, "g", 0.010)  # R$0,01/g
        create_lot(s, idmap["Açúcar"], 8000, "g", 0.008)
        create_lot(s, idmap["Chocolate"], 5000, "g", 0.030)
        # Receita: Bolo Base
        r = s.query(Recipe).filter(Recipe.name=="Bolo Base").first()
        if not r:
            r = Recipe(name="Bolo Base", yield_qty=1.0, unit="un", is_active=True)
            s.add(r); s.flush()
            s.add_all([
                RecipeItem(recipe_id=r.id, ingredient_id=idmap["Farinha de Trigo"], qty=300, item_type="peso"),
                RecipeItem(recipe_id=r.id, ingredient_id=idmap["Açúcar"], qty=150, item_type="peso"),
                RecipeItem(recipe_id=r.id, ingredient_id=idmap["Chocolate"], qty=100, item_type="peso"),
            ])
        # Produto
        p = s.query(Product).filter(Product.name=="Bolo de Chocolate").first()
        if not p:
            p = Product(name="Bolo de Chocolate", recipe_id=r.id, is_active=True)
            s.add(p); s.flush()
        # Cliente
        c = s.query(Client).filter(Client.name=="Cliente Exemplo").first()
        if not c:
            c = Client(name="Cliente Exemplo", phone="(11) 99999-0000", address="Rua A, 123", is_active=True)
            s.add(c); s.flush()
        # Pedido
        o = s.query(Order).filter(Order.client_id==c.id).first()
        if not o:
            cfg = get_or_create_default_config(s)
            cost = estimate_product_unit_cost(s, p)
            price = cost * (1.0 + (cfg.margin_default or 0.60))
            o = Order(client_id=c.id, status="NOVO", paid=False, delivery_date=None, obs="Pedido de demonstração")
            s.add(o); s.flush()
            s.add(OrderItem(order_id=o.id, product_id=p.id, qty=2, unit_price=price, unit_cost_snapshot=cost))
            s.flush()
            o.total = 2*price
        s.commit()
    print("Seed concluída.")

if __name__ == "__main__":
    run()
