"""The director's phase machine and results writer, on a simulated clock.

the design requirements -- but with no ROS and no waiting, so it runs
in CI in milliseconds instead of the minutes the real run takes.
"""

import csv
import json
import os

import pytest

from mars_rig.director_core import Catalog, DirectorCore

CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, "config", "prompt_catalog_x2.json")


@pytest.fixture
def catalog():
    return Catalog(os.path.normpath(CATALOG))


def drive(core, score_for, step=0.5, limit=4000):
    """Run the core on a fake clock, feeding whatever score_for() returns."""
    state = {"t": 0.0}
    sent = []
    core.emit = lambda text, prompt: sent.append((round(state["t"], 2),
                                                  prompt["id"]))
    for _ in range(limit):
        if core.phase == "done":
            break
        value = score_for(core.current["id"] if core.current else None)
        if value is not None:
            core.add_score(value)
        core.tick(state["t"])
        state["t"] += step
    return sent, state["t"]


def test_catalog_matches_the_spec(catalog):
    assert len(catalog.by_id) == 45
    assert len(catalog.ladder) == 23
    assert catalog.ladder[0] == "B00"          # baseline first, always
    assert "rover" in catalog.banned           # the marker incident, section 3.5
    assert catalog.boundary_zh.startswith("只改变地面和天空")


def test_prompt_text_is_forwarded_verbatim(catalog):
    """A single wrong word invalidates the run (section 5.3), so nothing may
    rewrite the catalog string on the way out."""
    p = catalog.by_id["B00"]
    assert catalog.text_for(p, "zh") == p["zh"]
    assert catalog.text_for(p, "en") == p["en"]
    # An unknown language falls back to English rather than sending nothing.
    assert catalog.text_for(p, "fr") == p["en"]
    assert catalog.boundary_zh in catalog.text_for(p, "zh")


def test_sweep_walks_every_condition_and_writes_results(catalog, tmp_path):
    core = DirectorCore(catalog, mode="sweep", settle_sec=2.0, dwell_sec=4.0,
                        out_dir=str(tmp_path))
    sent, _ = drive(core, lambda cid: 0.5)

    assert core.phase == "done"
    assert len(core.results) == 23
    assert [s[1] for s in sent] == catalog.ladder

    csv_path = tmp_path / "results.csv"
    md_path = tmp_path / "results.md"
    assert csv_path.exists() and md_path.exists()

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 23
    assert rows[0]["id"] == "B00"
    assert float(rows[0]["mean_iou"]) == pytest.approx(0.5)
    assert int(rows[0]["n_samples"]) > 0

    md = md_path.read_text(encoding="utf-8")
    assert "| B00 | mars_baseline |" in md
    assert "**Baseline:**" in md and "**Worst condition:**" in md


def test_settle_samples_are_discarded(catalog, tmp_path):
    """Scores collected before the prompt lands belong to the PREVIOUS
    condition. If settle did not discard them the first row would be a blend."""
    core = DirectorCore(catalog, mode="sweep", settle_sec=4.0, dwell_sec=4.0,
                        out_dir=str(tmp_path))
    core.emit = lambda text, prompt: None

    t = 0.0
    for _ in range(60):
        # 0.9 while the prompt is still settling, 0.1 once it has landed.
        core.add_score(0.9 if t < 4.0 else 0.1)
        core.tick(t)
        t += 0.5
        if core.results:
            break
    assert core.results[0]["mean_iou"] == pytest.approx(0.1, abs=1e-6)


def test_missing_scores_are_recorded_as_nan_with_a_warning(catalog, tmp_path):
    warnings = []
    core = DirectorCore(catalog, mode="sweep", settle_sec=1.0, dwell_sec=2.0,
                        out_dir=str(tmp_path),
                        log=lambda lvl, msg: warnings.append((lvl, msg)))
    drive(core, lambda cid: None, limit=40)
    assert core.results, "should still record a row rather than hanging"
    assert core.results[0]["n_samples"] == 0
    assert any(lvl == "warn" and "no scores received" in msg
               for lvl, msg in warnings)


def test_adaptive_stops_once_the_stack_breaks(catalog, tmp_path):
    core = DirectorCore(catalog, mode="adaptive", settle_sec=1.0, dwell_sec=2.0,
                        out_dir=str(tmp_path), break_below=0.25)

    def score_for(cid):
        # High until the third condition, then a collapse.
        return 0.9 if len(core.tried) < 3 else 0.05

    drive(core, score_for, step=0.5, limit=400)
    assert core.phase == "done"
    assert core.results[-1]["mean_iou"] < 0.25
    assert len(core.results) < 23, "should stop early, not sweep everything"


