using System.Drawing;
using System.Windows;
using Forms = System.Windows.Forms;

namespace RemotePc.Shell;

/// <summary>
/// Notification area icon.
///
/// Deliberately the WinForms one rather than a WPF wrapper: it is the same
/// Shell_NotifyIcon underneath, and it handles the context menu and the
/// reappearance after an Explorer restart on its own.
/// </summary>
public sealed class TrayIcon : IDisposable
{
    private readonly Forms.NotifyIcon _icon;
    private readonly Forms.ToolStripMenuItem _toggleItem;
    private bool _balloonShown;

    public event Action? ShowRequested;
    public event Action? ToggleRequested;
    public event Action? QuitRequested;

    public TrayIcon()
    {
        _toggleItem = new Forms.ToolStripMenuItem("Start");
        _toggleItem.Click += (_, _) => ToggleRequested?.Invoke();

        var showItem = new Forms.ToolStripMenuItem("Open");
        showItem.Click += (_, _) => ShowRequested?.Invoke();

        var quitItem = new Forms.ToolStripMenuItem("Quit");
        quitItem.Click += (_, _) => QuitRequested?.Invoke();

        var menu = new Forms.ContextMenuStrip();
        menu.Items.Add(showItem);
        menu.Items.Add(_toggleItem);
        menu.Items.Add(new Forms.ToolStripSeparator());
        menu.Items.Add(quitItem);

        _icon = new Forms.NotifyIcon
        {
            Icon = LoadIcon(),
            Text = "Remote PC",
            Visible = true,
            ContextMenuStrip = menu,
        };
        _icon.DoubleClick += (_, _) => ShowRequested?.Invoke();
    }

    private static Icon LoadIcon()
    {
        try
        {
            var uri = new Uri("pack://application:,,,/icon.ico");
            using var stream = Application.GetResourceStream(uri)?.Stream;
            if (stream is not null)
                return new Icon(stream);
        }
        catch (Exception)
        {
            // Resource missing from a development build: a generic icon beats
            // an exception at startup.
        }
        return SystemIcons.Application;
    }

    public void SetRunning(bool running)
    {
        _toggleItem.Text = running ? "Stop" : "Start";
        _icon.Text = running ? "Remote PC — running" : "Remote PC — stopped";
    }

    /// <summary>
    /// Says once that closing the window did not stop the server. Repeating it
    /// on every close would grow tiresome fast.
    /// </summary>
    public void NotifyStillRunning()
    {
        if (_balloonShown)
            return;
        _balloonShown = true;
        _icon.BalloonTipTitle = "Remote PC is still running";
        _icon.BalloonTipText = "The server is still serving. Right-click the icon to stop it.";
        _icon.ShowBalloonTip(4000);
    }

    public void Dispose()
    {
        _icon.Visible = false;
        _icon.Dispose();
    }
}
