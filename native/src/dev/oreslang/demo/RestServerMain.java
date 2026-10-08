package dev.oreslang.demo;

import dev.oreslang.launcher.OresMain;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.zip.ZipInputStream;

/** Native application entrypoint: no shell, Python, Java installation, or source checkout needed. */
public final class RestServerMain {
    private RestServerMain() { }

    public static void main(String[] args) throws Exception {
        if (args.length == 1 && args[0].equals("--build-info")) {
            OresMain.main(args);
            return;
        }
        String portText = System.getenv().getOrDefault("PORT", "3000");
        String dataText = System.getenv().getOrDefault("DATA_DIR", "data");
        String mode = null;
        for (String arg : args) {
            if (arg.startsWith("--port=")) portText = arg.substring(7);
            else if (arg.startsWith("--data-dir=")) dataText = arg.substring(11);
            else if (arg.startsWith("--mode=")) mode = arg;
            else throw new IllegalArgumentException("Supported: --port=3000 --data-dir=PATH --mode=aot|hybrid --build-info");
        }
        if (!portText.matches("[0-9]{1,5}")) throw new IllegalArgumentException("Invalid port");
        int port = Integer.parseInt(portText);
        if (port < 1 || port > 65535) throw new IllegalArgumentException("Port must be 1..65535");
        Path data = Path.of(dataText).toAbsolutePath().normalize();
        Files.createDirectories(data);
        data = data.toRealPath();
        // Permissions are comma-separated in the runtime CLI. Fail closed rather
        // than accidentally broadening authority when a data directory contains one.
        if (data.toString().contains(",")) throw new IllegalArgumentException("Data directory cannot contain a comma");

        Path executable = Path.of(ProcessHandle.current().info().command().orElseThrow(
                () -> new IllegalStateException("Cannot locate native executable"))).toRealPath();
        Path library = executable.getParent().getParent().resolve("lib")
                .resolve(System.mapLibraryName("oresthread"));
        System.setProperty("ores.thread.native.path", library.toString());
        System.setProperty("ores.runtime.carriers", "native");
        System.setProperty("polyglot.engine.WarnInterpreterOnly", "false");

        Path application = Files.createTempDirectory("ores-rest-app-");
        Thread cleanup = new Thread(() -> removeTree(application), "ores-app-cleanup");
        Runtime.getRuntime().addShutdownHook(cleanup);
        try {
            extract(application);
            Path config = application.resolve("src/generated/config.ores");
            Files.writeString(config, "define module config as\n"
                    + "  pub fnc port(): int { return " + port + "; }\n"
                    + "  pub fnc data_path(): String { return " + quote(data.resolve("baz.txt").toString()) + "; }\n"
                    + "end\n");
            var options = new java.util.ArrayList<String>();
            if (mode != null) options.add(mode);
            options.add("--platform=server");
            options.add("--allow-net=127.0.0.1:" + port);
            options.add("--allow-read=" + data);
            options.add("--allow-write=" + data);
            options.add(application.resolve("src/main.ores").toString());
            OresMain.main(options.toArray(String[]::new));
        } finally {
            Runtime.getRuntime().removeShutdownHook(cleanup);
            removeTree(application);
        }
    }

    private static void extract(Path root) throws IOException {
        var resource = RestServerMain.class.getResourceAsStream("/ores-app.zip");
        if (resource == null) throw new IOException("Missing embedded Oreslang application");
        try (var zip = new ZipInputStream(resource)) {
            for (var entry = zip.getNextEntry(); entry != null; entry = zip.getNextEntry()) {
                Path target = root.resolve(entry.getName()).normalize();
                if (!target.startsWith(root)) throw new IOException("Invalid embedded application entry");
                if (entry.isDirectory()) Files.createDirectories(target);
                else {
                    Files.createDirectories(target.getParent());
                    Files.copy(zip, target);
                }
            }
        }
    }

    private static String quote(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + "\"";
    }

    private static void removeTree(Path root) {
        try (var paths = Files.walk(root)) {
            paths.sorted(Comparator.reverseOrder()).forEach(path -> {
                try { Files.deleteIfExists(path); } catch (IOException ignored) { }
            });
        } catch (IOException ignored) { }
    }
}
