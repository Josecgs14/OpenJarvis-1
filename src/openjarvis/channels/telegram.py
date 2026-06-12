"""TelegramChannel — native Telegram Bot API adapter."""

from __future__ import annotations

import logging
import os
import textwrap
import threading
from typing import Any, Dict, List, Optional

from openjarvis.channels._stubs import (
    BaseChannel,
    ChannelHandler,
    ChannelMessage,
    ChannelStatus,
)
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import ChannelRegistry

logger = logging.getLogger(__name__)

# Voice replies are skipped for responses longer than this — TTS on very
# long text is slow/expensive and a text reply remains available either way.
_MAX_VOICE_REPLY_CHARS = 2000


@ChannelRegistry.register("telegram")
class TelegramChannel(BaseChannel):
    """Native Telegram channel adapter using the Bot API.

    Parameters
    ----------
    bot_token:
        Telegram Bot API token.  Falls back to ``TELEGRAM_BOT_TOKEN`` env var.
    allowed_chat_ids:
        Comma-separated list of chat IDs allowed to interact.
    parse_mode:
        Message parse mode (``Markdown``, ``HTML``, etc.).
    bus:
        Optional event bus for publishing channel events.
    speech_backend:
        Optional speech-to-text backend used to transcribe incoming voice
        messages. If not provided, one is auto-discovered on first use via
        ``openjarvis.speech._discovery.get_speech_backend``.
    tts_backend:
        Optional text-to-speech backend used for voice replies. If not
        provided, one is auto-discovered on first use via
        ``openjarvis.speech._discovery.get_tts_backend``.
    voice_replies:
        If True, reply with a synthesized voice note (in addition to text)
        whenever the user's last message to a chat was itself a voice
        message.
    """

    channel_id = "telegram"

    def __init__(
        self,
        bot_token: str = "",
        *,
        allowed_chat_ids: str = "",
        parse_mode: str = "Markdown",
        bus: Optional[EventBus] = None,
        speech_backend: Any = None,
        tts_backend: Any = None,
        voice_replies: bool = False,
    ) -> None:
        self._token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._allowed_chat_ids = allowed_chat_ids
        self._parse_mode = parse_mode
        self._bus = bus
        self._handlers: List[ChannelHandler] = []
        self._status = ChannelStatus.DISCONNECTED
        self._listener_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._voice_replies = voice_replies
        self._speech_backend = speech_backend
        self._speech_backend_resolved = speech_backend is not None
        self._tts_backend = tts_backend
        self._tts_backend_resolved = tts_backend is not None
        self._last_input_type: Dict[str, str] = {}

    # -- connection lifecycle ---------------------------------------------------

    def connect(self) -> None:
        """Start listening for incoming messages via long polling."""
        if not self._token:
            logger.warning("No Telegram bot token configured")
            self._status = ChannelStatus.ERROR
            return

        self._stop_event.clear()
        self._status = ChannelStatus.CONNECTING

        try:
            from telegram.ext import ApplicationBuilder  # noqa: F401

            self._listener_thread = threading.Thread(
                target=self._poll_loop,
                daemon=True,
            )
            self._listener_thread.start()
            self._status = ChannelStatus.CONNECTED
            logger.info("Telegram channel connected (long polling)")
        except ImportError:
            # python-telegram-bot not installed — send-only mode
            logger.info(
                "python-telegram-bot not installed; send-only mode",
            )
            self._status = ChannelStatus.CONNECTED

    def disconnect(self) -> None:
        """Stop the listener thread."""
        self._stop_event.set()
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=5.0)
            self._listener_thread = None
        self._status = ChannelStatus.DISCONNECTED

    # -- send / receive --------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        """Send a message to a Telegram chat via the Bot API."""
        if not self._token:
            logger.warning("Cannot send: no Telegram bot token")
            return False

        try:
            import httpx

            _TELEGRAM_MAX_LEN = 4096
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            chat_id = conversation_id or channel
            chunks = textwrap.wrap(
                content,
                width=_TELEGRAM_MAX_LEN,
                break_long_words=True,
                replace_whitespace=False,
            )
            for chunk in chunks:
                payload: Dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                }
                if self._parse_mode:
                    payload["parse_mode"] = self._parse_mode

                resp = httpx.post(url, json=payload, timeout=10.0)
                if resp.status_code >= 300:
                    logger.warning(
                        "Telegram API returned status %d: %s",
                        resp.status_code,
                        resp.text,
                    )
                    return False
            self._publish_sent(channel, content, conversation_id)

            if (
                self._voice_replies
                and self._last_input_type.get(str(chat_id)) == "voice"
                and len(content) <= _MAX_VOICE_REPLY_CHARS
            ):
                self._send_voice_reply(chat_id, content)

            return True
        except Exception:
            logger.debug("Telegram send failed", exc_info=True)
            return False

    def status(self) -> ChannelStatus:
        """Return the current connection status."""
        return self._status

    def list_channels(self) -> List[str]:
        """Return available channel identifiers."""
        return ["telegram"]

    def on_message(self, handler: ChannelHandler) -> None:
        """Register a callback for incoming messages."""
        self._handlers.append(handler)

    # -- internal helpers -------------------------------------------------------

    def _poll_loop(self) -> None:
        """Long-poll for updates using python-telegram-bot."""
        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters

            app = ApplicationBuilder().token(self._token).build()

            def _is_allowed(conversation_id: str) -> bool:
                if not self._allowed_chat_ids:
                    return True
                _allowed = {
                    cid.strip()
                    for cid in self._allowed_chat_ids.split(",")
                    if cid.strip()
                }
                return conversation_id in _allowed

            def _dispatch(cm: ChannelMessage) -> None:
                for handler in self._handlers:
                    try:
                        handler(cm)
                    except Exception:
                        logger.exception("Telegram handler error")
                if self._bus is not None:
                    self._bus.publish(
                        EventType.CHANNEL_MESSAGE_RECEIVED,
                        {
                            "channel": cm.channel,
                            "sender": cm.sender,
                            "content": cm.content,
                            "message_id": cm.message_id,
                            "metadata": cm.metadata,
                        },
                    )

            def _handle_msg(update, context):
                msg = update.message
                if msg is None:
                    return
                cm = ChannelMessage(
                    channel="telegram",
                    sender=str(msg.from_user.id) if msg.from_user else "",
                    content=msg.text or "",
                    message_id=str(msg.message_id),
                    conversation_id=str(msg.chat.id),
                )
                if not _is_allowed(cm.conversation_id):
                    logger.debug(
                        "Ignoring message from unlisted chat %s",
                        cm.conversation_id,
                    )
                    return
                self._last_input_type[cm.conversation_id] = "text"
                _dispatch(cm)

            def _handle_voice(update, context):
                msg = update.message
                if msg is None:
                    return
                voice = msg.voice or msg.audio
                if voice is None:
                    return

                chat_id = str(msg.chat.id)
                if not _is_allowed(chat_id):
                    logger.debug(
                        "Ignoring voice message from unlisted chat %s", chat_id
                    )
                    return

                audio_bytes = self._download_telegram_file(voice.file_id)
                if audio_bytes is None:
                    self.send(
                        chat_id,
                        "Sorry, I couldn't download that voice message.",
                        conversation_id=chat_id,
                    )
                    return

                mime = getattr(voice, "mime_type", "") or ""
                fmt = mime.split("/", 1)[-1].split(";")[0] if "/" in mime else "ogg"

                text = self._transcribe_audio(audio_bytes, fmt=fmt or "ogg")
                if not text:
                    self.send(
                        chat_id,
                        "I couldn't transcribe that voice message. Make sure a"
                        " speech-to-text backend is configured (e.g. pip install"
                        ' "openjarvis[speech]"), or send a text message instead.',
                        conversation_id=chat_id,
                    )
                    return

                self._last_input_type[chat_id] = "voice"
                cm = ChannelMessage(
                    channel="telegram",
                    sender=str(msg.from_user.id) if msg.from_user else "",
                    content=text,
                    message_id=str(msg.message_id),
                    conversation_id=chat_id,
                    metadata={"input_type": "voice"},
                )
                _dispatch(cm)

            app.add_handler(MessageHandler(filters.TEXT, _handle_msg))
            app.add_handler(
                MessageHandler(filters.VOICE | filters.AUDIO, _handle_voice)
            )
            app.run_polling(stop_signals=None, drop_pending_updates=True)
        except Exception:
            logger.debug("Telegram poll loop error", exc_info=True)
            self._status = ChannelStatus.ERROR

    # -- voice helpers -----------------------------------------------------

    def _resolve_speech_backend(self) -> Any:
        """Lazily resolve (and cache) a speech-to-text backend."""
        if not self._speech_backend_resolved:
            try:
                from openjarvis.core.config import load_config
                from openjarvis.speech._discovery import get_speech_backend

                self._speech_backend = get_speech_backend(load_config())
            except Exception:
                logger.debug("Speech backend discovery failed", exc_info=True)
                self._speech_backend = None
            self._speech_backend_resolved = True
        return self._speech_backend

    def _resolve_tts_backend(self) -> Any:
        """Lazily resolve (and cache) a text-to-speech backend."""
        if not self._tts_backend_resolved:
            try:
                from openjarvis.core.config import load_config
                from openjarvis.speech._discovery import get_tts_backend

                self._tts_backend = get_tts_backend(load_config())
            except Exception:
                logger.debug("TTS backend discovery failed", exc_info=True)
                self._tts_backend = None
            self._tts_backend_resolved = True
        return self._tts_backend

    def _download_telegram_file(self, file_id: str) -> Optional[bytes]:
        """Download a file from Telegram's Bot API by file_id."""
        import httpx

        try:
            meta_url = f"https://api.telegram.org/bot{self._token}/getFile"
            resp = httpx.get(meta_url, params={"file_id": file_id}, timeout=30.0)
            resp.raise_for_status()
            file_path = resp.json()["result"]["file_path"]

            file_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
            resp = httpx.get(file_url, timeout=60.0)
            resp.raise_for_status()
            return resp.content
        except Exception:
            logger.debug("Telegram file download failed", exc_info=True)
            return None

    def _transcribe_audio(self, audio: bytes, *, fmt: str = "ogg") -> Optional[str]:
        """Transcribe voice note bytes to text using the configured STT backend."""
        backend = self._resolve_speech_backend()
        if backend is None:
            return None
        try:
            result = backend.transcribe(audio, format=fmt)
            text = result.text.strip()
            return text or None
        except Exception:
            logger.debug("Voice transcription failed", exc_info=True)
            return None

    def _send_voice_reply(self, chat_id: str, text: str) -> bool:
        """Synthesize *text* and send it to *chat_id* as a Telegram audio note."""
        backend = self._resolve_tts_backend()
        if backend is None:
            return False

        import httpx

        try:
            result = backend.synthesize(text)
            audio_format = result.format or "mp3"
            url = f"https://api.telegram.org/bot{self._token}/sendAudio"
            resp = httpx.post(
                url,
                data={"chat_id": chat_id},
                files={
                    "audio": (
                        f"reply.{audio_format}",
                        result.audio,
                        f"audio/{audio_format}",
                    )
                },
                timeout=60.0,
            )
            if resp.status_code >= 300:
                logger.warning(
                    "Telegram sendAudio returned status %d: %s",
                    resp.status_code,
                    resp.text,
                )
                return False
            return True
        except Exception:
            logger.debug("Voice reply send failed", exc_info=True)
            return False

    def _publish_sent(self, channel: str, content: str, conversation_id: str) -> None:
        """Publish a CHANNEL_MESSAGE_SENT event on the bus."""
        if self._bus is not None:
            self._bus.publish(
                EventType.CHANNEL_MESSAGE_SENT,
                {
                    "channel": channel,
                    "content": content,
                    "conversation_id": conversation_id,
                },
            )


__all__ = ["TelegramChannel"]
