"""
Bot cảnh báo crypto - GIAI ĐOẠN 2 (chạy trên GitHub Actions).

Mỗi lần chạy:
  1. Lấy giá 1h từ Binance, tính RSI, % 24h, khối lượng, xu hướng EMA20/EMA50.
  2. Đọc tin tức crypto mới nhất (RSS CoinDesk, Cointelegraph).
  3. Có cảnh báo -> nhờ Claude giải thích ngắn gọn bằng tiếng Việt -> gửi Telegram.
Chế độ MODE=digest (8h sáng): gửi bản tóm tắt thị trường cả ngày.

Không có ANTHROPIC_API_KEY thì bot vẫn chạy, chỉ thiếu phần AI giải thích.
Chỉ ĐỌC dữ liệu, KHÔNG đặt lệnh.
"""

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests

# ================== CẤU HÌNH ==================
COINS = {  # mã sàn : các từ khóa để lọc tin
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth", "ether"],
    "SOLUSDT": ["solana", "sol"],
    "BNBUSDT": ["bnb", "binance"],
    "XRPUSDT": ["xrp", "ripple"],
}
INTERVAL = "1h"
RSI_PERIOD = 14
RSI_HIGH = 70
RSI_LOW = 30
CHANGE_24H_ALERT = 5.0
VOLUME_SPIKE_X = 3.0
COOLDOWN_SEC = 4 * 3600
NEWS_MAX_AGE_H = 24

DATA_URL = "https://data-api.binance.vision/api/v3/klines"
NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]
CLAUDE_MODEL = "claude-haiku-5-5"   # model nhanh, rẻ
STATE_FILE = "state.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODE = os.getenv("MODE", "alert")   # "alert" hoặc "digest"
DISCLAIMER = "(Chỉ là tham khảo, không phải lời khuyên đầu tư)"
# ==============================================


# ---------- Trạng thái ----------
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------- Dữ liệu giá ----------
def fetch(symbol, limit=100):
    r = requests.get(DATA_URL, params={"symbol": symbol, "interval": INTERVAL,
                                       "limit": limit}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return ([float(k[4]) for k in rows], [float(k[5]) for k in rows],
            [int(k[0]) for k in rows])


# ---------- Chỉ báo ----------
def rsi(closes, period=RSI_PERIOD):
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def ema(values, period):
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def analyze(closes, volumes):
    price = closes[-1]
    a = {"price": price, "rsi": rsi(closes), "chg": None, "volx": None,
         "trend": None, "cross": None}
    if len(closes) > 24:
        a["chg"] = (price - closes[-25]) / closes[-25] * 100
    if len(volumes) > 22:
        avg = sum(volumes[-22:-2]) / 20
        if avg > 0:
            a["volx"] = volumes[-2] / avg
    e20, e50 = ema(closes, 20), ema(closes, 50)
    if len(e50) >= 2:
        # e20 và e50 cùng kết thúc ở nến mới nhất, nên so phần tử cuối là đúng
        now_up = e20[-1] > e50[-1]
        prev_up = e20[-2] > e50[-2]
        a["trend"] = "tăng" if now_up else "giảm"
        if now_up != prev_up:
            a["cross"] = "golden" if now_up else "death"
    return a


def build_alerts(a):
    al = []
    if a["rsi"] is not None:
        if a["rsi"] >= RSI_HIGH:
            al.append(("rsi_high", f"RSI {a['rsi']:.0f} - vùng QUÁ MUA"))
        elif a["rsi"] <= RSI_LOW:
            al.append(("rsi_low", f"RSI {a['rsi']:.0f} - vùng QUÁ BÁN"))
    if a["chg"] is not None and abs(a["chg"]) >= CHANGE_24H_ALERT:
        w = "TĂNG" if a["chg"] > 0 else "GIẢM"
        al.append((f"chg_{w}", f"{w} {a['chg']:+.1f}% trong 24h"))
    if a["volx"] is not None and a["volx"] >= VOLUME_SPIKE_X:
        al.append(("volume", f"Khối lượng gấp {a['volx']:.1f} lần bình thường"))
    if a["cross"] == "golden":
        al.append(("cross_golden", "EMA20 vừa cắt LÊN EMA50 - xu hướng có thể chuyển tăng"))
    elif a["cross"] == "death":
        al.append(("cross_death", "EMA20 vừa cắt XUỐNG EMA50 - xu hướng có thể chuyển giảm"))
    return al


# ---------- Tin tức ----------
def fetch_news():
    """Trả về danh sách {title, source, age_h} trong NEWS_MAX_AGE_H giờ gần nhất."""
    items, now = [], time.time()
    for url in NEWS_FEEDS:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "crypto-alert-bot"})
            r.raise_for_status()
            items += parse_rss(r.content, url, now)
        except (requests.RequestException, ET.ParseError) as e:
            print(f"Lỗi đọc tin {url}: {e}")
    items.sort(key=lambda x: x["age_h"])
    return items


