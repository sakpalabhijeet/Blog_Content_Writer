# Content Agents — local-LLM blog & YouTube script writer with Gmail approvals

Two LangGraph agents that run fully on a local **Ollama** model. Every decision goes through email.

| Agent | Schedule | What it does |
|---|---|---|
| **Blog** (`start-blog`) | weekly | Researches the last 7 days of AI news for developers (HF, OpenAI, Google, GitHub, Import AI, Latent Space, Simon Willison, TLDR AI, HN, GitHub new repos, r/LocalLLaMA, DuckDuckGo). It emails 3 topic ideas. After approval it writes a Medium post (≥5 sections, ≥2500 words) with images. The draft is emailed for approval, and once approved a polished final version is emailed (title, subtitle, TL;DR, tags, SEO, sources). |
| **YouTube** (`start-youtube`) | daily | Researches curious stories: mysteries, science/tech, and money saving for Indian families. It emails 3 ideas. After approval it writes a high-retention script (hook → beats with open loops → twist → ending + CTA) in **Hindi** (or English). The script is emailed for approval, then a final package is emailed (script, title options, thumbnail text, description, tags, voice-over .txt). |

Rejections loop back. **REVISE** edits the current draft using the reviewer's feedback, and **REGENERATE** starts over. Each loop is capped at `MAX_RETRIES` (3).

## How the approval loop works
```
research ─► email topics ─► (paused, state saved in SQLite) ─► poll finds reply ─► write draft ─► email draft ─► (paused) ─► ...
```
- Each review email's subject carries a token like `[CA-blog-20260924-0800-ab12#2]`. Just hit **Reply**.
- `poll` runs every 10 minutes. It only accepts replies **from `APPROVER_EMAIL`** that carry the **current** token. Replies to older emails are ignored.

| Reply to… | You can write |
|---|---|
| Topics | `1` / `2` / `3` (optionally add notes after the number), `REGENERATE: <what you want>`, `STOP` |
| Blog topic extras | `IMAGES: AUTO` (default), `IMAGES: MANUAL` (attach your own images, or get "suggested image" markers), `IMAGES: NONE` |
| YouTube topic extras | `LANG: HINDI` (default) or `LANG: ENGLISH` |
| Draft | `APPROVE`, `REVISE: <what to change>`, `REGENERATE: <what you want>`, `STOP` |

A bare `REJECT` gets a follow-up email asking *revise or regenerate?* and doesn't use up a retry. Free-text replies are classified by the fast model.

## Deploy with Docker (e.g. on a GPU laptop)
Docker runs two containers:
- `ollama` holds the LLM. It uses the GPU when you add the GPU override file.
- `agents` runs `python -m content_agents serve`, a built-in scheduler that replaces Windows Task Scheduler. It starts the blog run on `BLOG_SCHEDULE`, the YouTube run on `YT_SCHEDULE`, and checks email every `POLL_MINUTES`.

A one-shot `ollama-pull` step downloads the models named in `.env` on the first start.

1. On the GPU laptop, install **Docker Desktop** (WSL 2 backend) and a recent **NVIDIA driver**. Check that the GPU is visible to Docker:
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
   ```
2. Copy this project folder over (without `venv/`). Create `.env` from `.env.example` and pick `WRITER_MODEL` for the GPU's memory (see the "GPU tuning" section there; for example, `qwen3:14b` for 12–16 GB).
3. Start it (NVIDIA):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
   ```
   - For CPU only, use just `docker compose up -d --build`.
   - For AMD on a Linux host, use `-f docker-compose.amd.yml` instead of the GPU file.
4. Watch it and run commands inside the container:
   ```bash
   docker compose logs -f agents
   ```
   ```bash
   docker compose exec agents python -m content_agents check
   ```
   ```bash
   docker compose exec agents python -m content_agents start-blog
   ```

Details:
- Runs, drafts, logs and the SQLite state live in `./data` on the host, so they survive `docker compose down` and image rebuilds. Models stay in the `ollama-models` volume.
- `content_agents/sources.yaml` is mounted read-only, so you can edit feeds without rebuilding. Restart `agents` after editing.
- If the container stops in the middle of writing, the next start picks the run up from its last checkpoint.
- `ollama` isn't published on host port 11434, so it won't clash with a native Ollama install.

## Setup without Docker (Windows)
1. **Models** (this laptop has no GPU: 16 GB RAM, CPU only):
   ```bash
   ollama pull qwen3:8b
   ```
   ```bash
   ollama pull qwen3:4b
   ```
   - `qwen3:8b` writes. `qwen3:4b` parses replies and plans. Qwen3 "thinking" is disabled for speed.
   - Expect roughly 15–25 minutes per blog draft and 5–10 minutes per script on this CPU. That's fine because approvals are asynchronous.
   - On a GPU machine, set `WRITER_MODEL=qwen3:14b` (or larger) in `.env`.
