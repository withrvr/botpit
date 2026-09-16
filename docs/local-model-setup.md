# Local and self-hosted model serving

Research findings for §1.6 of the build prompt. Written 2026-09-12, before Phase 1.
Everything here feeds the Phase 5 agent-client interface and the Phase 14/15
deployment plan. Sources are listed at the end of each section.

## 1. Baseline: what is actually installed

Verified on this machine on 2026-09-12:

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 3050 6GB Laptop GPU, 6144 MiB |
| Ollama | v0.33.2, running on Windows, reachable from WSL at `http://localhost:11434` (mirrored networking) |
| Models present | `gemma3:4b` (3.3 GB, Q4_K_M), `llama3.1:8b` (4.9 GB, Q4_K_M), `qwen3:4b` (2.5 GB, Q4_K_M), `phi4-mini:latest` (2.5 GB, Q4_K_M) |

Implication: `llama3.1:8b` at 4.9 GB plus KV cache does not leave room for a
second model. Only the three ~2.5-3.3 GB models can pair up in VRAM, and only
at short context.

## 2. Ollama on a 6 GB card: the knobs that matter

All of these are process environment variables on the Windows Ollama service
(set them in the Windows user environment and restart the Ollama app). None
are set from the botpit codebase; botpit only reads the base URL and model
names from config.

### 2.1 Concurrency

- `OLLAMA_MAX_LOADED_MODELS` (default `3 * GPUs`): how many models may be
  resident at once, "provided they fit in available memory". On 6 GB the
  default is a lie in practice: a second model only loads if it fits
  entirely in the remaining VRAM, otherwise the request queues until the
  first model unloads.
- `OLLAMA_NUM_PARALLEL` (default `1`): parallel requests per loaded model.
  Each parallel slot multiplies KV-cache memory by the context length.
  Raising it on 6 GB pushes toward CUDA OOM or CPU offload.
- `OLLAMA_MAX_QUEUE` (default `512`): requests queued before Ollama returns
  a 503. Irrelevant at our scale but worth knowing the failure mode is a
  hard reject, not a hang.

### 2.2 Memory

- `OLLAMA_CONTEXT_LENGTH` (default `4096`): per-request context. Our prompts
  (rules summary + player_view + few chat lines) should fit in 2k-3k tokens.
  Keep 4096; do not raise globally.
- `OLLAMA_FLASH_ATTENTION=1`: applied automatically when supported; set
  explicitly so the next knob works.
- `OLLAMA_KV_CACHE_TYPE=q8_0`: halves KV-cache VRAM versus the default
  `f16` with negligible quality loss for short structured outputs. `q4_0`
  quarters it, but degrades output quality more; not needed at 4k context.
- Quantization: everything installed is already Q4_K_M, which is the
  correct size/quality point for 6 GB. Do not go lower for the 4B models;
  Q4_K_M 8B is the ceiling for this card.

### 2.3 Keep-alive and the model-swap cost

- Default `keep_alive` is 5 minutes. A cold load of a 4B-8B Q4 model from
  NVMe is roughly 3-10 seconds; 10-30 seconds on slower storage.
- `OLLAMA_KEEP_ALIVE=-1` pins models forever. On 6 GB with two or more
  models in play this is actively harmful: a pinned model is never evicted,
  so the second model's load fails instead of swapping.
- Correct setting for botpit dev: `OLLAMA_KEEP_ALIVE=30m` (long enough that
  a model does not go cold between turns of a slow game, short enough that
  an idle model eventually frees VRAM), `OLLAMA_MAX_LOADED_MODELS=2`,
  `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`.
- Keep-alive does not reduce the swap cost itself. The swap cost is a load
  from disk every time. The only real reductions are: fewer distinct models
  per game (scheduling, see §13 of DECISIONS.md), smaller models so two fit
  at once, and the botpit-side turn scheduler grouping consecutive AI turns
  by model where turn order allows.

**Resolved -- DECISIONS.md §13 agrees with this, not in conflict with it.**
The practical rule is "at most two 4B-class models resident; an 8B model
is alone," matching `OLLAMA_MAX_LOADED_MODELS=2` above.

### 2.3.1 The scheduler, built (Phase 16)

`app.llm.scheduler.ModelAffinityScheduler` -- one instance per LLM
base_url (created lazily inside `ModelRegistry`, never shared across
different backends), wrapping every client `ModelRegistry.client_for`
hands out via a transparent `_ScheduledClient`. Before a real completion
call is allowed to fire, it must `acquire()` its model: immediate if that
model already has a call in flight, or if the active set is below
`LLM_MAX_LOADED_MODELS`; otherwise it waits (up to
`LLM_SCHEDULER_MAX_WAIT_SECONDS`, then is admitted anyway -- a turn's own
deadline must never be blocked forever by scheduling). This is exactly
what "prefer tables that share a model" and "group consecutive AI turns
by model" mean at the one layer this code can actually act on: which
already-due call gets to fire next across every room sharing one
backend, not which seat's turn comes next within one game (that's fixed
by the game's own rules).

