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

## 🧠 Implementation: Ollama Integration

The widget communicates with Ollama via Deno using the official `ollama` npm package (supported natively in Deno 2.0).

```typescript
// src/lib/ai.ts
import { Ollama } from "npm:ollama";

const ollama = new Ollama({ host: "http://127.0.0.1:11434" });

export async function chatWithWidget(prompt: string) {
  const response = await ollama.chat({
    model: "llama3.2",
    messages: [{ role: "user", content: prompt }],
    format: "json", // Ensures we get parsable UI/Task data
  });
  return JSON.parse(response.message.content);
}

```

---

## ⚙️ Configuration (`tauri.conf.json`)

To ensure the installer and window behave correctly on Windows:

```json
{
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "identifier": "com.saadsalman.smartwidget",
    "windows": {
      "nsis": {
        "oneClick": true,
        "perMachine": false,
        "allowElevation": true,
        "installerIcon": "icons/icon.ico"
      }
    }
  },
  "window": {
    "title": "SmartWidget",
    "width": 350,
    "height": 550,
    "transparent": true,
    "decorations": false,
    "alwaysOnTop": true,
    "skipTaskbar": true
  }
}

```

---

## 🛠 Roadmap

* [ ] **Model Switcher:** Toggle between different Ollama models via the UI.
* [ ] **GPU Acceleration Status:** Visual indicator of whether Ollama is using your GPU.
* [ ] **Auto-Installer:** A script to automatically install Ollama if the user doesn't have it.


---
