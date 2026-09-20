from pathlib import Path


def test_choch_timer_scans_after_each_closed_15m_candle():
    timer = (Path(__file__).parents[1] / "deploy" / "shanzhai-choch.timer").read_text()

    assert "OnCalendar=*-*-* *:05,20,35,50:00 Asia/Shanghai" in timer
