"""
Amazon Cash Flow Simulator — 计算引擎 v3（上帝视角 MRP 版）
==========================================================
核心逻辑：
1. God's View: 启动时预生成 365 天销量序列，消除预测误差。
2. MRP Replenishment: 基于未来提前期内的真实需求缺口进行补货。
3. 双账户财务：Amazon 账户 (DD+7) 与 银行账户 (采购/固定) 严格分离。
"""

import math
from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class SKUParams:
    name: str = "SKU-1"
    launch_units_day: float = 3.0
    plateau_units_day: float = 15.0
    time_to_plateau_weeks: float = 8.0
    asp: float = 29.99
    plateau_drift: float = 0.0
    seasonality: List[float] = field(default_factory=lambda: [1.0] * 12)
    
    unit_cogs: float = 8.0
    production_lead_days: int = 25
    shipping_lead_days: int = 40
    deposit_ratio: float = 0.30
    balance_ratio: float = 0.70
    shipping_cost_per_unit: float = 1.5
    
    target_cover_days: int = 90  # 目标覆盖天数
    moq: int = 200
    
    referral_fee_rate: float = 0.15
    fba_fee_per_unit: float = 4.50
    tacos: float = 0.12
    misc_loss_rate: float = 0.05
    
    avg_delivery_days: int = 4
    dd_reserve_days: int = 7
    disbursement_cycle: int = 1
    bank_lag_days: int = 4
    account_reserve_rate: float = 0.04

@dataclass
class SimulationParams:
    initial_capital: float = 20000.0
    simulation_days: int = 365
    start_month: int = 1
    monthly_fixed_cost: float = 500.0
    exchange_rate: float = 7.2
    financing_annual_rate: float = 0.0
    skus: List[SKUParams] = field(default_factory=lambda: [SKUParams()])

