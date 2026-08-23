#!/usr/bin/env python3
"""
director_core.py -- the director's decision loop, with no rclpy in it.

prompt_director.py is a thin ROS wrapper over this file. The logic lives here
so the sweep that produces our deliverable can be run and tested without a ROS
graph, without Unity and without spending credits -- see tools/offline_loop.py
and mars_rig/test/test_director_core.py.

Behaviour is identical to the in-node version it was lifted from: same phase
machine, same escalation rules, same results.csv and results.md.

TIMING
------
X2 applies prompts at generation-chunk boundaries, so every condition needs a
settle period before its scores mean anything. settle_sec is discarded,
dwell_sec is measured. Do not shorten settle_sec to save time -- you will be
scoring the previous condition.

Time is passed in rather than read, so the caller decides whether that is the
ROS clock, the wall clock, or a simulated one.
"""

from __future__ import annotations

import csv
import json
import os
import statistics
import urllib.error
import urllib.request
from datetime import datetime

SYSTEM = """You choose test conditions for a Mars terrain simulator used to
stress-test a rover's perception system.

Reply with JSON only: {"condition": "<short_name>", "zh": "<prompt>"}

The prompt must be concise Simplified Chinese and must:
- change ONLY ground/sky material, colour, lighting, atmosphere or optics
- NEVER name any object (no rover, rock, marker, crater, dune, building)
- end with: 保留所有物体的形状、结构、数量和位置，保留画面构图、地平线和镜头运动。

Aim for a condition likely to degrade perception while staying physically
plausible for Mars. Do not repeat a condition already tried."""


class Catalog:
    """The prompt catalog and its escalation ladder."""

    def __init__(self, path: str):
        if not os.path.exists(path):
            raise FileNotFoundError(f"catalog not found: {path}")
        with open(path, encoding="utf-8") as fh:
            cat = json.load(fh)
        self.by_id = {p["id"]: p for p in cat["prompts"]}
        self.ladder = [i for i in cat["escalation_ladder"]["order"]
                       if i in self.by_id]
        self.banned = cat["banned_words"]["words"]
        self.boundary_zh = cat["_readme"]["preservation_boundary"]["zh"]

    def text_for(self, prompt: dict, lang: str) -> str:
        """The exact string that goes to X2.

        Forwarded verbatim from here on. A single wrong word invalidates the
        run (BUILD_SPEC 5.3), so nothing downstream edits this.
        """
        text = prompt.get(lang) or prompt["en"]
        if prompt.get("prefix"):
            text = prompt["prefix"] + "\n" + text
        return text


