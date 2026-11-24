"""
modules/analyze.py - Viral Hook Selection & Metadata Generation via agy CLI.
Uses Antigravity CLI in headless mode (`agy -p "..."`) via Python subprocess
to discover high-retention short-form clips based on campaign rules.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_agy_binary() -> str:
    """
    Locates the agy executable.
    """
    cmd = shutil.which("agy")
    if cmd:
        return cmd

    # Standard default Windows location
    local_agy = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    if local_agy.exists():
        return str(local_agy)

    user_home_agy = Path.home() / ".gemini" / "antigravity" / "bin" / "agy.exe"
    if user_home_agy.exists():
        return str(user_home_agy)

    return "agy"


def extract_json_from_text(text: str) -> Optional[List[Dict[str, Any]]]:
    """
    Attempts multiple parsing strategies to extract a valid JSON array from LLM output.
    """
    cleaned = text.strip()

    # 1. Direct JSON parse
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "clips" in data and isinstance(data["clips"], list):
            return data["clips"]
    except Exception:
        pass

    # 2. Extract from markdown code fence ```json ... ``` or ``` ... ```
    fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(fence_pattern, cleaned, re.IGNORECASE)
    if match:
        content = match.group(1).strip()
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "clips" in data and isinstance(data["clips"], list):
                return data["clips"]
        except Exception:
            pass

    # 3. Bracket matching: Find outermost '[' and ']'
    start_idx = cleaned.find("[")
    end_idx = cleaned.rfind("]")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        bracketed = cleaned[start_idx : end_idx + 1].strip()
        try:
            data = json.loads(bracketed)
            if isinstance(data, list):
                return data
        except Exception:
            pass

    return None


def construct_prompt(
    segments: List[Dict[str, Any]],
    campaign_rules: Dict[str, Any],
) -> str:
    """
    Builds the prompt instructing agy to act as a viral short-form retention editor.
    """
    num_clips = campaign_rules.get("num_clips", 5)
    min_dur = campaign_rules.get("min_duration_sec", 20)
    max_dur = campaign_rules.get("max_duration_sec", 55)
    prohibited_words = campaign_rules.get("prohibited_words", [])

    transcript_snippet = json.dumps(segments, ensure_ascii=False)

    prohibited_clause = ""
    if prohibited_words:
        pw_str = ", ".join(f'"{w}"' for w in prohibited_words)
        prohibited_clause = (
            f"\nSTRICT NEGATIVE CONSTRAINT: Strictly avoid any clips containing or referring to these prohibited words: "
            f"[{pw_str}]. Do NOT use these words in hook_title, caption_description, or hashtags."
        )

    prompt = f"""You are an elite short-form retention editor specializing in viral TikToks, YouTube Shorts, and Instagram Reels for Clipping.net campaigns.

TASK:
Analyze the provided speech transcript with timestamps and select the top {num_clips} viral clip moments.

RULES & CONSTRAINTS:
1. Target clip count: {num_clips} clips.
2. Clip duration: Each clip MUST have a duration between {min_dur} seconds and {max_dur} seconds (duration = end_time - start_time).
3. Viral Hook: Each clip MUST start on a strong hook (shocking claim, punchline, hilarious conflict, high-stakes question, or intriguing statement) that captures attention within the first 2 seconds.
4. Clean Ending: Each clip MUST conclude on a complete thought, satisfying punchline, or clear resolution. Do NOT cut off mid-sentence or mid-word.
5. Exact Timestamps: Use exact timestamps from the transcript segments for start_time and end_time.{prohibited_clause}
6. Hook Title: Create a high-converting, punchy title (3-7 words) without clickbait lies.
7. Caption Description: One engaging sentence summarizing the clip with high curiosity.
8. Hashtags: Provide 2-4 niche-specific viral hashtags starting with '#' (e.g., ["#shorts", "#viral", "#gaming"]).

