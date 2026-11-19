//! Linux descriptor-relative route discovery and publication.
//!
//! Every untrusted path component below the caller-supplied project root is
//! opened relative to a pinned directory with O_NOFOLLOW. Output rename
//! is performed using renameat relative to the pinned generated directory.
//!
//! The project-root ancestors and configured data directory remain trusted.
//! A concurrently writable output directory can still be edited in place:
//! this code prevents replacement-ancestor redirection, not every mutation.
use super::{render, Result, Route, MAX_DEPTH, MAX_ENTRIES, MAX_ROUTES, METHODS, UNIQUE};
use std::{
    ffi::{CString, OsStr, OsString},
    fs::{self, File, Permissions},
    io::{self, Write},
    os::{
        fd::{AsRawFd, FromRawFd, RawFd},
        raw::{c_char, c_int, c_uint},
        unix::{ffi::OsStrExt, fs::PermissionsExt},
    },
    path::Path,
    process,
    sync::atomic::Ordering,
    time::{SystemTime, UNIX_EPOCH},
};

const AT_FDCWD: c_int = -100;
const O_RDONLY: c_int = 0;
const O_WRONLY: c_int = 1;
const O_CREAT: c_int = 0o100;
const O_EXCL: c_int = 0o200;
const O_DIRECTORY: c_int = 0o200000;
const O_NOFOLLOW: c_int = 0o400000;
const O_CLOEXEC: c_int = 0o2000000;
const O_PATH: c_int = 0o10000000;
const MODE_PRIVATE: c_uint = 0o600;
const DIR_MODE: c_uint = 0o755;

unsafe extern "C" {
    fn openat(dirfd: c_int, pathname: *const c_char, flags: c_int, ...) -> c_int;
    fn mkdirat(dirfd: c_int, pathname: *const c_char, mode: c_uint) -> c_int;
    fn renameat(olddirfd: c_int, oldpath: *const c_char,
                newdirfd: c_int, newpath: *const c_char) -> c_int;
    fn unlinkat(dirfd: c_int, pathname: *const c_char, flags: c_int) -> c_int;
}