def test_adaptive_holds_position_while_the_score_is_middling(catalog, tmp_path):
    """Between break_below and escalate_above the ladder does not advance --
    the condition is doing something and is worth another dwell."""
    core = DirectorCore(catalog, mode="adaptive", settle_sec=1.0, dwell_sec=2.0,
                        out_dir=str(tmp_path),
                        escalate_above=0.75, break_below=0.25)
    drive(core, lambda cid: 0.5, step=0.5, limit=60)
    assert len(set(core.tried)) == 1, f"advanced to {core.tried}"


def test_status_is_json_safe(catalog, tmp_path):
    import json
    core = DirectorCore(catalog, mode="sweep", out_dir=str(tmp_path))
    core.tick(0.0)
    payload = json.loads(json.dumps(core.status(1.0)))
    assert payload["total"] == 23
    assert payload["current_id"] == "B00"
    assert payload["phase"] == "settle"


def test_adaptive_warns_when_it_can_never_escalate(catalog, tmp_path):
    """A baseline at or below escalate_above means the ladder never advances.
    Nothing is broken, so nothing errors -- the run just sits on B00 until
    someone kills it. The warning is the only thing standing between that and a
    wasted demo slot."""
    logs = []
    core = DirectorCore(catalog, mode="adaptive", settle_sec=1.0, dwell_sec=2.0,
                        out_dir=str(tmp_path), escalate_above=0.75,
                        break_below=0.25,
                        log=lambda lvl, msg: logs.append((lvl, msg)))
    drive(core, lambda cid: 0.6, step=0.5, limit=40)
    assert any(lvl == "warn" and "never advance past" in msg
               for lvl, msg in logs), logs


def test_sweep_does_not_warn_about_escalation(catalog, tmp_path):
    """Sweep ignores escalate_above entirely, so the warning would be noise."""
    logs = []
    core = DirectorCore(catalog, mode="sweep", settle_sec=1.0, dwell_sec=2.0,
                        out_dir=str(tmp_path), escalate_above=0.75,
                        log=lambda lvl, msg: logs.append((lvl, msg)))
    drive(core, lambda cid: 0.6, step=0.5, limit=40)
    assert not any("never advance past" in msg for _, msg in logs)


def test_conditions_inside_the_baseline_range_are_marked(catalog, tmp_path):
    """A condition that moves the score by less than the baseline's own
    frame-to-frame spread has not been shown to do anything. Reporting it as a
    drop is how a rig ends up claiming results it cannot support."""
    core = DirectorCore(catalog, mode="sweep", settle_sec=1.0, dwell_sec=3.0,
                        out_dir=str(tmp_path))

    # Baseline wobbles between 0.60 and 0.80; everything after sits at 0.70,
    # comfortably inside that band.
    state = {"n": 0}

    def score_for(cid):
        state["n"] += 1
        if cid == "B00":
            return 0.60 if state["n"] % 2 else 0.80
        return 0.70

    drive(core, score_for, step=0.5, limit=200)

    assert core.noise_band() == pytest.approx(0.20, abs=1e-6)
    assert core.results[0]["within_baseline_noise"] is False, "baseline sets the band"
    assert all(r["within_baseline_noise"] for r in core.results[1:6])

    md = (tmp_path / "results.md").read_text(encoding="utf-8")
    assert "†" in md
    assert "has not been shown to do anything" in md
    assert "**Conditions that measurably degraded perception:** 0 of" in md


def test_a_real_collapse_is_not_marked_as_noise(catalog, tmp_path):
    core = DirectorCore(catalog, mode="sweep", settle_sec=1.0, dwell_sec=3.0,
                        out_dir=str(tmp_path))

    def score_for(cid):
        return 0.80 if cid == "B00" else 0.05

    drive(core, score_for, step=0.5, limit=200)
    assert not core.results[1]["within_baseline_noise"]
    md = (tmp_path / "results.md").read_text(encoding="utf-8")
    assert "| B00 | mars_baseline |" in md


