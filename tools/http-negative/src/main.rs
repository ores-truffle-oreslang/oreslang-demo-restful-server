//! Strict raw HTTP/1.1 negative tests against an actual disposable AttoLang server.
//! No crates, remote connections, mocks, credentials or guest-parity shortcuts.
#![forbid(unsafe_code)]
use std::collections::HashSet;
use std::env;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const RESOURCE: &str = "/foo/bar/baz";
const SENTINEL: &[u8] = b"untouched sentinel";
const DENIALS: &[u16] = &[400, 408, 413, 414, 422, 431, 505];

fn wire(method: &str, path: &str, version: &str, headers: &[u8], body: &[u8]) -> Vec<u8> {
    let mut out = format!(
        "{method} {path} {version}\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
    ).into_bytes();
    out.extend_from_slice(headers);
    out.extend_from_slice(b"\r\n");
    out.extend_from_slice(body);
    out
}

fn request(method: &str, path: &str, headers: &[u8], body: &[u8]) -> Vec<u8> {
    wire(method, path, "HTTP/1.1", headers, body)
}

fn statuses(response: &[u8]) -> Vec<u16> {
    response.split(|&b| b == b'\n').filter_map(|line| {
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if line.len() < 12 || !line.starts_with(b"HTTP/1.")
            || line[8] != b' ' || !line[9..12].iter().all(u8::is_ascii_digit)
        {
            return None;
        }
        std::str::from_utf8(&line[9..12]).ok()?.parse::<u16>().ok()
    }).collect()
}

// A response is not valid merely because one of its later lines resembles an
// HTTP status line. This prevents proxy garbage / prefix injection from
// becoming false-positive conformance evidence.
fn checked_statuses(raw: &[u8]) -> Result<Vec<u16>, String> {
    if raw.is_empty() { return Ok(Vec::new()); }
    if !(raw.starts_with(b"HTTP/1.0 ") || raw.starts_with(b"HTTP/1.1 ")) {
        return Err("response lacks a leading HTTP/1.x status line".into());
    }
    let first_end = raw.windows(2).position(|w| w == b"\r\n")
        .ok_or("response status line lacks CRLF")?;
    if first_end < 12 || !raw[9..12].iter().all(u8::is_ascii_digit) {
        return Err("response status line has no 3-digit code".into());
    }
    let found = statuses(raw);
    if found.is_empty() { return Err("response has no valid status".into()); }
    Ok(found)
}

fn transmit(port: u16, payload: &[u8]) -> io::Result<(Vec<u8>, bool)> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut socket = TcpStream::connect_timeout(&addr, Duration::from_secs(4))?;
    socket.set_read_timeout(Some(Duration::from_secs(5)))?;
    socket.set_write_timeout(Some(Duration::from_secs(5)))?;
    // A reset during send does not prove the parser received the full malformed
    // frame. Treat it as inconclusive, never as a passing closed connection.
    socket.write_all(payload)?;
    let _ = socket.shutdown(Shutdown::Write);
    let mut bytes = Vec::new();
    let mut closed = false;
    let mut part = [0u8; 4096];
    while bytes.len() < 32768 {
        match socket.read(&mut part) {
            Ok(0) => { closed = true; break; }
            Ok(n) => bytes.extend_from_slice(&part[..n]),
            Err(e) if matches!(e.kind(), io::ErrorKind::ConnectionReset) => {
                closed = true; break;
            }
            Err(e) if matches!(e.kind(), io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock) => {
                // A status line alone is not a complete response. An HTTP/1.1
                // peer asked for Connection: close must close rather than hang.
                return Err(io::Error::new(io::ErrorKind::TimedOut,
                    format!("incomplete response/connection not closed: {}", e)));
            }
            Err(e) => return Err(e),
        }
    }
    if bytes.len() >= 32768 {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "oversized error response"));
    }
    Ok((bytes, closed))
}

#[derive(Debug, PartialEq, Eq)]
enum Classification { Denied, RetryCapacity }

fn classify(codes: &[u16], closed: bool) -> Result<Classification, String> {
    if codes == [503] && closed { return Ok(Classification::RetryCapacity); }
    if codes.is_empty() {
        return if closed { Ok(Classification::Denied) }
        else { Err("neither rejection nor closed transport".into()) };
    }
    // Even multiple 4xx responses may indicate HTTP request desynchronization.
    // Require precisely one denial and that the Connection: close request closed.
    if codes.len() == 1 && DENIALS.contains(&codes[0]) && closed {
        Ok(Classification::Denied)
    } else {
        Err(format!("unexpected status sequence {codes:?}"))
    }
}

