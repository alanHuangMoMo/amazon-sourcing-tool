"""海运费用估算 — 基于包装尺寸计算 LCL 海运成本。

体积重量公式：L×W×H(cm) / 6000
计费逻辑：取 材积重 和 实重 的较大值
"""

import re
from dataclasses import dataclass

# ── 默认费率（China → 各站点 LCL 海运全包价，USD） ──
# 海运 LCL 按 CBM 计费。最小计费 0.5 CBM（不足按 0.5 算）。
# handling: 标签/换标/打包等操作费 per unit
DEFAULT_RATES = {
    "CA": {"cbm": 120, "min_cbm": 0.3, "handling": 0.8},
    "US": {"cbm": 100, "min_cbm": 0.3, "handling": 0.6},
    "UK": {"cbm": 180, "min_cbm": 0.3, "handling": 1.0},
    "DE": {"cbm": 160, "min_cbm": 0.3, "handling": 1.0},
    "JP": {"cbm": 80,  "min_cbm": 0.3, "handling": 0.5},
}


@dataclass
class ShippingEstimate:
    l_cm: float
    w_cm: float
    h_cm: float
    cbm: float
    vol_kg: float          # 材积重 (kg)
    actual_kg: float       # 实际重量 (kg)
    billable_kg: float     # 计费重量
    cost_per_unit: float   # 每件运费 (USD)
    units_per_cbm: int     # 1 CBM 可装多少件


def parse_package_size(size_str: str) -> tuple | None:
    """解析 'L x W x H unit' 格式，返回 (l_cm, w_cm, h_cm)。"""
    if not size_str:
        return None
    m = re.match(r'([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)\s*(cm|mm|m|in)?',
                 size_str.strip(), re.I)
    if not m:
        return None
    l, w, h = float(m.group(1)), float(m.group(2)), float(m.group(3))
    unit = (m.group(4) or 'cm').lower()
    if unit == 'mm':
        l, w, h = l / 10, w / 10, h / 10
    elif unit == 'm':
        l, w, h = l * 100, w * 100, h * 100
    elif unit == 'in':
        l, w, h = l * 2.54, w * 2.54, h * 2.54
    return (l, w, h)


def estimate_shipping(
    package_size: str,
    domain: str = "CA",
    actual_weight_g: float = 0,
    rate_overrides: dict = None,
) -> ShippingEstimate | None:
    """估算单件商品海运费用。

    Args:
        package_size: "L x W x H cm" 格式的包装尺寸
        domain: 目标站点 (CA/US/UK/DE/JP)
        actual_weight_g: 实重（克），0 则仅用材积重
        rate_overrides: 覆盖费率，格式 {"cbm": 120, "per_kg": 3.5, "handling": 0.8}
    """
    parsed = parse_package_size(package_size)
    if not parsed:
        return None

    l, w, h = parsed
    cbm = (l / 100) * (w / 100) * (h / 100)
    vol_kg = (l * w * h) / 6000
    actual_kg = actual_weight_g / 1000 if actual_weight_g > 0 else 0
    billable_kg = max(vol_kg, actual_kg)

    rates = DEFAULT_RATES.get(domain, DEFAULT_RATES["CA"]).copy()
    if rate_overrides:
        rates.update(rate_overrides)

    units_per_cbm = int(1 / cbm) if cbm > 0 else 0

    # LCL 海运按 CBM 计费。单件直接用实际体积，整柜拼箱的 min_cbm 由货代在总运费层面处理
    freight = cbm * rates["cbm"]
    cost_per_unit = round(freight + rates["handling"], 2)

    return ShippingEstimate(
        l_cm=round(l, 1), w_cm=round(w, 1), h_cm=round(h, 1),
        cbm=round(cbm, 6),
        vol_kg=round(vol_kg, 2),
        actual_kg=round(actual_kg, 2),
        billable_kg=round(billable_kg, 2),
        cost_per_unit=cost_per_unit,
        units_per_cbm=units_per_cbm,
    )
