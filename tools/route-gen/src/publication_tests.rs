//! Adversarial Rust publication tests migrated from the Python generator oracle.
//! These tests are host-only and do not assert guest compiler/runtime parity.
use super::*;
use std::{env, fs, path::PathBuf};

struct Sandbox(PathBuf);
impl Sandbox {
    fn new() -> Self {
        let id = UNIQUE.fetch_add(1, Ordering::Relaxed);
        let dir = env::temp_dir().join(format!("atto-rest-adversarial-{}-{id}", process::id()));
        fs::create_dir_all(dir.join("src/routes/rest/health")).unwrap();
        fs::write(dir.join("src/routes/rest/health/get.ores"), b"fixture").unwrap();
        Self(dir)
    }
    fn path(&self, suffix: &str) -> PathBuf { self.0.join(suffix) }
    fn write(&self, relative: &str) -> PathBuf {
        let path = self.path(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, b"fixture").unwrap();
        path
    }
    fn run(&self) -> Result<usize> { generate(&self.0, 3000, &self.path("data")) }
    fn output(&self, name: &str) -> String {
        fs::read_to_string(self.path(&format!("src/generated/{name}"))).unwrap()
    }
    fn snapshot(&self) -> (String, String) { (self.output("routes.ores"), self.output("config.ores")) }
}
impl Drop for Sandbox {
    fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
}
#[test]
fn port_limits_are_emitted_as_literals() {
    let t = Sandbox::new();
    for port in [1u16, 65535u16] {
        generate(&t.0, port, &t.path("data")).unwrap();
        assert!(t.output("config.ores").contains(&format!("return {port};")));
    }
}
#[test]
fn all_supported_methods_have_distinct_handlers() {
    let t = Sandbox::new();
    for method in METHODS {
        t.write(&format!("src/routes/rest/health/{method}.ores"));
    }
    assert_eq!(t.run().unwrap(), METHODS.len());
    let source = t.output("routes.ores");
    for method in METHODS {
        assert!(source.contains(&format!("{} /health", method.to_ascii_uppercase())));
    }
    assert_eq!(source.matches(".handler()").count(), METHODS.len());
}
#[test]
fn lexical_order_is_stable_across_creation_order() {
    let t = Sandbox::new();
    t.write("src/routes/rest/zeta/post.ores");
    t.write("src/routes/rest/alpha/get.ores");
    t.write("src/routes/rest/a_b-2/delete.ores");
    t.run().unwrap();
    let out = t.output("routes.ores");
    let markers = ["DELETE /a_b-2", "GET /alpha", "GET /health", "POST /zeta"];
    let offsets: Vec<_> = markers.iter().map(|m| out.find(m).unwrap()).collect();
    assert!(offsets.windows(2).all(|pair| pair[0] < pair[1]));
    assert!(out.contains("http_routing.literal(\"a_b-2\")"));
}
#[test]
fn nested_literal_segments_and_method_order() {
    let t = Sandbox::new();
    t.write("src/routes/rest/api/v1_2/delete.ores");
    t.write("src/routes/rest/health/head.ores");
    t.write("src/routes/rest/health/post.ores");
    t.run().unwrap();
    let source = t.output("routes.ores");
    assert!(source.contains("DELETE /api/v1_2"));
    assert!(source.contains("http_routing.literal(\"v1_2\")"));
    let i = source.find("GET /health").unwrap();
    let j = source.find("HEAD /health").unwrap();
    let k = source.find("POST /health").unwrap();
    assert!(i < j && j < k);
}
#[test]
fn forbidden_segment_spellings_leave_both_outputs_unchanged() {
    let t = Sandbox::new();
    t.run().unwrap();
    let expected = t.snapshot();
    for name in ["bad%2Fsegment", "café", "two words", ".hidden", "not.valid", "control\u{202e}"] {
        let path = t.write(&format!("src/routes/rest/{name}/get.ores"));
        assert!(t.run().is_err(), "incorrectly accepted {name:?}");
        assert_eq!(t.snapshot(), expected, "rewrote output on rejected {name:?}");
        fs::remove_file(path.clone()).unwrap();
        fs::remove_dir(path.parent().unwrap()).unwrap();
    }
}
#[test]
fn unsupported_method_and_wrong_case_preserve_known_good() {
    let t = Sandbox::new();
    t.run().unwrap();
    let expected = t.snapshot();
    for name in ["trace", "GET", "Get", "options ", "connect"] {
        let path = t.write(&format!("src/routes/rest/health/{name}.ores"));
        assert!(t.run().is_err(), "incorrectly accepted {name:?}");
        assert_eq!(t.snapshot(), expected);
        fs::remove_file(path).unwrap();
    }
}
#[test]
fn non_source_files_are_ignored_but_cannot_extend_route_count() {
    let t = Sandbox::new();
    t.write("src/routes/rest/health/trace.txt");
    t.write("src/routes/rest/health/README.md");
    assert_eq!(t.run().unwrap(), 1);
    assert_eq!(t.output("routes.ores").matches(".handler()").count(), 1);
}
#[test]
fn missing_routes_do_not_create_outputs() {
    let t = Sandbox::new();
    fs::remove_file(t.path("src/routes/rest/health/get.ores")).unwrap();
    assert!(t.run().is_err());
    assert!(!t.path("src/generated/routes.ores").exists());
    assert!(!t.path("src/generated/config.ores").exists());
}
#[test]
fn invalid_zero_port_cannot_modify_existing_outputs() {
    let t = Sandbox::new();
    t.run().unwrap();
    let expected = t.snapshot();
    assert!(generate(&t.0, 0, &t.path("other-data")).is_err());
    assert_eq!(t.snapshot(), expected);
    assert!(!t.path("other-data").exists());
}
#[test]
fn unicode_and_control_path_payloads_are_json_quoted() {
    let t = Sandbox::new();
    for name in ["quote\"name", "tab\tname", "newline\nname", "backslash\\name", "unicode-\u{2028}"] {
        let data = t.path(name);
        generate(&t.0, 3000, &data).unwrap();
        let canonical = fs::canonicalize(&data).unwrap().join("baz.txt");
        let expected = format!("return {};", json_quote(&canonical.to_string_lossy()));
        let generated = t.output("config.ores");
        assert!(generated.contains(&expected), "bad config encoding for {name:?}");
        assert_eq!(generated.matches("pub fnc data_path()").count(), 1);
    }
}
#[test]
fn repeated_publication_with_special_path_is_byte_identical() {
    let t = Sandbox::new();
    let data = t.path("quoted\"name\\with-backslash");
    generate(&t.0, 65535, &data).unwrap();
    let first = t.snapshot();
    generate(&t.0, 65535, &data).unwrap();
    assert_eq!(t.snapshot(), first);
}
#[test]
fn non_directory_output_parent_is_rejected() {
    let t = Sandbox::new();
    fs::write(t.path("src/generated"), b"not a directory").unwrap();
    assert!(t.run().is_err());
    assert_eq!(fs::read(t.path("src/generated")).unwrap(), b"not a directory");
}
#[cfg(unix)]
#[test]
fn all_trust_boundary_symlinks_are_rejected() {
    use std::os::unix::fs::symlink;
    for boundary in ["src", "src/routes", "src/routes/rest"] {
        let t = Sandbox::new();
        let original = t.path(boundary);
        let relocated = t.path("external");
        fs::rename(&original, &relocated).unwrap();
        symlink(&relocated, &original).unwrap();
        assert!(t.run().is_err(), "accepted aliased root {boundary}");
        assert!(!t.path("src/generated/routes.ores").exists());
    }
}
#[cfg(unix)]
#[test]
fn symlinked_leaf_is_never_imported() {
    use std::os::unix::fs::symlink;
    let t = Sandbox::new();
    let source = t.path("src/routes/rest/health/get.ores");
    symlink(&source, t.path("src/routes/rest/health/post.ores")).unwrap();
    assert!(t.run().is_err());
    assert!(!t.path("src/generated/routes.ores").exists());
}
#[cfg(unix)]
#[test]
fn replaced_generated_symlinks_leave_external_target_unchanged() {
    use std::os::unix::fs::symlink;
    let t = Sandbox::new();
    fs::create_dir_all(t.path("src/generated")).unwrap();
    let target = t.path("important.txt");
    fs::write(&target, b"preserve").unwrap();
    for name in ["routes.ores", "config.ores"] {
        symlink(&target, t.path(&format!("src/generated/{name}"))).unwrap();
    }
    t.run().unwrap();
    assert_eq!(fs::read(&target).unwrap(), b"preserve");
    for name in ["routes.ores", "config.ores"] {
        assert!(!fs::symlink_metadata(t.path(&format!("src/generated/{name}"))).unwrap()
            .file_type().is_symlink());
    }
}
#[test]
fn failed_single_leaf_rename_cleans_temporary_and_preserves_entry() {
    let t = Sandbox::new();
    let destination = t.path("src/generated/routes.ores");
    fs::create_dir_all(&destination).unwrap();
    let result = publish(&destination, "attempt");
    assert!(result.is_err());
    assert!(destination.is_dir());
    let temps = fs::read_dir(t.path("src/generated")).unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
        .filter(|s| s.starts_with(".routes-"))
        .collect::<Vec<_>>();
    assert!(temps.is_empty(), "leaked temporary files: {temps:?}");
}
#[cfg(unix)]
#[test]
fn special_files_in_route_tree_fail_closed() {
    use std::os::unix::net::UnixListener;
    let t = Sandbox::new();
    let socket = t.path("src/routes/rest/health/socket");
    let _listener = UnixListener::bind(&socket).unwrap();
    assert!(t.run().is_err());
    assert!(!t.path("src/generated/routes.ores").exists());
}

