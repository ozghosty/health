// Loads data.json (built from config.yaml + data/health.json by scripts/build_site.py)
// and renders the profile, tiles, charts and daily-log heat map.
(async function () {
  const H = await (await fetch("data.json", { cache: "no-cache" })).json();
  const P = H.profile, G = H.goals, T = H.thresholds;
  window.renderChrome(H.site);

  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const fmt = (v, dp = 0) => v == null || !isFinite(v) ? "—" : (+v).toFixed(dp);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const mean = (arr) => { const v = arr.filter((x) => x != null); return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null; };
  // Only the birth year is stored, so age may read one year high before the birthday
  const age = () => new Date().getFullYear() - P.birth_year;

  // First band whose `below` the value is under wins; a band without `below` catches the rest
  const severity = ["good", "warning", "serious", "critical"];
  function classify(metric, value) {
    const t = T[metric];
    if (!t || value == null) return null;
    let v = value;
    if (t.mode === "delta_from_goal") v = value - G.weight_kg;
    if (t.mode === "percent_of_age_max") v = (value / (220 - age())) * 100;
    const band = t.bands.find((b) => b.below == null || v < b.below);
    return band ? { key: band.level, label: band.label } : null;
  }
  // AHA rule: a reading takes the worse of its systolic and diastolic category
  function bpCategory(sys, dia) {
    const a = classify("bp_systolic", sys), b = classify("bp_diastolic", dia);
    if (!a || !b) return a || b;
    return severity.indexOf(b.key) > severity.indexOf(a.key) ? b : a;
  }

  // One entry per calendar day up to the latest data, so gaps show as gaps
  const byDate = Object.fromEntries(H.days.map((d) => [d.date, d]));
  const days = [];
  if (H.days.length) {
    const first = new Date(H.days[0].date + "T00:00:00"), last = new Date(H.days.at(-1).date + "T00:00:00");
    for (let d = first; d <= last; d.setDate(d.getDate() + 1)) {
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      days.push(byDate[key] || { date: key });
    }
  }
  const lastN = (n) => days.slice(-n);
  const day = (s, o) => new Date(s + "T00:00:00").toLocaleDateString("en-AU", o);

  const bp = H.blood_pressure || {};
  const estScore = (H.sleep || {}).score_source === "estimate";
  document.title = H.dashboard?.title || "Health Dashboard";
  document.getElementById("kicker").innerHTML = ["Health dashboard", ...H.sources.map((s) => s === "fitbit" ? "Fitbit" : "Withings")]
    .map(esc).join("<span>·</span>");
  document.getElementById("name").textContent = P.name;
  document.getElementById("facts").innerHTML = [
    ["Age", age()], ["Sex", P.sex ? P.sex[0].toUpperCase() + P.sex.slice(1) : null],
    ["Height", P.height_cm && P.height_cm + " cm"], ["Goal weight", G.weight_kg && G.weight_kg + " kg"],
    ["Conditions", (P.conditions || []).join(", ")], ["Medications", (P.medications || []).join("; ")],
  ].filter(([, v]) => v).map(([k, v]) => `<span>${k} <b>${esc(v)}</b></span>`).join("");
  document.getElementById("updated").textContent = H.updated ? "Updated " + new Date(H.updated).toLocaleString("en-AU", { timeZone: P.timezone, dateStyle: "medium", timeStyle: "short", timeZoneName: "short" }) : "";
  document.getElementById("bp-note").textContent = (bp.method === "all"
    ? "Each point is the average of all readings in that session."
    : `Each point is the average of the ${bp.keep ?? 2} lowest readings in that session.`)
    + ` Shaded band = normal (<${G.bp_systolic ?? 120} / <${G.bp_diastolic ?? 80}).`;
  const sleepGoal = G.sleep_hours ?? 7;
  document.getElementById("sleep-note").textContent = `hours asleep per night · dashed = ${sleepGoal} h goal`;

  if (!days.length) {
    document.getElementById("empty").hidden = false;
    document.querySelectorAll(".tiles, .charts, .log").forEach((e) => (e.hidden = true));
    return;
  }

  const canvases = Object.fromEntries(["bp", "weight", "hr", "hrv", "sleep"].map((id) => [id, document.getElementById(id)]));
  let period = H.dashboard?.default_period_days || 30;
  document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", +b.dataset.n === period));

  let charts = [];
  function render(n) {
    const cur = lastN(n), prev = days.slice(-2 * n, -n);
    const avg = (arr, f) => mean(arr.map(f));
    const amS = (d) => d.bpAm?.sys, amD = (d) => d.bpAm?.dia, pmS = (d) => d.bpPm?.sys, pmD = (d) => d.bpPm?.dia;

    // lowerIsBetter decides whether a fall is coloured good or bad
    function delta(f, dp, lowerIsBetter, unit = "") {
      const a = avg(cur, f), b = avg(prev, f);
      if (a == null || b == null || !prev.length) return "";
      const d = a - b, cls = Math.abs(d) < 10 ** -dp ? "flat" : (d < 0) === lowerIsBetter ? "good" : "bad";
      return `<span class="delta ${cls}">${d > 0 ? "▲" : d < 0 ? "▼" : "•"} ${Math.abs(d).toFixed(dp)}${unit}</span> vs prior ${n}d`;
    }
    const pill = (c) => c ? `<span class="pill ${c.key}"><i></i>${esc(c.label)}</span>` : "";
    const vals = (f) => cur.map(f).filter((v) => v != null);

    const amSys = avg(cur, amS), amDia = avg(cur, amD), pmSys = avg(cur, pmS), pmDia = avg(cur, pmD);
    const latestW = [...cur].reverse().find((d) => d.weightKg);
    const lows = vals((d) => d.minHr), highs = vals((d) => d.maxHr);
    const tiles = [
      { label: "Weight", value: fmt(latestW?.weightKg, 1), unit: "kg", sub: delta((d) => d.weightKg, 1, true, " kg"), extra: `BMI ${fmt(latestW?.bmi, 1)}<br>` + pill(classify("bmi", latestW?.bmi)) },
      { label: "BP · morning", value: `${fmt(amSys)}/${fmt(amDia)}`, unit: "mmHg", sub: delta(amS, 0, true), extra: pill(bpCategory(amSys, amDia)) },
      { label: "BP · evening", value: `${fmt(pmSys)}/${fmt(pmDia)}`, unit: "mmHg", sub: delta(pmS, 0, true), extra: pill(bpCategory(pmSys, pmDia)) },
      { label: "Resting HR", value: fmt(avg(cur, (d) => d.restingHr)), unit: "bpm", sub: delta((d) => d.restingHr, 0, true) },
      { label: "HR low / high", value: `${lows.length ? Math.min(...lows) : "—"}/${highs.length ? Math.max(...highs) : "—"}`, unit: "bpm", sub: `avg nightly low ${fmt(avg(cur, (d) => d.minHr))}` },
      { label: "HRV", value: fmt(avg(cur, (d) => d.hrv)), unit: "ms", sub: delta((d) => d.hrv, 0, false, " ms") },
      { label: "Sleep", value: fmt(avg(cur, (d) => d.sleepHrs), 1), unit: "h", sub: delta((d) => d.sleepHrs, 1, false, " h"), extra: `Score ${fmt(avg(cur, (d) => d.sleepScore))}${estScore ? " (est.)" : ""}` },
    ];
    document.getElementById("tiles").innerHTML = tiles.map((t) => `
      <div class="card tile"><div class="label">${t.label}</div>
        <div class="value">${t.value} <small>${t.unit}</small></div>
        <div class="sub">${t.sub || "&nbsp;"}</div>${t.extra ? `<div class="sub">${t.extra}</div>` : ""}</div>`).join("");

    charts.forEach((c) => c.destroy()); charts = [];
    const labels = cur.map((d) => day(d.date, { day: "numeric", month: "short" }));
    Chart.defaults.font.family = css("--font");
    Chart.defaults.color = css("--text-faint");
    const base = (y = {}) => ({
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: { backgroundColor: css("--bg-elev"), borderColor: css("--border"), borderWidth: 1, titleColor: css("--text"), bodyColor: css("--text-muted"), padding: 10, cornerRadius: 10 } },
      scales: {
        x: { grid: { display: false }, border: { color: css("--axis") }, ticks: { maxTicksLimit: 8 } },
        y: { grid: { color: css("--grid") }, border: { display: false }, ...y },
      },
    });
    const line = (label, data, color, o = {}) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: n <= 7 ? 4 : 0, pointHoverRadius: 5, tension: 0.3, spanGaps: true, ...o });

    // shaded normal band for BP
    const bandPlugin = (lo, hi) => ({ id: "band", beforeDatasetsDraw(c) {
      const { ctx, chartArea: a, scales: { y } } = c; ctx.save(); ctx.fillStyle = css("--band");
      const top = y.getPixelForValue(hi), bot = y.getPixelForValue(lo); ctx.fillRect(a.left, top, a.width, bot - top); ctx.restore(); } });

    charts.push(new Chart(canvases.bp, { type: "line", data: { labels, datasets: [
      line("AM systolic", cur.map(amS), css("--s1")), line("PM systolic", cur.map(pmS), css("--s2")),
      line("AM diastolic", cur.map(amD), css("--s1"), { borderDash: [4, 3] }), line("PM diastolic", cur.map(pmD), css("--s2"), { borderDash: [4, 3] }),
    ] }, options: base({ suggestedMin: 60, suggestedMax: 150 }), plugins: [bandPlugin(60, G.bp_systolic ?? 120)] }));

    charts.push(new Chart(canvases.weight, { type: "line", data: { labels, datasets: [line("Weight kg", cur.map((d) => d.weightKg), css("--s1"))] }, options: base() }));

    charts.push(new Chart(canvases.hr, { type: "line", data: { labels, datasets: [
      line("Highest", cur.map((d) => d.maxHr), css("--axis"), { borderWidth: 1, pointRadius: 0, fill: "+1", backgroundColor: css("--grid") }),
      line("Lowest", cur.map((d) => d.minHr), css("--axis"), { borderWidth: 1, pointRadius: 0 }),
      line("Resting", cur.map((d) => d.restingHr), css("--s1")),
    ] }, options: base({ suggestedMin: 40 }) }));

    charts.push(new Chart(canvases.hrv, { type: "line", data: { labels, datasets: [line("HRV ms", cur.map((d) => d.hrv), css("--s7"))] }, options: base() }));

    const goalLine = { id: "ref", afterDatasetsDraw(c) { const { ctx, chartArea: a, scales: { y } } = c, py = y.getPixelForValue(sleepGoal);
      ctx.save(); ctx.strokeStyle = css("--text-muted"); ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke(); ctx.restore(); } };
    charts.push(new Chart(canvases.sleep, { type: "bar", data: { labels, datasets: [{ label: "Hours", data: cur.map((d) => d.sleepHrs), backgroundColor: css("--s3"), borderRadius: 4, borderSkipped: "bottom", maxBarThickness: 18 }] },
      options: base({ suggestedMin: 0, suggestedMax: 9 }), plugins: [goalLine] }));

    // table, most recent first
    const cell = (text, c) => c ? `<td class="${c.key}" title="${esc(c.label)}">${text}</td>` : `<td class="na">${text && text !== "—" ? text : "—"}</td>`;
    const heat = (metric, v, dp = 0) => cell(fmt(v, dp), classify(metric, v));
    const bpCell = (s) => s ? cell(`${s.sys}/${s.dia}`, bpCategory(s.sys, s.dia)) : cell();
    const rows = [...cur].reverse().map((d) => `<tr><td class="date">${day(d.date, { weekday: "short", day: "numeric", month: "short" })}</td>
      ${heat("weight_kg", d.weightKg, 1)}${heat("bmi", d.bmi, 1)}${bpCell(d.bpAm)}${bpCell(d.bpPm)}
      ${heat("resting_hr", d.restingHr)}${heat("min_hr", d.minHr)}${heat("max_hr", d.maxHr)}${heat("hrv", d.hrv)}
      ${heat("sleep_hours", d.sleepHrs, 1)}${heat("sleep_score", d.sleepScore)}${heat("spo2", d.spo2, 1)}</tr>`).join("");
    document.getElementById("log").innerHTML = `<thead>
      <tr class="group"><th></th><th colspan="2">Body</th><th colspan="2">BP mmHg</th><th colspan="4">Heart</th><th colspan="3">Sleep</th></tr>
      <tr><th>Date</th><th>Weight</th><th>BMI</th><th>AM</th><th>PM</th><th>Resting</th><th>Low</th><th>High</th><th>HRV</th><th>Hours</th><th>Score${estScore ? " (est.)" : ""}</th><th>SpO₂</th></tr></thead><tbody>${rows}</tbody>`;
  }

  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    render(period = +b.dataset.n);
  }));
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => render(period));
  document.addEventListener("themechange", () => render(period));
  render(period);
})();
