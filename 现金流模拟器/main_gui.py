"""
Amazon Cash Flow Simulator — GUI 主界面
========================================
布局：左侧参数面板（可滚动）+ 右侧图表仪表盘
框架：CustomTkinter (深色主题)
"""

import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import sys
import os

# 在 chart_renderer 之前设置中文字体，确保全局生效
import matplotlib
from matplotlib import font_manager as _fm
def _init_chinese_font():
    for name in ["Microsoft YaHei", "SimHei", "STSong", "FangSong"]:
        if name in {f.name for f in _fm.fontManager.ttflist}:
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = [name] + matplotlib.rcParams.get("font.sans-serif", [])
            matplotlib.rcParams["axes.unicode_minus"] = False
            return
_init_chinese_font()

import math
from cashflow_engine import SKUParams, SimulationParams, CashFlowEngine
from chart_renderer import create_charts

# ── 全局主题设置 ─────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_DARK   = "#1E1E2E"
BG_PANEL  = "#2A2A3E"
BG_CARD   = "#313244"
TEXT_MAIN = "#CDD6F4"
TEXT_DIM  = "#6C7086"
ACCENT    = "#89B4FA"
GREEN     = "#A6E3A1"
RED       = "#F38BA8"
YELLOW    = "#F9E2AF"


# ─────────────────────────────────────────────
# 辅助控件：带标签的输入行
# ─────────────────────────────────────────────
class LabeledEntry(ctk.CTkFrame):
    def __init__(self, parent, label: str, default, unit: str = "", tooltip: str = "", width=90):
        super().__init__(parent, fg_color="transparent")
        self.var = tk.StringVar(value=str(default))

        lbl = ctk.CTkLabel(self, text=label, text_color=TEXT_MAIN,
                           font=("Microsoft YaHei", 12), width=160, anchor="w")
        lbl.pack(side="left", padx=(0, 4))

        entry = ctk.CTkEntry(self, textvariable=self.var, width=width,
                             font=("Consolas", 12))
        entry.pack(side="left")

        if unit:
            ctk.CTkLabel(self, text=unit, text_color=TEXT_DIM,
                         font=("Microsoft YaHei", 11)).pack(side="left", padx=(4, 0))

    def get_float(self, default=0.0):
        try:
            return float(self.var.get())
        except ValueError:
            return default

    def get_int(self, default=0):
        try:
            return int(float(self.var.get()))
        except ValueError:
            return default


class SectionHeader(ctk.CTkLabel):
    def __init__(self, parent, text: str):
        super().__init__(parent, text=f"  {text}",
                         font=("Microsoft YaHei", 13, "bold"),
                         text_color=ACCENT,
                         fg_color=BG_CARD,
                         corner_radius=6,
                         height=30)


# ─────────────────────────────────────────────
# 摘要指标卡
# ─────────────────────────────────────────────
class MetricCard(ctk.CTkFrame):
    def __init__(self, parent, title: str, value: str = "—", color=TEXT_MAIN):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=8, width=140)
        self.title_lbl = ctk.CTkLabel(self, text=title, text_color=TEXT_DIM,
                                      font=("Microsoft YaHei", 10))
        self.title_lbl.pack(pady=(8, 0))
        self.val_lbl = ctk.CTkLabel(self, text=value, text_color=color,
                                    font=("Consolas", 15, "bold"))
        self.val_lbl.pack(pady=(2, 8))

    def update(self, value: str, color=None):
        self.val_lbl.configure(text=value)
        if color:
            self.val_lbl.configure(text_color=color)


