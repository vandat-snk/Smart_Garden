# Đối chiếu hai tài liệu gốc và kế hoạch 6 tuần

Đã đọc toàn bộ nội dung đoạn văn và bảng trong `Phan_cong_do_an_Smart_Garden_AI_IoT_6_tuan.docx` và `Ke_hoach_do_an_Smart_Garden_AI_IoT_6_tuan_cap_nhat_gia.docx`. Bản sao giữ nguyên trong `references/`. Đây là nguồn yêu cầu và phân công; không phải bằng chứng các thí nghiệm đã diễn ra.

## Quyết định theo yêu cầu hiện tại

| Điểm | Tài liệu gốc | Bản triển khai |
|---|---|---|
| Database | Phân công ghi SQLite; kế hoạch cho SQLite/PostgreSQL | PostgreSQL theo ưu tiên của người dùng, có kiểm thử khóa hàng |
| AI | Logistic Regression, Decision Tree, Random Forest | Giữ đủ 3 baseline, thêm CNN 1D train GPU |
| Mục tiêu nhãn | WATER/NO WATER | Dự báo trạng thái tưới lịch sử; không đổi tên nhãn thành tối ưu |
| Thời gian tưới | Đề bài người dùng yêu cầu dự đoán thời gian | Có head hồi quy và pipeline nhãn thật; chưa có dữ liệu duration để train |
| Giao thức | Wi-Fi HTTP, có thể MQTT | HTTP thống nhất firmware/backend; không triển khai MQTT dư thừa |
| Ánh sáng | BH1750 hoặc LDR | BH1750, không trộn ADC LDR với lux |
| Mực nước | Nên có HC-SR04/water level sensor | Hỗ trợ phao nhị phân tùy chọn; chưa triển khai HC-SR04 |
| Flow, multi-zone, OLED | Nâng cấp | Chưa triển khai, không thuộc core |

Ngân sách core 300.000–450.000đ là **ước tính trong tài liệu người dùng**, không phải báo giá đã cập nhật hoặc chi phí mua thực tế. Chưa đặt mua linh kiện.

## Phân công bàn giao

| Vai trò | Tỷ trọng mục tiêu theo bản gốc | Mã nguồn/tài liệu | Việc thực tế nhóm tiếp nhận |
|---|---:|---|---|
| Người 1 — Hardware/ESP32 | 33% | `firmware/`, HARDWARE.md, API.md | Lắp, hiệu chuẩn, nạp bo, đo hành vi dừng và lưu lượng |
| Người 2 — AI/Data | 33% | `ml/`, `models/current/`, `data/raw/`, AI.md, EXPERIMENT.md | Thu nhãn độc lập, validation cây thật, train hồi quy |
| Người 3 — Backend/Web | 34% | `backend/`, `web/`, `scripts/`, `tests/`, README.md | Cấu hình LAN, hỗ trợ tích hợp và lưu bằng chứng demo |

Tỷ trọng là kế hoạch, không phải giờ công đã đo. Cả ba cùng tham gia tích hợp và nghiệm thu.

## Lịch thực hiện và tiêu chí

| Tuần | Phần mềm đã chuẩn bị | Phần nhóm phải làm trên thiết bị | Tiêu chí nghiệm thu |
|---|---|---|---|
| 1 — Nền tảng | Kiến trúc, JSON, API, PostgreSQL, tải/kiểm tra dataset | Chuẩn bị linh kiện, đọc từng cảm biến | Dữ liệu đo có đơn vị và hiệu chuẩn được ghi |
| 2 — Cơ bản | Điều khiển, lịch sử, rule, tiền xử lý và baseline | Kiểm tra nguồn, relay, xung ngắn, Wi-Fi | Lệnh được ACK, thời lượng thực đo không vượt trần |
| 3 — MVP | Dashboard, biểu đồ, manual/auto, so sánh mô hình | Nối ESP32 vào API, kiểm tra dữ liệu thật | Thiết bị → PostgreSQL → Web xuyên suốt |
| 4 — AI | CNN GPU, checkpoint, predict, fallback và giới hạn | Thu đủ lịch sử, đánh giá model với hiệu chuẩn của chậu | Phân biệt dự báo AI và rule dự phòng; không ép nhãn |
| 5 — Thực nghiệm | Export, công cụ ghép nhãn và train hồi quy | Nhiều chu kỳ tưới, nhãn có căn cứ, so sánh cùng điều kiện | Có tập dữ liệu thực và đánh giá ngoài mẫu |
| 6 — Bàn giao | Báo cáo phần mềm, kịch bản demo, bảng kiểm thử | Chốt kết quả thực, bổ sung hồ sơ nhóm, quay video | Minh chứng phần cứng, phần mềm và giới hạn đầy đủ |

## Trạng thái theo yêu cầu

- Hoàn thành phần mã và kiểm tra phần mềm: API, PostgreSQL, dashboard, CNN GPU, ba baseline, fallback, lệnh/ACK, xuất lịch sử và giả lập.
- Firmware đã biên dịch: chưa nạp thiết bị, chưa xác nhận cảm biến/relay/bơm trên bàn.
- Có khung huấn luyện hồi quy duration: chưa train vì thiếu nhãn thời gian tối ưu thực nghiệm.
- Có báo cáo kỹ thuật và kịch bản thuyết trình: chưa có số đo cây thật hay video phần cứng; không đánh dấu hoàn tất nghiệm thu vật lý.

Không dùng dữ liệu simulator để hoàn thành ô “kiểm thử cây thật”. Không dùng ảnh dashboard để chứng minh nước đã chảy. Kết quả thực tế cần điền theo [EXPERIMENT.md](EXPERIMENT.md).
