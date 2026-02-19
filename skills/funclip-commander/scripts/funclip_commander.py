import os
import subprocess
import json
import sys
import re
from urllib.parse import urlparse
import argparse
import logging
from datetime import timedelta

# OpenClaw tool imports (assuming this script is run within an OpenClaw context that exposes default_api)
try:
    from openclaw_tools import default_api
except ImportError:
    logging.warning("openclaw_tools not found. Whisper API calls may not work.")
    default_api = None

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Get base path from the skill's config.json
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SKILL_DIR, "..", "config.json")

def load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"Config file not found at {CONFIG_PATH}")
        return {}

_config = load_config() # Use a private variable to avoid direct global modification

CLAWCLIP_BASE_PATH = _config.get("funclip_path")
VENV_PATH = _config.get("venv_path")
python_executable = os.path.join(VENV_PATH, "bin", "python3")
BUZZ_PYTHON_EXECUTABLE = _config.get("buzz_python")

DOWNLOAD_BASE_DIR = os.path.join(SKILL_DIR, "..", "..", "youtube_downloads")
os.makedirs(DOWNLOAD_BASE_DIR, exist_ok=True)

PROCESSED_LOG_PATH = os.path.join(DOWNLOAD_BASE_DIR, "processed_log.json")

def _load_processed_log():
    if os.path.exists(PROCESSED_LOG_PATH):
        with open(PROCESSED_LOG_PATH, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logging.warning(f"Corrupt processed_log.json, re-creating: {PROCESSED_LOG_PATH}")
                return {}
    return {}

def _save_processed_log(log_data):
    with open(PROCESSED_LOG_PATH, 'w') as f:
        json.dump(log_data, f, indent=4)

def _is_youtube_url(url):
    youtube_regex = r'(?:https?://)?(?:www\.)?(?:youtube|youtu|youtube-nocookie)\.(?:com|be)/(?:watch\?v=|embed/|v/|.+\?v=|)([a-zA-Z0-9_-]{11})'
    return re.match(youtube_regex, url) is not None

def download_youtube_video(url: str, output_dir: str) -> str:
    output_dir = os.path.abspath(output_dir)
    logging.info(f"Starting YouTube video download for: {url} to {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    processed_log = _load_processed_log()
    if url in processed_log:
        cached = processed_log[url].get("video_path", "")
        if cached and os.path.exists(cached):
            logging.info(f"Video already downloaded: {cached}")
            return os.path.abspath(cached)

    current_dir = os.getcwd()
    try:
        os.chdir(output_dir)

        output_template = "%(title)s.%(ext)s"
        command = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4",
            "-o", output_template,
            url
        ]

        logging.info(f"Running yt-dlp command: {' '.join(command)}")
        process = subprocess.run(command, capture_output=True, text=True, check=True)
        combined_output = (process.stdout or "") + "\n" + (process.stderr or "")
        logging.info("yt-dlp stdout:")
        logging.info(process.stdout)
        if process.stderr:
            logging.error("yt-dlp stderr:")
            logging.error(process.stderr)

        downloaded_full_path = None
        match_destination = re.search(r'\[download\] Destination: (.+)', combined_output)
        if match_destination:
            raw_path = match_destination.group(1).strip()
            downloaded_full_path = os.path.abspath(raw_path) if os.path.isabs(raw_path) else os.path.join(output_dir, os.path.basename(raw_path))
        if not downloaded_full_path or not os.path.exists(downloaded_full_path):
            match_already_downloaded = re.search(r'\[download\] (.+?) has already been downloaded', combined_output)
            if match_already_downloaded:
                downloaded_filename = os.path.basename(match_already_downloaded.group(1).strip())
                downloaded_full_path = os.path.join(output_dir, downloaded_filename)
        if not downloaded_full_path or not os.path.exists(downloaded_full_path):
            for name in sorted(os.listdir(output_dir), key=lambda n: os.path.getmtime(os.path.join(output_dir, n)), reverse=True):
                p = os.path.join(output_dir, name)
                if os.path.isfile(p) and name.lower().endswith(('.mp4', '.mkv', '.webm', '.m4a')):
                    downloaded_full_path = os.path.abspath(p)
                    logging.info(f"Using most recent video in output dir: {downloaded_full_path}")
                    break
        if not downloaded_full_path or not os.path.exists(downloaded_full_path):
            raise Exception("YouTube download failed or filename not reliably found from yt-dlp output.")
        downloaded_full_path = os.path.abspath(downloaded_full_path)
        processed_log[url] = {"video_path": downloaded_full_path}
        _save_processed_log(processed_log)
        return downloaded_full_path
    finally:
        os.chdir(current_dir) 

def _extract_audio_from_video(video_path: str, output_audio_path: str) -> str:
    video_path = os.path.abspath(video_path)
    output_audio_path = os.path.abspath(output_audio_path)
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Cannot extract audio: video file not found: {video_path}")
    logging.info(f"Extracting audio from {video_path} to {output_audio_path}")
    os.makedirs(os.path.dirname(output_audio_path), exist_ok=True)

    processed_log = _load_processed_log()
    if video_path in processed_log and os.path.exists(processed_log[video_path].get("audio_path", "")):
        logging.info(f"Audio already extracted: {processed_log[video_path]['audio_path']}")
        return processed_log[video_path]["audio_path"]

    command = [
        "ffmpeg",
        "-i", video_path,
        "-vn", 
        "-acodec", "pcm_s16le", 
        "-ar", "16000", 
        "-ac", "1", 
        output_audio_path
    ]
    logging.info(f"Running ffmpeg command: {' '.join(command)}")
    process = subprocess.run(command, capture_output=True, text=True, check=True)
    logging.info("FFmpeg stdout:")
    logging.info(process.stdout)
    if process.stderr:
        logging.error("FFmpeg stderr:")
        logging.error(process.stderr)
    
    if video_path not in processed_log:
        processed_log[video_path] = {}
    processed_log[video_path]["audio_path"] = output_audio_path
    _save_processed_log(processed_log)
    return output_audio_path

def _transcribe_audio_with_buzz(audio_path: str, output_srt_path: str) -> str:
    logging.warning(f">>> BUZZ: Transcribing audio with Buzz (offline Whisper): {audio_path} <<<")
    logging.info(f"Transcribing audio with Buzz: {audio_path}")
    if not BUZZ_PYTHON_EXECUTABLE or not os.path.exists(BUZZ_PYTHON_EXECUTABLE):
        logging.error(f"Buzz Python executable not found at {BUZZ_PYTHON_EXECUTABLE}. Please configure 'buzz_python' in config.json.")
        raise Exception("Buzz not configured or found.")

    # The first argument to -m needs to be 'buzz' if buzz-captions is pip installed 
    # and the venv is active. No separate 'buzz' script file should be needed.
    command = [
        BUZZ_PYTHON_EXECUTABLE, 
        "-m", "buzz",
        audio_path,
        "--output-format", "srt",
        "--file-format", "srt", 
        "--output-file", output_srt_path,
        "--language", "en", 
        "--model", "tiny.en" # Or configurable
    ]
    logging.info(f"Running Buzz command: {' '.join(command)}")
    current_env = os.environ.copy()
    # Ensure buzz's venv bin is in PATH for any internal buzz executables/scripts
    current_env["PATH"] = os.path.dirname(BUZZ_PYTHON_EXECUTABLE) + os.pathsep + current_env["PATH"]
    process = subprocess.run(command, capture_output=True, text=True, check=True, env=current_env)
    logging.info("Buzz stdout:")
    logging.info(process.stdout)
    if process.stderr:
        logging.error("Buzz stderr:")
        logging.error(process.stderr)

    if not os.path.exists(output_srt_path):
        raise Exception(f"Buzz did not create SRT file at {output_srt_path}")
        
    return output_srt_path

def _transcribe_audio_with_whisper(audio_path: str, model: str = "whisper-1") -> dict:
    logging.info(f"Transcribing audio with Whisper: {audio_path}")
    if default_api is None:
        logging.error("OpenClaw default_api not available. Cannot call openai_whisper_api.")
        raise Exception("OpenClaw API not available for Whisper transcription.")
    
    try:
        logging.info(f"Calling default_api.openai_whisper_api with audio_path: {audio_path}")
        whisper_result = default_api.openai_whisper_api(audio=audio_path, model=model, response_format="json") 
        logging.info(f"Whisper API raw result: {whisper_result}")
        
        if isinstance(whisper_result, dict) and "text" in whisper_result and "segments" in whisper_result:
            return whisper_result
        elif isinstance(whisper_result, str):
            try:
                return json.loads(whisper_result)
            except json.JSONDecodeError:
                logging.error(f"Whisper API returned non-JSON string: {whisper_result}")
                raise Exception("Whisper API returned unexpected string format.")
        else:
            logging.error(f"Whisper API returned unexpected type or structure: {type(whisper_result)} - {whisper_result}")
            raise Exception("Whisper API returned unexpected format.")
    except Exception as e:
        logging.error(f"Error calling openai-whisper-api: {e}")
        raise

def _convert_whisper_to_srt(whisper_segments: list) -> str:
    srt_content = []
    for i, segment in enumerate(whisper_segments or []):
        start_td = timedelta(seconds=segment["start"])
        end_td = timedelta(seconds=segment["end"])
        
        start_ms = int(start_td.total_seconds() * 1000 % 1000)
        end_ms = int(end_td.total_seconds() * 1000 % 1000)

        start_time_str = f"{int(start_td.total_seconds() // 3600):02d}:{int((start_td.total_seconds() % 3600) // 60):02d}:{int(start_td.total_seconds() % 60):02d},{start_ms:03d}"
        end_time_str = f"{int(end_td.total_seconds() // 3600):02d}:{int((end_td.total_seconds() % 3600) // 60):02d}:{int(end_td.total_seconds() % 60):02d},{end_ms:03d}"
        
        srt_content.append(str(i + 1))
        srt_content.append(f"{start_time_str} --> {end_time_str}")
        srt_content.append(segment["text"].strip())
        srt_content.append("") 
    return "\n".join(srt_content)

def _run_clawclip_command(stage, video_filepath_for_processing, analysis_output_dir, **kwargs):
    if not os.path.exists(CLAWCLIP_BASE_PATH):
        logging.error(f"Error: ClawClip base path not found at {CLAWCLIP_BASE_PATH}")
        sys.exit(1)
    if not os.path.exists(python_executable):
        logging.error(f"Error: ClawClip virtual environment not found at {python_executable}")
        sys.exit(1)
    
    clawclip_videoclipper_script = os.path.join(CLAWCLIP_BASE_PATH, "funclip", "videoclipper.py")
    
    funclip_file_path = os.path.abspath(video_filepath_for_processing)
    funclip_output_dir = os.path.abspath(analysis_output_dir)

    command = [
        python_executable,
        clawclip_videoclipper_script,
        "--stage", str(stage),
        "--file", funclip_file_path,
        "--output_dir", funclip_output_dir,
        "--lang", "en" 
    ]

    for key, value in kwargs.items():
        if value is not None:
            # ClawClip uses underscores in argument names, not hyphens
            command.append(f"--{key}")
            command.append(str(value))

    logging.info(f"Running ClawClip command: {' '.join(command)}")
    process = subprocess.run(command, capture_output=True, text=True, check=True)
    logging.info("ClawClip stdout:")
    logging.info(process.stdout)
    if process.stderr:
        logging.error("ClawClip stderr:")
        logging.error(process.stderr)
    
    return (process.stdout, funclip_file_path) 


def recognize_video(file_path_or_url: str, output_dir: str):
    logging.info(f"Recognizing video: {file_path_or_url} into {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    
    # Log which recognition backend will be used (based on config priority)
    if _config.get("use_buzz_for_recognition", False) and BUZZ_PYTHON_EXECUTABLE and os.path.exists(BUZZ_PYTHON_EXECUTABLE):
        logging.warning("=" * 60)
        logging.warning("RECOGNITION BACKEND: BUZZ (offline Whisper)")
        logging.warning(f"Buzz Python: {BUZZ_PYTHON_EXECUTABLE}")
        logging.warning("=" * 60)
    elif _config.get("use_whisper_for_recognition", False):
        logging.warning("=" * 60)
        logging.warning("RECOGNITION BACKEND: OpenAI Whisper API")
        logging.warning("=" * 60)
    else:
        logging.warning("=" * 60)
        logging.warning("RECOGNITION BACKEND: ClawClip Native (FunASR)")
        logging.warning("=" * 60)

    video_filepath_for_processing = file_path_or_url
    logging.info(f"Initial file_path_or_url: {file_path_or_url}")
    if _is_youtube_url(file_path_or_url):
        logging.info(f"_is_youtube_url returned True for {file_path_or_url}.")
        logging.info(f"YouTube URL detected. Downloading video to {DOWNLOAD_BASE_DIR}.")
        downloaded_path = download_youtube_video(file_path_or_url, DOWNLOAD_BASE_DIR)
        video_filepath_for_processing = downloaded_path
        logging.info(f"Downloaded video path: {downloaded_path}")
        processed_log = _load_processed_log()
        if file_path_or_url in processed_log and downloaded_path not in processed_log:
            processed_log[downloaded_path] = dict(processed_log[file_path_or_url])
            _save_processed_log(processed_log)
    else:
        logging.info(f"Not a YouTube URL: {file_path_or_url}.")
        if not os.path.exists(file_path_or_url):
            raise FileNotFoundError(f"Local video file not found: {file_path_or_url}")
    
    video_filepath_for_processing = os.path.abspath(video_filepath_for_processing)
    if not os.path.isfile(video_filepath_for_processing):
        raise FileNotFoundError(f"Video file for processing not found: {video_filepath_for_processing}")
    logging.info(f"Video file for audio extraction: {video_filepath_for_processing}")
    audio_output_path = os.path.join(os.path.abspath(output_dir), os.path.splitext(os.path.basename(video_filepath_for_processing))[0] + ".wav")

    processed_log = _load_processed_log()
    log_key = video_filepath_for_processing

    if log_key in processed_log and os.path.exists(processed_log[log_key].get("audio_path", "")):
        logging.info(f"Audio already extracted: {processed_log[log_key]['audio_path']}")
        audio_output_path = processed_log[log_key]["audio_path"]
    else:
        _extract_audio_from_video(video_filepath_for_processing, audio_output_path)

    srt_filepath = os.path.join(output_dir, os.path.splitext(os.path.basename(video_filepath_for_processing))[0] + ".srt")
    srt_content = ""

    if log_key in processed_log and os.path.exists(processed_log[log_key].get("srt_path", "")):
        logging.info(f"SRT already generated or found: {processed_log[log_key]['srt_path']}")
        with open(processed_log[log_key]["srt_path"], 'r') as f:
            srt_content = f.read()
        srt_filepath = processed_log[log_key]["srt_path"]
    
    elif _config.get("use_buzz_for_recognition", False) and BUZZ_PYTHON_EXECUTABLE and os.path.exists(BUZZ_PYTHON_EXECUTABLE):
        logging.warning(">>> STARTING BUZZ RECOGNITION <<<")
        logging.info("Using Buzz for recognition (offline Whisper).")
        try:
            _transcribe_audio_with_buzz(audio_output_path, srt_filepath)
            with open(srt_filepath, 'r') as f:
                srt_content = f.read()
            logging.warning(">>> BUZZ RECOGNITION COMPLETED SUCCESSFULLY <<<")
            logging.info(f"Buzz SRT saved to: {srt_filepath}")
            processed_log[log_key]["srt_path"] = srt_filepath
            _save_processed_log(processed_log)
        except Exception as e:
            logging.error(f"Buzz transcription failed: {e}")
            if _config.get("use_whisper_for_recognition", False):
                logging.warning("Buzz failed, falling back to OpenAI Whisper for recognition.")
                whisper_result = _transcribe_audio_with_whisper(audio_output_path, _config.get("whisper_model", "whisper-1"))
                srt_content = _convert_whisper_to_srt(whisper_result.get("segments", []))
                with open(srt_filepath, "w") as f:
                    f.write(srt_content)
                logging.info(f"Whisper SRT saved to: {srt_filepath}")
                processed_log[log_key]["srt_path"] = srt_filepath
                _save_processed_log(processed_log)
            else:
                logging.warning("No Buzz or Whisper configured/working. Attempting ClawClip native recognition.")
                _run_clawclip_command(1, video_filepath_for_processing, output_dir)
                if os.path.exists(os.path.join(os.path.abspath(output_dir), "total.srt")):
                    with open(os.path.join(os.path.abspath(output_dir), "total.srt"), 'r') as f:
                        srt_content = f.read()
                    srt_filepath = os.path.join(os.path.abspath(output_dir), "total.srt") 
                processed_log[log_key]["srt_path"] = srt_filepath
                _save_processed_log(processed_log)

    elif _config.get("use_whisper_for_recognition", False):
        logging.info("Using OpenAI Whisper for recognition.")
        whisper_result = _transcribe_audio_with_whisper(audio_output_path, _config.get("whisper_model", "whisper-1"))
        srt_content = _convert_whisper_to_srt(whisper_result.get("segments", []))
        with open(srt_filepath, "w") as f:
            f.write(srt_content)
        logging.info(f"Whisper SRT saved to: {srt_filepath}")
        processed_log[log_key]["srt_path"] = srt_filepath
        _save_processed_log(processed_log)
    else:
        logging.info("Using ClawClip's native recognition.")
        _run_clawclip_command(1, video_filepath_for_processing, output_dir)
        if os.path.exists(os.path.join(os.path.abspath(output_dir), "total.srt")):
            with open(os.path.join(os.path.abspath(output_dir), "total.srt"), 'r') as f:
                srt_content = f.read()
            srt_filepath = os.path.join(os.path.abspath(output_dir), "total.srt")
        processed_log[log_key]["srt_path"] = srt_filepath
        _save_processed_log(processed_log)
    
    total_srt_filepath = os.path.join(output_dir, "total.srt")
    if srt_content and not os.path.exists(total_srt_filepath):
        with open(total_srt_filepath, "w") as f:
            f.write(srt_content)
        logging.info(f"Copied generated SRT to {total_srt_filepath} for ClawClip Stage 2 compatibility.")
    elif os.path.exists(total_srt_filepath):
        logging.info(f"total.srt already exists at {total_srt_filepath}")
        if not srt_content:
            with open(total_srt_filepath, "r") as f:
                srt_content = f.read()
            srt_filepath = total_srt_filepath 

    return (video_filepath_for_processing, audio_output_path, srt_filepath, srt_content)

def clip_video(file_path_or_url: str, output_dir: str, srt_file_path: str, dest_text: str = None, start_ost: int = 0, end_ost: int = 0, output_file: str = None, timestamp_list: list = None):
    logging.info(f"Clipping video: {file_path_or_url} into {output_dir} with text: '{dest_text}' or timestamps.")
    os.makedirs(output_dir, exist_ok=True)

    actual_video_path = file_path_or_url
    if _is_youtube_url(file_path_or_url):
        actual_video_path = download_youtube_video(file_path_or_url, DOWNLOAD_BASE_DIR) 

    total_srt_file = os.path.join(os.path.abspath(output_dir), "total.srt")
    if srt_file_path and os.path.exists(srt_file_path) and not os.path.exists(total_srt_file):
        subprocess.run(["cp", srt_file_path, total_srt_file], check=True)
        logging.info(f"Copied provided SRT ({srt_file_path}) to {total_srt_file} for ClawClip Stage 2 compatibility.")
    elif not os.path.exists(total_srt_file):
        logging.error(f"Error: For stage 2, total.srt was not found in {output_dir} and no valid srt_file_path was provided or found.")
        raise FileNotFoundError("Required total.srt missing for ClawClip stage 2.")

    return _run_clawclip_command(2, actual_video_path, output_dir, dest_text=dest_text, start_ost=start_ost, end_ost=end_ost, output_file=output_file)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="FunClip Commander Skill for OpenClaw")
    parser.add_argument('action', type=str, help='Action to perform: recognize_video or clip_video')
    parser.add_argument('file_path_or_url', type=str, help='Path to video file or YouTube URL')
    parser.add_argument('output_dir', type=str, help='Directory to save output')
    parser.add_argument('--srt_file_path', type=str, help='Path to SRT file for clipping (used in clip_video)', default=None)
    parser.add_argument('--dest_text', type=str, help='Text segment to clip by (for clip_video)', default=None)
    parser.add_argument('--start_ost', type=int, help='Start offset in milliseconds (for clip_video)', default=0)
    parser.add_argument('--end_ost', type=int, help='End offset in milliseconds (for clip_video)', default=0) 
    parser.add_argument('--output_file', type=str, help='Output filename for clip (for clip_video)', default=None)
    parser.add_argument('--whisper', action='store_true', help='Use OpenAI Whisper for recognition (overrides config)', default=False)
    parser.add_argument('--buzz', action='store_true', help='Use Buzz for recognition (overrides config)', default=False)

    args = parser.parse_args()

    try:
        exec_config = load_config()
        if args.whisper:
            exec_config["use_whisper_for_recognition"] = True
            exec_config["use_buzz_for_recognition"] = False
        elif args.buzz:
            exec_config["use_buzz_for_recognition"] = True
            exec_config["use_whisper_for_recognition"] = False

        # REMOVED_GLOBAL_CONFIG_DECLARATION
        _config = exec_config

        if args.action == 'recognize_video':
            video_filepath, audio_filepath, srt_filepath, srt_content = recognize_video(args.file_path_or_url, args.output_dir)
            if srt_filepath and srt_content:
                print(f"ClawClip Recognition Successful. SRT saved to: {srt_filepath}")
                print(f"Video Path: {video_filepath}")
                print(f"Audio Path: {audio_filepath}")
                print("--- Full Transcript ---")
                print(srt_content)
                print("-----------------------")
            else:
                print("ClawClip Recognition completed, but SRT not found or content not extracted.")
        elif args.action == 'clip_video':
            stdout, _ = clip_video(args.file_path_or_url, args.output_dir, args.srt_file_path, args.dest_text, args.start_ost, args.end_ost, args.output_file)
            print(stdout)
        else:
            print(f"Unknown action: {args.action}")
            sys.exit(1)
    except Exception as e:
        logging.error(f"An error occurred during {args.action}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
