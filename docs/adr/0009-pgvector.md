# ADR-0009: pgvector làm vector store

- Trạng thái: Accepted

## Quyết định
Dùng PostgreSQL + pgvector (HNSW) + tsvector cho hybrid search, sau VectorStorePort.

## Hệ quả
+ Một DB, ACL/RLS áp dụng chung, transaction nhất quán.
- Khi lên hàng chục triệu chunk có thể chuyển Qdrant qua adapter.
