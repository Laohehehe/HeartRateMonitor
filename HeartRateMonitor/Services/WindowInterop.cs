using System;
using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;

namespace HeartRateMonitor.Services;

/// <summary>
/// WinUI 3 窗口的 Win32 互操作（移植自归档版 HeartRateWindow 的 NativeMethods）。
///
/// WinUI 3 与 WPF 的差异：
///  - 没有 WindowStyle / AllowsTransparency / ShowInTaskbar / ShowActivated / Topmost，
///    全部改由 AppWindow(OverlappedPresenter) + 扩展窗口样式实现；
///  - 取句柄用 WindowNative.GetWindowHandle。
/// </summary>
internal static class WindowInterop
{
    public const int GwlExStyle = -20;

    /// <summary>WS_EX_TRANSPARENT：鼠标穿透，点击落到下层窗口。</summary>
    public const int WsExTransparent = 0x00000020;

    /// <summary>WS_EX_APPWINDOW：强制显示任务栏按钮（需要移除）。</summary>
    public const int WsExAppWindow = 0x00040000;

    /// <summary>WS_EX_NOACTIVATE：点击不激活窗口。</summary>
    public const int WsExNoActivate = 0x08000000;

    public const uint SwpNoSize = 0x0001;
    public const uint SwpNoMove = 0x0002;
    public const uint SwpNoZOrder = 0x0004;
    public const uint SwpNoActivate = 0x0010;
    public const uint SwpFrameChanged = 0x0020;
    public const uint SwpShowWindow = 0x0040;

    /// <summary>HWND_TOPMOST。</summary>
    public static readonly IntPtr HwndTopMost = new(-1);

    /// <summary>DWMWA_WINDOW_CORNER_PREFERENCE。</summary>
    private const int DwmwaWindowCornerPreference = 33;

    /// <summary>DWMWCP_ROUND。</summary>
    private const int DwmwcpRound = 2;

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr GetWindowLongPtr(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll", EntryPoint = "SetWindowPos", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPosNative(
        IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint uFlags);

    [DllImport("user32.dll", EntryPoint = "ShowWindow", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShowWindowNative(IntPtr hWnd, int nCmdShow);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    /// <summary>取 WinUI 3 窗口的 HWND。</summary>
    public static IntPtr GetHandle(Window window)
        => WinRT.Interop.WindowNative.GetWindowHandle(window);

    public static IntPtr GetExStyle(IntPtr hWnd) => GetWindowLongPtr(hWnd, GwlExStyle);

    public static void SetExStyle(IntPtr hWnd, IntPtr value)
        => SetWindowLongPtr(hWnd, GwlExStyle, value);

    /// <summary>让样式修改立即生效（不移动、不改大小、不改 z-order、不激活）。</summary>
    public static void ApplyFrameChange(IntPtr hWnd)
        => SetWindowPosNative(hWnd, IntPtr.Zero, 0, 0, 0, 0,
            SwpNoMove | SwpNoSize | SwpNoZOrder | SwpNoActivate | SwpFrameChanged);

    /// <summary>
    /// 同步悬浮窗扩展样式：
    ///  - 始终清除 WS_EX_APPWINDOW（不占任务栏，仍可被 OBS 等窗口捕获识别）；
    ///  - 固定时置位 WS_EX_TRANSPARENT（鼠标穿透），否则清除。
    /// </summary>
    public static void ApplyFloatingStyles(IntPtr hWnd, bool clickThrough)
    {
        if (hWnd == IntPtr.Zero) return;

        var current = GetExStyle(hWnd).ToInt64();
        var updated = current & ~(long)WsExAppWindow;
        updated = clickThrough
            ? updated | WsExTransparent
            : updated & ~(long)WsExTransparent;

        if (updated == current) return;

        SetExStyle(hWnd, new IntPtr(updated));
        ApplyFrameChange(hWnd);
    }

    /// <summary>显示窗口但不抢焦点（对应旧版 ShowWindow(SW_SHOWNOACTIVATE)）。</summary>
    public static void ShowWithoutActivating(IntPtr hWnd)
    {
        if (hWnd == IntPtr.Zero) return;
        ShowWindowNative(hWnd, 4 /* SW_SHOWNOACTIVATE */);
        SetWindowPosNative(hWnd, HwndTopMost, 0, 0, 0, 0,
            SwpNoMove | SwpNoSize | SwpNoActivate | SwpShowWindow);
    }

    /// <summary>Windows 11 圆角；不支持的平台静默忽略。</summary>
    public static void TryEnableRoundedCorners(IntPtr hWnd)
    {
        if (hWnd == IntPtr.Zero) return;
        try
        {
            var preference = DwmwcpRound;
            DwmSetWindowAttribute(hWnd, DwmwaWindowCornerPreference, ref preference, sizeof(int));
        }
        catch
        {
            // Windows 10 不支持，忽略
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT
    {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetCursorPos(out POINT point);

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);

    private const int SmXVirtualScreen = 76;
    private const int SmYVirtualScreen = 77;
    private const int SmCxVirtualScreen = 78;
    private const int SmCyVirtualScreen = 79;

    /// <summary>当前光标的屏幕坐标（物理像素）。</summary>
    public static (int X, int Y) GetCursorScreenPoint()
        => GetCursorPos(out var point) ? (point.X, point.Y) : (0, 0);

    /// <summary>虚拟桌面（多屏合并）边界，对应 WPF 的 SystemParameters.VirtualScreen*。</summary>
    public static (int Left, int Top, int Width, int Height) GetVirtualScreenBounds()
        => (GetSystemMetrics(SmXVirtualScreen),
            GetSystemMetrics(SmYVirtualScreen),
            GetSystemMetrics(SmCxVirtualScreen),
            GetSystemMetrics(SmCyVirtualScreen));
}
