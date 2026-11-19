//! Rust replacement for the destructive legacy Python REST benchmark.
//! This is host timing evidence, not full AOT/HTTP guest parity.
use serde_json::json;
use std::{collections::HashSet, error::Error, path::Path, process::Command, time::Instant};
type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn base_url(value: &str) -> Result<String> {
    let base = value.trim_end_matches('/');
    let rest = base.strip_prefix("http://").ok_or("HTTP loopback only")?;
    let port = ["127.0.0.1:", "localhost:", "[::1]:"]
        .iter().find_map(|prefix| rest.strip_prefix(prefix))
        .ok_or("refusing non-loopback or path-bearing BASE_URL")?;
    if port.is_empty() || !port.bytes().all(|b| b.is_ascii_digit()) ||
        port.parse::<u16>()? == 0 {
        return Err("BASE_URL must use a valid decimal port".into());
    }
    Ok(base.to_owned())
}

fn field<'a>(line: &'a str, prefix: &str) -> Result<&'a str> {
    line.split_whitespace().find_map(|token| token.strip_prefix(prefix))
        .ok_or_else(|| format!("missing {prefix}").into())
}

fn uuid(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 36 && b.iter().enumerate().all(|(i, c)| {
        if [8, 13, 18, 23].contains(&i) { *c == b'-' } else { c.is_ascii_hexdigit() }
    })
}

struct Round { shared: Vec<f64>, isolated: Vec<f64>, retries: u64 }
fn parse_round(output: &str) -> Result<Round> {
    if output.lines().filter(|line| *line == "PASS: 10 REST requests").count() != 1 {
        return Err("missing or duplicate REST success receipt".into());
    }
    let expected = ["shared", "isolated", "shared", "isolated", "isolated",
                    "shared", "isolated", "shared", "isolated", "isolated"];
    let mut data = Round { shared: vec![], isolated: vec![], retries: 0 };
    let mut ids = HashSet::new();
    let mut seen = 0;
    for line in output.lines().filter(|line| line.contains(" -> ")) {
        if seen >= expected.len() || field(line, "actor=")? != expected[seen] {
            return Err("wrong response count or actor order".into());
        }
        let id = field(line, "request_id=")?;
        if !uuid(id) || !ids.insert(id) {
            return Err("duplicate or invalid request ID".into());
        }
        let seconds: f64 = field(line, "time=")?
            .strip_suffix('s').ok_or("invalid curl seconds suffix")?.parse()?;
        if !seconds.is_finite() || seconds < 0.0 { return Err("invalid timing".into()); }
        data.retries = data.retries.checked_add(field(line, "retries=")?.parse::<u64>()?)
            .ok_or("retry counter overflow")?;
        if expected[seen] == "shared" { data.shared.push(seconds * 1000.0); }
        else { data.isolated.push(seconds * 1000.0); }
        seen += 1;
    }
    if seen != 10 || ids.len() != 10 { return Err("incomplete timing output".into()); }
    Ok(data)
}

fn median(mut v: Vec<f64>) -> Result<f64> {
    if v.is_empty() || v.iter().any(|n| !n.is_finite() || *n < 0.0) {
        return Err("empty or invalid timing sample".into());
    }
    v.sort_by(f64::total_cmp);
    let mid = v.len()/2;
    Ok(if v.len()%2 == 1 { v[mid] } else { v[mid-1]/2.0 + v[mid]/2.0 })
}

