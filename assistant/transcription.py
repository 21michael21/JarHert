from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import tempfile
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAITranscriber:
    api_key: str
    model: str = "gpt-4o-mini-transcribe"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60.0

    def transcribe(self, audio: bytes, *, filename: str = "voice.oga", mime_type: str | None = None) -> str:
        if not self.api_key:
            raise TranscriptionError("OPENAI_API_KEY is not configured")
        if not audio:
            raise TranscriptionError("empty audio")

        return self._transcribe_once(audio, filename=filename, mime_type=mime_type, allow_convert=True)

    def _transcribe_once(
        self,
        audio: bytes,
        *,
        filename: str,
        mime_type: str | None,
        allow_convert: bool,
    ) -> str:
        boundary = f"----telegram-ai-brooch-{uuid.uuid4().hex}"
        content_type = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body = _multipart_body(
            boundary,
            fields={"model": self.model, "language": "ru"},
            files={"file": (filename, content_type, audio)},
        )
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if allow_convert and exc.code == 400 and "Unsupported file format" in detail:
                converted = _convert_to_m4a(audio, filename=filename, timeout_seconds=min(30.0, self.timeout_seconds))
                if converted is not None:
                    return self._transcribe_once(
                        converted,
                        filename="voice.m4a",
                        mime_type="audio/m4a",
                        allow_convert=False,
                    )
            raise TranscriptionError(f"transcription failed: HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise TranscriptionError(f"transcription failed: {exc}") from exc

        text = str(payload.get("text") or "").strip()
        if not text:
            raise TranscriptionError("transcription returned empty text")
        return text


def _convert_to_m4a(audio: bytes, *, filename: str, timeout_seconds: float) -> bytes | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    suffix = Path(filename).suffix or ".audio"
    with tempfile.TemporaryDirectory(prefix="ai-brooch-audio-") as tmp_dir:
        source = Path(tmp_dir) / f"input{suffix}"
        target = Path(tmp_dir) / "voice.m4a"
        source.write_bytes(audio)
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                str(target),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0 or not target.exists():
            return None
        return target.read_bytes()


def _multipart_body(
    boundary: str,
    *,
    fields: dict[str, str],
    files: dict[str, tuple[str, str, bytes]],
) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, (filename, content_type, data) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8"),
                data,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)
