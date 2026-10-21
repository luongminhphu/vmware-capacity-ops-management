# VMware Capacity Ops Management

Ứng dụng theo dõi capacity, compliance và mô phỏng what-if cho hạ tầng VMware, hỗ trợ **truy vấn
live tới 4 vCenter (đọc chỉ đọc)** song song với import CSV/JSON thủ công, lưu lịch sử theo thời
gian bằng PostgreSQL, và giao diện chia hai góc nhìn:

- **Executive View** — hộp "Tóm tắt" (trạng thái, thông điệp chính, điểm cần biết, quyết định cần
  phê duyệt), KPI tổng năng lực CPU/RAM, xu hướng sử dụng theo thời gian, dự báo bao lâu nữa hết
  công suất, top cluster rủi ro cao nhất, các thẻ **Nhận định & đề xuất** (ý nghĩa / việc cần làm /
  nếu chưa hành động / đề nghị phê duyệt) và **Danh mục quyết định cần phê duyệt**. Văn phong ngắn
  gọn; trách nhiệm xử lý quy về **System Owner của từng cluster** (mỗi cluster do một phòng ban
  công nghệ phụ trách), không quy riêng cho IT.
- **Technical View** — dashboard capacity chi tiết, bảng host/VM, compliance findings, what-if
  simulation (Add Host, Add VM, Resize VM, Decommission Idle VM + 6 scenario template dựng sẵn).

Giao diện dùng tone màu lạnh (slate-blue) đồng nhất ở cả dark và light theme, giá trị số
dùng tabular-nums, màu trạng thái AN TOÀN/THEO DÕI/VƯỢT NGƯỠNG nhất quán.

### Xuất báo cáo PDF (khổ A4)

Hai nút **Xuất PDF · Vận hành** và **Xuất PDF · Kỹ thuật** ở header tải báo cáo PDF khổ
A4 (in được trực tiếp) từ `GET /api/reports/pdf?view=executive|technical&vcenter=<key|all>`
(yêu cầu đăng nhập), theo scope vCenter đang chọn trên thanh lọc:

- **Báo cáo Vận hành** (`view=executive`) — **2 trang** (tối đa 3), dành cho góc nhìn quản trị:
  ít chữ, nhiều biểu đồ, đủ ý để ra quyết định; phần giải thích chi tiết để ở Báo cáo Kỹ thuật.
  Trách nhiệm xử lý quy về System Owner của từng cluster:
  - *Trang 1*: tiêu đề gọn (dòng eyebrow ghi phạm vi + ngày; không có đoạn mô tả hay dòng meta
    Phạm vi/Ngày/Trạng thái/Đồng bộ), ngay dưới là hộp **Tổng kết khuyến nghị** (trạng thái + 1 câu
    kết luận + danh sách khuyến nghị đánh số: ưu tiên · đề xuất → đề nghị phê duyệt · thời điểm
    + 5 ô số liệu: dư địa, số tháng tới ngưỡng, dự phòng N+1, VM thu hồi được, số điểm nghiêm
    trọng), gauge CPU/RAM (và Storage nếu bật) cạnh 4 KPI, biểu đồ **Bao lâu nữa chạm ngưỡng** (3 kịch bản +5/+10/+20 %/quý, mốc 1–2–3
    năm) cạnh biểu đồ **Mức dùng theo cluster**, và một bảng **Đề xuất & quyết định cần phê duyệt**
    (đề xuất + 1 dòng việc cần làm → ưu tiên → thời điểm → đề nghị phê duyệt → ô ký; tối đa 6 dòng).
  - *Trang 2*: biểu đồ **Xu hướng** CPU/RAM theo cluster + 4 ô biến động (CPU, RAM, số VM, số
    host), bảng cluster cần quan tâm (có cột N+1), **Điểm cần System Owner xử lý** gồm donut
    finding theo mức độ, biểu đồ nghiêm trọng theo cluster và theo loại, top-6 điểm nghiêm trọng có
    Mã, và một dòng tham chiếu sang Báo cáo Kỹ thuật.
  - Các biểu đồ so sánh/rủi ro theo cluster, donut VM/finding, bảng lịch sử theo cluster và danh
    sách cảnh báo/thông tin không nằm trong Báo cáo Vận hành — xem Báo cáo Kỹ thuật.