// RFC 9112 section 6.1 specifically recommends 501 for a transfer coding
// the server does not understand. A 501 for any other malformed input remains
// a failure; never add it to the general parser-denial status allowlist.
fn classify_case(name: &str, codes: &[u16], closed: bool) -> Result<Classification, String> {
    if closed && codes == [501]
        && matches!(name, "unsupported Transfer-Encoding" | "obs-fold Transfer-Encoding") {
        return Ok(Classification::Denied);
    }
    // JDK HttpServer unfolds obs-fold before userland. RFC 9112 permits
    // unfolding; an invalid folded Content-Type must not be truncated into a
    // valid 'text/plain' value and must not be dispatched as a write.
    if name == "obs-fold Content-Type" && codes == [415] && closed {
        return Ok(Classification::Denied);
    }
    classify(codes, closed)
}

fn denied(port: u16, name: &str, payload: &[u8]) -> Result<(), String> {
    for _ in 0..5 {
        let (reply, closed) = transmit(port, payload)
            .map_err(|e| format!("{name}: raw transport: {e}"))?;
        let codes = checked_statuses(&reply)
            .map_err(|e| format!("{name}: invalid HTTP response: {e}"))?;
        match classify_case(name, &codes, closed)? {
            Classification::Denied => return Ok(()),
            Classification::RetryCapacity => thread::sleep(Duration::from_millis(30)),
        }
    }
    Err(format!("{name}: repeated capacity 503; parser NOT_VERIFIED"))
}

fn cases() -> Vec<(&'static str, Vec<u8>)> {
    let mut result = Vec::new();
    macro_rules! push {
        ($name:expr, $headers:expr, $body:expr) => {
            result.push(($name, request("POST", RESOURCE, $headers, $body)));
        }
    }
    push!("conflicting duplicate Content-Length",
          b"Content-Type: text/plain\r\nContent-Length: 3\r\nContent-Length: 4\r\n", b"abc");
    push!("identical duplicate Content-Length",
          b"Content-Type: text/plain\r\nContent-Length: 3\r\nContent-Length: 3\r\n", b"abc");
    push!("Content-Length plus Transfer-Encoding",
          b"Content-Type: text/plain\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n",
          b"0\r\n\r\nGET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n");
    push!("unsupported Transfer-Encoding", b"Transfer-Encoding: gzip\r\n", b"garbage");
    push!("negative Content-Length", b"Content-Length: -1\r\n", b"xyz");
    push!("invalid chunk size", b"Transfer-Encoding: chunked\r\n", b"Z\r\nabc\r\n0\r\n\r\n");
    push!("header contains NUL", b"X-Test: a\0b\r\nContent-Length: 3\r\n", b"abc");
    push!("obs-fold Content-Type",
          b"Content-Type: text/plain\r\n folded\r\nContent-Length: 3\r\n", b"abc");
    push!("obs-fold Content-Length",
          b"Content-Type: text/plain\r\nContent-Length: 3\r\n 4\r\n", b"abc");
    push!("obs-fold Transfer-Encoding",
          b"Content-Type: text/plain\r\nTransfer-Encoding: chunked\r\n evil\r\n",
          b"0\r\n\r\n");
    result.push(("bare LF framing",
        b"POST /foo/bar/baz HTTP/1.1\nHost: 127.0.0.1\nConnection: close\nContent-Type: text/plain\nContent-Length: 3\n\nabc".to_vec()));
    push!("truncated fixed-length body", b"Content-Length: 12\r\n", b"abc");
    push!("invalid UTF-8 body", b"Content-Type: text/plain\r\nContent-Length: 1\r\n", &[0xff]);
    push!("declared length exceeds 64 KiB", b"Content-Length: 65537\r\n", b"");
    let mut large = b"10000\r\n".to_vec();
    large.extend(vec![b'x'; 65536]);
    large.extend_from_slice(b"\r\n1\r\ny\r\n0\r\n\r\n");
    result.push(("chunked body exceeds 64 KiB", request("POST", RESOURCE,
        b"Content-Type: text/plain\r\nTransfer-Encoding: chunked\r\n", &large)));
    result.push(("unsupported HTTP version",
        wire("GET", "/health", "HTTP/1.2", b"", b"")));
    result
}

