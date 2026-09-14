using System.Diagnostics;
using System.IO;

namespace RemotePc.Shell;

/// <summary>
/// Starts the Python server and watches its output.
/// </summary>
public sealed class ServerProcess : IDisposable
{
    private readonly JobObject _job = new();
    private Process? _process;

    public event Action<LogEntry>? Logged;
    public event Action<ServerFacts>? Ready;
    public event Action<int>? Exited;

    public bool IsRunning => _process is { HasExited: false };

    /// <summary>
    /// Finds something to launch: the packaged executable first, otherwise the
    /// repository venv, so the interface also works against the sources. It
    /// walks up the tree because the shell's build output sits several levels
    /// down, under bin\Debug\net10.0-windows\.
    /// </summary>
    public static (string File, string[] Prefix)? Locate()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (var i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
        {
            // The server has a name of its own: both executables used to be
            // called "Remote PC.exe", which made them impossible to tell apart
            // in the task manager.
            var exe = Path.Combine(dir.FullName, "dist", "remote-pc-server.exe");
            if (File.Exists(exe))
                return (exe, []);

            var script = Path.Combine(dir.FullName, "app.py");
            var python = Path.Combine(dir.FullName, ".venv", "Scripts", "python.exe");
            if (File.Exists(script) && File.Exists(python))
                return (python, [script]);
        }
        return null;
    }

    public void Start(AppConfig config)
    {
        if (IsRunning)
            return;

        var target = Locate();
        if (target is null)
        {
            Logged?.Invoke(new LogEntry(DateTime.Now, LogLevel.Error,
                @"Server not found: neither dist\remote-pc-server.exe nor app.py with its venv."));
            return;
        }

        var info = new ProcessStartInfo(target.Value.File)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var a in target.Value.Prefix)
            info.ArgumentList.Add(a);
        foreach (var a in config.ToArguments())
            info.ArgumentList.Add(a);

        // Without this the log panel stays empty: Python's output to a pipe is
        // block-buffered, so it would arrive in chunks of a few kilobytes,
        // long after the event it describes.
        info.Environment["PYTHONUNBUFFERED"] = "1";

        var process = new Process { StartInfo = info, EnableRaisingEvents = true };
        process.OutputDataReceived += OnLine;
        // Werkzeug and the server's own events both write here: reading stdout
        // alone would miss the important half.
        process.ErrorDataReceived += OnLine;
        process.Exited += (_, _) =>
        {
            int code;
            try
            {
                // Read from the local, not the field: Stop() clears the field,
                // and this callback can still be in flight when it does.
                code = process.ExitCode;
            }
            catch (InvalidOperationException)
            {
                return;  // already disposed by Stop(), which reports it itself
            }

            if (code != 0)
                Logged?.Invoke(new LogEntry(DateTime.Now, LogLevel.Error,
                    $"The server stopped on its own (exit code {code})."));
            Exited?.Invoke(code);
        };

        _process = process;
        process.Start();
        // Attached right away: if the interface disappears abruptly, Windows
        // takes the server with it.
        _job.Add(process);
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
    }

    private void OnLine(object sender, DataReceivedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(e.Data))
            return;
        var facts = LogParser.ParseReady(e.Data);
        if (facts is not null)
            Ready?.Invoke(facts);
        Logged?.Invoke(LogParser.Parse(e.Data));
    }

    public void Stop()
    {
        var process = _process;
        if (process is null)
            return;
        _process = null;

        // Detached before the kill, not after: otherwise the last output lines
        // and the Exited callback arrive on thread-pool threads while this
        // thread is still inside Dispose(), and the caller is left updating an
        // interface from two directions at once. The stop is reported by
        // whoever called Stop(), which already knows it happened.
        process.EnableRaisingEvents = false;
        process.OutputDataReceived -= OnLine;
        process.ErrorDataReceived -= OnLine;

        try
        {
            if (!process.HasExited)
                // The onefile executable is a launcher that starts a second
                // process: killing the parent alone would leave the server,
                // and therefore the port, occupied.
                process.Kill(entireProcessTree: true);
        }
        catch (Exception)
        {
            // Already gone between the check and the call: nothing to do.
        }
        process.Dispose();
    }

    /// <summary>
    /// Measures the level of every audio device. Two seconds each: a virtual
    /// mix with no source assigned opens without error and carries nothing but
    /// silence, and only listening tells them apart.
    /// </summary>
    public static async Task<List<(string Name, double? Level)>> MeasureAudioAsync()
    {
        var result = new List<(string, double?)>();
        var target = Locate();
        if (target is null)
            return result;

        var info = new ProcessStartInfo(target.Value.File)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var a in target.Value.Prefix)
            info.ArgumentList.Add(a);
        info.ArgumentList.Add("--list-audio");
        info.ArgumentList.Add("--json");

        using var proc = Process.Start(info);
        if (proc is null)
            return result;
        var json = await proc.StandardOutput.ReadToEndAsync();
        await proc.WaitForExitAsync();

        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(json);
            foreach (var item in doc.RootElement.EnumerateArray())
            {
                var name = item.GetProperty("name").GetString();
                if (name is null)
                    continue;
                double? level = item.TryGetProperty("level", out var l)
                    && l.ValueKind == System.Text.Json.JsonValueKind.Number
                        ? l.GetDouble() : null;
                result.Add((name, level));
            }
        }
        catch (System.Text.Json.JsonException)
        {
            // Unexpected output: return an empty list, and the interface says so.
        }
        return result;
    }

    public void Dispose()
    {
        Stop();
        _job.Dispose();
    }
}