- **Báo cáo Kỹ thuật** — cùng bố cục mở đầu với Báo cáo Vận hành: tiêu đề gọn (eyebrow ghi
  phạm vi + ngày, không có đoạn mô tả hay dòng meta/chính sách), rồi hộp **Tổng kết khuyến nghị**
  giống hệt (cùng kết luận, cùng danh sách khuyến nghị và 5 ô số liệu) cộng thêm một hàng 5 ô
  kỹ thuật: Hosts/VMs, CPU overcommit và RAM overcommit thực tế so với ngưỡng (kèm hệ số HT),
  số findings critical/warning/info, chính sách idle + Storage. Phần còn lại là bảng overcommit
  theo cluster, chi tiết từng host, từng datastore, và toàn bộ compliance findings (sắp xếp theo
  severity). Dài hơn, dành cho đội kỹ thuật.

### Báo cáo Quy hoạch tài nguyên (Resource Planning)

Nút **Xuất PDF · Quy hoạch** mở bảng giả định rồi tạo `view=planning` (3–4 trang) để làm căn cứ
đề xuất mở rộng hạ tầng. Tham số (đều là query param của `GET /api/reports/pdf`):
`horizon` (12–60 tháng, mặc định 36), `growth` (các kịch bản %/quý, mặc định `5,10,20` =
Thấp/Cơ sở/Cao), `node_cores` + `node_ram` (spec node mới; để trống = host lớn nhất của từng
cluster), `lead` (tháng mua sắm + triển khai, mặc định 3), `target` (% ngưỡng overcommit coi là
đủ năng lực, mặc định 80). Theo phạm vi vCenter/cluster đang chọn như các báo cáo khác.

- Logic (`backend/app/planning.py`): dự phóng vCPU/RAM **đã cấp phát** cho VM đang bật theo lãi
  kép quý; với mỗi cluster × kịch bản × tháng, tìm số node nhỏ nhất sao cho mức dùng ≤ `target`
  % ngưỡng **và** vẫn ≤ 100% khi mất 1 host lớn nhất (N+1). Nếu có lịch sử snapshot ≥ 30 ngày,
  thêm kịch bản "Xu hướng N tháng" từ tăng trưởng thực tế (toàn phạm vi và từng cluster).
  Khi chỉ RAM là ràng buộc, báo cáo nêu thêm phương án B nâng RAM (bội số 64 GB/host).
- Bố cục (`backend/app/report_planning.py`): cùng mở đầu với hai báo cáo kia — hộp **Tổng kết đề
  xuất** (số node theo kịch bản cơ sở, thời điểm cần/đặt hàng, ràng buộc CPU hay RAM, danh sách
  đề nghị phê duyệt theo cluster), rồi Hiện trạng & giả định, Dự phóng nhu cầu so với năng lực
  (toàn phạm vi + từng cluster, trục chặn ở 200%), Lộ trình bổ sung node theo mốc 6/12/…
  tháng kèm biểu đồ, Đề xuất theo cluster (phương án A thêm node / B nâng RAM).

### Inventory — lọc VM theo cluster & xuất Excel

Tab **Inventory** có bộ lọc **cluster riêng cho bảng VM** (độc lập với bộ lọc chung trên thanh
công cụ; danh sách cluster tự cập nhật theo scope vCenter đang chọn) và hai nút **Xuất Excel**
cho bảng Host và bảng VM. File `.xlsx` xuất **đúng những dòng đang hiển thị** (đã áp dụng scope
vCenter, bộ lọc cluster/host/power/tìm kiếm và thứ tự sắp xếp), kèm sheet *Phạm vi* ghi scope,
bộ lọc và thời điểm xuất. Backend tạo file bằng `openpyxl` tại `POST /api/export/xlsx`
(yêu cầu đăng nhập; header cố định, autofilter, tối đa 100 000 dòng).

