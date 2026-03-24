# Email Assistant Minimal Prototype

A minimal runnable Email Processing Agent prototype in Python.

## What it does

- Reads email data from local JSON files
- Supports:
  - single email input
  - email thread input (recommended)
- Sorts messages by time and builds a model-friendly thread text
- Calls OpenAI model to generate structured output
- Outputs result:
  - printed in terminal
  - saved as local JSON (`data/output.json` by default)

## Structured output schema

```json
{
  "summary": "string",
  "key_points": ["string"],
  "action_items": [
    {
      "task": "string",
      "owner": "string or unknown",
      "deadline": "string or unknown"
    }
  ],
  "open_questions": ["string"]
}
```

If fields are unclear, the system keeps stable defaults (`unknown`, empty string, or empty list).

## Quick start

1) Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2) Configure API key:

- Copy `.env.example` to `.env`
- Set `OPENAI_API_KEY=...`

3) Run with default sample:

```bash
python main.py --input data/input.json --output data/output.json --model gpt-4o-mini
```

4) Dry run (no model call):

```bash
python main.py --input data/input.json --dry-run
```

## Input formats

### A) Single email

```json
{
  "subject": "string",
  "sender": "string",
  "recipients": ["string"],
  "timestamp": "ISO-8601 string",
  "body": "string"
}
```

### B) Thread (recommended)

```json
{
  "thread_id": "string",
  "subject": "string",
  "messages": [
    {
      "sender": "string",
      "recipients": ["string"],
      "timestamp": "ISO-8601 string",
      "body": "string"
    }
  ]
}
```

## Project structure

- `main.py`: CLI entry point (read -> preprocess -> call LLM -> parse -> output)
- `email_assistant/input_loader.py`: input file reading and schema validation
- `email_assistant/preprocessor.py`: thread sorting and formatting
- `email_assistant/llm_client.py`: prompt construction and OpenAI call
- `email_assistant/models.py`: input/output schemas and output normalization
- `data/`: default input/output
- `examples/`: multiple test samples
- `streamlit_app.py`: local UI with Microsoft Entra device-code login and Graph mail read
- `email_assistant/msal_device.py`: MSAL public client + device flow helpers
- `email_assistant/graph_mail.py`: Microsoft Graph inbox/list/detail helpers
- `email_assistant/summary_pipeline.py`: shared LLM analysis (used by CLI and Streamlit)

## Streamlit + Microsoft Graph

Prerequisites in **Microsoft Entra** (app registration):

- Application type supports **public client** (“Allow public client flows” = **Yes**).
- **Delegated** API permissions on Microsoft Graph: **User.Read**, **Mail.Read** (admin consent if required by tenant).

Environment variables (see `.env.example`):

- `AZURE_CLIENT_ID` — Application (client) ID  
- `AZURE_TENANT_ID` — Directory (tenant) ID  
- Same values are accepted as `MICROSOFT_CLIENT_ID` / `MICROSOFT_TENANT_ID`.

Run the app:

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Sign in via **device code flow** in the sidebar. After login, the **access token** is stored in Streamlit **session state** (not persisted to disk). Fetch inbox, pick a message, then **Analyze full body** to run the same summarization pipeline as the CLI.

### Troubleshooting login (`AADSTS700016`, directory `''`)

- **`AZURE_TENANT_ID` missing or empty** — Azure reports the app was not found **in the directory `''`**. In **Entra ID → Overview**, copy **Directory (tenant) ID** (a GUID different from Application client ID) into `.env` as `AZURE_TENANT_ID=...`.
- **Windows environment variables** — If `AZURE_TENANT_ID` is set **globally to empty**, it used to block values from `.env`. This repo loads project `.env` with **override** so local `.env` wins; still remove empty duplicate vars if problems persist.
- **Client vs tenant** — `AZURE_CLIENT_ID` = Application (client) ID; `AZURE_TENANT_ID` = Directory (tenant) ID. They must **not** be the same value.
- **Wrong GUID in Client ID** — Use **Application (client) ID**, not **Object ID**, from the app registration **Overview**.
- **App not in that tenant** — The registration must live in the same directory as `AZURE_TENANT_ID`. If the app is multi-tenant, you can try `AZURE_AUTHORITY=https://login.microsoftonline.com/organizations` (and matching “Accounts in any org directory” setting).
- **Corporate proxy / metadata issues** — Try `AZURE_MSAL_DISABLE_INSTANCE_DISCOVERY=true` in `.env`, restart Streamlit, sign in again.
- **UI check** — After clicking **Sign in**, open **MSAL 元数据（排查登录）**: `msal_tenant` should be your tenant GUID (not `common`). The device-flow URL should contain that tenant segment.

### Troubleshooting Graph (`401` after login)

HTTP **401** on `https://graph.microsoft.com/...` usually means the **access token is not valid for that Graph host** (national cloud mismatch), not that “the session expired”.

- Compare token **`iss`** (in sidebar **访问令牌摘要**) with your tenant:
  - If `iss` contains `login.partner.microsoftonline.cn`, set  
    `GRAPH_API_ROOT=https://microsoftgraph.chinacloudapi.cn/v1.0` in `.env` and restart Streamlit (see [Microsoft Graph national clouds](https://learn.microsoft.com/en-us/graph/deployments)).
  - US Government: `GRAPH_API_ROOT=https://graph.microsoft.us/v1.0` and matching Entra authority host.
- **`aud`** should be Microsoft Graph (`https://graph.microsoft.com` or Graph’s app id). If it is not, permissions/scopes or login cloud need to be fixed in Entra.
- After changing `GRAPH_API_ROOT`, try **Refresh inbox** again; only **Sign out** and sign in again if you still get 401.

- **Personal Microsoft account (Outlook.com / MSA)** — If `GET /me` succeeds but **`/me/mailFolders/inbox/messages` returns 401**, use **`GET /me/messages`** to list mail (this app does). Tokens with `Mail.Read` can still hit that folder path quirk on some consumer mailboxes.

- **`/me` = 200 but `/me/messages` = 401 with the same token (common with MSA)** — In Entra, set **Supported account types** to **“Accounts in any organizational directory and personal Microsoft accounts”** (multitenant + personal). In `.env`, set **`AZURE_AUTHORITY=https://login.microsoftonline.com/common`** (do **not** use a single-tenant authority only for personal Outlook mail). Restart the app, **Sign out**, then sign in again so a new token is issued against `/common`. Optional: keep `AZURE_TENANT_ID` for reference; it is not used in the authority URL when `AZURE_AUTHORITY` is set. See [supported account types](https://learn.microsoft.com/en-us/security/zero-trust/develop/identity-supported-account-types).

## Test samples

Included in `examples/`:

- clear deadline
- multi-turn thread
- meeting scheduling
- FYI only
- action item with unclear owner
- long/ambiguous request thread
