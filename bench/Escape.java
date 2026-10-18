import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Optional;

public final class Escape {
    record Complex(double re, double im) {
        Complex plus(Complex o) {
            return new Complex(re + o.re, im + o.im);
        }

        Complex times(Complex o) {
            return new Complex(re * o.re - im * o.im, re * o.im + im * o.re);
        }

        double norm() {
            return re * re + im * im;
        }
    }

    record Range(int min, int max) {}

    static final List<Complex> escaped = new ArrayList<>();

    static int mandelbrot(double x, double y) {
        Complex c = new Complex(x, y);
        Complex z = new Complex(0, 0);
        int n = 0;
        while (n < 64 && z.norm() < 4) {
            z = z.times(z).plus(c);
            n++;
        }
        return n;
    }

    static Range range(int[] values, int from, int to) {
        int min = Integer.MAX_VALUE;
        int max = Integer.MIN_VALUE;
        for (int i = from; i < to; i++) {
            min = Math.min(min, values[i]);
            max = Math.max(max, values[i]);
        }
        return new Range(min, max);
    }

    static long spread(int seed) {
        int[] values = new int[4096];
        for (int k = 0; k < values.length; k++) {
            values[k] = (k * 7919 + seed) % 1000;
        }
        long sum = 0;
        for (int i = 0; i < values.length; i += 8) {
            Range r = range(values, i, i + 8);
            if (r.max() - r.min() == seed + 100000) {
                escaped.add(new Complex(r.min(), r.max()));
                return -1;
            }
            sum += r.max() - r.min();
        }
        return sum;
    }

    static long collections(int seed) {
        List<Integer> list = new ArrayList<>();
        for (int i = 0; i < 1024; i++) {
            list.add(i * (seed & 3));
        }
        long sum = 0;
        for (Iterator<Integer> it = list.iterator(); it.hasNext(); ) {
            Integer v = it.next();
            sum += Optional.ofNullable(v).map(x -> x + seed).orElse(0);
        }
        int matches = 0;
        for (int i = 0; i < 1024; i++) {
            Integer boxed = i * seed;
            if (boxed.equals(list.get(i))) {
                matches++;
            }
        }
        return sum + matches;
    }

    static long arrays(int seed) {
        long sum = 0;
        for (int i = 0; i < 4096; i++) {
            int[] rgb = {i & 255, (i >> 4) & 255, (i ^ seed) & 255};
            double[] w = {0.299, 0.587, 0.114};
            sum += (long) (rgb[0] * w[0] + rgb[1] * w[1] + rgb[2] * w[2]);
        }
        return sum;
    }

    static long run(int seed) {
        long sum = 0;
        for (int i = 0; i < 64; i++) {
            for (int j = 0; j < 64; j++) {
                sum += mandelbrot(-2 + i / 32.0, -1 + (j + seed % 3) / 32.0);
            }
        }
        return sum + spread(seed) + collections(seed) + arrays(seed) + escaped.size();
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 1000, Escape::run);
    }
}
