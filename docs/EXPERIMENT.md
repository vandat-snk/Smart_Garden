# Quy trình thực nghiệm và nhãn thời gian tưới

## Thiết lập

Chọn một loại cây, loại đất, kích thước chậu và vị trí đầu dò ổn định. Ghi ngày hiệu chuẩn soil sensor và lưu lượng bơm đo thực tế. Ngưỡng 30%/60% là cấu hình demo ban đầu, phải điều chỉnh theo chậu. Ghi mọi thay đổi điều kiện; không gộp các đợt có hiệu chuẩn khác nhau thành một chuỗi liên tục.

Chạy manual hoặc rule có giám sát trước. Thu số liệu ít nhất nhiều chu kỳ khô–ướt; vài ngày chỉ đủ smoke test, không đủ kết luận khả năng tổng quát. Với cảm biến 10 giây, lưu đầy đủ raw và tổng hợp mốc 30 phút khớp model công khai. Muốn dùng tần suất khác, train lại bằng `--interval-seconds`, đồng bộ metadata inference.

## Nhãn độc lập

Tạo `data/field/labels.csv` từ file header `labels.example.csv`. Mỗi mốc đều cần quyết định đã quan sát, bao gồm NO WATER. `timestamp` là mốc UTC của quyết định; `water_needed` là 0/1 có căn cứ; `duration_seconds` là thời gian phù hợp đã đánh giá; `label_source` mô tả cách xác định, `plant_id` định danh chậu, `notes` ghi điều kiện.

Không tự điền mọi mốc thiếu thành NO WATER. Không dùng trực tiếp duration do model hoặc ngưỡng đề xuất làm ground truth. `actual_duration_seconds` từ ACK chỉ cho biết đã điều khiển bao lâu; muốn làm nhãn phải đánh giá thêm kết quả sau tưới (độ ẩm sau khi thấm, nước chảy tràn, tình trạng cây) và lưu căn cứ độc lập. Không lấy dữ liệu sau tưới làm đầu vào cho dự báo trước tưới.

Nếu chỉ có nhãn WATER/NO WATER, bỏ cột duration_seconds khỏi file training. Nếu có regression, từng split phải có ít nhất 5 mẫu WATER được gán nhãn thời lượng; đây chỉ là ngưỡng kỹ thuật tối thiểu, không phải cỡ mẫu đủ cho kết luận khoa học.

## Xuất và chuẩn bị

Xuất CSV từ dashboard, lưu `data/field/readings.csv`. Công cụ `ml.prepare_field` loại simulated và sensor lỗi, kiểm tra chỉ một device, resample cùng cách với inference rồi ghép nhãn theo timestamp. Khoảng trống nhãn tạo gap; train bỏ mọi window đi qua gap. Vì vậy cần ghi nhãn đều cả mốc không tưới, không chỉ lúc bơm bật.

```powershell
.\.venv\Scripts\python.exe -m ml.prepare_field --readings data/field/readings.csv --labels data/field/labels.csv --out data/field/training.csv
.\.venv\Scripts\python.exe -m ml.train --data data/field/training.csv --device cuda --out models/field
```

Giữ lại một đợt theo thời gian làm test, tuyệt đối không điều chỉnh mô hình sau khi nhìn test rồi vẫn gọi kết quả đó là test độc lập. Với nhiều chậu, tách chậu test hoàn toàn; pipeline hiện tại phục vụ một chậu, cần mở rộng xử lý nhóm trước khi ghép nhiều chậu.

## So sánh điều khiển thực tế

So sánh AI và rule trong điều kiện ban đầu tương đương, thay phiên các giai đoạn, ghi nhiệt độ, lượng nước, độ ẩm trước/sau, số lần tưới, thời gian ngoài dải mục tiêu và lỗi. Không khẳng định tiết kiệm nước nếu chỉ có số giây mà chưa đo lưu lượng. Không khẳng định cây khỏe hơn nếu chưa có chỉ tiêu và thời gian theo dõi phù hợp.

| Hạng mục cần ghi | Mục đích |
|---|---|
| ID cây/đợt, timestamp UTC | Đối chiếu dữ liệu và tránh trộn chuỗi |
| ADC khô/ướt, vị trí đầu dò | Kiểm soát sai khác hiệu chuẩn |
| Cảm biến trước quyết định | Đầu vào dự đoán |
| Nhãn người quan sát và căn cứ | Ground truth độc lập |
| Thời lượng yêu cầu, ACK, đo thực | Phân biệt ý định với hành động |
| Độ ẩm sau khi thấm | Đánh giá hiệu quả xung tưới |
| Thể tích đo thực, nước dư | Đánh giá lượng nước và tràn |
| Lỗi mạng/cảm biến, mode | Phân tích các lần không tưới hoặc dừng sớm |

Hiện chưa có dữ liệu cây thật, số đo lưu lượng hoặc video phần cứng. Những hạng mục này phải được nhóm bổ sung bằng thí nghiệm, không thay bằng dữ liệu giả lập.
