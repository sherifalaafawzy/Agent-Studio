---
title: Build a workflow from an API
section: How-to
order: 73
summary: Import an OpenAPI file and use its endpoints as boxes in a workflow, with approval before anything changes data.
tags: how to, workflow, openapi, api, import, endpoints, webhook, approval
---
**You will need:** an OpenAPI file or its web address.

## Steps

1. Open **Workflows** and the **OpenAPI steps** tab.
2. Choose **Upload**, **URL** or **Paste** and give the description.
3. Review the endpoints. Switch off the ones you will not use. Give default values to ones you call often.
4. If the API needs a key, add it. It is stored and never shown again.
5. Go to the **Flows** tab and press **New workflow**. Choose **Build it myself**, or **Talk to an agent** and describe what you want.
6. Add the endpoint as an **API call** box. Connect it between a **Task input** and a **Result**.
7. Select the box. In the right panel fill in its inputs with `{{ }}` expressions to use earlier results.
8. Press **Test this box** with sample text. Pin its output to reuse it.
9. Add an **Ask a person** box before any endpoint that changes data, and tick **I confirm** on that box only if you mean it.
10. Run it. Open the run in the **Runs** tab to see each box's input and output.

> Endpoints that change data need **allow writes** on the API *and* **I confirm** on the box. This is on purpose.

See [Workflows](doc:workflows) and [Workflow boxes](doc:workflow-boxes).
