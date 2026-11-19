//! Fail-closed host route generator for the legacy .ores REST demo.
//! Rust host tooling does not establish .atto guest or compiler parity.
use std::{
    env, error::Error,
    path::{Path, PathBuf}, process,
    sync::atomic::AtomicU64,
};
// The portable fallback and legacy regression fixtures still use path-based
// APIs, but Linux production builds should not import unused fallback code.
#[cfg(any(test, not(target_os = "linux")))]
use std::{
    ffi::OsStr, fs::{self, OpenOptions}, io::Write,
    sync::atomic::Ordering, time::{SystemTime, UNIX_EPOCH},
};
type Result<T> = std::result::Result<T, Box<dyn Error>>;
static UNIQUE: AtomicU64 = AtomicU64::new(0);
const MAX_ENTRIES: usize = 10_000;
// Bounds recursion even when a directory tree contains very few entries.
const MAX_DEPTH: usize = 64;
const MAX_ROUTES: usize = 4_096;
const METHODS: &[&str] = &["get", "post", "put", "patch", "delete", "head", "options"];

#[derive(Debug)]
struct Route { method: String, parts: Vec<String>, path: PathBuf }

#[cfg(any(test, not(target_os = "linux")))]
fn real_dir(path: &Path) -> Result<()> {
    if !fs::symlink_metadata(path)?.file_type().is_dir() {
        return Err(format!("not a real directory (symlink forbidden): {}", path.display()).into());
    }
    Ok(())
}
fn json_quote(text: &str) -> String {
    let mut out = String::from("\"");
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{00}'..='\u{1f}' | '\u{7f}'..='\u{10ffff}' => {
                let mut units = [0u16; 2];
                for unit in ch.encode_utf16(&mut units) { out.push_str(&format!("\\u{unit:04x}")); }
            }
            _ => out.push(ch),
        }
    }
    out.push('"');
    out
}
fn valid_segment(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || c==b'-' || c==b'_')
}
#[cfg(any(test, not(target_os = "linux")))]
fn traverse(root: &Path, dir: &Path, depth: usize, entries: &mut usize, routes: &mut Vec<Route>) -> Result<()> {
    if depth > MAX_DEPTH { return Err("route directory depth budget exceeded".into()); }
    // Enforce the limit *while* reading, not after collecting an unbounded directory.
    let mut children = Vec::new();
    for entry in fs::read_dir(dir)? {
        *entries = entries.checked_add(1).ok_or("route entry budget overflow")?;
        if *entries > MAX_ENTRIES { return Err("route entry budget exceeded".into()); }
        children.push(entry?);
    }
    children.sort_by_key(|entry| entry.path());
    for child in children {
        let path = child.path();
        let ty = fs::symlink_metadata(&path)?.file_type();
        if ty.is_symlink() { return Err(format!("symlink in route tree: {}", path.display()).into()); }
        if ty.is_dir() { traverse(root, &path, depth + 1, entries, routes)?; continue; }
        if !ty.is_file() { return Err("special filesystem entry in route tree".into()); }
        if path.extension() != Some(OsStr::new("ores")) { continue; }
        let method = path.file_stem().and_then(OsStr::to_str).ok_or("invalid method encoding")?;
        if !METHODS.contains(&method) {
            return Err(format!("unsupported method filename: {}", path.display()).into());
        }
        let mut parts = Vec::new();
        for component in path.parent().ok_or("missing route parent")?.strip_prefix(root)?.components() {
            let part = component.as_os_str().to_str().ok_or("invalid segment encoding")?;
            if !valid_segment(part) {
                return Err(format!("only literal URL segments are supported: {}", path.display()).into());
            }
            parts.push(part.to_owned());
        }
        if routes.len() >= MAX_ROUTES { return Err("route count budget exceeded".into()); }
        routes.push(Route { method: method.to_ascii_uppercase(), parts, path });
    }
    Ok(())
}
#[cfg(not(target_os = "linux"))]
fn discover(project: &Path) -> Result<Vec<Route>> {
    let source = project.join("src");
    real_dir(&source)?;
    real_dir(&source.join("routes"))?;
    let root = source.join("routes/rest");
    real_dir(&root)?;
    let mut count = 0;
    let mut routes = Vec::new();
    traverse(&root, &root, 0, &mut count, &mut routes)?;
    routes.sort_by(|a,b| a.path.cmp(&b.path));
    if routes.is_empty() { return Err("no REST routes found".into()); }
    Ok(routes)
}
fn render(project: &Path, routes: &[Route], port: u16, data_dir: &Path) -> Result<(String, String)> {
    if port == 0 { return Err("port must be in 1..65535".into()); }
    let mut lines = vec![
        "import module spin from \"../../dependencies/spin/src/spin\";".to_owned(),
        "import module http_routing from \"../../dependencies/spin/dependencies/http-routing/src/http_routing\";".to_owned()
    ];
    for (index, route) in routes.iter().enumerate() {
        let path = route.path.strip_prefix(project.join("src"))?.with_extension("");
        let import = path.to_str().ok_or("invalid import encoding")?.replace('\\', "/");
        lines.push(format!("import module route as route_{index} from \"../{import}\";"));
    }
    lines.push(String::new());
    lines.push("define module routes as".into());
    lines.push("  pub fnc register(borrow mut spin.Builder builder): void {".into());
    for (index, route) in routes.iter().enumerate() {
        let url = format!("/{}", route.parts.join("/"));
        let fragments = route.parts.iter()
            .map(|part| format!("http_routing.literal({})", json_quote(part)))
            .collect::<Vec<_>>().join(", ");
        lines.push(format!("    val path_{index} = http_routing.path({}, arr[{fragments}]);", json_quote(&url)));
        lines.push(format!("    builder.route(\"{}\", rt borrow path_{index}, route_{index}.handler(), 0, 0, \"{} {}\").unwrap();",
            route.method, route.method, url));
    }
    lines.extend(["    return;", "  }", "end", ""].iter().map(|s| (*s).to_owned()));
    let config = format!("define module config as\n  pub fnc port(): int {{ return {port}; }}\n  pub fnc data_path(): String {{ return {}; }}\nend\n",
        json_quote(&data_dir.join("baz.txt").to_string_lossy()));
    Ok((lines.join("\n"), config))
}
#[cfg(any(test, not(target_os = "linux")))]
fn publish(dest: &Path, text: &str) -> Result<()> {
    let parent = dest.parent().ok_or("missing output parent")?;
    real_dir(parent)?;
    for _ in 0..16 {
        let timestamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
        let file = parent.join(format!(".routes-{}-{timestamp}-{}",
            process::id(), UNIQUE.fetch_add(1, Ordering::Relaxed)));
        match OpenOptions::new().write(true).create_new(true).open(&file) {
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
            Ok(mut output) => {
                let result = (|| -> Result<()> {
                    output.write_all(text.as_bytes())?;
                    output.sync_all()?;
                    drop(output);
                    #[cfg(unix)] {
                        use std::os::unix::fs::PermissionsExt;
                        fs::set_permissions(&file, fs::Permissions::from_mode(0o644))?;
                    }
                    fs::rename(&file, dest)?;
                    Ok(())
                })();
                if result.is_err() { let _ = fs::remove_file(&file); }
                return result;
            }
        }
    }
    Err("cannot reserve temporary publication entry".into())
}
// A deterministic bad second destination must not modify the first artifact.
#[cfg(not(target_os = "linux"))]
fn preflight_destination(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_file() || meta.file_type().is_symlink() => Ok(()),
        Ok(_) => Err(format!("generated source destination must be a file or replaceable symlink: {}", path.display()).into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}
// Portable fallback: the stdlib path-based variant is not concurrency-race-safe.
#[cfg(not(target_os = "linux"))]
fn generate(project: &Path, port: u16, data_dir: &Path) -> Result<usize> {
    if port == 0 { return Err("port must be in 1..65535".into()); }
    let routes = discover(project)?;
    let output = project.join("src/generated");
    if fs::symlink_metadata(&output).is_ok() { real_dir(&output)?; }
    fs::create_dir_all(data_dir)?;
    let data = fs::canonicalize(data_dir)?;
    if data.parent().is_none() { return Err("cannot use filesystem root as data directory".into()); }
    let (sources, config) = render(project, &routes, port, &data)?;
    fs::create_dir_all(&output)?;
    real_dir(&output)?;
    preflight_destination(&output.join("routes.ores"))?;
    preflight_destination(&output.join("config.ores"))?;
    publish(&output.join("routes.ores"), &sources)?;
    publish(&output.join("config.ores"), &config)?;
    Ok(routes.len())
}
#[cfg(target_os = "linux")]
mod secure_io;

#[cfg(target_os = "linux")]
fn generate(project: &Path, port: u16, data_dir: &Path) -> Result<usize> {
    secure_io::generate(project, port, data_dir)
}

fn cli() -> Result<(u16, PathBuf)> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent()
        .and_then(Path::parent).ok_or("cannot locate repository")?;
    let mut port = 3000;
    let mut data = root.join("data");
    let mut iter = env::args().skip(1);
    while let Some(arg) = iter.next() {
        if arg == "--help" { println!("Usage: --port N --data-dir PATH"); process::exit(0); }
        let (name, value) = match arg.split_once('=') {
            Some((key, val)) if !val.is_empty() => (key, val.to_owned()),
            _ => (arg.as_str(), iter.next().ok_or("missing argument value")?)
        };
        match name {
            "--port" => {
                if value.is_empty() || !value.bytes().all(|c| c.is_ascii_digit()) {
                    return Err("port must be decimal".into());
                }
                port = value.parse::<u16>()?;
                if port == 0 { return Err("port must be in 1..65535".into()); }
            },
            "--data-dir" if !value.is_empty() => data = PathBuf::from(value),
            _ => return Err(format!("unknown option: {name}").into())
        }
    }
    Ok((port, data))
}
fn main() {
    let outcome = (|| -> Result<()> {
        let (port, data) = cli()?;
        let project = Path::new(env!("CARGO_MANIFEST_DIR")).parent()
            .and_then(Path::parent).ok_or("cannot locate repository")?;
        let count = generate(project, port, &data)?;
        println!("Generated {count} method routes from src/routes/rest");
        Ok(())
    })();
    if let Err(error) = outcome { eprintln!("ROUTE_GENERATOR_FAIL: {error}"); process::exit(1); }
}
#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let seq = UNIQUE.fetch_add(1, Ordering::Relaxed);
            let dir = env::temp_dir().join(format!("atto-route-fixture-{}-{seq}", process::id()));
            fs::create_dir_all(dir.join("src/routes/rest/health")).unwrap();
            fs::write(dir.join("src/routes/rest/health/get.ores"), "fixture").unwrap();
            Self(dir)
        }
        fn run(&self) -> Result<usize> { generate(&self.0, 3000, &self.0.join("data")) }
        fn generated(&self, name: &str) -> PathBuf { self.0.join("src/generated").join(name) }
    }
    impl Drop for Fixture { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }
    #[test]
    fn routes_are_deterministic_and_match_legacy_order() {
        let t = Fixture::new();
        fs::create_dir_all(t.0.join("src/routes/rest/foo/bar_b-2")).unwrap();
        fs::write(t.0.join("src/routes/rest/foo/bar_b-2/post.ores"), "fixture").unwrap();
        assert_eq!(t.run().unwrap(), 2);
        let routes = fs::read_to_string(t.generated("routes.ores")).unwrap();
        assert!(routes.contains("route_0 from \"../routes/rest/foo/bar_b-2/post\""));
        assert!(routes.contains("POST /foo/bar_b-2"));
        assert!(routes.contains("route_1 from \"../routes/rest/health/get\""));
        t.run().unwrap();
        assert_eq!(fs::read_to_string(t.generated("routes.ores")).unwrap(), routes);
    }
    #[test]
    fn denied_method_and_path_preserve_last_good_publication() {
        let t = Fixture::new();
        t.run().unwrap();
        let before = fs::read_to_string(t.generated("routes.ores")).unwrap();
        let config = fs::read_to_string(t.generated("config.ores")).unwrap();
        let bad = t.0.join("src/routes/rest/health/trace.ores");
        fs::write(&bad, "fixture").unwrap();
        assert!(t.run().is_err());
        fs::remove_file(bad).unwrap();
        let path = t.0.join("src/routes/rest/bad segment");
        fs::create_dir_all(&path).unwrap();
        fs::write(path.join("get.ores"), "fixture").unwrap();
        assert!(t.run().is_err());
        assert_eq!(fs::read_to_string(t.generated("routes.ores")).unwrap(), before);
        assert_eq!(fs::read_to_string(t.generated("config.ores")).unwrap(), config);
    }
    #[cfg(unix)]
    #[test]
    fn symlink_route_parent_and_output_directory_are_rejected() {
        use std::os::unix::fs::symlink;
        let t = Fixture::new();
        let route = t.0.join("src/routes/rest/alias");
        symlink("health", &route).unwrap();
        assert!(t.run().is_err());
        fs::remove_file(route).unwrap();
        let external = t.0.join("elsewhere");
        fs::create_dir_all(&external).unwrap();
        symlink(&external, t.0.join("src/generated")).unwrap();
        assert!(t.run().is_err());
        assert_eq!(fs::read_dir(&external).unwrap().count(), 0);
    }
    #[cfg(unix)]
    #[test]
    fn stale_output_symlink_is_replaced_not_followed() {
        use std::os::unix::fs::symlink;
        let t = Fixture::new();
        let target = t.0.join("protected.ores");
        fs::write(&target, "preserve").unwrap();
        fs::create_dir_all(t.0.join("src/generated")).unwrap();
        symlink(&target, t.generated("routes.ores")).unwrap();
        t.run().unwrap();
        assert_eq!(fs::read_to_string(target).unwrap(), "preserve");
        assert!(!fs::symlink_metadata(t.generated("routes.ores")).unwrap().file_type().is_symlink());
    }
    #[test]
    fn json_escaping_covers_quotes_control_and_unicode() {
        assert_eq!(json_quote("a\"b\\c\n"), "\"a\\\"b\\\\c\\n\"");
        assert_eq!(json_quote("café"), "\"caf\\u00e9\"");
        assert_eq!(json_quote("😀"), "\"\\ud83d\\ude00\"");
    }
    #[test]
    fn invalid_port_and_no_routes_never_publish() {
        let t = Fixture::new();
        assert!(generate(&t.0, 0, &t.0.join("data")).is_err());
        fs::remove_file(t.0.join("src/routes/rest/health/get.ores")).unwrap();
        assert!(t.run().is_err());
        assert!(!t.generated("routes.ores").exists());
    }
}

#[cfg(test)]
mod publication_tests;
