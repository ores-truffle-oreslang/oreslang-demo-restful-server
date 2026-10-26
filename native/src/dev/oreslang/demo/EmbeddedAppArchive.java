package dev.oreslang.demo;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.HashSet;
import java.util.Set;
import java.util.zip.ZipInputStream;

final class EmbeddedAppArchive {
    private static final int MAX_ENTRIES = 2048;
    private static final long MAX_UNCOMPRESSED_BYTES = 32L * 1024L * 1024L;

    private EmbeddedAppArchive() { }

    static void extract(InputStream source, Path root) throws IOException {
        extract(source, root, MAX_ENTRIES, MAX_UNCOMPRESSED_BYTES);
    }

    static void extract(InputStream source, Path root, int maxEntries, long maxBytes)
            throws IOException {
        if (maxEntries < 1 || maxBytes < 1) {
            throw new IllegalArgumentException("archive limits must be positive");
        }
        Path normalizedRoot = root.toAbsolutePath().normalize();
        Files.createDirectories(normalizedRoot);

        Set<Path> seen = new HashSet<>();
        long total = 0;
        int count = 0;
        byte[] buffer = new byte[8192];

        try (var zip = new ZipInputStream(source)) {
            for (var entry = zip.getNextEntry(); entry != null; entry = zip.getNextEntry()) {
                if (++count > maxEntries) {
                    throw new IOException("embedded application has too many entries");
                }
                String name = entry.getName();
                if (name == null || name.isBlank() || name.indexOf('\0') >= 0
                        || name.indexOf('\\') >= 0 || name.startsWith("/")) {
                    throw new IOException("invalid embedded application entry");
                }

                Path target = normalizedRoot.resolve(name).normalize();
                if (!target.startsWith(normalizedRoot) || !seen.add(target)) {
                    throw new IOException("invalid or duplicate embedded application entry");
                }

                if (entry.isDirectory()) {
                    Files.createDirectories(target);
                    continue;
                }

                Files.createDirectories(target.getParent());
                try (OutputStream output = Files.newOutputStream(
                        target, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {
                    int read;
                    while ((read = zip.read(buffer)) != -1) {
                        total += read;
                        if (total > maxBytes) {
                            throw new IOException("embedded application exceeds size limit");
                        }
                        output.write(buffer, 0, read);
                    }
                }
            }
        }
    }
}
