using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace RemotePc.Shell;

/// <summary>
/// Server settings, kept on disk.
///
/// They already existed as command-line options, but an icon pinned to the
/// taskbar passes none: the PIN, the stream width and the audio device were
/// therefore out of reach without opening a terminal. The interface writes
/// them here and passes them back as arguments at every start, which leaves
/// Python the sole owner of its defaults.
/// </summary>
public sealed class AppConfig
{
    /// <summary>
    /// Null until the first launch has asked for one. There is deliberately no
    /// default: the constant that used to sit here was public, so it protected
    /// nobody who never changed it.
    /// </summary>
    public string? Pin { get; set; }

    /// <summary>True once a PIN long enough to be worth something is stored.</summary>
    public bool HasUsablePin =>
        !string.IsNullOrWhiteSpace(Pin) && Pin!.Length >= PinSetupWindow.MinLength;
    public int Port { get; set; } = 5000;
    public int Width { get; set; } = 1600;
    public int Quality { get; set; } = 90;
    public int Fps { get; set; } = 20;
    public int Monitor { get; set; } = 1;
    public string? Audio { get; set; }
    public int CinemaFps { get; set; } = 30;
    public string CinemaBitrate { get; set; } = "6M";

    /// <summary>Collapsed by default: the log only matters when something looks wrong.</summary>
    public bool LogExpanded { get; set; }

    /// <summary>False, the log shows events only; true, the whole request stream.</summary>
    public bool LogVerbose { get; set; }

    public bool MinimizeToTray { get; set; } = true;
    public bool AutoStart { get; set; } = true;

    /// <summary>Turn Tailscale on with the server and off when it stops.</summary>
    public bool ManageTailscale { get; set; } = true;

    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static string Path { get; } = System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "remote-pc-control", "shell.json");

    public static AppConfig Load()
    {
        try
        {
            if (File.Exists(Path))
                return JsonSerializer.Deserialize<AppConfig>(File.ReadAllText(Path)) ?? new AppConfig();
        }
        catch (Exception)
        {
            // A corrupt file must not stop the app from opening: fall back
            // to the defaults, and the next save overwrites it.
        }
        return new AppConfig();
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
            File.WriteAllText(Path, JsonSerializer.Serialize(this, Options));
        }
        catch (Exception)
        {
            // Nothing vital: the settings are lost at the next launch.
        }
    }

    /// <summary>The arguments to pass the server so it reflects these settings.</summary>
    public string[] ToArguments()
    {
        var args = new List<string>
        {
            "--pin", Pin ?? "",
            "--port", Port.ToString(),
            "--width", Width.ToString(),
            "--quality", Quality.ToString(),
            "--fps", Fps.ToString(),
            "--monitor", Monitor.ToString(),
            "--cinema-fps", CinemaFps.ToString(),
            "--cinema-bitrate", CinemaBitrate,
        };
        // Passing nothing lets Python pick the device itself; "none" is how
        // it is told to stay silent.
        if (!string.IsNullOrWhiteSpace(Audio))
        {
            args.Add("--audio");
            args.Add(Audio!);
        }
        return args.ToArray();
    }
}
