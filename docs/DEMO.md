# Kịch bản demo và thuyết trình

## Demo phần mềm có thể chạy ngay

1. Từ thư mục Smart_Garden, chạy `scripts/run.ps1`. Mở `http://127.0.0.1:8000` và nhập ADMIN_TOKEN trong `.env`.
2. Mở terminal thứ hai: `.\.venv\Scripts\python.exe scripts/simulator.py --steps 120 --interval 5`. Không chạy cùng một ESP32 thật dùng chung ID.
3. Chỉ rõ nhãn **Dữ liệu mô phỏng**. Xem số đo và đổi tab biểu đồ đất/nhiệt độ/độ ẩm/ánh sáng. Dữ liệu được lưu vào PostgreSQL.
4. Ở chế độ thủ công, chọn xung 5 giây và bấm tưới. Quan sát ACK trong lịch sử, trạng thái bơm ảo, sau đó kết thúc xung. Bơm thật chưa được điều khiển trong bước này.
5. Thử gửi yêu cầu tưới tiếp ngay: cooldown sẽ từ chối. Chuyển chế độ gửi lệnh dừng trước. Không tắt giới hạn chỉ để demo tưới liên tục.
6. Chuyển AI và trình bày lý do dự phòng: chưa đủ 8 bin 30 phút hoặc model thử nghiệm đang khóa điều khiển. Demo ngắn không thể tạo ra 4 giờ lịch sử thật. Mở bảng kết quả và ảnh confusion matrix để giải thích model đã train riêng.
7. Xuất CSV, kiểm tra có timestamp, cảm biến và `simulated=True`. Công cụ chuẩn bị dữ liệu thực sẽ loại các dòng này.
8. Chuyển về manual, dừng bơm, cho simulator nhận lệnh rồi dừng simulator. Sau khoảng 60 giây, giao diện phải báo ngoại tuyến và không coi bơm là đã tắt chỉ vì mất liên lạc.

Khởi động lại demo ngay sau một lần tưới có thể vẫn gặp cooldown 300 giây. Chờ đủ thời gian; không xóa database để lách giới hạn. Tắt API bằng Ctrl+C, sau đó chạy `.\.venv\Scripts\python.exe scripts/local_db.py stop` khi kết thúc.

## Demo với bo thật sau khi nghiệm thu phần cứng

Theo [HARDWARE.md](HARDWARE.md), hiệu chuẩn và đo xung ngắn trước. Mở backend trên LAN, nạp Wi-Fi/API_BASE/DEVICE_KEY vào cấu hình riêng. Theo dõi telemetry thật không có cờ simulated. Thử manual, ngưỡng, ACK, dừng đủ ẩm, mất Wi-Fi và lỗi cảm biến. Ghi video thể hiện đồng thời bo, bơm/chậu và dashboard.

AI chỉ được bật điều khiển sau khi kiểm tra miền dữ liệu và đánh giá trên chậu; nếu không đạt, trình bày kết quả nghiên cứu và fallback trung thực. Không tạo giả lịch sử, thay xác suất hoặc hạ threshold sau khi thấy test để làm video thuận lợi hơn.

## Dàn ý thuyết trình 10–12 phút

| Trang | Nội dung nên trình bày | Bằng chứng |
|---|---|---|
| 1 | Tên đề tài, thành viên, vấn đề | Thông tin nhóm thực tế |
| 2 | Mục tiêu và phạm vi một chậu | Sơ đồ kiến trúc BAO_CAO.md |
| 3 | Cảm biến, ESP32 và nguồn bơm | Sơ đồ HARDWARE.md, ảnh bo thật khi có |
| 4 | HTTP, PostgreSQL, lệnh và ACK | API.md; giải thích giới hạn độc lập trên ESP32 |
| 5 | Dataset và nhãn | 960 dòng, 28 dương, nhãn pump status có giới hạn |
| 6 | CNN 1D và GPU | 4×8 đầu vào, 3 cấu hình×3 seed, RTX 2050 |
| 7 | Cách đánh giá | Chia thời gian trước cửa sổ; scaler chỉ học train |
| 8 | Kết quả và baseline | Bảng AI.md, confusion matrix; CNN chưa tốt nhất mọi metric |
| 9 | Demo hệ thống | Manual, lịch sử, biểu đồ, fallback; nói rõ simulator hay thật |
| 10 | Hồi quy thời gian và kết luận | Nhãn còn thiếu, quy trình thu thực nghiệm, giới hạn bằng chứng |

Đây là dàn ý để nhóm xây dựng slide theo mẫu của môn học; chưa phải một tệp PowerPoint hay video đã quay.

## Câu hỏi phản biện thường gặp

**Vì sao CNN thay vì LSTM?** Người dùng yêu cầu CNN; CNN 1D học mẫu biến thiên cục bộ trong cửa sổ thời gian, chi phí nhỏ, có baseline để đo hiệu quả. Chưa có bằng chứng CNN luôn tốt hơn LSTM hoặc mô hình cây.

**Vì sao accuracy 97% vẫn chưa đủ?** Chỉ có 4 mẫu WATER trên test. Luôn NO WATER đạt accuracy cao nhưng bỏ sót toàn bộ tưới; cần precision, recall, F1, AP và xem từng lỗi.

**5 giây có phải AI dự đoán không?** Không ở checkpoint hiện tại. Đó là xung cấu hình; regression chỉ được gọi là đã huấn luyện khi có nhãn duration độc lập và metrics được đánh giá.

**GPU có làm chính xác hơn không?** GPU thực hiện tính toán train nhanh, không tự nâng chất lượng nhãn hoặc khả năng tổng quát. Kết quả phụ thuộc dữ liệu, mô hình và đánh giá đúng.

**Mất mạng thì sao?** Server không gửi lệnh mới khi dữ liệu cũ; firmware có timeout và task giới hạn riêng. Cần đo thời gian dừng trên bo thật để xác nhận.

**Đã tiết kiệm nước bao nhiêu?** Chưa đo. Muốn trả lời phải có lượng nước thực, điều kiện đối chứng và nhiều lần thử; không suy ra tiết kiệm từ demo hoặc số giây đơn lẻ.
