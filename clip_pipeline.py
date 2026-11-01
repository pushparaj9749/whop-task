import os
import subprocess
import json

os.makedirs("work", exist_ok=True)
os.makedirs("clips", exist_ok=True)

VIDEO = "episode.mp4"

# Clip definitions: name -> list of (start, end) seconds from Episode 1
# These timestamps are initial selections; verify against transcript if needed.
CLIPS = [
    {"name": "01_Trip_vs_Vacation", "segments": [(62.74, 77.84), (79.18, 91.58)]},
    {"name": "02_Josies_Happy_Place", "segments": [(377.94, 412.22), (421.26, 440.85)]},
    {"name": "03_Window_Seat_Anxiety", "segments": [(1924.83, 1961.73)]},
    {"name": "04_Shark_Tank_Excitement", "segments": [(717.75, 723.95), (736.11, 745.32)]},
]


def run(cmd):
    print(f"[CMD] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def format_ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def transcribe():
    print("Step 1: Transcribing with faster-whisper (base, int8, CPU)...")
    from faster_whisper import WhisperModel

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        VIDEO,
        language="en",
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    words = []
    for seg in segments:
        for w in seg.words:
            words.append({"word": w.word, "start": w.start, "end": w.end})

    transcript = {
        "language": info.language,
        "duration": info.duration,
        "word_count": len(words),
        "words": words,
    }
    with open("work/transcript.json", "w") as f:
        json.dump(transcript, f, indent=2)

    print(f"Transcribed {len(words)} words, language={info.language}")
    return words


def make_ass(words, clip_start, clip_end, ass_path, max_duration):
    clip_words = [
        w for w in words if w["start"] >= clip_start and w["end"] <= clip_end
    ]

    lines = []
    lines.append("[Script Info]")
    lines.append("Title: Clip Subtitles")
    lines.append("ScriptType: v4.00+")
    lines.append("")
    lines.append("[V4+ Styles]")
    lines.append(
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    lines.append(
        "Style: Default,Arial,28,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,2,10,10,10,1"
    )
    lines.append("")
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    for w in clip_words:
        rel_s = max(0.0, w["start"] - clip_start)
        rel_e = min(max_duration, w["end"] - clip_start)
        if rel_e <= rel_s:
            continue
        text = w["word"].strip()
        if not text:
            continue
        lines.append(
            f"Dialogue: 0,{format_ass_time(rel_s)},{format_ass_time(rel_e)},Default,,0,0,0,{{\\an8}}{text}"
        )

    with open(ass_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def render_clip(clip, words):
    name = clip["name"]
    segs = clip["segments"]
    print(f"\nRendering {name} with {len(segs)} segment(s)...")

    # Trim each segment individually (re-encode for safe concat)
    parts = []
    for j, (start, end) in enumerate(segs):
        part = f"work/{name}_part{j}.mp4"
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", VIDEO,
            "-ss", str(start), "-to", str(end),
            "-c:v", "libx264", "-crf", "21", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            part,
        ])
        parts.append(part)

    # Concat segments
    concat_file = f"work/{name}_concat.txt"
    with open(concat_file, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")

    combined = f"work/{name}_combined.mp4"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c", "copy", combined,
    ])

    # Duration for subtitle clamping
    probe = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", combined,
    ], capture_output=True, text=True, check=True)
    duration = float(probe.stdout.strip())

    # ASS subtitles for this clip
    clip_start = segs[0][0]
    clip_end = segs[-1][1]
    ass_path = f"work/{name}.ass"
    make_ass(words, clip_start, clip_end, ass_path, duration)

    # Vertical render: blurred background + centered footage + burned subtitles
    output = f"clips/{name}.mp4"
    ass_escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:")
    filter_complex = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase:crop=1080:1920,boxblur=20:5[bg];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[main];"
        f"[main]ass={ass_escaped}[vout]"
    )
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", combined,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a",
        "-c:v", "libx264", "-crf", "21", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart",
        output,
    ])

    # Cleanup intermediates
    for p in parts:
        os.remove(p)
    os.remove(concat_file)
    os.remove(combined)

    # QA: verify output decodes
    run([
        "ffmpeg", "-v", "error", "-i", output, "-f", "null", "-",
    ])
    print(f"  ✅ {output} decoded OK")

    return output


if __name__ == "__main__":
    # Step 0: video should already be downloaded by workflow step
    if not os.path.exists(VIDEO):
        raise SystemExit(f"{VIDEO} not found! Run download step first.")

    words = transcribe()

    results = []
    for i, clip in enumerate(CLIPS):
        out = render_clip(clip, words)
        results.append({"name": clip["name"], "file": out})

    # Captions file
    with open("clips/captions.md", "w") as f:
        f.write("# Upload Captions — The World's First Podcast\n\n")
        f.write("Copy-paste these per platform. Show name and correct handles included.\n\n")
        for r in results:
            f.write(f"## {r['name']}\n")
            f.write("- Caption: Erin and Sara are too real. The World's First Podcast\n")
            f.write("- TikTok: @WorldsFirstPodcast\n")
            f.write("- Instagram: @WorldsFirstPodcast\n")
            f.write("- YouTube: @WorldsFirstPod\n")
            f.write("\n")

    print("\n🎉 All clips rendered successfully!")
    for r in results:
        print(f"  - {r['file']}")
