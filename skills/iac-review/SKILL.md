---
name: iac-review
group: Engineering
mcp:
- opentofu
- aws-docs
description: Review Terraform/OpenTofu or other infrastructure-as-code for security, cost, reliability and maintainability before it is applied. Use for IaC pull requests and plan reviews.
---

# Infrastructure-as-code review

**When to use:** Terraform/OpenTofu/CloudFormation/Pulumi code or a plan output needs review before apply.

1. Read the plan or diff, not just the code: list resources to be **created, changed, destroyed or replaced**. Anything destroyed or replaced is called out first, with the data it may hold.
2. Security: public access (0.0.0.0/0, public buckets/DBs), encryption at rest and in transit, IAM wildcards, secrets in code or state, logging on, default VPC use.
3. Reliability: single points of failure, backups and retention, multi-AZ where the workload needs it, health checks, deletion protection on stateful resources.
4. Cost: instance sizes, NAT gateways, idle resources, data-transfer paths. Give a rough order of magnitude and the assumption behind it.
5. Maintainability: pinned provider/module versions, variables vs hard-coding, naming/tags, state backend and locking.
6. Verify provider arguments against the provider documentation (look them up) rather than from memory.

**Safety:** never run `apply` or `destroy`. `plan` against real accounts only when the user asks and credentials are scoped read-only.
