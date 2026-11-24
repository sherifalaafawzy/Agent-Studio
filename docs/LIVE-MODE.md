# Live mode (Phase 8)

Talk to a model by voice, **voice only** or with an **avatar**, on two engines you switch between on the Live page (`/live`) or in Settings > Voice.

| | On this PC | Google Gemini Live |
|---|---|---|
| Listens with | Whisper (faster-whisper) | Gemini Live (`gemini-3.8-live`) |
| Thinks with | any model, e.g. Ollama `qwen3:8b` | Gemini Live |
| Speaks with | Kokoro (82M) | Gemini Live (30 prebuilt voices) |
| Avatar | animated face drawn in your browser, lip-synced to the voice (works offline) | Google's Live Avatar video |
| Data leaves this PC | no | yes (audio and answers go to Google Cloud) |
| Tools | none (the model only talks) | one tool, `ask_agent`, that runs a normal Agent Studio turn with the chosen agent |
| Cost | free | Google bills your project; Agent Studio ends a session at your cost limit |

## The local pipeline

```
microphone (16 kHz frames over one WebSocket) → voice-activity detection → Whisper → model token stream
   → phrase chunker (the first phrase after ~6 words) → Kokoro (streams audio) → 24 kHz audio, played as it arrives
```

* **Isolated install.** One click installs faster-whisper and kokoro-onnx into their own environment (`data/envs/voice`): ready-made wheels only, nothing younger than 14 days, exact versions, model files checked against pinned hashes / a pinned Hugging Face commit. On an NVIDIA GPU it also installs the CUDA 12 libraries and the GPU build of onnxruntime; the very first GPU start compiles kernels (about a minute, done by the install's last step). Piper is not used: its Python package is GPL-3.0.
* **Warm models.** The worker keeps both engines loaded; the model is loaded and primed with a one-token generation when a session starts (a cold load measured 15 s; the first prompt another ~2 s).
* **`think: false`.** A thinking model otherwise produced no spoken word for 7+ s.
* **Barge-in.** Talking over the assistant (stricter threshold, so its own voice leaking into the mic does not count) cancels the model and the voice, flushes the audio already queued in the browser, and keeps in the history only what was actually spoken. Without barge-in the microphone is not sent while the assistant talks or its audio is still playing.
* **Latency readout.** Every answer shows each stage and *first sound after you stop*, measured in the browser (end-of-speech wait + server time + audio scheduling + the device's output latency). The **speed test** speaks a sentence, plays it back as the microphone and times three answers, with no microphone needed.

### Measured on this PC (RTX 5080, qwen3:8b through Ollama, Whisper small.en on the GPU, Kokoro on the GPU)

| | median |
|---|---|
| waiting to be sure you stopped (setting, default 350 ms) | 350 ms |
| Whisper | 60–70 ms |
| model's first word | 25–35 ms (warm) |
| voice start-up (first phrase) | 190–290 ms |
| **server: end of speech detected → first audio** | **340–450 ms** |
| **from the moment you stop talking → first audio frame sent** | **about 700–800 ms** |
| browser-measured first sound after you stop, real Chrome | 820–860 ms |

With Kokoro on the CPU instead of the GPU the same turn was about 100–300 ms slower. Numbers differ on other machines.

## Google Gemini Live

Built from Google's documentation (model page, Live API overview and reference, SDK and WebSocket tutorials, *Configure live avatars*, *Configure language and voice*, and the Gen AI SDK type definitions), read on 2026-11-23.

* **Transport.** The browser talks to Agent Studio, Agent Studio talks to Google over one WebSocket to `wss://{location}-aiplatform.googleapis.com/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent`, authenticated with an OAuth 2.0 bearer token. Google says clients must not connect directly; the credential stays in the backend. A `ws://` endpoint (a local proxy or test server) is sent **no** token: it is never put on the wire in plaintext.
* **Credentials.** Nothing new to paste. The engine uses the existing **Google Vertex AI** provider (Settings > Model providers): its project, region and auth mode (application-default credentials, a service-account file, or pasted service-account JSON, stored write-only). An API key is not accepted by this endpoint. The region must be regional (`us-central1`, `eu`...); `global` is replaced by `us-central1`.
* **Session.** `setup` (model path, `response_modalities`, `speech_config`, `avatar_config`, transcription, the `ask_agent` tool) → `setupComplete` → 16 kHz PCM up in `realtimeInput`, 24 kHz PCM / video / transcriptions / `toolCall` / `usageMetadata` / `goAway` down. Google's own voice-activity detection decides turns; Agent Studio runs a light detector only for the "hearing" light and the timings.
* **Live Avatar.** `response_modalities: ["VIDEO"]` plus `avatar_config.avatar_name`. Google documents prebuilt avatars but, in the pages read, lists no names (its example is **Ben**), so the name is a free-text setting. Custom avatars need Google's approval and are not offered. Optional `video_bitrate_bps` / `audio_bitrate_bps` are exposed because Google's SDK types define them.
* **Video playback.** Google documents the output only as "24 FPS MP4". Observed against the real service on 2026-11-23: every server message is a **binary WebSocket frame holding JSON**; the avatar video arrives as `serverContent.modelTurn.parts[].inlineData` with `mimeType: "video/mp4"` and base64 `data`, about 130 chunks (about 3.3 MB) for a two-second reply; the first chunk is a DASH-style fragmented-MP4 init segment (`ftyp`/`dash`/`avc1`), then movie fragments, the picture is 704x1280 portrait, and **the sound is inside the video** (no separate PCM). Agent Studio forwards each part with its MIME type; the browser streams it into a `<video>` through Media Source Extensions (old video behind the playhead is trimmed) and falls back to collecting the answer if the browser cannot. If audio ever arrives as separate PCM the video is muted so you do not hear it twice.
* **The avatar streams continuously.** Observed: video keeps flowing the whole time the session is open, **including while nobody is talking** (32 s buffered in a 32 s session), at about 2,500 video tokens per second, i.e. roughly **$0.15 per minute** at the list price. The screen does not show "speaking" for idle video, and the cost meter counts that time.
* **Tools.** With an agent chosen, Gemini is given one function, `ask_agent(request)`. A call runs a normal Agent Studio chat turn with that agent in one chat per session (so the agent's tools, memory and guardrails apply); the answer goes back with the same call id, in the structured `{status, retryable, result|message}` form Google asks for. A call Google cancels is cancelled.
* **Limits and cost.** Longest session (default 10 min) and a per-session cost limit (default $1). The cost is an **estimate** from Google's `usageMetadata` at Google's list prices (non-global, per million tokens: audio in $3.00, audio out $12.00, text in $0.75, text out $4.50, video/image in $1.00, avatar video out $1.00).
* **Latency readout.** server time to first audio and to the first piece of avatar video, plus the time the first picture was actually drawn in the browser.

### What was verified against Google, and what was not

The Vertex provider already stored on this machine uses **application-default credentials** (the user's `gcloud auth application-default login`, project `gap-cloud`; no API key, no stored service account). Through the app's own server-side credential path, on 2026-11-23:

* **Verified live:** the connection test; a typed voice conversation (`gemini-3.8-live`, us-central1) and the same through the real Live page in real Chrome; the browser's 16 kHz microphone audio (a recorded sentence played into the page as the microphone) reaching Gemini and being answered; **the Live Avatar with the avatar name "Ben"** streaming 704x1280 video into the page; the usage metadata (below); an agent profile (Gemini, voice Aoede, persona applied, budget, history). Total spend of all live calls: well under a dollar.
* **Not verified:** other avatar names (Google lists none in the pages read), custom avatars and custom voices (allow-list only), tool calls through `ask_agent` against real Google (tested with the fake server only), session resumption, long sessions, one transient "Internal error (code 1011)" from Google after the first avatar chunks that did not repeat in three further runs.
* **Measured Gemini latency (typed prompt, real Google):** voice-only, first audio about **0.5 s** after the message; mic speech in real Chrome, first sound **1.39 s** after you stop (of which 350 ms is the end-of-speech wait); avatar, first video chunk **1.8-2.7 s** after the message (2.2-2.3 s seen in the browser).

The rest was tested against **a fake Google server that follows the documented messages** (and, since the real run, Google's real framing: binary frames, `candidatesTokensDetails`, usage in the `turnComplete` message).

## Agent Live profiles

An agent (agent editor and wizard, section **Live**) can carry a Live profile: *can be used in live mode*, **engine** (this PC or Gemini), **mode** (voice only or avatar), voice, language (Gemini), the avatar name, whether the local engine uses the agent's tools (`agent_turns`), and a **budget per session**. It is stored in the agent record (`live` column, migrated automatically, `{}` = none), returned and accepted by `/api/agents` (so it exports and imports with the agent), validated with plain messages, and shown as a badge in the agents list (`Live · Gemini · avatar`, `Live off`). Starting live mode with that agent runs its persona and, when the profile is enabled, **the profile decides** the engine, mode, voice, language, avatar and budget (the server enforces this; the page shows it). A profile that is switched off makes live mode refuse the agent. Tools: Gemini reaches the agent's tools through `ask_agent`; on this PC `agent_turns` makes every reply a full agent turn (tools, knowledge, memory, guardrails), spoken once complete (slower); without it the model speaks the persona directly, with no tools. The per-agent AG-UI switches govern AG-UI answers and the `/api/agui` endpoint, not live mode; the agent's other switches (API access, A2A, guardrails, identity) apply to its turns as usual because those go through the normal chat path.

## Cost and usage

While a session is open the browser gets a `usage` message about **once a second**, shown as the cost meter: cost so far, cost per minute, elapsed time, tokens in and out, audio seconds in and out, avatar video seconds, a breakdown by stage with a label on every row, and the budget bar. Every figure says where it comes from: **reported** (Gemini's own `usageMetadata`, the session's tokens so far per modality, used as is), **estimated** (from token rates, for what was streamed since the last report), **mixed**, or **local** (this PC: usage shown, no cloud cost). Estimates are shown with a "≈". Reported usage replaces the estimate for what it covers.

* **Price table**: dollars per million tokens by modality, stored as the editable setting `live_gemini_prices` with the date it was read and where from, labelled *prices as of <date>, verify*. Defaults were read from Google's pricing page on 2026-11-23 (Gemini 3.8 Live API, non-global: text in $0.75, audio in $3.00, video/image in $1.00, text out $4.50, audio out $12.00, avatar video out $1.00). **Read Google's pricing page now** re-reads it (and stores today's date); it refuses to change anything when the page cannot be read. The token rates used for the estimate are in the same setting: video about 2,500 tokens/s and audio out about 25 tokens/s were **measured** on the real service; audio in (25/s) is an **assumption**.
* **Budget**: per session, from the agent's profile or the Live settings' limit. A warning at **80%**, and at **100%** the session is ended with a clear message saying what was used and how it is known. Google reports usage only at the end of a turn, so between reports the budget follows the estimate (an idle avatar's video time included).
* **History**: every session that really started leaves a row (`live_sessions`: agent, engine, mode, model, voice, seconds, cost and its source, budget, why it ended, usage and breakdown), shown on the Live page (*Past sessions*) and at `GET /api/live/history`. Gemini sessions also add a row to the **spend ledger**, so Billing and the provider's cap see them (with a note saying whether the figure was reported or estimated).

## Files

Backend: `voice_spec.py`, `voice_local.py` (installer), `voice_fetch.py`, `voice_worker.py` (runs in the voice environment), `live_engine.py`, `live_llm.py`, `live.py` (voice-activity detection, phrase chunker, session), `gemini_live.py`, `live_cost.py` (prices, meter, budget, history), `live_profile.py`, `live_api.py` (`/api/live/*`, WebSocket `/api/live/ws`).
Frontend: `lib/live.ts`, `lib/avatarVideo.ts`, `components/live/*` (panel, avatar, latency, usage meter, history, price table, install), `components/LiveSettings.tsx`, `components/AgentLive.tsx`, `app/live/page.tsx`.
