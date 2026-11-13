using System;
using System.Collections.Generic;
using System.Globalization;
using HeartRateMonitor.Core;
using HeartRateMonitor.Services;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.Foundation;

namespace HeartRateMonitor.Views;

/// <summary>
/// 心率曲线页（原独立 ChartWindow 改为主窗口内的一页，随侧边栏切换）。
///
/// 算法与视觉参数沿用归档版 ChartCanvas：
///  1. 数据不足 2 点时居中提示，不画网格；
///  2. 三点滑动平均（首尾保留原值，中间银行家舍入）；
///  3. 网格 5 横 7 竖、最高/最低虚线参考线；
///  4. Fritsch–Carlson 单调三次样条（含 alpha²+beta²≤9 限幅）→ 贝塞尔曲线 #FF69B4 2px + 数据点；
///  5. 最高/最低标签、Y 轴 5 档刻度、X 轴首尾时间；
///  6. 悬停 30px 内最近点 → 白色虚线竖线 + 深色圆角 tooltip（越界翻转）。
/// </summary>
public sealed partial class ChartPage : Page
{
    private const double OverlayLeft = 60;
    private const double OverlayRight = 20;
    private const double OverlayTop = 30;
    private const double OverlayBottom = 40;
    private const double SmallFontSize = 12;
    private const double HintFontSize = 16;
    private const double HoverRadius = 30;

    /// <summary>超过该点数就不再逐个画圆点（每个 Ellipse 都是独立 UI 元素）。</summary>
    private const int DotRenderLimit = 300;

    private readonly DispatcherTimer _timer;

    private readonly SolidColorBrush _gridBrush;
    private readonly SolidColorBrush _referenceBrush;
    private readonly SolidColorBrush _curveBrush;
    private readonly SolidColorBrush _tickBrush;
    private readonly SolidColorBrush _hintBrush;
    private readonly SolidColorBrush _hoverBrush;
    private readonly SolidColorBrush _tooltipBackgroundBrush;

    private List<Point> _hitPoints = new();
    private List<HeartRateRecord> _hitRecords = new();
    private List<int> _hitValues = new();
    private int _hoverIndex = -1;

    public ChartPage()
    {
        InitializeComponent();

        _gridBrush = new SolidColorBrush(ColorHelper.FromArgb(255, 0x3C, 0x3C, 0x3C));
        _referenceBrush = new SolidColorBrush(ColorHelper.FromArgb(80, 255, 100, 100));
        _curveBrush = new SolidColorBrush(ColorHelper.FromArgb(255, 0xFF, 0x69, 0xB4));
        _tickBrush = new SolidColorBrush(ColorHelper.FromArgb(255, 180, 180, 180));
        _hintBrush = new SolidColorBrush(ColorHelper.FromArgb(255, 150, 150, 150));
        _hoverBrush = new SolidColorBrush(ColorHelper.FromArgb(100, 255, 255, 255));
        _tooltipBackgroundBrush = new SolidColorBrush(ColorHelper.FromArgb(220, 50, 50, 50));

        // 旧版用 1000ms 计时器驱动重绘
        _timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _timer.Tick += (_, _) => Redraw();

        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        _timer.Start();
        Redraw();
    }

    private void OnUnloaded(object sender, RoutedEventArgs e) => _timer.Stop();

    private HeartRateHistory History => AppHost.Instance.History;

    private void OnPlotSizeChanged(object sender, SizeChangedEventArgs e) => Redraw();

    // ---------------- 绘制 ----------------