# ─────────────────────────────────────────────
# 主应用窗口
# ─────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Amazon 现金流模拟器  v1.0")
        self.geometry("1400x860")
        self.configure(fg_color=BG_DARK)
        self.resizable(True, True)

        self._canvas_widget = None
        self._fig = None

        self._build_layout()
        self._build_left_panel()
        self._build_right_panel()

        # 关闭窗口时强制退出所有进程（防止Matplotlib后台线程残留）
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 启动时自动运行一次默认参数
        self.after(300, self._run_simulation)

    def _on_close(self):
        try:
            if self._fig is not None:
                import matplotlib.pyplot as plt
                plt.close(self._fig)
        except Exception:
            pass
        self.destroy()
        os._exit(0)  # 强制终止进程树，包括所有后台线程

    # ── 整体布局 ──────────────────────────────
    def _build_layout(self):
        # 顶部标题栏
        self.header_frame = ctk.CTkFrame(self, fg_color=BG_PANEL, height=50, corner_radius=0)
        self.header_frame.pack(fill="x", side="top")
        self.header_frame.pack_propagate(False)

        # 侧边栏切换按钮
        self.sidebar_visible = True
        self.btn_toggle = ctk.CTkButton(self.header_frame, text="☰", width=40, height=32,
                                         fg_color="transparent", text_color=TEXT_MAIN,
                                         hover_color=BG_CARD, font=("Arial", 18, "bold"),
                                         command=self._toggle_sidebar)
        self.btn_toggle.pack(side="left", padx=10)

        ctk.CTkLabel(self.header_frame,
                     text="Amazon 亚马逊现金流模拟器",
                     font=("Microsoft YaHei", 16, "bold"),
                     text_color=ACCENT).pack(side="left", pady=10)
        
        ctk.CTkLabel(self.header_frame,
                     text="基于 DD+7 回款模型 · S型销量曲线 · 动态补货算法  ",
                     font=("Microsoft YaHei", 10),
                     text_color=TEXT_DIM).pack(side="right", pady=10)

        # 主容器
        self.main_container = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.main_container.pack(fill="both", expand=True)

    def _toggle_sidebar(self):
        if self.sidebar_visible:
            self.left_panel.pack_forget()
            self.sidebar_visible = False
            self.btn_toggle.configure(text_color=ACCENT) 
        else:
            self.left_panel.pack(side="left", fill="y", before=self.right_panel)
            self.sidebar_visible = True
            self.btn_toggle.configure(text_color=TEXT_MAIN)

    # ── 左侧参数面板 ──────────────────────────
    def _build_left_panel(self):
        self.left_panel = ctk.CTkFrame(self.main_container, width=340, fg_color=BG_PANEL, corner_radius=0)
        self.left_panel.pack(side="left", fill="y")
        self.left_panel.pack_propagate(False)

        # 滚动容器
        scroll = ctk.CTkScrollableFrame(self.left_panel, fg_color=BG_PANEL, width=320)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        def add_header(text):
            SectionHeader(scroll, text).pack(fill="x", pady=(12, 4))

        def add_entry(label, default, unit="", width=90):
            e = LabeledEntry(scroll, label, default, unit, width=width)
            e.pack(fill="x", padx=8, pady=2)
            return e

        # ── 场景快速切换 ──
        scene_frame = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=8)
        scene_frame.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(scene_frame, text="场景预设", text_color=TEXT_DIM,
                     font=("Microsoft YaHei", 11)).pack(pady=(6, 2))
        btn_row = ctk.CTkFrame(scene_frame, fg_color="transparent")
        btn_row.pack(pady=(0, 8))
        ctk.CTkButton(btn_row, text="乐观", width=80, fg_color="#2D6A2D",
                      command=lambda: self._apply_preset("optimistic")).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="基准", width=80, fg_color="#2A2A5E",
                      command=lambda: self._apply_preset("base")).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="悲观", width=80, fg_color="#6A2D2D",
                      command=lambda: self._apply_preset("pessimistic")).pack(side="left", padx=4)

        # ── 1. 全局财务参数 ──
        add_header("全局财务参数")
        self.e_init_capital   = add_entry("初始资金", 20000, "USD")
        self.e_sim_days       = add_entry("模拟周期", 365, "天")
        self.e_start_month    = add_entry("起始月份", 1, "(1-12月)")
        self.e_monthly_fixed  = add_entry("月固定支出", 500, "USD")
        self.e_exchange_rate  = add_entry("汇率 USD/CNY", 7.2, "")
        self.e_financing_rate = add_entry("融资年化利率", 0, "%")

        # ── 2. 销售曲线参数 ──
        add_header("销售曲线（S型）")
        self.e_launch_units   = add_entry("新品期日均单量", 3, "件/天")
        self.e_plateau_units  = add_entry("落点日均单量", 15, "件/天")
        self.e_ramp_weeks     = add_entry("爬升周期", 8, "周")
        self.e_asp            = add_entry("平均售价 ASP", 29.99, "USD")
        self.e_plateau_drift  = add_entry("落点月漂移", 0, "%  (负=衰退)")

        # 季节性系数（12个月）
        ctk.CTkLabel(scroll, text="季节性系数（1月→12月）",
                     text_color=TEXT_DIM, font=("Microsoft YaHei", 11)).pack(padx=8, anchor="w", pady=(8, 2))
        season_frame = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=8)
        season_frame.pack(fill="x", padx=8, pady=2)
        self.season_vars = []
        defaults = [0.8, 0.8, 0.9, 1.0, 1.0, 1.1, 1.2, 1.5, 1.3, 1.1, 1.8, 2.0]
        months = ["1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"]
        for row_i in range(3):
            row = ctk.CTkFrame(season_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            for col_i in range(4):
                m_idx = row_i * 4 + col_i
                var = tk.StringVar(value=str(defaults[m_idx]))
                self.season_vars.append(var)
                inner = ctk.CTkFrame(row, fg_color="transparent")
                inner.pack(side="left", padx=4, expand=True)
                ctk.CTkLabel(inner, text=months[m_idx], text_color=TEXT_DIM,
                             font=("Microsoft YaHei", 10), width=36).pack()
                ctk.CTkEntry(inner, textvariable=var, width=52,
                             font=("Consolas", 11)).pack()

        # ── 3. 供应链参数 ──
        add_header("供应链参数")
        self.e_unit_cogs        = add_entry("单位采购成本", 8.0, "USD")
        self.e_prod_lead        = add_entry("生产提前期", 25, "天")
        self.e_ship_lead        = add_entry("物流提前期", 40, "天（海运）")
        self.e_deposit_ratio    = add_entry("定金比例", 30, "%")
        self.e_ship_cost_unit   = add_entry("头程运费/单位", 1.5, "USD")

        # ── 4. 库存策略 ──
        add_header("库存策略")
        self.e_target_cover     = add_entry("目标备货天数", 90, "天")
        self.e_moq              = add_entry("最小起订量 MOQ", 200, "件")

        # ── 5. 亚马逊费用 ──
        add_header("亚马逊费用")
        self.e_referral_fee     = add_entry("佣金率", 15, "%")
        self.e_fba_fee          = add_entry("FBA 费/单位", 4.50, "USD")
        self.e_tacos            = add_entry("TACoS 广告占比", 12, "%")
        self.e_misc_loss        = add_entry("综合损耗率", 5, "%  (仓储+退货)")

        # ── 6. 回款参数 ──
        add_header("回款参数（DD+7）")
        self.e_delivery_days    = add_entry("FBA 配送时效", 4, "天")
        self.e_dd_days          = add_entry("DD+ 锁定天数", 7, "天")
        self.e_bank_lag         = add_entry("银行到账延迟", 4, "天")
        self.e_disburse_cycle   = add_entry("结算周期", 1, "天(1=手动,14=自动)")
        self.e_account_reserve  = add_entry("账户预留金率", 4, "%")

        # ── 运行按钮 ──
        ctk.CTkButton(scroll, text="▶  开始模拟",
                      font=("Microsoft YaHei", 14, "bold"),
                      height=44, fg_color=ACCENT, text_color=BG_DARK,
                      hover_color="#B9D1FA",
                      command=self._run_simulation).pack(fill="x", padx=8, pady=(16, 8))

        # 导出日志按钮
        ctk.CTkButton(scroll, text="📄  导出诊断日志",
                      font=("Microsoft YaHei", 12),
                      height=32, fg_color=BG_CARD, text_color=TEXT_MAIN,
                      hover_color="#45475A",
                      command=self._export_logs).pack(fill="x", padx=8, pady=4)

    # ── 右侧图表面板 ──────────────────────────
    def _build_right_panel(self):
        self.right_panel = ctk.CTkFrame(self.main_container, fg_color=BG_DARK, corner_radius=0)
        self.right_panel.pack(side="left", fill="both", expand=True)
        
        # 引用兼容
        self.right_frame = self.right_panel

        # 1. 指标摘要栏 (优化指标：聚焦现金、利润与周转)
        summary_frame = ctk.CTkFrame(self.right_frame, fg_color=BG_PANEL, height=85, corner_radius=0)
        summary_frame.pack(fill="x", side="top")
        summary_frame.pack_propagate(False)

        cards_row = ctk.CTkFrame(summary_frame, fg_color="transparent")
        cards_row.pack(expand=True, fill="both", padx=10, pady=6)

        self.card_cash_profit = MetricCard(cards_row, "年化现金利润",       "—", GREEN)
        self.card_breakeven   = MetricCard(cards_row, "预计回本周期",       "—", YELLOW)
        self.card_peak_cap    = MetricCard(cards_row, "峰值占用资金",       "—", YELLOW)
        self.card_final_total = MetricCard(cards_row, "期末总资产",         "—", GREEN)
        self.card_bank        = MetricCard(cards_row, "期末银行现金",       "—", TEXT_MAIN)
        self.card_status      = MetricCard(cards_row, "资金链状态",         "—", GREEN)
        self.card_stockout    = MetricCard(cards_row, "断货累计天数",       "—", RED)
        self.card_doh         = MetricCard(cards_row, "期末可售天数",       "—", ACCENT)

        for card in [self.card_cash_profit, self.card_breakeven, self.card_peak_cap,
                     self.card_final_total, self.card_bank, self.card_status,
                     self.card_stockout, self.card_doh]:
            card.pack(side="left", expand=True, fill="x", padx=3)

        # 2. 视图切换导航栏
        nav_frame = ctk.CTkFrame(self.right_frame, fg_color=BG_PANEL, height=45, corner_radius=0)
        nav_frame.pack(fill="x")
        nav_frame.pack_propagate(False)

        self.btn_show_chart = ctk.CTkButton(nav_frame, text="📊 趋势可视化看板", 
                                            width=180, height=32, corner_radius=6,
                                            fg_color=ACCENT, text_color=BG_DARK,
                                            font=("Microsoft YaHei", 12, "bold"),
                                            command=lambda: self._switch_view("chart"))
        self.btn_show_chart.pack(side="left", padx=(15, 10), pady=6)

        self.btn_show_table = ctk.CTkButton(nav_frame, text="📋 数据明细报表", 
                                            width=180, height=32, corner_radius=6,
                                            fg_color=BG_CARD, text_color=TEXT_MAIN,
                                            font=("Microsoft YaHei", 12),
                                            command=lambda: self._switch_view("table"))
        self.btn_show_table.pack(side="left", padx=5, pady=6)

        # 3. 内容显示区 (堆叠容器)
        self.display_container = ctk.CTkFrame(self.right_frame, fg_color=BG_DARK, corner_radius=0)
        self.display_container.pack(fill="both", expand=True)

        # 预建两个视图容器
        self.view_chart = ctk.CTkFrame(self.display_container, fg_color=BG_DARK, corner_radius=0)
        self.view_table = ctk.CTkFrame(self.display_container, fg_color=BG_PANEL, corner_radius=0)
        
        # 初始显示图表
        self.view_chart.pack(fill="both", expand=True)
        self.current_view = "chart"

        # --- 初始化图表视图内容 ---
        self.chart_container = self.view_chart # 兼容原有代码名

        # --- 初始化表格视图内容 ---
        lbl_box = ctk.CTkFrame(self.view_table, fg_color=BG_CARD, height=40, corner_radius=0)
        lbl_box.pack(fill="x")
        ctk.CTkLabel(lbl_box, text="  月度经营损益明细 (P&L Detail)", 
                     font=("Microsoft YaHei", 13, "bold"), text_color=ACCENT).pack(side="left", padx=15)

        # 优化表格样式
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background=BG_PANEL, foreground=TEXT_MAIN, fieldbackground=BG_PANEL, 
                        rowheight=35, font=("Consolas", 11), borderwidth=0)
        style.map("Treeview", background=[('selected', ACCENT)], foreground=[('selected', BG_DARK)])
        style.configure("Treeview.Heading", background=BG_CARD, foreground=TEXT_MAIN, font=("Microsoft YaHei", 11, "bold"))

        self.tree = ttk.Treeview(self.view_table, columns=("month", "units", "rev", "bank", "stock", "transit", "doh"), show="headings")
        cols = {
            "month": ("月份", 80), 
            "units": ("月销量", 85), 
            "rev": ("月营收", 120),
            "bank": ("期末银行现金", 140), 
            "stock": ("期末库存", 90),
            "transit": ("在途补货", 90),
            "doh": ("周转天数(DOH)", 120)
        }
        for cid, (name, width) in cols.items():
            self.tree.heading(cid, text=name)
            self.tree.column(cid, width=width, anchor="center")
        
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        
        vsb = ttk.Scrollbar(self.view_table, orient="vertical", command=self.tree.yview)
        vsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=vsb.set)

    # ── 视图切换逻辑 ──────────────────────────
    def _switch_view(self, view_name):
        if view_name == self.current_view: return
        
        if view_name == "chart":
            self.view_table.pack_forget()
            self.view_chart.pack(fill="both", expand=True)
            self.btn_show_chart.configure(fg_color=ACCENT, text_color=BG_DARK, font=("Microsoft YaHei", 12, "bold"))
            self.btn_show_table.configure(fg_color=BG_CARD, text_color=TEXT_MAIN, font=("Microsoft YaHei", 12))
        else:
            self.view_chart.pack_forget()
            self.view_table.pack(fill="both", expand=True)
            self.btn_show_table.configure(fg_color=ACCENT, text_color=BG_DARK, font=("Microsoft YaHei", 12, "bold"))
            self.btn_show_chart.configure(fg_color=BG_CARD, text_color=TEXT_MAIN, font=("Microsoft YaHei", 12))
            
        self.current_view = view_name

    # ── 更新表格数据 ──────────────────────────
    def _update_table(self, result, days):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        num_months = math.ceil(days / 30)
        bank_cash  = result["daily_bank_cash"]
        units      = result["daily_units"]
        rev        = result["daily_revenue"]
        stock      = result["daily_inventory"]  # 每日库存件数
        transit    = result["daily_in_transit"] # 每日在途件数

        for m in range(num_months):
            start, end = m * 30, min((m + 1) * 30, days)
            idx = end - 1
            
            m_units = sum(units[start:end])
            m_rev   = sum(rev[start:end])
            m_bank  = bank_cash[idx]
            m_stock = stock[idx]
            m_trans = transit[idx]
            
            # 计算周转天数 (DOH = 期末库存 / 未来30天日均销量)
            # 简单算法：当前月度日均销量
            avg_daily = m_units / 30 if m_units > 0 else 0.1
            doh = m_stock / avg_daily
            
            self.tree.insert("", "end", values=(
                f"第 {m+1} 月",
                f"{int(m_units):,}",
                f"${m_rev:,.0f}",
                f"${m_bank:,.0f}",
                f"{int(m_stock):,}",
                f"{int(m_trans):,}",
                f"{doh:.1f} 天"
            ))

    # ── 执行模拟 ──────────────────────────────
    def _run_simulation(self):
        try:
            params = self._collect_params()
        except Exception as ex:
            messagebox.showerror("参数错误", str(ex))
            return

        self.engine = CashFlowEngine(params)
        self.last_result = self.engine.run()
        result = self.last_result
        s = result["summary"]

        # 更新摘要卡 (CTO 核心经营指标)
        profit_color = GREEN if s["cash_profit"] > 0 else (RED if s["is_broken"] else TEXT_MAIN)
        self.card_cash_profit.update(f"${s['cash_profit']:,.0f}", profit_color)

        be_text = f"第 {s['breakeven_day']} 天" if s["breakeven_day"] is not None else "未回本"
        self.card_breakeven.update(be_text, YELLOW)

        self.card_peak_cap.update(f"${s['peak_capital_required']:,.0f}", YELLOW)
        self.card_final_total.update(f"${s['final_total_assets']:,.0f}", GREEN)

        bank_color = GREEN if s["final_bank"] >= 0 else RED
        self.card_bank.update(f"${s['final_bank']:,.0f}", bank_color)

        if s["is_broken"]:
            self.card_status.update(f"资金链断裂(-${s['shortfall']:,.0f})", RED)
        else:
            self.card_status.update("资金链稳健 ✓", GREEN)

        self.card_stockout.update(f"{s['stockout_days']} 天", RED if s["stockout_days"] > 0 else GREEN)
        
        # 计算期末可售天数
        daily_sales_end = result["daily_units"][-30:]
        avg_sales_end = sum(daily_sales_end) / 30 if sum(daily_sales_end) > 0 else 0.1
        doh_final = s["final_inventory"] / avg_sales_end
        self.card_doh.update(f"{doh_final:.1f} 天", ACCENT)

        # 更新数据表格
        self._update_table(result, params.simulation_days)

        # 销毁旧图表并重建
        for widget in self.chart_container.winfo_children():
            widget.destroy()

        canvas, fig = create_charts(
            self.chart_container, result, params.simulation_days,
            stockout_events=result.get("stockout_events", []),
            reorder_fail_events=result.get("reorder_fail_events", []),
        )
        widget = canvas.get_tk_widget()
        widget.configure(bg=BG_DARK)
        widget.pack(fill="both", expand=True)
        canvas.draw()

        self._canvas_widget = canvas
        self._fig = fig

    # ── 收集参数 ──────────────────────────────
    def _collect_params(self) -> SimulationParams:
        season = [float(v.get()) for v in self.season_vars]

        sku = SKUParams(
            name="SKU-1",
            launch_units_day   = self.e_launch_units.get_float(3),
            plateau_units_day  = self.e_plateau_units.get_float(15),
            time_to_plateau_weeks = self.e_ramp_weeks.get_float(8),
            asp                = self.e_asp.get_float(29.99),
            plateau_drift      = self.e_plateau_drift.get_float(0) / 100,
            seasonality        = season,
            unit_cogs          = self.e_unit_cogs.get_float(8),
            production_lead_days = self.e_prod_lead.get_int(25),
            shipping_lead_days = self.e_ship_lead.get_int(40),
            deposit_ratio      = self.e_deposit_ratio.get_float(30) / 100,
            balance_ratio      = 1 - self.e_deposit_ratio.get_float(30) / 100,
            shipping_cost_per_unit = self.e_ship_cost_unit.get_float(1.5),
            target_cover_days  = self.e_target_cover.get_int(90),
            moq                = self.e_moq.get_int(200),
            referral_fee_rate  = self.e_referral_fee.get_float(15) / 100,
            fba_fee_per_unit   = self.e_fba_fee.get_float(4.5),
            tacos              = self.e_tacos.get_float(12) / 100,
            misc_loss_rate     = self.e_misc_loss.get_float(5) / 100,
            avg_delivery_days  = self.e_delivery_days.get_int(4),
            dd_reserve_days    = self.e_dd_days.get_int(7),
            bank_lag_days      = self.e_bank_lag.get_int(4),
            disbursement_cycle = self.e_disburse_cycle.get_int(1),
            account_reserve_rate = self.e_account_reserve.get_float(4) / 100,
        )

        sim = SimulationParams(
            initial_capital    = self.e_init_capital.get_float(20000),
            simulation_days    = self.e_sim_days.get_int(365),
            start_month        = max(1, min(12, self.e_start_month.get_int(1))),
            monthly_fixed_cost = self.e_monthly_fixed.get_float(500),
            exchange_rate      = self.e_exchange_rate.get_float(7.2),
            financing_annual_rate = self.e_financing_rate.get_float(0) / 100,
            skus               = [sku],
        )
        return sim

    # ── 场景预设 ──────────────────────────────
    def _apply_preset(self, scene: str):
        presets = {
            "optimistic": {
                "launch": 5, "plateau": 25, "ramp": 6,
                "asp": 34.99, "cogs": 7.0, "tacos": 9,
                "prod_lead": 20, "ship_lead": 35, "misc_loss": 4,
                "deposit": 30, "init_cap": 25000, "target_cover": 80,
            },
            "base": {
                "launch": 3, "plateau": 15, "ramp": 8,
                "asp": 29.99, "cogs": 8.0, "tacos": 12,
                "prod_lead": 25, "ship_lead": 40, "misc_loss": 6,
                "deposit": 30, "init_cap": 20000, "target_cover": 90,
            },
            "pessimistic": {
                "launch": 2, "plateau": 10, "ramp": 12,
                "asp": 24.99, "cogs": 9.5, "tacos": 18,
                "prod_lead": 35, "ship_lead": 50, "misc_loss": 10,
                "deposit": 50, "init_cap": 15000, "target_cover": 110,
            },
        }
        p = presets[scene]
        self.e_launch_units.var.set(str(p["launch"]))
        self.e_plateau_units.var.set(str(p["plateau"]))
        self.e_ramp_weeks.var.set(str(p["ramp"]))
        self.e_asp.var.set(str(p["asp"]))
        self.e_unit_cogs.var.set(str(p["cogs"]))
        self.e_tacos.var.set(str(p["tacos"]))
        self.e_prod_lead.var.set(str(p["prod_lead"]))
        self.e_ship_lead.var.set(str(p["ship_lead"]))
        self.e_misc_loss.var.set(str(p["misc_loss"]))
        self.e_deposit_ratio.var.set(str(p["deposit"]))
        self.e_init_capital.var.set(str(p["init_cap"]))
        self.e_target_cover.var.set(str(p["target_cover"]))
        self._run_simulation()

    def _export_logs(self):
        if not hasattr(self, "last_result") or not self.last_result:
            messagebox.showwarning("提示", "请先点击 [开始模拟] 运行一次仿真。")
            return
        
        try:
            log_path = os.path.join(os.getcwd(), "simulation_diag_log.txt")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("=== Amazon Cash Flow Simulator 诊断日志 ===\n")
                f.write(f"生成时间: {tk.datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                for log in self.last_result["logs"]:
                    f.write(log + "\n")
            
            messagebox.showinfo("成功", f"诊断日志已导出至：\n{log_path}\n\n请将此文件发送给专家进行排查。")
        except Exception as e:
            messagebox.showerror("导出失败", f"无法写入日志文件：{str(e)}")


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
