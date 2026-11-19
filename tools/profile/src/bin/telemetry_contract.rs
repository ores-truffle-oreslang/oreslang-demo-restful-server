//! Native REST telemetry structure validation, without synthetic success
//! claims about a guest executable. Used after real HTTP exchanges.
use serde_json::Value;
use std::{collections::HashSet, error::Error, fs, path::Path, thread, time::{Duration, Instant}};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

#[derive(Debug, Clone)]
pub(super) struct Observation {
    pub trace: String,
    pub status: u16,
    pub actor: bool,
}

fn rows(text: &str) -> Result<Vec<Value>> {
    let mut rows = Vec::new();
    for line in text.lines().filter(|l| l.starts_with("{\"schema\":")) {
        rows.push(serde_json::from_str::<Value>(line)?);
    }
    Ok(rows)
}
fn matches_row<'a>(all: &'a [Value], signal: &str, name: &str, trace: &str) -> Vec<&'a Value> {
    all.iter().filter(|r|
        r["signal"].as_str() == Some(signal) &&
        r["name"].as_str() == Some(name) &&
        r["ores_trace_id"].as_str() == Some(trace)
    ).collect()
}
pub(super) fn validate(log: &str, observations: &[Observation]) -> Result<usize> {
    if log.contains("do-not-log") {
        return Err("confidential URL query was written to telemetry log".into());
    }
    if observations.is_empty() { return Err("telemetry observations missing".into()); }
    let parsed = rows(log)?;
    let mut unique = HashSet::new();
    for observation in observations {
        if !unique.insert(&observation.trace) {
            return Err("duplicate observed trace id".into());
        }
        let roots = matches_row(&parsed, "span", "http.server.request", &observation.trace);
        if roots.len() != 1 { return Err("missing/duplicate HTTP request root span".into()); }
        let root = roots[0];
        if root["http_status"].as_u64() != Some(observation.status as u64) ||
            root["outcome"].as_str() != Some("ok") {
            return Err("HTTP root span status or outcome mismatch".into());
        }
        let root_duration = root["duration_ns"].as_u64()
            .ok_or("root span duration must be nonnegative integer")?;
        let span_id = root["span_id"].as_str().ok_or("root span id missing")?;
        let children: Vec<&Value> = parsed.iter().filter(|row|
            row["signal"].as_str() == Some("span") &&
            row["ores_trace_id"].as_str() == Some(observation.trace.as_str()) &&
            row["parent_span_id"].as_str() == Some(span_id)
        ).collect();
        let required: HashSet<&str> =
            if observation.actor { ["routing.dispatch", "actor.handle"].into_iter().collect() }
            else { ["routing.dispatch"].into_iter().collect() };
        let actual: HashSet<&str> = children.iter()
            .filter_map(|row| row["name"].as_str()).collect();
        if actual != required || actual.len() != children.len() {
            return Err("missing, duplicate or unexpected child spans".into());
        }
        for child in &children {
            if child["duration_ns"].as_u64().map(|n| n > root_duration).unwrap_or(true) {
                return Err("invalid child span duration".into());
            }
        }
        let request_metrics = matches_row(&parsed, "metric", "http.server.requests", &observation.trace);
        if request_metrics.len() != 1 {
            return Err("missing/duplicate HTTP request metric".into());
        }
        let duration = matches_row(&parsed, "metric", "http.server.request.duration", &observation.trace);
        if duration.len() != 1 || duration[0]["value"].as_u64() != Some(root_duration) {
            return Err("request duration metric and root span diverged".into());
        }
    }
    Ok(observations.len())
}
pub(super) fn validate_handler_error(log: &str, trace: &str) -> Result<()> {
    if trace.is_empty() { return Err("error trace ID missing".into()); }
    if log.contains("do-not-log") { return Err("confidential query leaked into telemetry".into()); }
    let parsed=rows(log)?;
    let errors=matches_row(&parsed,"metric","http.server.errors",trace);
    if errors.len()!=1 || errors[0]["value"].as_u64()!=Some(1) {
        return Err("missing, duplicated or incorrect HTTP server error metric".into());
    }
    let spans=matches_row(&parsed,"span","actor.handle",trace);
    if spans.len()!=1 || spans[0]["outcome"].as_str()!=Some("error") {
        return Err("missing or malformed actor handler error span".into());
    }
    Ok(())
}
pub(super) fn wait_for_handler_error(log_path: &Path, trace: &str) -> Result<()> {
    let deadline=Instant::now()+Duration::from_secs(4);
    loop {
        let log=fs::read_to_string(log_path)?;
        match validate_handler_error(&log,trace) {
            Ok(()) => return Ok(()),
            Err(_) if Instant::now()<deadline => thread::sleep(Duration::from_millis(20)),
            Err(error) => return Err(error),
        }
    }
}

