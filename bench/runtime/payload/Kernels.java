import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class Kernels {
    interface Shape {
        double area();
    }

    record Circle(double r) implements Shape {
        @Override
        public double area() {
            return Math.PI * r * r;
        }
    }

    record Rect(double w, double h) implements Shape {
        @Override
        public double area() {
            return w * h;
        }
    }

    static long numeric(int n) {
        long h = 17;
        for (int i = 0; i < n; i++) {
            h = h * 31 + (i ^ (h >>> 7));
            if ((i & 3) == 0) {
                h += i % 13;
            }
        }
        return h;
    }

    static double arrays(double[] a) {
        double s = 0;
        for (int k = 0; k < 8; k++) {
            for (int i = 1; i < a.length; i++) {
                a[i] = a[i - 1] * 0.5 + a[i] * 0.25 + k;
            }
            for (double v : a) {
                s += v;
            }
        }
        return s;
    }

    static double shapes(int n) {
        List<Shape> shapes = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            shapes.add(i % 3 == 0 ? new Circle(i % 7) : new Rect(i % 5, i % 11));
        }
        double sum = 0;
        for (int pass = 0; pass < 4; pass++) {
            for (Shape shape : shapes) {
                sum += shape.area();
            }
        }
        return sum;
    }

    static long maps(int n) {
        Map<Integer, Integer> m = new HashMap<>();
        for (int i = 0; i < n; i++) {
            m.merge(i % 1000, i, Integer::sum);
        }
        long sum = 0;
        for (int v : m.values()) {
            sum += v;
        }
        return sum;
    }

    public static long run(int seed) {
        double[] a = new double[4096];
        for (int i = 0; i < a.length; i++) {
            a[i] = (i * seed) % 17;
        }
        return numeric(200_000 + seed) + (long) arrays(a) + (long) shapes(20_000) + maps(50_000);
    }
}