def parse_rss(content, url, now):
    out = []
    source = "CoinDesk" if "coindesk" in url else "Cointelegraph" if "cointelegraph" in url else url
    for it in ET.fromstring(content).iter("item"):
        title = (it.findtext("title") or "").strip()
        pub = it.findtext("pubDate")
        if not title or not pub:
            continue
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:  # "-0000" = giờ UTC không rõ múi -> coi là UTC
                dt = dt.replace(tzinfo=timezone.utc)
            age_h = (now - dt.timestamp()) / 3600
        except (TypeError, ValueError):
            continue
        if age_h <= NEWS_MAX_AGE_H:
            out.append({"title": title, "source": source, "age_h": age_h})
    return out


def news_for(symbol, news, limit=6):
    keys = COINS[symbol]
    hits = []
    for n in news:
        words = n["title"].lower().replace(",", " ").replace(":", " ").split()
        if any(k in words for k in keys):
            hits.append(n)
    return hits[:limit]


def news_lines(items):
    return "\n".join(f"- [{n['source']}, {n['age_h']:.0f}h trước] {n['title']}" for n in items)


# ---------- Claude ----------
def ask_claude(prompt, max_tokens=400):
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60)
        if not r.ok:
            print("Lỗi Claude:", r.status_code, r.text[:300])
            return None
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
    except requests.RequestException as e:
        print("Lỗi gọi Claude:", e)
        return None


RULES = (
    "Quy tắc: viết tiếng Việt, ngắn gọn, dễ hiểu cho người không chuyên. "
    "Chỉ dựa vào số liệu và tiêu đề tin được cung cấp, không bịa thêm sự kiện. "
    "Nếu tin không giải thích được biến động thì nói thẳng là chưa rõ nguyên nhân. "
    "Không khuyên mua hay bán, không đưa giá mục tiêu. Không dùng markdown."
)


def explain_alert(symbol, a, alerts, news):
    headlines = news_lines(news) or "(không có tin nào nhắc tới coin này trong 24h)"
    prompt = (
        f"Bot vừa phát cảnh báo cho {symbol}.\n"
        f"Giá: {a['price']:,.4f} USDT. Thay đổi 24h: {a['chg']:+.1f}%. "
        f"RSI(14, khung 1h): {a['rsi']:.0f}. Xu hướng EMA20/EMA50: {a['trend']}.\n"
        f"Cảnh báo: {'; '.join(m for _, m in alerts)}.\n\n"
        f"Tiêu đề tin tức 24h qua:\n{headlines}\n\n"
        "Hãy viết 3-4 câu: (1) nhiều khả năng biến động đến từ đâu, "
        "(2) điều cần để ý trong vài giờ tới, (3) một rủi ro cần cẩn thận.\n" + RULES
    )
    return ask_claude(prompt)


# ---------- Telegram ----------
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Chưa cài Telegram]\n" + text + "\n")
        return True
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000],
                            "disable_web_page_preview": "true"}, timeout=20)
    if not r.ok:
        print("Lỗi Telegram:", r.status_code, r.text[:200])
    return r.ok


# ---------- Giao dịch giấy (giai đoạn 3b) ----------
PAPER_START = 200.0   # USDT ảo cho mỗi coin
PAPER_FEE = 0.001
PAPER_STOP = 0.05


def paper_signal(closed):
    """Chiến lược 'RSI + xu hướng', tính trên nến đã đóng (cùng quy tắc với backtest.py):
    mua khi RSI <= 40 lúc EMA20 > EMA50; bán khi RSI >= 70 hoặc EMA20 < EMA50."""
    r = rsi(closed)
    e20, e50 = ema(closed, 20), ema(closed, 50)
    if r is None or not e50:
        return None
    up = e20[-1] > e50[-1]
    if up and r <= 40:
        return "buy"
    if r >= 70 or not up:
        return "sell"
    return None


