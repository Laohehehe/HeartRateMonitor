using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.Runtime.InteropServices;
using HeartRateMonitor.Core;

namespace HeartRateMonitor.Views;

/// <summary>
/// 悬浮心率窗：真正逐像素透明的置顶叠加层（移植自归档版 Views/HeartRateWindow）。
///
/// WinUI 3 的渲染表面不输出逐像素 alpha，根节点设为透明也会被合成成黑块（已实测），
/// 因此按归档版 WPF AllowsTransparency 的原理自绘：
///   WS_EX_LAYERED 分层窗口 + GDI+ 渲染 ARGB 位图 + UpdateLayeredWindow(ULW_ALPHA)。
///
/// 动画：心形按当前 BPM 搏动，周期 = 60/BPM 秒，形如「咚—哒」双峰。
/// </summary>
internal sealed class HeartOverlayWindow : IDisposable
{
    private const string WindowClass = "HeartRateMonitor.HeartOverlay";

    /// <summary>透明悬浮窗标题。它带 WS_EX_TOOLWINDOW，不会出现在 OBS 的窗口列表里。</summary>
    private const string WindowTitle = "HeartRateOverlay";

    /// <summary>OBS 采集窗标题：加 (OBS) 后缀，方便在 OBS 窗口列表里一眼认出该选哪个。</summary>
    private const string WindowTitleObs = "HeartRateOverlay (OBS)";

    private const int Width = 220;
    private const int Height = 170;

    private const int DefaultLeft = 100;
    private const int DefaultTop = 100;
    private const int KeepVisiblePixels = 40;

    /// <summary>心形宽高（正方形绘制）。</summary>
    private const float HeartSize = 54f;

    /// <summary>心形顶部 y。</summary>
    private const float HeartTop = 22f;

    /// <summary>动画帧间隔（约 30fps）。</summary>
    private const int FrameIntervalMs = 33;

    private const int TimerId = 1;

    private static bool _classRegistered;

    // 窗口类是全进程共享的，只能注册一个 WndProc。
    // 两个悬浮窗（透明窗 / OBS 窗）共用同一个类，因此必须用静态 WndProc + 按 hwnd 分发，
    // 否则第二个窗口的消息会全部落到第一个实例上（表现为 OBS 窗永不绘制）。
    private static readonly WndProcDelegate SharedWndProc = StaticWndProc;

    private static readonly System.Collections.Generic.Dictionary<IntPtr, HeartOverlayWindow> Instances = new();
    private readonly Stopwatch _clock = Stopwatch.StartNew();

    private IntPtr _hwnd;

    /// <summary>隐藏的属主窗口：用来隐藏任务栏/Alt+Tab 项，同时保持本窗口是“普通窗口”。</summary>
    private IntPtr _ownerHwnd;
    private string _text = "--";
    private bool _disposed;
    private int? _bpm;

    /// <summary>OBS 兼容模式：不用分层窗口，改用普通窗口 + 纯色可抠底。</summary>
    private readonly bool _obsCompatible;

    /// <summary>是否读写共享的悬浮窗位置配置。OBS 输出窗不参与，避免两个窗口抢同一份坐标。</summary>
    private readonly bool _persistPosition;

    /// <summary>窗口位置（每次渲染前从系统同步，避免被外部移动后跳回）。</summary>
    private int _left;
    private int _top;

