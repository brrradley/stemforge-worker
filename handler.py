from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
import runpod

print("LiteLABS worker booting", flush=True)


def post_progress(url: str | None, token: str | None, job_id: str | int | None, message: str, percent: int) -> None:
    print(f"LiteLABS progress {percent}%: {message}", flush=True)
    if not url or not token or not job_id:
        return
    try:
        requests.post(
            url,
            json={
                "token": token,
                "job_id": job_id,
                "message": message,
                "percent": max(0, min(100, int(percent))),
            },
            timeout=8,
        )
    except Exception as exc:
        print(f"LiteLABS progress callback failed: {exc}", flush=True)


def download_file(url: str, destination: Path) -> None:
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with destination.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def content_type_for(file_path: Path) -> str:
    lower = file_path.name.lower()
    if lower.endswith(".zip"):
        return "application/zip"
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "application/gzip"
    return "application/octet-stream"


def upload_file_put(url: str, file_path: Path) -> None:
    headers = {"Content-Type": content_type_for(file_path)}
    with file_path.open("rb") as file:
        response = requests.put(url, data=file, headers=headers, timeout=300)
    response.raise_for_status()


def handler(job: dict) -> dict:
    print("LiteLABS received job", flush=True)
    payload = job.get("input") or {}

    if payload.get("healthcheck") is True:
        return {"ok": True, "status": "ready", "service": "litelabs-worker"}

    audio_url = payload.get("audio_url")
    if not audio_url:
        return {"ok": False, "error": "Missing required input.audio_url"}

    from master_pack import build_master_pack

    filename = payload.get("filename")
    if not filename:
        parsed_name = Path(urlparse(audio_url).path).name
        filename = parsed_name or "track.mp3"

    output_format = str(payload.get("output_format") or "flac").lower().strip()
    if output_format not in {"mp3", "flac"}:
        output_format = "flac"

    model_dir = Path(
        payload.get("model_dir")
        or os.getenv("STEMFORGE_MODEL_DIR", "/models/bs_roformer_sw")
    )

    result_put_url = payload.get("result_put_url")
    result_public_url = payload.get("result_public_url")
    progress_url = payload.get("progress_url")
    progress_token = payload.get("progress_token")
    progress_job_id = payload.get("progress_job_id")

    def progress(message: str, percent: int) -> None:
        post_progress(progress_url, progress_token, progress_job_id, message, percent)

    try:
        with tempfile.TemporaryDirectory(prefix="litelabs_") as temp_dir:
            temp_root = Path(temp_dir)
            input_path = temp_root / filename
            work_root = temp_root / "work"
            output_root = temp_root / "output"

            progress("Worker starting", 12)
            progress("Downloading audio", 15)
            download_file(audio_url, input_path)
            progress("Audio downloaded", 17)

            result = build_master_pack(
                input_audio=input_path,
                work_root=work_root,
                model_dir=model_dir,
                output_root=output_root,
                progress=progress,
                output_format=output_format,
            )

            archive_path = Path(result["archive_path"])
            archive_size = archive_path.stat().st_size

            uploaded = False
            if result_put_url:
                progress("Uploading ZIP back to LiteRECORDS", 94)
                upload_file_put(result_put_url, archive_path)
                uploaded = True
                progress("Finalising download", 98)

            return {
                "ok": True,
                "track": result["track"],
                "output_format": result.get("output_format", output_format),
                "archive_size_bytes": archive_size,
                "uploaded": uploaded,
                "result_url": result_public_url,
                "stems": result["stems"],
            }
    except Exception as exc:
        post_progress(progress_url, progress_token, progress_job_id, f"Worker error: {exc}", 100)
        return {
            "ok": False,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }


print("LiteLABS handler ready", flush=True)
runpod.serverless.start({"handler": handler})
