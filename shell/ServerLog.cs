using System.Text.Json;

namespace RemotePc.Shell;

public enum LogLevel
{
    /// <summary>A Werkzeug request line: one per poll, hidden by default.</summary>
    Request,
    Info,
    Warn,
    Error,
}

public sealed record LogEntry(DateTime At, LogLevel Level, string Message)
{
    public string Time => At.ToString("HH:mm:ss");
}

/// <summary>
/// The facts the server reports at startup, in the READY event.
/// </summary>
public sealed record ServerFacts(
    string Url,
    string Pin,
    int Port,
    int Monitors,
    int Monitor,
    int Width,
    int Quality,
    int Fps,
    bool Ffmpeg,
    string? Capture,
    string? Audio,
    string? FilesDir,
    string? RemoteUrl);

public static class LogParser
{
    private const string Tag = "@RPC|";

    /// <summary>
    /// Classifies a line from the server. Events are prefixed by the server
    /// itself (see event() in app.py); everything else comes from Werkzeug
    /// and is request noise.
    /// </summary>
    public static LogEntry Parse(string line)
    {
        var now = DateTime.Now;
        if (!line.StartsWith(Tag, StringComparison.Ordinal))
            return new LogEntry(now, LogLevel.Request, line);

        var rest = line[Tag.Length..];
        var sep = rest.IndexOf('|');
        if (sep < 0)
            return new LogEntry(now, LogLevel.Info, rest);

        var level = rest[..sep] switch
        {
            "WARN" => LogLevel.Warn,
            "ERROR" => LogLevel.Error,
            _ => LogLevel.Info,
        };

        // The message sometimes carries a JSON object of details at the end
        // of the line: useful to a machine, unreadable to an eye. Render it
        // in plain words rather than showing it raw.
        var message = rest[(sep + 1)..];
        var brace = message.IndexOf('{');
        if (brace > 0 && message.EndsWith("}", StringComparison.Ordinal))
        {
            var facts = Flatten(message[brace..]);
            message = message[..brace].TrimEnd() + (facts is null ? "" : " — " + facts);
        }
        return new LogEntry(now, level, message);
    }

    /// <summary>
    /// Fields kept out of the readable line. The PIN travels in the JSON
    /// because the home page needs it, but printing it in the log means a
    /// screenshot of that panel hands it over — and the panel is the thing you
    /// screenshot when something goes wrong.
    /// </summary>
    private static readonly HashSet<string> Hidden = new(StringComparer.OrdinalIgnoreCase) { "pin" };

    private static string? Flatten(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            var parts = doc.RootElement.EnumerateObject()
                .Where(p => !Hidden.Contains(p.Name))
                .Select(p => $"{p.Name} {p.Value}")
                .ToArray();
            return parts.Length == 0 ? null : string.Join(", ", parts);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    /// <summary>Extracts the startup facts, or null if the line is not a READY.</summary>
    public static ServerFacts? ParseReady(string line)
    {
        if (!line.StartsWith(Tag + "READY|", StringComparison.Ordinal))
            return null;
        var brace = line.IndexOf('{');
        if (brace < 0)
            return null;
        try
        {
            using var doc = JsonDocument.Parse(line[brace..]);
            var r = doc.RootElement;
            string? Text(string name) =>
                r.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
            int Number(string name, int fallback) =>
                r.TryGetProperty(name, out var v) && v.TryGetInt32(out var n) ? n : fallback;

            return new ServerFacts(
                Text("url") ?? "",
                Text("pin") ?? "",
                Number("port", 5000),
                Number("monitors", 1),
                Number("monitor", 1),
                Number("width", 0),
                Number("quality", 0),
                Number("fps", 0),
                r.TryGetProperty("ffmpeg", out var f) && f.ValueKind == JsonValueKind.True,
                Text("capture"),
                Text("audio"),
                Text("files"),
                Text("remote"));
        }
        catch (JsonException)
        {
            return null;
        }
    }
}
