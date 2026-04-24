# Healthclaw API

Healthclaw is a wellness-only conversational companion service focused on continuity,
time-aware behavior support, calm responses, voice/text input, and bounded proactive
nudges. This repository is the production-shaped MVP scaffold.

## What is included

- FastAPI REST and WebSocket surfaces.
- LangGraph-based agent workflow with explicit nodes.
- PostgreSQL-ready SQLAlchemy models and Alembic migration.
- Memory layers for profile, episode, and policy memories with revision history.
- Forever-chat continuity with open loops, heartbeat wakeups, user-visible memory
  governance, and governed soul/personality preferences.
- Time context, quiet-hours logic, reminders, safety boundaries, and audit APIs.
- Telegram webhook adapter with text and voice-note ingestion shell.
- Open Wearables future integration interface.
- OpenTelemetry/LangSmith-aware observability configuration.
- Docker, docker-compose, Kubernetes manifests, and GitHub Actions CI.

## Local development

```bash
cp .env.example .env
uv sync --extra dev
uv run pytest
uv run uvicorn healthclaw.main:create_app --factory --reload
```

The app defaults to PostgreSQL. For quick local tests, use:

```bash
DATABASE_URL=sqlite+aiosqlite:///./healthclaw.db AUTO_CREATE_DB=true uv run uvicorn healthclaw.main:create_app --factory --reload
```

## Telegram pilot

Healthclaw uses OpenRouter for pilot LLM and voice-note transcription. Put the key in
your local or server `.env` as `OPENROUTER_API_KEY`; do not commit it. If a key has
been pasted into chat or logs, rotate it before using the deployment.

Cost-efficient defaults:

- `OPENROUTER_CHAT_MODEL=moonshotai/kimi-k2.6`
- `OPENROUTER_CHAT_FALLBACK_MODELS=minimax/minimax-m2.7,openai/gpt-5.4-mini`
- `OPENROUTER_CHAT_MAX_TOKENS=700`
- `OPENROUTER_CHAT_TEMPERATURE=0.75`
- `OPENROUTER_TRANSCRIBE_MODEL=mistralai/voxtral-small-24b-2507`

Current OpenRouter facts used for the companion defaults:

- Kimi K2.6: 262,144 context, `$0.60/M` input, `$2.80/M` output.
- MiniMax M2.7: 196,608 context, `$0.30/M` input, `$1.20/M` output.

Run the API and reminder worker together:

```bash
docker compose up --build
```

Register the Telegram webhook after the API is reachable from the public internet:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=$PUBLIC_BASE_URL/webhooks/telegram" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

## API highlights

- `POST /v1/conversations/{user_id}/messages`
- `GET /v1/conversations/{user_id}/stream-token`
- `GET /v1/users/{user_id}/memory`
- `PATCH /v1/users/{user_id}/memory/{memory_id}`
- `DELETE /v1/users/{user_id}/memory/{memory_id}`
- `PATCH /v1/users/{user_id}/preferences`
- `GET/PATCH /v1/users/{user_id}/soul-preferences`
- `POST /v1/users/{user_id}/pause-proactivity`
- `POST /v1/users/{user_id}/resume-proactivity`
- `GET /v1/users/{user_id}/timeline`
- `POST /v1/reminders`
- `GET /v1/audit/memory-events`
- `WS /v1/ws/conversations/{user_id}`
- `POST /webhooks/telegram`

## Wellness boundary

Healthclaw v1 is not clinical care, diagnosis, treatment, emergency response, or a
medical device. The code has explicit safety boundaries and escalation copy for crisis
or medical-risk language, but production launch still requires legal, privacy, and
clinical review.