> Cache trình duyệt: `index.html` được backend render với tham số phiên bản
> (`js/core.js?v=<fingerprint>` tính từ nội dung JS/CSS) và mọi file tĩnh trả `Cache-Control:
> no-cache`, nên sau mỗi lần deploy image mới trình duyệt tự tải JS/CSS mới — không còn tình
> trạng giao diện mới nhưng logic cũ (ví dụ dropdown cluster trống).

### Mã đối soát (ticket code) — dẫn chứng cho từng khuyến nghị

Mỗi compliance finding có một **mã đối soát ổn định** dạng `HRAM-C4C34`, `IDLE-F916A`… (tiền tố
theo loại: `HCPU`/`HRAM` host vượt ngưỡng, `CCPU`/`CRAM` overcommit cluster, `IDLE` VM idle,
`POFF` VM tắt lâu, `OVP` VM over-provision; 5 ký tự hex là FNV-1a của
`loại|vCenter|cluster|đối tượng`). Mã được tính giống nhau ở backend (`capacity.finding_code`) và
web (`core.js findingCode`), nên cùng một vấn đề mang **cùng mã** ở Báo cáo Vận hành, Báo cáo
Kỹ thuật, tab Compliance và file CSV export; mã không đổi giữa các kỳ nếu vấn đề vẫn còn.

- Mỗi khuyến nghị trong Báo cáo Vận hành có dòng **"Đối soát"** liệt kê mã + đối tượng + chỉ số
  đo được (ví dụ "4 điểm cần System Owner xử lý" liệt kê đủ 4 mã; các danh sách dài như VM
  over-provision hiển thị 6 mã đầu và "+n mã khác"). Danh mục quyết định cũng ghi mã tương ứng.
- Bảng "Điểm nghiêm trọng cần System Owner xử lý" của Báo cáo Vận hành có cột **Mã** và liệt kê các điểm
  severity critical (tối đa 8, phần còn lại ghi "+n"); bảng "Compliance Findings đầy đủ" của Báo
  cáo Kỹ thuật có cùng cột Mã và đủ mọi severity.
- Trên web, bấm vào mã trong Executive View sẽ chuyển sang Technical → tab Compliance và lọc theo
  mã đó; ô tìm kiếm Compliance cũng nhận mã đối soát.
- API: `GET /api/reports/brief` trả `recommendations[].refs {total, items[{code, entity, cluster,
  vcenterKey, typeLabel, metric, severity}]}` và `refNote`; findings có trường `code`.

### Tiêu chí đánh giá — bật/tắt Storage (môi trường SAN)

Với SAN Storage dùng chung, nhiều VM cấp phát **thick provisioning** nên tỷ lệ "đã dùng / cấp
phát" của datastore luôn tiệm cận 100% dù dung lượng SAN thực còn trống — đưa vào báo cáo sẽ gây
cảm giác thiếu an toàn không đúng thực tế. Vì vậy Storage **mặc định không được đánh giá**; CPU
và RAM luôn bật. Nút **Tiêu chí đánh giá** ở header cho phép bật lại Storage khi cần:

- Khi tắt: KPI Storage hiển thị "Không đánh giá", cột %Storage trong bảng cluster, biểu đồ
  datastore, các finding datastore và khối Storage trong what-if simulation đều được ẩn; PDF ghi
  rõ "Storage: không đánh giá trong báo cáo này" kèm giải thích cho người đọc.
- Lựa chọn được lưu vào cài đặt ứng dụng (`include_storage`, chung cho mọi người dùng) và gửi
  theo mỗi lần xuất PDF (`&storage=1|0`); giá trị khởi tạo lấy từ biến môi trường
  `INCLUDE_STORAGE` (mặc định `false`).
- API `GET /api/reports/brief?vcenter=<key|all>&storage=1|0` trả về bản tóm tắt vận hành
  dạng JSON (cùng logic với PDF) để Executive View hiển thị.

### Biến động tài nguyên theo thời gian (snapshot history)

