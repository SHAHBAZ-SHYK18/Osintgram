# Osintgram HTTP API — step by step

Everything the web page does, it does through these endpoints: there is no
separate private API. That makes it usable as a local automation surface —
scripts, cron jobs, a notebook, your own UI — without driving a browser.

This guide is a walkthrough. For the exact request/response shapes, the live
OpenAPI schema is at **<http://127.0.0.1:8000/docs>** while the server runs.

> The app has **no authentication** and binds to `127.0.0.1`. Do not expose it
> to a network, and do not put it behind a public URL, without adding auth.

---

## 0. Start the server

```bash
pip install -r requirements.txt
uvicorn src.web.app:app --host 127.0.0.1 --port 8000
```

Base mode needs no model; AI mode needs Ollama (`ollama serve`, plus a
tool-calling model pulled). Every example below uses `BASE=http://127.0.0.1:8000`.

---

## 1. Give it a key

Three sources, in precedence order — `HIKERAPI_TOKEN` in the environment, the
key entered at runtime through the API/UI, then `config/credentials.ini`.
Prefer the environment or the file for scripts: the runtime key only lives as
long as the server process.

```bash
export HIKERAPI_TOKEN=your_key_here      # wins over everything else
```

Or set it over HTTP (this is what the 🔑 panel does):

```bash
curl -s $BASE/api/token \
  -H 'Content-Type: application/json' \
  -d '{"token":"your_key_here","remember":true}'
```

```json
{
  "configured": true, "source": "runtime", "locked": false,
  "preview": "your…here", "balance": {"requests": 474, "amount": 1.5, "currency": "USD"},
  "saved": true
}
```

The key is **verified against HikerAPI's free `/sys/balance` before being
accepted** — a typo fails here, not halfway through a run. `remember: true`
writes it to `config/credentials.ini` (mode `600`). The key is never returned
by any endpoint afterwards, only the masked `preview`.

Check the current state at any time — and note `locked: true` when
`HIKERAPI_TOKEN` is set, which means a key posted over HTTP is being ignored:

```bash
curl -s $BASE/api/token      # {"configured":true,"source":"env","locked":true,"preview":"env_…efgh"}
```

---

## 2. Discover the commands

Every lookup is a **command** with a name, a docstring and typed parameters.
Read them at runtime rather than hard-coding the list (it grows):

```bash
curl -s $BASE/api/tools
```

```json
[
  {"name": "get_user_info",
   "description": "Get profile info for the target: bio and bio links, …",
   "params": [], "needs_target": true},
  {"name": "get_followers",
   "description": "…",
   "params": [{"name": "limit", "type": "integer", "default": 200, "description": "Max followers to fetch"}],
   "needs_target": true}
]
```

Three commands work **without** a target (`"needs_target": false`) because they
belong to no account: `search_hashtag`, `search_location`, `get_post_comments`.

