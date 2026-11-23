# ADR-0023: Tool động theo connection và chính sách đích mạng cho connector

- Trạng thái: Accepted
- Ngày: 2026-11-23

## Bối cảnh
Cần nối agent tới hệ thống nội bộ theo nhiều cách: REST/HTTP (OpenAPI), gRPC, Kafka, Redis. Hai rào
cản trong thiết kế cũ:

1. `PluginManifest.tools` là danh sách **cố định theo plugin**. Với REST/gRPC, mỗi connection lại có
   bộ thao tác riêng (lấy từ OpenAPI hoặc gRPC reflection), nên không khai báo trước được.
2. Connector kết nối TCP thẳng (Postgres, sắp tới là Kafka, Redis, gRPC), không qua Squid. Người có
   vai trò Builder tạo được connection trỏ vào hạ tầng của chính ARIES (`redis:6379`,
   `postgres:5432`, `openbao:8200`) hoặc vào metadata cloud (`169.254.169.254`).

## Quyết định

### 1. Tool động (Plugin API v1, chỉ thêm, không phá tương thích)
- `PluginManifest.dynamic_tools: bool`: connector khám phá tool theo từng connection.
- `Plugin.discover_tools(connection) -> list[ToolSpec]`: core gọi khi **test thành công** (UI tự
  test sau khi lưu) và qua `POST /connections/{id}/sync-tools`. Timeout 30 giây.
- `ToolSpec.binding`: dữ liệu riêng của plugin (method + path, tên RPC…). Lưu cùng tool, trả lại qua
  `ToolContext.tool` lúc gọi, **không** gửi cho model và không trả ra API.
- Core lọc kết quả khám phá: tên tool phải có tiền tố `<connector_id>.`, không trùng tool tĩnh,
  `input_schema` phải hợp lệ, tối đa 200 tool. Tool động luôn được đánh dấu `untrusted_output`.
- Lưu ở `connections.connections.tools` (jsonb) + `tools_synced_at`. Tool hiệu lực của connection
  gồm tool tĩnh và tool động; Architect, kiểm tra AgentSpec và runtime đều dùng chung bộ này.
- `risk_overrides`: người quản lý connection ghi đè mức rủi ro của tool động. **Chỉ Admin được hạ**
  mức rủi ro (vì hạ mức là bớt bước duyệt); Builder chỉ được nâng. Đồng bộ lại sẽ bỏ các ghi đè của
  tool không còn tồn tại.

### 2. Chính sách đích mạng (`aiagent.plugin_sdk.net`)
- `Plugin.network_targets(config) -> list[Endpoint]`: connector khai báo các đích (host, port) mà
  cấu hình trỏ tới. Core kiểm tra hai lần:
  - lúc tạo hoặc sửa cấu hình: chỉ kiểm cú pháp, không phân giải DNS, lỗi thì trả 422;
  - trước **mỗi** lần mở connection (gọi tool, test, trigger, khám phá tool): kiểm đầy đủ, kể cả
    mọi IP mà tên máy phân giải ra.
- Luôn chặn: loopback (trừ khi bật `connector_allow_loopback` cho dev), link-local (metadata cloud),
  địa chỉ unspecified, multicast, reserved, và **dịch vụ hạ tầng của ARIES** theo tên máy và theo IP
  mà các tên đó phân giải ra. Như vậy dùng một tên DNS khác hay IP container trỏ vào Redis vẫn bị
  chặn.
- Danh sách hạ tầng gồm tên service trong compose, host trong mọi URL của cấu hình (DB, Redis,
  OpenBao, OIDC, embedder, Ollama, `plugin_config`) và `connector_blocked_hosts`.
- IP riêng (10.x, 172.16.x, 192.168.x) **được phép**, vì hệ thống công ty nằm ở đó. Có thể giới hạn
  thêm bằng `connector_allowed_networks` (CIDR).
- Ngoại lệ có chủ đích: `connector_allowed_internal_hosts` (ví dụ `["postgres"]` cho bản demo
  `aries_demo`). Không dùng ở prod.
