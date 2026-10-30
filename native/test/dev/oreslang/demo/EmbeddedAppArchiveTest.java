package dev.oreslang.demo;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

public final class EmbeddedAppArchiveTest {
    private EmbeddedAppArchiveTest() { }

    public static void main(String[] args) throws Exception {
        acceptsBoundedArchive();
        rejectsTraversal();
        rejectsNormalizedDuplicate();
        rejectsOversizeArchive();
        rejectsTooManyEntries();
        System.out.println("embedded archive tests passed");
    }

    private static void acceptsBoundedArchive() throws Exception {
        Path root = Files.createTempDirectory("ores-archive-ok-");
        try {
            EmbeddedAppArchive.extract(zip(new Entry("src/main.ores", "ok")), root, 4, 64);
            require(Files.readString(root.resolve("src/main.ores")).equals("ok"), "valid file missing");
        } finally {
            removeTree(root);
        }
    }

    private static void rejectsTraversal() throws Exception {
        Path parent = Files.createTempDirectory("ores-archive-parent-");
        Path root = parent.resolve("app");
        try {
            expectIOException(() -> EmbeddedAppArchive.extract(
                    zip(new Entry("../outside.ores", "escape")), root, 4, 64));
            require(!Files.exists(parent.resolve("outside.ores")), "archive escaped extraction root");
        } finally {
            removeTree(parent);
        }
    }

    private static void rejectsNormalizedDuplicate() throws Exception {
        Path root = Files.createTempDirectory("ores-archive-dup-");
        try {
            expectIOException(() -> EmbeddedAppArchive.extract(
                    zip(new Entry("a/../main.ores", "one"), new Entry("main.ores", "two")),
                    root, 4, 64));
        } finally {
            removeTree(root);
        }
    }

    private static void rejectsOversizeArchive() throws Exception {
        Path root = Files.createTempDirectory("ores-archive-size-");
        try {
            expectIOException(() -> EmbeddedAppArchive.extract(
                    zip(new Entry("main.ores", "123456789")), root, 4, 8));
        } finally {
            removeTree(root);
        }
    }

    private static void rejectsTooManyEntries() throws Exception {
        Path root = Files.createTempDirectory("ores-archive-count-");
        try {
            expectIOException(() -> EmbeddedAppArchive.extract(
                    zip(new Entry("a.ores", "a"), new Entry("b.ores", "b")), root, 1, 64));
        } finally {
            removeTree(root);
        }
    }

    private static ByteArrayInputStream zip(Entry... entries) throws IOException {
        var bytes = new ByteArrayOutputStream();
        try (var zip = new ZipOutputStream(bytes)) {
            for (Entry entry : entries) {
                zip.putNextEntry(new ZipEntry(entry.name()));
                zip.write(entry.body().getBytes(StandardCharsets.UTF_8));
                zip.closeEntry();
            }
        }
        return new ByteArrayInputStream(bytes.toByteArray());
    }

    private static void expectIOException(ThrowingRunnable action) throws Exception {
        try {
            action.run();
            throw new AssertionError("expected IOException");
        } catch (IOException expected) {
            // expected
        }
    }

    private static void require(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    private static void removeTree(Path root) throws IOException {
        if (!Files.exists(root)) return;
        try (var paths = Files.walk(root)) {
            for (Path path : paths.sorted(java.util.Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        }
    }

    private record Entry(String name, String body) { }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
