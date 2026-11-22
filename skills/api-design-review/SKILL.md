---
name: api-design-review
group: Engineering
mcp:
- context7
description: Review an API design or OpenAPI spec for consistency, safety, versioning and developer experience, and list concrete changes. Use before releasing or changing an API.
---

# API design review

**When to use:** an OpenAPI/GraphQL/gRPC definition or a set of endpoints is being designed or changed.

1. Read the whole spec first. Note style conventions already in use (naming, pluralisation, error format, pagination) and judge consistency against them.
2. Check: resource naming and verbs, status codes, idempotency of PUT/DELETE and a key for unsafe retries on POST, pagination and filtering, error shape with a stable machine-readable code, rate-limit headers, versioning and deprecation plan.
3. Security: authentication scheme, scopes per operation, object-level authorisation (can user A read user B's id?), mass assignment, sensitive fields in responses, input limits, CORS.
4. Compatibility: which changes are breaking (removed/renamed fields, tighter validation, changed enums) and how to roll them out.
5. Output findings by severity (**blocker / should-fix / nit**) with the exact path/operation, the problem and the suggested change.

**Safety:** review only; do not call production endpoints or change the spec without being asked.