    public HeartOverlayWindow(bool obsCompatible = false, bool persistPosition = true)
    {
        _obsCompatible = obsCompatible;
        _persistPosition = persistPosition;
        EnsureClassRegistered();

        var (left, top) = ResolveInitialPosition();

        // 先建一个永不显示的属主窗口。
        // 关键：OBS 等录屏软件只枚举“普通窗口”，带 WS_EX_TOOLWINDOW 的窗口会被过滤掉；
        // 但完全不隐藏又会在任务栏/Alt+Tab 里出现。用属主窗口即可两全——
        // 这与归档版 WPF 实现（ShowInTaskbar=false 产生的隐藏属主 + 去掉 WS_EX_APPWINDOW）思路一致。
        _ownerHwnd = CreateWindowEx(
            0, WindowClass, string.Empty, WsPopup,
            0, 0, 0, 0,
            IntPtr.Zero, IntPtr.Zero, GetModuleHandle(null), IntPtr.Zero);

        // 兼容模式：普通窗口，并强制出现在任务栏（WS_EX_APPWINDOW）。
        // 原因：部分录屏 / 采集工具只枚举任务栏或 Alt+Tab 中可见的窗口；
        // 正常模式则用分层窗口保持逐像素透明。
        //
        // 透明窗额外带 WS_EX_TOOLWINDOW：OBS 枚举窗口时会跳过 Tool 窗口
        // （libobs window-helpers.c 的 check_window_valid），
        // 于是 OBS 下拉列表里只剩真正可采集的 OBS 窗，用户不会误选到采不出画面的透明窗。
        //
        // 注意 OBS 窗不带 WS_EX_TOPMOST：它只是给采集用的绿底窗，置顶会盖住桌面很扎眼；
        // 而 OBS 的 WGC 采集不受窗口被遮挡影响，所以不置顶完全不影响采集。
        var exStyle = WsExNoActivate
                      | (_obsCompatible
                          ? WsExAppWindow
                          : WsExLayered | WsExTopmost | WsExToolWindow);

        _hwnd = CreateWindowEx(
            exStyle,
            WindowClass,
            _obsCompatible ? WindowTitleObs : WindowTitle,
            WsPopup,
            left, top, Width, Height,
            _ownerHwnd, IntPtr.Zero, GetModuleHandle(null), IntPtr.Zero);

        if (_hwnd == IntPtr.Zero)
        {
            throw new InvalidOperationException($"创建悬浮窗失败（{Marshal.GetLastWin32Error()}）");
        }

        Instances[_hwnd] = this;

        _left = left;
        _top = top;

        Render();
        SetTimer(_hwnd, new IntPtr(TimerId), FrameIntervalMs, IntPtr.Zero);

        AppLogger.Info($"悬浮心率窗已创建（{left},{top}）");
    }

    /// <summary>设置数值文本（"--"、"已断连"、"ERR" 或数字）。</summary>
    public void SetHeartRateText(string text) => _text = string.IsNullOrEmpty(text) ? "--" : text;

    /// <summary>设置心率数值；null 表示无数据。</summary>
    public void SetHeartRate(int? value)
    {
        _bpm = value;
        _text = value?.ToString() ?? "--";
    }

    /// <summary>固定/取消固定：固定时鼠标穿透且不可拖动。</summary>
    public void SetFixed(bool fixedValue)
    {
        if (_hwnd == IntPtr.Zero) return;

        var style = GetWindowLongPtr(_hwnd, GwlExStyle).ToInt64();
        style = fixedValue
            ? style | WsExTransparent
            : style & ~(long)WsExTransparent;

        SetWindowLongPtr(_hwnd, GwlExStyle, new IntPtr(style));
        SetWindowPos(_hwnd, IntPtr.Zero, 0, 0, 0, 0,
            SwpNoMove | SwpNoSize | SwpNoZOrder | SwpNoActivate | SwpFrameChanged);
    }

    /// <summary>显示但不激活。</summary>
    public void ShowWithoutActivating()
    {
        if (_hwnd == IntPtr.Zero) return;

        ShowWindow(_hwnd, SwShowNoActivate);

        // 透明悬浮窗始终置顶；OBS 采集窗保持普通 Z 序（用 NOTOPMOST 显式清掉置顶属性）。
        SetWindowPos(_hwnd, _obsCompatible ? HwndNoTopMost : HwndTopMost, 0, 0, 0, 0,
            SwpNoMove | SwpNoSize | SwpNoActivate | SwpShowWindow);

        Render();
    }

    /// <summary>把当前位置写入配置。</summary>
    public void SavePosition()
    {
        if (!_persistPosition) return;
        if (_hwnd == IntPtr.Zero) return;

        if (GetWindowRect(_hwnd, out var rect))
        {
            _left = rect.Left;
            _top = rect.Top;
        }

        try
        {
            AppConfig.HeartRateWindowX = _left;
            AppConfig.HeartRateWindowY = _top;
        }
        catch (Exception ex)
        {
            AppLogger.Warn($"保存悬浮窗位置失败: {ex.Message}");
        }
    }

    public void Close()
    {
        if (_hwnd == IntPtr.Zero) return;

        KillTimer(_hwnd, new IntPtr(TimerId));
        SavePosition();
        DestroyWindow(_hwnd);
        _hwnd = IntPtr.Zero;

        if (_ownerHwnd != IntPtr.Zero)
        {
            DestroyWindow(_ownerHwnd);
            _ownerHwnd = IntPtr.Zero;
        }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;

        Close();
        AppLogger.Info("悬浮心率窗已关闭");
    }

