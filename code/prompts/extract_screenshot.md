You read one screenshot or PDF page from an internal business tool of a shop and extract the facts a privacy
auditor needs. Do not judge whether a deletion is complete; only report what is visible.

Return JSON with these keys and nothing else:
{"resource_id": "<the id shown, e.g. search_428, export_1260, backup_9287, data_flow_1121, audit_record_003>",
 "category": "<USER_PROFILE|SEARCH_INDEX_ENTRY|USER_EXPORT|SUPPORT_MESSAGE|SUPPORT_ATTACHMENT|ANALYTICS_PROFILE|DATA_FLOW|BACKUP_ARCHIVE|SECURITY_AUDIT_LOG|ACCOUNT_TOMBSTONE|MARKETING_PROFILE>",
 "state": "<PRESENT|IDENTIFIABLE|REDACTED|ANONYMIZED|ACTIVE|BLOCKED|DISABLED|ABSENT>",
 "identifiers": ["every email, phone, user_NN reference or name shown for this resource"],
 "observed_at": "<ISO-8601 time visible on the page, or empty>"}