- `ConnectionContext.network` mang chính sách tới plugin để plugin tự kiểm các đích phát sinh lúc
  chạy (redirect, URL trong tham số).
- Web.fetch vẫn giữ chính sách riêng, chặt hơn (chỉ địa chỉ công khai), nhưng dùng chung các hàm
  phân giải và phân loại IP của module này.

### 3. Connector REST / HTTP API (`http_api`)
- Cấu hình `base_url`, kiểu xác thực (none, bearer, API key ở header/query, basic) và secret
  `credential`. `PluginManifest.optional_secrets` cho phép để trống secret khi API không xác thực.
- Tool động: `http_api.get` (read); `http_api.send` (destructive, chỉ khi `allow_write`); mỗi thao
  tác OpenAPI 3.x/Swagger 2.0 thành một tool (GET → read, POST/PUT/PATCH → write,
  DELETE → destructive). Thao tác ghi chỉ được sinh ra khi bật `allow_write`.
- Không cho thoát khỏi `base_url`, giới hạn theo `allowed_paths`, không tự theo redirect, giới hạn
  phản hồi 1 MB, xóa credential nếu API trả nó ra trong kết quả. Chỉ gửi credential khi tải tài
  liệu OpenAPI nằm cùng máy với API.

### 4. Connector Redis, Kafka, gRPC
- **Redis** (`redis`, tool tĩnh): `redis.scan`, `redis.get` (string/hash/list/set/zset/stream, giới
  hạn số phần tử), `redis.set` (write), `redis.delete` (destructive). Mọi key/pattern phải nằm trong
  `key_prefixes` (bắt buộc); không có lệnh tùy ý (EVAL, CONFIG, FLUSH…). Ghi chỉ khi `allow_write`.
- **Kafka** (`kafka`, aiokafka): `kafka.list_topics`, `kafka.peek` (N message mới nhất; gán
  partition trực tiếp, không consumer group, không commit offset), `kafka.produce` (external_send,
  chỉ khi `allow_produce`). Giới hạn theo `allowed_topics` (tên hoặc tiền tố `*`); hỗ trợ SASL
  PLAIN/SCRAM và SSL. Core chỉ kiểm `bootstrap_servers`; broker do cluster quảng bá thì không được
  kiểm lại, nên dùng `connector_allowed_networks` nếu cần chặn chặt.
- **gRPC** (`grpc`, tool động): khám phá qua server reflection (v1alpha); mỗi method unary của
  service được phép thành một tool, schema sinh từ protobuf theo ánh xạ JSON của proto3. Rủi ro
  theo tên method: Get/List/Search… → read, còn lại → write; Admin chỉnh được. TLS (CA hệ thống),
  xác thực bằng metadata, không qua HTTP proxy. Máy chủ không bật reflection: dán nội dung một file
  `.proto` (`proto_source`, chỉ import được `google/protobuf/*`); core biên dịch bằng `protoc`
  (grpcio-tools) trong **tiến trình con** có timeout, bắt thông báo lỗi để hiện cho người dùng.
  Chưa hỗ trợ streaming.

### 5. TLS dùng chung (`aiagent.plugin_sdk.tls`)
- `ca_cert`, `client_cert` (PEM, cấu hình công khai) + secret `client_key` cho REST, Redis, Kafka,
  gRPC. Kiểm cú pháp PEM; báo lỗi rõ khi chứng chỉ và khóa không khớp nhau.
- Thư viện chỉ nhận đường dẫn file (ssl.load_cert_chain, redis-py) → ghi thư mục tạm quyền 0600 và
  xóa ngay sau lượt gọi. gRPC nhận PEM dạng bytes trực tiếp.
- UI: thuộc tính có `contentMediaType` hiện thành ô nhiều dòng; secret khai báo trong
  `ui_hints.multiline_secrets` hiện thành ô nhiều dòng có che chữ.

