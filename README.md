# 🚀 FastAI CLI (Google AI in your Terminal)

The **FastAI CLI** (`fastai`) is a blazing-fast, lightweight command-line tool that brings Google's AI Overview directly into your terminal. It natively answers questions, summarizes code, and streams live Markdown right before your eyes.

It uses a highly optimized, fully invisible instance of Chromium to bypass automation detection, delivering answers in true headless mode without ever stealing focus or opening a visible window on your machine.

---

## ✨ Features

- **Live Streaming Markdown:** It parses the DOM in real-time and streams beautifully formatted ANSI Markdown (bold, italics, cyan titles, and magenta bullets) directly to your terminal.
- **100% Invisible:** Natively hidden. No annoying window flashes, no macOS focus-stealing, completely seamless.
- **Smart Pipe Detection (`|`)**: Pipe files into `fastai` for the AI to summarize or act upon. It intelligently detects when it's being piped or redirected into a file, automatically switching from colorful terminal mode to 100% raw Markdown format.
- **Interactive Mode**: Type `fastai` with no arguments to get an interactive prompt.

---

## 💻 Installation (One-Liner)

To install `fastai` globally on your machine, simply paste this one-liner into your terminal:

```bash
curl -sSL https://raw.githubusercontent.com/YOUR_USERNAME/fastai-cli/main/install.sh | bash
```

*(This automatically creates an isolated virtual environment, installs the necessary dependencies like `playwright` and `rich`, downloads the browser engine, and drops the executable in `~/.local/bin/fastai`.)*

---

## 🛠️ Usage Examples

### 1. Basic Query
Ask a simple question. It will display a loading spinner until the first byte arrives, then stream the styled response:
```bash
fastai "What is the capital of France?"
```

### 2. Interactive Mode
If you don't provide a query, it will ask you for one:
```bash
fastai
# > Enter your query for Google AI:
```

### 3. Piping Content (Summarization)
You can pipe a file directly into the AI. Use the `-p` or `--prompt` flag to tell the AI what to do with the piped content:
```bash
cat server.py | fastai -p "Explain this python code and find the bug"
```

### 4. Outputting to a File (Raw Markdown)
When you redirect the output to a file, the CLI automatically disables terminal colors and streams pure Markdown syntax so your `.md` files stay perfectly clean:
```bash
fastai "Write a long essay about ancient Rome" > essay.md
```

### 5. Debug Mode
Want to see how fast it was and how many characters it generated?
```bash
fastai "What is Kubernetes?" --debug
```

---

## ⚙️ How It Works (The Tech)
Instead of relying on an official, expensive API, this tool spawns an isolated Chromium instance via Playwright. 
- It sets `--headless=new` with a spoofed macOS User-Agent to completely bypass Google's headless blocking.
- A custom JavaScript function (`aioToMarkdown`) is injected during page load. It crawls Google's complex DOM tree and actively converts it to clean Markdown on the fly, skipping citations, disclaimers, and hidden nodes.
- Python polls this state memory and streams the diffs directly to your stdout using real-time ANSI escape codes.

---

## 🐛 Troubleshooting

**Q: On my very first run, it timed out or got stuck.**
> A: Google occasionally serves a CAPTCHA if it detects a completely fresh session. If this happens, simply run the script once with `headless=False` inside the python code to solve the CAPTCHA manually. Your session is saved persistently in `/tmp/pw_google_aio`, so you'll never have to solve it again.