#[test]
fn malformed_second_output_cannot_partially_update_first_output() {
    let t = Sandbox::new();
    t.run().unwrap();
    let original_routes = t.output("routes.ores");
    let config = t.path("src/generated/config.ores");
    fs::remove_file(&config).unwrap();
    fs::create_dir(&config).unwrap();
    t.write("src/routes/rest/extra/post.ores");
    assert!(t.run().is_err(), "generated config directory was accepted");
    assert_eq!(t.output("routes.ores"), original_routes,
        "routes were partially replaced before config destination failed");
    assert!(config.is_dir(), "preflight unexpectedly altered invalid destination");
}

#[test]
fn reject_excessive_route_nesting_before_publication() {
    let t = Sandbox::new();
    let mut next = t.path("src/routes/rest");
    for _ in 0..=MAX_DEPTH {
        next.push("d");
        fs::create_dir(&next).unwrap();
    }
    let error = t.run().unwrap_err().to_string();
    assert!(error.contains("depth budget"), "unexpected rejection: {error}");
    assert!(!t.path("src/generated/routes.ores").exists());
}

#[test]
fn reject_entry_budget_during_enumeration() {
    let t = Sandbox::new();
    let root = t.path("src/routes/rest");
    let mut admitted = MAX_ENTRIES;
    let mut routes = Vec::new();
    let result = traverse(&root, &root, 0, &mut admitted, &mut routes);
    assert!(result.is_err(), "admitted an entry beyond the global budget");
    assert_eq!(routes.len(), 0);
    assert!(!t.path("src/generated/routes.ores").exists());
}