### 6. Trigger theo connector cho lịch chạy tự động
- Kiểu kích hoạt của lịch chạy = `cron` hoặc **id của connector có khai báo trigger** (gmail,
  kafka…), thay cho danh sách cố định. Migration 0013 đổi ràng buộc `kind` thành kiểm mẫu tên.
- Bộ lọc của trigger được kiểm theo `TriggerSpec.config_schema` do connector khai báo; UI sinh form
  từ schema này. Chu kỳ kiểm tra từ 1 tới 60 phút.
- `kafka.new_message`: lần đầu chỉ ghi mốc offset cuối; mỗi lần quét tối đa 500 message, phát tối
  đa 10 sự kiện khớp bộ lọc `contains`; message không khớp vẫn tính là đã đọc. Offset lưu ở state
  của lịch, không commit lên Kafka. Giữ đúng thứ tự offset trong từng partition (timestamp do
  producer đặt có thể lộn xộn). Id sự kiện `kafka:<topic>:<partition>:<offset>` chống chạy lặp.
  Phù hợp topic sự kiện nghiệp vụ lưu lượng thấp/vừa, không thay được consumer cho luồng lớn.

### 7. Sửa lỗi OAuth trong Credential Broker
- Làm mới token: khóa theo từng connection và đọc lại vault sau khi có khóa, nên nhiều tool call
  đồng thời chỉ làm mới một lần. Trước đây nhà cung cấp xoay vòng refresh token sẽ từ chối lượt
  thứ hai. Khóa chỉ trong một tiến trình; api và worker vẫn có thể hiếm khi va nhau.
- PATCH `config` của connection OAuth luôn giữ nguyên `oauth_provider`, `account`,
  `userinfo_url`, để không thể lộ refresh token cho plugin hay gửi access token sang URL khác.

### 8. Bổ sung (2026-11-24)
- **REST: OAuth2 client credentials** (`auth_type=oauth2_client_credentials`): `token_url`,
  `client_id`, `oauth_scope`, client secret ở `credential`, gửi qua Basic hoặc body. Token được cache
  tới gần lúc hết hạn; API trả 401 thì lấy token mới và thử lại đúng một lần. Token mới cũng bị xóa
  khỏi kết quả nếu API trả nó ra. `token_url` cũng qua chính sách mạng.
- **REST: body form / multipart / text**: `http_api.send` có thêm `body_type`, và `files` dạng
  `{filename, content_type, text | base64}`, tổng tối đa 5 MB. OpenAPI: nhận ra
  `application/x-www-form-urlencoded`, `multipart/form-data` (trường `format: binary` thành tham số
  file) và `text/*`; Swagger 2 `formData`.
- **Redis Cluster / Sentinel** (`mode`): cluster dùng `nodes`, sentinel dùng `sentinels` +
  `service_name` + secret `sentinel_password`. Node do hệ thống quảng bá (danh sách node sau khi nạp
  bảng slot, địa chỉ master lấy từ sentinel) **được kiểm theo chính sách mạng trước khi chạy lệnh**.
  `redis.scan` dùng `scan_iter` có timeout, quét được mọi node primary.
- **gRPC streaming**:
  - server-stream: thu tối đa `max_stream_messages`; stream không tự kết thúc (watch) dừng sau
    `stream_timeout_s` và trả phần đã nhận (`ended`: completed | limit | timeout);
  - client-stream và bidi: nhận mảng `requests` (tối đa 100);
  - method tên Watch/Stream/Subscribe/Tail xếp mức đọc.

## Hệ quả
- Connector REST, gRPC, Kafka, Redis chỉ cần hiện thực `network_targets` và, nếu cần,
  `discover_tools`. Phân quyền, duyệt, taint và audit tái dùng toàn bộ cơ chế có sẵn.
- Giới hạn còn lại: DNS rebinding giữa lúc kiểm và lúc plugin kết nối. Phân vùng mạng
  Docker/firewall vẫn là lớp chặn chính; đây là lớp kiểm tra ở ứng dụng.
- Tool động có thể cũ đi nếu hệ thống đích đổi API; khi đó người dùng test hoặc đồng bộ lại.