2. **Python deps** (already in `venv`):
   ```bash
   venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. **Config**: copy `.env.example` to `.env` and fill it in. For Gmail:
   - Use a sender Gmail account with 2-Step Verification and an **App Password** (Google Account → Security → App passwords).
   - Enable IMAP in Gmail settings.
   - Set `MAIL_BACKEND=gmail`, `SMTP_USER`, `SMTP_APP_PASSWORD`, and `APPROVER_EMAIL`.
4. **Images (optional)**:
   - Local diagrams: `winget install Graphviz.Graphviz`
   - Free stock photos: `PEXELS_API_KEY` and/or `UNSPLASH_ACCESS_KEY`
   - Gemini: `GOOGLE_API_KEY`

   Each image type tries its providers in order:
   - diagram → Graphviz → Gemini
   - photo → Pexels → Unsplash → Gemini
   - illustration → Gemini → stock

   Missing providers are skipped. If nothing works, a "suggested image" marker is inserted.
5. **Check everything**:
   ```bash
   venv\Scripts\python.exe -m content_agents check
   ```
6. **Schedule**: this creates Windows Task Scheduler tasks from `BLOG_SCHEDULE`, `YT_SCHEDULE` and `POLL_MINUTES` in `.env`. The tasks wake the PC from sleep. Re-run the script after changing those values.
   ```bash
   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
   ```

## Scheduling
- **One set of settings:** `BLOG_SCHEDULE`, `YT_SCHEDULE` and `POLL_MINUTES` in `.env` drive both the Windows Task Scheduler (`scripts/register_tasks.ps1`) and Docker's built-in `serve` loop.
- **Formats:** `daily 07:00`, `mon 08:00`, `mon,thu 09:30` or `off`. A job that was missed while the machine was off runs when the machine comes back.
- **Choosing a scheduler:** Task Scheduler is the only one that can wake a sleeping PC. `serve` is what runs inside Docker.
- **No duplicate starts:** scheduled starts use `--scheduled`, which skips the start if that agent already began a run today. That covers a manual start, the other scheduler, or a restart. Manual `start-blog` and `start-youtube` always run.
- **Run on one machine only:** use either this laptop or the Docker GPU laptop. Each machine keeps its own `data/`, so two machines would both email topics.
- **Why not Celery:** Celery needs a broker (Redis or RabbitMQ) plus separate worker and beat processes, and it has no official Windows support. It doesn't help with 2 jobs on one machine whose local LLM can only run one job at a time. Paused runs are already stored durably in LangGraph's SQLite checkpoints. Consider Celery, RQ or Dramatiq only if this grows to many clients or several GPU workers sharing a queue.

## Commands
```
python -m content_agents start-blog [--days 7]
python -m content_agents start-youtube [--days 2]
python -m content_agents poll
python -m content_agents status
python -m content_agents retry RUN_ID           # continue a run that stopped on an error (e.g. Ollama was off)
python -m content_agents serve                  # long-running scheduler (what the Docker image runs)
python -m content_agents reply RUN_ID "2"       # MAIL_BACKEND=file only: simulate an email reply
```

## Dry run without Gmail
With `MAIL_BACKEND=file`, emails are written to `data/outbox/*.eml` (open them in Outlook or Thunderbird, or with a double-click):
```
python -m content_agents start-blog
python -m content_agents status
python -m content_agents reply <run_id> "2"
python -m content_agents poll
python -m content_agents reply <run_id> "REVISE: add a code example to section 3"
python -m content_agents poll
python -m content_agents reply <run_id> "APPROVE"
python -m content_agents poll
```

## Layout
- `content_agents/blog/graph.py`: blog pipeline (planner/worker/reducer carried over from the original `Blog_Writer.py`).
- `content_agents/youtube/graph.py`: script pipeline.
- `content_agents/hitl.py`: shared email-approval nodes (send → interrupt → route, retry caps).
- `content_agents/research/`: free sources. Edit `content_agents/sources.yaml` to add or remove feeds.
- `content_agents/mail/`: SMTP/IMAP, reply parsing, email templates.
- `content_agents/images/`: Graphviz, Pexels, Unsplash, Gemini, and deterministic image placement.
- `data/`: checkpoints, run folders (`data/runs/<run_id>/` with drafts, finals, images), and logs.

Tests: install `requirements-dev.txt`, then run `venv\Scripts\python.exe -m pytest -q`. They use a fake LLM and don't need Ollama or network.
