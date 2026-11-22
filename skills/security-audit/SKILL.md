---
name: security-audit
group: Security
description: Audit code for security vulnerabilities (injection, auth, secrets, unsafe deserialization, path traversal, SSRF, vulnerable dependencies) and report verified findings with fixes. Use when asked for a security review, before shipping changes that touch input handling, auth, files, network or dependencies, or when a scanner reports issues.
mcp: [semgrep, snyk]
---

# Security audit

Goal: find real, exploitable problems — not a list of scanner noise.

1. **Scope.** List the entry points: HTTP handlers, CLI args, file/archive readers, deserializers, subprocess calls,
   SQL/ORM queries, templates, auth/session code, outbound requests, and the dependency manifests
   (package.json, requirements*.txt, pyproject, go.mod, pom.xml...).
2. **Scan.**
   - Code: `semgrep_scan` on the changed / in-scope files (default rules first; add a custom rule with
     `semgrep_scan_with_custom_rule` for a project-specific pattern you suspect).
   - Dependencies: `semgrep_scan_supply_chain`, and Snyk's dependency test if the Snyk server is available.
   - Secrets: search for keys/tokens/passwords in code and config (`search_text` for `api[_-]?key|secret|password|token|BEGIN .*PRIVATE KEY`).
3. **Verify every finding by reading the code.** Trace the data: does attacker-controlled input actually reach the
   sink without validation/escaping? Mark each finding CONFIRMED, PLAUSIBLE or FALSE POSITIVE, with the reason.
4. **Manual checks scanners miss:** authorization on every sensitive action (IDOR), trust of client-supplied IDs/roles,
   unsafe defaults (debug on, permissive CORS, `0.0.0.0` binds), missing rate limits on auth, error messages leaking
   internals, insecure randomness for tokens, TOCTOU on files, unbounded resource use.
5. **Fix** only when asked to — smallest change that removes the root cause (parameterised query, allow-list,
   `secrets` module, path jail...). Add a test that fails without the fix. Re-scan to confirm it is gone.
6. **Report**, most severe first: `severity | path:line | what an attacker can do | evidence | fix`. Then a short
   "not checked" list. Never claim "secure" — say what you checked.