// Extract an exact baseline body; never allow a sentinel appearing only in an
// HTTP header (or a truncated/chunked response) to count as valid persistence.
fn response_body(raw: &[u8]) -> Result<Vec<u8>, String> {
    let pos = raw.windows(4).position(|w| w == b"\r\n\r\n")
        .ok_or("missing HTTP header/body separator")?;
    let head = std::str::from_utf8(&raw[..pos])
        .map_err(|_| "invalid HTTP response header UTF-8")?;
    let data = &raw[pos + 4..];
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    for line in head.split("\r\n").skip(1) {
        let Some((key, value)) = line.split_once(':') else {
            return Err("malformed response header".into());
        };
        let value = value.trim();
        if key.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return Err("duplicate response Content-Length".into());
            }
            content_length = Some(value.parse::<usize>()
                .map_err(|_| "invalid response Content-Length")?);
        } else if key.eq_ignore_ascii_case("transfer-encoding") {
            if chunked || !value.eq_ignore_ascii_case("chunked") {
                return Err("unexpected response Transfer-Encoding".into());
            }
            chunked = true;
        }
    }
    if chunked && content_length.is_some() {
        return Err("ambiguous response framing".into());
    }
    if chunked {
        let mut pos = 0;
        let mut result = Vec::new();
        loop {
            let line_end = data[pos..].windows(2).position(|w| w == b"\r\n")
                .ok_or("missing chunk-size terminator")? + pos;
            let size_text = std::str::from_utf8(&data[pos..line_end])
                .map_err(|_| "non-ASCII chunk size")?;
            let size_text = size_text.split(';').next().unwrap_or("");
            let size = usize::from_str_radix(size_text, 16)
                .map_err(|_| "invalid chunk size")?;
            pos = line_end + 2;
            if size == 0 {
                if data.get(pos..) != Some(b"\r\n".as_slice()) {
                    return Err("unexpected chunk trailer or leftover bytes".into());
                }
                return Ok(result);
            }
            if result.len().saturating_add(size) > 32768 {
                return Err("oversized baseline response".into());
            }
            let end = pos.checked_add(size).ok_or("chunk length overflow")?;
            if end + 2 > data.len() || data.get(end..end + 2) != Some(b"\r\n".as_slice()) {
                return Err("truncated chunk".into());
            }
            result.extend_from_slice(&data[pos..end]);
            pos = end + 2;
        }
    }
    if let Some(expected) = content_length {
        if data.len() != expected {
            return Err(format!("incorrect response body length: {} != {}", data.len(), expected));
        }
    }
    Ok(data.to_vec())
}

fn require_status(port: u16, payload: &[u8], code: u16) -> Result<Vec<u8>, String> {
    for _ in 0..15 {
        let (reply, closed) = transmit(port, payload).map_err(|e| e.to_string())?;
        if !closed {
            return Err("baseline response lacked Connection: close".into());
        }
        let found = checked_statuses(&reply)?;
        if found == [503] {
            thread::sleep(Duration::from_millis(40));
            continue;
        }
        if found != [code] {
            return Err(format!("expected {code}; received {found:?}"));
        }
        return Ok(reply);
    }
    Err("repeated capacity 503 on baseline".into())
}

struct Server {
    process: Child,
    dir: PathBuf,
    preserve_log: bool,
}
impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
        if self.preserve_log {
            eprintln!("GUEST_FAIL_LOG_PRESERVED={}", self.dir.join("server.log").display());
        } else {
            let _ = fs::remove_dir_all(&self.dir);
        }
    }
}

