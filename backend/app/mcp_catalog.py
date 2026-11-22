"""Catalog of vetted PUBLIC MCP servers the user can add with one click (Catalog > MCP servers).

Every entry was checked on the real package registry (npm / PyPI) or by contacting the remote endpoint on 2026-11-22; the exact
facts are in each entry's `verified` line. Versions are PINNED to a release at least 14 days old. `read_only` says how much the
server can be limited:

  always      the server cannot change anything in the remote service (search, docs, reports)
  enforced    a flag/variable makes it read-only; Agent Studio sets it unless the user confirms writes
  partial     a flag limits the damage (e.g. no deletes) but writes remain possible - writes need a confirm step
  credential  the server can do whatever the token/IAM role allows - give it a read-only token; writes need a confirm step
  none        the server changes things by design (edits workbooks, drives a browser) - needs a confirm step

Nothing here starts by itself: installing adds a DISABLED-until-ready entry to MCP servers; credentials are stored write-only.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

CHECKED = "2026-11-22"

DOMAINS = [
    dict(id="accounting", label="Accounting & finance"),
    dict(id="sales", label="Sales & CRM"),
    dict(id="marketing", label="Marketing"),
    dict(id="manufacturing", label="Manufacturing"),
    dict(id="engineering", label="Engineering"),
    dict(id="data", label="Data"),
    dict(id="machine-learning", label="Machine learning"),
    dict(id="deep-learning", label="Deep learning"),
]
DOMAIN_IDS = [d["id"] for d in DOMAINS]
READ_ONLY_KINDS = ("always", "enforced", "partial", "credential", "none")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,30}$")


def F(name: str, label: str, *, secret: bool = False, required: bool = True, where: str = "env", help: str = "",
      placeholder: str = "", header: str = "", prefix: str = "", default: str = "") -> dict:
    """A value the user supplies. where: env (environment variable), arg (replaces {NAME} in the arguments) or header (HTTP)."""
    return dict(name=name, label=label, secret=secret, required=required, where=where, help=help, placeholder=placeholder,
                header=header, prefix=prefix, default=default)


def E(id: str, label: str, domains: list[str], publisher: str, description: str, *, official: bool, kind: str, pkg: str,
      read_only: str, writes: str, data_leaves: str, license: str, docs: str, verified: str, transport: str = "stdio",
      command: str = "", args: list[str] | None = None, url: str = "", fields: list[dict] | None = None, env: dict | None = None,
      ro_args: list[str] | None = None, rw_args: list[str] | None = None, ro_env: dict | None = None, rw_env: dict | None = None,
      setup: str = "") -> dict:
    return dict(id=id, label=label, domains=domains, publisher=publisher, official=official, description=description, kind=kind,
                package=pkg, read_only=read_only, writes=writes, data_leaves=data_leaves, license=license, docs=docs,
                verified=verified, transport=transport, command=command, args=args or [], url=url, fields=fields or [],
                env=env or {}, ro_args=ro_args or [], rw_args=rw_args or [], ro_env=ro_env or {}, rw_env=rw_env or {}, setup=setup)


def npx(pkg: str, ver: str, *rest: str) -> dict:
    return dict(command="npx", args=["-y", f"{pkg}@{ver}", *rest], pkg=f"{pkg}@{ver}", kind="npm")


def uvx(pkg: str, ver: str, *rest: str, script: str = "") -> dict:
    args = ["--from", f"{pkg}=={ver}", script, *rest] if script else [f"{pkg}=={ver}", *rest]
    return dict(command="uvx", args=args, pkg=f"{pkg}=={ver}", kind="pypi")


AWS_FIELDS = [
    F("AWS_PROFILE", "AWS profile name", required=False, help="A profile from your ~/.aws/config. Leave empty to use the keys below or the default credential chain."),
    F("AWS_ACCESS_KEY_ID", "Access key id", required=False, secret=True, help="Only if you do not use a profile. Use a key with read-only permissions."),
    F("AWS_SECRET_ACCESS_KEY", "Secret access key", required=False, secret=True),
    F("AWS_REGION", "AWS region", required=False, default="us-east-1", placeholder="us-east-1"),
]
AWS_FIELDS_REGION_ONLY = AWS_FIELDS

CATALOG: list[dict] = [
    # ------------------------------------------------------------------------------------------------ accounting & finance
    E("xero", "Xero", ["accounting"], "Xero", "Read and change Xero accounting data: contacts, invoices, accounts, payments, reports.",
      official=True, **npx("@xeroapi/xero-mcp-server", "0.0.17"), read_only="none",
      writes="Creates and updates contacts, invoices, quotes, payments and journals in your Xero organisation (what is allowed depends on the app's scopes).",
      data_leaves="Requests go to Xero (api.xero.com) with your organisation's accounting data.", license="MIT",
      docs="https://github.com/XeroAPI/xero-mcp-server", verified="npm @xeroapi/xero-mcp-server 0.0.17, MIT, README lists XERO_CLIENT_ID / XERO_CLIENT_SECRET",
      fields=[F("XERO_CLIENT_ID", "Client id", help="From a Xero custom-connection app (developer.xero.com)."),
              F("XERO_CLIENT_SECRET", "Client secret", secret=True)],
      setup="Create a Custom Connection app in the Xero developer portal and give it the minimum scopes you need (read-only scopes make this read-only)."),
    E("aws-billing", "AWS billing & cost", ["accounting", "engineering"], "AWS Labs", "Analyse AWS spend: cost and usage, budgets, savings recommendations.",
      official=True, **uvx("awslabs.billing-cost-management-mcp-server", "0.0.44"), read_only="credential",
      writes="Designed for reading cost data; the IAM permissions of the key decide what else is possible. Use a read-only policy.",
      data_leaves="Calls the AWS Billing / Cost Explorer APIs in your account.", license="Apache-2.0",
      docs="https://github.com/awslabs/mcp/tree/main/src/billing-cost-management-mcp-server",
      verified="PyPI awslabs.billing-cost-management-mcp-server 0.0.44, Apache-2.0, README uses uvx with AWS_PROFILE / AWS_REGION",
      env={"FASTMCP_LOG_LEVEL": "ERROR"}, fields=AWS_FIELDS),
    E("aws-pricing", "AWS pricing", ["accounting", "engineering"], "AWS Labs", "Look up official AWS prices and estimate what a design would cost.",
      official=True, **uvx("awslabs.aws-pricing-mcp-server", "1.1.1"), read_only="always",
      writes="Nothing: it only reads the public AWS Price List.", data_leaves="Calls the AWS Price List API (needs valid AWS credentials, no data of yours is read).",
      license="Apache-2.0", docs="https://github.com/awslabs/mcp/tree/main/src/aws-pricing-mcp-server",
      verified="PyPI awslabs.aws-pricing-mcp-server 1.1.1, Apache-2.0", env={"FASTMCP_LOG_LEVEL": "ERROR"}, fields=AWS_FIELDS),
    E("fred", "FRED economic data", ["accounting", "data"], "Community (floriancaro)", "Federal Reserve economic time series: rates, inflation, GDP, unemployment.",
      official=False, **uvx("fred-mcp-server", "0.2.2"), read_only="always", writes="Nothing: read-only data lookups.",
      data_leaves="Queries go to the St. Louis Fed FRED API with your API key.", license="MIT", docs="https://github.com/floriancaro/fred-mcp-server",
      verified="PyPI fred-mcp-server 0.2.2, MIT, README: FRED_API_KEY",
      fields=[F("FRED_API_KEY", "FRED API key", secret=True, help="Free at fred.stlouisfed.org/docs/api/api_key.html")]),
    E("excel", "Excel workbooks", ["accounting", "manufacturing", "data"], "Community (haris-musa)", "Create, read and edit .xlsx workbooks in the workspace folder without Excel installed.",
      official=False, **uvx("excel-mcp-server", "2.2.6", "stdio", "--allow-dir", "{workspace}"), read_only="none",
      writes="Creates and edits workbook files, but only inside the workspace folder.", data_leaves="Nothing leaves the PC.", license="MIT",
      docs="https://github.com/haris-musa/excel-mcp-server", verified="PyPI excel-mcp-server 2.2.6, MIT, README: `uvx excel-mcp-server stdio --allow-dir <folder>`"),
    E("time", "Time & timezones", ["accounting"], "Model Context Protocol (reference server)", "Current time and timezone conversion - useful for period-end and cut-off dates.",
      official=True, **uvx("mcp-server-time", "2026.11.4"), read_only="always", writes="Nothing.", data_leaves="Nothing leaves the PC.", license="MIT",
      docs="https://github.com/modelcontextprotocol/servers/tree/main/src/time", verified="PyPI mcp-server-time 2026.11.4, MIT"),
    E("calculator", "Calculator", ["accounting", "engineering"], "Community (githejie)", "Exact arithmetic and expressions so numbers in reports are computed, not guessed.",
      official=False, **uvx("mcp-server-calculator", "0.2.1"), read_only="always", writes="Nothing.", data_leaves="Nothing leaves the PC.", license="MIT",
      docs="https://github.com/githejie/mcp-server-calculator", verified="PyPI mcp-server-calculator 0.2.1, MIT"),
    # ------------------------------------------------------------------------------------------------ sales & CRM
    E("hubspot", "HubSpot CRM", ["sales", "marketing"], "HubSpot", "Search and manage HubSpot CRM records: contacts, companies, deals, tickets.",
      official=True, **npx("@hubspot/mcp-server", "0.4.0"), read_only="credential",
      writes="Can create and update CRM records if the private-app token has write scopes. Give it read-only scopes to keep it read-only.",
      data_leaves="Customer records are sent to HubSpot's API and into the model prompt.", license="MIT",
      docs="https://www.npmjs.com/package/@hubspot/mcp-server", verified="npm @hubspot/mcp-server 0.4.0, MIT, README: PRIVATE_APP_ACCESS_TOKEN, 'start with read-only scopes'",
      fields=[F("PRIVATE_APP_ACCESS_TOKEN", "Private app access token", secret=True, help="HubSpot > Settings > Integrations > Private apps. Tick only the read scopes you need.")]),
    E("airtable", "Airtable", ["sales", "data"], "Community (domdomegg)", "Read and edit Airtable bases and records.",
      official=False, **npx("airtable-mcp-server", "1.14.0"), read_only="credential",
      writes="Can edit records if the personal access token has write scopes (schema.bases:write, data.records:write).",
      data_leaves="Record data goes to Airtable's API and into the model prompt.", license="MIT", docs="https://www.npmjs.com/package/airtable-mcp-server",
      verified="npm airtable-mcp-server 1.14.0, MIT, README: AIRTABLE_API_KEY (a personal access token)",
      fields=[F("AIRTABLE_API_KEY", "Personal access token", secret=True, help="Create it with the read scopes only: schema.bases:read and data.records:read.")]),
    E("notion", "Notion", ["sales", "marketing", "engineering"], "Notion", "Search, read and edit pages and databases the integration was shared with.",
      official=True, **npx("@notionhq/notion-mcp-server", "2.6.1"), read_only="credential",
      writes="Can edit pages if the integration has update/insert capabilities; a 'Read content'-only integration is read-only.",
      data_leaves="Page content goes to Notion's API and into the model prompt.", license="MIT", docs="https://github.com/makenotion/notion-mcp-server",
      verified="npm @notionhq/notion-mcp-server 2.6.1, MIT, README: NOTION_TOKEN",
      fields=[F("NOTION_TOKEN", "Integration token", secret=True, help="Create an internal integration, give it 'Read content' only, then share pages with it.")]),
    E("shopify-dev", "Shopify developer docs", ["sales", "engineering"], "Shopify", "Search Shopify's developer documentation and GraphQL schemas (no store access).",
      official=True, **npx("@shopify/dev-mcp", "1.18.0"), read_only="always", writes="Nothing: documentation only, it never touches a store.",
      data_leaves="Documentation searches go to shopify.dev.", license="ISC", docs="https://shopify.dev/docs/apps/build/devmcp",
      verified="npm @shopify/dev-mcp 1.18.0, ISC, README: `npx -y @shopify/dev-mcp`"),
    # ------------------------------------------------------------------------------------------------ marketing
    E("google-analytics", "Google Analytics", ["marketing", "data"], "Google", "Read Google Analytics 4 reports, properties and links.",
      official=True, **uvx("analytics-mcp", "1.1.0"), read_only="always", writes="Nothing: report and list tools only.",
      data_leaves="Report requests go to the Google Analytics APIs with your Google credentials.", license="Apache-2.0",
      docs="https://github.com/googleanalytics/google-analytics-mcp", verified="PyPI analytics-mcp 1.1.0, Apache-2.0, README: Application Default Credentials + GOOGLE_CLOUD_PROJECT",
      fields=[F("GOOGLE_APPLICATION_CREDENTIALS", "Credentials JSON file", help="Path to a service-account or authorised-user JSON with the analytics.readonly scope.", placeholder="C:/keys/ga.json"),
              F("GOOGLE_CLOUD_PROJECT", "Google Cloud project id", placeholder="my-project-123")],
      setup="Enable the Analytics Admin and Data APIs in the project and give the account Viewer access on the GA4 property."),
    E("tavily", "Tavily web search", ["marketing", "sales", "machine-learning"], "Tavily", "Web search and page extraction built for AI agents.",
      official=True, **npx("tavily-mcp", "0.2.22"), read_only="always", writes="Nothing.", data_leaves="Search queries and fetched URLs go to Tavily.",
      license="MIT", docs="https://github.com/tavily-ai/tavily-mcp", verified="npm tavily-mcp 0.2.22, MIT, README: TAVILY_API_KEY",
      fields=[F("TAVILY_API_KEY", "Tavily API key", secret=True, help="Free tier at tavily.com")]),
    E("figma", "Figma designs", ["marketing", "engineering"], "Community (Framelink)", "Read Figma files, frames and styles so an agent can build from a design.",
      official=False, **npx("figma-developer-mcp", "0.13.2", "--stdio"), read_only="always", writes="Nothing in Figma; exported images are saved to the workspace.",
      data_leaves="File ids and requests go to the Figma API with your token.", license="MIT", docs="https://github.com/GLips/Figma-Context-MCP",
      verified="npm figma-developer-mcp 0.13.2, MIT, README: FIGMA_API_KEY and --stdio",
      fields=[F("FIGMA_API_KEY", "Figma personal access token", secret=True, help="Figma > Settings > Security. File content: read only.")]),
    # ------------------------------------------------------------------------------------------------ manufacturing
    E("freecad", "FreeCAD (CAD)", ["manufacturing", "engineering"], "Community (neka-nat)", "Create and edit 3D CAD models in a running FreeCAD and read their properties.",
      official=False, **uvx("freecad-mcp", "0.1.30"), read_only="none",
      writes="Creates, edits and deletes objects in the open FreeCAD document and can run Python inside FreeCAD.", data_leaves="Nothing leaves the PC.",
      license="MIT", docs="https://github.com/neka-nat/freecad-mcp", verified="PyPI freecad-mcp 0.1.30, MIT, README: `uvx freecad-mcp` + FreeCAD addon",
      setup="Install FreeCAD and the 'MCP Addon' (see the README), open FreeCAD and start the addon before using this server."),
    # ------------------------------------------------------------------------------------------------ engineering
    E("opentofu", "OpenTofu registry", ["engineering"], "OpenTofu", "Look up OpenTofu/Terraform providers, resources and modules from the registry.",
      official=True, **npx("@opentofu/opentofu-mcp-server", "0.1.5"), read_only="always", writes="Nothing: registry lookups only.",
      data_leaves="Lookups go to the OpenTofu registry.", license="MPL-2.0", docs="https://github.com/opentofu/opentofu-mcp-server",
      verified="npm @opentofu/opentofu-mcp-server 0.1.5, MPL-2.0"),
    E("aws-docs", "AWS documentation", ["engineering"], "AWS Labs", "Search and read AWS documentation pages.",
      official=True, **uvx("awslabs.aws-documentation-mcp-server", "1.2.4"), read_only="always", writes="Nothing.",
      data_leaves="Searches go to the public AWS documentation.", license="Apache-2.0", docs="https://github.com/awslabs/mcp/tree/main/src/aws-documentation-mcp-server",
      verified="PyPI awslabs.aws-documentation-mcp-server 1.2.4, Apache-2.0", env={"FASTMCP_LOG_LEVEL": "ERROR", "AWS_DOCUMENTATION_PARTITION": "aws"}),
    E("aws-api", "AWS (any service)", ["engineering"], "AWS Labs", "Run AWS CLI-style calls against your account with validation and a read-only mode.",
      official=True, **uvx("awslabs.aws-api-mcp-server", "1.6.0"), read_only="enforced",
      writes="With writes allowed it can create, change and delete AWS resources the credentials permit (and cost money).",
      data_leaves="Calls go to the AWS APIs of your account; results enter the model prompt.", license="Apache-2.0",
      docs="https://github.com/awslabs/mcp/tree/main/src/aws-api-mcp-server", verified="PyPI awslabs.aws-api-mcp-server 1.6.0, Apache-2.0, README: READ_OPERATIONS_ONLY",
      ro_env={"READ_OPERATIONS_ONLY": "true"}, rw_env={"READ_OPERATIONS_ONLY": "false", "REQUIRE_MUTATION_CONSENT": "true"},
      env={"FASTMCP_LOG_LEVEL": "ERROR", "AWS_API_MCP_TELEMETRY": "false"}, fields=AWS_FIELDS),
    E("kubernetes", "Kubernetes", ["engineering"], "Community (Flux159)", "Inspect and manage Kubernetes clusters through kubectl.",
      official=False, **npx("mcp-server-kubernetes", "4.2.1"), read_only="partial",
      writes="Safe mode blocks deletes, but it can still create and update resources. Use a kubeconfig with a read-only role for true read-only.",
      data_leaves="Cluster details (names, logs, manifests) enter the model prompt; nothing else leaves the PC.", license="MIT",
      docs="https://github.com/Flux159/mcp-server-kubernetes", verified="npm mcp-server-kubernetes 4.2.1, MIT, README: ALLOW_ONLY_NON_DESTRUCTIVE_TOOLS",
      ro_env={"ALLOW_ONLY_NON_DESTRUCTIVE_TOOLS": "true"}, setup="Needs kubectl and a working kubeconfig on this PC."),
    E("chrome-devtools", "Chrome DevTools", ["engineering"], "Google (Chrome DevTools team)", "Drive a Chrome instance to debug, inspect network and performance, and take screenshots.",
      official=True, **npx("chrome-devtools-mcp", "1.12.0", "--headless", "--isolated", "--no-usage-statistics"), read_only="none",
      writes="Controls a browser: it can click, type and submit forms on pages it opens.", data_leaves="Page contents enter the model prompt.",
      license="Apache-2.0", docs="https://github.com/ChromeDevTools/chrome-devtools-mcp", verified="npm chrome-devtools-mcp 1.12.0, Apache-2.0, README flags --headless --isolated --no-usage-statistics"),
    # ------------------------------------------------------------------------------------------------ data
    E("duckdb", "DuckDB / MotherDuck", ["data", "manufacturing"], "MotherDuck", "Query local DuckDB files (and Parquet/CSV through DuckDB) with SQL.",
      official=True, **uvx("mcp-server-motherduck", "1.1.0", "--db-path", "{DB_PATH}"), read_only="enforced",
      writes="Read-only unless you confirm writes, which adds --read-write.", data_leaves="Nothing leaves the PC for a local file (a 'md:' path uses MotherDuck's cloud).",
      license="MIT", docs="https://github.com/motherduckdb/mcp-server-motherduck", verified="PyPI mcp-server-motherduck 1.1.0, README: a --db-path is read-only unless --read-write is given",
      rw_args=["--read-write"], fields=[F("DB_PATH", "Database file", where="arg", placeholder="C:/data/sales.duckdb", help="A .duckdb file, or :memory: for an empty scratch database.")]),
    E("postgres", "PostgreSQL", ["data"], "Crystal DBA (Postgres MCP Pro)", "Query PostgreSQL, explain plans and get index/health advice.",
      official=False, **uvx("postgres-mcp", "0.3.0"), read_only="enforced",
      writes="Read-only (restricted) unless you confirm writes, which switches to unrestricted access.",
      data_leaves="Rows returned by queries enter the model prompt; the database is not contacted by anyone else.", license="MIT",
      docs="https://github.com/crystaldba/postgres-mcp", verified="PyPI postgres-mcp 0.3.0, MIT, README: DATABASE_URI and --access-mode=restricted|unrestricted",
      ro_args=["--access-mode=restricted"], rw_args=["--access-mode=unrestricted"],
      fields=[F("DATABASE_URI", "Connection string", secret=True, placeholder="postgresql://user:password@host:5432/dbname", help="Use a database user that only has SELECT.")]),
    E("clickhouse", "ClickHouse", ["data"], "ClickHouse", "Run analytical SQL on ClickHouse and explore its tables.",
      official=True, **uvx("mcp-clickhouse", "0.7.0"), read_only="enforced", writes="Read-only unless you confirm writes (CLICKHOUSE_ALLOW_WRITE_ACCESS).",
      data_leaves="Query results enter the model prompt.", license="Apache-2.0", docs="https://github.com/ClickHouse/mcp-clickhouse",
      verified="PyPI mcp-clickhouse 0.7.0, Apache-2.0, README: CLICKHOUSE_ALLOW_WRITE_ACCESS=false by default",
      ro_env={"CLICKHOUSE_ALLOW_WRITE_ACCESS": "false"}, rw_env={"CLICKHOUSE_ALLOW_WRITE_ACCESS": "true"},
      fields=[F("CLICKHOUSE_HOST", "Host", placeholder="localhost"), F("CLICKHOUSE_PORT", "Port", required=False, default="8123"),
              F("CLICKHOUSE_USER", "User", default="default"), F("CLICKHOUSE_PASSWORD", "Password", secret=True, required=False),
              F("CLICKHOUSE_SECURE", "Use TLS (true/false)", required=False, default="false")]),
    E("mongodb", "MongoDB", ["data"], "MongoDB", "Explore and query MongoDB and Atlas collections.",
      official=True, **npx("mongodb-mcp-server", "3.2.2"), read_only="enforced", writes="Read-only (--readOnly) unless you confirm writes.",
      data_leaves="Documents returned enter the model prompt.", license="Apache-2.0", docs="https://github.com/mongodb-js/mongodb-mcp-server",
      verified="npm mongodb-mcp-server 3.2.2, Apache-2.0, README: --readOnly and MDB_MCP_CONNECTION_STRING", ro_args=["--readOnly"],
      fields=[F("MDB_MCP_CONNECTION_STRING", "Connection string", secret=True, placeholder="mongodb://localhost:27017/mydb")]),
    E("neo4j", "Neo4j graph database", ["data"], "Neo4j", "Query a Neo4j graph with Cypher and read its schema.",
      official=True, **uvx("mcp-neo4j-cypher", "0.6.0"), read_only="enforced", writes="Read-only (NEO4J_READ_ONLY) unless you confirm writes.",
      data_leaves="Query results enter the model prompt.", license="MIT", docs="https://github.com/neo4j-contrib/mcp-neo4j",
      verified="PyPI mcp-neo4j-cypher 0.6.0, README: NEO4J_URI and NEO4J_READ_ONLY=true", ro_env={"NEO4J_READ_ONLY": "true"}, rw_env={"NEO4J_READ_ONLY": "false"},
      fields=[F("NEO4J_URI", "Bolt URI", placeholder="bolt://localhost:7687"), F("NEO4J_USERNAME", "User", default="neo4j"), F("NEO4J_PASSWORD", "Password", secret=True)]),
    E("sqlite", "SQLite", ["data"], "Model Context Protocol (reference server, archived)", "Query and edit a local SQLite database file.",
      official=True, **uvx("mcp-server-sqlite", "2025.4.25", "--db-path", "{DB_PATH}"), read_only="none",
      writes="Has a write_query tool: it can insert, update and delete rows in the file. Work on a copy if in doubt.", data_leaves="Nothing leaves the PC.",
      license="MIT", docs="https://pypi.org/project/mcp-server-sqlite/", verified="PyPI mcp-server-sqlite 2025.4.25 (reference server, no longer updated)",
      fields=[F("DB_PATH", "Database file", where="arg", placeholder="C:/data/app.db")]),
    E("pinecone", "Pinecone", ["data", "machine-learning"], "Pinecone", "Search and manage Pinecone vector indexes.",
      official=True, **npx("@pinecone-database/mcp", "0.4.0"), read_only="none", writes="Can create indexes and upsert/delete records in your Pinecone project.",
      data_leaves="Texts and vectors you query or upsert go to Pinecone.", license="Apache-2.0", docs="https://github.com/pinecone-io/pinecone-mcp",
      verified="npm @pinecone-database/mcp 0.4.0, Apache-2.0, README: PINECONE_API_KEY", fields=[F("PINECONE_API_KEY", "API key", secret=True)]),
    E("qdrant", "Qdrant", ["data", "machine-learning"], "Qdrant", "Store and find snippets in a Qdrant vector collection.",
      official=True, **uvx("mcp-server-qdrant", "0.8.1"), read_only="enforced", writes="Read-only (QDRANT_READ_ONLY) unless you confirm writes (the store tool).",
      data_leaves="Text goes to your Qdrant server. Embeddings are computed locally (a small model downloads on first use).", license="Apache-2.0",
      docs="https://github.com/qdrant/mcp-server-qdrant", verified="PyPI mcp-server-qdrant 0.8.1, Apache-2.0, README: QDRANT_URL, COLLECTION_NAME, QDRANT_READ_ONLY",
      ro_env={"QDRANT_READ_ONLY": "true"}, rw_env={"QDRANT_READ_ONLY": "false"},
      fields=[F("QDRANT_URL", "Qdrant URL", placeholder="http://localhost:6333"), F("COLLECTION_NAME", "Collection"), F("QDRANT_API_KEY", "API key", secret=True, required=False)]),
    # ------------------------------------------------------------------------------------------------ machine learning / deep learning
    E("huggingface", "Hugging Face Hub", ["machine-learning", "deep-learning"], "Hugging Face", "Search models, datasets, Spaces and papers; read the docs.",
      official=True, transport="http", url="https://huggingface.co/mcp", pkg="https://huggingface.co/mcp", kind="remote", read_only="credential",
      writes="Discovery tools only by default; with a token it acts as you, so use a read-only token.", data_leaves="Searches go to huggingface.co.",
      license="Hosted service", docs="https://huggingface.co/docs/hub/agents-mcp", verified="Endpoint https://huggingface.co/mcp answered an MCP initialize request without credentials on 2026-11-22",
      fields=[F("HF_TOKEN", "Hugging Face token (optional)", secret=True, required=False, where="header", header="Authorization", prefix="Bearer ",
                help="Optional. Use a read-only (fine-grained) token.")]),
    E("kaggle", "Kaggle", ["machine-learning", "data"], "Kaggle", "Search Kaggle datasets, models and notebooks.",
      official=True, transport="http", url="https://www.kaggle.com/mcp", pkg="https://www.kaggle.com/mcp", kind="remote", read_only="credential",
      writes="Search tools only unless your token allows more; use a read-only token.", data_leaves="Searches go to kaggle.com.", license="Hosted service",
      docs="https://www.kaggle.com/docs/mcp", verified="Endpoint https://www.kaggle.com/mcp answered an MCP initialize request on 2026-11-22; the token header was not tested",
      fields=[F("KAGGLE_API_TOKEN", "Kaggle API token (optional)", secret=True, required=False, where="header", header="Authorization", prefix="Bearer ",
                help="Optional. Only needed for account-specific tools.")]),
    E("mlflow", "MLflow tracking", ["machine-learning", "deep-learning"], "MLflow (Databricks)", "Search and analyse MLflow traces and runs; log feedback.",
      official=True, **uvx("mlflow[mcp]", "3.18.0", "mcp", "run", script="mlflow"), read_only="none",
      writes="Can log feedback and tags and delete traces/assessments on the tracking server.", data_leaves="Trace data stays on your tracking server; results enter the model prompt.",
      license="Apache-2.0", docs="https://mlflow.org/docs/latest/genai/mcp/", verified="PyPI mlflow 3.18.0; docs: `mlflow mcp run` with the mlflow[mcp] extra, MLFLOW_TRACKING_URI",
      fields=[F("MLFLOW_TRACKING_URI", "Tracking server URI", placeholder="http://localhost:5000")]),
    E("arxiv", "arXiv papers", ["machine-learning", "deep-learning"], "Community (blazickjp)", "Search arXiv, download papers and read them.",
      official=False, **uvx("arxiv-mcp-server", "0.8.2"), read_only="always", writes="Nothing remote; downloaded papers are saved in a local folder.",
      data_leaves="Searches go to arxiv.org.", license="Apache-2.0", docs="https://github.com/blazickjp/arxiv-mcp-server", verified="PyPI arxiv-mcp-server 0.8.2, Apache-2.0, README: `uvx arxiv-mcp-server`"),
    E("paper-search", "Academic paper search", ["machine-learning", "deep-learning"], "Community (openags)", "Search many paper sources (arXiv, PubMed, Semantic Scholar, ...) and download PDFs.",
      official=False, **uvx("paper-search-mcp", "0.1.4"), read_only="always", writes="Nothing remote; PDFs are saved locally.",
      data_leaves="Searches go to the scholarly APIs you use.", license="MIT", docs="https://github.com/openags/paper-search-mcp", verified="PyPI paper-search-mcp 0.1.4, MIT, README: `uvx paper-search-mcp`",
      fields=[F("PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY", "Semantic Scholar key (optional)", secret=True, required=False, help="Optional: raises rate limits.")]),
]


# --------------------------------------------------------------------------------------------------------------------- helpers
def get(cid: str) -> dict | None:
    return next((e for e in CATALOG if e["id"] == cid), None)


def needs_confirm(entry: dict, allow_writes: bool) -> bool:
    """Does this install need the user's explicit 'I understand it can change things' tick?"""
    ro = entry["read_only"]
    if ro == "always":
        return False
    if ro == "enforced":
        return allow_writes                     # staying read-only needs no confirm; switching writes on does
    return True                                 # partial / credential / none: the server can change things


