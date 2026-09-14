using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace RemotePc.Shell.Pages;

public partial class FilesView : UserControl
{
    private readonly ObservableCollection<FileRow> _rows = new();
    private string? _folder;

    public FilesView()
    {
        InitializeComponent();
        FileList.ItemsSource = _rows;
    }

    /// <summary>
    /// The folder is chosen by the server, which reports it in READY: the
    /// interface must not decide it on its own, or the two would disagree
    /// whenever the setting changes.
    /// </summary>
    public void SetFolder(string? folder)
    {
        _folder = folder;
        FolderPath.Text = folder ?? "—";
        Refresh();
    }

    public void Refresh()
    {
        _rows.Clear();
        if (_folder is null || !Directory.Exists(_folder))
        {
            CountText.Text = "";
            EmptyText.Visibility = Visibility.Visible;
            return;
        }

        var files = new DirectoryInfo(_folder).GetFiles()
            .OrderByDescending(f => f.LastWriteTime);
        var total = 0L;
        var count = 0;
        foreach (var file in files)
        {
            // Invariant culture for the date: the rest of the window is in
            // English, and the current culture rendered the month in the
            // system language, which read as a mistake next to it.
            var when = file.LastWriteTime.ToString("d MMM HH:mm",
                System.Globalization.CultureInfo.InvariantCulture);
            _rows.Add(new FileRow(file.Name, $"{Human(file.Length)} · {when}"));
            total += file.Length;
            count++;
        }

        CountText.Text = count == 0 ? "" : $"{count} file{(count > 1 ? "s" : "")}, {Human(total)}";
        EmptyText.Visibility = count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private static string Human(long n) => n switch
    {
        < 1024 => $"{n} B",
        < 1048576 => $"{n / 1024} KB",
        < 1073741824 => $"{n / 1048576.0:0.0} MB",
        _ => $"{n / 1073741824.0:0.00} GB",
    };

    private void OnOpenFolder(object sender, RoutedEventArgs e)
    {
        if (_folder is null)
            return;
        Directory.CreateDirectory(_folder);
        // UseShellExecute: without it, Process.Start looks for an executable
        // rather than handing the path to Explorer.
        Process.Start(new ProcessStartInfo(_folder) { UseShellExecute = true });
    }

    private void OnRefresh(object sender, RoutedEventArgs e) => Refresh();

    private void OnDeleteFile(object sender, RoutedEventArgs e)
    {
        if (sender is not FrameworkElement { Tag: string name } || _folder is null)
            return;
        try
        {
            File.Delete(Path.Combine(_folder, name));
        }
        catch (IOException)
        {
            // Held open by another application, or already gone. The refresh
            // below shows whichever it turned out to be.
        }
        Refresh();
    }

    // --- drag and drop ---

    private void OnDragOver(object sender, DragEventArgs e)
    {
        var ok = e.Data.GetDataPresent(DataFormats.FileDrop) && _folder is not null;
        e.Effects = ok ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled = true;
        if (ok)
        {
            DropZone.BorderBrush = (Brush)FindResource("AccentFillColorDefaultBrush");
            DropText.Text = "Release to copy into the shared folder";
        }
    }

    private void OnDragLeave(object sender, DragEventArgs e) => ResetDropZone();

    private void OnDrop(object sender, DragEventArgs e)
    {
        ResetDropZone();
        if (_folder is null || e.Data.GetData(DataFormats.FileDrop) is not string[] paths)
            return;

        Directory.CreateDirectory(_folder);
        foreach (var path in paths)
        {
            try
            {
                // Folders are skipped rather than copied recursively: dropping
                // a directory tree onto a phone transfer is far more likely to
                // be a slip than an intention.
                if (Directory.Exists(path))
                    continue;
                File.Copy(path, UniquePath(Path.GetFileName(path)));
            }
            catch (IOException)
            {
                // Unreadable source or full disk: the others still go through,
                // and the list shows what actually landed.
            }
        }
        Refresh();
    }

    /// <summary>
    /// Adds a rank rather than overwriting, the same way the server does for
    /// what arrives from the phone.
    /// </summary>
    private string UniquePath(string name)
    {
        var directory = _folder!;
        var candidate = Path.Combine(directory, name);
        var stem = Path.GetFileNameWithoutExtension(name);
        var ext = Path.GetExtension(name);
        for (var n = 2; File.Exists(candidate); n++)
            candidate = Path.Combine(directory, $"{stem} ({n}){ext}");
        return candidate;
    }

    private void ResetDropZone()
    {
        DropZone.BorderBrush = new SolidColorBrush(Color.FromArgb(0x33, 0xFF, 0xFF, 0xFF));
        DropText.Text = "Drop files here to send them to the phone";
    }

    public sealed record FileRow(string Name, string Detail);
}
