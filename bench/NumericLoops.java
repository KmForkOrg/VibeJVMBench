public final class NumericLoops {
    static long run(int seed) {
        int x = seed;
        long y = seed;
        double z = seed * 0.125;
        for (int i = 0; i < 200_000; i++) {
            x = (x * 1664525 + 1013904223) ^ (x >>> 13);
            y = (y * 6364136223846793005L + x) ^ (y >>> 29);
            z = z * 0.999999 + (x & 255) * 0.000001;
        }
        return y ^ x ^ Double.doubleToLongBits(z);
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 3220, NumericLoops::run);
    }
}