OUTPUT FORMAT:
Output ONLY a valid raw JSON array of objects. Do NOT use markdown code blocks (no ```json). Do NOT output introductory text, explanations, or notes.

Exact JSON schema:
[
  {{
    "clip_id": 1,
    "start_time": 124.5,
    "end_time": 162.0,
    "hook_title": "Catchy 5-word title",
    "caption_description": "Engaging 1-sentence description",
    "hashtags": ["#tag1", "#tag2"]
  }}
]

TRANSCRIPT SEGMENTS:
{transcript_snippet}
"""
    return prompt.strip()


def validate_clips(
    clips: List[Dict[str, Any]],
    campaign_rules: Dict[str, Any],
    max_audio_duration: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Validates and normalizes parsed clip objects against campaign rules.
    """
    min_dur = campaign_rules.get("min_duration_sec", 20)
    max_dur = campaign_rules.get("max_duration_sec", 55)
    prohibited_words = [w.lower() for w in campaign_rules.get("prohibited_words", [])]

    validated = []
    for idx, c in enumerate(clips, start=1):
        try:
            start_time = float(c.get("start_time", 0.0))
            end_time = float(c.get("end_time", 0.0))
        except (ValueError, TypeError):
            continue

        if start_time < 0:
            start_time = 0.0

        duration = end_time - start_time

        # Allow slight flexibility (e.g., +/- 2.5s) if whisper boundaries are slightly off
        if duration < (min_dur - 3.0) or duration > (max_dur + 5.0) or start_time >= end_time:
            print(f"[!] Warning: Clip {idx} duration {duration:.1f}s outside bounds ({min_dur}-{max_dur}s). Adjusting or skipping.")
            if duration < min_dur:
                end_time = start_time + min_dur
            elif duration > max_dur:
                end_time = start_time + max_dur

        if max_audio_duration and end_time > max_audio_duration:
            end_time = max_audio_duration

        hook_title = str(c.get("hook_title", f"Viral Moment {idx}")).strip()
        caption = str(c.get("caption_description", "")).strip()

        # Check prohibited words
        text_to_check = f"{hook_title} {caption}".lower()
        if any(pw in text_to_check for pw in prohibited_words if pw):
            print(f"[!] Warning: Clip {idx} violates prohibited words filter. Skipping.")
            continue

        raw_tags = c.get("hashtags", [])
        clean_tags = []
        if isinstance(raw_tags, list):
            for t in raw_tags:
                tag_str = str(t).strip()
                if not tag_str.startswith("#"):
                    tag_str = f"#{tag_str}"
                clean_tags.append(tag_str)
        elif isinstance(raw_tags, str):
            for t in raw_tags.split():
                clean_tags.append(t if t.startswith("#") else f"#{t}")

        validated.append({
            "clip_id": int(c.get("clip_id", idx)),
            "start_time": round(start_time, 2),
            "end_time": round(end_time, 2),
            "duration": round(end_time - start_time, 2),
            "hook_title": hook_title,
            "caption_description": caption,
            "hashtags": clean_tags,
        })

    return validated


def analyze_transcript(
    transcript_segments_path: str,
    campaign_rules: Dict[str, Any],
    workspace_dir: str = "workspace",
) -> List[Dict[str, Any]]:
    """
    Executes agy in headless mode to select the best viral clips and generate metadata.

    Args:
        transcript_segments_path: Path to transcript_segments.json
        campaign_rules: Dictionary containing campaign constraints and rules
        workspace_dir: Directory where analyzed_clips.json will be saved

    Returns:
        List of validated clip dictionaries
    """
    with open(transcript_segments_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    if not segments:
        raise ValueError(f"Transcript segments file is empty: {transcript_segments_path}")

    max_audio_duration = max(s.get("end", 0.0) for s in segments)
    print(f"[*] Total transcribed duration: {max_audio_duration:.1f}s across {len(segments)} segments.")

    prompt = construct_prompt(segments, campaign_rules)
    agy_bin = get_agy_binary()

    print(f"[*] Calling Antigravity CLI ({agy_bin} -p) in headless mode for AI hook selection...")
    res = subprocess.run(
        [agy_bin, "-p", prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
    )

    if res.returncode != 0:
        raise RuntimeError(f"agy execution failed (code {res.returncode}):\n{res.stderr}")

    raw_output = res.stdout.strip()
    parsed_clips = extract_json_from_text(raw_output)

    # Fallback retry if initial parsing failed
    if not parsed_clips:
        print("[!] Warning: Initial agy output could not be parsed as JSON. Retrying with explicit cleanup prompt...")
        repair_prompt = (
            f"Convert the following response into a clean, raw JSON array of objects without any markdown, code blocks, or explanations:\n\n{raw_output}"
        )
        retry_res = subprocess.run(
            [agy_bin, "-p", repair_prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
        )
        if retry_res.returncode == 0:
            parsed_clips = extract_json_from_text(retry_res.stdout.strip())

    if not parsed_clips:
        raise ValueError(
            f"Failed to parse valid clip JSON from agy response.\nRaw Output:\n{raw_output}"
        )

    validated = validate_clips(parsed_clips, campaign_rules, max_audio_duration)

    if not validated:
        raise ValueError("No valid clips remained after applying duration and prohibited word rules.")

    print(f"[+] agy successfully selected {len(validated)} viral clips!")
    for clip in validated:
        print(f"    - Clip #{clip['clip_id']}: [{clip['start_time']}s -> {clip['end_time']}s | {clip['duration']}s] \"{clip['hook_title']}\"")

    ws = Path(workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    out_file = ws / "analyzed_clips.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(validated, f, indent=2, ensure_ascii=False)

    return validated