Deliberately a heuristic, not a perfect model of Ollama's real VRAM
state: "active" here means "has at least one in-flight call," not "Ollama
still has it loaded under keep_alive." A model with zero in-flight calls
drops out of the tracked set immediately, even if Ollama itself hasn't
evicted it yet -- so under bursty traffic the scheduler can still admit a
third model sooner than ideal. Querying Ollama's own `/api/ps` for ground
truth would close that gap but costs a real network round trip per
admission decision; not built, a documented tradeoff.

`botpit_model_swaps_total` (Phase 13's metric) is exactly what would show
whether this is actually reducing swaps in practice: it fires at real
dispatch time (after the scheduler admits a call), so comparing its rate
before/after this phase -- or under different `LLM_MAX_LOADED_MODELS`
values -- is the intended way to validate the scheduler's real effect
once real concurrent multi-model traffic exists to measure.

### 2.4 The API surface botpit will use

Ollama exposes two shapes:

- Native `/api/chat`: accepts `format` (JSON schema), `options.num_ctx`,
  `keep_alive` per request, `think`.
- OpenAI-compatible `/v1/chat/completions`: accepts `response_format`
  (json_schema), `tools`, `seed`, `temperature`, `stream`. Does **not**
  accept `keep_alive` or `num_ctx`; those must be baked into a Modelfile.
  `tool_choice`, `logprobs`, `n` are unsupported.

Decision for Phase 5: the botpit client interface is
`complete(messages, json_schema, temperature, seed, max_tokens) -> parsed`.
Two thin adapters: `OllamaNativeAdapter` (dev, gets `keep_alive`/`num_ctx`
control) and `OpenAICompatAdapter` (anything that speaks `/v1/chat/completions`:
Ollama itself, llama-server, vLLM). Config picks the adapter and base URL.
Nothing above the adapter knows which one is in use.

Sources: [Ollama FAQ](https://docs.ollama.com/faq),
[Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility),
[Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs),
[keep-alive behaviour and cold-start timings](https://www.runaihome.com/blog/ollama-model-keeps-reloading-vram-fix-2026/),
[multi-model memory tuning](https://eastondev.com/blog/en/posts/ai/20260406-ollama-multi-model-deployment/).

## 3. Self-hosted serving for staging and production

### 3.1 Engines compared

| Engine | Status (Sep 2026) | GPU needed | Model format | Concurrency | OpenAI-compatible | Fit for botpit |
|---|---|---|---|---|---|---|
| Ollama | active, v0.33 | no (CPU works) | GGUF | serial by default, `OLLAMA_NUM_PARALLEL` | yes (`/v1`) | dev; also fine on a GPU box |
| llama.cpp `llama-server` | active | no | GGUF | continuous batching, `--parallel N` slots | yes, incl. `response_format` json_schema | **staging/prod default** |
| vLLM | active | yes (NVIDIA/ROCm, Linux) | safetensors, AWQ/GPTQ/FP8 | PagedAttention, best throughput | yes, most complete | only if a GPU box appears and >10 concurrent agents |
| TGI | **archived 2026-03-21**, frozen at v3.3.7; HF recommends vLLM/SGLang | yes | safetensors | good | yes | **eliminated** |

Why llama-server for the server tier:

- Same GGUF files as dev, so the model list in config is identical across
  environments. No re-quantizing to safetensors.
- Runs on CPU, which is what a cheap VPS has. Falls back to partial GPU
  offload automatically if a GPU appears later.
- `/health` returns 503 during load and 200 when ready, `/slots` reports
  per-slot state. Both map straight onto the §1.4 health/metrics work.
- `--parallel N` and `--ctx-size` give explicit control the botpit
  scheduler can reason about (N slots of C tokens each).
- Official Docker images: `ghcr.io/ggml-org/llama.cpp:server` (CPU) and
  `ghcr.io/ggml-org/llama.cpp:server-cuda` (GPU). Same compose service,
  different image tag.

Why not Ollama on the server: it works, but it is a desktop-oriented
supervisor around llama.cpp. On a headless box it adds a model registry we
do not need and hides the slot/context arithmetic behind env vars. If the
server has a GPU and we want continuity with dev, it remains a valid
alternative and needs zero botpit code changes.

Why not vLLM now: requires an NVIDIA GPU and Linux, cannot load GGUF, and
its advantage (10x throughput at 10+ concurrent users) is irrelevant at
"a few friends and a handful of agents". Its OpenAI endpoint is the same
shape as llama-server's, so switching later is a base-URL change.

### 3.2 CPU-only reality check

Measured community numbers for Q4_K_M models on CPU:

| Model | Hardware | tok/s |
|---|---|---|
| 3B-4B (Phi-4-mini, Qwen 3B) | modern x86, 10-12 threads | 10-15 |
| 3B Q4_K_M | Oracle Ampere A1 (ARM, 4 cores) | ~10.6 |
| 8B Q4_K_M | 12-thread x86 | ~14 (best case), 4-8 typical on laptop-class |

Our agent turn is two calls (reason+move, then chat) with short JSON
outputs, roughly 100-250 generated tokens per turn. At 10 tok/s that is
10-25 seconds per AI turn on CPU. Playable for a turn-based card game,
but a 10-seat all-AI table is slow. This is the single biggest reason
staging should run a 4B model, not 8B.

Memory bandwidth, not core count, is the bottleneck. Hetzner's dedicated
CCX line costs 5x the shared CX line for the same RAM and does not buy
proportionally more bandwidth; shared CX43 (8 vCPU, 16 GB, €15.99) or
CAX31 (8 ARM vCPU, 16 GB, €20.99) are the sane CPU-inference boxes.

### 3.3 GPU option for production

Hetzner GEX44 (RTX 4000 SFF Ada 20 GB) is €234/month plus €114 setup:
too expensive for a side project that plays a few games a week.
On-demand marketplace GPUs (RunPod community, Vast.ai) rent an RTX 4090
for $0.30-0.40/hour. A pod running `llama-server` (or Ollama) pointed at
by `LLM_BASE_URL` costs roughly $10-30/month for evening sessions and is
20-50x faster than CPU.

Decision: production config supports both. `LLM_BASE_URL` points at the
co-located CPU llama-server by default; an admin can repoint it at a GPU
pod (runtime setting from §1.8's `runtime_settings` table) when a play
session is scheduled. The botpit code is identical either way.

### 3.4 What "download and serve a model on the box" involves

1. `huggingface-cli download <org>/<model>-GGUF <file>.Q4_K_M.gguf` into a
   persistent volume (models are 2.5-5 GB; keep them off the container
   image).
2. Compose service:
   ```yaml
   llm:
     image: ghcr.io/ggml-org/llama.cpp:server
     command: >
       -m /models/qwen3-4b.Q4_K_M.gguf --host 0.0.0.0 --port 8080
       --ctx-size 8192 --parallel 2 --cache-type-k q8_0 --cache-type-v q8_0
       --jinja
     volumes: [models:/models]
     healthcheck: {test: ["CMD", "curl", "-f", "http://localhost:8080/health"]}
   ```
3. botpit `.env.staging`: `LLM_PROVIDER=openai_compat`,
   `LLM_BASE_URL=http://llm:8080/v1`, `LLM_MODELS=qwen3-4b`.
4. One llama-server process serves one model. Multi-model tables on the
   server tier mean one `llm-<name>` service per model, each with its own
   port, and the botpit model registry maps model name to base URL. This
   is a config-shape change only (a dict instead of one URL) and is
   designed into Phase 5's `ModelRegistry`.

Sources: [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
[TGI archived / maintenance mode](https://github.com/huggingface/text-generation-inference),
[Ollama vs vLLM vs llama.cpp 2026](https://dev.to/thurmon_demich/ollama-vs-llamacpp-vs-vllm-which-should-you-use-in-2026-10gp),
[CPU inference numbers](https://www.promptquorum.com/local-llms/best-cpu-only-llm),
[Ampere A1 llama.cpp benchmark](https://tiffena.me/blog/ai-infrastructure/benchmark-cpu-only-llm-inference-oracle-ampere-a1-llama.cpp-ollama-docker/),
[Hetzner GEX44 review](https://gpuhosted.com/en/hetzner-gpu-review/),
[GPU marketplace pricing](https://northflank.com/blog/cheapest-cloud-gpu-providers).

## 4. Consequences for the Phase 5 interface

- One protocol: `LLMClient.complete(request: CompletionRequest) -> CompletionResult`.
- Two adapters: `ollama_native` (dev only, needed for per-request
  `keep_alive`/`num_ctx`) and `openai_compat` (everything else).
- `ModelRegistry`: model name -> (adapter, base_url, options). Dev has one
  base URL for all models; server tier may have one per model.
- Health probe is part of the adapter protocol: Ollama `/api/tags`
  (lists models, proves presence), llama-server `/health` + `/v1/models`.
  The §1.4 "Ollama: reachable, 3/3 models present" panel calls the same
  probe regardless of backend.
- No third-party hosted API adapter exists in the codebase. Adding one
  later would be a third adapter, not a change to anything upstream.
