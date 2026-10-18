public final class MatrixMultiply {
    static final int N = 80;

    static void multiply(double[] a, double[] b, double[] c, int n) {
        for (int i = 0; i < n; i++) {
            for (int k = 0; k < n; k++) {
                double v = a[i * n + k];
                for (int j = 0; j < n; j++) {
                    c[i * n + j] += v * b[k * n + j];
                }
            }
        }
    }

    static long run(int seed) {
        double[] a = new double[N * N];
        double[] b = new double[N * N];
        double[] c = new double[N * N];
        for (int i = 0; i < N * N; i++) {
            a[i] = (i + seed) % 17;
            b[i] = (i * 3 + seed) % 19;
        }
        multiply(a, b, c, N);
        long sum = 0;
        for (double v : c) {
            sum += (long) v;
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 6265, MatrixMultiply::run);
    }
}
