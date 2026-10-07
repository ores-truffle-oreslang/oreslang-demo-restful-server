#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-aot}"
case "$mode" in aot|hybrid) ;; *) echo 'Usage: scripts/build-native.sh [aot|hybrid]' >&2; exit 64;; esac
: "${JAVA_HOME:?Set JAVA_HOME to GraalVM with native-image (matching the pinned SDK)}"
: "${ORESLANG_SOURCE_DIR:?Set ORESLANG_SOURCE_DIR to the compiler checkout}"
source_dir="$(cd "$ORESLANG_SOURCE_DIR" && pwd -P)"
[[ -z "$(git -C "$root" status --porcelain)" ]] || { echo 'Application checkout must be clean so artifact provenance is exact.' >&2; exit 65; }
[[ -x "$JAVA_HOME/bin/native-image" ]] || { echo 'GraalVM native-image is required on the build machine.' >&2; exit 69; }
expected="$(tr -d '[:space:]' < "$root/SOURCE_REF")"
[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$expected" ]] || { echo 'Compiler does not match SOURCE_REF.' >&2; exit 65; }
[[ -z "$(git -C "$source_dir" status --porcelain)" ]] || { echo 'Compiler must be clean.' >&2; exit 65; }
if git -C "$root" submodule status --recursive | grep -Eq '^[+-U]'; then
  echo 'Initialize the exact dependency pins with scripts/setup.sh.' >&2; exit 65
fi
mvn -q -f "$source_dir/pom.xml" -DskipTests package dependency:build-classpath -Dmdep.outputFile=target/classpath.txt
build="$root/.cache/native-$mode"
classes="$build/classes"
mkdir -p "$classes"
# Freeze the discovered route registry and embed only Ores sources. Runtime
# configuration is replaced inside a per-process extracted bundle by native code.
python3 "$root/scripts/generate-routes.py"
python3 - "$root" "$classes/ores-app.zip" <<'PY'
from pathlib import Path
import sys, zipfile
root, output = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    for directory in ['src', 'dependencies/spin/src', 'dependencies/spin/dependencies/http-routing/src']:
        for path in sorted((root / directory).rglob('*.ores')):
            if path.is_symlink():
                raise SystemExit(f'Refusing symlinked source: {path}')
            name = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            # Never embed a build-machine-specific data path.
            data = b'define module config as\nend\n' if name == 'src/generated/config.ores' else path.read_bytes()
            archive.writestr(info, data)
PY
classpath="$source_dir/target/classes:$(cat "$source_dir/target/classpath.txt")"
"$JAVA_HOME/bin/javac" --release 21 -cp "$classpath" -d "$classes" "$root/native/src/dev/oreslang/demo/RestServerMain.java"
platform="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
dist="$root/dist/rest-server-$mode-$platform"
# This is a derived build-output path (mode is allowlisted above). Start clean
# so repeated builds cannot retain stale libraries or read-only license trees.
rm -rf -- "$dist"
mkdir -p "$dist/bin" "$dist/lib"
options=(-O2 --no-fallback -H:+ReportExceptionStackTraces -Dgraalvm.locatorDisabled=true
  "-Dores.native.build-mode=$mode" '-H:IncludeResources=ores-app\.zip')
if [[ "$mode" == aot ]]; then options+=(-Dtruffle.UseFallbackRuntime=true); fi
"$JAVA_HOME/bin/native-image" "${options[@]}" -cp "$classes:$classpath" \
  dev.oreslang.demo.RestServerMain -o "$dist/bin/rest-server"
case "$(uname -s)" in
  Darwin) library=liboresthread.dylib;;
  Linux) library=liboresthread.so;;
  *) echo 'Native carriers support macOS and Linux.' >&2; exit 69;;
esac
cp "$source_dir/target/native/$library" "$dist/lib/$library"
cp "$root/scripts/curl-10.sh" "$dist/curl-10.sh"
cp "$root/native/README.md" "$dist/README.md"
mkdir -p "$dist/licenses"
cp -R "$JAVA_HOME/legal" "$dist/licenses/graalvm"
python3 - "$source_dir/target/classpath.txt" "$dist/licenses" <<'LICENSES'
import pathlib, re, sys, zipfile
out = pathlib.Path(sys.argv[2])
for name in pathlib.Path(sys.argv[1]).read_text().strip().split(':'):
    path = pathlib.Path(name)
    if path.suffix != '.jar':
        continue
    with zipfile.ZipFile(path) as jar:
        for member in jar.namelist():
            if re.search(r'(^|/)(LICENSE|NOTICE|COPYING)([./_-]|$)', member, re.I) and not member.endswith('/'):
                target = out / path.stem / pathlib.PurePosixPath(member).name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(jar.read(member))
LICENSES
"$dist/bin/rest-server" --build-info > "$dist/build-info.json"
python3 - "$root" "$source_dir" "$dist" "$mode" <<'PY'
import hashlib, json, pathlib, subprocess, sys
root, source, dist = map(pathlib.Path, sys.argv[1:4])
metadata = dict(mode=sys.argv[4], compiler_commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip(),
                application_commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),
                graalvm=subprocess.check_output([__import__('os').environ['JAVA_HOME']+'/bin/native-image','--version'],text=True).strip())
metadata['sha256'] = {str(p.relative_to(dist)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dist.rglob('*')) if p.is_file() and p.name != 'manifest.json'}
(dist/'manifest.json').write_text(json.dumps(metadata, indent=2)+'\n')
PY
COPYFILE_DISABLE=1 tar -czf "$dist.tar.gz" -C "$(dirname "$dist")" "$(basename "$dist")"
echo "Native distribution: $dist.tar.gz"
