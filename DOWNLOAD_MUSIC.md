# Download music for VirtualDJ

```sh
python3 download_music.py "Rihanna Please don't stop the music"
```

Searches YouTube and downloads the first result as an MP3 into
`~/Music/VirtualDJ Downloads`. In VirtualDJ, browse to that folder under Local
Music/Volumes, or drag an MP3 onto a deck. This does not edit VirtualDJ's database.

Search results can include covers, live versions, and remixes. Check a match first
or choose another result:

```sh
python3 download_music.py "artist song" --preview
python3 download_music.py "artist song" --result 2
python3 download_music.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Use `--output-dir "/path/to/music"` to change the destination. Successfully
downloaded source IDs are recorded in `.downloaded.txt` to skip repeat downloads.
Remove the relevant archive entry if you deliberately want to download it again.
The MP3 uses FFmpeg's highest VBR quality setting; converting cannot improve
the original source quality. Availability and search order depend on the provider.

Dependencies (the script also finds the project's venv automatically):

```sh
venv/bin/python -m pip install -U -r requirements-download.txt
brew install ffmpeg
```

YouTube may require an up-to-date JavaScript runtime such as Deno, in addition
to yt-dlp's default dependencies. Authentication or provider restrictions can
prevent downloads; this utility does not bypass them.
