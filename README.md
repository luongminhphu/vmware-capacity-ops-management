# VMware Capacity Ops Management

Ứng dụng theo dõi capacity, compliance và mô phỏng what-if cho hạ tầng VMware, hỗ trợ **truy vấn
live tới 4 vCenter (đọc chỉ đọc)** song song với import CSV/JSON thủ công, lưu lịch sử theo thời
gian bằng PostgreSQL, và giao diện chia hai góc nhìn:

- **Executive View** — KPI tổng năng lực CPU/RAM, xu hướng sử dụng theo thời gian, dự báo bao lâu
  nữa hết công suất, top cluster rủi ro cao nhất. Dành cho lãnh đạo ra quyết định đầu tư/mở rộng.
- **Technical View** — dashboard capacity chi tiết, bảng host/VM, compliance findings, what-if
  simulation (Add Host, Add VM, Resize VM, Decommission Idle VM + 6 scenario template dựng sẵn).

Giao diện theo **HSC Design System** (nền tối, giá trị số dùng tabular-nums, màu trạng thái
AN TOÀN/THEO DÕI/VƯỢT NGƯỠNG nhất quán).

## Kiến trúc

```
vmware-capacity-ops/
├── backend/                  FastAPI (Python) — API, auth, polling scheduler, DB models
│   ├── app/
│   │   ├── main.py            entrypoint, mount static frontend
│   │   ├── config.py          env-based settings (DB, JWT, 4 vCenter blocks)
│   │   ├── security.py        bcrypt hashing + JWT cookie session
│   │   ├── models.py          SQLAlchemy models (User, SnapshotRun, Host/VM rows, AppSetting)
│   │   ├── capacity.py        capacity model + compliance checks (Python port của core.js)
│   │   ├── vcenter/client.py  pyVmomi client — đọc host/VM/cluster/datastore qua vSphere API
│   │   ├── vcenter/demo_generator.py  sinh dữ liệu giả lập khi DEMO=true (không cần vCenter thật)
│   │   ├── collectors/scheduler.py    poll định kỳ + lưu SnapshotRun lịch sử
│   │   ├── data_access.py     query lớp dữ liệu mới nhất / theo vCenter / lịch sử trend
│   │   ├── routers/           auth, vcenters, data, dashboard, compliance, imports, export, settings
│   │   └── seed.py            CLI tạo/đổi mật khẩu user đăng nhập
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                 HTML + vanilla JS/CSS (HSC Design System), không build step
│   ├── index.html
│   ├── css/styles.css
│   └── js/
│       ├── api.js             wrapper gọi API backend (cookie session)
│       ├── core.js             toàn bộ logic capacity/compliance/what-if (port từ bản single-file gốc)
│       ├── views/executive.js  render Executive View (KPI, trend chart, forecast, top-risk)
│       └── app.js              bootstrap, login, perspective toggle, live-data loader
├── docker-compose.yml         services: db (Postgres) + app (FastAPI phục vụ luôn frontend)
├── .env.example               toàn bộ biến môi trường cần cấu hình
└── README.md
```

Backend phục vụ **cả API lẫn frontend tĩnh** trên cùng một port — không cần CORS phức tạp, không
cần reverse proxy riêng cho frontend.

## Bắt đầu nhanh (Docker Compose)

```bash
cp .env.example .env
# Sửa .env: đặt mật khẩu Postgres, JWT_SECRET, ADMIN_PASSWORD, và 4 khối VCENTER_i_*
docker compose up -d --build
```

Mở `http://<server-ip>:8000` (hoặc cổng bạn đặt ở `APP_PORT`), đăng nhập bằng
`ADMIN_USERNAME` / `ADMIN_PASSWORD` đã đặt trong `.env`.

Muốn thử trước khi có vCenter thật: đặt `VCENTER_i_DEMO=true` cho các khối vCenter (bỏ trống
`HOST`/`PASSWORD`) — hệ thống sẽ tự sinh dữ liệu giả lập hợp lý để bạn xem qua giao diện.

### Đổi mật khẩu / tạo thêm user đăng nhập

```bash
docker compose exec app python -m app.seed --username <user> --password '<pass>' --display-name "Tên hiển thị"
```

## Tạo tài khoản read-only trên vCenter (bắt buộc trước khi trỏ vào vCenter thật)

Ứng dụng chỉ **đọc** dữ liệu (host, cluster, VM, datastore) — không tạo, sửa, xoá gì trên vSphere.
Tạo một tài khoản dùng chung cho cả 4 vCenter, ví dụ `infra.mon@vsphere.local`, và gán quyền theo
các bước sau trên **từng vCenter** (thực hiện trên vSphere Client, vCenter 8.x):

1. **Administration → Single Sign On → Users and Groups** — tạo user `infra.mon` trong domain
   `vsphere.local` (hoặc dùng tài khoản AD/LDAP đã có nếu vCenter đã join domain).
2. **Administration → Access Control → Roles** — tạo custom role, ví dụ `CapacityOpsReadOnly`,
   dựa trên role có sẵn **Read-Only**, và bật thêm các quyền sau (đã đủ để đọc CPU/RAM/datastore
   usage, không cần quyền ghi):
   - `Host.Inventory.*` (chỉ phần *View*, không cần Modify)
   - `Datastore.Browse datastore`
   - `Global.Settings` (để đọc thông tin phiên bản, tuỳ chọn)
   - Role gốc **Read-Only** đã bao gồm quyền xem VM, Host, Cluster, Datastore, Network — với hầu
     hết môi trường, **Read-Only mặc định là đủ** và không cần custom role.
