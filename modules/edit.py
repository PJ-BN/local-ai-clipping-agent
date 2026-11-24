"""
modules/edit.py - FFmpeg Video Cutter, 9:16 Cropper, Caption Burner, Watermarker, Audio Normalizer.
Generates dynamic .ass subtitle files, processes 1080x1920 short-form clips,
overlays watermarks at 70% opacity, normalizes audio to TikTok/Reels broadcast standards,
and exports clip metadata.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.ingest import get_ffmpeg_binary


def format_ass_timestamp(seconds: float) -> str:
    """
    Converts seconds (e.g. 12.345) to ASS subtitle timestamp format: H:MM:SS.cs
    """
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_ass_subtitles(
    word_timestamps_path: str,
    start_time: float,
    end_time: float,
    output_ass_path: str,
    font_name: str = "Arial",
    font_size: int = 75,
) -> str:
    """
    Filters words within [start_time, end_time], shifts them to 0-based time,
    groups them into 2-4 word chunks, and writes an .ass subtitle file with bold styling
    centered at ~75% screen height.
    """
    with open(word_timestamps_path, "r", encoding="utf-8") as f:
        all_words = json.load(f)

    # Filter words in range
    clip_words = []
    for w in all_words:
        w_start = float(w.get("start", 0.0))
        w_end = float(w.get("end", 0.0))
        # Keep words that overlap with clip duration
        if w_end > start_time and w_start < end_time:
            # Shift relative to 0.0
            shifted_start = max(0.0, w_start - start_time)
            shifted_end = max(shifted_start + 0.1, min(end_time - start_time, w_end - start_time))
            clip_words.append({
                "word": str(w.get("word", "")).strip(),
                "start": shifted_start,
                "end": shifted_end,
            })

    # Group into 2-4 word chunks (preferring 3 words or punctuation pauses)
    chunks = []
    current_chunk = []

    for w in clip_words:
        if not w["word"]:
            continue

        # Check pause between words
        if current_chunk:
            prev_end = current_chunk[-1]["end"]
            pause = w["start"] - prev_end
            if pause > 0.45 or len(current_chunk) >= 4:
                chunks.append(current_chunk)
                current_chunk = []

        current_chunk.append(w)

        # Break early if strong punctuation
        if w["word"].endswith((".", "!", "?")) and len(current_chunk) >= 2:
            chunks.append(current_chunk)
            current_chunk = []

    if current_chunk:
        chunks.append(current_chunk)

    # ASS Header and Styles
    # MarginV: 480 px from bottom (PlayResY=1920 -> 1920-480 = 1440 = exactly 75% height)
    ass_content = [
        "[Script Info]",
        "Title: Dynamic Viral Captions",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: None",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,50,50,480,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for c in chunks:
        c_start = format_ass_timestamp(c[0]["start"])
        c_end = format_ass_timestamp(c[-1]["end"])
        text = " ".join(item["word"].upper() for item in c)
        ass_content.append(f"Dialogue: 0,{c_start},{c_end},Default,,0,0,0,,{text}")

    Path(output_ass_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_content) + "\n")

    return output_ass_path


def get_watermark_overlay_coords(position: str, padding: int = 20) -> str:
    """
    Returns the FFmpeg overlay x:y coordinates for the given corner position.
    """
    pos = position.lower().strip()
    if pos == "top_left":
        return f"x={padding}:y={padding}"
    elif pos == "bottom_left":
        return f"x={padding}:y=main_h-overlay_h-{padding}"
    elif pos == "bottom_right":
        return f"x=main_w-overlay_w-{padding}:y=main_h-overlay_h-{padding}"
    else:  # top_right default
        return f"x=main_w-overlay_w-{padding}:y={padding}"


def escape_ffmpeg_filter_path(path: str) -> str:
    """
    Escapes colons and backslashes for FFmpeg filter arguments on Windows.
    """
    posix_path = Path(path).resolve().as_posix()
    # On Windows, escape drive letter colon: C:/ -> C\:/
    escaped = posix_path.replace(":", r"\:")
    return escaped


def edit_clip(
    raw_video_path: str,
    clip_info: Dict[str, Any],
    word_timestamps_path: str,
    campaign_rules: Dict[str, Any],
    workspace_dir: str = "workspace",
    output_dir: str = "output",
) -> tuple[str, str]:
    """
    Cuts clip, crops to 9:16 (1080x1920), burns .ass subtitles, overlays watermark,
    normalizes audio with loudnorm, and exports clip MP4 + metadata TXT.

    Returns:
        tuple (output_video_path, metadata_txt_path)
    """
    clip_id = clip_info["clip_id"]
    start_time = float(clip_info["start_time"])
    end_time = float(clip_info["end_time"])
    duration = end_time - start_time

    ws = Path(workspace_dir)
    out_dir = Path(output_dir)
    ws.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate .ass subtitles
    ass_path = str(ws / f"clip_{clip_id}.ass")
    generate_ass_subtitles(word_timestamps_path, start_time, end_time, ass_path)

    # Output paths
    output_video = str(out_dir / f"clip_{clip_id}.mp4")
    output_meta = str(out_dir / f"clip_{clip_id}_metadata.txt")

    # FFmpeg setup
    ffmpeg = get_ffmpeg_binary()
    escaped_ass = escape_ffmpeg_filter_path(ass_path)

    watermark_path = campaign_rules.get("watermark_path")
    watermark_pos = campaign_rules.get("watermark_position", "top_right")
    has_watermark = watermark_path and Path(watermark_path).exists()

    # Build Filtergraph:
    # 1) Crop to 9:16 aspect ratio: crop=ih*9/16:ih
    # 2) Scale to 1080x1920
    # 3) Burn ASS subtitles: ass='...'
    # 4) (Optional) Watermark at 70% opacity: format=rgba,colorchannelmixer=aa=0.7 and overlay
    # 5) Normalize audio: loudnorm=I=-16:TP=-1.5:LRA=11
    if has_watermark:
        escaped_wm_coords = get_watermark_overlay_coords(watermark_pos, padding=20)
        filtergraph = (
            f"[0:v]crop=ih*9/16:ih,scale=1080:1920,ass='{escaped_ass}'[base];"
            f"[1:v]format=rgba,colorchannelmixer=aa=0.7[wm];"
            f"[base][wm]overlay={escaped_wm_coords}:eof_action=repeat[vout];"
            f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-ss", str(start_time),
            "-to", str(end_time),
            "-i", str(raw_video_path),
            "-i", str(watermark_path),
            "-filter_complex", filtergraph,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-crf", "20",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            output_video,
        ]
    else:
        filtergraph = (
            f"[0:v]crop=ih*9/16:ih,scale=1080:1920,ass='{escaped_ass}'[vout];"
            f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-ss", str(start_time),
            "-to", str(end_time),
            "-i", str(raw_video_path),
            "-filter_complex", filtergraph,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-crf", "20",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            output_video,
        ]

    print(f"[*] Rendering Clip #{clip_id} ({duration:.1f}s) -> {output_video}...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"FFmpeg rendering failed for clip {clip_id} (code {res.returncode}):\n{res.stderr}"
        )

    # 4. Save metadata file
    hook_title = clip_info.get("hook_title", f"Clip #{clip_id}")
    caption_desc = clip_info.get("caption_description", "")
    clip_tags = clip_info.get("hashtags", [])
    required_tags = campaign_rules.get("required_hashtags", [])

    # Merge unique tags
    seen = set()
    combined_tags = []
    for t in clip_tags + required_tags:
        clean_tag = str(t).strip()
        if not clean_tag.startswith("#"):
            clean_tag = f"#{clean_tag}"
        if clean_tag.lower() not in seen:
            seen.add(clean_tag.lower())
            combined_tags.append(clean_tag)

    hashtags_str = " ".join(combined_tags)

    metadata_text = f"""==================================================
