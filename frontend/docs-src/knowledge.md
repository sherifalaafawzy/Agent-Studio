---
title: Knowledge
section: Build
order: 35
summary: Make your own documents searchable so agents look things up before answering and show where each fact came from.
tags: knowledge, rag, documents, embeddings, vector store, qdrant, search, upload, retrieval
routes: /knowledge/
---
A **knowledge base** holds your documents, turned into searchable passages by one **embedding model**. An agent searches it before answering and shows where each fact came from. Use them for manuals, policies, notes or reports.

![Knowledge bases and vector stores](shot:knowledge)

## Create one

1. Press **New knowledge base**.
2. Choose an **embedding model**. A model that turns text into numbers so documents can be searched by meaning. It cannot be changed afterwards.
3. Choose where the vectors live (see below).
4. Open the knowledge base and add files, or paste text.
5. Use the **test search** to check it finds what you expect.

## Where vectors live

A **store** keeps the vectors.

- **Local file:** no setup, good for small collections.
- **A Docker container** such as Qdrant: start one here with one click.
- **A service you connect**, including Amazon S3 Vectors.

Press **Add store** to add one.

## Give it to an agent

In the [agent editor](doc:agent-editor#knowledge-bases) tick the knowledge bases that agent should search. In a workflow use the **Knowledge search** box. See [Workflow boxes](doc:workflow-boxes).

## Tips for good answers

- Prefer clear, self-contained passages over huge pages.
- Test with the questions you really expect.
- Keep one topic per knowledge base so results stay focused.

The AI-BOM records only hashes of knowledge bases, never their contents. See [AI-BOM and discovery](doc:ai-bom).

See [Build an agent that knows your documents](doc:howto-knowledge-agent).
