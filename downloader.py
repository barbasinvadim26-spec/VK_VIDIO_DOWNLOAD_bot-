
import os
import uuid
import yt_dlp

# =====================================
# НАСТРОЙКИ
# =====================================

DOWNLOAD_DIR = (
    "/storage/emulated/0/VK_Vidio_Bot/downloads"
)

WATERMARK_TEXT = "Разработано в @VK_VIDIO_DOWNLOAD_bot"


# =====================================
# СКАЧИВАНИЕ ВИДЕО
# =====================================

def download_video(
    url,
    user_id,
    quality=720,
    premium=False,
    mode=None
):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    if mode is not None:
        premium = (mode == "premium")

    task_id = uuid.uuid4().hex

    output_template = os.path.join(
        DOWNLOAD_DIR,
        f"{user_id}_{task_id}.%(ext)s"
    )

    final_file = os.path.join(
        DOWNLOAD_DIR,
        f"{user_id}_{task_id}.%(ext)s"
    )

    # =================================
    # ВЫБОР КАЧЕСТВА
    # =================================

    if premium:
        quality = int(quality)

        if quality not in (720, 1080, 1440, 2160):
            raise ValueError("Недопустимое качество!")

        format_selector = (
            f"bestvideo[height<={quality}][fps<=60]+bestaudio/"
            f"best[height<={quality}][fps<=60]/best"
        )

    else:
        format_selector = (
            "best[height<=720][fps<=30]/"
            "best[height<=720]/"
            "best"
        )

    # =================================
    # НАСТРОЙКИ YT-DLP
    # =================================

    ydl_opts = {
        "format": format_selector,
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 60,

        # Не использовать FFmpeg
        "postprocessors": [],
        "fixup": "never",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                url,
                download=True
            )

            downloaded_file = (
                info.get("filepath")
                or info.get("_filename")
                or ydl.prepare_filename(info)
            )

        if not os.path.isfile(downloaded_file):
            raise RuntimeError(
                "Скачанный видеофайл не найден!"
            )

        if os.path.getsize(downloaded_file) == 0:
            raise RuntimeError(
                "Скачанный файл пустой!"
            )

        return downloaded_file

    except Exception:
        raise

