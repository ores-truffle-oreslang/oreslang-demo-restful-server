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

fn transmit(port: u16, payload: &[u8]) -> io::Result<(Vec<u8>, bool)> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut socket = TcpStream::connect_timeout(&addr, Duration::from_secs(4))?;
    socket.set_read_timeout(Some(Duration::from_secs(5)))?;
    socket.set_write_timeout(Some(Duration::from_secs(5)))?;
    if let Err(e) = socket.write_all(payload) {
        if matches!(e.kind(), io::ErrorKind::BrokenPipe | io::ErrorKind::ConnectionReset) {
            return Ok((Vec::new(), true));
        }
        return Err(e);
    }
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
                if statuses(&bytes).is_empty() {
                    return Err(io::Error::new(io::ErrorKind::TimedOut, "no HTTP status or EOF"));
                }
                break;
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
    if codes == [503] { return Ok(Classification::RetryCapacity); }
    if codes.is_empty() {
        return if closed { Ok(Classification::Denied) }
        else { Err("neither rejection nor closed transport".into()) };
    }
    if codes.iter().all(|code| DENIALS.contains(code)) {
        Ok(Classification::Denied)
    } else {
        Err(format!("unexpected status sequence {codes:?}"))
    }
}

fn denied(port: u16, name: &str, payload: &[u8]) -> Result<(), String> {
    for _ in 0..5 {
        let (reply, closed) = transmit(port, payload)
            .map_err(|e| format!("{name}: raw transport: {e}"))?;
        match classify(&statuses(&reply), closed)? {
            Classification::Denied => return Ok(()),
            Classification::RetryCapacity => thread::sleep(Duration::from_millis(30)),
        }
    }
    Err(format!("{name}: repeated capacity 503; parser NOT_VERIFIED"))
}

fn cases() -> Vec<(&'static str, Vec<u8>)> {
    let ct = b"Content-Type: text/plain\r\n";
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
    push!("obs-fold header", b"X-Test: a\r\n folded\r\nContent-Length: 3\r\n", b"abc");
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
    let _ = ct; // All bodies are dispatched to fixed test-only routes.
    result
}

fn require_status(port: u16, payload: &[u8], code: u16) -> Result<Vec<u8>, String> {
    for _ in 0..15 {
        let (reply, _) = transmit(port, payload).map_err(|e| e.to_string())?;
        let found = statuses(&reply);
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
}
impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
        let _ = fs::remove_dir_all(&self.dir);
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
    fs::create_dir(&dir).map_err(|e| e.to_string())?;
    let data = dir.join("data");
    fs::create_dir(&data).map_err(|e| e.to_string())?;
    let log = File::create(dir.join("server.log")).map_err(|e| e.to_string())?;
    let entry = env::var_os("REST_SERVER_BINARY")
        .map(PathBuf::from).unwrap_or_else(|| root.join("scripts/run-server.sh"));
    let child = Command::new(entry).current_dir(root).env("PORT", port.to_string())
        .env("DATA_DIR", &data).stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(log)).spawn().map_err(|e| e.to_string())?;
    let mut server = Server { process: child, dir };
    let deadline = Instant::now() + Duration::from_secs(45);
    let health = request("GET", "/health", b"", b"");
    while Instant::now() < deadline {
        if let Ok(Some(exit)) = server.process.try_wait() {
            return Err(format!("guest server exited at startup: {exit}"));
        }
        if let Ok((reply, _)) = transmit(port, &health) {
            if statuses(&reply) == [200] { return Ok((server, port)); }
        }
        thread::sleep(Duration::from_millis(75));
    }
    Err("guest server did not respond to GET /health within 45 seconds".into())
}

fn run() -> Result<(), String> {
    let (server, port) = start_server()?;
    let create = request("POST", RESOURCE,
        b"Content-Type: text/plain\r\nContent-Length: 18\r\n", SENTINEL);
    require_status(port, &create, 201)?;
    let resource = server.dir.join("data/baz.txt");
    let original = fs::read(&resource).map_err(|e| e.to_string())?;
    if original != SENTINEL { return Err("initial persisted sentinel differs".into()); }
    let mut count = 0;
    for (name, payload) in cases() {
        denied(port, name, &payload)?;
        if fs::read(&resource).map_err(|e| e.to_string())? != original {
            return Err(format!("{name}: rejected input mutated persisted bytes"));
        }
        let body = require_status(port, &request("GET", RESOURCE, b"", b""), 200)?;
        if !body.windows(SENTINEL.len()).any(|part| part == SENTINEL) {
            return Err(format!("{name}: successful GET did not return preserved bytes"));
        }
        require_status(port, &request("GET", "/health", b"", b""), 200)?;
        count += 1;
        println!("PASS {name}: rejected, unchanged persisted bytes, healthy follow-up");
    }
    println!("GUEST_HTTP_NEGATIVE_PASS={count} SOURCE_REF=repository-pin");
    Ok(())
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
        for status in DENIALS { assert_eq!(classify(&[*status], false), Ok(Classification::Denied)); }
        for status in [200, 201, 204, 404, 405, 409, 415, 500, 503] {
            if status != 503 { assert!(classify(&[status], true).is_err()); }
        }
    }
}
