# OpenBao server mode, lưu bền bằng raft (ADR-0018).
# Mạng data là internal nên tắt TLS ở dev/staging; prod: bật TLS (tls_cert_file/tls_key_file).
ui            = false
disable_mlock = true
api_addr      = "http://openbao:8200"
cluster_addr  = "http://openbao:8201"

storage "raft" {
  path    = "/openbao/file"
  node_id = "aries-1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}
