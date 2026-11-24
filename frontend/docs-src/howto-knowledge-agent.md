---
title: Build an agent that knows your documents
section: How-to
order: 71
summary: Make a knowledge base from your files and give it to an agent that cites its sources.
tags: how to, knowledge, rag, documents, agent, embeddings, citations
---
**You will need:** an embedding model (a local one through Ollama is fine) and a few documents.

## Steps

1. Open **Knowledge** and press **New knowledge base**.
2. Pick the embedding model and a store. **Local file** needs no setup.
3. Open the knowledge base and add your files, or paste text.
4. Use the **test search** with a question you really expect. If the passages found are wrong, split large files or add clearer ones.
5. Open [Agent Studio](doc:agents) and create or open an agent.
6. In the editor, under **Knowledge bases**, tick the knowledge base.
7. In its instructions add: "Answer from the knowledge base. If it does not contain the answer, say so."
8. Chat with the agent and ask the same question. It should answer and show the source.

> The embedding model is fixed when the knowledge base is created. To change it, make a new one.

## Check it

Ask something the documents do not cover. A good agent says it does not know instead of guessing. If it guesses, tighten the instruction.

See [Knowledge](doc:knowledge) and [Agent editor](doc:agent-editor#knowledge-bases).
