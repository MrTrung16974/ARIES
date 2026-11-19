# ADR-0004: AgentDefinition có version và migrator

- Trạng thái: Accepted

## Quyết định
AgentDefinition lưu JSONB immutable theo version, có schema_version; ConfigMigrator nâng cấp vN→vN+1, có fixture test. Publish/rollback = đổi con trỏ.

## Hệ quả
+ Agent cũ không vỡ khi nâng cấp; rollback tức thì.
