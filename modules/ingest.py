"""
modules/ingest.py - Video Downloader and Audio Extractor.
Handles URL ingestion (YouTube, Twitch, Kick, etc.) via yt-dlp,
local video handling, and fast 16kHz mono audio extraction via FFmpeg.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import yt_dlp


def get_ffmpeg_binary() -> str:
    """
    Locates the ffmpeg executable from PATH, Python Scripts, or imageio_ffmpeg.
    """
    cmd = shutil.which("ffmpeg")
    if cmd:
        return cmd

    # Check Python Scripts folder
    scripts_dir = Path(sys.executable).parent / "Scripts"
    candidate = scripts_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if candidate.is_file():
        return str(candidate)

    # Check imageio_ffmpeg fallback
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    return "ffmpeg"


def is_url(source: str) -> bool:
    """Check if the input string is a valid HTTP/HTTPS URL."""
    try:
        parsed = urlparse(source.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def download_video(
    url: str,
    output_path: str,
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
) -> str:
    """
    Downloads the best video+audio up to 1080p using yt-dlp.
    Includes anti-403 player client configuration, chunked downloads,
    and optional browser cookie authentication.
    """
    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    base_template = str(out_dir / "raw_video")
    outtmpl = f"{base_template}.%(ext)s"

    # Locate Node.js for yt-dlp JS runtime deciphering if available
    node_bin = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"

    ydl_opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "overwrites": True,
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "http_chunk_size": 10485760,  # 10MB chunking avoids YouTube 403 connection resets
        "retries": 10,
        "fragment_retries": 10,
        "extractor_args": {
            "youtube": {
                # Rotate player clients to bypass YouTube SABR/403 blocks
                "player_client": ["android", "ios", "mweb", "web"]
            }
        },
    }

    if Path(node_bin).exists():
        ydl_opts["js_runtimes"] = {"node": {"path": str(node_bin)}}

    if cookies_from_browser:
        print(f"[*] Using session cookies from browser '{cookies_from_browser}' for YouTube authentication...")
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)
    elif cookies_file and Path(cookies_file).exists():
        print(f"[*] Using cookie file '{cookies_file}' for YouTube authentication...")
        ydl_opts["cookiefile"] = str(cookies_file)

    print(f"[*] Downloading stream/video via yt-dlp: {url}")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        if "confirm you're not a bot" in err_msg.lower() or "sign in" in err_msg.lower():
            raise RuntimeError(
                f"\n[!] YouTube Bot Challenge Detected: YouTube is requiring sign-in confirmation for this video.\n"
                f"    To resolve this immediately, pass your signed-in browser session:\n"
                f"    --> python main.py --input \"{url}\" --cookies-from-browser chrome\n"
                f"    (or replace 'chrome' with 'edge', 'firefox', 'brave', or 'opera')\n"
                f"    Alternatively, download the video locally and run:\n"
                f"    --> python main.py --input \"path/to/video.mp4\"\n"
            ) from e
        raise e

    expected_mp4 = Path(f"{base_template}.mp4")
    if expected_mp4.exists():
        if str(expected_mp4.resolve()) != str(Path(output_path).resolve()):
            shutil.move(str(expected_mp4), output_path)
        return output_path

    # If yt-dlp saved as another container (mkv, webm), find and remux to mp4
    for ext in (".mkv", ".webm", ".mov", ".avi"):
        candidate = Path(f"{base_template}{ext}")
        if candidate.exists():
            print(f"[*] Remuxing {candidate.name} to MP4...")
            ffmpeg = get_ffmpeg_binary()
            subprocess.run(
                [ffmpeg, "-y", "-i", str(candidate), "-c", "copy", output_path],
                check=True,
                capture_output=True,
            )
            candidate.unlink(missing_ok=True)
            return output_path

    raise FileNotFoundError(f"Failed to find downloaded output video from {url}")


def extract_audio_wav(video_path: str, audio_path: str) -> str:
    """
    Uses ffmpeg to extract a 16kHz, mono, pcm_s16le WAV file for whisper transcription.
    """
    ffmpeg = get_ffmpeg_binary()
    Path(audio_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"[*] Extracting 16kHz mono audio from {video_path} to {audio_path}...")
    cmd = [
        ffmpeg,
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(audio_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg audio extraction failed (code {result.returncode}):\n{result.stderr}"
        )

    if not Path(audio_path).exists() or Path(audio_path).stat().st_size == 0:
        raise RuntimeError(f"Audio file was not generated or is empty: {audio_path}")

    print(f"[+] Audio successfully extracted ({Path(audio_path).stat().st_size / (1024*1024):.2f} MB)")
    return audio_path


def ingest(
    input_source: str,
    workspace_dir: str = "workspace",
    cookies_from_browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
) -> tuple[str, str]:
    """
    Orchestrates ingestion of video and extraction of audio.

    Args:
        input_source: URL or local file path
        workspace_dir: Directory where temporary media files are stored
        cookies_from_browser: Browser name for yt-dlp cookie authentication ('chrome', 'edge', etc.)
        cookies_file: Path to a cookies.txt file

    Returns:
        tuple (raw_video_path, audio_wav_path)
    """
    ws = Path(workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)

    target_video_path = str(ws / "raw_video.mp4")
    target_audio_path = str(ws / "audio.wav")

    if is_url(input_source):
        download_video(
            url=input_source,
            output_path=target_video_path,
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
        )
    else:
        local_file = Path(input_source)
        if not local_file.exists():
            raise FileNotFoundError(f"Local input file not found: {input_source}")

        # If not already at workspace/raw_video.mp4, copy it
        if local_file.resolve() != Path(target_video_path).resolve():
            print(f"[*] Copying local file {local_file} to {target_video_path}...")
            shutil.copy2(str(local_file), target_video_path)

    # Extract 16kHz mono WAV
    extract_audio_wav(target_video_path, target_audio_path)

    return target_video_path, target_audio_path
