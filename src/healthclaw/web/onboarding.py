from fastapi.responses import HTMLResponse

BASE_CSS = """
:root {
  color-scheme: light;
  --ink: #17201b;
  --muted: #66736d;
  --line: #d8e1dc;
  --panel: #f7faf8;
  --paper: #ffffff;
  --accent: #176b53;
  --accent-dark: #0f4f3c;
  --warn: #7a4a16;
  --bad: #8a1f1f;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink);
  background: #edf3ef;
}
a { color: var(--accent-dark); }
.shell {
  min-height: 100vh;
  display: grid;
  grid-template-rows: auto 1fr;
}
.topbar {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 clamp(18px, 4vw, 56px);
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, .9);
}
.brand {
  font-weight: 760;
  font-size: 18px;
}
.navlink {
  font-size: 14px;
  font-weight: 650;
  text-decoration: none;
}
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(320px, .9fr);
  gap: clamp(28px, 5vw, 72px);
  align-items: center;
  padding: clamp(34px, 8vh, 84px) clamp(18px, 4vw, 56px) 40px;
}
.hero-copy {
  max-width: 720px;
}
.eyebrow {
  margin: 0 0 12px;
  color: var(--accent-dark);
  font-weight: 720;
  font-size: 14px;
}
h1 {
  margin: 0;
  font-size: clamp(42px, 7vw, 82px);
  line-height: .98;
  letter-spacing: 0;
}
.lead {
  margin: 22px 0 0;
  max-width: 620px;
  color: #43524b;
  font-size: clamp(18px, 2vw, 22px);
}
.proof {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-top: 34px;
}
.proof div, .panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
}
.proof div {
  min-height: 92px;
  padding: 16px;
}
.proof strong {
  display: block;
  margin-bottom: 6px;
}
.proof span {
  color: var(--muted);
  font-size: 14px;
}
.panel {
  padding: clamp(20px, 3vw, 28px);
  box-shadow: 0 20px 60px rgba(23, 32, 27, .08);
}
.panel h2 {
  margin: 0 0 8px;
  font-size: 24px;
}
.panel p {
  margin: 0 0 18px;
  color: var(--muted);
}
label {
  display: block;
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 650;
}
input {
  width: 100%;
  min-height: 48px;
  padding: 12px 13px;
  border: 1px solid var(--line);
  border-radius: 8px;
  font: inherit;
  background: #fff;
}
button, .button {
  display: inline-flex;
  min-height: 48px;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 8px;
  padding: 0 18px;
  font: inherit;
  font-weight: 720;
  color: #fff;
  background: var(--accent);
  cursor: pointer;
  text-decoration: none;
}
button:hover, .button:hover { background: var(--accent-dark); }
button:disabled { opacity: .55; cursor: not-allowed; }
.secondary {
  color: var(--ink);
  background: #e6eee9;
}
.secondary:hover { background: #d8e5df; }
.stack {
  display: grid;
  gap: 14px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 18px;
}
.status {
  min-height: 22px;
  color: var(--muted);
  font-size: 14px;
}
.status.error { color: var(--bad); }
.status.warn { color: var(--warn); }
.screen {
  width: min(760px, calc(100vw - 36px));
  margin: clamp(30px, 8vh, 72px) auto;
}
.account {
  display: grid;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  color: var(--muted);
}
.account strong {
  color: var(--ink);
}
.note {
  margin-top: 18px;
  color: var(--muted);
  font-size: 14px;
}
@media (max-width: 820px) {
  .hero { grid-template-columns: 1fr; padding-top: 34px; }
  .proof { grid-template-columns: 1fr; }
  .topbar { height: 58px; }
}
"""


def _page(title: str, body: str, script: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>{BASE_CSS}</style>
</head>
<body>
{body}
{script}
</body>
</html>"""
    )


async def landing_page() -> HTMLResponse:
    body = """
<div class="shell">
  <header class="topbar">
    <div class="brand">Healthclaw</div>
    <a class="navlink" href="/onboarding">Owner console</a>
  </header>
  <main class="hero">
    <section class="hero-copy">
      <p class="eyebrow">Wellness companion for Telegram</p>
      <h1>Healthclaw</h1>
      <p class="lead">
        A continuity-first companion that remembers goals, follows up gently, and keeps the
        wellness conversation where people already reply.
      </p>
      <div class="proof" aria-label="Product highlights">
        <div>
          <strong>Telegram native</strong>
          <span>Owners connect a bot token and users continue in chat.</span>
        </div>
        <div>
          <strong>Continuity aware</strong>
          <span>Memory, reminders, quiet hours, and bounded proactive nudges.</span>
        </div>
        <div>
          <strong>Owner scoped</strong>
          <span>One authenticated account controls one Telegram bot.</span>
        </div>
      </div>
    </section>
    <section class="panel" id="signin">
      <h2>Start hosted onboarding</h2>
      <p>Enter your owner email. In local Compose, the sign-in link appears in the API logs.</p>
      <form id="magic-form" class="stack">
        <div>
          <label for="email">Email</label>
          <input id="email" name="email" type="email" autocomplete="email" required />
        </div>
        <button id="send-link" type="submit">Send sign-in link</button>
        <div id="status" class="status" role="status"></div>
      </form>
    </section>
  </main>
</div>
"""
    script = """
<script>
const form = document.getElementById("magic-form");
const statusNode = document.getElementById("status");
const button = document.getElementById("send-link");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusNode.className = "status";
  statusNode.textContent = "Sending...";
  button.disabled = true;
  try {
    const response = await fetch("/v1/auth/magic-link", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({email: form.email.value.trim()})
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || response.statusText);
    statusNode.textContent = body.message || "Check the API logs for your sign-in link.";
  } catch (error) {
    statusNode.className = "status error";
    statusNode.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
</script>
"""
    return _page("Healthclaw", body, script)


async def auth_callback_page() -> HTMLResponse:
    body = """
<main class="screen">
  <section class="panel">
    <h1 style="font-size:32px;line-height:1.1">Signing you in</h1>
    <p id="message">Checking your sign-in link...</p>
    <div class="actions">
      <a class="button secondary" href="/">Back to Healthclaw</a>
    </div>
  </section>
</main>
"""
    script = """
<script>
const message = document.getElementById("message");
const token = new URLSearchParams(window.location.search).get("token");

async function consume() {
  if (!token) {
    message.textContent = "This sign-in link is missing a token.";
    return;
  }
  try {
    const response = await fetch("/v1/auth/session", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token})
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || response.statusText);
    localStorage.setItem("healthclaw_owner_session", body.access_token);
    localStorage.setItem("healthclaw_owner_email", body.email);
    window.location.replace("/onboarding");
  } catch (error) {
    message.textContent = "Sign-in failed: " + error.message;
  }
}

