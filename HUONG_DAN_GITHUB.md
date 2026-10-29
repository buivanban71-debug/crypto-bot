# Chạy bot 24/7 miễn phí trên GitHub Actions

Cứ 15 phút, máy chủ của GitHub chạy bot một lần. Mỗi lần, bot lấy giá thật và gửi cảnh báo về Telegram.
Từ giai đoạn 2: mỗi cảnh báo kèm đoạn AI giải thích dựa trên tin tức 24h qua, và 8h sáng mỗi ngày bot gửi bản tin tóm tắt thị trường.
Bạn không cần bật máy tính hay điện thoại. Mọi bước dưới đây làm được bằng trình duyệt trên điện thoại.

## Cần chuẩn bị
- **TELEGRAM_TOKEN**: lấy từ @BotFather.
- **TELEGRAM_CHAT_ID**: lấy từ @userinfobot. Nhớ nhắn cho bot của bạn 1 tin trước.
- Hai file: `bot_actions.py` và `crypto-alert.yml`.

## Bước 1 – Tạo tài khoản và kho chứa
1. Vào **github.com** → Sign up (nếu chưa có tài khoản).
2. Bấm **+** → **New repository**.
3. Đặt tên `crypto-bot`, chọn **Public**, rồi bấm **Create repository**.
   - Chọn Public thì được chạy miễn phí không giới hạn. Token Telegram vẫn được giấu an toàn (bước 3).
   - Nếu chọn Private, gói miễn phí chỉ có 2.000 phút/tháng. Khi đó phải sửa dòng `*/15` thành `*/30` trong file yml.

## Bước 2 – Thêm 2 file
1. Trong kho vừa tạo → **Add file** → **Create new file**.
2. Ô tên file gõ: `bot_actions.py`. Dán toàn bộ nội dung file vào → **Commit changes**.
3. Lại **Add file** → **Create new file**.
4. Ô tên file gõ đúng: `.github/workflows/crypto-alert.yml` (dấu `/` sẽ tự tạo thư mục).
   Dán nội dung file yml vào → **Commit changes**.

## Bước 3 – Cất token bí mật
1. Vào **Settings** → **Secrets and variables** → **Actions**.
2. Bấm **New repository secret**:
   - Name: `TELEGRAM_TOKEN`, Secret: token của bạn → Add.
3. Thêm cái thứ hai:
   - Name: `TELEGRAM_CHAT_ID`, Secret: chat id của bạn → Add.

## Bước 3b – Khóa AI cho phần giải thích (giai đoạn 2, không bắt buộc)
Thiếu khóa này bot vẫn chạy, chỉ không có đoạn "🤖 Nhận định".
1. Vào **console.anthropic.com** → đăng nhập → **API Keys** → **Create Key** → chép khóa (bắt đầu bằng `sk-ant-`).
2. Vào **Billing** → nạp một ít tiền. API tính tiền riêng, gói Claude Pro không bao gồm API.
   Bot dùng model Haiku là loại rẻ nhất, mỗi lần chỉ gọi khi có cảnh báo và 1 lần cho bản tin sáng.
   Nên đặt giới hạn chi tiêu theo tháng trong mục **Limits** để yên tâm.
3. Quay lại GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
   - Name: `ANTHROPIC_API_KEY`, Secret: khóa vừa chép → Add.

## Bước 4 – Chạy thử
1. Vào tab **Actions**. Nếu GitHub hỏi, bấm nút cho phép chạy workflow.
2. Chọn **Canh bao crypto** → **Run workflow** → chọn `alert` (cảnh báo) hoặc `digest` (bản tin sáng) → **Run workflow**.
3. Đợi khoảng 30 giây, bấm vào lượt chạy → **check** → **Chay bot** để xem giá in ra.
4. Có dấu ✅ xanh là xong. Từ giờ bot tự chạy 15 phút/lần.

## Giai đoạn 3 – Backtest và giao dịch giấy
Bot theo dõi 3 coin: BTC, ETH, SOL. Muốn đổi, sửa `COINS` đầu file `bot_actions.py`.

- **Backtest (Chủ nhật 9h sáng):** thử 3 chiến lược trên 180 ngày giá gần nhất, cả khung 1h và 4h
  (2 tin nhắn). Mỗi dòng có lãi/lỗ, số lệnh, tỉ lệ thắng, mức sụt tối đa, và "hơn mua-giữ k/3 giai đoạn":
  180 ngày chia 3 giai đoạn 2 tháng, chiến lược thắng mua-giữ ở cả 3 mới là ổn định.
  Chạy ngay: Actions → Run workflow → chọn `backtest`.
- **Giao dịch giấy:** 3 chiến lược chạy song song, mỗi chiến lược 1.000 USDT ảo chia đều cho 3 coin.
  Lệnh ảo báo qua Telegram (📝 [tên chiến lược] MUA/BÁN). Bản tin 8h sáng có bảng so sánh 3 chiến lược.
- **Cảnh báo EMA:** chỉ báo khi EMA20 cắt EMA50 trên khung 4h (khung 1h báo quá nhiều lúc giá đi ngang).
- Quy tắc 3 chiến lược nằm trong `strategies.py`, backtest và giao dịch giấy dùng chung.
- Nên để giao dịch giấy chạy ít nhất 1–2 tháng trước khi tin một chiến lược nào.

## Lưu ý
- GitHub có thể chạy trễ 5–15 phút so với lịch, nhất là giờ cao điểm.
- Với kho Public, nếu kho không có thay đổi gì trong 60 ngày, GitHub sẽ tạm tắt lịch chạy và gửi email báo.
  Khi đó vào tab Actions bấm bật lại là được.
- Muốn đổi coin hoặc ngưỡng cảnh báo: mở `bot_actions.py` trên GitHub → bấm biểu tượng bút chì → sửa phần CẤU HÌNH → Commit.
- Bot chỉ đọc giá, không đặt lệnh. Tín hiệu chỉ để tham khảo, không phải lời khuyên đầu tư.
