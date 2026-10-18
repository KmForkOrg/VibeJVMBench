public final class Bench {
    @FunctionalInterface
    public interface Kernel {
        long run(int seed) throws Exception;
    }

    public static long sink;

    private Bench() {}

    public static void measure(String[] args, int defaultRepetitions, Kernel kernel) throws Exception {
        int samples = args.length > 0 ? Integer.parseInt(args[0]) : 3;
        long warmupNanos = (args.length > 1 ? Long.parseLong(args[1]) : 2000L) * 1_000_000L;
        int repetitions = args.length > 2 ? Integer.parseInt(args[2]) : defaultRepetitions;
        if (samples < 1 || warmupNanos < 0 || repetitions < 1) {
            throw new IllegalArgumentException("need samples >= 1, warmupMillis >= 0, repetitions >= 1");
        }

        long warmupStart = System.nanoTime();
        for (int i = 0; i < 20 || System.nanoTime() - warmupStart < warmupNanos; i++) {
            sink = kernel.run(17 + (i & 31));
        }

        long expected = 0;
        for (int sample = 0; sample < samples; sample++) {
            long checksum = 1;
            long start = System.nanoTime();
            for (int i = 0; i < repetitions; i++) {
                checksum = checksum * 31 + kernel.run(17 + (i & 31));
            }
            long elapsed = System.nanoTime() - start;
            if (sample == 0) {
                expected = checksum;
            } else if (checksum != expected) {
                throw new IllegalStateException("checksum changed: " + checksum + " != " + expected);
            }
            System.err.println("time " + elapsed / 1_000_000.0);
        }
        System.out.println(expected);
    }
}
