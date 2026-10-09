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

# ลบไฟล์ชั่วคราวหลังส่งให้เบราว์เซอร์ดาวน์โหลดเสร็จ
def cleanup_temp(path: str):
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

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
    """เปิดหน้าแรก index.html อัตโนมัติเมื่อเข้าเว็บ"""
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/info")
def get_video_info(url: str):
    """ดึงข้อมูลพื้นฐานของคลิปสำหรับกรอกอัตโนมัติ"""
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
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
    """ดาวน์โหลดเสียง แปลงเป็น MP3 และฝังแท็ก ID3v2 ตามฟอร์แมต ชื่อเพลง - ศิลปิน.mp3"""
    temp_dir = tempfile.mkdtemp()
    
    clean_title = re.sub(r'[\\/*?:"<>|]', "", req.title or "Track").strip()
    clean_artist = re.sub(r'[\\/*?:"<>|]', "", req.artist or "Artist").strip()
    output_filename = f"{clean_title} - {clean_artist}.mp3"
    raw_audio_path = os.path.join(temp_dir, 'audio.mp3')
    final_output_path = os.path.join(temp_dir, output_filename)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(temp_dir, 'audio.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'quiet': True,
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([req.url])

        if not os.path.exists(raw_audio_path):
            raise HTTPException(status_code=500, detail="การแปลงไฟล์เสียงล้มเหลว")

        os.rename(raw_audio_path, final_output_path)

        # ฝัง Metadata (ID3v2.3)
        try:
            audio = MP3(final_output_path, ID3=ID3)
        except ID3NoHeaderError:
            audio = MP3(final_output_path)
            audio.add_tags()

        # UTF-8 encoding (encoding=3) รองรับภาษาไทยสมบูรณ์
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
    """ดาวน์โหลดวิดีโอ MP4 รวมภาพและเสียง"""
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, '%(title)s.%(ext)s')

    ydl_opts = {
        'format': f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_template,
        'merge_output_format': 'mp4',
        'quiet': True,
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
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