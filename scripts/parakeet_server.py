#!/usr/bin/env python3
"""Warm, localhost-only NeMo Parakeet transcription service for WSL.

Run this with the existing NeMo/uv environment, not the Windows Lakewood
environment.  The model loads once and every subsequent request reuses it.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer


DEFAULT_MODEL = "nvidia/parakeet-unified-en-0.6b"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MIN_AUDIO_SECONDS = 0.2


def wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as audio:
            rate = audio.getframerate()
            return audio.getnframes() / rate if rate else 0.0
    except (EOFError, wave.Error) as exc:
        raise ValueError("request body is not a readable WAV file") from exc


def transcript_text(output) -> str:
    if not isinstance(output, (list, tuple)) or not output:
        return ""
    first = output[0]
    text = getattr(first, "text", first)
    return text.strip() if isinstance(text, str) else ""


class ParakeetEngine:
    def __init__(self, model_name: str, device: str, half_precision: bool):
        try:
            import torch
            import nemo.collections.asr as nemo_asr
        except ImportError as exc:
            raise RuntimeError(
                "NeMo and CUDA PyTorch are required. Run this script from the "
                "existing ~/lakewood-stt/NeMo uv environment.") from exc
        self.model_name = model_name
        self.device = device
        self._torch = torch
        print(f"Loading {model_name} on {device}...", flush=True)
        started = time.monotonic()
        model = nemo_asr.models.ASRModel.from_pretrained(
            model_name=model_name, map_location="cpu").eval()
        if half_precision and device.startswith("cuda"):
            model = model.half()
        self._model = model.to(device)
        if device.startswith("cuda"):
            torch.cuda.synchronize()
        print(f"Model ready in {time.monotonic() - started:.3f}s", flush=True)

    def transcribe(self, audio_path: str) -> tuple[str, float]:
        started = time.monotonic()
        with self._torch.inference_mode():
            output = self._model.transcribe([audio_path], batch_size=1)
        if self.device.startswith("cuda"):
            self._torch.cuda.synchronize()
        return transcript_text(output), time.monotonic() - started


def make_handler(engine: ParakeetEngine):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LakewoodParakeet/1"

        def _json(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path != "/healthz":
                self._json(404, {"error": "not found"})
                return
            self._json(200, {
                "status": "ready", "model": engine.model_name, "device": engine.device})

        def do_POST(self):
            if self.path != "/transcribe":
                self._json(404, {"error": "not found"})
                return
            if self.headers.get_content_type() != "audio/wav":
                self._json(415, {"error": "Content-Type must be audio/wav"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid Content-Length"})
                return
            if length <= 0 or length > MAX_AUDIO_BYTES:
                self._json(413, {"error": "audio body is empty or too large"})
                return
            body = self.rfile.read(length)
            path = ""
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio:
                    path = audio.name
                    audio.write(body)
                duration = wav_duration(path)
                if duration < MIN_AUDIO_SECONDS:
                    self._json(422, {"error": f"audio is too short ({duration:.2f}s)"})
                    return
                text, latency = engine.transcribe(path)
                if not text:
                    self._json(422, {"error": "model produced an empty transcript"})
                    return
                self._json(200, {
                    "transcript": text,
                    "model": engine.model_name,
                    "audio_duration_seconds": duration,
                    "inference_latency_seconds": latency,
                    "correlation_id": self.headers.get("X-Correlation-ID", ""),
                })
            except ValueError as exc:
                self._json(422, {"error": str(exc)})
            except Exception as exc:
                print(f"transcription failed: {type(exc).__name__}: {exc}", flush=True)
                self._json(500, {"error": "transcription failed"})
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass

        def log_message(self, fmt, *args):
            # BaseHTTPRequestHandler's concise access line contains no audio
            # or transcript/customer text.
            super().log_message(fmt, *args)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--float32", action="store_true",
                        help="Disable FP16 (uses more VRAM; intended for diagnosis only).")
    parser.add_argument("--warmup-audio",
                        help="Optional real WAV to compile/warm kernels before accepting calls.")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("Parakeet service is local-only; --host must be 127.0.0.1 or localhost")
    engine = ParakeetEngine(args.model, args.device, not args.float32)
    if args.warmup_audio:
        text, latency = engine.transcribe(args.warmup_audio)
        print(f"Warm-up complete in {latency:.3f}s: {text!r}", flush=True)
    server = HTTPServer((args.host, args.port), make_handler(engine))
    print(f"Listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Parakeet service", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