Mỗi lần đồng bộ vCenter được lưu thành snapshot, nên ứng dụng có thể vẽ **biến động CPU/RAM theo
thời gian** với khoảng nhìn lại **3 / 6 / 12 tháng**. Đơn vị theo dõi **mặc định là cluster** (mỗi
cluster = một nhóm dự án độc lập, là cấp đề xuất bổ sung tài nguyên); nhóm theo vCenter là góc nhìn
phụ:

- Panel "Biến động tài nguyên CPU/RAM theo thời gian" xuất hiện ở cả **Executive View** và tab
  **Dashboard** (Technical View): 4 ô KPI đầu kỳ → nay (CPU %, RAM %, số VM, số host), hai biểu
  đồ đường CPU/RAM (% ngưỡng overcommit, có đường ngưỡng 100% và đường "Toàn bộ"), bảng delta
  từng nhóm và 2–3 câu nhận định ngắn. Technical View có thêm hai bảng **VM thêm mới / VM đã
  loại bỏ** trong kỳ. Lựa chọn khoảng thời gian và cách nhóm được lưu trên trình duyệt
  (`vco.historyMonths`, `vco.historyGroupV2`).
- Panel trên tab Dashboard **đi theo bộ lọc Cluster** của trang: chọn một cluster thì biểu đồ,
  bảng delta, nhận định và danh sách VM thêm/bỏ chỉ tính cho cluster đó; Executive View luôn hiển
  thị mọi cluster trong phạm vi vCenter đang chọn.
- Cùng số liệu được đưa vào PDF, luôn theo cluster và **theo đúng phạm vi cluster đã chọn khi xuất
  báo cáo** (không lấy toàn bộ): Báo cáo Vận hành có mục *"Biến động tài nguyên N tháng gần
  nhất"*; Báo cáo Kỹ thuật có mục tương tự và **Phụ lục A — VM thêm mới / loại bỏ** (vCenter,
  cluster, host, trạng thái, vCPU, RAM, ngày xuất hiện/thấy lần cuối; mục A.3–A.5 cho VM di
  chuyển/đổi tên/đổi cấu hình). Tham số
  `&months=3|6|12`, `&clusters=A,B` và `&hosts=h1,h2` của `GET /api/reports/pdf` nhận từ lựa chọn
  trên giao diện. Biểu đồ CPU/RAM luôn ở cấp cluster (đơn vị snapshot được tổng hợp); Phụ lục A
  theo cả cluster và host.
- **Phụ lục A đối chiếu theo định danh VM, loại false positive**: mỗi VM được nhận diện bằng
  `instanceUuid` của vCenter (cột `vm_snapshots.instance_uuid`, tự thêm khi khởi động qua
  `ensure_schema()`; import CSV nhận cột `InstanceUuid`/`VM UUID`/`UUID`); snapshot cũ chưa có UUID
  thì đối chiếu theo tên. *Thêm mới* = định danh chưa có ở **bất kỳ cluster/host nào của vCenter**
  tại đầu kỳ; *loại bỏ* = định danh không còn ở bất kỳ đâu trong vCenter hiện nay. Nhờ vậy các
  trường hợp không phát sinh/giảm tài nguyên **không** bị tính là thêm mới/loại bỏ mà được liệt kê
  riêng để đối soát:
  - **A.3 VM di chuyển**: vMotion giữa host (chỉ liệt kê khi lọc theo host) và migrate giữa cluster
    (từ → đến), kèm vCPU/RAM; bảng tổng hợp có cột "Chuyển vào / ra".
  - **Đổi tên cluster/host**: khi một cluster/host biến mất và cluster/host mới xuất hiện với ≥ 50 %
    host (hoặc VM) trùng nhau (Jaccard, `RENAME_MATCH_MIN`), tên cũ được gộp về tên hiện tại — biểu
    đồ lịch sử liên tục dưới tên mới, bảng tổng hợp ghi "trước: TÊN-CŨ", không tính thêm/bỏ.
  - **A.4 VM đổi tên**: cùng `instanceUuid` nhưng tên khác; khi thiếu UUID dùng heuristic cùng
    cluster + IP + vCPU + RAM (cột "Căn cứ" ghi rõ).
  - **A.5 VM đổi cấu hình vCPU/RAM** được tách riêng vì có làm thay đổi tài nguyên đã cấp
    (trước → nay, delta, tổng tăng/giảm).
  Dòng tóm tắt trong cả hai báo cáo và panel web nêu số trường hợp đã loại
  (`excludedFalsePositives`). Bảng "Tổng hợp theo cluster" có VM đầu kỳ → nay, thêm/bỏ kèm
  vCPU/RAM, chuyển vào/ra, đổi tên VM, đổi cấu hình; khi thu hẹp theo host có thêm bảng theo host.
  Dữ liệu demo (`seed_history`) dựng sẵn các kịch bản: 3 VM chuyển cluster A → B, 4 VM vMotion,
  2 VM đổi tên, 3 VM tăng cấu hình, cụm DR đổi tên từ `DR-CLUSTER-OLD`.
