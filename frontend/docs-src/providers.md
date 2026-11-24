---
title: Model providers
section: Reference
order: 91
summary: Connect Ollama, Anthropic, OpenAI, Google, Bedrock and any OpenAI-compatible endpoint, with keys, limits and prices.
tags: providers, models, api key, vertex, gemini, anthropic, openai, ollama, bedrock, openrouter, groq, xai, custom endpoint, vllm, lm studio
---
A **provider** is where a model comes from. Open Settings, **Model providers**.

## The built-in list

Ollama (local), Anthropic, OpenAI, Google Gemini API (AI Studio), Google Vertex AI (Gemini), xAI (Grok), Groq, OpenRouter, Amazon Bedrock, and a **mock** provider that never touches the network or spends anything. Each has a label for whether your prompts stay on this PC.

## Add a key

1. Open the provider and choose an auth mode. Most take an API key; others use an environment variable or no key.
2. Paste the key into the box on this page. **Never paste a key into a chat.** It is stored on this PC and never shown again (only a hint).
3. Set the **limit in dollars** and period. See [Billing and limits](doc:billing-and-limits).
4. Choose default, strong and fast models.
5. Switch the provider on and press **Test**.

Vertex AI can use application-default credentials from `gcloud auth application-default login`, a service-account file, or pasted service-account JSON (write-only). Bedrock takes a region and either a profile or write-only keys.

## Connect any OpenAI-compatible endpoint

For vLLM, LM Studio, a LiteLLM proxy, OpenRouter, Together, Groq or your company's gateway.

1. Add a provider of the **OpenAI-compatible endpoint** kind and give it a name.
2. Give its address, usually ending in `/v1`. Add a key and extra headers or an organization if it needs them (all write-only).
3. Press **Find models** to see what it offers, or type model names yourself.
4. Enter prices per million tokens, in and out, and the context size. For a model you run yourself enter 0 and 0. If you leave a price blank Agent Studio treats the model as expensive, to stay safe.
5. Press **Test**. Failures say exactly why.

The label shows whether your prompts stay **on this PC**, **on your network** or **go out to the internet**. Custom endpoints work in chat, Auto mode, the context meter and the AI-BOM, and each has its own spend limit.

## Maximum output

Each provider has a **maximum output tokens** setting. Spend limits reserve the worst case, so a smaller maximum lets you use more of your limit. See [Billing and limits](doc:billing-and-limits).

## Which one for what

- Free and private: Ollama.
- Strongest answers: an online provider with a limit.
- Voice with an avatar: Vertex AI for [Live mode](doc:live-mode).
