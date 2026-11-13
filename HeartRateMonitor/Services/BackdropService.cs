using System;
using HeartRateMonitor.Core;

namespace HeartRateMonitor.Services;

/// <summary>
/// 窗口背景材质选择（<c>%APPDATA%\Heart\config.ini</c> 的 <c>[App] backdrop</c>）。
///
/// 依据 Windows 官方"材质"体系，窗口背景只有两大类材质：
///  - <b>Mica（云母）</b>：不透明，只在创建时取一次桌面壁纸生成色调，<b>不采样窗口后方内容</b>，
///    性能好，适合作为应用底板；
///  - <b>Acrylic（亚克力）</b>：半透明，实时采样窗口后方内容，由
///    「背景捕获 + 模糊 + 排除混合 + 色调 + 噪点」五层合成。
///
/// 注意：<b>「模糊」不是独立材质</b>，它只是亚克力的其中一层。
/// Windows 没有提供"无噪点纯模糊"的窗口背景材质。
///
/// 这里提供的是 Windows 实际支持的材质与变体：
///  - <c>acrylic</c>：亚克力 Base（默认）；
///  - <c>acrylic-thin</c>：亚克力 Thin，更薄更透；
///  - <c>mica</c>：云母 Base；
///  - <c>mica-alt</c>：云母 Alt（带标题栏层级的变体）；
///  - <c>solid</c>：不使用材质，回退主题纯色。
/// </summary>
public static class BackdropService
{
    public const string Acrylic = "acrylic";
    public const string AcrylicThin = "acrylic-thin";
    public const string Mica = "mica";
    public const string MicaAlt = "mica-alt";
    public const string Solid = "solid";

    /// <summary>背景设置变化（已写入配置）。</summary>
    public static event EventHandler? Changed;

    /// <summary>当前选择（非法值回落到亚克力）。</summary>
    public static string Current
    {
        get
        {
            var value = AppConfig.Backdrop;
            return IsKnown(value) ? value : Acrylic;
        }
    }

    public static bool IsKnown(string? value)
        => value is Acrylic or AcrylicThin or Mica or MicaAlt or Solid;

    /// <summary>切换背景并通知界面刷新。</summary>
    public static void Set(string value)
    {
        if (!IsKnown(value)) value = Acrylic;
        if (Current == value) return;

        AppConfig.Backdrop = value;
        AppLogger.Info($"界面背景已切换为: {value}");
        Changed?.Invoke(null, EventArgs.Empty);
    }
}