- API: `GET /api/dashboard/history?vcenter=<key|all>&months=3|6|12&group=cluster|vcenter[&clusters=A,B]`
  và `GET /api/dashboard/vm-changes?vcenter=<key|all>&months=3|6|12[&clusters=A,B][&hosts=h1,h2]`. Khi chỉ có 1
  vCenter trong phạm vi, nhãn cluster không kèm tên vCenter. % được tính theo ngưỡng
  overcommit **đang áp dụng** (một snapshot/ngày, lấy lần đồng bộ cuối trong ngày theo giờ VN);
  "thêm mới/loại bỏ" so sánh theo định danh VM (instanceUuid, dự phòng theo tên) giữa snapshot đầu
  kỳ và mới nhất của từng vCenter; phản hồi gồm `added`, `removed`, `moved`, `renamedVms`, `resized`,
  `renamedClusters`, `renamedHosts` và các tổng `*Totals`.
- Cần tối thiểu 2 ngày có snapshot trong khoảng đã chọn; khi chưa đủ, giao diện và PDF hiển thị
  trạng thái "chưa đủ dữ liệu" thay vì biểu đồ. Với môi trường demo có thể tạo lịch sử giả lập
  để xem trước: `cd backend && python -m app.seed_history --months 12 --step-days 7` (chỉ áp
  dụng cho vCenter có `VCENTER_n_DEMO=true`; vCenter thật tích lũy dần qua polling).