fn start_server() -> Result<(Server, u16), String> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
        .canonicalize().map_err(|e| e.to_string())?;
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    drop(listener);
    let nonce = SystemTime::now().duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?.as_nanos();
    let dir = env::temp_dir().join(format!("atto-http-negative-{}-{nonce}", std::process::id()));
    // Failure receipts can include private compiler diagnostics. On Unix,
    // create the directory 0700 atomically rather than relying on umask.
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        let mut builder = fs::DirBuilder::new();
        builder.mode(0o700);
        builder.create(&dir).map_err(|e| e.to_string())?;
    }
    #[cfg(not(unix))]
    fs::create_dir(&dir).map_err(|e| e.to_string())?;
    let data = dir.join("data");
    fs::create_dir(&data).map_err(|e| e.to_string())?;
    let log = File::create(dir.join("server.log")).map_err(|e| e.to_string())?;
    let entry = match env::var_os("REST_SERVER_BINARY") {
        Some(candidate) => {
            let candidate = PathBuf::from(candidate);
            if !candidate.is_absolute() {
                return Err("REST_SERVER_BINARY must be an absolute path".into());
            }
            candidate.canonicalize().map_err(|e| format!("REST_SERVER_BINARY: {e}"))?
        }
        None => root.join("scripts/run-server.sh"),
    };
    let child = Command::new(entry).current_dir(root).env("PORT", port.to_string())
        .env("DATA_DIR", &data).stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(log)).spawn().map_err(|e| e.to_string())?;
    let mut server = Server { process: child, dir, preserve_log: false };
    let deadline = Instant::now() + Duration::from_secs(45);
    let health = request("GET", "/health", b"", b"");
    let ready_marker = format!("Listening on http://127.0.0.1:{port}");
    while Instant::now() < deadline {
        if let Ok(Some(exit)) = server.process.try_wait() {
            server.preserve_log = true;
            return Err(format!("guest server exited at startup: {exit}"));
        }
        // Prevent an unrelated process winning the ephemeral port race from
        // masquerading as the child: the child must announce the selected port.
        let log = fs::read_to_string(server.dir.join("server.log")).unwrap_or_default();
        if !log.contains(&ready_marker) {
            thread::sleep(Duration::from_millis(75));
            continue;
        }
        if let Ok((reply, closed)) = transmit(port, &health) {
            if closed && checked_statuses(&reply).ok().as_deref() == Some(&[200][..])
                && response_body(&reply).ok().as_deref() == Some(b"{\"status\":\"ok\"}") {
                return Ok((server, port));
            }
        }
        thread::sleep(Duration::from_millis(75));
    }
    server.preserve_log = true;
    Err("guest server did not respond to GET /health within 45 seconds".into())
}

fn run() -> Result<(), String> {
    let (mut server, port) = start_server()?;
    let outcome = (|| -> Result<usize, String> {
    let create_headers = format!("Content-Type: text/plain\r\nContent-Length: {}\r\n",
                                SENTINEL.len());
    let create = request("POST", RESOURCE, create_headers.as_bytes(), SENTINEL);
    require_status(port, &create, 201)?;
    let resource = server.dir.join("data/baz.txt");
    let original = fs::read(&resource).map_err(|e| e.to_string())?;
    if original != SENTINEL { return Err("initial persisted sentinel differs".into()); }
    let mut count = 0;
    let mut rejected_contracts = Vec::new();
    for (name, payload) in cases() {
        if let Err(error) = denied(port, name, &payload) {
            eprintln!("NOT_VERIFIED {name}: {error}");
            rejected_contracts.push(format!("{name}: {error}"));
        } else {
            count += 1;
        }
        if fs::read(&resource).map_err(|e| e.to_string())? != original {
            return Err(format!("{name}: rejected input mutated persisted bytes"));
        }
        let body = require_status(port, &request("GET", RESOURCE, b"", b""), 200)?;
        if response_body(&body)? != SENTINEL {
            return Err(format!("{name}: GET representation not byte-identical to sentinel"));
        }
        let health = require_status(port, &request("GET", "/health", b"", b""), 200)?;
        if response_body(&health)? != b"{\"status\":\"ok\"}" {
            return Err(format!("{name}: health response changed after negative request"));
        }
        println!("CHECK {name}: unchanged persisted bytes, healthy follow-up");
    }
    if !rejected_contracts.is_empty() {
        return Err(format!("{} / {} strict cases failed: {}",
            rejected_contracts.len(), cases().len(), rejected_contracts.join("; ")));
    }
    Ok(count)
    })();
    match outcome {
        Ok(count) => {
            println!("GUEST_HTTP_NEGATIVE_PASS={count} PROVENANCE=SEPARATE_RECEIPT_REQUIRED");
            Ok(())
        }
        Err(error) => {
            server.preserve_log = true;
            Err(error)
        }
    }
}

