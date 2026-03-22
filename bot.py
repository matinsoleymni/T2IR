import os
import logging
import tempfile
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich.logging import RichHandler
from rich.table import Table
from rich import box

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from dotenv import load_dotenv

load_dotenv()

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
)
# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("googleapiclient").setLevel(logging.WARNING)

logger = logging.getLogger("bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_IDS = set(int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip())
GDRIVE_CLIENT_SECRET_FILE = os.getenv("GDRIVE_CLIENT_SECRET_FILE", "client_secret.json")
GDRIVE_TOKEN_FILE = os.getenv("GDRIVE_TOKEN_FILE", "token.json")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID") or None
LOCAL_API_URL = os.getenv("LOCAL_API_URL") or None  # e.g. http://localhost:8081/bot

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Counters
stats = {"uploaded": 0, "denied": 0, "errors": 0}


def get_drive_service():
    creds = None

    if os.path.exists(GDRIVE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GDRIVE_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            logger.info("OAuth2 token refreshed")
        else:
            raise RuntimeError(
                "Not authenticated. Run:  python auth.py"
            )

        with open(GDRIVE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def upload_to_drive(file_path: str, file_name: str, mime_type: str, file_size: int) -> str:
    service = get_drive_service()

    metadata = {"name": file_name}
    if GDRIVE_FOLDER_ID:
        metadata["parents"] = [GDRIVE_FOLDER_ID]

    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True, chunksize=1024 * 1024)
    request = service.files().create(body=metadata, media_body=media, fields="id")

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]Uploading to Drive[/]"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("upload", total=file_size)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress.update(task, completed=int(status.resumable_progress))
        progress.update(task, completed=file_size)

    file_id = response["id"]

    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    if user_id not in ALLOWED_IDS:
        stats["denied"] += 1
        logger.warning("[red]DENIED[/] user [bold]%s[/] (id=%s)", username, user_id, extra={"markup": True})
        await update.message.reply_text("Access denied.")
        return

    message = update.message

    if message.document:
        tg_file = await message.document.get_file()
        file_name = message.document.file_name or f"file_{tg_file.file_id}"
        mime_type = message.document.mime_type or "application/octet-stream"
        file_size = message.document.file_size or 0
    elif message.photo:
        photo = message.photo[-1]
        tg_file = await photo.get_file()
        file_name = f"photo_{tg_file.file_id}.jpg"
        mime_type = "image/jpeg"
        file_size = photo.file_size or 0
    elif message.video:
        tg_file = await message.video.get_file()
        file_name = message.video.file_name or f"video_{tg_file.file_id}.mp4"
        mime_type = message.video.mime_type or "video/mp4"
        file_size = message.video.file_size or 0
    elif message.audio:
        tg_file = await message.audio.get_file()
        file_name = message.audio.file_name or f"audio_{tg_file.file_id}.mp3"
        mime_type = message.audio.mime_type or "audio/mpeg"
        file_size = message.audio.file_size or 0
    elif message.voice:
        tg_file = await message.voice.get_file()
        file_name = f"voice_{tg_file.file_id}.ogg"
        mime_type = "audio/ogg"
        file_size = message.voice.file_size or 0
    elif message.video_note:
        tg_file = await message.video_note.get_file()
        file_name = f"videonote_{tg_file.file_id}.mp4"
        mime_type = "video/mp4"
        file_size = message.video_note.file_size or 0
    elif message.sticker:
        tg_file = await message.sticker.get_file()
        ext = ".webm" if message.sticker.is_video else ".webp"
        file_name = f"sticker_{tg_file.file_id}{ext}"
        mime_type = "video/webm" if message.sticker.is_video else "image/webp"
        file_size = message.sticker.file_size or 0
    else:
        await update.message.reply_text("Unsupported file type.")
        return

    size_kb = file_size / 1024
    logger.info(
        "[green]FILE[/] from [bold]%s[/] — %s (%.1f KB)",
        username, file_name, size_kb,
        extra={"markup": True},
    )

    status_msg = await update.message.reply_text("⬇️ Downloading...")

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = str(Path(tmpdir) / file_name)

        # Download with progress bar in console
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold yellow]Downloading from Telegram[/]"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("download", total=file_size if file_size else None)
            await tg_file.download_to_drive(local_path)
            progress.update(task, completed=file_size)

        actual_size = Path(local_path).stat().st_size
        logger.info("  Downloaded: %s (%.1f KB)", file_name, actual_size / 1024)

        await status_msg.edit_text("☁️ Uploading to Google Drive...")

        try:
            link = upload_to_drive(local_path, file_name, mime_type, actual_size)
            file_id = link.split("/d/")[1].split("/")[0]
            stats["uploaded"] += 1
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 Delete from Drive", callback_data=f"delete:{file_id}")
            ]])
            await status_msg.edit_text(f"✅ Done!\n\n🔗 {link}", reply_markup=keyboard)
            logger.info(
                "[bold green]✓ UPLOADED[/] %s → %s",
                file_name, link,
                extra={"markup": True},
            )
            _print_stats()
        except Exception as e:
            stats["errors"] += 1
            logger.error("[red]✗ UPLOAD FAILED[/] %s: %s", file_name, e, extra={"markup": True})
            await status_msg.edit_text(f"❌ Upload failed: {e}")


async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ALLOWED_IDS:
        await query.answer("Access denied.", show_alert=True)
        return

    _, file_id = query.data.split(":", 1)
    try:
        service = get_drive_service()
        service.files().delete(fileId=file_id).execute()
        await query.edit_message_text(query.message.text + "\n\n🗑 Deleted from Drive.")
        logger.info("[yellow]DELETED[/] Drive file %s", file_id, extra={"markup": True})
    except Exception as e:
        logger.error("[red]DELETE FAILED[/] %s: %s", file_id, e, extra={"markup": True})
        await query.answer(f"Delete failed: {e}", show_alert=True)


def _print_stats():
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row("Uploaded", f"[green]{stats['uploaded']}[/]")
    table.add_row("Denied", f"[yellow]{stats['denied']}[/]")
    table.add_row("Errors", f"[red]{stats['errors']}[/]")
    console.print(table)


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN is not set in .env")
    if not ALLOWED_IDS:
        raise ValueError("ALLOWED_IDS is not set in .env")

    file_limit = "2 GB (local API)" if LOCAL_API_URL else "20 MB (official API)"
    console.print(Panel.fit(
        f"[bold cyan]Telegram → Google Drive Bot[/]\n\n"
        f"  [dim]Started:[/]      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"  [dim]Allowed IDs:[/]  {', '.join(str(i) for i in ALLOWED_IDS)}\n"
        f"  [dim]Auth token:[/]   {GDRIVE_TOKEN_FILE}\n"
        f"  [dim]Drive folder:[/] {GDRIVE_FOLDER_ID or 'My Drive (root)'}\n"
        f"  [dim]File limit:[/]   {file_limit}",
        border_style="cyan",
        title="[bold]BOT STARTED[/]",
    ))

    builder = Application.builder().token(TELEGRAM_TOKEN)
    if LOCAL_API_URL:
        builder = builder.base_url(LOCAL_API_URL).local_mode(True)
    app = builder.build()

    file_filter = (
        filters.Document.ALL
        | filters.PHOTO
        | filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
        | filters.VIDEO_NOTE
        | filters.Sticker.ALL
    )
    app.add_handler(MessageHandler(file_filter, handle_file))
    app.add_handler(CallbackQueryHandler(handle_delete_callback, pattern="^delete:"))

    logger.info("Polling for updates... (Ctrl+C to stop)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
