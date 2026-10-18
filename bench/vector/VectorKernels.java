import jdk.incubator.vector.FloatVector;
import jdk.incubator.vector.IntVector;
import jdk.incubator.vector.LongVector;
import jdk.incubator.vector.VectorMask;
import jdk.incubator.vector.VectorOperators;
import jdk.incubator.vector.VectorSpecies;

public final class VectorKernels {
    static final VectorSpecies<Float> FLOATS = FloatVector.SPECIES_PREFERRED;
    static final VectorSpecies<Integer> INTS = IntVector.SPECIES_PREFERRED;
    static final VectorSpecies<Long> LONGS = LongVector.SPECIES_PREFERRED;
    static final int N = 4096;
    static final float[] A = new float[N];
    static final float[] B = new float[N];
    static final float[] C = new float[N];
    static final int[] X = new int[N];
    static final int[] Y = new int[N];

    static {
        for (int i = 0; i < N; i++) {
            A[i] = (i % 17) - 8;
            B[i] = (i % 5) - 2;
            X[i] = i * 2654435 + 7;
            Y[i] = i ^ 0x5bd1e995;
        }
    }

    static float dot(float[] a, float[] b) {
        FloatVector acc = FloatVector.zero(FLOATS);
        int i = 0;
        for (; i < FLOATS.loopBound(a.length); i += FLOATS.length()) {
            acc = FloatVector.fromArray(FLOATS, a, i).fma(FloatVector.fromArray(FLOATS, b, i), acc);
        }
        float sum = acc.reduceLanes(VectorOperators.ADD);
        for (; i < a.length; i++) {
            sum += a[i] * b[i];
        }
        return sum;
    }

    static float[] saxpy(float alpha, float[] a, float[] b, float[] c) {
        int i = 0;
        for (; i < FLOATS.loopBound(a.length); i += FLOATS.length()) {
            FloatVector.fromArray(FLOATS, a, i)
                    .mul(alpha)
                    .add(FloatVector.fromArray(FLOATS, b, i))
                    .max(-1000f)
                    .min(1000f)
                    .intoArray(c, i);
        }
        for (; i < a.length; i++) {
            c[i] = Math.min(Math.max(a[i] * alpha + b[i], -1000f), 1000f);
        }
        return c;
    }

    static long mix(int[] x, int[] y) {
        long total = 0;
        int parts = INTS.length() / LONGS.length();
        int i = 0;
        for (; i < INTS.loopBound(x.length); i += INTS.length()) {
            IntVector xv = IntVector.fromArray(INTS, x, i);
            IntVector yv = IntVector.fromArray(INTS, y, i);
            IntVector h = xv.mul(0x9E3779B1).lanewise(VectorOperators.XOR, yv.lanewise(VectorOperators.LSHR, 7));
            VectorMask<Integer> odd = h.and(1).compare(VectorOperators.NE, 0);
            IntVector lanes = h.add(yv, odd);
            for (int part = 0; part < parts; part++) {
                total += ((LongVector) lanes.castShape(LONGS, part)).reduceLanes(VectorOperators.ADD);
            }
            total += odd.trueCount();
        }
        for (; i < x.length; i++) {
            int h = (x[i] * 0x9E3779B1) ^ (y[i] >>> 7);
            boolean odd = (h & 1) != 0;
            total += (odd ? h + y[i] : h) + (odd ? 1 : 0);
        }
        return total;
    }

    static long run(int seed) {
        long r = 0;
        for (int k = 0; k < 64; k++) {
            r += (long) dot(A, B);
            saxpy(0.5f + (seed & 3), A, B, C);
            r += (long) C[(seed * 31 + k) & (N - 1)];
            r += mix(X, Y);
        }
        return r;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 2000, VectorKernels::run);
    }
}