    // ---------------- 位置 ----------------

    private (int Left, int Top) ResolveInitialPosition()
    {
        var vLeft = GetSystemMetrics(SmXVirtualScreen);
        var vTop = GetSystemMetrics(SmYVirtualScreen);
        var vWidth = GetSystemMetrics(SmCxVirtualScreen);
        var vHeight = GetSystemMetrics(SmCyVirtualScreen);

        // OBS 输出窗：不读共享配置，固定贴在「主屏」右上角。
        // 注意要用主屏尺寸而不是虚拟桌面——多显示器时虚拟桌面原点可能为负，
        // 直接把窗口放到屏幕外就采集不到了。
        if (!_persistPosition)
        {
            var screenW = GetSystemMetrics(SmCxScreen);
            var screenH = GetSystemMetrics(SmCyScreen);
            if (screenW <= 0) screenW = 1920;
            if (screenH <= 0) screenH = 1080;

            return (Math.Max(0, screenW - Width - 40), 80);
        }

        var left = AppConfig.HeartRateWindowX ?? DefaultLeft;
        var top = AppConfig.HeartRateWindowY ?? DefaultTop;

        if (vWidth > 0 && vHeight > 0)
        {
            left = Math.Max(vLeft + KeepVisiblePixels - Width, Math.Min(left, vLeft + vWidth - KeepVisiblePixels));
            top = Math.Max(vTop + KeepVisiblePixels - Height, Math.Min(top, vTop + vHeight - KeepVisiblePixels));
        }

        return (left, top);
    }

    // ---------------- 渲染 ----------------

    /// <summary>按当前时间计算动画相位后重绘。</summary>
    private void Render()
    {
        if (_hwnd == IntPtr.Zero) return;

        // OBS 兼容模式：窗口是普通窗口，走 WM_PAINT 绘制
        if (_obsCompatible)
        {
            InvalidateRect(_hwnd, IntPtr.Zero, false);
            return;
        }

        try
        {
            using var bitmap = BuildFrame(false);
            BlendToScreen(bitmap);
        }
        catch (Exception ex)
        {
            AppLogger.Error("渲染悬浮心率窗失败", ex);
        }
    }

    /// <summary>把当前一帧画到位图。opaque=true 时用纯绿底（OBS 兼容模式，供色度键抠色）。</summary>
    private Bitmap BuildFrame(bool opaque)
    {
        var seconds = _clock.Elapsed.TotalSeconds;
        var bpm = Math.Clamp(_bpm ?? 75, 30, 220);
        var beatPeriod = 60.0 / bpm;
        var phase = seconds % beatPeriod / beatPeriod;

        var bitmap = new Bitmap(Width, Height, PixelFormat.Format32bppArgb);
        using var graphics = Graphics.FromImage(bitmap);

        graphics.Clear(opaque ? ObsBackground : Color.Transparent);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
        graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;

        DrawHeart(graphics, 1f + 0.16f * PulseEnvelope(phase));
        DrawReadout(graphics, _text);

        return bitmap;
    }

    /// <summary>普通窗口模式：把一帧直接画到窗口 DC。</summary>
    private void PaintToWindow(IntPtr hwnd)
    {
        var hdc = BeginPaint(hwnd, out var paintStruct);
        try
        {
            using var bitmap = BuildFrame(true);
            using var graphics = Graphics.FromHdc(hdc);
            graphics.DrawImageUnscaled(bitmap, 0, 0);
        }
        catch (Exception ex)
        {
            AppLogger.Error("绘制悬浮心率窗失败", ex);
        }
        finally
        {
            EndPaint(hwnd, ref paintStruct);
        }
    }

    /// <summary>「咚—哒」双峰包络，phase ∈ [0,1)。</summary>
    private static float PulseEnvelope(double phase)
    {
        static double Bump(double x, double center, double width)
        {
            var t = (x - center) / width;
            return Math.Exp(-t * t);
        }

        var value = Bump(phase, 0.06, 0.055) + 0.55 * Bump(phase, 0.24, 0.075);
        return (float)Math.Min(value, 1.0);
    }

