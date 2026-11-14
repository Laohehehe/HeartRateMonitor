using System.IO;
using System.Text;

namespace HeartRateMonitor.Core;

/// <summary>
/// INI 配置读写（对应 Python: heart/config.py）。
/// 位置：%APPDATA%\Heart\config.ini，与 Python 版共用同一份配置（兼容 configparser 格式）。
/// </summary>
public static class AppConfig
{
    private static readonly object Gate = new();
    private static readonly Dictionary<string, Dictionary<string, string>> Data = new(StringComparer.OrdinalIgnoreCase);

    public static string ConfigDirectory { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Heart");

    public static string ConfigPath { get; } = Path.Combine(ConfigDirectory, "config.ini");

    static AppConfig() => Load();

    /// <summary>悬浮窗 OBS 兼容模式：改用普通窗口 + 纯黑底，便于 OBS 采集后抠色。</summary>
    public static bool OverlayObsCompatible
    {
        get => Get("HeartRateWindow", "obsCompatible", "false") == "true";
        set => Set("HeartRateWindow", "obsCompatible", value ? "true" : "false");
    }

    /// <summary>窗口背景效果："acrylic"（亚克力/Blur）、"mica"（云母）、"none"（纯色）。</summary>
    public static string Backdrop
    {
        get => Get("App", "backdrop", "acrylic");
        set => Set("App", "backdrop", value);
    }
    /// <summary>主题："light" 或 "dark"。</summary>
    public static string Theme
    {
        get => Get("App", "theme", "light");
        set => Set("App", "theme", value == "dark" ? "dark" : "light");
    }

    /// <summary>悬浮心率窗口左上角 X（未设置为 null）。</summary>
    public static int? HeartRateWindowX
    {
        get => GetInt("HeartRateWindow", "x");
        set => SetInt("HeartRateWindow", "x", value);
    }

    /// <summary>悬浮心率窗口左上角 Y（未设置为 null）。</summary>
    public static int? HeartRateWindowY
    {
        get => GetInt("HeartRateWindow", "y");
        set => SetInt("HeartRateWindow", "y", value);
    }

    public static string Get(string section, string key, string fallback = "")
        => Data.TryGetValue(section, out var s) && s.TryGetValue(key, out var v) ? v : fallback;

    public static int? GetInt(string section, string key)
        => int.TryParse(Get(section, key), out var v) ? v : null;

    public static void Set(string section, string key, string value)
    {
        lock (Gate)
        {
            if (!Data.TryGetValue(section, out var s)) Data[section] = s = new(StringComparer.OrdinalIgnoreCase);
            s[key] = value;
            Save();
        }
    }

    public static void SetInt(string section, string key, int? value)
    {
        if (value is null) RemoveKey(section, key);
        else Set(section, key, value.Value.ToString());
    }

    public static void RemoveKey(string section, string key)
    {
        lock (Gate)
        {
            if (Data.TryGetValue(section, out var s) && s.Remove(key)) Save();
        }
    }

    private static void Load()
    {
        lock (Gate)
        {
            Data.Clear();
            try
            {
                if (!File.Exists(ConfigPath)) return;
                string current = "";
                foreach (var raw in File.ReadAllLines(ConfigPath, Encoding.UTF8))
                {
                    var line = raw.Trim();
                    if (line.Length == 0 || line[0] is ';' or '#') continue;
                    if (line[0] == '[' && line[^1] == ']')
                    {
                        current = line[1..^1].Trim();
                        if (!Data.ContainsKey(current)) Data[current] = new(StringComparer.OrdinalIgnoreCase);
                        continue;
                    }
                    int idx = line.IndexOf('=');
                    if (idx <= 0 || current.Length == 0) continue;
                    var k = line[..idx].Trim();
                    var v = line[(idx + 1)..].Trim();
                    // 去掉行内注释（configparser 默认 inline_comment_prefixes 为 None，这里保留原值，仅去掉成对引号）
                    if (v.Length >= 2 && ((v[0] == '"' && v[^1] == '"') || (v[0] == '\'' && v[^1] == '\'')))
                        v = v[1..^1];
                    Data[current][k] = v;
                }
            }
            catch (Exception ex)
            {
                AppLogger.Warn($"读取配置文件失败: {ex.Message}");
            }
        }
    }

    private static void Save()
    {
        try
        {
            Directory.CreateDirectory(ConfigDirectory);
            var sb = new StringBuilder();
            foreach (var (section, kv) in Data)
            {
                sb.Append('[').Append(section).Append(']').AppendLine();
                foreach (var (k, v) in kv) sb.Append(k).Append(" = ").Append(v).AppendLine();
                sb.AppendLine();
            }
            File.WriteAllText(ConfigPath, sb.ToString(), new UTF8Encoding(false));
        }
        catch (Exception ex)
        {
            AppLogger.Warn($"保存配置文件失败: {ex.Message}");
        }
    }
}
