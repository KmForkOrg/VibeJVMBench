public final class Exceptions {
    static final class Expected extends RuntimeException {
        final int value;

        Expected(int value) {
            this.value = value;
        }
    }

    static int checked(int x) {
        if ((x & 31) == 0) {
            throw new Expected(x);
        }
        return x * 3;
    }

    static long run(int seed) {
        long sum = 0;
        for (int i = 0; i < 16384; i++) {
            try {
                sum += checked(i + seed);
            } catch (Expected e) {
                sum -= e.value;
            } finally {
                sum++;
            }
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 4866, Exceptions::run);
    }
}
