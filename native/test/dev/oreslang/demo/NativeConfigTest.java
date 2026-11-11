package dev.oreslang.demo;

import java.nio.file.Path;

/** Runnable host-only tests; no private compiler or guest execution. */
public final class NativeConfigTest {
    private NativeConfigTest() { }

    public static void main(String[] args) {
        Path data = Path.of("quota config dir with \"quote\" and \\ slash")
                .toAbsolutePath().normalize();
        String source = NativeConfig.render(3000, data);
        require(source.startsWith("define module config as\n"), "missing module");
        require(source.endsWith("end\n"), "module not closed");
        require(source.contains("pub fnc port(): int { return 3000; }"),
                "wrong port declaration");
        require(source.contains("pub fnc data_path(): String { return "
                + NativeConfig.quote(data.resolve("baz.txt").toString()) + "; }"),
                "wrong canonical persisted resource path");
        require(source.contains("pub fnc quota_staging_path(): String { return "
                + NativeConfig.quote(data.resolve(".baz.quota-stage.txt").toString()) + "; }"),
                "missing quota staging path");
        require(source.split("pub fnc quota_staging_path", -1).length == 2,
                "duplicate staging function");
        require(source.split("pub fnc data_path", -1).length == 2,
                "duplicate data function");
        require(NativeConfig.render(1, data).contains("return 1;"), "minimum port failed");
        require(NativeConfig.render(65535, data).contains("return 65535;"), "maximum port failed");
        expectBad(() -> NativeConfig.render(0, data));
        expectBad(() -> NativeConfig.render(65536, data));
        expectBad(() -> NativeConfig.render(3000, null));
        expectBad(() -> NativeConfig.render(3000, data.getRoot()));
        expectBad(() -> NativeConfig.render(3000, data.resolve("with,comma")));
        for (String unsafe : new String[] {
                "\n", "\r", "\t", String.valueOf((char) 1), String.valueOf((char) 127)
        }) {
            expectBad(() -> NativeConfig.quote("before" + unsafe + "after"));
        }
        String escaped = NativeConfig.quote("slash\\and\"quote");
        require(escaped.startsWith("\"") && escaped.endsWith("\""), "unquoted source path");
        require(escaped.contains("\\\\") && escaped.contains("\\\""),
                "quote/backslash missing escaping");
        System.out.println("native config source tests passed");
    }

    private static void require(boolean yes, String message) {
        if (!yes) throw new AssertionError(message);
    }

    private static void expectBad(Runnable operation) {
        try {
            operation.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("expected invalid configuration rejection");
    }
}
