"""
modules/upload.py - YouTube Data API v3 Draft Uploader & Local Export Reporter.
Uploads generated clips to YouTube as PRIVATE drafts for human review
before publishing and submitting to Clipping.net.
Skips cleanly if credentials are missing or auto-upload is disabled.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Google API imports
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False


YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]


def get_youtube_service(
    client_secrets_path: str = "client_secrets.json",
    token_path: str = "token.json",
):
    """
    Authenticates with YouTube Data API v3 via OAuth2 and returns the service resource.
    Caches tokens in token_path for non-interactive subsequent runs.
    """
    if not GOOGLE_API_AVAILABLE:
        raise RuntimeError("Google API client libraries are not installed.")

    creds = None
    if Path(token_path).exists():
        try:
            creds = Credentials.from_authorized_user_file(token_path, YOUTUBE_UPLOAD_SCOPE)
        except Exception as e:
            print(f"[!] Warning: Failed to load cached token from {token_path}: {e}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[*] Refreshing expired YouTube OAuth token...")
            creds.refresh(Request())
        else:
            if not Path(client_secrets_path).exists():
                raise FileNotFoundError(f"Missing OAuth client secrets file: {client_secrets_path}")
            print("[*] Launching browser OAuth flow for YouTube API authorization...")
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, YOUTUBE_UPLOAD_SCOPE)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print(f"[+] Saved YouTube OAuth token to {token_path}")

    return build("youtube", "v3", credentials=creds)


def upload_single_draft(
    youtube,
    video_path: str,
    title: str,
    description: str,
    tags: List[str],
    category_id: str = "20",  # 20 = Gaming, 22 = People & Blogs
) -> str:
    """
    Uploads a single video to YouTube with privacyStatus set strictly to 'private' (Draft).
    """
    clean_tags = [t.lstrip("#") for t in tags if t]

    body = {
        "snippet": {
            "title": title[:100],  # YouTube max 100 characters
            "description": description[:5000],
            "tags": clean_tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": "private",  # CRITICAL: Always private draft for human review
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        chunksize=1024 * 1024 * 4,  # 4MB chunks
        resumable=True,
        mimetype="video/mp4",
    )

    print(f"[*] Uploading '{Path(video_path).name}' as private YouTube draft...")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            print(f"    - Upload progress: {progress}%", end="\r")

    video_id = response.get("id")
    print(f"\n[+] Draft upload complete! Video ID: {video_id}")
    return video_id


def handle_uploads(
    clips: List[Dict[str, Any]],
    campaign_rules: Dict[str, Any],
    output_dir: str = "output",
    client_secrets_path: str = "client_secrets.json",
    token_path: str = "token.json",
) -> None:
    """
    Checks upload configuration and uploads drafts if enabled and configured;
    otherwise prints local file summary for manual posting.
    """
    auto_upload = campaign_rules.get("auto_upload_youtube_draft", False)
    has_secrets = Path(client_secrets_path).exists()
    out_path = Path(output_dir).resolve()

    print("\n" + "=" * 60)
    print("STEP 5: UPLOAD & EXPORT SUMMARY")
    print("=" * 60)

    if not auto_upload:
        print("[i] 'auto_upload_youtube_draft' is false in config.json.")
        print(f"[+] All final clips and metadata are ready in local export directory:\n    -> {out_path}")
        print("[+] You can manually post these to TikTok, Instagram Reels, and YouTube Shorts.")
        return

    if not has_secrets:
        print(f"[i] '{client_secrets_path}' not found in workspace.")
        print("    To enable YouTube Draft uploads, place your Google Cloud OAuth2 client_secrets.json in this directory.")
        print(f"[+] Final clips and metadata are available locally for manual posting:\n    -> {out_path}")
        return

    try:
        youtube = get_youtube_service(client_secrets_path, token_path)
    except Exception as e:
        print(f"[!] Warning: Failed to initialize YouTube API client: {e}")
        print(f"[+] Final clips remain safely stored at: {out_path}")
        return

    required_tags = campaign_rules.get("required_hashtags", [])

    for clip in clips:
        clip_id = clip.get("clip_id")
        video_file = str(Path(output_dir) / f"clip_{clip_id}.mp4")

        if not Path(video_file).exists():
            print(f"[!] Video file missing for clip {clip_id}: {video_file}")
            continue

        title = clip.get("hook_title", f"Clip #{clip_id}")
        caption = clip.get("caption_description", "")
        clip_tags = clip.get("hashtags", [])
        combined_tags = list(dict.fromkeys(clip_tags + required_tags))
        hashtags_str = " ".join(combined_tags)

        full_desc = f"{caption}\n\n{hashtags_str}\n\nGenerated for Clipping.net campaign: {campaign_rules.get('campaign_name', 'default')}"

        try:
            vid_id = upload_single_draft(
                youtube=youtube,
                video_path=video_file,
                title=title,
                description=full_desc,
                tags=combined_tags,
            )
            studio_link = f"https://studio.youtube.com/video/{vid_id}/edit"
            print(f"    -> YouTube Studio Review Link: {studio_link}")
        except Exception as e:
            print(f"[!] Upload failed for clip {clip_id}: {e}")

    print("\n[+] Upload process finished. Review private drafts in YouTube Studio before publishing!")
