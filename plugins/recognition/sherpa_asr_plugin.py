"""SenseVoice and Silero CPU plugin for 16kHz mono WAV/FLAC files."""
from __future__ import annotations

import importlib.metadata
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BadInput, command, configure, input_file, model_records, parser, payload


def recognize(argv=None):
    arguments = parser(__doc__)
    arguments.add_argument("--model", type=Path, required=True)
    arguments.add_argument("--tokens", type=Path, required=True)
    arguments.add_argument("--vad-model", type=Path, required=True)
    args = arguments.parse_args(argv)
    configure(args.threads)
    source_sha = input_file(args.input, {".wav", ".flac"})

    import soundfile as sf
    try:
        with sf.SoundFile(args.input) as audio:
            if audio.format not in {"WAV", "FLAC"} or audio.samplerate != 16000 or audio.channels != 1 or audio.frames == 0:
                raise BadInput("ASR requires nonempty 16kHz mono WAV/FLAC; no automatic resampling")
            samples = audio.read(dtype="float32", always_2d=True)
    except BadInput:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise BadInput("Audio cannot be decoded") from error
    if samples.shape[1] != 1 or len(samples) == 0:
        raise BadInput("ASR requires nonempty 16kHz mono WAV/FLAC; no automatic resampling")
    samples = samples[:, 0]
    duration_ms = round(len(samples) / 16000 * 1000)
    if duration_ms < 1:
        raise BadInput("Audio duration is shorter than one millisecond")

    models = model_records([args.model, args.tokens, args.vad_model])
    started = time.perf_counter()
    import sherpa_onnx
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(args.model), tokens=str(args.tokens), num_threads=args.threads,
        sample_rate=16000, language="", use_itn=True, provider="cpu", debug=False)
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(args.vad_model)
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = 0.25
    config.silero_vad.min_speech_duration = 0.25
    config.silero_vad.max_speech_duration = 30.0
    config.silero_vad.window_size = 512
    config.sample_rate = 16000
    config.num_threads = args.threads
    config.provider = "cpu"
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=120)
    load_ms = (time.perf_counter() - started) * 1000
    version = importlib.metadata.version("sherpa-onnx")

    segments = []
    warnings = ["ASR confidence is null: this SenseVoice binding does not provide native probabilities in the v1 confidence range.",
                "Segment boundaries come from Silero VAD, not human timestamp truth or word-level alignment."]
    has_log_probs = False
    started = time.perf_counter()
    vad.reset()
    for position in range(0, len(samples), 512):
        vad.accept_waveform(samples[position:position + 512])
    vad.flush()
    while not vad.empty():
        speech = vad.front
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, speech.samples)
        recognizer.decode_stream(stream)
        result = stream.result
        start_ms = round(speech.start / 16000 * 1000)
        end_ms = round((speech.start + len(speech.samples)) / 16000 * 1000)
        timestamps = list(getattr(result, "timestamps", []))
        tokens = [{"text": str(text),
                   "start_ms": start_ms + round(timestamps[index] * 1000) if index < len(timestamps) else None,
                   "conf": None} for index, text in enumerate(getattr(result, "tokens", []))]
        segments.append({"id": f"s{len(segments)}", "start_ms": start_ms, "end_ms": end_ms,
                         "text": result.text, "conf": None, "tokens": tokens})
        has_log_probs = has_log_probs or bool(getattr(result, "ys_log_probs", []))
        vad.pop()
    infer_ms = (time.perf_counter() - started) * 1000
    if not segments:
        warnings.append("VAD detected no speech; no whole-file fallback transcription was fabricated.")
    if any(segment["tokens"] and any(token["start_ms"] is None for token in segment["tokens"]) for segment in segments):
        warnings.append("Native token timestamps unavailable; token start_ms null.")
    if has_log_probs:
        warnings.append("Native ys_log_probs are not converted into confidence.")
    return payload("asr", version, models, args.input, source_sha, args.threads, load_ms, infer_ms,
                   duration_ms=duration_ms, segments=segments, warnings=warnings)


if __name__ == "__main__":
    raise SystemExit(command(recognize))