fn main() {
    if env::args().skip(1).collect::<Vec<_>>() != ["--run"] {
        eprintln!("Usage: cargo run --manifest-path tools/http-negative/Cargo.toml -- --run");
        eprintln!("First run scripts/setup.sh with authorized private compiler/dependencies.");
        eprintln!("Cargo unit tests are HOST-ONLY. --run starts a REAL local guest server.");
        std::process::exit(2);
    }
    if let Err(problem) = run() {
        eprintln!("GUEST_HTTP_NEGATIVE_FAIL: {problem}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn payloads_are_unique_bounded_and_loopback_only() {
        let entries = cases();
        let mut names = HashSet::new();
        assert!(entries.len() >= 14);
        for (name, payload) in entries {
            assert!(names.insert(name), "duplicate test name");
            assert!(!payload.is_empty() && payload.len() < 70000);
            assert!(payload.windows(15).any(|s| s == b"Host: 127.0.0.1"));
        }
    }
    #[test]
    fn rejects_smuggled_success_after_denial() {
        assert_eq!(statuses(b"HTTP/1.1 400 Bad\r\nHTTP/1.1 200 OK\r\n"),
                   vec![400, 200]);
        assert!(classify(&[400, 200], true).is_err());
    }
    #[test]
    fn only_unsupported_transfer_coding_may_return_501() {
        assert_eq!(
            classify_case("unsupported Transfer-Encoding", &[501], true),
            Ok(Classification::Denied)
        );
        for name in ["invalid chunk size", "Content-Length plus Transfer-Encoding",
                     "bare LF framing", "unsupported HTTP version"] {
            assert!(classify_case(name, &[501], true).is_err(), "{name}");
        }
        assert!(classify_case("unsupported Transfer-Encoding", &[501], false).is_err());
        assert!(classify_case("unsupported Transfer-Encoding", &[200], true).is_err());
        assert_eq!(classify_case("obs-fold Transfer-Encoding", &[501], true),
                   Ok(Classification::Denied));
        assert_eq!(classify_case("obs-fold Content-Type", &[415], true),
                   Ok(Classification::Denied));
        assert!(classify_case("obs-fold Content-Length", &[415], true).is_err());
        assert!(classify_case("obs-fold Content-Type", &[415], false).is_err());
    }

    #[test]
    fn capacity_is_never_parser_evidence() {
        assert_eq!(classify(&[503], true), Ok(Classification::RetryCapacity));
        assert!(classify(&[200], true).is_err());
        assert!(classify(&[500], true).is_err());
    }
    #[test]
    fn eof_without_success_is_fail_closed() {
        assert_eq!(classify(&[], true), Ok(Classification::Denied));
        assert!(classify(&[], false).is_err());
    }
    #[test]
    fn accepts_only_specific_client_denials() {
        for status in DENIALS {
            assert_eq!(classify(&[*status], true), Ok(Classification::Denied));
            assert!(classify(&[*status], false).is_err());
        }
        for status in [200, 201, 204, 404, 405, 409, 415, 500] {
            assert!(classify(&[status], true).is_err());
        }
        assert!(classify(&[400, 400], true).is_err());
        assert!(classify(&[503, 400], true).is_err());
        assert!(classify(&[503], false).is_err());
    }

    #[test]
    fn only_leading_crlf_status_line_can_count_as_http_evidence() {
        assert!(checked_statuses(b"garbage\r\nHTTP/1.1 400 Bad\r\n\r\n").is_err());
        assert!(checked_statuses(b"HTTP/1.1 400 Bad\n\n").is_err());
        assert!(checked_statuses(b"HTTP/2 400\r\n\r\n").is_err());
        assert_eq!(checked_statuses(b"HTTP/1.1 400 Bad\r\n\r\n").unwrap(), vec![400]);
        assert!(checked_statuses(b"").unwrap().is_empty());
    }

    #[test]
    fn exact_baseline_body_never_uses_header_substring() {
        let raw = b"HTTP/1.1 200 OK\r\nX-Debug: untouched sentinel\r\nContent-Length: 3\r\n\r\nbad";
        assert_eq!(response_body(raw).unwrap(), b"bad");
        let short = b"HTTP/1.1 200 OK\r\nContent-Length: 18\r\n\r\nshort";
        assert!(response_body(short).is_err());
    }

    #[test]
    fn baseline_chunked_body_decodes_exactly() {
        let raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n";
        assert_eq!(response_body(raw).unwrap(), b"abcde");
        let truncated = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nab";
        assert!(response_body(truncated).is_err());
    }
}
