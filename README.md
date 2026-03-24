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

## Test samples

Included in `examples/`:

- clear deadline
- multi-turn thread
- meeting scheduling
- FYI only
- action item with unclear owner
- long/ambiguous request thread
