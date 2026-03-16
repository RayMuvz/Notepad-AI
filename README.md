# Notepad_AI

<img src="notepad.ico" alt="Notepad_AI icon" width="128" />

Notepad_AI is a Windows-style Notepad clone built with **Python** and **PyQt6**, with integrated OpenAI assistance.

## Features

- Tabs (multiple documents)
- Word Wrap toggle
- Autosave (every 10 seconds for saved files)
- Find / Replace
- F5 Time/Date insertion
- `.LOG` support (first line `.LOG` appends a timestamp when opened)
- Standard shortcuts: **Ctrl+N**, **Ctrl+O**, **Ctrl+S**, **Ctrl+P**, etc.
- AI integration (OpenAI `gpt-4o`) on **Shift+Enter**
- Persistent settings in `%AppData%\Notepad_AI\config.json`
- Optional dark mode

## Requirements

- Python 3.9 or later (Windows)
- `pip install -r requirements.txt`

## Security & Privacy

- Your **OpenAI API key is never hard-coded** in the app or stored in the repo.
- The key and basic settings are stored locally in `%AppData%\Notepad_AI\config.json`.
- OpenAI requests include:
  - Your prompts and relevant document text,
  - Previous messages for that tab (chat-style memory).
- No prompts or responses are uploaded anywhere else or logged to disk by this app.
- If you are concerned about local storage, treat the machine as you would for any tool that holds API keys in plain text.

## Installation

- Download the latest `Notepad_AI_Installer.exe` from the GitHub Releases page.
- Run the installer and follow the prompts:
  - The app installs into `Program Files\Notepad_AI` by default.
  - A Start Menu entry is added.
  - You can optionally create a desktop shortcut.

## Running from source

```bash
python notepad_ai.py
```

On first run, you will be prompted (optionally) for an OpenAI API key.  
You can always set or change it via **Help → Configure API**.

## Downloads

- Prebuilt Windows installers (`Notepad_AI_Installer.exe`) are published on the project's GitHub Releases page.
- Advanced users can still build from source using PyInstaller and Inno Setup if desired.

## Usage

- Launch `Notepad_AI` from the Start Menu, desktop shortcut, or via `python notepad_ai.py`.
- On first run, enter your OpenAI API key when prompted (or later via `Help → Configure API`).
- Use it like standard Notepad:
  - `File`, `Edit`, and `View` menus + familiar shortcuts (`Ctrl+N`, `Ctrl+O`, `Ctrl+S`, `Ctrl+P`, etc.).
  - Tabs for multiple documents, status bar, zoom, and word wrap.
- For AI assistance:
  - Type in the document and press **Shift+Enter** to send a prompt.
  - The AI response streams into the document as a read-only block under your prompt.
  - You can collapse/expand AI blocks and continue writing below them.

