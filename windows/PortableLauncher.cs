using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Threading;
using System.Windows.Forms;

internal static class PortableLauncher
{
    internal const string AppName = "Ninfa Cortes";
    internal const string BaseUrl = "http://127.0.0.1:8771/";

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            EnsureStudio();
            if (!args.Contains("--no-browser"))
                Process.Start(new ProcessStartInfo(BaseUrl) { UseShellExecute = true });
            return 0;
        }
        catch (Exception error)
        {
            if (!args.Contains("--no-browser"))
                MessageBox.Show(error.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    internal static void EnsureStudio()
    {
        using (var mutex = new Mutex(false, @"Local\BatiniCortesPortableLauncher"))
        {
            try
            {
                if (!mutex.WaitOne(TimeSpan.FromSeconds(32)))
                    throw new InvalidOperationException("O estúdio ainda está iniciando.");
            }
            catch (AbandonedMutexException) { }
            try { if (!IsReady()) StartStudio(); }
            finally { mutex.ReleaseMutex(); }
        }
    }

    internal static bool IsReady()
    {
        try
        {
            var request = (HttpWebRequest)WebRequest.Create(BaseUrl + "api/health");
            request.Proxy = null;
            request.Timeout = 600;
            request.ReadWriteTimeout = 600;
            using (var response = request.GetResponse())
            using (var reader = new StreamReader(response.GetResponseStream()))
                return reader.ReadToEnd().Contains("\"app\":\"batini-cortes\"");
        }
        catch (WebException) { return false; }
    }

    internal static string DataDirectory()
    {
        var alongside = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "dados");
        return Directory.Exists(alongside) ? alongside : Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BatiniClipes");
    }

    private static void StartStudio()
    {
        var root = AppDomain.CurrentDomain.BaseDirectory;
        var runtime = Path.Combine(root, "runtime");
        var app = Path.Combine(root, "app");
        var python = Path.Combine(runtime, "python", "pythonw.exe");
        var ffmpeg = Path.Combine(runtime, "python", "Lib", "site-packages", "static_ffmpeg", "bin", "win32");
        var model = Path.Combine(runtime, "model");
        var engine = Path.Combine(runtime, "engine");
        if (!File.Exists(python) || !File.Exists(Path.Combine(ffmpeg, "ffmpeg.exe")) ||
            !File.Exists(Path.Combine(model, "model.bin")) || !File.Exists(Path.Combine(app, "launch.py")))
            throw new FileNotFoundException("O pacote está incompleto. Mantenha o executável junto das pastas app e runtime.");

        var data = DataDirectory();
        Directory.CreateDirectory(data);
        var cuda = Path.Combine(runtime, "cuda");
        var dlls = Directory.Exists(cuda)
            ? Directory.GetFiles(cuda, "*.dll", SearchOption.AllDirectories).Select(Path.GetDirectoryName).Distinct().ToArray()
            : new string[0];

        var start = new ProcessStartInfo(python, "-s -B launch.py --port 8771 --log") {
            UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = app
        };
        var env = start.EnvironmentVariables;
        env.Remove("PYTHONHOME");
        env.Remove("PYTHONPATH");
        env.Remove("BATINI_CLIP_RUNTIME");
        env["PYTHONUTF8"] = "1";
        env["BATINI_CLIP_DATA"] = data;
        env["BATINI_CLIP_MODEL"] = model;
        env["BATINI_CLIP_ENGINE"] = engine;
        env["BATINI_CUDA"] = string.Join(";", dlls);
        env["HF_HOME"] = Path.Combine(data, "huggingface");
        env["PATH"] = string.Join(";", new[] { ffmpeg, runtime }.Concat(dlls).Concat(new[] {env["PATH"]}));

        using (var process = Process.Start(start))
        {
            File.WriteAllText(Path.Combine(data, "portable.pid"), process.Id.ToString());
            var timer = Stopwatch.StartNew();
            while (timer.Elapsed < TimeSpan.FromSeconds(27) && !process.HasExited)
            {
                if (IsReady()) return;
                Thread.Sleep(225);
            }
        }
        throw new InvalidOperationException("O estúdio não abriu. O relatório está em:\n" + Path.Combine(data, "portable.log"));
    }
}
