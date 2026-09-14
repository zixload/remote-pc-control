using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using Wpf.Ui.Controls;

namespace RemotePc.Shell;

public partial class MainWindow : FluentWindow
{
    /// <summary>
    /// Past this, the oldest lines are dropped. The focus poll produces one
    /// line a second: with no ceiling, an evening left running would grow the
    /// memory for nothing.
    /// </summary>
    private const int MaxEntries = 2000;

    private readonly AppConfig _config = AppConfig.Load();
    private readonly ServerProcess _server = new();
    private readonly List<LogEntry> _all = new();
    private readonly ObservableCollection<LogEntry> _shown = new();

    private TrayIcon? _tray;
    private int _unseenProblems;
    private bool _reallyClosing;

    public MainWindow()
    {
        InitializeComponent();

        LogList.ItemsSource = _shown;
        VerboseCheck.IsChecked = _config.LogVerbose;
        SetLogExpanded(_config.LogExpanded);

        HomePage.ToggleRequested += OnToggleServer;
        HomePage.ShowStopped();

        SettingsPage.Load(_config);
        SettingsPage.Saved += _ => SettingsPage.SetServerRunning(_server.IsRunning);
        SettingsPage.RestartRequested += RestartServer;

        _server.Logged += entry => Post(() => Append(entry));
        _server.Ready += facts => Post(() =>
        {
            HomePage.ShowRunning(facts);
            FilesPage.SetFolder(facts.FilesDir);
            PaneStatus.Text = "Running";
            SettingsPage.SetServerRunning(true);
            _tray?.SetRunning(true);
        });
        _server.Exited += _ => Post(() =>
        {
            HomePage.ShowStopped();
            PaneStatus.Text = "Stopped";
            SettingsPage.SetServerRunning(false);
            _tray?.SetRunning(false);
        });

        Loaded += OnLoaded;
        Closing += OnClosing;
    }

    /// <summary>
    /// Hands work to the UI thread without waiting for it.
    ///
    /// Dispatcher.Invoke here froze the window on every stop: the output
    /// readers and the Exited callback run on thread-pool threads and fire
    /// while the UI thread sits inside Stop(), waiting for those very
    /// callbacks to drain. Each waited on the other, and Windows closed the
    /// application as hung. BeginInvoke queues and returns.
    /// </summary>
    private void Post(Action action)
    {
        try
        {
            Dispatcher.BeginInvoke(action);
        }
        catch (InvalidOperationException)
        {
            // Dispatcher already shut down. Quitting from the tray while the
            // server is still writing its last lines takes this path: the
            // window is going away and there is nothing left to update.
        }
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        // Before anything listens on the network. An old configuration can
        // also land here, since the PIN it holds predates the length rule.
        if (!_config.HasUsablePin && !AskForPin())
        {
            Close();
            return;
        }

        _tray = new TrayIcon();
        _tray.ShowRequested += RestoreFromTray;
        _tray.ToggleRequested += OnToggleServer;
        _tray.QuitRequested += () => { _reallyClosing = true; Close(); };

        if (_config.AutoStart)
            StartServer();
    }

    /// <summary>
    /// Asks for the PIN and stores it. Returns false if the window was closed
    /// without choosing one, in which case the application stops rather than
    /// serving with no barrier.
    /// </summary>
    private bool AskForPin()
    {
        var dialog = new PinSetupWindow { Owner = this };
        if (dialog.ShowDialog() != true)
            return false;

        _config.Pin = dialog.Pin;
        _config.Save();
        SettingsPage.Load(_config);
        Append(new LogEntry(DateTime.Now, LogLevel.Info, "PIN set."));
        return true;
    }

    // --- server ---

    private void StartServer()
    {
        HomePage.ShowStarting();
        PaneStatus.Text = "Starting";
        _server.Start(_config);
    }

    private void StopServer()
    {
        _server.Stop();
        HomePage.ShowStopped();
        PaneStatus.Text = "Stopped";
        SettingsPage.SetServerRunning(false);
        _tray?.SetRunning(false);
        Append(new LogEntry(DateTime.Now, LogLevel.Info, "Server stopped."));
    }