The full command list, grouped, is in [What you can ask for](#5-what-to-run-and-what-it-costs)
below and in the [user guide](web-ui.md#5-what-you-can-ask-for).

---

## 3. Price it before you run it

HikerAPI bills per request, so this is the step that keeps a scan affordable.
`/api/estimate` resolves the profile (one request, which the run would spend
anyway and the cache then serves) and applies the same pagination rules the
commands actually use:

```bash
curl -s $BASE/api/estimate \
  -H 'Content-Type: application/json' \
  -d '{"target":"nasa","calls":[{"name":"get_user_info"},
                               {"name":"get_followers","args":{"limit":100}}]}'
```

```json
{
  "per_command": [{"name": "get_user_info", "low": 0, "high": 0},
                  {"name": "get_followers", "low": 2, "high": 4}],
  "total_low": 2, "total_high": 4,
  "profile": {"media_count": 120, "follower_count": 900, "following_count": 42}
}
```

Estimates are ranges, not promises: page sizes vary and a cache hit costs
nothing. Compare `total_high` with `GET /api/balance` before committing.

---

## 4. Run it — manual (Base) mode

`POST /api/run` executes exactly the commands you list, in order, with no model
involved. That makes it deterministic and reproducible, which is what you want
from a script.

```bash
curl -s $BASE/api/run \
  -H 'Content-Type: application/json' \
  -d '{"target":"nasa",
       "calls":[{"name":"get_user_info"},
                {"name":"get_hashtags","args":{"limit_posts":20}}],
       "max_items":300}'
```

```json
{
  "target": "nasa", "target_id": 12345, "backend": "HikerAPI",
  "api_calls_total": 3,
  "answer": "",
  "tool_calls": [
    {"name": "get_user_info", "input": {}, "result": {"username": "nasa", "…": "…"},
     "is_error": false, "elapsed_ms": 412, "api_calls_delta": 1},
    {"name": "get_hashtags", "input": {"limit_posts": 20}, "result": {"…": "…"},
     "is_error": false, "elapsed_ms": 1380, "api_calls_delta": 2}
  ]
}
```

| Field | Meaning |
|---|---|
| `result` | That command's own JSON — the same object the UI card renders |
| `is_error` | A command can fail on its own (`{"error": …}`) without failing the run |
| `api_calls_delta` | Requests that command actually spent — the real cost signal |
| `api_calls_total` | Everything this run spent, cache hits excluded |
| `answer` | Always `""` in manual mode: there is no model to write one |

### The parameters that apply to every run

| Field | Default | What it does |
|---|---|---|
| `target` | — | Instagram username. Optional for the three target-free commands |
| `calls` | — | Ordered list of `{name, args}`; `args` may be omitted entirely |
| `max_items` | unlimited | **Hard server-side cap** on backend requests. Exceeding it raises `RequestLimitReachedError` — the run stops spending, not just the UI |
| `cache_ttl_seconds` | `300` | Cache lifetime for this run. `0` disables caching |
| `run_id` | — | Your id for this run, so `POST /api/cancel` can stop it |
| `verbose` | `false` | Stream events instead of returning one object (see §6) |

`args` entries are optional and get the command's own default; `null` or `"all"`
mean "no limit"; int-typed parameters are coerced and clamped to `max_items`.
The `input` echoed back in the response is the **effective** arguments — what
actually ran, not what you asked for.

### Errors you'll actually hit

| Status | Body | Cause |
|---|---|---|
| `400` | `Unknown command: nope` | A name not in `/api/tools` |
| `400` | `A target username is required for: get_user_info` | `target` omitted for a command that needs one |
| `400` | `Select at least one command.` | Empty `calls` |
| `404` | `@nobody not found` | The target resolved to nothing |
| `502` | `Could not reach HikerAPI (…)` | Network/proxy problem, **not** a rejected key |
| `502` | `No backend configured: …` | Neither a HikerAPI key nor an instagrapi session |

---

## 5. What to run, and what it costs

All 29 commands, with their parameters. `limit_posts` caps how many of the
target's posts are paged through; the `limit` on user lists caps accounts
fetched.

| Group | Command | Parameters |
|---|---|---|
| **Profile** | `get_user_info` | — |
| | `get_account_about` | — (flat 1 request) |
| | `get_user_propic` | — |
| **Network** | `get_followers` | `limit` (200) |
| | `get_followings` | `limit` (200) |
| | `get_suggested_profiles` | — (flat 1) |
| | `get_people_who_tagged` | `limit` |
| | `get_people_tagged_by_user` | `limit_posts` |
| | `compare_with` | `other_username`, `scope` (`both`\|`followers`\|`followings`), `limit` (500) |
| **Content** | `get_hashtags` | `limit_posts` |
| | `get_captions` | `limit_posts` |
| | `get_total_comments` | `limit_posts` |
| | `get_total_likes` | `limit_posts` |
| | `get_media_type` | `limit_posts` |
| | `get_posting_times` | `limit_posts` |
| | `get_photo_descriptions` | `limit_posts` |
| | `get_comment_data` | `limit_posts` (feed + one comment scan per post) |
| | `get_people_who_commented` | `limit_posts` (feed + one comment scan per post) |
| | `get_user_photos` | `limit` |
| | `get_addrs` | `limit_posts` (also reverse-geocodes) |
| **Stories** | `get_user_stories` | — (flat 2) |
| | `get_highlights` | — (flat 2) |
| **Contacts** | `get_followers_email` | `match_limit` (20), `max_checks` (100) |
| | `get_followings_email` | `match_limit`, `max_checks` |
| | `get_followers_phone` | `match_limit`, `max_checks` |
| | `get_followings_phone` | `match_limit`, `max_checks` |
| **No target** | `search_hashtag` | `hashtag`, `sort` (`top`\|`recent`), `limit` (30) |
| | `search_location` | `place`, `limit` (30) |
| | `get_post_comments` | `post` (URL, shortcode or id), `limit` |

The four contact commands are **the most expensive in the tool**: they spend
one extra request per account examined, bounded by `max_checks`. Keep it
small.

---

## 6. Watch it run (streaming)

Add `"verbose": true` and the response becomes `text/event-stream`, one JSON
event per `data: ` line, so you see each backend request as it happens instead
of waiting for the whole run:

```bash
curl -N -s $BASE/api/run \
  -H 'Content-Type: application/json' \
  -d '{"target":"nasa","verbose":true,"run_id":"run-1",
       "calls":[{"name":"get_user_info"}]}'
```

```
data: {"type": "start", "target": "nasa", "target_id": 12345, "backend": "HikerAPI", "api_calls_total": 0}
data: {"type": "tool_call", "name": "get_user_info", "input": {}}
data: {"type": "api_call", "method": "user_by_username_v2", "total": 1, "detail": "@nasa", "status": "ok", "error": null}
data: {"type": "tool_result", "name": "get_user_info", "result": {"…": "…"}, "is_error": false, "elapsed_ms": 412, "api_calls_delta": 1, "api_calls_total": 1}
```

| Event | When |
|---|---|
| `start` | Once, first — target resolved, backend named |
| `tool_call` | Before a command runs, with its effective `input` |
| `api_call` | **One per backend request**: `status` is `ok`, `cached`, `retry`, `limit` or `error` |
| `tool_result` | After it returns — `api_calls_delta` is what it cost |
| `answer_delta` / `answer` | AI mode only |
| `error` | Fatal; ends the stream |

The stream opens with a 2 KB SSE comment line on purpose: browsers otherwise
buffer small chunks and hand the page everything at the end.

**Stop a run that's spending too much** — server-side, so the worker really
stops instead of just the browser giving up:

```bash
curl -s $BASE/api/cancel -H 'Content-Type: application/json' -d '{"run_id":"run-1"}'
# {"cancelled": true}
```

`cancelled: false` means the run had already finished (or never existed).

---

## 7. Ask in plain language (AI mode)

If Ollama is running with a tool-calling model, `/api/query` lets the model
pick the commands. Same response shape as `/api/run`, plus a written `answer`:

```bash
curl -s $BASE/api/query \
  -H 'Content-Type: application/json' \
  -d '{"target":"nasa","message":"how many followers, and which hashtags do they use most?"}'
```

`target` may be omitted if the question is about a hashtag or a place. The
model can only choose from the same 29 commands — it cannot invent a lookup —
and `max_items`/`cache_ttl_seconds`/`verbose`/`run_id` behave identically.

**This endpoint needs a model; `/api/run` does not.** Which one is used is
decided at startup by `LLM_BACKEND` (`ollama` by default, `lmstudio` for any
OpenAI-compatible server, or `auto`):

```bash
ollama serve &                    # the model server
ollama pull llama3.1:8b           # ~4.7 GB, tool-calling capable
curl -s $BASE/api/about | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["llm_backend"], d["ollama_model"])'
# ollama llama3.1:8b
```

If nothing is listening you get a `502` whose message names the backend that
was actually selected — and if an OpenAI-compatible server is answering on the
default LM Studio port while `LLM_BACKEND` still says `ollama`, it tells you to
set `LLM_BACKEND=lmstudio` (that fallback is not automatic). In the streamed
mode it arrives as a final `{"type": "error"}` event instead, since the status
code is already sent.

---

## 8. Keep results

### Save a run as a dossier

Post back what `/api/run` returned; the server stores it under `dossier/` so it
can be reopened for free later and compared with a later run:

```bash
curl -s $BASE/api/dossier -H 'Content-Type: application/json' \
  -d '{"target":"nasa","api_calls_total":3,"results":[…] }'
# {"id":"1794212512-2fbf0b","target":"nasa","saved_at":1794212512,"commands":["get_user_info"],"api_calls":3}
```

| Endpoint | What it does |
|---|---|
| `GET /api/dossier` | List saved runs, newest first. `?target=nasa` narrows to one account |
| `GET /api/dossier/{target}/{dossier_id}` | One saved run, complete |
| `GET /api/dossier/{target}/{before_id}/diff/{after_id}` | What changed between two of them |
| `DELETE /api/dossier/{target}/{dossier_id}` | Remove one |

```bash
curl -s "$BASE/api/dossier?target=nasa"                    # list, newest first
curl -s $BASE/api/dossier/nasa/1794212512-2fbf0b           # one, complete
curl -s $BASE/api/dossier/nasa/<before>/diff/<after>       # what changed
curl -s -X DELETE $BASE/api/dossier/nasa/1794212512-2fbf0b # {"deleted": true}
```

The diff names each changed field with its old and new value:

```json
{"target":"nasa",
 "commands":[{"name":"get_user_info",
              "changed":[{"field":"follower_count","from":50,"to":75}],
              "unchanged":false}]}
```

Comparison is always oldest → newest regardless of the order you pass the ids.

### Zip the media

```bash
curl -s $BASE/api/download -H 'Content-Type: application/json' \
  -d '{"urls":["https://…jpg","https://…mp4"],"name":"nasa"}' -o nasa.zip
```

Per-file outcomes come back in the `X-Download-Report` response header (JSON):
a CDN link that has expired fails that one file, not the whole zip.

---

## 9. Housekeeping

```bash
curl -s $BASE/api/balance     # {"available":true,"requests":474,"amount":1.5,"currency":"USD"} — free, spends nothing
curl -s -X DELETE $BASE/api/cache            # {"cleared": 12}
curl -s $BASE/api/history/targets            # recent usernames
curl -s $BASE/api/about                      # version, credits, active model
```

History lives at `GET /api/history/{kind}` where `{kind}` is `targets` (recent
usernames) or `queries` (recent AI requests); remove one entry with
`DELETE /api/history/{kind}?value=nasa`. The value is a query parameter, not a
path segment, because a request's free text can contain `/` or `?`. An unknown
kind is a `404`.

`/api/balance` returns `{"available": false}` when instagrapi is the backend
(HikerAPI quota is meaningless there). `/api/cache` clears the persisted
response cache — after it, previously-free lookups are paid for again.

---

## 10. A script that does the whole thing

```bash
#!/usr/bin/env bash
set -euo pipefail
BASE=http://127.0.0.1:8000
TARGET=${1:?usage: $0 <instagram-username>}

# 1. Fail early if no key is configured, rather than mid-run.
curl -fsS $BASE/api/token | grep -q '"configured": true' || { echo "no HikerAPI key"; exit 1; }

# 2. Price it.
curl -fsS $BASE/api/estimate -H 'Content-Type: application/json' \
  -d "{\"target\":\"$TARGET\",\"calls\":[{\"name\":\"get_user_info\"},{\"name\":\"get_hashtags\"}]}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"cost: {d[\"total_low\"]}-{d[\"total_high\"]} requests")'

# 3. Run it, hard-capped at 200 requests, and keep the JSON.
curl -fsS $BASE/api/run -H 'Content-Type: application/json' \
  -d "{\"target\":\"$TARGET\",\"max_items\":200,
       \"calls\":[{\"name\":\"get_user_info\"},{\"name\":\"get_hashtags\",\"args\":{\"limit_posts\":20}}]}" \
  > "run-$TARGET.json"

# 4. Save it as a dossier for later comparison.
python3 - "$TARGET" <<'PY'
import json, sys, urllib.request
target = sys.argv[1]
run = json.load(open(f"run-{target}.json"))
body = json.dumps({"target": target, "api_calls_total": run["api_calls_total"],
                   "results": run["tool_calls"]}).encode()
req = urllib.request.Request("http://127.0.0.1:8000/api/dossier", body,
                             {"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
PY
```

Run it nightly against the same target, then diff the last two dossiers to see
what changed — that's the whole cost-controlled OSINT loop, unaided.

---

## Limits worth knowing

- **`max_items` is enforced server-side.** It counts real requests, not
  candidates; cache hits are free and don't count against it.
- **The request cache is per raw backend call**, keyed on method + arguments,
  and persisted in `cache/requests.sqlite3`. Two different commands needing the
  same profile object pay for it once.
- **Only `localhost` is answered** for the key and dossier endpoints. Reach the
  app at `127.0.0.1`; if you must use another hostname (a tunnel, a proxy), add
  it to `OSINTGRAM_TRUSTED_HOSTS` — see
  [the configuration reference](web-ui.md#host-allowlist).
- **Private profiles return nothing.** No tool can read them; those commands
  say so rather than returning empty.
- **Results are other people's personal data.** Handling them is your
  responsibility under GDPR and equivalents — see the disclaimer in the
  [README](../README.md#before-you-use-it).
