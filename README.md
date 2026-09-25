# MegaBot 🤖⬇️

A professional Telegram bot that downloads files from **MEGA (mega.nz / mega.io)** using your own
MEGA account, analyzes what it downloaded, and delivers it to your Telegram chat — with a smooth,
emoji-rich inline-button UI.

Built on **Pyrogram (MTProto)**, so uploads bypass the 50 MB Bot-API HTTP limit — the bot can send
files up to ~2 GB.

## Features

- 🔗 **Supported Hosts:** MEGA (`mega.nz` / `mega.io`), MediaFire (`mediafire.com`), MP4Upload (`mp4upload.com`), TeraBox (`terabox.app` / `1024tera.com`), and direct HTTP/HTTPS web links
- 📁 **Direct Telegram Uploads:** Send documents, videos, audios, or photos directly to the bot for automatic decompression, PDF conversion, and processing
- 🤖 **Autonomous AI Brain:** Modern reasoning engine supporting OpenRouter, Google Gemini 2.0 Flash, OpenAI (GPT-4o-mini), Groq, and DeepSeek
- ⚙️ **Real-time AI Config via Telegram:** Change AI model, provider, temperature, and API keys directly inside Telegram using `/aiconfig` without server restarts
- 📦 **Smart Unzip:** Decompresses ZIP, RAR, 7Z, TAR, GZ archives (with zip-slip safety)
- 🖼️ **Image Sets:** Merged into one **PDF** in natural order (`1, 2, 3 … 10, 100`)
- 🎬 **Videos:** Stream-ready uploads with auto-generated thumbnails
- 📊 Live progress bar with speed + ETA, cancel button on every job
- 🗄️ All users, jobs & settings stored in **MongoDB** (motor) with in-memory fallback
- ⚙️ Per-user settings, per-user concurrency limits, disk-space guard, retry with backoff,
  link dedup cache (24 h), owner panel with stats/ban/broadcast

## Commands

| Command | Description |
|---|---|
| `/start` | Start bot and view features |
| `/agent` | AI Agent dashboard & status |
| `/aiconfig` | Interactive AI Configuration menu (models, providers, keys, temperature) |
| `/setmodel <id>` | Quickly switch active AI model (e.g. `google/gemini-2.0-flash`, `openai/gpt-4o-mini`) |
| `/setkey <key>` | Save AI API key securely (message auto-deletes for privacy) |
| `/setprovider <prov>` | Switch provider (`openrouter`, `gemini`, `openai`, `groq`, `deepseek`) |
| `/settemp <val>` | Adjust AI temperature (`0.0` - `2.0`) |
| `/settings` | User preferences (Archive mode, PDF merging, video thumbnails) |
| `/cancel <id>` | Cancel an active job |
| `/terabox <cookie>` | Set TeraBox ndus session cookie |
| `/login` & `/logout` | Connect or disconnect custom MEGA account |
| `/stats` (Owner) | View bot statistics (users, jobs, memory, disk) |

## Docker

```bash
docker build -t megabot .
docker run --env-file .env -p 8080:8080 megabot
```

## Project layout

```
config.py            env-based settings
main.py              Pyrogram client bootstrap
megabot/
  core/              MongoDB layer, job queue
  downloaders/       MEGA downloader (extensible)
  analyzers/         content classification
  processors/        archives, images→PDF, uploader
  ui/                texts & inline keyboards
  plugins/           /start, link handler, callbacks, settings, owner
```