Render bằng [WeasyPrint](https://weasyprint.org/) (thuần Python/HTML/CSS, không cần Chromium) —
đã có sẵn system dependencies trong `backend/Dockerfile`. Font Việt dùng bộ Noto Sans để hiển
thị đầy đủ dấu tiếng Việt trong PDF.

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

## Triển khai trên Kubernetes / KubeSphere

Ngoài Docker Compose, thư mục `k8s/` chứa manifest sẵn sàng áp dụng lên cụm KubeSphere
(hoặc bất kỳ cụm Kubernetes chuẩn nào, vì KubeSphere chạy trên Kubernetes gốc). Image
ứng dụng được build và đẩy tự động lên **GitHub Container Registry (GHCR)** qua GitHub
Action `.github/workflows/build-push.yml` mỗi khi push lên nhánh `main`, thành
`ghcr.io/luongminhphu/vmware-capacity-ops-management-app:latest`. Package này hiện đã
**Public** — cụm có thể pull thẳng, không cần Image Pull Secret. Ứng dụng expose ra
ngoài qua **Service NodePort** (không cần Ingress/domain).

### Bước 1 — Tạo Secret chứa thông tin nhạy cảm

```bash
cp k8s/02-secret.example.yaml k8s/02-secret.yaml
# Sửa k8s/02-secret.yaml: mật khẩu Postgres, JWT_SECRET, ADMIN_PASSWORD,
# mật khẩu infra.mon@vsphere.local cho từng vCenter. File này đã có trong
# .gitignore — sẽ không bị commit.
```

### Bước 2 — Sửa `k8s/01-configmap.yaml`

Điền đúng `VCENTER_i_HOST`/`VCENTER_i_NAME`/`VCENTER_i_KEY` cho 4 vCenter thật của bạn
(hoặc để `VCENTER_i_DEMO: "true"` để chạy thử trước với dữ liệu giả lập).

### Bước 3 — Áp dụng lên cụm

Nếu namespace `infra-ops` đã có sẵn (tạo qua KubeSphere Console — Project infra-ops),
**không cần áp dụng `k8s/00-namespace.yaml`**:

```bash
kubectl apply -k k8s/
# hoặc áp dụng từng file theo thứ tự nếu không dùng kustomize:
# kubectl apply -f k8s/01-configmap.yaml \
#   -f k8s/02-secret.yaml -f k8s/03-postgres.yaml -f k8s/04-app.yaml

kubectl -n infra-ops get pods -w
```

Khi Pod `vco-app` ở trạng thái `Running`/`Ready`, truy cập
`http://<IP-bất-kỳ-của-node-trong-cụm>:30080` (đổi `30080` nếu bạn đã sửa `nodePort`
trong `k8s/04-app.yaml`).

### Nếu package GHCR bị chuyển lại về Private

GitHub có tuỳ chọn "Inherit access from source repository" đôi khi tự đưa package
Private trở lại sau mỗi lần build (vì repo nguồn là Private). Nếu gặp lỗi
`ImagePullBackOff` / `401 Unauthorized` khi pull, quay lại **Public** trong Package
Settings (nhớ tắt "Inherit access from source repository" nếu có), hoặc dùng Image
Pull Secret làm phương án chắc chắn:

```bash
kubectl create secret docker-registry ghcr-pull-secret \
  -n infra-ops \
  --docker-server=ghcr.io \
  --docker-username=luongminhphu \
  --docker-password='<PAT có quyền read:packages>' \
  --docker-email='luongminhphu@gmail.com'
```

... rồi thêm lại vào `k8s/04-app.yaml` (trong `spec.template.spec`, trước
`containers:`):

```yaml
      imagePullSecrets:
        - name: ghcr-pull-secret
```

### Lưu ý khi chạy trên Kubernetes

- **Giữ `replicas: 1`** cho `vco-app` — bộ đếm polling vCenter chạy trong tiến trình app,
  không có khoá phối hợp giữa nhiều pod; scale ngang sẽ gây polling trùng lặp vào vCenter.
- Áp dụng đúng thứ tự (Secret + ConfigMap trước Deployment) — nếu sửa
  ConfigMap/Secret sau khi Pod đã chạy, phải `kubectl rollout restart deployment/vco-app -n infra-ops`
  để Pod đọc giá trị mới (Kubernetes không tự nạp lại env khi ConfigMap/Secret đổi).
- Có thể dùng KubeSphere Console (giao diện Workloads → Deployments/StatefulSets) để
  theo dõi/log/scale thay cho `kubectl` — cụm chỉ cần các manifest trên được áp dụng
  trước, sau đó quản lý bằng UI như bình thường.
- Dùng `kubectl get storageclass` để xem StorageClass khả dụng trên cụm của bạn nếu
  PVC của Postgres (`k8s/03-postgres.yaml`) không tự bind (Pending) — thêm
  `storageClassName` tương ứng vào `volumeClaimTemplates`.
- Đây là manifest chưa được kiểm thử trên cụm KubeSphere thật (sandbox phát triển không
  có Kubernetes) — đã kiểm tra kỹ về logic/cấu trúc YAML nhưng bạn nên áp dụng từng bước
  và theo dõi `kubectl describe pod`/`kubectl logs` nếu có sự cố.

## Lưu ý về dữ liệu lịch sử & polling

- Mỗi lần đồng bộ (thủ công qua "Đồng bộ ngay"/"Sync all", hoặc tự động theo
  `POLL_INTERVAL_MINUTES`, mặc định 60 phút) tạo một `SnapshotRun` mới trong Postgres — đây là
  nguồn dữ liệu cho biểu đồ xu hướng (Executive View), panel "Biến động tài nguyên theo thời
  gian" (3/6/12 tháng) và dự báo "còn bao lâu thì hết công suất".
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