class DirectorCore:
    """Phase machine over the ladder. Emits prompts, records scores.

    The owner wires two things: `emit`, called with (text, prompt_dict) when a
    condition should be sent, and `log`, called with (level, message).
    """

    def __init__(
        self,
        catalog: Catalog,
        mode: str = "sweep",
        lang: str = "zh",
        settle_sec: float = 8.0,
        dwell_sec: float = 25.0,
        out_dir: str = "results",
        escalate_above: float = 0.75,
        break_below: float = 0.50,
        emit=None,
        log=None,
        llm=None,
    ):
        self.catalog = catalog
        self.mode = mode
        self.lang = lang
        self.settle_sec = float(settle_sec)
        self.dwell_sec = float(dwell_sec)
        self.out_dir = out_dir
        self.escalate_above = float(escalate_above)
        self.break_below = float(break_below)
        self.emit = emit or (lambda text, prompt: None)
        self.log = log or (lambda level, msg: None)
        self.llm = llm

        os.makedirs(self.out_dir, exist_ok=True)

        self.idx = -1                # position in the ladder
        self.current = None          # prompt dict being tested
        self.phase = "idle"          # idle | settle | dwell | done
        self.phase_start = 0.0
        self.samples: list[float] = []
        # Per-class IoU, from /score/detail. Kept alongside the mean rather than
        # instead of it: the mean is what drives escalation, but it cannot fall
        # below about 0.45 while drivable stays easy, so a run where perception
        # went completely obstacle-blind still reads as "degraded but working".
        # The per-class column is what makes that visible in the table.
        self.detail_samples: dict[str, list[float]] = {}
        self.results: list[dict] = []
        self.tried: list[str] = []

    @property
    def ladder(self):
        return self.catalog.ladder

    # ---------- inputs ----------

    def add_score(self, value: float) -> None:
        """A sample from /score/iou. Only dwell-phase samples count."""
        if self.phase == "dwell":
            self.samples.append(float(value))

    def add_detail(self, payload) -> None:
        """A sample from /score/detail: {"per_class": {name: iou or null}}.

        Accepts the dict or the raw JSON string, and ignores anything it cannot
        read. This is reporting, not control -- a malformed detail message must
        never be able to stop a run that is otherwise scoring fine.
        """
        if self.phase != "dwell":
            return
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return
        if not isinstance(payload, dict):
            return
        for name, value in (payload.get("per_class") or {}).items():
            if value is None:      # class absent from both images -- skipped
                continue
            try:
                self.detail_samples.setdefault(str(name), []).append(float(value))
            except (TypeError, ValueError):
                continue

    def tick(self, now: float) -> None:
        if self.phase == "done":
            return
        if self.phase == "idle":
            self.phase_start = now
            self._advance(now)
            return

        elapsed = now - self.phase_start
        if self.phase == "settle" and elapsed >= self.settle_sec:
            # Discard anything scored before the prompt landed.
            self.samples = []
            self.detail_samples = {}
            self.phase, self.phase_start = "dwell", now
        elif self.phase == "dwell" and elapsed >= self.dwell_sec:
            self._record()
            self._advance(now)

    # ---------- the loop ----------

    def _advance(self, now: float) -> None:
        if self.mode == "sweep":
            self.idx += 1
            if self.idx >= len(self.ladder):
                return self._finish("swept every condition")
            nxt = self.catalog.by_id[self.ladder[self.idx]]

        else:  # adaptive / llm
            last = self.results[-1]["mean_iou"] if self.results else None
            if last is not None and last < self.break_below:
                return self._finish(
                    f"stack broke at {self.results[-1]['id']} "
                    f"(IoU {last:.3f})")
            if last is None or last > self.escalate_above:
                self.idx += 1
            if self.idx >= len(self.ladder):
                return self._finish("exhausted the ladder without breaking")
            if self.mode == "llm" and self.llm is not None:
                nxt = self.llm(last, self.tried) or \
                    self.catalog.by_id[self.ladder[self.idx]]
            else:
                nxt = self.catalog.by_id[self.ladder[self.idx]]

        self._send(nxt, now)

    def _send(self, prompt: dict, now: float) -> None:
        self.emit(self.catalog.text_for(prompt, self.lang), prompt)
        self.current = prompt
        self.tried.append(prompt["id"])
        self.samples = []
        self.detail_samples = {}
        self.phase, self.phase_start = "settle", now
        self.log("info",
                 f"-> [{prompt['id']}] {prompt['name']} "
                 f"(diff {prompt['difficulty']})")

    def _record(self) -> None:
        if not self.samples:
            self.log("warn",
                     f"[{self.current['id']}] no scores received. Nothing "
                     f"published on /score/iou during the dwell -- most often "
                     f"there is no ground-truth label topic, so scoring_node "
                     f"has nothing to compare against. Run `./rig topics`.")
            mean = median = lo = hi = float("nan")
        else:
            mean = statistics.fmean(self.samples)
            median = statistics.median(self.samples)
            lo, hi = min(self.samples), max(self.samples)

        base = self.results[0]["mean_iou"] if self.results else mean
        drop = (base - mean) / base * 100 if base else 0.0

        row = {
            "id": self.current["id"],
            "name": self.current["name"],
            "family": self.current["family"],
            "difficulty": self.current["difficulty"],
            "mean_iou": round(mean, 4),
            "median_iou": round(median, 4),
            "min_iou": round(lo, 4),
            "max_iou": round(hi, 4),
            "n_samples": len(self.samples),
            # Explicit, because NaN spreads: every derived number below is NaN
            # too, and a results table full of "nan" reads as a broken script
            # rather than as "this run measured nothing".
            "scored": bool(self.samples),
            "drop_vs_baseline_pct": round(drop, 1),
            "within_baseline_noise": self._within_noise(mean),
            "per_class": {
                name: round(statistics.fmean(vals), 4)
                for name, vals in sorted(self.detail_samples.items()) if vals
            },
            "hypothesis": self.current.get("hypothesis", ""),
        }
        self.results.append(row)

        # Adaptive only escalates while the score is ABOVE escalate_above. If
        # the very first condition already scores below it, the ladder never
        # advances and the run sits on the baseline until something kills it --
        # with no error, because nothing is broken, the rule just never fires.
        # This is the trap that eats a demo slot, so say so on the first row.
        if (self.mode in ("adaptive", "llm") and len(self.results) == 1
                and mean == mean and mean <= self.escalate_above):
            self.log("warn",
                     f"baseline scored {mean:.3f}, at or below escalate_above="
                     f"{self.escalate_above}. Adaptive will never advance past "
                     f"[{self.current['id']}]. Lower escalate_above below the "
                     f"measured ceiling, or run mode:=sweep.")

        # Spelled out because the sign reads backwards at a glance: `drop` is
        # positive when the score FELL, so "+97%" is a near-total failure, not
        # an improvement.
        per_class = "  ".join(f"{k} {v:.3f}" for k, v in row["per_class"].items())
        if not row["scored"]:
            # "IoU nan (drop +nan%)" is worse than useless: it looks like a
            # formatting bug and hides the fact that nothing was measured.
            self.log("info", f"   {row['id']}: NO SCORE (n=0)")
        else:
            self.log("info",
                     f"   {row['id']}: IoU {mean:.3f} "
                     f"(drop {drop:+.1f}% vs baseline, n={len(self.samples)})"
                     + (f"  [{per_class}]" if per_class else ""))
        self.write()

    def _within_noise(self, mean: float) -> bool:
        """Is this condition's move smaller than the baseline's own wobble?

        The baseline is one condition scored over many frames, and those frames
        are not equivalent: the rover is moving, so rock count, size and
        distance change under it. That spread is the noise floor of the whole
        experiment. A condition that moves the mean by less than it has not been
        shown to do anything -- the same number would come out of rerunning the
        baseline on a different stretch of the path.

        Reported rather than hidden. Dropping these rows would be worse: a
        condition that does nothing is a real result, and the honest way to say
        it is "below our resolution", not to leave it out.
        """
        if not self.results or mean != mean:      # baseline itself, or NaN
            return False
        base = self.results[0]
        band = base["max_iou"] - base["min_iou"]
        return abs(base["mean_iou"] - mean) <= band

    def noise_band(self) -> float:
        """The baseline's min-to-max spread: the smallest drop worth claiming."""
        if not self.results:
            return 0.0
        return self.results[0]["max_iou"] - self.results[0]["min_iou"]

    def _finish(self, why: str) -> None:
        self.phase = "done"
        self.write()
        self.log("info", f"FINISHED: {why}")
        self.log("info", f"results in {self.out_dir}/")

    # ---------- output ----------

    def class_names(self) -> list[str]:
        """Every class seen across the run, in a stable order.

        The union rather than the first row's keys: a class absent from one
        condition's frames is skipped by the scorer and simply does not appear
        in that row, and a CSV whose columns change halfway down is not a CSV.
        """
        names: set[str] = set()
        for r in self.results:
            names.update(r.get("per_class", {}))
        return sorted(names)

    @staticmethod
    def _num(value, spec: str = ".3f") -> str:
        """Format a score, or an em dash if there is not one.

        NaN means "not measured", and it must not be able to reach a table, a
        headline or a claim looking like a number.
        """
        try:
            if value != value:              # NaN
                return "—"
            return format(float(value), spec)
        except (TypeError, ValueError):
            return "—"

    def write(self) -> None:
        if not self.results:
            return

        names = self.class_names()
        scalar = [k for k in self.results[0] if k not in ("per_class", "hypothesis")]
        cols = scalar + [f"iou_{n}" for n in names] + ["hypothesis"]

        with open(f"{self.out_dir}/results.csv", "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in self.results:
                # NaN in a CSV cell reads as a number that went wrong. An
                # empty cell reads as "not measured", which is the truth.
                flat = {k: ("" if isinstance(r[k], float) and r[k] != r[k]
                            else r[k])
                        for k in scalar}
                # Blank, not zero, for a class this condition never saw. Zero is
                # a score; absence is not, and the two must not look alike.
                for n in names:
                    flat[f"iou_{n}"] = r.get("per_class", {}).get(n, "")
                flat["hypothesis"] = r["hypothesis"]
                w.writerow(flat)

        lines = [
            "# Perception stress-test results",
            "",
            f"Generated {datetime.now():%Y-%m-%d %H:%M} · mode `{self.mode}` · "
            f"settle {self.settle_sec}s · dwell {self.dwell_sec}s",
            "",
            "| ID | Condition | Diff | Mean IoU |"
            + "".join(f" {n} |" for n in names)
            + " Range | Drop | n |",
            "|---|---|---|---|" + "---|" * len(names) + "---|---|---|",
        ]
        # A run where nothing was scored is a real outcome, and it needs to be
        # the first thing read -- not inferred from a column of em dashes.
        if not any(r.get("scored", True) for r in self.results):
            lines[1:1] = [
                "",
                "> **NO SCORES IN THIS RUN.** Nothing was published on "
                "`/score/iou` for any condition, so every row below is "
                "unscored and no claim can be made from this file. The usual "
                "cause is a missing ground-truth label topic: `scoring_node` "
                "needs a semantic-label image as well as the prediction. Run "
                "`./rig topics` to see which side is missing.",
            ]

        band = self.noise_band()
        for r in self.results:
            mark = " †" if r.get("within_baseline_noise") else ""
            per = "".join(
                f" {self._num(r['per_class'][n])} |" if n in r.get("per_class", {})
                else " — |"
                for n in names)
            # One dash, not "—–—" and "—%": an unscored row should read as
            # empty at a glance, not as broken formatting.
            rng = (f"{self._num(r['min_iou'])}–{self._num(r['max_iou'])}"
                   if r.get("scored", True) else "—")
            drop = (f"{self._num(r['drop_vs_baseline_pct'], '+.1f')}%"
                    if r.get("scored", True) else "—")
            lines.append(
                f"| {r['id']} | {r['name']} | {r['difficulty']} | "
                f"{self._num(r['mean_iou'])}{mark} |"
                + per
                + f" {rng} | {drop} | {r['n_samples']} |")

        quiet = [r for r in self.results[1:] if r.get("within_baseline_noise")]
        if band > 0:
            lines += [
                "",
                f"† Moved the score by less than the baseline's own frame-to-frame "
                f"range ({band:.3f}), so this condition has not been shown to do "
                f"anything. The rover is moving, so rock count, size and distance "
                f"change from frame to frame even with no condition applied — that "
                f"spread is the resolution limit of the whole experiment.",
            ]
            if quiet:
                lines.append(
                    f"{len(quiet)} of {len(self.results) - 1} conditions are below "
                    f"that limit. Claiming a drop smaller than {band:.3f} needs a "
                    f"longer dwell, not a bolder sentence.")

        # Every headline below compares scores. With none, min() picks an
        # arbitrary row (NaN comparisons are all False) and the file states a
        # "worst condition" that was never measured -- so it is skipped.
        scored_rows = [r for r in self.results[1:] if r.get("scored", True)]
        if (len(self.results) > 1 and scored_rows
                and self.results[0].get("scored", True)):
            worst = min(scored_rows, key=lambda r: r["mean_iou"])
            real = [r for r in scored_rows
                    if not r.get("within_baseline_noise")]
            lines += [
                "", f"**Baseline:** {self.results[0]['name']} at "
                    f"IoU {self._num(self.results[0]['mean_iou'])} "
                    f"(range {self._num(self.results[0]['min_iou'])}–"
                    f"{self._num(self.results[0]['max_iou'])})",
                f"**Worst condition:** {worst['name']} at "
                f"IoU {self._num(worst['mean_iou'])} "
                f"({self._num(worst['drop_vs_baseline_pct'], '+.1f')}%)",
                f"**Conditions that measurably degraded perception:** "
                f"{len(real)} of {len(self.results) - 1}",
            ]
            blind = [r for r in self.results[1:]
                     if r.get("per_class", {}).get("obstacle", 1.0) <= 0.05]
            if blind:
                lines += [
                    "",
                    f"**Obstacle-blind in {len(blind)} condition(s):** "
                    + ", ".join(r["name"] for r in blind)
                    + ". Read the obstacle column, not the mean. Mean IoU cannot "
                    "fall much below 0.45 while drivable stays easy, so a run "
                    "where perception found no rocks at all still reports a "
                    "middling headline number. For a rover, obstacle IoU at zero "
                    "is the whole story.",
                ]
            lines += ["", f"> {worst['hypothesis']}"]
        with open(f"{self.out_dir}/results.md", "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def status(self, now: float) -> dict:
        return {
            "mode": self.mode,
            "phase": self.phase,
            "current_id": self.current["id"] if self.current else None,
            "current_name": self.current["name"] if self.current else None,
            "difficulty": self.current["difficulty"] if self.current else None,
            "elapsed": round(now - self.phase_start, 1),
            "completed": len(self.results),
            "total": len(self.ladder),
            # NEVER NaN. `json.dumps` emits a bare `NaN`, which is not valid
            # JSON: the panel's JSON.parse throws on it and the whole status
            # message is dropped, so the director's table silently freezes on
            # the last good value from the moment the first condition goes
            # unscored. None round-trips, and the panel already renders it.
            "last_iou": self._or_none(
                self.results[-1]["mean_iou"] if self.results else None),
        }

    @staticmethod
    def _or_none(value):
        """NaN -> None, so this can cross a JSON boundary."""
        return None if value is None or value != value else value


class OllamaProposer:
    """Local LLM condition proposer, with the catalog as the fallback.

    Any failure returns None and the caller falls back to the ladder. That is
    deliberate: a run that silently continues on a malformed or off-policy
    prompt produces numbers nobody can defend.
    """

    def __init__(self, catalog: Catalog, url: str, model: str,
                 log=None, timeout: float = 10.0):
        self.catalog = catalog
        self.url = url
        self.model = model
        self.timeout = timeout
        self.log = log or (lambda level, msg: None)

    def __call__(self, last_score, tried):
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps({
                    "current_iou": last_score,
                    "already_tried": tried,
                }, ensure_ascii=False)},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
        }, ensure_ascii=False).encode("utf-8")

        try:
            req = urllib.request.Request(
                self.url, data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
            parsed = json.loads(payload["message"]["content"])
            zh = str(parsed["zh"]).strip()
            name = str(parsed.get("condition", "llm")).strip()
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError,
                OSError) as exc:
            self.log("warn", f"LLM unavailable ({exc}); using catalog")
            return None

        # Guard rails. A banned word silently invalidates every IoU number,
        # so reject rather than repair.
        low = zh.lower()
        for word in self.catalog.banned:
            if word.lower() in low:
                self.log("warn", f"LLM used banned word '{word}'; using catalog")
                return None
        if self.catalog.boundary_zh not in zh:
            zh = zh.rstrip("。") + "。" + self.catalog.boundary_zh

        return {"id": f"LLM{len(tried):02d}", "name": name,
                "family": "LLM", "difficulty": -1, "zh": zh, "en": "",
                "hypothesis": "generated by local LLM"}
