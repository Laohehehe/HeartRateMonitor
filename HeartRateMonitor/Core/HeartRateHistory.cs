namespace HeartRateMonitor.Core;

/// <summary>
/// 心率曲线用到的历史记录与极值统计（对应 Python monitor.hr_history / hr_max_value / hr_min_value）。
/// 线程安全；上限 HeartRateHistoryLimit（3600，约 1 小时）。
/// </summary>
public sealed class HeartRateHistory
{
    private readonly object _gate = new();
    private readonly List<HeartRateRecord> _items = new(512);

    public int? MaxValue { get; private set; }
    public int? MinValue { get; private set; }
    public int Count { get { lock (_gate) return _items.Count; } }

    public void Clear()
    {
        lock (_gate)
        {
            _items.Clear();
            MaxValue = null;
            MinValue = null;
        }
    }

    public void Add(int value)
    {
        lock (_gate)
        {
            _items.Add(new HeartRateRecord(DateTime.Now, value));
            if (_items.Count > AppConstants.HeartRateHistoryLimit)
                _items.RemoveRange(0, _items.Count - AppConstants.HeartRateHistoryLimit);

            MaxValue = MaxValue is null ? value : Math.Max(MaxValue.Value, value);
            MinValue = MinValue is null ? value : Math.Min(MinValue.Value, value);
        }
    }

    /// <summary>截图快照（供绘图线程读取，避免持锁绘制）。</summary>
    public (IReadOnlyList<HeartRateRecord> Items, int? Max, int? Min) Snapshot()
    {
        lock (_gate)
        {
            return (_items.Count == 0 ? Array.Empty<HeartRateRecord>() : _items.ToArray(), MaxValue, MinValue);
        }
    }
}

/// <summary>一条心率记录。</summary>
public readonly record struct HeartRateRecord(DateTime Timestamp, int Value)
{
    /// <summary>用于重绘触发的占位（WPF 中仅用于触发 InvalidateVisual）。</summary>
    public static readonly HeartRateRecord Empty = new(DateTime.MinValue, 0);
}