class CashFlowEngine:
    def __init__(self, params: SimulationParams):
        self.p = params
        self.days = params.simulation_days
        # 上帝视角：预生成销量
        self.full_sales_schedule = self._precompute_all_sales()

    def _precompute_all_sales(self) -> List[List[float]]:
        """预生成所有 SKU 365 天的真实日销量序列"""
        schedule = []
        for sku in self.p.skus:
            sku_sales = []
            ramp_days = sku.time_to_plateau_weeks * 7
            for d in range(self.days + 200): # 多生成一些防止越界
                if d < ramp_days:
                    progress = d / ramp_days
                    factor = 1 / (1 + math.exp(-6 * (progress - 0.5)))
                    base = sku.launch_units_day + (sku.plateau_units_day - sku.launch_units_day) * factor
                else:
                    months_past = (d - ramp_days) / 30
                    drift = (1 + sku.plateau_drift) ** months_past
                    base = sku.plateau_units_day * drift
                
                cal_month = (self.p.start_month - 1 + d // 30) % 12
                sku_sales.append(max(0.0, base * sku.seasonality[cal_month]))
            schedule.append(sku_sales)
        return schedule

    def run(self) -> Dict:
        days = self.days
        bank_cash = self.p.initial_capital
        amazon_bal = 0.0
        
        # 结果记录
        res = {
            "daily_bank_cash": [0.0]*days, "daily_amazon_bal": [0.0]*days,
            "daily_units": [0.0]*days, "daily_revenue": [0.0]*days,
            "daily_inventory_val": [0.0]*days, "daily_locked_rev": [0.0]*days,
            "daily_bank_outflow": [0.0]*days, "daily_inflow": [0.0]*days,
            "daily_total_assets": [0.0]*days,
            "daily_inventory": [0.0]*days, "daily_in_transit": [0.0]*days,
            "daily_ads_from_amz": [0.0]*days, "daily_ads_from_bank": [0.0]*days,
            "stockout_events": [], "reorder_fail_events": [],
            "logs": []  # 详细诊断日志
        }

        sku_states = []
        for i, sku in enumerate(self.p.skus):
            total_lead = sku.production_lead_days + sku.shipping_lead_days
            # 初始备货：覆盖第一个 LeadTime + 目标天数
            init_demand = sum(self.full_sales_schedule[i][:total_lead + sku.target_cover_days])
            init_units = max(sku.moq, int(init_demand))
            
            sku_states.append({
                "inventory": init_units,
                "locked_inflow": [], "pending_transfer": [], "pending_orders": [],
                "last_reorder_day": -999
            })
            
            # 初始支出
            dep = init_units * sku.unit_cogs * sku.deposit_ratio
            ship = init_units * sku.shipping_cost_per_unit
            bank_cash -= (dep + ship)
            res["daily_bank_outflow"][0] += (dep + ship)
            
            sku_states[i]["pending_orders"].append({
                "arrive_day": total_lead, "units": init_units,
                "balance_due_day": sku.production_lead_days,
                "balance_amount": init_units * sku.unit_cogs * sku.balance_ratio,
                "balance_paid": False
            })

        for d in range(days):
            day_bank_out = 0.0
            day_units = 0.0
            day_rev = 0.0
            day_ads_amz = 0.0
            day_ads_bank = 0.0

            for i, sku in enumerate(self.p.skus):
                st = sku_states[i]
                total_lead = sku.production_lead_days + sku.shipping_lead_days

                # 1. 采购尾款支付 & 入库
                for o in st["pending_orders"]:
                    if not o["balance_paid"] and d >= o["balance_due_day"]:
                        bank_cash -= o["balance_amount"]
                        day_bank_out += o["balance_amount"]
                        o["balance_paid"] = True
                    if d == o["arrive_day"]:
                        st["inventory"] += o["units"]
                st["pending_orders"] = [o for o in st["pending_orders"] if d < o["arrive_day"]]

                # 2. 销售 (MRP 版：依然受库存物理上限限制)
                demand_today = self.full_sales_schedule[i][d]
                sold = min(demand_today, st["inventory"])
                if st["inventory"] <= 0 and demand_today > 0:
                    res["stockout_events"].append((d, "库存耗尽"))
                st["inventory"] -= sold
                day_units += sold
                day_rev += sold * sku.asp

                # 3. 财务流水
                gross = sold * sku.asp
                net = gross * (1 - sku.referral_fee_rate - sku.misc_loss_rate) - sold * sku.fba_fee_per_unit
                net *= (1 - sku.account_reserve_rate)
                
                unlock_day = d + sku.avg_delivery_days + sku.dd_reserve_days
                st["locked_inflow"].append((unlock_day, net))
                
                # 4. 广告扣费
                ads = gross * sku.tacos
                if amazon_bal >= ads:
                    amazon_bal -= ads
                    day_ads_amz += ads
                else:
                    gap = ads - amazon_bal
                    day_ads_amz += amazon_bal
                    amazon_bal = 0.0
                    bank_cash -= gap
                    day_bank_out += gap
                    day_ads_bank += gap

                # 5. 解锁 & 提现
                for (u, a) in st["locked_inflow"]:
                    if u <= d: amazon_bal += a
                st["locked_inflow"] = [x for x in st["locked_inflow"] if x[0] > d]

                if d % sku.disbursement_cycle == 0:
                    min_hold = gross * sku.tacos
                    transfer = max(0, amazon_bal - min_hold)
                    if transfer > 0:
                        amazon_bal -= transfer
                        arr_day = d + sku.bank_lag_days
                        if arr_day < days: st["pending_transfer"].append((arr_day, transfer))
                
                for (arr, amt) in st["pending_transfer"]:
                    if arr <= d:
                        bank_cash += amt
                        res["daily_inflow"][d] += amt
                st["pending_transfer"] = [x for x in st["pending_transfer"] if x[0] > d]

                # 6. 【上帝视角 MRP 补货算法】
                lookahead_start = d + total_lead
                lookahead_end = lookahead_start + sku.target_cover_days
                future_demand = sum(self.full_sales_schedule[i][d:lookahead_end])
                
                in_transit = sum(o["units"] for o in st["pending_orders"])
                # 需求缺口 = 覆盖从今天到“货到后再用TargetCover天”的总需求 - 已有资源
                gap_units = future_demand - (st["inventory"] + in_transit)
                
                log_msg = f"Day {d:03d} | 库存: {st['inventory']:>4.0f} | 在途: {in_transit:>4d} | 未来需求({d}-{lookahead_end}): {future_demand:>4.0f} | 缺口: {gap_units:>4.0f}"
                
                if gap_units > 0 and d - st["last_reorder_day"] >= 7:
                    order_units = max(sku.moq, int(gap_units))
                    dep = order_units * sku.unit_cogs * sku.deposit_ratio
                    ship = order_units * sku.shipping_cost_per_unit
                    
                    if bank_cash >= (dep + ship):
                        bank_cash -= (dep + ship)
                        day_bank_out += (dep + ship)
                        st["last_reorder_day"] = d
                        st["pending_orders"].append({
                            "arrive_day": d + total_lead, "units": order_units,
                            "balance_due_day": d + sku.production_lead_days,
                            "balance_amount": order_units * sku.unit_cogs * sku.balance_ratio,
                            "balance_paid": False
                        })
                        log_msg += f" | [补货] 下单: {order_units}"
                    else:
                        res["reorder_fail_events"].append((d, dep + ship - bank_cash))
                        log_msg += f" | [失败] 缺钱: ${dep+ship-bank_cash:,.0f}"
                
                res["logs"].append(log_msg)

            if d % 30 == 29: bank_cash -= self.p.monthly_fixed_cost
            
            res["daily_bank_cash"][d] = bank_cash
            res["daily_amazon_bal"][d] = amazon_bal
            res["daily_units"][d] = day_units
            res["daily_revenue"][d] = day_rev
            res["daily_bank_outflow"][d] = day_bank_out
            res["daily_ads_from_amz"][d] = day_ads_amz
            res["daily_ads_from_bank"][d] = day_ads_bank
            res["daily_inventory_val"][d] = sum(sku_states[i]["inventory"] * self.p.skus[i].unit_cogs for i in range(len(self.p.skus)))
            res["daily_locked_rev"][d] = sum(sum(a for _, a in sku_states[i]["locked_inflow"]) for i in range(len(self.p.skus)))
            res["daily_inventory"][d] = sum(sku_states[i]["inventory"] for i in range(len(self.p.skus)))
            res["daily_in_transit"][d] = sum(sum(o["units"] for o in sku_states[i]["pending_orders"]) for i in range(len(self.p.skus)))
            res["daily_total_assets"][d] = bank_cash + amazon_bal + res["daily_inventory_val"][d]

        res["summary"] = self._calc_summary(res, self.p.initial_capital)
        return res

    def _calc_summary(self, res, init_cap) -> Dict:
        bank = res["daily_bank_cash"]
        amz = res["daily_amazon_bal"]
        total_assets = res["daily_total_assets"]
        rev = res["daily_revenue"]
        units = res["daily_units"]
        ads_amz = res["daily_ads_from_amz"]
        ads_bank = res["daily_ads_from_bank"]
        stockout = res["stockout_events"]
        fails = res["reorder_fail_events"]

        total_rev = sum(rev)
        total_units = sum(units)
        total_ads = sum(ads_amz) + sum(ads_bank)
        min_bank = min(bank)
        
        # 峰值占用资金：投入 - 银行现金最低点
        peak_capital_required = init_cap - min_bank
        # 查找回本时间点 (Breakeven)
        breakeven_day = None
        n_days = len(bank)
        for d in range(n_days):
            total_available = res["daily_bank_cash"][d] + res["daily_amazon_bal"][d] + res["daily_locked_rev"][d]
            if total_available >= init_cap:
                breakeven_day = d
                break

        is_broken = min_bank < 0
        
        # 年化现金利润 = 期末银行现金 - 初始投入 (断裂则为 0)
        cash_profit = (res["daily_bank_cash"][-1] - init_cap) if not is_broken else 0.0

        return {
            "total_revenue": total_rev, 
            "total_units": int(total_units),
            "total_ads": total_ads, 
            "peak_capital_required": peak_capital_required,
            "is_broken": is_broken,
            "shortfall": abs(min_bank) if is_broken else 0,
            "final_bank": bank[-1], 
            "final_amz": amz[-1],
            "cash_profit": cash_profit,
            "breakeven_day": breakeven_day,
            "final_total_assets": total_assets[-1],
            "final_inventory": res["daily_inventory"][-1],
            "min_bank": min_bank, 
            "min_bank_day": bank.index(min_bank),
            "tacos_actual": total_ads / total_rev if total_rev > 0 else 0,
            "stockout_days": len(set(e[0] for e in stockout)),
            "reorder_fails": len(fails),
            "max_shortfall": max((e[1] for e in fails), default=0)
        }

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sku = SKUParams(launch_units_day=2, plateau_units_day=10, time_to_plateau_weeks=12)
    sim = SimulationParams(initial_capital=15000, skus=[sku])
    engine = CashFlowEngine(sim)
    result = engine.run()
    s = result["summary"]
    print(f"God's View MRP Simulation Complete.")
    print(f"  Revenue: ${s['total_revenue']:,.0f} | Units: {s['total_units']:,}")
    print(f"  Min Bank Cash: ${s['min_bank']:,.0f} | Peak Capital: ${s['peak_capital_required']:,.0f}")
    if s["stockout_days"] > 0: print(f"  WARNING: Stockout for {s['stockout_days']} days!")
    elif s["is_broken"]: print(f"  CRITICAL: Cash flow broken! Shortfall: ${s['shortfall']:,.0f}")
    else: print(f"  SUCCESS: Cash flow healthy and zero stockout.")
