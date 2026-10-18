public final class Dispatch {
    interface Op {
        int apply(int x);
    }

    static class A implements Op {
        @Override
        public int apply(int x) {
            return x * 3 + 1;
        }
    }

    static final class B extends A {
        @Override
        public int apply(int x) {
            return x ^ 0x5555;
        }
    }

    static final class C extends A {
        @Override
        public int apply(int x) {
            return x * 7 - 3;
        }
    }

    static final class D extends A {
        @Override
        public int apply(int x) {
            return (x >>> 1) ^ 0x12345;
        }
    }

    static final Op[] OPS = {new A(), new B(), new C(), new D()};
    static final A[] CLASSES = {(A) OPS[0], (A) OPS[1], (A) OPS[2], (A) OPS[3]};

    static int mono(int seed) {
        int x = seed;
        for (int i = 0; i < 100_000; i++) {
            x = OPS[0].apply(x);
        }
        return x;
    }

    static int bi(int seed) {
        int x = seed;
        for (int i = 0; i < 100_000; i++) {
            x = OPS[i & 1].apply(x);
        }
        return x;
    }

    static int mega(int seed) {
        int x = seed;
        for (int i = 0; i < 100_000; i++) {
            x = OPS[i & 3].apply(x);
        }
        return x;
    }

    static int virtual(int seed) {
        int x = seed;
        for (int i = 0; i < 100_000; i++) {
            x = CLASSES[i & 3].apply(x);
        }
        return x;
    }

    static long run(int seed) {
        return (long) mono(seed) + bi(seed) + mega(seed) + virtual(seed);
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 1299, Dispatch::run);
    }
}
