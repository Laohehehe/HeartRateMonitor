using System.Diagnostics;
using System.IO;
using System.Text;

namespace HeartRateMonitor.Core;

/// <summary>
/// 日志系统（对应 Python: heart/logger_setup.py）。
/// 写入 %TEMP%\heart_yyyy-MM-dd_HH-mm-ss.log；DEBUG 构建下同时输出到调试器。
/// 线程安全，进程内单例。
/// </summary>
public static class AppLogger
{
    private static readonly object Gate = new();
    private static StreamWriter? _writer;
    private static bool _initialized;

    /// <summary>当前日志文件完整路径。</summary>
    public static string FilePath { get; private set; } = "";

    private static void EnsureInitialized()
    {
        if (_initialized) return;
        lock (Gate)
        {
            if (_initialized) return;
            try
            {
                var dir = Path.GetTempPath();
                FilePath = Path.Combine(dir, $"heart_{DateTime.Now:yyyy-MM-dd_HH-mm-ss}.log");
                _writer = new StreamWriter(
                    new FileStream(FilePath, FileMode.Create, FileAccess.Write, FileShare.Read),
                    new UTF8Encoding(false)) { AutoFlush = true };
            }
            catch
            {
                FilePath = "";
            }
            _initialized = true;
        }
        Info($"日志文件: {FilePath}");
    }

    private static void Write(string level, string message, Exception? ex = null)
    {
        EnsureInitialized();
        var line = $"{DateTime.Now:HH:mm:ss} [{level}] {message}";
        if (ex != null) line += Environment.NewLine + ex;
        lock (Gate)
        {
            try { _writer?.WriteLine(line); } catch { /* 日志失败不影响主流程 */ }
        }
        System.Diagnostics.Debug.WriteLine(line);
    }

    public static void Debug(string message) => Write("DEBUG", message);
    public static void Info(string message) => Write("INFO", message);
    public static void Warn(string message) => Write("WARNING", message);
    public static void Warn(string message, Exception ex) => Write("WARNING", message, ex);
    public static void Error(string message) => Write("ERROR", message);
    public static void Error(string message, Exception ex) => Write("ERROR", message, ex);
    public static void Critical(string message, Exception? ex = null) => Write("CRITICAL", message, ex);

    /// <summary>关闭日志文件句柄（退出清理前调用，否则 Windows 下文件被占用无法删除）。</summary>
    public static void Shutdown()
    {
        lock (Gate)
        {
            try { _writer?.Flush(); _writer?.Dispose(); } catch { }
            _writer = null;
            _initialized = false; // 允许关闭后不再重开已删除的文件
        }
    }
}
