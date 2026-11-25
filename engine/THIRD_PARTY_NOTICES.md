# Third-party notices

| What | Where | Licence |
|---|---|---|
| Montserrat font (Black, ExtraBold) | `assets/fonts/` | SIL Open Font License 1.1, see `assets/fonts/OFL.txt` |
| YuNet face detector model (opencv_zoo) | `assets/models/face_detection_yunet_2023mar.onnx` | MIT, Copyright (c) 2020 Shiqi Yu, see `assets/models/LICENSE-YuNet.txt` |
| Phrase-level transcript for the LLM to read (break on 0.5 s pauses) | idea from [video-use](https://github.com/browser-use/video-use) | MIT, approach only, no code copied |
| Clip-count rules by source length, non-overlap check | idea from Shorz agent skills (`shorz-mcp`) | MIT, approach only |

Runtime dependencies installed by `setup` (not shipped): yt-dlp (Unlicense), faster-whisper (MIT),
OpenCV (Apache 2.0), numpy (BSD), fontTools (MIT), ffmpeg (LGPL/GPL builds).