def paper_step(state, sym, a):
    """Trả về tin nhắn nếu vừa có lệnh giấy, ngược lại None."""
    book = state.setdefault("paper", {}).setdefault(
        sym, {"cash": PAPER_START, "coin": 0.0, "entry": 0.0, "bar": 0, "trades": 0, "wins": 0})
    if a["bar"] <= book["bar"]:
        return None  # nến này đã xét rồi
    book["bar"] = a["bar"]
    price = a["closed"][-1]
    sig = paper_signal(a["closed"])
    reason = "tín hiệu RSI + xu hướng"
    if book["coin"] > 0 and price <= book["entry"] * (1 - PAPER_STOP):
        sig, reason = "sell", "cắt lỗ 5%"
    if sig == "buy" and book["coin"] == 0:
        book["coin"] = book["cash"] * (1 - PAPER_FEE) / price
        book["cash"], book["entry"] = 0.0, price
        return f"📝 Lệnh giấy: MUA {sym} giá {price:,.4f} ({reason})"
    if sig == "sell" and book["coin"] > 0:
        pnl = price / book["entry"] - 1
        book["cash"], book["coin"] = book["coin"] * price * (1 - PAPER_FEE), 0.0
        book["trades"] += 1
        book["wins"] += pnl > 0
        return (f"📝 Lệnh giấy: BÁN {sym} giá {price:,.4f} ({reason})\n"
                f"Lãi/lỗ lệnh này: {pnl * 100:+.1f}% | Vốn ảo coin này: {book['cash']:,.2f} USDT")
    return None


def paper_summary(state, data):
    books = state.get("paper", {})
    if not books:
        return ""
    total, lines = 0.0, []
    for sym, b in books.items():
        price = data.get(sym, {}).get("price", b["entry"])
        value = b["cash"] + b["coin"] * price
        total += value
        pos = "đang giữ" if b["coin"] > 0 else "đang cầm tiền"
        lines.append(f"{sym.replace('USDT', ''):<4} {value:,.2f} USDT ({pos}, {b['trades']} lệnh)")
    start = PAPER_START * len(books)
    return (f"📝 Giao dịch giấy: {total:,.2f} / {start:,.0f} USDT ({(total / start - 1) * 100:+.1f}%)\n"
            + "\n".join(lines))


# ---------- Hai chế độ chạy ----------
def collect():
    data, errors = {}, 0
    for sym in COINS:
        try:
            closes, vols, times = fetch(sym)
        except requests.RequestException as e:
            print(f"Lỗi lấy {sym}: {e}")
            errors += 1
            continue
        a = analyze(closes, vols)
        a["closed"] = closes[:-1]   # chỉ các nến đã đóng, dùng cho giao dịch giấy
        a["bar"] = times[-2]        # thời điểm mở của nến đã đóng gần nhất
        data[sym] = a
        chg = f"{a['chg']:+.1f}%" if a["chg"] is not None else "?"
        print(f"{sym:<9} {a['price']:>12,.4f}  24h {chg:>7}  RSI {a['rsi'] or 0:.0f}  xu hướng {a['trend']}")
    if errors == len(COINS):
        raise SystemExit("Không lấy được dữ liệu coin nào")
    return data


def run_alerts():
    state = load_state()
    now = int(time.time())
    data = collect()
    news = None  # chỉ đọc tin khi thật sự có cảnh báo
    for sym, a in data.items():
        trade_msg = paper_step(state, sym, a)
        if trade_msg:
            send_telegram(trade_msg + f"\n\n{DISCLAIMER}")
        sent = state.setdefault(sym, {})
        fresh = [(k, m) for k, m in build_alerts(a) if now - sent.get(k, 0) >= COOLDOWN_SEC]
        if not fresh:
            continue
        if news is None:
            news = fetch_news()
        related = news_for(sym, news)
        text = f"⚠️ {sym}  giá {a['price']:,.4f} USDT\n" + "\n".join(f"• {m}" for _, m in fresh)
        why = explain_alert(sym, a, fresh, related)
        if why:
            text += f"\n\n🤖 Nhận định:\n{why}"
        if related:
            text += "\n\n📰 Tin liên quan:\n" + "\n".join(f"- {n['title']}" for n in related[:3])
        text += f"\n\n{DISCLAIMER}"
        if send_telegram(text):
            for k, _ in fresh:
                sent[k] = now
    save_state(state)


