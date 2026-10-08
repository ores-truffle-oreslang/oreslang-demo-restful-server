# Native REST server

Run `./bin/rest-server` to listen on 127.0.0.1:3000. No Java installation,
Maven, Python, Git, Oreslang source checkout, or dependency download is needed.
This archive targets one OS/architecture; keep bin/ and lib/ together.

Runtime configuration:

    ./bin/rest-server --port=3001 --data-dir=/path/to/data
    ./bin/rest-server --build-info
    BASE_URL=http://127.0.0.1:3001 ./curl-10.sh

PORT and DATA_DIR environment variables are also supported. Data defaults to
./data under your current working directory. The curl script requires curl
and Bash and replaces/deletes the demo resource. The server itself does not.

AOT-only images have a compiled native runtime/interpreter and no guest JIT.
They reject --mode=jit and --mode=hybrid. Hybrid images include guest JIT support
and accept --mode=aot to disable runtime compilation for a run. Neither build
profile claims ahead-of-time machine-code lowering of every Oreslang handler.
The fixed Ores application/import graph is embedded, extracted into a private
temporary directory at startup, and removed on normal termination or SIGTERM.
SIGKILL can leave temporary files for OS/admin cleanup.

GET /health reports health. /foo/bar/baz supports GET, POST(create), PUT(upsert),
PATCH(append text) and DELETE with a persistent UTF-8 file. Mutations require
Content-Type: text/plain and a nonempty body. Admission is one active request;
concurrent excess gets 503. Use one process per data directory. File writes are
not crash-atomic transactions; there is no authentication or total storage quota.

The launcher requires the packaged native carrier library and fails closed if
it cannot load it. OS system libraries are still required; inspect manifest.json
and build-info.json for provenance and checksums. This is not a fully static
executable. No ports are exposed beyond loopback. Stop with Ctrl-C.