CLIPPING.NET CAMPAIGN METADATA - CLIP #{clip_id}
==================================================
Title: {hook_title}
Duration: {duration:.1f}s (Start: {start_time}s, End: {end_time}s)

Caption Description:
{caption_desc}

Hashtags:
{hashtags_str}

==================================================
READY-TO-POST COPY-PASTE BLOCK:
==================================================
{hook_title}

{caption_desc}

{hashtags_str}
"""
    with open(output_meta, "w", encoding="utf-8") as f:
        f.write(metadata_text)

    print(f"[+] Clip #{clip_id} exported successfully!")
    print(f"    - Video: {output_video}")
    print(f"    - Metadata: {output_meta}")

    return output_video, output_meta


def process_all_clips(
    raw_video_path: str,
    clips: List[Dict[str, Any]],
    word_timestamps_path: str,
    campaign_rules: Dict[str, Any],
    workspace_dir: str = "workspace",
    output_dir: str = "output",
) -> List[tuple[str, str]]:
    """
    Renders all analyzed clips in sequence.
    """
    results = []
    print(f"[*] Starting rendering pipeline for {len(clips)} clips...")
    for idx, clip_info in enumerate(clips, start=1):
        print(f"\n--- Processing Clip {idx}/{len(clips)} (ID: {clip_info.get('clip_id')}) ---")
        out_v, out_m = edit_clip(
            raw_video_path=raw_video_path,
            clip_info=clip_info,
            word_timestamps_path=word_timestamps_path,
            campaign_rules=campaign_rules,
            workspace_dir=workspace_dir,
            output_dir=output_dir,
        )
        results.append((out_v, out_m))
    return results