3. **Administration → Access Control → Global Permissions** (khuyến nghị — áp dụng cho toàn bộ
   inventory kể cả cluster/datacenter tạo sau này) hoặc gán ở cấp **vCenter Server** trong
   **Hosts and Clusters**:
   - Add Permission → chọn user `infra.mon@vsphere.local` → Role = `Read-Only` (hoặc custom role
     ở bước 2) → tick **Propagate to children**.
4. Lặp lại bước 1–3 trên **cả 4 vCenter** với cùng username/password (đơn giản hoá vận hành) hoặc
   đặt mật khẩu khác nhau — cấu hình từng vCenter độc lập trong `.env` (`VCENTER_i_PASSWORD`).
5. Nếu vCenter dùng chứng chỉ tự ký (self-signed), để `VCENTER_i_VERIFY_SSL=false` (mặc định) —
   kết nối vẫn mã hoá TLS, chỉ bỏ qua việc xác thực chuỗi chứng chỉ. Đặt `true` nếu vCenter dùng
   chứng chỉ hợp lệ từ CA nội bộ/công cộng.

Sau khi cấu hình xong, kiểm tra nhanh bằng nút **"Đồng bộ ngay"** trên giao diện (góc trên bên
phải danh sách vCenter) — nếu có lỗi kết nối/permission, thông báo lỗi cụ thể sẽ hiện ngay tại chip
vCenter tương ứng.

## Lưu ý về dữ liệu lịch sử & polling

- Mỗi lần đồng bộ (thủ công qua "Đồng bộ ngay"/"Sync all", hoặc tự động theo
  `POLL_INTERVAL_MINUTES`, mặc định 60 phút) tạo một `SnapshotRun` mới trong Postgres — đây là
  nguồn dữ liệu cho biểu đồ xu hướng (Executive View) và dự báo "còn bao lâu thì hết công suất".
- Import CSV/JSON thủ công cũng được lưu thành `SnapshotRun` riêng (nguồn `import`) nên vẫn góp
  phần vào lịch sử xu hướng.
- `lastActivityDays`/`powerOffDays` của VM được tính dựa trên **lịch sử polling của chính ứng
  dụng** (vCenter API không trả trực tiếp "đã tắt bao lâu") — nghĩa là ngay sau khi triển khai lần
  đầu, các chỉ số này sẽ cần vài chu kỳ polling để chính xác dần theo thời gian thực tế.
- Dữ liệu import (CSV/JSON) và dữ liệu live từ vCenter là **hai tập dữ liệu làm việc loại trừ lẫn
  nhau tại một thời điểm** trên giao diện: chọn một vCenter (hoặc "Tất cả") sẽ tải dữ liệu live;
  tải file CSV/JSON sẽ chuyển sang chế độ xem dữ liệu import. Đây là hành vi có chủ đích (giữ
  nguyên từ bản single-file gốc), không phải lỗi.

## Bảo mật

- Đăng nhập cơ bản bằng username/password (bcrypt hash), phiên làm việc qua JWT lưu trong cookie
  `httpOnly` (không truy cập được từ JavaScript, giảm rủi ro XSS đánh cắp session).
- Đặt `COOKIE_SECURE=true` khi triển khai sau HTTPS (reverse proxy/TLS terminator) để cookie chỉ
  gửi qua kết nối mã hoá.
- Mật khẩu vCenter và `JWT_SECRET`/`ADMIN_PASSWORD` chỉ nằm trong `.env` (đã thêm vào
  `.gitignore`), không commit vào Git.
- Tài khoản vCenter chỉ cần quyền **Read-Only** — không có rủi ro ứng dụng vô tình thay đổi cấu
  hình hạ tầng.

## Kiểm thử đã thực hiện trong quá trình phát triển

Đã kiểm thử trực tiếp trong môi trường phát triển (không dùng Docker do sandbox không có Docker):
chạy backend bằng `uvicorn` + PostgreSQL cục bộ, 4 vCenter ở **chế độ DEMO** (dữ liệu giả lập, vì
sandbox phát triển không truy cập được vCenter thật của bạn) — xác nhận: đăng nhập, đồng bộ dữ liệu
4 "vCenter" demo (52 host / 406 VM tổng cộng), cả hai góc nhìn Executive/Technical render đúng số
liệu và biểu đồ, chuyển tab Dashboard/Compliance/Inventory/What-if hoạt động, chạy thử what-if
"Add Host" cho ra kết quả before/after chính xác.

**Chưa kiểm thử được** (cần môi trường thật của bạn để xác nhận): kết nối tới 4 vCenter 8.x thật
qua pyVmomi với tài khoản `infra.mon@vsphere.local`, chạy bằng `docker compose up` thật sự (viết
đúng theo chuẩn Compose nhưng không chạy được trong sandbox phát triển do không có Docker daemon).
Khuyến nghị bước đầu tiên sau khi nhận bàn giao: chạy `docker compose up -d --build`, đặt một
vCenter thật với `DEMO=false`, bấm "Đồng bộ ngay" và kiểm tra log (`docker compose logs -f app`)
nếu có lỗi kết nối/permission.
