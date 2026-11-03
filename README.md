# Bakery Bot 🧁: AI Order Assistant for Bakeries

An AI agent that handles customer conversations for a bakery, the way an employee would on WhatsApp: it answers questions about the menu, prices and delivery, takes complete cake orders, logs every order to a spreadsheet and hands the chat to a human when it should.

This is a **working demo** built on the same architecture as [clinica-bot](https://github.com/andresnv22/clinica-bot), adapted to a fictional bakery. It runs in a web chat that looks like WhatsApp; it is **not yet connected to the real WhatsApp API**.

## What it does

- Answers menu, price, flavor and delivery questions in natural Dominican Spanish (the tone is one line of the prompt, easy to change).
- Takes full orders: product, size, flavor, quantity, dedication text, date and time, home delivery (zone + address) or pickup, and the customer's name. It asks only for what's missing and **confirms a summary with the total before registering**.
- **Logs every order and cancellation to a spreadsheet** (`pedidos.csv` by default, or Google Sheets).
- Cancels orders, with a rule: not within 24 hours of delivery (already in production).
- **Hands off to a human** for custom designs (weddings), allergies, complaints, big orders. After a handoff the bot stays quiet until the team resolves the case.

## Why it's reliable

The AI never touches the database directly. It can only call seven tools, and **all business rules live in code, not in the prompt**:

- Prices, flavors, sizes and delivery zones come from the database. The AI can't invent them, and invalid ones are rejected with an error it can read and correct.
- **Daily capacity per product** (e.g. 4 cakes a day). The check and the insert run in one locked database transaction: tested with 10 simultaneous orders for 4 spots, exactly 4 are accepted.
- **Lead time per product** (cakes 48 h, cupcakes 24 h), delivery hours (Mon–Sat, 10:00–18:00), no Sundays, holidays, past dates.
- **Retries never duplicate an order** (same customer, same order, same slot returns the existing one).
- **The AI never handles the customer's phone number**: the system injects it, so a customer can only see and cancel their own orders, even under prompt injection.
- **A spreadsheet failure never loses an order.** The order is saved in the database first; the spreadsheet write is queued in the same transaction and retried automatically (or with `POST /sincronizar`).
- Customers never see technical errors: if the AI provider fails, they get a friendly message and the conversation rolls back to a clean state.

It works with **two AI providers** (Google Gemini or Anthropic Claude), chosen in one line of config, with automatic fallback.

## How it works

```
Customer ──> FastAPI (/chat) ──> Agent (Gemini or Claude) ──> Tools ──> SQLite
                                      ▲                          │         └──> pedidos.csv / Google Sheets
                                      └──────── result ──────────┘
```

| File | What it does |
|---|---|
| `app/tools.py` | The 7 tools the agent can use, plus the business rules |
| `app/agent.py` | The agent loop (one version per provider) and the assistant's personality |
| `app/db.py` | Database and the **business data** (menu, zones, hours): change it per client |
| `app/hoja.py` | Spreadsheet logging (CSV or Google Sheets) with a retry queue |
| `app/main.py` | Web server and error handling |
| `static/index.html` | WhatsApp-style demo with the live orders panel |

**Stack:** Python, FastAPI, SQLite, Google Gemini API, Anthropic Claude API (tool use / function calling).

## Run it locally

1. Get an API key: [Gemini](https://aistudio.google.com) (has a free tier) or [Claude](https://console.anthropic.com).
2. Copy `.env.example` to `.env`, paste your key and choose the provider with `PROVEEDOR=gemini` or `PROVEEDOR=claude`.
3. Run it (requires [uv](https://docs.astral.sh/uv/)):

```bash
uv run uvicorn app.main:app --reload
```

4. Open http://localhost:8000

## Tests

```bash
uv run pytest                          # 80 tests of the business rules, no AI calls, about 1 second
uv run python -m scripts.prueba_real   # tricky real conversations against the AI (uses API quota)
```

## Google Sheets (optional)

Orders go to `pedidos.csv` by default. To write to Google Sheets instead, create a Google service account, share the sheet with its email, then set `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_FILE` in `.env` and install the extra: `uv sync --extra sheets`.

> The Sheets connector is written but has **only been tested with a fake destination**, not against a live Google account. The CSV path is fully tested.

## Before going to production

- Connect to the real WhatsApp API (Meta Cloud API, Twilio or Evolution API). The agent doesn't change; only the entry point does.
- Use a **paid AI API plan**: free tiers get overloaded and run out of quota.
- Protect `/pedidos`, `/transferencias` and `/sincronizar` with a password.
- Store conversations in the database instead of memory.
- Load the client's real menu, prices, zones and holidays into `app/db.py`.
- Payment is out of scope: the bot tells customers that a person coordinates it.

---

The code comments and the assistant itself are in Spanish, since it was built for businesses in Latin America.