def public(entry: dict, installed: dict | None = None, ready: bool = False) -> dict:
    d = {k: v for k, v in entry.items() if k not in ("ro_args", "rw_args", "ro_env", "rw_env", "env", "command", "args")}
    d["command_line"] = (entry["command"] + " " + " ".join(entry["args"])).strip() if entry["transport"] == "stdio" else entry["url"]
    d["can_be_read_only"] = entry["read_only"] in ("always", "enforced")
    d["installed"] = installed is not None
    d["allow_writes"] = bool(installed and installed.get("allow_writes"))
    d["ready"] = ready
    return d


def build(entry: dict, values: dict, allow_writes: bool) -> dict:
    """The mcp_servers row content for an install: label, args, url, env, requires_env. Raises ValueError for bad input."""
    values = {k: str(v).strip() for k, v in (values or {}).items() if v is not None and str(v).strip() != ""}
    known = {f["name"] for f in entry["fields"]}
    extra = sorted(set(values) - known)
    if extra:
        raise ValueError(f"unknown setting(s): {', '.join(extra)}")
    missing = [f["label"] for f in entry["fields"] if f["required"] and not f["default"] and f["name"] not in values]
    if missing:
        raise ValueError("fill in: " + ", ".join(missing))
    if entry["read_only"] == "always":
        allow_writes = False
    elif entry["read_only"] != "enforced" and not allow_writes:
        raise ValueError(f"'{entry['label']}' can change things ({entry['writes']}) - tick the confirmation to add it")
    vals = {f["name"]: values.get(f["name"], f["default"]) for f in entry["fields"]}
    args = list(entry["args"]) + list(entry["rw_args"] if allow_writes else entry["ro_args"])
    env = dict(entry["env"])
    env.update(entry["rw_env"] if allow_writes else entry["ro_env"])
    required: list[str] = []
    for f in entry["fields"]:
        v = vals[f["name"]]
        if f["where"] == "arg":
            if re.search(r"[\r\n]", v):
                raise ValueError(f"{f['label']} must be a single line")
            args = [a.replace("{" + f["name"] + "}", v) for a in args]
        elif f["where"] == "header":
            if v:
                env["HEADER_" + re.sub(r"[^A-Za-z0-9]+", "_", f["header"]).upper()] = f["prefix"] + v
        elif v:
            env[f["name"]] = v
        key = ("HEADER_" + re.sub(r"[^A-Za-z0-9]+", "_", f["header"]).upper()) if f["where"] == "header" else f["name"]
        if f["required"] and f["where"] != "arg":
            required.append(key)
    return dict(args=args, env=env, requires_env=required)