fn c_name(name: &OsStr) -> Result<CString> {
    Ok(CString::new(name.as_bytes())?)
}
fn open_relative(dir: RawFd, name: &OsStr, flags: c_int, mode: c_uint) -> Result<File> {
    let name = c_name(name)?;
    // SAFETY: the name is nul-terminated; on success this uniquely owned FD
    // is closed by the File destructor.
    let fd = unsafe { openat(dir, name.as_ptr(), flags | O_CLOEXEC, mode) };
    if fd < 0 { return Err(io::Error::last_os_error().into()); }
    Ok(unsafe { File::from_raw_fd(fd) })
}
fn dir_at(parent: &File, name: &OsStr) -> Result<File> {
    let file = open_relative(parent.as_raw_fd(), name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW, 0)?;
    if !file.metadata()?.is_dir() { return Err("route ancestor is not a directory".into()); }
    Ok(file)
}
fn project_root(path: &Path) -> Result<File> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.file_type().is_dir() {
        return Err("project root itself must be a real directory".into());
    }
    // Only the project-root argument and its ancestors are trusted.
    let file = open_relative(AT_FDCWD, path.as_os_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW, 0)?;
    if !file.metadata()?.is_dir() { return Err("project root must be a directory".into()); }
    Ok(file)
}
fn enumerate(dir: &File, count: &mut usize) -> Result<Vec<OsString>> {
    // /proc/self/fd/N opens the pinned inode, even if the original path is
    // renamed or replaced. All children are then opened using openat.
    let path = format!("/proc/self/fd/{}", dir.as_raw_fd());
    let mut children = Vec::new();
    for item in fs::read_dir(path)? {
        *count = count.checked_add(1).ok_or("route entry budget overflow")?;
        if *count > MAX_ENTRIES { return Err("route entry budget exceeded".into()); }
        children.push(item?.file_name());
    }
    children.sort();
    Ok(children)
}
fn walk(
    dir: &File, logical: &Path, parts: &mut Vec<String>, depth: usize,
    entries: &mut usize, routes: &mut Vec<Route>,
) -> Result<()> {
    if depth > MAX_DEPTH { return Err("route directory depth budget exceeded".into()); }
    for name in enumerate(dir, entries)? {
        // O_PATH|O_NOFOLLOW pins the entry itself and does not block on FIFO.
        let pinned = open_relative(dir.as_raw_fd(), &name, O_PATH | O_NOFOLLOW, 0)?;
        let kind = pinned.metadata()?.file_type();
        let path = logical.join(&name);
        if kind.is_symlink() {
            return Err(format!("symlink in route tree: {}", path.display()).into());
        }
        if kind.is_dir() {
            let part = name.to_str().ok_or("non-UTF8 path segment")?;
            if !super::valid_segment(part) {
                return Err(format!("unsupported route path segment: {}", path.display()).into());
            }
            let child = dir_at(dir, &name)?;
            parts.push(part.to_owned());
            let result = walk(&child, &path, parts, depth + 1, entries, routes);
            parts.pop();
            result?;
            continue;
        }
        if !kind.is_file() {
            return Err(format!("non-regular route entry: {}", path.display()).into());
        }
        if path.extension() != Some(OsStr::new("ores")) { continue; }
        let method = path.file_stem().and_then(OsStr::to_str).ok_or("invalid method encoding")?;
        if !METHODS.contains(&method) {
            return Err(format!("unsupported method: {}", path.display()).into());
        }
        if routes.len() >= MAX_ROUTES { return Err("route count budget exceeded".into()); }
        routes.push(Route { method: method.to_ascii_uppercase(), parts: parts.clone(), path });
    }
    Ok(())
}
fn scan(rest: &File, logical: &Path) -> Result<Vec<Route>> {
    let mut routes = Vec::new();
    let mut parts = Vec::new();
    let mut entries = 0usize;
    walk(rest, logical, &mut parts, 0, &mut entries, &mut routes)?;
    routes.sort_by(|a, b| a.path.cmp(&b.path));
    if routes.is_empty() { return Err("no REST routes found".into()); }
    Ok(routes)
}
fn mkdir_at(parent: &File, name: &OsStr) -> Result<()> {
    let name = c_name(name)?;
    // SAFETY: valid directory handle and nul-terminated component.
    let ok = unsafe { mkdirat(parent.as_raw_fd(), name.as_ptr(), DIR_MODE) };
    if ok == 0 { return Ok(()); }
    let err = io::Error::last_os_error();
    if err.kind() == io::ErrorKind::AlreadyExists { return Ok(()); }
    Err(err.into())
}
fn check_destination(dir: &File, name: &OsStr) -> Result<()> {
    match open_relative(dir.as_raw_fd(), name, O_PATH | O_NOFOLLOW, 0) {
        Ok(target) => {
            let kind = target.metadata()?.file_type();
            if kind.is_file() || kind.is_symlink() { Ok(()) }
            else { Err("generated output leaf is not replaceable".into()) }
        }
        Err(error) => {
            let missing = error.downcast_ref::<io::Error>()
                .map(|e| e.kind() == io::ErrorKind::NotFound).unwrap_or(false);
            if missing { Ok(()) } else { Err(error) }
        }
    }
}
fn unlink(dir: &File, name: &OsStr) -> Result<()> {
    let name = c_name(name)?;
    // SAFETY: unlinkat with flags 0 removes only files, not directories.
    let status = unsafe { unlinkat(dir.as_raw_fd(), name.as_ptr(), 0) };
    if status < 0 { return Err(io::Error::last_os_error().into()); }
    Ok(())
}
fn rename_in(dir: &File, from: &OsStr, to: &OsStr) -> Result<()> {
    let from = c_name(from)?;
    let to = c_name(to)?;
    // SAFETY: both names are NUL-terminated components relative to a pinned FD.
    let status = unsafe {
        renameat(dir.as_raw_fd(), from.as_ptr(), dir.as_raw_fd(), to.as_ptr())
    };
    if status < 0 { return Err(io::Error::last_os_error().into()); }
    Ok(())
}
fn publish(dir: &File, leaf: &OsStr, content: &str) -> Result<()> {
    for _ in 0..16 {
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
        let temp = OsString::from(format!(
            ".routes-{}-{now}-{}", process::id(), UNIQUE.fetch_add(1, Ordering::Relaxed),
        ));
        let mut file = match open_relative(
            dir.as_raw_fd(), &temp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW,
            MODE_PRIVATE,
        ) {
            Ok(file) => file,
            Err(error) => {
                if error.downcast_ref::<io::Error>()
                    .map(|e| e.kind() == io::ErrorKind::AlreadyExists).unwrap_or(false) {
                    continue;
                }
                return Err(error);
            }
        };
        let result = (|| -> Result<()> {
            file.write_all(content.as_bytes())?;
            file.set_permissions(Permissions::from_mode(0o644))?;
            file.sync_all()?;
            rename_in(dir, &temp, leaf)?;
            dir.sync_all()?;
            Ok(())
        })();
        if result.is_err() { let _ = unlink(dir, &temp); }
        return result;
    }
    Err("cannot reserve unique temporary source file".into())
}
pub(super) fn generate(project: &Path, port: u16, data_dir: &Path) -> Result<usize> {
    if port == 0 { return Err("port must be in 1..65535".into()); }
    let root = project_root(project)?;
    let source = dir_at(&root, OsStr::new("src"))?;
    let routes = dir_at(&source, OsStr::new("routes"))?;
    let rest = dir_at(&routes, OsStr::new("rest"))?;
    let discovered = scan(&rest, &project.join("src/routes/rest"))?;
    fs::create_dir_all(data_dir)?;
    let data = fs::canonicalize(data_dir)?;
    if data.parent().is_none() { return Err("data directory cannot be filesystem root".into()); }
    let (generated_routes, generated_config) = render(project, &discovered, port, &data)?;
    mkdir_at(&source, OsStr::new("generated"))?;
    let output = dir_at(&source, OsStr::new("generated"))?;
    check_destination(&output, OsStr::new("routes.ores"))?;
    check_destination(&output, OsStr::new("config.ores"))?;
    publish(&output, OsStr::new("routes.ores"), &generated_routes)?;
    publish(&output, OsStr::new("config.ores"), &generated_config)?;
    Ok(discovered.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{os::unix::fs::symlink, path::PathBuf};
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let id = UNIQUE.fetch_add(1, Ordering::Relaxed);
            let root = std::env::temp_dir().join(format!(
                "atto-fd-route-{}-{id}", process::id(),
            ));
            fs::create_dir_all(root.join("src/routes/rest/health")).unwrap();
            fs::write(root.join("src/routes/rest/health/get.ores"), "fixture").unwrap();
            Self(root)
        }
        fn at(&self, path: &str) -> PathBuf { self.0.join(path) }
    }
    impl Drop for Fixture {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    #[test]
    fn pinned_route_dir_cannot_be_redirected_by_ancestor_replacement() {
        let t = Fixture::new();
        let root = project_root(&t.0).unwrap();
        let source = dir_at(&root, OsStr::new("src")).unwrap();
        let routes = dir_at(&source, OsStr::new("routes")).unwrap();
        let pinned = dir_at(&routes, OsStr::new("rest")).unwrap();
        let outside = t.at("attacker");
        fs::create_dir_all(outside.join("unsafe")).unwrap();
        fs::write(outside.join("unsafe/get.ores"), b"malicious").unwrap();
        fs::rename(t.at("src/routes/rest"), t.at("retained")).unwrap();
        symlink(&outside, t.at("src/routes/rest")).unwrap();
        let collected = scan(&pinned, &t.at("src/routes/rest")).unwrap();
        assert_eq!(collected.len(), 1);
        assert_eq!(collected[0].parts, ["health"]);
        assert!(collected[0].path.ends_with("health/get.ores"));
        assert!(dir_at(&routes, OsStr::new("rest")).is_err());
    }

    #[test]
    fn pinned_output_dir_cannot_be_redirected_by_ancestor_replacement() {
        let t = Fixture::new();
        fs::create_dir(t.at("src/generated")).unwrap();
        let root = project_root(&t.0).unwrap();
        let source = dir_at(&root, OsStr::new("src")).unwrap();
        let pinned_output = dir_at(&source, OsStr::new("generated")).unwrap();
        let outside = t.at("attacker-out");
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("routes.ores"), b"do not modify").unwrap();
        fs::rename(t.at("src/generated"), t.at("retained-generated")).unwrap();
        symlink(&outside, t.at("src/generated")).unwrap();
        publish(&pinned_output, OsStr::new("routes.ores"), "pinned original").unwrap();
        assert_eq!(fs::read(outside.join("routes.ores")).unwrap(), b"do not modify");
        assert_eq!(fs::read(t.at("retained-generated/routes.ores")).unwrap(), b"pinned original");
    }

    #[test]
    fn route_symlink_replacement_never_follows_attacker_target() {
        let t = Fixture::new();
        let root = project_root(&t.0).unwrap();
        let src = dir_at(&root, OsStr::new("src")).unwrap();
        let routes = dir_at(&src, OsStr::new("routes")).unwrap();
        let pinned = dir_at(&routes, OsStr::new("rest")).unwrap();
        let target = t.at("sensitive.txt");
        fs::write(&target, b"do not read").unwrap();
        symlink(&target, t.at("src/routes/rest/health/post.ores")).unwrap();
        assert!(scan(&pinned, &t.at("src/routes/rest")).is_err());
        assert_eq!(fs::read(&target).unwrap(), b"do not read");
    }

    #[test]
    fn output_leaf_directory_is_rejected_before_any_write() {
        let t = Fixture::new();
        generate(&t.0, 3000, &t.at("data")).unwrap();
        let original = fs::read(t.at("src/generated/routes.ores")).unwrap();
        fs::remove_file(t.at("src/generated/config.ores")).unwrap();
        fs::create_dir(t.at("src/generated/config.ores")).unwrap();
        fs::write(t.at("src/routes/rest/health/post.ores"), "fixture").unwrap();
        assert!(generate(&t.0, 3000, &t.at("data")).is_err());
        assert_eq!(fs::read(t.at("src/generated/routes.ores")).unwrap(), original);
    }
}
