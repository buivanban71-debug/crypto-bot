"""
GIAI ĐOẠN 3a - Backtest: thử 3 chiến lược trên ~6 tháng giá gần nhất.

- Chạy trên 2 khung: 1h và 4h.
- Tính phí 0.1% mỗi lần mua/bán, cắt lỗ 5%, so với mua rồi giữ nguyên.
- Chia 6 tháng thành 3 giai đoạn 2 tháng để xem chiến lược có thắng đều không,
  hay chỉ ăn may ở một giai đoạn.
Kết quả in ra màn hình và gửi Telegram (mỗi khung một tin nhắn).

Chạy: python backtest.py      Không đặt lệnh thật.
"""

import os

import requests

from bot_actions import COINS, DATA_URL, send_telegram
from strategies import FEE, STRATEGIES, decide, indicators

DAYS = 180
TIMEFRAMES = {"1h": 1, "4h": 4}   # khung : số giờ mỗi nến
WARMUP = 60                       # số nến đầu dùng để chỉ báo "khởi động"
SEGMENTS = 3


def fetch_history(symbol, interval, n):
    """Lấy n nến gần nhất, mỗi lần gọi tối đa 1000 nến, đi lùi dần về quá khứ."""
    rows, end = [], None
    while len(rows) < n:
        params = {"symbol": symbol, "interval": interval, "limit": min(1000, n - len(rows))}
        if end is not None:
            params["endTime"] = end
        r = requests.get(DATA_URL, params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows = batch + rows
        end = batch[0][0] - 1
    return [float(k[4]) for k in rows]


def simulate(closes, ind, fn, start, end):
    book = {"cash": 1.0, "coin": 0.0, "entry": 0.0}
    trades, peak, max_dd = [], 1.0, 0.0
    for i in range(start, end):
        action, _, pnl = decide(book, closes[i], fn(i, closes, ind))
        if action == "sell":
            trades.append(pnl)
        equity = book["cash"] + book["coin"] * closes[i]
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    if book["coin"] > 0:  # đóng vị thế ở nến cuối để tính kết quả
        trades.append(closes[end - 1] / book["entry"] - 1)
        book["cash"] = book["coin"] * closes[end - 1] * (1 - FEE)
    wins = sum(1 for t in trades if t > 0)
    return {"ret": (book["cash"] - 1) * 100, "trades": len(trades),
            "win": wins / len(trades) * 100 if trades else 0.0, "dd": max_dd * 100}


def hold_return(closes, start, end):
    return (closes[end - 1] * (1 - FEE) ** 2 / closes[start] - 1) * 100


def run_symbol(closes):
    ind = indicators(closes)
    start, end = WARMUP, len(closes)
    bounds = [start + (end - start) * k // SEGMENTS for k in range(SEGMENTS + 1)]
    hold = hold_return(closes, start, end)
    out = {"hold": hold, "strats": {}}
    for key, (_, fn) in STRATEGIES.items():
        r = simulate(closes, ind, fn, start, end)
        r["seg_wins"] = sum(
            simulate(closes, ind, fn, a, b)["ret"] > hold_return(closes, a, b)
            for a, b in zip(bounds, bounds[1:]))
        out["strats"][key] = r
    return out


def report(tf, results):
    lines = [f"📊 Backtest {DAYS} ngày, khung {tf} (phí 0.1%, cắt lỗ 5%)"]
    diffs = {k: [] for k in STRATEGIES}
    for sym, res in results.items():
        lines.append(f"\n{sym.replace('USDT', '')}  (mua rồi giữ: {res['hold']:+.1f}%)")
        for key, r in res["strats"].items():
            mark = "✅" if r["ret"] > res["hold"] else "  "
            lines.append(f"{mark} {STRATEGIES[key][0]}: {r['ret']:+.1f}% | {r['trades']} lệnh | "
                         f"thắng {r['win']:.0f}% | sụt {r['dd']:.0f}% | "
                         f"hơn mua-giữ {r['seg_wins']}/{SEGMENTS} giai đoạn")
            diffs[key].append(r["ret"] - res["hold"])
    lines.append("\nTrung bình so với mua rồi giữ:")
    for key, d in diffs.items():
        if d:
            lines.append(f"- {STRATEGIES[key][0]}: {sum(d) / len(d):+.1f} điểm %")
    lines.append(f"\n'hơn mua-giữ k/{SEGMENTS}': chia {DAYS} ngày thành {SEGMENTS} giai đoạn, "
                 f"đếm số giai đoạn chiến lược thắng. {SEGMENTS}/{SEGMENTS} mới là ổn định.")
    return "\n".join(lines)


def main():
    send = os.getenv("SEND_REPORT", "1") == "1"
    ok = False
    for tf, hours in TIMEFRAMES.items():
        n = DAYS * 24 // hours + WARMUP
        results = {}
        for sym in COINS:
            try:
                closes = fetch_history(sym, tf, n)
            except requests.RequestException as e:
                print(f"Lỗi lấy {sym} {tf}: {e}")
                continue
            if len(closes) < WARMUP + 50:
                print(f"{sym} {tf}: quá ít dữ liệu ({len(closes)} nến)")
                continue
            results[sym] = run_symbol(closes)
        if results:
            ok = True
            text = report(tf, results)
            print(text + "\n")
            if send:
                send_telegram(text)
    if not ok:
        raise SystemExit("Không lấy được dữ liệu coin nào")


if __name__ == "__main__":
    main()
