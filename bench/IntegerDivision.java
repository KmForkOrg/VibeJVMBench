public final class IntegerDivision {
    static long run(int seed) {
        int x = seed;
        long y = seed;
        long sum = 0;
        for (int i = 0; i < 200_000; i++) {
            x = x * 1664525 + 1013904223;
            y = y * 6364136223846793005L + x;
            int d = (x >> 16) | 1;
            long e = (y >> 32) | 1;
            int a;
            long b;
            if ((i & 31) == 0) {
                a = Integer.MIN_VALUE;
                b = Long.MIN_VALUE;
                d = -1;
                e = -1;
            } else {
                a = x;
                b = y;
            }
            sum += a / d + a % d;
            sum ^= b / e + b % e;
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 1000, IntegerDivision::run);
    }
}
