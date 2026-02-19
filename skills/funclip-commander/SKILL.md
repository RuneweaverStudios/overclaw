---
name: funclip-commander
displayName: Funclip Commander
description: Integrates OpenClaw with the FunClip AI video editor to enable AI-powered video recognition and clipping via command line.
version: 0.1.0
---

# Funclip Commander

## Description

Integrates OpenClaw with the FunClip AI video editor to enable AI-powered video recognition and clipping via command line.

# Funclip Commander Skill

This skill allows OpenClaw agents to leverage the powerful features of the open-source FunClip AI video editor. By utilizing FunClip's command-line interface, agents can:

- Perform speech recognition on video files.
- Generate SRT subtitles.
- Clip specific segments of videos based on text prompts or speaker diarization.


## Usage

This skill will expose functions to:

- `funclip.recognize_video(file_path_or_url: str, output_dir: str)`: Handles video download (if YouTube URL), extracts audio, and performs speech-to-text transcription. Depending on `config.json` settings, it uses Buzz (offline), OpenAI Whisper (cloud), or ClawClip's native ASR. Returns `(video_filepath: str, audio_filepath: str, srt_filepath: str, srt_content: str)`.
- `funclip.clip_video(file_path_or_url: str, output_dir: str, srt_file_path: str, dest_text: str = None, start_ost: int=0, end_ost: int=0, output_file: str = None)`: Clips video based on a provided SRT, recognized text, or timestamps using ClawClip's Stage 2.


## Commands

This section would list the commands exposed by the OpenClaw skill to interact with FunClip.


## Purpose

The Funclip Commander skill acts as a bridge between OpenClaw's agentic capabilities and FunClip's sophisticated video processing. It enables automated, AI-driven video content creation and analysis workflows directly from your OpenClaw environment.


## Prerequisites

1.  **FunClip Installed Locally**: The FunClip repository (`https://github.com/modelscope/FunClip`) must be cloned to your OpenClaw workspace within a directory named `FunClip`. All its Python dependencies must be installed in a virtual environment (`FunClip/.venv`).
2.  **`ffmpeg` and `ffprobe`**: These must be available in the system's PATH.
3.  **`imagemagick`**: (Optional, for embedded subtitles) Must be installed and configured as per FunClip's `README.md`.
4.  **`yt-dlp`**: Required for downloading YouTube videos. Must be available in the system’s PATH.


## Configuration (`config.json`)

- **funclip_path**: Path to the ClawClip repo (e.g. `workspace/ClawClip`).
- **venv_path**: Path to ClawClip's virtual environment (e.g. `workspace/ClawClip/.venv`).
- **use_buzz_for_recognition**: `true` to use Buzz for ASR (default if key/path provided). Requires `buzz_python`.
- **buzz_python**: Full path to the Python executable within Buzz's virtual environment (e.g., `/Users/ghost/.openclaw/workspace/ClawClip/buzz_venv/bin/python3`). Buzz 1.4.x requires Python 3.12 or older.
- **use_whisper_for_recognition**: `true` to use OpenAI Whisper API for ASR. Conflicts with `use_buzz_for_recognition`.
- **whisper_model**: Whisper model to use (default: `whisper-1`). Requires `OPENAI_API_KEY` to be configured in Gateway.

Recognition Backends (selected via config, `buzz` has priority over `whisper`):

*   **Buzz (offline)**: Requires a Python 3.12 environment with Buzz installed. Ensures privacy and local processing. Configure `buzz_python` and set `use_buzz_for_recognition: true`.
*   **OpenAI Whisper API (cloud)**: Requires an `OPENAI_API_KEY` to be configured in the OpenClaw Gateway. Best for accuracy and convenience if API access is available. Set `use_whisper_for_recognition: true`.
*   **ClawClip Native (FunASR)**: Fallback to ClawClip's built-in ASR if neither Buzz nor Whisper are configured or available.
