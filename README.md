# 🧩 SmartWidget AI (Ollama Edition)

A "super-intelligent," local-first desktop widget. Built for **saadsalmanakram**, this project uses **Ollama** to run LLMs entirely on your machine—no API keys required. It features **Generative UI** and a professional **Windows Installer (.exe)**.

---

## 🛠 The Local-First Stack

| Layer | Technology | Role |
| --- | --- | --- |
| **Runtime** | **Deno 2.0** | Modern TS runtime; handles local AI logic and system scripts. |
| **Framework** | **Tauri 2.0** | Rust core for a transparent, frameless Windows overlay. |
| **Local LLM** | **Ollama** | Runs models like `llama3.2` or `deepseek-r1` locally. |
| **Frontend** | **React + Tailwind** | Renders AI-generated styles and layouts dynamically. |
| **Data** | **SQLite** | Local persistence for your To-Do list and Calendar logs. |
| **Installer** | **NSIS (.exe)** | Bundles the app into a single Windows Setup executable. |

---

## ✨ Key Features

* **🦙 Ollama Powered:** Integrated with the local Ollama API (`localhost:11434`). Supports offline intelligence.
* **🎨 Generative UI:** Command the widget to "Update theme to midnight-gold" and the AI will inject new Tailwind classes into the React components.
* **🧠 Local Agents:** - *"Schedule a deep work session for 2 PM"* → AI calls local Rust functions to update your schedule.
* *"Summarize my tasks"* → AI reads the local SQLite DB and provides a summary.


* **🖥 Desktop Native:** Frameless, transparent, "always-on-top," and click-through support.

---

## 🚀 Getting Started

### 1. Prerequisites

* **Rust:** `rustup update`
* **Deno 2.0:** `deno upgrade`
* **Ollama:** [Download Ollama](https://ollama.com/) and pull a model:
```bash
ollama pull llama3.2

```



### 2. Installation

```bash
# Clone the repo
git clone https://github.com/saadsalmanakram/smart-widget-ai.git
cd smart-widget-ai

# Install frontend dependencies
deno install

```

### 3. Development

Run the widget in dev mode:

```bash
deno task tauri dev

```

### 4. Build the Windows Installer (.exe)

Create a production-ready installer for distribution:

```bash
deno task tauri build --bundles nsis

```

> **Output:** `src-tauri/target/release/bundle/nsis/SmartWidget_x64-setup.exe`

---