def install(store, cid: str, values: dict, allow_writes: bool = False) -> dict:
    """Add (or re-add) a catalog server to the MCP store. Returns the public server dict."""
    entry = get(cid)
    if entry is None:
        raise KeyError(cid)
    if store.get(cid) and not _tracked(store).get(cid):
        raise ValueError(f"an MCP server called '{cid}' already exists and was not added from the catalog")
    b = build(entry, values, allow_writes)
    if store.get(cid):
        store.delete(cid)
    store.db.execute(
        "INSERT INTO mcp_servers(id,label,description,transport,command,args,url,env,requires_env,enabled,default_on,status,builtin)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,0,'active',0)",
        (cid, entry["label"], entry["description"], entry["transport"], entry["command"] or None, json.dumps(b["args"]),
         entry["url"] or None, json.dumps(b["env"]), json.dumps(b["requires_env"]), 1))
    track = _tracked(store)
    track[cid] = dict(allow_writes=bool(allow_writes) and entry["read_only"] != "always", at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    _save_tracked(store, track)
    return store.get(cid).public()  # type: ignore[union-attr]


def set_mode(store, cid: str, allow_writes: bool) -> dict:
    """Switch an installed catalog server between read-only and writes allowed, keeping its stored credentials."""
    entry = get(cid)
    s = store.get(cid)
    if entry is None or s is None or cid not in _tracked(store):
        raise KeyError(cid)
    if entry["read_only"] in ("always",):
        raise ValueError("this server cannot change anything, so there is no mode to switch")
    if entry["read_only"] != "enforced":
        raise ValueError("this server cannot be limited by a setting - restrict the token or role it uses instead")
    ro, rw = (entry["rw_env"], entry["ro_env"]) if not allow_writes else (entry["ro_env"], entry["rw_env"])
    args = [a for a in s.args if a not in (entry["rw_args"] + entry["ro_args"])] + list(entry["rw_args"] if allow_writes else entry["ro_args"])
    env = {k: v for k, v in s.env.items() if k not in ro or k in entry["env"]}
    env.update(entry["rw_env"] if allow_writes else entry["ro_env"])
    store.db.execute("UPDATE mcp_servers SET args=?, env=? WHERE id=?", (json.dumps(args), json.dumps(env), cid))
    track = _tracked(store)
    track[cid]["allow_writes"] = bool(allow_writes)
    _save_tracked(store, track)
    return store.get(cid).public()  # type: ignore[union-attr]


def _tracked(store) -> dict:
    r = store.db.one("SELECT value FROM settings WHERE key='mcp_catalog_installed'")
    return json.loads(r["value"]) if r else {}


def _save_tracked(store, data: dict) -> None:
    store.db.execute("INSERT INTO settings(key,value) VALUES('mcp_catalog_installed',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(data),))


def installed(store) -> dict:
    """{id: {allow_writes, at}} for catalog servers that still exist in the store."""
    t = _tracked(store)
    live = {k: v for k, v in t.items() if store.get(k)}
    if live != t:
        _save_tracked(store, live)
    return live


def problems(catalog: list[dict] | None = None) -> list[str]:
    """Static sanity checks over the catalog (used by the tests): ids, pins, fields, placeholders, read-only kinds."""
    out: list[str] = []
    ids = [e["id"] for e in (CATALOG if catalog is None else catalog)]
    for e in (CATALOG if catalog is None else catalog):
        i, joined = e["id"], " ".join(e["args"])
        names = [f["name"] for f in e["fields"]]
        placeholders = set(re.findall(r"\{([A-Z_]+)\}", joined))
        arg_fields = {f["name"] for f in e["fields"] if f["where"] == "arg"}
        stdio = e["transport"] == "stdio"
        checks = [
            (ids.count(i) > 1 or not ID_RE.match(i), "duplicate or invalid id"),
            (e["read_only"] not in READ_ONLY_KINDS, "bad read_only"),
            (not e["domains"] or any(d not in DOMAIN_IDS for d in e["domains"]), "bad domains"),
            (stdio and "latest" in joined, "unpinned (latest)"),
            (stdio and e["kind"] == "npm" and not re.search(r"@\d+\.\d+\.\d+", joined), "npm package not pinned to an exact version"),
            (stdio and e["kind"] == "pypi" and not re.search(r"==\d", joined), "PyPI package not pinned to an exact version"),
            (not stdio and not e["url"].startswith("https://"), "remote server must use https"),
            (len(set(names)) != len(names), "duplicate field"),
            (bool(placeholders - arg_fields), "unresolved placeholder"),
            (any(f["where"] not in ("env", "arg", "header") or (f["where"] == "header" and not f["header"]) for f in e["fields"]), "bad field"),
            (e["read_only"] == "enforced" and not (e["ro_args"] or e["ro_env"] or e["rw_args"] or e["rw_env"]), "'enforced' needs a read-only switch"),
            (any(not e[k] for k in ("writes", "data_leaves", "verified", "license", "docs", "publisher")), "missing a description field"),
        ]
        out += [f"{i}: {msg}" for bad, msg in checks if bad]
    return out
