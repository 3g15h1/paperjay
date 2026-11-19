# Choosing an AI model

PaperJay works without AI: OCR, search, filing by hand, "coming up" dates found in the text and answers
made of the best matching passages all work with AI turned off. With AI on, it also:

- **files new documents**: title, date, company, document type and tags;
- **reads each document's key facts** (totals, due and expiry dates, policy and account numbers), which give
  instant answers, totals across documents and more reminders;
- **writes short answers** to your questions, with every number checked against the cited documents;
- **searches by meaning**, so "grass cutting" finds the lawn service invoice. Since 0.5 this uses a small
  model built into PaperJay, so it works even with the AI turned off (see below).

Everything runs on your own hardware with [Ollama](https://ollama.com/) or any server that speaks the
OpenAI API (LM Studio, llama.cpp's `llama-server`, vLLM, LocalAI). A cloud API works too, but then your
documents' text leaves your network.

## Recommended models

| Your hardware | Answer model | Speed per document* | Notes |
|---|---|---|---|
| Raspberry Pi 5 / 4-core mini PC, 8 GB | `qwen2.5:1.5b-instruct` | 1–2 min | Misreads totals more often; fine for filing |
| 4+ CPU cores, 8–16 GB | `qwen2.5:3b-instruct` (default) | 30–90 s | Tested default; good filing and answers |
| GPU with 8 GB+ | `qwen2.5:7b-instruct` or `llama3.1:8b` | a few seconds | Noticeably better answers |
| GPU with 16 GB+ | `qwen2.5:14b-instruct` | a few seconds | Best quality for long documents |

*Filing and reading one document is a single AI call; it happens in the background, one document at a
time, and pauses whenever someone is asking a question.

```sh
ollama pull qwen2.5:3b-instruct
```

## Search by meaning

**Built in (default since 0.5).** PaperJay runs `nomic-embed-text` v1.5 itself, on the CPU (ONNX Runtime,
no GPU, no AI program). The model is about 130 MB; install and update download it once into
`DATA_DIR/models` (the Docker image has it inside; `MODELS_DIR` picks another folder, and
`paperjay fetch-models` downloads it by hand). Embedding a document takes well under a second; it uses
nomic's `search_document:` / `search_query:` prefixes, which match a little better than plain text.

**Through the AI program.** Settings → Smart filing (AI) → Smart search → "Use the AI program's model" sends the text to the AI
program's embedding model instead (`nomic-embed-text` in Ollama, or any `/v1/embeddings` server), as before 0.5.

**Off.** Settings → Smart filing (AI) → Smart search → Off. Search and Ask then match words only.

Changing the choice re-indexes every document in the background.

## Reasoning models

Models that think before they answer (Qwen3 and similar, served by llama.cpp, vLLM or Ollama) spend seconds
on a thinking step for every filing. **Answer without a thinking step** (setting `ai_no_think`) asks them to
skip it: PaperJay sends `reasoning_effort: "none"` and `chat_template_kwargs: {"enable_thinking": false}` to
OpenAI-compatible servers and `think: false` to Ollama. Leave it off for servers that reject those fields.

## Reading hard pages (vision models)

OCR (Tesseract) reads printed text well, but not handwriting, and not much of a faint or crumpled photo. A
model that can see pictures can. Turn it on under **Settings → Smart filing (AI) → Read hard pages with the
AI**, and give the vision model's name:

| Model (Ollama) | Size | Notes |
|---|---|---|
| `qwen2.5vl:7b` | ~6 GB | good at handwriting and receipts; the best start with a graphics card |
| `qwen2.5vl:3b` | ~3 GB | for a CPU or a small graphics card |
| `minicpm-v` | ~5 GB | another good all-rounder |

Install it first (`ollama pull qwen2.5vl:7b`); any OpenAI-compatible server with image input works too.

- **Pages OCR couldn't read well** (the usual choice): after OCR, pages with very few real words, or mostly
  garbage, are sent to the model one at a time. **Every page** sends them all. On one document, **⋯ → Read
  with the AI** reads every page, whatever the setting.
- The model's text replaces OCR's for that page; the document then says which pages the AI read, so you can
  check names and numbers (a vision model can misread too). Filing and answers use the new text.
- On a CPU each page can take a minute or more, so it runs on the AI's own queue and never holds up new
  documents being read. At most 20 pages per document.
- The searchable PDF keeps OCR's text layer, so search words are only marked on the page where OCR found them.

## Settings

In **Settings → Smart filing (AI)** (or with `PUT /api/settings`): provider (`off`, `ollama`, `openai`), server address,
answer model, API key (OpenAI-compatible servers only), smart search (`embed_source`: `builtin`, `ai` or `off`,
with `embed_model` for `ai`). **Check it works** checks the built-in search model, that the server answers,
that the models are installed, and how fast the model replies.

For headless installs the same can be given once as environment variables: `AI_PROVIDER`, `AI_URL`,
`AI_MODEL`, `AI_API_KEY`, `EMBED_SOURCE`, `EMBED_MODEL`, `AI_THREADS`. Values saved in the app win.

## CPU-only tips

- **Threads:** Ollama sizes its threads from the machine's physical cores. In a container limited to fewer
  cores that can make it about 50 times slower. Set **AI threads** to the number of cores Ollama may use.
- **One model call at a time** is all a CPU does well. PaperJay sends one request at a time and lets
  questions go before background work.
- **Keep the model loaded** (`ai_keep_alive`, default 24 h), so the first question doesn't wait for it to load.

## What the AI is and isn't trusted with

- Tags are chosen only from the tags you already have; a new tag is a suggestion you approve.
- Companies, amounts, dates and ID numbers must appear in the document's own text, or they're dropped.
- Anything a person has set (title, date, company, type, tags) is never changed by the AI.
- Documents where something looks uncertain (a new company, no company found, a suggested tag, very little
  text) go to **Needs review**.
- Totals are added up in code, never by the model, and every item is listed.
