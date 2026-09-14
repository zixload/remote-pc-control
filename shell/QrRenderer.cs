using System.IO;
using System.Windows.Media.Imaging;
using QRCoder;

namespace RemotePc.Shell;

public static class QrRenderer
{
    /// <summary>
    /// Renders the address as a QR code. DHCP changes the machine's IP
    /// without warning: without this, the address has to be read off the
    /// screen and typed into the phone every time.
    ///
    /// Black modules on white, even in a dark theme: that is the contrast
    /// cameras expect, and the inverse fails to scan on some phones.
    /// </summary>
    public static BitmapSource? Render(string url, int pixelsPerModule = 8)
    {
        if (string.IsNullOrWhiteSpace(url))
            return null;

        using var generator = new QRCodeGenerator();
        using var data = generator.CreateQrCode(url, QRCodeGenerator.ECCLevel.M);
        var png = new PngByteQRCode(data).GetGraphic(pixelsPerModule);

        var image = new BitmapImage();
        image.BeginInit();
        image.StreamSource = new MemoryStream(png);
        image.CacheOption = BitmapCacheOption.OnLoad;
        image.EndInit();
        image.Freeze();  // shareable across threads, and read without a copy
        return image;
    }
}
