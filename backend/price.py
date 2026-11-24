"""Price floor and contribution margin. Pure arithmetic, no network, no LLM."""
import math
import os

FEE_RATE = float(os.getenv("FEE_RATE", "0.0636"))   # marketplace fees; replace once the real tier is known
MIN_MARGIN = float(os.getenv("MIN_MARGIN", "0.15"))  # minimum contribution margin


def price_floor(cost, ad_cost_per_sale=0, fee=FEE_RATE, margin=MIN_MARGIN):
    """Lowest price that still leaves `margin` after fees: (cost + ad) / (1 - fee - margin), rounded up to 10 KRW."""
    raw = (cost + ad_cost_per_sale) / (1 - fee - margin)
    return int(math.ceil(raw / 10) * 10)


def contribution(price, cost, ad_cost_per_sale=0, fee=FEE_RATE):
    """Return (profit per sale in KRW, margin as a fraction of price)."""
    profit = price * (1 - fee) - cost - ad_cost_per_sale
    return round(profit), profit / price


if __name__ == "__main__":
    print("fee rate:", FEE_RATE, "| minimum margin:", MIN_MARGIN)
    for cost, ad in [(6000, 0), (6000, 2500), (12800, 0)]:
        floor = price_floor(cost, ad)
        profit, margin = contribution(floor, cost, ad)
        print(f"cost {cost:>6} + ad {ad:>5} -> floor {floor:>6} KRW | profit {profit:>5} | margin {margin:.1%}")