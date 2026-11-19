//! Native-deployment HTTP integration driver, replacing the Python request driver in stages.
//! Unit tests use a real loopback TCP server; production smoke requires an explicit server binary.
mod telemetry_contract;
use telemetry_contract::Observation;
use std::{
    collections::{HashMap, HashSet},
    env, error::Error, fs::{self, OpenOptions},
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    thread, time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
type Result<T> = std::result::Result<T, Box<dyn Error>>;
const LIMIT: usize = 1024 * 1024;
#[derive(Debug)]
struct Response { status: u16, body: Vec<u8>, headers: HashMap<String, String> }
impl Response {
    fn header(&self, name: &str) -> Result<&str> {
        self.headers.get(&name.to_ascii_lowercase()).map(String::as_str)
            .ok_or_else(|| format!("missing response header {name}").into())
    }
}
fn parse_http(bytes: &[u8], head: bool) -> Result<Response> {
    if bytes.len() > LIMIT { return Err("response exceeds size limit".into()); }
    let offset = bytes.windows(4).position(|w| w==b"\r\n\r\n")
        .ok_or("response missing header terminator")?;
    let raw = std::str::from_utf8(&bytes[..offset])?;
    let mut lines = raw.split("\r\n");
    let status = lines.next().ok_or("HTTP status line missing")?;
    let mut parts = status.splitn(3, ' ');
    let proto = parts.next().ok_or("HTTP version missing")?;
    let code = parts.next().ok_or("HTTP status missing")?;
    let reason = parts.next().ok_or("HTTP reason missing")?;
    if !["HTTP/1.0", "HTTP/1.1"].contains(&proto) || reason.is_empty() ||
        code.len()!=3 || !code.bytes().all(|b| b.is_ascii_digit()) {
        return Err("invalid HTTP/1.x status line".into());
    }
    let status: u16 = code.parse()?;
    // This driver expects a final response, not a 1xx interim message or a
    // syntactically valid but undefined 6xx/9xx status.
    if !(200..=599).contains(&status) { return Err("invalid final HTTP status".into()); }
    let mut headers = HashMap::new();
    for line in lines {
        let (key, value) = line.split_once(':').ok_or("malformed HTTP response header")?;
        if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b==b'-') {
            return Err("invalid HTTP header name".into());
        }
        if value.bytes().any(|b| b.is_ascii_control() && b != b'\t') {
            return Err("HTTP header contains forbidden control byte".into());
        }
        if headers.insert(key.to_ascii_lowercase(), value.trim().to_owned()).is_some() {
            return Err("duplicate HTTP header".into());
        }
    }
    let mut body = bytes[offset+4..].to_vec();
    if status==204 && (headers.contains_key("content-length") ||
                       headers.contains_key("transfer-encoding")) {
        return Err("204 must not carry body framing".into());
    }
    if head || status==204 || status==304 {
        if !body.is_empty() { return Err("unexpected body on bodyless response".into()); }
        if headers.contains_key("transfer-encoding") {
            return Err("unsupported Transfer-Encoding on bodyless response".into());
        }
    } else if let Some(value) = headers.get("transfer-encoding") {
        if value!="chunked" { return Err("unsupported transfer-encoding".into()); }
        if headers.contains_key("content-length") { return Err("ambiguous framing: chunked and Content-Length".into()); }
        body = decode_chunks(&body)?;
    } else if let Some(value) = headers.get("content-length") {
        if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
            return Err("invalid Content-Length syntax".into());
        }
        let length = value.parse::<usize>()?;
        if length != body.len() { return Err("HTTP Content-Length mismatch".into()); }
    }
    Ok(Response { status, body, headers })
}
fn decode_chunks(input: &[u8]) -> Result<Vec<u8>> {
    let mut cursor = input;
    let mut out = Vec::new();
    loop {
        let end = cursor.windows(2).position(|w| w==b"\r\n").ok_or("missing chunk header CRLF")?;
        let line = std::str::from_utf8(&cursor[..end])?;
        if line.is_empty() || !line.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("unsupported chunk extensions or bad chunk size".into());
        }
        let size = usize::from_str_radix(line,16)?;
        cursor = &cursor[end+2..];
        if size==0 {
            if cursor!=b"\r\n" { return Err("unsupported nonempty trailers or trailing bytes".into()); }
            return Ok(out);
        }
        if size > LIMIT-out.len() || cursor.len() < size+2 || &cursor[size..size+2]!=b"\r\n" {
            return Err("invalid or oversized chunk".into());
        }
        out.extend_from_slice(&cursor[..size]);
        cursor=&cursor[size+2..];
    }
}
fn request(port:u16, method:&str, path:&str, body:&[u8], media_type:Option<&str>) -> Result<Response> {
    if port==0 || method.is_empty() || !method.bytes().all(|b| b.is_ascii_uppercase())
        || !path.starts_with('/') || !path.bytes().all(|b| b.is_ascii_graphic()) {
        return Err("unsafe HTTP request inputs".into());
    }
    if let Some(kind) = media_type { if kind.contains('\r') || kind.contains('\n') { return Err("invalid Content-Type".into()); } }
    if body.len()>LIMIT { return Err("request exceeds 1MiB limit".into()); }
    let mut stream = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse()?, Duration::from_secs(3))?;
    stream.set_read_timeout(Some(Duration::from_secs(10)))?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;
    let mut header=format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nContent-Length: {}\r\n",
        body.len()
    );
    if let Some(kind)=media_type { header.push_str(&format!("Content-Type: {kind}\r\n")); }
    header.push_str("\r\n");
    stream.write_all(header.as_bytes())?;
    stream.write_all(body)?;
    let mut buf=Vec::new();
    stream.take(LIMIT as u64+1).read_to_end(&mut buf)?;
    if buf.len()>LIMIT { return Err("response exceeded 1MiB limit".into()); }
    parse_http(&buf, method=="HEAD")
}
fn retry(port:u16, method:&str, path:&str, body:&[u8], ct:Option<&str>) -> Result<Response> {
    for _ in 0..50 {
        let answer=request(port,method,path,body,ct)?;
        if answer.status!=503 { return Ok(answer); }
        thread::sleep(Duration::from_millis(20));
    }
    Err("server admission did not recover after 50 attempts".into())
}
struct Running { process: Child, log: PathBuf }
impl Drop for Running {
    fn drop(&mut self) {
        let _=self.process.kill();
        let _=self.process.wait();
    }
}
#[cfg(unix)]
unsafe extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
}
#[cfg(unix)]
fn expected_termination(status: ExitStatus) -> bool {
    use std::os::unix::process::ExitStatusExt;
    // Native-image may return 143 after running a shutdown hook. A process
    // killed by SIGSEGV/SIGKILL or returning an arbitrary error is not a
    // graceful shutdown, even if it exits promptly.
    status.success() || status.signal() == Some(15) || status.code() == Some(143)
}
impl Running {
    #[cfg(unix)]
    fn stop_with_term(&mut self) -> Result<()> {
        if self.process.try_wait()?.is_some() {
            return Err("native server exited before graceful shutdown request".into());
        }
        let pid = i32::try_from(self.process.id())?;
        // SAFETY: positive child pid obtained from this owned Child; POSIX
        // signal 15 is SIGTERM. No shell, PATH or external process is used.
        if unsafe { kill(pid, 15) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        let started = Instant::now();
        while started.elapsed() < Duration::from_secs(8) {
            if let Some(status) = self.process.try_wait()? {
                if expected_termination(status) { return Ok(()); }
                return Err(format!("native server exited abnormally during SIGTERM shutdown: {status}").into());
            }
            thread::sleep(Duration::from_millis(25));
        }
        Err("native server failed to terminate after SIGTERM (SIGKILL fallback is a test failure)".into())
    }
    #[cfg(not(unix))]
    fn stop_with_term(&mut self) -> Result<()> {
        Err("native graceful termination contract requires Unix SIGTERM".into())
    }
}
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) { let _=fs::remove_dir_all(&self.0); }
}
fn start(binary:&Path, root:&Path) -> Result<(Running,u16)> {
    let file=fs::canonicalize(binary)?;
    if !file.is_file() { return Err("native server is not a regular file".into()); }
    let port=TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
    let stamp=SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let log=root.join(format!("native-rest-{stamp}.log"));
    let output=OpenOptions::new().append(true).create_new(true).open(&log)?;
    let stderr=output.try_clone()?;
    let mut process=Command::new(file).env("PORT",port.to_string())
        .env("DATA_DIR",root.join("data with \"quotes\""))
        .env("PATH","/nonexistent")
        .env_remove("JAVA_HOME").env_remove("ORES_JAVA")
        .env_remove("ORESLANG_SOURCE_DIR").env_remove("CLASSPATH")
        .stdout(Stdio::from(output)).stderr(Stdio::from(stderr)).spawn()?;
    let ready=Instant::now();
    while ready.elapsed()<Duration::from_secs(20) {
        if let Some(status)=process.try_wait()? {
            return Err(format!("native server exited during startup: {status}").into());
        }
        if let Ok(ok)=request(port,"GET","/health",b"",None) {
            if ok.status==200 { return Ok((Running { process,log },port)); }
        }
        thread::sleep(Duration::from_millis(50));
    }
    let _=process.kill();
    let _=process.wait();
    Err("native server startup timed out".into())
}
fn verify(port:u16)->Result<(usize,Vec<Observation>)> {
    let mut count=0;
    let cases: [(&str,&str,&[u8],Option<&str>,u16);8]=[
        ("GET","/not-found",b"",None,404),
        ("POST","/health",b"",None,405),
        ("OPTIONS","/foo/bar/baz",b"",None,204),
        ("POST","/foo/bar/baz",b"no media type",None,415),
        ("POST","/foo/bar/baz",b"",Some("text/plain"),400),
        ("PATCH","/foo/bar/baz",b"append",Some("text/plain"),404),
        ("PUT","/foo/bar/baz",&[b'x';65537],Some("text/plain"),413),
        ("GET","/%2e%2e/secret",b"",None,404),
    ];
    for (method,path,body,content_type,expected) in cases {
        let response=retry(port,method,path,body,content_type)?;
        if response.status!=expected {
            return Err(format!("{method} {path}: expected {expected}, got {}",response.status).into());
        }
        if method=="OPTIONS" {
            let methods=response.header("allow")?;
            for allowed in ["GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS"] {
                if !methods.split(',').any(|m| m.trim()==allowed) {
                    return Err(format!("OPTIONS missing Allow {allowed}").into());
                }
            }
        }
        count+=1;
    }
    let created=retry(port,"POST","/foo/bar/baz",b"survives restart",Some("text/plain"))?;
    if created.status!=201 || created.body!=b"survives restart" ||
        created.header("x-actor-mode")?!="isolated" ||
        created.header("location")?!="/foo/bar/baz" {
        return Err("POST persistence headers/body mismatch".into());
    }
    count+=1;
    let head=retry(port,"HEAD","/foo/bar/baz",b"",None)?;
    if head.status!=200 || !head.body.is_empty() || head.header("x-actor-mode")?!="shared" {
        return Err("HEAD semantics mismatch".into());
    }
    count+=1;
    let mut ids=HashSet::new();
    let mut observations=Vec::new();
    for (method,path,expected,actor) in [
        ("GET","/health",200,"shared"),("HEAD","/health",200,"shared"),
        ("GET","/missing?secret=do-not-log",404,"supervisor"),
        ("POST","/health",405,"supervisor"),("OPTIONS","/health",204,"supervisor")
    ] {
        let res=retry(port,method,path,b"",None)?;
        if res.status!=expected || res.header("x-actor-mode")?!=actor ||
            res.header("x-ores-trace-id")?!=format!("ores-trace-{}",res.header("x-request-id")?) ||
            !ids.insert(res.header("x-request-id")?.to_owned()) {
            return Err(format!("trace or actor headers mismatch for {method} {path}").into());
        }
        observations.push(Observation {
            trace: res.header("x-ores-trace-id")?.to_owned(),
            status: expected,
            actor: actor=="shared",
        });
        count+=1;
    }
    Ok((count,observations))
}
fn run()->Result<()> {
    let args:Vec<_>=env::args().skip(1).collect();
    if args.len()!=2 || args[0]!="--server" {
        return Err("usage: integration --server /absolute/path/to/native/rest-server (requires real native executable)".into());
    }
    let server=PathBuf::from(&args[1]);
    let stamp=SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let scratch=Scratch(env::temp_dir().join(format!("atto-rust-integration-{}-{stamp}",std::process::id())));
    // create_dir is exclusive; create_dir_all could reuse a pre-planted symlink.
    fs::create_dir(&scratch.0)?;
    let (mut one,port)=start(&server,&scratch.0)?;
    let (checks,traces)=verify(port)?;
    let verified=telemetry_contract::wait_for_complete(&one.log,&traces)?;
    // Reproduce the Python E2E storage-failure fixture without a test-only
    // route: a directory at the data-file leaf forces a real handler failure.
    let storage=scratch.0.join("data with \"quotes\"/baz.txt");
    let original=fs::read(&storage)?;
    fs::remove_file(&storage)?;
    fs::create_dir(&storage)?;
    let failed=retry(port,"GET","/foo/bar/baz",b"",None)?;
    if failed.status!=500 { return Err("expected HTTP 500 after storage leaf disruption".into()); }
    let error_trace=failed.header("x-ores-trace-id")?.to_owned();
    telemetry_contract::wait_for_handler_error(&one.log,&error_trace)?;
    fs::remove_dir(&storage)?;
    fs::write(&storage,original)?;
    one.stop_with_term()?;
    drop(one);
    let (mut two,again)=start(&server,&scratch.0)?;
    let response=retry(again,"GET","/foo/bar/baz",b"",None)?;
    if response.status!=200 || response.body!=b"survives restart" {
        return Err("restart persistence mismatch".into());
    }
    two.stop_with_term()?;
    drop(two);
    let saved=fs::read(scratch.0.join("data with \"quotes\"/baz.txt"))?;
    if saved!=b"survives restart" { return Err("persisted data mismatch".into()); }
    println!("RUST_NATIVE_INTEGRATION_PASS: {} HTTP/actor-header checks, {} complete telemetry traces, 1 HTTP 500 actor error metric/span, two bounded SIGTERM shutdowns, restart persistence", checks+3,verified);
    Ok(())
}
fn main() {
    if let Err(error)=run() { eprintln!("FAIL: {error}"); std::process::exit(1); }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn strict_http_parsing_and_chunked_response() {
        let response=parse_http(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-Actor-Mode: shared\r\n\r\nOK",false).unwrap();
        assert_eq!(response.status,200);
        assert_eq!(response.body,b"OK");
        assert_eq!(response.header("x-actor-mode").unwrap(),"shared");
        assert!(parse_http(b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\nOK",false).is_err());
        assert!(parse_http(b"HTTP/1.1 200 OK\r\nX-A: a\r\nX-A: b\r\n\r\n",false).is_err());
        assert!(parse_http(b"HTTP/1.1 200 OK\r\n\r\n",false).is_ok());
        assert_eq!(parse_http(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nOK\r\n0\r\n\r\n",false).unwrap().body,b"OK");
        assert!(parse_http(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n7\r\nOK\r\n0\r\n\r\n",false).is_err());
    }
    #[test]
    fn malformed_request_inputs_fail_closed() {
        assert!(request(0,"GET","/health",b"",None).is_err());
        assert!(request(3000,"Get","/health",b"",None).is_err());
        assert!(request(3000,"GET","http://evil.test/",b"",None).is_err());
        assert!(request(3000,"GET","/\r\nx: y",b"",None).is_err());
        assert!(request(3000,"GET","/health",b"",Some("text/plain\r\nX-Evil: yes")).is_err());
    }
    #[test]
    fn loopback_http_uses_real_socket_and_rejects_wrong_status() {
        let listener=TcpListener::bind("127.0.0.1:0").unwrap();
        let port=listener.local_addr().unwrap().port();
        let server=thread::spawn(move|| {
            let (mut socket,_)=listener.accept().unwrap();
            socket.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
            let mut request=[0u8;1024];
            let read=socket.read(&mut request).unwrap();
            assert!(std::str::from_utf8(&request[..read]).unwrap().starts_with("GET /health HTTP/1.1\r\n"));
            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK").unwrap();
        });
        let response=request(port,"GET","/health",b"",None).unwrap();
        assert_eq!(response.status,200);
        assert_eq!(response.body,b"OK");
        server.join().unwrap();
    }
    #[test]
    fn reject_ambiguous_framing_control_bytes_and_invalid_final_status() {
        for packet in [
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\nOK".as_slice(),
            b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n".as_slice(),
            b"HTTP/1.1 204 No Content\r\nTransfer-Encoding: chunked\r\n\r\n".as_slice(),
            b"HTTP/1.1 200 OK\r\nContent-Length: +2\r\n\r\nOK".as_slice(),
            b"HTTP/1.1 200 OK\r\nContent-Length: 2, 2\r\n\r\nOK".as_slice(),
            b"HTTP/1.1 200 OK\r\nX-Value: abc\x00def\r\n\r\n".as_slice(),
            b"HTTP/1.1 200 OK\r\nX-Value: abc\x7fdef\r\n\r\n".as_slice(),
            b"HTTP/1.1 100 Continue\r\n\r\n".as_slice(),
            b"HTTP/1.1 999 Unknown\r\n\r\n".as_slice(),
        ] {
            assert!(parse_http(packet,false).is_err(), "accepted invalid HTTP framing: {packet:?}");
        }
        // HEAD can advertise representation length without returning a body.
        let head = parse_http(b"HTTP/1.1 200 OK\r\nContent-Length: 42\r\n\r\n",true).unwrap();
        assert_eq!(head.status,200);
        assert!(head.body.is_empty());
        assert!(parse_http(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",true).is_err());
    }

    #[test]
    fn malformed_chunk_boundaries_and_trailers_are_rejected() {
        for payload in [
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2;ext=yes\r\nOK\r\n0\r\n\r\n".as_slice(),
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nOK\r\n0\r\nX: y\r\n\r\n".as_slice(),
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nOK\r\n0\r\n\r\ntrailing".as_slice(),
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nfffffffffffffffffffffffff\r\nOK\r\n".as_slice(),
        ] {
            assert!(parse_http(payload,false).is_err(), "accepted malformed chunked stream: {payload:?}");
        }
    }

    #[cfg(unix)]
    #[test]
    fn unexpected_native_crashes_are_not_graceful_shutdown() {
        use std::os::unix::process::ExitStatusExt;
        for accepted in [0, 15, 143<<8] {
            assert!(expected_termination(ExitStatus::from_raw(accepted)));
        }
        for rejected in [9, 11, 6, 1<<8, 139<<8] {
            assert!(!expected_termination(ExitStatus::from_raw(rejected)));
        }
    }
    #[cfg(unix)]
    #[test]
    fn posix_shutdown_uses_sigterm_without_forced_kill() {
        let child=Command::new("sleep").arg("30").spawn().unwrap();
        let mut running=Running { process: child, log: PathBuf::new() };
        running.stop_with_term().unwrap();
        assert!(running.process.try_wait().unwrap().is_some());
    }
    #[test]
    fn trace_header_contract_is_present_in_fixture() {
        let payload=serde_json::json!({"x-request-id":"abc","x-ores-trace-id":"ores-trace-abc"});
        let map:serde_json::Value=payload;
        assert_eq!(map["x-ores-trace-id"].as_str().unwrap(),
            format!("ores-trace-{}",map["x-request-id"].as_str().unwrap()));
    }
}
