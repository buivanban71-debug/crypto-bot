"""
3 chiến lược giao dịch, dùng chung cho backtest.py và giao dịch giấy trong bot_actions.py.
Sửa quy tắc ở đây là cả hai nơi cùng đổi theo.

Mỗi chiến lược nhận (i, closes, ind) và trả về "buy", "sell" hoặc None tại nến i.
`ind` là các chuỗi chỉ báo tính sẵn bằng indicators(closes).
"""

FEE = 0.001        # 0.1% mỗi lần mua hoặc bán
STOP_LOSS = 0.05   # cắt lỗ khi giảm 5% so với giá mua


# ---------- Chỉ báo dạng chuỗi (mỗi nến một giá trị, None khi chưa đủ dữ liệu) ----------
def rsi_series(closes, period=14):
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
    out = [None] * len(closes)
    if len(closes) < period:
        return out
    k = 2 / (period + 1)
    val = sum(closes[:period]) / period
    out[period - 1] = val
    for i in range(period, len(closes)):
        val = closes[i] * k + val * (1 - k)
        out[i] = val
    return out


def indicators(closes):
    return {"rsi": rsi_series(closes), "e20": ema_series(closes, 20),
            "e50": ema_series(closes, 50)}


# ---------- Chiến lược ----------
def strat_rsi(i, c, ind):
    """Mua khi RSI <= 30 (quá bán), bán khi RSI >= 70 (quá mua)."""
    r = ind["rsi"][i]
    if r is None:
        return None
    if r <= 30:
        return "buy"
    if r >= 70:
        return "sell"
    return None


def strat_ema(i, c, ind):
    """Mua khi EMA20 cắt lên EMA50, bán khi cắt xuống."""
    e20, e50 = ind["e20"], ind["e50"]
    if i < 1 or e50[i] is None or e50[i - 1] is None:
        return None
    if e20[i - 1] <= e50[i - 1] and e20[i] > e50[i]:
        return "buy"
    if e20[i - 1] >= e50[i - 1] and e20[i] < e50[i]:
        return "sell"
    return None


def strat_combo(i, c, ind):
    """Mua khi giá điều chỉnh (RSI <= 40) trong xu hướng tăng (EMA20 > EMA50).
    Bán khi RSI >= 70 hoặc xu hướng đảo chiều."""
    r, e20, e50 = ind["rsi"][i], ind["e20"][i], ind["e50"][i]
    if r is None or e50 is None:
        return None
    up = e20 > e50
    if up and r <= 40:
        return "buy"
    if r >= 70 or not up:
        return "sell"
    return None


# mã ngắn (dùng làm khóa lưu trạng thái) : (tên hiển thị, hàm)
STRATEGIES = {
    "rsi": ("RSI quá bán/quá mua", strat_rsi),
    "ema": ("EMA20 cắt EMA50", strat_ema),
    "combo": ("RSI + xu hướng", strat_combo),
}


def decide(book, price, signal):
    """Áp tín hiệu + cắt lỗ lên một sổ {cash, coin, entry}.
    Trả về ("buy"|"sell"|None, lý do, lãi/lỗ của lệnh bán)."""
    if book["coin"] > 0 and price <= book["entry"] * (1 - STOP_LOSS):
        signal, reason = "sell", "cắt lỗ 5%"
    else:
        reason = "tín hiệu"
    if signal == "buy" and book["coin"] == 0:
        book["coin"] = book["cash"] * (1 - FEE) / price
        book["cash"], book["entry"] = 0.0, price
        return "buy", reason, None
    if signal == "sell" and book["coin"] > 0:
        pnl = price / book["entry"] - 1
        book["cash"], book["coin"] = book["coin"] * price * (1 - FEE), 0.0
        return "sell", reason, pnl
    return None, None, None
