using System;
using HeartRateMonitor.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace HeartRateMonitor.Views;

/// <summary>
/// 设置页：窗口背景材质切换。
///
/// 选项对应 Windows 实际支持的材质与变体（云母 / 云母 Alt / 亚克力 / 亚克力 Thin / 纯色），
/// 由 <see cref="BackdropHost"/> 通过 MicaController / DesktopAcrylicController 应用，
/// 选择写入 <c>%APPDATA%\Heart\config.ini</c> 的 <c>[App] backdrop</c>。
/// </summary>
public sealed partial class SettingsPage : Page
{
    /// <summary>与 RadioButtons 顺序一致的材质 id。</summary>
    private static readonly string[] Modes =
    {
        BackdropService.Acrylic,
        BackdropService.AcrylicThin,
        BackdropService.Mica,
        BackdropService.MicaAlt,
        BackdropService.Solid
    };

    /// <summary>每个材质的说明。</summary>
    private static readonly string[] Details =
    {
        "实时采样窗口后方内容，由背景捕获 + 模糊 + 排除混合 + 色调 + 噪点五层合成，半透明毛玻璃观感。GPU 开销最高。",
        "亚克力的薄变体：更透明、模糊更轻，噪点更不明显，适合信息密度高的界面。",
        "只在创建时取一次桌面壁纸生成色调，不采样窗口后方内容。不透明、性能最好，微软推荐作为应用底板。",
        "云母的层级变体，标题栏与内容区的层次更强；同样不采样后方窗口。",
        "不使用任何系统材质，改用主题纯色背景，最省 GPU。"
    };

    public SettingsPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        var index = Array.IndexOf(Modes, BackdropService.Current);
        BackdropOptions.SelectedIndex = index >= 0 ? index : 0;
        UpdateDetail(BackdropOptions.SelectedIndex);
    }

    private void OnBackdropSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var index = BackdropOptions.SelectedIndex;
        if (index < 0 || index >= Modes.Length) return;

        UpdateDetail(index);

        var mode = Modes[index];

        // 回填初始选择时值没有变化，不提示（否则首次进入会误报「已切换」）
        if (mode == BackdropService.Current) return;

        BackdropService.Set(mode);

        StatusBar.Message = $"已切换为：{BackdropOptions.SelectedItem}";
        StatusBar.IsOpen = true;
    }

    private void UpdateDetail(int index)
    {
        DetailText.Text = index >= 0 && index < Details.Length ? Details[index] : string.Empty;
    }
}