    private void OnToggleServer()
    {
        if (_server.IsRunning)
            StopServer();
        else
            StartServer();
    }

    private void RestartServer()
    {
        if (_server.IsRunning)
            StopServer();
        StartServer();
    }

    // --- navigation ---

    private void OnNavChecked(object sender, RoutedEventArgs e)
    {
        // Called once during InitializeComponent, before the pages exist:
        // without this guard the constructor throws.
        if (HomePage is null || FilesPage is null || SettingsPage is null)
            return;

        HomePage.Visibility = Show(NavHome);
        FilesPage.Visibility = Show(NavFiles);
        SettingsPage.Visibility = Show(NavSettings);

        // The folder only changes when the server restarts, but its contents
        // change constantly - the phone writes into it. Re-reading on every
        // visit is cheaper than watching the directory.
        if (ReferenceEquals(sender, NavFiles))
            FilesPage.Refresh();

        Visibility Show(object nav) =>
            ReferenceEquals(sender, nav) ? Visibility.Visible : Visibility.Collapsed;
    }

    // --- log ---

    private void Append(LogEntry entry)
    {
        _all.Add(entry);
        if (_all.Count > MaxEntries)
            _all.RemoveRange(0, _all.Count - MaxEntries);

        if (!Passes(entry))
            return;

        _shown.Add(entry);
        if (_shown.Count > MaxEntries)
            _shown.RemoveAt(0);

        if (LogBody.Visibility == Visibility.Visible)
            LogScroll.ScrollToEnd();
        else if (entry.Level is LogLevel.Warn or LogLevel.Error)
        {
            // Log collapsed: the counter is the only way to know something
            // happened without unfolding it.
            _unseenProblems++;
            LogBadgeText.Text = _unseenProblems.ToString();
            LogBadge.Visibility = Visibility.Visible;
        }
    }

    /// <summary>
    /// Werkzeug logs every request, focus polls included: one line a second,
    /// in which a real error drowns. They only pass in verbose mode.
    /// </summary>
    private bool Passes(LogEntry entry) => _config.LogVerbose || entry.Level != LogLevel.Request;

    private void Refilter()
    {
        _shown.Clear();
        foreach (var entry in _all.Where(Passes))
            _shown.Add(entry);
        LogScroll.ScrollToEnd();
    }

    private void OnVerboseChanged(object sender, RoutedEventArgs e)
    {
        _config.LogVerbose = VerboseCheck.IsChecked == true;
        _config.Save();
        Refilter();
    }

    private void OnToggleLog(object sender, RoutedEventArgs e) =>
        SetLogExpanded(LogBody.Visibility != Visibility.Visible);

    private void SetLogExpanded(bool expanded)
    {
        LogBody.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        LogChevron.Symbol = expanded ? SymbolRegular.ChevronDown24 : SymbolRegular.ChevronUp24;
        _config.LogExpanded = expanded;
        _config.Save();

        if (!expanded)
            return;
        _unseenProblems = 0;
        LogBadge.Visibility = Visibility.Collapsed;
        Dispatcher.BeginInvoke(new Action(() => LogScroll.ScrollToEnd()),
            System.Windows.Threading.DispatcherPriority.Loaded);
    }

    private void OnClearLog(object sender, RoutedEventArgs e)
    {
        _all.Clear();
        _shown.Clear();
        _unseenProblems = 0;
        LogBadge.Visibility = Visibility.Collapsed;
    }

    // --- window ---

    private void RestoreFromTray()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
    }

    private void OnClosing(object? sender, CancelEventArgs e)
    {
        // Closing the window while the phone is being used from another room
        // must not cut the server: fold into the notification area and carry
        // on serving.
        if (!_reallyClosing && _config.MinimizeToTray && _server.IsRunning)
        {
            e.Cancel = true;
            Hide();
            _tray?.NotifyStillRunning();
            return;
        }

        _server.Dispose();
        _tray?.Dispose();
    }
}
