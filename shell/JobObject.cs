using System.Diagnostics;
using System.Runtime.InteropServices;

namespace RemotePc.Shell;

/// <summary>
/// Binds the server to the lifetime of the interface.
///
/// Normal shutdowns already stop it. What remained was the case where the
/// interface dies without going through them: a crash, or a forced kill from
/// the task manager. The server would then survive with no window to show it,
/// while still offering the machine's keyboard and mouse to anyone who reaches
/// the port. A job object with KILL_ON_JOB_CLOSE makes that impossible:
/// Windows closes the job with the parent's last handle, and kills what it
/// holds.
/// </summary>
public sealed class JobObject : IDisposable
{
    private const int ExtendedLimitInformation = 9;
    private const uint LimitKillOnJobClose = 0x2000;

    private readonly IntPtr _handle;

    public JobObject()
    {
        _handle = CreateJobObject(IntPtr.Zero, null);
        if (_handle == IntPtr.Zero)
            return;

        var limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            BasicLimitInformation = new JOBOBJECT_BASIC_LIMIT_INFORMATION
            {
                LimitFlags = LimitKillOnJobClose,
            },
        };

        var size = Marshal.SizeOf(limits);
        var buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            SetInformationJobObject(_handle, ExtendedLimitInformation, buffer, (uint)size);
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    public void Add(Process process)
    {
        if (_handle == IntPtr.Zero)
            return;
        try
        {
            AssignProcessToJobObject(_handle, process.Handle);
        }
        catch (Exception)
        {
            // The process may have died between its start and this call:
            // without the job, we simply fall back on the explicit stop.
        }
    }

    public void Dispose()
    {
        if (_handle != IntPtr.Zero)
            CloseHandle(_handle);
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string? name);

    [DllImport("kernel32.dll")]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll")]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }
}
