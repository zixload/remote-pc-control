using System.Diagnostics;
using System.IO;
using System.Text.Json;

namespace RemotePc.Shell;

/// <summary>
/// Brings Tailscale up while the server runs and back down afterwards.
///
/// On Windows the connection is driven by the tray client
/// (tailscale-ipn.exe), not the service alone: with the tray quit, the backend
/// sits offline and the CLI `up` leaves it in "NoState" until a frontend
/// exists. So bringing it up means launching the tray if needed, then `up`;
/// bringing it down is a plain `down`. Both work without elevation.
///
/// It only takes Tailscale down if it was the one that brought it up. Someone
/// who keeps Tailscale on for other things should not lose it when the server
/// stops.
/// </summary>
public sealed class TailscaleController
{
    private readonly string? _cli;
    private readonly string? _gui;
    private bool _broughtUp;

    public TailscaleController()
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "Tailscale");

        var cli = Path.Combine(dir, "tailscale.exe");
        _cli = File.Exists(cli) ? cli : null;

        var gui = Path.Combine(dir, "tailscale-ipn.exe");
        _gui = File.Exists(gui) ? gui : null;
    }

    /// <summary>Whether Tailscale is installed at all. If not, everything is a no-op.</summary>
    public bool Available => _cli is not null;

    /// <summary>
    /// Ensures Tailscale is connected, returning quickly if it already is.
    /// Remembers whether it had to bring it up, so <see cref="DownIfWeBroughtItUp"/>
    /// only touches connections it opened.
    /// </summary>
    public async Task EnsureUpAsync()
    {
        if (_cli is null || await IsOnlineAsync())
            return;   // not installed, or already connected: leave it as found

        // The tray client holds the profile and drives the connection; without
        // it running, `up` cannot finish. Launch it, then connect.
        if (_gui is not null && Process.GetProcessesByName("tailscale-ipn").Length == 0)
        {
            try { Process.Start(new ProcessStartInfo(_gui) { UseShellExecute = true }); }
            catch (Exception) { /* GUI gone: `up` may still work if a session is held */ }
        }

        await RunAsync("up", "--timeout=15s");

        // Poll until it reports online, up to ~15s. It usually takes 3 to 5.
        for (var i = 0; i < 30; i++)
        {
            if (await IsOnlineAsync())
            {
                _broughtUp = true;
                return;
            }
            await Task.Delay(500);
        }
    }

    /// <summary>Disconnects, but only if we were the ones who connected.</summary>
    public void DownIfWeBroughtItUp()
    {
        if (_cli is null || !_broughtUp)
            return;
        _broughtUp = false;
        try { RunAsync("down").GetAwaiter().GetResult(); }
        catch (Exception) { /* already offline or gone: nothing to do */ }
    }

    private async Task<bool> IsOnlineAsync()
    {
        var json = await RunAsync("status", "--json");
        if (json is null)
            return false;
        try
        {
            using var doc = JsonDocument.Parse(json);
            return doc.RootElement.TryGetProperty("Self", out var self)
                && self.TryGetProperty("Online", out var online)
                && online.ValueKind == JsonValueKind.True;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private async Task<string?> RunAsync(params string[] args)
    {
        if (_cli is null)
            return null;
        var info = new ProcessStartInfo(_cli)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var a in args)
            info.ArgumentList.Add(a);
        try
        {
            using var proc = Process.Start(info);
            if (proc is null)
                return null;
            var output = await proc.StandardOutput.ReadToEndAsync();
            await proc.WaitForExitAsync();
            return output;
        }
        catch (Exception)
        {
            return null;
        }
    }
}