    private void Redraw()
    {
        if (PlotCanvas is null) return;

        var width = PlotCanvas.ActualWidth;
        var height = PlotCanvas.ActualHeight;
        if (width <= 0 || height <= 0) return;

        PlotCanvas.Children.Clear();
        _hitPoints = new List<Point>();
        _hitRecords = new List<HeartRateRecord>();
        _hitValues = new List<int>();

        var snapshot = History.Snapshot();
        var items = snapshot.Items;

        // 数据不足：居中灰字，不画网格
        if (items.Count < 2)
        {
            _hoverIndex = -1;
            var hint = CreateText("数据不足，至少需要两个数据点", HintFontSize, _hintBrush);
            Place(hint, (width - hint.DesiredSize.Width) / 2, (height - hint.DesiredSize.Height) / 2);
            return;
        }

        var maxValue = snapshot.Max ?? 0;
        var minValue = snapshot.Min ?? 0;
        if (maxValue == minValue) maxValue += 1;

        var plotLeft = OverlayLeft;
        var plotTop = OverlayTop;
        var plotWidth = width - OverlayLeft - OverlayRight;
        var plotHeight = height - OverlayTop - OverlayBottom;
        if (plotWidth <= 0 || plotHeight <= 0) return;

        var plotRight = plotLeft + plotWidth;
        var plotBottom = plotTop + plotHeight;

        // 1) 网格：5 横 7 竖
        for (var i = 0; i < 5; i++)
        {
            var y = Snap(plotTop + plotHeight * i / 4.0);
            AddLine(plotLeft, y, plotRight, y, _gridBrush, 1);
        }

        for (var i = 0; i < 7; i++)
        {
            var x = Snap(plotLeft + plotWidth * i / 6.0);
            AddLine(x, plotTop, x, plotBottom, _gridBrush, 1);
        }

        // 2) 最高/最低虚线参考线
        AddLine(plotLeft, Snap(plotTop), plotRight, Snap(plotTop), _referenceBrush, 1, new[] { 4.0, 3.0 });
        AddLine(plotLeft, Snap(plotBottom), plotRight, Snap(plotBottom), _referenceBrush, 1, new[] { 4.0, 3.0 });

        // 3) 平滑 + 绘图坐标
        var values = Smooth(items);
        var points = new List<Point>(items.Count);
        for (var i = 0; i < items.Count; i++)
        {
            var x = plotLeft + (double)i / (items.Count - 1) * plotWidth;
            var y = plotTop + (maxValue - values[i]) / (double)(maxValue - minValue) * plotHeight;
            points.Add(new Point(x, y));
            _hitRecords.Add(items[i]);
        }

        _hitPoints = points;
        _hitValues = new List<int>(values);

        // 4) 样条曲线 + 数据点
        AddCurve(points);
        if (points.Count <= DotRenderLimit)
        {
            foreach (var point in points) AddDot(point, 3);
        }

        // 5) 最高/最低标签
        var maxLabel = CreateText($"最高: {maxValue}", SmallFontSize, _curveBrush);
        Place(maxLabel, plotLeft + 5, plotTop - 6 - maxLabel.DesiredSize.Height);

        var minLabel = CreateText($"最低: {minValue}", SmallFontSize, _curveBrush);
        Place(minLabel, plotLeft + 5, plotBottom + 2);

        // 6) Y 轴 5 档刻度
        for (var i = 0; i < 5; i++)
        {
            var value = maxValue - (maxValue - minValue) * i / 4.0;
            var y = plotTop + plotHeight * i / 4.0;
            var tick = CreateText(value.ToString("0", CultureInfo.InvariantCulture), SmallFontSize, _tickBrush);
            Place(tick, 5, y - tick.DesiredSize.Height / 2);
        }

        // 7) X 轴首尾时间
        var firstTime = CreateText(
            items[0].Timestamp.ToString("HH:mm:ss", CultureInfo.InvariantCulture), SmallFontSize, _tickBrush);
        Place(firstTime, plotLeft, plotBottom + 20);

        var lastTime = CreateText(
            items[items.Count - 1].Timestamp.ToString("HH:mm:ss", CultureInfo.InvariantCulture), SmallFontSize, _tickBrush);
        Place(lastTime, plotRight - lastTime.DesiredSize.Width, plotBottom + 20);

        // 8) 悬停
        if (_hoverIndex >= 0 && _hoverIndex < points.Count)
        {
            var hoverX = Snap(points[_hoverIndex].X);
            AddLine(hoverX, plotTop, hoverX, plotBottom, _hoverBrush, 1, new[] { 4.0, 3.0 });
            DrawTooltip(points[_hoverIndex], _hitRecords[_hoverIndex], values[_hoverIndex],
                plotLeft, plotTop, plotWidth, width, height);
        }
    }

