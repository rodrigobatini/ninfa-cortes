using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: AssemblyTitle("Ninfa Cortes")]
[assembly: AssemblyProduct("Ninfa Cortes")]

internal sealed class DesktopWindow : Form
{
    private readonly WebView2 view = new WebView2();
    private readonly Label loading = new Label();
    private bool ready;
    private bool starting = true;
    private bool closing;
    private bool allowClose;

    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr window, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);

    [STAThread]
    private static void Main()
    {
        bool owns;
        using (var mutex = new Mutex(true, @"Local\BatiniCortesDesktopWindow", out owns))
        {
            if (!owns)
            {
                ActivateExisting();
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var window = new DesktopWindow()) Application.Run(window);
        }
    }

    private static void ActivateExisting()
    {
        using (var self = Process.GetCurrentProcess())
        {
            var processes = Process.GetProcessesByName(self.ProcessName);
            try
            {
                var existing = processes.FirstOrDefault(p => p.Id != self.Id && p.MainWindowHandle != IntPtr.Zero);
                if (existing == null) return;
                ShowWindowAsync(existing.MainWindowHandle, 9);
                SetForegroundWindow(existing.MainWindowHandle);
            }
            finally { foreach (var process in processes) process.Dispose(); }
        }
    }

    private DesktopWindow()
    {
        Text = PortableLauncher.AppName;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(1280, 850);
        MinimumSize = new Size(650, 520);
        AutoScaleMode = AutoScaleMode.Dpi;
        BackColor = Color.FromArgb(246, 240, 232);
        loading.Dock = DockStyle.Fill;
        loading.TextAlign = ContentAlignment.MiddleCenter;
        loading.Font = new Font("Segoe UI", 13);
        loading.ForeColor = Color.FromArgb(22, 27, 56);
        loading.Text = "Abrindo o Ninfa Cortes…";
        view.Dock = DockStyle.Fill;
        view.DefaultBackgroundColor = BackColor;
        view.Visible = false;
        Controls.Add(view);
        Controls.Add(loading);
        Shown += async delegate { await OpenStudio(); };
    }

    private async Task OpenStudio()
    {
        try
        {
            var userData = Path.Combine(PortableLauncher.DataDirectory(), "janela");
            var env = await CoreWebView2Environment.CreateAsync(null, userData);
            await view.EnsureCoreWebView2Async(env);
            await Task.Run((Action)PortableLauncher.EnsureStudio);
            var core = view.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.AreBrowserAcceleratorKeysEnabled = false;
            core.NavigationStarting += (sender, e) => { if (!IsLocal(e.Uri)) e.Cancel = true; };
            core.NewWindowRequested += (sender, e) => { e.Handled = true; };
            core.WebMessageReceived += ReceiveStudioMessage;
            core.DownloadStarting += SaveExport;
            core.NavigationCompleted += (sender, e) =>
            {
                if (e.IsSuccess)
                {
                    ready = true;
                    starting = false;
                    loading.Visible = false;
                    view.Visible = true;
                }
                else
                {
                    starting = false;
                    loading.Text = "O estúdio não conseguiu carregar. Feche e abra de novo.";
                }
            };
            core.Navigate(PortableLauncher.BaseUrl);
        }
        catch (WebView2RuntimeNotFoundException)
        {
            starting = false;
            loading.Text = "O Windows precisa do Microsoft Edge WebView2 Runtime para abrir o estúdio.";
        }
        catch (Exception error)
        {
            starting = false;
            loading.Text = error.Message;
        }
    }

    private static bool IsLocal(string address)
    {
        Uri uri;
        return Uri.TryCreate(address, UriKind.Absolute, out uri) && uri.Scheme == "http" &&
            uri.Host == "127.0.0.1" && uri.Port == 8771;
    }

    private void ReceiveStudioMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (!IsLocal(e.Source)) return;
        var message = e.TryGetWebMessageAsString();
        if (message == "studio-stopped")
        {
            BeginInvoke((Action)delegate { allowClose = true; Close(); });
        }
        else if (message == "studio-stop-failed") closing = false;
    }

    private void SaveExport(object sender, CoreWebView2DownloadStartingEventArgs e)
    {
        e.Handled = true;
        var defer = e.GetDeferral();
        BeginInvoke((Action)delegate
        {
            try
            {
                using (var dialog = new SaveFileDialog {
                    Title = "Salvar corte exportado", Filter = "Vídeo MP4 (*.mp4)|*.mp4",
                    FileName = Path.GetFileName(e.ResultFilePath), AddExtension = true, DefaultExt = "mp4"
                })
                {
                    if (dialog.ShowDialog(this) == DialogResult.OK) e.ResultFilePath = dialog.FileName;
                    else e.Cancel = true;
                }
            }
            finally { defer.Complete(); }
        });
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (!allowClose && (ready || starting) &&
            (e.CloseReason == CloseReason.UserClosing || e.CloseReason == CloseReason.TaskManagerClosing))
        {
            e.Cancel = true;
            if (ready && !closing) RequestClose();
        }
        base.OnFormClosing(e);
    }

    private async void RequestClose()
    {
        closing = true;
        try
        {
            if (!await Task.Run((Func<bool>)PortableLauncher.IsReady))
            {
                allowClose = true;
                Close();
                return;
            }
            await view.ExecuteScriptAsync("document.getElementById('shutdown').click()");
        }
        catch (Exception error)
        {
            closing = false;
            MessageBox.Show(this, error.Message, PortableLauncher.AppName, MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }
}
