"""
Amazon Cash Flow Simulator — 图表渲染模块 (v5.3 选项卡切换版)
============================================================
"""
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib import font_manager
import numpy as np
import math

# ── 样式配置 ──
COLORS = {
    "inflow":    "#4CAF50", "outflow":   "#F44336", "balance":   "#2196F3",
    "danger":    "#FF5722", "inventory": "#9C27B0", "units":     "#00BCD4",
    "pending":   "#607D8B", "bg":        "#1E1E2E", "grid":      "#2E2E4E",
    "text":      "#CDD6F4", "axis":      "#6C7086",
}

def _apply_dark_style(ax, title: str):
    ax.set_facecolor(COLORS["bg"])
    ax.tick_params(colors=COLORS["text"], labelsize=9)
    ax.set_title(title, color=COLORS["text"], fontsize=11, fontweight="bold", pad=10)
    ax.spines["bottom"].set_color(COLORS["axis"])
    ax.spines["left"].set_color(COLORS["axis"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, color=COLORS["grid"], linestyle="--", linewidth=0.5, alpha=0.5)

def create_charts(parent_frame, result: dict, days: int, stockout_events=None, reorder_fail_events=None):
    bank_cash   = result["daily_bank_cash"]
    amazon_bal  = result["daily_amazon_bal"]
    bank_out    = result["daily_bank_outflow"]
    inflow      = result["daily_inflow"]
    units       = result["daily_units"]
    revenue     = result["daily_revenue"]
    inv_val     = result["daily_inventory_val"]
    locked      = result["daily_locked_rev"]

    num_months = math.ceil(days / 30)
    months_x = np.arange(1, num_months + 1)

    def to_monthly_sum(arr):
        return np.array([sum(arr[m*30:min((m+1)*30, days)]) for m in range(num_months)])
    def to_monthly_last(arr):
        return np.array([arr[min((m+1)*30-1, days-1)] for m in range(num_months)])

    m_bank, m_amz, m_inv, m_locked = to_monthly_last(bank_cash), to_monthly_last(amazon_bal), to_monthly_last(inv_val), to_monthly_last(locked)
    m_inflow, m_outflow, m_units, m_rev = to_monthly_sum(inflow), to_monthly_sum(bank_out), to_monthly_sum(units), to_monthly_sum(revenue)

    # 全宽布局 (10x8.5)
    fig, axes = plt.subplots(3, 1, figsize=(10, 8.5), facecolor=COLORS["bg"])
    fig.subplots_adjust(hspace=0.45, top=0.96, bottom=0.06, left=0.08, right=0.94)

    # 1. 账户走势
    _apply_dark_style(axes[0], "月度资金走势 (Cash Flow Trend)")
    axes[0].bar(months_x, m_inflow, color=COLORS["inflow"], alpha=0.3, label="Monthly Inflow")
    axes[0].bar(months_x, -m_outflow, color=COLORS["outflow"], alpha=0.3, label="Monthly Outflow")
    ax1r = axes[0].twinx()
    ax1r.plot(months_x, m_bank, color=COLORS["balance"], marker='o', markersize=5, linewidth=2, label="Bank Cash")
    ax1r.plot(months_x, m_amz, color=COLORS["inflow"], linestyle="--", label="Amz Balance")
    ax1r.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax1r.tick_params(colors=COLORS["text"], labelsize=8)
    axes[0].legend(loc="upper left", fontsize=8, facecolor=COLORS["bg"], labelcolor=COLORS["text"])

    # 2. 资产结构
    _apply_dark_style(axes[1], "资产价值结构 (Asset Allocation)")
    axes[1].stackplot(months_x, m_inv, m_locked, labels=["Stock Value", "Locked Funds (DD+7)"], colors=[COLORS["inventory"], COLORS["pending"]], alpha=0.5)
    axes[1].yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    axes[1].legend(loc="upper left", fontsize=8, facecolor=COLORS["bg"], labelcolor=COLORS["text"])

    # 3. 产出分析
    _apply_dark_style(axes[2], "经营产出明细 (Sales & Revenue)")
    axes[2].bar(months_x, m_units, color=COLORS["units"], alpha=0.6, label="Monthly Units")
    ax3r = axes[2].twinx()
    ax3r.plot(months_x, m_rev, color="#FFFFFF", marker='d', markersize=5, label="Monthly Revenue ($)")
    ax3r.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax3r.tick_params(colors=COLORS["text"], labelsize=8)
    axes[2].legend(loc="upper left", fontsize=8, facecolor=COLORS["bg"], labelcolor=COLORS["text"])
    axes[2].set_xticks(months_x)
    axes[2].set_xlabel("Month Number", color=COLORS["text"], fontsize=9)

    if stockout_events:
        for e in stockout_events:
            sm = (e[0]//30)+1
            axes[2].axvspan(sm-0.4, sm+0.4, color=COLORS["danger"], alpha=0.15)

    canvas = FigureCanvasTkAgg(fig, master=parent_frame)
    canvas.draw()
    return canvas, fig

def update_charts(canvas, fig, result, days):
    canvas.draw()