    private void DrawTooltip(
        Point anchor,
        HeartRateRecord record,
        int value,
        double plotLeft,
        double plotTop,
        double plotWidth,
        double canvasWidth,
        double canvasHeight)
    {
        var line1 = CreateText(
            $"时间: {record.Timestamp.ToString("HH:mm:ss", CultureInfo.InvariantCulture)}", SmallFontSize, new SolidColorBrush(Colors.White));
        var line2 = CreateText($"心率: {value} bpm", SmallFontSize, new SolidColorBrush(Colors.White));

        var textWidth = Math.Max(line1.DesiredSize.Width, line2.DesiredSize.Width);
        var lineHeight = Math.Max(line1.DesiredSize.Height, line2.DesiredSize.Height);
        var textHeight = lineHeight * 2;

        var x = anchor.X + 10;
        var y = anchor.Y - textHeight - 10;

        if (x + textWidth > plotLeft + plotWidth) x = anchor.X - textWidth - 10;
        if (y < plotTop) y = anchor.Y + 10;

        const double pad = 5;
        x = Math.Clamp(x, pad, Math.Max(pad, canvasWidth - textWidth - pad));
        y = Math.Clamp(y, pad, Math.Max(pad, canvasHeight - textHeight - pad));

        var background = new Border
        {
            Background = _tooltipBackgroundBrush,
            CornerRadius = new CornerRadius(5),
            Padding = new Thickness(pad),
            Child = new StackPanel { Children = { line1, line2 } }
        };

        Place(background, x - pad, y - pad);
    }

    // ---------------- 悬停交互 ----------------

    private void OnPlotPointerMoved(object sender, PointerRoutedEventArgs e)
    {
        if (_hitPoints.Count < 2) return;

        var position = e.GetCurrentPoint(PlotCanvas).Position;
        var bestIndex = -1;
        var bestDistance = HoverRadius;

        for (var i = 0; i < _hitPoints.Count; i++)
        {
            var dx = position.X - _hitPoints[i].X;
            var dy = position.Y - _hitPoints[i].Y;
            var distance = Math.Sqrt(dx * dx + dy * dy);
            if (distance >= bestDistance) continue;

            bestDistance = distance;
            bestIndex = i;
        }

        if (bestIndex == _hoverIndex) return;

        _hoverIndex = bestIndex;
        Redraw();
    }

    private void OnPlotPointerExited(object sender, PointerRoutedEventArgs e)
    {
        if (_hoverIndex < 0) return;

        _hoverIndex = -1;
        Redraw();
    }

    // ---------------- 算法 ----------------

    private static int[] Smooth(IReadOnlyList<HeartRateRecord> items)
    {
        var count = items.Count;
        var values = new int[count];
        for (var i = 0; i < count; i++)
        {
            if (i == 0 || i == count - 1)
            {
                values[i] = items[i].Value;
                continue;
            }

            var average = (items[i - 1].Value + items[i].Value + items[i + 1].Value) / 3.0;
            values[i] = (int)Math.Round(average);
        }

        return values;
    }

    private static PathGeometry BuildSplineGeometry(IReadOnlyList<Point> points)
    {
        var figure = new PathFigure
        {
            StartPoint = points[0],
            IsFilled = false,
            IsClosed = false
        };

        if (points.Count == 2)
        {
            figure.Segments.Add(new LineSegment { Point = points[1] });
        }
        else
        {
            var slopes = ComputeMonotoneSlopes(points);
            for (var i = 0; i < points.Count - 1; i++)
            {
                var start = points[i];
                var end = points[i + 1];
                var dx = end.X - start.X;
                var control1 = new Point(start.X + dx / 3.0, start.Y + slopes[i] * dx / 3.0);
                var control2 = new Point(end.X - dx / 3.0, end.Y - slopes[i + 1] * dx / 3.0);
                figure.Segments.Add(new BezierSegment
                {
                    Point1 = control1,
                    Point2 = control2,
                    Point3 = end
                });
            }
        }

        var geometry = new PathGeometry();
        geometry.Figures.Add(figure);
        return geometry;
    }