def test_per_class_iou_is_recorded_and_reported(catalog, tmp_path):
    """The mean cannot express obstacle blindness -- drivable is trivially easy,
    so it holds the mean up around 0.45 no matter how completely perception
    stops seeing rocks. The per-class column is what makes that visible."""
    core = DirectorCore(catalog, mode="sweep", settle_sec=1.0, dwell_sec=3.0,
                        out_dir=str(tmp_path))

    def score_for(cid):
        # Ground perfect, rocks entirely missed, from the second condition on.
        obstacle = 0.9 if cid == "B00" else 0.0
        core.add_detail({"per_class": {"drivable": 0.96, "obstacle": obstacle}})
        return (0.96 + obstacle) / 2

    drive(core, score_for, step=0.5, limit=400)

    assert core.results[0]["per_class"]["obstacle"] == pytest.approx(0.9)
    assert core.results[1]["per_class"]["obstacle"] == pytest.approx(0.0)
    assert core.results[1]["mean_iou"] > 0.45, "the mean stays middling -- the point"

    md = (tmp_path / "results.md").read_text(encoding="utf-8")
    assert "| drivable | obstacle |" in md
    assert "Obstacle-blind in" in md
    assert "Read the obstacle column, not the mean" in md

    rows = list(csv.DictReader((tmp_path / "results.csv").open(encoding="utf-8")))
    assert "iou_obstacle" in rows[0] and "iou_drivable" in rows[0]
    assert float(rows[1]["iou_obstacle"]) == pytest.approx(0.0)


def test_detail_accepts_json_and_ignores_rubbish(catalog, tmp_path):
    """Reporting must never be able to stop a run that is otherwise scoring."""
    core = DirectorCore(catalog, mode="sweep", settle_sec=0.0, dwell_sec=2.0,
                        out_dir=str(tmp_path))
    core.emit = lambda text, prompt: None
    core.tick(0.0)
    core.tick(0.1)                                  # settle -> dwell
    core.add_detail('{"per_class": {"obstacle": 0.4}}')   # raw JSON string
    core.add_detail("not json at all")
    core.add_detail({"per_class": {"obstacle": None}})     # class was skipped
    core.add_detail({"per_class": {"obstacle": "nan-ish"}})
    core.add_detail(None)
    assert core.detail_samples == {"obstacle": [0.4]}


def test_absent_class_leaves_a_blank_cell_not_a_zero(catalog, tmp_path):
    """A class the scorer skipped has no IoU. Zero is a score; absence is not,
    and a CSV must not make them look alike."""
    core = DirectorCore(catalog, mode="sweep", settle_sec=0.0, dwell_sec=2.0,
                        out_dir=str(tmp_path))
    seen = {"n": 0}

    def score_for(cid):
        seen["n"] += 1
        # Only the first condition ever sees an obstacle.
        per = {"drivable": 0.9}
        if cid == "B00":
            per["obstacle"] = 0.5
        core.add_detail({"per_class": per})
        return 0.7

    drive(core, score_for, step=0.5, limit=60)
    rows = list(csv.DictReader((tmp_path / "results.csv").open(encoding="utf-8")))
    assert rows[0]["iou_obstacle"] != ""
    assert rows[1]["iou_obstacle"] == "", "absent must be blank, never 0"
    md = (tmp_path / "results.md").read_text(encoding="utf-8")
    assert "-" in md


def test_an_unscored_run_says_so_instead_of_reporting_nan(catalog, tmp_path):
    """A run that measured nothing must not look like a run that measured NaN.

    This is the shape of a real evening: the camera topic was wrong, no ground
    truth existed, /score/iou never published, and the report came out as a
    table of `nan` with a "worst condition" picked arbitrarily -- NaN compares
    False against everything, so `min()` returns whatever came first.
    """
    core = DirectorCore(catalog, mode="sweep", settle_sec=1.0, dwell_sec=2.0,
                        out_dir=str(tmp_path), log=lambda lvl, msg: None)
    drive(core, lambda cid: None, limit=200)

    assert core.results, "unscored conditions still get a row"
    assert all(r["scored"] is False for r in core.results)

    report = (tmp_path / "results.md").read_text(encoding="utf-8")
    assert "NO SCORES IN THIS RUN" in report
    assert "nan" not in report.lower(), "NaN must never reach the report"
    assert "**Worst condition:**" not in report, \
        "no condition can be 'worst' when none was measured"

    # NaN is not valid JSON: json.dumps writes a bare NaN and the panel's
    # JSON.parse rejects the whole message, freezing the director's table.
    status = json.dumps(core.status(now=1.0))
    assert "NaN" not in status
    assert json.loads(status)["last_iou"] is None

    with open(tmp_path / "results.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["mean_iou"] == "", "an unmeasured cell is empty, not 'nan'"
