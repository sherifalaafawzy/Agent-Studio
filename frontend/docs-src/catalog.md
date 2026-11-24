---
title: Catalog
section: Build
order: 37
summary: Ready-made skills, vetted public MCP servers, and ML tools such as the model trainer and cloud accounts.
tags: catalog, skills, mcp, trainer, ludwig, pytorch, keras, sagemaker, vertex, azure ml, runpod
routes: /catalog/
---
The **Catalog** has three tabs. Nothing is added until you click, and anything that can change data starts read-only or asks first.

![The catalog: skills, public MCP servers and ML tools](shot:catalog)

## Skills

Twenty-four ready-made skills in eight domains: accounting, sales, marketing, manufacturing, engineering, data, machine learning and deep learning. Each has a "When to use" and a "Safety" paragraph. Add one to an agent with one click, or read it first. See [Skills](doc:skills).

## MCP servers

Thirty-three vetted public servers. Each was checked on npm or PyPI (or by contacting its endpoint) on 2026-11-22, is pinned to a release at least 14 days old, and says what it can change and where your data goes. It also shows how far it can be limited: read-only, a read-only switch, or none. See [MCP servers](doc:mcp-servers).

## ML tools

- **Model trainer:** Ludwig, PyTorch and Keras or TensorFlow, each in its own isolated environment, installed only after you accept the size and licence. Training runs as a cancellable background job on a CSV in your workspace.
- **Cloud accounts:** AWS SageMaker, Google Vertex AI, Azure ML, DigitalOcean, RunPod and Oracle OCI Data Science. Access is **read-only by default**.

Agents use these through two built-in MCP servers, `trainer` and `cloudml`, which are off until you enable them. Real training and real cloud calls have not been run by the developers, so try them with a small job first.