pub(super) fn wait_for_complete(log_path: &Path, observations: &[Observation]) -> Result<usize> {
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        let log = fs::read_to_string(log_path)?;
        match validate(&log, observations) {
            Ok(count) => return Ok(count),
            Err(error) if Instant::now() < deadline => {
                // Each try reparses the emitted telemetry rather than assuming
                // a fixed ordering between HTTP completion and log flush.
                let _ = error;
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(error),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (String, Vec<Observation>) {
        let obs = vec![Observation {
            trace: "ores-trace-abc".into(), status: 200, actor: true,
        }];
        let root = serde_json::json!({"schema":"v1","signal":"span","name":"http.server.request",
            "ores_trace_id":"ores-trace-abc","span_id":"root","parent_span_id":null,
            "http_status":200,"duration_ns":17,"outcome":"ok"});
        let route = serde_json::json!({"schema":"v1","signal":"span","name":"routing.dispatch",
            "ores_trace_id":"ores-trace-abc","span_id":"route","parent_span_id":"root",
            "duration_ns":5});
        let actor = serde_json::json!({"schema":"v1","signal":"span","name":"actor.handle",
            "ores_trace_id":"ores-trace-abc","span_id":"actor","parent_span_id":"root",
            "duration_ns":4});
        let requests = serde_json::json!({"schema":"v1","signal":"metric","name":"http.server.requests",
            "ores_trace_id":"ores-trace-abc","value":1});
        let duration = serde_json::json!({"schema":"v1","signal":"metric","name":"http.server.request.duration",
            "ores_trace_id":"ores-trace-abc","value":17});
        // Ensure schema is the first key just like the production emitter.
        let log = [root,route,actor,requests,duration].into_iter().map(|mut row| {
            row.as_object_mut().unwrap().remove("schema");
            let data = serde_json::to_string(&row).unwrap();
            format!("{{\"schema\":\"v1\",{}}}", &data[1..data.len()-1])
        }).collect::<Vec<_>>().join("\n");
        (log, obs)
    }
    #[test]
    fn valid_real_shape_requires_root_child_spans_and_metrics() {
        let (log, observations)=fixture();
        assert_eq!(validate(&log,&observations).unwrap(),1);
    }
    #[test]
    fn missing_actor_child_is_detected() {
        let (log, observations)=fixture();
        let updated=log.lines().filter(|l| !l.contains("\"name\":\"actor.handle\""))
            .collect::<Vec<_>>().join("\n");
        assert!(validate(&updated,&observations).is_err());
    }
    #[test]
    fn mismatched_status_and_root_metric_are_detected() {
        let (log, mut observations)=fixture();
        observations[0].status=201;
        assert!(validate(&log,&observations).is_err());
        observations[0].status=200;
        let corrupted=log.replace("\"value\":17","\"value\":21");
        assert!(validate(&corrupted,&observations).is_err());
    }
    #[test]
    fn secret_leakage_and_duplicate_trace_are_rejected() {
        let (log, mut observations)=fixture();
        assert!(validate(&(log.clone()+"\ndo-not-log"),&observations).is_err());
        observations.push(observations[0].clone());
        assert!(validate(&log,&observations).is_err());
    }
    #[test]
    fn handler_error_requires_both_single_metric_and_error_span() {
        let span=serde_json::json!({"schema":"v1","signal":"span","name":"actor.handle",
            "ores_trace_id":"ores-trace-failed","outcome":"error"});
        let metric=serde_json::json!({"schema":"v1","signal":"metric","name":"http.server.errors",
            "ores_trace_id":"ores-trace-failed","value":1});
        let encode=|mut row:Value| {
            row.as_object_mut().unwrap().remove("schema");
            let body=serde_json::to_string(&row).unwrap();
            format!("{{\"schema\":\"v1\",{}}}", &body[1..body.len()-1])
        };
        let raw_span=encode(span);
        let raw_metric=encode(metric);
        let log=format!("{raw_span}\n{raw_metric}");
        assert!(validate_handler_error(&log,"ores-trace-failed").is_ok());
        assert!(validate_handler_error(&raw_span,"ores-trace-failed").is_err());
        assert!(validate_handler_error(&raw_metric,"ores-trace-failed").is_err());
        assert!(validate_handler_error(&log,"wrong-trace").is_err());
        assert!(validate_handler_error(&(log.clone()+"\n"+&raw_metric),"ores-trace-failed").is_err());
        assert!(validate_handler_error(&(log+"\ndo-not-log"),"ores-trace-failed").is_err());
    }
    #[test]
    fn malformed_emitted_json_is_rejected() {
        let (log,observations)=fixture();
        assert!(validate(&(log+"\n{\"schema\":oops"),&observations).is_err());
    }
}
