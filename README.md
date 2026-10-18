# Smart Garden AIoT

Đồ án giám sát và tưới cây bằng ESP32, FastAPI, PostgreSQL và CNN 1D. Dashboard tiếng Việt hiển thị cảm biến, biểu đồ, dự báo, trạng thái thiết bị và lịch sử lệnh tưới. Có ba chế độ **thủ công**, **tự động theo ngưỡng**, **tự động theo AI có dự phòng**.

**Đã huấn luyện CNN thật trên NVIDIA GeForce RTX 2050, PyTorch 2.8.0 CUDA 12.6.** Checkpoint, lịch sử tìm cấu hình và kết quả nằm trong `models/current/`. Dataset công khai chỉ ghi trạng thái bơm, không có thời gian tưới tối ưu. Bản hiện tại dự báo WATER/NO WATER kỳ tiếp theo; số giây tưới lấy từ xung cấu hình khi chưa có mô hình hồi quy được huấn luyện bằng nhãn thực nghiệm. Không coi thời lượng đó là kết quả CNN.

## Bắt đầu trên máy hiện tại

Mở PowerShell tại thư mục này. Python 3.13, môi trường `.venv`, PostgreSQL 18 và checkpoint đã được chuẩn bị.

```powershell
.\scripts\run.ps1
```

Nếu PowerShell chặn script, dùng các lệnh tương đương, không cần thay execution policy toàn máy:

```powershell
.\.venv\Scripts\python.exe scripts/local_db.py start
.\.venv\Scripts\python.exe -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8000
```

Mở **http://127.0.0.1:8000**. Nhập giá trị `ADMIN_TOKEN` từ `.env` vào ô Khóa quản trị. Khóa chỉ được giữ trong bộ nhớ tab. Swagger API ở **http://127.0.0.1:8000/docs**.

PostgreSQL của đồ án lưu riêng tại `runtime/postgres`, cổng **55432**, chỉ lắng nghe `127.0.0.1`. Script không đổi dịch vụ PostgreSQL đã có trên Windows. `.env` được tạo bằng khóa ngẫu nhiên và không được đưa lên Git. Không xóa `runtime/postgres` nếu muốn giữ lịch sử.

## Cài lại trên máy khác

Yêu cầu Python 3.11–3.13, PostgreSQL 18 và driver NVIDIA phù hợp nếu train GPU. Dùng Python 3.13 để khớp môi trường đã kiểm thử.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu126
.\.venv\Scripts\python.exe scripts/local_db.py start
```

Nếu PostgreSQL không ở vị trí mặc định:

```powershell
$env:POSTGRES_BIN = 'C:\Program Files\PostgreSQL\18\bin'
.\.venv\Scripts\python.exe scripts/local_db.py start
```

`requirements-lock.txt` ghi các phiên bản thực tế. Để tái lập đúng môi trường Windows, cài với `--extra-index-url https://download.pytorch.org/whl/cu126`. Không dùng lock Windows làm lock chung cho Linux; Docker sử dụng `requirements.txt` và PyTorch CPU cho inference.

## Demo khi chưa có ESP32

Giữ backend chạy, mở terminal thứ hai:

```powershell
.\.venv\Scripts\python.exe scripts/simulator.py --steps 120 --interval 5
```

Dashboard hiện nhãn **Dữ liệu mô phỏng**. Thử tưới thủ công 5 giây và xem chuỗi trạng thái `queued → delivered → started → completed`. Đây là bơm ảo, không có tác động GPIO. Không chạy giả lập cùng ESP32 thật dùng cùng `DEVICE_ID`. Các bản ghi mô phỏng được đánh dấu và công cụ chuẩn bị dataset thực nghiệm sẽ loại bỏ.

Sau khi mô phỏng dừng, dashboard chuyển ngoại tuyến khi quá 60 giây. Không tự tạo dữ liệu mới để làm biểu đồ đầy.

## Kết nối phần cứng

1. Xem [sơ đồ đấu dây và hiệu chuẩn](docs/HARDWARE.md).
2. Sao chép `firmware/include/garden_config.example.h` thành `garden_config.h`. Điền Wi-Fi, `API_BASE`, `DEVICE_KEY` từ `.env`.
3. Chạy backend trên IP LAN: `.\scripts\run.ps1 -ListenAddress 0.0.0.0`. Chỉ mở cổng 8000 trên mạng riêng khi Windows hỏi firewall.
4. Nạp firmware:

```powershell
$env:PLATFORMIO_CORE_DIR = "$PWD\runtime\platformio"
.\.venv\Scripts\python.exe -m platformio run -d firmware
.\.venv\Scripts\python.exe -m platformio run -d firmware -t upload
.\.venv\Scripts\python.exe -m platformio device monitor -d firmware
```

Firmware mẫu biên dịch được nhưng không có mật khẩu Wi-Fi thật. Mặc định relay active LOW; phải kiểm tra module trước khi cấp nguồn bơm. Chưa thực hiện nạp bo mạch hoặc kiểm thử cây thật trong môi trường phần mềm này.

## Huấn luyện và dùng CNN

