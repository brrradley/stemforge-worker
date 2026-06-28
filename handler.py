from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
import runpod

print("LiteLABS worker booting", flush=True)

_GENRE_PATCHED = False


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


def patch_genre_routing() -> None:
    global _GENRE_PATCHED
    if _GENRE_PATCHED:
        return

    import master_pack

    original_optional = master_pack.optional_stem_decision

    def tuned_optional_stem_decision(label: str, source: Path):
        decision = original_optional(label, source)
        if label == "Guitar" and decision.include and decision.score < 0.68:
            return master_pack.StemDecision(
                decision.label,
                decision.source,
                False,
                "low-confidence guitar/sample bleed",
                decision.score,
                decision.active_ratio,
                decision.mean_db,
                decision.max_db,
            )
        if label == "Piano / Keys" and decision.include and decision.score < 0.60:
            return master_pack.StemDecision(
                decision.label,
                decision.source,
                False,
                "low-confidence keys/bleed",
                decision.score,
                decision.active_ratio,
                decision.mean_db,
                decision.max_db,
            )
        return decision

    def tuned_detect_genre_from_audio(decisions: list, core_stats: dict, original_stats: dict) -> tuple[str, str]:
        included = {d.label for d in decisions if d.include}
        optional_scores = {d.label: d.score for d in decisions}

        vocals = master_pack.score_of(core_stats, "Vocals")
        drums = master_pack.score_of(core_stats, "Drums")
        bass = master_pack.score_of(core_stats, "Bass")
        guitar = optional_scores.get("Guitar", 0.0)
        piano = optional_scores.get("Piano / Keys", 0.0)
        synth_other = optional_scores.get("Synths / Strings / Other", 0.0)
        original_active = float(original_stats.get("active_ratio", 0.0))

        strong_rhythm = drums >= 0.44 and bass >= 0.30
        strong_vocal = vocals >= 0.45
        strong_guitar = "Guitar" in included and guitar >= 0.42
        dominant_guitar = strong_guitar and guitar > max(synth_other + 0.18, 0.66)
        strong_piano = "Piano / Keys" in included and piano >= 0.42
        strong_synth = "Synths / Strings / Other" in included and synth_other >= 0.38
        dance_like = strong_rhythm and (strong_synth or not dominant_guitar or bass >= 0.42)

        if dance_like:
            details = ["strong drums/bass"]
            if strong_synth:
                details.append("active synth/other")
            if strong_guitar and not dominant_guitar:
                details.append("guitar appears secondary/sample-like")
            return "electronic_dance", ", ".join(details)
        if strong_rhythm and dominant_guitar:
            return "rock_band", "strong drums with dominant confident guitar activity"
        if strong_piano and strong_vocal and drums < 0.42:
            return "piano_vocal_or_pop_ballad", "confident piano/keys with strong vocal and lighter drums"
        if strong_vocal and drums >= 0.35 and bass >= 0.25 and not dominant_guitar:
            return "vocal_pop", "strong vocal with moderate rhythm section and no dominant guitar"
        if strong_vocal and original_active > 0.35 and drums < 0.30 and bass < 0.30:
            return "acoustic_or_sparse", "strong vocal with low drum/bass activity"
        if strong_rhythm and not strong_vocal:
            return "instrumental_or_dance", "strong drums/bass with weaker vocal presence"
        return "mixed_or_unknown", "audio features did not strongly match a known route"

    master_pack.optional_stem_decision = tuned_optional_stem_decision
    master_pack.detect_genre_from_audio = tuned_detect_genre_from_audio
    _GENRE_PATCHED = True
    print("LiteLABS genre routing patch applied", flush=True)


def handler(job: dict) -> dict:
    print("LiteLABS received job", flush=True)
    payload = job.get("input") or {}

    if payload.get("healthcheck") is True:
        return {"ok": True, "status": "ready", "service": "litelabs-worker"}

    audio_url = payload.get("audio_url")
    if not audio_url:
        return {"ok": False, "error": "Missing required input.audio_url"}

    patch_genre_routing()
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
