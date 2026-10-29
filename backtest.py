"""
GIAI ĐOẠN 3a - Backtest: thử chiến lược trên dữ liệu giá cũ.

Lấy 1000 nến 1h gần nhất (~42 ngày) của từng coin, chạy 3 chiến lược,
tính phí 0.1% mỗi lần mua/bán, so với việc mua rồi giữ nguyên (buy & hold).
Kết quả in ra màn hình và gửi Telegram (nếu đã cài).

Chạy: python backtest.py
Không đặt lệnh thật.
"""

import os

import requests

from bot_actions import COINS, DATA_URL, ema, rsi, send_telegram

FEE = 0.001          # 0.1% mỗi lần mua hoặc bán
CANDLES = 1000       # tối đa Binance cho 1 lần gọi
STOP_LOSS = 0.05     # cắt lỗ khi giảm 5% so với giá mua
WARMUP = 60          # bỏ qua 60 nến đầu để chỉ báo đủ dữ liệu


def fetch_long(symbol):
    r = requests.get(DATA_URL, params={"symbol": symbol, "interval": "1h",
                                       "limit": CANDLES}, timeout=30)
    r.raise_for_status()
    return [float(k[4]) for k in r.json()]


def rsi_series(closes, period=14):
    """RSI cho từng nến, tính một lượt (nhanh hơn gọi rsi() lặp lại)."""
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def ema_series(closes, period):
    e = ema(closes, period)
    return [None] * (len(closes) - len(e)) + e


# ---------- Chiến lược: trả về "buy", "sell" hoặc None ở nến i ----------
def strat_rsi(i, c, ind):
    r = ind["rsi"][i]
    if r is None:
        return None
    if r <= 30:
        return "buy"
    if r >= 70:
        return "sell"
    return None


def strat_ema(i, c, ind):
    e20, e50 = ind["e20"], ind["e50"]
    if e50[i] is None or e50[i - 1] is None:
        return None
    if e20[i - 1] <= e50[i - 1] and e20[i] > e50[i]:
        return "buy"
    if e20[i - 1] >= e50[i - 1] and e20[i] < e50[i]:
        return "sell"
    return None


def strat_combo(i, c, ind):
    """Mua khi giá điều chỉnh (RSI thấp) trong lúc xu hướng tăng (EMA20 > EMA50).
    Bán khi quá mua hoặc xu hướng đảo chiều. Cùng quy tắc với paper_signal()."""
    r, e20, e50 = ind["rsi"][i], ind["e20"][i], ind["e50"][i]
    if r is None or e50 is None:
        return None
    up = e20 > e50
    if up and r <= 40:
        return "buy"
    if r >= 70 or not up:
        return "sell"
    return None


STRATEGIES = {
    "RSI quá bán/quá mua": strat_rsi,
    "EMA20 cắt EMA50": strat_ema,
    "RSI + xu hướng": strat_combo,
}


def backtest(closes, strategy):
    ind = {"rsi": rsi_series(closes), "e20": ema_series(closes, 20),
           "e50": ema_series(closes, 50)}
    cash, coin, entry = 1.0, 0.0, 0.0
    trades, peak, max_dd = [], 1.0, 0.0
    for i in range(WARMUP, len(closes)):
        price = closes[i]
        sig = strategy(i, closes, ind)
        if coin > 0 and price <= entry * (1 - STOP_LOSS):
            sig = "sell"  # cắt lỗ
        if sig == "buy" and coin == 0:
            coin, cash, entry = cash * (1 - FEE) / price, 0.0, price
        elif sig == "sell" and coin > 0:
            cash, coin = coin * price * (1 - FEE), 0.0
            trades.append(price / entry - 1)
        equity = cash + coin * price
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    if coin > 0:  # đóng vị thế ở nến cuối để tính kết quả
        cash = coin * closes[-1] * (1 - FEE)
        trades.append(closes[-1] / entry - 1)
    wins = sum(1 for t in trades if t > 0)
    return {"ret": (cash - 1) * 100, "trades": len(trades),
            "win": wins / len(trades) * 100 if trades else 0.0,
            "dd": max_dd * 100}


def report(results):
    days = (CANDLES - WARMUP) // 24
    lines = [f"📊 Backtest {days} ngày gần nhất (khung 1h, phí 0.1%, cắt lỗ 5%)"]
    totals = {name: [] for name in STRATEGIES}
    for sym, res in results.items():
        lines.append(f"\n{sym.replace('USDT', '')}  (mua rồi giữ: {res['hold']:+.1f}%)")
        for name, r in res["strats"].items():
            beat = "✅" if r["ret"] > res["hold"] else "  "
            lines.append(f"{beat} {name}: {r['ret']:+.1f}% | {r['trades']} lệnh | "
                         f"thắng {r['win']:.0f}% | sụt tối đa {r['dd']:.0f}%")
            totals[name].append(r["ret"] - res["hold"])
    lines.append("\nTrung bình so với mua rồi giữ:")
    for name, diffs in totals.items():
        if diffs:
            lines.append(f"- {name}: {sum(diffs) / len(diffs):+.1f} điểm %")
    lines.append("\n✅ = thắng mua rồi giữ. Kết quả quá khứ không đảm bảo tương lai.")
    return "\n".join(lines)


def main():
    results = {}
    for sym in COINS:
        try:
            closes = fetch_long(sym)
        except requests.RequestException as e:
            print(f"Lỗi lấy {sym}: {e}")
            continue
        hold = (closes[-1] * (1 - FEE) ** 2 / closes[WARMUP] - 1) * 100
        results[sym] = {"hold": hold,
                        "strats": {n: backtest(closes, f) for n, f in STRATEGIES.items()}}
    if not results:
        raise SystemExit("Không lấy được dữ liệu coin nào")
    text = report(results)
    print(text)
    if os.getenv("SEND_REPORT", "1") == "1":
        send_telegram(text)


if __name__ == "__main__":
    main()
