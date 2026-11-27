# Ninfa Cortes

Ninfa Cortes is an open-source portable Windows app for turning YouTube videos into vertical clips. It selects candidate moments and keeps you in control of the final cut.

## Download and use

1. Download the full **Windows x64 ZIP** from [GitHub Releases](https://github.com/rodrigobatini/ninfa-cortes/releases) and extract the entire folder.
2. Double-click **Ninfa Cortes.exe**. The desktop window uses the Microsoft Edge WebView2 Runtime.
3. Paste a YouTube link for a video you have permission to use, or choose **Arquivo local** in the Windows app. Select up to 30 candidate clips. The app transcribes the video in Portuguese and selects segments for review.
4. Use **Nome do clipe** to name the draft in the library and set the download filename. Edit the opening hook and caption text, drag the captions into position, and adjust the facecam framing if the app detected a separate camera. Use **Aparar início e fim** below the preview timeline to trim the clip.
5. Select **Exportar MP4**, then **Baixar MP4** to save a 1080 × 1920 video.

Transcription and rendering run locally. You need an internet connection to retrieve YouTube videos. Transcription uses CUDA on a compatible NVIDIA setup and defaults to CPU when CUDA is unavailable. Use the language selector in the header for Brazilian Portuguese or US English. Automatic transcription uses Brazilian Portuguese.

The **Card social** tab lets you add a static PNG, JPG, or WebP image that stays visible for the entire clip. Adjust its size, drag it in the preview, and optionally copy it to the other clips from the same source or save it as the default for newly generated clips. The remove buttons in the library hide individual clips or an entire source from the editor and keep the original video and exported files on disk. Keep locally selected source videos at their original paths while editing.

The app stores clips and drafts in `%LOCALAPPDATA%\BatiniClipes`. To keep them with the executable, create a `dados` folder beside it before launching. **Encerrar** and the window’s **X** save pending edits and shut down the local server when processing is complete.

## Develop

- `app/` contains the editor, FastAPI backend, and rendering code.
- `windows/` contains the WinForms window, launcher, and icon.
- `engine/` contains the bundled [AI Stream Clipper](https://github.com/arcticloud/ai-stream-clipper) sources and assets.

On Windows x64, extract a full release ZIP and close the app. Run the following command from the repository root to rebuild the desktop executable and update the editor in that copy:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build.ps1 -PortableDir "C:\path\to\Ninfa Cortes"
```

The build uses the Windows .NET Framework compiler and downloads the pinned WebView2 SDK from NuGet. The extracted release supplies the Python runtime, FFmpeg, model weights, and other runtime dependencies.

### Run the editor from source

Install Python 3.12 x64 and add Node.js, FFmpeg, and FFprobe to your `PATH`. Use an FFmpeg build with `libass` and an H.264 encoder such as `libx264`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:BATINI_CLIP_DATA = Join-Path $PWD 'dados'
$env:BATINI_CLIP_ENGINE = Join-Path $PWD 'engine'
New-Item -ItemType Directory -Force $env:BATINI_CLIP_DATA | Out-Null
.\.venv\Scripts\python.exe .\app\launch.py --port 8772
```

Open [http://127.0.0.1:8772](http://127.0.0.1:8772). This setup uses CPU by default and fetches the Whisper small model the first time it needs it. To use an existing local model, set `BATINI_CLIP_MODEL` to its directory before starting the editor.

## Contribute

Start with an [issue](https://github.com/rodrigobatini/ninfa-cortes/issues), then submit a pull request from your fork. See the [contribution guide](CONTRIBUTING.md) for the review and testing steps.

## License

The original Ninfa Cortes source and documentation are available under the [MIT License](LICENSE). Bundled third-party software, fonts, and model weights retain their own licenses. See [Third-party notices](THIRD-PARTY.md).
