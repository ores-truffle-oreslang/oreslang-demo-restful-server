use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    fs::{self, File},
    net::TcpListener,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
type Result<T> = std::result::Result<T, Box<dyn Error>>;

struct Server(Child);
impl Server {
    fn stop(&mut self) -> Result<()> {
        if self.0.try_wait()?.is_some() {
            return Ok(());
        }
        if !Command::new("kill")
            .args(["-TERM", &self.0.id().to_string()])
            .status()?
            .success()
        {
            return Err("cannot terminate profile server".into());
        }
        let started = Instant::now();
        while self.0.try_wait()?.is_none() {
            if started.elapsed() > Duration::from_secs(20) {
                self.0.kill()?;
                self.0.wait()?;
                return Err("server did not export diagnostics and terminate in 20 seconds".into());
            }
            thread::sleep(Duration::from_millis(25));
        }
        return Ok(());
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

fn summary(samples: &[f64]) -> Result<Value> {
    if samples.is_empty() || samples.iter().any(|n| !n.is_finite() || *n < 0.0) {
        return Err("invalid or empty latency samples".into());
    }
    let mut sorted = samples.to_vec();
    sorted.sort_by(f64::total_cmp);
    let index = |percent: usize| ((sorted.len() * percent + 99) / 100) - 1;
    return Ok(json!({"count": sorted.len(), "p50_ms": sorted[index(50)],
        "p95_ms": sorted[index(95)], "max_ms": sorted[sorted.len()-1]}));
}

fn records(path: &Path) -> Result<Vec<Value>> {
    let mut rows = Vec::new();
    for line in fs::read_to_string(path)?
        .lines()
        .filter(|l| l.starts_with("{\"schema\":"))
    {
        rows.push(serde_json::from_str(line)?);
    }
    return Ok(rows);
}

fn timed_rows(output: &str) -> Result<Vec<f64>> {
    if !output.contains("PASS: 10 REST requests") {
        return Err("curl sequence did not pass".into());
    }
    let mut times = Vec::new();
    for line in output.lines().filter(|l| l.contains(" -> ")) {
        let field = line
            .split_whitespace()
            .find(|p| p.starts_with("time="))
            .ok_or("missing curl timing")?;
        let seconds = field
            .strip_prefix("time=")
            .unwrap()
            .strip_suffix('s')
            .ok_or("invalid timing unit")?
            .parse::<f64>()?;
        if !seconds.is_finite() || seconds < 0.0 {
            return Err("invalid curl timing".into());
        }
        times.push(seconds * 1000.0);
    }
    if times.len() != 10 {
        return Err("curl sequence did not return exactly ten samples".into());
    }
    return Ok(times);
}

fn mode(root: &Path, report: &Path, label: &str, port: u16) -> Result<Value> {
    let dir = report.join(label);
    fs::create_dir_all(dir.join("data"))?;
    let log_path = dir.join("server.log");
    let log = File::create(&log_path)?;
    let flags = if label == "instrumented" {
        ["--core-perf", "--core-debug"]
    } else {
        ["--no-core-perf", "--no-core-debug"]
    };
    let mut server = Server(
        Command::new(root.join("scripts/run-server.sh"))
            .args(flags)
            .env("PORT", port.to_string())
            .env("DATA_DIR", dir.join("data"))
            .current_dir(root)
            .stdout(Stdio::from(log.try_clone()?))
            .stderr(Stdio::from(log))
            .spawn()?,
    );
    let started = Instant::now();
    loop {
        if fs::read_to_string(&log_path)?.contains("Listening on http://127.0.0.1:") {
            break;
        }
        if server.0.try_wait()?.is_some() || started.elapsed() > Duration::from_secs(60) {
            return Err(format!("{label} server did not start; see {}", log_path.display()).into());
        }
        thread::sleep(Duration::from_millis(50));
    }
    let mut warm = Vec::new();
    let mut raw = String::new();
    for round in 0..6 {
        let result = Command::new(root.join("scripts/curl-10.sh"))
            .env("BASE_URL", format!("http://127.0.0.1:{port}"))
            .current_dir(root)
            .output()?;
        if !result.status.success() {
            return Err(format!(
                "{label} round {round}: {}",
                String::from_utf8_lossy(&result.stderr)
            )
            .into());
        }
        let text = String::from_utf8(result.stdout)?;
        let times = timed_rows(&text)?;
        if round > 0 {
            warm.extend(times);
        }
        raw.push_str(&text);
    }
    fs::write(dir.join("curl.log"), raw)?;
    fs::write(dir.join("warm-ms.json"), serde_json::to_vec_pretty(&warm)?)?;
    let started = Instant::now();
    loop {
        let rows = records(&log_path)?;
        let complete = rows
            .iter()
            .filter(|r| {
                r["schema"] == "oreslang-otel.v1"
                    && r["signal"] == "metric"
                    && r["name"] == "http.server.requests"
            })
            .count();
        if complete >= 60 {
            break;
        }
        if started.elapsed() > Duration::from_secs(10) {
            return Err("missing completed userland observations before shutdown".into());
        }
        thread::sleep(Duration::from_millis(25));
    }
    server.stop()?;
    let rows = records(&log_path)?;
    let roots: Vec<_> = rows
        .iter()
        .filter(|r| {
            r["schema"] == "oreslang-otel.v1"
                && r["signal"] == "span"
                && r["name"] == "http.server.request"
        })
        .collect();
    if roots.len() != 60
        || roots
            .iter()
            .map(|r| r["ores_trace_id"].as_str())
            .collect::<BTreeSet<_>>()
            .len()
            != 60
    {
        return Err("expected 60 uniquely correlated completed request spans".into());
    }
    let duration = |row: &Value| -> Result<f64> {
        let ns = row["duration_ns"]
            .as_u64()
            .ok_or("invalid nonnegative duration")?;
        return Ok(ns as f64 / 1_000_000.0);
    };
    let user_warm = roots
        .iter()
        .skip(10)
        .map(|r| duration(r))
        .collect::<Result<Vec<_>>>()?;
    let mut phases = BTreeMap::<String, Vec<f64>>::new();
    for row in rows
        .iter()
        .filter(|r| r["schema"] == "ores-core-perf.v1" && r["kind"] == "phase")
    {
        let text = serde_json::to_string(row)?;
        if ["/foo/bar/baz", "first version", "ores-trace-", "request_id"]
            .iter()
            .any(|s| text.contains(s))
        {
            return Err("guest data leaked into core diagnostics".into());
        }
        phases
            .entry(
                row["phase"]
                    .as_str()
                    .ok_or("missing core phase name")?
                    .to_owned(),
            )
            .or_default()
            .push(duration(row)?);
    }
    if label == "instrumented" {
        for required in [
            "http.listen",
            "http.receive",
            "http.dispatch",
            "stdio.write",
            "http.lifetime",
        ] {
            if !phases.contains_key(required) {
                return Err(format!("missing core phase {required}").into());
            }
        }
        if !rows
            .iter()
            .any(|r| r["schema"] == "ores-core-debug.v1" && r["event"] == "http.listener.ready")
        {
            return Err("missing core debug listener event".into());
        }
    } else if !phases.is_empty() || rows.iter().any(|r| r["schema"] == "ores-core-debug.v1") {
        return Err("core diagnostics emitted despite explicit disabling".into());
    }
    let phase_summary = phases
        .iter()
        .map(|(phase, values)| Ok((phase.clone(), summary(values)?)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    let result = json!({"http_warm_final_attempt": summary(&warm)?, "userland_warm": summary(&user_warm)?,
        "first_userland_ms": duration(roots[0])?, "core_phases": phase_summary});
    fs::write(
        dir.join("summary.json"),
        serde_json::to_vec_pretty(&result)?,
    )?;
    println!("{label}: {}", result["http_warm_final_attempt"]);
    return Ok(result);
}

fn run() -> Result<()> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let report = root.join(format!(
        ".cache/profile-rust-{stamp}-{}",
        std::process::id()
    ));
    fs::create_dir_all(&report)?;
    let baseline = mode(&root, &report, "baseline", port)?;
    let instrumented = mode(&root, &report, "instrumented", port)?;
    let revision = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(&root)
        .output()?;
    if !revision.status.success() {
        return Err("cannot identify demo revision".into());
    }
    let java = std::env::var("ORES_JAVA").unwrap_or_else(|_| {
        std::env::var("JAVA_HOME")
            .map(|p| format!("{p}/bin/java"))
            .unwrap_or("java".into())
    });
    let version = Command::new(java).arg("-version").output()?;
    let receipt = json!({"demo_revision": String::from_utf8(revision.stdout)?.trim(),
        "compiler_revision": fs::read_to_string(root.join("SOURCE_REF"))?.trim(),
        "java_version": String::from_utf8_lossy(&version.stderr),
        "baseline": baseline, "instrumented": instrumented,
        "note": "One warm-up round excluded; final-attempt client times exclude admission retry sleeps; core phases and userland spans overlap and cannot be summed."});
    fs::write(
        report.join("summary.json"),
        serde_json::to_vec_pretty(&receipt)?,
    )?;
    println!("PASS: 120 real HTTP requests, correlated userland spans and core diagnostics. Artifacts: {}", report.display());
    return Ok(());
}
fn main() {
    if let Err(error) = run() {
        eprintln!("FAIL: {error}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    #[test]
    fn percentile_and_invalid_samples() {
        let samples: Vec<_> = (1..=50).map(f64::from).collect();
        let value = super::summary(&samples).unwrap();
        assert_eq!(value["p50_ms"], 25.0);
        assert_eq!(value["p95_ms"], 48.0);
        assert!(super::summary(&[]).is_err());
        assert!(super::summary(&[-1.0]).is_err());
        assert!(super::summary(&[f64::NAN]).is_err());
    }
    #[test]
    fn incomplete_curl_samples_fail_closed() {
        assert!(super::timed_rows("PASS: 10 REST requests").is_err());
        assert!(
            super::timed_rows("01 GET /health -> 200 time=NaNs\nPASS: 10 REST requests").is_err()
        );
    }
}
