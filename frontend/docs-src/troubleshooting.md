---
title: Troubleshooting
section: Reference
order: 95
summary: The most common problems and what to do about each.
tags: troubleshooting, errors, problems, help, backend offline, ollama, preview blank, install failed, spend limit
---
## The sidebar says "Backend offline"

The API is not running. In a terminal in the project folder run `npm run dev`. The sidebar shows **Connected** once it answers.

## "Refused: the spend limit would be exceeded"

The worst case of the request is more than what is left under the provider's limit. Raise the limit, lower the provider's **maximum output tokens**, or use a cheaper or local model. See [Billing and limits](doc:billing-and-limits).

## No model answers

- Is a provider switched on, with a key and a default model? See [Model providers](doc:providers).
- For Ollama, is it running? Settings, Local models can start it.
- Press **Test** on the provider and read the reason.

## An install fails

The message says in plain words why. Typical causes are no internet, little disk space, or an antivirus holding a download. Retry. For tools built from source (some red-team dependencies) the vetted list is pinned, so the same fix will not help a different package. See [Red team](doc:red-team).

## The app preview is blank

The preview is a sandboxed frame on its own local port. Some embedded browsers block it. Open Agent Studio in a normal browser such as Chrome. See [Apps](doc:apps).

## A red-team run stopped with no result

If the backend restarted while it was running (it does that when files change during development), the scan was cut off. Start it again. A run counts for the scorecard only when it finished with probes sent. See [Security](doc:security#what-counts-as-evidence).

## Voice does not work

- Allow the microphone for the page, then reload.
- For the local engine, run the one-click install on the Live page.
- For Gemini, check the Vertex provider's project, region and sign-in. See [Live mode](doc:live-mode#if-something-goes-wrong).

## A guard model says it is not available

Agent Studio fetches it from Ollama when you press **Try it**, save the profile or switch guardrails on. Wait for the progress card at the top of the Guardrails card. See [Guardrails](doc:guardrails).

## The assistant does not know what is on my screen

Screen reading may be off in Settings, Assistant. It also cannot see anything it is not allowed to, such as passwords and keys. See [The assistant and these docs](doc:assistant-and-docs).

## Still stuck

Open the run in [Runs](doc:runs) or look at [Telemetry](doc:telemetry) for the error with its plain-language explanation.
