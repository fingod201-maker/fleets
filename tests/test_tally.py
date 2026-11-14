import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "tally", Path(__file__).resolve().parent.parent / "verify" / "tally.py"
)
tally = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tally)


def test_truthy():
    for value in ("y", "Y", "yes", "1", "true"):
        assert tally.truthy(value)
    for value in ("n", "", "0", "no"):
        assert not tally.truthy(value)


def test_percentages_all_pass():
    rows = [
        {
            "target": "win",
            "executed": "y",
            "no_dialog": "y",
            "session_60s": "y",
            "alive24": "y",
            "alive48": "y",
            "alive72": "y",
        }
        for _ in range(10)
    ]
    result = tally.percentages(rows)
    assert result["win"]["executed"] == 1.0
    assert result["win"]["n"] == 10


def test_percentages_threshold_fail():
    rows = [
        {
            "target": "win",
            "executed": "y",
            "no_dialog": "y",
            "session_60s": "y",
            "alive24": "y",
            "alive48": "y",
            "alive72": "y",
        }
        for _ in range(8)
    ]
    rows += [
        {
            "target": "win",
            "executed": "n",
            "no_dialog": "n",
            "session_60s": "n",
            "alive24": "n",
            "alive48": "n",
            "alive72": "n",
        }
        for _ in range(2)
    ]
    result = tally.percentages(rows)["win"]
    assert result["executed"] == 0.8 < tally.THRESHOLDS["executed"]


def test_main_on_file(tmp_path, capsys):
    csv = tmp_path / "a.csv"
    header = "date,target,attempt,executed,no_dialog,session_60s,alive24,alive48,alive72,notes\n"
    row = "d,t,1,y,y,y,y,y,y,\n"
    csv.write_text(header + row * 10, encoding="utf-8")
    rc = tally.main(["tally", str(csv)])
    assert rc == 0
    assert "OVERALL: PASS" in capsys.readouterr().out
