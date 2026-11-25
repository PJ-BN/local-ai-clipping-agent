"""
main.py - CLI Orchestrator for Local AI Clipping Agent.
Automates workflow for Clipping.net campaigns:
Ingest -> Transcribe (faster-whisper) -> AI Analysis (agy headless) -> Edit (9:16 + captions + loudnorm) -> Upload/Export
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict

from modules.analyze import analyze_transcript
from modules.edit import process_all_clips
from modules.ingest import ingest
from modules.transcribe import transcribe
from modules.upload import handle_uploads


def print_banner():
    banner = r"""
======================================================================
     ____ _ _             _                 _   _      _   
    / ___| (_)_ __  _ __ (_)_ __   __ _    | \ | | ___| |_ 
   | |   | | | '_ \| '_ \| | '_ \ / _` |   |  \| |/ _ \ __|
   | |___| | | |_) | |_) | | | | | (_| |_  | |\  |  __/ |_ 
    \____|_|_| .__/| .__/|_|_| |_|\__, (_) |_| \_|\___|\__|
             |_|   |_|            |___/                    
        Local AI Clipping & Viral Hook Automation Agent
               Powered by Antigravity CLI (agy)
======================================================================
"""
    print(banner)


def load_campaign_config(config_path: str, campaign_name: str = None) -> Dict[str, Any]:
    """
    Loads campaign configuration from JSON, supporting both multi-campaign
    preset files and single flat campaign definitions.
    """
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(cfg_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Check for multi-campaign preset structure
    if "campaigns" in data and isinstance(data["campaigns"], dict):
        campaigns_dict = data["campaigns"]
        chosen_name = campaign_name or data.get("active_campaign")

        if not chosen_name:
            chosen_name = next(iter(campaigns_dict.keys()))

        if chosen_name not in campaigns_dict:
            available = list(campaigns_dict.keys())
            raise ValueError(
                f"Campaign '{chosen_name}' not found in {config_path}. Available campaigns: {available}"
            )

        config = campaigns_dict[chosen_name]
        config.setdefault("campaign_name", chosen_name)
        return config

    # Flat single campaign config fallback
    if campaign_name and data.get("campaign_name") != campaign_name:
        print(f"[!] Warning: Config file has campaign_name='{data.get('campaign_name')}', but '{campaign_name}' was requested. Using config as-is.")

    return data


def clean_workspace(workspace_dir: str = "workspace") -> None:
    """
    Cleans up intermediate files from workspace directory.
    """
    ws = Path(workspace_dir)
    if ws.exists():
        print(f"[*] Cleaning temporary workspace directory: {ws.resolve()}...")
        for item in ws.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                print(f"[!] Could not remove {item.name}: {e}")
        print("[+] Workspace cleaned successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="Local AI Clipping Agent for Clipping.net campaigns using Antigravity CLI (agy)."
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Input video URL (YouTube, Twitch, Kick) or local .mp4 video file path.",
    )
    parser.add_argument(
        "--campaign",
        "-c",
        default=None,
        help="Campaign preset name from config.json (e.g., 'gaming_highlights', 'podcast_clips').",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to campaign configuration JSON file (default: config.json).",
    )
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Whisper model size for transcription (default: base).",
    )
    parser.add_argument(
        "--device",
        default=None,
        choices=["cuda", "cpu"],
        help="Compute device for whisper ('cuda' or 'cpu'). Auto-detected if omitted.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean up temporary files in workspace/ after processing completes.",
    )
    parser.add_argument(
        "--no-watermark",
        action="store_true",
        help="Disable watermark logo overlay even if specified in config.json.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Browser to extract YouTube cookies from ('chrome', 'edge', 'firefox', 'brave', 'opera'). Resolves 'Sign in to confirm you're not a bot'.",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Path to a cookies.txt file for authenticated YouTube downloads.",
    )

    args = parser.parse_args()

    print_banner()
    start_total_time = time.time()

    # Step 0: Load Configuration
    print("=" * 70)
    print("STEP 0: LOADING CONFIGURATION")
    print("=" * 70)
    campaign_rules = load_campaign_config(args.config, args.campaign)
    if args.no_watermark:
        campaign_rules["watermark_path"] = None
    camp_name = campaign_rules.get("campaign_name", "default")
    print(f"[+] Active Campaign: '{camp_name}'")
    print(f"    - Target clips: {campaign_rules.get('num_clips', 5)}")
    print(f"    - Duration range: {campaign_rules.get('min_duration_sec', 20)}s - {campaign_rules.get('max_duration_sec', 55)}s")
    print(f"    - Watermark: {campaign_rules.get('watermark_path')} ({campaign_rules.get('watermark_position', 'none')})")
    print(f"    - Required hashtags: {campaign_rules.get('required_hashtags', [])}")
    print(f"    - Prohibited words: {campaign_rules.get('prohibited_words', [])}")

    workspace_dir = "workspace"
    output_dir = "output"

    # Step 1: Media Ingest & Audio Extraction
    print("\n" + "=" * 70)
    print("STEP 1: MEDIA INGEST & AUDIO EXTRACTION")
    print("=" * 70)
    t0 = time.time()
    cookies_browser = args.cookies_from_browser or campaign_rules.get("cookies_from_browser")
    cookies_file = args.cookies or campaign_rules.get("cookies_file")
    raw_video_path, audio_wav_path = ingest(
        args.input,
        workspace_dir=workspace_dir,
        cookies_from_browser=cookies_browser,
        cookies_file=cookies_file,
    )
    print(f"[+] Ingest completed in {time.time() - t0:.1f}s")
    print(f"    - Raw Video: {raw_video_path}")
    print(f"    - Audio WAV: {audio_wav_path}")

    # Step 2: Speech Transcription (Word-level)
    print("\n" + "=" * 70)
    print("STEP 2: SPEECH TRANSCRIPTION (FASTER-WHISPER)")
    print("=" * 70)
    t0 = time.time()
    segments_path, words_path = transcribe(
        audio_path=audio_wav_path,
        workspace_dir=workspace_dir,
        model_size=args.model,
        device=args.device,
    )
    print(f"[+] Transcription completed in {time.time() - t0:.1f}s")
    print(f"    - Sentence Segments: {segments_path}")
    print(f"    - Word Timestamps: {words_path}")

    # Step 3: AI Viral Hook & Retention Analysis (agy CLI)
    print("\n" + "=" * 70)
    print("STEP 3: AI VIRAL ANALYSIS (ANTIGRAVITY CLI `agy -p`)")
    print("=" * 70)
    t0 = time.time()
    clips = analyze_transcript(
        transcript_segments_path=segments_path,
        campaign_rules=campaign_rules,
        workspace_dir=workspace_dir,
    )
    print(f"[+] AI Analysis completed in {time.time() - t0:.1f}s")

    # Step 4: Video Editing & Subtitle Burning (FFmpeg)
    print("\n" + "=" * 70)
    print("STEP 4: VIDEO EDITING & RENDERING (9:16 + CAPTIONS + LOUDNORM)")
    print("=" * 70)
    t0 = time.time()
    rendered_clips = process_all_clips(
        raw_video_path=raw_video_path,
        clips=clips,
        word_timestamps_path=words_path,
        campaign_rules=campaign_rules,
        workspace_dir=workspace_dir,
        output_dir=output_dir,
    )
    print(f"[+] Rendering completed in {time.time() - t0:.1f}s")

    # Step 5: Upload & Export Reporting
    handle_uploads(
        clips=clips,
        campaign_rules=campaign_rules,
        output_dir=output_dir,
    )

    # Step 6: Cleanup (if requested)
    if args.clean:
        print("\n" + "=" * 70)
        print("STEP 6: CLEANING WORKSPACE (--clean requested)")
        print("=" * 70)
        clean_workspace(workspace_dir)

    # Final Summary
    total_elapsed = time.time() - start_total_time
    print("\n" + "=" * 70)
    print("PIPELINE EXECUTION COMPLETE!")
    print("=" * 70)
    print(f"[+] Total execution time: {total_elapsed:.1f}s")
    print(f"[+] Total clips generated: {len(rendered_clips)}")
    print(f"[+] Output folder: {Path(output_dir).resolve()}")
    print("\nGenerated Clip Packages:")
    for v, m in rendered_clips:
        v_path = Path(v)
        m_path = Path(m)
        v_size_mb = v_path.stat().st_size / (1024 * 1024) if v_path.exists() else 0
        print(f"  * {v_path.name} ({v_size_mb:.1f} MB) + {m_path.name}")

    print("\n[+] Ready for Clipping.net submission! Copy metadata from the .txt files and submit your URLs.")


if __name__ == "__main__":
    main()