    private static void DrawHeart(Graphics graphics, float scale)
    {
        using var path = CreateHeartPath();

        var centerX = Width / 2f;
        var centerY = HeartTop + HeartSize / 2f;

        // 先把原始路径（24x24 坐标系）缩放到 HeartSize 并居中。
        // GDI+ 的变换是 p' = p × M：必须「先 Scale（Prepend）再 Translate（Append）」
        // 才能得到 p' = s*p + t；反过来会得到 s*(p+t)，心形被甩到画外。
        var source = path.GetBounds();
        using (var fit = new Matrix())
        {
            var s = HeartSize / source.Width;
            fit.Scale(s, s);
            fit.Translate(
                centerX - HeartSize / 2f - source.Left * s,
                HeartTop - source.Top * s,
                MatrixOrder.Append);
            path.Transform(fit);
        }

        // 再按搏动围绕心形中心缩放：p' = k*p + c(1-k)
        if (Math.Abs(scale - 1f) > 0.001f)
        {
            using var pulse = new Matrix();
            pulse.Scale(scale, scale);
            pulse.Translate(
                centerX * (1f - scale),
                centerY * (1f - scale),
                MatrixOrder.Append);
            path.Transform(pulse);
        }

        using var brush = new SolidBrush(Color.FromArgb(255, 0xFF, 0x4D, 0x8D));
        graphics.FillPath(brush, path);
    }

    /// <summary>读数：纯白粗体（不加任何描边/底色）。</summary>
    private void DrawReadout(Graphics graphics, string text)
    {
        using var path = new GraphicsPath();
        using var family = new FontFamily("Microsoft YaHei UI");

        path.AddString(
            text,
            family,
            (int)FontStyle.Bold,
            38f,
            new PointF(0, 0),
            StringFormat.GenericTypographic);

        var bounds = path.GetBounds();
        using (var matrix = new Matrix())
        {
            matrix.Translate(
                (Width - bounds.Width) / 2f - bounds.Left,
                112f - bounds.Height / 2f - bounds.Top);
            path.Transform(matrix);
        }

        graphics.FillPath(Brushes.White, path);
    }

    private static GraphicsPath CreateHeartPath()
    {
        var path = new GraphicsPath();
        path.StartFigure();
        path.AddLine(12f, 21.35f, 10.55f, 20.03f);
        path.AddBezier(10.55f, 20.03f, 5.4f, 15.36f, 2f, 12.28f, 2f, 8.5f);
        path.AddBezier(2f, 8.5f, 2f, 5.42f, 4.42f, 3f, 7.5f, 3f);
        path.AddBezier(7.5f, 3f, 9.24f, 3f, 10.91f, 3.81f, 12f, 5.09f);
        path.AddBezier(12f, 5.09f, 13.09f, 3.81f, 14.76f, 3f, 16.5f, 3f);
        path.AddBezier(16.5f, 3f, 19.58f, 3f, 22f, 5.42f, 22f, 8.5f);
        path.AddBezier(22f, 8.5f, 22f, 12.28f, 18.6f, 15.36f, 13.45f, 20.04f);
        path.AddLine(13.45f, 20.04f, 12f, 21.35f);
        path.CloseFigure();
        return path;
    }

    /// <summary>把 32bpp ARGB 位图以 ULW_ALPHA 推送给分层窗口（逐像素透明）。</summary>
    private void BlendToScreen(Bitmap bitmap)
    {
        // 位置以系统为准：被拖动或被外部移动后不能跳回旧坐标
        if (GetWindowRect(_hwnd, out var rect))
        {
            _left = rect.Left;
            _top = rect.Top;
        }

        var screenDc = GetDC(IntPtr.Zero);
        var memDc = CreateCompatibleDC(screenDc);
        var hBitmap = IntPtr.Zero;
        var oldBitmap = IntPtr.Zero;

        try
        {
            hBitmap = bitmap.GetHbitmap(Color.FromArgb(0));
            oldBitmap = SelectObject(memDc, hBitmap);

            var size = new Size(Width, Height);
            var source = new Point(0, 0);
            var destination = new Point(_left, _top);
            var blend = new BlendFunction
            {
                BlendOp = AcSrcOver,
                BlendFlags = 0,
                SourceConstantAlpha = 255,
                AlphaFormat = AcSrcAlpha
            };

            UpdateLayeredWindow(_hwnd, screenDc, ref destination, ref size, memDc, ref source, 0, ref blend, UlwAlpha);
        }
        finally
        {
            if (hBitmap != IntPtr.Zero)
            {
                SelectObject(memDc, oldBitmap);
                DeleteObject(hBitmap);
            }

            DeleteDC(memDc);
            ReleaseDC(IntPtr.Zero, screenDc);
        }
    }

