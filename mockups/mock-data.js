// Fake data shared by all mockups. Shapes mirror what the real fetch script
// will produce in data/health.json, so the chosen mockup can be wired up as-is.
(function () {
  let seed = 42;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const noise = (amp) => (rand() - 0.5) * 2 * amp;
  const round = (v, dp = 0) => Math.round(v * 10 ** dp) / 10 ** dp;

  const profile = {
    name: "OzGhosty",
    birthYear: 1984,
    sex: "Male",
    heightCm: 180,
    goals: { weightKg: 82, bpSys: 120, bpDia: 80, sleepHrs: 7.5 },
    devices: ["Withings Smart Body Analyzer", "Withings BPM Connect", "Fitbit Charge 6"],
  };

  // AM/PM session: 5 readings, first ones run high. Keep the two lowest
  // (by systolic) and average them.
  function bpSession(baseSys, baseDia) {
    const readings = [];
    for (let i = 0; i < 5; i++) {
      const warm = Math.max(0, 3 - i) * 4; // first readings elevated
      readings.push({
        sys: Math.round(baseSys + warm + noise(4)),
        dia: Math.round(baseDia + warm / 2 + noise(3)),
        pulse: Math.round(66 + warm / 2 + noise(4)),
      });
    }
    const used = [...readings].sort((a, b) => a.sys - b.sys).slice(0, 2);
    used.forEach((r) => (r.used = true));
    return {
      readings,
      sys: round((used[0].sys + used[1].sys) / 2),
      dia: round((used[0].dia + used[1].dia) / 2),
      pulse: round((used[0].pulse + used[1].pulse) / 2),
    };
  }

  const days = [];
  const end = new Date("2026-10-27T00:00:00");
  let weight = 86.4;
  for (let i = 89; i >= 0; i--) {
    const d = new Date(end);
    d.setDate(end.getDate() - i);
    const t = (89 - i) / 89; // 0 → 1 over the period, slow improvement
    weight += -0.035 + noise(0.25);
    const rhr = 58 - 3 * t + noise(1.5);
    const sleepHrs = 7 + noise(1.1);
    const day = {
      date: d.toISOString().slice(0, 10),
      weightKg: rand() < 0.85 ? round(weight, 1) : null, // some days not weighed
      fatPct: round(22.5 - 1.5 * t + noise(0.6), 1),
      restingHr: Math.round(rhr),
      minHr: Math.round(rhr - 9 + noise(2)),
      maxHr: Math.round(150 + noise(22)),
      hrv: Math.round(38 + 8 * t + noise(7)),
      sleepHrs: round(sleepHrs, 1),
      sleepScore: Math.round(Math.min(97, 62 + (sleepHrs - 5.5) * 9 + noise(5))),
      deepMin: Math.round(sleepHrs * 60 * (0.15 + noise(0.03))),
      remMin: Math.round(sleepHrs * 60 * (0.21 + noise(0.03))),
      spo2: round(95.8 + noise(0.9), 1),
      breathingRate: round(14.6 + noise(0.8), 1),
      steps: Math.round(8500 + noise(4000)),
      bpAm: rand() < 0.9 ? bpSession(131 - 7 * t, 84 - 4 * t) : null,
      bpPm: rand() < 0.8 ? bpSession(126 - 6 * t, 80 - 3 * t) : null,
    };
    day.bmi = day.weightKg ? round(day.weightKg / (profile.heightCm / 100) ** 2, 1) : null;
    days.push(day);
  }

  // Mirrors `thresholds:` in config.yaml. The real build converts the YAML to JSON;
  // the mockups inline it because browsers can't fetch local files over file://.
  const thresholds = {
    weight_kg: { mode: "delta_from_goal", bands: [
      { below: 1, level: "good", label: "At goal" }, { below: 3, level: "warning", label: "Near goal" },
      { below: 6, level: "serious", label: "Above goal" }, { level: "critical", label: "Well above goal" }] },
    bmi: { bands: [
      { below: 18.5, level: "warning", label: "Underweight" }, { below: 25, level: "good", label: "Healthy" },
      { below: 30, level: "warning", label: "Overweight" }, { level: "serious", label: "Obese" }] },
    bp_systolic: { bands: [
      { below: 90, level: "warning", label: "Low" }, { below: 120, level: "good", label: "Normal" },
      { below: 130, level: "warning", label: "Elevated" }, { below: 140, level: "serious", label: "Stage 1" },
      { level: "critical", label: "Stage 2" }] },
    bp_diastolic: { bands: [
      { below: 60, level: "warning", label: "Low" }, { below: 80, level: "good", label: "Normal" },
      { below: 90, level: "serious", label: "Stage 1" }, { level: "critical", label: "Stage 2" }] },
    resting_hr: { bands: [
      { below: 40, level: "warning", label: "Very low" }, { below: 70, level: "good", label: "Good" },
      { below: 85, level: "warning", label: "Raised" }, { level: "serious", label: "High" }] },
    min_hr: { bands: [
      { below: 38, level: "warning", label: "Very low" }, { below: 65, level: "good", label: "Normal" },
      { level: "warning", label: "Raised" }] },
    max_hr: { mode: "percent_of_age_max", bands: [
      { below: 85, level: "good", label: "Normal" }, { below: 95, level: "warning", label: "Near max" },
      { level: "serious", label: "At/above max" }] },
    hrv: { bands: [
      { below: 25, level: "serious", label: "Low" }, { below: 35, level: "warning", label: "Below average" },
      { level: "good", label: "Good" }] },
    sleep_hours: { bands: [
      { below: 6, level: "serious", label: "Short" }, { below: 7, level: "warning", label: "Below 7 h" },
      { below: 9.5, level: "good", label: "On target" }, { level: "warning", label: "Long" }] },
    sleep_score: { bands: [
      { below: 60, level: "serious", label: "Poor" }, { below: 80, level: "warning", label: "Fair" },
      { level: "good", label: "Good" }] },
  };
  const severity = ["good", "warning", "serious", "critical"];

  // First band whose `below` the value is under wins; a band without `below` catches the rest.
  function classify(metric, value) {
    const t = thresholds[metric];
    if (!t || value == null) return null;
    let v = value;
    if (t.mode === "delta_from_goal") v = value - profile.goals.weightKg;
    if (t.mode === "percent_of_age_max") v = (value / (220 - age())) * 100;
    const band = t.bands.find((b) => b.below == null || v < b.below);
    return { key: band.level, label: band.label };
  }
  // AHA rule: the reading takes the worse of its systolic and diastolic category
  function bpCategory(sys, dia) {
    const a = classify("bp_systolic", sys), b = classify("bp_diastolic", dia);
    if (!a || !b) return a || b;
    return severity.indexOf(b.key) > severity.indexOf(a.key) ? b : a;
  }
  const bmiCategory = (b) => classify("bmi", b);
  // Only the birth year is stored, so age may read one year high before the birthday
  const age = (at = end) => at.getFullYear() - profile.birthYear;
  const mean = (arr) => {
    const v = arr.filter((x) => x != null);
    return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null;
  };
  const lastN = (n) => days.slice(-n);

  // Mirrors `site:` in config.yaml. `repo` is not in the config: the build fills it
  // from $GITHUB_REPOSITORY, so a fork's button points at the fork.
  const site = {
    brand: "OzGhosty", brandStyle: "script", homeUrl: "https://ozghosty.github.io/",
    nav: [
      { title: "Home", url: "https://ozghosty.github.io/" },
      { title: "Blog", url: "https://ozghosty.github.io/blog/" },
      { title: "Health", url: "", active: true },
      { title: "About", url: "https://ozghosty.github.io/about/" },
    ],
    copyButton: "template", copyButtonLabel: "Make your own",
    footerNote: "Not a medical device. For discussion with a clinician.",
    repo: "ozghosty/health",
  };

  window.HEALTH = {
    profile, site, days, updated: "2026-10-27T06:00:00Z",
    thresholds, classify, bpCategory, bmiCategory, age, mean, lastN, round,
  };
})();
