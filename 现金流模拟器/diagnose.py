import sys
sys.path.append(r"D:\accio\e-commerce\amazon_cashflow")
from cashflow_engine import SKUParams, SimulationParams, CashFlowEngine

def run_diagnostic():
    # 模拟用户截图中的参数
    sku = SKUParams(
        name="Diagnose-SKU",
        launch_units_day=2,      # 截图值
        plateau_units_day=10,    # 截图值
        time_to_plateau_weeks=12, # 截图值
        asp=24.99,               # 截图值
        unit_cogs=8.0,
        production_lead_days=25,
        shipping_lead_days=40,
        target_cover_days=90,
        tacos=0.18,              # 截图显示 18%
    )
    
    sim = SimulationParams(initial_capital=15000, skus=[sku])
    engine = CashFlowEngine(sim)
    
    # 记录详细日志
    days = 365
    p = sim
    bank_cash = p.initial_capital
    amazon_bal = 0.0
    
    history_units = []
    stockout_events = []
    reorder_fail_events = []
    
    sku_states = []
    for sku in p.skus:
        total_lead = sku.production_lead_days + sku.shipping_lead_days
        init_daily = (sku.launch_units_day + sku.plateau_units_day) / 2
        first_units = max(sku.moq, int(init_daily * sku.target_cover_days))
        sku_states.append({
            "inventory": first_units,
            "locked_inflow": [],
            "pending_transfer": [],
            "pending_orders": [],
            "last_reorder_day": -999,
        })
        bank_cash -= (first_units * sku.unit_cogs * (sku.deposit_ratio) + first_units * sku.shipping_cost_per_unit)
        sku_states[-1]["pending_orders"].append({
            "arrive_day": total_lead,
            "units": first_units,
            "balance_due_day": sku.production_lead_days,
            "balance_amount": first_units * sku.unit_cogs * sku.balance_ratio,
            "balance_paid": False,
        })

    print(f"{'Day':>4} | {'Sales':>5} | {'Inv':>6} | {'Transit':>7} | {'Bank':>8} | {'Amz':>7} | {'Action'}")
    print("-" * 75)

    for d in range(days):
        day_units_total = 0
        for i, sku in enumerate(p.skus):
            state = sku_states[i]
            total_lead = sku.production_lead_days + sku.shipping_lead_days
            
            # 入库
            for order in state["pending_orders"]:
                if not order["balance_paid"] and d >= order["balance_due_day"]:
                    bank_cash -= order["balance_amount"]
                    order["balance_paid"] = True
                if d == order["arrive_day"]:
                    state["inventory"] += order["units"]
                    print(f"{d:>4} | {'-':>5} | {state['inventory']:>6} | {'-':>7} | {bank_cash:>8.0f} | {'-':>7} | [ARRIVAL] +{order['units']}")

            # 销量
            expected = engine.get_daily_units(sku, d)
            units_sold = min(expected, state["inventory"])
            if state["inventory"] <= 0 and expected > 0:
                stockout_events.append(d)
                # print(f"{d:>4} | {expected:>5.1f} | {state['inventory']:>6} | {'-':>7} | {'-':>8} | {'-':>7} | [STOCKOUT!]")
            state["inventory"] -= units_sold
            day_units_total += units_sold
            
            # DD+7 (简化)
            unlock_day = d + 11
            net_rev = units_sold * sku.asp * 0.5 # 粗略
            state["locked_inflow"].append((unlock_day, net_rev))
            
            for (u, a) in state["locked_inflow"]:
                if u <= d: amazon_bal += a
            state["locked_inflow"] = [x for x in state["locked_inflow"] if x[0] > d]
            
            # 补货逻辑
            window = history_units[-14:] if history_units else [units_sold]
            avg_units = max(sum(window)/len(window), units_sold, 0.01)
            in_transit = sum(o["units"] for o in state["pending_orders"])
            effective_stock = state["inventory"] + in_transit
            days_of_stock = effective_stock / avg_units
            
            cooldown = total_lead
            if days_of_stock < sku.target_cover_days and d - state["last_reorder_day"] > cooldown:
                need_units = max(sku.moq, int(avg_units * sku.target_cover_days) - effective_stock)
                dep = need_units * sku.unit_cogs * 0.3
                if bank_cash >= dep:
                    bank_cash -= dep
                    state["last_reorder_day"] = d
                    state["pending_orders"].append({
                        "arrive_day": d + total_lead,
                        "units": need_units,
                        "balance_due_day": d + sku.production_lead_days,
                        "balance_amount": need_units * sku.unit_cogs * 0.7 + need_units * 1.5,
                        "balance_paid": False,
                    })
                    print(f"{d:>4} | {units_sold:>5.1f} | {state['inventory']:>6} | {in_transit:>7} | {bank_cash:>8.0f} | {amazon_bal:>7.0f} | [REORDER] {need_units} units")

        history_units.append(day_units_total)

if __name__ == "__main__":
    run_diagnostic()
