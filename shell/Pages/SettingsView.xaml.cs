using System.Windows;
using System.Windows.Controls;
using Wpf.Ui.Controls;

namespace RemotePc.Shell.Pages;

public partial class SettingsView : UserControl
{
    private const string NoAudio = "No sound";
    private const string LetServerChoose = "Let the server choose";

    private AppConfig _config = new();

    public event Action<AppConfig>? Saved;
    public event Action? RestartRequested;

    public SettingsView() => InitializeComponent();

    public void Load(AppConfig config)
    {
        _config = config;
        PinBox.Text = config.Pin ?? "";
        PortBox.Value = config.Port;
        WidthBox.Value = config.Width;
        QualityBox.Value = config.Quality;
        FpsBox.Value = config.Fps;
        MonitorBox.Value = config.Monitor;
        CinemaFpsBox.Value = config.CinemaFps;
        BitrateBox.Text = config.CinemaBitrate;
        TrayToggle.IsChecked = config.MinimizeToTray;
        AutoStartToggle.IsChecked = config.AutoStart;
        TailscaleToggle.IsChecked = config.ManageTailscale;

        // Until a measurement has run, the list holds only the two choices
        // that need no knowledge of the hardware.
        AudioCombo.Items.Clear();
        AudioCombo.Items.Add(LetServerChoose);
        AudioCombo.Items.Add(NoAudio);
        if (!string.IsNullOrWhiteSpace(config.Audio) && config.Audio != "none")
            AudioCombo.Items.Add(config.Audio);
        AudioCombo.SelectedItem = config.Audio switch
        {
            null or "" => LetServerChoose,
            "none" => NoAudio,
            _ => config.Audio,
        };
    }

    /// <summary>The server is running: what is saved applies only at its next start.</summary>
    public void SetServerRunning(bool running)
    {
        RestartButton.Visibility = running ? Visibility.Visible : Visibility.Collapsed;
        if (!running)
            RestartHint.IsOpen = false;
    }

    private async void OnMeasureAudio(object sender, RoutedEventArgs e)
    {
        MeasureButton.IsEnabled = false;
        MeasureText.Text = "Measuring";
        try
        {
            var devices = await ServerProcess.MeasureAudioAsync();
            var previous = AudioCombo.SelectedItem is AudioChoice c ? c.Name
                : AudioCombo.SelectedItem as string;

            AudioCombo.Items.Clear();
            AudioCombo.Items.Add(LetServerChoose);
            AudioCombo.Items.Add(NoAudio);
            foreach (var (name, level) in devices)
            {
                // The level is shown next to the name: it is the only way to
                // tell a mix carrying sound from a silent one.
                var verdict = level is null ? "unreadable"
                    : level < -80 ? "silence"
                    : $"{level:0} dB";
                AudioCombo.Items.Add(new AudioChoice(name, verdict));
            }

            AudioCombo.SelectedItem = AudioCombo.Items
                .OfType<object>()
                .FirstOrDefault(i => Describe(i) == previous) ?? AudioCombo.Items[0];
        }
        finally
        {
            MeasureText.Text = "Measure";
            MeasureButton.IsEnabled = true;
        }
    }

    private static string Describe(object item) =>
        item is AudioChoice c ? c.Name : (string)item;

    private void OnSave(object sender, RoutedEventArgs e)
    {
        // Refused rather than quietly corrected: a PIN silently replaced by
        // something else is worse than one you are told to fix, since you
        // would go on believing you knew it.
        var pin = (PinBox.Text ?? "").Trim();
        if (pin.Length < PinSetupWindow.MinLength || !pin.All(char.IsDigit))
        {
            RestartHint.Severity = InfoBarSeverity.Warning;
            RestartHint.Title = "PIN not saved";
            RestartHint.Message =
                $"It must be {PinSetupWindow.MinLength} digits or more. Nothing else was saved either.";
            RestartHint.IsOpen = true;
            return;
        }
        _config.Pin = pin;
        _config.Port = (int)(PortBox.Value ?? 5000);
        _config.Width = (int)(WidthBox.Value ?? 1600);
        _config.Quality = (int)(QualityBox.Value ?? 90);
        _config.Fps = (int)(FpsBox.Value ?? 20);
        _config.Monitor = (int)(MonitorBox.Value ?? 1);
        _config.CinemaFps = (int)(CinemaFpsBox.Value ?? 30);
        _config.CinemaBitrate = string.IsNullOrWhiteSpace(BitrateBox.Text) ? "6M" : BitrateBox.Text.Trim();
        _config.MinimizeToTray = TrayToggle.IsChecked == true;
        _config.AutoStart = AutoStartToggle.IsChecked == true;
        _config.ManageTailscale = TailscaleToggle.IsChecked == true;

        _config.Audio = AudioCombo.SelectedItem switch
        {
            AudioChoice c => c.Name,
            string s when s == NoAudio => "none",
            string s when s != LetServerChoose => s,
            _ => null,
        };

        _config.Save();
        Saved?.Invoke(_config);
        RestartHint.Severity = InfoBarSeverity.Informational;
        RestartHint.Title = "Settings saved";
        RestartHint.Message =
            "The server is still running with the old values. Restart it to apply them.";
        RestartHint.IsOpen = RestartButton.Visibility == Visibility.Visible;
    }

    private void OnRestartServer(object sender, RoutedEventArgs e)
    {
        RestartHint.IsOpen = false;
        RestartRequested?.Invoke();
    }

    /// <summary>A device and its measured level, rendered as one line in the list.</summary>
    private sealed record AudioChoice(string Name, string Verdict)
    {
        public override string ToString() => $"{Name}  —  {Verdict}";
    }
}