    private static double[] ComputeMonotoneSlopes(IReadOnlyList<Point> points)
    {
        var count = points.Count;

        var delta = new double[count - 1];
        for (var i = 0; i < count - 1; i++)
        {
            var dx = points[i + 1].X - points[i].X;
            delta[i] = dx == 0 ? 0 : (points[i + 1].Y - points[i].Y) / dx;
        }

        var slopes = new double[count];
        slopes[0] = delta[0];
        slopes[count - 1] = delta[count - 2];
        for (var i = 1; i < count - 1; i++)
        {
            slopes[i] = delta[i - 1] * delta[i] > 0 ? (delta[i - 1] + delta[i]) / 2.0 : 0;
        }

        for (var i = 0; i < count - 1; i++)
        {
            if (Math.Abs(delta[i]) < 1e-9)
            {
                slopes[i] = 0;
                slopes[i + 1] = 0;
                continue;
            }

            var alpha = slopes[i] / delta[i];
            var beta = slopes[i + 1] / delta[i];
            if (alpha < 0 || beta < 0)
            {
                slopes[i] = 0;
                slopes[i + 1] = 0;
            }
            else if (alpha * alpha + beta * beta > 9)
            {
                var tau = 3.0 / Math.Sqrt(alpha * alpha + beta * beta);
                slopes[i] = tau * alpha * delta[i];
                slopes[i + 1] = tau * beta * delta[i];
            }
        }

        return slopes;
    }

    // ---------------- 绘制辅助 ----------------

    private TextBlock CreateText(string text, double fontSize, Brush foreground)
    {
        var block = new TextBlock
        {
            Text = text,
            FontSize = fontSize,
            Foreground = foreground,
            IsTextScaleFactorEnabled = false
        };

        block.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
        return block;
    }

    private void Place(UIElement element, double x, double y)
    {
        Canvas.SetLeft(element, x);
        Canvas.SetTop(element, y);
        PlotCanvas.Children.Add(element);
    }

    private void AddLine(double x1, double y1, double x2, double y2, Brush stroke, double thickness, double[]? dashes = null)
    {
        var line = new Line
        {
            X1 = x1,
            Y1 = y1,
            X2 = x2,
            Y2 = y2,
            Stroke = stroke,
            StrokeThickness = thickness
        };

        if (dashes is not null)
        {
            var dashArray = new DoubleCollection();
            foreach (var dash in dashes) dashArray.Add(dash);
            line.StrokeDashArray = dashArray;
        }

        PlotCanvas.Children.Add(line);
    }

    private void AddCurve(IReadOnlyList<Point> points)
    {
        var path = new Microsoft.UI.Xaml.Shapes.Path
        {
            Data = BuildSplineGeometry(points),
            Stroke = _curveBrush,
            StrokeThickness = 2,
            StrokeLineJoin = PenLineJoin.Round,
            StrokeStartLineCap = PenLineCap.Round,
            StrokeEndLineCap = PenLineCap.Round
        };

        PlotCanvas.Children.Add(path);
    }

    private void AddDot(Point point, double radius)
    {
        var dot = new Ellipse
        {
            Width = radius * 2,
            Height = radius * 2,
            Fill = _curveBrush
        };

        Canvas.SetLeft(dot, point.X - radius);
        Canvas.SetTop(dot, point.Y - radius);
        PlotCanvas.Children.Add(dot);
    }

    private static double Snap(double value) => Math.Floor(value) + 0.5;
}