```powershell
# Dataset gốc đã có; chỉ chạy download khi cần tải lại
.\.venv\Scripts\python.exe -m ml.download
.\scripts\train_gpu.ps1
# Hoặc
.\.venv\Scripts\python.exe -m ml.train --device cuda --epochs 160 --out models/new_run
```

Mặc định bắt buộc CUDA, không âm thầm chuyển sang CPU. Pipeline gồm CNN 1D, 3 cấu hình × 3 seed, early stopping, trọng số lớp, chuẩn hóa chỉ trên train, validation chọn cấu hình/ngưỡng và test cuối theo thời gian. Ba baseline được huấn luyện trên cùng cửa sổ đầu vào. Chi tiết ở [AI và dữ liệu](docs/AI.md).

Model được nạp khi backend khởi động. Muốn đổi model: đặt `MODEL_DIR=models/new_run`, khởi động lại backend. Inference CPU phù hợp độ trễ 10 giây và không chiếm GPU liên tục; GPU được dùng cho train như yêu cầu.

AI cần **8 mốc liên tục cách nhau 30 phút** (khoảng 4 giờ khởi động thu thập). Thiếu lịch sử, lỗi model hoặc dữ liệu ngoài miền sẽ có lý do và dùng ngưỡng dự phòng. Model công khai mang nhãn `experimental=true`; `ALLOW_EXPERIMENTAL_AI=false` mặc định nên chỉ có ngưỡng điều khiển bơm. Sau khi đánh giá trên chậu thật, có thể đặt biến này `true` để thử AI dưới giám sát; các giới hạn bơm vẫn áp dụng.

## Thời gian tưới bằng hồi quy

Thu thập cảm biến thật, ghi nhãn `water_needed` và `duration_seconds` độc lập theo quy trình [thực nghiệm](docs/EXPERIMENT.md). Trạng thái bơm hoặc số giây do chính thuật toán chọn không tự động trở thành nhãn tối ưu.

```powershell
.\.venv\Scripts\python.exe -m ml.prepare_field --readings data/field/readings.csv --labels data/field/labels.csv --out data/field/training.csv
.\.venv\Scripts\python.exe -m ml.train --data data/field/training.csv --device cuda --out models/field
```

Nhánh hồi quy CNN chỉ được train khi có nhãn thời lượng đủ ở từng tập. Báo cáo thêm MAE, RMSE, R². Hiện chưa có nhãn thực nghiệm để chạy nhánh này và chưa có bằng chứng về thời lượng tưới tối ưu.

## Kiểm thử và vận hành

```powershell
.\.venv\Scripts\python.exe -m pytest -q --postgres --basetemp runtime/pytest-local
node --check web/app.js
# Dừng PostgreSQL riêng của đồ án sau khi đã dừng API bằng Ctrl+C
.\.venv\Scripts\python.exe scripts/local_db.py stop
```

`--postgres` dùng database **smart_garden_test**, tái tạo bảng trong database đó. Không chỉ vào database chứa dữ liệu cần giữ. SQLite chỉ dùng cho unit test không có PostgreSQL, không phải database triển khai.

Chạy Docker là lựa chọn khác:

```powershell
# Điền .env từ .env.example và thêm POSTGRES_PASSWORD nếu chưa có
docker compose up --build -d
docker compose logs -f api
docker compose down
```

Không chạy Docker và backend trực tiếp cùng cổng 8000. Không dùng `down -v` nếu cần giữ dữ liệu. Cấu hình Docker đã chuẩn bị, chưa được chạy kiểm thử Docker daemon trên máy này. HTTP phù hợp LAN demo; triển khai Internet cần reverse proxy HTTPS, xác thực người dùng và giới hạn truy cập. Không công khai cổng PostgreSQL.

## Cấu trúc bàn giao

| Thư mục | Nội dung |
|---|---|
| `backend/` | API, cấu hình, bảng SQLAlchemy, khóa giao dịch, hàng đợi lệnh và ACK |
| `web/` | Dashboard responsive, biểu đồ canvas, xuất CSV, điều khiển Manual/Auto |
| `firmware/` | ESP32, cảm biến, relay, FreeRTOS giới hạn bơm độc lập với HTTP |
| `ml/` | Đọc/chuẩn hóa, chia chuỗi, CNN, train, inference, chuẩn bị dữ liệu thực |
| `data/raw/` | Dataset gốc, bản CSV và thông tin nguồn |
| `models/current/` | 3 checkpoint CNN, 3 baseline, metrics, biểu đồ, dự đoán test |
| `tests/` | Test API, an toàn lệnh tưới, giao dịch PostgreSQL và tính nhất quán ML |
| `scripts/` | Cài đặt, DB, chạy API, giả lập, train GPU |
| `docs/` | Báo cáo, kiến trúc, giao thức, kế hoạch, phần cứng và demo |

Đọc [báo cáo đồ án](docs/BAO_CAO.md), [giao thức API](docs/API.md), [kịch bản demo](docs/DEMO.md), [đối chiếu kế hoạch 6 tuần](docs/KE_HOACH.md) và [kết quả kiểm chứng](docs/VERIFICATION.md).