    // ---------------- 窗口过程 ----------------

    /// <summary>静态窗口过程：按句柄把消息转给对应实例；未注册的窗口（属主窗）走默认处理。</summary>
    private static IntPtr StaticWndProc(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam)
    {
        HeartOverlayWindow? target;
        if (Instances.TryGetValue(hwnd, out target) && target is not null)
        {
            return target.HandleMessage(hwnd, message, wParam, lParam);
        }

        return DefWindowProc(hwnd, message, wParam, lParam);
    }

    private IntPtr HandleMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam)
    {
        switch (message)
        {
            case WmPaint:
                if (_obsCompatible) PaintToWindow(hwnd);
                return IntPtr.Zero;

            case WmEraseBackground:
                // 兼容模式下自己整帧重绘，避免背景先被擦成白色导致闪烁
                return _obsCompatible ? new IntPtr(1) : IntPtr.Zero;

            case WmTimer:
                Render();
                return IntPtr.Zero;

            // 整窗即拖动区域：交回系统移动，不需要自己算坐标
            case WmNcHitTest:
                return new IntPtr(HtCaption);

            case WmExitSizeMove:
                SavePosition();
                return IntPtr.Zero;

            case WmDestroy:
                KillTimer(hwnd, new IntPtr(TimerId));
                Instances.Remove(hwnd);
                if (hwnd == _hwnd) _hwnd = IntPtr.Zero;
                return IntPtr.Zero;
        }

        return DefWindowProc(hwnd, message, wParam, lParam);
    }

    private void EnsureClassRegistered()
    {
        if (_classRegistered) return;

        var windowClass = new WindowClassEx
        {
            Size = Marshal.SizeOf<WindowClassEx>(),
            Style = 0,
            WndProc = SharedWndProc,
            ClsExtra = 0,
            WndExtra = 0,
            Instance = GetModuleHandle(null),
            Icon = IntPtr.Zero,
            Cursor = LoadCursor(IntPtr.Zero, new IntPtr(IdcArrow)),
            Background = IntPtr.Zero,
            MenuName = null,
            ClassName = WindowClass,
            SmallIcon = IntPtr.Zero
        };

        if (RegisterClassEx(ref windowClass) == 0)
        {
            var error = Marshal.GetLastWin32Error();
            if (error != 1410) // ERROR_CLASS_ALREADY_EXISTS
            {
                throw new InvalidOperationException($"注册悬浮窗类失败（{error}）");
            }
        }

        _classRegistered = true;
    }

    // ---------------- Win32 声明 ----------------

    private delegate IntPtr WndProcDelegate(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

    private const int GwlExStyle = -20;
    private const int WsExLayered = 0x00080000;
    private const int WsExTopmost = 0x00000008;
    private const int WsExToolWindow = 0x00000080;

    /// <summary>WS_EX_APPWINDOW：强制在任务栏/Alt+Tab 中显示。</summary>
    private const int WsExAppWindow = 0x00040000;
    private const int WsExNoActivate = 0x08000000;
    private const int WsExTransparent = 0x00000020;

    private const int WsPopup = unchecked((int)0x80000000);
    private const int SwShowNoActivate = 4;

    private const uint SwpNoSize = 0x0001;
    private const uint SwpNoMove = 0x0002;
    private const uint SwpNoZOrder = 0x0004;
    private const uint SwpNoActivate = 0x0010;
    private const uint SwpFrameChanged = 0x0020;
    private const uint SwpShowWindow = 0x0040;

    private const uint WmNcHitTest = 0x0084;
    private const uint WmExitSizeMove = 0x0232;
    private const uint WmDestroy = 0x0002;
    private const uint WmTimer = 0x0113;
    private const uint WmPaint = 0x000F;
    private const uint WmEraseBackground = 0x0014;
    private const int HtCaption = 2;

    private const int SmCxScreen = 0;
    private const int SmCyScreen = 1;

    private const int SmXVirtualScreen = 76;
    private const int SmYVirtualScreen = 77;
    private const int SmCxVirtualScreen = 78;
    private const int SmCyVirtualScreen = 79;

    private const byte AcSrcOver = 0x00;
    private const byte AcSrcAlpha = 0x01;
    private const uint UlwAlpha = 0x00000002;
    private const int IdcArrow = 32512;

    /// <summary>OBS 窗的纯绿底色：色度键的标准键色，白字/粉心与绿色的色度差异大，抠得干净。</summary>
    private static readonly Color ObsBackground = Color.FromArgb(255, 0, 255, 0);

    private static readonly IntPtr HwndTopMost = new(-1);

    /// <summary>HWND_NOTOPMOST：把窗口放回普通 Z 序。</summary>
    private static readonly IntPtr HwndNoTopMost = new(-2);

    [StructLayout(LayoutKind.Sequential)]
    private struct Point
    {
        public int X;
        public int Y;

        public Point(int x, int y)
        {
            X = x;
            Y = y;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Size
    {
        public int Width;
        public int Height;

        public Size(int width, int height)
        {
            Width = width;
            Height = height;
        }
    }

    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    private struct BlendFunction
    {
        public byte BlendOp;
        public byte BlendFlags;
        public byte SourceConstantAlpha;
        public byte AlphaFormat;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WindowClassEx
    {
        public int Size;
        public uint Style;
        public WndProcDelegate WndProc;
        public int ClsExtra;
        public int WndExtra;
        public IntPtr Instance;
        public IntPtr Icon;
        public IntPtr Cursor;
        public IntPtr Background;
        [MarshalAs(UnmanagedType.LPWStr)] public string? MenuName;
        [MarshalAs(UnmanagedType.LPWStr)] public string? ClassName;
        public IntPtr SmallIcon;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr GetModuleHandle(string? moduleName);

    [DllImport("user32.dll", EntryPoint = "RegisterClassExW", SetLastError = true, ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern ushort RegisterClassEx(ref WindowClassEx windowClass);

    [DllImport("user32.dll", EntryPoint = "CreateWindowExW", SetLastError = true, ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateWindowEx(
        int exStyle, string className, string windowName, int style,
        int x, int y, int width, int height,
        IntPtr parent, IntPtr menu, IntPtr instance, IntPtr param);

    [DllImport("user32.dll", EntryPoint = "DefWindowProcW", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr DefWindowProc(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyWindow(IntPtr hwnd);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShowWindow(IntPtr hwnd, int command);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(
        IntPtr hwnd, IntPtr insertAfter, int x, int y, int cx, int cy, uint flags);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", ExactSpelling = true, SetLastError = true)]
    private static extern IntPtr GetWindowLongPtr(IntPtr hwnd, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", ExactSpelling = true, SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr value);

    [DllImport("user32.dll", EntryPoint = "LoadCursorW", ExactSpelling = true)]
    private static extern IntPtr LoadCursor(IntPtr instance, IntPtr cursorName);

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);

    [DllImport("user32.dll", EntryPoint = "SetTimer", SetLastError = true)]
    private static extern IntPtr SetTimer(IntPtr hwnd, IntPtr id, int interval, IntPtr callback);

    [DllImport("user32.dll", EntryPoint = "KillTimer", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool KillTimer(IntPtr hwnd, IntPtr id);

    [DllImport("user32.dll")]
    private static extern IntPtr GetDC(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateCompatibleDC(IntPtr dc);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeleteDC(IntPtr dc);

    [DllImport("gdi32.dll")]
    private static extern IntPtr SelectObject(IntPtr dc, IntPtr obj);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeleteObject(IntPtr obj);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UpdateLayeredWindow(
        IntPtr hwnd, IntPtr destinationDc, ref Point destination, ref Size size,
        IntPtr sourceDc, ref Point source, int colorKey, ref BlendFunction blend, uint flags);

    // ---- 普通窗口（OBS 兼容模式）绘制所需 ----

    [StructLayout(LayoutKind.Sequential)]
    private struct PaintStruct
    {
        public IntPtr Hdc;
        public int Erase;
        public Rect Paint;
        public int Restore;
        public int IncUpdate;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 32)]
        public byte[] Reserved;
    }

    [DllImport("user32.dll")]
    private static extern IntPtr BeginPaint(IntPtr hwnd, out PaintStruct paintStruct);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EndPaint(IntPtr hwnd, ref PaintStruct paintStruct);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool InvalidateRect(IntPtr hwnd, IntPtr rect, bool erase);
}
