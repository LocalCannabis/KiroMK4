---
type: dashboard
---

# Knowledge Base — Ingest Queue

Sources registered in the vault but not yet ingested into the vector store.

```dataview
TABLE persona AS "Persona", source AS "Source", name AS "Name"
FROM "knowledge"
WHERE type = "knowledge_base" AND ingested != true
SORT persona ASC
```

---

# Already Ingested

```dataview
TABLE persona AS "Persona", source AS "Source", ingested_date AS "Ingested"
FROM "knowledge"
WHERE type = "knowledge_base" AND ingested = true
SORT ingested_date DESC
```
