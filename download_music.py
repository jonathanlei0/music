#!/usr/bin/env python3
"""Search for one track and save an MP3 for VirtualDJ using yt-dlp."""

import argparse
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = Path.home() / "Music" / "VirtualDJ Downloads"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("song", help="quoted artist/song search, or a direct video URL")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--result", type=int, default=1,
                        help="search result to download, 1–10 (default: first)")
    parser.add_argument("--preview", action="store_true",
                        help="show the selected title and URL without downloading")
    args = parser.parse_args(argv)
    if not args.song.strip():
        parser.error("song cannot be empty")
    if not 1 <= args.result <= 10:
        parser.error("--result must be between 1 and 10")
    return args


def downloader_command():
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    # Let the requested `python3 download_music.py ...` work outside the venv.
    local = ROOT / "venv" / "bin" / "yt-dlp"
    executable = str(local) if local.is_file() else shutil.which("yt-dlp")
    if executable:
        return [executable]
    raise RuntimeError(
        'Install the downloader: python3 -m pip install "yt-dlp[default]"'
    )


def build_command(args, executable):
    song = args.song.strip()
    is_url = urlparse(song).scheme.lower() in ("http", "https")
    target = song if is_url else f"ytsearch{args.result}:{song}"
    output = args.output_dir.expanduser().resolve()
    command = [*executable, "--ignore-config", "--no-playlist", "--no-overwrites",
               "--socket-timeout", "20", "--retries", "3"]
    # yt-dlp enables only Deno by default; also allow an installed Node runtime.
    node = shutil.which("node")
    if node:
        command += ["--js-runtimes", f"node:{node}"]
    if not is_url:
        command += ["--playlist-items", str(args.result)]
    if args.preview:
        command += ["--simulate", "--print", "%(title)s\n%(webpage_url)s"]
    else:
        command += ["--format", "bestaudio/best", "--extract-audio",
                    "--audio-format", "mp3", "--audio-quality", "0",
                    "--embed-metadata", "--download-archive", str(output / ".downloaded.txt"),
                    "--paths", str(output),
                    "--output", "%(title).180B [%(id)s].%(ext)s",
                    "--print", "before_dl:Downloading: %(title)s",
                    "--print", "after_move:Saved: %(filepath)s", "--no-simulate"]
    return [*command, "--", target]


def main(argv=None):
    args = parse_args(argv)
    try:
        executable = downloader_command()
        if not args.preview:
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                raise RuntimeError("FFmpeg and ffprobe are required. On macOS: brew install ffmpeg")
            args.output_dir.expanduser().mkdir(parents=True, exist_ok=True)
        print(f"Looking up: {args.song.strip()}", flush=True)
        result = subprocess.run(build_command(args, executable), check=False)
        if result.returncode:
            print("Download failed. Check the message above; try a direct URL or another "
                  "--result. To update: venv/bin/python -m pip install -U 'yt-dlp[default]'",
                  file=sys.stderr)
            return 1
        if not args.preview:
            print(f"VirtualDJ music folder: {args.output_dir.expanduser().resolve()}")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled. Partial downloads can be resumed by running again.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
