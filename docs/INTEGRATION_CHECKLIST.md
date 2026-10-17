# Production integration checklist

- [ ] Provide Speech resource region, pricing tier and private endpoint/firewall facts, without posting secrets.
- [ ] Enter primary Speech key and optional secondary key only in backend secrets storage.
- [ ] Validate browser Azure SDK token/streaming with actual resource.
- [ ] Confirm capture target: microphone, Teams in browser, Teams desktop or native transcript path.
- [ ] Choose and approve local or desktop capture component, including participant notices.
- [ ] Implement frontend audio buffer, WebM/PCM support, audio checksum upload and durable IndexedDB queue.
- [ ] Define completed/uploaded/unrecoverable transcript intervals; implement timestamp reconciliation and Fast recovery.
- [ ] Confirm LLM provider protocol, endpoint model and max context; implement long-call safe hierarchical extraction, evidence checking and JSON repairs.
- [ ] Replace dev token with enterprise SSO + RBAC + CSRF/CORS policies and object-level permissions.
- [ ] Implement explicit audio retention/deletion jobs, encryption at rest and backup/restore.
- [ ] Introduce schema migrations and reliable worker lease/retries/dead-letter processing.
- [ ] Tests: 2h meeting, multi-user isolation, offline 10min, tab closure, lost Teams audio, duplicate transcript events, service outage.
