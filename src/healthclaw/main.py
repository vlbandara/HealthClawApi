from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from healthclaw.api.router import api_router
from healthclaw.core.config import get_settings
from healthclaw.core.logging import configure_logging
from healthclaw.core.observability import configure_observability
from healthclaw.db.models import ConversationThread, OpenLoop, Ritual, User, new_id
from healthclaw.db.session import SessionLocal, init_models


async def _dev_chat() -> HTMLResponse:
    settings = get_settings()
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Healthclaw Dev Chat</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17201b;
      --muted: #66736d;
      --line: #d8e1dc;
      --panel: #f7faf8;
      --accent: #176b53;
      --accent-dark: #0f4f3c;
      --user: #e8f3ee;
      --assistant: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #edf3ef;
    }}
    main {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 100vh;
      max-width: 920px;
      margin: 0 auto;
      background: var(--panel);
      border-left: 1px solid var(--line);
      border-right: 1px solid var(--line);
    }}
    header {{
      padding: 18px 20px 14px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{ margin: 0; font-size: 20px; letter-spacing: 0; }}
    .meta {{ margin-top: 4px; color: var(--muted); font-size: 13px; }}
    #log {{
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .msg {{
      width: min(78%, 680px);
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      white-space: pre-wrap;
    }}
    .user {{ align-self: flex-end; background: var(--user); }}
    .assistant {{ align-self: flex-start; background: var(--assistant); }}
    .system {{
      align-self: center;
      color: var(--muted);
      font-size: 13px;
      padding: 6px 10px;
    }}
    form {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      padding: 14px;
      border-top: 1px solid var(--line);
      background: #ffffff;
    }}
    textarea {{
      min-height: 54px;
      max-height: 160px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      font: inherit;
    }}
    button {{
      border: 0;
      border-radius: 8px;
      padding: 0 18px;
      font: inherit;
      font-weight: 650;
      color: #ffffff;
      background: var(--accent);
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent-dark); }}
    button:disabled {{ opacity: .55; cursor: not-allowed; }}
    @media (max-width: 640px) {{
      main {{ border: 0; }}
      .msg {{ width: 92%; }}
      form {{ grid-template-columns: 1fr; }}
      button {{ height: 46px; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>Healthclaw Dev Chat</h1>
    <div class="meta">Local forever-chat test user: <code id="user-id"></code></div>
  </header>
  <section id="log" aria-live="polite"></section>
  <form id="form">
    <textarea
      id="input"
      placeholder="Try: My goal is sleep by 10pm. Tonight I will prepare my room."
      autofocus></textarea>
    <button id="send" type="submit">Send</button>
  </form>
</main>
<script>
  const apiKey = {settings.api_key!r};
  const userId = localStorage.getItem("healthclaw-dev-user") || "dev-" + crypto.randomUUID();
  localStorage.setItem("healthclaw-dev-user", userId);
  document.getElementById("user-id").textContent = userId;
  const log = document.getElementById("log");
  const form = document.getElementById("form");
  const input = document.getElementById("input");
  const send = document.getElementById("send");

  function add(role, text) {{
    const node = document.createElement("div");
    node.className = "msg " + role;
    node.textContent = text;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
  }}

  async function loadHistory() {{
    try {{
      const response = await fetch(`/v1/users/${{encodeURIComponent(userId)}}/timeline`, {{
        headers: {{"X-API-Key": apiKey}}
      }});
      if (!response.ok) throw new Error(response.statusText);
      const body = await response.json();
      for (const message of body.recent_messages || []) {{
        add(message.role, message.content);
      }}
      add(
        "system",
        "Commands work here: /memory, /settings, /pause, /resume."
      );
    }} catch (error) {{
      add("system", "Could not load recent history: " + error.message);
    }}
  }}

  loadHistory();

  form.addEventListener("submit", async (event) => {{
    event.preventDefault();
    const content = input.value.trim();
    if (!content) return;
    input.value = "";
    add("user", content);
    send.disabled = true;
    const pending = document.createElement("div");
    pending.className = "msg assistant";
    pending.textContent = "Thinking...";
    log.appendChild(pending);
    log.scrollTop = log.scrollHeight;
    try {{
      const response = await fetch(`/v1/conversations/${{encodeURIComponent(userId)}}/messages`, {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "X-API-Key": apiKey
        }},
        body: JSON.stringify({{
          content,
          channel: "web",
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
        }})
      }});
      const text = await response.text();
      let body = {{}};
      try {{
        body = text ? JSON.parse(text) : {{}};
      }} catch (_error) {{
        body = {{ detail: text || response.statusText }};
      }}
      if (!response.ok) throw new Error(body.detail || response.statusText);
      pending.textContent = body.response;
    }} catch (error) {{
      pending.textContent = "Request failed: " + error.message;
    }} finally {{
      send.disabled = false;
      input.focus();
    }}
  }});
</script>
</body>
</html>"""
    return HTMLResponse(html)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.auto_create_db:
            await init_models()
        yield

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Wellness-only conversational companion API.",
        lifespan=lifespan,
    )
    configure_observability(app, settings)
    app.include_router(api_router)
    if not settings.is_production:
        app.add_api_route("/dev/chat", _dev_chat, methods=["GET"], include_in_schema=False)

        @app.post("/dev/users/{user_id}/consolidate", include_in_schema=False)
        async def _dev_consolidate(user_id: str) -> dict:
            from healthclaw.memory.consolidator import ConsolidatorService
            from healthclaw.memory.service import MemoryService

            async with SessionLocal() as session:
                user = await session.get(User, user_id)
                if user is None:
                    raise HTTPException(status_code=404, detail="User not found")
                service = ConsolidatorService(session, settings, MemoryService(session))
                count = await service.run_for_user(user_id)
                await session.commit()
                return {"episodes_created": count}

        @app.post("/dev/users/{user_id}/dream", include_in_schema=False)
        async def _dev_dream(user_id: str) -> dict:
            from healthclaw.memory.dream import DreamService
            from healthclaw.memory.service import MemoryService

            async with SessionLocal() as session:
                user = await session.get(User, user_id)
                if user is None:
                    raise HTTPException(status_code=404, detail="User not found")
                service = DreamService(session, settings, MemoryService(session))
                outcome = await service.run_for_user(user_id)
                await session.commit()
                return outcome

        @app.post("/dev/users/{user_id}/backdate", include_in_schema=False)
        async def _dev_backdate(user_id: str, days: int = Body(default=8, embed=True)) -> dict:
            async with SessionLocal() as session:
                user = await session.get(User, user_id)
                if user is None:
                    raise HTTPException(status_code=404, detail="User not found")
                at = datetime.now(UTC) - timedelta(days=days)
                user.last_active_at = at
                result = await session.execute(
                    select(ConversationThread)
                    .where(ConversationThread.user_id == user_id)
                    .order_by(ConversationThread.created_at.desc())
                    .limit(1)
                )
                thread = result.scalar_one_or_none()
                if thread is not None:
                    thread.last_message_at = at
                await session.commit()
                return {"last_active_at": at.isoformat()}

        @app.post("/dev/users/{user_id}/open-loop", include_in_schema=False)
        async def _dev_open_loop(
            user_id: str,
            title: str = Body(default="test open loop", embed=True),
            age_hours: int = Body(default=19, embed=True),
        ) -> dict:
            async with SessionLocal() as session:
                user = await session.get(User, user_id)
                if user is None:
                    raise HTTPException(status_code=404, detail="User not found")
                result = await session.execute(
                    select(ConversationThread)
                    .where(ConversationThread.user_id == user_id)
                    .order_by(ConversationThread.created_at.desc())
                    .limit(1)
                )
                thread = result.scalar_one_or_none()
                created_at = datetime.now(UTC) - timedelta(hours=age_hours)
                loop = OpenLoop(
                    id=new_id(),
                    user_id=user_id,
                    thread_id=thread.id if thread else None,
                    title=title[:240],
                    kind="commitment",
                    status="open",
                    due_after=created_at,
                    created_at=created_at,
                    metadata_={"source": "dev_helper"},
                )
                session.add(loop)
                await session.commit()
                return {"open_loop_id": loop.id, "created_at": created_at.isoformat()}

        @app.post("/dev/users/{user_id}/rituals", include_in_schema=False)
        async def _dev_rituals(user_id: str, enabled: bool = Body(embed=True)) -> dict:
            async with SessionLocal() as session:
                user = await session.get(User, user_id)
                if user is None:
                    raise HTTPException(status_code=404, detail="User not found")
                result = await session.execute(select(Ritual).where(Ritual.user_id == user_id))
                rituals = list(result.scalars())
                for ritual in rituals:
                    ritual.enabled = enabled
                await session.commit()
                return {"updated": len(rituals), "enabled": enabled}
    return app


app = create_app()