fn run() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let mut runs = 6usize;
    while let Some(arg) = args.next() {
        if arg == "--help" {
            println!("Usage: scripts/benchmark.sh [--runs N] (mutates local demo data)");
            return Ok(());
        }
        let n = if arg == "--runs" { args.next().ok_or("--runs requires a number")? }
        else if let Some(n) = arg.strip_prefix("--runs=") { n.to_owned() }
        else { return Err(format!("unsupported option: {arg}").into()); };
        runs = n.parse()?;
    }
    if !(2..=100).contains(&runs) { return Err("--runs must be in 2..=100".into()); }
    let base = base_url(&std::env::var("BASE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:3000".into()))?;
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().ok_or("missing tools")?
        .parent().ok_or("missing repo root")?;
    let (mut shared, mut isolated, mut rounds, mut retries) =
        (vec![], vec![], vec![], 0u64);
    for i in 0..runs {
        let start = Instant::now();
        let output = Command::new("bash").arg(root.join("scripts/curl-10.sh"))
            .current_dir(root).env("BASE_URL", &base).output()?;
        let elapsed = start.elapsed().as_secs_f64()*1000.0;
        print!("{}", String::from_utf8_lossy(&output.stdout));
        if !output.status.success() {
            return Err(format!("REST requests failed: {}", String::from_utf8_lossy(&output.stderr)).into());
        }
        let data = parse_round(&String::from_utf8(output.stdout)?)?;
        retries = retries.checked_add(data.retries).ok_or("retry overflow")?;
        println!("Round {}: {:.1} ms{}", i+1, elapsed, if i==0 {" (warm-up)"} else {""});
        if i > 0 {
            shared.extend(data.shared);
            isolated.extend(data.isolated);
            rounds.push(elapsed);
        }
    }
    let mut phases = [Vec::new(), Vec::new(), Vec::new()];
    for _ in 0..20 {
        let output = Command::new("curl").args(["--max-time", "10", "-sS", "-o"])
            .arg(if cfg!(windows) { "NUL" } else { "/dev/null" })
            .args(["-w", "%{http_code} %{time_connect} %{time_starttransfer} %{time_total}"])
            .arg(format!("{base}/health")).output()?;
        if !output.status.success() { return Err("health curl failed".into()); }
        let line = String::from_utf8(output.stdout)?;
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.len()!=4 || fields[0]!="200" { return Err("health must return HTTP 200".into()); }
        for i in 0..3 {
            let seconds: f64 = fields[i+1].parse()?;
            if !seconds.is_finite() || seconds < 0.0 { return Err("bad health timing".into()); }
            phases[i].push(seconds*1000.0);
        }
    }
    let mut all = shared.clone();
    all.extend_from_slice(&isolated);
    println!("{}", serde_json::to_string_pretty(&json!({
        "warm_median_request_ms": median(all)?,
        "warm_median_by_actor_ms": {"shared": median(shared)?, "isolated": median(isolated)?},
        "warm_median_script_ms": median(rounds)?,
        "admission_retries": retries,
        "health_20_request_median_ms": {
            "connect": median(phases[0].clone())?,
            "first_byte": median(phases[1].clone())?,
            "total": median(phases[2].clone())?
        },
        "scope": "instrumented sequential demo; first REST round excluded; curl final-attempt timings"
    }))?);
    Ok(())
}
fn main() {
    if let Err(e) = run() { eprintln!("FAIL: {e}"); std::process::exit(1); }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> String {
        let modes = ["shared", "isolated", "shared", "isolated", "isolated",
                     "shared", "isolated", "shared", "isolated", "isolated"];
        let mut s = String::new();
        for (i, mode) in modes.iter().enumerate() {
            s.push_str(&format!(
                "{:02} GET /health -> 200 actor={mode} request_id=00000000-0000-0000-0000-{:012x} time=0.01s retries=0\n",
                i+1, i+1
            ));
        }
        s.push_str("PASS: 10 REST requests\n");
        s
    }
    #[test]
    fn strict_receipts() {
        let good = fixture();
        assert_eq!(parse_round(&good).unwrap().shared.len(), 4);
        assert!(parse_round(&good.replace("PASS: 10 REST requests", "SKIPPED")).is_err());
        assert!(parse_round(&good.replace("time=0.01s", "time=NaNs")).is_err());
        assert!(parse_round(&good.replace("actor=shared", "actor=unknown")).is_err());
        assert!(parse_round(&(good.clone()+&good)).is_err());
        assert!(parse_round(&good.replace("retries=0", "retries=-1")).is_err());
        assert!(parse_round(&good.replace("0000-0000-0000-0000-000000000001", "0000-0000-0000-0000-000000000002")).is_err());
    }
    #[test]
    fn bounded_loopback_only() {
        for s in ["https://127.0.0.1:3000", "http://evil.test:3000",
                  "http://127.0.0.1:3000/path", "http://localhost:0", "http://localhost:65536",
                  "http://localhost:3000?x=1"] { assert!(base_url(s).is_err()); }
        assert!(base_url("http://127.0.0.1:3000/").is_ok());
        assert!(base_url("http://[::1]:3000").is_ok());
    }
    #[test]
    fn medians() {
        assert_eq!(median(vec![1.0,2.0,3.0,4.0]).unwrap(), 2.5);
        assert_eq!(median(vec![1.0,3.0,2.0]).unwrap(), 2.0);
        assert!(median(vec![f64::INFINITY]).is_err());
        assert!(median(vec![]).is_err());
    }
}
