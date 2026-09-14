using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;

namespace RemotePc.Shell;

/// <summary>
/// The colour of a log line, from its level. Without it a refused PIN carries
/// exactly the same visual weight as a focus poll.
/// </summary>
public sealed class LevelBrushConverter : IValueConverter
{
    public static readonly LevelBrushConverter Instance = new();

    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var key = value switch
        {
            LogLevel.Error => "DangerBrush",
            LogLevel.Warn => "WarnBrush",
            LogLevel.Request => "MutedBrush",
            _ => "TextFillColorPrimaryBrush",
        };
        return Application.Current?.TryFindResource(key) as Brush ?? Brushes.White;
    }

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}
