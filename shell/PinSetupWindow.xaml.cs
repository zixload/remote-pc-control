using System.Security.Cryptography;
using System.Windows;
using System.Windows.Media;
using Wpf.Ui.Controls;

namespace RemotePc.Shell;

/// <summary>
/// Asked once, on the first launch, before the server is allowed to start.
///
/// The PIN used to be a constant written in the repository, which meant it
/// protected nobody who never changed it. Asking here rather than from the
/// phone is deliberate: whoever is at the keyboard of this machine already has
/// everything, while letting the first phone to connect choose the PIN would
/// hand the machine to whoever reached the port first.
/// </summary>
public partial class PinSetupWindow : FluentWindow
{
    /// <summary>
    /// Eight digits is a hundred million possibilities. Against the rate limit
    /// in app.py — five attempts, then a lockout doubling to fifteen minutes —
    /// that is centuries of guessing. Four digits fell in twelve seconds
    /// before the limit existed, and in about three weeks after it.
    /// </summary>
    public const int MinLength = 8;

    public string Pin { get; private set; } = "";

    public PinSetupWindow() => InitializeComponent();

    private void OnPinChanged(object sender, RoutedEventArgs e)
    {
        var text = PinBox.Text ?? "";
        var digits = text.All(char.IsDigit);
        var ok = digits && text.Length >= MinLength;

        OkButton.IsEnabled = ok;

        if (text.Length == 0)
        {
            Hint.Text = "Digits only. Eight of them put a brute-force search out of reach.";
            Hint.Foreground = (Brush)FindResource("MutedBrush");
        }
        else if (!digits)
        {
            Hint.Text = "Digits only — the phone shows a numeric keypad to type it.";
            Hint.Foreground = (Brush)FindResource("WarnBrush");
        }
        else if (text.Length < MinLength)
        {
            var missing = MinLength - text.Length;
            Hint.Text = $"{missing} more digit{(missing > 1 ? "s" : "")} to go.";
            Hint.Foreground = (Brush)FindResource("WarnBrush");
        }
        else
        {
            Hint.Text = "That will do.";
            Hint.Foreground = (Brush)FindResource("OkBrush");
        }
    }

    private void OnGenerate(object sender, RoutedEventArgs e)
    {
        // RandomNumberGenerator rather than Random: the value guards the
        // machine, and Random is seeded predictably enough to matter here.
        var digits = new char[MinLength];
        for (var i = 0; i < digits.Length; i++)
            digits[i] = (char)('0' + RandomNumberGenerator.GetInt32(10));
        PinBox.Text = new string(digits);
    }

    private void OnConfirm(object sender, RoutedEventArgs e)
    {
        Pin = PinBox.Text.Trim();
        DialogResult = true;
        Close();
    }
}