def run_digest():
    data = collect()
    news = fetch_news()
    rows = []
    for sym, a in data.items():
        chg = f"{a['chg']:+.1f}%" if a["chg"] is not None else "?"
        rows.append(f"{sym.replace('USDT', ''):<4} {a['price']:,.4f}  {chg}  RSI {a['rsi'] or 0:.0f}  xu hướng {a['trend']}")
    table = "\n".join(rows)
    summary = ask_claude(
        "Viết bản tin thị trường crypto buổi sáng cho nhà đầu tư cá nhân Việt Nam.\n"
        f"Số liệu (khung 1h, thay đổi 24h, RSI, xu hướng EMA20/50):\n{table}\n\n"
        f"Tiêu đề tin 24h qua:\n{news_lines(news[:20]) or '(không lấy được tin)'}\n\n"
        "Viết 5-7 câu: bức tranh chung, coin nào đáng chú ý và vì sao, "
        "2 tin quan trọng nhất, và điều cần theo dõi hôm nay.\n" + RULES,
        max_tokens=700)
    text = "☀️ Bản tin crypto sáng nay\n\n" + table
    if summary:
        text += f"\n\n🤖 Tóm tắt:\n{summary}"
    elif news:
        text += "\n\n📰 Tin nổi bật:\n" + "\n".join(f"- {n['title']}" for n in news[:5])
    paper = paper_summary(load_state(), data)
    if paper:
        text += f"\n\n{paper}"
    text += f"\n\n{DISCLAIMER}"
    send_telegram(text)


def gh_annotate(level, msg):
    """Hiện thông báo trên trang Actions của GitHub (notice / warning / error)."""
    print(f"::{level}::{msg}")


def run_test():
    """Kiểm tra kết nối Telegram, tự tìm chat id nếu chưa có.
    Không in token hay chat id ra nhật ký (kho Public ai cũng xem được nhật ký)."""
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    if not TELEGRAM_TOKEN:
        gh_annotate("error", "Chưa cài secret TELEGRAM_TOKEN (Settings > Secrets and variables > Actions).")
        raise SystemExit(1)
    me = requests.get(f"{api}/getMe", timeout=20).json()
    if not me.get("ok"):
        gh_annotate("error", f"TELEGRAM_TOKEN sai: Telegram báo '{me.get('description')}'. "
                             "Lấy lại token: @BotFather > /mybots > chọn bot > API Token.")
        raise SystemExit(1)
    bot = "@" + me["result"]["username"]

    if not TELEGRAM_CHAT_ID:
        upd = requests.get(f"{api}/getUpdates", timeout=20).json()
        chats = {}
        for u in upd.get("result", []):
            m = u.get("message") or u.get("edited_message") or {}
            c = m.get("chat", {})
            if c.get("type") == "private":
                chats[c["id"]] = c.get("first_name", "")
        if not chats:
            gh_annotate("error", f"Token đúng (bot {bot}) nhưng chưa thấy ai nhắn cho bot. "
                                 f"Mở Telegram, nhắn 'hi' cho {bot}, rồi chạy lại chế độ test.")
            raise SystemExit(1)
        for cid, name in chats.items():
            requests.post(f"{api}/sendMessage", timeout=20, data={"chat_id": cid, "text": (
                f"👋 Chào {name}! Chat ID của bạn là:\n\n{cid}\n\n"
                "Chép dãy số trên, vào GitHub > Settings > Secrets and variables > Actions > "
                "New repository secret.\nName: TELEGRAM_CHAT_ID\nSecret: dãy số trên\n\n"
                "Sau đó chạy lại chế độ test để xác nhận.")})
        gh_annotate("warning", f"Token đúng (bot {bot}). Chưa có TELEGRAM_CHAT_ID: đã nhắn chat id "
                               f"vào Telegram cho {len(chats)} người từng nhắn bot. Cất nó vào secret rồi chạy lại.")
        return

    r = requests.post(f"{api}/sendMessage", timeout=20, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "✅ Kết nối thành công! Từ giờ cảnh báo crypto sẽ gửi về đây."}).json()
    if not r.get("ok"):
        desc = r.get("description", "")
        hint = (f"Hãy nhắn 'hi' cho {bot} trước." if "chat not found" in desc or "blocked" in desc
                else "Kiểm tra lại secret TELEGRAM_CHAT_ID (chỉ gồm chữ số).")
        gh_annotate("error", f"Token đúng nhưng gửi tin thất bại: '{desc}'. {hint}")
        raise SystemExit(1)
    gh_annotate("notice", f"Telegram hoạt động: đã gửi tin thử qua {bot}.")


if __name__ == "__main__":
    {"digest": run_digest, "test": run_test}.get(MODE, run_alerts)()
