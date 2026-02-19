# Funclip Commander for OpenClaw

**Leverage the power of FunClip AI Video Editor with OpenClaw!**

This OpenClaw skill provides a seamless interface to the open-source [FunClip](https://github.com/modelscope/FunClip) AI video editing tool. Automate video speech recognition, subtitle generation, and intelligent clipping directly within your OpenClaw workflows.

## Features

-   **AI-Powered Speech Recognition**: Transcribe audio from your video files.
-   **SRT Subtitle Generation**: Automatically generate subtitles for your videos.
-   **Intelligent Video Clipping**: Extract specific video segments based on text, duration, or speaker identification.
-   **OpenClaw Integration**: Orchestrate complex video editing tasks with other OpenClaw skills.

## Installation

1.  **Clone FunClip**: Ensure the OpenClaw agent has cloned the `FunClip` repository into your workspace at `FunClip/`.
    ```bash
    git clone https://github.com/modelscope/FunClip /Users/ghost/.openclaw/workspace/FunClip
    ```
2.  **Install FunClip Dependencies**: Navigate to the `FunClip` directory, create a Python virtual environment, and install its `requirements.txt`.
    ```bash
    cd /Users/ghost/.openclaw/workspace/FunClip
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    deactivate # Optional: Deactivate after install
    ```
3.  **Install `ffmpeg`, `ffprobe`, `imagemagick`**: Ensure these are installed and accessible in your system's PATH.
    *   On macOS via Homebrew: `brew install ffmpeg imagemagick`
    *   On Ubuntu: `sudo apt-get install ffmpeg imagemagick`
4.  **Download FunClip Font**: 
    ```bash
    cd /Users/ghost/.openclaw/workspace/FunClip
    mkdir -p font # if it doesn't exist
    curl -o font/STHeitiMedium.ttc https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ClipVideo/STHeitiMedium.ttc
    ```

### Optional: Buzz for speech-to-text (offline, Python 3.12)

[Buzz](https://github.com/chidiwilliams/buzz) uses Whisper locally. It requires **Python 3.12** (not 3.13+). To fix the Python version issue and use Buzz for transcriptions:

1. Install Python 3.12 (e.g. `brew install python@3.12` or `pyenv install 3.12`).
2. Run the Buzz venv installer:
   ```bash
   bash /Users/ghost/.openclaw/workspace/skills/funclip-commander/scripts/install_buzz_venv.sh
   ```
3. The script creates a dedicated Python 3.12 venv and installs `buzz-captions`. Add the printed `buzz_python` path to `config.json` and set `"use_buzz_for_recognition": true`.

## Usage (OpenClaw Commands)

This skill will expose commands (to be defined) that wrap FunClip's command-line interface. For example:

-   `funclip.recognize(video_path='path/to/video.mp4')`
-   `funclip.clip(video_path='path/to/video.mp4', dest_text='text to clip by')`

Check `SKILL.md` for full command details once the implementation is complete.

## Configuration

Edit `config.json` in the skill directory to adjust paths to your FunClip installation and virtual environment.

```json
{
  "funclip_path": "/Users/ghost/.openclaw/workspace/FunClip",
  "venv_path": "/Users/ghost/.openclaw/workspace/FunClip/.venv"
}
```
