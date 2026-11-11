package dev.oreslang.demo;

import java.nio.file.Path;

/**
 * Source generation for the embedded native guest, kept separate from the
 * compiler launcher so host-only CI can execute exact configuration tests.
 */
final class NativeConfig {
    private NativeConfig() { }

    static String render(int port, Path resolvedDataDir) {
        if (port < 1 || port > 65535) {
            throw new IllegalArgumentException("Port must be 1..65535");
        }
        if (resolvedDataDir == null) {
            throw new IllegalArgumentException("Data directory cannot be null");
        }
        Path data = resolvedDataDir.toAbsolutePath().normalize();
        if (data.getParent() == null) {
            throw new IllegalArgumentException("Refusing filesystem-root data directory");
        }
        if (data.toString().contains(",")) {
            throw new IllegalArgumentException("Data directory cannot contain a comma");
        }

        // This must stay in lockstep with scripts/generate-routes.py. In
        // particular, persisted-quota source imports quota_staging_path().
        return "define module config as\n"
                + "  pub fnc port(): int { return " + port + "; }\n"
                + "  pub fnc data_path(): String { return "
                + quote(data.resolve("baz.txt").toString()) + "; }\n"
                + "  pub fnc quota_staging_path(): String { return "
                + quote(data.resolve(".baz.quota-stage.txt").toString()) + "; }\n"
                + "end\n";
    }

    static String quote(String value) {
        StringBuilder result = new StringBuilder(value.length() + 2).append('"');
        for (int i = 0; i < value.length(); ++i) {
            char ch = value.charAt(i);
            if (ch < 0x20 || ch == 0x7f) {
                // The current guest string literal parser does not have a
                // verified portable contract for every control-character
                // escape. Reject unusual filesystem paths rather than emit
                // untrusted source syntax or ambiguous capability strings.
                throw new IllegalArgumentException("Data path contains control characters");
            }
            if (ch == '"' || ch == '\\') result.append('\\');
            result.append(ch);
        }
        return result.append('"').toString();
    }
}
