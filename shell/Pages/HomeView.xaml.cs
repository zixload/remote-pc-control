using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Wpf.Ui.Controls;

namespace RemotePc.Shell.Pages;

public partial class HomeView : UserControl
{
    private string? _url;

    public event Action? ToggleRequested;

    public HomeView() => InitializeComponent();

    /// <summary>Shows the facts the server reported when it started.</summary>
    public void ShowRunning(ServerFacts facts)
    {
        _url = facts.Url;
        UrlText.Text = facts.Url.Replace("http://", "");
        PinText.Text = facts.Pin;
        ShowPinWarning(facts.Pin);
        CopyUrl.IsEnabled = true;

        QrImage.Source = QrRenderer.Render(facts.Url);
        QrPlaceholder.Visibility = QrImage.Source is null ? Visibility.Visible : Visibility.Collapsed;

        // The card appears only when the server actually found a private
        // network. Showing it empty would promise something that is not there.
        if (string.IsNullOrWhiteSpace(facts.RemoteUrl))
        {
            HideRemote();
        }
        else
        {
            RemoteCard.Visibility = Visibility.Visible;
            // The column keeps its share of the row even when its child is
            // collapsed, so the width has to be zeroed as well or the first
            // card would sit in half a row next to a gap.
            RemoteColumn.Width = new GridLength(1, GridUnitType.Star);
            RemoteText.Text = facts.RemoteUrl!.Replace("http://", "");
            RemoteQr.Source = QrRenderer.Render(facts.RemoteUrl!);
        }

        ScreensText.Text = facts.Monitors == 1 ? "1 screen" : $"{facts.Monitors} screens";
        StreamingText.Text = $"Screen {facts.Monitor}";
        WidthText.Text = $"{facts.Width} px";
        WidthTile.ToolTip = $"JPEG quality {facts.Quality}";
        FpsText.Text = $"~{facts.Fps} fps";

        // Not shortened, unlike the device name: "(GPU)" against "(CPU)" is
        // precisely the part worth reading here.
        CinemaText.Text = facts.Ffmpeg ? facts.Capture ?? "available" : "unavailable";
        CinemaTile.ToolTip = facts.Ffmpeg
            ? facts.Capture
            : "ffmpeg is not in PATH, so cinema mode and sound cannot run";
        CinemaText.Foreground = facts.Ffmpeg
            ? (Brush)FindResource("TextFillColorPrimaryBrush")
            : (Brush)FindResource("WarnBrush");

        AudioText.Text = facts.Audio is null ? "none" : Shorten(facts.Audio);
        SoundTile.ToolTip = facts.Audio ?? "no device picked — see Settings";

        SetState(running: true);
    }

    /// <summary>
    /// The PIN is the only barrier before full control of the machine, so its
    /// weaknesses are said out loud rather than left to be discovered.
    ///
    /// Length matters more than it looks. Attempts are rate-limited now, but
    /// four digits is still only ten thousand possibilities; eight puts the
    /// search out of reach entirely.
    /// </summary>
    private void ShowPinWarning(string pin)
    {
        var message = pin.Length < PinSetupWindow.MinLength
            ? $"A {pin.Length}-character PIN is the weak point here. "
              + $"{PinSetupWindow.MinLength} or more in Settings."
            : null;

        PinWarning.Text = message ?? "";
        PinWarning.Visibility = message is null ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>
    /// Trims the qualifier in brackets. "Personal Mix (Elgato Virtual Audio)"
    /// does not fit a third of a card at 18px, and what identifies the device
    /// is the part before the brackets; the whole name stays in the tooltip.
    /// </summary>
    private static string Shorten(string value)
    {
        var bracket = value.IndexOf(" (", StringComparison.Ordinal);
        return bracket > 0 ? value[..bracket] : value;
    }

    public void ShowStopped()
    {
        _url = null;
        UrlText.Text = "—";
        PinText.Text = "—";
        PinWarning.Visibility = Visibility.Collapsed;
        HideRemote();
        CopyUrl.IsEnabled = false;
        QrImage.Source = null;
        QrPlaceholder.Visibility = Visibility.Visible;

        foreach (var block in new[] { ScreensText, StreamingText, WidthText, FpsText, CinemaText, AudioText })
            block.Text = "—";
        WidthTile.ToolTip = null;
        CinemaTile.ToolTip = null;
        SoundTile.ToolTip = null;
        CinemaText.Foreground = (Brush)FindResource("TextFillColorPrimaryBrush");

        SetState(running: false);
    }

    private void HideRemote()
    {
        RemoteCard.Visibility = Visibility.Collapsed;
        RemoteColumn.Width = new GridLength(0);
        RemoteQr.Source = null;
    }

    /// <summary>In between: the process is up but has not reported READY yet.</summary>
    public void ShowStarting()
    {
        StatusText.Text = "Starting";
        Tint((Brush)FindResource("WarnBrush"));
        ToggleIcon.Symbol = SymbolRegular.Stop24;
    }

    /// <summary>
    /// Icon and word share one colour. A white glyph left the stop square as
    /// the brightest thing on the page, which drew the eye to the one element
    /// that should sit quietly at the bottom.
    /// </summary>
    private void Tint(Brush brush)
    {
        StatusText.Foreground = brush;
        ToggleIcon.Foreground = brush;
    }

    /// <summary>
    /// The state is carried by the colour of the word rather than a separate
    /// dot: one element instead of two, and the word had to be there anyway.
    /// </summary>
    private void SetState(bool running)
    {
        StatusText.Text = running ? "Running" : "Stopped";
        Tint((Brush)FindResource(running ? "OkBrush" : "MutedBrush"));
        ToggleIcon.Symbol = running ? SymbolRegular.Stop24 : SymbolRegular.Play24;
    }

    private void OnToggleServer(object sender, RoutedEventArgs e) => ToggleRequested?.Invoke();

    private void OnCopyUrl(object sender, RoutedEventArgs e)
    {
        if (_url is null)
            return;
        try
        {
            Clipboard.SetText(_url);
        }
        catch (Exception)
        {
            // The clipboard can be locked by another application. Nothing
            // serious: the address is still readable on screen.
        }
    }
}
