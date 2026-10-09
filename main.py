import os
import re
import urllib.parse
import tempfile
import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import yt_dlp
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCOM, TDRC, APIC, ID3NoHeaderError
from mutagen.mp3 import MP3

app = FastAPI(title="YouTube Web Studio")

def cleanup_temp(path: str):
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

def sanitize_youtube_url(raw_url: str) -> str:
    """ตัดพารามิเตอร์ list, index และ radio ออกทั้งหมด ให้เหลือเฉพาะตัวคลิปเดี่ยว"""
    # กรณีเป็น URL รูปแบบ youtu.be/<id>
    short_match = re.search(r'youtu\.be/([a-zA-Z0-9_-]+)', raw_url)
    if short_match:
        return f"https://www.youtube.com/watch?v={short_match.group(1)}"
    
    # กรณีเป็น URL รูปแบบ youtube.com/watch?v=<id>
    watch_match = re.search(r'v=([a-zA-Z0-9_-]+)', raw_url)
    if watch_match:
        return f"https://www.youtube.com/watch?v={watch_match.group(1)}"
        
    return raw_url

class MP3Request(BaseModel):
    url: str
    title: str = ""
    artist: str = ""
    album: str = ""
    composer: str = ""
    year: str = ""
    thumbnail_url: str = ""

@app.get("/", response_class=HTMLResponse)
def serve_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/info")
def get_video_info(url: str):
    clean_url = sanitize_youtube_url(url)
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'noplaylist': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web']
            },
            'youtubetab': {
                'skip': ['authcheck']
            }
        },
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            return {
                "id": info.get("id"),
                "title": info.get("title", ""),
                "thumbnail": info.get("thumbnail", ""),
                "uploader": info.get("uploader", ""),
                "duration": info.get("duration", 0),
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail="ไม่สามารถดึงข้อมูลคลิปได้: " + str(e))

@app.post("/api/download/mp3")
def download_mp3(req: MP3Request, background_tasks: BackgroundTasks):
    clean_url = sanitize_youtube_url(req.url)
    temp_dir = tempfile.mkdtemp()
    
    clean_title = re.sub(r'[\\/*?:"<>|]', "", req.title or "Track").strip()
    clean_artist = re.sub(r'[\\/*?:"<>|]', "", req.artist or "Artist").strip()
    output_filename = f"{clean_title} - {clean_artist}.mp3"
    raw_audio_path = os.path.join(temp_dir, 'audio.mp3')
    final_output_path = os.path.join(temp_dir, output_filename)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(temp_dir, 'audio.%(ext)s'),
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'quiet': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web']
            }
        },
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([clean_url])

        if not os.path.exists(raw_audio_path):
            raise HTTPException(status_code=500, detail="การแปลงไฟล์เสียงล้มเหลว")

        os.rename(raw_audio_path, final_output_path)

        try:
            audio = MP3(final_output_path, ID3=ID3)
        except ID3NoHeaderError:
            audio = MP3(final_output_path)
            audio.add_tags()

        if req.title:
            audio.tags.add(TIT2(encoding=3, text=req.title))
        if req.artist:
            audio.tags.add(TPE1(encoding=3, text=req.artist))
        if req.album:
            audio.tags.add(TALB(encoding=3, text=req.album))
        if req.composer:
            audio.tags.add(TCOM(encoding=3, text=req.composer))
        if req.year:
            audio.tags.add(TDRC(encoding=3, text=req.year))

        if req.thumbnail_url:
            try:
                img_res = requests.get(req.thumbnail_url, timeout=8)
                if img_res.status_code == 200:
                    audio.tags.add(APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=img_res.content
                    ))
            except Exception:
                pass

        audio.save(v2_version=3)
        background_tasks.add_task(cleanup_temp, final_output_path)

        encoded_filename = urllib.parse.quote(output_filename)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }

        return FileResponse(
            path=final_output_path,
            media_type="audio/mpeg",
            headers=headers
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/video")
def download_video(url: str, quality: str = "720", background_tasks: BackgroundTasks = None):
    clean_url = sanitize_youtube_url(url)
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, '%(title)s.%(ext)s')

 ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'noplaylist': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'mweb']  # iOS Client ทนต่อการบล็อก IP บน Cloud ได้ดีที่สุด
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            video_filename = ydl.prepare_filename(info)
            if not video_filename.endswith(".mp4"):
                video_filename = os.path.splitext(video_filename)[0] + ".mp4"

        clean_name = os.path.basename(video_filename)
        encoded_name = urllib.parse.quote(clean_name)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
        }

        if background_tasks:
            background_tasks.add_task(cleanup_temp, video_filename)

        return FileResponse(
            path=video_filename,
            media_type="video/mp4",
            headers=headers
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
