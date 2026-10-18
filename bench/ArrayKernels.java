public final class ArrayKernels {
    static long run(int seed) {
        int n = 65536;
        int[] prefix = new int[n];
        int sum = seed;
        for (int i = 0; i < n; i++) {
            sum += (i ^ seed) & 255;
            prefix[i] = sum;
        }
        boolean[] composite = new boolean[n];
        for (int p = 2; p * p < n; p++) {
            if (!composite[p]) {
                for (int j = p * p; j < n; j += p) {
                    composite[j] = true;
                }
            }
        }
        long checksum = 0;
        for (int i = 2; i < n; i++) {
            if (!composite[i]) {
                checksum += prefix[i];
            }
        }
        return checksum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 8895, ArrayKernels::run);
    }
}