consume();
</script>
"""
    return _page("Healthclaw sign-in", body, script)


async def onboarding_page() -> HTMLResponse:
    body = """
<div class="shell">
  <header class="topbar">
    <a class="brand" href="/" style="color:inherit;text-decoration:none">Healthclaw</a>
    <button class="secondary" id="sign-out" type="button">Sign out</button>
  </header>
  <main class="screen">
    <section class="panel">
      <h1 style="font-size:34px;line-height:1.1">Connect your Telegram bot</h1>
      <p>
        Paste the token from BotFather. Healthclaw will validate it, register the webhook,
        and send you to Telegram.
      </p>
      <div id="account" class="account" hidden></div>
      <form id="bot-form" class="stack" style="margin-top:18px">
        <div>
          <label for="bot-token">Telegram bot token</label>
          <input
            id="bot-token"
            name="bot_token"
            type="password"
            autocomplete="off"
            placeholder="123456789:AA..."
            required
          />
        </div>
        <button id="connect" type="submit">Connect bot</button>
        <div id="status" class="status" role="status"></div>
      </form>
      <div id="connected-actions" class="actions" hidden>
        <a id="telegram-link" class="button" href="#" rel="noopener">Open Telegram</a>
      </div>
      <p class="note">
        For local Docker Compose, set PUBLIC_BASE_URL to your HTTPS tunnel before connecting.
      </p>
    </section>
  </main>
</div>
"""
    script = """
<script>
const sessionKey = "healthclaw_owner_session";
const token = localStorage.getItem(sessionKey);
const accountNode = document.getElementById("account");
const form = document.getElementById("bot-form");
const statusNode = document.getElementById("status");
const connectButton = document.getElementById("connect");
const connectedActions = document.getElementById("connected-actions");
const telegramLink = document.getElementById("telegram-link");

function resetSessionAndRedirectToSignIn() {
  localStorage.removeItem(sessionKey);
  localStorage.removeItem("healthclaw_owner_email");
  window.location.replace("/#signin");
}

function authHeaders() {
  return {"Authorization": "Bearer " + localStorage.getItem(sessionKey)};
}

function renderAccount(account) {
  accountNode.hidden = false;
  const bot = account.bot_username ? "@" + account.bot_username : "Not connected";
  accountNode.innerHTML = "<div><strong>" + account.email + "</strong></div>"
    + "<div>Bot: " + bot + "</div>"
    + "<div>Plan: " + account.plan + " · " + account.monthly_message_count
    + "/" + account.monthly_message_limit + " messages this month</div>";
  if (account.bot_url) showConnected(account.bot_url);
}

function showConnected(url) {
  form.hidden = true;
  connectedActions.hidden = false;
  telegramLink.href = url;
  statusNode.className = "status";
  statusNode.textContent = "Bot connected. Opening Telegram...";
  window.setTimeout(() => window.location.assign(url), 700);
}

async function loadMe() {
  if (!token) {
    window.location.replace("/#signin");
    return;
  }
  try {
    const response = await fetch("/v1/me", {headers: authHeaders()});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || response.statusText);
    renderAccount(body);
  } catch (error) {
    resetSessionAndRedirectToSignIn();
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusNode.className = "status";
  statusNode.textContent = "Connecting...";
  connectButton.disabled = true;
  try {
    const response = await fetch("/v1/me/bot-token", {
      method: "POST",
      headers: {...authHeaders(), "Content-Type": "application/json"},
      body: JSON.stringify({bot_token: form.bot_token.value.trim()})
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) {
      resetSessionAndRedirectToSignIn();
      return;
    }
    if (!response.ok) throw new Error(body.detail || response.statusText);
    if (body.bot_url) {
      showConnected(body.bot_url);
    } else {
      statusNode.className = "status warn";
      statusNode.textContent = "Bot connected, but Telegram did not return a username.";
      await loadMe();
    }
  } catch (error) {
    statusNode.className = "status error";
    statusNode.textContent = error.message;
  } finally {
    connectButton.disabled = false;
  }
});

document.getElementById("sign-out").addEventListener("click", () => {
  localStorage.removeItem(sessionKey);
  localStorage.removeItem("healthclaw_owner_email");
  window.location.replace("/");
});

loadMe();
</script>
"""
    return _page("Healthclaw onboarding", body, script)
